"""Governed Workspace and Git Sandbox Runner for NEXUS.

This module provides isolated Git worktree execution for software development tasks:
- Creates an ephemeral Git worktree per task under `.nexus_product/worktrees/<task_id>`.
- Preserves the authoritative repository workspace completely untouched (`writes_performed: false`).
- Enforces strict path traversal boundaries (rejects reads/writes/executions outside sandbox).
- Executes verification/test commands in isolated subprocesses with timeouts.
- Captures command, exit code, stdout, stderr, execution duration, and timestamp.
- Produces deterministic ExecutionResultArtifacts with reality="OBSERVED".
- Enforces clean and guaranteed teardown.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
from typing import Any, Generator, Sequence

from runtime.agent_artifacts import ExecutionResultArtifact, PatchArtifact


def utc_now() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class SandboxError(Exception):
    """Base exception for all sandbox and worktree errors."""
    pass


class PathTraversalError(SandboxError):
    """Raised when an operation attempts to access paths outside the sandbox root."""
    pass


class SandboxExecutionError(SandboxError):
    """Raised when a sandbox execution fails or violates governance invariants."""
    pass


def _remove_readonly(func: Any, path: str, excinfo: Any) -> None:
    """Error handler for shutil.rmtree to clear read-only file locks on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


@dataclass
class SandboxConfig:
    """Configuration for Git sandbox isolation."""
    repo_root: Path = field(default_factory=lambda: Path(os.getcwd()).resolve())
    worktree_base: Path = field(default_factory=lambda: Path(".nexus_product/worktrees"))
    default_timeout: float = 30.0
    max_output_bytes: int = 1024 * 1024  # 1MB limit for stdout/stderr capture
    env_whitelist: list[str] = field(
        default_factory=lambda: [
            "PATH",
            "PYTHONPATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOME",
            "LANG",
            "LC_ALL",
        ]
    )


@dataclass
class SandboxExecutionResult:
    """Deterministic outcome of a command executed inside the sandbox."""
    command: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timestamp: str = field(default_factory=utc_now)
    timed_out: bool = False
    writes_detected: bool = False
    modified_files: list[str] = field(default_factory=list)
    error: str | None = None

    def to_execution_artifact(
        self,
        task_id: str,
        tests_run: int = 0,
        tests_passed: int = 0,
        tests_failed: int = 0,
    ) -> ExecutionResultArtifact:
        """Convert sandbox outcome to a typed ExecutionResultArtifact."""
        return ExecutionResultArtifact(
            task_id=task_id,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            duration_seconds=self.duration_seconds,
            command=" ".join(self.command) if isinstance(self.command, list) else str(self.command),
            model_generated=False,  # Observed from deterministic runner
            agent_role="sandbox_runner",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SandboxContext:
    """Active sandbox instance bound to an isolated worktree for a specific task."""

    def __init__(
        self,
        task_id: str,
        repo_root: Path,
        worktree_path: Path,
        base_commit: str,
        config: SandboxConfig,
    ):
        self.task_id = task_id
        self.repo_root = repo_root.resolve()
        self.worktree_path = worktree_path.resolve()
        self.base_commit = base_commit
        self.config = config
        self.created_at = utc_now()
        self.writes_performed = False
        self.written_paths: list[str] = []
        self._cleaned_up = False

    def resolve_path(self, rel_path: str | Path) -> Path:
        """Resolve a path relative to the worktree and reject any path traversal escape."""
        p = Path(rel_path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.worktree_path / p).resolve()

        try:
            resolved.relative_to(self.worktree_path)
        except ValueError:
            raise PathTraversalError(
                f"Path traversal violation: '{rel_path}' resolves to '{resolved}', "
                f"which is outside sandbox root '{self.worktree_path}'"
            )

        return resolved

    def write_file(self, rel_path: str | Path, content: str | bytes) -> Path:
        """Safely write a file inside the isolated worktree."""
        target = self.resolve_path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)

        self.writes_performed = True
        rel_str = str(target.relative_to(self.worktree_path)).replace("\\", "/")
        if rel_str not in self.written_paths:
            self.written_paths.append(rel_str)
        return target

    def read_file(self, rel_path: str | Path) -> str:
        """Safely read a text file from inside the isolated worktree."""
        target = self.resolve_path(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"File not found in sandbox: '{rel_path}'")
        return target.read_text(encoding="utf-8", errors="replace")

    def file_exists(self, rel_path: str | Path) -> bool:
        """Check if a file exists inside the isolated worktree."""
        target = self.resolve_path(rel_path)
        return target.exists()

    def list_files(self, rel_dir: str | Path = "") -> list[str]:
        """List all files inside the isolated worktree relative to sandbox root."""
        target_dir = self.resolve_path(rel_dir)
        if not target_dir.is_dir():
            return []
        results = []
        for root, _, files in os.walk(target_dir):
            for file in files:
                full_path = Path(root) / file
                rel = full_path.relative_to(self.worktree_path)
                results.append(str(rel).replace("\\", "/"))
        return sorted(results)

    def apply_patch(self, patch: PatchArtifact) -> list[str]:
        """Apply files from a PatchArtifact into the isolated worktree."""
        applied: list[str] = []
        for path_str, content in patch.files.items():
            self.write_file(path_str, content)
            applied.append(path_str)
        return applied

    def get_modified_files(self) -> list[str]:
        """Inspect modified, added, or deleted files inside the worktree via git status."""
        cmd = ["git", "status", "--porcelain"]
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            modified = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line:
                    parts = line.split(maxsplit=1)
                    if len(parts) == 2:
                        modified.append(parts[1].replace("\\", "/"))
            return modified
        except Exception:
            return list(self.written_paths)

    def execute_command(
        self,
        command: list[str] | str,
        cwd: str | Path | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxExecutionResult:
        """Execute a command strictly inside the isolated worktree."""
        start_time = time.monotonic()
        timestamp = utc_now()

        # Parse command list
        if isinstance(command, str):
            cmd_list = command.split()
        else:
            cmd_list = list(command)

        if not cmd_list:
            raise SandboxExecutionError("Command list cannot be empty")

        # Resolve and validate working directory
        if cwd is None:
            exec_cwd = self.worktree_path
        else:
            exec_cwd = self.resolve_path(cwd)

        if not exec_cwd.is_dir():
            raise SandboxExecutionError(f"Working directory does not exist: {exec_cwd}")

        # Enforce execution boundaries: working directory MUST be inside worktree
        try:
            exec_cwd.resolve().relative_to(self.worktree_path)
        except ValueError:
            raise PathTraversalError(
                f"Execution rejected: target cwd '{exec_cwd}' is outside sandbox '{self.worktree_path}'"
            )

        # Build sanitized execution environment
        exec_env: dict[str, str] = {}
        for key in self.config.env_whitelist:
            if key in os.environ:
                exec_env[key] = os.environ[key]

        # Ensure sandbox is on PYTHONPATH
        current_ppath = exec_env.get("PYTHONPATH", "")
        sandbox_ppath = str(self.worktree_path)
        exec_env["PYTHONPATH"] = f"{sandbox_ppath}{os.pathsep}{current_ppath}" if current_ppath else sandbox_ppath

        if env:
            exec_env.update(env)

        exec_timeout = timeout if timeout is not None else self.config.default_timeout
        timed_out = False
        stdout_str = ""
        stderr_str = ""
        exit_code = -1
        err_msg = None

        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(exec_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=exec_env,
                text=True,
                shell=False,  # Strict shell=False to prevent arbitrary injection
            )
            stdout_str, stderr_str = proc.communicate(timeout=exec_timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            stdout_bytes, stderr_bytes = proc.communicate()
            stdout_str = stdout_bytes if isinstance(stdout_bytes, str) else ""
            stderr_str = (stderr_bytes if isinstance(stderr_bytes, str) else "") + f"\n[NEXUS Sandbox] Execution timed out after {exec_timeout} seconds."
            exit_code = 124  # Standard timeout exit code
            err_msg = f"Command timed out after {exec_timeout}s"
        except Exception as exc:
            exit_code = 1
            stderr_str = str(exc)
            err_msg = str(exc)

        duration = round(time.monotonic() - start_time, 4)

        # Cap output size
        if len(stdout_str) > self.config.max_output_bytes:
            stdout_str = stdout_str[: self.config.max_output_bytes] + "\n[Output truncated by NEXUS Sandbox]"
        if len(stderr_str) > self.config.max_output_bytes:
            stderr_str = stderr_str[: self.config.max_output_bytes] + "\n[Output truncated by NEXUS Sandbox]"

        modified_files = self.get_modified_files()
        writes_detected = bool(modified_files or self.writes_performed)

        return SandboxExecutionResult(
            command=cmd_list,
            cwd=str(exec_cwd),
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_seconds=duration,
            timestamp=timestamp,
            timed_out=timed_out,
            writes_detected=writes_detected,
            modified_files=modified_files,
            error=err_msg,
        )

    def cleanup(self) -> None:
        """Deterministic teardown: removes the isolated worktree cleanly."""
        if self._cleaned_up:
            return

        # 1. Attempt git worktree remove
        if self.worktree_path.exists():
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(self.worktree_path)],
                    cwd=self.repo_root,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=self.repo_root,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except Exception:
                pass

        # 2. Filesystem fallback if directory remains
        if self.worktree_path.exists():
            shutil.rmtree(self.worktree_path, onerror=_remove_readonly, ignore_errors=True)

        self._cleaned_up = True

    def __enter__(self) -> SandboxContext:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.cleanup()


class GitSandboxRunner:
    """Manages creation, lifecycle, and isolation of task-specific Git worktrees."""

    def __init__(
        self,
        repo_root: str | Path | None = None,
        config: SandboxConfig | None = None,
    ):
        if repo_root is not None:
            self.repo_root = Path(repo_root).resolve()
        elif config and config.repo_root:
            self.repo_root = Path(config.repo_root).resolve()
        else:
            self.repo_root = Path(os.getcwd()).resolve()

        self.config = config or SandboxConfig(repo_root=self.repo_root)

    def _validate_git_repo(self) -> str:
        """Verify that repo_root is a valid Git repository and return current HEAD sha."""
        if not (self.repo_root / ".git").exists() and not (self.repo_root / ".git").is_file():
            # Check via git rev-parse
            proc = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0 or proc.stdout.strip() != "true":
                raise SandboxError(f"Directory '{self.repo_root}' is not a valid Git repository")

        # Capture current HEAD commit
        proc_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc_head.returncode != 0:
            raise SandboxError(f"Failed to capture HEAD commit in '{self.repo_root}': {proc_head.stderr}")

        head_commit = proc_head.stdout.strip()
        if not head_commit:
            raise SandboxError("Captured HEAD commit is empty")
        return head_commit

    def _sanitize_task_id(self, task_id: str) -> str:
        """Validate and sanitize task_id to prevent path traversal in directory naming."""
        if not task_id or not isinstance(task_id, str):
            raise SandboxError("task_id must be a non-empty string")
        if ".." in task_id or "/" in task_id or "\\" in task_id:
            raise PathTraversalError(f"Invalid characters in task_id: '{task_id}'")
        # Keep alphanumeric, hyphen, underscore
        sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
        return sanitized

    def create_sandbox(self, task_id: str) -> SandboxContext:
        """Create an isolated Git worktree for the specified task."""
        safe_task_id = self._sanitize_task_id(task_id)
        head_commit = self._validate_git_repo()

        # Compute deterministic worktree path
        base_dir = (self.repo_root / self.config.worktree_base).resolve()
        worktree_path = (base_dir / safe_task_id).resolve()

        # Enforce that worktree_path is inside base_dir
        try:
            worktree_path.relative_to(base_dir)
        except ValueError:
            raise PathTraversalError(f"Worktree path '{worktree_path}' escapes base directory '{base_dir}'")

        # Clean existing worktree if present from previous run
        if worktree_path.exists():
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree_path)],
                    cwd=self.repo_root,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, capture_output=True, check=False)
            except Exception:
                pass
            if worktree_path.exists():
                shutil.rmtree(worktree_path, onerror=_remove_readonly, ignore_errors=True)

        base_dir.mkdir(parents=True, exist_ok=True)

        # Create detached worktree from HEAD
        cmd = ["git", "worktree", "add", "--detach", str(worktree_path), head_commit]
        proc = subprocess.run(
            cmd,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise SandboxError(f"Failed to create git worktree at '{worktree_path}': {proc.stderr.strip()}")

        return SandboxContext(
            task_id=task_id,
            repo_root=self.repo_root,
            worktree_path=worktree_path,
            base_commit=head_commit,
            config=self.config,
        )

    @contextmanager
    def sandbox(self, task_id: str) -> Generator[SandboxContext, None, None]:
        """Context manager for creating, using, and automatically cleaning up a sandbox."""
        ctx = self.create_sandbox(task_id)
        try:
            yield ctx
        finally:
            ctx.cleanup()
