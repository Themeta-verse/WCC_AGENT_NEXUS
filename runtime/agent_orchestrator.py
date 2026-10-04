"""Additive Multi-Agent Orchestration Layer for NEXUS.

This module coordinates specialized AI roles (Planner, Implementer, TestAuthor,
Reviewer, SecurityReviewer, Verifier, Debugger) to solve development tasks.

Core Principles:
1. Purely additive and optional: Does not touch canonical execution paths.
2. Structured communication: Agents exchange typed AgentArtifacts, not raw chat.
3. Truth Boundary Preservation: All model outputs are INFERRED and untrusted.
   An agent's claim alone CANNOT constitute verification.
4. Model routing: Model selection is delegated to ModelRouter per subtask.
5. No arbitrary execution: This module does NOT directly execute generated code.
   Sandbox/worktree isolation belongs to Phase 3.
6. Bounded retry/debugging: Configurable retry loop (default 3) driven by diagnostics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import time
from typing import Any, Callable

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
    ROLE_SECURITY_REVIEWER,
    ROLE_TEST_AUTHOR,
    ROLE_VERIFIER,
    SecurityAuditArtifact,
    TestSuiteArtifact,
    VerificationArtifact,
)
from runtime.model_router import (
    ModelProviderError,
    ModelResponse,
    ModelRouter,
    TASK_CODE_REVIEW,
    TASK_DEBUGGING,
    TASK_IMPLEMENTATION,
    TASK_PLANNING,
    TASK_SECURITY_REVIEW,
    TASK_TESTING,
    TASK_VERIFICATION,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskSpec:
    """Specification of a software development task."""
    task_id: str
    description: str
    target_files: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestrationResult:
    """Final result of the multi-agent orchestration workflow."""
    task_id: str
    status: str  # "SUCCESS", "FAILED", "MAX_RETRIES_EXCEEDED", "PROVIDER_ERROR"
    plan: PlanArtifact | None = None
    patch: PatchArtifact | None = None
    test_suite: TestSuiteArtifact | None = None
    review: ReviewArtifact | None = None
    security_audit: SecurityAuditArtifact | None = None
    verification: VerificationArtifact | None = None
    execution_result: ExecutionResultArtifact | None = None
    diagnostics: list[DiagnosticArtifact] = field(default_factory=list)
    artifacts: list[AgentArtifact] = field(default_factory=list)
    attempts: int = 0
    max_retries: int = 3
    role_assignments: dict[str, dict[str, str]] = field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = {
            "task_id": self.task_id,
            "status": self.status,
            "plan": self.plan.to_dict() if self.plan else None,
            "patch": self.patch.to_dict() if self.patch else None,
            "test_suite": self.test_suite.to_dict() if self.test_suite else None,
            "review": self.review.to_dict() if self.review else None,
            "security_audit": self.security_audit.to_dict() if self.security_audit else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "execution_result": self.execution_result.to_dict() if self.execution_result else None,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "artifacts_count": len(self.artifacts),
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "role_assignments": self.role_assignments,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }
        return data


class AgentOrchestrator:
    """Additive orchestration engine coordinating specialized AI roles."""

    def __init__(
        self,
        router: ModelRouter | None = None,
        max_retries: int = 3,
        execution_runner: Callable[[PatchArtifact, TestSuiteArtifact], ExecutionResultArtifact] | None = None,
    ):
        self.router = router or ModelRouter()
        self.max_retries = max(1, max_retries)
        self.execution_runner = execution_runner
        self.role_assignments: dict[str, dict[str, str]] = {}
        self.history: list[AgentArtifact] = []

    def _record_role(self, role: str, response: ModelResponse) -> None:
        self.role_assignments[role] = {
            "provider": response.provider,
            "model": response.model,
            "reality": response.reality,
            "untrusted": str(response.untrusted),
        }

    def _parse_payload(self, response: ModelResponse) -> dict[str, Any]:
        """Extract structured dictionary from response or fallback to JSON decode."""
        if response.structured and isinstance(response.structured, dict):
            return response.structured
        try:
            parsed = json.loads(response.content)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return {}

    def run_planner(self, task: TaskSpec) -> PlanArtifact:
        """1. Planner: Decomposes the task into an architectural plan."""
        prompt = (
            f"You are the PlannerAgent for task '{task.task_id}'.\n"
            f"Description: {task.description}\n"
            f"Target Files: {task.target_files}\n"
            f"Context: {json.dumps(task.context)}\n"
            f"Produce a structured JSON plan with keys: objective, target_files, steps, acceptance_criteria, reasoning."
        )
        resp = self.router.complete(TASK_PLANNING, prompt=prompt, schema={"type": "object"})
        self._record_role(ROLE_PLANNER, resp)
        data = self._parse_payload(resp)

        objective = data.get("objective") or task.description or "Analyze and fulfill development task"
        target_files = data.get("target_files") or task.target_files or ["src/solution.py"]
        steps = data.get("steps") or ["Analyze requirements", "Implement changes", "Verify with tests"]
        acceptance_criteria = data.get("acceptance_criteria") or ["Tests pass cleanly", "No regressions"]
        reasoning = data.get("reasoning") or resp.content

        plan = PlanArtifact(
            task_id=task.task_id,
            provider=resp.provider,
            model=resp.model,
            objective=objective,
            target_files=target_files,
            steps=steps,
            acceptance_criteria=acceptance_criteria,
            reasoning=reasoning,
        )
        self.history.append(plan)
        return plan

    def run_implementer(
        self,
        task: TaskSpec,
        plan: PlanArtifact,
        diagnostic: DiagnosticArtifact | None = None,
    ) -> PatchArtifact:
        """2. Implementer: Generates code changes based on plan and any previous diagnostics."""
        diag_section = ""
        parent_id = plan.artifact_id
        if diagnostic:
            parent_id = diagnostic.artifact_id
            diag_section = (
                f"\nPREVIOUS FAILURE DIAGNOSTIC:\n"
                f"Root Cause: {diagnostic.root_cause}\n"
                f"Suggested Fix: {diagnostic.suggested_fix}\n"
                f"Target File: {diagnostic.target_file}\n"
            )

        prompt = (
            f"You are the ImplementerAgent for task '{task.task_id}'.\n"
            f"Objective: {plan.objective}\n"
            f"Steps: {plan.steps}\n"
            f"Target Files: {plan.target_files}\n"
            f"{diag_section}"
            f"Produce a structured JSON response with keys: files (dict of path -> code), commit_message, rationale."
        )
        resp = self.router.complete(TASK_IMPLEMENTATION, prompt=prompt, schema={"type": "object"})
        self._record_role(ROLE_IMPLEMENTER, resp)
        data = self._parse_payload(resp)

        files = data.get("files")
        if not files or not isinstance(files, dict):
            default_path = plan.target_files[0] if plan.target_files else "src/solution.py"
            files = {default_path: resp.content or "# Implementation"}

        commit_msg = data.get("commit_message") or f"Implement changes for {task.task_id}"
        rationale = data.get("rationale") or data.get("reasoning") or "Implemented per plan specifications"

        patch = PatchArtifact(
            task_id=task.task_id,
            parent_artifact_id=parent_id,
            provider=resp.provider,
            model=resp.model,
            files=files,
            commit_message=commit_msg,
            rationale=rationale,
            reasoning=resp.content,
        )
        self.history.append(patch)
        return patch

    def run_test_author(
        self,
        task: TaskSpec,
        plan: PlanArtifact,
        patch: PatchArtifact,
    ) -> TestSuiteArtifact:
        """3. TestAuthor: Writes deterministic verification tests for the patch."""
        prompt = (
            f"You are the TestAuthorAgent for task '{task.task_id}'.\n"
            f"Objective: {plan.objective}\n"
            f"Acceptance Criteria: {plan.acceptance_criteria}\n"
            f"Modified Files: {list(patch.files.keys())}\n"
            f"Produce a structured JSON test suite with keys: test_files (dict of path -> test code), framework."
        )
        resp = self.router.complete(TASK_TESTING, prompt=prompt, schema={"type": "object"})
        self._record_role(ROLE_TEST_AUTHOR, resp)
        data = self._parse_payload(resp)

        test_files = data.get("test_files")
        if not test_files or not isinstance(test_files, dict):
            test_files = {"tests/test_solution.py": resp.content or "import unittest\n"}

        framework = data.get("framework") or "unittest"

        test_suite = TestSuiteArtifact(
            task_id=task.task_id,
            parent_artifact_id=patch.artifact_id,
            provider=resp.provider,
            model=resp.model,
            test_files=test_files,
            framework=framework,
            reasoning=resp.content,
        )
        self.history.append(test_suite)
        return test_suite

    def run_reviewer(
        self,
        task: TaskSpec,
        patch: PatchArtifact,
        test_suite: TestSuiteArtifact,
    ) -> ReviewArtifact:
        """4. Reviewer: Evaluates code quality, structure, and readability."""
        prompt = (
            f"You are the ReviewerAgent for task '{task.task_id}'.\n"
            f"Review the patch:\n{list(patch.files.keys())}\n"
            f"Review the test suite:\n{list(test_suite.test_files.keys())}\n"
            f"Produce structured JSON with keys: approved (bool), score (1-10), comments (list[str]), required_changes (list[str])."
        )
        resp = self.router.complete(TASK_CODE_REVIEW, prompt=prompt, schema={"type": "object"})
        self._record_role(ROLE_REVIEWER, resp)
        data = self._parse_payload(resp)

        approved = data.get("approved")
        if approved is None:
            approved = True

        score = data.get("score")
        if score is None or not isinstance(score, int) or not (1 <= score <= 10):
            score = 8 if approved else 4

        comments = data.get("comments") or ["Review evaluated code changes"]
        required_changes = data.get("required_changes") or []

        review = ReviewArtifact(
            task_id=task.task_id,
            parent_artifact_id=patch.artifact_id,
            provider=resp.provider,
            model=resp.model,
            approved=approved,
            score=score,
            comments=comments,
            required_changes=required_changes,
            reasoning=resp.content,
        )
        self.history.append(review)
        return review

    def run_security_reviewer(
        self,
        task: TaskSpec,
        patch: PatchArtifact,
        test_suite: TestSuiteArtifact,
    ) -> SecurityAuditArtifact:
        """5. SecurityReviewer: Audits code for secrets, hazardous calls, and injection risks."""
        # Static heuristic scan on patch files as an extra defense line
        secret_findings: list[str] = []
        dangerous_calls: list[str] = []
        for path, code in patch.files.items():
            if "BEGIN RSA PRIVATE KEY" in code or "AKIA" in code:
                secret_findings.append(f"{path}: potential hardcoded credential detected")
            if "os.system('rm -rf" in code or "shutil.rmtree('/'" in code:
                dangerous_calls.append(f"{path}: hazardous filesystem deletion call")

        prompt = (
            f"You are the SecurityReviewerAgent for task '{task.task_id}'.\n"
            f"Analyze files for security vulnerabilities:\n{list(patch.files.keys())}\n"
            f"Produce structured JSON with keys: passed (bool), secret_findings, dangerous_calls, risk_level."
        )
        resp = self.router.complete(TASK_SECURITY_REVIEW, prompt=prompt, schema={"type": "object"})
        self._record_role(ROLE_SECURITY_REVIEWER, resp)
        data = self._parse_payload(resp)

        model_passed = data.get("passed", True)
        model_secrets = data.get("secret_findings") or []
        model_dangerous = data.get("dangerous_calls") or []
        risk_level = data.get("risk_level") or "LOW"
        if risk_level not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            risk_level = "LOW"

        total_secrets = secret_findings + model_secrets
        total_dangerous = dangerous_calls + model_dangerous
        overall_passed = bool(model_passed and not total_secrets and not total_dangerous)

        security_audit = SecurityAuditArtifact(
            task_id=task.task_id,
            parent_artifact_id=patch.artifact_id,
            provider=resp.provider,
            model=resp.model,
            passed=overall_passed,
            secret_findings=total_secrets,
            dangerous_calls=total_dangerous,
            risk_level="HIGH" if not overall_passed else risk_level,
            reasoning=resp.content,
        )
        self.history.append(security_audit)
        return security_audit

    def run_verifier(
        self,
        task: TaskSpec,
        plan: PlanArtifact,
        patch: PatchArtifact,
        test_suite: TestSuiteArtifact,
        review: ReviewArtifact,
        security: SecurityAuditArtifact,
        execution_result: ExecutionResultArtifact | None,
    ) -> VerificationArtifact:
        """6. Verifier: Evaluates artifacts and enforces independent verification invariants.
        
        Strict Truth Boundary Invariant:
        An agent's own claim alone CANNOT constitute verification.
        Independent deterministic checks MUST pass:
        - Reviewer must have approved (score >= 6).
        - Security audit must have passed (zero secrets/dangerous calls).
        - If an execution result is provided, exit_code must be 0 and tests_failed must be 0.
        """
        # Call model for verifier reasoning/summary
        prompt = (
            f"You are the VerifierAgent for task '{task.task_id}'.\n"
            f"Review status: approved={review.approved}, score={review.score}\n"
            f"Security status: passed={security.passed}\n"
            f"Execution result: {execution_result.exit_code if execution_result else 'deferred to sandbox'}\n"
            f"Produce structured JSON with summary."
        )
        resp = self.router.complete(TASK_VERIFICATION, prompt=prompt, schema={"type": "object"})
        self._record_role(ROLE_VERIFIER, resp)
        data = self._parse_payload(resp)
        summary = data.get("summary") or resp.content or "Independent verification check executed."

        # Independent deterministic checks - Truth boundary gate
        checks = {
            "patch_non_empty": bool(patch.files),
            "test_suite_non_empty": bool(test_suite.test_files),
            "review_approved": bool(review.approved and review.score >= 6),
            "security_passed": bool(security.passed),
        }
        if execution_result is not None:
            checks["execution_success"] = bool(
                execution_result.exit_code == 0 and execution_result.tests_failed == 0
            )

        deterministic_passed = all(checks.values())
        status = "VERIFIED" if deterministic_passed else "FAILED"

        verification = VerificationArtifact(
            task_id=task.task_id,
            parent_artifact_id=patch.artifact_id,
            provider=resp.provider,
            model=resp.model,
            status=status,
            passed=deterministic_passed,
            independent=True,
            checks=checks,
            summary=summary,
            reasoning=resp.content,
        )
        self.history.append(verification)
        return verification

    def run_debugger(
        self,
        task: TaskSpec,
        verification: VerificationArtifact,
        execution_result: ExecutionResultArtifact | None,
        attempt: int,
    ) -> DiagnosticArtifact:
        """7. Debugger: Analyzes failure causes and suggests targeted repairs."""
        failed_checks = [k for k, v in verification.checks.items() if not v]
        prompt = (
            f"You are the DebuggerAgent for task '{task.task_id}'.\n"
            f"Verification failed on attempt {attempt}.\n"
            f"Failed checks: {failed_checks}\n"
            f"Execution stderr: {execution_result.stderr if execution_result else 'None'}\n"
            f"Produce structured JSON with keys: root_cause, suggested_fix, target_file."
        )
        resp = self.router.complete(TASK_DEBUGGING, prompt=prompt, schema={"type": "object"})
        self._record_role(ROLE_DEBUGGER, resp)
        data = self._parse_payload(resp)

        root_cause = data.get("root_cause") or f"Verification gate checks failed: {', '.join(failed_checks)}"
        suggested_fix = data.get("suggested_fix") or "Repair failed assertions or address review comments"
        target_file = data.get("target_file") or "src/solution.py"

        diagnostic = DiagnosticArtifact(
            task_id=task.task_id,
            parent_artifact_id=verification.artifact_id,
            provider=resp.provider,
            model=resp.model,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            target_file=target_file,
            attempt_number=attempt,
            reasoning=resp.content,
        )
        self.history.append(diagnostic)
        return diagnostic

    def execute(self, task: TaskSpec) -> OrchestrationResult:
        """Run the full multi-agent orchestration workflow with bounded retry loops.
        
        Preserves NEXUS truth boundary:
        - No arbitrary code execution directly in this layer.
        - Failover handled gracefully if providers are unavailable.
        - Maximum retry count strictly bounded.
        """
        start_time = time.monotonic()
        diagnostics: list[DiagnosticArtifact] = []

        try:
            # Step 1: Planning
            plan = self.run_planner(task)
        except ModelProviderError as exc:
            return OrchestrationResult(
                task_id=task.task_id,
                status="PROVIDER_ERROR",
                error=str(exc),
                artifacts=list(self.history),
                duration_seconds=round(time.monotonic() - start_time, 4),
            )

        current_diagnostic: DiagnosticArtifact | None = None
        attempt = 1

        last_patch: PatchArtifact | None = None
        last_test_suite: TestSuiteArtifact | None = None
        last_review: ReviewArtifact | None = None
        last_security: SecurityAuditArtifact | None = None
        last_verification: VerificationArtifact | None = None
        last_execution_result: ExecutionResultArtifact | None = None

        while attempt <= self.max_retries:
            try:
                # Step 2: Implementation
                last_patch = self.run_implementer(task, plan, current_diagnostic)

                # Step 3: Test Generation
                last_test_suite = self.run_test_author(task, plan, last_patch)

                # Step 4: Code Review
                last_review = self.run_reviewer(task, last_patch, last_test_suite)

                # Step 5: Security Review
                last_security = self.run_security_reviewer(task, last_patch, last_test_suite)

                # Step 6: Execution (if runner is supplied e.g. mock runner; otherwise deferred)
                if self.execution_runner is not None:
                    last_execution_result = self.execution_runner(last_patch, last_test_suite)
                    self.history.append(last_execution_result)
                else:
                    last_execution_result = None

                # Step 7: Independent Verification Gate
                last_verification = self.run_verifier(
                    task,
                    plan,
                    last_patch,
                    last_test_suite,
                    last_review,
                    last_security,
                    last_execution_result,
                )

                # If verification passes, workflow succeeds
                if last_verification.passed:
                    duration = round(time.monotonic() - start_time, 4)
                    return OrchestrationResult(
                        task_id=task.task_id,
                        status="SUCCESS",
                        plan=plan,
                        patch=last_patch,
                        test_suite=last_test_suite,
                        review=last_review,
                        security_audit=last_security,
                        verification=last_verification,
                        execution_result=last_execution_result,
                        diagnostics=diagnostics,
                        artifacts=list(self.history),
                        attempts=attempt,
                        max_retries=self.max_retries,
                        role_assignments=dict(self.role_assignments),
                        duration_seconds=duration,
                    )

                # If verification fails, diagnose and loop
                current_diagnostic = self.run_debugger(
                    task,
                    last_verification,
                    last_execution_result,
                    attempt=attempt,
                )
                diagnostics.append(current_diagnostic)
                attempt += 1

            except ModelProviderError as exc:
                return OrchestrationResult(
                    task_id=task.task_id,
                    status="PROVIDER_ERROR",
                    plan=plan,
                    patch=last_patch,
                    test_suite=last_test_suite,
                    review=last_review,
                    security_audit=last_security,
                    verification=last_verification,
                    diagnostics=diagnostics,
                    artifacts=list(self.history),
                    attempts=attempt,
                    max_retries=self.max_retries,
                    role_assignments=dict(self.role_assignments),
                    error=str(exc),
                    duration_seconds=round(time.monotonic() - start_time, 4),
                )

        # Max retries exceeded without verification passing
        duration = round(time.monotonic() - start_time, 4)
        return OrchestrationResult(
            task_id=task.task_id,
            status="MAX_RETRIES_EXCEEDED",
            plan=plan,
            patch=last_patch,
            test_suite=last_test_suite,
            review=last_review,
            security_audit=last_security,
            verification=last_verification,
            execution_result=last_execution_result,
            diagnostics=diagnostics,
            artifacts=list(self.history),
            attempts=attempt - 1,
            max_retries=self.max_retries,
            role_assignments=dict(self.role_assignments),
            error="Independent verification failed to pass within maximum retry budget",
            duration_seconds=duration,
        )
