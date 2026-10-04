"""NEXUS canonical demonstration (Phase 9).

Executable on this machine with the real backend stack — no mocks:

    python scripts/nexus_canonical_demo.py
    python scripts/nexus_canonical_demo.py --workspace E:/tmp/demo-ws --db E:/tmp/demo.db

Chain demonstrated:
    Planner -> ResearchAgent (real filesystem.read) -> ArchitectureAgent
    -> SecurityAgent (peer QUESTION/ANSWER) -> Reporter -> Verifier
    + dynamic adaptation (auth file -> security follow-up)
    + failure/retry path is exercised by the test suite, not forced here
    + verification -> final report -> persisted execution trace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.agent_registry import AgentRegistry
from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
from runtime.messaging_hub import MessagingHub
from runtime.mission_composer import MissionComposer
from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
from runtime.workflow_planner import WorkflowPlanner
from runtime.workflow_worker import WorkflowWorker, WorkerConfig
from nexus_independent.database import NexusDatabase


OBJECTIVE = "Analyze this repository and produce a security/architecture report."


def build_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "auth_service.py").write_text("import jwt\nSECRET = 'demo-only'\n\ndef login(t):\n    return jwt.encode({}, SECRET)\n")
    (root / "app.py").write_text("print('hello nexus')\n")
    (root / "notes.md").write_text("# demo\nreal workspace for NEXUS autonomy demo\n")
    return root


def build_stack(db_path: Path, artifacts_root: Path):
    db = NexusDatabase(str(db_path))
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as c:
        c.execute("INSERT OR IGNORE INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("demo", "Demo", now))
        c.execute(
            "INSERT OR IGNORE INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("demo-proj", "demo", "Demo", now, now),
        )
        c.commit()
    registry = AgentRegistry()
    register_default_agents(registry)
    hub = MessagingHub(db)
    executor = MultiAgentExecutor(
        database=db, agent_registry=registry, settings=None,
        principal={"tenant_id": "demo", "project_id": "demo-proj"}, messaging_hub=hub,
    )
    engine = WorkflowEngine(
        database=db, composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=False, auto_retry_on_failure=True),
        agent_registry=registry, artifacts_root=artifacts_root, messaging_hub=hub,
    )
    engine.set_executor(executor, agent_registry=registry)
    engine.messaging_hub = hub
    rt = AutonomousRuntime(
        database=db, engine=engine, executor=executor, agent_registry=registry,
        messaging_hub=hub,
        config=AutonomousConfig(tenant_id="demo", project_id="demo-proj", poll_interval_seconds=0.02,
                                max_iterations=200, dynamic_task_creation=True, max_dynamic_tasks=5),
    )
    worker = WorkflowWorker(
        database=db, engine=engine, executor=executor, agent_registry=registry,
        messaging_hub=hub, autonomous_runtime=rt,
        config=WorkerConfig(worker_id="demo-worker", tenant_id="demo", poll_interval_seconds=0.02,
                            stop_on_idle=False, auto_recover_stuck=True, claim_stale_seconds=30),
    )
    return db, engine, registry, hub, rt, worker


def main() -> int:
    parser = argparse.ArgumentParser(description="NEXUS canonical autonomous demo")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="nexus-demo-"))
    workspace = Path(args.workspace) if args.workspace else tmp / "workspace"
    db_path = Path(args.db) if args.db else tmp / "demo.db"
    artifacts_root = db_path.parent / "artifacts"
    build_workspace(workspace)

    db, engine, registry, hub, rt, worker = build_stack(db_path, artifacts_root)
    planner = WorkflowPlanner(agent_registry=registry)
    planned = planner.plan(objective=OBJECTIVE, scope=str(workspace), tenant_id="demo",
                           execution_mode="REAL_READ", project_id="demo-proj")
    assert planned.is_valid, f"plan invalid: {planned.validations}"
    wf = engine.create_workflow("demo", "demo-proj", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    print(f"OBJECTIVE : {OBJECTIVE}")
    print(f"WORKSPACE : {workspace}")
    print(f"WORKFLOW  : {wid}")
    print(f"PLAN      : {[t['task_id'] for t in planned.task_specs]}")

    state = worker.execute_workflow("demo", "demo-proj", wid, max_ticks=150, poll_interval=0.02)
    print(f"STATUS    : {state.get('status')}")

    tasks = db.list_workflow_tasks("demo", wid)
    arts = db.list_workflow_artifacts("demo", wid)
    msgs = db.list_workflow_messages("demo", wid, limit=200)
    events = db.list_workflow_events("demo", wid, limit=200)
    dyn = db.get_dynamic_tasks("demo", wid)
    trace = engine.get_execution_trace("demo", wid)

    print("\n-- agents/tasks --")
    for t in tasks:
        print(f"  {t['task_id']} [{t['task_type']}] agent={t['agent_id']} status={t['status']}")

    print("\n-- artifacts (reality) --")
    for a in arts:
        print(f"  {a['kind']} reality={a['reality']} task={a['task_id']} agent={a['agent_id']}")

    print("\n-- messaging --")
    q = [m for m in msgs if m["message_type"] == "QUESTION"]
    ans = [m for m in msgs if m["message_type"] == "ANSWER"]
    print(f"  total={len(msgs)} QUESTION={len(q)} ANSWER={len(ans)}")
    tools = trace.get("tools_used", {})
    print(f"  tools_used={tools}")
    print(f"\n-- dynamic tasks: {len(dyn)} --")
    for d in dyn:
        print(f"  {d['task_id']} reason={d.get('generated_reason')}")

    # Real observation honesty: hashes on disk must match findings.
    research = [a for a in arts if a["kind"] == "research_report"]
    assert research, "no research_report artifact"
    content = json.loads(Path(research[0]["content_path"]).read_text())
    for f in content["research"]["findings"]:
        digest = hashlib.sha256(Path(f["file"]).read_bytes()).hexdigest()
        assert digest == f["content_hash"], f"hash mismatch for {f['file']}"

    kinds_observed = {a["kind"] for a in arts if a["reality"] == "OBSERVED"}
    assert kinds_observed == {"research_report"}, f"fake OBSERVED: {kinds_observed}"
    assert state.get("status") == "COMPLETED", f"workflow did not complete: {state.get('status')}"
    assert trace.get("finish_reason"), "trace missing finish_reason"

    report_path = artifacts_root / "demo_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({
        "objective": OBJECTIVE, "workspace": str(workspace), "workflow_id": wid,
        "status": state.get("status"), "finish_reason": trace.get("finish_reason"),
        "tasks": [{"task_id": t["task_id"], "agent": t["agent_id"], "status": t["status"]} for t in tasks],
        "artifacts": [{"kind": a["kind"], "reality": a["reality"]} for a in arts],
        "tools_used": tools, "dynamic_tasks": len(dyn),
        "reality_breakdown": trace.get("reality_breakdown"),
    }, indent=2))
    print(f"\nDEMO OK — report: {report_path}")
    print(f"DB: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
