"""NEXUS Phase 8 — Real autonomous agent runtime: behavioral reality tests.

Proves (against persisted SQLite state, never in-memory claims):

 1. real provider invocation through ModelRouter (skipped unless configured)
 2. provider fallback with explicit fallback_used + attempted_providers
 3. model-requested filesystem.read through the bounded tool layer
 4. tool capability rejection (unknown/staged tools never execute)
 5. OBSERVED tool result (sha matches bytes on disk)
 6. INFERRED model result (model output is never OBSERVED)
 7. QUESTION/ANSWER agent collaboration with correlation IDs
 8. HANDOFF from reporter to verifier
 9. discovery-driven dynamic task (auth_service.py -> security follow-up)
10. artifact handoff (downstream parents reference upstream artifact IDs)
11. independent verification (verifier earns VERIFIED; never trusts claims)
12. worker restart from the same SQLite file with no duplication/loss
13. deterministic fallback when no provider is configured (MODEL NOT CONFIGURED)
14. secret redaction (no API key in status, events, or trace)
15. complete execution trace (objective -> ... -> verified report)

A scripted in-process provider (ScriptedProvider) is used ONLY as a
deterministic harness for the model<->tool loop mechanics. It never stands
in for test 1: the real-provider test is explicitly skipped when no provider
is configured, and is never replaced by a fake while claiming proof.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.agent_registry import AgentRegistry
from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
from runtime.messaging_hub import MessagingHub
from runtime.mission_composer import MissionComposer
from runtime.model_agent_loop import ModelAgentLoop, AgentLoopConfig
from runtime.model_context import ModelContextBuilder
from runtime.model_router import (
    MockModelAdapter,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelToolCall,
    ToolDefinition,
    parse_tool_calls_from_payload,
    redact_for_log,
)
from runtime.model_strategy import ExecutionStrategy, resolve_strategy, should_use_model
from runtime.model_tools import execute_tool_call, validate_tool_call
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
from runtime.workflow_planner import WorkflowPlanner
from runtime.workflow_worker import WorkflowWorker, WorkerConfig
from nexus_independent.database import NexusDatabase

TENANT = "p8-tenant"
PROJECT = "p8-project"

ACCEPTANCE_OBJECTIVE = (
    "Analyze this repository, identify security risks, propose remediation, "
    "and produce a verified report."
)


# ---------------------------------------------------------------------------
# Scripted harness provider (loop mechanics only — never "proof" of a real LLM)
# ---------------------------------------------------------------------------

class ScriptedProvider(ModelProvider):
    """Deterministic scripted stand-in for loop/tool-path tests only.

    script entries: {"text": ...} | {"tool_calls": [...]} | {"fail": ...}.
    Every response is INFERRED/untrusted, like any model output.
    """

    name = "scripted-harness"

    def __init__(self, script=None, model="scripted-harness-v1"):
        self.script = list(script or [{"text": '{"insight": "default"}'}])
        self.default_model = model
        self.calls = []
        self._index = 0

    def health(self):
        return {"provider": self.name, "status": "AVAILABLE", "availability": True}

    def complete(self, prompt, *, system=None, schema=None, temperature=0.2, timeout=30, model=None):
        self.calls.append({"prompt": prompt, "model": model or self.default_model})
        entry = self.script[min(self._index, len(self.script) - 1)]
        self._index += 1
        if "fail" in entry:
            raise ModelProviderError(self.name, entry["fail"])
        content = entry.get("text", json.dumps({"tool_calls": entry.get("tool_calls", [])}))
        if "tool_calls" in entry and "text" not in entry:
            content = json.dumps({"tool_calls": entry["tool_calls"]})
        try:
            structured = json.loads(content)
        except Exception:
            structured = None
        resp = ModelResponse(
            content=content, model=model or self.default_model, provider=self.name,
            duration_seconds=0.01, reality="INFERRED", untrusted=True,
            status="SUCCESS", structured=structured, raw_response={"harness": True},
        )
        if structured is not None:
            resp.tool_calls = parse_tool_calls_from_payload(structured)
        return resp


class AlwaysFailProvider(ModelProvider):
    name = "always-fail-p8"

    def health(self):
        return {"provider": self.name, "status": "AVAILABLE", "availability": True}

    def complete(self, prompt, *, system=None, schema=None, temperature=0.2, timeout=30, model=None):
        raise ModelProviderError(self.name, "intentional provider failure")


# ---------------------------------------------------------------------------
# Stack helpers
# ---------------------------------------------------------------------------

def _make_repo(root: str) -> str:
    repo = os.path.join(root, "repo")
    os.makedirs(repo, exist_ok=True)
    Path(repo, "README.md").write_text("# Acme Service\nToken-based auth, see auth_service.py.\n", encoding="utf-8")
    Path(repo, "app.py").write_text("import os\n\ndef main():\n    print('acme')\n", encoding="utf-8")
    Path(repo, "auth_service.py").write_text(
        "import hashlib\nSESSION_TIMEOUT = 3600\n\ndef login(user, pw):\n    return hashlib.sha256(pw.encode()).hexdigest()\n",
        encoding="utf-8",
    )
    Path(repo, "config.json").write_text('{"service": "acme", "port": 8080}\n', encoding="utf-8")
    return repo


def _stack(tmpdir: str, router=None, strategy: str = "DETERMINISTIC"):
    db_path = os.path.join(tmpdir, "p8.db")
    db = NexusDatabase(db_path)
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", (TENANT, "P8", now))
        conn.execute(
            "INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)",
            (PROJECT, TENANT, "P8", now, now))
        conn.commit()
    registry = AgentRegistry()
    register_default_agents(registry)
    hub = MessagingHub(db)
    router = router or ModelRouter()
    executor = MultiAgentExecutor(
        database=db, agent_registry=registry, settings=None,
        principal={"tenant_id": TENANT, "project_id": PROJECT},
        messaging_hub=hub, model_router=router, default_execution_strategy=strategy)
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
                                max_iterations=200, dynamic_task_creation=True, max_dynamic_tasks=5))
    worker = WorkflowWorker(
        database=db, engine=engine, executor=executor, agent_registry=registry,
        messaging_hub=hub, autonomous_runtime=runtime,
        config=WorkerConfig(worker_id="p8-worker", tenant_id=TENANT, poll_interval_seconds=0.02,
                            stop_on_idle=False, idle_limit=20, auto_recover_stuck=True,
                            claim_stale_seconds=5))
    return {"db": db, "db_path": db_path, "engine": engine, "registry": registry, "hub": hub,
            "executor": executor, "planner": planner, "runtime": runtime, "worker": worker,
            "router": router}


def _run_acceptance(stack, repo: str, strategy: str = "DETERMINISTIC"):
    for _aid, _reg in list(stack["registry"]._agents.items()):
        try:
            _reg.instance.execution_strategy = strategy
        except Exception:
            pass
    stack["executor"].default_execution_strategy = strategy
    return stack["runtime"].execute_objective(
        objective=ACCEPTANCE_OBJECTIVE, scope=repo, constraints={}, max_iterations=120)


@pytest.fixture(scope="module")
def acceptance(tmp_path_factory):
    """One full acceptance run, shared by trace/collaboration assertions."""
    tmpdir = str(tmp_path_factory.mktemp("p8-accept"))
    repo = _make_repo(tmpdir)
    stack = _stack(tmpdir)
    result = _run_acceptance(stack, repo)
    wid = result["workflow_id"]
    state = stack["engine"].get_workflow_state(TENANT, wid)
    trace = stack["engine"].get_execution_trace(TENANT, wid)
    return {"stack": stack, "repo": repo, "wid": wid, "result": result,
            "state": state, "trace": trace}


# ---------------------------------------------------------------------------
# 1. Real provider invocation (explicitly skipped when unconfigured)
# ---------------------------------------------------------------------------

def test_01_real_provider_invocation_or_skip():
    router = ModelRouter()
    if not router.is_configured():
        pytest.skip("no real model provider configured (MODEL NOT CONFIGURED); "
                    "real-provider proof requires NEXUS_MODEL_PROVIDER + key or local Ollama")
    request = ModelRequest(agent_objective="smoke test", task="ping",
                           task_type="agent_reasoning", timeout=25)
    resp = router.generate(request, "Reply with exactly: NEXUS_SMOKE_OK")
    assert resp.provider != "mock", "a real provider must serve this call, never the mock"
    assert resp.reality == "INFERRED" and resp.untrusted is True
    assert resp.execution_id.startswith("model-exec-")
    assert resp.provider in resp.attempted_providers


# ---------------------------------------------------------------------------
# 2. Provider fallback is explicit
# ---------------------------------------------------------------------------

def test_02_provider_fallback_recorded():
    fail = AlwaysFailProvider()
    mock = MockModelAdapter()
    router = ModelRouter(providers=[fail, mock], allow_mock_fallback=True)
    router.set_task_policy("agent_reasoning", ["always-fail-p8", "mock"])
    resp = router.generate(ModelRequest(agent_objective="x", task="y", task_type="agent_reasoning"), "hi")
    assert resp.provider == "mock"
    assert resp.fallback_used is True
    assert resp.attempted_providers[0] == "always-fail-p8"
    assert "mock" in resp.attempted_providers
    assert resp.reality == "INFERRED" and resp.untrusted is True


def test_02b_all_providers_failing_raises_honestly():
    router = ModelRouter(providers=[AlwaysFailProvider()], allow_mock_fallback=False)
    router.set_task_policy("agent_reasoning", ["always-fail-p8"])
    with pytest.raises(ModelProviderError):
        router.generate(ModelRequest(agent_objective="x", task="y", task_type="agent_reasoning"), "hi")


# ---------------------------------------------------------------------------
# 3. Model-requested filesystem.read executes for real
# ---------------------------------------------------------------------------

def test_03_model_requested_filesystem_read(tmp_path):
    repo = _make_repo(str(tmp_path))
    target = os.path.join(repo, "auth_service.py")
    fake = ScriptedProvider(script=[
        {"tool_calls": [{"tool": "filesystem.read", "arguments": {"path": target}}]},
        {"text": '{"insight": "saw auth_service.py via bounded tool"}'},
    ])
    router = ModelRouter(providers=[fake], allow_mock_fallback=False)
    router.set_task_policy("agent_reasoning", ["scripted-harness"])
    loop = ModelAgentLoop(router=router,
                          config=AgentLoopConfig(max_iterations=5, max_tool_calls=4, timeout_seconds=30))
    result = loop.run(
        agent_id="researcher", agent_role="researcher", agent_capabilities=["filesystem.read"],
        objective="Analyze repo", task_name="Research", task_type="agent_reasoning",
        observed_evidence=[],
        tool_definitions=[ToolDefinition(name="filesystem.read")],
        workspace_root=repo)
    assert result.finished, f"loop must finish: {result.error}"
    assert len(result.tool_records) == 1
    rec = result.tool_records[0]
    assert rec.capability == "filesystem.read" and rec.status == "EXECUTED"
    assert rec.reality == "OBSERVED"
    assert rec.content_sha256 == hashlib.sha256(Path(target).read_bytes()).hexdigest()
    assert "def login" in (rec.observation.get("text", "") or "")
    assert any(e["type"] == "model_invocation" for e in result.events)
    assert any(e["type"] == "tool_result" and e["reality"] == "OBSERVED" for e in result.events)
    assert result.final_response is not None and result.final_response.reality == "INFERRED"


def test_03b_model_requested_git_status(tmp_path):
    repo = _make_repo(str(tmp_path))
    fake = ScriptedProvider(script=[
        {"tool_calls": [{"tool": "git.status", "arguments": {}}]},
        {"text": '{"insight": "git status observed"}'},
    ])
    router = ModelRouter(providers=[fake], allow_mock_fallback=False)
    router.set_task_policy("agent_reasoning", ["scripted-harness"])
    loop = ModelAgentLoop(router=router,
                          config=AgentLoopConfig(max_iterations=5, max_tool_calls=4, timeout_seconds=30))
    result = loop.run(
        agent_id="researcher", agent_role="researcher",
        agent_capabilities=["filesystem.read", "git.status", "git.diff"],
        objective="Analyze repo", task_name="Research", task_type="agent_reasoning",
        observed_evidence=[],
        tool_definitions=[ToolDefinition(name="git.status")],
        workspace_root=repo)
    assert result.finished
    rec = result.tool_records[0]
    # tmp repo is not a git checkout -> honest BLOCKED, still OBSERVED, never fabricated
    assert rec.capability == "git.status" and rec.status == "BLOCKED"
    assert rec.reality == "OBSERVED"


def test_03c_model_requested_git_diff_real_repo():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # E:\Nexus is a git repo
    if not os.path.isdir(os.path.join(repo, ".git")):
        pytest.skip("NEXUS checkout is not a git work tree here")
    from runtime.tools import git_status, git_diff
    receipt, obs = git_status(agent_id="researcher", workspace_root=repo)
    assert receipt.status == "EXECUTED" and receipt.reality == "OBSERVED"
    assert obs is not None and "output" in obs
    receipt2, obs2 = git_diff(agent_id="researcher", workspace_root=repo, max_chars=5000)
    assert receipt2.status in ("EXECUTED", "BLOCKED")
    assert receipt2.reality == "OBSERVED"


# ---------------------------------------------------------------------------
# 4. Tool capability rejection
# ---------------------------------------------------------------------------

def test_04_tool_capability_rejection(tmp_path):
    repo = _make_repo(str(tmp_path))
    # Unknown tool is REJECTED without execution
    rec = execute_tool_call(ModelToolCall(tool="process.execute", arguments={"cmd": "id"}),
                            agent_id="researcher", agent_capabilities=["filesystem.read"],
                            workspace_root=repo)
    assert rec.status in ("REJECTED", "BLOCKED") and rec.reality == "OBSERVED"
    # Staged write capability is refused even when declared (approval policy)
    rec2 = execute_tool_call(ModelToolCall(tool="filesystem.write", arguments={"path": "x.txt"}),
                             agent_id="writer", agent_capabilities=["filesystem.write"],
                             workspace_root=repo)
    assert rec2.status == "BLOCKED" and rec2.reality == "OBSERVED"
    assert "approval" in rec2.reason.lower()
    assert not os.path.exists(os.path.join(repo, "x.txt"))
    # Undeclared tool is REJECTED (capability policy enforced)
    v = validate_tool_call(ModelToolCall(tool="filesystem.read", arguments={"path": "a.py"}),
                           agent_capabilities=["knowledge.read"])
    assert v.allowed is False


# ---------------------------------------------------------------------------
# 5. OBSERVED tool result matches disk bytes
# ---------------------------------------------------------------------------

def test_05_observed_tool_result(tmp_path):
    from runtime.tools import filesystem_read
    repo = _make_repo(str(tmp_path))
    target = os.path.join(repo, "app.py")
    receipt, obs = filesystem_read(agent_id="researcher", workspace_root=repo,
                                   target_path=target, max_chars=10000)
    assert receipt.status == "EXECUTED" and receipt.reality == "OBSERVED"
    assert receipt.content_sha256 == hashlib.sha256(Path(target).read_bytes()).hexdigest()
    assert obs is not None and obs["reality"] == "OBSERVED"
    # Relative paths resolve against the workspace root, never the CWD
    receipt_rel, obs_rel = filesystem_read(agent_id="researcher", workspace_root=repo,
                                           target_path="config.json", max_chars=1000)
    assert receipt_rel.status == "EXECUTED"
    assert obs_rel is not None and obs_rel["path"].endswith("config.json")
    # Outside-root reads are BLOCKED but still OBSERVED facts
    receipt_out, obs_out = filesystem_read(agent_id="researcher", workspace_root=repo,
                                           target_path=os.path.join(str(tmp_path), "nope.txt"),
                                           max_chars=100)
    assert receipt_out.status == "BLOCKED" and receipt_out.reality == "OBSERVED"
    assert obs_out is None


# ---------------------------------------------------------------------------
# 6. Model output is INFERRED, never OBSERVED
# ---------------------------------------------------------------------------

def test_06_inferred_model_result():
    router = ModelRouter(providers=[MockModelAdapter()], allow_mock_fallback=True)
    resp = router.generate(ModelRequest(agent_objective="x", task="y", task_type="agent_reasoning"), "hello")
    assert resp.reality == "INFERRED"
    assert resp.untrusted is True
    assert "OBSERVED" not in resp.reality and "VERIFIED" not in resp.reality


# ---------------------------------------------------------------------------
# 7/8/9/10/15. Acceptance run: collaboration, dynamic tasks, handoff, trace
# ---------------------------------------------------------------------------

def test_07_question_answer_collaboration(acceptance):
    stack, wid = acceptance["stack"], acceptance["wid"]
    msgs = stack["db"].list_workflow_messages(TENANT, wid, limit=300)
    questions = [m for m in msgs if m["message_type"] == "QUESTION" and m["to_agent_id"] == "security-analyst"]
    answers = [m for m in msgs if m["message_type"] == "ANSWER" and m["from_agent_id"] == "security-analyst"]
    # ResearchAgent -> QUESTION -> SecurityAgent (auth discovery) AND Architect -> QUESTION
    senders = {m["from_agent_id"] for m in questions}
    assert "researcher" in senders, f"researcher must ask security; senders={senders}"
    assert len(answers) >= 1
    q_corr = {m.get("correlation_id") for m in questions if m.get("correlation_id")}
    a_corr = {m.get("correlation_id") for m in answers if m.get("correlation_id")}
    assert q_corr & a_corr, "every QUESTION needs a correlation-matched ANSWER"
    for m in questions + answers:
        assert m.get("workflow_id") == wid and m.get("created_at") and m.get("message_id")


def test_08_handoff_reporter_to_verifier(acceptance):
    stack, wid = acceptance["stack"], acceptance["wid"]
    msgs = stack["db"].list_workflow_messages(TENANT, wid, limit=300)
    handoffs = [m for m in msgs if m["message_type"] == "HANDOFF"]
    assert len(handoffs) >= 1, "reporter must HANDOFF the final report to the verifier"
    h = handoffs[0]
    assert h["from_agent_id"] == "reporter" and h["to_agent_id"] == "verifier"
    assert h.get("correlation_id") and h.get("created_at")
    assert h["content"].get("payload", {}).get("artifact_kind") == "final_report"


def test_09_discovery_driven_dynamic_task(acceptance):
    stack, wid = acceptance["stack"], acceptance["wid"]
    dyn = stack["db"].get_dynamic_tasks(TENANT, wid)
    assert len(dyn) >= 1, "auth_service.py discovery must create a security follow-up task"
    reasons = " ".join(d.get("generated_reason", "") for d in dyn)
    assert "auth_service.py" in reasons or "security-relevant" in reasons
    assert all(d.get("parent_task_id") and d.get("generated_reason") for d in dyn)
    # The reason exists in the persisted event stream, not just the row
    events = stack["db"].list_workflow_events(TENANT, wid, limit=500)
    dyn_events = [e for e in events if e.get("event_type") == "dynamic_task_created"]
    assert len(dyn_events) >= 1
    assert any("auth_service.py" in json.dumps(e.get("detail", {})) or "security" in json.dumps(e.get("detail", {})).lower()
               for e in dyn_events)
    # The triggering discovery announcement is itself in the trace
    msgs = stack["db"].list_workflow_messages(TENANT, wid, limit=300)
    assert any("Authentication-related code discovered" in json.dumps(m.get("content", {})) for m in msgs)


def test_10_artifact_handoff_lineage(acceptance):
    stack, wid = acceptance["stack"], acceptance["wid"]
    arts = stack["db"].list_workflow_artifacts(TENANT, wid)
    by_id = {a["artifact_id"]: a for a in arts}
    kinds = {a["kind"] for a in arts}
    assert {"research_report", "architecture_plan", "security_report", "final_report", "verification_result"} <= kinds
    research_ids = {a["artifact_id"] for a in arts if a["kind"] == "research_report"}
    arch = [a for a in arts if a["kind"] == "architecture_plan"]
    assert any(set(a.get("parent_artifacts") or []) & research_ids for a in arch), \
        "architecture plan must descend from the research artifact"
    report = [a for a in arts if a["kind"] == "final_report"][0]
    assert len(report.get("parent_artifacts") or []) >= 2, "final report must descend from multiple upstream artifacts"
    for pid in report["parent_artifacts"]:
        assert pid in by_id, f"dangling parent reference {pid}"


def test_11_independent_verification(acceptance):
    stack, wid = acceptance["stack"], acceptance["wid"]
    arts = stack["db"].list_workflow_artifacts(TENANT, wid)
    verify = [a for a in arts if a["kind"] == "verification_result"]
    assert len(verify) >= 1
    assert verify[0]["reality"] == "VERIFIED"
    content = json.loads(Path(verify[0]["content_path"]).read_text())
    vr = content["verification_result"]
    assert vr["all_passed"] is True and vr["independent"] is True
    assert all(c["status"] == "PASS" for c in vr["checks"])
    # A bare claim without evidence can never become VERIFIED
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent
    ctx = AgentContext(workflow_id="w", task_id="t", task_name="v", agent_id="verifier",
                       scope="s",
                       input_artifacts=[{"artifact_id": "a", "kind": "final_report", "name": "f",
                                         "content_hash": "h", "reality": "INFERRED", "provenance": []}],
                       artifact_contents=[{"content": {"final_report": {}}}],
                       tenant_id=TENANT, project_id=PROJECT, messaging_hub=None)
    res = VerificationAgent().execute(ctx)
    assert res.reality == "INFERRED" and res.untrusted is True


def test_15_complete_execution_trace(acceptance):
    assert acceptance["result"]["status"] == "COMPLETED"
    trace = acceptance["trace"]
    assert trace["objective"] == ACCEPTANCE_OBJECTIVE
    assert trace["status"] == "COMPLETED"
    assert len(trace["tasks"]) >= 5
    assert set(trace["agents"]) >= {"researcher", "architect", "security-analyst", "reporter", "verifier"}
    assert len(trace["messages"]) >= 5
    assert trace["tools_used"].get("filesystem.read", {}).get("count", 0) >= 2
    assert len(trace["artifacts"]) >= 5
    assert len(trace["dynamic_tasks"]) >= 1
    assert len(trace["provenance_chain"]) >= 5
    assert trace["final_result"]["completed"] == trace["final_result"]["total"]
    assert trace["final_result"]["failed"] == 0
    assert trace["model"]["execution_mode"] == "DETERMINISTIC"  # honest when unconfigured
    assert "NOT_CONFIGURED" in json.dumps(trace["model"])
    assert trace["finish_reason"].startswith("all ")
    assert trace["verification"]["all_completed"] is True
    # Planner classified the objective (not a default guess)
    assert trace["planning"]["template_type"] in ("repository_analysis", "repository_audit", "repository_health")


# ---------------------------------------------------------------------------
# 12. Worker restart from the same SQLite file
# ---------------------------------------------------------------------------

def test_12_worker_restart_no_duplication_no_loss(tmp_path):
    from runtime.mission_composer import MissionComposer as _MC
    tmpdir = str(tmp_path)
    stack = _stack(tmpdir)
    repo = _make_repo(tmpdir)
    planned = stack["planner"].plan(objective=ACCEPTANCE_OBJECTIVE, scope=repo,
                                    tenant_id=TENANT, execution_mode="REAL_READ",
                                    project_id=PROJECT)
    wf = stack["engine"].create_workflow(TENANT, PROJECT,
                                         stack["planner"].plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    stack["engine"].start_workflow(TENANT, PROJECT, wid)
    stack["worker"].execute_workflow(TENANT, PROJECT, wid, max_ticks=2, poll_interval=0.02)
    before_tasks = {t["task_id"]: t["status"] for t in stack["db"].list_workflow_tasks(TENANT, wid)}
    before_arts = stack["db"].list_workflow_artifacts(TENANT, wid)
    before_msgs = stack["db"].list_workflow_messages(TENANT, wid, limit=500)
    before_events = stack["db"].list_workflow_events(TENANT, wid, limit=500)
    assert any(s == "COMPLETED" for s in before_tasks.values()), "partial run must complete >= 1 task"
    before_msg_ids = {m["message_id"] for m in before_msgs}
    before_event_ids = {e["event_id"] for e in before_events}

    # === Simulated process restart: brand-new objects over the same DB file ===
    db2 = NexusDatabase(stack["db_path"])
    registry2 = AgentRegistry()
    register_default_agents(registry2)
    hub2 = MessagingHub(db2)
    exe2 = MultiAgentExecutor(database=db2, agent_registry=registry2, settings=None,
                              principal={"tenant_id": TENANT, "project_id": PROJECT},
                              messaging_hub=hub2)
    engine2 = WorkflowEngine(database=db2, composer=_MC(),
                             policy=WorkflowExecutionPolicy(max_retries_default=2,
                                                            fail_on_agent_not_available=False,
                                                            auto_retry_on_failure=True),
                             agent_registry=registry2,
                             artifacts_root=Path(tmpdir) / "artifacts", messaging_hub=hub2)
    engine2.set_executor(exe2, agent_registry=registry2)
    engine2.messaging_hub = hub2
    worker2 = WorkflowWorker(database=db2, engine=engine2, executor=exe2, agent_registry=registry2,
                             messaging_hub=hub2,
                             config=WorkerConfig(worker_id="p8-worker-restart", tenant_id=TENANT,
                                                 poll_interval_seconds=0.02, stop_on_idle=False,
                                                 auto_recover_stuck=True, claim_stale_seconds=1))
    state = worker2.execute_workflow(TENANT, PROJECT, wid, max_ticks=150, poll_interval=0.02)
    assert state["status"] == "COMPLETED"

    after_tasks = db2.list_workflow_tasks(TENANT, wid)
    after_arts = db2.list_workflow_artifacts(TENANT, wid)
    after_msgs = db2.list_workflow_messages(TENANT, wid, limit=500)
    after_events = db2.list_workflow_events(TENANT, wid, limit=500)
    # No duplicate completed tasks: each task completed exactly once
    assert all(t["status"] == "COMPLETED" for t in after_tasks)
    for t in after_tasks:
        kinds = [a["kind"] for a in after_arts if a["task_id"] == t["task_id"]]
        assert len(kinds) == len(set(kinds)), f"task {t['task_id']} produced duplicate artifacts: {kinds}"
    # Everything from before the restart survived
    after_msg_ids = {m["message_id"] for m in after_msgs}
    after_event_ids = {e["event_id"] for e in after_events}
    assert before_msg_ids <= after_msg_ids, "messages lost across restart"
    assert before_event_ids <= after_event_ids, "events (incl. model/strategy) lost across restart"
    assert len(after_arts) >= len(before_arts)
    # Trace remains complete after resume
    trace = engine2.get_execution_trace(TENANT, wid)
    assert trace["status"] == "COMPLETED"
    assert trace["final_result"]["completed"] == trace["final_result"]["total"]
    trace_msg_ids = {m["message_id"] for m in trace["messages"]}
    assert before_msg_ids <= trace_msg_ids, "trace lost pre-restart messages"
    assert len(trace["artifacts"]) == len(after_arts)


# ---------------------------------------------------------------------------
# 13. Deterministic fallback when no provider is configured
# ---------------------------------------------------------------------------

def test_13_deterministic_fallback_without_provider(tmp_path, monkeypatch):
    for var in ("NEXUS_MODEL_PROVIDER", "NEXUS_MODEL_API_KEY", "NEXUS_OPENROUTER_API_KEY",
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
                "NEXUS_OPENAI_COMPATIBLE_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    router = ModelRouter()
    assert router.is_configured() is False
    status = router.redacted_status()
    assert status["provider"] == "NOT_CONFIGURED"
    assert status["execution_mode"] == "DETERMINISTIC"
    assert router.execution_mode() == "DETERMINISTIC"
    # HYBRID requested + unconfigured router -> honest deterministic fallback
    from runtime.model_agent_support import run_model_enhancement
    out = run_model_enhancement(
        agent_id="researcher", agent_role="researcher", agent_capabilities=["filesystem.read"],
        objective="Analyze", task_name="R", task_type="research",
        observed_evidence=[], strategy_value="HYBRID", router=router)
    assert out["model_used"] is False
    assert out["strategy"]["effective"] == "DETERMINISTIC"
    assert out["fallback_reason"] != ""
    # should_use_model is False for DETERMINISTIC even with a router
    assert should_use_model(resolve_strategy("DETERMINISTIC"), router) is False


# ---------------------------------------------------------------------------
# 14. Secret redaction
# ---------------------------------------------------------------------------

def test_14_secret_redaction(tmp_path, monkeypatch):
    secret = "sk-test-P8-SECRET-abc123xyz"
    monkeypatch.setenv("NEXUS_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("NEXUS_MODEL_API_KEY", secret)
    router = ModelRouter()
    for blob in (json.dumps(router.redacted_status()),
                 json.dumps(router.health()),
                 json.dumps(router.model_config().redacted_dict()),
                 json.dumps(redact_for_log({"api_key": secret, "nested": {"token": secret}})),
                 json.dumps(redact_for_log({"note": f"api_key={secret} bearer {secret}"}))):
        assert secret not in blob, "API key leaked into observable metadata"
    # Persisted model/strategy events from a real run carry no secrets either
    tmpdir = str(tmp_path)
    stack = _stack(tmpdir, router=router)
    repo = _make_repo(tmpdir)
    result = _run_acceptance(stack, repo)
    events = stack["db"].list_workflow_events(TENANT, result["workflow_id"], limit=500)
    assert len(events) > 0
    assert secret not in json.dumps(events)


# ---------------------------------------------------------------------------
# 16. Executor injects the shared router (Phase 8 fix proof)
# ---------------------------------------------------------------------------

def test_16_executor_injects_shared_router(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_MODEL_PROVIDER", "scripted-harness")
    monkeypatch.setenv("NEXUS_MODEL_API_KEY", "harness-key")
    fake = ScriptedProvider(script=[{"text": '{"insight": "harness reasoning"}'}])
    router = ModelRouter(providers=[fake], allow_mock_fallback=False)
    router.set_task_policy("agent_reasoning", ["scripted-harness"])
    router.set_task_policy("planning", ["scripted-harness"])
    assert router.is_configured() is True
    tmpdir = str(tmp_path)
    stack = _stack(tmpdir, router=router, strategy="HYBRID")
    repo = _make_repo(tmpdir)
    for _aid, _reg in list(stack["registry"]._agents.items()):
        try:
            _reg.instance.execution_strategy = "HYBRID"
            _reg.instance.model_router = None  # prove the executor injects it
        except Exception:
            pass
    planned = stack["planner"].plan(objective="Analyze this repository for architecture.",
                                    scope=repo, tenant_id=TENANT, execution_mode="REAL_READ",
                                    project_id=PROJECT)
    wf = stack["engine"].create_workflow(TENANT, PROJECT,
                                         stack["planner"].plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    stack["engine"].start_workflow(TENANT, PROJECT, wid)
    state = stack["worker"].execute_workflow(TENANT, PROJECT, wid, max_ticks=120, poll_interval=0.02)
    assert state["status"] == "COMPLETED"
    events = stack["db"].list_workflow_events(TENANT, wid, limit=500)
    invocations = [e for e in events if e.get("event_type") == "model_invocation"]
    # The shared router reached the agents: at least one real model invocation persisted
    assert len(invocations) >= 1, "executor must inject the shared router so agents invoke the model"
    assert all(e.get("detail", {}).get("provider") == "scripted-harness" for e in invocations)
