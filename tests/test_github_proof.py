"""Focused GitHub proof tests: token -> CONNECTED -> registry -> propagation -> branch -> OBSERVED -> verifier."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reset_singleton():
    import runtime.github_provider as gp
    gp._github_connector = None
    gp._github_registry = None


def test_env_token_yields_configured_not_connected():
    """A present token string is CONFIGURED — never CONNECTED without live validation."""
    _reset_singleton()
    os.environ["NEXUS_GITHUB_TOKEN"] = "dummy-token-for-test"
    try:
        from runtime.github_provider import initialize_github_connector_registration
        conn, reg = initialize_github_connector_registration()
        assert conn is not None and reg is not None
        assert conn.auth_state == "CONFIGURED"
        assert conn.auth_validated is False
        assert conn.auth_detail()["credential_configured"] is True
        assert reg.get_connector("github") is conn
        # ...but a CONFIGURED credential is still resolvable: the live call
        # itself is the validation (proof-chain compatibility).
        from runtime.capability_fabric import resolve_connector
        assert resolve_connector(reg, "github.repository.read") is conn
    finally:
        os.environ.pop("NEXUS_GITHUB_TOKEN", None)
        _reset_singleton()


def test_live_success_promotes_configured_to_connected(monkeypatch):
    """Live validation earns CONNECTED (stubbed transport, no network)."""
    _reset_singleton()
    os.environ["NEXUS_GITHUB_TOKEN"] = "dummy-token-for-test"
    try:
        import json as _json

        class _Resp:
            status_code = 200
            headers = {"X-RateLimit-Remaining": "59"}

            def json(self):
                return {"login": "octocat"}

        class _Client:
            def __init__(self, *a, **k):
                pass

            def get(self, url, **k):
                return _Resp()

        import runtime.github_provider as _gp
        monkeypatch.setattr(_gp.httpx, "Client", _Client)
        from runtime.github_provider import initialize_github_connector_registration
        conn, _ = initialize_github_connector_registration()
        assert conn.auth_state == "CONFIGURED"
        assert conn.validate_auth() is True
        assert conn.auth_state == "CONNECTED"
        assert conn.auth_validated is True
    finally:
        os.environ.pop("NEXUS_GITHUB_TOKEN", None)
        _reset_singleton()


def test_missing_token_yields_not_configured():
    _reset_singleton()
    os.environ.pop("NEXUS_GITHUB_TOKEN", None)
    # Ensure no leaked token from outer env.
    import runtime.github_provider as gp
    from runtime.github_provider import initialize_github_connector_registration
    conn, reg = initialize_github_connector_registration()
    assert conn.auth_state == "NOT_CONFIGURED"
    assert reg.get_connector("github") is conn
    _reset_singleton()


def test_singleton_refresh_when_token_appears():
    _reset_singleton()
    os.environ.pop("NEXUS_GITHUB_TOKEN", None)
    from runtime.github_provider import initialize_github_connector_registration
    conn, _ = initialize_github_connector_registration()
    assert conn.auth_state == "NOT_CONFIGURED"
    os.environ["NEXUS_GITHUB_TOKEN"] = "late-token"
    try:
        conn2, _ = initialize_github_connector_registration()
        assert conn2 is conn
        assert conn2.auth_state == "CONFIGURED"
        assert conn2.auth_validated is False
    finally:
        os.environ.pop("NEXUS_GITHUB_TOKEN", None)
        _reset_singleton()


def test_planner_adds_github_cap_for_owner_repo():
    from runtime.agent_registry import AgentRegistry
    from runtime.multi_agent_executor import register_default_agents
    from runtime.workflow_planner import WorkflowPlanner
    reg = AgentRegistry()
    register_default_agents(reg)
    planner = WorkflowPlanner(agent_registry=reg)
    planned = planner.plan(
        objective="Read the GitHub repository metadata for Themeta-verse/Nexus using the GitHub read capability.",
        scope="Themeta-verse/Nexus",
        tenant_id="t1",
        execution_mode="REAL_READ",
        project_id="p1",
    )
    research = [t for t in planned.task_specs if t["task_type"] == "research"][0]
    assert "github.repository.read" in research["required_capabilities"]


def test_planner_preserves_empty_required():
    from runtime.agent_registry import AgentRegistry
    from runtime.multi_agent_executor import register_default_agents
    from runtime.workflow_planner import WorkflowPlanner
    reg = AgentRegistry()
    register_default_agents(reg)
    planner = WorkflowPlanner(agent_registry=reg)
    planned = planner.plan(
        objective="Review the health of this repository and report risks.",
        scope="/tmp",
        tenant_id="t1",
        execution_mode="REAL_READ",
        project_id="p1",
    )
    for spec in planned.task_specs:
        if spec["task_type"] in ("engineering-analysis", "security-analysis", "qa-analysis"):
            # Must not inject github cap into tasks that never requested it.
            assert "github.repository.read" not in spec["required_capabilities"]


class _StubConnector:
    connector_id = "github"
    provider = "github"
    auth_state = "CONNECTED"
    capabilities = {"github.repository.read": {"scope_kind": "owner_repo"}}
    # Declared as the real GitHubConnector declares it, so the researcher's
    # profile-driven routing selects the repository observation path.
    research_profile = "repository"

    def __init__(self, payload, status="SUCCESS"):
        self._payload = payload
        self._status = status
        self.calls = []

    def execute(self, operation, input_data):
        self.calls.append((operation, dict(input_data)))
        # Normalize scope/owner_repo like the real GitHubConnector
        owner_repo = (input_data.get("owner_repo") or input_data.get("scope") or "").strip()
        receipt = {
            "receipt_id": "receipt-github-test",
            "connector_id": "github",
            "provider": "github",
            "operation": operation,
            "target": owner_repo,
            "result_hash": "abc123",
            "duration_seconds": 0.1,
            "capability": operation,
        }
        if self._status == "SUCCESS":
            return {"status": "SUCCESS", "data": self._payload, "receipt": receipt, "authentication": "TOKEN_ACTIVE"}
        return {"status": "FAILED", "data": {"error": "boom"}, "receipt": receipt, "authentication": "TOKEN_ACTIVE"}


class _StubRegistry:
    """Registry double WITH capability discovery.

    Generic resolution must ask "which connectors declare this capability?".
    Without that surface an agent has to guess a connector id from the
    capability name, which is provider-specific logic in a generic layer.
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


def _ctx(scope="Themeta-verse/Nexus", registry=None, caps=None):
    from runtime.agent_base import AgentContext
    return AgentContext(
        workflow_id="w", task_id="t", task_name="Research repository", agent_id="researcher",
        scope=scope, observation_scope=scope,
        execution_metadata={"capabilities_requested": caps or ["filesystem.read", "github.repository.read"]},
        connector_registry=registry,
    )


def test_researcher_github_branch_observed():
    from runtime.agents.researcher import ResearchAgent
    payload = {"full_name": "Themeta-verse/Nexus", "id": 12345, "private": True, "default_branch": "main", "html_url": "https://github.com/Themeta-verse/Nexus"}
    stub = _StubConnector(payload)
    agent = ResearchAgent()
    result = agent.execute(_ctx(registry=_StubRegistry(stub)))
    assert result.status == "COMPLETED"
    assert result.reality == "OBSERVED"
    assert result.untrusted is False
    assert stub.calls and stub.calls[0][0] == "github.repository.read"
    # Provider-neutral path passes "scope", not "owner_repo". The connector
    # normalizes internally.
    assert stub.calls[0][1].get("scope") == "Themeta-verse/Nexus" or stub.calls[0][1].get("owner_repo") == "Themeta-verse/Nexus"
    art = result.artifacts[0]
    assert art["kind"] == "research_report"
    # Provenance is provider-neutral: connector_id + capability, no provider name
    assert "github.repository.read" in art["provenance"]
    assert "connector:github" in art["provenance"]
    content = art["content"]
    # Generic path produces observation from connector response, not provider-specific metadata
    research = content["research"]
    assert research["observation"]["full_name"] == "Themeta-verse/Nexus"
    assert research["evidence"]
    assert result.execution_metadata["tool_executions"]
    te = result.execution_metadata["tool_executions"][0]
    assert te["capability"] == "github.repository.read" and te["status"] == "SUCCESS"


def test_researcher_explicit_failure_no_fallback():
    from runtime.agents.researcher import ResearchAgent
    agent = ResearchAgent()
    # No usable registry -> must FAIL, not fall back to filesystem.
    #
    # Note: an AgentContext with connector_registry=None now resolves to the
    # canonical process registry rather than "no fabric at all" (that hole let
    # the researcher bypass the fabric entirely). The guarantee under test is
    # therefore about the OUTCOME, not the wording of a particular refusal.
    r1 = agent.execute(_ctx(registry=None))
    assert r1.status == "FAILED" and r1.reality == "UNKNOWN"
    assert r1.artifacts == [], "an unavailable capability must produce no artifact"
    assert r1.execution_metadata.get("tool_executions") == [], \
        "an unavailable capability must report zero tool executions"
    # Not connected -> FAIL.
    stub = _StubConnector({}, status="SUCCESS")
    stub.auth_state = "NOT_CONFIGURED"
    r2 = agent.execute(_ctx(registry=_StubRegistry(stub)))
    assert r2.status == "FAILED"
    # Connector execution FAILED -> agent FAILED (not COMPLETED with empty findings).
    stub2 = _StubConnector({}, status="FAILED")
    r3 = agent.execute(_ctx(registry=_StubRegistry(stub2)))
    assert r3.status == "FAILED"
    # Invalid scope -> FAIL, not filesystem fallback.
    stub3 = _StubConnector({"full_name": "x"})
    r4 = agent.execute(_ctx(scope="not-a-repo", registry=_StubRegistry(stub3)))
    assert r4.status == "FAILED"
    assert stub3.calls == []


def test_verifier_rejects_fake_github_proof():
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent
    # Inferred artifact claiming github lineage but with no observation data.
    bad_content = {"research": {"scope": "Themeta-verse/Nexus", "findings": [], "analysis": {}, "evidence": []}}
    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="Verify", agent_id="verifier",
        scope="Themeta-verse/Nexus", observation_scope="Themeta-verse/Nexus",
        input_artifacts=[{"artifact_id": "a1", "kind": "research_report", "name": "r", "content_hash": "h1", "reality": "INFERRED", "provenance": ["agent:researcher", "github-connector", "github.repository.read"]}],
        artifact_contents=[{"artifact_id": "a1", "content": bad_content}],
    )
    result = VerificationAgent().execute(ctx)
    assert result.reality == "INFERRED"
    assert result.result["all_passed"] is False


def test_verifier_accepts_real_github_proof():
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent
    # Genuine digest (not a placeholder): under the integrity contract the
    # verifier recomputes the content hash, so a real proof must carry one.
    from runtime.agents.verifier import _digest as _vdigest
    meta = {"full_name": "Themeta-verse/Nexus", "id": 999, "private": True}
    content = {"research": {"scope": "Themeta-verse/Nexus", "findings": [{"file": "github://Themeta-verse/Nexus"}], "analysis": {}, "evidence": [{"type": "github_receipt"}], "github_metadata": meta, "observation": meta}}
    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="Verify", agent_id="verifier",
        scope="Themeta-verse/Nexus", observation_scope="Themeta-verse/Nexus",
        input_artifacts=[{"artifact_id": "a1", "kind": "research_report", "name": "r", "content_hash": _vdigest(content), "reality": "OBSERVED", "provenance": ["agent:researcher", "github-connector", "github.repository.read"]}],
        artifact_contents=[{"artifact_id": "a1", "content": content}],
    )
    result = VerificationAgent().execute(ctx)
    assert result.reality == "VERIFIED"
    assert result.result["all_passed"] is True


def test_connector_propagation_runtime_to_context():
    import tempfile
    from runtime.agent_registry import AgentRegistry
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
    from nexus_independent.database import NexusDatabase
    _reset_singleton()
    os.environ["NEXUS_GITHUB_TOKEN"] = "prop-token"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = NexusDatabase(os.path.join(tmp, "t.db"))
            db.migrate()
            registry = AgentRegistry()
            register_default_agents(registry)
            hub = MessagingHub(db)
            executor = MultiAgentExecutor(database=db, agent_registry=registry, settings=None, principal={"tenant_id": "t", "project_id": "p"}, messaging_hub=hub)
            engine = WorkflowEngine(database=db, composer=MissionComposer(), policy=WorkflowExecutionPolicy(), agent_registry=registry, artifacts_root=tmp, messaging_hub=hub)
            engine.set_executor(executor, agent_registry=registry)
            rt = AutonomousRuntime(database=db, engine=engine, executor=executor, agent_registry=registry, messaging_hub=hub, config=AutonomousConfig(tenant_id="t", project_id="p"))
            assert rt.connector_registry is not None
            assert rt.connector_registry.get_connector("github") is not None
            assert executor.connector_registry is rt.connector_registry
            # Same object reaches AgentContext via executor.
            assert executor.connector_registry.get_connector("github").auth_state in ("CONFIGURED", "CONNECTED")
    finally:
        os.environ.pop("NEXUS_GITHUB_TOKEN", None)
        _reset_singleton()
