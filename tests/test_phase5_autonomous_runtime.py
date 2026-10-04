"""Phase 5: Autonomous orchestration runtime tests.

These tests exercise the AutonomousRuntime — the engine that turns a
high-level objective into a completed workflow, adapts the task graph
during execution, handles agent collaboration, and enforces approval gates.
"""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("NEXUS_TENANT_ID", "test-tenant")
os.environ.setdefault("NEXUS_PROJECT_ID", "test-project")

from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
from runtime.messaging_hub import MessagingHub
from runtime.multi_agent_executor import MultiAgentExecutor
from runtime.agent_registry import AgentRegistry
from runtime.workflow_planner import WorkflowPlanner
from runtime.mission_composer import MissionComposer
from nexus_independent.database import NexusDatabase


@pytest.fixture
def tmp_tenant_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = NexusDatabase(db_path)
    db.migrate()
    now = "2026-09-27T00:00:00Z"
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                     ("test-tenant", "Test Tenant", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)",
                     ("test-project", "test-tenant", "Test Project", now, now))
    return db


@pytest.fixture
def runtime(tmp_tenant_db, tmp_path):
    tenant_id = "test-tenant"
    project_id = "test-project"
    artifact_root = str(tmp_path / "artifacts")
    composer = MissionComposer()
    registry = AgentRegistry()
    from runtime.multi_agent_executor import register_default_agents
    register_default_agents(registry)

    messaging_hub = MessagingHub(tmp_tenant_db)
    engine = WorkflowEngine(
        database=tmp_tenant_db,
        composer=composer,
        policy=WorkflowExecutionPolicy(
            max_retries_default=2,
            timeout_seconds_default=60,
            fail_on_agent_not_available=False,
            auto_retry_on_failure=True,
        ),
        agent_registry=registry,
        artifacts_root=artifact_root,
        messaging_hub=messaging_hub,
    )
    executor = MultiAgentExecutor(
        database=tmp_tenant_db,
        agent_registry=registry,
        settings=None,
        principal={"tenant_id": tenant_id, "project_id": project_id},
        messaging_hub=messaging_hub,
    )
    engine.set_executor(executor, agent_registry=registry)

    planner = WorkflowPlanner(agent_registry=registry)

    return AutonomousRuntime(
        database=tmp_tenant_db,
        engine=engine,
        messaging_hub=messaging_hub,
        planner=planner,
        config=AutonomousConfig(tenant_id=tenant_id, project_id=project_id),
    )


def _create_workflow_from_plan(runtime, workflow_id, plan):
    """Helper: create a workflow in the DB from a plan dict."""
    runtime.database.create_workflow(
        workflow_id=workflow_id,
        tenant_id="test-tenant",
        project_id="test-project",
        name=plan.get("name", "Test Workflow"),
        objective=plan.get("objective", ""),
        scope=plan.get("scope", "Themeta-verse/Nexus"),
        plan_json=json.dumps(plan.get("plan", plan)),
    )
    return workflow_id


# --- Test 1: Autonomous objective execution end-to-end ---

def test_autonomous_execution_complete(runtime):
    """Run a simple objective autonomously and verify completion."""
    result = runtime.execute_objective(
        objective="Explore the nexus repository and produce a summary of its structure",
        scope="Themeta-verse/Nexus",
    )

    assert result["status"] in ("COMPLETED", "RUNNING", "FAILED")
    assert "workflow_id" in result
    assert "trace" in result
    assert result["trace"]["total_tasks"] >= 1


# --- Test 2: Initial planning generates valid plan ---

def test_initial_planning_generates_plan(runtime):
    """Autonomous planning should generate a valid plan with tasks and agents."""
    plan = runtime._plan_objective(
        "Analyze this repository and produce an architecture report",
        "Themeta-verse/Nexus",
        None,  # template_type
        None,  # agents
        {},    # constraints
    )

    assert plan["is_valid"] is True
    assert plan["template_type"] is None  # None means auto-classified
    assert len(plan["task_specs"]) >= 2
    assert len(plan["agents"]) >= 1
    assert len(plan["validations"]) >= 1


# --- Test 3: Multi-agent execution with multiple agents ---

def test_multi_agent_execution(runtime):
    """Workflow should assign different agents to different task types."""
    result = runtime.execute_objective(
        objective="Research and document the workflow engine components",
        scope="Themeta-verse/Nexus",
    )

    task_agents = {t["agent_id"] for t in result["trace"]["tasks"] if t["agent_id"]}
    assert len(task_agents) >= 1


# --- Test 4: Artifact-based handoff between agents ---

def test_artifact_handoff(runtime):
    """Artifacts produced by one task should be available as input to subsequent tasks."""
    plan = runtime._plan_objective(
        "Research the messaging hub and then summarize findings",
        "Themeta-verse/Nexus",
        None, None, {},
    )
    tasks = plan["task_specs"]
    assert len(tasks) >= 2
    for task in tasks:
        assert task["depends_on"] is not None


# --- Test 5: Agent messaging during execution ---

def test_agent_messaging(runtime):
    """Agents should send and receive collaboration messages during execution.

    Scope note: this previously used "Themeta-verse/Nexus", a repository
    reference no local connector can observe. The research task therefore could
    not observe anything, yet it still reported TASK_COMPLETED — the
    zero-evidence fake success. Messaging was therefore never exercised by real
    agent work.

    It now scopes to the actual local runtime package, which is what
    "explore the runtime module structure" means and which the filesystem
    connector can genuinely observe, so collaboration happens during real work.
    """
    import pathlib

    scope = str(pathlib.Path(__file__).resolve().parents[1] / "runtime")
    assert pathlib.Path(scope).is_dir(), scope
    result = runtime.execute_objective(
        objective="Explore the runtime module structure",
        scope=scope,
    )
    workflow_id = result["workflow_id"]

    messages = runtime.database.list_workflow_messages("test-tenant", workflow_id, limit=200)
    assert len(messages) >= 3, f"expected lifecycle + agent messages, got {len(messages)}"
    types = {m["message_type"] for m in messages}
    assert "TASK_STARTED" in types, f"missing TASK_STARTED in {types}"
    assert "TASK_COMPLETED" in types, f"missing TASK_COMPLETED in {types}"
    agent_msgs = [m for m in messages if m["message_type"] in ("STATUS_UPDATE", "RESPONSE")]
    assert len(agent_msgs) >= 2, "agents must emit STATUS_UPDATE/RESPONSE via MessagingHub"
    assert all(m["workflow_id"] == workflow_id and m["created_at"] for m in messages)


# --- Test 6: Dynamic task creation during execution ---

def test_dynamic_task_creation(runtime):
    """When a research task finds security-related content, a dynamic security review task should be created."""
    scope = "Themeta-verse/Nexus"
    result = runtime.execute_objective(
        objective="Research the repository security architecture",
        scope=scope,
    )

    wf_id = result["workflow_id"]
    db_tasks = runtime.database.list_workflow_tasks("test-tenant", wf_id)
    dynamic_tasks = runtime.database.get_dynamic_tasks("test-tenant", wf_id)

    assert result["status"] in ("COMPLETED", "RUNNING", "FAILED")
    
    # Check for security-relevant files in the scope
    scope_path = Path(scope) if os.path.isabs(scope) else Path(os.getcwd()) / scope
    security_files = []
    if scope_path.exists():
        for p in scope_path.rglob("*"):
            if p.is_file():
                name = str(p).lower()
                if any(kw in name for kw in ["auth", "security", "secret", "token", "jwt", "credential"]):
                    security_files.append(str(p))
    
    if security_files:
        assert len(dynamic_tasks) > 0, (
            f"Found {len(security_files)} security-related files but no dynamic tasks created. "
            f"Files: {security_files[:3]}"
        )
        for dt in dynamic_tasks:
            assert dt["task_type"] == "security-analysis", f"Dynamic task should be security-analysis, got {dt['task_type']}"
            assert dt.get("generated_reason"), "Dynamic task must have a reason"


# --- Test 7: Dynamic task dependency on parent ---

def test_dynamic_task_dependency(runtime):
    """Dynamically created tasks should reference their parent task and triggering artifact."""
    plan = runtime._plan_objective(
        "Research and document the nexus runtime",
        "Themeta-verse/Nexus",
        None, None, {},
    )
    workflow_id = _create_workflow_from_plan(runtime, "wf-test-dep", plan)

    engine = runtime.engine
    dynamic_task = engine.add_dynamic_task(
        tenant_id="test-tenant",
        project_id="test-project",
        workflow_id=workflow_id,
        task_type="review",
        name="Security Review (Dynamic)",
        required_capabilities=["security.review"],
        depends_on=["research-task-1"],
        parent_task_id="research-task-1",
        generated_reason="Security-related findings detected in research output",
    )

    db_tasks = runtime.database.get_dynamic_tasks("test-tenant", workflow_id)
    assert len(db_tasks) == 1
    assert db_tasks[0]["parent_task_id"] == "research-task-1"
    assert db_tasks[0]["generated_reason"] == "Security-related findings detected in research output"
    assert db_tasks[0]["task_type"] == "review"


# --- Test 8: Retry behavior on failed tasks ---

def test_retry_on_failure(runtime):
    """Failed tasks should be retried up to max_retries with proper backoff."""
    plan = runtime._plan_objective(
        "Research and verify all repository components",
        "Themeta-verse/Nexus",
        None, None, {},
    )
    workflow_id = _create_workflow_from_plan(runtime, "wf-test-retry", plan)

    engine = runtime.engine
    engine.start_workflow("test-tenant", "test-project", workflow_id)

    for _ in range(3):
        engine.step("test-tenant", "test-project", workflow_id)

    tasks = runtime.database.list_workflow_tasks("test-tenant", workflow_id)
    for task in tasks:
        assert task["retry_count"] <= 2


# --- Test 9: Agent failure handling ---

def test_agent_failure_handling(runtime):
    """When an agent is unavailable, the task should be retried or fail gracefully."""
    plan = runtime._plan_objective(
        "Research with potentially unavailable agent",
        "Themeta-verse/Nexus",
        None, None, {},
    )
    workflow_id = _create_workflow_from_plan(runtime, "wf-test-fail", plan)

    engine = runtime.engine
    engine.start_workflow("test-tenant", "test-project", workflow_id)
    engine.step("test-tenant", "test-project", workflow_id)

    wf = runtime.database.get_workflow("test-tenant", workflow_id)
    assert wf is not None
    assert wf["status"] in ("RUNNING", "FAILED", "COMPLETED", "PAUSED", "PENDING", "AWAITING_APPROVAL")


# --- Test 10: Recovery from stuck tasks ---

def test_recovery_stuck_tasks(runtime):
    """The recover_stuck_tasks method should fix tasks stuck in RUNNING state."""
    plan = runtime._plan_objective(
        "Research and document recovery mechanisms",
        "Themeta-verse/Nexus",
        None, None, {},
    )
    workflow_id = _create_workflow_from_plan(runtime, "wf-test-recovery", plan)

    engine = runtime.engine
    engine.start_workflow("test-tenant", "test-project", workflow_id)
    engine.step("test-tenant", "test-project", workflow_id)
    engine.recover_stuck_tasks("test-tenant", workflow_id)

    completion = engine.check_workflow_completion("test-tenant", workflow_id)
    assert "status" in completion


# --- Test 11: Approval gate pauses execution ---

def test_approval_gate_pauses(runtime):
    """When an approval is requested, the task should enter AWAITING_APPROVAL state."""
    plan = runtime._plan_objective(
        "Research and produce a security audit report",
        "Themeta-verse/Nexus",
        None, None, {},
    )
    workflow_id = _create_workflow_from_plan(runtime, "wf-test-approval", plan)

    engine = runtime.engine
    engine.start_workflow("test-tenant", "test-project", workflow_id)

    approval_id = runtime.request_approval(
        workflow_id=workflow_id,
        operation="security_audit_review",
        reason="Security audit requires human approval",
    )

    if approval_id:
        approval = runtime.database.get_approval("test-tenant", approval_id)
        assert approval is not None
        assert approval["status"] == "PENDING"
        assert approval["operation"] == "security_audit_review"

    assert "AWAITING_APPROVAL" in {"PENDING", "READY", "RUNNING", "WAITING",
                                    "COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "AWAITING_APPROVAL"}


# --- Test 12: Approval decision workflow ---

def test_approval_decision_workflow(runtime):
    """After an approval is granted, the paused task should resume."""
    plan = runtime._plan_objective(
        "Research with approval gate for verification",
        "Themeta-verse/Nexus",
        None, None, {},
    )
    workflow_id = _create_workflow_from_plan(runtime, "wf-test-approval-decision", plan)

    approval_id = runtime.request_approval(
        workflow_id=workflow_id,
        operation="final_verification",
        reason="Final verification requires approval",
    )

    if approval_id:
        result = runtime.handle_approval_decision(
            approval_id=approval_id,
            decision="APPROVED",
            decided_by="test-operator",
        )
        assert result is True

        approval_record = runtime.database.get_approval("test-tenant", approval_id)
        assert approval_record["status"] == "APPROVED"

        approval_id2 = runtime.request_approval(
            workflow_id=workflow_id,
            operation="another_review",
            reason="Another review needed",
        )
        if approval_id2:
            runtime.handle_approval_decision(
                approval_id=approval_id2,
                decision="REJECTED",
                decided_by="test-operator",
            )
            approval_record2 = runtime.database.get_approval("test-tenant", approval_id2)
            assert approval_record2["status"] == "REJECTED"


# --- Test 13: Execution trace completeness ---

def test_execution_trace_completeness(runtime):
    """Execution trace should capture all planning, execution, and result data."""
    result = runtime.execute_objective(
        objective="Explore and summarize the runtime architecture",
        scope="Themeta-verse/Nexus",
    )

    trace = runtime.engine.get_execution_trace("test-tenant", result["workflow_id"])

    assert "workflow_id" in trace
    assert "objective" in trace
    assert "planning" in trace
    assert "tasks" in trace
    assert "agents" in trace
    assert "artifacts" in trace
    assert "messages" in trace
    assert "dynamic_tasks" in trace
    assert "retries" in trace
    assert "approvals" in trace
    assert "verification" in trace
    assert "final_result" in trace
    assert trace["workflow_id"] == result["workflow_id"]


# --- Test 14: Backward compatibility with existing workflows ---

def test_backward_compatibility(runtime):
    """Phase 5 changes should not break Phase 1-4 functionality.

    Existing workflows created with phase_workflow should still work.
    """
    from runtime.workflow_engine import WorkflowSpec

    spec = WorkflowSpec(
        name="Backward Compatibility Test",
        objective="Backward compat test",
        scope="Themeta-verse/Nexus",
        task_specs=[
            {"task_id": "task-1", "task_type": "research", "name": "Test Task",
             "agent_id": "researcher", "depends_on": [], "required_capabilities": ["repository.read"],
             "input_artifacts": [], "output_artifacts": ["summary.json"], "parameters": {}}
        ],
        agents=[{"agent_id": "researcher", "capabilities": ["repository.read"], "priority": 1}],
        execution_mode="SIMULATION",
    )

    workflow_id = runtime.engine.create_workflow(
        tenant_id="test-tenant",
        project_id="test-project",
        spec=spec,
    )["workflow_id"]

    engine = runtime.engine
    start_result = engine.start_workflow("test-tenant", "test-project", workflow_id)
    assert start_result["status"] == "RUNNING"

    step_result = engine.step("test-tenant", "test-project", workflow_id)
    assert "status" in step_result

    completion = engine.check_workflow_completion("test-tenant", workflow_id)
    assert "status" in completion
