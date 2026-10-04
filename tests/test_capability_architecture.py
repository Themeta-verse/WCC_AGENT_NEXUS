"""Canonical capability-architecture tests (A-T): generic resolution, selection,
execution, auth, receipts, verification, decoupling, and failure propagation.

Real implementations throughout: the canonical ConnectorRegistry, the real
capability fabric, the real researcher/verifier agents, and the real
workflow engine. Fakes exist ONLY at the connector/network boundary (a
canned in-memory connector, a stubbed httpx client) — never for the fabric,
registry, agents, or engine under test.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.capability_fabric import (
    CANONICAL_RECEIPT_FIELDS,
    CapabilityRequest,
    CapabilityResolutionError,
    execute_capability,
    normalize_status,
    resolution_error_to_response,
    resolve_connector,
    response_reality,
    sanitize_data,
    verify_capability_response,
)
from runtime.connector_registry import ConnectorRegistry


# ---------------------------------------------------------------------------
# Boundary fakes (connector/network edge only)
# ---------------------------------------------------------------------------

class _FakeConnector:
    """Minimal in-memory connector behind the generic Connector contract."""

    def __init__(self, connector_id="fake", provider="fake", capabilities=None,
                 auth_state="CONNECTED", payload=None, status="SUCCESS",
                 research_profile="generic"):
        self.connector_id = connector_id
        self.provider = provider
        self.version = "9.9.9"
        self.capabilities = capabilities or {"demo.capability": {"risk": "LOW"}}
        self.auth_state = auth_state
        # Profile drives which research path an agent takes; the real connector
        # base class defaults this to "generic".
        self.research_profile = research_profile
        self._payload = payload if payload is not None else {"hello": "world"}
        self._status = status
        self.calls: list = []

    def health(self):
        return {"status": self.auth_state, "authentication": "FAKE"}

    def execute(self, operation, input_data):
        self.calls.append((operation, dict(input_data)))
        result_hash = hashlib.sha256(
            json.dumps(self._payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        receipt = {
            "receipt_id": f"receipt-{self.connector_id}-{operation}-test",
            "connector_id": self.connector_id,
            "provider": self.provider,
            "operation": operation,
            "capability": operation,
            "target": input_data.get("owner_repo", "demo-target"),
            "status": self._status,
            "started_at": "2026-01-01T00:00:00+00:00",
            "duration_seconds": 0.01,
            "result_hash": result_hash if self._status == "SUCCESS" else None,
            "input_digest": "inputdigest",
            "authentication": "FAKE_ACTIVE",
            "error": None if self._status == "SUCCESS" else "boom",
        }
        return {"status": self._status, "data": dict(self._payload), "receipt": receipt}

    def revoke(self):
        self.auth_state = "REVOKED"

    def refresh(self):
        return {"auth_state": self.auth_state}

    def metadata(self):
        return {"connector_id": self.connector_id, "auth_state": self.auth_state}


def _registry_with(*connectors) -> ConnectorRegistry:
    reg = ConnectorRegistry()
    for conn in connectors:
        reg.register(conn)
    return reg


def _req(capability="demo.capability", **kw):
    args = {"capability": capability, "input": {"owner_repo": "o/r"},
            "scope": "o/r", "task_id": "t", "agent_id": "a"}
    args.update(kw)
    return CapabilityRequest(**args)


# ---------------------------------------------------------------------------
# A. generic capability resolution
# ---------------------------------------------------------------------------

def test_a_resolve_known_capability():
    reg = _registry_with(_FakeConnector())
    assert resolve_connector(reg, "demo.capability").connector_id == "fake"


def test_a_resolve_unknown_capability_typed_error():
    reg = _registry_with(_FakeConnector())
    try:
        resolve_connector(reg, "nope.missing")
        raise AssertionError("expected CapabilityResolutionError")
    except CapabilityResolutionError as exc:
        assert exc.code == "UNKNOWN_CAPABILITY"


def test_a_resolve_none_registry_typed_error():
    try:
        resolve_connector(None, "demo.capability")
        raise AssertionError("expected CapabilityResolutionError")
    except CapabilityResolutionError as exc:
        assert exc.code == "NO_CONNECTOR"


# ---------------------------------------------------------------------------
# B. connector selection (authenticated/usable wins)
# ---------------------------------------------------------------------------

def test_b_selection_prefers_connected():
    reg = _registry_with(
        _FakeConnector(connector_id="stale", auth_state="NOT_CONFIGURED"),
        _FakeConnector(connector_id="live", auth_state="CONNECTED"),
    )
    assert resolve_connector(reg, "demo.capability").connector_id == "live"


def test_b_selection_all_unauthenticated_is_not_authorized():
    reg = _registry_with(_FakeConnector(auth_state="EXPIRED"))
    try:
        resolve_connector(reg, "demo.capability")
        raise AssertionError("expected CapabilityResolutionError")
    except CapabilityResolutionError as exc:
        assert exc.code == "NOT_AUTHORIZED"


# ---------------------------------------------------------------------------
# C/D. successful vs failed capability execution
# ---------------------------------------------------------------------------

def test_c_successful_execution_is_observed():
    reg = _registry_with(_FakeConnector())
    resp = execute_capability(reg, _req())
    assert resp.status == "SUCCESS"
    assert resp.reality == "OBSERVED"
    assert resp.data == {"hello": "world"}
    assert resp.receipt["receipt_id"].startswith("receipt-fake-")
    assert resp.error is None
    assert resp.connector_id == "fake"


def test_d_failed_execution_stays_failed():
    reg = _registry_with(_FakeConnector(status="FAILED"))
    resp = execute_capability(reg, _req())
    assert resp.status == "FAILED"
    assert resp.reality == "UNKNOWN"
    assert resp.error
    # A failed capability must not carry success evidence.
    assert resp.receipt["status"] == "FAILED"


def test_d_connector_exception_becomes_failed_response():
    class _Boom(_FakeConnector):
        def execute(self, operation, input_data):
            raise RuntimeError("network down")

    reg = _registry_with(_Boom())
    resp = execute_capability(reg, _req())
    assert resp.status == "FAILED"
    assert "RuntimeError" in (resp.error or "")


def test_d_unknown_connector_status_normalizes_to_unknown():
    class _Weird(_FakeConnector):
        def execute(self, operation, input_data):
            return {"status": "SPLENDID", "data": {}, "receipt": {}}

    reg = _registry_with(_Weird())
    resp = execute_capability(reg, _req())
    assert resp.status == "UNKNOWN"
    assert resp.reality == "UNKNOWN"
    assert normalize_status("SPLENDID") == "UNKNOWN"
    assert normalize_status("success") == "SUCCESS"


# ---------------------------------------------------------------------------
# E/F. unavailable + auth-required states
# ---------------------------------------------------------------------------

def test_e_unknown_capability_maps_to_unavailable_response():
    err = CapabilityResolutionError("UNKNOWN_CAPABILITY", "demo.missing", "gone")
    resp = resolution_error_to_response(err, request=_req("demo.missing"))
    assert resp.status == "UNAVAILABLE"
    assert resp.reality == "UNKNOWN"
    assert resp.receipt["status"] == "UNAVAILABLE"


def test_f_not_authorized_maps_to_auth_required_response():
    err = CapabilityResolutionError("NOT_AUTHORIZED", "demo.capability", "need CONNECTED")
    resp = resolution_error_to_response(err, request=_req())
    assert resp.status == "AUTH_REQUIRED"
    assert resp.reality == "UNKNOWN"
    assert "NOT_AUTHORIZED" in (resp.error or "")


def test_response_reality_mapping():
    assert response_reality("SUCCESS") == "OBSERVED"
    assert response_reality("PARTIAL") == "OBSERVED"
    assert response_reality("BLOCKED") == "OBSERVED"
    assert response_reality("FAILED") == "UNKNOWN"
    assert response_reality("UNAVAILABLE") == "UNKNOWN"
    assert response_reality("AUTH_REQUIRED") == "UNKNOWN"
    assert response_reality("UNKNOWN") == "UNKNOWN"


# ---------------------------------------------------------------------------
# G. canonical receipt (real GitHubConnector, stubbed network)
# ---------------------------------------------------------------------------

class _CannedResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"X-RateLimit-Remaining": "59"}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)


class _CannedClient:
    payload = {"full_name": "Themeta-verse/Nexus", "id": 1}

    def __init__(self, *a, **k):
        pass

    def get(self, url, **k):
        return _CannedResponse(dict(_CannedClient.payload))


def _real_github_connector(token="test-token"):
    from runtime.github_provider import GITHUB_CAPABILITIES, GitHubConnector
    return GitHubConnector(
        token=token, connector_id="github", provider="github",
        version="1.0.0", capabilities=dict(GITHUB_CAPABILITIES),
    )


def test_g_canonical_receipt_shape():
    import runtime.github_provider as gp
    conn = _real_github_connector()
    receipt = conn._make_receipt(
        "github.repository.read", {"full_name": "o/r"}, "2026-01-01T00:00:00+00:00",
        0.5, "github.repository.read", {"owner_repo": "o/r"}, "o/r",
    )
    missing = [k for k in CANONICAL_RECEIPT_FIELDS if k not in receipt]
    assert missing == []
    assert receipt["started_at"] == receipt["timestamp"]
    assert receipt["input_digest"]
    assert receipt["error"] is None


def test_g_git_connector_receipt_matches_canonical_shape():
    import tempfile
    from unittest import mock
    from runtime.git_connector import GIT_CAPABILITIES, GitConnector
    conn = GitConnector(connector_id="git", provider="git", version="1.0.0",
                        capabilities=dict(GIT_CAPABILITIES))
    with mock.patch("runtime.tools.git_status") as _gs:
        from runtime.bounded_agent import ObservationReceipt
        _gs.return_value = (
            ObservationReceipt(receipt_id="r", agent_id="a", operation="git.status",
                               target_resource="w", requested_capability="git.status",
                               execution_mode="REAL", start_time="s", end_time="e",
                               status="EXECUTED", reality="OBSERVED", reason="",
                               evidence_digest="d", provenance=["p"],
                               content_preview="## main", content_sha256="abc"),
            {"command": "git status", "output": "## main"},
        )
        out = conn.execute("git.status", {"workspace": tempfile.gettempdir(), "agent_id": "a"})
    missing = [k for k in CANONICAL_RECEIPT_FIELDS if k not in out["receipt"]]
    assert missing == []


# ---------------------------------------------------------------------------
# H. receipt/result hash consistency (real connector, stubbed httpx)
# ---------------------------------------------------------------------------

def test_h_hash_consistent_for_real_connector_response():
    import runtime.github_provider as gp
    real_client = gp.httpx.Client
    gp.httpx.Client = _CannedClient
    try:
        reg = _registry_with(_real_github_connector())
        resp = execute_capability(
            reg, _req("github.repository.read",
                      input={"owner_repo": "Themeta-verse/Nexus"},
                      scope="Themeta-verse/Nexus"))
    finally:
        gp.httpx.Client = real_client
    assert resp.status == "SUCCESS"
    recomputed = hashlib.sha256(
        json.dumps(sanitize_data(resp.data), sort_keys=True, default=str).encode()
    ).hexdigest()
    assert resp.receipt["result_hash"] == recomputed
    checks = verify_capability_response(resp, expected_capability="github.repository.read",
                                        expected_scope="Themeta-verse/Nexus")
    by_name = {c["check"]: c["status"] for c in checks}
    assert by_name["result_hash_consistent"] == "PASS"


def test_h_tampered_evidence_fails_consistency():
    import runtime.github_provider as gp
    real_client = gp.httpx.Client
    gp.httpx.Client = _CannedClient
    try:
        reg = _registry_with(_real_github_connector())
        resp = execute_capability(
            reg, _req("github.repository.read",
                      input={"owner_repo": "Themeta-verse/Nexus"},
                      scope="Themeta-verse/Nexus"))
    finally:
        gp.httpx.Client = real_client
    resp.data["full_name"] = "Evil/Tampered"
    checks = verify_capability_response(resp, expected_capability="github.repository.read")
    by_name = {c["check"]: c["status"] for c in checks}
    assert by_name["result_hash_consistent"] == "FAIL"


# ---------------------------------------------------------------------------
# I. tool record generation
# ---------------------------------------------------------------------------

def test_i_tool_record_links_receipt():
    reg = _registry_with(_FakeConnector())
    resp = execute_capability(reg, _req())
    record = resp.to_tool_record(task_id="t", agent_id="a")
    assert record["capability"] == "demo.capability"
    assert record["connector_id"] == "fake"
    assert record["receipt_id"] == resp.receipt["receipt_id"]
    assert record["result_hash"] == resp.receipt["result_hash"]
    assert record["status"] == "SUCCESS"
    assert record["reality"] == "OBSERVED"
    assert record["task_id"] == "t" and record["agent_id"] == "a"


# ---------------------------------------------------------------------------
# J. provenance propagation
# ---------------------------------------------------------------------------

def test_j_provenance_propagates_agent_capability_connector():
    reg = _registry_with(_FakeConnector())
    resp = execute_capability(reg, _req())
    assert f"agent:a" in resp.provenance
    assert "capability:demo.capability" in resp.provenance
    assert "connector:fake" in resp.provenance


# ---------------------------------------------------------------------------
# K. generic verification
# ---------------------------------------------------------------------------

def test_k_generic_verification_passes_for_success():
    reg = _registry_with(_FakeConnector())
    resp = execute_capability(reg, _req())
    checks = verify_capability_response(resp, expected_capability="demo.capability",
                                        expected_scope="o/r", artifact_reality="OBSERVED",
                                        artifact_provenance=["agent:a"])
    failed = [c for c in checks if c["status"] != "PASS"]
    assert failed == []


def test_k_generic_verification_rejects_failed_response():
    reg = _registry_with(_FakeConnector(status="FAILED"))
    resp = execute_capability(reg, _req())
    checks = verify_capability_response(resp, expected_capability="demo.capability")
    by_name = {c["check"]: c["status"] for c in checks}
    assert by_name["observation_exists"] == "PASS"  # error payload exists...
    assert by_name["reality_observed"] == "FAIL"    # ...but reality is not OBSERVED
    assert by_name["status_canonical"] == "PASS"


def test_k_generic_verification_rejects_receipt_replay():
    reg = _registry_with(_FakeConnector())
    resp = execute_capability(reg, _req())
    resp.receipt["capability"] = "other.capability"
    checks = verify_capability_response(resp)
    by_name = {c["check"]: c["status"] for c in checks}
    assert by_name["receipt_matches_capability"] == "FAIL"


# ---------------------------------------------------------------------------
# L. GitHub provider-specific verification adapter
# ---------------------------------------------------------------------------

def _github_research(scope="Themeta-verse/Nexus"):
    meta = {"full_name": scope, "id": 999, "private": True}
    return {
        "scope": scope,
        "findings": [{"file": f"github://{scope}"}],
        "analysis": {},
        "evidence": [{"type": "github_receipt"}],
        "github_metadata": dict(meta),
        "observation": dict(meta),
    }


def test_l_github_adapter_accepts_real_observation():
    from runtime.capability_verifiers import GitHubCapabilityVerifier
    checks = GitHubCapabilityVerifier.verify(_github_research(), expected_scope="Themeta-verse/Nexus",
                                             artifact_reality="OBSERVED")
    assert all(c["status"] == "PASS" for c in checks)


def test_l_github_adapter_rejects_fake_observation():
    from runtime.capability_verifiers import GitHubCapabilityVerifier
    checks = GitHubCapabilityVerifier.verify(
        {"scope": "x", "findings": [], "evidence": []},
        expected_scope="Themeta-verse/Nexus", artifact_reality="INFERRED")
    assert any(c["status"] == "FAIL" for c in checks)
    by_name = {c["check"]: c["status"] for c in checks}
    assert by_name["github_reality_observed"] == "FAIL"
    assert by_name["github_no_filesystem_fallback"] == "FAIL"


# ---------------------------------------------------------------------------
# M. no external-capability filesystem fallback
# ---------------------------------------------------------------------------

def test_m_explicit_github_request_never_falls_back():
    from runtime.agent_base import AgentContext
    from runtime.agents.researcher import ResearchAgent
    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="Research repository", agent_id="researcher",
        scope="Themeta-verse/Nexus", observation_scope="Themeta-verse/Nexus",
        execution_metadata={"capabilities_requested": ["filesystem.read", "github.repository.read"]},
        connector_registry=ConnectorRegistry(),  # real but empty
    )
    result = ResearchAgent().execute(ctx)
    assert result.status == "FAILED"
    assert result.reality == "UNKNOWN"
    assert result.artifacts == []
    # The no-fallback guarantee is about EVIDENCE, not about prose. A failed run
    # legitimately mentions the capability it could not obtain, so asserting on
    # the substring "filesystem" only tested the wording of the error message.
    # What must never happen is filesystem-derived content standing in for the
    # unavailable external observation.
    blob = json.dumps(result.result, default=str)
    for evidence_key in ("findings", "evidence", "observations", "research_findings"):
        assert evidence_key not in result.result, f"failed research must carry no {evidence_key}"
    assert result.execution_metadata.get("tool_executions") == [], \
        "a failed run must report zero tool executions (no receipt may be claimed)"
    assert "full_name" not in blob and "owner_repo" not in blob, \
        "no repository-shaped content may appear when the connector is absent"


# ---------------------------------------------------------------------------
# N. token redaction
# ---------------------------------------------------------------------------

_SENTINEL = "SENTINEL_TOKEN_9f8e7d6c5b"


def test_n_secrets_never_reach_response_receipt_or_tool_record():
    class _Leaky(_FakeConnector):
        def execute(self, operation, input_data):
            assert _SENTINEL not in json.dumps(input_data)
            return {"status": "SUCCESS",
                    "data": {"ok": True, "token": _SENTINEL, "nested": {"access_token": _SENTINEL},
                             "clone_token_tmp": _SENTINEL, "secret": _SENTINEL},
                    "receipt": {"receipt_id": "r", "connector_id": self.connector_id,
                                "provider": self.provider, "operation": operation,
                                "capability": operation, "target": "t", "status": "SUCCESS",
                                "started_at": "s", "duration_seconds": 0.1,
                                "result_hash": "h", "input_digest": "d",
                                "authentication": "FAKE", "error": None}}

    reg = _registry_with(_Leaky())
    resp = execute_capability(reg, _req())
    assert _SENTINEL not in json.dumps(resp.data, default=str)
    assert _SENTINEL not in json.dumps(resp.receipt, default=str)
    assert _SENTINEL not in json.dumps(resp.to_tool_record(), default=str)
    checks = verify_capability_response(resp)
    assert {c["check"]: c["status"] for c in checks}["no_secrets_in_receipt"] == "PASS"


# ---------------------------------------------------------------------------
# O. multiple connectors supporting the same capability
# ---------------------------------------------------------------------------

def test_o_multiple_connectors_discoverable_first_connected_wins():
    reg = _registry_with(
        _FakeConnector(connector_id="c1", capabilities={"shared.cap": {}}),
        _FakeConnector(connector_id="c2", capabilities={"shared.cap": {}}),
    )
    ids = sorted(c.connector_id for c in reg.get_capability_connectors("shared.cap"))
    assert ids == ["c1", "c2"]
    assert len(reg.discover("shared.cap")) == 2
    assert resolve_connector(reg, "shared.cap").connector_id == "c1"


# ---------------------------------------------------------------------------
# P. registry registration failure is explicit
# ---------------------------------------------------------------------------

def test_p_invalid_registration_raises_and_safe_records():
    reg = ConnectorRegistry()
    try:
        reg.register(object())
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert reg.register_safe(object(), source="test") is False
    assert len(reg.registration_errors) == 1
    assert reg.registration_errors[0]["source"] == "test"
    # A failed registration must not shadow the capability namespace.
    assert reg.get_capability_connectors("demo.capability") == []


# ---------------------------------------------------------------------------
# Q. connector discovery failure is explicit
# ---------------------------------------------------------------------------

def test_q_broken_health_is_reported_not_dropped():
    class _Sick(_FakeConnector):
        def health(self):
            raise RuntimeError("health exploded")

    reg = _registry_with(_Sick())
    found = reg.discover("demo.capability")
    assert len(found) == 1
    assert found[0]["health"]["status"] == "ERROR"
    # Resolution still works (gated on auth_state, honestly reported health).
    assert resolve_connector(reg, "demo.capability").connector_id == "fake"
    snap = reg.health_snapshot()
    assert snap["demo.capability"][0]["health"]["status"] == "ERROR"


# ---------------------------------------------------------------------------
# R. researcher GitHub path (generic fabric, both registry shapes)
# ---------------------------------------------------------------------------

def _research_ctx(scope, registry, caps):
    from runtime.agent_base import AgentContext
    return AgentContext(
        workflow_id="w", task_id="t", task_name="Research repository", agent_id="researcher",
        scope=scope, observation_scope=scope,
        execution_metadata={"capabilities_requested": caps},
        connector_registry=registry,
    )


class _StubOnlyRegistry:
    """Minimal registry without request_capability, but WITH capability discovery.

    The previous version implemented only get_connector(). That forced the
    researcher to guess a connector id from the capability name's prefix, which
    is provider-specific logic inside a generic agent and silently routed any
    unknown capability to whichever connector happened to be named "git".

    Generic resolution needs to answer "which connectors declare this
    capability?". A registry that cannot answer that cannot participate in the
    canonical path, so this double now implements the discovery surface — and
    the researcher keeps no prefix-guessing fallback.
    """

    def __init__(self, conn):
        self._conn = conn

    def get_connector(self, cid):
        return self._conn if cid == self._conn.connector_id else None

    def get_capability_connectors(self, capability):
        if capability in (getattr(self._conn, "capabilities", {}) or {}):
            return [self._conn]
        return []

    def discover(self, capability):
        return [{"connector_id": c.connector_id, "provider": c.provider,
                 "auth_state": getattr(c, "auth_state", "UNKNOWN")}
                for c in self.get_capability_connectors(capability)]

    @property
    def connector_ids(self):
        return [self._conn.connector_id]

    @property
    def capabilities(self):
        return list(getattr(self._conn, "capabilities", {}) or {})

    @property
    def registration_errors(self):
        return []


def test_r_researcher_github_via_full_registry():
    from runtime.agents.researcher import ResearchAgent
    payload = {"full_name": "Themeta-verse/Nexus", "id": 123, "private": True}
    reg = _registry_with(_FakeConnector(connector_id="github", provider="github",
                                         capabilities={"github.repository.read": {}},
                                         payload=payload))
    result = ResearchAgent().execute(
        _research_ctx("Themeta-verse/Nexus", reg, ["github.repository.read"]))
    assert result.status == "COMPLETED"
    assert result.reality == "OBSERVED"
    assert result.execution_metadata["tools_used"] == ["github.repository.read"]
    art = result.artifacts[0]
    assert art["reality"] == "OBSERVED"
    assert "github.repository.read" in art["provenance"]


def test_r_researcher_github_via_stub_registry():
    from runtime.agents.researcher import ResearchAgent
    payload = {"full_name": "Themeta-verse/Nexus", "id": 123, "private": True}
    stub = _StubOnlyRegistry(_FakeConnector(connector_id="github", provider="github",
                                             capabilities={"github.repository.read": {}},
                                             payload=payload))
    result = ResearchAgent().execute(
        _research_ctx("Themeta-verse/Nexus", stub, ["github.repository.read"]))
    assert result.status == "COMPLETED"
    assert result.reality == "OBSERVED"


def test_r_researcher_github_auth_failure_is_failed_not_completed():
    from runtime.agents.researcher import ResearchAgent
    reg = _registry_with(_FakeConnector(connector_id="github", provider="github",
                                         capabilities={"github.repository.read": {}},
                                         auth_state="NOT_CONFIGURED",
                                         research_profile="repository"))
    result = ResearchAgent().execute(
        _research_ctx("Themeta-verse/Nexus", reg, ["github.repository.read"]))
    assert result.status == "FAILED"
    # The error reports authorization failure, not authentication per se
    assert "authorized" in (result.error or "").lower() or "not_authorized" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# S. researcher filesystem path goes THROUGH the canonical fabric
# ---------------------------------------------------------------------------

def test_s_researcher_filesystem_path_observed():
    """Local filesystem research must be connector-backed, not hand-rolled.

    This previously asserted tools_used == ["filesystem.read"] while the agent
    walked the tree itself and hand-minted receipt ids. The path now requests
    capabilities from the connector registry like every other provider, so the
    recorded capabilities are the ones actually executed, and every recorded
    tool execution must carry a connector-produced receipt.
    """
    import tempfile
    from runtime.agents.researcher import ResearchAgent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "a.txt"), "w") as f:
            f.write("hello nexus")
        with open(os.path.join(tmp, "b.py"), "w") as f:
            f.write("print('hi')")
        result = ResearchAgent().execute(_research_ctx(tmp, None, ["filesystem.read"]))
    assert result.status == "COMPLETED"
    assert result.reality == "OBSERVED"
    assert result.result["files_discovered"] == 2
    tools_used = result.execution_metadata["tools_used"]
    # filesystem.read is the requested capability; filesystem.list is the
    # internal discovery step. Both are real connector executions and both are
    # reported — hiding either would misstate what was observed.
    assert "filesystem.read" in tools_used
    assert "filesystem.list" in tools_used
    assert set(tools_used) <= {"filesystem.read", "filesystem.list"}

    # Truth boundary: every recorded tool execution is connector-backed.
    tool_executions = result.execution_metadata.get("tool_executions") or []
    assert tool_executions, "filesystem research must record real tool executions"
    for te in tool_executions:
        assert te.get("connector_id") == "filesystem", te
        assert te.get("provider") == "filesystem", te
        assert te.get("receipt_id"), f"tool execution without a connector receipt: {te}"
        assert te.get("result_hash") or te.get("content_sha256"), \
            f"tool execution without a result hash: {te}"
        assert te.get("reality") == "OBSERVED", te

    # And the artifact must not claim a source it did not use.
    artifact = result.artifacts[0]
    for ev in artifact["content"].get("evidence", []):
        assert ev.get("source") == "FilesystemConnector", ev


# ---------------------------------------------------------------------------
# T. workflow FAILED propagation (real engine + real database)
# ---------------------------------------------------------------------------

def test_t_failed_capability_propagates_to_failed_task_and_workflow():
    import tempfile
    from runtime.agent_registry import AgentRegistry
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
    from nexus_independent.database import NexusDatabase

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
        # Empty REAL registry: github capability unresolvable -> honest failure.
        executor.connector_registry = ConnectorRegistry()
        engine = WorkflowEngine(database=db, composer=MissionComposer(),
                                policy=WorkflowExecutionPolicy(max_retries_default=0),
                                agent_registry=registry, artifacts_root=tmp, messaging_hub=hub)
        engine.set_executor(executor, agent_registry=registry)

        spec = WorkflowSpec(
            name="failing-github",
            objective="Read Themeta-verse/Nexus",
            scope="Themeta-verse/Nexus",
            task_specs=[{
                "task_id": "task-0", "name": "Research repository", "task_type": "research",
                "agent_id": "researcher", "required_capabilities": ["github.repository.read"],
                "depends_on": [], "input_artifacts": [],
            }],
            agents=[{"agent_id": "researcher", "name": "Research Agent", "role": "researcher",
                     "capabilities": ["filesystem.read", "github.repository.read"],
                     "allowed_operations": ["read"], "prohibited_operations": ["write"],
                     "scope": {"project_id": "p"}, "expected_behaviour": "research"}],
        )
        wf = engine.create_workflow("t", "p", spec)
        wid = wf["workflow_id"]
        engine.start_workflow("t", "p", wid)
        state = engine.step("t", "p", wid)

        tasks = state["tasks"]
        assert tasks[0]["status"] == "FAILED"
        # A FAILED task must not produce OBSERVED artifacts.
        arts = [a for a in state["artifacts"] if a.get("reality") == "OBSERVED"]
        assert arts == []
        assert state["status"] == "FAILED"
