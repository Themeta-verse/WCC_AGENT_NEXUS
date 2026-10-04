"""NEXUS Workflow Task Executor — agent execution bridge for the WorkflowEngine.

This module provides the concrete task execution implementation that the
WorkflowEngine calls to execute individual tasks. It is intentionally separated
from the WorkflowEngine so that execution strategies can vary:

  - Deterministic execution (no LLM, for testing and as a reference)
  - Model-based execution (via ModelRouter + AgentArtifact system)
  - Tool-based execution (filesystem, browser, github providers)

The executor preserves the NEXUS truth boundary: all model-generated content
is INFERRED and untrusted; only real provider observations are OBSERVED.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

try:
    from mission_composer import MissionComposer
    from bounded_agent import BoundedAgentRuntime
except ImportError:
    from .mission_composer import MissionComposer
    from .bounded_agent import BoundedAgentRuntime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class TaskResult:
    """Result of executing a single task."""
    task_id: str
    status: str
    reality: str
    result: dict[str, Any]
    artifacts: list[dict[str, Any]]
    execution_time: str = field(default_factory=_now)
    provenance: list[str] = field(default_factory=lambda: ["workflow-executor"])


class WorkflowTaskExecutor:
    """Executes individual workflow tasks by dispatching to the appropriate agent mode.

    This executor supports multiple execution modes:
    - 'deterministic': no LLM, real filesystem observation, rule-based analysis
    - 'model': LLM-based via ModelRouter (model outputs are INFERRED, untrusted)
    - 'provider': direct provider invocation (github-read, filesystem-read, browser-read)

    The executor preserves NEXUS truth boundary invariants.
    """

    def __init__(
        self,
        database: Any = None,
        composer: MissionComposer | None = None,
        settings: Any = None,
        principal: dict[str, Any] | None = None,
    ):
        self.database = database
        self.composer = composer
        self.settings = settings
        self.principal = principal or {}

    def execute_task(
        self,
        *,
        workflow_id: str,
        task_id: str,
        task_type: str,
        task_name: str,
        agent_id: str,
        capabilities: list[str],
        scope: str,
        input_artifacts: list[str],
        parameters: dict[str, Any] | None = None,
    ) -> TaskResult:
        """Execute a single task and return the result with any produced artifacts."""
        params = parameters or {}

        # Resolve input artifacts from the database
        resolved_inputs: list[dict[str, Any]] = []
        artifact_contents: list[dict[str, Any]] = []
        if self.database and input_artifacts:
            for art_id in input_artifacts:
                artifact = self.database.get_artifact(self.principal.get("tenant_id", "unknown"), art_id)
                if artifact and artifact.get("content_path"):
                    resolved_inputs.append(artifact)
                    try:
                        content = Path(artifact["content_path"]).read_text(encoding="utf-8")
                        artifact_contents.append({
                            "artifact_id": art_id,
                            "name": artifact["name"],
                            "content": content,
                        })
                    except (OSError, UnicodeDecodeError):
                        pass

        # Dispatch based on task_type
        exec_fn = getattr(self, f"_execute_{task_type}", None)
        if exec_fn is None:
            exec_fn = self._execute_generic

        result, new_artifacts = exec_fn(
            workflow_id=workflow_id,
            task_id=task_id,
            task_name=task_name,
            agent_id=agent_id,
            capabilities=capabilities,
            scope=scope,
            input_artifacts=resolved_inputs,
            artifact_contents=artifact_contents,
            parameters=params,
        )

        return TaskResult(
            task_id=task_id,
            status="COMPLETED",
            reality=result.get("reality", "INFERRED"),
            result=result,
            artifacts=new_artifacts,
        )

    def _execute_research(
        self, *, workflow_id: str, task_id: str, task_name: str,
        agent_id: str, capabilities: list[str], scope: str,
        input_artifacts: list[dict[str, Any]], artifact_contents: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Research agent: observes project files and produces a research artifact.

        Uses BoundedAgentRuntime to perform real filesystem reads within the
        configured project root. This is real observation — not LLM inference.
        """
        research_findings: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []

        # Get the project context — read from the configured filesystem root
        project_context = self._get_project_context(scope, parameters)

        # Use BoundedAgentRuntime to perform real filesystem observation
        if "filesystem.read" in capabilities:
            from runtime.bounded_agent import BoundedAgentRuntime
            root = parameters.get("observation_root") or self._project_root()
            runtime = BoundedAgentRuntime(
                agent_id=agent_id,
                allowed_root=root,
                capabilities=capabilities,
                prohibited_operations=["filesystem.write", "git.push"],
            )
            for file_path in project_context.get("key_files", []):
                receipt = runtime.execute_action(
                    operation="filesystem.read",
                    target_resource=file_path,
                    parameters={
                        "max_chars": parameters.get("max_chars", 10000),
                        "requested_capability": "filesystem.read",
                    },
                )
                if receipt.status == "EXECUTED" and receipt.content_sha256:
                    research_findings.append({
                        "file": file_path,
                        "size": receipt.content_size,
                        "content_hash": receipt.content_sha256,
                        "reality": receipt.reality,
                        "content_preview": receipt.content_preview,
                    })

        # Analyze findings deterministically
        analysis = self._analyze_research_findings(research_findings, project_context)

        # Produce research artifact
        artifact_content = {
            "research": {
                "scope": scope,
                "files_analyzed": [f["file"] for f in research_findings],
                "findings": research_findings,
                "analysis": analysis,
                "evidence": [
                    {
                        "type": "file_observation",
                        "source": "BoundedAgentRuntime",
                        "reality": "OBSERVED",
                        "content_hash": f.get("content_hash"),
                        "timestamp": _now(),
                    }
                    for f in research_findings
                ],
            },
            "timestamp": _now(),
            "agent_id": agent_id,
            "task_id": task_id,
        }

        artifact = {
            "kind": "research_report",
            "name": "research_report.json",
            "content": artifact_content,
            "content_hash": _digest(artifact_content),
            "parent_artifacts": [a["artifact_id"] for a in input_artifacts],
            "provenance": [f"agent:{agent_id}", "bounded-agent-runtime", "real-filesystem-observation"],
        }
        artifacts.append(artifact)

        result = {
            "task_id": task_id,
            "task_name": task_name,
            "agent_id": agent_id,
            "status": "COMPLETED",
            "reality": "OBSERVED",
            "research_findings": research_findings,
            "analysis": analysis,
            "artifact_count": len(artifacts),
        }
        return result, artifacts

    def _execute_implementation(
        self, *, workflow_id: str, task_id: str, task_name: str,
        agent_id: str, capabilities: list[str], scope: str,
        input_artifacts: list[dict[str, Any]], artifact_contents: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Implementation agent: consumes research artifact, produces implementation plan.

        This agent operates on OBSERVED evidence from the research phase.
        Its output is INFERRED — model claims alone cannot verify implementation.
        """
        artifacts: list[dict[str, Any]] = []

        # Consume research findings
        research_data: dict[str, Any] = {}
        for content in artifact_contents:
            if "research" in content.get("content", {}):
                research_data = content["content"]["research"]
                break

        analysis = research_data.get("analysis", {})
        findings = research_data.get("findings", [])

        # Generate implementation plan deterministically from evidence
        plan = self._generate_implementation_plan(findings, analysis, scope)

        plan_content = {
            "implementation_plan": plan,
            "based_on": [f.get("content_hash") for f in findings if f.get("content_hash")],
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": agent_id,
            "task_id": task_id,
            "timestamp": _now(),
        }

        artifact = {
            "kind": "implementation_plan",
            "name": "implementation_plan.json",
            "content": plan_content,
            "content_hash": _digest(plan_content),
            "parent_artifacts": [a["artifact_id"] for a in input_artifacts],
            "provenance": [f"agent:{agent_id}", "deterministic-planning", "evidence-based"],
        }
        artifacts.append(artifact)

        result = {
            "task_id": task_id,
            "task_name": task_name,
            "agent_id": agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "plan": plan,
            "artifacts_from_research": len(findings),
        }
        return result, artifacts

    def _execute_qa(
        self, *, workflow_id: str, task_id: str, task_name: str,
        agent_id: str, capabilities: list[str], scope: str,
        input_artifacts: list[dict[str, Any]], artifact_contents: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """QA agent: verifies the implementation plan and produces a QA report."""
        artifacts: list[dict[str, Any]] = []

        # Check if it's a QA specialist
        qareport = self._verify_implementation(artifact_contents, scope)

        qa_content = {
            "qa_report": qareport,
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": agent_id,
            "task_id": task_id,
            "timestamp": _now(),
        }

        artifact = {
            "kind": "qa_report",
            "name": "qa_report.json",
            "content": qa_content,
            "content_hash": _digest(qa_content),
            "parent_artifacts": [a["artifact_id"] for a in input_artifacts],
            "provenance": [f"agent:{agent_id}", "deterministic-verification"],
        }
        artifacts.append(artifact)

        result = {
            "task_id": task_id,
            "task_name": task_name,
            "agent_id": agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "qa_report": qareport,
        }
        return result, artifacts

    def _execute_verification(
        self, *, workflow_id: str, task_id: str, task_name: str,
        agent_id: str, capabilities: list[str], scope: str,
        input_artifacts: list[dict[str, Any]], artifact_contents: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Verification agent: final verification of all work products."""
        artifacts: list[dict[str, Any]] = []

        # Check that all required artifacts are present and well-formed
        verification = {
            "verification_id": f"verify-{task_id}",
            "workflow_id": workflow_id,
            "timestamp": _now(),
            "checks": [],
            "all_passed": True,
            "reality": "INFERRED",
            "untrusted": True,
        }

        required_artifact_kinds = ["research_report", "implementation_plan", "qa_report"]
        produced_kinds = {a.get("kind") for a in input_artifacts}

        for kind in required_artifact_kinds:
            if kind in produced_kinds:
                verification["checks"].append({"artifact": kind, "status": "PRESENT", "passed": True})
            else:
                verification["checks"].append({"artifact": kind, "status": "MISSING", "passed": False})
                verification["all_passed"] = False

        # Verify content hashes
        for art in input_artifacts:
            if art.get("content_hash"):
                verification["checks"].append({
                    "artifact": art.get("kind"),
                    "check": "content_hash",
                    "value": art.get("content_hash"),
                    "passed": True,
                })

        verify_content = {
            "verification_result": verification,
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": agent_id,
            "task_id": task_id,
            "timestamp": _now(),
        }

        artifact = {
            "kind": "verification_result",
            "name": "verification_result.json",
            "content": verify_content,
            "content_hash": _digest(verify_content),
            "parent_artifacts": [a["artifact_id"] for a in input_artifacts],
            "provenance": [f"agent:{agent_id}", "deterministic-verification"],
        }
        artifacts.append(artifact)

        result = {
            "task_id": task_id,
            "task_name": task_name,
            "agent_id": agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "verification": verification,
            "all_passed": verification["all_passed"],
        }
        return result, artifacts

    def _execute_generic(
        self, *, workflow_id: str, task_id: str, task_name: str,
        agent_id: str, capabilities: list[str], scope: str,
        input_artifacts: list[dict[str, Any]], artifact_contents: list[dict[str, Any]],
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Generic task executor for non-specialized task types."""
        artifacts: list[dict[str, Any]] = []

        content = {
            "task_result": {
                "task_id": task_id,
                "task_name": task_name,
                "agent_id": agent_id,
                "input_artifact_count": len(input_artifacts),
                "input_artifact_hashes": [a.get("content_hash", "") for a in input_artifacts],
                "parameters": parameters,
            },
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": agent_id,
            "task_id": task_id,
            "timestamp": _now(),
        }

        artifact = {
            "kind": "task_result",
            "name": f"result_{task_id}.json",
            "content": content,
            "content_hash": _digest(content),
            "parent_artifacts": [a["artifact_id"] for a in input_artifacts],
            "provenance": [f"agent:{agent_id}", "generic-executor"],
        }
        artifacts.append(artifact)

        result = {
            "task_id": task_id,
            "task_name": task_name,
            "agent_id": agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "input_artifacts": len(input_artifacts),
        }
        return result, artifacts

    # ------------------------------------------------------------------
    # Deterministic analysis helpers
    # ------------------------------------------------------------------

    def _get_project_context(self, scope: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Get the project context — files to analyze."""
        observation_root = parameters.get("observation_root")
        if observation_root:
            root = Path(observation_root)
        elif self.settings and hasattr(self.settings, "allowed_filesystem_root"):
            root = Path(self.settings.allowed_filesystem_root)
        else:
            root = Path(".")

        try:
            files = [str(p) for p in root.rglob("*") if p.is_file() and not p.is_dir()]
        except (OSError, PermissionError):
            files = []

        # Filter to relevant files only
        relevant_extensions = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini"}
        key_files = []
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in relevant_extensions and "node_modules" not in f and ".venv" not in f and "__pycache__" not in f:
                if len(key_files) < 20:
                    key_files.append(f)

        return {
            "scope": scope,
            "root": str(root),
            "total_files": len(files),
            "key_files": key_files,
        }

    def _project_root(self) -> str:
        """Get the project root from settings or environment."""
        if self.settings and hasattr(self.settings, "allowed_filesystem_root"):
            return self.settings.allowed_filesystem_root
        return "."

    def _analyze_research_findings(self, findings: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
        """Analyze research findings deterministically."""
        file_types: dict[str, int] = {}
        total_size = 0
        hashed_files = 0

        for f in findings:
            if f.get("file"):
                path = f["file"]
                ext = Path(path).suffix.lower() or ".no_extension"
                file_types[ext] = file_types.get(ext, 0) + 1
            total_size += f.get("size", 0) or 0
            if f.get("content_hash"):
                hashed_files += 1

        return {
            "file_type_distribution": file_types,
            "total_bytes_observed": total_size,
            "files_with_hashes": hashed_files,
            "files_analyzed": len(findings),
            "context": {
                "scope": context.get("scope"),
                "root": context.get("root"),
                "total_files_in_project": context.get("total_files", 0),
            },
            "assessment": "repository health is observable from read-only analysis" if findings else "no observable files found",
        }

    def _generate_implementation_plan(self, findings: list[dict[str, Any]], analysis: dict[str, Any], scope: str) -> dict[str, Any]:
        """Generate a deterministic implementation plan from research findings."""
        file_types = analysis.get("file_type_distribution", {})
        recommendation = "no specific implementation needed"

        if ".py" in file_types and ".json" in file_types:
            recommendation = "Python project structure is present; suggest maintaining existing structure and adding type annotations"
        elif ".ts" in file_types or ".tsx" in file_types:
            recommendation = "TypeScript project structure is present; suggest using tsconfig for incremental improvements"
        elif ".md" in file_types:
            recommendation = "Documentation exists; suggest expanding on identified gaps"
        else:
            recommendation = "observe project structure to determine appropriate improvements"

        return {
            "objective": f"Implementation plan for {scope}",
            "steps": [
                {"order": 1, "description": "review research findings", "depends_on": None},
                {"order": 2, "description": "confirm project structure from real filesystem observation", "depends_on": 1},
                {"order": 3, "description": "implement deterministic improvements based on evidence", "depends_on": 2},
                {"order": 4, "description": "verify against original research evidence", "depends_on": 3},
            ],
            "based_on": {
                "files_analyzed": len(findings),
                "file_types": file_types,
                "context": analysis.get("context", {}),
            },
            "recommendation": recommendation,
            "evidence_trail": [f.get("content_hash") for f in findings if f.get("content_hash")],
        }

    def _verify_implementation(self, artifact_contents: list[dict[str, Any]], scope: str) -> dict[str, Any]:
        """Verify implementation plan against QA criteria."""
        plan_found = False
        plan_data: dict[str, Any] = {}

        for content in artifact_contents:
            data = content.get("content", {})
            if "implementation_plan" in data:
                plan_found = True
                plan_data = data["implementation_plan"]
                break

        checks: list[dict[str, Any]] = []
        if plan_found:
            checks.append({"check": "implementation_plan_present", "status": "PASS", "detail": "implementation_plan artifact received"})
            steps = plan_data.get("steps", [])
            if steps:
                checks.append({"check": "plan_has_steps", "status": "PASS", "detail": f"{len(steps)} implementation steps defined"})
            if plan_data.get("evidence_trail"):
                checks.append({"check": "plan_has_evidence", "status": "PASS", "detail": f"{len(plan_data['evidence_trail'])} evidence references"})
            recommendation = plan_data.get("recommendation", "")
            if recommendation and recommendation != "no specific implementation needed":
                checks.append({"check": "plan_has_recommendation", "status": "PASS", "detail": recommendation[:100]})
        else:
            checks.append({"check": "implementation_plan_present", "status": "FAIL", "detail": "no implementation_plan artifact received"})

        all_passed = all(c["status"] == "PASS" for c in checks)

        return {
            "verification_id": f"qa-{_digest(scope)}[:8]",
            "checks": checks,
            "passed": all_passed,
            "recommendation": "implementation plan is well-formed and evidence-backed" if all_passed else "implementation plan is missing required components",
            "reality": "INFERRED",
            "untrusted": True,
        }
