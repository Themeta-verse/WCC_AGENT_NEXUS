"""Offline unit tests for NEXUS Governed Workspace and Git Sandbox Runner.

Tests all 12 core requirements:
1. worktree creation
2. worktree isolation
3. authoritative workspace remains untouched
4. path traversal rejection
5. command execution inside sandbox
6. timeout handling
7. stdout/stderr capture
8. non-zero exit handling
9. cleanup
10. task isolation
11. Git HEAD preservation
12. prevention of execution outside the sandbox
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.agent_artifacts import PatchArtifact
from runtime.sandbox_runner import (
    GitSandboxRunner,
    PathTraversalError,
    SandboxConfig,
    SandboxError,
    SandboxExecutionError,
)


def _remove_readonly(func, path, excinfo):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


class SandboxRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        # Create an isolated temporary Git repository
        self.temp_dir = tempfile.mkdtemp(prefix="nexus-sandbox-test-")
        self.repo_root = Path(self.temp_dir).resolve()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Nexus Test"], cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@nexus.local"], cwd=self.repo_root, capture_output=True, check=True)

        # Create initial authoritative files
        self.initial_file = self.repo_root / "app.py"
        self.initial_file.write_text("print('authoritative original')\n", encoding="utf-8")

        subprocess.run(["git", "add", "app.py"], cwd=self.repo_root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial authoritative commit"], cwd=self.repo_root, capture_output=True, check=True)

        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, check=True)
        self.initial_head = proc.stdout.strip()

        self.runner = GitSandboxRunner(repo_root=self.repo_root)

    def tearDown(self) -> None:
        # Teardown any remaining worktrees
        try:
            subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, capture_output=True, check=False)
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, onerror=_remove_readonly, ignore_errors=True)

    def test_1_worktree_creation(self) -> None:
        """1. Test creation of isolated Git worktree under .nexus_product/worktrees/<task_id>."""
        with self.runner.sandbox("task-01") as ctx:
            self.assertTrue(ctx.worktree_path.exists())
            self.assertTrue(ctx.worktree_path.is_dir())
            self.assertEqual(ctx.base_commit, self.initial_head)
            self.assertTrue((ctx.worktree_path / "app.py").exists())
            self.assertEqual(ctx.task_id, "task-01")
            self.assertEqual(ctx.read_file("app.py").strip(), "print('authoritative original')")

    def test_2_worktree_isolation(self) -> None:
        """2. Test that modifications in worktree do NOT propagate to authoritative workspace."""
        with self.runner.sandbox("task-02") as ctx:
            # Overwrite app.py inside sandbox
            ctx.write_file("app.py", "print('modified in worktree')\n")
            self.assertEqual(ctx.read_file("app.py").strip(), "print('modified in worktree')")

            # Check authoritative repo file
            authoritative_content = self.initial_file.read_text(encoding="utf-8").strip()
            self.assertEqual(authoritative_content, "print('authoritative original')")

    def test_3_authoritative_workspace_remains_untouched(self) -> None:
        """3. Test that git status in authoritative repo remains clean after sandbox writes."""
        with self.runner.sandbox("task-03") as ctx:
            ctx.write_file("new_feature.py", "# new code\n")
            ctx.write_file("nested/module.py", "# nested code\n")
            self.assertTrue(ctx.file_exists("new_feature.py"))
            self.assertTrue(ctx.writes_performed)

        # Inspect authoritative workspace git status
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo_root, capture_output=True, text=True, check=True)
        # Only untracked .nexus_product directory may exist (or nothing)
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip() and not line.endswith(".nexus_product/")]
        self.assertEqual(lines, [], "Authoritative workspace must remain pristine")

    def test_4_path_traversal_rejection(self) -> None:
        """4. Test that attempts to read/write/resolve outside worktree are strictly rejected."""
        with self.runner.sandbox("task-04") as ctx:
            with self.assertRaises(PathTraversalError):
                ctx.resolve_path("../../secret.txt")

            with self.assertRaises(PathTraversalError):
                ctx.write_file("../escape.txt", "exploit")

            with self.assertRaises(PathTraversalError):
                ctx.read_file("../../outside.py")

            # Absolute path outside worktree
            with self.assertRaises(PathTraversalError):
                ctx.resolve_path(self.repo_root / "app.py")

        # Invalid task_id with path traversal characters
        with self.assertRaises(PathTraversalError):
            self.runner.create_sandbox("../bad_task_id")

    def test_5_command_execution_inside_sandbox(self) -> None:
        """5. Test executing commands inside the isolated worktree."""
        with self.runner.sandbox("task-05") as ctx:
            script = "import sys\nprint('Execution successful from sandbox')\n"
            ctx.write_file("test_script.py", script)

            result = ctx.execute_command([sys.executable, "test_script.py"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("Execution successful from sandbox", result.stdout)
            self.assertEqual(result.cwd, str(ctx.worktree_path))
            self.assertGreaterEqual(result.duration_seconds, 0.0)

            # Test conversion to ExecutionResultArtifact
            artifact = result.to_execution_artifact("task-05", tests_run=1, tests_passed=1)
            self.assertEqual(artifact.reality, "OBSERVED")
            self.assertFalse(artifact.untrusted)
            self.assertFalse(artifact.model_generated)
            self.assertEqual(artifact.agent_role, "sandbox_runner")

    def test_6_timeout_handling(self) -> None:
        """6. Test enforcement of execution timeout limits."""
        with self.runner.sandbox("task-06") as ctx:
            # Command that attempts to sleep for 5 seconds with 0.5s timeout
            result = ctx.execute_command(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                timeout=0.5,
            )
            self.assertTrue(result.timed_out)
            self.assertEqual(result.exit_code, 124)
            self.assertIn("timed out", result.stderr.lower())
            self.assertLess(result.duration_seconds, 3.0)

    def test_7_stdout_and_stderr_capture(self) -> None:
        """7. Test comprehensive stdout and stderr capture."""
        with self.runner.sandbox("task-07") as ctx:
            cmd = [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('STANDARD_OUT\\n'); sys.stderr.write('STANDARD_ERR\\n')",
            ]
            result = ctx.execute_command(cmd)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("STANDARD_OUT", result.stdout)
            self.assertIn("STANDARD_ERR", result.stderr)

    def test_8_nonzero_exit_handling(self) -> None:
        """8. Test handling of non-zero exit codes."""
        with self.runner.sandbox("task-08") as ctx:
            cmd = [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('Fatal error\\n'); sys.exit(7)",
            ]
            result = ctx.execute_command(cmd)
            self.assertEqual(result.exit_code, 7)
            self.assertIn("Fatal error", result.stderr)
            self.assertFalse(result.timed_out)

    def test_9_cleanup(self) -> None:
        """9. Test deterministic worktree teardown and removal."""
        ctx = self.runner.create_sandbox("task-09")
        worktree_path = ctx.worktree_path
        self.assertTrue(worktree_path.exists())

        # Cleanup
        ctx.cleanup()
        self.assertFalse(worktree_path.exists())

        # Verify git worktree list
        proc = subprocess.run(["git", "worktree", "list"], cwd=self.repo_root, capture_output=True, text=True, check=True)
        self.assertNotIn("task-09", proc.stdout)

    def test_10_task_isolation(self) -> None:
        """10. Test that two concurrent task worktrees are mutually isolated."""
        with self.runner.sandbox("task-10-alpha") as ctx_a:
            with self.runner.sandbox("task-10-beta") as ctx_b:
                ctx_a.write_file("alpha_only.txt", "Alpha data")
                ctx_b.write_file("beta_only.txt", "Beta data")

                self.assertTrue(ctx_a.file_exists("alpha_only.txt"))
                self.assertFalse(ctx_a.file_exists("beta_only.txt"))

                self.assertTrue(ctx_b.file_exists("beta_only.txt"))
                self.assertFalse(ctx_b.file_exists("alpha_only.txt"))

    def test_11_git_head_preservation(self) -> None:
        """11. Test that authoritative repository HEAD commit is unchanged after sandbox activity."""
        with self.runner.sandbox("task-11") as ctx:
            # Commit inside the worktree
            ctx.write_file("sandbox_commit.py", "x = 1")
            subprocess.run(["git", "add", "sandbox_commit.py"], cwd=ctx.worktree_path, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-m", "Worktree commit"], cwd=ctx.worktree_path, capture_output=True, check=True)

        # Inspect authoritative HEAD
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, check=True)
        self.assertEqual(proc.stdout.strip(), self.initial_head)

    def test_12_prevention_of_execution_outside_sandbox(self) -> None:
        """12. Test rejection of execution targets outside the governed sandbox."""
        with self.runner.sandbox("task-12") as ctx:
            # Attempting to execute with cwd set to authoritative repo root
            with self.assertRaises((PathTraversalError, SandboxExecutionError)):
                ctx.execute_command([sys.executable, "-c", "pass"], cwd=self.repo_root)

            # Attempting to execute with cwd set to parent directory
            with self.assertRaises((PathTraversalError, SandboxExecutionError)):
                ctx.execute_command([sys.executable, "-c", "pass"], cwd="../..")


def run_tests() -> bool:
    suite = unittest.TestLoader().loadTestsFromTestCase(SandboxRunnerTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    if not success:
        raise SystemExit(1)
