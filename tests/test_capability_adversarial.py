"""Adversarial capability-fabric tests: request validation, resolution attacks,
authentication spoofing, response/receipt tampering, replay, exceptions.

Deterministic. Fakes exist ONLY at the connector/network boundary (canned
in-memory connectors); the registry, fabric, agents, and verifier under
test are always the real implementations. No token, no network, no gh CLI.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.capability_fabric import (
    CapabilityRequest,
    CapabilityResponse,
    CapabilityResolutionError,
    execute_capability,
    resolve_connector,
    sanitize_data,
    scrub_error_text,
    validate_request,
    verify_capability_response,
)
from runtime.connector_registry import ConnectorRegistry


class _AdvConnector:
    """Boundary fake with fault-injection knobs (network edge only)."""

    def __init__(self, connector_id="adv", provider="adv", capability="adv.cap",
                 auth_state="CONNECTED", payload=None, status="SUCCESS",
                 raise_mode=None, raw_return="dict", receipt_patch=None,
                 receipt_drop=(), omit_receipt=False):
        self.connector_id = connector_id
        self.provider = provider
        self.version = "0.0.1"
        self.capabilities = {capability: {"risk": "LOW"}}
        self.auth_state = auth_state
        self._payload = {"ok": True} if payload is None else payload
        self._status = status
        self._raise_mode = raise_mode
        self._raw_return = raw_return
        self._receipt_patch = receipt_patch or {}
        self._receipt_drop = tuple(receipt_drop)
        self._omit_receipt = omit_receipt
        self.calls: list = []

    def health(self):
        if self.auth_state == "SICK":
            raise RuntimeError("health exploded")
        return {"status": self.auth_state}

    def execute(self, operation, input_data):
        self.calls.append((operation, dict(input_data)))
        if self._raise_mode == "runtime":
            raise RuntimeError("connector exploded")
        if self._raise_mode == "timeout":
            raise TimeoutError("connector timed out")
        if self._raise_mode == "secret-error":
            raise RuntimeError("auth failed for ghp_fakesecret999")
        if self._raw_return == "none":
            return None
        if self._raw_return == "list":
            return [{"not": "a dict"}]
        if self._raw_return == "str":
            return "SUCCESS"
        payload = dict(self._payload)
        rh = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        receipt = {
            "receipt_id": f"receipt-{self.connector_id}-{operation}-adv",
            "connector_id": self.connector_id,
            "provider": self.provider,
            "operation": operation,
            "capability": operation,
            "target": input_data.get("target", "adv-target"),
            "status": self._status,
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:01+00:00",
            "duration_seconds": 1.0,
            "result_hash": rh if self._status == "SUCCESS" else None,
            "input_digest": hashlib.sha256(b"input").hexdigest(),
            "authentication": "ADV_ACTIVE",
            "error": None,
        }
        receipt.update(self._receipt_patch)
        for key in self._receipt_drop:
            receipt.pop(key, None)
        if self._omit_receipt:
            return {"status": self._status, "data": payload}
        return {"status": self._status, "data": payload, "receipt": receipt}

    def revoke(self):
        self.auth_state = "REVOKED"

    def refresh(self):
        return {"auth_state": self.auth_state}

    def metadata(self):
        return {"connector_id": self.connector_id}


def _req(capability="adv.cap", **kw):
    args = {"capability": capability, "input": {"target": "t"},
            "scope": "t", "task_id": "t", "agent_id": "a"}
    args.update(kw)
    return CapabilityRequest(**args)


def _reg(conn) -> ConnectorRegistry:
    reg = ConnectorRegistry()
    reg.register(conn)
    return reg


def _failed_checks(resp, **kv):
    return [c for c in verify_capability_response(resp, **kv) if c["status"] != "PASS"]


# ---------------------------------------------------------------------------
# C. Request validation: malformed requests never reach a connector
# ---------------------------------------------------------------------------

def test_adv_empty_capability_rejected():
    conn = _AdvConnector()
    for bad in ("", "   "):
        try:
            execute_capability(_reg(conn), _req(bad))
            raise AssertionError(f"{bad!r} should be rejected")
        except CapabilityResolutionError as exc:
            assert exc.code == "INVALID_REQUEST"
    assert conn.calls == []


def test_adv_malformed_capability_rejected():
    conn = _AdvConnector()
    for bad in ("../etc/passwd", "HAS SPACES", "CAPS.LOCK", "a;b", ".leading", "trailing."):
        try:
            execute_capability(_reg(conn), _req(bad))
            raise AssertionError(f"{bad!r} should be rejected")
        except CapabilityResolutionError as exc:
            assert exc.code == "INVALID_REQUEST"
    assert conn.calls == []


def test_adv_non_dict_input_rejected():
    conn = _AdvConnector()
    req = _req()
    req.input = ["not", "a", "dict"]
    try:
        execute_capability(_reg(conn), req)
        raise AssertionError("non-dict input should be rejected")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST"
    assert conn.calls == []


def test_adv_none_request_rejected():
    try:
        validate_request(None)
        raise AssertionError("None request should be rejected")
    except CapabilityResolutionError as exc:
        assert exc.code == "INVALID_REQUEST"


def test_adv_unsupported_capability_unknown_never_executes():
    conn = _AdvConnector()
    reg = _reg(conn)
    try:
        execute_capability(reg, _req("adv.unknown"))
        raise AssertionError("unknown capability should raise")
    except CapabilityResolutionError as exc:
        assert exc.code == "UNKNOWN_CAPABILITY"
    assert conn.calls == []


def test_adv_connector_level_operation_mismatch_is_failed():
    class _Strict(_AdvConnector):
        def execute(self, operation, input_data):
            raise ValueError(f"unsupported operation: {operation}")

    resp = execute_capability(_reg(_Strict()), _req())
    assert resp.status == "FAILED"
    assert resp.reality == "UNKNOWN"


# ---------------------------------------------------------------------------
# D. Resolution attacks
# ---------------------------------------------------------------------------

def test_adv_empty_registry_is_unknown_capability():
    try:
        resolve_connector(ConnectorRegistry(), "adv.cap")
        raise AssertionError("should raise")
    except CapabilityResolutionError as exc:
        assert exc.code == "UNKNOWN_CAPABILITY"


def test_adv_provider_switch_is_explicit_in_provenance():
    c1 = _AdvConnector(connector_id="c1", provider="p1")
    c2 = _AdvConnector(connector_id="c2", provider="p2")
    reg = ConnectorRegistry()
    reg.register(c1)
    reg.register(c2)
    assert execute_capability(reg, _req()).connector_id == "c1"
    # No silent substitution: the executed connector is named in provenance.
    assert "connector:c1" in execute_capability(reg, _req()).provenance
    reg.unregister("c1")
    resp = execute_capability(reg, _req())
    assert resp.connector_id == "c2"
    assert "connector:c2" in resp.provenance


def test_adv_duplicate_registration_replaces_deterministically():
    reg = ConnectorRegistry()
    reg.register(_AdvConnector(connector_id="dup", payload={"v": 1}))
    reg.register(_AdvConnector(connector_id="dup", payload={"v": 2}))
    assert reg.get_capability_connectors("adv.cap").count(reg.get_connector("dup")) == 1
    assert execute_capability(reg, _req()).data == {"v": 2}


def test_adv_unregister_mid_workflow_is_explicit():
    reg = ConnectorRegistry()
    reg.register(_AdvConnector())
    assert execute_capability(reg, _req()).status == "SUCCESS"
    reg.unregister("adv")
    try:
        execute_capability(reg, _req())
        raise AssertionError("should raise after unregister")
    except CapabilityResolutionError as exc:
        assert exc.code in ("UNKNOWN_CAPABILITY", "NO_CONNECTOR")


def test_adv_registration_failure_visible():
    reg = ConnectorRegistry()
    assert reg.register_safe(object(), source="adv-test") is False
    assert len(reg.registration_errors) == 1
    assert reg.registration_errors[0]["source"] == "adv-test"


def test_adv_health_failure_reported_not_hidden():
    reg = _reg(_AdvConnector(auth_state="SICK"))
    found = reg.discover("adv.cap")
    assert found[0]["health"]["status"] == "ERROR"


def test_adv_connector_disappears_between_resolve_and_execute():
    class _Vanishing(_AdvConnector):
        def execute(self, operation, input_data):
            raise RuntimeError("connector gone")

    resp = execute_capability(_reg(_Vanishing()), _req())
    assert resp.status == "FAILED"
    assert resp.reality == "UNKNOWN"
    assert [c["check"] for c in _failed_checks(resp)] != []


# ---------------------------------------------------------------------------
# E. Authentication spoofing and stale states
# ---------------------------------------------------------------------------

def test_adv_spoofed_connected_without_successful_execution_never_observed():
    # A connector may CLAIM connected; only a successful execution yields
    # OBSERVED. A spoofed claim plus failed execution stays FAILED.
    resp = execute_capability(
        _reg(_AdvConnector(auth_state="CONNECTED", status="FAILED")), _req())
    assert resp.status == "FAILED"
    assert resp.reality != "OBSERVED"
    failed = {c["check"] for c in _failed_checks(resp)}
    assert "reality_observed" in failed


def test_adv_stale_states_block_resolution():
    for state in ("EXPIRED", "REAUTH_REQUIRED", "REVOKED", "ERROR", "NOT_CONFIGURED"):
        try:
            resolve_connector(_reg(_AdvConnector(auth_state=state)), "adv.cap")
            raise AssertionError(f"{state} should not resolve")
        except CapabilityResolutionError as exc:
            assert exc.code == "NOT_AUTHORIZED"


def test_adv_configured_is_usable_but_flagged():
    from runtime.capability_fabric import USABLE_AUTH_STATES
    assert "CONFIGURED" in USABLE_AUTH_STATES
    assert resolve_connector(_reg(_AdvConnector(auth_state="CONFIGURED")), "adv.cap")


# ---------------------------------------------------------------------------
# F+N. Malicious responses and connector exceptions
# ---------------------------------------------------------------------------

def test_adv_success_without_result_rejected():
    conn = _AdvConnector(payload={})
    # Empty payload dict is falsy -> observation_exists FAIL.
    resp = execute_capability(_reg(conn), _req())
    assert {c["check"] for c in _failed_checks(resp)} >= {"observation_exists"}


def test_adv_success_without_receipt_rejected():
    # A connector omitting the receipt entirely cannot claim SUCCESS: the
    # fabric normalizes to UNKNOWN/FAILED, never OBSERVED.
    resp = execute_capability(_reg(_AdvConnector(omit_receipt=True)), _req())
    assert resp.status == "FAILED"
    assert resp.reality == "UNKNOWN"
    # A dropped receipt_id alone is deterministically re-minted (bound to
    # target+operation), never silently detached.
    resp2 = execute_capability(_reg(_AdvConnector(receipt_drop=("receipt_id",))), _req())
    assert resp2.receipt["receipt_id"].startswith("receipt-adv-adv.cap-")


def test_adv_success_with_malformed_receipt_rejected():
    class _BadReceipt(_AdvConnector):
        def execute(self, operation, input_data):
            return {"status": "SUCCESS", "data": {"x": 1}, "receipt": {"junk": True}}

    resp = execute_capability(_reg(_BadReceipt()), _req())
    # Zero integrity fields -> treated as missing receipt -> explicit failure.
    assert resp.status == "FAILED"
    assert resp.reality == "UNKNOWN"


def test_adv_wrong_hash_rejected():
    resp = execute_capability(
        _reg(_AdvConnector(receipt_patch={"result_hash": "0" * 64})), _req())
    assert "result_hash_consistent" in {c["check"] for c in _failed_checks(resp)}


def test_adv_target_capability_connector_mismatch_rejected():
    resp = execute_capability(_reg(_AdvConnector()), _req())
    resp.receipt["target"] = "somewhere-else"
    resp.receipt["capability"] = "other.cap"
    resp.receipt["connector_id"] = "impostor"
    resp.receipt["provider"] = "impostor-p"
    failed = {c["check"] for c in _failed_checks(resp, expected_capability="adv.cap", expected_scope="t")}
    assert {"scope_match", "receipt_matches_capability", "receipt_matches_connector",
            "receipt_matches_provider"} <= failed


def test_adv_failed_claiming_observed_rejected():
    resp = CapabilityResponse(status="FAILED", capability="adv.cap", connector_id="c",
                              provider="p", reality="OBSERVED", data={"error": "x"},
                              receipt={"receipt_id": "r"}, provenance=["a"], error="x")
    failed = {c["check"] for c in _failed_checks(resp)}
    assert "reality_consistent_with_status" in failed
    assert "reality_observed" not in failed  # reality IS observed; consistency is what fails


def test_adv_response_claiming_verified_rejected():
    resp = CapabilityResponse(status="SUCCESS", capability="adv.cap", connector_id="c",
                              provider="p", reality="VERIFIED", data={"x": 1},
                              receipt={"receipt_id": "r"}, provenance=["a"])
    failed = {c["check"] for c in _failed_checks(resp)}
    # Only OBSERVED is acceptable for a connector response; VERIFIED can only
    # come from the independent verifier, never from a response.
    assert "reality_observed" in failed
    assert "reality_consistent_with_status" in failed


def test_adv_unknown_reality_with_success_rejected():
    resp = execute_capability(_reg(_AdvConnector()), _req())
    resp.reality = "UNKNOWN"
    assert "reality_observed" in {c["check"] for c in _failed_checks(resp)}


def test_adv_non_dict_data_rejected():
    resp = CapabilityResponse(status="SUCCESS", capability="adv.cap", connector_id="c",
                              provider="p", reality="OBSERVED", data="just-a-string",
                              receipt={"receipt_id": "r"}, provenance=["a"])
    assert "data_shape_valid" in {c["check"] for c in _failed_checks(resp)}


def test_adv_connector_returns_none_or_junk_is_failed():
    for mode in ("none", "list", "str"):
        resp = execute_capability(_reg(_AdvConnector(raw_return=mode)), _req())
        assert resp.status == "FAILED", mode
        assert resp.reality == "UNKNOWN", mode


def test_adv_connector_exception_variants_are_failed():
    for mode in ("runtime", "timeout"):
        resp = execute_capability(_reg(_AdvConnector(raise_mode=mode)), _req())
        assert resp.status == "FAILED"
        assert resp.reality == "UNKNOWN"
        assert resp.error


def test_adv_secret_in_connector_error_is_scrubbed():
    resp = execute_capability(_reg(_AdvConnector(raise_mode="secret-error")), _req())
    blob = json.dumps({"receipt": resp.receipt, "data": resp.data, "error": resp.error})
    assert "ghp_fakesecret999" not in blob
    assert "[REDACTED]" in blob
    assert resp.status == "FAILED"


# ---------------------------------------------------------------------------
# G. Receipt tampering, field by field
# ---------------------------------------------------------------------------

def _valid_adv_response():
    return execute_capability(_reg(_AdvConnector()), _req())


def test_adv_receipt_tampering_detected_where_covered():
    base = _valid_adv_response()
    assert _failed_checks(base, expected_capability="adv.cap", expected_scope="t") == []

    cases = {
        "capability": ({"capability": "evil.cap"}, {"receipt_matches_capability"}),
        "connector_id": ({"connector_id": "evil"}, {"receipt_matches_connector"}),
        "provider": ({"provider": "evil-p"}, {"receipt_matches_provider"}),
        "status": ({"status": "FAILED"}, {"receipt_matches_status"}),
        "result_hash": ({"result_hash": "f" * 64}, {"result_hash_consistent"}),
    }
    for field, (patch, expected) in cases.items():
        resp = _valid_adv_response()
        resp.receipt.update(patch)
        failed = {c["check"] for c in _failed_checks(resp, expected_capability="adv.cap", expected_scope="t")}
        assert expected <= failed, f"{field}: {failed}"

    # Dropped input binding is detected.
    resp = _valid_adv_response()
    resp.receipt.pop("input_digest")
    assert "input_digest_present" in {c["check"] for c in _failed_checks(resp)}

    # Credential material in any receipt value is detected.
    resp = _valid_adv_response()
    resp.receipt["authentication"] = "Bearer ghp_fakesecret999"
    failed = {c["check"] for c in _failed_checks(resp)}
    assert "authentication_no_secret" in failed
    assert "no_secrets_in_receipt" in failed


def test_adv_sanitized_hashing_is_self_consistent():
    # When the connector itself emits secret-free data, the receipt hash
    # recomputes cleanly (no false tamper alarm).
    resp = _valid_adv_response()
    assert "result_hash_consistent" not in {c["check"] for c in _failed_checks(resp)}


# ---------------------------------------------------------------------------
# L. Replay
# ---------------------------------------------------------------------------

def test_adv_receipt_replayed_across_capabilities_rejected():
    resp = _valid_adv_response()
    # Attacker replays receipt A's hash/ids under capability B.
    resp.capability = "adv.other"
    failed = {c["check"] for c in _failed_checks(resp, expected_capability="adv.other")}
    assert "receipt_matches_capability" in failed


def test_adv_receipt_replayed_under_new_scope_rejected():
    resp = _valid_adv_response()
    failed = {c["check"] for c in _failed_checks(resp, expected_scope="different-target")}
    assert "scope_match" in failed


def test_adv_identical_reverification_is_deterministic():
    # Re-verifying IDENTICAL evidence gives IDENTICAL verdicts (recomputation,
    # not expiry: freshness is NOT implemented and not claimed).
    resp = _valid_adv_response()
    first = verify_capability_response(resp, expected_capability="adv.cap", expected_scope="t")
    second = verify_capability_response(resp, expected_capability="adv.cap", expected_scope="t")
    assert first == second
    assert all(c["status"] == "PASS" for c in first)


# ---------------------------------------------------------------------------
# Q. Brand-new provider plugs in with zero core changes
# ---------------------------------------------------------------------------

def test_adv_brand_new_provider_end_to_end():
    class TelemetryConnector:
        connector_id = "telemetry-1"
        provider = "telemetry-mock"
        version = "0.1"
        capabilities = {"telemetry.snapshot": {"risk": "LOW"}}
        auth_state = "CONNECTED"

        def health(self):
            return {"status": "CONNECTED"}

        def execute(self, operation, input_data):
            assert operation == "telemetry.snapshot"
            payload = {"metric": "cpu", "value": 0.42}
            rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            return {"status": "SUCCESS", "data": payload,
                    "receipt": {
                        "receipt_id": "receipt-telemetry-1", "connector_id": self.connector_id,
                        "provider": self.provider, "operation": operation, "capability": operation,
                        "target": "host-a", "status": "SUCCESS",
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "completed_at": "2026-01-01T00:00:01+00:00",
                        "duration_seconds": 1.0, "result_hash": rh,
                        "input_digest": hashlib.sha256(b"i").hexdigest(),
                        "authentication": "NO_CREDENTIAL_REQUIRED", "error": None}}

        def revoke(self):
            self.auth_state = "REVOKED"

        def refresh(self):
            return {"auth_state": self.auth_state}

        def metadata(self):
            return {"connector_id": self.connector_id}

    reg = ConnectorRegistry()
    reg.register(TelemetryConnector())
    assert [d["connector_id"] for d in reg.discover("telemetry.snapshot")] == ["telemetry-1"]
    resp = execute_capability(
        reg, CapabilityRequest(capability="telemetry.snapshot", input={}, scope="host-a",
                               task_id="t", agent_id="a"))
    assert resp.status == "SUCCESS" and resp.reality == "OBSERVED"
    assert resp.provider == "telemetry-mock"
    record = resp.to_tool_record(task_id="t", agent_id="a")
    assert record["receipt_id"] == "receipt-telemetry-1"
    failed = _failed_checks(resp, expected_capability="telemetry.snapshot", expected_scope="host-a")
    assert failed == []


# ---------------------------------------------------------------------------
# M. Secret redaction at every boundary (synthetic secrets only)
# ---------------------------------------------------------------------------

_SYN_TOKEN = "TEST_GITHUB_TOKEN_123"
_SYN_PAT = "ghp_fakesecret999"


def test_adv_synthetic_secret_stripped_from_data():
    dirty = {"ok": True, "token": _SYN_TOKEN, "nested": {"secret": _SYN_TOKEN},
             "note": f"value {_SYN_PAT} here", "access_token": "x", "clone_token_tmp": "y"}
    clean = sanitize_data(dirty)
    blob = json.dumps(clean)
    assert _SYN_TOKEN not in blob
    assert _SYN_PAT not in blob
    assert clean["ok"] is True


def test_adv_secret_error_scrubbed():
    assert _SYN_PAT not in scrub_error_text(f"boom {_SYN_PAT} bang")
    assert "[REDACTED]" in scrub_error_text(f"boom {_SYN_PAT} bang")


def test_adv_leaky_connector_data_is_sanitized_before_response():
    conn = _AdvConnector(payload={"repo": "o/r", "token": _SYN_TOKEN, "msg": _SYN_PAT})
    resp = execute_capability(_reg(conn), _req())
    blob = json.dumps({"data": resp.data, "receipt": resp.receipt,
                       "record": resp.to_tool_record()})
    assert _SYN_TOKEN not in blob
    # NOTE: the receipt result_hash was computed by the connector over raw
    # data while the fabric scrubbed secrets from response data, so the
    # recompute honestly reports a mismatch instead of persisting secrets
    # silently: fail-closed beats secret persistence.
    assert "result_hash_consistent" in {c["check"] for c in _failed_checks(resp)}
