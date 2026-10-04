"""Phase 7 — Controlled model context builder.

The model receives ONLY relevant, bounded information:

  OBJECTIVE / TASK / CONSTRAINTS / PREVIOUS MESSAGES /
  INPUT ARTIFACTS / OBSERVED EVIDENCE / AVAILABLE TOOLS / EXPECTED OUTPUT

Never dumps the entire database or workflow into a model call.
All bounds are explicit and enforced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json
import os

from runtime.model_router import ModelRequest, ToolDefinition, _redact_dict


DEFAULT_MAX_ARTIFACTS = 8
DEFAULT_MAX_MESSAGES = 12
DEFAULT_MAX_CHARS_PER_ARTIFACT = 4000
DEFAULT_MAX_TOTAL_CHARS = 24000


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _safe_json(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, default=str, indent=2)
    except Exception:
        text = str(value)
    return _truncate(text, limit)


@dataclass
class ContextLimits:
    max_artifacts: int = DEFAULT_MAX_ARTIFACTS
    max_messages: int = DEFAULT_MAX_MESSAGES
    max_chars_per_artifact: int = DEFAULT_MAX_CHARS_PER_ARTIFACT
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS

    @classmethod
    def from_env(cls) -> "ContextLimits":
        def _int(name: str, default: int) -> int:
            try:
                return max(1, int(os.getenv(name, str(default)) or default))
            except ValueError:
                return default
        return cls(
            max_artifacts=_int("NEXUS_MODEL_MAX_ARTIFACTS", DEFAULT_MAX_ARTIFACTS),
            max_messages=_int("NEXUS_MODEL_MAX_MESSAGES", DEFAULT_MAX_MESSAGES),
            max_chars_per_artifact=_int("NEXUS_MODEL_MAX_CHARS_PER_ARTIFACT", DEFAULT_MAX_CHARS_PER_ARTIFACT),
            max_total_chars=_int("NEXUS_MODEL_MAX_TOTAL_CHARS", DEFAULT_MAX_TOTAL_CHARS),
        )


@dataclass
class BuiltContext:
    """Bounded prompt + structured request for one model invocation."""
    prompt: str
    request: ModelRequest
    stats: dict[str, Any] = field(default_factory=dict)


class ModelContextBuilder:
    """Builds bounded ModelRequest prompts from agent execution context."""

    def __init__(self, limits: ContextLimits | None = None):
        self.limits = limits or ContextLimits.from_env()

    def build(
        self,
        *,
        agent_id: str,
        agent_role: str,
        objective: str,
        task_name: str,
        task_type: str,
        constraints: dict[str, Any] | None = None,
        input_artifacts: list[dict[str, Any]] | None = None,
        artifact_contents: list[dict[str, Any]] | None = None,
        previous_messages: list[dict[str, Any]] | None = None,
        observed_evidence: list[dict[str, Any]] | None = None,
        available_tools: list[ToolDefinition] | None = None,
        expected_output: str = "",
        system_instructions: str | None = None,
        extra_context: str = "",
        execution_metadata: dict[str, Any] | None = None,
    ) -> BuiltContext:
        limits = self.limits
        artifacts = list(input_artifacts or [])
        contents = list(artifact_contents or [])
        messages = list(previous_messages or [])
        evidence = list(observed_evidence or [])

        # Bound counts
        artifacts = artifacts[: limits.max_artifacts]
        contents = contents[: limits.max_artifacts]
        messages = messages[-limits.max_messages :]
        evidence = evidence[: limits.max_artifacts]

        sections: list[str] = []
        total = 0

        def _add(title: str, body: str) -> None:
            nonlocal total
            remaining = limits.max_total_chars - total
            if remaining <= 0:
                return
            chunk = _truncate(body, remaining)
            sections.append(f"## {title}\n{chunk}")
            total += len(chunk)

        _add("OBJECTIVE", _truncate(objective or "(none)", 2000))
        _add("TASK", f"agent={agent_id} role={agent_role}\ntask_name={task_name}\ntask_type={task_type}")
        if constraints:
            _add("CONSTRAINTS", _safe_json(_redact_dict(constraints), 2000))
        if messages:
            compact = [
                {
                    "type": m.get("message_type"),
                    "from": m.get("from_agent_id"),
                    "content": _truncate(json.dumps(m.get("content", {}), default=str), 800),
                }
                for m in messages
            ]
            _add("PREVIOUS MESSAGES", _safe_json(compact, 6000))
        if evidence:
            _add("OBSERVED EVIDENCE (tools actually observed — never claim more)", _safe_json(evidence, 8000))
        if contents:
            rendered = []
            for c in contents:
                rendered.append({
                    "artifact_id": c.get("artifact_id"),
                    "kind": c.get("kind"),
                    "name": c.get("name"),
                    "content": _truncate(
                        c.get("content") if isinstance(c.get("content"), str)
                        else json.dumps(c.get("content"), default=str),
                        limits.max_chars_per_artifact,
                    ),
                })
            _add("INPUT ARTIFACTS", _safe_json(rendered, 10000))
        elif artifacts:
            meta = [
                {k: a.get(k) for k in ("artifact_id", "kind", "name", "reality", "content_hash") if k in a}
                for a in artifacts
            ]
            _add("INPUT ARTIFACTS (metadata only)", _safe_json(meta, 3000))
        if available_tools:
            _add("AVAILABLE TOOLS (request via structured tool_calls only)", _safe_json(
                [t.to_dict() for t in available_tools], 2000))
        if expected_output:
            _add("EXPECTED OUTPUT", _truncate(expected_output, 1500))
        if extra_context:
            _add("ADDITIONAL CONTEXT", _truncate(extra_context, 3000))

        # Reality boundary reminder is always part of the prompt.
        sections.append(
            "## REALITY BOUNDARY (must obey)\n"
            "- OBSERVED = directly obtained through a real tool/system observation in this run.\n"
            "- INFERRED = your reasoning or transformation over observed evidence.\n"
            "- You must NEVER claim you observed something the tools did not actually observe.\n"
            "- Respond with JSON when a schema is expected. To request a tool, return "
            '{"tool_calls": [{"tool": "filesystem.read", "arguments": {"path": "..."}}]}.'
        )

        prompt = "\n\n".join(sections)
        system = system_instructions or (
            f"You are {agent_role} ({agent_id}) in the NEXUS evidence-first agent runtime. "
            "Reason only over the bounded evidence provided. Be concise and honest about uncertainty."
        )

        # Structured request keeps full (bounded) artifacts/messages for the router log,
        # while the prompt string is what providers actually receive.
        bounded_artifacts = []
        for c in contents:
            bounded_artifacts.append({
                "artifact_id": c.get("artifact_id"),
                "kind": c.get("kind"),
                "name": c.get("name"),
                "content": _truncate(
                    c.get("content") if isinstance(c.get("content"), str)
                    else json.dumps(c.get("content"), default=str),
                    limits.max_chars_per_artifact,
                ),
            })
        bounded_messages = [
            {
                "message_type": m.get("message_type"),
                "from_agent_id": m.get("from_agent_id"),
                "to_agent_id": m.get("to_agent_id"),
                "content": m.get("content", {}),
            }
            for m in messages
        ]

        request = ModelRequest(
            agent_objective=(objective or "")[:2000],
            task=f"{task_name} [{task_type}]",
            system_instructions=system,
            artifacts=bounded_artifacts,
            messages=bounded_messages,
            constraints=_redact_dict(dict(constraints or {})),
            tool_definitions=list(available_tools or []),
            execution_metadata=_redact_dict(dict(execution_metadata or {})),
            task_type=task_type if task_type else "agent_reasoning",
        )
        stats = {
            "sections": len(sections),
            "prompt_chars": len(prompt),
            "artifacts_included": len(bounded_artifacts),
            "messages_included": len(bounded_messages),
            "evidence_items": len(evidence),
            "truncated": total >= limits.max_total_chars,
        }
        return BuiltContext(prompt=prompt, request=request, stats=stats)
