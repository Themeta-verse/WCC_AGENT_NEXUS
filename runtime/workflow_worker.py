"""NEXUS Phase 4 — Durable WorkflowWorker.

The WorkflowWorker is a persistent, autonomous process that drives workflow
execution without external orchestration. It:

  - Heartbeats into the database so other workers can detect liveness
  - Claims READY tasks atomically (via the database claim_task method)
  - Executes tasks via the injected executor (MultiAgentExecutor)
  - Emits lifecycle messages through the MessagingHub
  - Recovers stuck tasks from dead workers
  - Retries failed tasks per policy
  - Polls until all workflows are COMPLETED / FAILED / CANCELLED

Architecture rule: The Worker does NOT replace the WorkflowEngine. It drives
the engine's step() loop and handles the durable lifecycle (claim, execute,
recover). The engine remains the orchestration authority for task graph logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import logging
import time
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger("nexus.workflow_worker")


@dataclass
class WorkerConfig:
    """Configuration for the WorkflowWorker."""
    worker_id: str = field(default_factory=lambda: f"worker-{uuid.uuid4().hex[:12]}")
    tenant_id: str = "default"
    poll_interval_seconds: float = 1.0
    claim_stale_seconds: int = 30
    heartbeat_interval_seconds: float = 5.0
    max_concurrent_tasks: int = 4
    stop_on_idle: bool = False
    idle_limit: int = 3
    auto_recover_stuck: bool = True


@dataclass
class WorkerStatus:
    """Current status of a worker."""
    worker_id: str
    status: str  # "ACTIVE", "IDLE", "STOPPED", "ERROR"
    running: bool
    started_at: str
    last_heartbeat: str
    claims_active: int
    claims_completed: int
    claims_failed: int
    errors: list[str] = field(default_factory=list)


class WorkflowWorker:
    """Durable, autonomous workflow worker.

    The worker runs a polling loop:
    1. Heartbeat (update liveness in database)
    2. Recover stuck tasks (if enabled)
    3. Claim READY tasks (atomic claim via database)
    4. Execute claimed tasks via the executor
    5. Process results (complete / retry / fail)
    6. Repeat

    The worker is designed to survive restarts: all state is in the database.
    A crashed worker's claimed tasks will be recovered by the next worker
    (or the same worker on restart) via the claim_stale_seconds mechanism.
    """

    def __init__(
        self,
        database: Any,
        engine: Any,
        executor: Any | None = None,
        agent_registry: Any = None,
        messaging_hub: Any = None,
        config: WorkerConfig | None = None,
        autonomous_runtime: Any = None,
    ):
        self.database = database
        self.engine = engine
        self.executor = executor or engine.executor
        self._agent_registry = agent_registry or getattr(engine, "_agent_registry", None)
        self._messaging_hub = messaging_hub or getattr(engine, "_messaging_hub", None)

        if self._messaging_hub is None:
            from runtime.messaging_hub import MessagingHub
            self._messaging_hub = MessagingHub(self.database)
            if hasattr(self.engine, "messaging_hub"):
                try:
                    self.engine.messaging_hub = self._messaging_hub
                except Exception:
                    pass

        self.config = config or WorkerConfig()
        self._autonomous_runtime = autonomous_runtime
        self._status = WorkerStatus(
            worker_id=self.config.worker_id,
            status="STOPPED",
            running=False,
            started_at=_now(),
            last_heartbeat=_now(),
            claims_active=0,
            claims_completed=0,
            claims_failed=0,
        )
        self._active_claims: dict[str, dict[str, Any]] = {}

    @property
    def status(self) -> WorkerStatus:
        return self._status

    @property
    def messaging_hub(self) -> Any:
        return self._messaging_hub

    def register(self) -> None:
        """Durably register this worker (STARTING) and mark it IDLE.

        Registration is the lease anchor: recovery distinguishes a
        registered-but-silent worker (STALE, tasks recoverable) from an
        orderly shutdown (STOPPED, nothing to recover).
        """
        try:
            capabilities = []
            registry = getattr(self, "_agent_registry", None)
            if registry is not None and hasattr(registry, "list_agents"):
                try:
                    capabilities = [a.agent_id for a in registry.list_agents(status="ACTIVE")]
                except Exception:
                    capabilities = []
            self.database.register_worker(
                self.config.worker_id,
                tenant_id=self.config.tenant_id,
                capabilities=capabilities,
            )
            self.database.heartbeat_worker(
                self.config.worker_id, "IDLE",
                {"lifecycle": "registered", "active_claims": 0},
                tenant_id=self.config.tenant_id,
            )
        except Exception as exc:
            logger.warning(f"Worker registration failed for {self.config.worker_id}: {exc}")
        self._status.status = "IDLE"
        self._status.last_heartbeat = _now()

    def heartbeat(self, current_workflow_id: str | None = None, current_task_id: str | None = None) -> None:
        """Renew the worker lease in durable state and update internal status."""
        lease_status = "BUSY" if (self._active_claims or current_task_id) else (
            self._status.status if self._status.status in ("IDLE", "BUSY", "ACTIVE") else "ACTIVE")
        details = {
            "status": lease_status,
            "active_claims": len(self._active_claims),
            "completed": self._status.claims_completed,
            "failed": self._status.claims_failed,
        }
        try:
            self.database.heartbeat_worker(
                self.config.worker_id, lease_status, details,
                tenant_id=self.config.tenant_id,
                current_workflow_id=current_workflow_id,
                current_task_id=current_task_id,
                clear_current_task=current_task_id is None and not self._active_claims,
            )
        except Exception as exc:
            logger.warning(f"Heartbeat failed for {self.config.worker_id}: {exc}")
        self._status.last_heartbeat = _now()

    def start_workflow_if_pending(self, tenant_id: str, project_id: str, workflow_id: str) -> bool:
        """Start a workflow if it's still PENDING. Idempotent."""
        workflow = self.database.get_workflow(tenant_id, workflow_id)
        if workflow is None:
            return False
        if workflow["status"] == "PENDING":
            self.engine.start_workflow(tenant_id, project_id, workflow_id)
            self._messaging_hub.task_lifecycle(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                event="TASK_STARTED",
                agent_id=self.config.worker_id,
                task_id=None,
                details={"worker_id": self.config.worker_id, "workflow_id": workflow_id},
            )
            return True
        return False

    def execute_workflow(
        self,
        tenant_id: str,
        project_id: str,
        workflow_id: str,
        max_ticks: int = 100,
        poll_interval: float | None = None,
    ) -> dict[str, Any]:
        """Execute a workflow to completion via the engine's step() loop.

        This is the autonomous execution path: the worker starts the workflow
        (if PENDING), then repeatedly calls step() until all tasks complete.
        """
        interval = poll_interval if poll_interval is not None else self.config.poll_interval_seconds

        # Phase 9: durable registration first — the lease anchor for recovery.
        self.register()
        self.start_workflow_if_pending(tenant_id, project_id, workflow_id)

        self._status.running = True
        self._status.status = "ACTIVE"

        idle_count = 0
        prev_completed = 0
        prev_failed = 0
        for tick in range(max_ticks):
            if not self._status.running:
                break

            self.heartbeat(current_workflow_id=workflow_id)

            if self.config.auto_recover_stuck:
                self.recover_stuck(tenant_id, workflow_id)

            state = self.engine.step(tenant_id, project_id, workflow_id)

            # Phase 5/6: Observe and adapt — detect need for dynamic tasks, retries, recovery
            if self._autonomous_runtime is not None:
                try:
                    self._autonomous_runtime._observe_and_adapt(workflow_id, state)
                except Exception as exc:
                    logger.warning(f"Autonomous observe_and_adapt failed: {exc}")

            # Handle both check_workflow_completion format and get_workflow_state format
            summary = state.get("summary", {})
            if not summary:
                total = state.get("total_tasks", 0)
                completed = state.get("completed", 0)
                failed = state.get("failed", 0)
                running = state.get("running", 0)
            else:
                total = summary.get("total_tasks", 0)
                completed = summary.get("completed", 0)
                failed = summary.get("failed", 0)
                running = summary.get("running", 0)

            if completed + failed >= total and total > 0:
                self._messaging_hub.task_lifecycle(
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    event="TASK_COMPLETED" if failed == 0 else "TASK_FAILED",
                    agent_id=self.config.worker_id,
                    task_id=None,
                    details={
                        "total": total,
                        "completed": completed,
                        "failed": failed,
                        "workflow_status": state.get("status"),
                    },
                )
                return state

            # If tasks are awaiting approval, pause execution until resolved
            await_approval = sum(1 for t in state.get("tasks", []) if t.get("status") == "AWAITING_APPROVAL")
            if await_approval > 0 and completed + failed < total:
                logger.info(f"Workflow {workflow_id} has {await_approval} task(s) awaiting approval")
                return state

            if summary.get("running", 0) == 0 and completed + failed < total:
                progress_made = completed > prev_completed or failed > prev_failed
                if progress_made:
                    idle_count = 0
                else:
                    idle_count += 1
                    if self.config.stop_on_idle and idle_count >= self.config.idle_limit:
                        logger.warning(f"Workflow {workflow_id} stalled with no running tasks")
                        return state
            else:
                idle_count = 0

            prev_completed = completed
            prev_failed = failed

            time.sleep(interval)

        return self.engine.get_workflow_state(tenant_id, workflow_id)

    def execute_task_claim(self, tenant_id: str, project_id: str, workflow_id: str, task_id: str) -> bool:
        """Claim and execute a single task atomically.

        Returns True if the task was claimed and executed, False if it
        could not be claimed (already in progress or not READY).

        Every attempt carries an execution_id (workflow/task/attempt/worker)
        so artifacts, events and retries stay attributable after recovery.
        """
        execution_id = f"exec-{uuid.uuid4().hex[:12]}"
        claimed = self.database.claim_task(tenant_id, task_id, self.config.worker_id,
                                           execution_id=execution_id)
        if not claimed:
            return False
        self.heartbeat(current_workflow_id=workflow_id, current_task_id=task_id)

        self._messaging_hub.task_lifecycle(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            event="TASK_STARTED",
            agent_id=self.config.worker_id,
            task_id=task_id,
            details={"worker_id": self.config.worker_id, "execution_id": execution_id},
        )

        task = self.database.get_workflow_task(tenant_id, task_id)
        if task is None:
            self.database.release_task(tenant_id, task_id, self.config.worker_id)
            return False

        try:
            self._execute_single_task(tenant_id, project_id, workflow_id, task,
                                      execution_id=execution_id)
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=self.config.worker_id,
                event_type="worker_completed",
                detail={"worker_id": self.config.worker_id, "task_id": task_id,
                        "execution_id": execution_id},
            )
            self.heartbeat(current_workflow_id=workflow_id)
            return True
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(f"Task execution failed: {task_id}")
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=self.config.worker_id,
                event_type="worker_error",
                detail={"task_id": task_id, "error": error_msg,
                        "worker_id": self.config.worker_id, "execution_id": execution_id},
            )
            self.database.release_task(tenant_id, task_id, self.config.worker_id)
            return False

    def _execute_single_task(
        self,
        tenant_id: str,
        project_id: str,
        workflow_id: str,
        task: dict[str, Any],
        execution_id: str | None = None,
    ) -> None:
        """Execute a single claimed task through the executor."""
        task_id = task["task_id"]
        agent_id = task.get("agent_id")

        if agent_id is None:
            agent_id = self.engine._assign_agent(tenant_id, project_id, task)
            if agent_id:
                self.database.assign_task_to_agent(tenant_id, task_id, agent_id)

        if agent_id is None:
            if self.engine.policy.fail_on_agent_not_available:
                self.database.update_task_status(
                    tenant_id, task_id, "BLOCKED",
                    error="no available agent with required capabilities"
                )
                return
            agent_id = "generic-agent"

        self._messaging_hub.task_lifecycle(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            event="TASK_STARTED",
            agent_id=agent_id,
            task_id=task_id,
        )

        self.database.assign_task_to_agent(tenant_id, task_id, agent_id)
        self.database.update_task_status(tenant_id, task_id, "RUNNING")

        workflow = self.database.get_workflow(tenant_id, workflow_id)
        observation_scope = workflow.get("scope") if workflow else None
        # Phase E: workflow environment reaches the agent via task parameters.
        execution_environment = "LOCAL"
        try:
            import json as _json
            _plan = _json.loads((workflow or {}).get("plan_json") or "{}")
            from runtime.execution_environment import resolve_environment as _resolve_env
            execution_environment = _resolve_env(_plan.get("execution_environment")).value
        except (ValueError, TypeError, AttributeError):
            execution_environment = "LOCAL"

        self.database.add_workflow_event(
            event_id=f"evt-{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            event_type="agent_started",
            detail={"task_id": task_id, "agent_id": agent_id,
                    "worker_id": self.config.worker_id,
                    "execution_id": execution_id,
                    "attempt": int(task.get("claim_count") or 0),
                    "execution_environment": execution_environment},
        )

        result = self.executor.execute_task(
            workflow_id=workflow_id,
            task_id=task_id,
            task_type=task["task_type"],
            task_name=task["name"],
            agent_id=agent_id,
            capabilities=task["required_capabilities"],
            scope=observation_scope or workflow_id,
            observation_scope=observation_scope,
            input_artifacts=task["input_artifacts"],
            parameters={"execution_environment": execution_environment},
        )

        self._process_task_result(tenant_id, project_id, workflow_id, task, agent_id, result)

    def _process_task_result(
        self,
        tenant_id: str,
        project_id: str,
        workflow_id: str,
        task: dict[str, Any],
        agent_id: str,
        result: Any,
    ) -> None:
        """Process the result of a task execution: store artifacts, emit events, update status."""
        task_id = task["task_id"]

        if hasattr(result, "artifacts"):
            artifacts = result.artifacts
            reality = getattr(result, "reality", "INFERRED")
            untrusted = getattr(result, "untrusted", True)
            result_dict = getattr(result, "result", {})
            provenance = getattr(result, "provenance", [f"agent:{agent_id}"])
            status = getattr(result, "status", "COMPLETED")
            error = getattr(result, "error", None)
            exec_meta = getattr(result, "execution_metadata", {}) or {}
        elif hasattr(result, "get"):
            artifacts = result.get("artifacts", [])
            reality = result.get("reality", "INFERRED")
            untrusted = result.get("untrusted", True)
            result_dict = result.get("result", {})
            provenance = result.get("provenance", [f"agent:{agent_id}"])
            status = result.get("status", "COMPLETED")
            error = result.get("error")
            exec_meta = result.get("execution_metadata", {}) or {}
        else:
            artifacts = []
            reality = "INFERRED"
            untrusted = True
            result_dict = {}
            provenance = [f"agent:{agent_id}"]
            status = "COMPLETED"
            error = None
            exec_meta = {}

        for tool_use in (exec_meta.get("tool_executions") or []):
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type="tool_used",
                detail={
                    "capability": tool_use.get("capability"),
                    "target": tool_use.get("target"),
                    "status": tool_use.get("status"),
                    "reality": tool_use.get("reality"),
                    "content_sha256": tool_use.get("content_sha256"),
                    "receipt_id": tool_use.get("receipt_id"),
                    "workspace_root": tool_use.get("workspace_root"),
                },
            )

        # Phase 8: persist model invocation / tool-request / failure events
        # reported by model-backed agents (pre-redacted by the agent layer;
        # no API keys ever reach the database). Mirrors WorkflowEngine so
        # the trace is complete whichever driver executes the task.
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
            detail = {k: v for k, v in inv.items() if k != "api_key" and "secret" not in k.lower() and "token" not in k.lower() or k in ("content_sha256",)}
            for k, v in list(detail.items()):
                if isinstance(v, str) and len(v) > 2000:
                    detail[k] = v[:2000] + "...[truncated]"
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type=inv_type,
                detail=detail,
            )
        _strategy = exec_meta.get("execution_strategy")
        if isinstance(_strategy, dict) and _strategy:
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
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

        for input_art_name in (task.get("input_artifacts") or []):
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type="artifact_consumed",
                detail={
                    "artifact_ref": input_art_name,
                    "agent_id": agent_id,
                    "task_id": task_id,
                },
            )

        produced_artifact_ids: list[str] = []
        for artifact_data in (artifacts or []):
            artifact_id = f"art-{uuid.uuid4().hex[:12]}"
            content = artifact_data.get("content")
            content_path = self._persist_artifact_content(artifact_id, content)

            # Phase 9: link the artifact to this execution attempt for
            # crash-safe idempotency (same content reproduced after recovery
            # reuses the existing row instead of duplicating it).
            execution_id = task.get("last_execution_id")
            if not execution_id:
                try:
                    fresh = self.database.get_workflow_task(tenant_id, task_id) or {}
                    execution_id = fresh.get("last_execution_id")
                except Exception:
                    execution_id = None
            artifact = self.database.create_artifact(
                artifact_id=artifact_id,
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                kind=artifact_data.get("kind", "unknown"),
                name=artifact_data.get("name", f"artifact_{artifact_id}"),
                content_hash=artifact_data.get("content_hash", self._digest(content if content else "")),
                parent_artifacts=artifact_data.get("parent_artifacts", []),
                content_path=content_path,
                content_size=artifact_data.get("content_size"),
                reality=artifact_data.get("reality", reality),
                untrusted=artifact_data.get("untrusted", untrusted),
                verification_state=artifact_data.get("verification_state", "UNVERIFIED"),
                provenance=artifact_data.get("provenance", provenance),
                execution_id=execution_id,
            )
            surviving_id = artifact.get("artifact_id", artifact_id)
            if artifact.get("deduplicated") and content_path and artifact.get("content_path") != content_path:
                try:
                    from pathlib import Path as _Path
                    _Path(content_path).unlink(missing_ok=True)
                except OSError:
                    pass
            produced_artifact_ids.append(surviving_id)
            self.database.add_output_artifact(tenant_id, task_id, surviving_id)

            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type="artifact_produced",
                detail={
                    "artifact_id": surviving_id,
                    "kind": artifact["kind"],
                    "name": artifact["name"],
                    "content_hash": artifact["content_hash"],
                    "reality": artifact_data.get("reality", reality),
                    "provenance": artifact_data.get("provenance", provenance),
                    "parent_artifacts": artifact_data.get("parent_artifacts", []),
                    "execution_id": execution_id,
                    "deduplicated": bool(artifact.get("deduplicated")),
                },
            )

        if status == "COMPLETED":
            self.database.update_task_status(
                tenant_id, task_id, "COMPLETED",
                reality=reality,
                result_json=json.dumps(result_dict),
            )
            self._messaging_hub.task_lifecycle(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                event="TASK_COMPLETED",
                agent_id=agent_id,
                task_id=task_id,
                details={"artifact_count": len(artifacts or [])},
            )
            self.database.add_workflow_event(
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                event_type="agent_completed",
                detail={"task_id": task_id, "agent_id": agent_id, "artifact_count": len(artifacts or [])},
            )
        else:
            task_record = self.database.get_workflow_task(tenant_id, task_id)
            retry_count = task_record.get("retry_count", 0) if task_record else 0
            max_retries = task_record.get("max_retries", self.engine.policy.max_retries_default) if task_record else 0

            if retry_count < max_retries and self.engine.policy.auto_retry_on_failure:
                self.database.increment_task_retry(tenant_id, task_id)
                self._messaging_hub.task_lifecycle(
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    event="TASK_RETRY",
                    agent_id=agent_id,
                    task_id=task_id,
                    details={"attempt": retry_count + 1, "error": error or str(result)},
                )
                self.database.update_task_status(tenant_id, task_id, "READY", error=None)
            else:
                self.database.update_task_status(
                    tenant_id, task_id, "FAILED",
                    reality="UNKNOWN",
                    error=error or f"status={status}",
                )
                self._messaging_hub.task_lifecycle(
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    event="TASK_FAILED",
                    agent_id=agent_id,
                    task_id=task_id,
                    details={"error": error or str(result), "retry_count": retry_count},
                )
                self.database.add_workflow_event(
                    event_id=f"evt-{uuid.uuid4().hex[:12]}",
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    task_id=task_id,
                    agent_id=agent_id,
                    event_type="task_failed",
                    detail={"task_id": task_id, "agent_id": agent_id, "error": error or str(result)},
                )

        self.database.release_task(tenant_id, task_id, self.config.worker_id)

    def _persist_artifact_content(self, artifact_id: str, content: Any | None) -> str | None:
        """Persist artifact content to disk, mirroring WorkflowEngine._persist_artifact_content."""
        if content is None:
            return None
        root = getattr(self.engine, "_artifacts_root", None)
        if root is None:
            return None
        try:
            from pathlib import Path
            artifacts_dir = root / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            file_path = artifacts_dir / f"{artifact_id}.json"
            if isinstance(content, str):
                file_path.write_text(content, encoding="utf-8")
            else:
                file_path.write_text(json.dumps(content, default=str, indent=2), encoding="utf-8")
            return str(file_path)
        except (OSError, TypeError):
            return None

    def _digest(self, value: Any) -> str:
        import hashlib
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    def recover_stuck(self, tenant_id: str, workflow_id: str, stale_seconds: int | None = None) -> int:
        """Recover tasks stuck on dead workers."""
        seconds = stale_seconds if stale_seconds is not None else self.config.claim_stale_seconds
        return self.engine.recover_stuck_tasks(tenant_id, workflow_id, seconds)

    def run(self, tenant_id: str, project_id: str, workflow_id: str | None = None) -> WorkerStatus:
        """Run the worker loop, executing workflows or a specific workflow.

        If workflow_id is provided, executes only that workflow.
        Otherwise, finds and executes all RUNNING workflows.
        """
        self._status.running = True
        self._status.status = "ACTIVE"
        self._status.started_at = _now()

        try:
            if workflow_id:
                self.execute_workflow(tenant_id, project_id, workflow_id)
            else:
                workflows = self.database.list_workflows(tenant_id, limit=50)
                for wf in workflows:
                    if wf["status"] == "RUNNING":
                        try:
                            self.execute_workflow(tenant_id, project_id, wf["workflow_id"])
                        except Exception as exc:
                            logger.exception(f"Worker error on workflow {wf['workflow_id']}: {exc}")
                            self._status.errors.append(f"{wf['workflow_id']}: {exc}")
        finally:
            self._status.running = False
            self._status.status = "STOPPED"

        return self._status

    def stop(self, reason: str = "operator stop") -> None:
        """Signal the worker to stop and persist the orderly shutdown.

        A STOPPED worker is never reported STALE: recovery must not chase
        tasks that were deliberately released.
        """
        self._status.running = False
        self._status.status = "STOPPED"
        try:
            self.database.stop_worker(self.config.worker_id, reason=reason)
        except Exception as exc:
            logger.warning(f"Worker stop persistence failed for {self.config.worker_id}: {exc}")
        self._status.last_heartbeat = _now()
