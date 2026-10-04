"""Security Analyst Agent — performs security analysis on architecture plans.

This agent consumes architecture plan artifacts and performs deterministic
security scanning. It has read-only capabilities with security-specific
operations. Its analysis is INFERRED (static heuristics + pattern matching).
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


# Security-relevant patterns (deterministic, not LLM-based)
SECRET_PATTERNS = [
    "BEGIN RSA PRIVATE KEY",
    "BEGIN PRIVATE KEY",
    "AKIA",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
    "ghp_",
    "gho_",
    "BEGIN PGP PRIVATE KEY",
]

DANGEROUS_PATTERNS = [
    "eval(",
    "exec(",
    "os.system(",
    "subprocess.call(",
    "subprocess.Popen(",
    "__import__(",
    "eval ",
    "exec ",
]


@dataclass
class SecurityAgent:
    """Agent that performs deterministic security analysis."""
    agent_id: str = "security-analyst"
    name: str = "Security Analyst"
    role: str = "security-analyst"
    capabilities: list[str] = field(default_factory=lambda: ["security.read"])
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "analyze", "security.scan"])
    prohibited_operations: list[str] = field(default_factory=lambda: ["filesystem.write", "execute", "delete"])
    scope: dict = field(default_factory=dict)
    # Phase 7: DETERMINISTIC | MODEL | HYBRID
    execution_strategy: str = "DETERMINISTIC"
    model_router: Any = None
    input_contract: tuple = ("architecture_plan artifact + peer QUESTION messages",)
    output_contract: tuple = ("security_report artifact (INFERRED, untrusted)",)
    tool_permissions: tuple = ("no direct tools; deterministic pattern scan over consumed plan",)
    artifact_behavior: str = "produces security_report; parents = consumed input artifact IDs"
    message_behavior: str = "sends STATUS_UPDATE, ANSWER to peer question, and RESPONSE"

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        """Execute security analysis on architecture plan artifacts."""
        if context.messaging_hub:
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="STATUS_UPDATE",
                content={
                    "agent_id": context.agent_id,
                    "status": "scanning_for_security_issues",
                    "input_artifact_count": len(context.input_artifacts),
                },
                from_agent_id=context.agent_id,
                task_id=context.task_id,
            )

        # Read targeted questions addressed to this agent (workflow-level messages).
        # Phase 8: answer EVERY pending QUESTION (researcher discovery + architect
        # review request), each with its own correlation ID, so the trace shows
        # a complete QUESTION -> ANSWER pair per asker instead of only the last.
        # Correlation IDs this agent already answered (idempotent collaboration:
        # a re-executed security task must not duplicate ANSWERs).
        answered_corr = {
            (msg.get("content", {}) or {}).get("correlation_id")
            for msg in context.previous_messages
            if msg.get("message_type") == "ANSWER"
            and msg.get("from_agent_id") in (context.agent_id, "security-analyst")
            and (msg.get("content", {}) or {}).get("correlation_id")
        }
        peer_questions: list[dict[str, Any]] = []
        for msg in context.previous_messages:
            if msg.get("message_type") != "QUESTION":
                continue
            to_id = msg.get("to_agent_id")
            if to_id is not None and to_id != context.agent_id and to_id != "security-analyst":
                continue
            corr = (msg.get("content", {}) or {}).get("correlation_id")
            if corr and corr in answered_corr:
                continue
            peer_questions.append(msg)
        peer_question_text = None
        peer_question_from = None
        if peer_questions:
            latest = peer_questions[-1]
            payload = latest.get("content", {}) or {}
            peer_question_text = payload.get("question")
            peer_question_from = latest.get("from_agent_id")

        # Consume architecture plan from input artifacts (dict or JSON string)
        arch_plan: dict[str, Any] = {}
        for content in context.artifact_contents:
            data = content.get("content", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (ValueError, TypeError):
                    continue
            if isinstance(data, dict) and "architecture_plan" in data:
                arch_plan = data["architecture_plan"]
                break

        # Perform deterministic security scan
        findings = self._scan_for_security_issues(arch_plan)

        # Phase 7: optional model reasoning over deterministic findings (INFERRED only).
        model_used = False
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
                    task_name=context.task_name, task_type="security-analysis",
                    constraints=context.constraints,
                    input_artifacts=[dict(a) for a in context.input_artifacts],
                    artifact_contents=[dict(a) for a in context.artifact_contents],
                    previous_messages=[dict(m) for m in (context.previous_messages or [])],
                    observed_evidence=[{"finding": dict(f)} for f in findings],
                    workspace_root=context.observation_scope,
                    expected_output="Security insight as INFERRED assessment grounded in deterministic findings; never invent unobserved vulnerabilities.",
                    strategy_value=_strategy, router=_router,
                    execution_environment=((context.parameters.get("execution_environment", "LOCAL")
                        if isinstance(context.parameters, dict) else "LOCAL")),
                )
                strategy_descriptor = _res.get("strategy", strategy_descriptor)
                model_events = list(_res.get("events", []))
                if _res.get("model_used"):
                    model_used = True
                    _text = str(_res.get("model_text", ""))[:2000]
                    findings.append({
                        "type": "model_insight",
                        "severity": "LOW",
                        "description": f"Model-assisted insight (INFERRED, untrusted): {_text[:500]}",
                        "check": "model_reasoning",
                        "reality": "INFERRED",
                    })
        except Exception:
            model_used = False

        # Determine risk level
        critical_findings = [f for f in findings if f["severity"] == "CRITICAL"]
        high_findings = [f for f in findings if f["severity"] == "HIGH"]
        risk_level = "LOW"
        if critical_findings:
            risk_level = "CRITICAL"
        elif high_findings:
            risk_level = "HIGH"
        elif findings:
            risk_level = "MEDIUM"

        security_report = {
            "scan_target": "architecture_plan",
            "scan_method": "deterministic_pattern_matching",
            "findings": findings,
            "risk_level": risk_level,
            "checks_passed": len(findings) == 0,
            "total_findings": len(findings),
            "by_severity": {
                "critical": len(critical_findings),
                "high": len(high_findings),
                "medium": len([f for f in findings if f["severity"] == "MEDIUM"]),
                "low": len([f for f in findings if f["severity"] == "LOW"]),
            },
            "scanned_steps": len(arch_plan.get("steps", [])),
            "scanned_recommendations": len(arch_plan.get("recommendations", [])),
        }

        artifact_content = {
            "security_report": security_report,
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "timestamp": _now(),
            "scanned_evidence": len(arch_plan.get("based_on_findings", [])) if arch_plan else 0,
            "parent_artifact_hashes": [a.get("content_hash") for a in context.input_artifacts if a.get("content_hash")],
            "peer_review": {
                "question_received": peer_question_text is not None,
                "question": peer_question_text,
                "question_from": peer_question_from,
            } if peer_question_text else {"question_received": False},
        }

        artifact = {
            "kind": "security_report",
            "name": "security_report.json",
            "content": artifact_content,
            "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": [f"agent:{context.agent_id}", "type:security-analyst", "deterministic-pattern-matching"],
        }

        result = {
            "task_id": context.task_id,
            "task_name": context.task_name,
            "agent_id": context.agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "risk_level": risk_level,
            "findings_count": len(findings),
            "checks_passed": len(findings) == 0,
        }

        if context.messaging_hub:
            for _q in peer_questions:
                _payload = _q.get("content", {}) or {}
                if not (_payload.get("question") and _q.get("from_agent_id")):
                    continue
                context.messaging_hub.answer(
                    workflow_id=context.workflow_id,
                    tenant_id=context.tenant_id,
                    answer_text=(
                        f"Security review complete: risk={risk_level}, "
                        f"findings={len(findings)} "
                        f"({len(critical_findings)} critical, {len(high_findings)} high)."
                    ),
                    from_agent_id=context.agent_id,
                    to_agent_id=_q.get("from_agent_id"),
                    task_id=context.task_id,
                    correlation_id=_payload.get("correlation_id"),
                    confidence="INFERRED",
                )
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="RESPONSE",
                content={
                    "agent_id": context.agent_id,
                    "status": "completed",
                    "risk_level": risk_level,
                    "findings_count": len(findings),
                    "critical_count": len(critical_findings),
                    "high_count": len(high_findings),
                    "answered_peer_question": peer_question_text is not None,
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
            provenance=[f"agent:{context.agent_id}", "type:security-analyst"],
            execution_metadata={
                "execution_strategy": strategy_descriptor,
                "model_used": model_used,
                "model_invocations": model_events,
            },
        )

    def _scan_for_security_issues(self, arch_plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Deterministically scan architecture plan for security issues."""
        findings: list[dict[str, Any]] = []
        if not arch_plan:
            findings.append({
                "type": "missing_input",
                "severity": "LOW",
                "description": "No architecture plan artifacts were consumed for security scan",
                "check": "architecture_plan_present",
            })
            return findings

        # Scan steps for dangerous operations
        for step in arch_plan.get("steps", []):
            step_desc = step.get("description", "")
            for pattern in DANGEROUS_PATTERNS:
                if pattern in step_desc:
                    findings.append({
                        "type": "dangerous_operation_in_plan",
                        "severity": "HIGH",
                        "description": f"Step '{step_desc}' may involve dangerous operation: {pattern}",
                        "match": pattern,
                        "step_order": step.get("order"),
                    })

        # Scan recommendations
        for rec in arch_plan.get("recommendations", []):
            for secret in SECRET_PATTERNS:
                if secret in str(rec):
                    findings.append({
                        "type": "potential_secret_in_recommendation",
                        "severity": "MEDIUM",
                        "description": f"Recommendation may reference sensitive material matching pattern: {secret}",
                        "match": secret,
                    })

        # Check for missing security considerations
        has_security_step = any("security" in str(s).lower() for s in arch_plan.get("steps", []))
        if not has_security_step:
            findings.append({
                "type": "no_security_step_in_plan",
                "severity": "MEDIUM",
                "description": "Architecture plan does not include a dedicated security review step",
                "check": "security_step_present",
            })

        return findings
