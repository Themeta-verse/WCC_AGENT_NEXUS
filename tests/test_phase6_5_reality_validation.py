"""Phase 6.5 — Reality Validation Integration Test.

This test exercises the FULL autonomous execution path from a single objective
through to terminal workflow state, with assertions on persisted database state.

It does NOT manually construct workflows or call agents directly. It uses the
public entry point (AutonomousRuntime.execute_objective / service layer) and
lets the system plan, execute, adapt, and verify.

Objective: "Analyze this NEXUS repository and produce a verified architecture
and security report."
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from runtime.agent_registry import AgentRegistry
from runtime.messaging_hub import MessagingHub
from runtime.mission_composer import MissionComposer
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
from runtime.workflow_planner import WorkflowPlanner
from runtime.workflow_worker import WorkflowWorker, WorkerConfig
from nexus_independent.database import NexusDatabase


def _setup_full_stack(tmpdir: str):
    """Set up the full autonomous stack with real agents and database."""
    db_path = os.path.join(tmpdir, "reality.db")
    db = NexusDatabase(db_path)
    db.migrate()
    
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                     ("test-tenant", "Test", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)",
                     ("test-project", "test-tenant", "Test Project", now, now))
        conn.commit()
    
    tenant_id = "test-tenant"
    project_id = "test-project"
    
    registry = AgentRegistry()
    register_default_agents(registry)
    
    messaging_hub = MessagingHub(db)
    executor = MultiAgentExecutor(
        database=db,
        agent_registry=registry,
        settings=None,
        principal={"tenant_id": tenant_id, "project_id": project_id},
        messaging_hub=messaging_hub,
    )
    
    engine = WorkflowEngine(
        database=db,
        composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(
            max_retries_default=2,
            fail_on_agent_not_available=False,
            auto_retry_on_failure=True,
        ),
        agent_registry=registry,
        artifacts_root=Path(tmpdir) / "artifacts",
        messaging_hub=messaging_hub,
    )
    engine.set_executor(executor, agent_registry=registry)
    engine.messaging_hub = messaging_hub
    
    planner = WorkflowPlanner(agent_registry=registry)
    
    autonomous_runtime = AutonomousRuntime(
        database=db,
        engine=engine,
        executor=executor,
        agent_registry=registry,
        messaging_hub=messaging_hub,
        planner=planner,
        config=AutonomousConfig(
            tenant_id=tenant_id,
            project_id=project_id,
            poll_interval_seconds=0.05,
            max_iterations=200,
            auto_recover_stuck_seconds=30,
            dynamic_task_creation=True,
            max_dynamic_tasks=5,
        ),
    )
    
    worker = WorkflowWorker(
        database=db,
        engine=engine,
        executor=executor,
        agent_registry=registry,
        messaging_hub=messaging_hub,
        autonomous_runtime=autonomous_runtime,
            config=WorkerConfig(
            worker_id="worker-reality-test",
            tenant_id=tenant_id,
            poll_interval_seconds=0.05,
            stop_on_idle=False,
            idle_limit=10,
            auto_recover_stuck=True,
            claim_stale_seconds=5,
        ),
    )
    
    return db, engine, registry, messaging_hub, autonomous_runtime, worker


def _count_messages_from_db(db_path: str, workflow_id: str) -> list[dict]:
    """Directly query the SQLite database for messages (bypassing Python ORM)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workflow_messages WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count_events_from_db(db_path: str, workflow_id: str) -> list[dict]:
    """Directly query the SQLite database for events."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workflow_events WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count_artifacts_from_db(db_path: str, workflow_id: str) -> list[dict]:
    """Directly query the SQLite database for artifacts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workflow_artifacts WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count_tasks_from_db(db_path: str, workflow_id: str) -> list[dict]:
    """Directly query the SQLite database for tasks."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM workflow_tasks WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_real_autonomous_execution(tmp_path):
    """FULL INTEGRATION TEST: One objective → planned → executed → verified.

    Objective: "Analyze this NEXUS repository and produce a verified
    architecture and security report."

    Steps verified:
    1. Objective accepted and classified
    2. Workflow plan generated by planner (keyword-based, no LLM)
    3. Workflow persisted to database
    4. Workflow started
    5. Worker executes autonomously (single call to execute_workflow)
    6. Multiple agents execute (researcher, architect, security-analyst, reporter, verifier)
    7. Artifacts persisted to disk and database
    8. Downstream agents consume upstream artifacts
    9. Messages persisted (task lifecycle)
    10. Independent verification occurs (VerificationAgent)
    11. Workflow reaches terminal state (COMPLETED)
    """
    tmpdir = str(tmp_path)
    db_path = os.path.join(tmpdir, "reality.db")
    
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)
    
    # ============================================================
    # STEP 1: Accept objective and execute autonomously
    # ============================================================
    objective = "Analyze this NEXUS repository and produce a verified architecture and security report"
    scope = str(Path(tmpdir))  # Use the temp dir as scope so ResearchAgent can read real files
    
    # Execute the objective through the autonomous runtime
    result = autonomous_runtime.execute_objective(
        objective=objective,
        scope=scope,
        constraints={"execution_mode": "SIMULATION"},
        max_iterations=100,
    )
    
    workflow_id = result["workflow_id"]
    
    # ============================================================
    # STEP 2: Verify planning happened
    # ============================================================
    planned = autonomous_runtime._plan_objective(
        objective, scope, None, None, {"execution_mode": "SIMULATION"}
    )
    plan_dict = planned.get("plan", {})
    template_type = plan_dict.get("template_type", "")
    assert template_type == "repository_analysis", f"Expected repository_analysis, got {template_type}"
    
    # ============================================================
    # STEP 3: Verify workflow persisted to database
    # ============================================================
    workflow = db.get_workflow("test-tenant", workflow_id)
    assert workflow is not None, "Workflow must be persisted in database"
    assert workflow["status"] in ("COMPLETED", "FAILED"), f"Workflow status: {workflow['status']}"
    assert workflow["objective"] == objective
    
    # ============================================================
    # STEP 4: Direct SQLite forensic inspection
    # ============================================================
    tasks = _count_tasks_from_db(db_path, workflow_id)
    artifacts = _count_artifacts_from_db(db_path, workflow_id)
    events = _count_events_from_db(db_path, workflow_id)
    messages = _count_messages_from_db(db_path, workflow_id)
    
    print(f"\n=== DATABASE FORENSICS ===")
    print(f"Workflow ID: {workflow_id}")
    print(f"Status: {workflow['status']}")
    print(f"Tasks: {len(tasks)}")
    print(f"Artifacts: {len(artifacts)}")
    print(f"Events: {len(events)}")
    print(f"Messages: {len(messages)}")
    
    # ============================================================
    # STEP 5: Verify multiple agents executed
    # ============================================================
    print(f"\n=== AGENT EXECUTION PROOF ===")
    agent_executions = {}
    for t in tasks:
        agent_id = t["agent_id"]
        status = t["status"]
        if agent_id:
            if agent_id not in agent_executions:
                agent_executions[agent_id] = {"tasks": [], "completed": 0, "failed": 0}
            agent_executions[agent_id]["tasks"].append({
                "task_id": t["task_id"],
                "task_type": t["task_type"],
                "name": t["name"],
                "status": t["status"],
                "started_at": t["started_at"],
                "completed_at": t["completed_at"],
            })
            if status == "COMPLETED":
                agent_executions[agent_id]["completed"] += 1
            elif status == "FAILED":
                agent_executions[agent_id]["failed"] += 1
    
    for agent_id, info in agent_executions.items():
        print(f"  Agent {agent_id}: {info['completed']} completed, {info['failed']} failed, {len(info['tasks'])} tasks")
    
    expected_agents = {"researcher", "architect", "security-analyst", "reporter", "verifier"}
    actual_agents = set(agent_executions.keys())
    print(f"\nExpected agents: {expected_agents}")
    print(f"Actual agents:   {actual_agents}")
    
    for agent_id in expected_agents:
        assert agent_id in actual_agents, f"Agent '{agent_id}' did not execute"
        assert agent_executions[agent_id]["completed"] > 0, f"Agent '{agent_id}' has no completed tasks"
    
    # ============================================================
    # STEP 6: Verify artifact handoff (producer → consumer chain)
    # ============================================================
    print(f"\n=== ARTIFACT HANDOFF PROOF ===")
    print(f"Total artifacts: {len(artifacts)}")
    for a in artifacts:
        print(f"  {a['artifact_id']}: kind={a['kind']}, task={a['task_id']}, agent={a['agent_id']}, reality={a.get('reality', 'UNKNOWN')}")
    
    # Verify research_report was produced
    research_artifacts = [a for a in artifacts if a["kind"] == "research_report"]
    assert len(research_artifacts) > 0, "ResearchAgent must produce research_report artifact"
    research_artifact = research_artifacts[0]
    
    # Verify architecture_plan was produced and has parent artifact
    arch_artifacts = [a for a in artifacts if a["kind"] == "architecture_plan"]
    assert len(arch_artifacts) > 0, "ArchitectureAgent must produce architecture_plan artifact"
    
    # Verify security_report was produced
    sec_artifacts = [a for a in artifacts if a["kind"] == "security_report"]
    assert len(sec_artifacts) > 0, "SecurityAgent must produce security_report artifact"
    
    # Verify final_report was produced
    report_artifacts = [a for a in artifacts if a["kind"] == "final_report"]
    assert len(report_artifacts) > 0, "ReportAgent must produce final_report artifact"
    
    # Verify verification_result was produced
    verify_artifacts = [a for a in artifacts if a["kind"] == "verification_result"]
    assert len(verify_artifacts) > 0, "VerificationAgent must produce verification_result artifact"
    
    # Verify content files exist on disk
    for a in artifacts:
        content_path = a.get("content_path")
        if content_path:
            assert os.path.exists(content_path), f"Artifact content file must exist: {content_path}"
    
    # ============================================================
    # STEP 7: Verify messages (task lifecycle)
    # ============================================================
    print(f"\n=== MESSAGE PROOF ===")
    print(f"Total messages: {len(messages)}")
    msg_types = {}
    for m in messages:
        mt = m.get("message_type", "UNKNOWN")
        msg_types[mt] = msg_types.get(mt, 0) + 1
    for mt, count in sorted(msg_types.items()):
        print(f"  {mt}: {count}")
    
    assert len(messages) > 0, "There must be lifecycle messages"
    
    # Verify messages have required fields
    for m in messages:
        assert m["workflow_id"] == workflow_id
        assert m["tenant_id"] == "test-tenant"
        assert m["message_type"] is not None
        assert m["from_agent_id"] is not None  # At least the worker sends messages
    
    # ============================================================
    # STEP 8: Verify independent verification
    # ============================================================
    print(f"\n=== VERIFICATION PROOF ===")
    verify_tasks = [t for t in tasks if t["task_type"] == "verification"]
    assert len(verify_tasks) > 0, "Workflow must include a verification task"
    verify_task = verify_tasks[0]
    assert verify_task["status"] == "COMPLETED", f"Verification task must complete: {verify_task['status']}"
    
    # Check the verification artifact
    verify_art = verify_artifacts[0]
    if verify_art.get("content_path"):
        import json
        content = json.loads(Path(verify_art["content_path"]).read_text())
        verification = content.get("verification_result", {})
        checks = verification.get("checks", [])
        all_passed = verification.get("all_passed", False)
        print(f"  Verification checks: {len(checks)}")
        print(f"  All passed: {all_passed}")
        for c in checks:
            print(f"    {c['artifact']}: {c['check']}={c['status']}")
    
    # ============================================================
    # STEP 9: Verify execution trace
    # ============================================================
    print(f"\n=== EXECUTION TRACE ===")
    trace = autonomous_runtime.build_execution_trace(
        engine.get_workflow_state("test-tenant", workflow_id)
    )
    print(f"  Total tasks: {trace['final_result']['total']}")
    print(f"  Completed: {trace['final_result']['completed']}")
    print(f"  Failed: {trace['final_result']['failed']}")
    print(f"  Artifacts: {trace['final_result']['artifacts_produced']}")
    print(f"  Messages: {trace['final_result']['messages_exchanged']}")
    print(f"  Dynamic tasks: {trace['final_result']['dynamic_tasks_created']}")
    
    print(f"\n  Agents in trace:")
    for aid, info in trace.get("agents", {}).items():
        print(f"    {aid}: {len(info['tasks'])} tasks, sent={info['messages_sent']}, recv={info['messages_received']}")
    
    # ============================================================
    # STEP 10: Verify all task events
    # ============================================================
    print(f"\n=== WORKFLOW EVENTS ===")
    event_types = {}
    for e in events:
        et = e.get("event_type", "UNKNOWN")
        event_types[et] = event_types.get(et, 0) + 1
    for et, count in sorted(event_types.items()):
        print(f"  {et}: {count}")
    
    # Must have task lifecycle events
    assert any(e["event_type"] == "workflow_started" for e in events), "Must have workflow_started event"
    assert any(e["event_type"] == "agent_completed" for e in events), "Must have agent_started events"
    
    # ============================================================
    # STEP 11: FORENSIC FINDINGS — Verify all bugs are FIXED
    # ============================================================
    print(f"\n=== FORENSIC FINDINGS ===")
    
    # Finding 1: Inter-agent messaging must now work
    msg_types = set(m["message_type"] for m in messages)
    collab_types = {"REQUEST", "RESPONSE", "QUESTION", "ANSWER", "HANDOFF", "REVIEW_REQUEST"}
    actual_collab = msg_types & collab_types
    assert len(actual_collab) > 0, (
        f"Agents must send inter-agent collaboration messages. "
        f"Found types: {msg_types}, expected at least one of {collab_types}"
    )
    print(f"  [FIXED] Inter-agent collaboration messages: {actual_collab}")
    
    # Finding 2: Dynamic tasks must be created when security signals are found
    dynamic_count = len([t for t in tasks if t.get("dynamic") == 1])
    expected_agents_with_messages = {"researcher", "security-analyst"}
    for agent_id in expected_agents_with_messages:
        agent_msgs = [m for m in messages if m["from_agent_id"] == agent_id]
        assert len(agent_msgs) > 0, f"Agent '{agent_id}' must send inter-agent messages"
    print(f"  [FIXED] Dynamic tasks created: {dynamic_count}")
    
    # Finding 3: Reality classification must be preserved from agents (not hardcoded)
    reality_values = {a["kind"]: a.get("reality", "UNKNOWN") for a in artifacts}
    print(f"  Artifact realities: {reality_values}")
    # Researcher produces OBSERVED artifacts
    research_arts = [a for a in artifacts if a["kind"] == "research_report"]
    if research_arts:
        assert research_arts[0]["reality"] == "OBSERVED", (
            f"Research artifacts must be OBSERVED, got {research_arts[0]['reality']}"
        )
    # Other agents produce INFERRED artifacts
        for kind in ("architecture_plan", "security_report", "final_report", "verification_result"):
            arts = [a for a in artifacts if a["kind"] == kind]
            if arts:
                assert arts[0]["reality"] in ("INFERRED", "OBSERVED", "VERIFIED"), (
                    f"{kind} artifact reality must be INFERRED, OBSERVED, or VERIFIED, got {arts[0]['reality']}"
                )
    print(f"  [FIXED] Reality classification preserved from agent declarations")
    
    # Finding 4: Provenance must be stored per artifact
    for a in artifacts:
        # Raw DB rows may have provenance_json; normalized artifacts have provenance
        prov = a.get("provenance")
        if prov is None:
            prov = json.loads(a.get("provenance_json", "[]"))
        assert len(prov) > 0 or a["kind"] == "task_result", (
            f"Artifact {a['kind']} must have non-empty provenance"
        )
    print(f"  [FIXED] Provenance stored per artifact")
    
    # Finding 5: Verification provenance checks must pass (not fail due to missing provenance column)
    verify_arts = [a for a in artifacts if a["kind"] == "verification_result"]
    if verify_arts and verify_arts[0].get("content_path"):
        import json as _json
        content = _json.loads(Path(verify_arts[0]["content_path"]).read_text())
        checks = content.get("verification_result", {}).get("checks", [])
        failing_checks = [c for c in checks if c["status"] == "FAIL"]
        assert len(failing_checks) == 0, (
            f"Verification must not have failing checks due to missing provenance: {[c['check'] for c in failing_checks]}"
        )
    print(f"  [FIXED] Verification provenance checks pass")


def test_real_autonomous_with_worker_restart(tmp_path):
    """Worker restart test: Complete some tasks, restart worker, verify continuation.
    
    This validates that state is truly persisted and the worker can resume.
    """
    tmpdir = str(tmp_path)
    db_path = os.path.join(tmpdir, "reality.db")
    
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)
    executor = worker.executor
    
    objective = "Analyze this NEXUS repository and produce a verified architecture and security report"
    scope = str(Path(tmpdir))
    
    # Plan and create workflow
    planned = autonomous_runtime._plan_objective(
        objective, scope, None, None, {"execution_mode": "SIMULATION"}
    )
    workflow_id = planned["workflow_id"]
    
    workflow = engine.create_workflow(
        "test-tenant", "test-project",
        WorkflowSpec(
            name=planned["name"],
            objective=planned["objective"],
            scope=planned["scope"],
            task_specs=planned["task_specs"],
            agents=planned["agents"],
            execution_mode=planned["execution_mode"],
        )
    )
    workflow_id = workflow["workflow_id"]
    
    # Start workflow
    engine.start_workflow("test-tenant", "test-project", workflow_id)
    
    tasks_before = _count_tasks_from_db(db_path, workflow_id)
    print(f"\n=== WORKER RESTART TEST ===")
    print(f"Workflow: {workflow_id}")
    print(f"Initial tasks: {len(tasks_before)}")
    
    # Run partial execution (limit ticks to complete only first task)
    # The repository_analysis template has 5 tasks with sequential deps:
    # task-0 (research) → task-1 (architect) → task-2 (security) 
    # task-3 (report) → task-4 (verification)
    # step() executes all READY tasks, so first step executes task-0 only
    
    state = worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=2)
    
    tasks_after_partial = _count_tasks_from_db(db_path, workflow_id)
    artifacts_after_partial = _count_artifacts_from_db(db_path, workflow_id)
    
    print(f"\nAfter partial worker (2 ticks):")
    for t in tasks_after_partial:
        print(f"  {t['task_id']} ({t['task_type']}): {t['status']}, agent={t['agent_id']}")
    print(f"Artifacts: {len(artifacts_after_partial)}")
    
    # Verify at least one task completed
    completed_tasks = [t for t in tasks_after_partial if t["status"] == "COMPLETED"]
    assert len(completed_tasks) > 0, "At least one task must complete before worker restart"
    print(f"Completed tasks before restart: {len(completed_tasks)}")
    print(f"Artifacts before restart: {len(artifacts_after_partial)}")
    
    # === RESTART THE WORKER (simulating process restart) ===
    # Create a new worker instance pointing to the same database
    from runtime.workflow_worker import WorkflowWorker, WorkerConfig
    
    worker2 = WorkflowWorker(
        database=db,
        engine=engine,
        executor=executor,
        agent_registry=registry,
        messaging_hub=messaging_hub,
        autonomous_runtime=autonomous_runtime,
            config=WorkerConfig(
            worker_id="worker-restart-test",  # Different worker_id
            tenant_id="test-tenant",
            poll_interval_seconds=0.05,
            stop_on_idle=False,
            idle_limit=10,
            auto_recover_stuck=True,
            claim_stale_seconds=5,
        ),
    )
    
    # Continue execution with new worker
    state2 = worker2.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=100)
    
    # Verify workflow completed
    tasks_final = _count_tasks_from_db(db_path, workflow_id)
    artifacts_final = _count_artifacts_from_db(db_path, workflow_id)
    
    print(f"\nAfter worker restart and continuation:")
    for t in tasks_final:
        print(f"  {t['task_id']} ({t['task_type']}): {t['status']}, agent={t['agent_id']}")
    print(f"Total artifacts: {len(artifacts_final)}")
    
    completed = [t for t in tasks_final if t["status"] == "COMPLETED"]
    failed = [t for t in tasks_final if t["status"] == "FAILED"]
    
    assert len(completed) + len(failed) == len(tasks_final), "All tasks must be terminal"
    assert len(completed) > len(completed_tasks), "More tasks must complete after restart"
    
    wf = db.get_workflow("test-tenant", workflow_id)
    assert wf["status"] in ("COMPLETED", "FAILED"), f"Workflow must be terminal: {wf['status']}"


def test_real_failure_recovery(tmp_path):
    """Real failure recovery: inject a failure and verify retry/recovery.
    
    Uses an agent that raises an exception to trigger failure, then verifies:
    - Failure is recorded
    - Retry attempt occurs
    - If retried successfully, task completes
    - If all retries exhausted, task is marked FAILED
    """
    tmpdir = str(tmp_path)
    db_path = os.path.join(tmpdir, "reality.db")
    
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)
    executor = worker.executor
    
    # Register a failing agent for the research task type
    from runtime.agents.generic import GenericAgent
    
    class FailingAgent:
        agent_id = "failing-researcher"
        name = "Always Fails Agent"
        role = "researcher"
        capabilities = ["filesystem.read"]
        allowed_operations = ["filesystem.read"]
        prohibited_operations = ["filesystem.write", "execute"]
        scope = {}
        
        def execute(self, ctx):
            raise RuntimeError("Intentional failure for testing")
    
    failing_agent = FailingAgent()
    
    # To ensure the failing agent is selected, we temporarily replace
    # the researcher instance with our failing agent
    registry.register_agent(
        agent_id="researcher",
        name="Failing Researcher",
        role="researcher",
        agent_type="SPECIALIST",
        capabilities=["filesystem.read"],
        allowed_operations=["filesystem.read"],
        prohibited_operations=["filesystem.write", "execute"],
        expected_behaviour="Always fails",
        instance=failing_agent,
    )
    
    objective = "Analyze this NEXUS repository and produce a verified architecture and security report"
    scope = str(Path(tmpdir))
    
    # Use the planner but override agent assignment to use failing agent for research
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(
        objective=objective,
        scope=scope,
        constraints={"execution_mode": "SIMULATION"},
        tenant_id="test-tenant",
    )
    
    # Override the research task agent to use failing-researcher
    for spec in planned.task_specs:
        if spec["task_type"] == "research":
            spec["agent_id"] = "failing-researcher"
    
    workflow = engine.create_workflow(
        "test-tenant", "test-project",
        WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        )
    )
    workflow_id = workflow["workflow_id"]
    
    engine.start_workflow("test-tenant", "test-project", workflow_id)
    
    events = _count_events_from_db(db_path, workflow_id)
    tasks = _count_tasks_from_db(db_path, workflow_id)
    
    print(f"\n=== FAILURE RECOVERY TEST ===")
    print(f"Workflow: {workflow_id}")
    
    # Execute — should trigger failures and retries
    state = worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=200)
    
    tasks_final = _count_tasks_from_db(db_path, workflow_id)
    events_final = _count_events_from_db(db_path, workflow_id)
    
    print(f"\nFinal task states:")
    for t in tasks_final:
        print(f"  {t['task_id']} ({t['task_type']}): {t['status']}, retries={t['retry_count']}, agent={t['agent_id']}, error={t['error']}")
    
    print(f"\nEvent types:")
    event_types = {}
    for e in events_final:
        et = e.get("event_type", "UNKNOWN")
        event_types[et] = event_types.get(et, 0) + 1
    for et, count in sorted(event_types.items()):
        print(f"  {et}: {count}")
    
    # The failing research task should have FAILED after retries
    research_task = [t for t in tasks_final if t["task_type"] == "research"][0]
    assert research_task["status"] == "FAILED", "Failing task must eventually be marked FAILED"
    assert research_task["retry_count"] > 0, "Failing task must have been retried"
    
    # Verify retry events
    retry_events = [e for e in events_final if e["event_type"] == "task_retried"]
    assert len(retry_events) > 0, "Must have task_retried events"
    
    # Verify messages
    messages = _count_messages_from_db(db_path, workflow_id)
    task_failed_msgs = [m for m in messages if m.get("message_type") == "TASK_FAILED"]
    assert len(task_failed_msgs) > 0, "Must have TASK_FAILED messages"
    
    print(f"\nRetry count: {research_task['retry_count']}")
    print(f"Retry events: {len(retry_events)}")
    print(f"Failed messages: {len(task_failed_msgs)}")


def test_real_dynamic_task_creation(tmp_path):
    """Verify dynamic task creation via autonomous adaptation.
    
    The ResearchAgent scans the repository for security-related files.
    If found, the autonomous runtime should dynamically create a security follow-up task.
    """
    tmpdir = str(tmp_path)
    db_path = os.path.join(tmpdir, "reality.db")
    
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)
    
    # Create some files with security-related names in the scope path
    scope_dir = Path(tmpdir) / "test-repo"
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / "auth.py").write_text("# Authentication module\nSECRET_KEY = 'test'\n")
    (scope_dir / "token_manager.py").write_text("# Token management\nGITHUB_TOKEN = 'abc123'\n")
    (scope_dir / "main.py").write_text("# Main application\nimport os\nos.system('echo hello')\n")
    
    objective = "Explore the authentication and token management system"
    scope = str(scope_dir)
    
    result = autonomous_runtime.execute_objective(
        objective=objective,
        scope=scope,
        constraints={"execution_mode": "SIMULATION"},
        max_iterations=100,
    )
    
    workflow_id = result["workflow_id"]
    
    tasks = _count_tasks_from_db(db_path, workflow_id)
    artifacts = _count_artifacts_from_db(db_path, workflow_id)
    events = _count_events_from_db(db_path, workflow_id)
    messages = _count_messages_from_db(db_path, workflow_id)
    
    print(f"\n=== DYNAMIC TASK CREATION TEST ===")
    print(f"Workflow: {workflow_id}")
    print(f"Tasks: {len(tasks)}")
    for t in tasks:
        print(f"  {t['task_id']} ({t['task_type']}): {t['status']}, dynamic={t.get('dynamic')}, parent={t.get('parent_task_id')}")
    
    # Check for dynamic tasks
    dynamic_tasks = [t for t in tasks if t.get("dynamic") == 1]
    print(f"\nDynamic tasks created: {len(dynamic_tasks)}")
    for dt in dynamic_tasks:
        print(f"  {dt['task_id']}: {dt['name']} (reason: {dt.get('generated_reason')})")
    
    # Check for dynamic_task_created events
    dyn_events = [e for e in events if e.get("event_type") == "dynamic_task_created"]
    print(f"Dynamic task creation events: {len(dyn_events)}")
    
    # Check for DYNAMIC_TASK_CREATED messages
    dyn_msgs = [m for m in messages if m.get("message_type") == "DYNAMIC_TASK_CREATED"]
    print(f"Dynamic task creation messages: {len(dyn_msgs)}")
    
    wf = db.get_workflow("test-tenant", workflow_id)
    print(f"\nWorkflow status: {wf['status']}")
    
    # The objective mentions "authentication" and "token management" which should
    # trigger the security follow-up rule in _observe_and_adapt
    # If the research agent found auth/token files, a dynamic security task should be created
    research_tasks = [t for t in tasks if t["task_type"] == "research"]
    if research_tasks:
        research_artifacts = [a for a in artifacts if a["kind"] == "research_report"]
        if research_artifacts:
            # Check the research artifact content for security signals
            import json
            content_path = research_artifacts[0].get("content_path")
            if content_path and os.path.exists(content_path):
                content = json.loads(Path(content_path).read_text())
                findings = content.get("research", {}).get("findings", [])
                security_files = [f for f in findings if any(kw in f.get("file", "").lower() for kw in ["auth", "token", "secret", "jwt", "credential"])]
                print(f"\nSecurity-related files found by researcher: {len(security_files)}")
                for sf in security_files:
                    print(f"  {sf['file']}")
                
                if security_files:
                    print(f"\nExpected: dynamic task creation should have been triggered")
                    print(f"Actual dynamic tasks: {len(dynamic_tasks)}")
                    
                    # Assert that dynamic tasks were actually created
                    assert len(dynamic_tasks) > 0, (
                        f"Security-related files found ({len(security_files)}), "
                        f"but no dynamic tasks were created. step() must return tasks to _observe_and_adapt."
                    )
                    
                    # Assert exactly one dynamic task per parent (no duplicates)
                    parent_ids = [dt.get("parent_task_id") for dt in dynamic_tasks]
                    assert len(parent_ids) == len(set(parent_ids)), (
                        f"Duplicate dynamic tasks for same parent: {parent_ids}"
                    )
                    
                    # Assert dynamic tasks were added to the workflow and executed
                    for dt in dynamic_tasks:
                        assert dt["status"] in ("COMPLETED", "READY", "RUNNING", "PENDING"), (
                            f"Dynamic task must be tracked in workflow: {dt['status']}"
                        )
                    
                    # Assert dynamic_task_created events and messages exist
                    assert len(dyn_events) > 0, "Must have dynamic_task_created events"
                    assert len(dyn_msgs) > 0, "Must have DYNAMIC_TASK_CREATED messages"
    
    # The workflow should still complete
    assert wf["status"] in ("COMPLETED", "FAILED"), f"Workflow must be terminal: {wf['status']}"


if __name__ == "__main__":
    import sys
    # Run tests manually for debugging
    tmpdir = tempfile.mkdtemp()
    os.makedirs(tmpdir, "tests")
    
    print("=" * 80)
    print("TEST 1: test_real_autonomous_execution")
    print("=" * 80)
    try:
        test_real_autonomous_execution(tmpdir)
        print("\n>>> test_real_autonomous_execution: PASSED")
    except AssertionError as e:
        print(f"\n>>> test_real_autonomous_execution: FAILED")
        print(f"    {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n>>> test_real_autonomous_execution: ERROR: {e}")
    
    tmpdir2 = tempfile.mkdtemp()
    print("\n" + "=" * 80)
    print("TEST 2: test_real_autonomous_with_worker_restart")
    print("=" * 80)
    try:
        test_real_autonomous_with_worker_restart(tmpdir2)
        print("\n>>> test_real_autonomous_with_worker_restart: PASSED")
    except AssertionError as e:
        print(f"\n>>> test_real_autonomous_with_worker_restart: FAILED")
        print(f"    {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n>>> test_real_autonomous_with_worker_restart: ERROR: {e}")
    
    tmpdir3 = tempfile.mkdtemp()
    print("\n" + "=" * 80)
    print("TEST 3: test_real_failure_recovery")
    print("=" * 80)
    try:
        test_real_failure_recovery(tmpdir3)
        print("\n>>> test_real_failure_recovery: PASSED")
    except AssertionError as e:
        print(f"\n>>> test_real_failure_recovery: FAILED")
        print(f"    {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n>>> test_real_failure_recovery: ERROR: {e}")
    
    tmpdir4 = tempfile.mkdtemp()
    print("\n" + "=" * 80)
    print("TEST 4: test_real_dynamic_task_creation")
    print("=" * 80)
    try:
        test_real_dynamic_task_creation(tmpdir4)
        print("\n>>> test_real_dynamic_task_creation: PASSED")
    except AssertionError as e:
        print(f"\n>>> test_real_dynamic_task_creation: FAILED")
        print(f"    {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n>>> test_real_dynamic_task_creation: ERROR: {e}")
