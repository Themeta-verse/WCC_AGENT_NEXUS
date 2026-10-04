"""Phase 7 — Controlled agent execution loop (model <-> bounded tools).

Conceptual loop::

    while not finished:
        model_response = model_router.generate(context)
        if model_response requests tool:
            validate tool
            execute bounded tool
            create OBSERVED result
            append result to context
            continue
        else:
            produce agent result
            finish

Guarantees: maximum iterations, timeout, tool-call limits, failure handling,
cancellation support. Never an infinite loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
import os
import time

from runtime.model_context import ModelContextBuilder
from runtime.model_router import ModelRequest, ModelResponse, ModelRouter, ToolDefinition
from runtime.model_tools import execute_tool_call, ToolExecutionRecord


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)) or default))
    except ValueError:
        return default


@dataclass
class AgentLoopConfig:
    max_iterations: int = 5
    max_tool_calls: int = 4
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "AgentLoopConfig":
        return cls(
            max_iterations=_int_env("NEXUS_MODEL_MAX_ITERATIONS", 5),
            max_tool_calls=_int_env("NEXUS_MODEL_MAX_TOOL_CALLS", 4),
            timeout_seconds=_int_env("NEXUS_MODEL_TIMEOUT_SECONDS", 60),
        )


@dataclass
class AgentLoopResult:
    finished: bool
    final_response: ModelResponse | None
    tool_records: list[ToolExecutionRecord] = field(default_factory=list)
    iterations: int = 0
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


class ModelAgentLoop:
    """Runs the bounded model<->tool loop for one agent invocation."""

    def __init__(
        self,
        router: ModelRouter | None = None,
        context_builder: ModelContextBuilder | None = None,
        config: AgentLoopConfig | None = None,
    ):
        self.router = router or ModelRouter()
        self.context_builder = context_builder or ModelContextBuilder()
        self.config = config or AgentLoopConfig.from_env()

    def run(
        self,
        *,
        agent_id: str,
        agent_role: str,
        agent_capabilities: list[str],
        objective: str,
        task_name: str,
        task_type: str,
        constraints: dict[str, Any] | None = None,
        input_artifacts: list[dict[str, Any]] | None = None,
        artifact_contents: list[dict[str, Any]] | None = None,
        previous_messages: list[dict[str, Any]] | None = None,
        observed_evidence: list[dict[str, Any]] | None = None,
        tool_definitions: list[ToolDefinition] | None = None,
        expected_output: str = "",
        system_instructions: str | None = None,
        workspace_root: str | None = None,
        agent_allowed_operations: list[str] | None = None,
        agent_prohibited_operations: list[str] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        extra_context: str = "",
        execution_environment: str = "LOCAL",
    ) -> AgentLoopResult:
        config = self.config
        deadline = time.monotonic() + config.timeout_seconds
        tool_records: list[ToolExecutionRecord] = []
        events: list[dict[str, Any]] = []
        tool_observations: list[dict[str, Any]] = list(observed_evidence or [])
        iterations = 0
        final_response: ModelResponse | None = None

        tools = list(tool_definitions or [])
        base_metadata = {"agent_id": agent_id, "agent_role": agent_role, "task_type": task_type}

        while iterations < config.max_iterations:
            if is_cancelled and is_cancelled():
                return AgentLoopResult(finished=False, final_response=final_response,
                                       tool_records=tool_records, iterations=iterations,
                                       cancelled=True, error="cancelled", events=events)
            if time.monotonic() > deadline:
                events.append({"type": "model_timeout", "iterations": iterations})
                return AgentLoopResult(finished=False, final_response=final_response,
                                       tool_records=tool_records, iterations=iterations,
                                       timed_out=True, error="model loop timeout", events=events)
            iterations += 1
            built = self.context_builder.build(
                agent_id=agent_id, agent_role=agent_role,
                objective=objective, task_name=task_name, task_type=task_type,
                constraints=constraints, input_artifacts=input_artifacts,
                artifact_contents=artifact_contents, previous_messages=previous_messages,
                observed_evidence=tool_observations, available_tools=tools,
                expected_output=expected_output, system_instructions=system_instructions,
                extra_context=extra_context + (
                    f"\n\nTOOL RESULTS SO FAR ({len(tool_records)}):\n" + "\n".join(
                        f"- {r.capability} {r.target} -> {r.status} ({r.reason})"
                        + (f" sha256={r.content_sha256}" if r.content_sha256 else "")
                        for r in tool_records
                    ) if tool_records else ""
                ),
                execution_metadata={**base_metadata, "iteration": iterations},
            )
            request = built.request
            try:
                response = self.router.generate(request, built.prompt)
            except Exception as exc:
                events.append({"type": "model_failure", "iteration": iterations, "error": str(exc)[:500]})
                return AgentLoopResult(finished=False, final_response=None,
                                       tool_records=tool_records, iterations=iterations,
                                       error=f"provider failure: {exc}", events=events)
            events.append({
                "type": "model_invocation",
                "iteration": iterations,
                "provider": response.provider,
                "model": response.model,
                "execution_id": response.execution_id,
                "prompt_chars": built.stats.get("prompt_chars", 0),
                "duration_seconds": response.duration_seconds,
                "fallback_used": response.fallback_used,
            })
            if not response.tool_calls:
                final_response = response
                events.append({"type": "model_result", "iteration": iterations})
                return AgentLoopResult(finished=True, final_response=final_response,
                                       tool_records=tool_records, iterations=iterations, events=events)
            # Tool requests: enforce tool-call limits
            if len(tool_records) + len(response.tool_calls) > config.max_tool_calls:
                events.append({"type": "tool_limit_exceeded", "iteration": iterations,
                               "requested": len(response.tool_calls)})
                final_response = response
                final_response.tool_calls = []
                return AgentLoopResult(finished=True, final_response=final_response,
                                       tool_records=tool_records, iterations=iterations,
                                       error="tool-call limit exceeded; returning last model result", events=events)
            for call in response.tool_calls:
                record = execute_tool_call(
                    call, agent_id=agent_id, agent_capabilities=agent_capabilities,
                    workspace_root=workspace_root,
                    agent_allowed_operations=agent_allowed_operations,
                    agent_prohibited_operations=agent_prohibited_operations,
                    is_cancelled=is_cancelled,
                    execution_environment=execution_environment,
                )
                tool_records.append(record)
                events.append({
                    "type": "tool_requested" if record.status in ("EXECUTED", "BLOCKED") else "tool_rejected",
                    "tool": record.capability, "target": record.target,
                    "status": record.status, "reason": record.reason[:300],
                })
                events.append({
                    "type": "tool_result", "tool": record.capability,
                    "status": record.status, "reality": "OBSERVED",
                    "content_sha256": record.content_sha256,
                })
                # OBSERVED result feeds back into context for the next iteration
                tool_observations.append({
                    "tool": record.capability,
                    "target": record.target,
                    "status": record.status,
                    "reality": "OBSERVED",
                    "reason": record.reason,
                    "content_sha256": record.content_sha256,
                    "content_size": record.content_size,
                    "content_preview": (record.content_preview or "")[:2000],
                    "receipt_id": record.receipt_id,
                })
            # continue loop; model sees tool results next iteration
            final_response = response

        events.append({"type": "max_iterations_exceeded", "iterations": iterations})
        return AgentLoopResult(finished=False, final_response=final_response,
                               tool_records=tool_records, iterations=iterations,
                               error="max iterations exceeded", events=events)
