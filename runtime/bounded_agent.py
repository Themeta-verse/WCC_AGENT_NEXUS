"""Bounded local agent runtime for PS002 Phase 2C real observation.

This module implements a minimal, bounded agent that performs real local
filesystem operations within a single configured test root. It is the
*bridge* between an actual agent runtime and NEXUS observation: the agent
does something, and NEXUS observes the resulting fact.

Safety model:
  - Only read operations are permitted on real files inside the test root.
  - Write operations are never executed; they are *observed as attempted*
    (the attempt is the real fact that NEXUS records).
  - No network access, no process execution, no privilege escalation.
  - The test root is the only path the agent may touch.

The runtime produces an ObservationReceipt that NEXUS can ingest to prove
that an action genuinely occurred, not merely that someone submitted a claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class ObservationReceipt:
    """Verifiable evidence that a bounded agent performed or attempted an action."""
    receipt_id: str
    agent_id: str
    operation: str
    target_resource: str
    requested_capability: str
    execution_mode: str
    start_time: str
    end_time: str
    status: str
    reality: str
    reason: str
    content_sha256: str | None = None
    content_size: int | None = None
    content_preview: str | None = None
    evidence_digest: str = ""
    provenance: list[str] = field(default_factory=list)


@dataclass
class BoundedAgentRuntime:
    """A bounded local agent that performs real filesystem operations.

    The agent is intentionally minimal: it can read files inside an allowed
    root and can *attempt* (but never execute) write operations. Every
    action produces an ObservationReceipt with cryptographic evidence of
    what actually occurred.
    """
    agent_id: str
    allowed_root: str
    capabilities: list[str] = field(default_factory=lambda: ["filesystem.read"])
    prohibited_operations: list[str] = field(default_factory=lambda: ["filesystem.write", "git.push"])

    def _resolve_within_root(self, path: str) -> Path | None:
        """Resolve a path and verify it is inside the allowed root.

        Relative paths are resolved against the allowed root (never the
        process working directory). Symlinks are resolved before the
        containment check, so symlink escapes are refused.
        """
        if not path or not isinstance(path, str):
            return None
        try:
            raw = Path(path.strip()).expanduser()
            candidate = (Path(self.allowed_root) / raw).resolve() if not raw.is_absolute() else raw.resolve()
        except (OSError, ValueError):
            return None
        try:
            root = Path(self.allowed_root).resolve()
        except (OSError, ValueError):
            return None
        if candidate == root or root in candidate.parents:
            return candidate
        return None

    def _attempt_read(self, path: str, max_chars: int = 20000) -> tuple[str, dict[str, Any] | None, str]:
        """Actually read a file from the filesystem."""
        resolved = self._resolve_within_root(path)
        if resolved is None:
            return "BLOCKED", None, f"path '{path}' is outside allowed root {self.allowed_root}"
        if not resolved.is_file():
            return "BLOCKED", None, f"path '{path}' does not exist or is not a file"
        try:
            data = resolved.read_bytes()
        except PermissionError:
            return "BLOCKED", None, f"permission denied reading '{path}'"
        except OSError as exc:
            return "BLOCKED", None, f"filesystem read failed: {type(exc).__name__}: {exc}"
        text = data[:max_chars].decode("utf-8", "replace")
        sha = sha256(data).hexdigest()
        observation = {
            "path": str(resolved),
            "size": len(data),
            "sha256": sha,
            "text": text,
            "text_hash": sha256(text.encode()).hexdigest(),
            "reality": "OBSERVED",
            "untrusted_content": True,
        }
        return "EXECUTED", observation, "real local filesystem read"

    def _attempt_write(self, path: str) -> tuple[str, str]:
        """Attempt a write operation without actually executing it.

        The runtime *never* performs writes. The attempt itself is the real
        fact that NEXUS observes.
        """
        return "BLOCKED", "write operation is prohibited by bounded runtime configuration; not executed"

    # ---- Phase 8: read-only git inspection (fixed argv, no shell) ----

    GIT_TIMEOUT_DEFAULT = 15
    GIT_TIMEOUT_MAX = 60

    def _git_repo_root(self) -> Path | None:
        """Return the allowed root when it is inside a git work tree, else None."""
        import subprocess
        try:
            root = Path(self.allowed_root).resolve()
        except (OSError, ValueError):
            return None
        if not root.is_dir():
            return None
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
        except (OSError, ValueError):
            return None
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return root

    def _run_git(self, argv: list[str], timeout: int, is_cancelled: Any = None) -> tuple[str, str, str]:
        """Run a fixed git argv inside the allowed root. Returns (status, output, reason)."""
        import subprocess
        if is_cancelled is not None:
            try:
                if is_cancelled():
                    return "BLOCKED", "", "cancelled before git execution"
            except Exception:
                pass
        repo = self._git_repo_root()
        if repo is None:
            return "BLOCKED", "", f"workspace root {self.allowed_root} is not inside a git repository; refusing rather than fabricating"
        try:
            timeout = max(1, min(int(timeout), self.GIT_TIMEOUT_MAX))
        except (TypeError, ValueError):
            timeout = self.GIT_TIMEOUT_DEFAULT
        try:
            proc = subprocess.run(
                argv,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return "BLOCKED", "", f"git command timed out after {timeout}s: {' '.join(argv[:3])}"
        except (OSError, ValueError) as exc:
            return "BLOCKED", "", f"git execution failed: {type(exc).__name__}: {exc}"
        except Exception as exc:
            return "BLOCKED", "", f"git execution failed: {type(exc).__name__}: {str(exc)[:200]}"
        if proc.returncode != 0:
            return "BLOCKED", "", f"git exited {proc.returncode}: {(proc.stderr or '')[:300]}"
        return "EXECUTED", (proc.stdout or "")[:20000], "real bounded git inspection"

    def _attempt_git_status(self, timeout: int = 15, is_cancelled: Any = None) -> tuple[str, dict[str, Any] | None, str]:
        """Real read-only `git status --short --branch` observation."""
        from hashlib import sha256 as _sha
        status, output, reason = self._run_git(
            ["git", "status", "--short", "--branch"], timeout, is_cancelled)
        if status != "EXECUTED":
            return "BLOCKED", None, reason
        digest = _sha(output.encode()).hexdigest()
        return "EXECUTED", {
            "command": "git status --short --branch",
            "output": output,
            "sha256": digest,
            "reality": "OBSERVED",
            "untrusted_content": True,
        }, reason

    def _attempt_git_diff(self, path: str | None = None, max_chars: int = 10000,
                          timeout: int = 15, is_cancelled: Any = None) -> tuple[str, dict[str, Any] | None, str]:
        """Real read-only `git diff` observation, optionally scoped to one in-root path."""
        from hashlib import sha256 as _sha
        argv = ["git", "diff", "--no-color"]
        if path:
            resolved = self._resolve_within_root(path)
            if resolved is None:
                return "BLOCKED", None, f"path '{path}' is outside allowed root {self.allowed_root}"
            argv = ["git", "diff", "--no-color", "--", str(resolved)]
        status, output, reason = self._run_git(argv, timeout, is_cancelled)
        if status != "EXECUTED":
            return "BLOCKED", None, reason
        try:
            limit = max(1, min(int(max_chars), 100000))
        except (TypeError, ValueError):
            limit = 10000
        text = output[:limit]
        return "EXECUTED", {
            "command": " ".join(argv[:4]),
            "path": str(path or ""),
            "output": text,
            "sha256": _sha(output.encode()).hexdigest(),
            "reality": "OBSERVED",
            "untrusted_content": True,
        }, reason

    def execute_action(self, operation: str, target_resource: str, parameters: dict[str, Any] | None = None,
                       is_cancelled: Any = None) -> ObservationReceipt:
        """Execute a real action and produce an observation receipt.

        The operation actually happens (or is actually blocked) here.
        """
        start = _now()
        params = parameters or {}
        receipt_id = f"receipt-{operation}-{sha256(target_resource.encode()).hexdigest()[:12]}-{int(datetime.now(timezone.utc).timestamp())}"

        write_ops = {"filesystem.write", "filesystem.create", "filesystem.patch", "write", "create", "patch",
                       "delete", "modify", "git.push", "push", "merge", "execute", "command"}
        write_attempts = {"filesystem.write", "filesystem.create", "filesystem.patch",
                          "git.push", "push", "merge", "execute", "command"}

        observation: dict[str, Any] | None = None
        status: str
        reality: str
        reason: str
        content_sha: str | None = None
        content_size: int | None = None
        content_preview: str | None = None

        if operation in write_ops:
            # Write operations are observed as attempted but never executed.
            # The block itself is the OBSERVED fact.
            status, reason = self._attempt_write(target_resource)
            reality = "OBSERVED"
            content_sha = None
            content_size = None
            content_preview = None
        elif operation in {"git.status"}:
            _timeout = (params.get("timeout_seconds", self.GIT_TIMEOUT_DEFAULT)
                        if isinstance(params, dict) else self.GIT_TIMEOUT_DEFAULT)
            status, observation, reason = self._attempt_git_status(timeout=_timeout, is_cancelled=is_cancelled)
            reality = "OBSERVED"
            if status == "EXECUTED" and observation:
                content_sha = observation.get("sha256")
                content_size = len(observation.get("output", ""))
                content_preview = observation.get("output", "")[:200]
        elif operation in {"git.diff"}:
            _timeout = (params.get("timeout_seconds", self.GIT_TIMEOUT_DEFAULT)
                        if isinstance(params, dict) else self.GIT_TIMEOUT_DEFAULT)
            _max = params.get("max_chars", 10000) if isinstance(params, dict) else 10000
            status, observation, reason = self._attempt_git_diff(
                path=target_resource or None, max_chars=_max, timeout=_timeout, is_cancelled=is_cancelled)
            reality = "OBSERVED"
            if status == "EXECUTED" and observation:
                content_sha = observation.get("sha256")
                content_size = len(observation.get("output", ""))
                content_preview = observation.get("output", "")[:200]
        elif operation in {"filesystem.read", "read"}:
            status, observation, reason = self._attempt_read(target_resource, max_chars=params.get("max_chars", 20000))
            # Both successful reads and honest BLOCKED refusals are OBSERVED facts.
            reality = "OBSERVED"
            if status == "EXECUTED" and observation:
                content_sha = observation.get("sha256")
                content_size = observation.get("size")
                content_preview = observation.get("text", "")[:200]
        else:
            resolved = self._resolve_within_root(target_resource or "")
            if resolved is None:
                status = "BLOCKED"
                reason = f"operation '{operation}' on '{target_resource}' is not permitted"
                reality = "OBSERVED"
            else:
                status = "BLOCKED"
                reason = f"operation '{operation}' is not recognized by bounded runtime"
                reality = "OBSERVED"

        end = _now()
        evidence_payload = {
            "receipt_id": receipt_id,
            "agent_id": self.agent_id,
            "operation": operation,
            "target_resource": target_resource,
            "status": status,
            "reality": reality,
            "content_sha256": content_sha,
            "content_size": content_size,
            "start_time": start,
            "end_time": end,
            "reason": reason,
        }

        return ObservationReceipt(
            receipt_id=receipt_id,
            agent_id=self.agent_id,
            operation=operation,
            target_resource=str(target_resource) if target_resource else "",
            requested_capability=params.get("requested_capability", operation),
            execution_mode="REAL_READ" if operation in {"filesystem.read", "read"} else "OBSERVED",
            start_time=start,
            end_time=end,
            status=status,
            reality=reality,
            reason=reason,
            content_sha256=content_sha,
            content_size=content_size,
            content_preview=content_preview,
            evidence_digest=_digest(evidence_payload),
            provenance=["bounded-agent-runtime", "real-filesystem-operation"],
        )
