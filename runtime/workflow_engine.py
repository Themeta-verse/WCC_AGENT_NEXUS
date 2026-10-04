"""NEXUS Workflow Engine — general-purpose multi-agent workflow orchestration.

This module extends the existing MissionComposer infrastructure with a
durable, agent-aware workflow engine. It does NOT replace MissionComposer:
MissionComposer remains the sole planner, executor, verifier, and
LocalStateStore checkpoint author. The WorkflowEngine manages the lifecycle
of workflows that span multiple agents, tasks, and artifacts.

Design principles:
  - Extend existing infrastructure, do not duplicate it.
  - Workflow tasks reuse the MissionTask/MissionTask dataclass concepts.
  - Artifacts reuse the AgentArtifact provenance model.
  - Reality states come from canonical_core.REALITY_STATES.
  - Execution receipts reuse persistent_fabric.ExecutionReceipt.
  - The engine emits structured events for external observers (e.g. GuardDog).
  - Model outputs are always INFERRED and untrusted (truth boundary preserved).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import sys

try:
    from canonical_core import REALITY_STATES, core_id, utc_now
    from persistent_fabric import LocalStateStore
    from mission_composer import MissionComposer, MissionTask as CanonicalMissionTask
except ImportError:
    from .canonical_core import REALITY_STATES, core_id, utc_now
    from .persistent_fabric import LocalStateStore
    from .mission_composer import MissionComposer, MissionTask as CanonicalMissionTask


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    """SHA-256 digest of a JSON-serializable value, stable against volatile keys."""
    volatile = {"id", "execution_id", "request_id", "timestamp", "start_time",
                "end_time", "created_at", "updated_at"}
    if isinstance(value, dict):
        cleaned = {k: _digest(v) if isinstance(v, (dict, list)) else v
                   for k, v in sorted(value.items()) if k not in volatile}
    elif isinstance(value, list):
        cleaned = [_digest(v) if isinstance(v, (dict, list)) else v for v in value]
    else:
        cleaned = value
    return hashlib.sha256(json.dumps(cleaned, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class WorkflowArtifact:
    """An artifact produced by an agent executing a task."""
    artifact_id: str
    workflow_id: str
    task_id: str | None
    agent_id: str | None
    kind: str
    name: str
    content: dict[str, Any] | str | None
    content_hash: str
    parent_artifacts: list[str]
    created_at: str
    reality: str = "INFERRED"
    untrusted: bool = True
    verification_state: str = "UNVERIFIED"
    provenance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "kind": self.kind,
            "name": self.name,
            "content_hash": self.content_hash,
            "content_preview": (self.content[:500] if isinstance(self.content, str)
                                else json.dumps(self.content, default=str)[:500]) if self.content else None,
            "content_size": len(self.content) if isinstance(self.content, str) else len(json.dumps(self.content, default=str)) if self.content else 0,
            "parent_artifacts": self.parent_artifacts,
            "created_at": self.created_at,
            "reality": self.reality,
            "untrusted": self.untrusted,
            "verification_state": self.verification_state,
            "provenance": self.provenance,
        }


@dataclass
class WorkflowEvent:
    """A structured runtime event for observability."""
    event_id: str
    workflow_id: str
    task_id: str | None
    agent_id: str | None
    event_type: str
    detail: dict[str, Any]
    timestamp: str
    provenance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workflow_id": self.workflow_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "detail": self.detail,
            "timestamp": self.timestamp,
            "provenance": self.provenance,
        }


@dataclass
class WorkflowTask:
    """A task within a workflow graph, extending the MissionTask concept."""
    task_id: str
    workflow_id: str
    task_type: str
    name: str
    agent_id: str | None
    required_capabilities: list[str]
    depends_on: list[str]
    input_artifacts: list[str]
    output_artifacts: list[str]
    status: str = "PENDING"
    reality: str = "UNKNOWN"
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None
    result: dict[str, Any] | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def is_ready(self) -> bool:
        """A task is ready when all dependencies have completed."""
        if self.depends_on:
            return False
        return self.status == "PENDING"

    def dependencies_completed(self, all_tasks: list["WorkflowTask"]) -> bool:
        """Check if all dependency tasks are COMPLETED."""
        task_map = {t.task_id: t for t in all_tasks}
        return all(
            task_map.get(dep) and task_map[dep].status == "COMPLETED"
            for dep in self.depends_on
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "task_type": self.task_type,
            "name": self.name,
            "agent_id": self.agent_id,
            "required_capabilities": self.required_capabilities,
            "depends_on": self.depends_on,
            "input_artifacts": self.input_artifacts,
            "output_artifacts": self.output_artifacts,
            "status": self.status,
            "reality": self.reality,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error": self.error,
            "result": self.result,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class WorkflowExecutionPolicy:
    """Policy governing workflow execution behavior."""
    max_retries_default: int = 3
    timeout_seconds_default: int = 300
    fail_on_agent_not_available: bool = True
    auto_retry_on_failure: bool = True


@dataclass
class WorkflowSpec:
    """Specification for creating a workflow from a user goal."""
    name: str
    objective: str
    scope: str
    task_specs: list[dict[str, Any]]
    agents: list[dict[str, Any]]
    execution_mode: str = "REAL_READ"
    # Phase E: bounded execution environment (LOCAL | SANDBOX). Unknown
    # values resolve to LOCAL at creation; agents/tools enforce the policy.
    execution_environment: str = "LOCAL"


TASK_STATES = {
    "PENDING", "READY", "RUNNING", "WAITING",
    "COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "AWAITING_APPROVAL"
}

WORKFLOW_STATES = {
    "PENDING", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"
}


class WorkflowEngine:
    """Manages the lifecycle of multi-agent workflows.

    The WorkflowEngine is the orchestration layer between:
    - MissionComposer (planner/verifier — produces task graph specs)
    - Agent registry (provides available agents with capabilities)
    - Persistence (SQLite workflows, tasks, artifacts, events)

    It does NOT replace MissionComposer. It uses MissionComposer for:
    - Intent classification (classify_mission_intent)
    - Task graph compilation (_task_graph)
    - Capability resolution
    - Verification

    The WorkflowEngine adds:
    - Persistent workflow/task state across process restarts
    - Dynamic agent assignment based on capability matching
    - Artifact handoff between agent-executed tasks
    - Event emission for observability
    - Failure/retry handling
    """

    def __init__(
        self,
        database: Any = None,
        composer: MissionComposer | None = None,
        policy: WorkflowExecutionPolicy | None = None,
        agent_registry: Any = None,
        artifacts_root: str | None = None,
        messaging_hub: Any = None,
    ):
        self.database = database
        self.composer = composer or MissionComposer()
        self.policy = policy or WorkflowExecutionPolicy()
        self._executor: Any = None
        self._agent_registry = agent_registry
        self._artifacts_root = Path(artifacts_root) if artifacts_root else None
        self._messaging_hub = messaging_hub

    @property
    def messaging_hub(self) -> Any:
        if self._messaging_hub is None:
            from runtime.messaging_hub import MessagingHub
            if self.database is None:
                raise RuntimeError("MessagingHub requires a database — pass messaging_hub= explicitly")
            self._messaging_hub = MessagingHub(self.database)
        return self._messaging_hub

    @messaging_hub.setter
    def messaging_hub(self, hub: Any) -> None:
        self._messaging_hub = hub

    def set_executor(self, executor: Any, agent_registry: Any = None,
                     connector_registry: Any = None) -> None:
        """Inject the agent task executor (separated for testability).

        If agent_registry is provided, it is wired into the executor so the
        executor can dispatch to the correct agent instance.
        If connector_registry is provided, it is wired into the executor so
        the executor can dispatch GitHub connector capabilities.
        """
        self._executor = executor
        if agent_registry:
            self._agent_registry = agent_registry
        if hasattr(executor, "registry") and self._agent_registry:
            executor.registry = self._agent_registry
        if hasattr(executor, "messaging_hub") and self._messaging_hub:
            executor.messaging_hub = self._messaging_hub
        if connector_registry is not None and hasattr(executor, "connector_registry"):
            executor.connector_registry = connector_registry

    @property
    def executor(self) -> Any:
        if self._executor is None:
            raise RuntimeError("WorkflowEngine requires an executor — call set_executor() first")
        return self._executor

    # ------------------------------------------------------------------
    # Workflow creation
    # ------------------------------------------------------------------

    def create_workflow(
        self,
        tenant_id: str,
        project_id: str,
        spec: WorkflowSpec,
    ) -> dict[str, Any]:
        """Create a new workflow from a specification.

        Uses MissionComposer for intent classification and capability resolution,
        then creates persistent workflow tasks in the database.
        """
        from runtime.mission_composer import classify_mission_intent, _task_graph as _compile_task_graph, CapabilityRequirement

        workflow_id = f"workflow-{core_id('wf')}"

        # Use MissionComposer's intent classifier to refine the spec
        intent_classification = classify_mission_intent(spec.objective)
        capability = intent_classification.get("capability", "knowledge.read")

        # Phase E: validate the execution environment once, at creation.
        from runtime.execution_environment import resolve_environment
        execution_environment = resolve_environment(
            getattr(spec, "execution_environment", "LOCAL")).value

        # Compile the task graph using MissionComposer's infrastructure
        task_specs = spec.task_specs
        plan = self._compile_plan(spec, intent_classification)
        plan["execution_environment"] = execution_environment

        # Create the workflow record
        workflow = self.database.create_workflow(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            name=spec.name,
            objective=spec.objective,
            scope=spec.scope,
            plan_json=json.dumps(plan),
        )

        # Create workflow events for observability
        self.database.add_workflow_event(
            event_id=f"evt-{core_id('evt')}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            event_type="workflow_created",
            detail={
                "name": spec.name,
                "objective": spec.objective,
                "scope": spec.scope,
                "capability": capability,
                "task_count": len(task_specs),
                "agent_count": len(spec.agents),
                "execution_environment": execution_environment,
            },
        )

        # Phase E: task_ids are globally unique in SQLite, but planners emit
        # deterministic `task-N` ids. When this database already holds such
        # ids (multi-workflow operator DB), remap this workflow's tasks with
        # a workflow-scoped suffix — consistently across task_id, depends_on
        # and agent references — instead of failing. No collision: ids stay
        # exactly as planned (existing tests unaffected).
        try:
            with self.database.connect() as _db:
                _taken = {r[0] for r in _db.execute("SELECT task_id FROM workflow_tasks").fetchall()}
        except Exception:
            _taken = set()
        _planned_ids = {ts["task_id"] for ts in task_specs}
        if _planned_ids & _taken:
            # Full workflow-unique suffix (short timestamp prefixes repeat
            # across workflows created seconds apart — that re-collided).
            _suffix = workflow_id[len("workflow-"):] if workflow_id.startswith("workflow-") else workflow_id
            _remap = {tid: f"{tid}-{_suffix}" for tid in _planned_ids}
            for ts in task_specs:
                ts["task_id"] = _remap[ts["task_id"]]
                ts["depends_on"] = [_remap.get(d, d) for d in ts.get("depends_on", [])]

        # Create tasks from spec
        for ts in task_specs:
            self.database.create_workflow_task(
                task_id=ts["task_id"],
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_type=ts["task_type"],
                name=ts["name"],
                agent_id=ts.get("agent_id"),
                required_capabilities=ts.get("required_capabilities", []),
                depends_on=ts.get("depends_on", []),
                max_retries=self.policy.max_retries_default,
                input_artifacts=ts.get("input_artifacts", []),
            )

            self.database.add_workflow_event(
                event_id=f"evt-{core_id('evt')}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=ts["task_id"],
                event_type="task_created",
                detail={
                    "task_id": ts["task_id"],
                    "task_type": ts["task_type"],
                    "name": ts["name"],
                    "required_capabilities": ts.get("required_capabilities", []),
                    "depends_on": ts.get("depends_on", []),
                },
            )

        # Register agents if provided
        for ag in spec.agents:
            self._ensure_agent(tenant_id, project_id, ag)

        return self.database.get_workflow(tenant_id, workflow_id)

    def _compile_plan(self, spec: WorkflowSpec, intent_classification: dict[str, Any]) -> dict[str, Any]:
        """Compile a workflow plan, reusing MissionComposer's capability contracts."""
        tasks = []
        for ts in spec.task_specs:
            tasks.append({
                "task_id": ts["task_id"],
                "name": ts["name"],
                "task_type": ts["task_type"],
                "depends_on": ts.get("depends_on", []),
                "required_capabilities": ts.get("required_capabilities", []),
                "agent_id": ts.get("agent_id"),
            })

        # Use _task_graph for topological ordering
        from runtime.mission_composer import MissionTask as CanonicalMissionTask, _task_graph as _compile_task_graph
        canonical_tasks = []
        for ts in spec.task_specs:
            canonical_tasks.append(CanonicalMissionTask(
                task_id=ts["task_id"],
                title=ts["name"],
                kind=ts["task_type"],
                depends_on=ts.get("depends_on", []),
                relation="SEQUENTIAL",
                capability_requirement=None,
                specialist=ts.get("agent_id"),
                state="PLANNED",
                reality="PLANNED",
                side_effect_risk="NONE",
                retryable=True,
                evidence_ids=[],
                output={},
                failure=None,
            ))
        graph = _compile_task_graph(canonical_tasks)

        return {
            "workflow_id": f"workflow-{core_id('plan')}",
            "objective": spec.objective,
            "scope": spec.scope,
            "capability": intent_classification.get("capability"),
            "mission_type": intent_classification.get("mission_type"),
            "tasks": tasks,
            "task_graph": graph,
            "agents": spec.agents,
        }

    def _ensure_agent(self, tenant_id: str, project_id: str, agent_spec: dict[str, Any]) -> None:
        """Ensure an agent exists in the registry, creating if needed."""
        agent_id = agent_spec["agent_id"]
        existing = self.database.get_agent(tenant_id, agent_id)
        if existing is None:
            agent = self.database.create_agent(
                agent_id=agent_id,
                tenant_id=tenant_id,
                project_id=project_id,
                display_name=agent_spec.get("name", agent_id),
                status="ACTIVE",
            )
            # Create initial policy
            self.database.create_agent_policy(
                policy_id=f"policy-{core_id('pol')}",
                agent_id=agent_id,
                tenant_id=tenant_id,
                declared_capabilities=agent_spec.get("capabilities", []),
                allowed_operations=agent_spec.get("allowed_operations", ["read"]),
                prohibited_operations=agent_spec.get("prohibited_operations", ["write", "execute"]),
                scope=agent_spec.get("scope", {"project_id": project_id}),
                expected_behaviour=agent_spec.get("expected_behaviour", f"{agent_spec.get('name', agent_id)} agent"),
                version=1,
            )

    # ------------------------------------------------------------------
    # Workflow lifecycle
    # ------------------------------------------------------------------

    def start_workflow(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Start a workflow: mark tasks ready without executing them.

        Phase 4: Tasks are transitioned from PENDING to READY and agents are
        assigned. Actual execution is left to the durable worker (or explicit
        step() calls). This is backward-compatible: step() will dispatch and
        execute any READY tasks.
        """
        workflow = self.database.get_workflow(tenant_id, workflow_id)
        if workflow is None:
            raise ValueError(f"workflow '{workflow_id}' not found")
        if workflow["status"] != "PENDING":
            raise ValueError(f"workflow '{workflow_id}' cannot be started from status '{workflow['status']}'")

        self.database.update_workflow_status(tenant_id, workflow_id, "RUNNING")

        self.database.add_workflow_event(
            event_id=f"evt-{core_id('evt')}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            event_type="workflow_started",
            detail={"workflow_id": workflow_id, "at": _now()},
        )

        # Mark ready tasks without executing (worker or step() dispatches)
        self._mark_ready_tasks(tenant_id, project_id, workflow_id)

        return self.database.get_workflow(tenant_id, workflow_id)

    def _mark_ready_tasks(self, tenant_id: str, project_id: str, workflow_id: str) -> list[str]:
        """Find PENDING tasks whose deps are completed, transition to READY, assign agents."""
        tasks = self.database.list_workflow_tasks(tenant_id, workflow_id)
        ready: list[str] = []

        task_ids = {t["task_id"] for t in tasks}
        task_map = {t["task_id"]: t for t in tasks}
        for task in tasks:
            if task["status"] != "PENDING":
                continue
            # A dependency that does not exist is never "satisfied": block
            # honestly instead of treating all([]) == True as ready.
            missing = [d for d in (task["depends_on"] or []) if d not in task_ids]
            if missing:
                self.database.update_task_status(
                    tenant_id, task["task_id"], "BLOCKED",
                    error=f"missing dependency: {missing[0]}",
                )
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    event_type="task_blocked",
                    detail={"reason": f"missing dependency: {missing[0]}", "missing": missing},
                )
                continue
            if not all(task_map[d]["status"] == "COMPLETED" for d in (task["depends_on"] or [])):
                continue
            self.database.update_task_status(tenant_id, task["task_id"], "READY")
            self.database.add_workflow_event(
                event_id=f"evt-{core_id('evt')}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task["task_id"],
                event_type="task_ready",
                detail={"task_id": task["task_id"], "name": task["name"]},
            )

            agent_id = self._assign_agent(tenant_id, project_id, task)
            if agent_id:
                self.database.assign_task_to_agent(tenant_id, task["task_id"], agent_id)
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="agent_selected",
                    detail={
                        "task_id": task["task_id"],
                        "agent_id": agent_id,
                        "agent_role": task.get("agent_id", ""),
                        "required_capabilities": task["required_capabilities"],
                        "capability_match": "pre-assigned" if task.get("agent_id") == agent_id else "capability-matched",
                    },
                )
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="task_assigned",
                    detail={"task_id": task["task_id"], "agent_id": agent_id, "name": task["name"]},
                )
            else:
                if self.policy.fail_on_agent_not_available:
                    self.database.update_task_status(tenant_id, task["task_id"], "BLOCKED", error="no available agent with required capabilities")
                    self.database.add_workflow_event(
                        event_id=f"evt-{core_id('evt')}",
                        workflow_id=workflow_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        task_id=task["task_id"],
                        event_type="task_blocked",
                        detail={"reason": "no available agent with required capabilities"},
                    )
                continue

            ready.append(task["task_id"])

        return ready

    def _execute_ready_tasks(self, tenant_id: str, project_id: str, workflow_id: str) -> list[str]:
        """Execute all tasks currently in READY state."""
        tasks = self.database.list_workflow_tasks(tenant_id, workflow_id)
        executed: list[str] = []

        for task in tasks:
            if task["status"] != "READY":
                continue
            agent_id = task.get("agent_id")
            if not agent_id:
                agent_id = self._assign_agent(tenant_id, project_id, task)
                if agent_id:
                    self.database.assign_task_to_agent(tenant_id, task["task_id"], agent_id)
            if agent_id is None:
                if self.policy.fail_on_agent_not_available:
                    self.database.update_task_status(tenant_id, task["task_id"], "BLOCKED", error="no available agent with required capabilities")
                continue

            executed.append(task["task_id"])
            self._execute_task(tenant_id, project_id, workflow_id, task, agent_id)

        return executed

    def _dispatch_ready_tasks(self, tenant_id: str, project_id: str, workflow_id: str) -> list[str]:
        """Backward-compatible dispatch: mark ready then execute READY tasks."""
        self._mark_ready_tasks(tenant_id, project_id, workflow_id)
        return self._execute_ready_tasks(tenant_id, project_id, workflow_id)

    def _assign_agent(self, tenant_id: str, project_id: str, task: dict[str, Any]) -> str | None:
        """Find an available agent matching the task's required capabilities.

        Selection priority:
        1. If task has agent_id, check if it's active in the DB or registry
        2. Find an active agent (DB or registry) whose capabilities match requirements
        """
        required = set(task["required_capabilities"] or [])
        task_agent_id = task.get("agent_id")

        # Try database-based agent lookup first
        db = self.database
        if db is not None and hasattr(db, "list_agents"):
            agents = db.list_agents(tenant_id, project_id)
            if task_agent_id:
                for a in agents:
                    if a["agent_id"] == task_agent_id and a["status"] == "ACTIVE":
                        if not required or self._agent_has_capabilities(db, tenant_id, task_agent_id, required):
                            return task_agent_id
            for a in agents:
                if a["status"] not in ("ACTIVE",):
                    continue
                if task_agent_id and a["agent_id"] == task_agent_id:
                    return a["agent_id"]
                if required:
                    policy = db.get_latest_agent_policy(tenant_id, a["agent_id"])
                    if policy is None:
                        continue
                    declared = set(json.loads(policy["declared_capabilities_json"]))
                    if required.issubset(declared):
                        return a["agent_id"]
                else:
                    return a["agent_id"]

        # Fallback to AgentRegistry (in-memory)
        if self._agent_registry is not None:
            if task_agent_id:
                agent_info = self._agent_registry.get_agent(task_agent_id)
                if agent_info and agent_info.status == "ACTIVE":
                    if not required or set(agent_info.capabilities).issuperset(required):
                        return task_agent_id
            # Find any active agent matching capabilities (try without tenant first, then with)
            candidates = self._agent_registry.list_agents(status="ACTIVE")
            if not required:
                if task_agent_id:
                    return task_agent_id
                for a in candidates:
                    return a.agent_id
            for a in candidates:
                if set(a.capabilities).issuperset(required):
                    return a.agent_id

        return None

    def _agent_has_capabilities(self, db: Any, tenant_id: str, agent_id: str, required: set[str]) -> bool:
        """Check if an agent in the DB has the required capabilities."""
        policy = db.get_latest_agent_policy(tenant_id, agent_id)
        if policy is None:
            return False
        declared = set(json.loads(policy["declared_capabilities_json"]))
        return required.issubset(declared)

    def _execute_task(
        self,
        tenant_id: str,
        project_id: str,
        workflow_id: str,
        task: dict[str, Any],
        agent_id: str,
    ) -> None:
        """Execute a single task via the injected executor.

        Phase 9: every engine-driven attempt gets an execution_id linked to
        the task row, so artifacts/events stay attributable across recovery.
        """
        execution_id = f"exec-{core_id('exec')}"
        self.database.assign_task_to_agent(tenant_id, task["task_id"], agent_id)
        self.database.update_task_status(tenant_id, task["task_id"], "RUNNING")
        try:
            self.database.set_task_execution(tenant_id, task["task_id"], execution_id)
        except Exception:
            pass

        # Phase E: the workflow's environment reaches every agent through
        # task parameters (agents forward it to the model/tool layer).
        workflow = self.database.get_workflow(tenant_id, workflow_id)
        execution_environment = "LOCAL"
        try:
            _plan = json.loads((workflow or {}).get("plan_json") or "{}")
            from runtime.execution_environment import resolve_environment as _resolve_env
            execution_environment = _resolve_env(_plan.get("execution_environment")).value
        except (ValueError, TypeError, AttributeError):
            execution_environment = "LOCAL"

        self.database.add_workflow_event(
            event_id=f"evt-{core_id('evt')}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task["task_id"],
            agent_id=agent_id,
            event_type="agent_started",
            detail={"task_id": task["task_id"], "agent_id": agent_id,
                    "execution_id": execution_id,
                    "attempt": int(task.get("claim_count") or 0) + 1,
                    "execution_environment": execution_environment},
        )

        try:
            observation_scope = workflow.get("scope") if workflow else None

            task_result = self.executor.execute_task(
                workflow_id=workflow_id,
                task_id=task["task_id"],
                task_type=task["task_type"],
                task_name=task["name"],
                agent_id=agent_id,
                capabilities=task["required_capabilities"],
                scope=observation_scope or workflow_id,
                observation_scope=observation_scope,
                input_artifacts=task["input_artifacts"],
                parameters={"execution_environment": execution_environment},
            )

            # Handle both AgentExecutionResult dataclass and TaskResult dict return types.
            #
            # A result that does not state a status states NOTHING. Defaulting
            # that to COMPLETED turned "the agent said nothing" into "the task
            # succeeded" and persisted it downstream. Absent or unrecognized
            # status is now UNKNOWN, which is a non-success terminal state that
            # the workflow surfaces honestly.
            if hasattr(task_result, "artifacts"):
                artifacts = task_result.artifacts
                realty = getattr(task_result, "reality", "INFERRED")
                untrusted = getattr(task_result, "untrusted", True)
                result_dict = getattr(task_result, "result", {})
                provenance = getattr(task_result, "provenance", [f"agent:{agent_id}"])
                exec_meta = getattr(task_result, "execution_metadata", {}) or {}
                result_status = getattr(task_result, "status", None)
                result_error = getattr(task_result, "error", None)
            elif hasattr(task_result, "get"):
                artifacts = task_result.get("artifacts", [])
                realty = task_result.get("reality", "INFERRED")
                untrusted = task_result.get("untrusted", True)
                result_dict = task_result.get("result", {})
                provenance = task_result.get("provenance", [f"agent:{agent_id}"])
                exec_meta = task_result.get("execution_metadata", {}) or {}
                result_status = task_result.get("status")
                result_error = task_result.get("error")
            else:
                # Not a result object at all: explicit failure, never success.
                artifacts = []
                realty = "UNKNOWN"
                untrusted = True
                result_dict = {}
                provenance = [f"agent:{agent_id}"]
                exec_meta = {}
                result_status = "FAILED"
                result_error = (
                    f"executor returned {type(task_result).__name__}, "
                    f"which is not a task result"
                )
            if not isinstance(result_status, str) or not result_status.strip():
                result_status = "FAILED"
                result_error = result_error or "task result carried no status; treated as FAILED, never COMPLETED"
            elif result_status.strip().upper() not in ("COMPLETED", "FAILED", "BLOCKED"):
                result_status = "FAILED"
                result_error = (
                    f"task result carried a non-terminal status {result_status!r}; "
                    f"treated as FAILED, never COMPLETED"
                )
            else:
                result_status = result_status.strip().upper()
            # Defensive: agent-declared reality must be a known state; coerce
            # unknown values to UNKNOWN instead of crashing artifact persistence.
            _allowed_realities = {"OBSERVED", "INFERRED", "VERIFIED", "UNVERIFIED", "UNKNOWN"}
            if realty not in _allowed_realities:
                realty = "UNKNOWN"

            # Persist auditable tool executions reported by the agent (e.g.
            # filesystem.read / github.repository.read receipts from the
            # researcher). Each receipt is a real observation or an honest
            # refusal — never a summary. Connector identity fields are kept so
            # the trace proves which connector executed which capability.
            for tool_use in (exec_meta.get("tool_executions") or []):
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="tool_used",
                    detail={
                        "capability": tool_use.get("capability"),
                        "connector_id": tool_use.get("connector_id"),
                        "provider": tool_use.get("provider"),
                        "operation": tool_use.get("operation"),
                        "target": tool_use.get("target"),
                        "status": tool_use.get("status"),
                        "reality": tool_use.get("reality"),
                        "content_sha256": tool_use.get("content_sha256") or tool_use.get("result_hash"),
                        "receipt_id": tool_use.get("receipt_id"),
                        "result_hash": tool_use.get("result_hash"),
                        "workspace_root": tool_use.get("workspace_root"),
                    },
                )

            # Phase 7: persist model invocation / tool-request / failure events
            # reported by model-backed agents. All details are pre-redacted by
            # the agent layer (no API keys ever reach the database).
            for inv in (exec_meta.get("model_invocations") or []):
                if not isinstance(inv, dict):
                    continue
                inv_type = str(inv.get("type", "model_invocation"))
                allowed_types = {
                    "model_invocation", "model_result", "model_failure",
                    "model_timeout", "tool_requested", "tool_result",
                    "tool_rejected", "tool_limit_exceeded",
                    "max_iterations_exceeded", "model_fallback",
                }
                if inv_type not in allowed_types:
                    inv_type = "model_invocation"
                # Strip any accidental secret-bearing keys defensively.
                detail = {k: v for k, v in inv.items() if k != "api_key" and "secret" not in k.lower() and "token" not in k.lower() or k in ("content_sha256",)}
                # Keep event detail bounded.
                for k, v in list(detail.items()):
                    if isinstance(v, str) and len(v) > 2000:
                        detail[k] = v[:2000] + "...[truncated]"
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type=inv_type,
                    detail=detail,
                )
            # Persist declared execution strategy (honest mode reporting).
            _strategy = exec_meta.get("execution_strategy")
            if isinstance(_strategy, dict) and _strategy:
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="execution_strategy",
                    detail={
                        "requested": _strategy.get("requested"),
                        "effective": _strategy.get("effective"),
                        "provider": _strategy.get("provider"),
                        "model": _strategy.get("model"),
                        "model_used": exec_meta.get("model_used", False),
                    },
                )

            # Emit artifact_consumed events for input artifacts
            for input_art_name in (task.get("input_artifacts") or []):
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="artifact_consumed",
                    detail={
                        "artifact_ref": input_art_name,
                        "agent_id": agent_id,
                        "task_id": task["task_id"],
                    },
                )

            # Store any artifacts produced — write content to disk
            produced_artifact_ids: list[str] = []
            for artifact_data in (artifacts or []):
                artifact_id = f"art-{core_id('art')}"
                content = artifact_data.get("content")
                content_path = self._persist_artifact_content(artifact_id, content)

                _art_reality = artifact_data.get("reality", realty)
                if _art_reality not in _allowed_realities:
                    _art_reality = "UNKNOWN"
                _art_ver = artifact_data.get("verification_state", "UNVERIFIED")
                if _art_ver not in ("UNVERIFIED", "VERIFIED", "FAILED", "UNKNOWN"):
                    _art_ver = "UNVERIFIED"
                artifact = self.database.create_artifact(
                    artifact_id=artifact_id,
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    kind=artifact_data.get("kind", "unknown"),
                    name=artifact_data.get("name", f"artifact_{artifact_id}"),
                    content_hash=artifact_data.get("content_hash", _digest(content if content else "")),
                    parent_artifacts=artifact_data.get("parent_artifacts", []),
                    content_path=content_path,
                    content_size=artifact_data.get("content_size"),
                    reality=_art_reality,
                    untrusted=artifact_data.get("untrusted", untrusted),
                    verification_state=_art_ver,
                    provenance=artifact_data.get("provenance", provenance),
                    execution_id=execution_id,
                )
                # Phase 9 idempotency: a recovered attempt reproducing the same
                # artifact reuses the existing row (no duplicate effect). Drop
                # the orphan content file and reference the surviving row.
                surviving_id = artifact.get("artifact_id", artifact_id)
                if artifact.get("deduplicated") and content_path and artifact.get("content_path") != content_path:
                    try:
                        Path(content_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                produced_artifact_ids.append(surviving_id)
                self.database.add_output_artifact(tenant_id, task["task_id"], surviving_id)

                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="artifact_produced",
                    detail={
                        "artifact_id": surviving_id,
                        "kind": artifact["kind"],
                        "name": artifact["name"],
                        "content_hash": artifact["content_hash"],
                        "reality": artifact_data.get("reality", realty),
                        "provenance": artifact_data.get("provenance", provenance),
                        "parent_artifacts": artifact_data.get("parent_artifacts", []),
                        "execution_id": execution_id,
                        "deduplicated": bool(artifact.get("deduplicated")),
                    },
                )

            # Respect explicit agent FAILED: a GitHub-required task that could
            # not observe must fail honestly (with retry) instead of being
            # marked COMPLETED with zero observations.
            if result_status == "FAILED":
                _err = result_error or (result_dict.get("error") if isinstance(result_dict, dict) else None) or "agent reported FAILED"
                task_record = self.database.get_workflow_task(tenant_id, task["task_id"])
                _retries = task_record.get("retry_count", 0) if task_record else 0
                _max = task_record.get("max_retries", self.policy.max_retries_default) if task_record else self.policy.max_retries_default
                if task_record and _retries < _max and self.policy.auto_retry_on_failure:
                    self.database.increment_task_retry(tenant_id, task["task_id"])
                    self.database.add_workflow_event(
                        event_id=f"evt-{core_id('evt')}",
                        workflow_id=workflow_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        task_id=task["task_id"],
                        agent_id=agent_id,
                        event_type="task_retried",
                        detail={"attempt": _retries + 1, "error": str(_err)},
                    )
                    self.database.update_task_status(tenant_id, task["task_id"], "PENDING", error=None)
                else:
                    self.database.update_task_status(
                        tenant_id, task["task_id"], "FAILED",
                        reality="UNKNOWN", error=str(_err),
                        result_json=json.dumps(result_dict),
                    )
                    self.database.add_workflow_event(
                        event_id=f"evt-{core_id('evt')}",
                        workflow_id=workflow_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        task_id=task["task_id"],
                        agent_id=agent_id,
                        event_type="task_failed",
                        detail={"task_id": task["task_id"], "agent_id": agent_id, "error": str(_err)},
                    )
                    if self.messaging_hub:
                        self.messaging_hub.task_lifecycle(
                            workflow_id=workflow_id,
                            tenant_id=tenant_id,
                            event="TASK_FAILED",
                            agent_id=agent_id,
                            task_id=task["task_id"],
                            details={"error": str(_err)},
                        )
                return

            self.database.update_task_status(
                tenant_id, task["task_id"], "COMPLETED",
                reality=realty,
                result_json=json.dumps(result_dict),
            )

            self.database.add_workflow_event(
                event_id=f"evt-{core_id('evt')}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task["task_id"],
                agent_id=agent_id,
                event_type="agent_completed",
                detail={"task_id": task["task_id"], "agent_id": agent_id, "artifact_count": len(artifacts or [])},
            )

        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            task_record = self.database.get_workflow_task(tenant_id, task["task_id"])
            if task_record and task_record["retry_count"] < task_record["max_retries"] and self.policy.auto_retry_on_failure:
                self.database.increment_task_retry(tenant_id, task["task_id"])
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="task_retried",
                    detail={"attempt": task_record["retry_count"] + 1, "error": error_msg},
                )
                self.database.update_task_status(tenant_id, task["task_id"], "PENDING", error=None)
            else:
                self.database.update_task_status(
                    tenant_id, task["task_id"], "FAILED",
                    reality="UNKNOWN", error=error_msg,
                )
                self.database.add_workflow_event(
                    event_id=f"evt-{core_id('evt')}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task["task_id"],
                    agent_id=agent_id,
                    event_type="task_failed",
                    detail={"task_id": task["task_id"], "agent_id": agent_id, "error": error_msg},
                )
                if self.messaging_hub:
                    self.messaging_hub.task_lifecycle(
                        workflow_id=workflow_id,
                        tenant_id=tenant_id,
                        event="TASK_FAILED",
                        agent_id=agent_id,
                        task_id=task["task_id"],
                        details={"error": error_msg},
                    )

    def _persist_artifact_content(self, artifact_id: str, content: Any | None) -> str | None:
        """Persist artifact content to disk and return the file path.

        If the artifacts_root is not configured, returns None (content is
        tracked via content_hash only).
        """
        if content is None:
            return None
        if self._artifacts_root is None:
            return None
        try:
            artifacts_dir = self._artifacts_root / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            file_path = artifacts_dir / f"{artifact_id}.json"
            if isinstance(content, str):
                file_path.write_text(content, encoding="utf-8")
            else:
                file_path.write_text(json.dumps(content, default=str, indent=2), encoding="utf-8")
            return str(file_path)
        except (OSError, TypeError):
            return None

    def step(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Process one step of workflow execution.

        This method:
        1. Dispatches any ready (READY or PENDING-with-completed-deps) tasks
        2. Checks workflow completion

        Returns the current workflow state. Call repeatedly to advance
        the workflow through all its tasks.
        """
        self._dispatch_ready_tasks(tenant_id, project_id, workflow_id)
        return self.check_workflow_completion(tenant_id, workflow_id)

    def check_workflow_completion(self, tenant_id: str, workflow_id: str) -> dict[str, Any]:
        """Check if all tasks are complete and update workflow status.

        Returns the complete, authoritative execution state straight from the
        database (no fabricated fields): workflow/task states, per-status
        breakdown, produced/consumed artifacts, messages, events, dynamic
        tasks, and progress. Flat keys are kept for backward compatibility
        with AutonomousRuntime and WorkflowWorker.
        """
        tasks = self.database.list_workflow_tasks(tenant_id, workflow_id)
        total = len(tasks)
        completed = sum(1 for t in tasks if t["status"] == "COMPLETED")
        failed = sum(1 for t in tasks if t["status"] == "FAILED")
        running = sum(1 for t in tasks if t["status"] in ("RUNNING", "READY", "AWAITING_APPROVAL"))
        pending = sum(1 for t in tasks if t["status"] == "PENDING")
        ready = sum(1 for t in tasks if t["status"] == "READY")
        blocked = sum(1 for t in tasks if t["status"] == "BLOCKED")
        by_status: dict[str, int] = {}
        for t in tasks:
            by_status[t["status"]] = by_status.get(t["status"], 0) + 1

        workflow = self.database.get_workflow(tenant_id, workflow_id)
        if workflow is None:
            return {"status": "UNKNOWN", "workflow_id": workflow_id, "total_tasks": total,
                    "completed": completed, "failed": failed, "running": running,
                    "pending": pending, "ready": ready, "blocked": blocked,
                    "by_status": by_status, "tasks": tasks}

        artifacts = self.database.list_workflow_artifacts(tenant_id, workflow_id)
        messages = self.database.list_workflow_messages(tenant_id, workflow_id, limit=200)
        events = self.database.list_workflow_events(tenant_id, workflow_id, limit=200)
        dynamic_tasks = self.database.get_dynamic_tasks(tenant_id, workflow_id)
        artifact_ids = [a.get("artifact_id") for a in artifacts]
        consumed_refs = sorted({e.get("detail", {}).get("artifact_ref")
                                for e in events if e.get("event_type") == "artifact_consumed"
                                and e.get("detail", {}).get("artifact_ref")})
        progress_percent = round(100.0 * (completed + failed) / total, 1) if total else 0.0

        state: dict[str, Any] = {
            "status": workflow["status"],
            "workflow_id": workflow_id,
            "workflow": workflow,
            "workflow_state": workflow["status"],
            "workflow_status": workflow["status"],
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": pending,
            "ready": ready,
            "blocked": blocked,
            "by_status": by_status,
            "task_states": by_status,
            "tasks": tasks,
            "completed_tasks": [t["task_id"] for t in tasks if t["status"] == "COMPLETED"],
            "failed_tasks": [t["task_id"] for t in tasks if t["status"] == "FAILED"],
            "running_tasks": [t["task_id"] for t in tasks if t["status"] in ("RUNNING", "READY", "AWAITING_APPROVAL")],
            "artifacts_produced": len(artifacts),
            "produced_artifacts": artifact_ids,
            "artifact_ids": artifact_ids,
            "artifacts": artifacts,
            "consumed_artifacts": consumed_refs,
            "artifacts_consumed_refs": consumed_refs,
            "messages_exchanged": len(messages),
            "messages": messages[:50],
            "recent_message_types": sorted({m.get("message_type") for m in messages}),
            "events_emitted": len(events),
            "events": events[:50],
            "recent_event_types": sorted({e.get("event_type") for e in events}),
            "dynamic_tasks": len(dynamic_tasks),
            "newly_created_tasks": [d.get("task_id") for d in dynamic_tasks],
            "dynamic_task_ids": [d.get("task_id") for d in dynamic_tasks],
            "progress_percent": progress_percent,
            "progress": {
                "total": total,
                "completed": completed,
                "failed": failed,
                "running": running,
                "pending": pending,
                "percent": progress_percent,
            },
        }

        if completed == total:
            self.database.update_workflow_status(tenant_id, workflow_id, "COMPLETED")
            self.database.save_workflow_result(tenant_id, workflow_id, {
                "status": "COMPLETED",
                "tasks_completed": completed,
                "tasks_total": total,
                "artifacts_produced": len(artifacts),
            })
            state["status"] = "COMPLETED"
            state["workflow_status"] = "COMPLETED"
            return state

        if failed > 0 and running == 0:
            self.database.update_workflow_status(tenant_id, workflow_id, "FAILED", error=f"{failed} tasks failed")
            state["status"] = "FAILED"
            state["workflow_status"] = "FAILED"
            return state

        if running > 0:
            if workflow["status"] != "RUNNING":
                self.database.update_workflow_status(tenant_id, workflow_id, "RUNNING")
                state["workflow_status"] = "RUNNING"

        return state

    def add_dynamic_task(
        self,
        tenant_id: str,
        project_id: str,
        workflow_id: str,
        *,
        task_type: str,
        name: str,
        agent_id: str | None = None,
        required_capabilities: list[str],
        depends_on: list[str] | None = None,
        input_artifacts: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
        generated_reason: str | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Add a dynamically-created task to an existing workflow.

        Every dynamically-created task records:
        - reason (generated_reason)
        - parent task (parent_task_id)
        - triggering artifact/event
        - required capability
        - dependencies
        - creation timestamp
        - creator = NEXUS runtime
        """
        task_id = f"dyn-{core_id('task')}"
        db_task = self.database.create_dynamic_task(
            task_id=task_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            task_type=task_type,
            name=name,
            agent_id=agent_id,
            required_capabilities=required_capabilities,
            depends_on=depends_on or [],
            input_artifacts=input_artifacts or [],
            max_retries=max_retries if max_retries is not None else self.policy.max_retries_default,
            parent_task_id=parent_task_id,
            generated_reason=generated_reason or "Dynamically created during autonomous execution",
        )

        self.database.add_workflow_event(
            event_id=f"evt-{core_id('evt')}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task_id,
            agent_id="NEXUS runtime",
            event_type="dynamic_task_created",
            detail={
                "task_type": task_type,
                "name": name,
                "required_capabilities": required_capabilities,
                "depends_on": depends_on or [],
                "parent_task_id": parent_task_id,
                "generated_reason": generated_reason,
                "creator": "NEXUS runtime",
                "creation_timestamp": _now(),
            },
        )

        if self._messaging_hub:
            self._messaging_hub.send(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                message_type="DYNAMIC_TASK_CREATED",
                content={
                    "task_id": task_id,
                    "task_type": task_type,
                    "name": name,
                    "reason": generated_reason,
                    "parent_task_id": parent_task_id,
                    "required_capabilities": required_capabilities,
                    "depends_on": depends_on or [],
                },
                from_agent_id="NEXUS runtime",
                task_id=task_id,
            )

        return db_task

    def get_execution_trace(self, tenant_id: str, workflow_id: str) -> dict[str, Any]:
        """Build a complete execution trace for a workflow.

        Returns the full observable history: planning decisions, tasks,
        agents, messages, artifacts, dynamic tasks, retries, approvals,
        and verification state.
        """
        state = self.get_workflow_state(tenant_id, workflow_id)
        workflow = state.get("workflow", {})
        tasks = state.get("tasks", [])
        artifacts = state.get("artifacts", [])
        events = state.get("events", [])
        messages = state.get("messages", [])

        plan = {}
        if workflow.get("plan_json"):
            try:
                plan = json.loads(workflow["plan_json"])
            except (json.JSONDecodeError, TypeError):
                plan = {}

        # Collect agent participation
        agents = {}
        for task in tasks:
            agent_id = task.get("agent_id")
            if agent_id:
                if agent_id not in agents:
                    agents[agent_id] = {"agent_id": agent_id, "tasks": []}
                agents[agent_id]["tasks"].append(task.get("task_id"))
        for msg in messages:
            from_id = msg.get("from_agent_id")
            if from_id and from_id not in agents:
                agents[from_id] = {"agent_id": from_id, "tasks": [], "messages_sent": 0, "messages_received": 0}
            if from_id:
                agents.setdefault(from_id, {"agent_id": from_id, "tasks": [], "messages_sent": 0, "messages_received": 0})
                agents[from_id]["messages_sent"] = agents[from_id].get("messages_sent", 0) + 1
            to_id = msg.get("to_agent_id")
            if to_id:
                agents.setdefault(to_id, {"agent_id": to_id, "tasks": [], "messages_sent": 0, "messages_received": 0})
                agents[to_id]["messages_received"] = agents[to_id].get("messages_received", 0) + 1

        # Collect active approvals
        pending_approvals = self.database.list_pending_approvals(
            tenant_id, workflow.get("project_id")
        )

        dynamic_tasks = self.database.get_dynamic_tasks(tenant_id, workflow_id)

        trace = {
            "workflow_id": workflow_id,
            "objective": workflow.get("objective", ""),
            "scope": workflow.get("scope", ""),
            "status": workflow.get("status"),
            "planning": {
                "plan": plan,
                "template_type": plan.get("template_type"),
                "validations": plan.get("validations", []),
            },
            "execution_environment": plan.get("execution_environment", "LOCAL"),
            "tasks": [
                {
                    "task_id": t.get("task_id"),
                    "task_type": t.get("task_type"),
                    "name": t.get("name"),
                    "status": t.get("status"),
                    "agent_id": t.get("agent_id"),
                    "worker_id": t.get("worker_id"),
                    "claimed_at": t.get("claimed_at"),
                    "claim_count": t.get("claim_count", 0),
                    "last_execution_id": t.get("last_execution_id"),
                    "retry_count": t.get("retry_count", 0),
                    "max_retries": t.get("max_retries", 0),
                    "error": t.get("error"),
                    "started_at": t.get("started_at"),
                    "completed_at": t.get("completed_at"),
                    "depends_on": t.get("depends_on", []),
                    "required_capabilities": t.get("required_capabilities", []),
                    "input_artifacts": t.get("input_artifacts", []),
                    "output_artifacts": t.get("output_artifacts", []),
                    "is_dynamic": bool(t.get("dynamic")),
                    "parent_task_id": t.get("parent_task_id"),
                    "generated_reason": t.get("generated_reason"),
                }
                for t in tasks
            ],
            "agents": agents,
            "messages": messages,
            "artifacts": artifacts,
            "dynamic_tasks": [
                {
                    "task_id": dt.get("task_id"),
                    "name": dt.get("name"),
                    "reason": dt.get("generated_reason"),
                    "parent_task_id": dt.get("parent_task_id"),
                    "created_at": dt.get("created_at"),
                    "creator": "NEXUS runtime",
                }
                for dt in dynamic_tasks
            ],
            "retries": [
                {
                    "task_id": e.get("task_id"),
                    "retry_count": next((t.get("retry_count", 0) for t in tasks if t.get("task_id") == e.get("task_id")), 0),
                    "detail": e.get("detail", {}),
                    "timestamp": e.get("created_at"),
                }
                for e in events if e.get("event_type") == "task_retried"
            ],
            "approvals": [
                {
                    "approval_id": a.get("approval_id"),
                    "operation": a.get("operation"),
                    "reason": a.get("reason"),
                    "status": a.get("status"),
                    "requested_by": a.get("requested_by"),
                    "created_at": a.get("created_at"),
                }
                for a in pending_approvals
            ],
            "verification": {
                "checks": [
                    {"task_id": t.get("task_id"), "status": t.get("status"), "error": t.get("error")}
                    for t in tasks
                ],
                "all_completed": all(t.get("status") == "COMPLETED" for t in tasks if t.get("status") != "CANCELLED"),
            },
            "final_result": {
                "status": workflow.get("status"),
                "completed": sum(1 for t in tasks if t.get("status") == "COMPLETED"),
                "failed": sum(1 for t in tasks if t.get("status") == "FAILED"),
                "total": len(tasks),
                "dynamic_tasks": len(dynamic_tasks),
                "artifacts": len(artifacts),
                "messages": len(messages),
            },
            "all_events": events,
        }

        # ---- Authoritative enrichments (all derived from persisted rows) ----
        tool_events = [e for e in events if e.get("event_type") == "tool_used"]
        tools_used: dict[str, Any] = {}
        for e in tool_events:
            detail = e.get("detail", {}) or {}
            cap = detail.get("capability", "unknown")
            entry = tools_used.setdefault(cap, {"count": 0, "targets": [], "receipts": []})
            entry["count"] += 1
            if detail.get("target") and detail["target"] not in entry["targets"]:
                entry["targets"].append(detail["target"])
            if detail.get("receipt_id"):
                entry["receipts"].append(detail["receipt_id"])
        trace["tools_used"] = tools_used

        # Phase 9: fabric observability — durable worker registry, attempts,
        # and recovery/approval history, all from persisted state.
        try:
            trace["workers"] = self.database.list_workers(tenant_id, stale_seconds=60)
        except Exception:
            trace["workers"] = []
        recovery_types = {
            "task_recovered", "worker_error", "worker_completed", "task_retried",
            "approval_requested", "approval_granted", "approval_rejected",
        }
        trace["recovery_events"] = [
            {"event_id": e.get("event_id"), "event_type": e.get("event_type"),
             "task_id": e.get("task_id"), "agent_id": e.get("agent_id"),
             "detail": e.get("detail", {}), "created_at": e.get("created_at")}
            for e in events if e.get("event_type") in recovery_types
        ]
        approval_events = [e for e in events if (e.get("event_type") or "").startswith("approval_")]
        trace["approvals_history"] = [
            {"event_type": e.get("event_type"), "task_id": e.get("task_id"),
             "detail": e.get("detail", {}), "created_at": e.get("created_at")}
            for e in approval_events
        ]

        reality_breakdown: dict[str, int] = {}
        for a in artifacts:
            reality_breakdown[a.get("reality", "UNKNOWN")] = reality_breakdown.get(a.get("reality", "UNKNOWN"), 0) + 1
        trace["reality_breakdown"] = reality_breakdown
        trace["observed"] = [a.get("artifact_id") for a in artifacts if a.get("reality") == "OBSERVED"]
        trace["inferred"] = [a.get("artifact_id") for a in artifacts if a.get("reality") == "INFERRED"]
        trace["verified"] = [a.get("artifact_id") for a in artifacts if a.get("reality") == "VERIFIED"]

        # Provenance: each artifact lists parents + downstream consumers so the
        # UI can render Researcher -> Architect -> Security -> Reporter ->
        # Verifier chains without guessing.
        artifact_ids = {a.get("artifact_id") for a in artifacts}
        consumed_events = [e for e in events if e.get("event_type") == "artifact_consumed"]
        enriched_artifacts = []
        for a in artifacts:
            parents = [p for p in (a.get("parent_artifacts") or []) if p in artifact_ids]
            consumers: list[str] = []
            for t in tasks:
                inputs = t.get("input_artifacts", []) or []
                if a.get("name") in inputs or a.get("kind") in inputs or a.get("artifact_id") in inputs:
                    consumers.append(t.get("task_id"))
            for e in consumed_events:
                detail = e.get("detail", {}) or {}
                if detail.get("artifact_ref") in (a.get("name"), a.get("kind"), a.get("artifact_id")):
                    if e.get("task_id") and e["task_id"] not in consumers:
                        consumers.append(e["task_id"])
            # Downstream artifacts that list this one as a parent
            children = [c.get("artifact_id") for c in artifacts
                        if a.get("artifact_id") in (c.get("parent_artifacts") or [])]
            enriched_artifacts.append({
                **a,
                "consumed_by": sorted(c for c in consumers if c),
                "child_artifacts": children,
            })
        trace["artifacts"] = enriched_artifacts
        trace["provenance_chain"] = [
            {
                "artifact_id": a.get("artifact_id"),
                "kind": a.get("kind"),
                "producer": a.get("agent_id"),
                "task_id": a.get("task_id"),
                "parents": [p for p in (a.get("parent_artifacts") or []) if p in artifact_ids],
                "consumed_by": a.get("consumed_by", []),
                "reality": a.get("reality"),
            }
            for a in enriched_artifacts
        ]

        # Why did the workflow finish? Deterministic, from persisted state.
        failed_ids = [t.get("task_id") for t in tasks if t.get("status") == "FAILED"]
        if workflow.get("status") == "COMPLETED":
            trace["finish_reason"] = f"all {len(tasks)} tasks COMPLETED"
        elif workflow.get("status") == "FAILED":
            trace["finish_reason"] = f"{len(failed_ids)} tasks FAILED: {failed_ids}"
        elif workflow.get("status") == "CANCELLED":
            trace["finish_reason"] = "workflow CANCELLED by operator"
        elif workflow.get("status") == "PAUSED":
            trace["finish_reason"] = "workflow PAUSED by operator"
        else:
            remaining = [t.get("task_id") for t in tasks if t.get("status") not in ("COMPLETED", "FAILED", "CANCELLED")]
            trace["finish_reason"] = f"in progress: {len(remaining)} tasks remaining"

        # Phase 7: model runtime visibility (all derived from persisted events).
        model_event_types = {
            "model_invocation", "model_result", "model_failure", "model_timeout",
            "tool_requested", "tool_result", "tool_rejected", "tool_limit_exceeded",
            "max_iterations_exceeded", "model_fallback", "execution_strategy",
        }
        model_events = [e for e in events if e.get("event_type") in model_event_types]
        invocations = [e for e in model_events if e.get("event_type") == "model_invocation"]
        providers_used: list[str] = []
        for e in invocations:
            prov = (e.get("detail", {}) or {}).get("provider")
            if prov and prov not in providers_used:
                providers_used.append(prov)
        strategies = [e for e in model_events if e.get("event_type") == "execution_strategy"]
        effective_modes = {(e.get("detail", {}) or {}).get("effective") for e in strategies}
        if invocations:
            execution_mode = "MODEL/HYBRID"
        elif strategies and effective_modes - {"DETERMINISTIC", None}:
            execution_mode = "MODEL/HYBRID"
        else:
            execution_mode = "DETERMINISTIC"
        trace["model"] = {
            "execution_mode": execution_mode,
            "invocations": len(invocations),
            "providers_used": providers_used,
            "failures": len([e for e in model_events if e.get("event_type") in ("model_failure", "model_timeout")]),
            "tool_requests": len([e for e in model_events if e.get("event_type") == "tool_requested"]),
            "events": model_events[:100],
        }
        try:
            from runtime.model_router import ModelRouter as _MR
            trace["model"]["router_status"] = _MR().redacted_status()
        except Exception:
            trace["model"]["router_status"] = {"execution_mode": execution_mode}

        return trace

    def resume_workflow(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Resume a paused workflow."""
        workflow = self.database.get_workflow(tenant_id, workflow_id)
        if workflow is None:
            raise ValueError(f"workflow '{workflow_id}' not found")
        if workflow["status"] not in ("PAUSED", "FAILED"):
            raise ValueError(f"workflow '{workflow_id}' cannot be resumed from status '{workflow['status']}'")

        self.database.update_workflow_status(tenant_id, workflow_id, "RUNNING")
        self.database.add_workflow_event(
            event_id=f"evt-{core_id('evt')}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            event_type="workflow_resumed",
            detail={"workflow_id": workflow_id, "at": _now()},
        )
        self._dispatch_ready_tasks(tenant_id, project_id, workflow_id)
        return self.database.get_workflow(tenant_id, workflow_id)

    def pause_workflow(self, tenant_id: str, workflow_id: str) -> bool:
        """Pause a running workflow."""
        workflow = self.database.get_workflow(tenant_id, workflow_id)
        if workflow is None:
            raise ValueError(f"workflow '{workflow_id}' not found")
        self.database.update_workflow_status(tenant_id, workflow_id, "PAUSED")
        return True

    def cancel_workflow(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Cancel a workflow and all its tasks."""
        self.database.update_workflow_status(tenant_id, workflow_id, "CANCELLED")
        tasks = self.database.list_workflow_tasks(tenant_id, workflow_id)
        for task in tasks:
            if task["status"] not in ("COMPLETED", "FAILED", "CANCELLED"):
                self.database.update_task_status(tenant_id, task["task_id"], "CANCELLED")

        self.database.add_workflow_event(
            event_id=f"evt-{core_id('evt')}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            event_type="workflow_cancelled",
            detail={"workflow_id": workflow_id, "at": _now()},
        )
        return self.database.get_workflow(tenant_id, workflow_id)

     # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def recover(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Recover a workflow from persisted state, re-dispatching ready tasks."""
        workflow = self.database.get_workflow(tenant_id, workflow_id)
        if workflow is None:
            raise ValueError(f"workflow '{workflow_id}' not found")

        self.recover_stuck_tasks(tenant_id, workflow_id)

        if workflow["status"] == "RUNNING":
            self._mark_ready_tasks(tenant_id, project_id, workflow_id)
            self._execute_ready_tasks(tenant_id, project_id, workflow_id)

        tasks = self.database.list_workflow_tasks(tenant_id, workflow_id)
        return {
            "workflow": workflow,
            "tasks": tasks,
            "artifacts": self.database.list_workflow_artifacts(tenant_id, workflow_id),
            "events": self.database.list_workflow_events(tenant_id, workflow_id, limit=50),
            "recovery": {
                "total_tasks": len(tasks),
                "incomplete_tasks": [t["task_id"] for t in tasks if t["status"] not in ("COMPLETED", "CANCELLED")],
            },
        }

    def recover_stuck_tasks(self, tenant_id: str, workflow_id: str, stale_seconds: int = 30) -> int:
        """Recover tasks claimed by dead workers back to READY state.

        Returns the number of tasks recovered.
        """
        stuck = self.database.list_stuck_tasks(tenant_id, stale_seconds)
        recovered = 0
        for task in stuck:
            if task["workflow_id"] != workflow_id:
                continue
            self.database.reset_stuck_task(tenant_id, task["task_id"])
            self.messaging_hub.task_lifecycle(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                event="TASK_RECOVERED",
                agent_id=task.get("agent_id") or "unknown",
                task_id=task["task_id"],
                details={"reason": "stuck_worker_recovery", "stale_seconds": stale_seconds},
            )
            recovered += 1
        return recovered

    def get_artifact_lineage(self, tenant_id: str, artifact_id: str) -> dict[str, Any]:
        """Queryable provenance chain for one artifact (producer -> consumers).

        Delegates to the database so the chain reflects durable persisted
        state, never in-memory guesses.
        """
        return self.database.get_artifact_lineage(tenant_id, artifact_id)

    def get_workflow_state(self, tenant_id: str, workflow_id: str) -> dict[str, Any]:
        """Get full workflow state: workflow, tasks, artifacts, events, messages."""
        workflow = self.database.get_workflow(tenant_id, workflow_id)
        if workflow is None:
            return {"status": "NOT_FOUND"}
        tasks = self.database.list_workflow_tasks(tenant_id, workflow_id)
        artifacts = self.database.list_workflow_artifacts(tenant_id, workflow_id)
        events = self.database.list_workflow_events(tenant_id, workflow_id, limit=100)
        messages = self.database.list_workflow_messages(tenant_id, workflow_id, limit=100)

        return {
            "workflow": workflow,
            "tasks": tasks,
            "artifacts": artifacts,
            "events": events,
            "messages": messages,
            "summary": {
                "total_tasks": len(tasks),
                "completed": sum(1 for t in tasks if t["status"] == "COMPLETED"),
                "failed": sum(1 for t in tasks if t["status"] == "FAILED"),
                "running": sum(1 for t in tasks if t["status"] in ("RUNNING", "READY")),
                "total_artifacts": len(artifacts),
                "total_messages": len(messages),
            },
        }


@dataclass
class AgentTaskResult:
    """Result of an agent executing a task."""
    task_id: str
    status: str
    reality: str
    result: dict[str, Any]
    artifacts: list[dict[str, Any]]
    error: str | None = None


@dataclass
class WorkflowSummary:
    """Summary of a workflow's current state for UI display."""
    workflow_id: str
    name: str
    objective: str
    status: str
    scope: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    tasks_total: int
    tasks_completed: int
    tasks_failed: int
    tasks_running: int
    tasks_pending: int
    artifacts_count: int
    recent_events: list[dict[str, Any]]
    error: str | None
