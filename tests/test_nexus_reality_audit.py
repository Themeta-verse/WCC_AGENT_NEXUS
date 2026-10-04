"""NEXUS Reality Audit — strong end-to-end proof tests (added after forensic baseline).

Each test asserts persisted backend state with meaningful failure conditions.
No `assert x >= 0` placeholders. Covers the three reality-breaking bugs fixed:
  1. WorkflowWorker claim path dropped reality/provenance on persisted artifacts.
  2. WorkflowWorker claim path passed scope=workflow_id (no observation_scope).
  3. AutonomousRuntime read state["summary"] while engine.step() returns flat keys.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from runtime.agent_registry import AgentRegistry
from runtime.agent_base import AgentExecutionResult
from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
from runtime.messaging_hub import MessagingHub
from runtime.mission_composer import MissionComposer
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
from runtime.workflow_planner import WorkflowPlanner
from runtime.workflow_worker import WorkflowWorker, WorkerConfig
from nexus_independent.database import NexusDatabase


def _stack(tmp_path, **cfg):
    db = NexusDatabase(str(tmp_path / "audit.db"))
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as c:
        c.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("t1", "T", now))
        c.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("p1", "t1", "P", now, now))
        c.commit()
    registry = AgentRegistry()
    register_default_agents(registry)
    hub = MessagingHub(db)
    executor = MultiAgentExecutor(database=db, agent_registry=registry, settings=None,
                                  principal={"tenant_id": "t1", "project_id": "p1"}, messaging_hub=hub)
    engine = WorkflowEngine(database=db, composer=MissionComposer(),
                            policy=WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=False, auto_retry_on_failure=True),
                            agent_registry=registry, artifacts_root=tmp_path / "artifacts", messaging_hub=hub)
    engine.set_executor(executor, agent_registry=registry)
    engine.messaging_hub = hub
    rt = AutonomousRuntime(database=db, engine=engine, executor=executor, agent_registry=registry,
                           messaging_hub=hub, config=AutonomousConfig(tenant_id="t1", project_id="p1", poll_interval_seconds=0.02,
                                                                     max_iterations=200, dynamic_task_creation=True, max_dynamic_tasks=5))
    worker = WorkflowWorker(database=db, engine=engine, executor=executor, agent_registry=registry,
                            messaging_hub=hub, autonomous_runtime=rt,
                            config=WorkerConfig(worker_id="w-audit", tenant_id="t1", poll_interval_seconds=0.02,
                                                stop_on_idle=False, auto_recover_stuck=True, claim_stale_seconds=5))
    return db, engine, registry, hub, rt, worker


def _scope_with_files(tmp_path):
    scope = tmp_path / "scope"
    scope.mkdir(exist_ok=True)
    (scope / "auth_service.py").write_text("import jwt\nSECRET='x'\n")
    (scope / "app.py").write_text("print('hello')\n")
    return str(scope)


def test_claim_path_preserves_reality_and_provenance(tmp_path):
    """Worker claim path must persist agent-declared reality + provenance (was: defaults)."""
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _scope_with_files(tmp_path)
    spec = WorkflowSpec(name="claim-reality", objective="o", scope=scope,
                        task_specs=[{"task_id": "c-0", "name": "research", "task_type": "research",
                                     "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                                     "depends_on": [], "input_artifacts": [], "output_artifacts": ["research_report"], "parameters": {}}],
                        agents=[], execution_mode="REAL_READ")
    wf = engine.create_workflow("t1", "p1", spec)
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    claimed = worker.execute_task_claim("t1", "p1", wid, "c-0")
    assert claimed is True
    arts = db.list_workflow_artifacts("t1", wid)
    assert len(arts) == 1
    art = arts[0]
    assert art["reality"] == "OBSERVED"
    assert art["untrusted"] in (0, False)
    assert art["provenance"] is not None and len(art["provenance"]) >= 2
    assert any("researcher" in p for p in art["provenance"])


def test_claim_path_uses_real_observation_scope(tmp_path):
    """Worker claim path must observe the workflow scope, not the workflow_id."""
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _scope_with_files(tmp_path)
    spec = WorkflowSpec(name="claim-scope", objective="o", scope=scope,
                        task_specs=[{"task_id": "s-0", "name": "research", "task_type": "research",
                                     "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                                     "depends_on": [], "input_artifacts": [], "output_artifacts": ["research_report"], "parameters": {}}],
                        agents=[], execution_mode="REAL_READ")
    wf = engine.create_workflow("t1", "p1", spec)
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    assert worker.execute_task_claim("t1", "p1", wid, "s-0") is True
    arts = db.list_workflow_artifacts("t1", wid)
    assert len(arts) == 1
    content = json.loads(Path(arts[0]["content_path"]).read_text())
    findings = content["research"]["findings"]
    assert len(findings) >= 2
    observed = [f["file"] for f in findings]
    assert any("auth_service.py" in f for f in observed)
    # hashes must match real bytes on disk
    for f in findings:
        real = hashlib.sha256(Path(f["file"]).read_bytes()).hexdigest()
        assert real == f["content_hash"]


def test_autonomous_objective_completes_on_flat_step_state(tmp_path):
    """execute_objective must terminate on engine.step() flat keys (no 'summary' key)."""
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _scope_with_files(tmp_path)
    t0 = time.time()
    out = rt.execute_objective("Analyze this repository codebase", scope, max_iterations=60)
    dt = time.time() - t0
    assert out["status"] == "COMPLETED"
    assert out["summary"]["completed"] >= 3
    assert out["summary"]["failed"] == 0
    assert dt < 55  # must break early, not burn all 60 iterations x sleep


def test_failure_retry_then_recovery(tmp_path):
    """Controlled failure must persist FAILED-attempt state, retry, then COMPLETE."""
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _scope_with_files(tmp_path)

    class FailOnce:
        agent_id = "failer"
        name = "F"
        role = "failer"
        capabilities = ["x"]
        allowed_operations = ["read"]
        prohibited_operations = []
        calls = 0

        def execute(self, ctx):
            FailOnce.calls += 1
            if FailOnce.calls == 1:
                raise RuntimeError("controlled failure")
            return AgentExecutionResult(task_id=ctx.task_id, agent_id=ctx.agent_id, status="COMPLETED",
                                        reality="INFERRED", untrusted=True, result={}, artifacts=[], provenance=["agent:failer"])

    registry.register_agent(agent_id="failer", name="F", role="failer", agent_type="SPECIALIST",
                            capabilities=["x"], allowed_operations=["read"], prohibited_operations=[],
                            instance=FailOnce(), tenant_id="t1")
    spec = WorkflowSpec(name="fail-test", objective="o", scope=scope,
                        task_specs=[{"task_id": "f-0", "name": "flaky", "task_type": "generic",
                                     "agent_id": "failer", "required_capabilities": ["x"],
                                     "depends_on": [], "input_artifacts": [], "output_artifacts": [], "parameters": {}}],
                        agents=[], execution_mode="REAL_READ")
    wf = engine.create_workflow("t1", "p1", spec)
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    engine.step("t1", "p1", wid)
    mid = db.get_workflow_task("t1", "f-0")
    assert mid["retry_count"] >= 1
    events = db.list_workflow_events("t1", wid, limit=50)
    assert any(e["event_type"] == "task_retried" for e in events)
    engine.step("t1", "p1", wid)
    final = db.get_workflow_task("t1", "f-0")
    assert final["status"] == "COMPLETED"


def test_worker_restart_continues_same_database(tmp_path):
    """A new worker against the same DB must discover and finish the workflow."""
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _scope_with_files(tmp_path)
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective="Analyze this repository", scope=scope, tenant_id="t1",
                           execution_mode="REAL_READ", project_id="p1")
    wf = engine.create_workflow("t1", "p1", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    engine.step("t1", "p1", wid)  # partial progress, then "restart"
    partial = [t for t in db.list_workflow_tasks("t1", wid) if t["status"] == "COMPLETED"]
    assert len(partial) >= 1
    # New worker + engine instances, same DB file
    db2 = NexusDatabase(str(tmp_path / "audit.db"))
    hub2 = MessagingHub(db2)
    engine2 = WorkflowEngine(database=db2, composer=MissionComposer(),
                             policy=WorkflowExecutionPolicy(fail_on_agent_not_available=False),
                             agent_registry=registry, artifacts_root=tmp_path / "artifacts", messaging_hub=hub2)
    executor2 = MultiAgentExecutor(database=db2, agent_registry=registry, settings=None,
                                   principal={"tenant_id": "t1", "project_id": "p1"}, messaging_hub=hub2)
    engine2.set_executor(executor2, agent_registry=registry)
    engine2.messaging_hub = hub2
    worker2 = WorkflowWorker(database=db2, engine=engine2, executor=executor2, agent_registry=registry,
                             messaging_hub=hub2, config=WorkerConfig(worker_id="w-restart", tenant_id="t1", poll_interval_seconds=0.02))
    state = worker2.execute_workflow("t1", "p1", wid, max_ticks=120, poll_interval=0.02)
    assert state["status"] == "COMPLETED"
    assert state["completed"] == state["total_tasks"]
    assert len(db2.list_workflow_artifacts("t1", wid)) >= 3
    assert len(db2.list_workflow_messages("t1", wid, limit=200)) >= 1


def test_full_chain_messages_handoff_and_trace(tmp_path):
    """Objective→plan→agents→artifacts→messages→dynamic task→verify→COMPLETED, all persisted."""
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _scope_with_files(tmp_path)
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective="Analyze the repository, produce architecture and security report, verify",
                           scope=scope, tenant_id="t1", execution_mode="REAL_READ", project_id="p1")
    assert planned.is_valid
    wf = engine.create_workflow("t1", "p1", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    state = worker.execute_workflow("t1", "p1", wid, max_ticks=120, poll_interval=0.02)
    assert state["status"] == "COMPLETED"
    tasks = db.list_workflow_tasks("t1", wid)
    assert all(t["status"] == "COMPLETED" for t in tasks)
    arts = db.list_workflow_artifacts("t1", wid)
    assert len(arts) >= 4
    kinds = {a["kind"] for a in arts}
    assert "research_report" in kinds and "verification_result" in kinds
    for a in arts:
        assert a["artifact_id"] is not None and a["provenance"] is not None and len(a["provenance"]) >= 1
    researcher_arts = [a for a in arts if a["agent_id"] == "researcher"]
    assert len(researcher_arts) >= 1 and all(a["reality"] == "OBSERVED" for a in researcher_arts)
    msgs = db.list_workflow_messages("t1", wid, limit=200)
    assert len(msgs) >= 5
    assert any(m["message_type"] == "RESPONSE" and m["from_agent_id"] == "researcher" for m in msgs)
    assert any(m["message_type"] == "STATUS_UPDATE" for m in msgs)
    dyn = db.get_dynamic_tasks("t1", wid)
    assert len(dyn) >= 1
    assert all(d.get("generated_reason") for d in dyn)
    trace = engine.get_execution_trace("t1", wid)
    assert trace["final_result"]["failed"] == 0
    assert trace["final_result"]["completed"] == len(tasks)
    assert len(trace["messages"]) >= 5 and len(trace["artifacts"]) >= 4
