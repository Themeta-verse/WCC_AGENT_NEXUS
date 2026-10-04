"""Offline unit tests for NEXUS Agent Artifact Schemas and Truth Boundary Enforcement.

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
    AgentArtifact,
    ArtifactValidationError,
    DiagnosticArtifact,
    ExecutionResultArtifact,
    PatchArtifact,
    PlanArtifact,
    ReviewArtifact,
    ROLE_DEBUGGER,
    ROLE_IMPLEMENTER,
    ROLE_PLANNER,
    ROLE_REVIEWER,
    ROLE_SANDBOX,
    ROLE_SECURITY_REVIEWER,
    ROLE_TEST_AUTHOR,
    ROLE_VERIFIER,
    SecurityAuditArtifact,
    TestSuiteArtifact,
    VerificationArtifact,
    artifact_from_dict,
    artifact_from_json,
)


class AgentArtifactsTest(unittest.TestCase):
    def test_artifact_creation(self) -> None:
        """1. Test typed creation of all 8 artifact variants."""
        plan = PlanArtifact(
            task_id="task-001",
            objective="Build factorial module",
            target_files=["src/math.py"],
            steps=["Write factorial function"],
            acceptance_criteria=["Handles 0 and positive ints"],
        )
        self.assertEqual(plan.agent_role, ROLE_PLANNER)
        self.assertEqual(plan.artifact_type, "plan")
        self.assertEqual(plan.reality, "INFERRED")
        self.assertTrue(plan.untrusted)

        patch = PatchArtifact(
            task_id="task-001",
            files={"src/math.py": "def factorial(n): return 1 if n <= 1 else n * factorial(n - 1)"},
            commit_message="Implement factorial",
            parent_artifact_id=plan.artifact_id,
        )
        self.assertEqual(patch.agent_role, ROLE_IMPLEMENTER)
        self.assertEqual(patch.artifact_type, "patch")
        self.assertEqual(patch.parent_artifact_id, plan.artifact_id)

        test_suite = TestSuiteArtifact(
            task_id="task-001",
            test_files={"tests/test_math.py": "def test_fac(): assert factorial(5) == 120"},
            framework="unittest",
            parent_artifact_id=patch.artifact_id,
        )
        self.assertEqual(test_suite.agent_role, ROLE_TEST_AUTHOR)
        self.assertEqual(test_suite.artifact_type, "test_suite")

        review = ReviewArtifact(
            task_id="task-001",
            approved=True,
            score=9,
            comments=["Clean recursive implementation"],
            parent_artifact_id=patch.artifact_id,
        )
        self.assertEqual(review.agent_role, ROLE_REVIEWER)
        self.assertEqual(review.score, 9)

        security = SecurityAuditArtifact(
            task_id="task-001",
            passed=True,
            risk_level="LOW",
            parent_artifact_id=patch.artifact_id,
        )
        self.assertEqual(security.agent_role, ROLE_SECURITY_REVIEWER)
        self.assertTrue(security.passed)

        exec_res = ExecutionResultArtifact(
            task_id="task-001",
            exit_code=0,
            tests_run=5,
            tests_passed=5,
            tests_failed=0,
            model_generated=False,
        )
        self.assertEqual(exec_res.agent_role, ROLE_SANDBOX)
        self.assertEqual(exec_res.reality, "OBSERVED")
        self.assertFalse(exec_res.untrusted)

        diag = DiagnosticArtifact(
            task_id="task-001",
            root_cause="Recursion depth exceeded for negative inputs",
            suggested_fix="Add validation for n < 0",
            attempt_number=1,
        )
        self.assertEqual(diag.agent_role, ROLE_DEBUGGER)

        verif = VerificationArtifact(
            task_id="task-001",
            status="VERIFIED",
            passed=True,
            checks={"review_approved": True, "security_passed": True},
        )
        self.assertEqual(verif.agent_role, ROLE_VERIFIER)
        self.assertEqual(verif.status, "VERIFIED")

    def test_serialization_and_deserialization(self) -> None:
        """2. Test round-trip dictionary and JSON serialization and reconstruction."""
        original = PlanArtifact(
            task_id="task-100",
            provider="mock",
            model="mock-v1",
            objective="Add string utils",
            target_files=["src/utils.py"],
            steps=["Step 1", "Step 2"],
            acceptance_criteria=["Tests pass"],
            reasoning="Decomposed per task",
        )

        # Dictionary round-trip
        data_dict = original.to_dict()
        self.assertEqual(data_dict["task_id"], "task-100")
        self.assertEqual(data_dict["artifact_type"], "plan")

        reconstructed_dict = artifact_from_dict(data_dict)
        self.assertIsInstance(reconstructed_dict, PlanArtifact)
        self.assertEqual(reconstructed_dict.objective, "Add string utils")
        self.assertEqual(reconstructed_dict.target_files, ["src/utils.py"])
        self.assertEqual(reconstructed_dict.reality, "INFERRED")

        # JSON round-trip
        json_str = original.to_json(indent=2)
        reconstructed_json = artifact_from_json(json_str)
        self.assertIsInstance(reconstructed_json, PlanArtifact)
        self.assertEqual(reconstructed_json.artifact_id, original.artifact_id)
        self.assertEqual(reconstructed_json.steps, ["Step 1", "Step 2"])

    def test_role_identification(self) -> None:
        """3. Test role identification and rejection of invalid roles."""
        art = AgentArtifact(task_id="task-200", agent_role=ROLE_PLANNER)
        self.assertEqual(art.agent_role, ROLE_PLANNER)

        # Invalid agent role
        with self.assertRaises(ArtifactValidationError):
            AgentArtifact(task_id="task-200", agent_role="invalid_role_xyz")

    def test_provenance_and_metadata(self) -> None:
        """4. Test metadata and provenance preservation across artifacts."""
        patch = PatchArtifact(
            task_id="task-300",
            provider="ollama",
            model="qwen2.5-coder",
            files={"src/app.py": "print('hello')"},
            commit_message="feat: add hello",
            evidence_references=["ref:spec#12"],
            reasoning="Detailed generation reasoning",
        )
        self.assertEqual(patch.provider, "ollama")
        self.assertEqual(patch.model, "qwen2.5-coder")
        self.assertEqual(patch.evidence_references, ["ref:spec#12"])
        self.assertIn("T", patch.timestamp)  # ISO timestamp

    def test_parent_child_relationships(self) -> None:
        """5. Test parent-child lineage tracking between artifacts."""
        plan = PlanArtifact(task_id="task-400", objective="Refactor database")
        patch = PatchArtifact(
            task_id="task-400",
            files={"db.py": "# db update"},
            parent_artifact_id=plan.artifact_id,
        )
        test_suite = TestSuiteArtifact(
            task_id="task-400",
            test_files={"test_db.py": "# test"},
            parent_artifact_id=patch.artifact_id,
        )

        self.assertIsNone(plan.parent_artifact_id)
        self.assertEqual(patch.parent_artifact_id, plan.artifact_id)
        self.assertEqual(test_suite.parent_artifact_id, patch.artifact_id)

    def test_inferred_and_untrusted_enforcement(self) -> None:
        """6. Strict Truth Boundary Invariant test:
        Model-generated artifacts cannot be promoted to OBSERVED or VERIFIED.
        """
        # Attempt to create model artifact claiming OBSERVED reality and trusted status
        plan = PlanArtifact(
            task_id="task-500",
            objective="Test invariant",
            steps=["Analyze requirements"],
            reality="OBSERVED",   # Attempted tamper
            untrusted=False,       # Attempted tamper
        )
        self.assertEqual(plan.reality, "INFERRED")
        self.assertTrue(plan.untrusted)

        # Attempt to create PatchArtifact claiming VERIFIED reality
        patch = PatchArtifact(
            task_id="task-500",
            files={"test.py": "x = 1"},
            reality="VERIFIED",   # Attempted tamper
            untrusted=False,       # Attempted tamper
        )
        self.assertEqual(patch.reality, "INFERRED")
        self.assertTrue(patch.untrusted)

        # Explicit validation check
        plan.validate()
        patch.validate()

        # If manually modified after construction, validate() raises violation
        plan.reality = "OBSERVED"
        with self.assertRaises(ArtifactValidationError):
            plan.validate()

        plan.reality = "INFERRED"
        plan.untrusted = False
        with self.assertRaises(ArtifactValidationError):
            plan.validate()

    def test_invalid_artifact_rejection(self) -> None:
        """7. Test rejection of malformed or inconsistent artifacts."""
        # Empty task_id
        with self.assertRaises(ArtifactValidationError):
            PlanArtifact(task_id="", objective="Objective")

        # Empty objective in PlanArtifact
        with self.assertRaises(ArtifactValidationError):
            PlanArtifact(task_id="task-600", objective="")

        # Empty files in PatchArtifact validation
        empty_patch = PatchArtifact(task_id="task-600", files={})
        with self.assertRaises(ArtifactValidationError):
            empty_patch.validate()

        # Empty test_files in TestSuiteArtifact validation
        empty_tests = TestSuiteArtifact(task_id="task-600", test_files={})
        with self.assertRaises(ArtifactValidationError):
            empty_tests.validate()

        # Invalid review score
        with self.assertRaises(ArtifactValidationError):
            ReviewArtifact(task_id="task-600", score=11)

        # Contradictory security audit: passed=True with active secret findings
        bad_sec = SecurityAuditArtifact(
            task_id="task-600",
            passed=True,
            secret_findings=["AKIA12345: leaked AWS key"],
        )
        with self.assertRaises(ArtifactValidationError):
            bad_sec.validate()

        # Contradictory verification: status=VERIFIED but passed=False
        with self.assertRaises(ArtifactValidationError):
            VerificationArtifact(task_id="task-600", status="INVALID_STATUS")

        bad_verif = VerificationArtifact(task_id="task-600", status="VERIFIED", passed=False)
        with self.assertRaises(ArtifactValidationError):
            bad_verif.validate()

        # Unknown artifact type in deserialization
        with self.assertRaises(ArtifactValidationError):
            artifact_from_dict({"artifact_type": "alien_type", "task_id": "t"})


def run_tests() -> bool:
    suite = unittest.TestLoader().loadTestsFromTestCase(AgentArtifactsTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    if not success:
        raise SystemExit(1)
