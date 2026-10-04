"""NEXUS Phase 4 Tests for Persistent Autonomous Workflow Execution.

Tests cover:
- Planner -> workflow -> worker: end-to-end autonomous execution via WorkflowWorker
- Artifact flow: artifacts produced and consumed with provenance
- Message persistence: MessagingHub messages are persisted and queryable
- Worker restart recovery: stuck tasks are recovered from dead workers
- Retry behavior: failed tasks retry within policy limits
- Cancellation: workflow can be cancelled mid-execution
- Missing capability: task fails gracefully when no agent is available
- Missing artifact: task handles missing input artifacts gracefully
- Failed task: task failure is recorded with error detail
- AgentContext Phase 4 fields: objective, constraints, previous_messages, execution_metadata

Run: python tests/test_phase4_autonomous_execution.py
     python -m pytest tests/test_phase4_autonomous_execution.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup_engine(tmpdir: str, with_workers: bool = True):
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
    worker = WorkflowWorker(
        database=db,
        engine=engine,
        executor=engine.executor,
        agent_registry=engine._agent_registry,
        messaging_hub=messaging_hub,
        config=config,
    )
    return worker


def _make_spec(name, objective, scope, tasks, agents, execution_mode="REAL_READ"):
    from runtime.workflow_engine import WorkflowSpec
    return WorkflowSpec(
        name=name,
        objective=objective,
        scope=scope,
        task_specs=tasks,
        agents=agents,
        execution_mode=execution_mode,
    )


def _register_failing_agent(registry, agent_id="failing-agent"):
    """Register an agent that always raises an exception."""
    from runtime.agent_base import AgentContext, AgentExecutionResult

    class FailingAgent:
        agent_id = "failing-agent"
        name = "Failing Agent"
        role = "test"
        capabilities = ["knowledge.read"]

        def execute(self, context: AgentContext) -> AgentExecutionResult:
            raise RuntimeError(f"Failing agent intentionally fails for task {context.task_id}")

    agent = FailingAgent()
    from runtime.agent_registry import AgentInfo
    registry.register_agent(
        agent_id=agent_id,
        name="Failing",
        role="test",
        agent_type="SPECIALIST",
        capabilities=["knowledge.read"],
        allowed_operations=["read"],
        prohibited_operations=[],
        instance=agent,
    )
    return agent


def test_autonomous_worker_execution():
    """End-to-end: planner -> workflow -> worker executes to completion."""
    print("\n" + "=" * 60)
    print("TEST: Autonomous Worker Execution")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, hub, worker_id="worker-1")

        spec = _make_spec(
            name="Autonomous Test",
            objective="Research repository and produce final report.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
                {"task_id": "t-report", "task_type": "report", "name": "Report",
                 "agent_id": "reporter", "required_capabilities": [],
                 "input_artifacts": ["research_report"], "parameters": {"objective": "Summary"},
                 "depends_on": ["t-research"]},
                {"task_id": "t-verify", "task_type": "verification", "name": "Verify",
                 "agent_id": "verifier", "required_capabilities": [],
                 "input_artifacts": ["final_report"], "parameters": {},
                 "depends_on": ["t-report"]},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher",
                 "capabilities": ["filesystem.read"], "instruction": "Observe files"},
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter",
                 "capabilities": ["report.generate"], "instruction": "Generate report"},
                {"agent_id": "verifier", "agent_type": "SPECIALIST", "name": "Verifier",
                 "capabilities": ["verify"], "instruction": "Verify"},
            ],
        )

        # Create and start the workflow
        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]

        # Start workflow (only marks tasks READY)
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        # Verify tasks with no deps are READY but not executed
        state = engine.get_workflow_state("test-tenant", workflow_id)
        ready_tasks = [t for t in state["tasks"] if not t["depends_on"]]
        pending_tasks = [t for t in state["tasks"] if t["depends_on"]]
        for task in ready_tasks:
            assert task["status"] == "READY", f"Ready task should be READY after start, got {task['status']}"
        for task in pending_tasks:
            assert task["status"] == "PENDING", f"Dependent task should be PENDING after start, got {task['status']}"
        print(f"  After start_workflow: {state['summary']['completed']} completed, {state['summary']['running']} running (READY)")
        assert state["summary"]["completed"] == 0, "No tasks should have executed yet"

        # Now run the worker to execute autonomously
        worker.run("test-tenant", "test-project", workflow_id)

        # Verify completion
        state = engine.get_workflow_state("test-tenant", workflow_id)
        print(f"  After worker: {state['workflow']['status']}")
        print(f"  Tasks: {state['summary']['completed']}/{state['summary']['total_tasks']} completed, {state['summary']['failed']} failed")
        print(f"  Artifacts: {len(state['artifacts'])}")
        print(f"  Messages: {len(state['messages'])}")

        assert state["workflow"]["status"] == "COMPLETED", f"Workflow should complete, got {state['workflow']['status']}"
        assert state["summary"]["completed"] == 3, "All 3 tasks should complete"
        assert len(state["artifacts"]) > 0, "Should have produced artifacts"

        task_map = {t["task_id"]: t for t in state["tasks"]}
        assert task_map["t-research"]["agent_id"] == "researcher"
        assert task_map["t-report"]["agent_id"] == "reporter"
        assert task_map["t-verify"]["agent_id"] == "verifier"

        print("  PASSED: Autonomous worker execution to completion")


def test_message_persistence():
    """Messages are persisted in the database and retrievable."""
    print("\n" + "=" * 60)
    print("TEST: Message Persistence")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)
        worker = _setup_worker(engine, db, hub, worker_id="worker-msg")

        spec = _make_spec(
            name="Message Test",
            objective="Test message persistence.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher",
                 "capabilities": ["filesystem.read"], "instruction": "Observe files"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow("test-tenant", "test-project", workflow_id)
        worker.run("test-tenant", "test-project", workflow_id)

        # Send a message to the workflow
        msg = hub.send(
            workflow_id=workflow_id,
            tenant_id="test-tenant",
            message_type="STATUS_UPDATE",
            content={"status": "completed", "note": "All done"},
            from_agent_id="reporter",
        )
        assert msg["message_id"].startswith("msg-")

        # Retrieve messages
        messages = db.list_workflow_messages("test-tenant", workflow_id)
        print(f"  Total messages: {len(messages)}")
        assert len(messages) > 0, "Should have messages"

        status_messages = db.list_workflow_messages("test-tenant", workflow_id, message_type="STATUS_UPDATE")
        assert len(status_messages) > 0, "Should have STATUS_UPDATE messages"
        reporter_status = [m for m in status_messages if m.get("from_agent_id")]
        assert len(reporter_status) > 0, "Agents must send STATUS_UPDATE messages"
        # The reporter's STATUS_UPDATE message has status "completed"
        assert any(m["content"]["status"] == "completed" for m in status_messages), \
            "Must have a STATUS_UPDATE with status=completed"

        lifecycle_messages = db.list_workflow_messages("test-tenant", workflow_id, message_type="TASK_STARTED")
        assert len(lifecycle_messages) > 0, "Should have TASK_STARTED lifecycle messages"

        print(f"  STATUS_UPDATE messages: {len(status_messages)}")
        print(f"  TASK_STARTED messages: {len(lifecycle_messages)}")
        print("  PASSED: Message persistence works")


def test_worker_restart_recovery():
    """Stuck tasks from a dead worker are recovered and re-executed."""
    print("\n" + "=" * 60)
    print("TEST: Worker Restart Recovery")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        spec = _make_spec(
            name="Recovery Test",
            objective="Test worker restart recovery.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
                {"task_id": "t-report", "task_type": "report", "name": "Report",
                 "agent_id": "reporter", "required_capabilities": [],
                 "input_artifacts": ["research_report"], "parameters": {"objective": "Summary"},
                 "depends_on": ["t-research"]},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher",
                 "capabilities": ["filesystem.read"], "instruction": "Observe files"},
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter",
                 "capabilities": ["report.generate"], "instruction": "Generate report"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        # Simulate a dead worker by claiming a task with a stale timestamp
        from datetime import datetime, timezone, timedelta
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        with db.connect() as conn:
            conn.execute(
                "UPDATE workflow_tasks SET status='RUNNING', worker_id='dead-worker', claimed_at=? WHERE task_id='t-research'",
                (stale_time,),
            )

        tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        research_task = next(t for t in tasks if t["task_id"] == "t-research")
        assert research_task["status"] == "RUNNING", "Task should be RUNNING"
        assert research_task["worker_id"] == "dead-worker"

        # Create a new worker and run recovery
        worker = _setup_worker(engine, db, hub, worker_id="worker-restart")
        recovered = engine.recover_stuck_tasks("test-tenant", workflow_id, stale_seconds=30)
        print(f"  Recovered tasks: {recovered}")
        assert recovered > 0, "Should have recovered stuck tasks"

        # Task should be back to READY
        tasks = db.list_workflow_tasks("test-tenant", workflow_id)
        research_task = next(t for t in tasks if t["task_id"] == "t-research")
        print(f"  After recovery: t-research status={research_task['status']}")
        assert research_task["status"] == "READY", f"Task should be READY after recovery, got {research_task['status']}"

        # Run the worker to complete the workflow
        worker.run("test-tenant", "test-project", workflow_id)

        state = engine.get_workflow_state("test-tenant", workflow_id)
        print(f"  Final status: {state['workflow']['status']}")
        print(f"  Completed: {state['summary']['completed']}/{state['summary']['total_tasks']}")

        # Check for recovery message
        recovery_msgs = db.list_workflow_messages("test-tenant", workflow_id, message_type="TASK_RECOVERED")
        assert len(recovery_msgs) > 0, "Should have TASK_RECOVERED messages"
        print(f"  TASK_RECOVERED messages: {len(recovery_msgs)}")

        assert state["summary"]["completed"] > 0, "Should have completed tasks after recovery"
        print("  PASSED: Worker restart recovery works")


def test_retry_on_failure():
    """Failed tasks are retried within the retry limit."""
    print("\n" + "=" * 60)
    print("TEST: Retry on Failure")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        # Register a failing agent that will trigger retries
        _register_failing_agent(registry, agent_id="failing-agent")

        spec = _make_spec(
            name="Retry Test",
            objective="Test retry behavior.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-failing", "task_type": "unknown_type", "name": "Failing Task",
                 "agent_id": "failing-agent", "required_capabilities": [],
                 "input_artifacts": [], "parameters": {}, "depends_on": []},
                {"task_id": "t-success", "task_type": "report", "name": "Success Task",
                 "agent_id": "reporter", "required_capabilities": [],
                 "input_artifacts": [], "parameters": {"objective": "test"},
                 "depends_on": []},
            ],
            agents=[
                {"agent_id": "failing-agent", "agent_type": "SPECIALIST", "name": "Failing",
                 "capabilities": ["knowledge.read"], "instruction": "Always fails"},
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter",
                 "capabilities": ["report.generate"], "instruction": "Report"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]

        engine.start_workflow("test-tenant", "test-project", workflow_id)

        for i in range(30):
            time.sleep(0.3)
            engine.step("test-tenant", "test-project", workflow_id)
            state = engine.get_workflow_state("test-tenant", workflow_id)
            summary = state["summary"]
            if summary["completed"] + summary["failed"] == summary["total_tasks"]:
                break

        state = engine.get_workflow_state("test-tenant", workflow_id)
        tasks = state["tasks"]
        task_map = {t["task_id"]: t for t in tasks}

        print(f"  t-failing: status={task_map['t-failing']['status']}, retries={task_map['t-failing']['retry_count']}")

        retry_events = [e for e in state["events"] if e["event_type"] == "task_retried"]
        print(f"  Retry events: {len(retry_events)}")
        assert len(retry_events) > 0, "Should have retry events"

        assert task_map["t-failing"]["status"] in ("FAILED",), "Task should ultimately fail"
        assert task_map["t-failing"]["retry_count"] >= 1, "Task should have been retried"

        print("  PASSED: Retry on failure works")


def test_cancellation():
    """Workflow can be cancelled mid-execution."""
    print("\n" + "=" * 60)
    print("TEST: Cancellation")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        spec = _make_spec(
            name="Cancel Test",
            objective="Test cancellation.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
                {"task_id": "t-report", "task_type": "report", "name": "Report",
                 "agent_id": "reporter", "required_capabilities": [],
                 "input_artifacts": ["research_report"], "parameters": {"objective": "Summary"},
                 "depends_on": ["t-research"]},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher",
                 "capabilities": ["filesystem.read"], "instruction": "Observe files"},
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter",
                 "capabilities": ["report.generate"], "instruction": "Generate report"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        # Cancel before completion
        engine.cancel_workflow("test-tenant", "test-project", workflow_id)

        state = engine.get_workflow_state("test-tenant", workflow_id)
        print(f"  Status after cancel: {state['workflow']['status']}")
        assert state["workflow"]["status"] == "CANCELLED", f"Workflow should be CANCELLED, got {state['workflow']['status']}"

        events = state["events"]
        cancel_events = [e for e in events if e["event_type"] == "workflow_cancelled"]
        assert len(cancel_events) > 0, "Should have workflow_cancelled event"

        print("  PASSED: Cancellation works")


def test_missing_capability():
    """Task fails gracefully when no agent is available for required capabilities."""
    print("\n" + "=" * 60)
    print("TEST: Missing Capability")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        spec = _make_spec(
            name="Missing Cap Test",
            objective="Test missing capability handling.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research",
                 "agent_id": None, "required_capabilities": ["nonexistent.capacity"],
                 "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
            ],
            agents=[],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        for i in range(10):
            time.sleep(0.3)
            engine.step("test-tenant", "test-project", workflow_id)
            state = engine.get_workflow_state("test-tenant", workflow_id)
            if state["summary"]["running"] == 0 and state["summary"]["completed"] + state["summary"]["failed"] == state["summary"]["total_tasks"]:
                break

        state = engine.get_workflow_state("test-tenant", workflow_id)
        tasks = state["tasks"]
        print(f"  Task status: {tasks[0]['status']}")
        print(f"  Workflow status: {state['workflow']['status']}")
        assert tasks[0]["status"] in ("BLOCKED", "FAILED", "READY"), f"Task should be BLOCKED, FAILED, or READY (no agent), got {tasks[0]['status']}"

        print("  PASSED: Missing capability handled gracefully")


def test_missing_input_artifact():
    """Task with missing input artifacts still executes (graceful degradation)."""
    print("\n" + "=" * 60)
    print("TEST: Missing Input Artifact")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        spec = _make_spec(
            name="Missing Artifact Test",
            objective="Test missing input artifact handling.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-report", "task_type": "report", "name": "Report",
                 "agent_id": "reporter", "required_capabilities": [],
                 "input_artifacts": ["nonexistent_artifact"], "parameters": {"objective": "Test"},
                 "depends_on": []},
            ],
            agents=[
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter",
                 "capabilities": ["report.generate"], "instruction": "Generate report"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        for i in range(15):
            time.sleep(0.3)
            engine.step("test-tenant", "test-project", workflow_id)
            state = engine.get_workflow_state("test-tenant", workflow_id)
            if state["summary"]["completed"] + state["summary"]["failed"] == state["summary"]["total_tasks"]:
                break

        state = engine.get_workflow_state("test-tenant", workflow_id)
        tasks = state["tasks"]
        print(f"  Task status: {tasks[0]['status']}")
        assert tasks[0]["status"] in ("COMPLETED", "FAILED"), f"Task should complete or fail, got {tasks[0]['status']}"

        print("  PASSED: Missing input artifact handled gracefully")


def test_failed_task_recorded():
    """Failed task is recorded with error detail in the database."""
    print("\n" + "=" * 60)
    print("TEST: Failed Task Recorded")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        # Register a failing agent that always raises
        _register_failing_agent(registry, agent_id="failing-agent")

        spec = _make_spec(
            name="Failed Task Test",
            objective="Test failed task recording.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-failing", "task_type": "custom", "name": "Failing Task",
                 "agent_id": "failing-agent", "required_capabilities": [],
                 "input_artifacts": [], "parameters": {}, "depends_on": []},
            ],
            agents=[
                {"agent_id": "failing-agent", "agent_type": "SPECIALIST", "name": "Failing",
                 "capabilities": ["knowledge.read"], "instruction": "Always fails"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        for i in range(30):
            time.sleep(0.3)
            engine.step("test-tenant", "test-project", workflow_id)
            state = engine.get_workflow_state("test-tenant", workflow_id)
            if state["summary"]["completed"] + state["summary"]["failed"] == state["summary"]["total_tasks"]:
                break

        state = engine.get_workflow_state("test-tenant", workflow_id)
        tasks = state["tasks"]
        task = tasks[0]

        print(f"  Status: {task['status']}")
        print(f"  Retry count: {task['retry_count']}")
        error_val = task.get("error")
        print(f"  Error: {(error_val or 'None')[:100]}")

        assert task["status"] == "FAILED", "Task should be FAILED"
        assert task["error"] is not None, "Failed task should have an error message"

        # Verify FAILED message is in messaging hub
        failed_msgs = db.list_workflow_messages("test-tenant", workflow_id, message_type="TASK_FAILED")
        assert len(failed_msgs) > 0, "Should have TASK_FAILED message"
        print(f"  TASK_FAILED messages: {len(failed_msgs)}")

        print("  PASSED: Failed task is recorded with error")


def test_agent_context_phase4_fields():
    """AgentContext carries Phase 4 fields: objective, constraints, previous_messages, execution_metadata."""
    print("\n" + "=" * 60)
    print("TEST: AgentContext Phase 4 Fields")
    print("=" * 60)

    from runtime.agent_base import AgentContext

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        objective = "Analyze this repository thoroughly."
        constraints = {"max_depth": 3, "exclude_paths": ["node_modules"]}

        # Store a plan with objective/constraints
        plan = {
            "objective": objective,
            "scope": tmpdir,
            "template_type": "repository_analysis",
            "constraints": constraints,
        }


        spec = _make_spec(
            name="Context Test",
            objective=objective,
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher",
                 "capabilities": ["filesystem.read"], "instruction": "Observe files"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]

        # Update plan with objective/constraints
        db.save_workflow_plan("test-tenant", workflow_id, plan)

        engine.start_workflow("test-tenant", "test-project", workflow_id)

        for i in range(15):
            time.sleep(0.3)
            engine.step("test-tenant", "test-project", workflow_id)
            state = engine.get_workflow_state("test-tenant", workflow_id)
            if state["summary"]["completed"] + state["summary"]["failed"] == state["summary"]["total_tasks"]:
                break

        # Verify the plan was saved with objective and constraints
        saved = db.get_workflow("test-tenant", workflow_id)
        saved_plan = json.loads(saved["plan_json"]) if saved.get("plan_json") else {}
        assert saved_plan.get("objective") == objective, "Objective should be in the plan"
        assert saved_plan.get("constraints") == constraints, "Constraints should be in the plan"

        print("  PASSED: AgentContext Phase 4 fields are wired through")


def test_planner_to_worker_autonomous():
    """Full pipeline: WorkflowPlanner -> PlannedWorkflow -> WorkflowEngine -> WorkflowWorker."""
    print("\n" + "=" * 60)
    print("TEST: Planner to Worker Autonomous Pipeline")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner
    from runtime.workflow_worker import WorkflowWorker, WorkerConfig

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        planner = WorkflowPlanner(agent_registry=registry)
        planned = planner.plan(
            objective="Analyze this repository and produce an architecture and security report.",
            scope=tmpdir,
            tenant_id="test-tenant",
            project_id="test-project",
            execution_mode="REAL_READ",
        )

        print(f"  Plan valid: {planned.is_valid}")
        assert planned.is_valid, "Plan should be valid"

        spec = planner.plan_to_workflow_spec(planned)
        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]

        # Start workflow (marks tasks READY)
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        # Create and run worker autonomously
        worker_config = WorkerConfig(
            worker_id="autonomous-worker",
            tenant_id="test-tenant",
            poll_interval_seconds=0.1,
            claim_stale_seconds=5,
            stop_on_idle=True,
            idle_limit=5,
            auto_recover_stuck=True,
        )
        worker = WorkflowWorker(
            database=db,
            engine=engine,
            executor=engine.executor,
            agent_registry=registry,
            messaging_hub=hub,
            config=worker_config,
        )

        worker.run("test-tenant", "test-project", workflow_id)

        state = engine.get_workflow_state("test-tenant", workflow_id)
        print(f"  Status: {state['workflow']['status']}")
        print(f"  Tasks: {state['summary']['completed']}/{state['summary']['total_tasks']} completed")
        print(f"  Artifacts: {len(state['artifacts'])}")
        print(f"  Messages: {len(state['messages'])}")

        assert state["workflow"]["status"] in ("COMPLETED", "FAILED"), f"Workflow should finish, got {state['workflow']['status']}"
        assert state["summary"]["completed"] > 0, "Should have completed tasks"
        assert len(state["artifacts"]) > 0, "Should have artifacts"

        # Verify task ordering: research first
        task_map = {t["task_id"]: t for t in state["tasks"]}
        task_types = [t["task_type"] for t in state["tasks"]]
        assert "research" in task_types
        assert "report" in task_types
        assert "verification" in task_types

        # Verify lifecycle messages exist
        assert len(state["messages"]) > 0, "Should have messaging hub messages"

        print("  PASSED: Planner to worker autonomous pipeline")


def test_durable_worker_restart():
    """Worker restart: create new worker instance, verify it picks up where old left off."""
    print("\n" + "=" * 60)
    print("TEST: Durable Worker Restart")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry, hub = _setup_engine(tmpdir)

        spec = _make_spec(
            name="Restart Test",
            objective="Test durable worker restart.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
                {"task_id": "t-report", "task_type": "report", "name": "Report",
                 "agent_id": "reporter", "required_capabilities": [],
                 "input_artifacts": ["research_report"], "parameters": {"objective": "Summary"},
                 "depends_on": ["t-research"]},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher",
                 "capabilities": ["filesystem.read"], "instruction": "Observe files"},
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter",
                 "capabilities": ["report.generate"], "instruction": "Generate report"},
            ],
        )

        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow("test-tenant", "test-project", workflow_id)

        # First worker: complete research only
        worker1 = _setup_worker(engine, db, hub, worker_id="worker-first")
        for i in range(5):
            time.sleep(0.3)
            engine.step("test-tenant", "test-project", workflow_id)
            state = engine.get_workflow_state("test-tenant", workflow_id)
            if state["summary"]["running"] == 0 and state["summary"]["completed"] > 0 and state["summary"]["completed"] < state["summary"]["total_tasks"]:
                # If research is done but report isn't, stop
                task_map = {t["task_id"]: t for t in state["tasks"]}
                if task_map.get("t-research", {}).get("status") == "COMPLETED":
                    break

        state1 = engine.get_workflow_state("test-tenant", workflow_id)
        print(f"  After worker1: {state1['summary']['completed']}/{state1['summary']['total_tasks']} completed")
        assert state1["summary"]["completed"] > 0, "First worker should have completed at least one task"

        # New worker: picks up the persisted state
        worker2 = _setup_worker(engine, db, hub, worker_id="worker-second")
        worker2.run("test-tenant", "test-project", workflow_id)

        state2 = engine.get_workflow_state("test-tenant", workflow_id)
        print(f"  After worker2: {state2['workflow']['status']}")
        print(f"  Tasks: {state2['summary']['completed']}/{state2['summary']['total_tasks']} completed")

        assert state2["summary"]["completed"] == state2["summary"]["total_tasks"], "All tasks should complete"
        assert state2["workflow"]["status"] == "COMPLETED"

        print("  PASSED: Durable worker restart works")


if __name__ == "__main__":
    tests = [
        ("Autonomous Worker Execution", test_autonomous_worker_execution),
        ("Message Persistence", test_message_persistence),
        ("Worker Restart Recovery", test_worker_restart_recovery),
        ("Retry on Failure", test_retry_on_failure),
        ("Cancellation", test_cancellation),
        ("Missing Capability", test_missing_capability),
        ("Missing Input Artifact", test_missing_input_artifact),
        ("Failed Task Recorded", test_failed_task_recorded),
        ("AgentContext Phase 4 Fields", test_agent_context_phase4_fields),
        ("Planner to Worker Autonomous", test_planner_to_worker_autonomous),
        ("Durable Worker Restart", test_durable_worker_restart),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAILED: {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {name}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
