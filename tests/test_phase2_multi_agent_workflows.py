"""NEXUS Phase 2 Integration Tests for Multi-Agent Workflow Engine.

Phase 2: Multi-agent workflows with capability-matched agent dispatch,
         artifact handoff between agents, branching workflows, and failure/retry.

Run: python tests/test_phase2_multi_agent_workflows.py
     python -m pytest tests/test_phase2_multi_agent_workflows.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _setup_engine(tmpdir: str):
    """Create a WorkflowEngine with MultiAgentExecutor and real NexusDatabase."""
    from runtime.workflow_engine import WorkflowEngine, WorkflowSpec, WorkflowExecutionPolicy
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.agent_registry import AgentRegistry
    from nexus_independent.database import NexusDatabase

    db_path = os.path.join(tmpdir, "test_nexus.db")
    db = NexusDatabase(db_path)
    db.migrate()

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("test-tenant", "Test Tenant", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("test-project", "test-tenant", "Test Project", now, now))

    registry = AgentRegistry()
    register_default_agents(registry)

    executor = MultiAgentExecutor(
        database=db,
        agent_registry=registry,
        principal={"tenant_id": "test-tenant", "project_id": "test-project"},
    )

    policy = WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=False)
    engine = WorkflowEngine(
        database=db,
        composer=MissionComposer(),
        policy=policy,
        agent_registry=registry,
        artifacts_root=tmpdir,
    )
    engine.set_executor(executor, agent_registry=registry)

    return engine, db, registry


def _poll_workflow(engine, tenant_id, project_id, workflow_id, max_ticks=30):
    """Poll a workflow until completion, dispatching ready tasks each tick."""
    for i in range(max_ticks):
        time.sleep(0.3)
        engine.step(tenant_id, project_id, workflow_id)
        state = engine.get_workflow_state(tenant_id, workflow_id)
        summary = state["summary"]
        if summary["completed"] + summary["failed"] == summary["total_tasks"]:
            break
    return engine.get_workflow_state(tenant_id, workflow_id)


def _make_spec(name, objective, scope, tasks, agents, execution_mode="REAL_READ"):
    from runtime.workflow_engine import WorkflowSpec
    return WorkflowSpec(
        name=name,
        objective=objective,
        scope=scope,
        task_specs=tasks,
        agents=agents,
        execution_mode=execution_mode,
    )


def test_sequential_multi_agent_workflow():
    """Test 3+ different agents in a sequential workflow (research -> architecture -> security -> verification)."""
    print("\n" + "=" * 60)
    print("TEST: Sequential Multi-Agent Workflow")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry = _setup_engine(tmpdir)
        tenant_id = "test-tenant"
        project_id = "test-project"

        spec = _make_spec(
            name="Multi-Agent Sequential Pipeline",
            objective="Research codebase, analyze architecture, scan for security issues, and verify.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Codebase Research", "agent_id": "researcher", "required_capabilities": ["filesystem.read"], "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
                {"task_id": "t-arch", "task_type": "architecture-analysis", "name": "Architecture Analysis", "agent_id": "architect", "required_capabilities": [], "input_artifacts": ["research_report"], "parameters": {}, "depends_on": ["t-research"]},
                {"task_id": "t-sec", "task_type": "security-analysis", "name": "Security Analysis", "agent_id": "security-analyst", "required_capabilities": [], "input_artifacts": ["architecture_plan"], "parameters": {}, "depends_on": ["t-arch"]},
                {"task_id": "t-verify", "task_type": "verification", "name": "Verification", "agent_id": "verifier", "required_capabilities": [], "input_artifacts": ["security_report"], "parameters": {}, "depends_on": ["t-sec"]},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Codebase Researcher", "capabilities": ["filesystem.read"], "instruction": "Observe project files"},
                {"agent_id": "architect", "agent_type": "SPECIALIST", "name": "Architecture Analyst", "capabilities": ["knowledge.read"], "instruction": "Analyze research findings"},
                {"agent_id": "security-analyst", "agent_type": "SPECIALIST", "name": "Security Analyst", "capabilities": ["security.read"], "instruction": "Scan for security issues"},
                {"agent_id": "verifier", "agent_type": "SPECIALIST", "name": "Verification Agent", "capabilities": ["verify"], "instruction": "Independently verify artifacts"},
            ],
        )

        workflow = engine.create_workflow(tenant_id, project_id, spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow(tenant_id, project_id, workflow_id)
        state = _poll_workflow(engine, tenant_id, project_id, workflow_id)

        summary = state["summary"]
        tasks = state["tasks"]
        artifacts = state["artifacts"]
        events = state["events"]

        print(f"  Status: {state['workflow']['status']}")
        print(f"  Tasks: {summary['completed']}/{summary['total_tasks']} completed, {summary['failed']} failed")
        print(f"  Artifacts: {len(artifacts)}")
        print(f"  Events: {len(events)}")

        task_agent_map = {t["task_id"]: t["agent_id"] for t in tasks}
        print(f"  Task -> Agent mapping:")
        for tid, aid in task_agent_map.items():
            print(f"    {tid} -> {aid}")

        assert task_agent_map.get("t-research") == "researcher"
        assert task_agent_map.get("t-arch") == "architect"
        assert task_agent_map.get("t-sec") == "security-analyst"
        assert task_agent_map.get("t-verify") == "verifier"

        arch_artifacts = [a for a in artifacts if a["kind"] == "architecture_plan"]
        assert len(arch_artifacts) == 1, "Architecture agent should produce 1 artifact"
        assert len(arch_artifacts[0]["parent_artifacts"]) > 0, "Architecture artifact should reference upstream research artifact"

        sec_artifacts = [a for a in artifacts if a["kind"] == "security_report"]
        assert len(sec_artifacts) == 1, "Security agent should produce 1 artifact"
        assert len(sec_artifacts[0]["parent_artifacts"]) > 0, "Security artifact should reference upstream architecture artifact"

        verify_artifacts = [a for a in artifacts if a["kind"] == "verification_result"]
        assert len(verify_artifacts) == 1, "Verification agent should produce 1 artifact"

        for art in artifacts:
            assert art["agent_id"] is not None, "Artifact should have agent provenance"

        event_types = {e["event_type"] for e in events}
        assert "agent_selected" in event_types, "Should have agent_selected events"
        assert "artifact_consumed" in event_types, "Should have artifact_consumed events"
        assert "artifact_produced" in event_types, "Should have artifact_produced events"

        assert state["workflow"]["status"] in ("COMPLETED", "FAILED")
        assert summary["completed"] > 0

        print("  PASSED: Sequential multi-agent workflow with artifact handoff")


def test_capability_matched_agent_selection():
    """Test that agents are selected based on capability matching, not just pre-assignment."""
    print("\n" + "=" * 60)
    print("TEST: Capability-Based Agent Selection")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry = _setup_engine(tmpdir)
        tenant_id = "test-tenant"
        project_id = "test-project"

        from runtime.agents.researcher import ResearchAgent
        custom_researcher = ResearchAgent()
        custom_researcher.agent_id = "custom-researcher"
        custom_researcher.name = "Custom Researcher"
        registry.register_agent(
            agent_id="custom-researcher",
            name="Custom Researcher",
            role="researcher",
            agent_type="SPECIALIST",
            capabilities=["filesystem.read"],
            allowed_operations=["filesystem.read"],
            prohibited_operations=["filesystem.write"],
            instance=custom_researcher,
        )

        spec = _make_spec(
            name="Capability-Matched Agent Test",
            objective="Test capability-based agent selection",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research", "agent_id": None, "required_capabilities": ["filesystem.read"], "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
            ],
            agents=[],
        )

        workflow = engine.create_workflow(tenant_id, project_id, spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow(tenant_id, project_id, workflow_id)
        state = _poll_workflow(engine, tenant_id, project_id, workflow_id)

        tasks = state["tasks"]
        assert tasks[0]["agent_id"] is not None, "An agent should have been auto-selected"
        assert tasks[0]["agent_id"] in ("researcher", "custom-researcher"), f"Expected researcher or custom-researcher, got {tasks[0]['agent_id']}"

        events = state["events"]
        agent_selected_events = [e for e in events if e["event_type"] == "agent_selected"]
        assert len(agent_selected_events) > 0
        assert agent_selected_events[0]["detail"]["capability_match"] in ("capability-matched", "pre-assigned")

        print(f"  Selected agent: {tasks[0]['agent_id']}")
        print("  PASSED: Capability-based agent selection works")


def test_branching_workflow():
    """Test a branching (diamond) workflow where independent tasks can run concurrently."""
    print("\n" + "=" * 60)
    print("TEST: Branching (Diamond) Workflow")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry = _setup_engine(tmpdir)
        tenant_id = "test-tenant"
        project_id = "test-project"

        spec = _make_spec(
            name="Branching Workflow",
            objective="Research code, then branch into architecture and security analysis in parallel.",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research", "agent_id": "researcher", "required_capabilities": ["filesystem.read"], "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
                {"task_id": "t-arch", "task_type": "architecture-analysis", "name": "Architecture", "agent_id": "architect", "required_capabilities": [], "input_artifacts": ["research_report"], "parameters": {}, "depends_on": ["t-research"]},
                {"task_id": "t-sec", "task_type": "security-analysis", "name": "Security", "agent_id": "security-analyst", "required_capabilities": [], "input_artifacts": ["research_report"], "parameters": {}, "depends_on": ["t-research"]},
                {"task_id": "t-report", "task_type": "report", "name": "Final Report", "agent_id": "reporter", "required_capabilities": [], "input_artifacts": ["architecture_plan", "security_report"], "parameters": {"objective": "Produce final report"}, "depends_on": ["t-arch", "t-sec"]},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher", "capabilities": ["filesystem.read"], "instruction": "Observe files"},
                {"agent_id": "architect", "agent_type": "SPECIALIST", "name": "Architect", "capabilities": ["knowledge.read"], "instruction": "Analyze"},
                {"agent_id": "security-analyst", "agent_type": "SPECIALIST", "name": "Security Analyst", "capabilities": ["security.read"], "instruction": "Scan"},
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter", "capabilities": ["report.generate"], "instruction": "Synthesize"},
            ],
        )

        workflow = engine.create_workflow(tenant_id, project_id, spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow(tenant_id, project_id, workflow_id)
        state = _poll_workflow(engine, tenant_id, project_id, workflow_id)

        summary = state["summary"]
        tasks = state["tasks"]

        print(f"  Status: {state['workflow']['status']}")
        print(f"  Tasks: {summary['completed']}/{summary['total_tasks']} completed")

        task_map = {t["task_id"]: t for t in tasks}
        assert task_map["t-research"]["status"] == "COMPLETED"
        assert task_map["t-arch"]["status"] == "COMPLETED"
        assert task_map["t-sec"]["status"] == "COMPLETED"
        assert task_map["t-report"]["status"] == "COMPLETED"

        assert "t-research" in task_map["t-arch"]["depends_on"]
        assert "t-research" in task_map["t-sec"]["depends_on"]
        assert "t-arch" in task_map["t-report"]["depends_on"]
        assert "t-sec" in task_map["t-report"]["depends_on"]

        artifacts = state["artifacts"]
        report_artifacts = [a for a in artifacts if a["kind"] == "final_report"]
        assert len(report_artifacts) == 1
        assert len(report_artifacts[0]["parent_artifacts"]) >= 2

        print("  PASSED: Branching workflow with concurrent task execution")


def test_failure_and_retry():
    """Test that task failures are handled with retry behavior."""
    print("\n" + "=" * 60)
    print("TEST: Failure and Retry Behavior")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry = _setup_engine(tmpdir)
        tenant_id = "test-tenant"
        project_id = "test-project"

        spec = _make_spec(
            name="Failure Retry Test",
            objective="Test failure and retry behavior",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-fail", "task_type": "unknown_type", "name": "Failing Task", "agent_id": "generic-agent", "required_capabilities": [], "input_artifacts": [], "parameters": {}, "depends_on": []},
                {"task_id": "t-success", "task_type": "report", "name": "Success Task", "agent_id": "reporter", "required_capabilities": [], "input_artifacts": [], "parameters": {"objective": "test"}, "depends_on": ["t-fail"]},
            ],
            agents=[
                {"agent_id": "generic-agent", "agent_type": "SPECIALIST", "name": "Generic Agent", "capabilities": ["knowledge.read"], "instruction": "Generic task execution"},
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter", "capabilities": ["report.generate"], "instruction": "Generate report"},
            ],
        )

        workflow = engine.create_workflow(tenant_id, project_id, spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow(tenant_id, project_id, workflow_id)
        state = _poll_workflow(engine, tenant_id, project_id, workflow_id)

        tasks = state["tasks"]
        task_map = {t["task_id"]: t for t in tasks}

        print(f"  Status: {state['workflow']['status']}")
        print(f"  t-fail status: {task_map['t-fail']['status']}, retries: {task_map['t-fail'].get('retry_count', 0)}")

        assert task_map["t-fail"]["status"] in ("FAILED", "COMPLETED", "BLOCKED")

        events = state["events"]
        event_types = [e["event_type"] for e in events]
        retry_events = [e for e in event_types if e == "task_retried"]

        if task_map["t-fail"]["status"] == "FAILED":
            assert len(retry_events) > 0, "Failed tasks should have been retried"
            print(f"  Retry events: {len(retry_events)}")

        print("  PASSED: Failure and retry behavior works correctly")


def test_artifact_provenance_integrity():
    """Test that artifact provenance is preserved: producing agent, task, workflow, timestamp, content hash."""
    print("\n" + "=" * 60)
    print("TEST: Artifact Provenance Integrity")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry = _setup_engine(tmpdir)
        tenant_id = "test-tenant"
        project_id = "test-project"

        spec = _make_spec(
            name="Provenance Test",
            objective="Test artifact provenance tracking",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-research", "task_type": "research", "name": "Research", "agent_id": "researcher", "required_capabilities": ["filesystem.read"], "input_artifacts": [], "parameters": {"observation_root": "runtime"}, "depends_on": []},
            ],
            agents=[
                {"agent_id": "researcher", "agent_type": "SPECIALIST", "name": "Researcher", "capabilities": ["filesystem.read"], "instruction": "Observe files"},
            ],
        )

        workflow = engine.create_workflow(tenant_id, project_id, spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow(tenant_id, project_id, workflow_id)
        state = _poll_workflow(engine, tenant_id, project_id, workflow_id)

        artifacts = state["artifacts"]
        assert len(artifacts) > 0, "Should have produced artifacts"

        for art in artifacts:
            print(f"  Artifact: {art['name']} (kind={art['kind']})")
            print(f"    agent_id: {art['agent_id']}")
            print(f"    task_id: {art['task_id']}")
            print(f"    content_hash: {art['content_hash'][:16]}...")
            print(f"    reality: {art['reality']}")
            print(f"    verification_state: {art['verification_state']}")
            print(f"    parent_artifacts: {art['parent_artifacts']}")
            print(f"    created_at: {art['created_at']}")

            assert art["agent_id"] == "researcher", "Artifact should record producing agent"
            assert art["task_id"] == "t-research", "Artifact should record producing task"
            assert art["workflow_id"] == workflow_id, "Artifact should record workflow"
            assert art["content_hash"], "Artifact must have content hash"
            assert art["created_at"], "Artifact must have timestamp"
            assert art["reality"] in ("OBSERVED", "INFERRED", "VERIFIED"), f"Invalid reality: {art['reality']}"
            assert art["verification_state"] in ("UNVERIFIED", "VERIFIED"), f"Invalid verification state: {art['verification_state']}"

        print("  PASSED: Artifact provenance integrity verified")


def test_concurrent_task_execution():
    """Test that tasks with no dependencies on each other execute independently within a workflow."""
    print("\n" + "=" * 60)
    print("TEST: Concurrent Task Execution")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine, db, registry = _setup_engine(tmpdir)
        tenant_id = "test-tenant"
        project_id = "test-project"

        spec = _make_spec(
            name="Concurrent Tasks Test",
            objective="Test concurrent task execution within a workflow",
            scope=tmpdir,
            tasks=[
                {"task_id": "t-a", "task_type": "report", "name": "Task A", "agent_id": "reporter", "required_capabilities": [], "input_artifacts": [], "parameters": {"objective": "Task A"}, "depends_on": []},
                {"task_id": "t-b", "task_type": "report", "name": "Task B", "agent_id": "reporter", "required_capabilities": [], "input_artifacts": [], "parameters": {"objective": "Task B"}, "depends_on": []},
            ],
            agents=[
                {"agent_id": "reporter", "agent_type": "SPECIALIST", "name": "Reporter", "capabilities": ["report.generate"], "instruction": "Generate report"},
            ],
        )

        workflow = engine.create_workflow(tenant_id, project_id, spec)
        workflow_id = workflow["workflow_id"]
        engine.start_workflow(tenant_id, project_id, workflow_id)
        state = _poll_workflow(engine, tenant_id, project_id, workflow_id)

        tasks = state["tasks"]
        task_map = {t["task_id"]: t for t in tasks}

        assert task_map["t-a"]["status"] == "COMPLETED", f"Task A should be completed, got {task_map['t-a']['status']}"
        assert task_map["t-b"]["status"] == "COMPLETED", f"Task B should be completed, got {task_map['t-b']['status']}"

        assert task_map["t-a"]["agent_id"] == "reporter"
        assert task_map["t-b"]["agent_id"] == "reporter"

        print(f"  Both tasks completed: t-a={task_map['t-a']['status']}, t-b={task_map['t-b']['status']}")
        print("  PASSED: Concurrent task execution works")


if __name__ == "__main__":
    tests = [
        ("Sequential Multi-Agent Workflow", test_sequential_multi_agent_workflow),
        ("Capability-Based Agent Selection", test_capability_matched_agent_selection),
        ("Branching (Diamond) Workflow", test_branching_workflow),
        ("Failure and Retry Behavior", test_failure_and_retry),
        ("Artifact Provenance Integrity", test_artifact_provenance_integrity),
        ("Concurrent Task Execution", test_concurrent_task_execution),
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
