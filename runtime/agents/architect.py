"""Architecture Analyst Agent — analyzes research findings and produces an architecture plan.

This agent consumes research artifacts produced by upstream Researcher agents.
It has no direct capabilities (no filesystem.read) — it works entirely from
the evidence passed to it. Its output is INFERRED (rule-based reasoning over
OBSERVED evidence).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import json

from runtime.agent_base import AgentContext, AgentExecutionResult


def _digest(value: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArchitectureAgent:
    """Agent that analyzes research findings and produces an architecture plan."""
    agent_id: str = "architect"
    name: str = "Architecture Analyst"
    role: str = "architect"
    capabilities: list[str] = field(default_factory=lambda: ["knowledge.read"])
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "analyze"])
    prohibited_operations: list[str] = field(default_factory=lambda: ["filesystem.write", "execute"])
    scope: dict = field(default_factory=dict)
    # Phase 7: DETERMINISTIC | MODEL | HYBRID (model reasons over OBSERVED evidence only)
    execution_strategy: str = "DETERMINISTIC"
    model_router: Any = None
    input_contract: tuple = ("research_report artifact contents (OBSERVED evidence)",)
    output_contract: tuple = ("architecture_plan artifact (INFERRED, untrusted)",)
    tool_permissions: tuple = ("no direct tools; reasons over consumed artifacts",)
    artifact_behavior: str = "produces architecture_plan; parents = consumed input artifact IDs"
    message_behavior: str = "sends STATUS_UPDATE, QUESTION to security-analyst, and RESPONSE"

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        """Execute architecture analysis: consume research, produce plan."""
        if context.messaging_hub:
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="STATUS_UPDATE",
                content={
                    "agent_id": context.agent_id,
                    "status": "analyzing_architecture",
                    "input_artifact_count": len(context.input_artifacts),
                },
                from_agent_id=context.agent_id,
                task_id=context.task_id,
            )

        # Consume upstream research findings (content may be a dict or a JSON string)
        research_data: dict[str, Any] = {}
        for content in context.artifact_contents:
            data = content.get("content", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (ValueError, TypeError):
                    continue
            if isinstance(data, dict) and "research" in data:
                research_data = data["research"]
                break

        findings = research_data.get("findings", [])
        analysis = research_data.get("analysis", {})
        evidence = research_data.get("evidence", [])

        # Deterministically derive architecture plan from evidence
        file_type_dist = analysis.get("file_type_distribution", {})
        file_types = list(file_type_dist.keys())

        plan_steps = []
        next_order = 1

        if ".py" in file_type_dist:
            plan_steps.append({"order": next_order, "description": "Python project detected; analyze module structure and imports", "depends_on": None})
            next_order += 1
            plan_steps.append({"order": next_order, "description": "Add type annotations to public interfaces", "depends_on": next_order - 1})
            next_order += 1

        if ".ts" in file_type_dist or ".tsx" in file_type_dist:
            plan_steps.append({"order": next_order, "description": "TypeScript project detected; verify tsconfig paths and module resolution", "depends_on": None})
            next_order += 1
            plan_steps.append({"order": next_order, "description": "Review component hierarchy and prop flow", "depends_on": next_order - 1})
            next_order += 1

        if ".json" in file_type_dist:
            plan_steps.append({"order": next_order, "description": "Validate JSON configuration files for schema compliance", "depends_on": None})
            next_order += 1

        if ".md" in file_type_dist:
            plan_steps.append({"order": next_order, "description": "Audit documentation for gaps against observed code structure", "depends_on": None})
            next_order += 1

        if not plan_steps:
            plan_steps.append({"order": 1, "description": "Review research findings for project structure analysis", "depends_on": None})

        # Build recommendations based on observed evidence
        recommendations = []
        if file_type_dist:
            primary_lang = max(file_type_dist, key=file_type_dist.get)
            recommendations.append(f"Primary language detected: {primary_lang} ({file_type_dist[primary_lang]} files)")

        if any(f.get("reality") == "OBSERVED" for f in findings):
            recommendations.append("All file observations were produced by BoundedAgentRuntime with real filesystem reads")

        # Phase 7: optional model reasoning over OBSERVED evidence (INFERRED only).
        model_used = False
        model_text = ""
        model_events: list[dict[str, Any]] = []
        model_tool_records: list[dict[str, Any]] = []
        strategy_descriptor: dict[str, Any] = {"requested": self.execution_strategy, "effective": "DETERMINISTIC"}
        try:
            import os as _os
            _strategy = ((context.parameters.get("execution_strategy") if isinstance(context.parameters, dict) else None) or self.execution_strategy or _os.getenv("NEXUS_EXECUTION_MODE", "DETERMINISTIC"))
            _router = self.model_router
            if _router is None and _strategy.strip().upper() in ("MODEL", "HYBRID"):
                try:
                    from runtime.model_router import ModelRouter as _MR
                    _router = _MR()
                except Exception:
                    _router = None
            if _router is not None and _strategy.strip().upper() in ("MODEL", "HYBRID"):
                from runtime.model_agent_support import run_model_enhancement as _enhance
                _res = _enhance(
                    agent_id=context.agent_id, agent_role=self.role,
                    agent_capabilities=self.capabilities,
                    agent_allowed_operations=self.allowed_operations,
                    agent_prohibited_operations=self.prohibited_operations,
                    objective=context.objective or context.scope,
                    task_name=context.task_name, task_type="architecture-analysis",
                    constraints=context.constraints,
                    input_artifacts=[dict(a) for a in context.input_artifacts],
                    artifact_contents=[dict(a) for a in context.artifact_contents],
                    previous_messages=[dict(m) for m in (context.previous_messages or [])],
                    observed_evidence=[dict(f) for f in findings],
                    workspace_root=context.observation_scope,
                    expected_output="Architecture insight as concise INFERRED recommendations grounded in the observed evidence.",
                    strategy_value=_strategy, router=_router,
                    execution_environment=((context.parameters.get("execution_environment", "LOCAL")
                        if isinstance(context.parameters, dict) else "LOCAL")),
                )
                strategy_descriptor = _res.get("strategy", strategy_descriptor)
                model_events = list(_res.get("events", []))
                model_tool_records = list(_res.get("tool_records", []))
                if _res.get("model_used"):
                    model_used = True
                    model_text = str(_res.get("model_text", ""))[:6000]
                    recommendations.append(f"Model insight (INFERRED, {str(_res.get('provider'))}): {model_text[:500]}")
        except Exception:
            model_used = False

        plan = {
            "objective": context.parameters.get("objective", "Analyze project architecture from research findings"),
            "scope": context.scope,
            "file_types_observed": file_types,
            "evidence_count": len(evidence),
            "steps": plan_steps,
            "recommendations": recommendations,
            "based_on_findings": [f.get("content_hash") for f in findings if f.get("content_hash")],
            "analysis_context": {
                "files_analyzed": analysis.get("files_analyzed", 0),
                "total_bytes": analysis.get("total_bytes_observed", 0),
                "file_type_distribution": file_type_dist,
            },
        }

        artifact_content = {
            "architecture_plan": plan,
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "timestamp": _now(),
            "evidence_references": [e.get("content_hash") for e in evidence if e.get("content_hash")],
            "parent_artifact_hashes": [a.get("content_hash") for a in context.input_artifacts if a.get("content_hash")],
        }
        if model_used:
            artifact_content["model_insight"] = {
                "text": model_text, "reality": "INFERRED", "untrusted": True,
                "derived_from": [e.get("content_hash") for e in evidence if e.get("content_hash")],
                "strategy": strategy_descriptor,
            }

        artifact = {
            "kind": "architecture_plan",
            "name": "architecture_plan.json",
            "content": artifact_content,
            "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": [f"agent:{context.agent_id}", "type:architect", "evidence-based-reasoning"],
        }

        result = {
            "task_id": context.task_id,
            "task_name": context.task_name,
            "agent_id": context.agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "plan_steps": len(plan_steps),
            "recommendations": recommendations,
            "based_on_evidence": len(evidence),
        }

        # Targeted collaboration: ask the security specialist to review this plan.
        # Persisted via MessagingHub as a QUESTION addressed to security-analyst;
        # the security agent reads workflow-level messages and ANSWERs.
        if context.messaging_hub:
            context.messaging_hub.question(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                question=(
                    f"Architecture plan ready with {len(plan_steps)} steps "
                    f"from {len(evidence)} evidence items. Please review for security risks."
                ),
                from_agent_id=context.agent_id,
                to_agent_id="security-analyst",
                task_id=context.task_id,
                context={"plan_steps": len(plan_steps), "based_on_evidence": len(evidence)},
            )
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="RESPONSE",
                content={
                    "agent_id": context.agent_id,
                    "status": "completed",
                    "plan_steps": len(plan_steps),
                    "recommendations_count": len(recommendations),
                    "based_on_evidence": len(evidence),
                },
                from_agent_id=context.agent_id,
                task_id=context.task_id,
            )

        return AgentExecutionResult(
            task_id=context.task_id,
            agent_id=context.agent_id,
            status="COMPLETED",
            reality="INFERRED",
            untrusted=True,
            result=result,
            artifacts=[artifact],
            provenance=[f"agent:{context.agent_id}", "type:architect"],
            execution_metadata={
                "execution_strategy": strategy_descriptor,
                "model_used": model_used,
                "model_invocations": model_events,
                "model_tool_records": model_tool_records,
            },
        )
