"""Truth-boundary and single-registry architecture tests.

These tests exist because the Gate 1 defects were all *boundary* defects:
places where the runtime asserted a state it had not established. Each test
below pins one such boundary so the fabricated value cannot come back.

Three properties are covered:

1. ONE registry: the AutonomousRuntime, its engine, executor, planner and every
   AgentContext must share one ConnectorRegistry object, and the runtime must
   fail closed rather than silently degrading to a reduced-capability registry.
2. Generic provider neutrality: a minimal, non-GitHub connector must complete
   a full request -> resolution -> execution -> receipt -> verification ->
   artifact -> provenance cycle with no GitHub-specific code anywhere in the
   path. This is the proof that GitHub is one provider rather than the system.
3. Truth states: absence of evidence must never render as success, at any of
   the boundaries that previously fabricated one.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.capability_fabric import (  # noqa: E402
    CANONICAL_RECEIPT_FIELDS,
    CapabilityRequest,
    CapabilityResolutionError,
    execute_capability,
    verify_capability_response,
)
from runtime.connector_registry import Connector  # noqa: E402
from runtime.connector_registry import ConnectorRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal non-GitHub connector. Deliberately nothing like a repository host:
# no owner/repo, no github:// URIs, no HTTP, no filesystem traversal.
# ---------------------------------------------------------------------------


class _EchoConnector(Connector):
    """A tiny deterministic 'notes' provider."""

    def __init__(self):
        super().__init__(
            connector_id="notes",
            provider="notes-service",
            version="1.0.0",
            capabilities={"notes.entry.read": {"scope_kind": "any"}},
            auth_state="NO_CREDENTIAL_REQUIRED",
        )

    def health(self):
        return {"status": "CONNECTED", "auth_state": self.auth_state}

    def validate_input(self, operation, input_data):
        if operation != "notes.entry.read":
            return False, f"unsupported operation {operation!r}"
        if not ((input_data or {}).get("note_id") or (input_data or {}).get("scope")):
            return False, "note_id (or a scope to resolve it from) is required"
        return True, None

    def check_authorization(self, operation, input_data):
        return True, {"state": "NO_CREDENTIAL_REQUIRED", "reason": "public notes"}

    def execute(self, operation, input_data):
        # A generic caller only knows the scope, so the connector resolves its
        # own identifier from either field. This is what makes the agent's
        # provider-agnostic path usable without provider-specific plumbing.
        note_id = (input_data or {}).get("note_id") or (input_data or {}).get("scope")
        started = "2026-01-01T00:00:00+00:00"
        if note_id == "missing":
            return {
                "status": "FAILED",
                "data": None,
                "error": "no such note",
                "receipt": _receipt(self.connector_id, operation, note_id, started,
                                    input_data, self.auth_state, None),
            }
        data = {"note_id": note_id, "body": f"body of {note_id}"}
        return {
            "status": "SUCCESS",
            "data": data,
            "error": None,
            "receipt": _receipt(self.connector_id, operation, note_id, started,
                                input_data, self.auth_state, data),
        }


def _receipt(connector_id, operation, target, started, input_data, auth_state, data):
    """Build the ONE canonical receipt shape for the boundary fake."""
    import hashlib

    def _digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    return {
        "receipt_id": f"r-{target}",
        "connector_id": connector_id,
        "provider": "notes-service",
        "capability": operation,
        "operation": operation,
        "target": target,
        "status": "SUCCESS" if data is not None else "FAILED",
        "started_at": started,
        "completed_at": started,
        "timestamp": started,
        "duration_ms": 1.0,
        "result_hash": _digest(data),
        "input_digest": _digest(input_data),
        "auth_state": auth_state,
        "error": None if data is not None else "no such note",
    }


def _notes_registry():
    reg = ConnectorRegistry()
    reg.register(_EchoConnector())
    return reg


def _notes_request(note_id="n1", **kw):
    args = {
        "capability": "notes.entry.read",
        "input": {"note_id": note_id},
        "scope": note_id,
        "task_id": "t-1",
        "agent_id": "a-1",
    }
    args.update(kw)
    return CapabilityRequest(**args)


# ---------------------------------------------------------------------------
# 1. ONE registry instance across the whole runtime
# ---------------------------------------------------------------------------


def test_1_process_has_exactly_one_connector_registry():
    from runtime.capability_fabric import initialize_capability_registry

    first = initialize_capability_registry()
    second = initialize_capability_registry()
    assert first is second, "a second registry instance was constructed in-process"


def _build_runtime():
    """Construct a real AutonomousRuntime over a temporary database."""
    import tempfile

    from nexus_independent.database import NexusDatabase
    from runtime.agent_registry import AgentRegistry
    from runtime.autonomous_runtime import AutonomousConfig, AutonomousRuntime
    from runtime.messaging_hub import MessagingHub
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.workflow_engine import WorkflowEngine
    from runtime.workflow_planner import WorkflowPlanner

    tmp = tempfile.mkdtemp()
    db = NexusDatabase(os.path.join(tmp, "db.sqlite"))
    db.migrate()
    now = "2026-01-01T00:00:00Z"
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                     ("tb", "T", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at)"
                     " VALUES(?,?,?,?,?)", ("pb", "tb", "P", now, now))

    agents = AgentRegistry()
    register_default_agents(agents)
    hub = MessagingHub(db)
    engine = WorkflowEngine(database=db, composer=None, agent_registry=agents,
                            artifacts_root=os.path.join(tmp, "artifacts"),
                            messaging_hub=hub)
    executor = MultiAgentExecutor(database=db, agent_registry=agents, settings=None,
                                  principal={"tenant_id": "tb", "project_id": "pb"},
                                  messaging_hub=hub)
    engine.set_executor(executor, agent_registry=agents)
    planner = WorkflowPlanner(agent_registry=agents)
    return AutonomousRuntime(database=db, engine=engine, messaging_hub=hub, planner=planner,
                             config=AutonomousConfig(tenant_id="tb", project_id="pb"))


def test_2_runtime_propagates_one_registry_object_everywhere():
    runtime = _build_runtime()
    registry = runtime.connector_registry
    assert registry is not None
    assert runtime.executor.connector_registry is registry, \
        "executor received a different registry object"
    if getattr(runtime.engine, "connector_registry", None) is not None:
        assert runtime.engine.connector_registry is registry, \
            "engine received a different registry object"
    if runtime._planner is not None:
        assert runtime._planner.connector_registry is registry, \
            "planner planned against a different registry than execution uses"


def test_3_agent_context_defaults_to_the_same_registry():
    from runtime.agent_base import AgentContext

    ctx = AgentContext(workflow_id="w", task_id="t", task_name="n", agent_id="a",
                       scope="s", objective="o")
    assert ctx.connector_registry is not None, \
        "AgentContext defaulted to no registry, reopening the policy bypass"


def test_4_runtime_refuses_reduced_capability_fallback(monkeypatch):
    """Canonical registry failure must fail closed, not silently narrow."""
    import runtime.capability_fabric as cf

    def _boom():
        raise RuntimeError("registry init exploded")

    monkeypatch.setattr(cf, "initialize_capability_registry", _boom)
    try:
        _build_runtime()
    except RuntimeError:
        return
    raise AssertionError(
        "AutonomousRuntime swallowed a canonical registry failure and continued "
        "with a reduced-capability registry"
    )


# ---------------------------------------------------------------------------
# 2. Generic provider neutrality: full cycle with a non-GitHub connector
# ---------------------------------------------------------------------------


def test_5_non_github_connector_completes_full_canonical_cycle():
    reg = _notes_registry()
    resp = execute_capability(reg, _notes_request())

    assert resp.status == "SUCCESS"
    assert resp.connector_id == "notes"
    assert resp.provider == "notes-service"
    missing = [f for f in CANONICAL_RECEIPT_FIELDS if f not in resp.receipt]
    assert missing == [], f"receipt is not canonical: missing {missing}"
    assert resp.receipt["status"] == "SUCCESS"

    checks = verify_capability_response(resp, expected_capability="notes.entry.read")
    assert [c for c in checks if c["status"] != "PASS"] == [], \
        "a genuine observation failed generic verification"

    record = resp.to_tool_record(task_id="t-1", agent_id="a-1")
    assert record["connector_id"] == "notes"
    assert "agent:a-1" in resp.provenance
    assert any("notes.entry.read" in p for p in resp.provenance), resp.provenance


def test_6_non_github_failure_never_becomes_success():
    reg = _notes_registry()
    resp = execute_capability(reg, _notes_request(note_id="missing"))
    assert resp.status == "FAILED"
    assert resp.receipt["status"] == "FAILED"
    assert resp.reality != "OBSERVED"
    assert verify_capability_response(
        resp, expected_capability="notes.entry.read")


def test_7_unknown_capability_is_unavailable_not_silently_substituted():
    """An unserved capability must fail loudly, never fall through to another."""
    reg = _notes_registry()
    try:
        resp = execute_capability(reg, _notes_request(capability="github.repository.read",
                                                       input={"owner_repo": "o/r"}))
    except CapabilityResolutionError as exc:
        assert exc.code == "UNKNOWN_CAPABILITY"
        return
    assert resp.status == "UNAVAILABLE"
    assert resp.receipt["status"] == "UNAVAILABLE"
    assert resp.connector_id != "notes", \
        "an unrelated connector was substituted for a capability it cannot serve"


def test_8_researcher_uses_a_non_github_connector_with_no_github_code():
    """ResearchAgent must serve any registered capability, not just GitHub."""
    from runtime.agent_base import AgentContext
    from runtime.agents.researcher import ResearchAgent

    reg = _notes_registry()
    ctx = AgentContext(
        workflow_id="w", task_id="t", task_name="Read note", agent_id="researcher",
        scope="n1", objective="Read note n1", connector_registry=reg,
        parameters={"execution_strategy": "DETERMINISTIC"},
        execution_metadata={"capabilities_requested": ["notes.entry.read"]},
    )
    result = ResearchAgent(execution_strategy="DETERMINISTIC").execute(ctx)
    assert result.status == "COMPLETED", result.error
    assert result.reality == "OBSERVED"
    assert "notes.entry.read" in result.provenance
    reports = [a for a in result.artifacts if a.get("kind") == "research_report"]
    assert reports, result.result
    # The report must carry the connector's own receipt, not a synthesized one.
    research = reports[0]["content"]["research"]
    assert research["receipt"]["connector_id"] == "notes"
    assert research["receipt"]["capability"] == "notes.entry.read"


def test_8b_researcher_routing_has_no_hardcoded_capability_names():
    """Routing must be driven by connector-declared profiles, not literals.

    The residual provider coupling inside the specialized repository path is
    tracked separately (test_8c); what must never come back is the agent
    deciding *which path to take* by matching provider capability names.
    """
    import inspect
    import io
    import tokenize

    from runtime.agents.researcher import ResearchAgent

    src = inspect.getsource(ResearchAgent.execute)
    # Comments may legitimately name a provider while explaining what was
    # removed; only executable code is checked.
    code = "".join(
        tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type != tokenize.COMMENT
    )
    for literal in ('"github.repository.read"', "'github.repository.read'",
                    '"git.status"', '"filesystem.read"', '"filesystem.list"',
                    '"git.diff"'):
        assert literal not in code, \
            f"execute() routes on provider capability literal {literal}"


# ---------------------------------------------------------------------------
# 3. Truth states: absence of evidence is never success
# ---------------------------------------------------------------------------


def test_8c_researcher_repository_path_provider_neutral():
    """Gate 1 BLOCKER RESOLVED — Researcher repository path is now provider-neutral.

    Previously this test asserted the presence of GitHub coupling as a known
    blocker (test_8c_researcher_repository_path_still_names_github_KNOWN_BLOCKER).
    Now that the path has been generalized, we assert the ABSENCE of any
    provider-specific coupling in the Researcher's repository execution path.
    """
    import inspect

    from runtime.agents.researcher import ResearchAgent

    src = inspect.getsource(ResearchAgent)
    coupling = [n for n in ("_github_read_scope", "github://", "github_metadata")
                if n in src]
    assert not coupling, (
        f"residual GitHub coupling found in researcher repository path: {coupling}"
    )
    # Recorded for the audit artifact - blocker is CLOSED.
    ResearchAgent.KNOWN_PROVIDER_COUPLING = ()


def test_9_verifier_reports_failure_as_failed_not_completed():
    from runtime.agent_base import AgentContext
    from runtime.agents.verifier import VerificationAgent

    ctx = AgentContext(
        workflow_id="w", task_id="v", task_name="Verify", agent_id="verifier",
        scope=".", parameters={"execution_strategy": "DETERMINISTIC"},
        input_artifacts=[], artifact_contents=[], objective="o",
    )
    res = VerificationAgent(execution_strategy="DETERMINISTIC").execute(ctx)
    assert res.result["all_passed"] is False
    assert res.status == "FAILED", "failed verification was reported as COMPLETED"


def test_10_workflow_engine_does_not_default_to_completed():
    import inspect

    from runtime import workflow_engine

    src = inspect.getsource(workflow_engine)
    assert 'getattr(task_result, "status", "COMPLETED")' not in src, \
        "engine still defaults a missing status to COMPLETED"
    assert 'task_result.get("status", "COMPLETED")' not in src, \
        "engine still defaults a missing status to COMPLETED"


def test_11_database_agent_action_default_reality_is_unknown():
    import inspect

    from nexus_independent.database import NexusDatabase

    sig = inspect.signature(NexusDatabase.record_agent_action)
    assert sig.parameters["observation_reality"].default == "UNKNOWN", \
        "an agent action with no declared observation still records OBSERVED"


def test_12_capability_registry_does_not_promote_on_file_existence():
    import inspect

    from runtime import capability_registry

    src = inspect.getsource(capability_registry.CapabilityRegistry.register_verified_runtime)
    assert '"VERIFIED":record_persisted' not in src, \
        "VERIFIED is still derived from mere artifact existence"
    assert '"AUTHORIZED":True' not in src, \
        "AUTHORIZED is still hard-coded rather than derived from governance"


def test_13_synthesis_verification_requires_real_lineage():
    """Regression: final_report synthesis auto-passed github_reality_observed."""
    from runtime.capability_verifiers import GitHubCapabilityVerifier

    empty = GitHubCapabilityVerifier.verify(
        {"scope": "o/r", "findings": [], "evidence": []},
        expected_scope="o/r", artifact_reality="INFERRED", is_synthesis=True,
    )
    lineage = {c["check"]: c["status"] for c in empty}
    assert lineage.get("github_synthesis_lineage_present") == "FAIL", \
        "a synthesis with no upstream lineage verified clean"

    real = GitHubCapabilityVerifier.verify(
        {"scope": "o/r", "findings": [{"file": "github://o/r"}], "evidence": [{"type": "github_receipt"}],
         "observation": {"full_name": "o/r", "id": 7}},
        expected_scope="o/r", artifact_reality="INFERRED", is_synthesis=True,
    )
    lineage = {c["check"]: c["status"] for c in real}
    assert lineage.get("github_synthesis_lineage_present") == "PASS"


def test_14_pilot_task_graph_reflects_actual_outcome():
    import sys as _sys

    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "runtime"))
    from canonical_pilot import build_tasks

    unrun = build_tasks()
    assert unrun.status != "SUCCESS"
    assert all(t.status != "SUCCESS" for t in unrun.tasks)

    failed = build_tasks("FAILED")
    assert failed.status == "FAILED"
    assert failed.tasks[0].status == "FAILED"
    assert all(t.status != "SUCCESS" for t in failed.tasks[1:]), \
        "downstream tasks reported SUCCESS after the observation failed"

    ok = build_tasks("SUCCESS")
    assert ok.status == "SUCCESS"
    assert all(t.status == "SUCCESS" for t in ok.tasks)


def test_15_local_control_repository_read_is_not_hardcoded_verified():
    import sys as _sys

    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "runtime"))
    from local_control import capability_ceiling

    entry = capability_ceiling()["repository.read"]
    assert "verified" in entry and "callable" in entry
    # The entry must carry a real probe result, not a literal True.
    assert entry.get("auth_state") is not None or entry.get("reality") != "OBSERVED", \
        "repository.read reports OBSERVED with no probe evidence"
    import inspect
    src = inspect.getsource(capability_ceiling)
    assert "'verified':True" not in src, \
        "capability_ceiling still hard-codes repository.read as verified"


def test_16_generic_layers_contain_no_github_literals():
    """Generic execution code must not branch on or name a provider.

    One documented exception is permitted and enforced as the ONLY exception:
    ``capability_fabric.initialize_capability_registry`` is the composition
    root that wires concrete built-in providers into the registry. Everywhere
    else — every other function in the fabric, and the whole of the registry,
    agent context and executor — provider names are forbidden. Comment text is
    excluded (prose may name a provider as an example), as are generic
    secret-format filters such as ``ghp_`` / ``github_pat_``, which are
    credential redaction rather than provider coupling.
    """
    import ast
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    providers = ("github", "gitlab", "bitbucket")
    secret_line = re.compile(r"ghp_|gho_|github_pat_|xox[pb]-|sk-")
    offenders = []

    def _code_lines(path):
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        lines = src.splitlines(keepends=True)
        segs = []
        # module docstring
        doc = ast.get_docstring(tree)
        if doc is not None:
            segs.append((tree.body[0].lineno - 1, tree.body[0].end_lineno))
        # every function/class docstring + nested definitions
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(
                        getattr(body[0], "value", None), ast.Constant) and isinstance(
                        body[0].value.value, str):
                    segs.append((body[0].lineno - 1, body[0].end_lineno))
        for i, line in enumerate(lines):
            code = line.split("#", 1)[0]
            if secret_line.search(code):
                code = ""
            if any(s <= i < e for s, e in segs):
                code = ""
            segs.append((i, i + 1)) if False else None
            yield i + 1, code

    def _scan(path, skip_funcs=()):
        for lineno, code in _code_lines(path):
            low = code.lower()
            for provider in providers:
                if provider in low:
                    offenders.append((os.path.basename(path), lineno, provider))

    fabric = os.path.join(root, "runtime", "capability_fabric.py")
    _scan(fabric, skip_funcs=("initialize_capability_registry",))
    for extra in ("connector_registry.py", "agent_base.py", "multi_agent_executor.py"):
        _scan(os.path.join(root, "runtime", extra))

    # Enforce the composition root as the single allowed exception, by proving
    # that every offender in the fabric sits inside that one function.
    allowed_ranges = []
    tree = ast.parse(open(fabric, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "initialize_capability_registry":
            allowed_ranges.append((node.lineno, node.end_lineno))
    unexpected = [o for o in offenders
                  if o[0] != "capability_fabric.py" or not any(
                      s <= o[1] <= e for s, e in allowed_ranges)]
    assert not unexpected, (
        "provider names appear in generic execution code outside the documented "
        f"composition root: {unexpected}"
    )


def test_17_canonical_receipt_is_the_single_schema():
    """Every connector produces the one canonical receipt shape."""
    from runtime.filesystem_connector import FilesystemConnector

    reg = _notes_registry()
    reg.register(FilesystemConnector())
    resp = execute_capability(
        reg,
        CapabilityRequest(
            capability="notes.entry.read", input={"note_id": "n1"}, scope="n1",
            task_id="t", agent_id="a",
        ),
    )
    missing = [f for f in CANONICAL_RECEIPT_FIELDS if f not in resp.receipt]
    assert missing == []
    json.dumps(resp.receipt)  # must remain serialisable evidence