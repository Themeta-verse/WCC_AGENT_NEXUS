"""Integrity tests: artifact/provenance attacks, truth-boundary attacks, retry
integrity, verifier architecture separation, reporter reality preservation.

Real fabric/registry/agents/verifier throughout; fakes only at the
connector boundary. Deterministic, offline, no token.
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
    execute_capability,
    verify_capability_response,
)
from runtime.connector_registry import ConnectorRegistry


def _vdigest(value):
    from runtime.agents.verifier import _digest as _vd
    return _vd(value)


def _ctx(inputs, contents, scope="s", tenant="t1", project="p1", agent="verifier"):
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent
    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="v", agent_id=agent,
        scope=scope, observation_scope=scope, input_artifacts=inputs,
        artifact_contents=contents, tenant_id=tenant, project_id=project,
        messaging_hub=None,
    )
    return VerificationAgent().execute(ctx)


def _resoarch(content, art_id="a1", reality="OBSERVED", prov=None):
    prov = prov or ["agent:researcher", "github-connector", "github.repository.read"]
    return ({"artifact_id": art_id, "kind": "research_report", "name": "r",
             "content_hash": _vdigest(content), "reality": reality, "provenance": list(prov)},
            {"artifact_id": art_id, "content": content})


def _github_content(scope="o/r", receipt_extra=None, meta_extra=None):
    meta = {"full_name": scope, "id": 42, "private": False}
    if meta_extra:
        meta.update(meta_extra)
    payload = {"full_name": scope, "id": 42}
    rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    receipt = {"receipt_id": "receipt-gh-1", "connector_id": "github", "provider": "github",
               "operation": "github.repository.read", "capability": "github.repository.read",
               "target": scope, "status": "SUCCESS",
               "started_at": "2026-01-01T00:00:00+00:00",
               "completed_at": "2026-01-01T00:00:01+00:00",
               "duration_seconds": 1.0, "result_hash": rh,
               "input_digest": hashlib.sha256(b"i").hexdigest(),
               "authentication": "TOKEN_ACTIVE", "error": None,
               "tenant_id": "", "project_id": ""}
    if receipt_extra:
        receipt.update(receipt_extra)
    return {"research": {"scope": scope,
                         "findings": [{"file": f"github://{scope}"}],
                         "analysis": {}, "evidence": [{"type": "github_receipt"}],
                         "github_metadata": dict(meta), "observation": dict(payload),
                         "receipt": receipt}}


# ---------------------------------------------------------------------------
# H. Artifact / provenance attacks via the production verifier
# ---------------------------------------------------------------------------

def test_integ_content_modified_after_hash_rejected():
    content = _github_content()
    meta, entry = _resoarch(content)
    evil = json.loads(json.dumps(content))
    evil["research"]["observation"]["id"] = 666
    result = _ctx([meta], [{**entry, "content": evil}], scope="o/r")
    assert result.reality == "INFERRED"
    checks = {c["check"]: c["status"] for c in result.result["verification"]["checks"]}
    assert checks.get("content_hash_verified") == "FAIL"


def test_integ_hash_modified_rejected():
    content = _github_content()
    meta, entry = _resoarch(content)
    meta = dict(meta, content_hash="0" * 64)
    result = _ctx([meta], [entry], scope="o/r")
    assert result.reality == "INFERRED"
    assert result.result["verification"]["all_passed"] is False


def test_integ_artifact_substitution_rejected():
    content_a = _github_content(scope="o/r")
    content_b = _github_content(scope="other/repo")
    meta_a, entry_a = _resoarch(content_a, art_id="a1")
    # Attacker swaps in artifact B's content under A's identity.
    result = _ctx([meta_a], [{**entry_a, "content": content_b}], scope="o/r")
    assert result.reality == "INFERRED"


def test_integ_reality_upgraded_to_observed_rejected():
    # INFERRED artifact claiming the connector path must not pass as OBSERVED.
    content = _github_content()
    meta, entry = _resoarch(content, reality="INFERRED")
    result = _ctx([meta], [entry], scope="o/r")
    assert result.reality == "INFERRED"
    checks = {c["check"]: c["status"] for c in result.result["verification"]["checks"]}
    assert checks.get("github_reality_observed") == "FAIL"


def test_integ_wrong_parent_rejected():
    content = _github_content()
    meta, entry = _resoarch(content)
    meta = dict(meta, parent_artifacts=["ghost-artifact"])
    result = _ctx([meta], [entry], scope="o/r")
    checks = {c["check"]: c["status"] for c in result.result["verification"]["checks"]}
    assert checks.get("parent_link_valid") == "FAIL"
    assert result.reality == "INFERRED"


def test_integ_valid_parents_accepted():
    content = _github_content()
    meta, entry = _resoarch(content)
    meta = dict(meta, parent_artifacts=["a1"])
    result = _ctx([meta], [entry], scope="o/r")
    checks = {c["check"]: c["status"] for c in result.result["verification"]["checks"]}
    assert checks.get("parent_link_valid") == "PASS"


def test_integ_capability_provenance_stripped_still_verified_generically():
    # Attacker strips connector provenance markers. The receipt in content
    # still triggers receipt-gated generic verification (hash/scope/reality).
    content = _github_content()
    meta, entry = _resoarch(content, prov=["agent:researcher", "type:researcher"])
    result = _ctx([meta], [entry], scope="o/r")
    names = [c["check"] for c in result.result["verification"]["checks"]]
    assert any(n.startswith("result_hash_consistent") or "result_hash_consistent" in n for n in names)
    assert result.reality == "VERIFIED"


def test_integ_forged_provenance_without_evidence_rejected():
    # Forged connector provenance but no receipt/evidence in content.
    content = {"research": {"scope": "o/r", "findings": [], "analysis": {},
                            "evidence": []}}
    meta, entry = _resoarch(content)
    result = _ctx([meta], [entry], scope="o/r")
    assert result.reality == "INFERRED"


def test_integ_receipt_removed_downgrades_but_adapter_still_rejects():
    content = _github_content()
    del content["research"]["receipt"]
    content["research"]["evidence"] = []
    meta, entry = _resoarch(content)
    result = _ctx([meta], [entry], scope="o/r")
    # No receipt -> no generic receipt checks; the GitHub adapter still
    # demands evidence and rejects.
    assert result.reality == "INFERRED"


def test_integ_final_report_replaced_with_unrelated_rejected():
    # Substitution: valid meta, unrelated content swapped in -> hash gate fires.
    content = _github_content()
    meta, entry = _resoarch(content)
    result = _ctx([meta], [{**entry, "content": {"totally": "unrelated"}}], scope="o/r")
    assert result.reality == "INFERRED"
    # ...while a well-formed standalone artifact verifies on integrity alone
    # (documented semantic: integrity, not kind, is the generic gate).
    valid = _ctx([meta], [entry], scope="o/r")
    assert valid.reality == "VERIFIED"


def test_integ_missing_content_rejected():
    meta, _ = _resoarch(_github_content())
    result = _ctx([meta], [], scope="o/r")
    checks = {c["check"]: c["status"] for c in result.result["verification"]["checks"]}
    assert checks.get("content_available") == "FAIL"
    assert result.reality == "INFERRED"


# ---------------------------------------------------------------------------
# I. Truth-boundary attacks
# ---------------------------------------------------------------------------

def test_integ_failed_response_never_verifies():
    resp = CapabilityResponse(status="FAILED", capability="c", connector_id="c",
                              provider="p", reality="UNKNOWN", data={"error": "x"},
                              receipt={"receipt_id": "r", "input_digest": "d"},
                              provenance=["a"], error="x")
    failed = {c["check"] for c in verify_capability_response(resp) if c["status"] != "PASS"}
    assert "reality_observed" in failed


def test_integ_inferred_response_never_verifies():
    resp = CapabilityResponse(status="SUCCESS", capability="c", connector_id="c",
                              provider="p", reality="INFERRED", data={"x": 1},
                              receipt={"receipt_id": "r", "input_digest": "d"},
                              provenance=["a"])
    failed = {c["check"] for c in verify_capability_response(resp) if c["status"] != "PASS"}
    assert "reality_observed" in failed


def test_integ_researcher_never_emits_verified():
    import tempfile
    from runtime.agent_base import AgentContext
    from runtime.agents.researcher import ResearchAgent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "f.txt"), "w") as f:
            f.write("data")
        ctx = AgentContext(workflow_id="w", task_id="t", task_name="R", agent_id="researcher",
                           scope=tmp, observation_scope=tmp,
                           execution_metadata={"capabilities_requested": ["filesystem.read"]},
                           connector_registry=None)
        result = ResearchAgent().execute(ctx)
    assert result.reality in ("OBSERVED", "FAILED")
    assert result.reality != "VERIFIED"
    assert all(a.get("reality") != "VERIFIED" for a in result.artifacts)


def test_integ_no_agent_module_assigns_verified_reality():
    import pathlib
    import re
    agents_dir = pathlib.Path(__file__).resolve().parents[1] / "runtime" / "agents"
    offenders = []
    for path in agents_dir.glob("*.py"):
        if path.name == "verifier.py":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            # Flags only genuine VERIFIED reality assignments (UNVERIFIED and
            # verification_state mentions do not match).
            if re.search(r"""reality["']?\s*[:=]\s*["']VERIFIED["']""", line):
                offenders.append(f"{path.name}:{i}: {s}")
    assert offenders == []


# ---------------------------------------------------------------------------
# O. Retry integrity at the fabric level
# ---------------------------------------------------------------------------

class _Flaky:
    connector_id = "flaky"
    provider = "flaky"
    version = "1"
    capabilities = {"flaky.cap": {}}
    auth_state = "CONNECTED"
    calls = 0

    def health(self):
        return {"status": "CONNECTED"}

    def execute(self, operation, input_data):
        type(self).calls += 1
        if type(self).calls == 1:
            raise RuntimeError("first attempt fails")
        payload = {"n": type(self).calls}
        rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {"status": "SUCCESS", "data": payload,
                "receipt": {"receipt_id": "r-flaky", "connector_id": "flaky",
                            "provider": "flaky", "operation": operation,
                            "capability": operation, "target": "t", "status": "SUCCESS",
                            "started_at": "s", "completed_at": "e", "duration_seconds": 0.1,
                            "result_hash": rh, "input_digest": "d",
                            "authentication": "X", "error": None}}

    def revoke(self):
        pass

    def refresh(self):
        return {}


def test_integ_retry_failed_then_success_only_success_verifies():
    _Flaky.calls = 0
    reg = ConnectorRegistry()
    reg.register(_Flaky())
    req = CapabilityRequest(capability="flaky.cap", input={}, scope="t", task_id="t", agent_id="a")
    first = execute_capability(reg, req)
    assert first.status == "FAILED" and first.reality == "UNKNOWN"
    second = execute_capability(reg, req)
    assert second.status == "SUCCESS" and second.reality == "OBSERVED"
    # The failed attempt remains failed; only the genuine success verifies.
    assert [c for c in verify_capability_response(first) if c["status"] != "PASS"]
    assert not [c for c in verify_capability_response(second) if c["status"] != "PASS"]
    assert first.data != second.data


def test_integ_tampered_second_attempt_cannot_rewrite_first():
    _Flaky.calls = 1  # next call succeeds immediately
    reg = ConnectorRegistry()
    reg.register(_Flaky())
    req = CapabilityRequest(capability="flaky.cap", input={}, scope="t", task_id="t", agent_id="a")
    good = execute_capability(reg, req)
    tampered = execute_capability(reg, req)
    tampered.data["n"] = 9999
    assert not [c for c in verify_capability_response(good) if c["status"] != "PASS"]
    failed = {c["check"] for c in verify_capability_response(tampered) if c["status"] != "PASS"}
    assert "result_hash_consistent" in failed
    # The valid lineage is untouched by the later tampering.
    assert not [c for c in verify_capability_response(good) if c["status"] != "PASS"]


# ---------------------------------------------------------------------------
# P. Generic verifier architecture separation
# ---------------------------------------------------------------------------

def test_integ_generic_checks_carry_no_provider_fields():
    from runtime.capability_verifiers import GenericCapabilityVerifier
    content = _github_content()
    art = {"artifact_id": "a1", "kind": "research_report", "reality": "OBSERVED",
           "provenance": ["agent:researcher"]}
    checks = GenericCapabilityVerifier.verify_artifact(art, content,
                                                       expected_capability="github.repository.read",
                                                       expected_scope="o/r")
    assert checks
    names = " ".join(c["check"] for c in checks)
    assert "github" not in names and "git" not in names
    details = " ".join(c["detail"] for c in checks).lower()
    assert "full_name" not in details and "github://" not in details


def test_integ_adapters_are_namespaced_and_supplemental():
    from runtime.capability_verifiers import GitCapabilityVerifier, GitHubCapabilityVerifier
    gh = GitHubCapabilityVerifier.verify({"scope": "s", "findings": [], "evidence": []},
                                         expected_scope="s", artifact_reality="OBSERVED")
    assert gh and all(c["check"].startswith("github_") for c in gh)
    git = GitCapabilityVerifier.verify({"scope": "w", "findings": [], "evidence": []},
                                       expected_scope="w", artifact_reality="OBSERVED")
    assert git and all(c["check"].startswith("git_") for c in git)


# ---------------------------------------------------------------------------
# T. Reporter preserves reality classifications
# ---------------------------------------------------------------------------

def test_integ_reporter_preserves_observed_vs_inferred():
    from runtime.agent_base import AgentContext
    from runtime.agents.reporter import ReportAgent
    research = {"scope": "o/r", "source": "GitHubConnector", "capability": "github.repository.read",
                "findings": [{"file": "github://o/r"}], "analysis": {"assessment": "ok"},
                "evidence": [{"type": "github_receipt"}],
                "observation": {"full_name": "o/r", "id": 1}, "receipt": {"receipt_id": "r"}}
    arch = {"steps": [{"s": 1}], "recommendations": ["do-x"]}
    sec = {"risk_level": "LOW", "total_findings": 0, "checks_passed": True}
    content = {"research": research, "architecture_plan": arch, "security_report": sec}

    def _meta(aid, kind):
        return {"artifact_id": aid, "kind": kind, "name": kind, "content_hash": "h",
                "reality": "OBSERVED" if kind == "research_report" else "INFERRED",
                "provenance": ["agent:x"]}

    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="Report", agent_id="reporter",
        scope="o/r", observation_scope="o/r",
        input_artifacts=[_meta("a1", "research_report"), _meta("a2", "architecture_plan"),
                         _meta("a3", "security_report")],
        artifact_contents=[{"artifact_id": "a1", "content": content},
                           {"artifact_id": "a2", "content": {"architecture_plan": arch}},
                           {"artifact_id": "a3", "content": {"security_report": sec}}],
    )
    result = ReportAgent().execute(ctx)
    assert result.reality == "INFERRED"
    report = result.artifacts[0]["content"]["final_report"]
    # Research observation preserved distinctly from inferred analysis.
    assert report["sections"]["research"]["repository"] == "o/r"
    assert report["sections"]["research"]["observation"] == {"full_name": "o/r", "id": 1}
    assert report["sections"]["architecture"]["recommendations"] == ["do-x"]
    assert "VERIFIED" not in json.dumps(result.artifacts[0]["content"])
