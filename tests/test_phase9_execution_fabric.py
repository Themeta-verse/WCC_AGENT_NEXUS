"""NEXUS Phase 9 — Autonomous Execution Fabric: behavioral reality tests.

Proves the fabric (all against persisted SQLite state):

 1. worker registration (durable STARTING row, capabilities, ravivable)
 2. heartbeat lease renewal (heartbeat_at advances, health counts it)
 3. atomic claim (READY->RUNNING once; losers get False)
 4. competing workers (threads race one task -> exactly one winner)
 5. stale worker detection (silent worker -> STALE, distinct from STOPPED)
 6. task recovery (stale RUNNING -> READY, never auto-FAILED) + completion
 7. artifact idempotency (crash between artifact + completion -> no duplicate)
 8. worker restart mid-task (A claims, dies; B recovers + finishes workflow)
 9. frontend-independent execution (service API only, background thread, trace)
10. approval resume (AWAITING_APPROVAL -> APPROVED via service -> COMPLETED)
11. approval rejection (REJECTED -> task FAILED with reason, workflow FAILED)
12. long-running workflow (heartbeats advance, work accumulates, completes)
13. complete trace (workers, attempts, recovery + approval history)
14. worker shutdown (STOPPED persisted, never STALE, claims still recoverable)
15. clean recovery is a no-op (no spurious events on healthy workflows)
16. CLI control plane (workers/workflow/approval commands, same backend)

No assertion inspects method existence; every test drives behavior and
checks persisted outcomes with strict (non-trivially-true) conditions.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.agent_registry import AgentRegistry
from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
from runtime.messaging_hub import MessagingHub
from runtime.mission_composer import MissionComposer
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
from runtime.workflow_planner import WorkflowPlanner
from runtime.workflow_worker import WorkflowWorker, WorkerConfig
from nexus_independent.database import NexusDatabase

TENANT = "p9-tenant"
PROJECT = "p9-project"


def _repo(root: str) -> str:
    repo = os.path.join(root, "repo")
    os.makedirs(repo, exist_ok=True)
    Path(repo, "app.py").write_text("def main():\n    print('p9')\n", encoding="utf-8")
    Path(repo, "notes.md").write_text("# p9 notes\nreal content\n", encoding="utf-8")
    Path(repo, "config.json").write_text('{"svc": "p9"}\n', encoding="utf-8")
    return repo


def _stack(tmpdir: str):
    db_path = os.path.join(tmpdir, "p9.db")
    db = NexusDatabase(db_path)
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                     (TENANT, "P9", now))
        conn.execute(
            "INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) "
            "VALUES(?,?,?,?,?)", (PROJECT, TENANT, "P9", now, now))
        conn.commit()
    registry = AgentRegistry()
    register_default_agents(registry)
    hub = MessagingHub(db)
    executor = MultiAgentExecutor(
        database=db, agent_registry=registry, settings=None,
        principal={"tenant_id": TENANT, "project_id": PROJECT}, messaging_hub=hub)
    engine = WorkflowEngine(
        database=db, composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=False,
                                       auto_retry_on_failure=True),
        agent_registry=registry, artifacts_root=Path(tmpdir) / "artifacts", messaging_hub=hub)
    engine.set_executor(executor, agent_registry=registry)
    engine.messaging_hub = hub
    planner = WorkflowPlanner(agent_registry=registry)
    runtime = AutonomousRuntime(
        database=db, engine=engine, executor=executor, agent_registry=registry,
        messaging_hub=hub, planner=planner,
        config=AutonomousConfig(tenant_id=TENANT, project_id=PROJECT, poll_interval_seconds=0.02,
                                max_iterations=200, dynamic_task_creation=False, max_dynamic_tasks=0))
    worker = WorkflowWorker(
        database=db, engine=engine, executor=executor, agent_registry=registry,
        messaging_hub=hub, autonomous_runtime=runtime,
        config=WorkerConfig(worker_id="p9-worker", tenant_id=TENANT, poll_interval_seconds=0.02,
                            stop_on_idle=False, idle_limit=30, auto_recover_stuck=True,
                            claim_stale_seconds=60))
    return {"db": db, "db_path": db_path, "engine": engine, "registry": registry, "hub": hub,
            "executor": executor, "planner": planner, "runtime": runtime, "worker": worker}


def _make_worker(stack, worker_id: str, **cfg_kwargs):
    cfg = dict(worker_id=worker_id, tenant_id=TENANT, poll_interval_seconds=0.02,
               stop_on_idle=False, idle_limit=30, auto_recover_stuck=True,
               claim_stale_seconds=60)
    cfg.update(cfg_kwargs)
    return WorkflowWorker(
        database=stack["db"], engine=stack["engine"], executor=stack["executor"],
        agent_registry=stack["registry"], messaging_hub=stack["hub"],
        autonomous_runtime=stack["runtime"], config=WorkerConfig(**cfg))


def _single_task_workflow(stack, repo: str):
    spec = WorkflowSpec(
        name="p9-single", objective="p9 single research", scope=repo,
        task_specs=[{"task_id": "t-0", "name": "Research", "task_type": "research",
                     "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                     "depends_on": [], "input_artifacts": [], "output_artifacts": ["research_report"],
                     "parameters": {}}],
        agents=[], execution_mode="REAL_READ")
    wf = stack["engine"].create_workflow(TENANT, PROJECT, spec)
    stack["engine"].start_workflow(TENANT, PROJECT, wf["workflow_id"])
    stack["db"].update_task_status(TENANT, "t-0", "READY")
    return wf["workflow_id"]


def _backdate(db, table: str, id_col: str, id_val: str, col: str, seconds_ago: int):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))
    with db.connect() as conn:
        conn.execute(f"UPDATE {table} SET {col}=? WHERE {id_col}=?", (old, id_val))
        conn.commit()


# ---------------------------------------------------------------------------
# 1. Worker registration
# ---------------------------------------------------------------------------

def test_01_worker_registration(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    row = db.register_worker("w-reg-1", tenant_id=TENANT, project_id=PROJECT,
                             capabilities=["filesystem.read"])
    assert row["worker_id"] == "w-reg-1" and row["status"] == "STARTING"
    assert row["started_at"] and row["capabilities"] == ["filesystem.read"]
    assert row["current_task_id"] is None
    # Revive keeps the original started_at (same durable identity)
    row2 = db.register_worker("w-reg-1", tenant_id=TENANT, project_id=PROJECT)
    assert row2["started_at"] == row["started_at"]
    assert row2["status"] == "STARTING"
    workers = db.list_workers(TENANT, stale_seconds=60)
    assert any(w["worker_id"] == "w-reg-1" for w in workers)


# ---------------------------------------------------------------------------
# 2. Heartbeat lease
# ---------------------------------------------------------------------------

def test_02_heartbeat_lease(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    db.register_worker("w-hb-1", tenant_id=TENANT, project_id=PROJECT)
    before = db.list_workers(TENANT, stale_seconds=60)
    hb_before = [w for w in before if w["worker_id"] == "w-hb-1"][0]["heartbeat_at"]
    time.sleep(0.05)
    db.heartbeat_worker("w-hb-1", "BUSY", {"active_claims": 1}, tenant_id=TENANT,
                        current_workflow_id="wf-x", current_task_id="t-x")
    after = [w for w in db.list_workers(TENANT, stale_seconds=60) if w["worker_id"] == "w-hb-1"][0]
    assert after["heartbeat_at"] > hb_before, "heartbeat must advance the lease"
    assert after["status"] == "BUSY" and after["liveness"] == "BUSY"
    assert after["current_task_id"] == "t-x"
    health = db.worker_health(stale_seconds=60)
    assert health["active_count"] >= 1


# ---------------------------------------------------------------------------
# 3. Atomic claim
# ---------------------------------------------------------------------------

def test_03_atomic_claim(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    wid = _single_task_workflow(stack, repo)
    assert db.claim_task(TENANT, "t-0", "w-a", execution_id="exec-1") is True
    # Losers (same or competing worker) get False — never double execution
    assert db.claim_task(TENANT, "t-0", "w-a", execution_id="exec-2") is False
    assert db.claim_task(TENANT, "t-0", "w-b", execution_id="exec-3") is False
    task = db.get_workflow_task(TENANT, "t-0")
    assert task["status"] == "RUNNING" and task["worker_id"] == "w-a"
    assert task["claim_count"] == 1 and task["last_execution_id"] == "exec-1"
    assert wid  # workflow persists independently of the claim


# ---------------------------------------------------------------------------
# 4. Competing workers
# ---------------------------------------------------------------------------

def test_04_competing_workers_single_winner(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    _single_task_workflow(stack, repo)
    results: dict[str, bool] = {}
    errors: list[str] = []

    def _race(worker_id: str):
        try:
            fresh = NexusDatabase(stack["db_path"])
            results[worker_id] = fresh.claim_task(TENANT, "t-0", worker_id,
                                                 execution_id=f"exec-{worker_id}")
        except Exception as exc:  # pragma: no cover — must not happen
            errors.append(f"{worker_id}: {exc}")

    threads = [threading.Thread(target=_race, args=(f"w-race-{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, f"claim path raised under contention: {errors}"
    winners = [w for w, ok in results.items() if ok]
    assert len(results) == 8 and len(winners) == 1, f"exactly one winner required, got {winners}"
    task = db.get_workflow_task(TENANT, "t-0")
    assert task["worker_id"] == winners[0] and task["claim_count"] == 1


# ---------------------------------------------------------------------------
# 5. Stale worker detection
# ---------------------------------------------------------------------------

def test_05_stale_worker_detection(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    db.register_worker("w-stale-1", tenant_id=TENANT, project_id=PROJECT)
    db.heartbeat_worker("w-stale-1", "BUSY", {}, tenant_id=TENANT)
    assert db.list_workers(TENANT, stale_seconds=60)[0]["liveness"] == "BUSY"
    _backdate(db, "worker_heartbeats", "worker_id", "w-stale-1", "heartbeat_at", 3600)
    stale = [w for w in db.list_workers(TENANT, stale_seconds=60) if w["worker_id"] == "w-stale-1"][0]
    assert stale["liveness"] == "STALE"
    # STOPPED is terminal and never reported STALE
    db.stop_worker("w-stale-1", reason="test shutdown")
    stopped = [w for w in db.list_workers(TENANT, stale_seconds=60) if w["worker_id"] == "w-stale-1"][0]
    assert stopped["status"] == "STOPPED" and stopped["liveness"] == "STOPPED"


# ---------------------------------------------------------------------------
# 6. Task recovery is not task failure
# ---------------------------------------------------------------------------

def test_06_task_recovery_not_failure(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    wid = _single_task_workflow(stack, repo)
    assert db.claim_task(TENANT, "t-0", "w-dead", execution_id="exec-dead") is True
    _backdate(db, "workflow_tasks", "task_id", "t-0", "claimed_at", 3600)
    _backdate(db, "workflow_tasks", "task_id", "t-0", "updated_at", 3600)
    stuck = db.list_stuck_tasks(TENANT, stale_seconds=60)
    assert any(t["task_id"] == "t-0" for t in stuck)
    recovered = stack["engine"].recover_stuck_tasks(TENANT, wid, stale_seconds=60)
    assert recovered >= 1
    task = db.get_workflow_task(TENANT, "t-0")
    assert task["status"] == "READY", "worker disappearance must not fail the task"
    assert task["worker_id"] is None
    events = db.list_workflow_events(TENANT, wid, limit=100)
    assert any(e["event_type"] == "task_recovered" for e in events)
    # Another worker completes it for real
    worker_b = _make_worker(stack, "w-alive")
    assert worker_b.execute_task_claim(TENANT, PROJECT, wid, "t-0") is True
    assert db.get_workflow_task(TENANT, "t-0")["status"] == "COMPLETED"
    assert len(db.list_workflow_artifacts(TENANT, wid)) == 1


# ---------------------------------------------------------------------------
# 7. Artifact idempotency
# ---------------------------------------------------------------------------

def test_07_artifact_idempotency(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    wid = _single_task_workflow(stack, repo)
    first = db.create_artifact(
        artifact_id="art-first", workflow_id=wid, tenant_id=TENANT, project_id=PROJECT,
        task_id="t-0", agent_id="researcher", kind="research_report", name="research_report.json",
        content_hash="hash-abc", parent_artifacts=[], reality="OBSERVED", untrusted=False,
        execution_id="exec-1")
    assert first.get("deduplicated") is False
    # Crash-replay: same task/kind/hash under a new attempt reuses the row
    second = db.create_artifact(
        artifact_id="art-replay", workflow_id=wid, tenant_id=TENANT, project_id=PROJECT,
        task_id="t-0", agent_id="researcher", kind="research_report", name="research_report.json",
        content_hash="hash-abc", parent_artifacts=[], reality="OBSERVED", untrusted=False,
        execution_id="exec-2")
    assert second.get("deduplicated") is True
    assert second["artifact_id"] == "art-first"
    rows = db.list_workflow_artifacts(TENANT, wid)
    assert len(rows) == 1, "recovery must not duplicate artifact effects"
    # Full crash-replay through the worker with a deterministic agent: the
    # first attempt's artifact is on disk, the task is reset to READY (as if
    # the worker died before marking COMPLETED), and the replay must reuse
    # the identical artifact instead of duplicating it.
    import hashlib as _hl

    from runtime.agent_base import AgentExecutionResult

    class FixedAgent:
        agent_id = "fixed-agent"
        name = "Fixed"
        role = "fixed"
        capabilities = ["test.fixed"]
        allowed_operations = ["read"]
        prohibited_operations = []
        model_router = None

        def execute(self, ctx):
            content = {"v": 1, "task": ctx.task_id}
            return AgentExecutionResult(
                task_id=ctx.task_id, agent_id=self.agent_id, status="COMPLETED",
                reality="INFERRED", untrusted=True,
                result={"status": "COMPLETED"}, artifacts=[{
                    "kind": "fixed_result", "name": "fixed.json", "content": content,
                    "content_hash": _hl.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(),
                    "parent_artifacts": [], "provenance": ["agent:fixed-agent"]}],
                provenance=["agent:fixed-agent"], execution_metadata={})

    stack["registry"].register_agent(
        agent_id="fixed-agent", name="Fixed", role="fixed", agent_type="SPECIALIST",
        capabilities=["test.fixed"], allowed_operations=["read"], prohibited_operations=[],
        expected_behaviour="deterministic fixed output", instance=FixedAgent())
    spec = WorkflowSpec(
        name="p9-fixed", objective="p9 fixed replay", scope=repo,
        task_specs=[{"task_id": "f-0", "name": "Fixed", "task_type": "research",
                     "agent_id": "fixed-agent", "required_capabilities": ["test.fixed"],
                     "depends_on": [], "input_artifacts": [], "output_artifacts": ["fixed_result"],
                     "parameters": {}}],
        agents=[], execution_mode="REAL_READ")
    wf2 = stack["engine"].create_workflow(TENANT, PROJECT, spec)
    wid2 = wf2["workflow_id"]
    stack["engine"].start_workflow(TENANT, PROJECT, wid2)
    stack["db"].update_task_status(TENANT, "f-0", "READY")
    worker_b = _make_worker(stack, "w-replay")
    assert worker_b.execute_task_claim(TENANT, PROJECT, wid2, "f-0") is True
    assert len(db.list_workflow_artifacts(TENANT, wid2)) == 1
    # Crash before completion: artifact persists, task goes READY again.
    db.update_task_status(TENANT, "f-0", "READY")
    assert worker_b.execute_task_claim(TENANT, PROJECT, wid2, "f-0") is True
    replayed = db.list_workflow_artifacts(TENANT, wid2)
    assert len(replayed) == 1, f"crash-replay duplicated artifacts: {len(replayed)}"
    assert db.get_workflow_task(TENANT, "f-0")["claim_count"] == 2
    events = db.list_workflow_events(TENANT, wid2, limit=100)
    assert any(e["event_type"] == "artifact_produced" and e.get("detail", {}).get("deduplicated") is True
               for e in events)


# ---------------------------------------------------------------------------
# 8. Worker restart mid-task
# ---------------------------------------------------------------------------

def test_08_worker_restart_mid_task(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    wid = _single_task_workflow(stack, repo)
    worker_a = _make_worker(stack, "w-crash")
    worker_a.register()
    # A claims the task then "dies": no release, no further heartbeat, process gone.
    assert db.claim_task(TENANT, "t-0", "w-crash", execution_id="exec-crash") is True
    msgs_before = db.list_workflow_messages(TENANT, wid, limit=100)
    # Lease expires...
    _backdate(db, "workflow_tasks", "task_id", "t-0", "claimed_at", 3600)
    _backdate(db, "workflow_tasks", "task_id", "t-0", "updated_at", 3600)
    # ...brand-new worker objects over the SAME database file recover and finish.
    # (New NexusDatabase + MessagingHub instances; the engine/executor see the
    # same SQLite file, which is exactly the cross-process guarantee.)
    db2 = NexusDatabase(stack["db_path"])
    worker_b = WorkflowWorker(
        database=db2, engine=stack["engine"], executor=stack["executor"],
        agent_registry=stack["registry"], messaging_hub=MessagingHub(db2),
        autonomous_runtime=stack["runtime"],
        config=WorkerConfig(worker_id="w-rescue", tenant_id=TENANT, poll_interval_seconds=0.02,
                            stop_on_idle=False, auto_recover_stuck=True, claim_stale_seconds=60))
    recovered = stack["engine"].recover_stuck_tasks(TENANT, wid, stale_seconds=60)
    assert recovered >= 1
    assert worker_b.execute_task_claim(TENANT, PROJECT, wid, "t-0") is True
    final = db2.get_workflow_task(TENANT, "t-0")
    assert final["status"] == "COMPLETED"
    assert final["claim_count"] == 2, "exactly two attempts: crashed + rescue, no more"
    assert len(db2.list_workflow_messages(TENANT, wid, limit=100)) >= len(msgs_before)
    events = db2.list_workflow_events(TENANT, wid, limit=200)
    assert any(e["event_type"] == "task_recovered" for e in events)
    assert any(e["event_type"] == "worker_completed" for e in events)


# ---------------------------------------------------------------------------
# 9. Frontend-independent execution (service API only, background thread)
# ---------------------------------------------------------------------------

def test_09_frontend_independent_execution(tmp_path):
    from nexus_independent.config import ProductSettings
    from nexus_independent.service import StandaloneMissionService
    from nexus_independent.schemas import (
        WorkflowAgentSpec, WorkflowCreateRequest, WorkflowPlanRequest, WorkflowTaskSpec)

    root = Path(str(tmp_path))
    service = StandaloneMissionService(ProductSettings(
        product_root=Path("E:/Nexus"), database_path=root / "svc.db", state_root=root / "state",
        allowed_filesystem_root=Path("E:/Nexus"), github_repository="x/y",
        browser_url="https://example.invalid", allow_real_reads=True,
        api_host="127.0.0.1", api_port=8799, web_origins=("http://127.0.0.1:3000",),
        bootstrap_owner_email="p9@local.test", bootstrap_owner_password="p9 owner password long",
        bootstrap_tenant_name="P9 Tenant", bootstrap_project_id="local"))
    principal = service.login("p9@local.test", "p9 owner password long")["user"]
    tenant = principal["tenant_id"]
    repo = _repo(str(tmp_path))
    # Entirely through the service API: plan -> create -> start -> background run.
    plan = service.plan_workflow(principal, WorkflowPlanRequest(
        objective="Analyze this repository and produce a short architecture report.",
        scope=repo, project_id="local", execution_mode="REAL_READ"))
    assert plan["is_valid"] is True
    created = service.create_workflow(principal, WorkflowCreateRequest(
        name="p9-api-run", objective=plan["objective"], scope=repo, project_id="local",
        task_specs=[WorkflowTaskSpec(**t) for t in plan["task_specs"]],
        agents=[], execution_mode="REAL_READ"))
    wid = created["workflow_id"]
    # run_workflow starts the PENDING workflow itself, then executes it in a
    # background thread — the API equivalent of "UI closed, worker continues".
    launched = service.run_workflow(principal, wid)
    assert launched["status"] == "RUNNING" and launched["worker_id"]

    def _status() -> str:
        return service.get_workflow_state(principal, wid).get("workflow", {}).get("status", "?")

    deadline = time.time() + 180
    while _status() not in ("COMPLETED", "FAILED", "CANCELLED") and time.time() < deadline:
        time.sleep(0.5)
    # No frontend process was ever involved; reconstruct everything from the API.
    assert _status() == "COMPLETED", "API-only run must complete without any UI"
    trace = service.get_execution_trace(principal, wid)
    assert trace["status"] == "COMPLETED"
    assert len(trace["tasks"]) >= 3 and len(trace["artifacts"]) >= 3
    assert trace["final_result"]["failed"] == 0
    assert service.get_workflow(principal, wid)["workflow_id"] == wid


# ---------------------------------------------------------------------------
# 10. Approval resume through the service API
# ---------------------------------------------------------------------------

def _service_stack(tmp_path, tag: str):
    from nexus_independent.config import ProductSettings
    from nexus_independent.service import StandaloneMissionService
    root = Path(str(tmp_path))
    service = StandaloneMissionService(ProductSettings(
        product_root=Path("E:/Nexus"), database_path=root / f"{tag}.db", state_root=root / "state",
        allowed_filesystem_root=Path("E:/Nexus"), github_repository="x/y",
        browser_url="https://example.invalid", allow_real_reads=True,
        api_host="127.0.0.1", api_port=8799, web_origins=("http://127.0.0.1:3000",),
        bootstrap_owner_email=f"{tag}@local.test", bootstrap_owner_password="p9 owner password long",
        bootstrap_tenant_name="P9 Tenant", bootstrap_project_id="local"))
    principal = service.login(f"{tag}@local.test", "p9 owner password long")["user"]
    stack = service._headless_stack(principal["tenant_id"], "local")
    return service, principal, stack


def _gated_workflow(service, principal, stack, repo: str):
    tenant = principal["tenant_id"]
    spec = WorkflowSpec(
        name="p9-gated", objective="p9 gated research", scope=repo,
        task_specs=[{"task_id": "g-0", "name": "Research", "task_type": "research",
                     "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                     "depends_on": [], "input_artifacts": [], "output_artifacts": ["research_report"],
                     "parameters": {}}],
        agents=[], execution_mode="REAL_READ")
    wf = stack["engine"].create_workflow(tenant, "local", spec)
    wid = wf["workflow_id"]
    stack["engine"].start_workflow(tenant, "local", wid)
    stack["engine"].step(tenant, "local", wid)  # completes g-0
    assert service.database.get_workflow_task(tenant, "g-0")["status"] == "COMPLETED"
    return wid


def test_10_approval_resume(tmp_path):
    service, principal, stack = _service_stack(tmp_path, "p9ap")
    tenant = principal["tenant_id"]
    repo = _repo(str(tmp_path))
    wid = _gated_workflow(service, principal, stack, repo)
    approval_id = stack["runtime"].request_approval(
        workflow_id=wid, operation="publish_report", reason="human must clear publication",
        task_id="g-0")
    assert service.database.get_workflow_task(tenant, "g-0")["status"] == "AWAITING_APPROVAL"
    # Worker pauses on the gate (healthy, no failure)...
    stack["worker"].execute_workflow(tenant, "local", wid, max_ticks=5, poll_interval=0.02)
    assert db_task(service, tenant, "g-0") == "AWAITING_APPROVAL"
    assert service.database.get_workflow(tenant, wid)["status"] == "RUNNING"
    # ...human approves through the API...
    decided = service.decide_approval(principal, approval_id, "APPROVED", "looks good")
    assert decided["status"] == "APPROVED"
    assert db_task(service, tenant, "g-0") == "READY"
    # ...and the worker resumes automatically to completion.
    stack["worker"].execute_workflow(tenant, "local", wid, max_ticks=50, poll_interval=0.02)
    assert db_task(service, tenant, "g-0") == "COMPLETED"
    events = service.database.list_workflow_events(tenant, wid, limit=100)
    assert any(e["event_type"] == "approval_granted" for e in events)


def db_task(service, tenant: str, task_id: str) -> str:
    return service.database.get_workflow_task(tenant, task_id)["status"]


# ---------------------------------------------------------------------------
# 11. Approval rejection terminates safely
# ---------------------------------------------------------------------------

def test_11_approval_rejection(tmp_path):
    service, principal, stack = _service_stack(tmp_path, "p9rj")
    tenant = principal["tenant_id"]
    repo = _repo(str(tmp_path))
    spec = WorkflowSpec(
        name="p9-reject", objective="p9 rejected research", scope=repo,
        task_specs=[{"task_id": "r-0", "name": "Research", "task_type": "research",
                     "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                     "depends_on": [], "input_artifacts": [], "output_artifacts": ["research_report"],
                     "parameters": {}}],
        agents=[], execution_mode="REAL_READ")
    wf = stack["engine"].create_workflow(tenant, "local", spec)
    wid = wf["workflow_id"]
    stack["engine"].start_workflow(tenant, "local", wid)
    approval_id = stack["runtime"].request_approval(
        workflow_id=wid, operation="risky_publish", reason="needs review", task_id="r-0")
    decided = service.decide_approval(principal, approval_id, "REJECTED", "not safe")
    assert decided["status"] == "REJECTED"
    task = service.database.get_workflow_task(tenant, "r-0")
    assert task["status"] == "FAILED", "rejection must terminate the gated task, never strand it"
    assert "not safe" in (task.get("error") or "")
    stack["worker"].execute_workflow(tenant, "local", wid, max_ticks=20, poll_interval=0.02)
    assert service.database.get_workflow(tenant, wid)["status"] == "FAILED"
    events = service.database.list_workflow_events(tenant, wid, limit=100)
    assert any(e["event_type"] == "approval_rejected" for e in events)


# ---------------------------------------------------------------------------
# 12. Long-running workflow
# ---------------------------------------------------------------------------

def test_12_long_running_workflow(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    planned = stack["planner"].plan(objective="Review the health of this repository and report risks.",
                                    scope=repo, tenant_id=TENANT, execution_mode="REAL_READ",
                                    project_id=PROJECT)
    assert planned.is_valid and len(planned.task_specs) >= 5
    wf = stack["engine"].create_workflow(TENANT, PROJECT,
                                         stack["planner"].plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    stack["engine"].start_workflow(TENANT, PROJECT, wid)
    worker = _make_worker(stack, "w-long")
    beats: list[str] = []
    passes = 0
    state = {"status": "RUNNING"}
    deadline = time.time() + 240
    while state.get("status") not in ("COMPLETED", "FAILED", "CANCELLED") and time.time() < deadline:
        # One tick per pass: proves the worker sustains the run across many
        # lease renewals instead of finishing inside a single call.
        state = worker.execute_workflow(TENANT, PROJECT, wid, max_ticks=1, poll_interval=0.02)
        passes += 1
        regs = [w for w in db.list_workers(TENANT, stale_seconds=300) if w["worker_id"] == "w-long"]
        assert regs, "worker must stay registered while work continues"
        beats.append(regs[0]["heartbeat_at"])
        time.sleep(0.05)
    assert state.get("status") == "COMPLETED", "long workflow must finish without frontend"
    assert passes >= 3, f"expected a multi-pass run, got {passes}"
    assert len(set(beats)) > 1, "lease must renew across the run"
    assert len(db.list_workflow_messages(TENANT, wid, limit=500)) >= 5
    assert len(db.list_workflow_artifacts(TENANT, wid)) >= 4


# ---------------------------------------------------------------------------
# 13. Complete fabric trace
# ---------------------------------------------------------------------------

def test_13_complete_fabric_trace(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    wid = _single_task_workflow(stack, repo)
    worker = _make_worker(stack, "w-trace")
    assert worker.execute_task_claim(TENANT, PROJECT, wid, "t-0") is True
    stack["engine"].check_workflow_completion(TENANT, wid)
    trace = stack["engine"].get_execution_trace(TENANT, wid)
    assert trace["status"] == "COMPLETED"
    workers = trace.get("workers", [])
    assert any(w["worker_id"] == "w-trace" for w in workers), "trace must expose the durable worker"
    task_entry = [t for t in trace["tasks"] if t["task_id"] == "t-0"][0]
    assert task_entry["worker_id"] == "w-trace"
    assert task_entry["claim_count"] >= 1 and task_entry["last_execution_id"]
    assert "recovery_events" in trace and "approvals_history" in trace
    assert trace["model"]["execution_mode"] == "DETERMINISTIC"
    assert trace["finish_reason"].startswith("all ")
    assert trace["final_result"]["completed"] == 1


# ---------------------------------------------------------------------------
# 14. Worker shutdown
# ---------------------------------------------------------------------------

def test_14_worker_shutdown(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    worker = _make_worker(stack, "w-bye")
    worker.register()
    worker.stop(reason="test complete")
    assert worker.status.status == "STOPPED"
    rows = [w for w in db.list_workers(TENANT, stale_seconds=0) if w["worker_id"] == "w-bye"]
    assert len(rows) == 1 and rows[0]["status"] == "STOPPED"
    assert rows[0]["liveness"] == "STOPPED", "orderly shutdown is never STALE"
    assert rows[0]["current_task_id"] is None, "stop clears the lease assignment"
    live = [w["worker_id"] for w in db.list_workers(TENANT, stale_seconds=3600)
            if w["liveness"] in ("ACTIVE", "IDLE", "BUSY", "STARTING")]
    assert "w-bye" not in live
    # orderly-stopped claims are still time-recoverable (lease expiry is time-based)
    repo = _repo(str(tmp_path))
    wid = _single_task_workflow(stack, repo)
    assert db.claim_task(TENANT, "t-0", "w-bye", execution_id="exec-bye") is True
    worker.stop(reason="died holding a claim")
    _backdate(db, "workflow_tasks", "task_id", "t-0", "claimed_at", 3600)
    _backdate(db, "workflow_tasks", "task_id", "t-0", "updated_at", 3600)
    assert stack["engine"].recover_stuck_tasks(TENANT, wid, stale_seconds=60) >= 1
    assert db.get_workflow_task(TENANT, "t-0")["status"] == "READY"


# ---------------------------------------------------------------------------
# 15. Clean recovery is a no-op
# ---------------------------------------------------------------------------

def test_15_clean_recovery_noop(tmp_path):
    stack = _stack(str(tmp_path))
    db = stack["db"]
    repo = _repo(str(tmp_path))
    wid = _single_task_workflow(stack, repo)
    worker = _make_worker(stack, "w-clean")
    assert worker.execute_task_claim(TENANT, PROJECT, wid, "t-0") is True
    events_before = db.list_workflow_events(TENANT, wid, limit=200)
    assert stack["engine"].recover_stuck_tasks(TENANT, wid, stale_seconds=60) == 0
    events_after = db.list_workflow_events(TENANT, wid, limit=200)
    assert len(events_after) == len(events_before), "healthy workflows gain no spurious recovery events"
    assert db.get_workflow_task(TENANT, "t-0")["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# 16. CLI control plane (same durable backend, no second implementation)
# ---------------------------------------------------------------------------

def _cli(tmp_path, *argv: str) -> dict:
    env = dict(os.environ)
    env["NEXUS_DATABASE_PATH"] = str(Path(str(tmp_path)) / "cli.db")
    proc = subprocess.run(
        [sys.executable, "-m", "nexus_independent.cli", *argv],
        cwd="E:/Nexus", capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, f"CLI {' '.join(argv)} failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout)


def _cli_raw(tmp_path, *argv: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["NEXUS_DATABASE_PATH"] = str(Path(str(tmp_path)) / "cli.db")
    proc = subprocess.run(
        [sys.executable, "-m", "nexus_independent.cli", *argv],
        cwd="E:/Nexus", capture_output=True, text=True, timeout=300, env=env)
    assert proc.returncode == 0, f"CLI {' '.join(argv)} failed: {proc.stderr[-2000:]}"
    return proc


def test_16_cli_control_plane(tmp_path):
    tmpdir = str(tmp_path)
    db = NexusDatabase(os.path.join(tmpdir, "cli.db"))
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                     ("cli-t", "CLI", now))
        conn.execute(
            "INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) "
            "VALUES(?,?,?,?,?)", ("cli-p", "cli-t", "CLI", now, now))
        conn.commit()
    repo = _repo(tmpdir)
    # Objective -> plan -> persist -> headless execution, all via CLI.
    # `workflow run` prints the human summary; --record carries the machine record.
    record_path = os.path.join(tmpdir, "nexus-run.json")
    proc = _cli_raw(tmp_path, "workflow", "run", "--tenant", "cli-t", "--project", "cli-p",
                    "--objective", "Analyze this repository and summarize its structure.",
                    "--scope", repo, "--max-ticks", "200", "--poll-interval", "0.05",
                    "--record", record_path)
    assert "COMPLETED" in proc.stdout and "VERIFIED" in proc.stdout
    run = json.loads(Path(record_path).read_text())
    assert run["status"] == "COMPLETED" and run["workflow_id"]
    assert len(run["tool_calls"]) >= 1 and len(run["artifacts"]) >= 3
    wid = run["workflow_id"]
    status_proc = _cli_raw(tmp_path, "workflow", "status", "--tenant", "cli-t", "--project", "cli-p",
                           "--workflow-id", wid)
    assert "COMPLETED" in status_proc.stdout and "VERIFIED" in status_proc.stdout
    assert wid in status_proc.stdout
    trace = _cli(tmp_path, "workflow", "trace", "--tenant", "cli-t", "--project", "cli-p",
                 "--workflow-id", wid)
    assert trace["status"] == "COMPLETED" and len(trace["artifacts"]) >= 3
    workers = _cli(tmp_path, "workers", "list", "--tenant", "cli-t")
    assert any(w["worker_id"].startswith("cli-worker-") for w in workers)
    assert any(w["worker_id"] in {x["worker_id"] for x in run["workers"]} for w in workers)
    recover = _cli(tmp_path, "workflow", "recover", "--tenant", "cli-t", "--project", "cli-p",
                   "--workflow-id", wid, "--stale-seconds", "60")
    assert recover["recovered"] == 0
    target = next(w["worker_id"] for w in workers if w["worker_id"].startswith("cli-worker-"))
    stopped = _cli(tmp_path, "workers", "stop", "--worker-id", target)
    assert stopped["stopped"] is True
    approvals = _cli(tmp_path, "approval", "list", "--tenant", "cli-t", "--project", "cli-p")
    assert isinstance(approvals, list)
