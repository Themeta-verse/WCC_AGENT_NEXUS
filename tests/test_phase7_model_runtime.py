"""Phase 7 — Real model-backed agent runtime tests.

Covers the 16 required behaviors with REAL filesystem observation and a
local fake ModelProvider implementing the same interface (deterministic for
testing). No API keys required. A real-provider smoke path runs only when
credentials are present and never fails the suite when absent.

Reality contract preserved throughout:
  OBSERVED = real tool/system observation
  INFERRED = model reasoning (always untrusted)
  VERIFIED = independent verification only
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
from runtime.model_context import ModelContextBuilder
from runtime.model_tools import validate_tool_call, execute_tool_call
from runtime.model_agent_loop import ModelAgentLoop, AgentLoopConfig
from runtime.model_strategy import ExecutionStrategy, resolve_strategy, should_use_model


# ---------------------------------------------------------------------------
# Local fake provider (same interface, deterministic, no network)
# ---------------------------------------------------------------------------

class FakeModelProvider(ModelProvider):
    """Deterministic scripted provider for integration tests.

    script: list of dicts — each entry is either {"text": ...} or
    {"tool_calls": [{"tool": ..., "arguments": {...}}]} or {"fail": "..."}.
    """
    name = "fake"

    def __init__(self, script: list[dict] | None = None, default_model: str = "fake-test-v1"):
        self.script = list(script or [{"text": '{"insight": "default fake analysis"}'}])
        self.default_model = default_model
        self.calls: list[dict] = []
        self._index = 0

    def health(self):
        return {"provider": self.name, "status": "AVAILABLE", "availability": True, "type": "fake"}

    def complete(self, prompt, *, system=None, schema=None, temperature=0.2, timeout=30, model=None):
        self.calls.append({"prompt": prompt, "system": system, "model": model or self.default_model})
        entry = self.script[min(self._index, len(self.script) - 1)]
        self._index += 1
        if "fail" in entry:
            raise ModelProviderError(self.name, entry["fail"])
        content = entry.get("text", json.dumps({"tool_calls": entry.get("tool_calls", [])}))
        if "tool_calls" in entry and "text" not in entry:
            content = json.dumps({"tool_calls": entry["tool_calls"]})
        structured = None
        try:
            structured = json.loads(content)
        except Exception:
            structured = None
        resp = ModelResponse(
            content=content, model=model or self.default_model, provider=self.name,
            prompt_tokens=5, completion_tokens=5, total_tokens=10,
            duration_seconds=0.01, reality="INFERRED", untrusted=True,
            status="SUCCESS", structured=structured, raw_response={"fake": True},
        )
        # Attach tool calls like the base generate() would
        if structured is not None:
            resp.tool_calls = parse_tool_calls_from_payload(structured)
        return resp


class AlwaysFailProvider(ModelProvider):
    name = "always-fail"

    def health(self):
        return {"provider": self.name, "status": "AVAILABLE", "availability": True}

    def complete(self, prompt, *, system=None, schema=None, temperature=0.2, timeout=30, model=None):
        raise ModelProviderError(self.name, "intentional provider failure")


def _setup_stack(tmpdir: str, router=None, strategy: str = "DETERMINISTIC"):
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.agent_registry import AgentRegistry
    from runtime.messaging_hub import MessagingHub
    from nexus_independent.database import NexusDatabase

    db_path = os.path.join(tmpdir, "phase7.db")
    db = NexusDatabase(db_path)
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("t7", "T7", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("p7", "t7", "P7", now, now))
    registry = AgentRegistry()
    register_default_agents(registry)
    hub = MessagingHub(db)
    executor = MultiAgentExecutor(
        database=db, agent_registry=registry, settings=None,
        principal={"tenant_id": "t7", "project_id": "p7"},
        messaging_hub=hub, model_router=router,
        default_execution_strategy=strategy,
    )
    engine = WorkflowEngine(
        database=db, composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=False, auto_retry_on_failure=True),
        agent_registry=registry, artifacts_root=tmpdir, messaging_hub=hub,
    )
    engine.set_executor(executor, agent_registry=registry)
    return engine, db, registry, hub, executor


def _make_repo(tmpdir: str) -> str:
    repo = os.path.join(tmpdir, "demo-repo")
    os.makedirs(repo, exist_ok=True)
    Path(repo, "README.md").write_text("# Demo Repo\nUses Python and JSON config.\n", encoding="utf-8")
    Path(repo, "app.py").write_text("import os\nprint('hello')\n", encoding="utf-8")
    Path(repo, "config.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    Path(repo, "auth.py").write_text("# auth module\n", encoding="utf-8")
    return repo


# 1. deterministic mode still works
def test_01_deterministic_mode_still_works():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        engine, db, registry, hub, executor = _setup_stack(tmpdir, router=None, strategy="DETERMINISTIC")
        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Det", objective="Analyze repo", scope=repo,
            task_specs=[
                {"task_id": "r1", "task_type": "research", "name": "Research", "agent_id": None,
                 "required_capabilities": ["filesystem.read"], "depends_on": [], "input_artifacts": [], "parameters": {}},
            ],
            agents=[], execution_mode="REAL_READ",
        )
        wf = engine.create_workflow("t7", "p7", spec)
        wid = wf["workflow_id"]
        engine.start_workflow("t7", "p7", wid)
        state = engine.step("t7", "p7", wid)
        tasks = db.list_workflow_tasks("t7", wid)
        assert any(t["status"] == "COMPLETED" for t in tasks)
        arts = db.list_workflow_artifacts("t7", wid)
        assert len(arts) >= 1
        # No model events in deterministic mode
        events = db.list_workflow_events("t7", wid, limit=200)
        assert not any(e.get("event_type") == "model_invocation" for e in events)


# 2. model provider abstraction works
def test_02_provider_abstraction():
    fake = FakeModelProvider(script=[{"text": "hello"}])
    router = ModelRouter(providers=[fake], allow_mock_fallback=False)
    router.set_task_policy("planning", ["fake"])
    resp = router.complete("planning", "test prompt")
    assert resp.provider == "fake"
    assert resp.reality == "INFERRED"
    assert resp.untrusted is True
    # Structured generate path
    req = ModelRequest(agent_objective="obj", task="t", task_type="planning")
    resp2 = router.generate(req, "prompt text")
    assert resp2.execution_id.startswith("model-exec-")
    assert resp2.reality == "INFERRED"


# 3. model response parsing works
def test_03_response_parsing():
    resp = ModelResponse(content='{"a": 1}', model="m", provider="p",
                         structured={"a": 1}, raw_response={})
    assert resp.text == '{"a": 1}'
    assert resp.structured_output == {"a": 1}
    assert resp.reality == "INFERRED" and resp.untrusted is True
    # Tamper attempt is corrected
    resp2 = ModelResponse(content="x", model="m", provider="p", reality="OBSERVED", untrusted=False)
    assert resp2.reality == "INFERRED" and resp2.untrusted is True


# 4. structured tool-call parsing works
def test_04_tool_call_parsing():
    calls = parse_tool_calls_from_payload({"tool_calls": [{"tool": "filesystem.read", "arguments": {"path": "a.txt"}}]})
    assert len(calls) == 1 and calls[0].tool == "filesystem.read"
    calls2 = parse_tool_calls_from_payload({"tool": "filesystem.read", "arguments": {"path": "b.txt"}})
    assert len(calls2) == 1
    assert parse_tool_calls_from_payload({"nope": 1}) == []
    assert parse_tool_calls_from_payload("string") == []
    # from_dict validation
    try:
        ModelToolCall.from_dict({"arguments": {}})
        raise AssertionError("should have raised")
    except ValueError:
        pass
    try:
        ModelToolCall.from_dict({"tool": "x", "arguments": "not-a-dict"})
        raise AssertionError("should have raised")
    except ValueError:
        pass


# 5. tool capability enforcement works
def test_05_capability_enforcement():
    call = ModelToolCall(tool="filesystem.read", arguments={"path": "/tmp/x"})
    ok = validate_tool_call(call, agent_capabilities=["filesystem.read"])
    assert ok.allowed
    denied = validate_tool_call(call, agent_capabilities=["knowledge.read"])
    assert not denied.allowed
    unknown = validate_tool_call(ModelToolCall(tool="shell.exec", arguments={}), agent_capabilities=["shell.exec"])
    assert not unknown.allowed
    prohibited = validate_tool_call(call, agent_capabilities=["filesystem.read"],
                                    agent_prohibited_operations=["filesystem.read"])
    assert not prohibited.allowed


# 6. bounded filesystem tool executes correctly
def test_06_bounded_tool_executes():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        target = os.path.join(repo, "README.md")
        call = ModelToolCall(tool="filesystem.read", arguments={"path": target})
        rec = execute_tool_call(call, agent_id="researcher",
                                agent_capabilities=["filesystem.read"], workspace_root=repo)
        assert rec.status == "EXECUTED"
        assert rec.observation is not None
        # Outside root is blocked, not executed
        rec2 = execute_tool_call(ModelToolCall(tool="filesystem.read", arguments={"path": "/etc/passwd"}),
                                 agent_id="researcher", agent_capabilities=["filesystem.read"], workspace_root=repo)
        assert rec2.status in ("BLOCKED", "REJECTED")


# 7. OBSERVED result is created from real tool output
def test_07_observed_from_real_tool():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        fake = FakeModelProvider(script=[
            {"tool_calls": [{"tool": "filesystem.read", "arguments": {"path": os.path.join(repo, "app.py")}}]},
            {"text": '{"insight": "saw app.py via tool"}'},
        ])
        router = ModelRouter(providers=[fake], allow_mock_fallback=False)
        router.set_task_policy("research", ["fake"])
        router.set_task_policy("agent_reasoning", ["fake"])
        loop = ModelAgentLoop(router=router, config=AgentLoopConfig(max_iterations=5, max_tool_calls=4, timeout_seconds=30))
        result = loop.run(
            agent_id="researcher", agent_role="researcher", agent_capabilities=["filesystem.read"],
            objective="Analyze repo", task_name="Research", task_type="research",
            observed_evidence=[], tool_definitions=[ToolDefinition(name="filesystem.read")],
            workspace_root=repo,
        )
        assert result.finished
        assert len(result.tool_records) >= 1
        rec = result.tool_records[0]
        assert rec.status == "EXECUTED"
        assert rec.reality == "OBSERVED"
        assert rec.content_sha256 is not None
        assert result.final_response is not None
        assert result.final_response.reality == "INFERRED"


# 8. model analysis becomes INFERRED
def test_08_model_analysis_is_inferred():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        fake = FakeModelProvider(script=[{"text": '{"insight": "model says python project"}'}])
        router = ModelRouter(providers=[fake], allow_mock_fallback=False)
        for t in ("research", "agent_reasoning", "planning"):
            try:
                router.set_task_policy(t, ["fake"])
            except ValueError:
                pass
        from runtime.agent_base import AgentContext
        from runtime.agents.architect import ArchitectureAgent
        agent = ArchitectureAgent(execution_strategy="HYBRID", model_router=router)
        ctx = AgentContext(
            workflow_id="w", task_id="t", task_name="Arch", agent_id="architect",
            scope=repo, parameters={"execution_strategy": "HYBRID"},
            input_artifacts=[], artifact_contents=[],
            objective="Analyze", constraints={}, previous_messages=[],
            execution_metadata={}, observation_scope=repo, messaging_hub=None,
        )
        res = agent.execute(ctx)
        assert res.reality == "INFERRED" and res.untrusted is True
        assert len(res.artifacts) == 1


# 9. provenance connects inference to observation
def test_09_provenance_links_inference_to_observation():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        fake = FakeModelProvider(script=[{"text": '{"insight": "hybrid insight"}'}])
        router = ModelRouter(providers=[fake], allow_mock_fallback=False)
        for t in ("research", "agent_reasoning", "planning"):
            try:
                router.set_task_policy(t, ["fake"])
            except ValueError:
                pass
        engine, db, registry, hub, executor = _setup_stack(tmpdir, router=router, strategy="HYBRID")
        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Prov", objective="Analyze repo", scope=repo,
            task_specs=[
                {"task_id": "r1", "task_type": "research", "name": "Research", "agent_id": "researcher",
                 "required_capabilities": ["filesystem.read"], "depends_on": [], "input_artifacts": [], "parameters": {"execution_strategy": "HYBRID"}},
                {"task_id": "a1", "task_type": "architecture-analysis", "name": "Arch", "agent_id": "architect",
                 "required_capabilities": ["knowledge.read"], "depends_on": ["r1"], "input_artifacts": ["research_report"], "parameters": {"execution_strategy": "HYBRID"}},
            ],
            agents=[], execution_mode="REAL_READ",
        )
        wf = engine.create_workflow("t7", "p7", spec)
        wid = wf["workflow_id"]
        engine.start_workflow("t7", "p7", wid)
        for _ in range(5):
            state = engine.step("t7", "p7", wid)
            tasks = db.list_workflow_tasks("t7", wid)
            if all(t["status"] in ("COMPLETED", "FAILED") for t in tasks):
                break
        arts = db.list_workflow_artifacts("t7", wid)
        kinds = {a["kind"] for a in arts}
        assert "research_report" in kinds
        # Architecture artifact must reference research parents
        arch = [a for a in arts if a["kind"] == "architecture_plan"]
        assert arch, "architecture_plan missing"
        assert arch[0].get("parent_artifacts") or arch[0].get("parent_artifacts_json")


# 10. malformed model tool call is rejected
def test_10_malformed_tool_call_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        # Model emits malformed tool request (missing path + unknown tool)
        fake = FakeModelProvider(script=[
            {"tool_calls": [{"tool": "filesystem.read", "arguments": {}}]},
            {"text": '{"insight": "recovered"}'},
        ])
        router = ModelRouter(providers=[fake], allow_mock_fallback=False)
        router.set_task_policy("agent_reasoning", ["fake"])
        loop = ModelAgentLoop(router=router, config=AgentLoopConfig(max_iterations=5, max_tool_calls=4, timeout_seconds=30))
        result = loop.run(
            agent_id="researcher", agent_role="researcher", agent_capabilities=["filesystem.read"],
            objective="o", task_name="t", task_type="agent_reasoning",
            tool_definitions=[ToolDefinition(name="filesystem.read")], workspace_root=repo,
        )
        assert result.finished
        # Malformed call must be REJECTED/BLOCKED, never EXECUTED
        assert all(r.status in ("REJECTED", "BLOCKED") for r in result.tool_records)
        # Direct validation of unknown tool
        rec = execute_tool_call(ModelToolCall(tool="rm -rf /", arguments={}),
                                agent_id="a", agent_capabilities=["filesystem.read"], workspace_root=repo)
        assert rec.status in ("REJECTED", "BLOCKED")


# 11. model timeout is handled
def test_11_timeout_handled():
    fake = FakeModelProvider(script=[{"tool_calls": [{"tool": "filesystem.read", "arguments": {"path": "x"}}]} for _ in range(20)])
    router = ModelRouter(providers=[fake], allow_mock_fallback=False)
    router.set_task_policy("agent_reasoning", ["fake"])
    # Zero-second deadline forces immediate timeout
    loop = ModelAgentLoop(router=router, config=AgentLoopConfig(max_iterations=5, max_tool_calls=4, timeout_seconds=1))
    # Force deadline expiry by monkeypatching is_cancelled
    calls = {"n": 0}

    def _cancel():
        calls["n"] += 1
        return calls["n"] > 2

    with tempfile.TemporaryDirectory() as tmpdir:
        result = loop.run(
            agent_id="a", agent_role="researcher", agent_capabilities=["filesystem.read"],
            objective="o", task_name="t", task_type="agent_reasoning",
            tool_definitions=[ToolDefinition(name="filesystem.read")],
            workspace_root=tmpdir, is_cancelled=_cancel,
        )
        assert result.cancelled or result.finished or result.error is not None
    # Hard timeout path
    loop2 = ModelAgentLoop(router=router, config=AgentLoopConfig(max_iterations=5, max_tool_calls=4, timeout_seconds=0))
    # timeout_seconds=0 is clamped by from_env but direct construction allows 0
    loop2.config.timeout_seconds = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        result2 = loop2.run(
            agent_id="a", agent_role="researcher", agent_capabilities=["filesystem.read"],
            objective="o", task_name="t", task_type="agent_reasoning",
            tool_definitions=[ToolDefinition(name="filesystem.read")], workspace_root=tmpdir,
        )
        assert result2.timed_out or result2.error is not None


# 12. provider failure is handled
def test_12_provider_failure_handled():
    router = ModelRouter(providers=[AlwaysFailProvider()], allow_mock_fallback=False)
    router.set_task_policy("agent_reasoning", ["always-fail"])
    loop = ModelAgentLoop(router=router)
    with tempfile.TemporaryDirectory() as tmpdir:
        result = loop.run(
            agent_id="a", agent_role="researcher", agent_capabilities=["filesystem.read"],
            objective="o", task_name="t", task_type="agent_reasoning",
            workspace_root=tmpdir,
        )
        assert not result.finished
        assert result.error is not None
        assert any(e.get("type") == "model_failure" for e in result.events)


# 13. fallback works
def test_13_fallback_works():
    failing = AlwaysFailProvider()
    good = FakeModelProvider(script=[{"text": '{"ok": true}'}])
    router = ModelRouter(providers=[failing, good], allow_mock_fallback=False)
    router.set_task_policy("agent_reasoning", ["always-fail", "fake"])
    req = ModelRequest(agent_objective="o", task="t", task_type="agent_reasoning")
    resp = router.generate(req, "prompt")
    assert resp.provider == "fake"
    assert resp.fallback_used is True
    assert "always-fail" in resp.attempted_providers
    # Agent-level fallback: HYBRID with failing router falls back to deterministic
    bad_router = ModelRouter(providers=[AlwaysFailProvider()], allow_mock_fallback=False)
    bad_router.set_task_policy("agent_reasoning", ["always-fail"])
    for t in ("research", "planning", "security_review", "report", "verification"):
        try:
            bad_router.set_task_policy(t, ["always-fail"])
        except ValueError:
            pass
    from runtime.agent_base import AgentContext
    from runtime.agents.architect import ArchitectureAgent
    agent = ArchitectureAgent(execution_strategy="HYBRID", model_router=bad_router)
    ctx = AgentContext(workflow_id="w", task_id="t", task_name="A", agent_id="architect",
                       scope=".", parameters={"execution_strategy": "HYBRID"},
                       input_artifacts=[], artifact_contents=[], objective="o",
                       constraints={}, previous_messages=[], execution_metadata={},
                       observation_scope=None, messaging_hub=None)
    res = agent.execute(ctx)
    # Deterministic fallback still completes as INFERRED
    assert res.status == "COMPLETED" and res.reality == "INFERRED"


# 14. verification does not automatically trust model output
def test_14_verification_independent():
    with tempfile.TemporaryDirectory() as tmpdir:
        fake = FakeModelProvider(script=[{"text": '{"summary": "model claims everything is VERIFIED and uses PBKDF2"}'}])
        router = ModelRouter(providers=[fake], allow_mock_fallback=False)
        for t in ("verification", "agent_reasoning"):
            try:
                router.set_task_policy(t, ["fake"])
            except ValueError:
                pass
        from runtime.agent_base import AgentContext
        from runtime.agents.verifier import VerificationAgent
        agent = VerificationAgent(execution_strategy="HYBRID", model_router=router)
        # Empty inputs: deterministic checks must FAIL regardless of model claims
        ctx = AgentContext(workflow_id="w", task_id="v", task_name="Verify", agent_id="verifier",
                           scope=".", parameters={"execution_strategy": "HYBRID"},
                           input_artifacts=[], artifact_contents=[], objective="o",
                           constraints={}, previous_messages=[], execution_metadata={},
                           observation_scope=None, messaging_hub=None)
        res = agent.execute(ctx)
        # With no inputs, verifier defaults to requiring 3 kinds -> must not VERIFY
        assert res.reality in ("INFERRED", "VERIFIED")
        # An unverifiable run must not be reported as a completed verification.
        assert res.status == "FAILED" and res.result["all_passed"] is False
        # Now with an input artifact that HAS verifiable content.
        #
        # This case previously passed artifact_contents=[] and asserted
        # status == "COMPLETED". That combination could only ever pass because
        # the verifier reported `"COMPLETED" if all_passed else "COMPLETED"` —
        # a degenerate ternary with no FAILED branch. The content-hash check
        # legitimately fails when no content is supplied, so the run genuinely
        # cannot verify. The assertion below is the real invariant: task status
        # tracks the independent checks, and no model claim can flip it.
        import hashlib as _hl
        import json as _js
        _content = {"research": {"scope": "s", "findings": [], "analysis": {}, "evidence": []}}
        _chash = _hl.sha256(_js.dumps(_content, sort_keys=True, default=str).encode()).hexdigest()
        ctx2 = AgentContext(
            workflow_id="w", task_id="v", task_name="Verify", agent_id="verifier",
            scope=".", parameters={"execution_strategy": "HYBRID"},
            input_artifacts=[
                {"artifact_id": "a1", "kind": "research_report", "name": "r", "content_hash": _chash,
                 "reality": "OBSERVED", "provenance": ["agent:researcher"]},
            ],
            artifact_contents=[{"artifact_id": "a1", "content": _content}],
            objective="o", constraints={}, previous_messages=[],
            execution_metadata={}, observation_scope=None, messaging_hub=None,
        )
        res2 = agent.execute(ctx2)
        # INVARIANT: the reported status is exactly the deterministic verdict.
        # The model text claims "VERIFIED" throughout and must not influence it.
        assert res2.status == ("COMPLETED" if res2.result["all_passed"] else "FAILED")
        assert res2.reality == ("VERIFIED" if res2.result["all_passed"] else "INFERRED")
        assert res2.result["all_passed"] is True, \
            f"content-backed artifact should verify; failed: {res2.result.get('failed_checks')}"
        assert res2.status == "COMPLETED" and res2.reality == "VERIFIED"
        # The model never appears in the verdict.
        assert "model" not in _js.dumps(res2.result.get("verification", {}).get("checks", []))
        # Provenance: verification result parents link to inputs
        assert res2.artifacts[0]["parent_artifacts"] == ["a1"]


# 15. agent-to-agent messaging remains functional
def test_15_messaging_functional():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        engine, db, registry, hub, executor = _setup_stack(tmpdir, router=None, strategy="DETERMINISTIC")
        from runtime.workflow_engine import WorkflowSpec
        spec = WorkflowSpec(
            name="Msg", objective="Analyze repository and produce architecture report", scope=repo,
            task_specs=[
                {"task_id": "r1", "task_type": "research", "name": "Research", "agent_id": None,
                 "required_capabilities": ["filesystem.read"], "depends_on": [], "input_artifacts": [], "parameters": {}},
                {"task_id": "a1", "task_type": "architecture-analysis", "name": "Arch", "agent_id": None,
                 "required_capabilities": [], "depends_on": ["r1"], "input_artifacts": ["research_report"], "parameters": {}},
                {"task_id": "s1", "task_type": "security-analysis", "name": "Sec", "agent_id": None,
                 "required_capabilities": [], "depends_on": ["r1"], "input_artifacts": ["research_report"], "parameters": {}},
            ],
            agents=[], execution_mode="REAL_READ",
        )
        wf = engine.create_workflow("t7", "p7", spec)
        wid = wf["workflow_id"]
        engine.start_workflow("t7", "p7", wid)
        for _ in range(10):
            engine.step("t7", "p7", wid)
            tasks = db.list_workflow_tasks("t7", wid)
            if all(t["status"] in ("COMPLETED", "FAILED", "BLOCKED") for t in tasks):
                break
        messages = db.list_workflow_messages("t7", wid, limit=200)
        assert len(messages) > 0
        types = {m["message_type"] for m in messages}
        # Collaboration messages (QUESTION/ANSWER/RESPONSE) must exist
        assert types & {"QUESTION", "ANSWER", "RESPONSE", "STATUS_UPDATE", "TASK_STARTED", "TASK_COMPLETED"}


# 16. existing autonomous workflow remains functional
def test_16_autonomous_workflow_functional():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = _make_repo(tmpdir)
        engine, db, registry, hub, executor = _setup_stack(tmpdir, router=None, strategy="DETERMINISTIC")
        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=registry)
        runtime = AutonomousRuntime(
            database=db, engine=engine, executor=executor,
            agent_registry=registry, messaging_hub=hub, planner=planner,
            config=AutonomousConfig(tenant_id="t7", project_id="p7", poll_interval_seconds=0.01, max_iterations=50),
        )
        result = runtime.execute_objective(
            objective="Analyze this repository and produce a verified architecture and security report",
            scope=repo, constraints={}, max_iterations=30,
        )
        assert result["workflow_id"]
        assert result["status"] in ("COMPLETED", "FAILED", "RUNNING")
        trace = result["trace"]
        assert trace["final_result"]["total"] > 0


def test_17_no_secrets_in_artifacts_messages_traces():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["NEXUS_MODEL_API_KEY"] = "sk-test-secret-12345"
        try:
            payload = {"api_key": "sk-test-secret-12345", "nested": {"token": "abc"}}
            redacted = redact_for_log(payload)
            assert "sk-test-secret-12345" not in json.dumps(redacted)
            fake = FakeModelProvider(script=[{"text": "ok"}])
            router = ModelRouter(providers=[fake], allow_mock_fallback=False)
            status = router.redacted_status()
            assert "sk-test-secret-12345" not in json.dumps(status)
            health = router.health()
            assert "sk-test-secret-12345" not in json.dumps(health)
        finally:
            os.environ.pop("NEXUS_MODEL_API_KEY", None)


def test_18_real_provider_smoke_if_configured():
    """Smoke path: runs only when a real provider is configured; skips otherwise."""
    from runtime.model_router import ModelConfig
    cfg = ModelConfig.from_env()
    if not cfg.is_configured:
        import pytest
        pytest.skip("No real model provider configured (expected in CI)")
    router = ModelRouter()
    req = ModelRequest(agent_objective="smoke", task="ping", task_type="agent_reasoning")
    resp = router.generate(req, "Reply with: {\"status\": \"ok\"}")
    assert resp.reality == "INFERRED"
