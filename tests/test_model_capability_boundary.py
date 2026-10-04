"""Model -> capability boundary tests (Phase 15, A-X).

The model is untrusted reasoning: it may REQUEST via typed
ModelCapabilityRequest, but only the canonical fabric executes. Fakes exist
only at the connector boundary and for the model itself. Deterministic,
offline, token-free.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.capability_fabric import (
    CapabilityResolutionError,
    execute_with_retry,
    verify_capability_response,
)
from runtime.connector_registry import ConnectorRegistry
from runtime.model_capability import (
    ModelCapabilityGate,
    ModelCapabilityRequest,
    ModelRuntimeContext,
    model_request_capability,
    request_capability,
)


class _TeleConnector:
    """Boundary fake: telemetry provider (not GitHub, not Git)."""

    def __init__(self, capability="telemetry.snapshot", status="SUCCESS", fail_times=0):
        self.connector_id = "telemetry-1"
        self.provider = "telemetry-mock"
        self.version = "0.2.0"
        self.capabilities = {capability: {"risk": "LOW"}}
        self.auth_state = "CONNECTED"
        self._capability = capability
        self._status = status
        self._fail_times = fail_times
        self.calls = 0

    def health(self):
        return {"status": "CONNECTED"}

    def execute(self, operation, input_data):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("telemetry outage")
        payload = {"metric": "cpu", "value": 0.42, "host": input_data.get("target")}
        rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {"status": self._status,
                "data": payload if self._status == "SUCCESS" else {"error": "bad"},
                "receipt": {
                    "receipt_id": f"receipt-telemetry-{self.calls}",
                    "connector_id": self.connector_id, "provider": self.provider,
                    "operation": operation, "capability": operation,
                    "target": input_data.get("target", "?"), "status": self._status,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:00:01+00:00",
                    "duration_seconds": 1.0,
                    "result_hash": rh if self._status == "SUCCESS" else None,
                    "input_digest": hashlib.sha256(b"i").hexdigest(),
                    "authentication": "NO_CREDENTIAL_REQUIRED", "error": None}}

    def revoke(self):
        self.auth_state = "REVOKED"

    def refresh(self):
        return {"auth_state": self.auth_state}

    def metadata(self):
        return {"connector_id": self.connector_id}


def _ctx(scope="host-a", **kw):
    args = {"workflow_id": "wf-1", "task_id": "task-0", "agent_id": "researcher",
            "model_id": "test-model-1", "tenant_id": "t1", "project_id": "p1",
            "scope": scope, "principal": {"tenant_id": "t1", "project_id": "p1"}}
    args.update(kw)
    return ModelRuntimeContext(**args)


def _reg(conn=None):
    reg = ConnectorRegistry()
    reg.register(conn or _TeleConnector())
    return reg


def _gate(*caps):
    return ModelCapabilityGate(allowed_capabilities=list(caps or ["telemetry.snapshot"]))


def _summary(capability="telemetry.snapshot", target="host-a", scope="host-a",
             registry=None, gate=None, ctx=None, **kw):
    req = request_capability(capability, target, parameters=kw.get("parameters", {}),
                             intent=kw.get("intent", "check cpu"))
    return model_request_capability(registry or _reg(), req, ctx or _ctx(scope),
                                    gate=gate or _gate(capability))


# ---------------------------------------------------------------------------
# A. valid model request
# ---------------------------------------------------------------------------

def test_a_valid_model_request_executes():
    summary, resp = _summary()
    assert summary["status"] == "SUCCESS"
    assert summary["reality"] == "OBSERVED"
    assert summary["capability"] == "telemetry.snapshot"
    assert summary["target"] == "host-a"
    assert summary["receipt_id"] == resp.receipt["receipt_id"]
    assert summary["connector_id"] == "telemetry-1"
    assert summary["verified"] is False  # verification happens downstream, never here
    assert resp.provider == "telemetry-mock"


# ---------------------------------------------------------------------------
# B. malformed requests never reach a connector
# ---------------------------------------------------------------------------

def test_b_malformed_requests_rejected():
    conn = _TeleConnector()
    reg = _reg(conn)
    gate = _gate("telemetry.snapshot")
    ctx = _ctx()
    bad = [
        request_capability("", "host-a", {}, "x"),                      # empty capability
        request_capability("../etc", "host-a", {}, "x"),                # malformed capability
        request_capability("telemetry.snapshot", "", {}, "x"),          # missing target
        request_capability("telemetry.snapshot", "a" * 600, {}, "x"),  # oversized
        request_capability("telemetry.snapshot", "a\x01b", {}, "x"),    # control chars
        request_capability("telemetry.snapshot", "../host-a", {}, "x"),  # traversal
        request_capability("telemetry.snapshot", "a;b", {}, "x"),        # shell metachars
        request_capability("telemetry.snapshot", "$(x)", {}, "x"),      # substitution
        request_capability("telemetry.snapshot", "workflow-abc", {}, "x"),  # wf-id target
        request_capability("telemetry.snapshot", "host-a", {}, ""),      # missing intent
        request_capability("telemetry.snapshot", "~", {}, "x"),         # home expansion
    ]
    for req in bad:
        try:
            model_request_capability(reg, req, ctx, gate=gate)
            raise AssertionError(f"should reject {req!r}")
        except CapabilityResolutionError as exc:
            assert exc.code == "INVALID_REQUEST"
    non_dict = ModelCapabilityRequest(capability="telemetry.snapshot", target="host-a",
                                      parameters=["nope"], intent="x")
    try:
        model_request_capability(reg, non_dict, ctx, gate=gate)
        raise AssertionError("non-dict parameters should reject")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST"
    incomplete = ModelRuntimeContext(workflow_id="", task_id="t", agent_id="a",
                                     model_id="m", tenant_id="t", project_id="p", scope="s")
    try:
        model_request_capability(reg, request_capability("telemetry.snapshot", "s", {}, "x"),
                                 incomplete, gate=ModelCapabilityGate(allowed_capabilities=["x"]))
        raise AssertionError("incomplete context should reject")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST"
    assert conn.calls == 0


# ---------------------------------------------------------------------------
# C. unknown capability
# ---------------------------------------------------------------------------

def test_c_unknown_capability_rejected():
    conn = _TeleConnector()
    reg = _reg(conn)
    # Allowlisted but unregistered -> fabric UNKNOWN_CAPABILITY.
    gate = _gate("telemetry.snapshot", "void.capability")
    try:
        model_request_capability(reg, request_capability("void.capability", "host-a", {}, "x"),
                                 _ctx(), gate=gate)
        raise AssertionError("should raise")
    except CapabilityResolutionError as exc:
        assert exc.code == "UNKNOWN_CAPABILITY"
    # Not allowlisted -> gate INVALID_REQUEST before resolution.
    try:
        model_request_capability(reg, request_capability("void.capability", "host-a", {}, "x"),
                                 _ctx(), gate=_gate("telemetry.snapshot"))
        raise AssertionError("should reject")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST"
    assert conn.calls == 0


# ---------------------------------------------------------------------------
# D. cross-scope requests
# ---------------------------------------------------------------------------

def test_d_scope_binding():
    conn = _TeleConnector()
    reg = _reg(conn)
    gate = _gate("telemetry.snapshot")
    ok, _ = model_request_capability(reg, request_capability("telemetry.snapshot", "host-a", {}, "x"),
                                     _ctx("host-a"), gate=gate)
    assert ok["status"] == "SUCCESS"
    try:
        model_request_capability(reg, request_capability("telemetry.snapshot", "host-b", {}, "x"),
                                 _ctx("host-a"), gate=gate)
        raise AssertionError("cross-scope should reject")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST" and "scope" in exc.detail.lower()
    # Explicit operator opt-in permits (still bound, still verified).
    ctx = _ctx("host-a")
    ctx.allow_cross_scope = True
    ok2, _ = model_request_capability(reg, request_capability("telemetry.snapshot", "host-b", {}, "x"),
                                      ctx, gate=gate)
    assert ok2["status"] == "SUCCESS"
    assert conn.calls == 2


# ---------------------------------------------------------------------------
# E/F. tenant/project spoofing rejected, runtime binding applied
# ---------------------------------------------------------------------------

def test_e_tenant_spoof_rejected_and_runtime_bound():
    conn = _TeleConnector()
    reg = _reg(conn)
    gate = _gate("telemetry.snapshot")
    spoof = {"capability": "telemetry.snapshot", "target": "host-a", "parameters": {},
             "intent": "x", "tenant_id": "tenant-evil", "project_id": "p-evil"}
    try:
        model_request_capability(reg, spoof, _ctx(), gate=gate)
        raise AssertionError("tenant spoof should reject")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST"
    summary, resp = _summary(registry=reg, gate=gate)
    assert resp.receipt["tenant_id"] == "t1"
    assert resp.receipt["project_id"] == "p1"
    assert conn.calls == 1


# ---------------------------------------------------------------------------
# G/H. policy integration (canonical policy engine)
# ---------------------------------------------------------------------------

def test_g_deny_policy_blocks_model_request():
    from runtime.capability_fabric import CapabilityPolicy, PolicyDecision

    class _Deny(CapabilityPolicy):
        policy_id = "deny-test"

        def decide(self, *, request, connector):
            return PolicyDecision("DENY", "nope", self.policy_id)

    conn = _TeleConnector()
    reg = _reg(conn)
    reg.policy = _Deny()
    summary, resp = _summary(registry=reg)
    assert summary["status"] == "BLOCKED"
    assert resp.status == "BLOCKED" and conn.calls == 0


def test_h_approval_policy_never_silently_executes():
    from runtime.capability_fabric import CapabilityPolicy, PolicyDecision

    class _Approve(CapabilityPolicy):
        policy_id = "approve-test"

        def decide(self, *, request, connector):
            return PolicyDecision("REQUIRES_APPROVAL", "ask human", self.policy_id)

    conn = _TeleConnector()
    reg = _reg(conn)
    reg.policy = _Approve()
    summary, _ = _summary(registry=reg)
    assert summary["status"] == "BLOCKED" and conn.calls == 0


# ---------------------------------------------------------------------------
# I/J. failure path + retry lineage
# ---------------------------------------------------------------------------

def test_i_provider_failure_is_failed_never_observed():
    reg = _reg(_TeleConnector(status="FAILED"))
    summary, resp = _summary(registry=reg)
    assert summary["status"] == "FAILED"
    assert summary["reality"] == "UNKNOWN"
    assert summary["verified"] is False


def test_j_retry_lineage_through_gate_built_request():
    from runtime.model_capability import ModelCapabilityGate as _G
    reg = ConnectorRegistry()
    reg.register(_TeleConnector(fail_times=1))
    gate = _G(allowed_capabilities=["telemetry.snapshot"])
    req = gate.build_request(request_capability("telemetry.snapshot", "host-a", {}, "x"),
                             _ctx())
    final, attempts = execute_with_retry(reg, req, max_attempts=3)
    assert final.status == "SUCCESS"
    assert [a.status for a in attempts] == ["FAILED", "SUCCESS"]
    assert attempts[1].parent_receipt_id == attempts[0].receipt_id
    assert "model:test-model-1" in final.provenance


# ---------------------------------------------------------------------------
# K/L. secrets in keys and credential-shaped values
# ---------------------------------------------------------------------------

def test_k_secret_keys_rejected():
    reg = _reg()
    gate = _gate("telemetry.snapshot")
    for payload in ({"token": "x"}, {"api_key": "x"}, {"secret": "x"},
                    {"password": "x"}, {"authorization": "Bearer x"}):
        req = {"capability": "telemetry.snapshot", "target": "host-a",
               "parameters": {}, "intent": "x", **payload}
        try:
            model_request_capability(reg, req, _ctx(), gate=gate)
            raise AssertionError(f"should reject {payload}")
        except CapabilityResolutionError as exc:
            assert exc.code == "INVALID_REQUEST"
    try:
        model_request_capability(
            reg, request_capability("telemetry.snapshot", "host-a",
                                    {"api_key": "x"}, "x"), _ctx(), gate=gate)
        raise AssertionError("secret param name should reject")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST"


def test_l_credential_shaped_values_rejected():
    reg = _reg()
    gate = _gate("telemetry.snapshot")
    bad_values = ["ghp_fakesecret1", "gho_fakesecret1", "github_pat_fake_1",
                  "Bearer faketoken123", "sk-fake-key-1"]
    for val in bad_values:
        for req in (request_capability("telemetry.snapshot", "host-a", {"note": val}, "x"),
                    request_capability("telemetry.snapshot", "host-a", {}, f"do {val}"),
                    request_capability("telemetry.snapshot", val, {}, "x")):
            try:
                model_request_capability(reg, req, _ctx(), gate=gate)
                raise AssertionError(f"should reject {val}")
            except CapabilityResolutionError as exc:
                assert exc.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# M/N. no connector/token access from the model layer
# ---------------------------------------------------------------------------

def test_m_model_gate_holds_no_execution_authority():
    gate = _gate("telemetry.snapshot")
    for attr in ("registry", "connector", "token", "execute", "event_sink"):
        assert not hasattr(gate, attr), attr
    import runtime.model_capability as mc
    assert not hasattr(mc, "GitHubConnector") and not hasattr(mc, "GitConnector")


def test_n_no_tokens_in_model_layer():
    import pathlib
    for name in ("model_capability.py", "model_agent_loop.py", "model_tools.py",
                 "model_router.py"):
        text = (pathlib.Path(__file__).resolve().parents[1] / "runtime" / name).read_text()
        assert "NEXUS_GITHUB_TOKEN" not in text, name
        assert "GitHubConnector" not in text, name
        assert "GitConnector" not in text or name == "model_tools.py", name
    # model_tools legitimately names git tool capabilities (bounded local
    # tools, not connectors); the gate module must not even do that.
    gate_text = (pathlib.Path(__file__).resolve().parents[1] / "runtime" / "model_capability.py").read_text()
    assert "GitConnector" not in gate_text and "github" not in gate_text.lower()


# ---------------------------------------------------------------------------
# O/P/Q. truth boundary
# ---------------------------------------------------------------------------

def test_o_model_never_claims_observation():
    for payload in ({"capability": "telemetry.snapshot", "target": "host-a",
                     "parameters": {}, "intent": "x", "reality": "OBSERVED"},
                    {"capability": "telemetry.snapshot", "target": "host-a",
                     "parameters": {}, "intent": "x", "verified": True}):
        try:
            model_request_capability(_reg(), payload, _ctx(), gate=_gate("telemetry.snapshot"))
            raise AssertionError("reality smuggling should reject")
        except CapabilityResolutionError as exc:
            assert exc.code == "INVALID_REQUEST"


def test_p_connector_observation_flows_to_response():
    summary, resp = _summary()
    assert resp.reality == "OBSERVED" and summary["reality"] == "OBSERVED"


def test_q_verifier_is_only_verified_authority():
    summary, _ = _summary()
    assert summary["verified"] is False


# ---------------------------------------------------------------------------
# R/U/V. end-to-end: request -> fabric -> receipt -> tool record -> artifact
# ---------------------------------------------------------------------------

def _telemetry_artifact(summary, resp):
    observation = dict(resp.data)
    content = {"observation": observation, "receipt": dict(resp.receipt),
               "telemetry": {"host": observation.get("host")}}
    from runtime.agents.verifier import _digest as _vd
    meta = {"artifact_id": "art-tele-1", "kind": "telemetry_report", "name": "t",
            "content_hash": _vd(content), "reality": "OBSERVED",
            "provenance": ["agent:researcher", "telemetry.snapshot"]}
    return meta, {"artifact_id": "art-tele-1", "content": content}


def test_r_end_to_end_model_to_verified():
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent
    summary, resp = _summary()
    record = resp.to_tool_record(task_id="task-0", agent_id="researcher")
    assert record["receipt_id"] == summary["receipt_id"] == resp.receipt["receipt_id"]
    meta, entry = _telemetry_artifact(summary, resp)
    ctx = AgentContext(workflow_id="wf-1", task_id="task-1", task_name="v",
                       agent_id="verifier", scope="host-a", observation_scope="host-a",
                       input_artifacts=[meta], artifact_contents=[entry],
                       tenant_id="t1", project_id="p1", messaging_hub=None)
    result = VerificationAgent().execute(ctx)
    assert result.reality == "VERIFIED", result.result["verification"]["checks"]
    assert result.result["verification"]["all_passed"] is True


# ---------------------------------------------------------------------------
# S. audit events
# ---------------------------------------------------------------------------

def test_s_model_event_chain():
    reg = _reg()
    events = []
    reg.event_sink = events.append
    gate = _gate("telemetry.snapshot")
    model_request_capability(reg, request_capability("telemetry.snapshot", "host-a", {}, "x"),
                             _ctx(), gate=gate)
    kinds = [e["event"] for e in events]
    assert kinds == ["model_request", "capability_requested", "capability_resolved",
                     "capability_execution_started", "capability_execution_completed"]
    first = events[0]
    assert first["model_id"] == "test-model-1" and first["workflow_id"] == "wf-1"
    assert first["capability"] == "telemetry.snapshot"
    blob = json.dumps(events)
    assert "ghp_" not in blob and "Bearer" not in blob
    # Rejection emits model_request_rejected and stops.
    events.clear()
    try:
        model_request_capability(reg, request_capability("nope.cap", "host-a", {}, "x"),
                                 _ctx(), gate=gate)
    except CapabilityResolutionError:
        pass
    assert [e["event"] for e in events] == ["model_request_rejected"]


# ---------------------------------------------------------------------------
# T. provenance
# ---------------------------------------------------------------------------

def test_t_model_provenance_without_corruption():
    _, resp = _summary()
    assert "model:test-model-1" in resp.provenance
    assert any(p.startswith("model-request:") for p in resp.provenance)
    assert "agent:researcher" in resp.provenance
    assert "capability:telemetry.snapshot" in resp.provenance
    assert "connector:telemetry-1" in resp.provenance
    assert "tenant:t1" in resp.provenance
    assert not any("VERIFIED" in p for p in resp.provenance)


# ---------------------------------------------------------------------------
# W/X. provider independence (incl. second provider, zero gate changes)
# ---------------------------------------------------------------------------

def test_w_model_layer_knows_no_github():
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1] / "runtime" / "model_capability.py").read_text().lower()
    for token in ("github", "owner_repo", "full_name", "gitconnector", "git_connector"):
        assert token not in text, token


def test_x_second_provider_without_model_layer_changes():
    class _Weather:
        connector_id = "weather-1"
        provider = "weather-mock"
        version = "1.0"
        capabilities = {"weather.snapshot": {"risk": "LOW"}}
        auth_state = "CONNECTED"
        calls = 0

        def health(self):
            return {"status": "CONNECTED"}

        def execute(self, operation, input_data):
            type(self).calls = getattr(type(self), "calls", 0) + 1
            payload = {"sky": "clear", "target": input_data.get("target")}
            rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            return {"status": "SUCCESS", "data": payload,
                    "receipt": {"receipt_id": "receipt-weather-1",
                                "connector_id": "weather-1", "provider": "weather-mock",
                                "operation": operation, "capability": operation,
                                "target": input_data.get("target"), "status": "SUCCESS",
                                "started_at": "s", "completed_at": "e",
                                "duration_seconds": 0.1, "result_hash": rh,
                                "input_digest": "d", "authentication": "X", "error": None}}

        def revoke(self):
            pass

        def refresh(self):
            return {}

        def metadata(self):
            return {}

    reg = ConnectorRegistry()
    reg.register(_Weather())
    gate = ModelCapabilityGate(allowed_capabilities=["weather.snapshot"])
    summary, resp = model_request_capability(
        reg, request_capability("weather.snapshot", "city-a", {}, "check sky"),
        _ctx("city-a"), gate=gate)
    assert summary["status"] == "SUCCESS" and resp.provider == "weather-mock"
    assert not [c for c in verify_capability_response(resp) if c["status"] != "PASS"]
