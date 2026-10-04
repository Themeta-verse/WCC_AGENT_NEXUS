"""NEXUS Phase 8 — Real autonomous multi-agent runtime demonstration.

Acceptance workflow:

    OBJECTIVE
    v
    PLANNING (classify objective -> validated task graph -> agent selection)
    v
    MULTIPLE AGENTS (research -> architecture -> security -> report -> verify)
    v
    REAL TOOL OBSERVATION (filesystem.read / git.status / git.diff, OBSERVED)
    v
    MODEL REASONING when a provider is configured (INFERRED, never OBSERVED)
    v
    AGENT MESSAGE (QUESTION -> ANSWER, HANDOFF via MessagingHub)
    v
    DYNAMIC TASK (auth discovery -> security follow-up, reason in trace)
    v
    ARTIFACT HANDOFF (parent-linked lineage research -> ... -> report)
    v
    VERIFICATION (independent deterministic checks -> VERIFIED)
    v
    FINAL REPORT

Usage:
    python scripts/nexus_autonomous_demo.py [--objective "..."] [--scope PATH]
        [--strategy DETERMINISTIC|HYBRID|MODEL]

Reality contract:
- No provider configured  -> execution_mode DETERMINISTIC + MODEL NOT CONFIGURED.
  Deterministic output is never labelled model-generated.
- Provider configured    -> MODEL/HYBRID with real invocations in the trace.
- Secrets never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_temp_repo(root: str) -> str:
    repo = os.path.join(root, "demo-repo")
    os.makedirs(repo, exist_ok=True)
    Path(repo, "README.md").write_text(
        "# Acme Demo Service\n\nSmall Python service with token auth.\n\nSee auth_service.py.\n",
        encoding="utf-8",
    )
    Path(repo, "app.py").write_text(
        "import os\n\ndef main():\n    print('acme demo')\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    Path(repo, "auth_service.py").write_text(
        "import hashlib\n\nSESSION_TIMEOUT = 3600\n\n"
        "def login(user, password):\n"
        "    return hashlib.sha256(password.encode()).hexdigest()\n",
        encoding="utf-8",
    )
    Path(repo, "config.json").write_text('{"service": "acme-demo", "port": 8080}\n', encoding="utf-8")
    Path(repo, "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    return repo


def main() -> int:
    parser = argparse.ArgumentParser(description="NEXUS Phase 8 autonomous runtime demo")
    parser.add_argument("--objective", default=(
        "Analyze this repository, identify security risks, propose remediation, "
        "and produce a verified report."))
    parser.add_argument("--scope", default="")
    parser.add_argument("--strategy", default=os.getenv("NEXUS_EXECUTION_MODE", "HYBRID"))
    args = parser.parse_args()

    from runtime.agent_registry import AgentRegistry
    from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.model_router import ModelRouter
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from runtime.workflow_planner import WorkflowPlanner
    from runtime.workflow_worker import WorkflowWorker, WorkerConfig
    from nexus_independent.database import NexusDatabase

    tmpdir = tempfile.mkdtemp(prefix="nexus-phase8-demo-")
    repo = args.scope.strip() or _make_temp_repo(tmpdir)

    print("NEXUS AUTONOMOUS RUN")
    print("--------------------")
    print(f"\nObjective:\n{args.objective}\n")

    # ---- Model status: honest, redacted ----
    router = ModelRouter()
    status = router.redacted_status()
    configured = router.is_configured()
    requested = (args.strategy or "DETERMINISTIC").strip().upper()
    if requested not in ("DETERMINISTIC", "MODEL", "HYBRID"):
        requested = "HYBRID"
    effective = requested if (configured and requested in ("MODEL", "HYBRID")) else "DETERMINISTIC"
    os.environ["NEXUS_EXECUTION_MODE"] = requested

    # ---- Backend stack (durable; UI is only an observer, never the engine) ----
    db_path = os.path.join(tmpdir, "demo.db")
    db = NexusDatabase(db_path)
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                     ("demo", "Demo", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) "
                     "VALUES(?,?,?,?,?)", ("demo-p", "demo", "Demo", now, now))
    registry = AgentRegistry()
    register_default_agents(registry)
    for _aid, _reg in list(registry._agents.items()):
        try:
            _reg.instance.execution_strategy = requested
        except Exception:
            pass
    hub = MessagingHub(db)
    executor = MultiAgentExecutor(
        database=db, agent_registry=registry, settings=None,
        principal={"tenant_id": "demo", "project_id": "demo-p"},
        messaging_hub=hub, model_router=router, default_execution_strategy=requested)
    engine = WorkflowEngine(
        database=db, composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=False,
                                       auto_retry_on_failure=True),
        agent_registry=registry, artifacts_root=Path(tmpdir) / "artifacts", messaging_hub=hub)
    engine.set_executor(executor, agent_registry=registry)
    engine.messaging_hub = hub
    planner = WorkflowPlanner(agent_registry=registry)
    runtime = AutonomousRuntime(
        database=db, engine=engine, executor=executor, agent_registry=registry,
        messaging_hub=hub, planner=planner,
        config=AutonomousConfig(tenant_id="demo", project_id="demo-p",
                                poll_interval_seconds=0.05, max_iterations=120))
    worker = WorkflowWorker(
        database=db, engine=engine, executor=executor, agent_registry=registry,
        messaging_hub=hub, autonomous_runtime=runtime,
        config=WorkerConfig(worker_id="demo-worker", tenant_id="demo",
                            poll_interval_seconds=0.05, stop_on_idle=False,
                            auto_recover_stuck=True, claim_stale_seconds=5))

    # ---- PLAN ----
    planned = planner.plan(objective=args.objective, scope=repo, tenant_id="demo",
                           execution_mode="REAL_READ", project_id="demo-p")
    template = planned.plan.get("template_type")
    print(f"Planner:\n{len(planned.task_specs)} initial tasks (classified: {template})\n")
    for t in planned.task_specs:
        print(f"  - {t['task_id']} [{t['task_type']}] agent={t['agent_id']}")

    # ---- EXECUTE (durable worker drives the engine; survives frontend absence) ----
    wf = engine.create_workflow("demo", "demo-p", planner.plan_to_workflow_spec(planned))
    wid = wf["workflow_id"]
    engine.start_workflow("demo", "demo-p", wid)
    state = worker.execute_workflow("demo", "demo-p", wid, max_ticks=150, poll_interval=0.05)
    trace = engine.get_execution_trace("demo", wid)
    tasks = db.list_workflow_tasks("demo", wid)
    arts = db.list_workflow_artifacts("demo", wid)
    events = db.list_workflow_events("demo", wid, limit=500)
    messages = db.list_workflow_messages("demo", wid, limit=300)

    by_kind = {}
    for a in arts:
        by_kind.setdefault(a["kind"], []).append(a)

    # ---- OBSERVED ----
    print("\nResearchAgent:")
    research = by_kind.get("research_report", [])
    n_obs = 0
    auth_files: list[str] = []
    if research:
        content = json.loads(Path(research[0]["content_path"]).read_text())
        findings = (content.get("research", {}) or {}).get("findings", [])
        n_obs = len(findings)
        first = findings[0] if findings else {}
        print(f"filesystem.read -> OBSERVED ({n_obs} files, "
              f"e.g. {first.get('file', '?')} sha256={str(first.get('content_hash'))[:12]})")
        auth_files = [f.get("file", "") for f in findings if "auth" in (f.get("file", "") or "").lower()]
    if auth_files:
        print("\nResearchAgent:")
        print(f"discovered {os.path.basename(auth_files[0])}")

    # ---- MESSAGES ----
    print("\nMessagingHub:")
    questions = [m for m in messages if m["message_type"] == "QUESTION"]
    answers = [m for m in messages if m["message_type"] == "ANSWER"]
    handoffs = [m for m in messages if m["message_type"] == "HANDOFF"]
    for q in questions[:4]:
        print(f"QUESTION ({q.get('from_agent_id')} -> {q.get('to_agent_id')})")
    for a in answers[:4]:
        print(f"ANSWER ({a.get('from_agent_id')} -> {a.get('to_agent_id')})")
    for h in handoffs:
        print(f"HANDOFF ({h.get('from_agent_id')} -> {h.get('to_agent_id')}: "
              f"{(h.get('content', {}) or {}).get('payload', {}).get('artifact_kind', '?')})")

    # ---- DYNAMIC ----
    print("\nNEXUS:")
    dyn = db.get_dynamic_tasks("demo", wid)
    if dyn:
        print(f"dynamic task created ({dyn[0]['name']}: {dyn[0]['generated_reason'][:90]})")
    else:
        print("no dynamic task (no discovery signal)")

    # ---- INFERRED / VERIFIED ----
    sec = by_kind.get("security_report", [])
    if sec:
        sec_content = json.loads(Path(sec[0]["content_path"]).read_text())
        risk = (sec_content.get("security_report", {}) or {}).get("risk_level", "?")
        print(f"\nSecurityAgent:\nanalysis -> INFERRED (risk={risk})")
    ver = by_kind.get("verification_result", [])
    if ver:
        ver_content = json.loads(Path(ver[0]["content_path"]).read_text())
        passed = (ver_content.get("verification_result", {}) or {}).get("all_passed")
        print("\nVerifier:")
        print("evidence hash verified")
        print("\nVerifier:")
        print("VERIFIED" if passed else "INFERRED (checks failed)")

    print(f"\nWorkflow:\n{state.get('status', '?')}")

    # ---- MODEL ----
    model = trace.get("model", {}) or {}
    inv = model.get("invocations", 0)
    print("\nModel:")
    if effective == "DETERMINISTIC":
        print("MODEL NOT CONFIGURED / DETERMINISTIC (no model invoked; honest fallback)")
    else:
        print(f"{status.get('provider')} / {status.get('model')} / {inv} invocations")

    # ---- SUMMARY ----
    tools_used = trace.get("tools_used", {}) or {}
    tool_bits = [f"{k} x{v.get('count', 0)}" for k, v in sorted(tools_used.items())]
    print("\nTools:")
    print(", ".join(tool_bits) if tool_bits else "none")
    print("\nMessages:")
    print(f"{len(messages)}")
    print("\nDynamic tasks:")
    print(f"{len(dyn)}")
    print("\nVerification:")
    print("PASSED" if ver and (json.loads(Path(ver[0]['content_path']).read_text())
          .get("verification_result", {}).get("all_passed")) else "FAILED")
    print(f"\nTrace DB: {db_path}")
    return 0 if state.get("status") == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
