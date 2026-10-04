"""NEXUS Phase 11 — Model-driven local coding: deterministic proof.

A scripted (deterministic, in-process) model provider stands in for any
vendor LLM behind the provider-neutral ModelRouter. Every test proves that
model output is treated as INFERRED proposal only, and that reality comes
solely from connector/process/filesystem observations plus the independent
verifier:

  A. model proposal -> coding task (params carry model authorship)
  B. model proposal -> actual file creation (exact bytes on disk)
  C. model proposal -> actual test execution (exit codes, captured stdout)
  D. test failure -> model diagnosis -> bounded repair
  E. successful repair -> independent verification (VERIFIED)
  F. model claims success while execution fails -> workflow FAILS
  G. model claims tests pass while tests fail -> workflow FAILS
  H. malformed model proposal -> rejected safely (nothing executes)
  I. model attempts forbidden operation -> policy blocks it (held, never run)
  J. secret-shaped model output -> rejected/scrubbed, never persisted raw
  K. provider swap changes no core logic (same outcome, different provenance)
  L. no model-originated statement can create VERIFIED status

Approval seam:
  M1. consequential ops pause the run (APPROVAL_PENDING, task AWAITING_APPROVAL)
  M2. APPROVED resumes and executes with the grant recorded
  M3. REJECTED prevents execution and is recorded; empty remainder FAILS honestly
  M4. nothing is auto-approved to make tests pass
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime import model_coding as mc
from runtime.agent_registry import AgentRegistry
from runtime.capability_fabric import initialize_capability_registry
from runtime.messaging_hub import MessagingHub
from runtime.mission_composer import MissionComposer
from runtime.model_router import ModelProvider, ModelProviderError, ModelResponse, ModelRouter
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from runtime.truth_boundary import assert_no_upgrade
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
from runtime.workflow_planner import WorkflowPlanner
from nexus_independent.database import NexusDatabase

TENANT = "p11-tenant"
PROJECT = "p11-project"

CALC_V1 = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "def sub(a, b):\n"
    "    return a - b\n"
    "\n"
    "def mul(a, b):\n"
    "    return a * b\n"
    "\n"
    "def div(a, b):\n"
    "    return a / b\n"
)

CALC_TEST = (
    "from calc import add, sub, mul, div\n"
    "\n"
    "def test_add():\n"
    "    assert add(2, 3) == 5\n"
    "\n"
    "def test_sub():\n"
    "    assert sub(5, 3) == 2\n"
    "\n"
    "def test_mul():\n"
    "    assert mul(2, 3) == 6\n"
    "\n"
    "def test_div():\n"
    "    assert div(6, 3) == 2\n"
)

CALC_V1_BROKEN = CALC_V1.replace("    return a - b\n", "    return a + b  # BUG\n")

CALC_V1_FIXED = CALC_V1


def _proposal_json(**overrides):
    base = {
        "objective": "calculator module with tests",
        "files_to_create": {"calc.py": CALC_V1, "test_calc.py": CALC_TEST},
        "tests_to_run": [{"capability": "project.test.run",
                           "test_args": ["-q", "test_calc.py"],
                           "expect_exit_code": 0}],
        "reasoning_summary": "scripted proposal for tests",
    }
    base.update(overrides)
    return json.dumps(base)


class ScriptedProvider(ModelProvider):
    """Deterministic stand-in for any vendor LLM: queued JSON replies."""

    name = "scripted-test"

    def __init__(self, responses, *, name="scripted-test", model="scripted-model-v1"):
        self._responses = list(responses)
        self.calls = []
        self.model_name = model
        # instance-level name so two scripted providers can coexist
        self.name = name

    def health(self):
        return {"status": "AVAILABLE", "availability": True,
                "type": "deterministic_script", "offline": True}

    def complete(self, prompt, *, system=None, schema=None, temperature=0.2,
                 timeout=30, model=None):
        self.calls.append({"prompt": prompt, "system": system})
        if not self._responses:
            raise ModelProviderError(self.name, "script exhausted: no more canned replies")
        content = self._responses.pop(0)
        structured = None
        if content.strip().startswith("{"):
            try:
                structured = json.loads(content)
            except ValueError:
                structured = None
        return ModelResponse(
            content=content, model=model or self.model_name, provider=self.name,
            reality="INFERRED", untrusted=True, status="SUCCESS",
            structured=structured, raw_response={"scripted": True})


def _stack(tmpdir: str, provider, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.delenv("NEXUS_AUTO_APPROVE", raising=False)
    db_path = os.path.join(tmpdir, "p11.db")
    db = NexusDatabase(db_path)
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                      (TENANT, "P11", now))
        conn.execute(
            "INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) "
            "VALUES(?,?,?,?,?)", (PROJECT, TENANT, "P11", now, now))
        conn.commit()
    registry = AgentRegistry()
    register_default_agents(registry)
    connector_registry = initialize_capability_registry()
    hub = MessagingHub(db)
    executor = MultiAgentExecutor(
        database=db, agent_registry=registry, settings=None,
        principal={"tenant_id": TENANT, "project_id": PROJECT}, messaging_hub=hub,
        connector_registry=connector_registry)
    artifacts_root = Path(tmpdir) / "artifacts"
    engine = WorkflowEngine(
        database=db, composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(max_retries_default=1, fail_on_agent_not_available=True,
                                       auto_retry_on_failure=False),
        agent_registry=registry, artifacts_root=artifacts_root, messaging_hub=hub)
    engine.set_executor(executor, agent_registry=registry, connector_registry=connector_registry)
    engine.messaging_hub = hub
    planner = WorkflowPlanner(agent_registry=registry, connector_registry=connector_registry)
    from runtime.model_router import TASK_DEBUGGING, TASK_IMPLEMENTATION
    router = ModelRouter(providers=[provider], allow_mock_fallback=False)
    # Provider selection is operator configuration, not core logic: route the
    # coding tasks at the scripted test provider by name.
    router.set_task_policy(TASK_IMPLEMENTATION, [provider.name])
    router.set_task_policy(TASK_DEBUGGING, [provider.name])
    driver = mc.ModelCodingDriver(
        database=db, engine=engine, planner=planner, router=router,
        tenant_id=TENANT, project_id=PROJECT, messaging_hub=hub,
        connector_registry=connector_registry, artifacts_root=artifacts_root)
    return {"db": db, "engine": engine, "registry": registry, "hub": hub,
            "executor": executor, "planner": planner, "router": router,
            "driver": driver, "connectors": connector_registry}


def _coding_task(stack, workflow_id):
    tasks = stack["db"].list_workflow_tasks(TENANT, workflow_id)
    return next(t for t in tasks if t.get("task_type") == "coding")


def _impl_artifact(stack, workflow_id):
    arts = [a for a in stack["db"].list_workflow_artifacts(TENANT, workflow_id)
            if a.get("kind") == "implementation_result"]
    assert len(arts) == 1, "exactly one implementation artifact expected"
    raw = Path(arts[0]["content_path"]).read_text(encoding="utf-8")
    return json.loads(raw)


def _verify_artifact(stack, workflow_id):
    arts = [a for a in stack["db"].list_workflow_artifacts(TENANT, workflow_id)
            if a.get("kind") == "verification_result"]
    assert arts, "verification_result artifact must exist"
    raw = Path(arts[0]["content_path"]).read_text(encoding="utf-8")
    return json.loads(raw)


OBJECTIVE = ("Create a Python project with a calculator module and tests "
             "for addition, subtraction, multiplication and division.")


# ---------------------------------------------------------------- A/B/C ---

def test_a_model_proposal_becomes_coding_task(tmp_path, monkeypatch):
    stack = _stack(str(tmp_path), ScriptedProvider([_proposal_json()]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["workflow_id"], "a workflow must be created and persisted"
    assert result["provider"] == "scripted-test", "provider identity must flow as data"
    # The proposal itself is stamped INFERRED — a claim, not evidence.
    assert result["proposal"]["reality"] == "INFERRED"
    assert result["proposal"]["untrusted"] is True
    # The coding task received model-authored proposals (authorship preserved).
    coding = _coding_task(stack, result["workflow_id"])
    assert coding["agent_id"] == "coder"
    assert coding["parameters"]["proposals"][0]["author_model"] == \
        "scripted-test/scripted-model-v1"
    # The proposal survives as an INFERRED lineage artifact, never evidence.
    proposals = [a for a in stack["db"].list_workflow_artifacts(TENANT, result["workflow_id"])
                 if a.get("kind") == "model_proposal"]
    assert len(proposals) == 1
    assert proposals[0]["reality"] == "INFERRED"
    assert "filesystem.file.write" in result["capabilities"]


def test_b_proposal_creates_exact_files(tmp_path, monkeypatch):
    stack = _stack(str(tmp_path), ScriptedProvider([_proposal_json()]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "COMPLETED", result.get("error")
    assert (ws / "calc.py").read_text(encoding="utf-8") == CALC_V1
    assert (ws / "test_calc.py").read_text(encoding="utf-8") == CALC_TEST
    written = {f["path"]: f for f in result["observations"]["files_written"]}
    import hashlib
    assert written["calc.py"]["sha256"] == hashlib.sha256(CALC_V1.encode()).hexdigest(), \
        "observation hash must match the actual on-disk bytes"
    assert all(f["receipt_id"] for f in written.values()), "every write needs a receipt"


def test_c_proposal_runs_tests_for_real(tmp_path, monkeypatch):
    stack = _stack(str(tmp_path), ScriptedProvider([_proposal_json()]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["test_results"], "test executions must be recorded"
    assert all(t["exit_code"] == 0 and t["status"] == "SUCCESS" for t in result["test_results"])
    assert result["tool_executions"], "fabric tool executions must be persisted as events"
    assert any(t["capability"] == "project.test.run" and t["receipt_id"]
               for t in result["tool_executions"])
    assert result["verification"]["all_passed"] is True
    assert result["verification"]["task_reality"] == "VERIFIED"


# ---------------------------------------------------------------- D/E -----

def test_d_failure_diagnosis_bounded_repair_then_verified(tmp_path, monkeypatch):
    broken = _proposal_json(files_to_create={"calc.py": CALC_V1_BROKEN,
                                             "test_calc.py": CALC_TEST})
    fixed = json.dumps({"files": {"calc.py": CALC_V1_FIXED},
                        "reasoning_summary": "sub must subtract"})
    stack = _stack(str(tmp_path), ScriptedProvider([broken, fixed]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws), max_repairs=2)
    assert result["status"] == "COMPLETED", result.get("error")
    # Exactly one bounded repair round happened (budget respected).
    assert len(result["repairs"]) == 1, result["repairs"]
    assert result["repairs"][0]["outcome"] == "COMPLETED"
    assert result["repairs"][0]["files"] == ["calc.py"]
    # E: the repaired state was independently verified, not model-asserted.
    assert (ws / "calc.py").read_text(encoding="utf-8") == CALC_V1_FIXED
    assert result["verification"]["all_passed"] is True
    assert result["verification"]["task_reality"] == "VERIFIED"
    assert result["verification"]["checks_passed"] == result["verification"]["checks_total"]


def test_d_repair_may_fix_expectations_never_facts(tmp_path, monkeypatch):
    """The model may correct its own stdout assertion, never argv or exit codes."""
    proposal = _proposal_json(
        files_to_create={"app.py": "print('hello')\n"},
        commands_to_run=[{"capability": "process.command.run",
                           "argv": [sys.executable, "app.py"],
                           "expect_exit_code": 0,
                           "expect_stdout_contains": "WRONG-SUBSTRING"}],
        tests_to_run=[])
    repair = json.dumps({"files": {"app.py": "print('hello')\n"},
                         "command_fixes": [{"index": 0,
                                            "expect_stdout_contains": "hello"}],
                         "reasoning_summary": "output goes to stdout as 'hello'"})
    stack = _stack(str(tmp_path), ScriptedProvider([proposal, repair]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws), max_repairs=2)
    assert result["status"] == "COMPLETED", result.get("error")
    assert result["repairs"][0]["command_fixes"] == [
        {"index": 0, "expect_stdout_contains": "hello"}]
    assert result["verification"]["task_reality"] == "VERIFIED"


def test_d_repair_cannot_edit_facts(tmp_path, monkeypatch):
    """argv / exit-code edits inside a repair are rejected; facts are not model-editable."""
    proposal = _proposal_json(
        files_to_create={"app.py": "print('hello')\n"},
        commands_to_run=[{"capability": "process.command.run",
                           "argv": [sys.executable, "app.py"],
                           "expect_exit_code": 0,
                           "expect_stdout_contains": "WRONG-SUBSTRING"}],
        tests_to_run=[])
    bad_repair = json.dumps({"files": {"app.py": "print('hello')\n"},
                             "command_fixes": [{"index": 0,
                                                "expect_stdout_contains": "hello",
                                                "argv": ["evil", "x"]}],
                             "reasoning_summary": "trying to smuggle argv"})
    stack = _stack(str(tmp_path), ScriptedProvider([proposal, bad_repair]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws), max_repairs=1)
    assert result["status"] == "FAILED"
    assert result["repairs"][0]["outcome"] == "repair-rejected"
    assert "argv" in result["repairs"][0]["error"]


def test_d_repair_budget_exhaustion_fails_honestly(tmp_path, monkeypatch):
    broken = _proposal_json(files_to_create={"calc.py": CALC_V1_BROKEN,
                                             "test_calc.py": CALC_TEST})
    still_broken = json.dumps({"files": {"calc.py": CALC_V1_BROKEN},
                               "reasoning_summary": "no real fix"})
    stack = _stack(str(tmp_path), ScriptedProvider([broken, still_broken]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws), max_repairs=1)
    assert result["status"] == "FAILED", "exhausted repair budget must fail, never pass"
    assert len(result["repairs"]) == 1
    assert result["repairs"][0]["outcome"] == "FAILED"
    assert result["verification"].get("all_passed") is not True


# ---------------------------------------------------------------- F/G -----

def test_f_model_success_claim_vs_failed_execution(tmp_path, monkeypatch):
    lying = _proposal_json(
        files_to_create={"app.py": "import sys\nsys.exit(3)\n"},
        commands_to_run=[{"capability": "process.command.run",
                          "argv": [sys.executable, "app.py"],
                          "expect_exit_code": 0}],
        tests_to_run=[],
        reasoning_summary="the code works, tests pass, all good")
    stack = _stack(str(tmp_path), ScriptedProvider([lying]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "FAILED", "failed execution beats a model success claim"
    assert result["proposal"]["reasoning_summary"].startswith("the code works"), \
        "the false claim is preserved as INFERRED lineage, not deleted"
    assert result["proposal"]["reality"] == "INFERRED"
    assert result["verification"].get("all_passed") is not True
    assert result["verification"].get("task_reality") != "VERIFIED"


def test_g_model_tests_pass_claim_vs_failing_tests(tmp_path, monkeypatch):
    lying = _proposal_json(
        files_to_create={"calc.py": "def add(a, b):\n    return a - b\n",
                         "test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"},
        tests_to_run=[{"capability": "project.test.run",
                       "test_args": ["-q", "test_calc.py"],
                       "expect_exit_code": 0}],
        reasoning_summary="tests pass")
    stack = _stack(str(tmp_path), ScriptedProvider([lying]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws), max_repairs=0)
    assert result["status"] == "FAILED", "failing tests beat a model pass claim"
    failing = [t for t in result["test_results"] if t["exit_code"] != 0]
    assert failing, "the real non-zero exit must be on record"
    assert result["verification"].get("task_reality") != "VERIFIED"


# ---------------------------------------------------------------- H --------

@pytest.mark.parametrize("bad_payload", [
    "this is not json",
    "[1, 2, 3]",
    json.dumps({"files_to_create": "calc.py"}),
    json.dumps({"files_to_create": {"../escape.py": "x"}}),
    json.dumps({"files_to_create": {"/abs/path.py": "x"}}),
    json.dumps({"files_to_create": {"calc.py": "ok"},
                "commands_to_run": [{"capability": "process.command.run",
                                     "argv": "rm -rf /"}]}),
    json.dumps({"files_to_create": {"calc.py": "ok"},
                "commands_to_run": [{"capability": "nuke.the.world"}]}),
    json.dumps({"files_to_create": {"calc.py": "ok"},
                "tests_to_run": [{"capability": "project.test.run",
                                   "test_args": "not-a-list"}]}),
    json.dumps({"files_to_create": {"calc.py": "ok"},
                "deletes": ["../escape.py"]}),
    json.dumps({"files_to_create": {"C:/abs/path.py": "ok"}}),
])
def test_h_malformed_proposals_rejected_safely(tmp_path, monkeypatch, bad_payload):
    stack = _stack(str(tmp_path), ScriptedProvider([bad_payload]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "PROPOSAL_REJECTED", bad_payload[:60]
    assert result["workflow_id"] is None, "rejected proposals create no workflow"
    assert list(ws.iterdir()) == [], "rejected proposals execute nothing"


# ---------------------------------------------------------------- I --------

def test_i_forbidden_ops_held_never_executed(tmp_path, monkeypatch):
    hostile = _proposal_json(
        files_to_create={"calc.py": CALC_V1,
                         "leak.py": "API_TOKEN = 'ghp_TestSecretValue1234567890abcdef'\n"},
        deletes=["calc.py"],
        commands_to_run=[{"capability": "process.command.run",
                           "argv": ["calc-evil-bin", "--destroy"],
                           "expect_exit_code": 0}],
        tests_to_run=[])
    stack = _stack(str(tmp_path), ScriptedProvider([hostile]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "APPROVAL_PENDING"
    pending = {op["kind"] for op in result["pending_ops"]}
    assert pending == {"delete", "command"}, result["pending_ops"]
    assert len(result["approval_ids"]) == 2
    # The secret-shaped file was rejected at conversion (fail-closed) while
    # the allowlisted remainder still pauses only for the held ops.
    rejected = result["conversion"]["rejected"]
    assert any(r["field"] == "files_to_create" and "secret" in r["reason"].lower()
               for r in rejected), rejected
    coding = _coding_task(stack, result["workflow_id"])
    assert coding["status"] == "AWAITING_APPROVAL"
    # Nothing held has executed: the allowed file is not yet written either
    # (the whole task waits for the human decision).
    assert not (ws / "calc.py").exists()
    approvals = stack["db"].list_pending_approvals(TENANT, PROJECT)
    assert len(approvals) == 2
    assert all(a["status"] == "PENDING" for a in approvals)


# ---------------------------------------------------------------- J --------

def test_j_secret_shaped_output_rejected(tmp_path, monkeypatch):
    tainted = _proposal_json(
        files_to_create={"config.py": "API_TOKEN = 'ghp_TestSecretValue1234567890abcdef'\n"},
        tests_to_run=[])
    stack = _stack(str(tmp_path), ScriptedProvider([tainted]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "PROPOSAL_REJECTED"
    assert result["workflow_id"] is None
    assert not (ws / "config.py").exists(), "secret-shaped content is never written"
    assert "secret" in result["error"].lower()


def test_j_secret_in_reasoning_scrubbed(tmp_path, monkeypatch):
    from runtime.capability_fabric import scrub_error_text
    dirty = "Bearer abcdef1234567890"
    assert scrub_error_text(dirty) != dirty, "diagnostic scrubber must redact bearer material"


# ---------------------------------------------------------------- K --------

def test_k_provider_swap_changes_no_core_logic(tmp_path, monkeypatch):
    payload = _proposal_json()
    left = _stack(str(tmp_path / "left"), ScriptedProvider(
        [payload], name="vendor-alpha", model="alpha-1"), monkeypatch)
    right = _stack(str(tmp_path / "right"), ScriptedProvider(
        [payload], name="vendor-beta", model="beta-2"), monkeypatch)
    # NOTE: same-process shared connector registry is provider-neutral here;
    # each stack owns its own database/engine/planner.
    ws_left = tmp_path / "left" / "ws"
    ws_left.mkdir(parents=True)
    ws_right = tmp_path / "right" / "ws"
    ws_right.mkdir(parents=True)
    res_left = left["driver"].run(OBJECTIVE, str(ws_left))
    res_right = right["driver"].run(OBJECTIVE, str(ws_right))
    assert res_left["status"] == res_right["status"] == "COMPLETED"
    assert (ws_left / "calc.py").read_text() == (ws_right / "calc.py").read_text()
    assert res_left["provider"] == "vendor-alpha" and res_right["provider"] == "vendor-beta"
    left_params = _coding_task(left, res_left["workflow_id"])["parameters"]["proposals"]
    right_params = _coding_task(right, res_right["workflow_id"])["parameters"]["proposals"]
    assert [(p["path"], p["content"]) for p in left_params] == \
           [(p["path"], p["content"]) for p in right_params], \
        "executable content identical across vendors"
    assert left_params[0]["author_model"] != right_params[0]["author_model"], \
        "only model provenance differs"


# ---------------------------------------------------------------- L --------

def test_l_model_cannot_create_verified(tmp_path, monkeypatch):
    stack = _stack(str(tmp_path), ScriptedProvider([_proposal_json()]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "COMPLETED"
    # The model's own artifact can never be VERIFIED.
    proposals = [a for a in stack["db"].list_workflow_artifacts(TENANT, result["workflow_id"])
                 if a.get("kind") == "model_proposal"]
    assert proposals and proposals[0]["reality"] == "INFERRED"
    assert proposals[0]["verification_state"] == "UNVERIFIED"
    # Only the verifier's artifact carries VERIFIED, with verifier provenance.
    verified = [a for a in stack["db"].list_workflow_artifacts(TENANT, result["workflow_id"])
                if a.get("reality") == "VERIFIED"]
    assert verified, "independent verification must have issued VERIFIED"
    assert all("verifier" in " ".join(v.get("provenance", [])).lower() for v in verified)
    # The ladder itself forbids the upgrade, structurally.
    with pytest.raises(ValueError):
        assert_no_upgrade("INFERRED", "VERIFIED", who="model")


# ---------------------------------------------------------------- M --------

def _decide(stack, approval_id, decision):
    return stack["driver"]._autonomy().handle_approval_decision(
        approval_id, decision, decided_by="test-human", note=f"test {decision.lower()}")


def test_m2_approved_resumes_and_executes_with_grant(tmp_path, monkeypatch):
    hostile = _proposal_json(
        files_to_create={"calc.py": CALC_V1},
        deletes=["old.py"],
        commands_to_run=[{"capability": "process.command.run",
                           "argv": [sys.executable, "calc.py"],
                           "expect_exit_code": 0}],
        tests_to_run=[])
    stack = _stack(str(tmp_path), ScriptedProvider([hostile]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "old.py").write_text("stale = True\n", encoding="utf-8")
    paused = stack["driver"].run(OBJECTIVE, str(ws))
    assert paused["status"] == "APPROVAL_PENDING"
    # Only the delete is held: the python command is allowlisted and needs no
    # approval, which is exactly the boundary being proven.
    assert len(paused["approval_ids"]) == 1
    assert paused["pending_ops"][0]["kind"] == "delete"
    for approval_id in paused["approval_ids"]:
        assert _decide(stack, approval_id, "APPROVED") is True
    assert not stack["db"].list_pending_approvals(TENANT, PROJECT), \
        "decisions must leave no pending approvals behind"
    result = stack["driver"].resume(paused["workflow_id"])
    assert result["status"] == "COMPLETED", result.get("error")
    assert not (ws / "old.py").exists(), "the approved delete executed after approval"
    assert (ws / "calc.py").read_text(encoding="utf-8") == CALC_V1
    assert [d["decision"] for d in result["decisions"]] == ["APPROVED"]
    assert all(d["approval_id"] for d in result["decisions"])
    deleted = result["observations"]["deleted"]
    assert deleted and deleted[0]["approval_id"], "the grant must be recorded on the deletion"


def test_m3_rejected_prevents_execution_and_records(tmp_path, monkeypatch):
    hostile = _proposal_json(
        files_to_create={"calc.py": CALC_V1},
        deletes=["calc.py"],
        commands_to_run=[{"capability": "process.command.run",
                           "argv": ["calc-evil-bin", "--destroy"],
                           "expect_exit_code": 0}],
        tests_to_run=[])
    stack = _stack(str(tmp_path), ScriptedProvider([hostile]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    paused = stack["driver"].run(OBJECTIVE, str(ws))
    assert paused["status"] == "APPROVAL_PENDING"
    for approval_id in paused["approval_ids"]:
        assert _decide(stack, approval_id, "REJECTED") is True
    result = stack["driver"].resume(paused["workflow_id"])
    # The allowed file still executes; every rejected op is absent from reality.
    assert result["status"] == "COMPLETED", result.get("error")
    assert (ws / "calc.py").read_text(encoding="utf-8") == CALC_V1
    assert [d["decision"] for d in result["decisions"]] == ["REJECTED", "REJECTED"]
    assert result["observations"]["deleted"] == [], "rejected deletes never execute"
    assert all(r["capability"] != "process.command.run" or "calc-evil-bin" not in str(r.get("argv"))
               for r in result["observations"]["commands"])


def test_m3_reject_all_leaves_nothing_fails_honestly(tmp_path, monkeypatch):
    hostile = _proposal_json(
        files_to_create={},
        deletes=["gone.py"],
        commands_to_run=[{"capability": "process.command.run",
                           "argv": ["calc-evil-bin", "--destroy"],
                           "expect_exit_code": 0}],
        tests_to_run=[])
    stack = _stack(str(tmp_path), ScriptedProvider([hostile]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    paused = stack["driver"].run(OBJECTIVE, str(ws))
    assert paused["status"] == "APPROVAL_PENDING"
    for approval_id in paused["approval_ids"]:
        assert _decide(stack, approval_id, "REJECTED") is True
    result = stack["driver"].resume(paused["workflow_id"])
    assert result["status"] == "FAILED", "no executable remainder must fail, never fake success"
    assert "rejected" in result["error"].lower()


def test_m4_no_auto_approve_without_operator_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_AUTO_APPROVE", raising=False)
    hostile = _proposal_json(
        files_to_create={"calc.py": CALC_V1},
        deletes=["calc.py"])
    stack = _stack(str(tmp_path), ScriptedProvider([hostile]), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "APPROVAL_PENDING", \
        "without operator opt-in the run must pause, never auto-approve"
    assert (ws / "calc.py").exists() is False
    coding = _coding_task(stack, result["workflow_id"])
    assert coding["status"] == "AWAITING_APPROVAL"


def test_m_resume_unknown_workflow_is_honest(tmp_path, monkeypatch):
    stack = _stack(str(tmp_path), ScriptedProvider([_proposal_json()]), monkeypatch)
    result = stack["driver"].resume("workflow-does-not-exist")
    assert result["status"] == "CANNOT_RESUME"


def test_model_unavailable_fails_closed(tmp_path, monkeypatch):
    from runtime.model_router import MockModelAdapter
    stack = _stack(str(tmp_path), MockModelAdapter(should_fail=True), monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    result = stack["driver"].run(OBJECTIVE, str(ws))
    assert result["status"] == "MODEL_UNAVAILABLE"
    assert result["workflow_id"] is None, "no proposal means no workflow and no execution"
    assert list(ws.iterdir()) == []
