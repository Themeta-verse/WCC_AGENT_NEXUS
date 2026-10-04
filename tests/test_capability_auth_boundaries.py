"""Authentication-state semantics, reality boundaries, registry identity,
executor keying, receipt completion, and historical-proof compatibility.

Covers the phase-12 gaps: CONFIGURED vs CONNECTED honesty (E/M9), completed_at
(D/M7), OBSERVED/INFERRED/VERIFIED boundaries (M13), duplicate-registry
prevention (M18), verifier/executor integration (M19), and the historical
GitHub proof contract (M20). No live network, no token required.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.capability_fabric import (
    CANONICAL_RECEIPT_FIELDS,
    USABLE_AUTH_STATES,
    VALID_AUTH_STATES,
    CapabilityRequest,
    CapabilityResolutionError,
    execute_capability,
    resolve_connector,
)
from runtime.connector_registry import ConnectorRegistry


def _fake(auth_state="CONFIGURED", connector_id="c1", capability="demo.cap"):
    from test_capability_architecture import _FakeConnector
    return _FakeConnector(connector_id=connector_id, auth_state=auth_state,
                          capabilities={capability: {}})


def _req(capability="demo.cap"):
    return CapabilityRequest(capability=capability, input={}, scope="s",
                             task_id="t", agent_id="a")


# ---------------------------------------------------------------------------
# M9 / E: CONFIGURED vs CONNECTED
# ---------------------------------------------------------------------------

def test_auth_vocabulary_separates_configured_from_connected():
    assert "CONFIGURED" in VALID_AUTH_STATES
    assert "CONNECTED" in VALID_AUTH_STATES
    assert "CONFIGURED" in USABLE_AUTH_STATES
    assert "NOT_CONFIGURED" not in USABLE_AUTH_STATES


def test_configured_connector_is_selectable_but_flagged_unvalidated():
    from runtime.github_provider import GITHUB_CAPABILITIES, GitHubConnector
    conn = GitHubConnector(token="dummy", connector_id="github", provider="github",
                           version="1.0.0", capabilities=dict(GITHUB_CAPABILITIES))
    assert conn.auth_state == "CONFIGURED"
    assert conn.auth_validated is False
    reg = ConnectorRegistry()
    reg.register(conn)
    assert resolve_connector(reg, "github.repository.read") is conn


def test_connected_preferred_over_configured():
    c1 = _fake("CONFIGURED", "c1")
    c2 = _fake("CONNECTED", "c2")
    reg = ConnectorRegistry()
    reg.register(c1)
    reg.register(c2)
    assert resolve_connector(reg, "demo.cap").connector_id == "c2"


def test_terminal_auth_states_are_not_authorized():
    for state in ("NOT_CONFIGURED", "EXPIRED", "REAUTH_REQUIRED", "REVOKED", "ERROR"):
        reg = ConnectorRegistry()
        reg.register(_fake(state))
        try:
            resolve_connector(reg, "demo.cap")
            raise AssertionError(f"{state} should not resolve")
        except CapabilityResolutionError as exc:
            assert exc.code == "NOT_AUTHORIZED"


def test_configured_stub_researcher_executes():
    from runtime.agent_base import AgentContext
    from runtime.agents.researcher import ResearchAgent
    from test_capability_architecture import _StubOnlyRegistry, _FakeConnector as _FC
    payload = {"full_name": "Themeta-verse/Nexus", "id": 7, "private": True}
    stub = _StubOnlyRegistry(_FC(connector_id="github", provider="github",
                                 capabilities={"github.repository.read": {}},
                                 auth_state="CONFIGURED", payload=payload))
    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="R", agent_id="researcher",
        scope="Themeta-verse/Nexus", observation_scope="Themeta-verse/Nexus",
        execution_metadata={"capabilities_requested": ["github.repository.read"]},
        connector_registry=stub,
    )
    result = ResearchAgent().execute(ctx)
    assert result.status == "COMPLETED"
    assert result.reality == "OBSERVED"


def test_github_capability_catalog_intact():
    from runtime.github_provider import GITHUB_CAPABILITIES
    assert set(GITHUB_CAPABILITIES) == {
        "github.repository.read", "github.repository.list",
        "github.repository.commits.read", "github.repository.issues.read",
    }


# ---------------------------------------------------------------------------
# D / M7: completed_at everywhere
# ---------------------------------------------------------------------------

def test_completed_at_in_fabric_receipts():
    from runtime.capability_fabric import make_canonical_receipt
    r = make_canonical_receipt(
        connector_id="c", provider="p", operation="demo.cap", target="t",
        status="SUCCESS", started_at="2026-01-02T00:00:00+00:00",
        duration_seconds=1.5, result_hash="h", input_digest="d")
    assert r["completed_at"].startswith("2026-01-02T00:00:01")
    assert "completed_at" in CANONICAL_RECEIPT_FIELDS


def test_completed_at_in_both_connector_receipts():
    from runtime.github_provider import GITHUB_CAPABILITIES, GitHubConnector
    from runtime.git_connector import GIT_CAPABILITIES, GitConnector
    gh = GitHubConnector(token="x", connector_id="github", provider="github",
                         version="1.0.0", capabilities=dict(GITHUB_CAPABILITIES))
    r1 = gh._make_receipt("github.repository.read", {"a": 1},
                          "2026-01-02T00:00:00+00:00", 2.0,
                          "github.repository.read", {"owner_repo": "o/r"}, "o/r")
    assert r1["completed_at"].startswith("2026-01-02T00:00:02")
    g = GitConnector(connector_id="git", provider="git", version="1.0.0",
                     capabilities=dict(GIT_CAPABILITIES))
    import tempfile
    from unittest import mock
    from runtime.bounded_agent import ObservationReceipt
    with mock.patch("runtime.tools.git_status") as _gs:
        _gs.return_value = (
            ObservationReceipt(receipt_id="r", agent_id="a", operation="git.status",
                               target_resource="w", requested_capability="git.status",
                               execution_mode="REAL", start_time="s", end_time="e",
                               status="EXECUTED", reality="OBSERVED", reason="",
                               evidence_digest="d", provenance=["p"],
                               content_preview="## main", content_sha256="abc"),
            {"command": "git status", "output": "## main"},
        )
        out = g.execute("git.status", {"workspace": tempfile.gettempdir(), "agent_id": "a"})
    assert out["receipt"]["completed_at"]
    assert out["receipt"]["started_at"] <= out["receipt"]["completed_at"]


# ---------------------------------------------------------------------------
# M13: OBSERVED / INFERRED / VERIFIED boundaries
# ---------------------------------------------------------------------------

def test_researcher_failed_is_unknown_never_observed():
    from runtime.agent_base import AgentContext
    from runtime.agents.researcher import ResearchAgent
    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="R", agent_id="researcher",
        scope="Themeta-verse/Nexus", observation_scope="Themeta-verse/Nexus",
        execution_metadata={"capabilities_requested": ["github.repository.read"]},
        connector_registry=ConnectorRegistry(),
    )
    result = ResearchAgent().execute(ctx)
    assert result.status == "FAILED"
    assert result.reality == "UNKNOWN"
    assert result.reality != "OBSERVED"


def test_reporter_synthesis_is_inferred_never_verified():
    import tempfile
    from runtime.agent_base import AgentContext
    from runtime.agents.reporter import ReportAgent
    with tempfile.TemporaryDirectory() as tmp:
        research_content = {
            "research": {
                "scope": tmp, "findings": [], "analysis": {},
                "evidence": [], "observation": {},
            }
        }
        ctx = AgentContext(
            workflow_id="w", task_id="t", task_name="Report", agent_id="reporter",
            scope=tmp, observation_scope=tmp,
            input_artifacts=[{"artifact_id": "a1", "kind": "research_report",
                              "name": "research_report.json", "content_hash": "h",
                              "reality": "OBSERVED", "provenance": ["agent:researcher"]}],
            artifact_contents=[{"artifact_id": "a1", "kind": "research_report",
                                "name": "research_report.json", "content_hash": "h",
                                "content": research_content, "provenance": []}],
        )
        result = ReportAgent().execute(ctx)
    assert result.reality == "INFERRED"
    assert result.reality != "VERIFIED"
    art = result.artifacts[0]
    assert art.get("reality", "INFERRED") != "VERIFIED"
    assert art["content"].get("reality") == "INFERRED"


def test_no_non_verifier_agent_self_declares_verified():
    import pathlib
    agents_dir = pathlib.Path(__file__).resolve().parents[1] / "runtime" / "agents"
    offenders = []
    for path in agents_dir.glob("*.py"):
        if path.name == "verifier.py":
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if "verifi" in low and "unverifi" not in low and "verifier" not in low:
                if '"VERIFIED"' in line or "'VERIFIED'" in line or "reality=\"VERIFIED\"" in line:
                    offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert offenders == []


# ---------------------------------------------------------------------------
# M18: duplicate-registry prevention
# ---------------------------------------------------------------------------

def test_registry_alias_is_single_canonical_class():
    import runtime.connector_registry as cr
    import runtime.github_provider as gp
    assert gp.ConnectorRegistry is cr.ConnectorRegistry


def test_initialize_is_idempotent_single_object():
    import runtime.github_provider as gp
    gp._github_connector = None
    gp._github_registry = None
    import runtime.capability_fabric as cf
    cf._capability_registry = None
    try:
        r1 = cf.initialize_capability_registry()
        r2 = cf.initialize_capability_registry()
        assert r1 is r2
        assert r1.get_connector("github") is not None
        assert r1.get_connector("git") is not None
        assert len(r1.registration_errors) == 0
    finally:
        gp._github_connector = None
        gp._github_registry = None
        cf._capability_registry = None


# ---------------------------------------------------------------------------
# M19: executor artifact keying for the verifier
# ---------------------------------------------------------------------------

def test_executor_keys_contents_by_artifact_id_for_verifier():
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
        with open(os.path.join(repo, "app.py"), "w") as f:
            f.write("print('hi')\n")
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
            name="research-verify", objective="Research and verify", scope=repo,
            task_specs=[
                {"task_id": "task-0", "name": "Research", "task_type": "research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "depends_on": [], "input_artifacts": []},
                {"task_id": "task-1", "name": "Verify", "task_type": "verification",
                 "agent_id": "verifier", "required_capabilities": [],
                 "depends_on": ["task-0"], "input_artifacts": ["research_report"]},
            ],
            agents=[{"agent_id": "researcher", "name": "R", "role": "researcher",
                     "capabilities": ["filesystem.read"], "allowed_operations": ["read"],
                     "prohibited_operations": [], "scope": {}, "expected_behaviour": ""},
                    {"agent_id": "verifier", "name": "V", "role": "verifier",
                     "capabilities": ["verify"], "allowed_operations": ["read"],
                     "prohibited_operations": [], "scope": {}, "expected_behaviour": ""}],
        )
        wf = engine.create_workflow("t", "p", spec)
        wid = wf["workflow_id"]
        engine.start_workflow("t", "p", wid)
        state = engine.step("t", "p", wid)
        state = engine.step("t", "p", wid)
        kinds = {a["kind"]: a for a in state["artifacts"]}
        assert kinds["research_report"]["reality"] == "OBSERVED"
        # The verifier resolved the upstream artifact by its real artifact ID
        # (not a workflow-level reference) and independently verified it.
        assert kinds["verification_result"]["reality"] == "VERIFIED"


# ---------------------------------------------------------------------------
# M20: historical GitHub proof compatibility
# ---------------------------------------------------------------------------

def test_historical_github_proof_contract():
    import pathlib
    proof_path = pathlib.Path(__file__).resolve().parents[1] / "github-proof-final.json"
    if not proof_path.exists():
        import pytest
        pytest.skip("historical proof file absent; live proof pending credentials")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    assert proof["workflow_id"] == "workflow-wf-1790880609916"
    assert proof["status"] == "COMPLETED"
    assert proof["workspace"] == "Themeta-verse/Nexus"
    tool_caps = {(c.get("capability")) for c in proof.get("tool_calls", [])}
    assert "github.repository.read" in tool_caps
    good_calls = [c for c in proof.get("tool_calls", [])
                  if c.get("capability") == "github.repository.read"]
    assert good_calls and all(c.get("status") == "SUCCESS" for c in good_calls)
    assert all(c.get("reality") == "OBSERVED" for c in good_calls)
    by_kind = {a.get("kind"): a for a in proof.get("artifacts", [])}
    assert by_kind["research_report"]["reality"] == "OBSERVED"
    assert by_kind["research_report"]["provenance"] == [
        "agent:researcher", "type:researcher", "github-connector", "github.repository.read"]
    assert by_kind["verification_result"]["reality"] == "VERIFIED"
    assert proof["verification_result"]["verification_result"]["all_passed"] is True
