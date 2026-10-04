"""NEXUS Agent Registry — Centralized agent management with capability matching.

The AgentRegistry is the source of truth for available agents within a tenant/project
scope. It provides:
- Registration/unregistration of agents with their capabilities
- Capability-based agent selection (which agent can perform a task?)
- Agent status management (ACTIVE, FLAGGED, HALTED, RETIRED)
- Lookup by agent_id or by capability requirement

Agents registered here are real execution boundaries: each agent has a distinct
identity, set of capabilities, and execution strategy. The registry is extensible
— new agent types can be registered at runtime without modifying the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from runtime.agent_base import AgentInfo, AgentExecutionResult, AgentContext, BaseAgent, utc_now


AGENT_STATUS_ACTIVE = "ACTIVE"
AGENT_STATUS_FLAGGED = "FLAGGED"
AGENT_STATUS_HALTED = "HALTED"
AGENT_STATUS_RETIRED = "RETIRED"
VALID_AGENT_STATUSES = {AGENT_STATUS_ACTIVE, AGENT_STATUS_FLAGGED, AGENT_STATUS_HALTED, AGENT_STATUS_RETIRED}


@dataclass
class RegisteredAgent:
    """An agent registered with the registry, holding its instance and metadata."""
    info: AgentInfo
    instance: Any  # BaseAgent implementation
    current_task: str | None = None
    execution_history: list[dict[str, Any]] = field(default_factory=list)
    last_active: str | None = None


class AgentRegistry:
    """In-memory registry of agents available for workflow execution.

    The registry supports:
    - Capability-matched agent selection
    - Status-based filtering (only ACTIVE agents are dispatched)
    - Extensible agent type registration
    - Per-tenant isolation
    """

    def __init__(self):
        self._agents: dict[str, RegisteredAgent] = {}
        self._tenant_agents: dict[str, list[str]] = {}

    def register_agent(self, agent_id: str, name: str, role: str, agent_type: str,
                       capabilities: list[str], allowed_operations: list[str],
                       prohibited_operations: list[str], scope: dict[str, Any] | None = None,
                       expected_behaviour: str = "", instance: Any = None,
                       tenant_id: str = "default") -> AgentInfo:
        """Register a new agent with the registry.

        Args:
            agent_id: Unique agent identifier
            name: Human-readable name
            role: Agent role (e.g. 'researcher', 'architect', 'security-analyst')
            agent_type: Type of agent (e.g. 'SPECIALIST', 'LLM', 'HUMAN')
            capabilities: Capabilities this agent declares
            allowed_operations: Operations the agent may perform
            prohibited_operations: Operations the agent may never perform
            scope: Scope boundary for the agent
            expected_behaviour: Description of expected behavior
            instance: The actual agent implementation instance
            tenant_id: Tenant scope for this agent

        Returns:
            AgentInfo for the registered agent
        """
        info = AgentInfo(
            agent_id=agent_id,
            name=name,
            role=role,
            agent_type=agent_type,
            capabilities=capabilities,
            allowed_operations=allowed_operations,
            prohibited_operations=prohibited_operations,
            scope=scope or {},
            expected_behaviour=expected_behaviour,
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        self._agents[agent_id] = RegisteredAgent(info=info, instance=instance)

        if tenant_id not in self._tenant_agents:
            self._tenant_agents[tenant_id] = []
        if agent_id not in self._tenant_agents[tenant_id]:
            self._tenant_agents[tenant_id].append(agent_id)

        return info

    def unregister_agent(self, agent_id: str) -> bool:
        """Remove an agent from the registry."""
        if agent_id not in self._agents:
            return False
        self._agents.pop(agent_id)
        for tenant_id, agent_ids in self._tenant_agents.items():
            if agent_id in agent_ids:
                agent_ids.remove(agent_id)
        return True

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        """Look up an agent by ID."""
        registered = self._agents.get(agent_id)
        return registered.info if registered else None

    def get_agent_instance(self, agent_id: str) -> Any | None:
        """Get the actual agent implementation instance."""
        registered = self._agents.get(agent_id)
        return registered.instance if registered else None

    def list_agents(self, tenant_id: str | None = None, status: str | None = None) -> list[AgentInfo]:
        """List all registered agents, optionally filtered by tenant and status."""
        result = []
        if tenant_id:
            agent_ids = self._tenant_agents.get(tenant_id, [])
            for aid in agent_ids:
                registered = self._agents.get(aid)
                if registered and (status is None or registered.info.status == status):
                    result.append(registered.info)
        else:
            for registered in self._agents.values():
                if status is None or registered.info.status == status:
                    result.append(registered.info)
        return result

    def select_agent(self, required_capabilities: list[str], tenant_id: str | None = None,
                     agent_id: str | None = None) -> AgentInfo | None:
        """Select an agent that satisfies the required capabilities.

        Selection priority:
        1. If agent_id is provided, use that agent (if it matches capabilities and is active)
        2. Otherwise, find the first active agent whose declared capabilities
           are a superset of the required capabilities

        Args:
            required_capabilities: Capabilities the task requires
            tenant_id: Optional tenant scope for agent lookup
            agent_id: Optional explicit agent assignment

        Returns:
            AgentInfo for the selected agent, or None if no suitable agent found
        """
        if not required_capabilities:
            required_capabilities = []

        if agent_id:
            registered = self._agents.get(agent_id)
            if registered and registered.info.status == AGENT_STATUS_ACTIVE:
                if registered.info.matches_capabilities(required_capabilities):
                    return registered.info
            return None

        candidates = self.list_agents(tenant_id=tenant_id, status=AGENT_STATUS_ACTIVE)
        for agent in candidates:
            if agent.matches_capabilities(required_capabilities):
                return agent
        return None

    def update_agent_status(self, agent_id: str, status: str) -> bool:
        """Update an agent's status."""
        if status not in VALID_AGENT_STATUSES:
            raise ValueError(f"invalid agent status: {status}")
        registered = self._agents.get(agent_id)
        if registered is None:
            return False
        registered.info.status = status
        registered.info.updated_at = utc_now()
        return True

    def get_agents_for_tenant(self, tenant_id: str) -> dict[str, RegisteredAgent]:
        """Get all registered agents for a tenant."""
        result = {}
        for aid in self._tenant_agents.get(tenant_id, []):
            registered = self._agents.get(aid)
            if registered:
                result[aid] = registered
        return result

    # ---- Lifecycle state (Phase 2): the registry is the source of agent
    # selection AND of who is currently doing what. The engine reports
    # task start/finish here; the data stays in-memory per process while
    # task assignment itself stays durable in SQLite. --------------------

    def mark_task_started(self, agent_id: str, task_id: str) -> None:
        """Record that an agent started a task (lifecycle: BUSY)."""
        registered = self._agents.get(agent_id)
        if registered is None:
            return
        registered.current_task = task_id
        registered.last_active = utc_now()

    def mark_task_finished(self, agent_id: str, task_id: str, status: str) -> None:
        """Record that an agent finished a task (lifecycle: AVAILABLE)."""
        registered = self._agents.get(agent_id)
        if registered is None:
            return
        if registered.current_task == task_id:
            registered.current_task = None
        registered.execution_history.append(
            {"task_id": task_id, "status": status, "finished_at": utc_now()}
        )
        registered.last_active = utc_now()

    def lifecycle_snapshot(self) -> list[dict[str, Any]]:
        """Queryable lifecycle state for every registered agent."""
        snapshot = []
        for agent_id, registered in self._agents.items():
            snapshot.append({
                "agent_id": agent_id,
                "name": registered.info.name,
                "role": registered.info.role,
                "status": registered.info.status,
                "lifecycle": "BUSY" if registered.current_task else "AVAILABLE",
                "current_task": registered.current_task,
                "capabilities": list(registered.info.capabilities),
                "allowed_operations": list(registered.info.allowed_operations),
                "prohibited_operations": list(registered.info.prohibited_operations),
                "execution_history": list(registered.execution_history),
                "last_active": registered.last_active,
            })
        return snapshot

    def describe_agents(self) -> list[dict[str, Any]]:
        """Full agent contracts: identity, role, capabilities, I/O contracts,
        tool permissions, artifact/message behavior, and lifecycle state."""
        descriptions = []
        for agent_id, registered in self._agents.items():
            instance = registered.instance
            descriptions.append({
                "agent_id": agent_id,
                "name": registered.info.name,
                "role": registered.info.role,
                "agent_type": registered.info.agent_type,
                "capabilities": list(registered.info.capabilities),
                "allowed_operations": list(registered.info.allowed_operations),
                "prohibited_operations": list(registered.info.prohibited_operations),
                "input_contract": list(getattr(instance, "input_contract", ("unspecified",))),
                "output_contract": list(getattr(instance, "output_contract", ("unspecified",))),
                "tool_permissions": list(getattr(instance, "tool_permissions", ("unspecified",))),
                "artifact_behavior": getattr(instance, "artifact_behavior", "unspecified"),
                "message_behavior": getattr(instance, "message_behavior", "unspecified"),
                "status": registered.info.status,
                "lifecycle": "BUSY" if registered.current_task else "AVAILABLE",
                "current_task": registered.current_task,
                "execution_history": list(registered.execution_history),
            })
        return descriptions
