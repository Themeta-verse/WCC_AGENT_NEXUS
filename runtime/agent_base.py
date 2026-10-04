"""NEXUS Agent Base — Abstract interface for workflow-executing agents.

An Agent is the unit of execution within NEXUS workflows. Each agent:
- Has an identity (agent_id, name, role)
- Declares capabilities (what it can do)
- Declares allowed/prohibited operations (governance boundary)
- Implements an execute() method that consumes input artifacts and produces output

The agent boundary is real: different agents have different capabilities,
different execution strategies, and different provenance. The protocol is
intentionally minimal so that future agent types (LLM-based, tool-based,
human-in-the-loop) can be plugged in without modifying the workflow engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
import json
import hashlib


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class AgentContext:
    """Execution context provided to an agent when executing a task.

    - objective: High-level workflow objective (for agent awareness)
    - constraints: Constraints dict (for agent awareness)
    - previous_messages: Cross-agent messages relevant to this task
    - execution_metadata: Metadata for the current execution (retry count, etc.)
    - observation_scope: Explicit filesystem/observation scope boundary.
      This is the validated root path the agent may observe. It is NOT
      the workflow_id. Agents use this for bounded filesystem operations.
    - connector_registry: The ConnectorRegistry used for ALL capability-based
      external access. When left None it defaults to the single canonical
      process-wide registry.

      That default is load-bearing. An absent registry used to mean agents had
      no capability fabric at all and silently degraded to whatever private
      execution path they carried — which is how "hand-rolled filesystem reads
      with self-minted receipts" survived. There is now always exactly ONE
      authoritative registry, so an agent can never quietly bypass the fabric.
      Pass an explicit registry (as tests and sandboxed runs do) to isolate a
      context; that registry is then used verbatim, so object identity
      assertions remain meaningful.
    """
    workflow_id: str
    task_id: str
    task_name: str
    agent_id: str
    scope: str
    parameters: dict[str, Any] = field(default_factory=dict)
    input_artifacts: list[dict[str, Any]] = field(default_factory=list)
    artifact_contents: list[dict[str, Any]] = field(default_factory=list)
    principal: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = ""
    project_id: str = ""
    objective: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    previous_messages: list[dict[str, Any]] = field(default_factory=list)
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    observation_scope: str | None = None
    messaging_hub: Any = None
    connector_registry: Any = None

    def __post_init__(self) -> None:
        if self.connector_registry is not None:
            return
        try:
            from runtime.capability_fabric import get_capability_registry
            self.connector_registry = get_capability_registry()
        except Exception:  # pragma: no cover - defensive
            # Leave None. An agent that then requests a capability fails
            # explicitly, which is correct: it must never bypass the fabric.
            self.connector_registry = None


@dataclass
class AgentExecutionResult:
    """Result returned by an agent after executing a task."""
    task_id: str
    agent_id: str
    status: str  # "COMPLETED", "FAILED", "BLOCKED"
    reality: str  # "OBSERVED", "INFERRED"
    untrusted: bool
    result: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    provenance: list[str] = field(default_factory=lambda: ["agent-execution"])


class BaseAgent(Protocol):
    """Protocol that all NEXUS agents must implement."""

    agent_id: str
    name: str
    role: str
    capabilities: list[str]
    allowed_operations: list[str]
    prohibited_operations: list[str]

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        """Execute a task and return the result with any produced artifacts.

        Args:
            context: Execution context with input artifacts and parameters

        Returns:
            AgentExecutionResult with status, reality, artifacts, and metadata
        """
        ...


@dataclass
class AgentInfo:
    """Metadata describing a registered agent."""
    agent_id: str
    name: str
    role: str
    agent_type: str
    capabilities: list[str]
    allowed_operations: list[str]
    prohibited_operations: list[str]
    scope: dict[str, Any] = field(default_factory=dict)
    expected_behaviour: str = ""
    status: str = "ACTIVE"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def matches_capabilities(self, required: list[str]) -> bool:
        """Check if this agent's declared capabilities are a superset of required."""
        required_set = set(required)
        declared_set = set(self.capabilities)
        return required_set.issubset(declared_set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "agent_type": self.agent_type,
            "capabilities": self.capabilities,
            "allowed_operations": self.allowed_operations,
            "prohibited_operations": self.prohibited_operations,
            "scope": self.scope,
            "expected_behaviour": self.expected_behaviour,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
