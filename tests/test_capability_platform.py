"""Phase-14 platform tests: negotiation, multi-provider, policy, retry lineage,
freshness, discovery, audit events, adapter plugability, model boundary,
registry identity, provider-leakage scan.

Deterministic, offline, token-free. Fakes only at the connector boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.capability_fabric import (
    CapabilityPolicy,
    CapabilityRequest,
    CapabilityResolutionError,
    PolicyDecision,
    execute_capability,
    execute_with_retry,
    resolve_capability_detailed,
    resolve_connector,
    verify_capability_response,
)
from runtime.connector_registry import ConnectorRegistry


class _PlatConnector:
    def __init__(self, connector_id="p1", provider="pa", capability="plat.cap",
                 auth_state="CONNECTED", payload=None, status="SUCCESS", fail_times=0,
                 research_profile="generic"):
        self.connector_id = connector_id
        self.provider = provider
        self.version = "3.1.4"
        self.capabilities = {capability: {"risk": "LOW"}}
        self.auth_state = auth_state
        # Contract field: which research path this connector feeds.
        self.research_profile = research_profile
        self._payload = {"v": connector_id} if payload is None else payload
        self._status = status
        self._fail_times = fail_times
        self.calls = 0
        self.last_validated_at = "2026-01-01T00:00:00+00:00" if auth_state == "CONNECTED" else ""

    def health(self):
        return {"status": self.auth_state, "authentication": "PLAT"}

    def execute(self, operation, input_data):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError(f"attempt {self.calls} fails")
        rh = hashlib.sha256(json.dumps(self._payload, sort_keys=True).encode()).hexdigest()
        return {"status": self._status, "data": dict(self._payload),
                "receipt": {
                    "receipt_id": f"receipt-{self.connector_id}-{self.calls}",
                    "connector_id": self.connector_id, "provider": self.provider,
                    "operation": operation, "capability": operation,
                    "target": input_data.get("target", "t"), "status": self._status,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:00:01+00:00",
                    "duration_seconds": 1.0, "result_hash": rh,
                    "input_digest": hashlib.sha256(b"i").hexdigest(),
                    "authentication": "PLAT_ACTIVE", "error": None}}

    def revoke(self):
        self.auth_state = "REVOKED"

    def refresh(self):
        return {"auth_state": self.auth_state}

    def metadata(self):
        return {"connector_id": self.connector_id}


def _req(capability="plat.cap", **kw):
    args = {"capability": capability, "input": {"target": "t"}, "scope": "t",
            "task_id": "task-1", "agent_id": "agent-1"}
    args.update(kw)
    return CapabilityRequest(**args)


# ---------------------------------------------------------------------------
# S4+S5: negotiation + multi-provider selection
# ---------------------------------------------------------------------------

def test_plat_resolution_record_shape():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector("c1", "pa"))
    reg.register(_PlatConnector("c2", "pb", auth_state="CONFIGURED"))
    reg.register(_PlatConnector("c3", "pc", auth_state="EXPIRED"))
    res = resolve_capability_detailed(reg, "plat.cap")
    assert res.status == "RESOLVED"
    assert res.connector_id == "c1" and res.provider == "pa"
    assert res.connector_state == "CONNECTED" and res.auth_usable is True
    assert "CONNECTED" in res.selection_reason
    by_id = {a.connector_id: a for a in res.alternatives}
    assert set(by_id) == {"c1", "c2", "c3"}
    assert by_id["c1"].usable and by_id["c2"].usable and not by_id["c3"].usable
    d = res.to_dict()
    assert d["capability"] == "plat.cap" and len(d["alternatives"]) == 3


def test_plat_configured_selected_only_without_connected():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector("c2", auth_state="CONFIGURED"))
    res = resolve_capability_detailed(reg, "plat.cap")
    assert res.status == "RESOLVED" and res.connector_id == "c2"
    assert "CONFIGURED" in res.selection_reason
    assert "equivalent" not in res.selection_reason.lower()


def test_plat_resolution_failure_statuses():
    assert resolve_capability_detailed(ConnectorRegistry(), "plat.cap").status == "UNKNOWN_CAPABILITY"
    assert resolve_capability_detailed(None, "plat.cap").status == "NO_CONNECTOR"
    assert resolve_capability_detailed(ConnectorRegistry(), "../x").status == "INVALID_REQUEST"
    reg = ConnectorRegistry()
    reg.register(_PlatConnector(auth_state="REVOKED"))
    denied = resolve_capability_detailed(reg, "plat.cap")
    assert denied.status == "NOT_AUTHORIZED"
    assert denied.error_code == "NOT_AUTHORIZED"


def test_plat_resolve_connector_matches_detailed():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector("c1", auth_state="CONFIGURED"))
    reg.register(_PlatConnector("c2", auth_state="CONNECTED"))
    assert resolve_connector(reg, "plat.cap").connector_id == "c2"
    for bad_reg, code in ((ConnectorRegistry(), "UNKNOWN_CAPABILITY"),):
        try:
            resolve_connector(bad_reg, "plat.cap")
            raise AssertionError("should raise")
        except CapabilityResolutionError as exc:
            assert exc.code == code


def test_plat_provider_switch_is_explicit_never_silent():
    reg = ConnectorRegistry()
    a = _PlatConnector("a", "provider-a")
    b = _PlatConnector("b", "provider-b")
    reg.register(a)
    reg.register(b)
    first = execute_capability(reg, _req())
    assert (first.connector_id, first.provider) == ("a", "provider-a")
    assert a.calls == 1 and b.calls == 0
    reg.unregister("a")
    second = execute_capability(reg, _req())
    assert (second.connector_id, second.provider) == ("b", "provider-b")
    assert reg.get_connector("a") is None  # removal is explicit, not silent


def test_plat_failed_selected_provider_does_not_silently_switch():
    reg = ConnectorRegistry()
    a = _PlatConnector("a", "provider-a", status="FAILED")
    b = _PlatConnector("b", "provider-b")
    reg.register(a)
    reg.register(b)
    resp = execute_capability(reg, _req())
    assert resp.status == "FAILED" and resp.connector_id == "a"
    assert b.calls == 0  # no silent failover


# ---------------------------------------------------------------------------
# S13: policy boundary
# ---------------------------------------------------------------------------

class _DenyAll(CapabilityPolicy):
    policy_id = "deny-all-test"

    def decide(self, *, request, connector):
        return PolicyDecision("DENY", "test policy denies everything", self.policy_id)


class _ApproveAll(CapabilityPolicy):
    policy_id = "approve-test"

    def decide(self, *, request, connector):
        return PolicyDecision("REQUIRES_APPROVAL", "human must approve", self.policy_id)


def test_plat_policy_deny_executes_nothing():
    reg = ConnectorRegistry()
    conn = _PlatConnector()
    reg.register(conn)
    reg.policy = _DenyAll()
    resp = execute_capability(reg, _req())
    assert resp.status == "BLOCKED"
    assert resp.reality == "OBSERVED"  # the refusal itself is observed
    assert conn.calls == 0
    assert "DENY" in (resp.error or "")


def test_plat_policy_approval_is_not_success():
    reg = ConnectorRegistry()
    conn = _PlatConnector()
    reg.register(conn)
    reg.policy = _ApproveAll()
    resp = execute_capability(reg, _req())
    assert resp.status == "BLOCKED"
    assert resp.status != "SUCCESS"
    assert conn.calls == 0


def test_plat_policy_allow_executes():
    reg = ConnectorRegistry()
    conn = _PlatConnector()
    reg.register(conn)
    reg.policy = CapabilityPolicy()
    resp = execute_capability(reg, _req())
    assert resp.status == "SUCCESS" and conn.calls == 1


def test_plat_default_is_explicit_allow():
    reg = ConnectorRegistry()
    assert reg.policy is None
    conn = _PlatConnector()
    reg.register(conn)
    assert execute_capability(reg, _req()).status == "SUCCESS"


def test_plat_invalid_policy_decision_is_deny():
    class _Broken:
        policy_id = "broken"

        def decide(self, *, request, connector):
            return "yes, sure"

    reg = ConnectorRegistry()
    conn = _PlatConnector()
    reg.register(conn)
    reg.policy = _Broken()
    resp = execute_capability(reg, _req())
    assert resp.status == "BLOCKED" and conn.calls == 0


# ---------------------------------------------------------------------------
# S6: explicit retry with attempt lineage
# ---------------------------------------------------------------------------

def test_plat_retry_fail_then_success_lineage():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector(fail_times=1))
    resp, attempts = execute_with_retry(reg, _req(), max_attempts=3)
    assert resp.status == "SUCCESS"
    assert len(attempts) == 2
    assert [a.status for a in attempts] == ["FAILED", "SUCCESS"]
    assert attempts[0].parent_receipt_id == ""
    assert attempts[1].parent_receipt_id == attempts[0].receipt_id
    assert attempts[0].receipt_id != attempts[1].receipt_id
    assert resp.receipt["attempt"] == 2
    assert resp.receipt["parent_receipt_id"] == attempts[0].receipt_id
    # Only the successful attempt verifies.
    assert [c for c in verify_capability_response(resp) if c["status"] != "PASS"] == []


def test_plat_retry_exhaustion_keeps_every_attempt():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector(fail_times=9))
    resp, attempts = execute_with_retry(reg, _req(), max_attempts=3)
    assert resp.status == "FAILED"
    assert len(attempts) == 3
    assert len({a.receipt_id for a in attempts}) == 3


def test_plat_retry_stops_at_first_success():
    reg = ConnectorRegistry()
    conn = _PlatConnector()
    reg.register(conn)
    resp, attempts = execute_with_retry(reg, _req(), max_attempts=5)
    assert resp.status == "SUCCESS" and len(attempts) == 1 and conn.calls == 1


def test_plat_policy_refusal_is_not_retried():
    reg = ConnectorRegistry()
    conn = _PlatConnector()
    reg.register(conn)
    reg.policy = _DenyAll()
    resp, attempts = execute_with_retry(reg, _req(), max_attempts=3)
    assert resp.status == "BLOCKED" and len(attempts) == 1 and conn.calls == 0


def test_plat_engine_retry_produces_independent_lineage():
    import tempfile
    from runtime.agent_registry import AgentRegistry
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
    from nexus_independent.database import NexusDatabase

    class _FlakyGithub:
        connector_id = "github"
        provider = "github"
        version = "1.0.0"
        capabilities = {"github.repository.read": {"risk": "LOW", "scope_kind": "owner_repo"}}
        auth_state = "CONFIGURED"
        calls = 0

        def health(self):
            return {"status": self.auth_state}

        def execute(self, operation, input_data):
            type(self).calls += 1
            if type(self).calls == 1:
                raise RuntimeError("transient outage")
            payload = {"full_name": input_data.get("owner_repo"), "id": 1}
            rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            return {"status": "SUCCESS", "data": payload,
                    "receipt": {"receipt_id": f"receipt-github-retry-{type(self).calls}",
                                "connector_id": "github", "provider": "github",
                                "operation": operation, "capability": operation,
                                "target": input_data.get("owner_repo"), "status": "SUCCESS",
                                "started_at": "s", "completed_at": "e", "duration_seconds": 0.1,
                                "result_hash": rh, "input_digest": "d",
                                "authentication": "TOKEN_ACTIVE", "error": None}}

        def revoke(self):
            pass

        def refresh(self):
            return {}

    _FlakyGithub.calls = 0
    with tempfile.TemporaryDirectory() as tmp:
        db = NexusDatabase(os.path.join(tmp, "t.db"))
        db.migrate()
        import time as _t
        now = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
        with db.connect() as conn:
            conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                         ("t", "T", now))
            conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)",
                         ("p", "t", "P", now, now))
        registry = AgentRegistry()
        register_default_agents(registry)
        hub = MessagingHub(db)
        executor = MultiAgentExecutor(database=db, agent_registry=registry, settings=None,
                                      principal={"tenant_id": "t", "project_id": "p"},
                                      messaging_hub=hub)
        reg = ConnectorRegistry()
        reg.register(_FlakyGithub())
        executor.connector_registry = reg
        engine = WorkflowEngine(database=db, composer=MissionComposer(),
                                policy=WorkflowExecutionPolicy(max_retries_default=2),
                                agent_registry=registry, artifacts_root=tmp, messaging_hub=hub)
        engine.set_executor(executor, agent_registry=registry)
        spec = WorkflowSpec(
            name="retry", objective="Read o/r", scope="Themeta-verse/Nexus",
            task_specs=[{"task_id": "task-0", "name": "R", "task_type": "research",
                         "agent_id": "researcher",
                         "required_capabilities": ["github.repository.read"],
                         "depends_on": [], "input_artifacts": []}],
            agents=[{"agent_id": "researcher", "name": "R", "role": "researcher",
                     "capabilities": ["github.repository.read"],
                     "allowed_operations": ["read"], "prohibited_operations": [],
                     "scope": {}, "expected_behaviour": ""}])
        wf = engine.create_workflow("t", "p", spec)
        wid = wf["workflow_id"]
        engine.start_workflow("t", "p", wid)
        engine.step("t", "p", wid)
        state = engine.step("t", "p", wid)
        assert state["tasks"][0]["status"] == "COMPLETED"
        tools = [e for e in state["events"] if e.get("event_type") == "tool_used"]
        assert len(tools) == 2  # failed attempt + successful attempt
        assert tools[0]["detail"]["status"] == "FAILED"
        assert tools[1]["detail"]["status"] == "SUCCESS"
        assert tools[0]["detail"]["receipt_id"] != tools[1]["detail"]["receipt_id"]
        arts = [a for a in state["artifacts"] if a.get("task_id") == "task-0"]
        observed = [a for a in arts if a.get("reality") == "OBSERVED"]
        failed_diag = [a for a in arts if a.get("reality") == "UNKNOWN"]
        # Exactly one OBSERVED artifact (the successful attempt); the failed
        # attempt left only an UNKNOWN diagnostic, never evidence.
        assert len(observed) == 1
        assert len(failed_diag) == 1


# ---------------------------------------------------------------------------
# S7: freshness metadata (never auto-invalidation)
# ---------------------------------------------------------------------------

def test_plat_freshness_metadata_when_requested():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector())
    req = _req(constraints={"freshness_ttl_seconds": 3600})
    resp = execute_capability(reg, req)
    assert resp.receipt["observed_at"]
    assert resp.receipt["expires_at"]
    assert resp.receipt["freshness_policy"] == "ttl:3600s"


def test_plat_no_freshness_by_default_and_no_invalidation():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector())
    resp = execute_capability(reg, _req())
    assert resp.receipt["freshness_policy"] == "none"
    # Even a long-expired observation is not auto-invalidated by verification:
    # freshness is metadata for policy, not a verdict input.
    resp.receipt["expires_at"] = "2000-01-01T00:00:00+00:00"
    assert not [c for c in verify_capability_response(resp) if c["status"] != "PASS"]


# ---------------------------------------------------------------------------
# S11: discovery surface (secret-safe)
# ---------------------------------------------------------------------------

def test_plat_discovery_surface_shape():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector("c1", "pa"))
    reg.register(_PlatConnector("c2", "pb", auth_state="CONFIGURED"))
    surface = reg.describe_capabilities()
    assert set(surface) == {"plat.cap"}
    entry = surface["plat.cap"]
    assert entry["resolution_order"][0] == "c1"
    assert set(entry["selectable"]) == {"c1", "c2"}
    c1 = next(c for c in entry["connectors"] if c["connector_id"] == "c1")
    assert c1["provider"] == "pa" and c1["version"] == "3.1.4"
    assert c1["auth_usable"] is True and c1["last_validated_at"]


def test_plat_discovery_never_exposes_secrets():
    class _LeakyHealth(_PlatConnector):
        def health(self):
            return {"status": "CONNECTED", "debug_token": "ghp_fakesecret999",
                    "note": "Bearer ghp_fakesecret999"}

    reg = ConnectorRegistry()
    reg.register(_LeakyHealth())
    blob = json.dumps(reg.describe_capabilities())
    # Credential VALUES never appear; the offending key is retained with a
    # REDACTED marker as an auditable scrub trail (not silently dropped).
    assert "ghp_fakesecret999" not in blob
    assert "Bearer ghp_fakesecret999" not in blob
    assert "[REDACTED]" in blob


# ---------------------------------------------------------------------------
# S14: audit event chain
# ---------------------------------------------------------------------------

def test_plat_event_chain_success():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector())
    events = []
    reg.event_sink = events.append
    resp = execute_capability(reg, _req())
    kinds = [e["event"] for e in events]
    assert kinds == ["capability_requested", "capability_resolved",
                     "capability_execution_started", "capability_execution_completed"]
    by_kind = {e["event"]: e for e in events}
    assert by_kind["capability_resolved"]["connector_id"] == "p1"
    assert by_kind["capability_resolved"]["policy"] == "ALLOW"
    assert by_kind["capability_execution_completed"]["receipt_id"] == resp.receipt["receipt_id"]
    for e in events:
        assert e["task_id"] == "task-1" and e["agent_id"] == "agent-1"
        assert e["capability"] == "plat.cap"
    assert "ghp_" not in json.dumps(events)


def test_plat_event_chain_failure_and_deny():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector())
    events = []
    reg.event_sink = events.append
    import pytest
    with pytest.raises(CapabilityResolutionError):
        execute_capability(reg, _req("plat.missing"))
    assert [e["event"] for e in events] == ["capability_requested",
                                            "capability_resolution_failed"]
    events.clear()
    reg.policy = _DenyAll()
    execute_capability(reg, _req())
    assert [e["event"] for e in events] == ["capability_requested", "capability_resolved",
                                            "capability_execution_completed"]
    assert events[-1]["status"] == "BLOCKED"


def test_plat_broken_sink_never_breaks_execution():
    reg = ConnectorRegistry()
    reg.register(_PlatConnector())
    reg.event_sink = lambda e: (_ for _ in ()).throw(RuntimeError("sink down"))
    assert execute_capability(reg, _req()).status == "SUCCESS"


# ---------------------------------------------------------------------------
# S9: second adapter proves generic verifier architecture
# ---------------------------------------------------------------------------

def test_plat_second_adapter_plugs_in_without_core_edits():
    from runtime.capability_verifiers import (
        GenericCapabilityVerifier, register_verifier_adapter,
        unregister_verifier_adapter, verifier_adapters_for,
    )

    def _verify_telemetry(research, *, expected_scope, artifact_reality, is_synthesis=False):
        ok = (research.get("metric") == "cpu" and artifact_reality == "OBSERVED"
              and not is_synthesis)
        return [{"check": "telemetry_metric_present",
                 "status": "PASS" if ok else "FAIL",
                 "detail": "telemetry metric observed" if ok else "no telemetry metric"}]

    register_verifier_adapter(name="telemetry-test", markers=("telemetry.snapshot",),
                              verify=_verify_telemetry)
    try:
        assert [a["name"] for a in verifier_adapters_for(["telemetry.snapshot"])] == ["telemetry-test"]
        assert verifier_adapters_for(["filesystem.read"]) == []
        # Generic runs first, adapter second, on real fabric output.
        reg = ConnectorRegistry()
        reg.register(_PlatConnector("tm", "telemetry-mock", capability="telemetry.snapshot",
                                    payload={"metric": "cpu"}))
        from runtime.capability_fabric import CapabilityRequest as _CR
        resp = execute_capability(
            reg, _CR(capability="telemetry.snapshot", input={}, scope="host-a",
                     task_id="t", agent_id="a"))
        assert not [c for c in verify_capability_response(resp) if c["status"] != "PASS"]
    finally:
        assert unregister_verifier_adapter("telemetry-test") is True
        assert verifier_adapters_for(["telemetry.snapshot"]) == []


# ---------------------------------------------------------------------------
# S12: model-agent boundary
# ---------------------------------------------------------------------------

def test_plat_model_cannot_request_connector_capabilities():
    from runtime.model_router import ModelToolCall
    from runtime.model_tools import validate_tool_call
    verdict = validate_tool_call(
        ModelToolCall(tool="github.repository.read", arguments={"owner_repo": "o/r"}),
        agent_capabilities=["github.repository.read"],
        agent_allowed_operations=["read"])
    assert verdict.allowed is False


def test_plat_model_response_is_always_inferred_untrusted():
    from runtime.model_router import ModelResponse
    resp = ModelResponse(content="hi", model="m", provider="p", duration_seconds=0.1)
    assert resp.reality == "INFERRED" and resp.untrusted is True


def test_plat_model_modules_touch_no_connectors_or_tokens():
    import pathlib
    for name in ("model_agent_loop.py", "model_tools.py", "model_router.py"):
        text = (pathlib.Path(__file__).resolve().parents[1] / "runtime" / name).read_text()
        assert "ConnectorRegistry" not in text, name
        assert "NEXUS_GITHUB_TOKEN" not in text, name
        assert "capability_fabric" not in text, name


# ---------------------------------------------------------------------------
# S2: singleton identity across runtimes
# ---------------------------------------------------------------------------

def test_plat_two_runtimes_share_one_registry():
    import tempfile
    import runtime.github_provider as gp
    import runtime.capability_fabric as cf
    gp._github_connector = None
    gp._github_registry = None
    cf._capability_registry = None
    try:
        from runtime.autonomous_runtime import AutonomousConfig, AutonomousRuntime
        from runtime.agent_registry import AgentRegistry
        from runtime.messaging_hub import MessagingHub
        from runtime.mission_composer import MissionComposer
        from runtime.multi_agent_executor import MultiAgentExecutor
        from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
        from nexus_independent.database import NexusDatabase
        with tempfile.TemporaryDirectory() as tmp:
            db = NexusDatabase(os.path.join(tmp, "t.db"))
            db.migrate()
            agent_registry = AgentRegistry()
            hub = MessagingHub(db)
            mk = lambda: AutonomousRuntime(
                database=db,
                engine=WorkflowEngine(database=db, composer=MissionComposer(),
                                      policy=WorkflowExecutionPolicy(),
                                      agent_registry=agent_registry, artifacts_root=tmp,
                                      messaging_hub=hub),
                executor=MultiAgentExecutor(database=db, agent_registry=agent_registry,
                                            settings=None, principal={}, messaging_hub=hub),
                agent_registry=agent_registry, messaging_hub=hub,
                config=AutonomousConfig())
            rt1, rt2 = mk(), mk()
            assert rt1.connector_registry is rt2.connector_registry
            assert rt1.executor.connector_registry is rt2.executor.connector_registry
            assert rt1.connector_registry is cf.get_capability_registry()
    finally:
        gp._github_connector = None
        gp._github_registry = None
        cf._capability_registry = None


# ---------------------------------------------------------------------------
# S10: provider-leakage scan (generic layers import no providers)
# ---------------------------------------------------------------------------

def test_plat_no_provider_imports_in_generic_layers():
    import pathlib
    generic = ["capability_fabric.py", "connector_registry.py", "workflow_engine.py",
               "multi_agent_executor.py"]
    offenders = []
    for name in generic:
        lines = (pathlib.Path(__file__).resolve().parents[1] / "runtime" / name).read_text().splitlines()
        for i, line in enumerate(lines, 1):
            # Column-0 imports only: lazy function-level bootstrap imports
            # (documented, e.g. initialize_capability_registry) are allowed;
            # module-level provider coupling is not.
            if line.startswith(("import ", "from ")) and ("github_provider" in line or "git_connector" in line):
                offenders.append(f"{name}:{i}: {line.strip()}")
    assert offenders == []


def test_plat_connector_enforces_required_target_offline():
    # Missing target fails BEFORE any network I/O (ValueError, no HTTP).
    from runtime.github_provider import GITHUB_CAPABILITIES, GitHubConnector
    conn = GitHubConnector(token="dummy", connector_id="github", provider="github",
                           version="1.0.0", capabilities=dict(GITHUB_CAPABILITIES))
    reg = ConnectorRegistry()
    reg.register(conn)
    resp = execute_capability(
        reg, CapabilityRequest(capability="github.repository.read", input={},
                               scope="", task_id="t", agent_id="a"))
    assert resp.status == "FAILED"
    assert "owner_repo" in (resp.error or "")


def test_plat_artifact_carries_workflow_task_linkage():
    import tempfile
    from runtime.agent_registry import AgentRegistry
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
    from nexus_independent.database import NexusDatabase
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        with open(os.path.join(repo, "f.txt"), "w") as f:
            f.write("x")
        db = NexusDatabase(os.path.join(tmp, "t.db"))
        db.migrate()
        import time as _t
        now = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
        with db.connect() as conn:
            conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                         ("t", "T", now))
            conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)",
                         ("p", "t", "P", now, now))
        registry = AgentRegistry()
        register_default_agents(registry)
        hub = MessagingHub(db)
        executor = MultiAgentExecutor(database=db, agent_registry=registry, settings=None,
                                      principal={"tenant_id": "t", "project_id": "p"},
                                      messaging_hub=hub)
        engine = WorkflowEngine(database=db, composer=MissionComposer(),
                                policy=WorkflowExecutionPolicy(max_retries_default=0),
                                agent_registry=registry, artifacts_root=tmp, messaging_hub=hub)
        engine.set_executor(executor, agent_registry=registry)
        spec = WorkflowSpec(
            name="link", objective="R", scope=repo,
            task_specs=[{"task_id": "task-0", "name": "R", "task_type": "research",
                         "agent_id": "researcher",
                         "required_capabilities": ["filesystem.read"],
                         "depends_on": [], "input_artifacts": []}],
            agents=[{"agent_id": "researcher", "name": "R", "role": "researcher",
                     "capabilities": ["filesystem.read"], "allowed_operations": ["read"],
                     "prohibited_operations": [], "scope": {}, "expected_behaviour": ""}])
        wf = engine.create_workflow("t", "p", spec)
        wid = wf["workflow_id"]
        engine.start_workflow("t", "p", wid)
        state = engine.step("t", "p", wid)
        assert len(state["artifacts"]) == 1
        art = state["artifacts"][0]
        assert art["workflow_id"] == wid
        assert art["task_id"] == "task-0"
        assert art["agent_id"] == "researcher"
        assert art["provenance"] and art["content_hash"]


def test_plat_evidence_chain_derives_reality():
    from runtime.agents.reporter import ReportAgent
    agent = ReportAgent()
    observed_research = {
        "source": "GitHubConnector",
        "findings": [{"file": "github://o/r", "reality": "OBSERVED"}],
        "analysis": {}, "evidence": [{"type": "github_receipt"}],
    }
    chain = agent._build_evidence_chain(
        observed_research, {"based_on_findings": ["abc"]}, {"scanned_evidence": 1})
    by_src = {e["source"]: e for e in chain}
    assert by_src["github-connector"]["reality"] == "OBSERVED"
    # Architecture derivation over hash references is inference, not observation.
    assert by_src["research_findings"]["reality"] == "INFERRED"
    # Unobserved findings must not be labeled OBSERVED.
    chain2 = agent._build_evidence_chain(
        {"source": "GitHubConnector",
         "findings": [{"file": "github://o/r", "reality": "INFERRED"}],
         "analysis": {}, "evidence": [{"type": "github_receipt"}]},
        {}, {})
    assert chain2[0]["reality"] == "UNKNOWN"
