"""NEXUS canonical real-autonomy proof (Phase 10).

Objective -> planning -> multiple real agents -> real tool use -> real
observation -> artifact handoff -> targeted agent messaging -> autonomous
adaptation -> background worker -> persisted state -> recovery ->
verification -> final result.

Every assertion checks persisted backend state with a meaningful failure
condition. No `>= 0` placeholders.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from runtime.agent_registry import AgentRegistry
from runtime.agent_base import AgentExecutionResult
from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
from runtime.messaging_hub import MessagingHub
from runtime.mission_composer import MissionComposer
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from runtime.tools import describe_tools, is_workflow_id_scope, refuse_staged, resolve_workspace_scope
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
from runtime.workflow_planner import WorkflowPlanner
from runtime.workflow_worker import WorkflowWorker, WorkerConfig
from nexus_independent.database import NexusDatabase


def _stack(tmp_path):
    db = NexusDatabase(str(tmp_path / "autonomy.db"))
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
                            config=WorkerConfig(worker_id="w-auto", tenant_id="t1", poll_interval_seconds=0.02,
                                                stop_on_idle=False, auto_recover_stuck=True, claim_stale_seconds=5))
    return db, engine, registry, hub, rt, worker


def _workspace(tmp_path):
    scope = tmp_path / "workspace"
    scope.mkdir(exist_ok=True)
    (scope / "auth_service.py").write_text("import jwt\nSECRET='x'\n")
    (scope / "app.py").write_text("print('hello')\n")
    (scope / "notes.md").write_text("# notes\nreal\n")
    return str(scope)


def test_nested_project_layout_end_to_end(tmp_path):
    # Milestone shape: project/README.md, package.json, src/app.ts, src/config.ts
    # Proves nested real files are observed (hash-matched), handed downstream,
    # and that no dynamic task is faked when no security signal exists.
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    proj = tmp_path / "project"
    (proj / "src").mkdir(parents=True)
    (proj / "README.md").write_text("# demo project\nreal content\n")
    (proj / "package.json").write_text('{"name": "demo", "version": "1.0.0"}\n')
    (proj / "src" / "app.ts").write_text("export const app = 1;\n")
    (proj / "src" / "config.ts").write_text("export const config = { debug: true };\n")
    scope = str(proj)
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective="Analyze this repository and produce a technical architecture report.",
                           scope=scope, tenant_id="t1", execution_mode="REAL_READ", project_id="p1")
    assert planned.is_valid
    wf = engine.create_workflow("t1", "p1", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    state = worker.execute_workflow("t1", "p1", wid, max_ticks=120, poll_interval=0.02)
    assert state["status"] == "COMPLETED"
    arts = db.list_workflow_artifacts("t1", wid)
    research = [a for a in arts if a["kind"] == "research_report"]
    assert len(research) == 1
    content = json.loads(Path(research[0]["content_path"]).read_text())
    observed = {f["file"] for f in content["research"]["findings"]}
    for expected in ("README.md", "package.json", "app.ts", "config.ts"):
        assert any(expected in f for f in observed), f"{expected} not observed: {sorted(observed)}"
    nested = [f for f in observed if "src" in f]
    assert len(nested) >= 2, "nested src/ files must be reached by bounded observation"
    for f in content["research"]["findings"]:
        assert hashlib.sha256(Path(f["file"]).read_bytes()).hexdigest() == f["content_hash"]
    # No security-signal filenames here -> no dynamic task must be invented.
    assert db.get_dynamic_tasks("t1", wid) == []
    # Downstream architect genuinely consumed the nested evidence.
    arch = [a for a in arts if a["kind"] == "architecture_plan"][0]
    arch_content = json.loads(Path(arch["content_path"]).read_text())
    assert arch_content["architecture_plan"]["evidence_count"] >= 4


def test_verifier_independent_not_automatic(tmp_path):
    # VerificationAgent must earn VERIFIED; missing provenance -> INFERRED.
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent
    # Genuine digest (not a placeholder): the integrity contract recomputes
    # the content hash, so the "good" artifact must carry a verifiable one.
    from runtime.agents.verifier import _digest as _vdigest
    _good_content = {"final_report": {}}

    verifier = VerificationAgent()
    good = {"artifact_id": "a1", "kind": "final_report", "name": "final_report.json",
            "content_hash": _vdigest(_good_content), "reality": "INFERRED", "provenance": ["agent:reporter"]}
    bad = {"artifact_id": "a2", "kind": "final_report", "name": "final_report.json",
           "content_hash": "abc123", "reality": "INFERRED", "provenance": []}

    def _ctx(inputs):
        # messaging_hub=None: agents must work without messaging; the verdict
        # logic under test does not depend on message persistence.
        return AgentContext(workflow_id="w", task_id="t", task_name="v", agent_id="verifier",
                            scope="s", input_artifacts=inputs,
                            artifact_contents=[{"artifact_id": "a1", "content": _good_content}],
                            tenant_id="t1", project_id="p1", messaging_hub=None)

    ok_result = verifier.execute(_ctx([good]))
    assert ok_result.reality == "VERIFIED" and ok_result.untrusted is False
    assert ok_result.artifacts[0]["content"]["verification_result"]["all_passed"] is True

    weak_result = verifier.execute(_ctx([bad]))
    assert weak_result.reality == "INFERRED" and weak_result.untrusted is True
    assert weak_result.artifacts[0]["content"]["verification_result"]["all_passed"] is False


def _run_canonical(tmp_path):
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _workspace(tmp_path)
    objective = "Analyze this repository and produce a security/architecture report."
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective=objective, scope=scope, tenant_id="t1",
                           execution_mode="REAL_READ", project_id="p1")
    assert planned.is_valid
    wf = engine.create_workflow("t1", "p1", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    # step() contract: full authoritative state from the very first call
    first = engine.step("t1", "p1", wid)
    for key in ("tasks", "completed_tasks", "failed_tasks", "running_tasks", "artifacts_produced",
                "artifact_ids", "messages_exchanged", "events_emitted", "dynamic_tasks", "progress_percent", "by_status"):
        assert key in first, f"step() missing '{key}'"
    state = worker.execute_workflow("t1", "p1", wid, max_ticks=120, poll_interval=0.02)
    return db, engine, registry, hub, rt, worker, wid, scope, objective, state


def test_canonical_chain_end_to_end(tmp_path):
    db, engine, registry, hub, rt, worker, wid, scope, objective, state = _run_canonical(tmp_path)
    assert state["status"] == "COMPLETED"
    tasks = db.list_workflow_tasks("t1", wid)
    arts = db.list_workflow_artifacts("t1", wid)
    msgs = db.list_workflow_messages("t1", wid, limit=200)
    by_id = {a["artifact_id"]: a for a in arts}

    # 1. real workspace created
    assert Path(scope).is_dir() and len(list(Path(scope).iterdir())) >= 3
    # 2. real file observed (hash matches bytes on disk)
    research = [a for a in arts if a["kind"] == "research_report"]
    assert len(research) >= 1
    content = json.loads(Path(research[0]["content_path"]).read_text())
    findings = content["research"]["findings"]
    assert len(findings) >= 2
    for f in findings:
        assert hashlib.sha256(Path(f["file"]).read_bytes()).hexdigest() == f["content_hash"]
    # 3. observation classified OBSERVED (and untrusted content flag honest)
    assert all(a["reality"] == "OBSERVED" for a in research)
    # 4. researcher artifact persisted with provenance
    assert all(a["provenance"] and len(a["provenance"]) >= 2 for a in research)
    # 5. downstream agent consumes artifact (content-level, not just metadata)
    arch = [a for a in arts if a["kind"] == "architecture_plan"]
    assert len(arch) >= 1
    arch_content = json.loads(Path(arch[0]["content_path"]).read_text())
    assert arch_content["architecture_plan"]["evidence_count"] >= 1
    assert len(arch_content["architecture_plan"]["based_on_findings"]) >= 1
    # 6. architecture artifact classified INFERRED
    assert all(a["reality"] == "INFERRED" and a["untrusted"] in (1, True) for a in arch)
    # 7. agents exchange real targeted messages (architect QUESTION -> security ANSWER)
    questions = [m for m in msgs if m["message_type"] == "QUESTION" and m["to_agent_id"] == "security-analyst"]
    answers = [m for m in msgs if m["message_type"] == "ANSWER" and m["from_agent_id"] == "security-analyst"]
    assert len(questions) >= 1 and len(answers) >= 1
    assert any(a.get("correlation_id") == questions[0].get("correlation_id") for a in answers)
    sec_reports = [json.loads(Path(a["content_path"]).read_text()) for a in arts if a["kind"] == "security_report"]
    assert any(r.get("peer_review", {}).get("question_received") is True for r in sec_reports)
    # 8. dynamic task actually created with justification
    dyn = db.get_dynamic_tasks("t1", wid)
    assert len(dyn) >= 1
    assert all(d.get("generated_reason") and d.get("parent_task_id") for d in dyn)
    # 9. dynamically created task executed to COMPLETED with its own artifact
    dyn_ids = {d["task_id"] for d in dyn}
    dyn_tasks = [t for t in tasks if t["task_id"] in dyn_ids]
    assert len(dyn_tasks) >= 1 and all(t["status"] == "COMPLETED" for t in dyn_tasks)
    assert any(a["task_id"] in dyn_ids for a in arts)
    # 10. artifact provenance persisted across the whole chain
    for a in arts:
        assert a["artifact_id"] and a["workflow_id"] == wid and a["task_id"]
        assert a["agent_id"] and a["created_at"] and a["content_hash"]
        assert isinstance(a.get("provenance"), list) and len(a["provenance"]) >= 1
    # parent links point at real artifacts
    for a in arts:
        for parent in a.get("parent_artifacts", []):
            assert parent in by_id, f"dangling parent {parent}"
    # 11. verifier creates VERIFIED artifact via independent checks
    verified = [a for a in arts if a["reality"] == "VERIFIED"]
    assert len(verified) >= 1
    v_content = json.loads(Path(verified[0]["content_path"]).read_text())
    assert v_content["verification_result"]["all_passed"] is True
    # 12. workflow reaches COMPLETED with zero failures
    assert all(t["status"] == "COMPLETED" for t in tasks)
    # 13. execution trace contains all major events from persisted state
    trace = engine.get_execution_trace("t1", wid)
    assert trace["final_result"]["failed"] == 0
    assert trace["final_result"]["completed"] == len(tasks)
    assert len(trace["messages"]) >= len(msgs) - 1
    assert len(trace["artifacts"]) == len(arts)
    assert len(trace["dynamic_tasks"]) == len(dyn)
    assert trace["objective"] == objective and trace["scope"] == scope
    # 15. no fake OBSERVED classifications exist
    kinds_observed = {a["kind"] for a in arts if a["reality"] == "OBSERVED"}
    assert kinds_observed == {"research_report"}, f"non-observation marked OBSERVED: {kinds_observed}"
    # 16. API-equivalent readers retrieve the same real state
    full = engine.get_workflow_state("t1", wid)
    assert full["summary"]["completed"] == len(tasks)
    assert len(full["artifacts"]) == len(arts)


def test_restart_recovery_from_same_database(tmp_path):
    # 14. restart/recovery works with no state loss
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _workspace(tmp_path)
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective="Analyze this repository", scope=scope, tenant_id="t1",
                           execution_mode="REAL_READ", project_id="p1")
    wf = engine.create_workflow("t1", "p1", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    engine.step("t1", "p1", wid)
    before_tasks = db.list_workflow_tasks("t1", wid)
    before_arts = db.list_workflow_artifacts("t1", wid)
    assert any(t["status"] == "COMPLETED" for t in before_tasks)
    db2 = NexusDatabase(str(tmp_path / "autonomy.db"))
    hub2 = MessagingHub(db2)
    engine2 = WorkflowEngine(database=db2, composer=MissionComposer(),
                             policy=WorkflowExecutionPolicy(fail_on_agent_not_available=False),
                             agent_registry=registry, artifacts_root=tmp_path / "artifacts", messaging_hub=hub2)
    exe2 = MultiAgentExecutor(database=db2, agent_registry=registry, settings=None,
                              principal={"tenant_id": "t1", "project_id": "p1"}, messaging_hub=hub2)
    engine2.set_executor(exe2, agent_registry=registry)
    engine2.messaging_hub = hub2
    worker2 = WorkflowWorker(database=db2, engine=engine2, executor=exe2, agent_registry=registry,
                             messaging_hub=hub2, config=WorkerConfig(worker_id="w-restart", tenant_id="t1", poll_interval_seconds=0.02))
    state = worker2.execute_workflow("t1", "p1", wid, max_ticks=120, poll_interval=0.02)
    assert state["status"] == "COMPLETED"
    after_arts = db2.list_workflow_artifacts("t1", wid)
    assert len(after_arts) >= len(before_arts) and len(after_arts) >= 4
    assert len(db2.list_workflow_messages("t1", wid, limit=200)) >= 1


def test_negative_unauthorized_scope_is_honest(tmp_path):
    """A scope that is not a real directory must fail honestly.

    This test previously asserted that a nonexistent scope still produced ONE
    artifact whose findings were empty. That is the fabricated-observation
    defect: the researcher could not read anything, yet it published an
    OBSERVED research_report. It passed only because every read silently
    returned nothing and the agent reported "no observable files found".

    The real guarantees are stricter and are what is asserted now:
      * the task FAILS rather than publishing an unbacked observation
      * NO content from outside the intended scope can leak
      * no receipt is claimed for an execution that never happened
    """
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    assert resolve_workspace_scope("/nonexistent-path-xyz") is None
    assert is_workflow_id_scope("workflow-wf-123") is True
    spec = WorkflowSpec(name="neg-scope", objective="o", scope="/nonexistent-path-xyz",
                        task_specs=[{"task_id": "n-0", "name": "r", "task_type": "research", "agent_id": "researcher",
                                     "required_capabilities": ["filesystem.read"], "depends_on": [],
                                     "input_artifacts": [], "output_artifacts": ["research_report"], "parameters": {}}],
                        agents=[], execution_mode="REAL_READ")
    wf = engine.create_workflow("t1", "p1", spec)
    engine.start_workflow("t1", "p1", wf["workflow_id"])
    # Drive to a terminal state: a single step dispatches the task but does not
    # necessarily finish it, which would leave the row PENDING rather than
    # recording the outcome under test.
    worker.execute_workflow("t1", "p1", wf["workflow_id"], max_ticks=60, poll_interval=0.01)

    # Honest failure: no artifact is published for a scope nothing could read.
    arts = db.list_workflow_artifacts("t1", wf["workflow_id"])
    assert arts == [], f"an unreadable scope must not publish artifacts, got {len(arts)}"

    task = db.get_workflow_task("t1", "n-0")
    assert task["status"] == "FAILED", task["status"]
    assert task.get("error"), "a failed research task must carry an error"

    # Nothing from outside the intended scope may appear anywhere.
    serialized = json.dumps([arts, task], default=str)
    assert "secret" not in serialized, "content from outside the scope leaked"
    assert "top secret" not in serialized

    # And no receipt may be claimed for work that never happened.
    events = db.list_workflow_events("t1", wf["workflow_id"], limit=200)
    tool_events = [e for e in events if e.get("event_type") == "tool_used"]
    assert tool_events == [], f"a failed scope must report zero tool executions: {tool_events}"


def test_negative_invalid_artifact_reference(tmp_path):
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _workspace(tmp_path)
    spec = WorkflowSpec(name="neg-art", objective="o", scope=scope,
                        task_specs=[{"task_id": "g-0", "name": "generic", "task_type": "report", "agent_id": "reporter",
                                     "required_capabilities": [], "depends_on": [],
                                     "input_artifacts": ["art-does-not-exist"], "output_artifacts": ["final_report"], "parameters": {}}],
                        agents=[], execution_mode="REAL_READ")
    wf = engine.create_workflow("t1", "p1", spec)
    engine.start_workflow("t1", "p1", wf["workflow_id"])
    engine.step("t1", "p1", wf["workflow_id"])
    t = db.get_workflow_task("t1", "g-0")
    assert t["status"] == "COMPLETED"  # dangling refs resolve to empty, never fabricate
    arts = db.list_workflow_artifacts("t1", wf["workflow_id"])
    assert len(arts) == 1 and arts[0]["parent_artifacts"] == []


def test_negative_failed_agent_retries_then_fails(tmp_path):
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _workspace(tmp_path)

    class AlwaysFails:
        agent_id = "failer"
        name = "F"
        role = "failer"
        capabilities = ["x"]
        allowed_operations = ["read"]
        prohibited_operations = []

        def execute(self, ctx):
            raise RuntimeError("permanent failure")

    registry.register_agent(agent_id="failer", name="F", role="failer", agent_type="SPECIALIST",
                            capabilities=["x"], allowed_operations=["read"], prohibited_operations=[],
                            instance=AlwaysFails(), tenant_id="t1")
    spec = WorkflowSpec(name="neg-fail", objective="o", scope=scope,
                        task_specs=[{"task_id": "f-0", "name": "doomed", "task_type": "generic", "agent_id": "failer",
                                     "required_capabilities": ["x"], "depends_on": [],
                                     "input_artifacts": [], "output_artifacts": [], "parameters": {}}],
                        agents=[], execution_mode="REAL_READ")
    wf = engine.create_workflow("t1", "p1", spec)
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    for _ in range(5):
        engine.step("t1", "p1", wid)
    t = db.get_workflow_task("t1", "f-0")
    assert t["status"] == "FAILED" and t["retry_count"] >= 1 and "permanent failure" in (t["error"] or "")
    assert db.get_workflow("t1", wid)["status"] == "FAILED"


def test_negative_cancel_halts_execution(tmp_path):
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _workspace(tmp_path)
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective="Analyze this repository", scope=scope, tenant_id="t1",
                           execution_mode="REAL_READ", project_id="p1")
    wf = engine.create_workflow("t1", "p1", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    engine.cancel_workflow("t1", "p1", wid)
    assert db.get_workflow("t1", wid)["status"] == "CANCELLED"
    assert all(t["status"] in ("COMPLETED", "FAILED", "CANCELLED") for t in db.list_workflow_tasks("t1", wid))


def test_tool_layer_contract(tmp_path):
    from runtime.tools import requires_approval
    tools = {t["capability"]: t for t in describe_tools()}
    # Phase 8 contract: read-only git inspection is executable; writes are
    # staged behind an explicit approval policy (deny-by-default).
    assert set(tools) == {"filesystem.read", "filesystem.write", "filesystem.create", "filesystem.patch",
                          "git.status", "git.diff", "process.execute", "http.request", "repository.inspect"}
    assert tools["filesystem.read"]["executable"] is True
    assert tools["git.status"]["executable"] is True
    assert tools["git.diff"]["executable"] is True
    assert all(tools[c]["executable"] is False for c in ("filesystem.write", "filesystem.create", "filesystem.patch",
                                                         "process.execute", "http.request"))
    assert all(tools[c]["approval"] == "required" for c in ("filesystem.write", "filesystem.create", "filesystem.patch"))
    assert requires_approval("filesystem.write") is True
    assert requires_approval("filesystem.read") is False
    assert requires_approval("git.status") is False
    receipt = refuse_staged(agent_id="tester", capability="filesystem.write", target_resource="/x")
    assert receipt.status == "BLOCKED" and receipt.reality == "OBSERVED"
    assert "approval" in receipt.reason.lower()


def test_duplicate_execution_is_idempotent(tmp_path):
    # Re-stepping a COMPLETED workflow must not duplicate work or artifacts.
    db, engine, registry, hub, rt, worker, wid, scope, objective, state = _run_canonical(tmp_path)
    assert state["status"] == "COMPLETED"
    arts_before = db.list_workflow_artifacts("t1", wid)
    tasks_before = db.list_workflow_tasks("t1", wid)
    engine.step("t1", "p1", wid)
    engine.step("t1", "p1", wid)
    assert db.get_workflow("t1", wid)["status"] == "COMPLETED"
    assert len(db.list_workflow_artifacts("t1", wid)) == len(arts_before)
    assert len(db.list_workflow_tasks("t1", wid)) == len(tasks_before)


def test_missing_dependency_never_executes(tmp_path):
    # A task whose dependency does not exist must stay PENDING, not crash.
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _workspace(tmp_path)
    spec = WorkflowSpec(name="neg-dep", objective="o", scope=scope,
                        task_specs=[{"task_id": "m-0", "name": "orphan", "task_type": "research", "agent_id": "researcher",
                                     "required_capabilities": ["filesystem.read"], "depends_on": ["task-does-not-exist"],
                                     "input_artifacts": [], "output_artifacts": ["research_report"], "parameters": {}}],
                        agents=[], execution_mode="REAL_READ")
    wf = engine.create_workflow("t1", "p1", spec)
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    state = engine.step("t1", "p1", wid)
    t = db.get_workflow_task("t1", "m-0")
    assert t["status"] == "BLOCKED"
    assert "missing dependency" in (t["error"] or "")
    assert state["status"] != "COMPLETED"
    assert db.list_workflow_artifacts("t1", wid) == []


def test_pause_and_resume_roundtrip(tmp_path):
    db, engine, registry, hub, rt, worker = _stack(tmp_path)
    scope = _workspace(tmp_path)
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective="Analyze this repository", scope=scope, tenant_id="t1",
                           execution_mode="REAL_READ", project_id="p1")
    wf = engine.create_workflow("t1", "p1", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    engine.start_workflow("t1", "p1", wid)
    assert engine.pause_workflow("t1", wid) is True
    assert db.get_workflow("t1", wid)["status"] == "PAUSED"
    engine.resume_workflow("t1", "p1", wid)
    assert db.get_workflow("t1", wid)["status"] == "RUNNING"
    state = worker.execute_workflow("t1", "p1", wid, max_ticks=120, poll_interval=0.02)
    assert state["status"] == "COMPLETED"


def test_invalid_reality_rejected_and_lineage_queryable(tmp_path):
    import pytest as _pytest
    db, engine, registry, hub, rt, worker, wid, scope, objective, state = _run_canonical(tmp_path)
    assert state["status"] == "COMPLETED"
    with _pytest.raises(ValueError):
        db.create_artifact(artifact_id="art-bogus", workflow_id=wid, tenant_id="t1", project_id="p1",
                           task_id="task-0", agent_id="researcher", kind="x", name="x",
                           content_hash="abc", parent_artifacts=[], reality="FAKE")
    arts = db.list_workflow_artifacts("t1", wid)
    final = [a for a in arts if a["kind"] == "final_report"][0]
    lineage = engine.get_artifact_lineage("t1", final["artifact_id"])
    assert lineage["artifact"]["artifact_id"] == final["artifact_id"]
    assert len(lineage["ancestors"]) >= 2
    assert len(lineage["consumed_by"]) >= 1
    trace = engine.get_execution_trace("t1", wid)
    assert trace["finish_reason"].startswith("all ")
    assert trace["tools_used"]["filesystem.read"]["count"] >= 2
    assert trace["reality_breakdown"]["OBSERVED"] >= 1
    assert trace["reality_breakdown"]["VERIFIED"] >= 1
