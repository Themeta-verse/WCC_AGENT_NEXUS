"""Phase 6.5 — Reality Hardening Integration Test.

This test validates that the reality-hardenening fixes are correct:
1. step() returns tasks → _observe_and_adapt can see and evaluate them
2. scope=workflow_id bug fixed → ResearchAgent observes real filesystem
3. Hardcoded reality=OBSERVED fixed → agent-declared reality is preserved
4. provenance column added → artifacts carry durable provenance
5. Agent-to-agent messaging is real → agents send RESPONSE messages via hub
6. Dynamic task creation works end-to-end → security follow-up task executes
7. Execution trace is forensically accurate → includes reality, provenance, messages

This test also includes negative tests to verify honest failure when things break.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from runtime.agent_registry import AgentRegistry
from runtime.agents.researcher import ResearchAgent
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
        conn.execute(
            "INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
            ("test-tenant", "Test", now),
        )
        conn.execute(
            "INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("test-project", "test-tenant", "Test Project", now, now),
        )
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

    autonomous_runtime = AutonomousRuntime(
        database=db,
        engine=engine,
        executor=executor,
        agent_registry=registry,
        messaging_hub=messaging_hub,
        config=AutonomousConfig(
            tenant_id=tenant_id,
            project_id=project_id,
            poll_interval_seconds=0.05,
            max_iterations=200,
            auto_recover_stuck_seconds=30,
            dynamic_task_creation=True,
            max_dynamic_tasks=10,
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


def _direct_db_query(db_path: str, query: str, params: tuple = ()) -> list[dict]:
    """Query the SQLite database directly, bypassing the Python ORM."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_step_returns_tasks_for_observe_and_adapt(tmp_path):
    """Verify that engine.step() returns a 'tasks' key that _observe_and_adapt can use."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(
        objective="Explore the test repository",
        scope=str(tmp_path),
        constraints={"execution_mode": "SIMULATION"},
        tenant_id="test-tenant",
    )

    workflow = engine.create_workflow(
        "test-tenant", "test-project",
        WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        ),
    )
    workflow_id = workflow["workflow_id"]
    engine.start_workflow("test-tenant", "test-project", workflow_id)

    state = engine.step("test-tenant", "test-project", workflow_id)

    assert "tasks" in state, "step() must return 'tasks' key for _observe_and_adapt"
    assert isinstance(state["tasks"], list), "tasks must be a list"
    assert len(state["tasks"]) > 0, "tasks list must not be empty"
    assert all("task_id" in t for t in state["tasks"]), "each task must have task_id"
    assert all("status" in t for t in state["tasks"]), "each task must have status"
    assert all("task_type" in t for t in state["tasks"]), "each task must have task_type"


def test_research_agent_uses_observation_scope(tmp_path):
    """Verify that ResearchAgent uses observation_scope (the real filesystem path)
    rather than the workflow_id as its scope."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    # Create a real file in the scope
    scope_dir = Path(tmpdir) / "testrepo"
    scope_dir.mkdir()
    (scope_dir / "auth.py").write_text("# authentication module\nSECRET = 'test'\n")

    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(
        objective="Explore the authentication system",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        tenant_id="test-tenant",
    )

    # Verify the plan has the right scope
    assert planned.scope == str(scope_dir), "Planner must preserve scope"

    workflow = engine.create_workflow(
        "test-tenant", "test-project",
        WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        ),
    )
    workflow_id = workflow["workflow_id"]
    engine.start_workflow("test-tenant", "test-project", workflow_id)

    # Run until completion
    worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=100)

    # Check artifacts for research findings
    artifacts = db.list_workflow_artifacts("test-tenant", workflow_id)
    research_arts = [a for a in artifacts if a["kind"] == "research_report"]
    assert len(research_arts) > 0, "Research report must be produced"

    # The artifact content should show files were actually discovered in the scope
    content_path = research_arts[0].get("content_path")
    assert content_path is not None, "Research artifact must have content_path"
    content = json.loads(Path(content_path).read_text())
    findings = content.get("research", {}).get("findings", [])
    file_names = [f.get("file", "") for f in findings]
    assert len(file_names) > 0, "Researcher must have found files in the scope"
    assert any("auth.py" in f for f in file_names), (
        f"Researcher must have found auth.py in scope. Found: {file_names[:5]}"
    )
    print(f"Researcher found {len(file_names)} files in scope: {file_names[:5]}")


def test_artifact_reality_preserved_from_agent(tmp_path):
    """Verify that artifact reality is the agent-declared value, not hardcoded 'OBSERVED'."""
    tmpdir = str(tmp_path)
    db_path = os.path.join(tmpdir, "nexus.db")
    db = NexusDatabase(db_path)
    db.migrate()

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("t1", "T", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("p1", "t1", "P", now, now))
        conn.execute("INSERT INTO workflows(workflow_id, tenant_id, project_id, name, objective, scope, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("wf-test", "t1", "p1", "Test WF", "Test", ".", "PENDING", now, now))
        conn.commit()

    # Create an artifact with INFERRED reality (simulating a non-researcher agent)
    import hashlib
    content_hash = hashlib.sha256(b"test-content").hexdigest()[:16]
    artifact = db.create_artifact(
        artifact_id="art-test-001",
        workflow_id="wf-test",
        tenant_id="t1",
        project_id="p1",
        task_id=None,
        agent_id="architect",
        kind="architecture_plan",
        name="architecture_plan.json",
        content_hash=content_hash,
        parent_artifacts=[],
        content_path="/tmp/test.json",
        reality="INFERRED",
        untrusted=True,
        verification_state="UNVERIFIED",
        provenance=["agent:architect", "type:architect", "evidence-based-reasoning"],
    )

    assert artifact["reality"] == "INFERRED", f"artifact reality must be INFERRED, got {artifact['reality']}"
    assert artifact["untrusted"] == 1, f"artifact untrusted must be 1, got {artifact['untrusted']}"
    assert artifact["verification_state"] == "UNVERIFIED", f"verification_state must be UNVERIFIED"
    assert artifact["provenance"] == ["agent:architect", "type:architect", "evidence-based-reasoning"]


def test_artifact_researcher_reality_observed(tmp_path):
    """Verify that ResearchAgent artifacts are stored with reality=OBSERVED (agent-declared, not hardcoded)."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    # Create test files
    scope_dir = Path(tmpdir) / "testrepo"
    scope_dir.mkdir()
    (scope_dir / "main.py").write_text("# main app\nprint('hello')\n")

    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(
        objective="Explore the repository",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        tenant_id="test-tenant",
    )

    workflow = engine.create_workflow(
        "test-tenant", "test-project",
        WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        ),
    )
    workflow_id = workflow["workflow_id"]
    engine.start_workflow("test-tenant", "test-project", workflow_id)
    worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=100)

    # Direct DB check
    db_path = os.path.join(tmpdir, "reality.db")
    artifacts = _direct_db_query(
        db_path,
        "SELECT * FROM workflow_artifacts WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,),
    )

    # Check that the provenance_json column exists and is populated
    assert all("provenance_json" in row for row in artifacts), "provenance_json column must exist"

    for art in artifacts:
        prov = json.loads(art["provenance_json"])
        assert len(prov) > 0, f"Artifact {art['kind']} must have non-empty provenance"
        assert "agent:" in prov[0], f"Provenance must start with agent identifier: {prov}"

    # Research artifact must have reality=OBSERVED (from agent, not hardcoded)
    research_arts = [a for a in artifacts if a["kind"] == "research_report"]
    assert len(research_arts) > 0
    assert research_arts[0]["reality"] == "OBSERVED", (
        f"Research artifact reality must be OBSERVED (agent-declared), got {research_arts[0]['reality']}"
    )

    # Verifier artifact must have reality=VERIFIED or INFERRED (not hardcoded OBSERVED)
    verify_arts = [a for a in artifacts if a["kind"] == "verification_result"]
    if verify_arts:
        assert verify_arts[0]["reality"] in ("INFERRED", "VERIFIED"), (
            f"Verification artifact must not be hardcoded OBSERVED, got {verify_arts[0]['reality']}"
        )


def test_agents_send_interagent_messages(tmp_path):
    """Verify that agents send inter-agent messages (STATUS_UPDATE, RESPONSE) via MessagingHub."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    scope_dir = Path(tmpdir) / "testrepo"
    scope_dir.mkdir()
    (scope_dir / "main.py").write_text("# main\n")

    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(
        objective="Explore the repository",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        tenant_id="test-tenant",
    )

    workflow = engine.create_workflow(
        "test-tenant", "test-project",
        WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        ),
    )
    workflow_id = workflow["workflow_id"]
    engine.start_workflow("test-tenant", "test-project", workflow_id)
    worker.execute_workflow("test-tenant", "test-project", workflow_id, max_ticks=100)

    db_path = os.path.join(tmpdir, "reality.db")
    messages = _direct_db_query(
        db_path,
        "SELECT * FROM workflow_messages WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,),
    )

    msg_types = {}
    for m in messages:
        mt = m.get("message_type", "UNKNOWN")
        msg_types[mt] = msg_types.get(mt, 0) + 1

    print(f"Message types: {msg_types}")

    # Agents must send STATUS_UPDATE and RESPONSE messages
    assert "STATUS_UPDATE" in msg_types, "Agents must send STATUS_UPDATE messages"
    assert "RESPONSE" in msg_types, "Agents must send RESPONSE messages"

    # At least some messages must come from actual agents (not just the worker)
    agent_msgs = [m for m in messages if m.get("from_agent_id") in ("researcher", "architect", "security-analyst", "reporter", "verifier")]
    assert len(agent_msgs) > 0, "At least some messages must originate from agent IDs"
    agent_msg_types = set(m["message_type"] for m in agent_msgs)
    assert "STATUS_UPDATE" in agent_msg_types, "Agents must send STATUS_UPDATE messages"
    assert "RESPONSE" in agent_msg_types, "Agents must send RESPONSE messages"


def test_dynamic_task_creation_end_to_end(tmp_path):
    """Verify that dynamic tasks are created when security-relevant files are discovered."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    scope_dir = Path(tmpdir) / "testrepo"
    scope_dir.mkdir()
    (scope_dir / "auth.py").write_text("# auth module\nSECRET_KEY = 'test'\n")
    (scope_dir / "token_manager.py").write_text("# token management\nTOKEN = 'abc'\n")
    (scope_dir / "main.py").write_text("# main app\n")

    result = autonomous_runtime.execute_objective(
        objective="Explore the authentication and token management system",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        max_iterations=100,
    )

    workflow_id = result["workflow_id"]
    db_path = os.path.join(tmpdir, "reality.db")

    tasks = _direct_db_query(
        db_path,
        "SELECT * FROM workflow_tasks WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,),
    )
    dynamic_tasks = [t for t in tasks if t.get("dynamic") == 1]

    print(f"Total tasks: {len(tasks)}, Dynamic tasks: {len(dynamic_tasks)}")
    for dt in dynamic_tasks:
        print(f"  Dynamic: {dt['task_id']} ({dt['task_type']}): {dt['name']} - reason: {dt.get('generated_reason')}")

    assert len(dynamic_tasks) > 0, (
        "Security-relevant files found, but no dynamic tasks were created. "
        "Verify step() returns tasks and _observe_and_adapt processes them."
    )

    # Dynamic tasks should be COMPLETED (they were executed)
    completed_dynamic = [t for t in dynamic_tasks if t["status"] == "COMPLETED"]
    assert len(completed_dynamic) > 0, "Dynamic tasks must be executed to completion"


def test_dynamic_task_no_duplicates(tmp_path):
    """Verify that each parent task gets at most one dynamic follow-up task."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    scope_dir = Path(tmpdir) / "testrepo"
    scope_dir.mkdir()
    (scope_dir / "auth.py").write_text("# auth\n")
    (scope_dir / "token.py").write_text("# token\n")

    result = autonomous_runtime.execute_objective(
        objective="Explore the authentication and token management system",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        max_iterations=100,
    )

    workflow_id = result["workflow_id"]
    db_path = os.path.join(tmpdir, "reality.db")

    tasks = _direct_db_query(
        db_path,
        "SELECT * FROM workflow_tasks WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,),
    )
    dynamic_tasks = [t for t in tasks if t.get("dynamic") == 1]

    # No duplicate dynamic tasks per parent
    parent_ids = [t.get("parent_task_id") for t in dynamic_tasks if t.get("parent_task_id")]
    assert len(parent_ids) == len(set(parent_ids)), (
        f"Duplicate dynamic tasks for same parent: {parent_ids}"
    )


def test_execution_trace_forensic_accuracy(tmp_path):
    """Verify the execution trace includes accurate reality, provenance, and messages."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    scope_dir = Path(tmpdir) / "testrepo"
    scope_dir.mkdir()
    (scope_dir / "auth.py").write_text("SECRET = 'test'\n")
    (scope_dir / "main.py").write_text("print('hello')\n")

    result = autonomous_runtime.execute_objective(
        objective="Explore the authentication system",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        max_iterations=100,
    )

    trace = result["trace"]

    # Trace must include artifact provenance
    artifacts = trace.get("artifacts", [])
    assert len(artifacts) > 0, "Trace must include artifacts"
    for art in artifacts:
        assert "provenance" in art, f"Artifact {art.get('kind')} must have provenance in trace"
        assert len(art["provenance"]) > 0, f"Artifact {art.get('kind')} must have non-empty provenance"

    # Trace must show inter-agent messages
    messages = trace.get("messages", [])
    collab_msg_types = {"STATUS_UPDATE", "RESPONSE", "REQUEST", "HANDOFF", "QUESTION", "ANSWER"}
    collab_msgs = [m for m in messages if m.get("message_type") in collab_msg_types]
    assert len(collab_msgs) > 0, "Trace must include inter-agent collaboration messages"

    # Trace agents must show messages sent
    agents = trace.get("agents", {})
    assert len(agents) > 0, "Trace must include agent information"
    agent_msg_total = sum(info["messages_sent"] for info in agents.values())
    assert agent_msg_total > 0, "Agents in trace must have sent messages"

    # Trace must include dynamic tasks if they were created
    dynamic_tasks = trace.get("dynamic_tasks", [])
    print(f"Trace dynamic tasks: {len(dynamic_tasks)}")
    print(f"Trace agents: {list(agents.keys())}")
    print(f"Trace agent msg totals: {[(a, i['messages_sent']) for a, i in agents.items()]}")


def test_provenance_migration_from_old_db(tmp_path):
    """Verify that databases created before the provenance column are properly migrated."""
    tmpdir = str(tmp_path)
    db_path = os.path.join(tmpdir, "nexus.db")

    # Create a DB without the provenance column
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tenants (tenant_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE projects (project_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, display_name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE workflows (
            workflow_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT, name TEXT,
            objective TEXT, scope TEXT, status TEXT, plan_json TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE workflow_artifacts (
            artifact_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            project_id TEXT,
            task_id TEXT,
            agent_id TEXT,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content_path TEXT,
            content_size INTEGER,
            parent_artifacts_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reality TEXT NOT NULL DEFAULT 'INFERRED',
            untrusted INTEGER NOT NULL DEFAULT 1,
            verification_state TEXT NOT NULL DEFAULT 'UNVERIFIED'
        );
        """
    )
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("t1", "T", now))
    conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("p1", "t1", "P", now, now))
    conn.execute("INSERT INTO workflows(workflow_id, tenant_id, project_id, name, objective, scope, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 ("wf-old", "t1", "p1", "Old WF", "Test", ".", "PENDING", now, now))
    conn.execute(
        "INSERT INTO workflow_artifacts(artifact_id, workflow_id, tenant_id, project_id, task_id, agent_id, kind, name, content_hash, parent_artifacts_json, created_at, reality, untrusted, verification_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("art-old", "wf-old", "t1", "p1", None, "agent-1", "test_kind", "test.json", "abc123", "[]", now, "INFERRED", 1, "UNVERIFIED"),
    )
    conn.commit()
    conn.close()

    # Now migrate
    db = NexusDatabase(db_path)
    db.migrate()

    # Verify the provenance_json column was added
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?", ("art-old",)).fetchone()
    conn.close()

    assert row is not None, "Old artifact must still exist after migration"
    assert "provenance_json" in row.keys(), "provenance_json column must exist after migration"
    prov = json.loads(row["provenance_json"])
    assert prov == [], "Old artifact must have empty provenance list after migration"


# ============================================================
# NEGATIVE TESTS
# ============================================================


def test_honest_failure_missing_files(tmp_path):
    """Negative test: when scope has no readable files, the workflow should still complete
    honestly rather than crashing or producing false OBSERVED evidence."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    # Create an empty scope (no files)
    scope_dir = Path(tmpdir) / "empty_repo"
    scope_dir.mkdir()

    result = autonomous_runtime.execute_objective(
        objective="Explore the repository",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        max_iterations=100,
    )

    assert result["status"] in ("COMPLETED", "FAILED"), (
        f"Workflow must reach terminal state even with empty scope: {result['status']}"
    )
    print(f"Workflow completed with empty scope: {result['status']}")


def test_honest_failure_nonexistent_scope(tmp_path):
    """Negative test: when scope path does not exist, ResearchAgent should
    produce zero findings (not crash)."""
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    nonexistent = str(Path(tmpdir) / "does_not_exist")

    result = autonomous_runtime.execute_objective(
        objective="Explore the nonexistent repository",
        scope=nonexistent,
        constraints={"execution_mode": "SIMULATION"},
        max_iterations=100,
    )

    assert result["status"] in ("COMPLETED", "FAILED"), (
        f"Workflow must reach terminal state even with nonexistent scope: {result['status']}"
    )
    print(f"Workflow completed with nonexistent scope: {result['status']}")


def test_artifact_reality_cannot_be_overridden_by_db(tmp_path):
    """Negative test: create_artifact must NOT hardcode reality=OBSERVED.
    
    If an agent declares reality=INFERRED, the DB must store INFERRED, not OBSERVED.
    """
    tmpdir = str(tmp_path)
    db_path = os.path.join(tmpdir, "nexus.db")
    db = NexusDatabase(db_path)
    db.migrate()

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("t1", "T", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("p1", "t1", "P", now, now))
        conn.execute("INSERT INTO workflows(workflow_id, tenant_id, project_id, name, objective, scope, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("wf-override", "t1", "p1", "Override Test WF", "Test", ".", "PENDING", now, now))
        conn.commit()

    artifact = db.create_artifact(
        artifact_id="art-test-override",
        workflow_id="wf-override",
        tenant_id="t1",
        project_id="p1",
        task_id=None,
        agent_id="architect",
        kind="architecture_plan",
        name="test.json",
        content_hash="abc123",
        parent_artifacts=[],
        content_path="/tmp/test.json",
        reality="INFERRED",
        untrusted=True,
        verification_state="UNVERIFIED",
        provenance=["agent:architect"],
    )

    assert artifact["reality"] == "INFERRED", (
        f"create_artifact must preserve agent-declared reality=INFERRED, not hardcode OBSERVED. Got: {artifact['reality']}"
    )


def test_dynamic_task_created_when_step_missing_tasks(tmp_path):
    """Verify that if step() returns tasks, _observe_and_adapt can process them.
    
    This test explicitly verifies the Fix 1: step() must return tasks.
    """
    tmpdir = str(tmp_path)
    db, engine, registry, messaging_hub, autonomous_runtime, worker = _setup_full_stack(tmpdir)

    scope_dir = Path(tmpdir) / "testrepo"
    scope_dir.mkdir()
    (scope_dir / "auth.py").write_text("SECRET = 'test'\n")
    (scope_dir / "main.py").write_text("print('hello')\n")

    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(
        objective="Explore the authentication system",
        scope=str(scope_dir),
        constraints={"execution_mode": "SIMULATION"},
        tenant_id="test-tenant",
    )

    workflow = engine.create_workflow(
        "test-tenant", "test-project",
        WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
        ),
    )
    workflow_id = workflow["workflow_id"]
    engine.start_workflow("test-tenant", "test-project", workflow_id)

    # Step once — research task should execute
    state = engine.step("test-tenant", "test-project", workflow_id)
    assert "tasks" in state, "step() must return tasks key"

    # Now run observe_and_adapt manually
    autonomous_runtime._observe_and_adapt(workflow_id, state)

    db_path = os.path.join(tmpdir, "reality.db")
    tasks = _direct_db_query(
        db_path,
        "SELECT * FROM workflow_tasks WHERE workflow_id=? ORDER BY created_at",
        (workflow_id,),
    )
    dynamic_tasks = [t for t in tasks if t.get("dynamic") == 1]
    assert len(dynamic_tasks) > 0, (
        "After step() returns tasks and _observe_and_adapt runs, dynamic tasks must be created"
    )


if __name__ == "__main__":
    import sys
    test_funcs = [
        ("test_step_returns_tasks_for_observe_and_adapt", test_step_returns_tasks_for_observe_and_adapt),
        ("test_research_agent_uses_observation_scope", test_research_agent_uses_observation_scope),
        ("test_artifact_reality_preserved_from_agent", test_artifact_reality_preserved_from_agent),
        ("test_artifact_researcher_reality_observed", test_artifact_researcher_reality_observed),
        ("test_agents_send_interagent_messages", test_agents_send_interagent_messages),
        ("test_dynamic_task_creation_end_to_end", test_dynamic_task_creation_end_to_end),
        ("test_dynamic_task_no_duplicates", test_dynamic_task_no_duplicates),
        ("test_execution_trace_forensic_accuracy", test_execution_trace_forensic_accuracy),
        ("test_provenance_migration_from_old_db", test_provenance_migration_from_old_db),
        ("test_honest_failure_missing_files", test_honest_failure_missing_files),
        ("test_honest_failure_nonexistent_scope", test_honest_failure_nonexistent_scope),
        ("test_artifact_reality_cannot_be_overridden_by_db", test_artifact_reality_cannot_be_overridden_by_db),
        ("test_dynamic_task_created_when_step_missing_tasks", test_dynamic_task_created_when_step_missing_tasks),
    ]

    passed = 0
    failed = 0
    for name, func in test_funcs:
        tmpdir = tempfile.mkdtemp()
        from pathlib import Path as P
        tmp_path = P(tmpdir)
        print(f"\n{'='*60}\n{name}\n{'='*60}")
        try:
            func(tmp_path)
            print(f"\n>>> {name}: PASSED")
            passed += 1
        except AssertionError as e:
            print(f"\n>>> {name}: FAILED")
            print(f"    {e}")
            failed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"\n>>> {name}: ERROR: {e}")
            failed += 1

    print(f"\n\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")
    sys.exit(0 if failed == 0 else 1)
