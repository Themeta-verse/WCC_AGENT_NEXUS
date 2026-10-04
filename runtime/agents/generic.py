"""Generic Agent — fallback agent for non-specialized workflow tasks.

This agent handles arbitrary task types using deterministic rule-based execution.
It serves as the default when no specialized agent is registered for a task type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json

from runtime.agent_base import AgentContext, AgentExecutionResult


def _digest(value: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GenericAgent:
    """Generic task executor for non-specialized task types."""
    agent_id: str = "generic-agent"
    name: str = "Generic Task Agent"
    role: str = "generic"
    capabilities: list[str] = field(default_factory=lambda: ["knowledge.read"])
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "analyze", "transform"])
    prohibited_operations: list[str] = field(default_factory=lambda: ["filesystem.write", "execute"])
    scope: dict = field(default_factory=dict)
    # Phase 7: DETERMINISTIC | MODEL | HYBRID
    execution_strategy: str = "DETERMINISTIC"
    model_router: Any = None
    input_contract: tuple = ("arbitrary task inputs + resolvable artifact contents",)
    output_contract: tuple = ("task_result artifact (INFERRED, untrusted)",)
    tool_permissions: tuple = ("no direct tools; deterministic rule-based transform",)
    artifact_behavior: str = "produces task_result; parents = consumed input artifact IDs"
    message_behavior: str = "lifecycle messages only (TASK_STARTED/COMPLETED via executor)"

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        """Execute a generic task: consume inputs, produce a result artifact."""
        input_hashes = [a.get("content_hash", "") for a in context.input_artifacts]
        input_kinds = [a.get("kind", "unknown") for a in context.input_artifacts]
        # Phase 7: optional model reasoning (INFERRED only, deterministic fallback).
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
                    task_name=context.task_name, task_type="agent_reasoning",
                    constraints=context.constraints,
                    input_artifacts=[dict(a) for a in context.input_artifacts],
                    artifact_contents=[dict(a) for a in context.artifact_contents],
                    previous_messages=[dict(m) for m in (context.previous_messages or [])],
                    observed_evidence=[], workspace_root=context.observation_scope,
                    expected_output="Task synthesis grounded in provided inputs.",
                    strategy_value=_strategy, router=_router,
                    execution_environment=((context.parameters.get("execution_environment", "LOCAL")
                        if isinstance(context.parameters, dict) else "LOCAL")),
                )
                strategy_descriptor = _res.get("strategy", strategy_descriptor)
                model_events = list(_res.get("events", []))
                model_used = bool(_res.get("model_used"))
        except Exception:
            model_used = False

        result_content = {
            "task_result": {
                "task_id": context.task_id,
                "task_name": context.task_name,
                "agent_id": context.agent_id,
                "input_artifact_count": len(context.input_artifacts),
                "input_artifact_hashes": input_hashes,
                "input_artifact_kinds": input_kinds,
                "parameters": context.parameters,
            },
            "reality": "INFERRED",
            "untrusted": True,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "timestamp": _now(),
        }

        artifact = {
            "kind": "task_result",
            "name": f"result_{context.task_id}.json",
            "content": result_content,
            "content_hash": _digest(result_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": [f"agent:{context.agent_id}", "type:generic", "deterministic-execution"],
        }

        result = {
            "task_id": context.task_id,
            "task_name": context.task_name,
            "agent_id": context.agent_id,
            "status": "COMPLETED",
            "reality": "INFERRED",
            "untrusted": True,
            "input_artifacts": len(context.input_artifacts),
            "artifact_count": 1,
        }

        return AgentExecutionResult(
            task_id=context.task_id,
            agent_id=context.agent_id,
            status="COMPLETED",
            reality="INFERRED",
            untrusted=True,
            result=result,
            artifacts=[artifact],
            provenance=[f"agent:{context.agent_id}", "type:generic"],
            execution_metadata={
                "execution_strategy": strategy_descriptor,
                "model_used": model_used,
                "model_invocations": model_events,
            },
        )
