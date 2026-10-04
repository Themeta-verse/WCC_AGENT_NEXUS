"""Report Generator Agent — produces a consolidated final report from all upstream artifacts.

This agent consumes research reports, architecture plans, and security reports
to produce a final verification report. Its output is INFERRED (synthesis of
upstream evidence).
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
class ReportAgent:
    """Agent that generates a final consolidated report from upstream artifacts."""
    agent_id: str = "reporter"
    name: str = "Report Generator"
    role: str = "reporter"
    capabilities: list[str] = field(default_factory=lambda: ["report.generate"])
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "synthesize", "report.generate"])
    prohibited_operations: list[str] = field(default_factory=lambda: ["filesystem.write", "execute"])
    scope: dict = field(default_factory=dict)
    # Phase 7: DETERMINISTIC | MODEL | HYBRID
    execution_strategy: str = "DETERMINISTIC"
    model_router: Any = None
    input_contract: tuple = ("research_report + architecture_plan + security_report contents",)
    output_contract: tuple = ("final_report artifact (INFERRED synthesis, untrusted)",)
    tool_permissions: tuple = ("no direct tools; synthesizes consumed artifacts",)
    artifact_behavior: str = "produces final_report; parents = consumed input artifact IDs"
    message_behavior: str = "sends STATUS_UPDATE at start, HANDOFF to verifier with the final report, and RESPONSE at completion"

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        """Execute report generation: synthesize all upstream artifacts into a final report."""
        if context.messaging_hub:
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="STATUS_UPDATE",
                content={
                    "agent_id": context.agent_id,
                    "status": "generating_report",
                    "input_artifact_count": len(context.input_artifacts),
                },
                from_agent_id=context.agent_id,
                task_id=context.task_id,
            )

        # Collect evidence from all consumed artifacts
        research_data = {}
        arch_plan = {}
        security_report = {}

        for content in context.artifact_contents:
            data = content.get("content", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (ValueError, TypeError):
                    continue
            if isinstance(data, dict):
                if "research" in data:
                    research_data = data["research"]
                elif "architecture_plan" in data:
                    arch_plan = data["architecture_plan"]
                elif "security_report" in data:
                    security_report = data["security_report"]

        # Phase 7: optional model synthesis over upstream evidence (INFERRED only).
        model_used = False
        model_text = ""
        model_events: list[dict[str, Any]] = []
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
                    task_name=context.task_name, task_type="report",
                    constraints=context.constraints,
                    input_artifacts=[dict(a) for a in context.input_artifacts],
                    artifact_contents=[dict(a) for a in context.artifact_contents],
                    previous_messages=[dict(m) for m in (context.previous_messages or [])],
                    observed_evidence=[],
                    workspace_root=context.observation_scope,
                    expected_output="Executive synthesis as INFERRED summary grounded strictly in the provided artifacts.",
                    strategy_value=_strategy, router=_router,
                    execution_environment=((context.parameters.get("execution_environment", "LOCAL")
                        if isinstance(context.parameters, dict) else "LOCAL")),
                )
                strategy_descriptor = _res.get("strategy", strategy_descriptor)
                model_events = list(_res.get("events", []))
                if _res.get("model_used"):
                    model_used = True
                    model_text = str(_res.get("model_text", ""))[:6000]
        except Exception:
            model_used = False

        # Detect a GitHub-connector observation (proof path) vs filesystem audit.
        _is_github = bool(
            (research_data.get("source") == "GitHubConnector")
            or research_data.get("github_metadata")
            or research_data.get("observation")
            or research_data.get("receipt")
            or research_data.get("capability") == "github.repository.read"
        )
        _github_meta = research_data.get("github_metadata") or research_data.get("observation") or {}
        # Synthesize final report
        report = {
            "title": "NEXUS Multi-Agent Workflow Report",
            "objective": context.parameters.get("objective", "Multi-agent cooperative task execution"),
            "scope": context.scope,
            "timestamp": _now(),
            "workflow_id": context.workflow_id,
            "task_id": context.task_id,
            "agent_id": context.agent_id,
            "sections": {
                "research": {
                    "files_analyzed": len(research_data.get("findings", [])),
                    "evidence_count": len(research_data.get("evidence", [])),
                    "assessment": research_data.get("analysis", {}).get("assessment", ""),
                    **({"source": "GitHubConnector", "capability": "github.repository.read",
                        "repository": research_data.get("scope"),
                        "observation": _github_meta} if _is_github else {}),
                },
                "architecture": {
                    "steps": len(arch_plan.get("steps", [])),
                    "recommendations": arch_plan.get("recommendations", []),
                },
                "security": {
                    "risk_level": security_report.get("risk_level", "UNKNOWN"),
                    "findings": security_report.get("total_findings", 0),
                    "checks_passed": security_report.get("checks_passed", False),
                },
            },
            "summary": self._build_summary(research_data, arch_plan, security_report),
            "evidence_chain": self._build_evidence_chain(research_data, arch_plan, security_report),
        }

        if model_used and model_text:
            report["model_synthesis"] = {
                "text": model_text, "reality": "INFERRED", "untrusted": True,
            }
        artifact_content = {
            "final_report": report,
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "timestamp": _now(),
            "sources_consulted": len(context.input_artifacts),
            "parent_artifact_hashes": [a.get("content_hash") for a in context.input_artifacts if a.get("content_hash")],
        }

        _prov = [f"agent:{context.agent_id}", "type:reporter", "evidence-synthesis"]
        if _is_github:
            # Preserve the GitHub observation lineage so the verifier can
            # independently validate the connector path from the final report.
            _prov = [f"agent:{context.agent_id}", "type:reporter", "evidence-synthesis", "github-connector", "github.repository.read"]
        artifact = {
            "kind": "final_report",
            "name": "final_report.json",
            "content": artifact_content,
            "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": _prov,
        }

        result = {
            "task_id": context.task_id,
            "task_name": context.task_name,
            "agent_id": context.agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "report_sections": list(report["sections"].keys()),
            "summary": report["summary"],
        }

        if context.messaging_hub:
            # Phase 8: explicit agent-to-agent HANDOFF. The final report is
            # handed to the verifier for independent validation; the HANDOFF
            # carries the artifact lineage so the trace shows who gave what
            # to whom (never a silent dependency edge).
            context.messaging_hub.handoff(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                task_id=context.task_id,
                from_agent_id=context.agent_id,
                to_agent_id="verifier",
                payload={
                    "artifact_kind": "final_report",
                    "report_sections": list(report["sections"].keys()),
                    "evidence_sources": len(report["evidence_chain"]),
                    "parent_artifact_hashes": [a.get("content_hash") for a in context.input_artifacts if a.get("content_hash")],
                },
                notes="Final report ready for independent verification.",
            )
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="RESPONSE",
                content={
                    "agent_id": context.agent_id,
                    "status": "completed",
                    "report_sections": list(report["sections"].keys()),
                    "evidence_sources": len(report["evidence_chain"]),
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
            provenance=[f"agent:{context.agent_id}", "type:reporter"],
            execution_metadata={
                "execution_strategy": strategy_descriptor,
                "model_used": model_used,
                "model_invocations": model_events,
            },
        )

    def _build_summary(self, research: dict, arch: dict, security: dict) -> str:
        """Build a human-readable summary from upstream evidence."""
        parts = []
        # GitHub proof path: describe the connector observation, never a
        # filesystem audit count.
        if (research.get("source") == "GitHubConnector" or research.get("github_metadata") or research.get("observation")):
            scope = research.get("scope", "unknown")
            meta = research.get("github_metadata") or research.get("observation") or {}
            full = meta.get("full_name", scope) if isinstance(meta, dict) else scope
            ev = len(research.get("evidence", []))
            parts.append(f"Observed GitHub repository {full} via github.repository.read with {ev} evidence receipt(s)")
            # Still include downstream context if present.
            steps = len(arch.get("steps", [])) if isinstance(arch, dict) else 0
            if steps:
                parts.append(f"Architecture plan contains {steps} steps")
            risk = security.get("risk_level", "UNKNOWN") if isinstance(security, dict) else "UNKNOWN"
            findings = security.get("total_findings", 0) if isinstance(security, dict) else 0
            parts.append(f"Security analysis: risk={risk}, findings={findings}")
            return ". ".join(parts) + "."
        files = len(research.get("findings", []))
        parts.append(f"Audited {files} files during research phase")

        steps = len(arch.get("steps", []))
        if steps:
            parts.append(f"Architecture plan contains {steps} steps")

        risk = security.get("risk_level", "UNKNOWN")
        findings = security.get("total_findings", 0)
        parts.append(f"Security analysis: risk={risk}, findings={findings}")

        return ". ".join(parts) + "."

    def _build_evidence_chain(self, research: dict, arch: dict, security: dict) -> list[dict[str, Any]]:
        """Build an evidence chain showing artifact provenance.

        Realities are DERIVED from upstream data, never hardcoded: the
        research link is OBSERVED only when every finding was actually
        observed; architecture derivation over content-hash references is
        INFERRED work (hashes alone prove nothing about observation).
        """
        chain = []

        if research.get("evidence"):
            _src = "github-connector" if (research.get("source") == "GitHubConnector" or research.get("github_metadata")) else "research"
            _findings = research.get("findings", []) or []
            _research_reality = (
                "OBSERVED"
                if _findings and all(f.get("reality") == "OBSERVED" for f in _findings if isinstance(f, dict))
                else "UNKNOWN"
            )
            chain.append({
                "source": _src,
                "evidence_count": len(research["evidence"]),
                "reality": _research_reality,
            })

        if arch.get("based_on_findings"):
            chain.append({
                "source": "research_findings",
                "evidence_count": len(arch["based_on_findings"]),
                "reality": "INFERRED",
            })

        if security.get("scanned_evidence"):
            chain.append({
                "source": "security_scan",
                "evidence_count": security["scanned_evidence"],
                "reality": "INFERRED",
            })

        return chain
