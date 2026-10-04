"""NEXUS canonical run result (Phase F).

NexusRunResult is the machine-readable record for every objective executed
by the runtime. It is built strictly from persisted rows (trace + workflow
state + events) — never from live objects or agent claims — so it stays
valid for future integrations (API, UI, external orchestrators).

The CLI renders it for humans; --record persists it as JSON.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json


@dataclass
class NexusRunResult:
    """Canonical machine-readable result for one executed objective."""
    objective: str = ""
    workflow_id: str = ""
    status: str = "UNKNOWN"
    started_at: str | None = None
    completed_at: str | None = None
    workspace: str = ""
    execution_environment: str = "LOCAL"
    strategy: str = "DETERMINISTIC"
    agents_used: dict[str, Any] = field(default_factory=dict)
    tasks_executed: list[dict[str, Any]] = field(default_factory=list)
    artifacts_produced: list[dict[str, Any]] = field(default_factory=list)
    verification_result: dict[str, Any] = field(default_factory=dict)
    execution_trace: dict[str, Any] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    retries: list[dict[str, Any]] = field(default_factory=list)
    final_artifact_location: str | None = None
    worker_id: str | None = None
    finish_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NexusRunResult":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_trace(
        cls,
        *,
        trace: dict[str, Any],
        workflow: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        verification: dict[str, Any] | None = None,
        worker_id: str | None = None,
        strategy: str = "DETERMINISTIC",
    ) -> "NexusRunResult":
        """Build from an engine execution trace + workflow row (both persisted)."""
        workflow = workflow or {}
        tasks = trace.get("tasks", []) or []
        failures = [
            {"task_id": t.get("task_id"), "task_type": t.get("task_type"),
             "error": t.get("error"), "agent_id": t.get("agent_id")}
            for t in tasks if t.get("status") == "FAILED"
        ]
        artifacts = trace.get("artifacts", []) or []
        finals = [a.get("content_path") for a in artifacts
                  if a.get("kind") == "final_report" and a.get("content_path")]
        return cls(
            objective=trace.get("objective", "") or workflow.get("objective", ""),
            workflow_id=trace.get("workflow_id", "") or workflow.get("workflow_id", ""),
            status=trace.get("status", "") or workflow.get("status", "UNKNOWN"),
            started_at=workflow.get("started_at"),
            completed_at=workflow.get("completed_at"),
            workspace=trace.get("scope", "") or workflow.get("scope", ""),
            execution_environment=(
                (trace.get("planning", {}) or {}).get("plan", {}) or {}).get(
                "execution_environment", "LOCAL") or "LOCAL",
            strategy=strategy,
            agents_used=trace.get("agents", {}) or {},
            tasks_executed=tasks,
            artifacts_produced=[
                {"artifact_id": a.get("artifact_id"), "kind": a.get("kind"),
                 "name": a.get("name"), "content_hash": a.get("content_hash"),
                 "content_path": a.get("content_path"), "reality": a.get("reality"),
                 "parents": a.get("parent_artifacts", []),
                 "consumed_by": a.get("consumed_by", []),
                 "provenance": a.get("provenance", []),
                 "task_id": a.get("task_id"), "agent_id": a.get("agent_id"),
                 "execution_id": a.get("execution_id")}
                for a in artifacts
            ],
            verification_result=verification or {},
            execution_trace={
                "messages": trace.get("messages", []),
                "dynamic_tasks": trace.get("dynamic_tasks", []),
                "workers": trace.get("workers", []),
                "recovery_events": trace.get("recovery_events", []),
                "model": trace.get("model", {}),
                "tools_used": trace.get("tools_used", {}),
                "reality_breakdown": trace.get("reality_breakdown", {}),
                "provenance_chain": trace.get("provenance_chain", []),
                "tool_calls": tool_calls or [],
            },
            failures=failures,
            retries=trace.get("retries", []) or [],
            final_artifact_location=finals[0] if finals else None,
            worker_id=worker_id,
            finish_reason=trace.get("finish_reason", ""),
        )
