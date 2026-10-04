from __future__ import annotations

import argparse
import json
import os
import sys

import uvicorn

from .api import create_app
from .config import ProductSettings
from .schemas import MissionSubmission
from .service import StandaloneMissionService


def main() -> None:
    known_commands = {"serve", "worker", "workers", "workflow-worker", "workflow", "approval",
                      "run", "submit", "status", "trace", "workflows", "agents",
                      "inspect", "logs", "cancel", "resume", "daemon",
                      "bootstrap", "reset-owner-password", "ask", "health",
                      "migrate", "backup", "restore", "recover", "-h", "--help"}
    if len(sys.argv) > 1 and sys.argv[1] not in known_commands:
        sys.argv.insert(1, "ask")
    parser = argparse.ArgumentParser(prog="nexus-independent", description="Independent NEXUS product runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="start the authenticated standalone NEXUS API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    worker = sub.add_parser("worker", help="process durable queued missions")
    worker.add_argument("--once", action="store_true", help="claim and process at most one queued mission")
    worker.add_argument("--worker-id", default=None)
    wf_worker = sub.add_parser("workflow-worker", help="drive autonomous workflows to completion without the UI (headless background execution)")
    wf_worker.add_argument("--tenant", default=os.getenv("NEXUS_TENANT", "default"))
    wf_worker.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    wf_worker.add_argument("--workflow-id", default=None, help="run one workflow; omit to run all RUNNING/PENDING workflows")
    wf_worker.add_argument("--worker-id", default=None)
    wf_worker.add_argument("--max-ticks", type=int, default=500)
    wf_worker.add_argument("--poll-interval", type=float, default=1.0)
    # Phase 9 control plane: worker registry + workflow lifecycle + approvals.
    # (The pre-existing `worker` command drives the mission queue; `workers`
    # inspects the durable workflow-worker registry.)
    worker_ctl = sub.add_parser("workers", help="durable worker registry: list/stop workflow workers")
    worker_ctl.add_argument("action", choices=["list", "stop"], help="list registered workers or stop one")
    worker_ctl.add_argument("--worker-id", default=None, help="worker to stop (for 'stop')")
    worker_ctl.add_argument("--tenant", default=os.getenv("NEXUS_TENANT", "default"))
    worker_ctl.add_argument("--stale-seconds", type=int, default=30)
    worker_ctl.add_argument("--reason", default="cli stop")
    wf_ctl = sub.add_parser("workflow", help="workflow control plane: run/submit/status/recover/trace (headless, same durable backend)")
    wf_ctl.add_argument("action", choices=["run", "submit", "status", "recover", "trace"])
    wf_ctl.add_argument("--tenant", default=os.getenv("NEXUS_TENANT", "default"))
    wf_ctl.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    wf_ctl.add_argument("--workflow-id", default=None)
    wf_ctl.add_argument("--objective", default=None, help="objective for 'run'/'submit' (plan + persist [+ execute])")
    wf_ctl.add_argument("--scope", default=".", help="workspace path (alias: --workspace)")
    wf_ctl.add_argument("--workspace", default=None, help="workspace path (overrides --scope)")
    wf_ctl.add_argument("--strategy", default=os.getenv("NEXUS_EXECUTION_MODE", "DETERMINISTIC"),
                        help="DETERMINISTIC (default, honest fallback) | HYBRID | MODEL")
    wf_ctl.add_argument("--env", default="LOCAL",
                        help="bounded execution environment: LOCAL (full read-only tools) | SANDBOX (filesystem.read only)")
    wf_ctl.add_argument("--max-ticks", type=int, default=300)
    wf_ctl.add_argument("--poll-interval", type=float, default=0.5)
    wf_ctl.add_argument("--stale-seconds", type=int, default=30)
    wf_ctl.add_argument("--record", default=None, help="write machine-readable execution record (nexus-run.json) to PATH")
    wf_ctl.add_argument("--export-dir", default=None, help="copy final_report + verification_result JSON here")
    # Top-level operator shortcuts (same backend as `workflow ...`).
    st = sub.add_parser("status", help="human-readable workflow summary from durable state")
    st.add_argument("workflow_id")
    st.add_argument("--tenant", default=None)
    st.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    tr = sub.add_parser("trace", help="machine-readable execution trace (JSON) from durable state")
    tr.add_argument("workflow_id")
    tr.add_argument("--tenant", default=None)
    tr.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    wl = sub.add_parser("workflows", help="list persisted workflows")
    wl.add_argument("--tenant", default=os.getenv("NEXUS_TENANT", "default"))
    wl.add_argument("--project", default=None)
    ag = sub.add_parser("agents", help="list registered agents and capabilities")
    ins = sub.add_parser("inspect", help="detailed workflow view: tasks, artifacts, messages, verification (every node is a persisted row)")
    ins.add_argument("workflow_id")
    ins.add_argument("--tenant", default=None)
    ins.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    ins.add_argument("--json", action="store_true", help="machine-readable output instead of human text")
    lg = sub.add_parser("logs", help="workflow event log from durable state")
    lg.add_argument("workflow_id")
    lg.add_argument("--tenant", default=None)
    lg.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    lg.add_argument("--limit", type=int, default=100)
    lg.add_argument("--type", default=None, help="filter by event type")
    lg.add_argument("--json", action="store_true")
    cx = sub.add_parser("cancel", help="hard-stop a workflow (terminal; workers will not resume it)")
    cx.add_argument("workflow_id")
    cx.add_argument("--tenant", default=None)
    cx.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    rs = sub.add_parser("resume", help="resume a PAUSED workflow to RUNNING for the next worker pass")
    rs.add_argument("workflow_id")
    rs.add_argument("--tenant", default=None)
    rs.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    dm = sub.add_parser("daemon", help="run NEXUS continuously: recover stuck work and advance RUNNING/PENDING workflows until stopped")
    dm.add_argument("--tenant", default=os.getenv("NEXUS_TENANT", "default"))
    dm.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    dm.add_argument("--worker-id", default=None)
    dm.add_argument("--poll-interval", type=float, default=2.0)
    dm.add_argument("--max-ticks", type=int, default=50, help="worker ticks per workflow per cycle")
    dm.add_argument("--cycles", type=int, default=0, help="daemon cycles to run (0 = until SIGTERM/Ctrl-C)")
    approval_ctl = sub.add_parser("approval", help="approval gates: list pending or decide (APPROVED/REJECTED/CANCELLED)")
    approval_ctl.add_argument("action", choices=["list", "decide"])
    approval_ctl.add_argument("--tenant", default=os.getenv("NEXUS_TENANT", "default"))
    approval_ctl.add_argument("--project", default=os.getenv("NEXUS_PROJECT", "local"))
    approval_ctl.add_argument("--approval-id", default=None)
    approval_ctl.add_argument("--decision", choices=["APPROVED", "REJECTED", "CANCELLED"], default=None)
    approval_ctl.add_argument("--note", default=None)
    bootstrap = sub.add_parser("bootstrap", help="create or recover the product owner and primary tenant project")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--password", required=True)
    bootstrap.add_argument("--tenant", default="NEXUS")
    bootstrap.add_argument("--project-id", default="local")
    reset_password = sub.add_parser("reset-owner-password", help="development-host-only owner password recovery (audited; revokes sessions)")
    reset_password.add_argument("--email", required=True)
    reset_password.add_argument("--password", required=True)
    reset_password.add_argument("--confirm-dev-local", action="store_true", help="required explicit acknowledgement that this is a local development recovery")
    run = sub.add_parser("run", help='execute a NEXUS objective ("nexus run \\"<objective>\\""); legacy mission path only with --email/--password')
    run.add_argument("intent", nargs="?")
    run.add_argument("--objective", default=None, help="objective text (defaults to the positional intent)")
    run.add_argument("--email", default=os.getenv("NEXUS_CLI_EMAIL"))
    run.add_argument("--password", default=os.getenv("NEXUS_CLI_PASSWORD"))
    run.add_argument("--tenant", default=os.getenv("NEXUS_TENANT", "default"))
    run.add_argument("--project-id", default="local")
    run.add_argument("--project", default=None, help="project alias for the workflow path")
    run.add_argument("--scope", default="Themeta-verse/Nexus")
    run.add_argument("--workspace", default=None, help="workspace path for the workflow path (overrides --scope)")
    run.add_argument("--mode", choices=["REAL_READ", "SIMULATION"], default="SIMULATION")
    run.add_argument("--strategy", default=os.getenv("NEXUS_EXECUTION_MODE", "DETERMINISTIC"))
    run.add_argument("--env", default="LOCAL", help="bounded execution environment: LOCAL | SANDBOX")
    run.add_argument("--max-ticks", type=int, default=300)
    run.add_argument("--poll-interval", type=float, default=0.5)
    run.add_argument("--record", default=None, help="write machine-readable execution record to PATH")
    run.add_argument("--export-dir", default=None, help="copy final_report + verification_result JSON here")
    run.add_argument("--capability", action="append", dest="capabilities")
    run.add_argument("--browser-url")
    run.add_argument("--filesystem-path")
    run.add_argument("--repository-scope")
    health = sub.add_parser("health", help="show standalone runtime health")
    migrate = sub.add_parser("migrate", help="apply standalone SQLite product migrations")
    backup = sub.add_parser("backup", help="create a consistent standalone SQLite backup")
    backup.add_argument("destination")
    restore = sub.add_parser("restore", help="restore a stopped standalone runtime from a SQLite backup")
    restore.add_argument("source")
    restore.add_argument("--confirm-restore", action="store_true", help="required because restore replaces the live product database")
    recover = sub.add_parser("recover", help="recover a persisted mission")
    recover.add_argument("mission_id")
    recover.add_argument("--email", required=True)
    recover.add_argument("--password", required=True)
    ask = sub.add_parser("ask", help="queue a governed objective or read durable project continuity from ordinary language")
    ask.add_argument("intent")
    ask.add_argument("--email", default=os.getenv("NEXUS_CLI_EMAIL"))
    ask.add_argument("--password", default=os.getenv("NEXUS_CLI_PASSWORD"))
    ask.add_argument("--project-id", default=os.getenv("NEXUS_CLI_PROJECT", "local"))
    ask.add_argument("--scope", default=os.getenv("NEXUS_GITHUB_REPOSITORY", "Themeta-verse/Nexus"))
    ask.add_argument("--mode", choices=["REAL_READ", "SIMULATION"], default="REAL_READ")
    args = parser.parse_args()
    settings = ProductSettings.from_env()
    service = StandaloneMissionService(settings)
    if args.command == "serve":
        uvicorn.run(create_app(service), host=args.host or settings.api_host, port=args.port or settings.api_port)
    elif args.command == "worker":
        print(json.dumps({"processed": service.run_worker(args.worker_id, once=args.once), "worker_id": args.worker_id}, indent=2))
    elif args.command == "workflow-worker":
        print(json.dumps(service.run_workflow_worker(
            args.tenant, args.project,
            workflow_id=args.workflow_id,
            worker_id=args.worker_id,
            max_ticks=args.max_ticks,
            poll_interval_seconds=args.poll_interval,
        ), indent=2, default=str))
    elif args.command == "workers":
        if args.action == "list":
            print(json.dumps(service.cli_worker_list(args.tenant, args.stale_seconds), indent=2, default=str))
        else:
            if not args.worker_id:
                raise SystemExit("workers stop requires --worker-id")
            print(json.dumps(service.cli_worker_stop(args.worker_id, args.reason), indent=2, default=str))
    elif args.command == "workflow":
        scope = args.workspace or args.scope
        if args.action == "run":
            if not args.objective:
                raise SystemExit("workflow run requires --objective")
            summary = service.cli_workflow_run(
                args.tenant, args.project, args.objective, scope,
                max_ticks=args.max_ticks, poll_interval_seconds=args.poll_interval,
                strategy=args.strategy, export_dir=args.export_dir,
                environment=args.env)
            if args.record:
                record = service.cli_execution_record(
                    args.tenant, args.project, summary["workflow_id"],
                    worker_id=summary.get("worker_id"),
                    strategy=summary.get("strategy_requested", "DETERMINISTIC"))
                with open(args.record, "w", encoding="utf-8") as fh:
                    json.dump(record, fh, indent=2, default=str)
                summary["record"] = args.record
            print(_format_run_summary(summary))
        elif args.action == "submit":
            if not args.objective:
                raise SystemExit("workflow submit requires --objective")
            print(json.dumps(service.cli_workflow_submit(
                args.tenant, args.project, args.objective, scope,
                environment=args.env,
            ), indent=2, default=str))
        elif args.action == "status":
            if not args.workflow_id:
                raise SystemExit("workflow status requires --workflow-id")
            print(_format_run_summary(service.cli_workflow_summary(
                args.tenant, args.project, args.workflow_id)))
        elif args.action == "recover":
            if not args.workflow_id:
                raise SystemExit("workflow recover requires --workflow-id")
            print(json.dumps(service.cli_workflow_recover(
                args.tenant, args.project, args.workflow_id, args.stale_seconds,
            ), indent=2, default=str))
        else:
            if not args.workflow_id:
                raise SystemExit("workflow trace requires --workflow-id")
            print(json.dumps(service.cli_workflow_trace(args.tenant, args.project, args.workflow_id),
                             indent=2, default=str))
    elif args.command == "status":
        resolved = _resolve_workflow_tenant(service, args.workflow_id, args.tenant, args.project)
        print(_format_run_summary(service.cli_workflow_summary(
            resolved[0], resolved[1], args.workflow_id)))
    elif args.command == "trace":
        resolved = _resolve_workflow_tenant(service, args.workflow_id, args.tenant, args.project)
        print(json.dumps(service.cli_workflow_trace(resolved[0], resolved[1], args.workflow_id),
                         indent=2, default=str))
    elif args.command == "workflows":
        print(json.dumps(service.cli_workflow_list(args.tenant, args.project), indent=2, default=str))
    elif args.command == "agents":
        print(json.dumps(service.cli_agents(), indent=2, default=str))
    elif args.command == "inspect":
        resolved = _resolve_workflow_tenant(service, args.workflow_id, args.tenant, args.project)
        detail = service.cli_workflow_inspect(resolved[0], resolved[1], args.workflow_id)
        if args.json:
            print(json.dumps(detail, indent=2, default=str))
        else:
            print(_format_inspect(detail))
    elif args.command == "logs":
        resolved = _resolve_workflow_tenant(service, args.workflow_id, args.tenant, args.project)
        entries = service.cli_workflow_logs(resolved[0], resolved[1], args.workflow_id,
                                            limit=args.limit, event_type=args.type)
        if args.json:
            print(json.dumps(entries, indent=2, default=str))
        else:
            if not entries:
                print("(no events)")
            for e in entries:
                print(f"{e.get('timestamp', '?')}  [{e.get('event_type', '?')}]"
                      f"  task={e.get('task_id') or '-'} agent={e.get('agent_id') or '-'}")
                if e.get("detail"):
                    print(f"    {e['detail'][:200]}")
    elif args.command == "cancel":
        resolved = _resolve_workflow_tenant(service, args.workflow_id, args.tenant, args.project)
        print(json.dumps(service.cli_workflow_cancel(resolved[0], resolved[1], args.workflow_id),
                         indent=2, default=str))
    elif args.command == "resume":
        resolved = _resolve_workflow_tenant(service, args.workflow_id, args.tenant, args.project)
        try:
            print(json.dumps(service.cli_workflow_resume(resolved[0], resolved[1], args.workflow_id),
                             indent=2, default=str))
        except ValueError as exc:
            raise SystemExit(f"cannot resume: {exc}")
    elif args.command == "daemon":
        _run_daemon(service, args)
    elif args.command == "approval":
        if args.action == "list":
            print(json.dumps(service.cli_approval_list(args.tenant, args.project), indent=2, default=str))
        else:
            if not args.approval_id or not args.decision:
                raise SystemExit("approval decide requires --approval-id and --decision")
            print(json.dumps(service.cli_approval_decide(
                args.tenant, args.approval_id, args.decision, args.note,
            ), indent=2, default=str))
    elif args.command == "bootstrap":
        print(json.dumps(service.bootstrap_owner(args.email, args.password, args.tenant, args.project_id), indent=2, default=str))
    elif args.command == "reset-owner-password":
        if os.getenv("NEXUS_ALLOW_DEV_OWNER_RESET") != "true":
            raise SystemExit("refused: set NEXUS_ALLOW_DEV_OWNER_RESET=true to enable this development-host-only recovery path")
        if not args.confirm_dev_local:
            raise SystemExit("refused: pass --confirm-dev-local to acknowledge local development recovery")
        try:
            result = service.reset_owner_password(args.email, args.password)
        except ValueError as exc:
            raise SystemExit(f"recovery failed: {exc}") from exc
        print(json.dumps({"status": "PASSWORD_RESET", **result}, indent=2, default=str))
    elif args.command == "run":
        # Dual-mode: with product credentials -> legacy mission path;
        # otherwise -> NEXUS workflow path (`nexus run "<objective>"`).
        if args.email and args.password and not (args.objective or args.workspace):
            session = service.login(args.email, args.password)
            if not session:
                raise SystemExit("authentication failed")
            payload = MissionSubmission(intent=args.intent, project_id=args.project_id, scope=args.scope, mode=args.mode, capabilities=args.capabilities, browser_url=args.browser_url, filesystem_path=args.filesystem_path, repository_scope=args.repository_scope)
            print(json.dumps(service.submit_and_execute(session["user"], payload), indent=2, default=str))
        else:
            objective = args.objective or args.intent
            if not objective:
                raise SystemExit('run requires an objective: nexus run "<objective>" [--workspace PATH]')
            project = args.project or args.project_id
            scope = args.workspace or (args.scope if args.scope != "Themeta-verse/Nexus" else ".")
            summary = service.cli_workflow_run(
                args.tenant, project, objective, scope,
                max_ticks=args.max_ticks, poll_interval_seconds=args.poll_interval,
                strategy=args.strategy, export_dir=args.export_dir,
                environment=args.env)
            if args.record:
                record = service.cli_execution_record(
                    args.tenant, project, summary["workflow_id"],
                    worker_id=summary.get("worker_id"),
                    strategy=summary.get("strategy_requested", "DETERMINISTIC"))
                with open(args.record, "w", encoding="utf-8") as fh:
                    json.dump(record, fh, indent=2, default=str)
                summary["record"] = args.record
            print(_format_run_summary(summary))
    elif args.command == "health":
        print(json.dumps(service.health(), indent=2, default=str))
    elif args.command == "migrate":
        service.database.migrate()
        print(json.dumps({"status": "MIGRATED", "database": str(settings.database_path)}, indent=2))
    elif args.command == "backup":
        print(json.dumps({"status": "BACKED_UP", "database": str(service.database.backup_to(args.destination))}, indent=2))
    elif args.command == "restore":
        if not args.confirm_restore:
            raise SystemExit("restore requires --confirm-restore after stopping API and worker processes")
        print(json.dumps({"status": "RESTORED", "database": str(service.database.restore_from(args.source))}, indent=2))
    elif args.command == "recover":
        session = service.login(args.email, args.password)
        if not session:
            raise SystemExit("authentication failed")
        result = service.recover(session["user"], args.mission_id)
        print(json.dumps(result or {"status": "NOT_FOUND"}, indent=2, default=str))
    elif args.command == "ask":
        if not args.email or not args.password:
            raise SystemExit("ask requires product credentials via --email/--password or NEXUS_CLI_EMAIL/NEXUS_CLI_PASSWORD")
        session = service.login(args.email, args.password)
        if not session:
            raise SystemExit("authentication failed")
        principal = session["user"]
        normalized = args.intent.strip().lower()
        context_questions = {"where were we?", "where were we", "what changed?", "what changed", "what should happen next?", "what should happen next", "what next?", "what next"}
        if normalized in context_questions:
            print(json.dumps({"kind": "project_context", "context": service.project_context(principal, args.project_id)}, indent=2, default=str))
            return
        context = service.project_context(principal, args.project_id)
        if normalized in {"continue", "continue."}:
            latest = context.get("latest_mission")
            if latest is None:
                print(json.dumps({"kind": "continuation", "status": "NO_DURABLE_MISSION", "next_action": context["next_action"]}, indent=2))
                return
            print(json.dumps({"kind": "continuation", "result": service.continue_mission(principal, latest["mission_id"])}, indent=2, default=str))
            return
        capabilities = ["repository.metadata.read"]
        if any(term in normalized for term in ("analyze", "project", "repository", "code", "blocking", "unfinished")):
            capabilities = ["repository.read", "filesystem.read"]
        elif any(term in normalized for term in ("browser", "web page", "website")):
            capabilities = ["browser.read"]
        payload = MissionSubmission(intent=args.intent, project_id=args.project_id, scope=args.scope, mode=args.mode, capabilities=capabilities)
        queued = service.enqueue_mission(principal, payload)
        print(json.dumps({"kind": "queued_objective", "mission": queued, "required_worker": "nexus worker", "capabilities": capabilities}, indent=2, default=str))


def _resolve_workflow_tenant(service, workflow_id: str, tenant: str | None, project: str) -> tuple[str, str]:
    """Locate a workflow across tenants when --tenant is omitted (operator UX)."""
    if tenant:
        return tenant, project
    found = service.database.get_workflow_by_id(workflow_id)
    if found is None:
        raise SystemExit(f"workflow not found: {workflow_id}")
    return found["tenant_id"], found.get("project_id") or project


def _format_inspect(detail: dict) -> str:
    """Human-readable workflow inspection, every node backed by a persisted row."""
    lines = ["", "NEXUS INSPECT", "-----------------------------", ""]
    lines.append(f"Workflow: {detail.get('workflow_id', '?')}  [{detail.get('status', '?')}]")
    lines.append(f"Objective: {detail.get('objective', '-')}")
    lines.append(f"Scope: {detail.get('scope', '-')}  (env: {detail.get('execution_environment', 'LOCAL')})")
    lines.append(f"Finish: {detail.get('finish_reason', '-')}")
    lines.append("")
    lines.append("TASKS:")
    for t in detail.get("tasks", []):
        lines.append(f"  - {t.get('task_id')} [{t.get('task_type')}] {t.get('status')}"
                     f"  agent={t.get('agent_id') or 'unassigned'}"
                     f"  worker={t.get('worker_id') or '-'} attempt={t.get('attempt', 0)}"
                     f"  mode={t.get('execution_mode', '?')}")
        if t.get("inputs"):
            lines.append(f"      inputs: {', '.join(str(x)[:40] for x in t['inputs'])}")
        if t.get("outputs"):
            lines.append(f"      outputs: {', '.join(str(x)[:40] for x in t['outputs'])}")
        for a in t.get("artifacts", []):
            lines.append(f"      artifact: {a.get('kind')} [{a.get('reality')}] {str(a.get('artifact_id'))[:18]}")
        lines.append(f"      messages: {t.get('messages', 0)}  retries: {t.get('retry_count', 0)}"
                     f"  started={t.get('started_at') or '-'} completed={t.get('completed_at') or '-'}")
        if t.get("error"):
            lines.append(f"      error: {str(t['error'])[:200]}")
    lines.append("")
    lines.append("ARTIFACTS:")
    for a in detail.get("artifacts", []):
        lines.append(f"  - {a.get('kind')} [{a.get('reality')}] {str(a.get('artifact_id'))[:18]}")
        lines.append(f"      producer={a.get('producer') or '-'} task={a.get('task_id') or '-'}")
        lines.append(f"      hash={str(a.get('content_hash') or '-')[:16]}"
                     f"  verification={a.get('verification_state', 'UNVERIFIED')}")
        if a.get("consumers"):
            lines.append(f"      consumers: {', '.join(str(x)[:16] for x in a['consumers'])}")
        if a.get("provenance"):
            lines.append(f"      provenance: {', '.join(str(x)[:40] for x in a['provenance'][:4])}")
    lines.append("")
    lines.append("APPROVALS:")
    approvals = detail.get("approvals", []) or []
    if approvals:
        for ap in approvals:
            lines.append(f"  - {ap.get('operation')} [{ap.get('status')}] by={ap.get('requested_by') or '-'}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("WORKERS:")
    workers = detail.get("workers", []) or []
    if workers:
        for w in workers:
            lines.append(f"  - {str(w.get('worker_id'))[:20]} [{w.get('liveness') or w.get('status')}]")
    else:
        lines.append("  (none recorded)")
    lines.append("")
    verification = detail.get("verification", {}) or {}
    lines.append(f"Verification: all_completed={verification.get('all_completed', '?')}")
    lines.append("")
    return "\n".join(lines)


def _run_daemon(service, args) -> None:
    """Run NEXUS continuously: recover + advance workflows until stopped.

    Portable by design: no OS service installation, just a reliable loop.
    Survives terminal closure via nohup/Start-Process; survives process
    restart because all state lives in SQLite (a fresh daemon resumes).
    """
    import signal as _signal
    import time as _time
    worker_id = args.worker_id or f"daemon-{args.tenant[:8]}"
    stop = {"flag": False}

    def _handle_stop(signum, frame):
        stop["flag"] = True

    try:
        _signal.signal(_signal.SIGTERM, _handle_stop)
    except (ValueError, OSError):
        pass
    print(f"NEXUS daemon starting: worker={worker_id} tenant={args.tenant} project={args.project}",
          flush=True)
    cycles = 0
    try:
        while not stop["flag"]:
            cycles += 1
            try:
                tick = service.daemon_tick(args.tenant, args.project, worker_id,
                                           max_ticks_per_workflow=args.max_ticks,
                                           poll_interval_seconds=args.poll_interval)
            except Exception as exc:
                print(f"daemon cycle {cycles}: ERROR {exc}", flush=True)
                tick = {"workflows": []}
            active = [w for w in tick.get("workflows", [])
                      if str(w.get("status", "")) not in ("COMPLETED", "FAILED", "CANCELLED")]
            print(f"daemon cycle {cycles}: {len(tick.get('workflows', []))} workflow(s), "
                  f"{len(active)} active", flush=True)
            if args.cycles and cycles >= args.cycles:
                break
            _time.sleep(max(0.5, args.poll_interval))
    except KeyboardInterrupt:
        pass
    finally:
        try:
            service.database.stop_worker(worker_id, reason="daemon shutdown")
        except Exception:
            pass
        print(f"NEXUS daemon stopped after {cycles} cycle(s).", flush=True)


def _format_run_summary(summary: dict) -> str:
    """Human-facing execution summary (Milestone 7), rendered from runtime state only."""
    # ASCII-only so the summary prints on any terminal encoding (cp1252-safe).
    lines = ["", "NEXUS", "-----------------------------", ""]
    lines.append("Objective:")
    lines.append(str(summary.get("objective") or "-"))
    lines.append("")
    lines.append(f"Workflow:\n{summary.get('workflow_id', '?')}  [{summary.get('status', '?')}]")
    lines.append("")
    lines.append("Agents:")
    agents = summary.get("agents", []) or []
    if agents:
        for a in agents:
            mark = "[done]" if a.get("state") == "done" else "[active]"
            lines.append(f"  {mark} {a.get('agent_id')} ({a.get('state')})")
    else:
        lines.append("  -")
    lines.append("")
    lines.append("Tasks:")
    lines.append(f"  {summary.get('tasks_completed', 0)} completed")
    lines.append(f"  {summary.get('tasks_failed', 0)} failed")
    lines.append("")
    lines.append("Observations:")
    lines.append(f"  {summary.get('observations', 0)} real tool observations")
    tools = summary.get("tools", {}) or {}
    for cap, count in tools.items():
        lines.append(f"    {cap} x {count}")
    lines.append("")
    lines.append("Artifacts:")
    lines.append(f"  {summary.get('artifacts', 0)} produced")
    lines.append("")
    lines.append("Messages:")
    lines.append(f"  {summary.get('messages', 0)}")
    lines.append("")
    lines.append("Dynamic tasks:")
    lines.append(f"  {summary.get('dynamic_tasks', 0)}")
    lines.append("")
    lines.append("Verification:")
    lines.append(f"  {summary.get('verification', 'UNKNOWN')}")
    lines.append("")
    if summary.get("duration_seconds") is not None:
        lines.append("Duration:")
        lines.append(f"  {summary['duration_seconds']}s")
        lines.append("")
    if summary.get("final_artifact"):
        lines.append("Final artifact:")
        lines.append(f"  {summary['final_artifact']}")
        lines.append("")
    if summary.get("exported"):
        lines.append("Exported:")
        for path in summary["exported"]:
            lines.append(f"  {path}")
        lines.append("")
    model = summary.get("model", {}) or {}
    if model:
        lines.append(f"Model: {model.get('execution_mode', '?')} "
                     f"(invocations: {model.get('invocations', 0)})")
        lines.append("")
    lines.append(f"Trace:\nnexus trace {summary.get('workflow_id', '')}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
