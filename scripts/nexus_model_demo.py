"""NEXUS Phase 7 — Real model-backed agent runtime demonstration.

Demonstrates the full evidence-first loop:

  REAL OBSERVATION -> MODEL REASONING -> TOOL REQUEST ->
  REAL TOOL RESULT -> MODEL RESULT -> AGENT HANDOFF -> VERIFICATION

Usage:
  python scripts/nexus_model_demo.py [--objective "..."] [--scope PATH] [--strategy DETERMINISTIC|HYBRID|MODEL]

Behavior:
- Creates a temporary repository with realistic files.
- Starts NEXUS (planner -> workflow -> specialized agents -> messaging ->
  artifacts -> verification -> persistent trace).
- ResearchAgent performs REAL filesystem observation via BoundedAgentRuntime.
- When a model provider is configured (NEXUS_MODEL_PROVIDER + key, or
  NEXUS_OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY /
  GOOGLE_API_KEY, or Ollama running locally), agents use MODEL/HYBRID
  reasoning over observed evidence with bounded tool requests.
- When no provider is configured, runs deterministic fallback and states:
    MODEL PROVIDER: NOT CONFIGURED
    EXECUTION MODE: DETERMINISTIC
  Never fakes model execution.
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
        "# Acme Demo Service\n\nA small Python service with JSON config.\n\n"
        "## Auth\nUses token-based auth (see auth.py).\n",
        encoding="utf-8",
    )
    Path(repo, "app.py").write_text(
        "import os\nimport json\n\ndef main():\n    print('acme demo')\n\nif __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    Path(repo, "auth.py").write_text(
        "# Authentication helpers\nSESSION_TIMEOUT = 3600\n",
        encoding="utf-8",
    )
    Path(repo, "config.json").write_text('{"service": "acme-demo", "port": 8080}\n', encoding="utf-8")
    Path(repo, "package.json").write_text('{"name": "acme-demo", "dependencies": {"react": "^18.0.0"}}\n', encoding="utf-8")
    return repo


def main() -> int:
    parser = argparse.ArgumentParser(description="NEXUS Phase 7 model runtime demo")
    parser.add_argument("--objective", default="Analyze this repository, identify problems, propose fixes, and produce a verified architecture and security report")
    parser.add_argument("--scope", default="")
    parser.add_argument("--strategy", default=os.getenv("NEXUS_EXECUTION_MODE", "HYBRID"))
    args = parser.parse_args()

    from runtime.agent_registry import AgentRegistry
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from runtime.workflow_planner import WorkflowPlanner
    from runtime.model_router import ModelRouter
    from nexus_independent.database import NexusDatabase

    tmpdir = tempfile.mkdtemp(prefix="nexus-phase7-demo-")
    repo = args.scope.strip() or _make_temp_repo(tmpdir)
    print(f"[1] Temporary repository: {repo}")
    for f in sorted(Path(repo).rglob("*")):
        if f.is_file():
            print(f"      - {f.name} ({f.stat().st_size} bytes)")

    # Model status (honest, redacted)
    router = ModelRouter()
    status = router.redacted_status()
    configured = router.is_configured()
    print(f"\n[2] NEXUS model runtime status:")
    print(f"      MODEL PROVIDER: {status.get('provider')}")
    print(f"      MODEL NAME: {status.get('model')}")
    requested = (args.strategy or 'DETERMINISTIC').strip().upper()
    if requested not in ("DETERMINISTIC", "MODEL", "HYBRID"):
        requested = "HYBRID"
    if configured and requested in ("MODEL", "HYBRID"):
        effective = requested
    else:
        effective = "DETERMINISTIC"
    print(f"      EXECUTION MODE: {effective} (requested {requested})")
    if not configured:
        print("      NOTE: no model provider configured — running deterministic fallback.")
        print("            Set NEXUS_MODEL_PROVIDER + NEXUS_MODEL_API_KEY (or run Ollama) for MODEL/HYBRID.")
    # Agents receive the REQUESTED strategy; each agent computes effective mode
    # honestly via router.is_configured() (HYBRID requested -> DETERMINISTIC effective when unconfigured).
    os.environ["NEXUS_EXECUTION_MODE"] = requested

    # Start NEXUS stack
    db_path = os.path.join(tmpdir, "demo.db")
    db = NexusDatabase(db_path)
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("demo", "Demo", now))
        conn.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", ("demo-p", "demo", "Demo", now, now))
    registry = AgentRegistry()
    register_default_agents(registry)
    # Apply REQUESTED strategy to all agents (deterministic behavior preserved; model only enhances)
    for _aid, _reg in list(registry._agents.items()):
        try:
            _reg.instance.execution_strategy = requested
        except Exception:
            pass
    hub = MessagingHub(db)
    # Shared router so HYBRID agents use the real configured provider (or honest fallback)
    executor = MultiAgentExecutor(
        database=db, agent_registry=registry, settings=None,
        principal={"tenant_id": "demo", "project_id": "demo-p"},
        messaging_hub=hub, model_router=router, default_execution_strategy=requested,
    )
    engine = WorkflowEngine(
        database=db, composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(max_retries_default=2, fail_on_agent_not_available=False, auto_retry_on_failure=True),
        agent_registry=registry, artifacts_root=tmpdir, messaging_hub=hub,
    )
    engine.set_executor(executor, agent_registry=registry)
    planner = WorkflowPlanner(agent_registry=registry)
    runtime = AutonomousRuntime(
        database=db, engine=engine, executor=executor,
        agent_registry=registry, messaging_hub=hub, planner=planner,
        config=AutonomousConfig(tenant_id="demo", project_id="demo-p", poll_interval_seconds=0.05, max_iterations=100),
    )

    objective = args.objective
    print(f"\n[3] Objective: {objective}")
    planned = planner.plan(objective=objective, scope=repo, constraints={}, execution_mode="REAL_READ")
    print(f"[4] Planner -> {planned.name} ({len(planned.task_specs)} tasks, template={planned.plan.get('template_type')})")
    for t in planned.task_specs:
        print(f"      - {t['task_id']} [{t['task_type']}] agent={t['agent_id']} depends={t['depends_on']}")

    print("\n[5] Executing autonomous workflow (research -> architecture -> security -> report -> verify) ...")
    result = runtime.execute_objective(objective=objective, scope=repo, constraints={}, max_iterations=60)
    wid = result["workflow_id"]
    print(f"      workflow_id={wid} status={result['status']}")

    # Forensic trace
    state = engine.get_workflow_state("demo", wid)
    trace = engine.get_execution_trace("demo", wid)
    tasks = db.list_workflow_tasks("demo", wid)
    arts = db.list_workflow_artifacts("demo", wid)
    events = db.list_workflow_events("demo", wid, limit=500)
    messages = db.list_workflow_messages("demo", wid, limit=200)

    print("\n[6] ResearchAgent: real filesystem observation")
    research = [a for a in arts if a["kind"] == "research_report"]
    if research:
        content = json.loads(Path(research[0]["content_path"]).read_text()) if research[0].get("content_path") else {}
        findings = (content.get("research", {}) or {}).get("findings", [])
        print(f"      OBSERVED files: {len(findings)}")
        for f in findings[:6]:
            print(f"        - {f.get('file')} sha256={str(f.get('content_hash'))[:12]} reality={f.get('reality')}")
        analysis = (content.get("research", {}) or {}).get("analysis", {})
        if "model_analysis" in analysis:
            print(f"      MODEL reasoning (INFERRED): {str(analysis['model_analysis'])[:300]}")
        elif effective == "DETERMINISTIC":
            print("      (deterministic mode: no model reasoning — honest fallback)")
    else:
        print("      WARNING: no research_report artifact")

    print("\n[7] Model/tool loop events (persisted workflow events)")
    model_events = [e for e in events if e.get("event_type") in (
        "model_invocation", "model_result", "model_failure", "model_timeout",
        "tool_requested", "tool_result", "tool_rejected", "execution_strategy")]
    if model_events:
        for e in model_events[:20]:
            d = e.get("detail", {}) or {}
            print(f"      - {e['event_type']} task={e.get('task_id')} provider={d.get('provider', '')} status={d.get('status', '')} tool={d.get('tool', '')}")
    else:
        print("      (no model events — deterministic execution, no model invoked)")

    print("\n[8] Agent handoff (Architecture <- Research, Security <- Research, Report <- all)")
    for a in arts:
        print(f"      - {a['kind']} by {a.get('agent_id')} reality={a.get('reality')} parents={len(a.get('parent_artifacts') or [])}")

    print("\n[9] Messaging (MessagingHub, persistent)")
    msg_types: dict[str, int] = {}
    for m in messages:
        msg_types[m["message_type"]] = msg_types.get(m["message_type"], 0) + 1
    for k, v in sorted(msg_types.items()):
        print(f"      - {k}: {v}")

    print("\n[10] Verification (independent, never trusts model output)")
    verify = [a for a in arts if a["kind"] == "verification_result"]
    if verify:
        content = json.loads(Path(verify[0]["content_path"]).read_text()) if verify[0].get("content_path") else {}
        vr = content.get("verification_result", {})
        print(f"      all_passed={vr.get('all_passed')} reality={verify[0].get('reality')}")
        for c in vr.get("checks", [])[:10]:
            print(f"        - {c.get('artifact')}/{c.get('check')}: {c.get('status')}")

    print("\n[11] Final trace")
    print(f"      status={trace.get('status')} finish={trace.get('finish_reason')}")
    print(f"      reality_breakdown={trace.get('reality_breakdown')}")
    print(f"      tools_used={trace.get('tools_used')}")
    print(f"      model={json.dumps(trace.get('model', {}), default=str)[:600]}")
    print(f"      tasks={trace['final_result']['completed']}/{trace['final_result']['total']} completed, "
          f"artifacts={trace['final_result']['artifacts']}, messages={trace['final_result']['messages']}")

    print("\n==================================================")
    print(f"MODEL PROVIDER: {status.get('provider')}")
    print(f"EXECUTION MODE: {effective}")
    print(f"WORKFLOW: {wid} -> {result['status']}")
    print(f"TRACE DB: {db_path}")
    if effective == "DETERMINISTIC":
        print("REAL OBSERVATION -> DETERMINISTIC REASONING -> HANDOFF -> VERIFICATION (model not configured)")
    else:
        inv = (trace.get("model", {}) or {}).get("invocations", 0)
        if inv > 0:
            print(f"REAL OBSERVATION -> MODEL REASONING x{inv} -> TOOL REQUEST -> REAL TOOL RESULT -> MODEL RESULT -> HANDOFF -> VERIFICATION")
        else:
            print("WARNING: MODEL requested but no invocations persisted — provider may have been unavailable; deterministic fallback applied.")
    print("==================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
