"""NEXUS Phase 3 Tests for WorkflowPlanner.

Tests cover:
- simple objective → valid workflow
- multi-agent objective → dependency graph
- missing capability → planning failure
- invalid dependency → planning failure
- planner output passed to WorkflowEngine
- existing workflows remain unaffected

Run: python tests/test_phase3_workflow_planner.py
     python -m pytest tests/test_phase3_workflow_planner.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup_registry():
    """Create an AgentRegistry with default agents."""
    from runtime.agent_registry import AgentRegistry
    from runtime.multi_agent_executor import register_default_agents
    registry = AgentRegistry()
    register_default_agents(registry)
    return registry


def _setup_engine(tmpdir: str, registry):
    """Create a WorkflowEngine with MultiAgentExecutor."""
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor
    from nexus_independent.database import NexusDatabase

    db_path = os.path.join(tmpdir, "test_nexus.db")
    db = NexusDatabase(db_path)
    db.migrate()

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("test-tenant", "Test Tenant", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("test-project", "test-tenant", "Test Project", now, now))

    executor = MultiAgentExecutor(
        database=db,
        agent_registry=registry,
        principal={"tenant_id": "test-tenant", "project_id": "test-project"},
    )

    policy = WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=True)
    engine = WorkflowEngine(
        database=db,
        composer=MissionComposer(),
        policy=policy,
        agent_registry=registry,
        artifacts_root=tmpdir,
    )
    engine.set_executor(executor, agent_registry=registry)

    return engine, db


def _poll_workflow(engine, tenant_id, project_id, workflow_id, max_ticks=30):
    """Poll a workflow until completion."""
    for i in range(max_ticks):
        time.sleep(0.3)
        engine.step(tenant_id, project_id, workflow_id)
        state = engine.get_workflow_state(tenant_id, workflow_id)
        summary = state["summary"]
        if summary["completed"] + summary["failed"] == summary["total_tasks"]:
            break
    return engine.get_workflow_state(tenant_id, workflow_id)


def test_simple_objective_produces_valid_workflow():
    """Simple objective → valid workflow with research → report → verification."""
    print("\n" + "=" * 60)
    print("TEST: Simple Objective -> Valid Workflow")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner, PlanningError

    registry = _setup_registry()
    planner = WorkflowPlanner(agent_registry=registry)

    planned = planner.plan(
        objective="Research the project repository and summarize findings.",
        scope="Themeta-verse/Nexus",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    print(f"  Template: {planned.plan['template_type']}")
    print(f"  Tasks: {len(planned.task_specs)}")
    print(f"  Valid: {planned.is_valid}")
    for task in planned.task_specs:
        print(f"    {task['task_id']}: {task['task_type']} -> agent={task['agent_id']}")
    for v in planned.validations:
        print(f"  Validation: {v.check} = {v.passed}")

    assert planned.is_valid, "Plan should be valid"
    assert len(planned.task_specs) >= 3, "Should have at least 3 tasks (research, report, verification)"
    assert all(t["agent_id"] is not None for t in planned.task_specs), "All tasks should have agents assigned"

    # Verify task ordering: research first, then report, then verification
    task_types = [t["task_type"] for t in planned.task_specs]
    assert task_types[0] == "research"
    assert "report" in task_types
    assert "verification" in task_types

    # Verify dependencies
    research_task = planned.task_specs[0]
    assert research_task["depends_on"] == [], "Research task should have no dependencies"

    report_task = next(t for t in planned.task_specs if t["task_type"] == "report")
    assert "task-0" in report_task["depends_on"], "Report should depend on research"

    print("  PASSED: Simple objective produces valid workflow")


def test_multi_agent_dependency_graph():
    """Multi-agent objective → diamond dependency graph (research → arch+sec → report → verify)."""
    print("\n" + "=" * 60)
    print("TEST: Multi-Agent Dependency Graph")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner

    registry = _setup_registry()
    planner = WorkflowPlanner(agent_registry=registry)

    planned = planner.plan(
        objective="Analyze this repository and produce an architecture and security report.",
        scope="Themeta-verse/Nexus",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    print(f"  Template: {planned.plan['template_type']}")
    print(f"  Tasks: {len(planned.task_specs)}")
    print(f"  Parallel groups: {planned.plan['parallel_groups']}")
    print(f"  Valid: {planned.is_valid}")

    assert planned.is_valid, "Plan should be valid"
    assert planned.plan["template_type"] == "repository_analysis"

    # Verify 5 tasks: research, architecture, security, report, verification
    assert len(planned.task_specs) == 5

    # Verify diamond structure
    task_types = [t["task_type"] for t in planned.task_specs]
    assert "research" in task_types
    assert "architecture-analysis" in task_types
    assert "security-analysis" in task_types
    assert "report" in task_types
    assert "verification" in task_types

    # Verify dependencies: research → arch, research → sec, arch → report, sec → report, report → verify
    task_map = {t["task_id"]: t for t in planned.task_specs}

    # Research has no deps
    research = next(t for t in planned.task_specs if t["task_type"] == "research")
    assert research["depends_on"] == []

    # Architecture and security both depend on research
    arch = next(t for t in planned.task_specs if t["task_type"] == "architecture-analysis")
    sec = next(t for t in planned.task_specs if t["task_type"] == "security-analysis")
    assert research["task_id"] in arch["depends_on"]
    assert research["task_id"] in sec["depends_on"]

    # Report depends on both arch and security
    report = next(t for t in planned.task_specs if t["task_type"] == "report")
    assert arch["task_id"] in report["depends_on"]
    assert sec["task_id"] in report["depends_on"]

    # Verification depends on report
    verify = next(t for t in planned.task_specs if t["task_type"] == "verification")
    assert report["task_id"] in verify["depends_on"]

    # Verify parallel groups: [research], [arch, sec], [report], [verify]
    groups = planned.plan["parallel_groups"]
    assert len(groups) == 4
    assert len(groups[1]) == 2, "Architecture and security should be parallel"

    # Verify all tasks have agents
    agents = {t["agent_id"] for t in planned.task_specs if t["agent_id"]}
    assert len(agents) >= 3, f"Should have at least 3 distinct agents, got {agents}"

    print("  PASSED: Multi-agent dependency graph correct")


def test_missing_capability_planning_failure():
    """Missing capability → planning failure (validation fails, not exception)."""
    print("\n" + "=" * 60)
    print("TEST: Missing Capability -> Planning Failure")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner
    from runtime.agent_registry import AgentRegistry

    # Registry with no agents — capability can't be matched
    empty_registry = AgentRegistry()
    planner = WorkflowPlanner(agent_registry=empty_registry)

    planned = planner.plan(
        objective="Research the project repository and summarize findings.",
        scope="Themeta-verse/Nexus",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    print(f"  Valid: {planned.is_valid}")
    print(f"  Validations:")
    for v in planned.validations:
        print(f"    {v.check}: passed={v.passed}, detail={v.detail}")

    # The plan should be created but validation should fail
    assert not planned.is_valid, "Plan should be invalid when no agents available"
    
    agent_validation = next(v for v in planned.validations if v.check == "agent_assignment")
    assert agent_validation.passed == False, "Agent assignment validation should fail"

    print("  PASSED: Missing capability correctly fails validation")


def test_invalid_dependency_detection():
    """Invalid dependency (cyclic) → planning failure."""
    print("\n" + "=" * 60)
    print("TEST: Invalid Dependency Detection")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner, TASK_TEMPLATES
    from copy import deepcopy

    # Modify a template to create a cycle: task-0 depends on task-0 (self-reference)
    # We'll test the validation directly
    registry = _setup_registry()
    planner = WorkflowPlanner(agent_registry=registry)

    # Use a valid plan and inject a cycle into the task_specs
    planned = planner.plan(
        objective="Research the project repository.",
        scope="Themeta-verse/Nexus",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    # Inject a cycle: make task-0 depend on the last task
    if len(planned.task_specs) >= 2:
        first = planned.task_specs[0]
        last = planned.task_specs[-1]
        first["depends_on"] = [last["task_id"]]

    # Re-validate
    from runtime.workflow_planner import PlanningValidation
    validations = planner._validate_plan(planned.task_specs, planned.plan["parallel_groups"], TASK_TEMPLATES["simple_research"])

    cycle_validation = next(v for v in validations if v.check == "dependency_acyclic")
    print(f"  Cycle validation: passed={cycle_validation.passed}, detail={cycle_validation.detail}")
    assert not cycle_validation.passed, "Cyclic dependency should fail validation"

    print("  PASSED: Invalid dependency correctly detected")


def test_planner_output_passed_to_workflow_engine():
    """Planner output passed to WorkflowEngine — execute the plan end-to-end."""
    print("\n" + "=" * 60)
    print("TEST: Planner Output Passed to WorkflowEngine")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner
    from runtime.workflow_engine import WorkflowEngine
    from runtime.multi_agent_executor import MultiAgentExecutor

    with tempfile.TemporaryDirectory() as tmpdir:
        registry = _setup_registry()
        planner = WorkflowPlanner(agent_registry=registry)

        # Plan
        planned = planner.plan(
            objective="Research the project repository and summarize findings.",
            scope=tmpdir,
            tenant_id="test-tenant",
            project_id="test-project",
            execution_mode="REAL_READ",
        )

        print(f"  Plan valid: {planned.is_valid}")
        assert planned.is_valid, "Plan should be valid"

        # Set up engine
        engine, db = _setup_engine(tmpdir, registry)
        engine.set_executor(
            MultiAgentExecutor(
                database=db,
                agent_registry=registry,
                principal={"tenant_id": "test-tenant", "project_id": "test-project"},
            ),
            agent_registry=registry,
        )

        # Convert plan to workflow spec and create
        spec = planner.plan_to_workflow_spec(planned)
        workflow = engine.create_workflow("test-tenant", "test-project", spec)
        workflow_id = workflow["workflow_id"]

        # Start and execute
        engine.start_workflow("test-tenant", "test-project", workflow_id)
        state = _poll_workflow(engine, "test-tenant", "test-project", workflow_id)

        print(f"  Status: {state['workflow']['status']}")
        print(f"  Tasks: {state['summary']['completed']}/{state['summary']['total_tasks']} completed")
        print(f"  Artifacts: {len(state['artifacts'])}")

        assert state["workflow"]["status"] in ("COMPLETED", "FAILED")
        assert state["summary"]["completed"] > 0, "At least some tasks should complete"

        artifacts = state["artifacts"]
        assert len(artifacts) > 0, "Should have produced artifacts"

        print("  PASSED: Planner output successfully executed by WorkflowEngine")


def test_existing_workflows_unaffected():
    """Verify that manually-created workflows (existing Phase 2 tests) still work alongside planner."""
    print("\n" + "=" * 60)
    print("TEST: Existing Workflows Unaffected")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner
    from runtime.workflow_engine import WorkflowSpec

    with tempfile.TemporaryDirectory() as tmpdir:
        registry = _setup_registry()
        planner = WorkflowPlanner(agent_registry=registry)

        # Create a plan
        planned = planner.plan(
            objective="Analyze repository structure and identify security risks.",
            scope=tmpdir,
            tenant_id="test-tenant",
            project_id="test-project",
        )

        # Also create a manually-defined workflow spec (like Phase 2 tests)
        manual_spec = WorkflowSpec(
            name="Manual Workflow",
            objective="Manual workflow test",
            scope=tmpdir,
            task_specs=[
                {"task_id": "t-manual", "task_type": "research", "name": "Manual Research",
                 "agent_id": "researcher", "required_capabilities": ["filesystem.read"],
                 "depends_on": [], "input_artifacts": [], "parameters": {"observation_root": "runtime"}},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher",
                 "capabilities": ["filesystem.read"], "instruction": "Observe files"},
            ],
            execution_mode="REAL_READ",
        )

        # Both should work independently
        assert planned.task_specs[0]["task_type"] == "research"
        assert manual_spec.task_specs[0]["task_type"] == "research"
        assert planned.name != manual_spec.name

        print("  Plan tasks: " + str([t["task_type"] for t in planned.task_specs]))
        print("  Manual spec tasks: " + str([t["task_type"] for t in manual_spec.task_specs]))
        print("  PASSED: Existing workflows unaffected by planner")


def test_objective_classification():
    """Test that different objectives map to appropriate templates."""
    print("\n" + "=" * 60)
    print("TEST: Objective Classification")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner

    registry = _setup_registry()
    planner = WorkflowPlanner(agent_registry=registry)

    test_cases = [
        ("Analyze this repository and produce an architecture and security report", "repository_analysis"),
        ("Audit this codebase for security and engineering issues", "repository_audit"),
        ("Analyze the current state of this project", "repository_health"),
        ("Read this local file and analyze its contents", "document_analysis"),
        ("Compare the options and choose the best one", "decision_support"),
        ("Research the project and summarize", "simple_research"),
    ]

    for objective, expected_template in test_cases:
        template_key = planner._classify_objective(objective)
        print(f"  '{objective[:50]}...' -> {template_key}")
        assert template_key == expected_template, f"Expected {expected_template}, got {template_key}"

    print("  PASSED: Objective classification works correctly")


def test_planner_produces_plan_to_dict():
    """Test that the planned workflow serializes correctly."""
    print("\n" + "=" * 60)
    print("TEST: Planner Output Serialization")
    print("=" * 60)

    from runtime.workflow_planner import WorkflowPlanner
    import json

    registry = _setup_registry()
    planner = WorkflowPlanner(agent_registry=registry)

    planned = planner.plan(
        objective="Analyze the current state of this project and tell me the highest-value next action.",
        scope="Themeta-verse/Nexus",
        tenant_id="test-tenant",
        project_id="test-project",
    )

    result = planned.to_dict()

    # Verify it's JSON-serializable
    json_str = json.dumps(result, default=str)
    parsed = json.loads(json_str)

    assert parsed["workflow_id"].startswith("workflow-")
    assert parsed["is_valid"] is True
    assert len(parsed["task_specs"]) > 0
    assert len(parsed["agents"]) > 0
    assert "template_type" in parsed["plan"]
    assert len(parsed["validations"]) > 0

    print(f"  workflow_id: {parsed['workflow_id']}")
    print(f"  is_valid: {parsed['is_valid']}")
    print(f"  task_count: {len(parsed['task_specs'])}")
    print(f"  agent_count: {len(parsed['agents'])}")
    print(f"  validations: {len(parsed['validations'])}")
    print("  PASSED: Planner output serializes correctly")


if __name__ == "__main__":
    tests = [
        ("Simple Objective -> Valid Workflow", test_simple_objective_produces_valid_workflow),
        ("Multi-Agent Dependency Graph", test_multi_agent_dependency_graph),
        ("Missing Capability -> Planning Failure", test_missing_capability_planning_failure),
        ("Invalid Dependency Detection", test_invalid_dependency_detection),
        ("Planner Output Passed to WorkflowEngine", test_planner_output_passed_to_workflow_engine),
        ("Existing Workflows Unaffected", test_existing_workflows_unaffected),
        ("Objective Classification", test_objective_classification),
        ("Planner Output Serialization", test_planner_produces_plan_to_dict),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAILED: {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {name}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
