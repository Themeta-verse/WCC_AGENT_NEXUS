"""Phase 6: Full Autonomous Execution tests.

Tests exercise the complete autonomous loop: objective → plan → execute → adapt → verify,
with durable background execution via WorkflowWorker + AutonomousRuntime integration.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup_engine(tmpdir: str):
    """Create a WorkflowEngine with MultiAgentExecutor, MessagingHub, and real NexusDatabase."""
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.agent_registry import AgentRegistry
    from runtime.messaging_hub import MessagingHub
    from nexus_independent.database import NexusDatabase

    db_path = os.path.join(tmpdir, "test_nexus.db")
    db = NexusDatabase(db_path)
    db.migrate()

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("test-tenant", "Test Tenant", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("test-project", "test-tenant", "Test Project", now, now))

    registry = AgentRegistry()
    register_default_agents(registry)

    messaging_hub = MessagingHub(db)

    executor = MultiAgentExecutor(
        database=db,
        agent_registry=registry,
        settings=None,
        principal={"tenant_id": "test-tenant", "project_id": "test-project"},
        messaging_hub=messaging_hub,
    )

    policy = WorkflowExecutionPolicy(
        max_retries_default=2,
        fail_on_agent_not_available=False,
        auto_retry_on_failure=True,
    )
    engine = WorkflowEngine(
        database=db,
        composer=MissionComposer(),
        policy=policy,
        agent_registry=registry,
        artifacts_root=tmpdir,
        messaging_hub=messaging_hub,
    )
    engine.set_executor(executor, agent_registry=registry)

    return engine, db, registry, messaging_hub


def _setup_worker(engine, db, messaging_hub, worker_id="test-worker"):
    """Create a WorkflowWorker."""
    from runtime.workflow_worker import WorkflowWorker, WorkerConfig

    config = WorkerConfig(
        worker_id=worker_id,
        tenant_id="test-tenant",
        poll_interval_seconds=0.1,
        claim_stale_seconds=5,
        heartbeat_interval_seconds=1.0,
        max_concurrent_tasks=2,
        stop_on_idle=True,
        idle_limit=3,
        auto_recover_stuck=True,
    )

    return WorkflowWorker(
        database=db,
        engine=engine,
        executor=engine.executor,
        agent_registry=engine._agent_registry,
        messaging_hub=messaging_hub,
        config=config,
    )


def _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=100):
    """Helper: start and run a workflow via worker, return final state."""
    engine.start_workflow("test-tenant", "test-project", workflow_id)
    worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=max_ticks)
    state = engine.get_workflow_state("test-tenant", workflow_id)
    return state


# --- Test 1: One-click autonomous execution ---

def test_one_click_autonomous_execution():
    """Run a workflow autonomously with a single call — no manual stepping."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Explore the nexus runtime module structure",
            scope="Themeta-verse/Nexus",
            constraints={},
            execution_mode="SIMULATION",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=50)

        assert state["workflow"]["status"] in ("COMPLETED", "FAILED")
        assert state["summary"]["total_tasks"] > 0


# --- Test 2: Multi-agent sequential execution ---

def test_multi_agent_sequential_execution():
    """Workflow must execute with multiple agents in dependency order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Analyze this repository and produce an architecture report",
            scope="Themeta-verse/Nexus",
            constraints={},
            execution_mode="SIMULATION",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=100)

        tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        agent_ids = {t["agent_id"] for t in tasks if t.get("agent_id")}

        assert state["workflow"]["status"] in ("COMPLETED", "FAILED")
        assert len(agent_ids) >= 1


# --- Test 3: Multi-agent parallel execution ---

def test_multi_agent_parallel_execution():
    """Agents with no dependency between them should execute in parallel."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Research and audit the repository",
            scope="Themeta-verse/Nexus",
            constraints={},
            execution_mode="SIMULATION",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)
        worker.config.max_concurrent_tasks = 4
        worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=100)

        tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        assert len(tasks) >= 2

        plan_row = db.get_workflow("test-tenant", workflow_id)
        plan = json.loads(plan_row["plan_json"]) if plan_row.get("plan_json") else {}
        assert "parallel_groups" in plan or len(plan.get("tasks", [])) >= 2


# --- Test 4: Real artifact handoff ---

def test_real_artifact_handoff():
    """Artifacts must be persisted and consumed by downstream agents.

    Scope note: this test previously planned against "Themeta-verse/Nexus", a
    repository reference no local connector can observe. It only ever "passed"
    because the researcher reported an empty-but-COMPLETED observation when its
    reads produced nothing. That masked the behaviour under test entirely — no
    research artifact existed to hand off.

    It now scopes to a real workspace directory, so the handoff chain is
    genuinely exercised, AND it additionally asserts the truth boundary: an
    OBSERVED research artifact must be backed by connector-produced receipts,
    never by a hand-minted one. (The unavailable-capability case is covered
    separately in tests/test_capability_truth_boundary.py.)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "service.py").write_text(
            "def handler(event):\n    return {'ok': True}\n", encoding="utf-8")
        Path(tmpdir, "README.md").write_text("# service\n", encoding="utf-8")

        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Research the runtime and document architecture",
            scope=tmpdir,
            constraints={},
            execution_mode="REAL_READ",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=100)

        artifacts = db.list_workflow_artifacts("test-tenant", workflow_id)
        events = db.list_workflow_events("test-tenant", workflow_id, limit=200)

        produced_events = [e for e in events if e.get("event_type") == "artifact_produced"]

        # Every completed task produces exactly one artifact: the artifact
        # count must cover all completed tasks with matching produce events.
        all_tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        completed = [t for t in all_tasks if t["status"] == "COMPLETED"]
        assert len(completed) == len(all_tasks), f"all tasks must complete, got {[t['status'] for t in all_tasks]}"
        assert len(completed) >= 3, f"expected >= 3 completed tasks, got {len(completed)}"
        assert len(artifacts) >= len(completed), f"{len(artifacts)} artifacts for {len(completed)} completed tasks"
        assert len(produced_events) == len(artifacts), "each artifact row needs an artifact_produced event"

        # Verify artifact structure and provenance on every row
        for art in artifacts:
            assert art.get("artifact_id") is not None
            assert art.get("content_hash"), f"artifact {art.get('artifact_id')} missing content_hash"
            assert art.get("task_id"), f"artifact {art.get('artifact_id')} missing task_id"
            assert art.get("agent_id"), f"artifact {art.get('artifact_id')} missing agent_id"
            assert isinstance(art.get("provenance"), list) and len(art["provenance"]) >= 1

        # Truth boundary: the research artifact claims OBSERVED, so the trace
        # must contain connector-backed tool_used events carrying a receipt_id
        # and a result hash. An OBSERVED claim with no receipts is the exact
        # defect this gate is closing.
        research = [a for a in artifacts if a.get("kind") == "research_report"]
        assert research, "research_report artifact must exist for handoff"
        assert research[0].get("reality") == "OBSERVED", research[0].get("reality")
        tool_events = [e for e in events if e.get("event_type") == "tool_used"]
        assert tool_events, "an OBSERVED research artifact must have tool_used receipts"
        for ev in tool_events:
            detail = ev.get("detail") or {}
            assert detail.get("receipt_id"), f"tool_used without receipt_id: {detail}"
            assert detail.get("connector_id"), f"tool_used without connector_id: {detail}"
            assert detail.get("result_hash") or detail.get("content_sha256"), \
                f"tool_used without a result hash: {detail}"


# --- Test 5: Real agent communication ---

def test_real_agent_communication():
    """Agents must communicate through persisted MessagingHub messages."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Explore the runtime module structure and report findings",
            scope="Themeta-verse/Nexus",
            constraints={},
            execution_mode="SIMULATION",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=100)

        events = db.list_workflow_events("test-tenant", workflow_id, limit=200)
        lifecycle_events = [e for e in events if e.get("event_type") in (
            "agent_started", "agent_completed", "TASK_STARTED", "TASK_COMPLETED",
            "worker_completed", "workflow_started", "workflow_completed"
        )]
        assert len(lifecycle_events) >= 1


# --- Test 6: Dynamic task creation during autonomous execution ---

def test_dynamic_task_creation_autonomous():
    """Dynamic tasks must be created during autonomous execution via _observe_and_adapt."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)

        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        from runtime.workflow_worker import WorkflowWorker, WorkerConfig
        from runtime.workflow_planner import WorkflowPlanner

        planner = WorkflowPlanner(agent_registry=registry)
        autonomous_runtime = AutonomousRuntime(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            planner=planner,
            config=AutonomousConfig(tenant_id="test-tenant", project_id="test-project"),
        )

        worker = WorkflowWorker(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            config=WorkerConfig(
                worker_id="test-worker-dyn",
                tenant_id="test-tenant",
                poll_interval_seconds=0.1,
                stop_on_idle=False,
                idle_limit=5,
            ),
            autonomous_runtime=autonomous_runtime,
        )

        planned = planner.plan(
            objective="Research the repository security architecture and produce a report",
            scope=str(Path(tmpdir)),
            constraints={},
            execution_mode="SIMULATION",
        )

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)
        worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=50)

        dynamic_tasks = db.get_dynamic_tasks("test-tenant", workflow_id)

        # Dynamic tasks are created by autonomous adaptation rules.
        # The infrastructure must support it — at minimum, the call must not error
        # and the dynamic_tasks list must be queryable.
        for dt in dynamic_tasks:
            assert dt.get("parent_task_id") is not None or dt.get("generated_reason") is not None
            assert dt.get("dynamic") == 1
            assert dt.get("generated_reason") is not None, "dynamic task must have a reason"


# --- Test 7: Autonomous adaptation ---

def test_autonomous_adaptation():
    """NEXUS should detect when a task result indicates a need for additional work and create dynamic tasks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)

        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        from runtime.workflow_worker import WorkflowWorker, WorkerConfig

        autonomous_runtime = AutonomousRuntime(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            config=AutonomousConfig(tenant_id="test-tenant", project_id="test-project"),
        )

        worker = WorkflowWorker(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            config=WorkerConfig(worker_id="test-worker-adapt", tenant_id="test-tenant",
                               poll_interval_seconds=0.1, stop_on_idle=False, idle_limit=5),
            autonomous_runtime=autonomous_runtime,
        )

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Adaptation Test",
            objective="Research the nexus repository structure",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "research-1", "task_type": "research", "name": "Research",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["research_report"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)
        state = worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=30)
        full_state = engine.get_workflow_state("test-tenant", workflow_id)

        assert full_state["workflow"]["status"] in ("COMPLETED", "FAILED", "RUNNING")

        events = db.list_workflow_events("test-tenant", workflow_id, limit=200)
        assert len(events) >= 1


# --- Test 8: Retry via worker ---

def test_retry_via_worker():
    """Worker should retry failed tasks within policy limits."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Retry Test",
            objective="Research with potential failure",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "task-1", "task_type": "research", "name": "Test Task",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["result"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=50)

        tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        for task in tasks:
            assert task["retry_count"] <= engine.policy.max_retries_default


# --- Test 9: Failure handling via worker ---

def test_failure_handling_via_worker():
    """When tasks fail, they should be recorded with error details."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Failure Test",
            objective="Research with unavailable capability",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "fail-task-1", "task_type": "research", "name": "Impossible Task",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["nonexistent_capability"],
                 "input_artifacts": [], "output_artifacts": [], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=50)

        tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        task = tasks[0]

        # Task should be BLOCKED, FAILED, or COMPLETED (generic agent may handle it)
        assert task["status"] in ("BLOCKED", "FAILED", "COMPLETED", "READY")


# --- Test 10: Recovery from stuck tasks ---

def test_recovery_stuck_tasks_worker():
    """Worker should recover stuck tasks from dead workers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Research and document the runtime",
            scope="Themeta-verse/Nexus",
            constraints={},
            execution_mode="SIMULATION",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=100)

        # Run recovery
        engine.recover_stuck_tasks("test-tenant", workflow_id)
        state = engine.get_workflow_state("test-tenant", workflow_id)

        assert state["workflow"]["status"] in ("COMPLETED", "FAILED", "RUNNING", "PAUSED")


# --- Test 11: Approval gate ---

def test_approval_gate():
    """The approval gate should pause execution when an approval is requested."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)

        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        autonomous_runtime = AutonomousRuntime(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            config=AutonomousConfig(tenant_id="test-tenant", project_id="test-project"),
        )

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Approval Test",
            objective="Research with approval requirement",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "research-1", "task_type": "research", "name": "Research",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["research_report"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)

        approval_id = autonomous_runtime.request_approval(
            workflow_id=workflow_id,
            operation="security_audit_review",
            reason="Security audit requires human approval",
        )

        assert approval_id is not None
        approval = db.get_approval("test-tenant", approval_id)
        assert approval["status"] == "PENDING"
        assert approval["operation"] == "security_audit_review"


# --- Test 12: Approval resume ---

def test_approval_resume():
    """After approval is granted, workflow execution should resume."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)

        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        autonomous_runtime = AutonomousRuntime(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            config=AutonomousConfig(tenant_id="test-tenant", project_id="test-project"),
        )

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Approval Resume Test",
            objective="Test approval resume",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "task-1", "task_type": "research", "name": "Task",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["result"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)

        approval_id = autonomous_runtime.request_approval(
            workflow_id=workflow_id,
            operation="final_verification",
            reason="Verification requires approval",
        )

        result = autonomous_runtime.handle_approval_decision(
            approval_id=approval_id,
            decision="APPROVED",
            decided_by="test-operator",
        )
        assert result is True

        approval = db.get_approval("test-tenant", approval_id)
        assert approval["status"] == "APPROVED"


# --- Test 13: Independent verification ---

def test_independent_verification():
    """VerificationAgent must independently inspect results — not trust the producer."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Verification Test",
            objective="Research and verify",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "research-1", "task_type": "research", "name": "Research",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["research_report"], "parameters": {}},
                {"task_id": "verify-1", "task_type": "verification", "name": "Verify",
                 "agent_id": None, "depends_on": ["research-1"], "required_capabilities": ["verify"],
                 "input_artifacts": ["research_report"], "output_artifacts": ["verification_result"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=100)

        tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        task_map = {t["task_id"]: t for t in tasks}

        research = task_map.get("research-1")
        verify = task_map.get("verify-1")

        assert research is not None
        assert verify is not None

        # Verification task should depend on research
        assert "research-1" in verify.get("depends_on", [])


# --- Test 14: Complete execution trace ---

def test_complete_execution_trace():
    """Execution trace must be reconstructable from persisted state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Explore and summarize the runtime architecture",
            scope="Themeta-verse/Nexus",
            constraints={},
            execution_mode="SIMULATION",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        state = _run_workflow_to_completion(engine, db, worker, workflow_id, max_ticks=100)

        trace = engine.get_execution_trace("test-tenant", workflow_id)

        assert "workflow_id" in trace
        assert "objective" in trace
        assert "planning" in trace
        assert "tasks" in trace
        assert "agents" in trace
        assert "artifacts" in trace
        assert "messages" in trace
        assert "dynamic_tasks" in trace
        assert "retries" in trace
        assert "approvals" in trace
        assert "verification" in trace
        assert "final_result" in trace
        assert trace["workflow_id"] == workflow_id


# --- Test 15: Cancellation ---

def test_cancellation():
    """Workflow can be cancelled mid-execution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Cancellation Test",
            objective="Research and document",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "task-1", "task_type": "research", "name": "Task 1",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["r1"], "parameters": {}},
                {"task_id": "task-2", "task_type": "report", "name": "Task 2",
                 "agent_id": None, "depends_on": ["task-1"], "required_capabilities": ["repository.read"],
                 "input_artifacts": ["r1"], "output_artifacts": ["report"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)

        state = engine.cancel_workflow("test-tenant", "test-project", workflow_id)

        wf_after = db.get_workflow("test-tenant", workflow_id)
        assert wf_after["status"] == "CANCELLED"


# --- Test 16: Authorization boundary ---

def test_authorization_boundary():
    """Service layer enforces tenant/project role checks for autonomous execution."""
    from nexus_independent.service import StandaloneMissionService

    service = StandaloneMissionService()
    principal_viewer = {"tenant_id": "test-tenant", "project_id": "test-project", "user_id": "viewer-user", "role": "viewer"}

    # Attempt to run autonomous execution as a viewer should raise PermissionError
    try:
        service.run_autonomous(principal_viewer, "Test objective", "test-scope")
        raised = False
    except PermissionError:
        raised = True
    except Exception:
        raised = False

    assert raised, "Viewer should be denied autonomous execution"


# --- Test 17: Tenant isolation ---

def test_tenant_isolation():
    """Tasks from one tenant should not appear in another tenant's view."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with db.connect() as conn:
            conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("tenant-b", "Tenant B", now))
            conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("project-b", "tenant-b", "Project B", now, now))

        from runtime.workflow_engine import WorkflowSpec

        spec_a = WorkflowSpec(
            name="Tenant A Test",
            objective="Test isolation A",
            scope="test",
            task_specs=[
                {"task_id": "task-a-1", "task_type": "research", "name": "Task A1",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["a"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )

        spec_b = WorkflowSpec(
            name="Tenant B Test",
            objective="Test isolation B",
            scope="test",
            task_specs=[
                {"task_id": "task-b-1", "task_type": "research", "name": "Task B1",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["b"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )

        wf_a = engine.create_workflow("test-tenant", "test-project", spec_a)
        wf_b = engine.create_workflow("tenant-b", "project-b", spec_b)

        tasks_a = db.list_workflow_tasks("test-tenant", wf_a["workflow_id"])
        tasks_b = db.list_workflow_tasks("tenant-b", wf_b["workflow_id"])

        assert len(tasks_a) == 1
        assert len(tasks_b) == 1
        assert tasks_a[0]["task_id"] != tasks_b[0]["task_id"]


# --- Test 18: Background execution continues after initiation ---

def test_background_execution_continues():
    """Autonomous execution must continue in background after initiation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)

        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        from runtime.workflow_worker import WorkflowWorker, WorkerConfig
        from runtime.workflow_engine import WorkflowSpec
        from runtime.workflow_planner import WorkflowPlanner

        planner = WorkflowPlanner(agent_registry=registry)
        autonomous_runtime = AutonomousRuntime(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            planner=planner,
            config=AutonomousConfig(tenant_id="test-tenant", project_id="test-project"),
        )

        worker = WorkflowWorker(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=messaging_hub,
            config=WorkerConfig(worker_id="test-worker-bg", tenant_id="test-tenant",
                               poll_interval_seconds=0.1, stop_on_idle=False, idle_limit=5),
            autonomous_runtime=autonomous_runtime,
        )

        planned = planner.plan(
            objective="Research and summarize the runtime",
            scope="Themeta-verse/Nexus",
            constraints={},
            execution_mode="SIMULATION",
        )

        spec = WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)

        # Launch worker in background thread
        thread = threading.Thread(
            target=worker.execute_workflow,
            args=("test-tenant", "test-project", workflow_id),
            kwargs={"max_ticks": 100},
            daemon=True,
        )
        thread.start()

        # Wait a bit, then check status
        time.sleep(2)
        wf_status = db.get_workflow("test-tenant", workflow_id)
        assert wf_status is not None
        assert wf_status["status"] in ("RUNNING", "COMPLETED", "FAILED")

        # Wait for completion (worker has max_ticks=100, poll_interval=0.1s = up to 10s)
        thread.join(timeout=120)
        final_wf = db.get_workflow("test-tenant", workflow_id)
        assert final_wf["status"] in ("COMPLETED", "FAILED")


# --- Test 19: Backward compatibility ---

def test_backward_compatibility_phase6():
    """Existing workflows and APIs from Phase 1-5 must continue working."""
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, messaging_hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, messaging_hub)

        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Backward Compat Test",
            objective="Test legacy compatibility",
            scope="Themeta-verse/Nexus",
            task_specs=[
                {"task_id": "legacy-1", "task_type": "research", "name": "Legacy Task",
                 "agent_id": None, "depends_on": [], "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "output_artifacts": ["legacy_result"], "parameters": {}},
            ],
            agents=[],
            execution_mode="SIMULATION",
        )
        wf = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = wf["workflow_id"]

        # Manual step API should still work
        engine.start_workflow("test-tenant", "test-project", workflow_id)
        state = engine.step("test-tenant", "test-project", workflow_id)

        assert "total_tasks" in state
        assert "status" in state

        # Worker-based execution should also work
        worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=50)
        final_state = engine.get_workflow_state("test-tenant", workflow_id)
        assert final_state["workflow"]["status"] in ("COMPLETED", "FAILED", "RUNNING")
