#!/usr/bin/env python3
"""NEXUS WCC Demo — End-to-end calculator workflow.

This script runs the complete NEXUS coding workflow:
1. Creates a workspace
2. Runs the model-driven coding workflow
3. Shows approval prompts for consequential operations
4. Executes approved operations
5. Generates receipts and artifacts
6. Runs independent verification
7. Displays final VERIFIED/FAILED status
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from runtime.model_coding import CodingDriver, create_demo_workflow, ScriptedProvider, DEMO_PROPOSALS
from runtime.model_router import ModelRouter
from runtime.capability_fabric import initialize_capability_registry
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
from runtime.workflow_planner import WorkflowPlanner
from runtime.agent_registry import AgentRegistry
from runtime.messaging_hub import MessagingHub
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from nexus_independent.database import NexusDatabase
from nexus_independent.config import ProductSettings


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def run_demo(
    workspace: str,
    objective: str = None,
    demo_mode: bool = True,
    interactive: bool = True,
) -> dict[str, Any]:
    """Run the complete NEXUS WCC demo workflow."""

    print("=" * 70)
    print("NEXUS WCC LAUNCHPAD 30 — CODING WORKFLOW DEMO")
    print("=" * 70)
    print()

    if objective is None:
        objective = (
            "Create a calculator application with addition, subtraction, "
            "multiplication and division. Write the implementation in calculator.py, "
            "create test_calculator.py with unit tests for all operations, "
            "run the tests, and verify the implementation works correctly."
        )

    print(f"OBJECTIVE: {objective}")
    print(f"WORKSPACE: {workspace}")
    print(f"DEMO MODE: {demo_mode}")
    print()

    # Setup database and runtime
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "demo.db")
        db = NexusDatabase(db_path)
        db.migrate()

        now = utc_now()
        with db.connect() as conn:
            conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                         ("wcc-tenant", "WCC Demo", now))
            conn.execute(
                "INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) "
                "VALUES(?,?,?,?,?)", ("wcc-project", "wcc-tenant", "WCC Demo", now, now)
            )
            conn.commit()

        # Initialize runtime components
        from runtime.messaging_hub import MessagingHub
        from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
        from runtime.workflow_planner import WorkflowPlanner
        from runtime.capability_fabric import initialize_capability_registry
        from runtime.model_router import ModelRouter
        from runtime.model_router import TASK_IMPLEMENTATION

        connector_registry = initialize_capability_registry()

        db_obj = NexusDatabase(os.path.join(tmpdir, "demo.db"))
        db_obj.migrate()

        registry = AgentRegistry()
        register_default_agents(registry)

        hub = MessagingHub(db_obj)
        engine = WorkflowEngine(
            database=db_obj,
            composer=None,
            policy=WorkflowExecutionPolicy(max_retries_default=1, fail_on_agent_not_available=True),
            agent_registry=registry,
            artifacts_root=os.path.join(tmpdir, "artifacts"),
            messaging_hub=MessagingHub(db_obj),
        )

        executor = MultiAgentExecutor(
            database=db_obj,
            agent_registry=registry,
            settings=None,
            principal={"tenant_id": "wcc-tenant", "project_id": "wcc-project"},
            messaging_hub=MessagingHub(db_obj),
            connector_registry=initialize_capability_registry(),
        )
        engine.set_executor(executor, agent_registry=registry)

        planner = WorkflowPlanner(agent_registry=registry, connector_registry=initialize_capability_registry())

        # Model router
        if demo_mode:
            provider = ScriptedProvider([json.dumps(DEMO_PROPOSALS["calculator"])])
            router = ModelRouter(providers=[provider], allow_mock_fallback=False)
            router.set_task_policy("implementation", ["scripted-test"])
        else:
            # Real model mode - would use Ollama
            from runtime.model_router import OllamaProvider
            provider = OllamaProvider(model="qwen2.5-coder:7b")
            router = ModelRouter(providers=[provider], allow_mock_fallback=False)
            router.set_task_policy("implementation", [provider.name])

        # Create driver
        driver = CodingDriver(
            database=db_obj,
            engine=engine,
            planner=WorkflowPlanner(agent_registry=AgentRegistry()),
            router=ModelRouter(providers=[ScriptedProvider([json.dumps(DEMO_PROPOSALS["calculator"])])], allow_mock_fallback=False),
            tenant_id="wcc-tenant",
            project_id="wcc-project",
            messaging_hub=MessagingHub(db),
            connector_registry=initialize_capability_registry(),
            artifacts_root=os.path.join(tmpdir, "artifacts"),
            demo_mode=True,
        )

        print("Starting workflow...")
        print("-" * 70)

        result = driver.run_objective(
            objective=create_demo_workflow(),
            workspace=workspace,
            demo_mode=True,
        )

    print()
    print("=" * 70)
    print("WORKFLOW COMPLETE")
    print("=" * 70)
    print(f"Status: {result.get('status', 'UNKNOWN')}")
    print(f"Verified: {result.get('verified', False)}")
    print(f"Tasks: {len(result.get('tasks', []))}")
    print(f"Artifacts: {len(result.get('artifacts', []))}")

    if result.get('verification'):
        for v in result['verification']:
            print(f"  Verification: {v['artifact_id']} -> {v['status']} ({v['reality']})")

    return result


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS WCC Demo")
    parser.add_argument("--workspace", default=None, help="Workspace directory (default: temp)")
    parser.add_argument("--objective", default=None, help="Custom objective")
    parser.add_argument("--no-demo", action="store_true", help="Disable demo mode (requires Ollama)")
    parser.add_argument("--non-interactive", action="store_true", help="Run without prompts")

    args = parser.parse_args()

    workspace = args.workspace or os.path.join(tempfile.gettempdir(), "nexus_wcc_demo")
    os.makedirs(workspace, exist_ok=True)

    result = run_demo(
        workspace=workspace,
        objective=args.objective,
        demo_mode=not args.no_demo,
        interactive=not args.non_interactive,
    )

    sys.exit(0 if result.get("verified") else 1)