"""Isolation tests: no-fallback under attack, cross-scope/cross-tenant evidence,
replay behavior, tenant/project binding from request to receipt to verdict.

Real fabric/registry/agents/verifier/engine; fakes only at the connector
boundary. Deterministic, offline, no token.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.capability_fabric import (
    CapabilityRequest,
    _scope_labels,
    execute_capability,
    verify_capability_response,
)
from runtime.connector_registry import ConnectorRegistry


def _vdigest(value):
    from runtime.agents.verifier import _digest as _vd
    return _vd(value)


class _IsoConnector:
    """Counting boundary fake (github-shaped success by default)."""

    def __init__(self, status="SUCCESS", auth_state="CONNECTED"):
        self.connector_id = "github"
        self.provider = "github"
        self.version = "1.0.0"
        self.capabilities = {"github.repository.read": {"risk": "LOW", "scope_kind": "owner_repo"}}
        self.auth_state = auth_state
        # Matches the real GitHubConnector so profile-driven routing picks the
        # repository observation path.
        self.research_profile = "repository"
        self._status = status
        self.calls: list = []

    def health(self):
        return {"status": self.auth_state}

    def execute(self, operation, input_data):
        self.calls.append((operation, dict(input_data)))
        payload = {"full_name": input_data.get("owner_repo", "o/r"), "id": 7}
        rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {"status": self._status,
                "data": payload if self._status == "SUCCESS" else {"error": "denied"},
                "receipt": {
                    "receipt_id": "receipt-iso-1", "connector_id": "github",
                    "provider": "github", "operation": operation, "capability": operation,
                    "target": input_data.get("owner_repo") or input_data.get("scope", "unknown"), "status": self._status,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:00:01+00:00",
                    "duration_seconds": 1.0,
                    "result_hash": rh if self._status == "SUCCESS" else None,
                    "input_digest": hashlib.sha256(b"i").hexdigest(),
                    "authentication": "TOKEN_ACTIVE", "error": None}}

    def revoke(self):
        self.auth_state = "REVOKED"

    def refresh(self):
        return {"auth_state": self.auth_state}

    def metadata(self):
        return {"connector_id": "github"}


def _research_ctx(scope, registry, caps, principal=None, tenant="tA", project="pA"):
    from runtime.agent_base import AgentContext
    return AgentContext(
        workflow_id="w", task_id="t", task_name="R", agent_id="researcher",
        scope=scope, observation_scope=scope,
        execution_metadata={"capabilities_requested": caps},
        connector_registry=registry, principal=principal or {},
        tenant_id=tenant, project_id=project,
    )


def _stamped_github_artifact(scope, tenant, project, art_id="a1", reality="OBSERVED"):
    from runtime.capability_fabric import _digest as _fd
    payload = {"full_name": scope, "id": 7}
    rh = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    receipt = {"receipt_id": "receipt-iso-9", "connector_id": "github", "provider": "github",
               "operation": "github.repository.read", "capability": "github.repository.read",
               "target": scope, "status": "SUCCESS",
               "started_at": "2026-01-01T00:00:00+00:00",
               "completed_at": "2026-01-01T00:00:01+00:00",
               "duration_seconds": 1.0, "result_hash": rh,
               "input_digest": _fd({"owner_repo": scope}),
               "authentication": "TOKEN_ACTIVE", "error": None,
               "tenant_id": tenant, "project_id": project}
    content = {"research": {"scope": scope, "findings": [{"file": f"github://{scope}"}],
                            "analysis": {}, "evidence": [{"type": "github_receipt"}],
                            "github_metadata": dict(payload), "observation": dict(payload),
                            "receipt": receipt}}
    meta = {"artifact_id": art_id, "kind": "research_report", "name": "r",
            "content_hash": _vdigest(content), "reality": reality,
            "provenance": ["agent:researcher", "github-connector", "github.repository.read"]}
    return meta, {"artifact_id": art_id, "content": content}


def _verify(inputs, contents, scope, tenant, project):
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent
    ctx = AgentContext(workflow_id="w", task_id="t", task_name="v", agent_id="verifier",
                       scope=scope, observation_scope=scope, input_artifacts=inputs,
                       artifact_contents=contents, tenant_id=tenant, project_id=project,
                       messaging_hub=None)
    return VerificationAgent().execute(ctx)


# ---------------------------------------------------------------------------
# J. No-fallback under attack (github AND git)
# ---------------------------------------------------------------------------

def test_iso_github_unavailable_no_filesystem_fallback():
    from runtime.agents.researcher import ResearchAgent
    result = ResearchAgent().execute(
        _research_ctx("Themeta-verse/Nexus", ConnectorRegistry(),
                      ["filesystem.read", "github.repository.read"]))
    assert result.status == "FAILED"
    assert result.reality == "UNKNOWN"
    assert result.artifacts == []
    # Assert on EVIDENCE, not on the word "filesystem". An honest failure names
    # the capability it could not obtain; only substituted content would be a
    # violation. The real guarantee: no filesystem-derived observation, and no
    # tool execution record claiming a receipt that was never issued.
    assert result.execution_metadata.get("tool_executions") == [], \
        "no filesystem execution may be reported when the registry has no connectors"
    for key in ("findings", "evidence", "observations", "research_findings"):
        assert key not in result.result, f"no {key} may exist in a fully-failed research run"
    blob = json.dumps(result.result, default=str)
    assert "receipt-" not in blob, "a failed run must not echo any receipt id"


def test_iso_git_unavailable_no_filesystem_fallback():
    import tempfile
    from runtime.agents.researcher import ResearchAgent
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "notes.txt"), "w") as f:
            f.write("local data that must never substitute a git observation")
        result = ResearchAgent().execute(
            _research_ctx(tmp, ConnectorRegistry(), ["git.status"]))
    assert result.status == "FAILED"
    assert result.reality == "UNKNOWN"
    assert result.artifacts == []
    blob = json.dumps(result.result)
    assert "notes.txt" not in blob and "local data" not in blob


def test_iso_git_blocked_refusal_is_failed_not_observed():
    from runtime.agent_base import AgentContext
    from runtime.agents.researcher import ResearchAgent

    class _BlockedRegistry:
        def request_capability(self, request):
            from runtime.capability_fabric import CapabilityResponse
            return CapabilityResponse(
                status="BLOCKED", capability=request.capability, connector_id="git",
                provider="git", reality="OBSERVED", data={"error": "not a git repo"},
                receipt={"receipt_id": "r-b", "connector_id": "git", "provider": "git",
                         "operation": request.capability, "capability": request.capability,
                         "target": "w", "status": "BLOCKED", "started_at": "s",
                         "completed_at": "e", "duration_seconds": 0.1,
                         "result_hash": None, "input_digest": "d",
                         "authentication": "NO_CREDENTIAL_REQUIRED",
                         "error": "not a git repo"},
                provenance=["agent:researcher"])

        def get_connector(self, cid):
            return None

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ctx = AgentContext(
            workflow_id="w", task_id="t", task_name="R", agent_id="researcher",
            scope=tmp, observation_scope=tmp,
            execution_metadata={"capabilities_requested": ["git.status"]},
            connector_registry=_BlockedRegistry())
        result = ResearchAgent().execute(ctx)
    assert result.status == "FAILED"
    assert result.reality == "UNKNOWN"
    assert result.artifacts == []


# ---------------------------------------------------------------------------
# K. Cross-scope / cross-tenant evidence
# ---------------------------------------------------------------------------

def test_iso_request_principal_binds_receipt_and_provenance():
    conn = _IsoConnector()
    reg = ConnectorRegistry()
    reg.register(conn)
    req = CapabilityRequest(capability="github.repository.read",
                            input={"owner_repo": "o/r"}, scope="o/r",
                            task_id="t", agent_id="a",
                            principal={"tenant_id": "tenant-A", "project_id": "project-A"})
    assert _scope_labels(req) == {"tenant_id": "tenant-A", "project_id": "project-A"}
    resp = execute_capability(reg, req)
    assert resp.receipt["tenant_id"] == "tenant-A"
    assert resp.receipt["project_id"] == "project-A"
    assert "tenant:tenant-A" in resp.provenance
    assert "project:project-A" in resp.provenance


def test_iso_cross_tenant_evidence_rejected():
    meta, entry = _stamped_github_artifact("o/r", "tenant-A", "project-A")
    result = _verify([meta], [entry], scope="o/r", tenant="tenant-B", project="project-A")
    checks = {c["check"]: c["status"] for c in result.result["verification"]["checks"]}
    assert checks.get("receipt_tenant_id_match") == "FAIL"
    assert result.reality == "INFERRED"


def test_iso_cross_project_evidence_rejected():
    meta, entry = _stamped_github_artifact("o/r", "tenant-A", "project-A")
    result = _verify([meta], [entry], scope="o/r", tenant="tenant-A", project="project-B")
    checks = {c["check"]: c["status"] for c in result.result["verification"]["checks"]}
    assert checks.get("receipt_project_id_match") == "FAIL"
    assert result.reality == "INFERRED"


def test_iso_matching_scope_identity_verifies():
    meta, entry = _stamped_github_artifact("o/r", "tenant-A", "project-A")
    result = _verify([meta], [entry], scope="o/r", tenant="tenant-A", project="project-A")
    assert result.reality == "VERIFIED"


def test_iso_wrong_repository_target_rejected():
    meta, entry = _stamped_github_artifact("other/repo", "tenant-A", "project-A")
    result = _verify([meta], [entry], scope="o/r", tenant="tenant-A", project="project-A")
    assert result.reality == "INFERRED"


def test_iso_malicious_principal_shape_is_sanitized_or_detected():
    # A caller-supplied principal is identity, not a secret channel: PAT
    # shapes inside principal labels must not survive silently.
    req = CapabilityRequest(capability="github.repository.read",
                            input={"owner_repo": "o/r"}, scope="o/r",
                            task_id="t", agent_id="a",
                            principal={"tenant_id": "ghp_evilshapedvalue"})
    reg = ConnectorRegistry()
    reg.register(_IsoConnector())
    resp = execute_capability(reg, req)
    failed = {c["check"] for c in verify_capability_response(resp) if c["status"] != "PASS"}
    assert "no_secrets_in_receipt" in failed or "authentication_no_secret" in failed \
        or "ghp_evilshapedvalue" not in json.dumps(resp.receipt)


# ---------------------------------------------------------------------------
# R. One canonical registry along the production path
# ---------------------------------------------------------------------------

def test_iso_single_registry_runtime_executor_context():
    import tempfile
    from runtime.agent_base import AgentContext
    from runtime.agent_registry import AgentRegistry
    from runtime.autonomous_runtime import AutonomousConfig, AutonomousRuntime
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from nexus_independent.database import NexusDatabase

    captured = {}

    class _Probe:
        agent_id = "probe"
        name = "Probe"
        role = "probe"
        capabilities = ["probe.read"]
        allowed_operations = ["read"]
        prohibited_operations = []

        def execute(self, context: AgentContext):
            from runtime.agent_base import AgentExecutionResult
            captured["registry"] = context.connector_registry
            return AgentExecutionResult(task_id=context.task_id, agent_id="probe",
                                        status="COMPLETED", reality="INFERRED",
                                        untrusted=True, result={}, artifacts=[])

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
        registry.register_agent(agent_id="probe", name="Probe", role="probe",
                                agent_type="SPECIALIST", capabilities=["probe.read"],
                                allowed_operations=["read"], prohibited_operations=[],
                                expected_behaviour="probe", instance=_Probe())
        hub = MessagingHub(db)
        executor = MultiAgentExecutor(database=db, agent_registry=registry, settings=None,
                                      principal={"tenant_id": "t", "project_id": "p"},
                                      messaging_hub=hub)
        engine = WorkflowEngine(database=db, composer=MissionComposer(),
                                policy=WorkflowExecutionPolicy(),
                                agent_registry=registry, artifacts_root=tmp, messaging_hub=hub)
        engine.set_executor(executor, agent_registry=registry)
        rt = AutonomousRuntime(database=db, engine=engine, executor=executor,
                               agent_registry=registry, messaging_hub=hub,
                               config=AutonomousConfig(tenant_id="t", project_id="p"))
        assert rt.connector_registry is executor.connector_registry
        from runtime.workflow_engine import WorkflowSpec
        wf = engine.create_workflow(
            "t", "p",
            WorkflowSpec(name="probe-flow", objective="probe", scope="s",
                         task_specs=[{"task_id": "task-0", "name": "Probe",
                                      "task_type": "probe", "agent_id": "probe",
                                      "required_capabilities": ["probe.read"],
                                      "depends_on": [], "input_artifacts": []}],
                         agents=[{"agent_id": "probe", "name": "Probe", "role": "probe",
                                  "capabilities": ["probe.read"],
                                  "allowed_operations": ["read"],
                                  "prohibited_operations": [], "scope": {},
                                  "expected_behaviour": "probe"}]))
        executor.execute_task(workflow_id=wf["workflow_id"], task_id="task-0",
                              task_type="probe", task_name="Probe", agent_id="probe",
                              capabilities=["probe.read"], scope="s",
                              input_artifacts=[], parameters={})
        assert captured.get("registry") is rt.connector_registry


# ---------------------------------------------------------------------------
# S. Tool/event trace integrity
# ---------------------------------------------------------------------------

def _engine_stack(tmp):
    from runtime.agent_registry import AgentRegistry
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec
    from nexus_independent.database import NexusDatabase
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
    fake = _IsoConnector()
    reg.register(fake)
    executor.connector_registry = reg
    engine = WorkflowEngine(database=db, composer=MissionComposer(),
                            policy=WorkflowExecutionPolicy(max_retries_default=0),
                            agent_registry=registry, artifacts_root=tmp, messaging_hub=hub)
    engine.set_executor(executor, agent_registry=registry)
    return db, engine, fake


def _github_spec():
    from runtime.workflow_engine import WorkflowSpec
    return WorkflowSpec(
        name="iso", objective="Read o/r", scope="Themeta-verse/Nexus",
        task_specs=[{"task_id": "task-0", "name": "Research", "task_type": "research",
                     "agent_id": "researcher",
                     "required_capabilities": ["github.repository.read"],
                     "depends_on": [], "input_artifacts": []}],
        agents=[{"agent_id": "researcher", "name": "R", "role": "researcher",
                 "capabilities": ["filesystem.read", "github.repository.read"],
                 "allowed_operations": ["read"], "prohibited_operations": [],
                 "scope": {}, "expected_behaviour": ""}],
    )


def test_iso_tool_events_correspond_to_real_execution():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db, engine, fake = _engine_stack(tmp)
        wf = engine.create_workflow("t", "p", _github_spec())
        wid = wf["workflow_id"]
        engine.start_workflow("t", "p", wid)
        state = engine.step("t", "p", wid)
        tool_events = [e for e in state["events"] if e.get("event_type") == "tool_used"]
        assert len(fake.calls) == 1
        assert len(tool_events) == len(fake.calls) == 1
        detail = tool_events[0].get("detail", {})
        assert detail.get("capability") == "github.repository.read"
        assert detail.get("status") == "SUCCESS"
        assert detail.get("reality") == "OBSERVED"
        assert detail.get("target") == "Themeta-verse/Nexus"


def test_iso_failed_execution_produces_no_success_tool_event():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db, engine, fake = _engine_stack(tmp)
        fake._status = "FAILED"
        wf = engine.create_workflow("t", "p", _github_spec())
        wid = wf["workflow_id"]
        engine.start_workflow("t", "p", wid)
        state = engine.step("t", "p", wid)
        tool_events = [e for e in state["events"] if e.get("event_type") == "tool_used"]
        assert state["tasks"][0]["status"] == "FAILED"
        assert all(e.get("detail", {}).get("status") != "SUCCESS" for e in tool_events)
        assert [a for a in state["artifacts"] if a.get("reality") == "OBSERVED"] == []
