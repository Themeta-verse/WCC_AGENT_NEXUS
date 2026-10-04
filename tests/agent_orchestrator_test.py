"""Deterministic offline unit tests for NEXUS Multi-Agent Orchestrator.

These tests run completely offline with 0 network calls, 0 cloud API keys, and 0 dependencies
outside the standard library.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.agent_artifacts import (
    ExecutionResultArtifact,
    PatchArtifact,
    PlanArtifact,
    ROLE_DEBUGGER,
    ROLE_IMPLEMENTER,
    ROLE_PLANNER,
    ROLE_REVIEWER,
    ROLE_SECURITY_REVIEWER,
    ROLE_TEST_AUTHOR,
    ROLE_VERIFIER,
    TestSuiteArtifact,
)
from runtime.agent_orchestrator import (
    AgentOrchestrator,
    OrchestrationResult,
    TaskSpec,
)
from runtime.model_router import (
    MockModelAdapter,
    ModelRouter,
    TASK_CODE_REVIEW,
    TASK_DEBUGGING,
    TASK_IMPLEMENTATION,
    TASK_PLANNING,
    TASK_SECURITY_REVIEW,
    TASK_TESTING,
    TASK_VERIFICATION,
)


class AgentOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.default_mock = MockModelAdapter(
            default_response='{"status": "ok", "approved": true, "score": 9, "passed": true}',
            default_model="mock-default-model",
        )
        self.default_mock.name = "default_mock"

        self.router = ModelRouter(
            providers=[self.default_mock],
            allow_mock_fallback=True,
        )

    def test_full_successful_orchestration_flow(self) -> None:
        """1. Test full planner → implementer → tester → reviewer → security → verifier pipeline."""
        orchestrator = AgentOrchestrator(router=self.router, max_retries=3)
        task = TaskSpec(
            task_id="task-success-01",
            description="Add math utilities module",
            target_files=["src/math_utils.py"],
        )

        result = orchestrator.execute(task)

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.attempts, 1)
        self.assertIsNotNone(result.plan)
        self.assertIsNotNone(result.patch)
        self.assertIsNotNone(result.test_suite)
        self.assertIsNotNone(result.review)
        self.assertIsNotNone(result.security_audit)
        self.assertIsNotNone(result.verification)

        # Verify lineage
        self.assertEqual(result.patch.parent_artifact_id, result.plan.artifact_id)
        self.assertEqual(result.test_suite.parent_artifact_id, result.patch.artifact_id)
        self.assertEqual(result.review.parent_artifact_id, result.patch.artifact_id)
        self.assertEqual(result.security_audit.parent_artifact_id, result.patch.artifact_id)
        self.assertEqual(result.verification.parent_artifact_id, result.patch.artifact_id)

        # Verify role assignments recorded
        self.assertIn(ROLE_PLANNER, result.role_assignments)
        self.assertIn(ROLE_IMPLEMENTER, result.role_assignments)
        self.assertIn(ROLE_TEST_AUTHOR, result.role_assignments)
        self.assertIn(ROLE_REVIEWER, result.role_assignments)
        self.assertIn(ROLE_SECURITY_REVIEWER, result.role_assignments)
        self.assertIn(ROLE_VERIFIER, result.role_assignments)

    def test_reviewer_rejection_flow(self) -> None:
        """2. Test that a rejected code review halts verification and triggers debugger."""
        # Provider that returns code review rejection
        rejecting_mock = MockModelAdapter(
            default_response='{"approved": false, "score": 3, "comments": ["Code lacks type annotations"]}',
            default_model="reviewer-strict",
        )
        rejecting_mock.name = "rejecting_mock"
        self.router.register_provider(rejecting_mock)
        self.router.set_task_policy(TASK_CODE_REVIEW, ["rejecting_mock"])

        orchestrator = AgentOrchestrator(router=self.router, max_retries=1)
        task = TaskSpec(
            task_id="task-review-reject",
            description="Implement complex algorithm",
        )
        result = orchestrator.execute(task)

        # Independent verification must fail because review was rejected
        self.assertEqual(result.status, "MAX_RETRIES_EXCEEDED")
        self.assertFalse(result.verification.passed)
        self.assertFalse(result.verification.checks["review_approved"])
        self.assertEqual(len(result.diagnostics), 1)

    def test_security_audit_failure_flow(self) -> None:
        """3. Test that security findings fail the verification gate deterministically."""
        security_fail_mock = MockModelAdapter(
            default_response='{"passed": false, "secret_findings": ["AWS key in code"], "risk_level": "HIGH"}',
            default_model="security-scanner",
        )
        security_fail_mock.name = "sec_fail_mock"
        self.router.register_provider(security_fail_mock)
        self.router.set_task_policy(TASK_SECURITY_REVIEW, ["sec_fail_mock"])

        orchestrator = AgentOrchestrator(router=self.router, max_retries=1)
        task = TaskSpec(
            task_id="task-sec-reject",
            description="Implement cloud upload",
        )
        result = orchestrator.execute(task)

        self.assertFalse(result.verification.passed)
        self.assertFalse(result.verification.checks["security_passed"])
        self.assertEqual(result.status, "MAX_RETRIES_EXCEEDED")

    def test_failure_diagnostic_debugger_and_retry_flow(self) -> None:
        """4. Test failure on attempt 1 leading to diagnostic, debugger invocation, and success on attempt 2."""
        call_count = {"test_runs": 0}

        def dynamic_mock_runner(patch: PatchArtifact, tests: TestSuiteArtifact) -> ExecutionResultArtifact:
            call_count["test_runs"] += 1
            if call_count["test_runs"] == 1:
                # Attempt 1 fails
                return ExecutionResultArtifact(
                    task_id=patch.task_id,
                    exit_code=1,
                    stdout="AssertionError: 2 != 3",
                    stderr="Traceback...",
                    tests_run=1,
                    tests_passed=0,
                    tests_failed=1,
                    model_generated=False,
                )
            # Attempt 2 passes
            return ExecutionResultArtifact(
                task_id=patch.task_id,
                exit_code=0,
                stdout="OK",
                tests_run=1,
                tests_passed=1,
                tests_failed=0,
                model_generated=False,
            )

        orchestrator = AgentOrchestrator(
            router=self.router,
            max_retries=3,
            execution_runner=dynamic_mock_runner,
        )

        task = TaskSpec(
            task_id="task-retry-01",
            description="Fix arithmetic logic",
        )
        result = orchestrator.execute(task)

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].attempt_number, 1)
        # Attempt 2 patch was linked to diagnostic
        self.assertEqual(result.patch.parent_artifact_id, result.diagnostics[0].artifact_id)
        self.assertTrue(result.verification.passed)

    def test_bounded_retry_behavior_max_exceeded(self) -> None:
        """5. Test that retry loop respects configurable max_retries limit."""
        def always_failing_runner(patch: PatchArtifact, tests: TestSuiteArtifact) -> ExecutionResultArtifact:
            return ExecutionResultArtifact(
                task_id=patch.task_id,
                exit_code=1,
                tests_run=1,
                tests_failed=1,
                model_generated=False,
            )

        orchestrator = AgentOrchestrator(
            router=self.router,
            max_retries=3,
            execution_runner=always_failing_runner,
        )

        task = TaskSpec(
            task_id="task-bounded-retry",
            description="Impossible task",
        )
        result = orchestrator.execute(task)

        self.assertEqual(result.status, "MAX_RETRIES_EXCEEDED")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(result.diagnostics), 3)
        self.assertFalse(result.verification.passed)
        self.assertIn("maximum retry budget", result.error)

    def test_provider_model_selection_through_router(self) -> None:
        """6. Test that specialized roles route to their respective configured providers and models."""
        planner_mock = MockModelAdapter(
            default_response='{"objective": "Plan", "steps": ["s1"]}',
            default_model="qwen-coder-32b",
        )
        planner_mock.name = "planner_provider"

        reviewer_mock = MockModelAdapter(
            default_response='{"approved": true, "score": 9}',
            default_model="claude-style-reviewer",
        )
        reviewer_mock.name = "reviewer_provider"

        self.router.register_provider(planner_mock)
        self.router.register_provider(reviewer_mock)

        self.router.set_task_policy(TASK_PLANNING, ["planner_provider"])
        self.router.set_task_policy(TASK_CODE_REVIEW, ["reviewer_provider"])

        orchestrator = AgentOrchestrator(router=self.router, max_retries=1)
        task = TaskSpec(task_id="task-router-test", description="Routing test")
        result = orchestrator.execute(task)

        self.assertEqual(result.role_assignments[ROLE_PLANNER]["provider"], "planner_provider")
        self.assertEqual(result.role_assignments[ROLE_PLANNER]["model"], "qwen-coder-32b")
        self.assertEqual(result.role_assignments[ROLE_REVIEWER]["provider"], "reviewer_provider")
        self.assertEqual(result.role_assignments[ROLE_REVIEWER]["model"], "claude-style-reviewer")

    def test_unavailable_provider_clean_handling(self) -> None:
        """7. Test clean error capture when a required provider is unavailable and fallback disabled."""
        failing_mock = MockModelAdapter(should_fail=True)
        failing_mock.name = "failing_provider"

        strict_router = ModelRouter(
            providers=[failing_mock],
            allow_mock_fallback=False,
        )
        strict_router.set_task_policy(TASK_PLANNING, ["failing_provider"])

        orchestrator = AgentOrchestrator(router=strict_router, max_retries=1)
        task = TaskSpec(task_id="task-unavail", description="Test failover")
        result = orchestrator.execute(task)

        self.assertEqual(result.status, "PROVIDER_ERROR")
        self.assertIsNotNone(result.error)
        self.assertIn("failing_provider", result.error)

    def test_truth_boundary_enforcement(self) -> None:
        """8. Strict Truth Boundary Invariant test:
        - All model responses remain INFERRED and untrusted.
        - An agent claiming 'VERIFIED' is rejected if independent criteria fail.
        """
        # Verifier agent mock that claims everything is verified even if reviews failed
        hallucinating_verifier = MockModelAdapter(
            default_response='{"status": "VERIFIED", "passed": true, "summary": "Trust me, it works"}',
            default_model="hallucinating-model",
        )
        hallucinating_verifier.name = "hallucinating_verifier"
        self.router.register_provider(hallucinating_verifier)
        self.router.set_task_policy(TASK_VERIFICATION, ["hallucinating_verifier"])

        # Reviewer fails
        rejecting_review = MockModelAdapter(
            default_response='{"approved": false, "score": 2}',
            default_model="strict-reviewer",
        )
        rejecting_review.name = "rejecting_review"
        self.router.register_provider(rejecting_review)
        self.router.set_task_policy(TASK_CODE_REVIEW, ["rejecting_review"])

        orchestrator = AgentOrchestrator(router=self.router, max_retries=1)
        task = TaskSpec(task_id="task-truth-test", description="Verify truth boundary")
        result = orchestrator.execute(task)

        # Independent verification check must override model claims
        self.assertFalse(result.verification.passed)
        self.assertEqual(result.verification.status, "FAILED")
        self.assertEqual(result.verification.reality, "INFERRED")
        self.assertTrue(result.verification.untrusted)

        # All model-generated artifacts must have reality='INFERRED' and untrusted=True
        for art in result.artifacts:
            if art.model_generated:
                self.assertEqual(art.reality, "INFERRED")
                self.assertTrue(art.untrusted)

    def test_no_arbitrary_code_execution(self) -> None:
        """9. Test that orchestrator does not execute arbitrary patch code directly."""
        hazardous_patch = MockModelAdapter(
            default_response='{"files": {"src/exploit.py": "raise SystemExit(99)"}, "commit_message": "test"}',
            default_model="hazardous-coder",
        )
        hazardous_patch.name = "hazardous_patch"
        self.router.register_provider(hazardous_patch)
        self.router.set_task_policy(TASK_IMPLEMENTATION, ["hazardous_patch"])

        orchestrator = AgentOrchestrator(router=self.router, max_retries=1)
        task = TaskSpec(task_id="task-safety-test", description="Ensure no execution")

        # Must complete without SystemExit or executing the exploit
        result = orchestrator.execute(task)
        self.assertIsNotNone(result.patch)
        self.assertIn("src/exploit.py", result.patch.files)

    def test_deterministic_orchestration_state_and_serialization(self) -> None:
        """10. Test that complete orchestration result is fully serializable and inspectable."""
        orchestrator = AgentOrchestrator(router=self.router, max_retries=2)
        task = TaskSpec(task_id="task-state-serial", description="State serial test")
        result = orchestrator.execute(task)

        data = result.to_dict()
        self.assertEqual(data["task_id"], "task-state-serial")
        self.assertEqual(data["status"], "SUCCESS")
        self.assertIn("plan", data)
        self.assertIn("patch", data)
        self.assertIn("verification", data)
        self.assertGreaterEqual(data["artifacts_count"], 5)

        # Valid JSON serialization
        json_output = json.dumps(data, indent=2)
        self.assertIn('"task-state-serial"', json_output)


def run_tests() -> bool:
    suite = unittest.TestLoader().loadTestsFromTestCase(AgentOrchestratorTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    if not success:
        raise SystemExit(1)
