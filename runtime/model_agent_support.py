"""Phase 7 — Shared model-enhancement helper for specialist agents.

Agents keep their deterministic behavior. When execution strategy is
MODEL or HYBRID *and* a real provider is configured, they may invoke the
model runtime for reasoning over already-observed evidence.

Contract:
- Deterministic evidence collection always runs first.
- Model reasoning output is INFERRED, never OBSERVED.
- On any provider failure, agents fall back to deterministic results and
  record the failure honestly (never pretend the model executed).
- API keys never enter artifacts, messages, logs, or traces.
"""
from __future__ import annotations

from typing import Any

from runtime.model_agent_loop import ModelAgentLoop, AgentLoopConfig
from runtime.model_context import ModelContextBuilder
from runtime.model_router import ToolDefinition
from runtime.model_strategy import ExecutionStrategy, describe_strategy, model_reasoning_task_for_role, resolve_strategy, should_use_model
from runtime.model_tools import default_tool_definitions_for


def run_model_enhancement(
    *,
    agent_id: str,
    agent_role: str,
    agent_capabilities: list[str],
    agent_allowed_operations: list[str] | None = None,
    agent_prohibited_operations: list[str] | None = None,
    objective: str,
    task_name: str,
    task_type: str,
    constraints: dict[str, Any] | None = None,
    input_artifacts: list[dict[str, Any]] | None = None,
    artifact_contents: list[dict[str, Any]] | None = None,
    previous_messages: list[dict[str, Any]] | None = None,
    observed_evidence: list[dict[str, Any]] | None = None,
    workspace_root: str | None = None,
    expected_output: str = "",
    system_instructions: str | None = None,
    strategy_value: str | None = None,
    router: Any | None = None,
    execution_environment: str = "LOCAL",
) -> dict[str, Any]:
    """Optionally invoke model reasoning; always safe to call.

    Returns dict with: model_used, model_text, tool_records, events,
    strategy descriptor, fallback_reason, provider, model_name.
    """
    strategy = resolve_strategy(strategy_value)
    descriptor = describe_strategy(strategy, router)
    base: dict[str, Any] = {
        "model_used": False,
        "model_text": "",
        "tool_records": [],
        "events": [],
        "strategy": descriptor,
        "fallback_reason": "",
        "provider": descriptor.get("provider", "NOT_CONFIGURED"),
        "model_name": descriptor.get("model", "default"),
    }
    if not should_use_model(strategy, router):
        base["fallback_reason"] = descriptor.get("reason", "deterministic")
        return base
    assert router is not None
    try:
        from runtime.execution_environment import resolve_environment
        task_for_router = model_reasoning_task_for_role(agent_role)
        env = resolve_environment(execution_environment).value
        tools = default_tool_definitions_for(agent_capabilities, execution_environment=env)
        loop = ModelAgentLoop(router=router, context_builder=ModelContextBuilder(), config=AgentLoopConfig.from_env())
        result = loop.run(
            agent_id=agent_id, agent_role=agent_role,
            agent_capabilities=agent_capabilities,
            objective=objective, task_name=task_name, task_type=task_for_router,
            constraints=constraints, input_artifacts=input_artifacts,
            artifact_contents=artifact_contents, previous_messages=previous_messages,
            observed_evidence=observed_evidence,
            tool_definitions=tools,
            expected_output=expected_output,
            system_instructions=system_instructions,
            workspace_root=workspace_root,
            agent_allowed_operations=agent_allowed_operations,
            agent_prohibited_operations=agent_prohibited_operations,
            execution_environment=env,
        )
        # Sanitize events (no secrets)
        from runtime.model_router import _redact_dict
        events = _redact_dict(result.events)
        tool_dicts = [r.to_dict() for r in result.tool_records]
        if result.finished and result.final_response is not None:
            base.update({
                "model_used": True,
                "model_text": (result.final_response.content or "")[:6000],
                "tool_records": tool_dicts,
                "events": events,
                "provider": result.final_response.provider,
                "model_name": result.final_response.model,
                "execution_id": result.final_response.execution_id,
                "fallback_used": result.final_response.fallback_used,
            })
        else:
            base.update({
                "model_used": False,
                "tool_records": tool_dicts,
                "events": events,
                "fallback_reason": result.error or "model loop did not finish; deterministic fallback",
            })
        return base
    except Exception as exc:
        base["fallback_reason"] = f"model invocation failed: {str(exc)[:300]}; deterministic fallback"
        base["events"] = [{"type": "model_failure", "error": str(exc)[:300]}]
        return base
