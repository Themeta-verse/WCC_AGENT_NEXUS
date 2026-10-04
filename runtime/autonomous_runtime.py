"""NEXUS Phase 5 — Autonomous Orchestration Runtime.

The AutonomousRuntime is the top-level control loop that drives a workflow
from a single high-level objective to verified completion, adapting the workflow
graph as needed based on execution evidence.

Control Loop:

    OBJECTIVE
        ↓
    PLAN (WorkflowPlanner)
        ↓
    EXECUTE (WorkflowEngine.step)
        ↓
    OBSERVE (state, artifacts, messages)
        ↓
    EVALUATE (is the objective satisfied?)
        ↓
    DECIDE (retry / reassign / create task / request approval / stop)
        ↓
    MODIFY / EXTEND WORKFLOW (add tasks, reassign agents)
        ↓
        ... repeat until terminal state

The runtime is deterministic at the orchestration layer: decisions are based
on concrete evidence (task results, artifacts, messages), not on LLM
hallucination. Dynamic task creation follows explicit rules that inspect
artifacts and messages for justification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import logging
import time
import uuid
from runtime.github_provider import initialize_github_connector_registration


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger("nexus.autonomous_runtime")


@dataclass
class AutonomousConfig:
    """Configuration for the AutonomousRuntime."""
    tenant_id: str = "default"
    project_id: str = "default"
    poll_interval_seconds: float = 1.0
    max_iterations: int = 500
    auto_recover_stuck_seconds: int = 30
    auto_approve_safe_operations: bool = True
    stop_on_idle: bool = True
    idle_limit: int = 5
    dynamic_task_creation: bool = True
    max_dynamic_tasks: int = 10


@dataclass
class DynamicTaskRequest:
    """A request to dynamically create a task during autonomous execution.

    Every dynamically-created task must record its justification:
    - reason: why this task is needed
    - parent_task_id: which task triggered the need
    - triggering_artifact: the artifact that justified the task
    - required_capability: what capability the new task needs
    - dependency_task_ids: which tasks the new task depends on
    - creation_timestamp: when it was created
    - creator: always "NEXUS runtime"
    """
    task_type: str
    name: str
    reason: str
    parent_task_id: str | None
    triggering_artifact: str | None
    required_capabilities: list[str]
    depends_on: list[str]
    parameters: dict[str, Any] = field(default_factory=dict)
    agent_id: str | None = None


class AutonomousRuntime:
    """Autonomous orchestration runtime for Phase 5.

    Coordinates WorkflowEngine, WorkflowWorker, MessagingHub, and
    WorkflowPlanner to execute objectives autonomously with dynamic
    task creation, agent collaboration, and human approval gates.

    The runtime is deterministic: it inspects evidence from the database
    (task results, artifacts, messages) and makes decisions based on
    explicit rules.
    """

    def __init__(
        self,
        database: Any,
        engine: Any,
        executor: Any = None,
        agent_registry: Any = None,
        messaging_hub: Any = None,
        planner: Any = None,
        config: AutonomousConfig | None = None,
    ):
        self.database = database
        self.engine = engine
        self.executor = executor or getattr(engine, "executor", None)
        self._agent_registry = agent_registry or getattr(engine, "_agent_registry", None)
        self._messaging_hub = messaging_hub or getattr(engine, "messaging_hub", None)

        if self._messaging_hub is None:
            from runtime.messaging_hub import MessagingHub
            self._messaging_hub = MessagingHub(self.database)
            if hasattr(self.engine, "messaging_hub"):
                try:
                    self.engine.messaging_hub = self._messaging_hub
                except Exception as exc:
                    # Explicit: engine keeps its own hub; record, don't hide.
                    logger.warning("engine messaging_hub wiring failed: %s: %s", type(exc).__name__, exc)

        if planner is not None:
            self._planner = planner
        else:
            self._planner = None

        self.config = config or AutonomousConfig()
        # Canonical capability registry — exactly ONE per process.
        #
        # There is deliberately no reduced-capability fallback here. The previous
        # fallback built a "GitHub-only" registry when canonical init failed,
        # which silently dropped the git and filesystem connectors: the runtime
        # kept running while capabilities quietly disappeared, and every
        # downstream plan silently narrowed instead of reporting the loss. A
        # failure to build the canonical registry is a wiring fault, so it fails
        # closed and is never downgraded to a smaller truth.
        from runtime.capability_fabric import initialize_capability_registry as _init_caps
        try:
            _cap_registry = _init_caps()
        except Exception as exc:
            logger.error(
                "canonical capability registry init failed (%s: %s); refusing to "
                "continue with a reduced-capability registry",
                type(exc).__name__, exc,
            )
            raise
        self.connector_registry = _cap_registry
        self._propagate_connector_registry(_cap_registry)
        self._dynamic_task_count = 0

    def _propagate_connector_registry(self, registry: Any) -> None:
        """Propagate the single ConnectorRegistry down to the engine and executor.

        The engine keeps its AGENT registry untouched — the connector registry is
        a separate dependency that only the executor needs, so it is forwarded
        verbatim (no second registry is created anywhere).
        """
        if registry is None:
            return
        if self.executor is not None:
            try:
                self.executor.connector_registry = registry
            except (AttributeError, TypeError):
                pass
        if hasattr(self.engine, "connector_registry"):
            try:
                self.engine.connector_registry = registry
            except (AttributeError, TypeError):
                pass
        # The planner must plan against the SAME registry object the executor
        # will execute with. It previously fell back to the module-level
        # singleton, which happened to be the same object but was never wired
        # or asserted; an injected planner or a re-initialised singleton would
        # let planning and execution disagree about what is available.
        if self._planner is not None:
            try:
                self._planner.connector_registry = registry
            except (AttributeError, TypeError):
                pass
        if getattr(self.engine, "planner", None) is not None:
            try:
                self.engine.planner.connector_registry = registry
            except (AttributeError, TypeError):
                pass

    @property
    def messaging_hub(self) -> Any:
        return self._messaging_hub

    @property
    def planner(self) -> Any:
        if self._planner is None:
            from runtime.workflow_planner import WorkflowPlanner
            self._planner = WorkflowPlanner(agent_registry=self._agent_registry)
        return self._planner

    def execute_objective(
        self,
        objective: str,
        scope: str,
        *,
        template_type: str | None = None,
        agents: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        """Execute a high-level objective autonomously.

        This is the main entry point for Phase 5 autonomous execution:
        1. Plan the workflow from the objective (using WorkflowPlanner)
        2. Start the workflow
        3. Run the autonomous control loop
        4. Return the final state with execution trace
        """
        iterations = max_iterations if max_iterations is not None else self.config.max_iterations

        # Step 1: Plan
        planned = self._plan_objective(objective, scope, template_type, agents, constraints)

        # Step 2: Create and start the workflow
        workflow_id = planned.get("workflow_id")
        workflow = self.database.get_workflow(self.config.tenant_id, workflow_id) if workflow_id else None
        if workflow is None:
            workflow = self.engine.create_workflow(
                self.config.tenant_id,
                self.config.project_id,
                self._planner_to_spec(planned),
            )
            workflow_id = workflow["workflow_id"]

        # Save the plan with objective/constraints for later reference
        plan = {
            "objective": objective,
            "scope": scope,
            "template_type": template_type or "repository_analysis",
            "constraints": constraints or {},
            "planned_tasks": planned.get("task_specs", []),
        }
        self.database.save_workflow_plan(self.config.tenant_id, workflow_id, plan)

        # Step 3: Start the workflow
        self.engine.start_workflow(self.config.tenant_id, self.config.project_id, workflow_id)

        # Step 4: Run the control loop
        for i in range(iterations):
            if not self._should_continue(workflow_id):
                break

            # Heartbeat
            self._heartbeat(f"iteration-{i}")

            # Recover stuck tasks
            if self.config.auto_recover_stuck_seconds > 0:
                self.engine.recover_stuck_tasks(
                    self.config.tenant_id, workflow_id,
                    stale_seconds=self.config.auto_recover_stuck_seconds,
                )

            # Execute one step
            state = self.engine.step(self.config.tenant_id, self.config.project_id, workflow_id)

            # Phase 5: Observe and adapt
            self._observe_and_adapt(workflow_id, state)

            # engine.step() returns flat keys; get_workflow_state() nests under "summary"
            summary = state.get("summary", {}) or {}
            total = summary.get("total_tasks", state.get("total_tasks", 0))
            completed = summary.get("completed", state.get("completed", 0))
            failed = summary.get("failed", state.get("failed", 0))

            if total > 0 and completed + failed >= total:
                break

            time.sleep(self.config.poll_interval_seconds)

        # Return final state with trace
        final_state = self.engine.get_workflow_state(self.config.tenant_id, workflow_id)
        trace = self.build_execution_trace(final_state)

        return {
            "workflow_id": workflow_id,
            "status": final_state.get("workflow", {}).get("status"),
            "summary": final_state.get("summary", {}),
            "trace": trace,
        }

    def _plan_objective(
        self,
        objective: str,
        scope: str,
        template_type: str | None,
        agents: list[dict[str, Any]] | None,
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Plan a workflow from a high-level objective."""
        planner = self.planner
        if template_type:
            planned = planner.plan_with_template(
                objective=objective,
                scope=scope,
                template_type=template_type,
                constraints=constraints or {},
            )
        else:
            planned = planner.plan(
                objective=objective,
                scope=scope,
                constraints=constraints or {},
                tenant_id=self.config.tenant_id,
                execution_mode="SIMULATION",
                project_id=self.config.project_id,
            )
        result = planned.to_dict() if hasattr(planned, "to_dict") else dict(planned)
        result["template_type"] = template_type
        return result

    def _planner_to_spec(self, planned: dict[str, Any]) -> Any:
        """Convert a planned workflow dict to a WorkflowSpec for engine.create_workflow."""
        from runtime.workflow_engine import WorkflowSpec
        return WorkflowSpec(
            name=planned.get("name", "Autonomous Workflow"),
            objective=planned.get("objective", ""),
            scope=planned.get("scope", "."),
            task_specs=planned.get("task_specs", []),
            agents=planned.get("agents", []),
            execution_mode=planned.get("execution_mode", "REAL_READ"),
        )

    def _should_continue(self, workflow_id: str) -> bool:
        """Check if the autonomous runtime should continue running."""
        workflow = self.database.get_workflow(self.config.tenant_id, workflow_id)
        if workflow is None:
            return False
        status = workflow.get("status", "")
        return status in ("RUNNING", "PAUSED")

    def _heartbeat(self, activity: str) -> None:
        """Send a heartbeat via the database."""
        try:
            self.database.heartbeat_worker(
                f"autonomous-{self.config.tenant_id[:12]}",
                "ACTIVE",
                {"activity": activity, "iteration": self._dynamic_task_count},
            )
        except Exception as exc:
            logger.warning(f"Heartbeat failed: {exc}")

    def _observe_and_adapt(self, workflow_id: str, state: dict[str, Any]) -> None:
        """Observe workflow state and decide whether to adapt the workflow.

        This is the core of Phase 5: the runtime inspects completed tasks,
        their artifacts, and cross-agent messages to determine if new work
        is needed.
        """
        if not self.config.dynamic_task_creation:
            return

        if self._dynamic_task_count >= self.config.max_dynamic_tasks:
            return

        state_tasks = state.get("tasks", [])
        completed_tasks = [t for t in state_tasks if t.get("status") == "COMPLETED"]

        for task in completed_tasks:
            self._evaluate_for_dynamic_tasks(workflow_id, task)

        # Check for blocked tasks
        blocked_tasks = [t for t in state_tasks if t.get("status") == "BLOCKED"]
        for task in blocked_tasks:
            self._handle_blocked_task(workflow_id, task)

        # Check for failed tasks
        failed_tasks = [t for t in state_tasks if t.get("status") == "FAILED"]
        for task in failed_tasks:
            self._handle_failed_task(workflow_id, task)

    def _evaluate_for_dynamic_tasks(self, workflow_id: str, task: dict[str, Any]) -> None:
        """Evaluate a completed task's output for evidence that justifies new tasks."""
        task_id = task.get("task_id")
        if not task_id:
            return

        # Check if this task already has pending dynamic follow-ups
        existing_dynamic = self.database.get_dynamic_tasks(self.config.tenant_id, workflow_id)
        already_has_followup = any(
            dt.get("parent_task_id") == task_id for dt in existing_dynamic
        )
        if already_has_followup:
            return

        # Inspect the task's output artifacts for evidence
        task_artifacts = self._get_task_artifacts(workflow_id, task_id)

        # Rule 1: If a research task found authentication-related files,
        # create a security follow-up task
        if task.get("task_type") == "research":
            self._check_research_for_security_followup(workflow_id, task, task_artifacts)

        # Rule 2: If a security task found critical findings,
        # create a follow-up review task
        if task.get("task_type") == "security-analysis":
            self._check_security_for_review_task(workflow_id, task, task_artifacts)

        # Rule 3: If an architecture task identified subsystems,
        # create subsystem analysis tasks
        if task.get("task_type") == "architecture-analysis":
            self._check_architecture_for_subtasks(workflow_id, task, task_artifacts)

        # Rule 4: If a task produced an error or was retried,
        # check if reassignment or escalation is needed
        if task.get("retry_count", 0) > 0:
            self._handle_retried_task(workflow_id, task)

    def _check_research_for_security_followup(
        self, workflow_id: str, task: dict[str, Any], artifacts: list[dict[str, Any]]
    ) -> None:
        """If research found security-related patterns, suggest a security follow-up."""
        task_id = task.get("task_id")

        # Inspect research findings for security-relevant evidence
        security_signals = []
        for art in artifacts:
            content = art.get("content")
            if not content:
                content = self._load_artifact_content(art)
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    continue
            research = content.get("research", {}) if isinstance(content, dict) else {}
            findings = research.get("findings", [])
            analysis = research.get("analysis", {})

            # Check for auth/security-related files
            for f in findings:
                file_name = f.get("file", "")
                if any(keyword in file_name.lower() for keyword in ["auth", "security", "secret", "token", "jwt", "credential"]):
                    security_signals.append(file_name)

            # Check file type distribution for auth-related patterns
            file_types = analysis.get("file_type_distribution", {})
            if ".py" in file_types or ".ts" in file_types:
                # Python/TypeScript projects often have auth code
                pass

        if security_signals:
            # Dynamically create a security analysis task that consumes the research artifact
            parent_artifact = next((a.get("artifact_id") for a in artifacts if a.get("kind") == "research_report"), None)
            new_task = self._create_dynamic_task(
                workflow_id=workflow_id,
                task_type="security-analysis",
                name="Security follow-up from research",
                reason=f"Research task {task_id} discovered security-relevant files: {security_signals[:3]}",
                parent_task_id=task_id,
                triggering_artifact=parent_artifact,
                required_capabilities=["security.read"],
                depends_on=[],
                parameters={},
                input_artifact_refs=[parent_artifact] if parent_artifact else [],
            )
            if new_task:
                self._messaging_hub.task_lifecycle(
                    workflow_id=workflow_id,
                    tenant_id=self.config.tenant_id,
                    event="DYNAMIC_TASK_CREATED",
                    agent_id="autonomous-runtime",
                    task_id=new_task["task_id"],
                    details={
                        "reason": f"Research discovered security-related files: {security_signals[:3]}",
                        "parent_task_id": task_id,
                    },
                )

    def _check_security_for_review_task(
        self, workflow_id: str, task: dict[str, Any], artifacts: list[dict[str, Any]]
    ) -> None:
        """If security analysis found critical findings, create a review task."""
        task_id = task.get("task_id")

        critical_count = 0
        for art in artifacts:
            content = self._load_artifact_content(art)
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    continue
            sec_report = content.get("security_report", {}) if isinstance(content, dict) else {}
            findings = sec_report.get("findings", [])
            critical_count += sum(1 for f in findings if f.get("severity") in ("CRITICAL", "HIGH"))

        if critical_count > 0:
            parent_artifact = next((a.get("artifact_id") for a in artifacts if a.get("kind") == "security_report"), None)
            new_task = self._create_dynamic_task(
                workflow_id=workflow_id,
                task_type="verification",
                name="Security findings verification",
                reason=f"Security analysis found {critical_count} critical/high findings requiring verification",
                parent_task_id=task_id,
                triggering_artifact=parent_artifact,
                required_capabilities=["verify"],
                depends_on=[t.get("task_id") for t in self.database.list_workflow_tasks(self.config.tenant_id, workflow_id) if t.get("status") == "COMPLETED"],
                parameters={"verify_findings": True},
            )
            if new_task:
                self._messaging_hub.escalate(
                    workflow_id=workflow_id,
                    tenant_id=self.config.tenant_id,
                    reason=f"Security analysis found {critical_count} critical/high findings",
                    from_agent_id="security-analyst",
                    task_id=new_task["task_id"],
                    details={"finding_count": critical_count, "parent_task_id": task_id},
                )

    def _check_architecture_for_subtasks(
        self, workflow_id: str, task: dict[str, Any], artifacts: list[dict[str, Any]]
    ) -> None:
        """If architecture analysis identified subsystems, suggest sub-analysis tasks."""
        task_id = task.get("task_id")
        # For now, only create a follow-up if the architecture plan has > 5 steps
        for art in artifacts:
            content = self._load_artifact_content(art)
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    continue
            arch_plan = content.get("architecture_plan", {}) if isinstance(content, dict) else {}
            steps = arch_plan.get("steps", [])

            if len(steps) > 5:
                parent_artifact = art.get("artifact_id")
                new_task = self._create_dynamic_task(
                    workflow_id=workflow_id,
                    task_type="verification",
                    name="Deep verification of complex architecture",
                    reason=f"Architecture plan has {len(steps)} steps; deep verification warranted",
                    parent_task_id=task_id,
                    triggering_artifact=parent_artifact,
                    required_capabilities=["verify"],
                    depends_on=[],
                    parameters={"deep_verify": True},
                )
                if new_task:
                    self._messaging_hub.task_lifecycle(
                        workflow_id=workflow_id,
                        tenant_id=self.config.tenant_id,
                        event="DYNAMIC_TASK_CREATED",
                        agent_id="autonomous-runtime",
                        task_id=new_task["task_id"],
                        details={"reason": f"Complex architecture with {len(steps)} steps"},
                    )
                break

    def _handle_blocked_task(self, workflow_id: str, task: dict[str, Any]) -> None:
        """Handle a blocked task: try reassignment or escalation."""
        task_id = task.get("task_id")
        reason = task.get("error", "blocked")

        if "no available agent" in (reason or "").lower():
            # Try to find an alternative agent with matching capabilities
            agent_id = self.engine._assign_agent(
                self.config.tenant_id, self.config.project_id, task
            )
            if agent_id:
                self.database.assign_task_to_agent(self.config.tenant_id, task_id, agent_id)
                self.database.update_task_status(self.config.tenant_id, task_id, "READY")
                self._messaging_hub.task_lifecycle(
                    workflow_id=workflow_id,
                    tenant_id=self.config.tenant_id,
                    event="TASK_STARTED",
                    agent_id=agent_id,
                    task_id=task_id,
                    details={"reassigned": True, "original_reason": reason},
                )
            else:
                self._messaging_hub.escalate(
                    workflow_id=workflow_id,
                    tenant_id=self.config.tenant_id,
                    reason=f"Task {task_id} is blocked: {reason}",
                    from_agent_id="autonomous-runtime",
                    task_id=task_id,
                )

    def _handle_failed_task(self, workflow_id: str, task: dict[str, Any]) -> None:
        """Handle a failed task: escalate if retries exhausted."""
        task_id = task.get("task_id")
        retry_count = task.get("retry_count", 0)
        max_retries = task.get("max_retries", 3)

        if retry_count >= max_retries:
            self._messaging_hub.escalate(
                workflow_id=workflow_id,
                tenant_id=self.config.tenant_id,
                reason=f"Task {task_id} (type={task.get('task_type')}) failed after {retry_count} retries",
                from_agent_id=task.get("agent_id") or "unknown",
                task_id=task_id,
                details={"error": task.get("error", "")},
            )

    def _handle_retried_task(self, workflow_id: str, task: dict[str, Any]) -> None:
        """Handle a retried task: check if pattern warrants escalation."""
        task_id = task.get("task_id")
        retry_count = task.get("retry_count", 0)

        if retry_count >= 2:
            self._messaging_hub.escalate(
                workflow_id=workflow_id,
                tenant_id=self.config.tenant_id,
                reason=f"Task {task_id} has been retried {retry_count} times",
                from_agent_id=task.get("agent_id") or "unknown",
                task_id=task_id,
            )

    def _create_dynamic_task(
        self,
        *,
        workflow_id: str,
        task_type: str,
        name: str,
        reason: str,
        parent_task_id: str | None,
        triggering_artifact: str | None,
        required_capabilities: list[str],
        depends_on: list[str],
        parameters: dict[str, Any],
        input_artifact_refs: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Create a dynamic task with full provenance metadata."""
        if self._dynamic_task_count >= self.config.max_dynamic_tasks:
            return None

        task_id = f"dyn-{uuid.uuid4().hex[:12]}"
        task_spec = {
            "task_id": task_id,
            "task_type": task_type,
            "name": name,
            "agent_id": None,  # Will be assigned by _mark_ready_tasks
            "required_capabilities": required_capabilities,
            "depends_on": depends_on,
            "input_artifacts": input_artifact_refs or [],
            "parameters": parameters,
            "parent_task_id": parent_task_id,
            "dynamic": True,
            "generated_reason": reason,
        }

        # Use the database's create_dynamic_task method
        try:
            task = self.database.create_dynamic_task(
                task_id=task_id,
                workflow_id=workflow_id,
                tenant_id=self.config.tenant_id,
                project_id=self.config.project_id,
                task_type=task_type,
                name=name,
                agent_id=None,
                required_capabilities=required_capabilities,
                depends_on=depends_on,
                input_artifacts=input_artifact_refs or [],
                max_retries=self.engine.policy.max_retries_default,
                parent_task_id=parent_task_id,
                generated_reason=reason,
            )

            # Record the dynamic task creation event
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=self.config.tenant_id,
                project_id=self.config.project_id,
                task_id=task_id,
                agent_id="autonomous-runtime",
                event_type="dynamic_task_created",
                detail={
                    "task_type": task_type,
                    "name": name,
                    "reason": reason,
                    "parent_task_id": parent_task_id,
                    "triggering_artifact": triggering_artifact,
                    "required_capabilities": required_capabilities,
                    "depends_on": depends_on,
                    "creator": "NEXUS runtime",
                    "creation_timestamp": _now(),
                },
            )

            # Update plan_json to include the dynamic task
            workflow = self.database.get_workflow(self.config.tenant_id, workflow_id)
            if workflow and workflow.get("plan_json"):
                plan = json.loads(workflow["plan_json"])
                plan.setdefault("dynamic_tasks", []).append({
                    "task_id": task_id,
                    "task_type": task_type,
                    "name": name,
                    "reason": reason,
                    "parent_task_id": parent_task_id,
                    "triggering_artifact": triggering_artifact,
                    "created_at": _now(),
                    "creator": "NEXUS runtime",
                })
                self.database.save_workflow_plan(self.config.tenant_id, workflow_id, plan)

            self._dynamic_task_count += 1
            return task
        except Exception as exc:
            logger.error(f"Failed to create dynamic task: {exc}")
            return None

    def _get_task_artifacts(self, workflow_id: str, task_id: str) -> list[dict[str, Any]]:
        """Get artifacts produced by a specific task."""
        all_artifacts = self.database.list_workflow_artifacts(self.config.tenant_id, workflow_id)
        return [a for a in all_artifacts if a.get("task_id") == task_id]

    def _load_artifact_content(self, artifact: dict[str, Any]) -> Any:
        """Load artifact content from the database or file."""
        content_path = artifact.get("content_path")
        if content_path:
            try:
                from pathlib import Path
                path = Path(content_path)
                if path.exists():
                    return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
        return artifact.get("content") or artifact.get("content_json")

    def build_execution_trace(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build a complete execution trace from persisted state.

        A workflow exposes:
        Objective → Planning decisions → Tasks → Agents → Messages →
        Tool executions → Artifacts → Dynamic tasks → Retries → Approvals →
        Verification → Final result
        """
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

        # Group events by type
        task_events = [e for e in events if e.get("task_id")]
        dynamic_tasks = [t for t in tasks if t.get("dynamic")]
        dynamic_task_events = [e for e in events if e.get("event_type") == "dynamic_task_created"]

        trace = {
            "objective": workflow.get("objective", ""),
            "scope": workflow.get("scope", ""),
            "status": workflow.get("status", ""),
            "total_tasks": len(tasks),
            "planning": {
                "plan": plan,
                "task_count": len(plan.get("tasks", [])),
                "template_type": plan.get("template_type"),
                "validations": plan.get("validations", []),
            },
            "tasks": [
                {
                    "task_id": t.get("task_id"),
                    "task_type": t.get("task_type"),
                    "name": t.get("name"),
                    "status": t.get("status"),
                    "agent_id": t.get("agent_id"),
                    "retry_count": t.get("retry_count", 0),
                    "max_retries": t.get("max_retries", 0),
                    "error": t.get("error"),
                    "started_at": t.get("started_at"),
                    "completed_at": t.get("completed_at"),
                    "depends_on": t.get("depends_on", []),
                    "required_capabilities": t.get("required_capabilities", []),
                    "output_artifacts": t.get("output_artifacts", []),
                    "is_dynamic": t.get("dynamic", False),
                    "generated_reason": t.get("generated_reason"),
                    "parent_task_id": t.get("parent_task_id"),
                    "creator": t.get("generated_reason") and "NEXUS runtime",
                    "creation_timestamp": t.get("created_at"),
                }
                for t in tasks
            ],
            "agents": {},
            "messages": messages,
            "artifacts": artifacts,
            "dynamic_tasks": [
                {
                    "task_id": dt.get("task_id"),
                    "name": dt.get("name"),
                    "reason": dt.get("generated_reason"),
                    "parent_task_id": dt.get("parent_task_id"),
                    "triggering_artifact": (dt.get("output_artifacts") or [None])[0],
                    "created_at": dt.get("created_at"),
                    "creator": "NEXUS runtime",
                }
                for dt in dynamic_tasks
            ],
            "dynamic_task_events": dynamic_task_events,
            "retries": [
                {
                    "task_id": e.get("task_id"),
                    "retry_count": next((t.get("retry_count", 0) for t in tasks if t.get("task_id") == e.get("task_id")), 0),
                    "event_type": e.get("event_type"),
                    "detail": e.get("detail", {}),
                    "timestamp": e.get("created_at"),
                }
                for e in events if e.get("event_type") == "task_retried"
            ],
            "approvals": [],
            "verification": {
                "checks": [
                    {
                        "artifact": t.get("task_id"),
                        "status": t.get("status"),
                        "error": t.get("error"),
                    }
                    for t in tasks
                ],
                "verified": all(t.get("status") == "COMPLETED" for t in tasks if t.get("task_type") != "verification"),
            },
            "final_result": {
                "status": workflow.get("status"),
                "completed": sum(1 for t in tasks if t.get("status") == "COMPLETED"),
                "failed": sum(1 for t in tasks if t.get("status") == "FAILED"),
                "total": len(tasks),
                "artifacts_produced": len(artifacts),
                "messages_exchanged": len(messages),
                "dynamic_tasks_created": len(dynamic_tasks),
            },
            "task_events": task_events,
        }

        # Authoritative enrichments from persisted rows (mirrors engine trace).
        tool_events = [e for e in events if e.get("event_type") == "tool_used"]
        tools_used: dict[str, Any] = {}
        for e in tool_events:
            detail = e.get("detail", {}) or {}
            cap = detail.get("capability", "unknown")
            entry = tools_used.setdefault(cap, {"count": 0, "targets": []})
            entry["count"] += 1
            if detail.get("target") and detail["target"] not in entry["targets"]:
                entry["targets"].append(detail["target"])
        trace["tools_used"] = tools_used
        reality_breakdown: dict[str, int] = {}
        for a in artifacts:
            reality_breakdown[a.get("reality", "UNKNOWN")] = reality_breakdown.get(a.get("reality", "UNKNOWN"), 0) + 1
        trace["reality_breakdown"] = reality_breakdown
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
            trace["finish_reason"] = "in progress"

        # Build agent info
        for task in tasks:
            agent_id = task.get("agent_id")
            if agent_id:
                if agent_id not in trace["agents"]:
                    trace["agents"][agent_id] = {
                        "agent_id": agent_id,
                        "tasks": [],
                        "messages_sent": 0,
                        "messages_received": 0,
                    }
                trace["agents"][agent_id]["tasks"].append({
                    "task_id": task.get("task_id"),
                    "status": task.get("status"),
                })
        for msg in messages:
            from_id = msg.get("from_agent_id")
            to_id = msg.get("to_agent_id")
            if from_id and from_id in trace["agents"]:
                trace["agents"][from_id]["messages_sent"] += 1
            if to_id and to_id in trace["agents"]:
                trace["agents"][to_id]["messages_received"] += 1

        # Add approval info
        approvals = self.database.list_pending_approvals(
            self.config.tenant_id, self.config.project_id
        )
        trace["approvals"] = [
            {
                "approval_id": a.get("approval_id"),
                "operation": a.get("operation"),
                "reason": a.get("reason"),
                "status": a.get("status"),
                "requested_by": a.get("requested_by"),
                "created_at": a.get("created_at"),
            }
            for a in approvals
        ]

        # Phase 7: model runtime visibility (persisted events only, no secrets).
        _model_types = {
            "model_invocation", "model_result", "model_failure", "model_timeout",
            "tool_requested", "tool_result", "tool_rejected", "tool_limit_exceeded",
            "max_iterations_exceeded", "execution_strategy",
        }
        _model_events = [e for e in events if e.get("event_type") in _model_types]
        _invocations = [e for e in _model_events if e.get("event_type") == "model_invocation"]
        _providers: list[str] = []
        for _e in _invocations:
            _p = (_e.get("detail", {}) or {}).get("provider")
            if _p and _p not in _providers:
                _providers.append(_p)
        trace["model"] = {
            "execution_mode": "MODEL/HYBRID" if _invocations else "DETERMINISTIC",
            "invocations": len(_invocations),
            "providers_used": _providers,
            "failures": len([e for e in _model_events if e.get("event_type") in ("model_failure", "model_timeout")]),
            "tool_requests": len([e for e in _model_events if e.get("event_type") == "tool_requested"]),
            "events": _model_events[:100],
        }
        try:
            from runtime.model_router import ModelRouter as _MR
            trace["model"]["router_status"] = _MR().redacted_status()
        except Exception:
            pass

        return trace

    def request_approval(
        self,
        workflow_id: str,
        operation: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Request human approval for a sensitive operation.

        Puts the task into AWAITING_APPROVAL state and creates a
        workflow_approvals record.
        """
        from runtime.canonical_core import core_id
        approval_id = f"approval-{core_id('app')}"

        approval = self.database.create_approval_request(
            approval_id=approval_id,
            workflow_id=workflow_id,
            tenant_id=self.config.tenant_id,
            project_id=self.config.project_id,
            task_id=task_id,
            requested_by="autonomous-runtime",
            operation=operation,
            reason=reason,
            payload=payload or {},
        )

        if task_id:
            self.database.update_task_status(
                self.config.tenant_id, task_id, "AWAITING_APPROVAL",
            )

        self._messaging_hub.approval_request(
            workflow_id=workflow_id,
            tenant_id=self.config.tenant_id,
            operation=operation,
            from_agent_id="autonomous-runtime",
            task_id=task_id,
            reason=reason,
            payload=payload,
        )

        self.database.add_workflow_event(
            event_id=f"evt-{core_id('evt')}",
            workflow_id=workflow_id,
            tenant_id=self.config.tenant_id,
            project_id=self.config.project_id,
            task_id=task_id,
            agent_id="autonomous-runtime",
            event_type="approval_requested",
            detail={"approval_id": approval_id, "operation": operation, "reason": reason},
        )

        return approval_id

    def handle_approval_decision(self, approval_id: str, decision: str, decided_by: str, note: str | None = None) -> bool:
        """Handle an approval decision (APPROVED/REJECTED)."""
        approval = self.database.get_approval(self.config.tenant_id, approval_id)
        if approval is None:
            return False

        self.database.decide_approval(
            self.config.tenant_id, approval_id, decision, decided_by, note
        )

        workflow_id = approval.get("workflow_id")
        task_id = approval.get("task_id")

        if task_id and decision == "APPROVED":
            self.database.update_task_status(self.config.tenant_id, task_id, "READY")
            self._messaging_hub.task_lifecycle(
                workflow_id=workflow_id,
                tenant_id=self.config.tenant_id,
                event="TASK_STARTED",
                agent_id="autonomous-runtime",
                task_id=task_id,
                details={"approved": True, "operation": approval.get("operation")},
            )
        elif task_id and decision == "REJECTED":
            # Phase 9: a denied approval terminates the gated task safely with
            # a persisted reason — it must never sit in AWAITING_APPROVAL forever.
            reason = f"approval rejected by {decided_by}: {note or approval.get('reason', '')}".strip()
            self.database.update_task_status(self.config.tenant_id, task_id, "FAILED", error=reason)

        event_type = "approval_granted" if decision == "APPROVED" else "approval_rejected"
        self.database.add_workflow_event(
            event_id=f"evt-{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            tenant_id=self.config.tenant_id,
            project_id=self.config.project_id,
            task_id=task_id,
            agent_id="autonomous-runtime",
            event_type=event_type,
            detail={"approval_id": approval_id, "decision": decision, "decided_by": decided_by},
        )

        return True

    def evaluate_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Evaluate an observation and determine if it requires action.

        Returns a dict with:
        - action: "proceed" | "retry" | "reassign" | "create_task" | "escalate" | "approve"
        - reason: explanation
        - payload: additional data for the action
        """
        reality = observation.get("reality", "UNKNOWN")
        status = observation.get("status", "UNKNOWN")
        error = observation.get("error", "")

        if status == "EXECUTED" and reality == "OBSERVED":
            return {"action": "proceed", "reason": "Real observation confirmed", "payload": {"reality": reality}}
        elif status == "BLOCKED":
            return {"action": "escalate", "reason": f"Action blocked: {error}", "payload": {"error": error}}
        elif status in ("SIMULATED", "INFERRED"):
            return {"action": "proceed", "reason": "Inferred result accepted", "payload": {"reality": reality, "untrusted": True}}
        else:
            return {"action": "retry", "reason": f"Unexpected status: {status}", "payload": {"status": status}}

    def run(self, tenant_id: str, project_id: str, workflow_id: str, max_iterations: int = 500) -> dict[str, Any]:
        """Run the autonomous control loop on an existing workflow."""
        self.config.tenant_id = tenant_id
        self.config.project_id = project_id

        for i in range(max_iterations):
            if not self._should_continue(workflow_id):
                break

            self._heartbeat(f"run-iteration-{i}")

            if self.config.auto_recover_stuck_seconds > 0:
                self.engine.recover_stuck_tasks(
                    self.config.tenant_id, workflow_id,
                    stale_seconds=self.config.auto_recover_stuck_seconds,
                )

            state = self.engine.step(tenant_id, project_id, workflow_id)
            self._observe_and_adapt(workflow_id, state)

            # engine.step() returns flat keys; get_workflow_state() nests under "summary"
            summary = state.get("summary", {}) or {}
            total = summary.get("total_tasks", state.get("total_tasks", 0))
            completed = summary.get("completed", state.get("completed", 0))
            failed = summary.get("failed", state.get("failed", 0))

            if total > 0 and completed + failed >= total:
                break

            time.sleep(self.config.poll_interval_seconds)

        final_state = self.engine.get_workflow_state(tenant_id, workflow_id)
        return self.build_execution_trace(final_state)
