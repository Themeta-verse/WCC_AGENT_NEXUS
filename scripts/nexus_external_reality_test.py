"""NEXUS external reality test (Milestone 4).

Proves the NEXUS runtime is real through the PUBLIC operator interface only:

  1. Creates a fresh workspace OUTSIDE the NEXUS source tree with known files.
  2. Submits an objective ONLY via the CLI (`workflow submit`).
  3. Executes ONLY via the CLI worker (`workflow-worker`) — separate processes.
  4. Reads the NEXUS SQLite database directly (independent observer).
  5. Independently computes SHA-256 of the original files.
  6. Compares hashes with NEXUS observation records (expected == observed).
  7. Verifies artifact lineage, task graph, multi-agent execution, handoff,
     dynamic adaptation, independent verification, and final workflow state.

Nothing here imports agents, the engine, or the worker. The only NEXUS
contact points are CLI subprocesses and read-only SQLite inspection.

Usage:
    python scripts/nexus_external_reality_test.py [--keep DIR]

Exit code 0 + RESULT: NEXUS_RUNTIME_REAL on full pass, else non-zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBJECTIVE = "Analyze this repository, identify security risks, propose remediation, and produce a verified report."

FILES = {
    "README.md": "# External Demo Service\n\nToken auth lives in src/auth.py.\n",
    "package.json": '{"name": "external-demo", "version": "0.1.0"}\n',
    "src/app.py": "from auth import login\n\ndef main():\n    print(login('op', 'pw'))\n",
    "src/auth.py": "import hashlib\n\nSESSION_TIMEOUT = 3600\n\ndef login(user, pw):\n    return hashlib.sha256(pw.encode()).hexdigest()\n",
    "src/config.py": "PORT = 8080\nDEBUG = False\n",
}

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    return bool(ok)


def cli(db_path: str, *argv: str, timeout: int = 600) -> str:
    env = dict(os.environ)
    env["NEXUS_DATABASE_PATH"] = db_path
    proc = subprocess.run(
        [sys.executable, "-m", "nexus_independent.cli", *argv],
        cwd=NEXUS_ROOT, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"CLI {' '.join(argv)} failed:\n{proc.stderr[-3000:]}")
    return proc.stdout


def rows(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="NEXUS external reality test")
    parser.add_argument("--keep", default=None, help="keep workspace + DB under DIR for inspection")
    args = parser.parse_args()

    print("NEXUS EXTERNAL REALITY TEST")
    print("===========================")
    tmp = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="nexus-ext-reality-"))
    tmp.mkdir(parents=True, exist_ok=True)
    ws = tmp / "nexus_external_demo"
    (ws / "src").mkdir(parents=True, exist_ok=True)
    expected: dict[str, str] = {}
    for rel, text in FILES.items():
        p = ws / rel
        p.write_text(text, encoding="utf-8")
        # Hash the actual bytes on disk (write_text may normalize newlines
        # per-platform) — the same bytes any independent observer would read.
        expected[str(p.resolve())] = hashlib.sha256(p.read_bytes()).hexdigest()
    db_path = str(tmp / "external.db")
    check("Workspace created", ws.is_dir() and len(expected) == 5, f"{ws} with {len(expected)} known files")

    tenant, project = "external", "demo"
    t0 = time.time()
    # --- ONLY the public interface from here on: submit, then worker, separate processes.
    submitted = json.loads(cli(db_path, "workflow", "submit", "--tenant", tenant, "--project", project,
                               "--objective", OBJECTIVE, "--workspace", str(ws)))
    wid = submitted.get("workflow_id", "")
    check("Objective submitted", bool(wid) and submitted.get("status") == "RUNNING",
          f"workflow_id={wid or '?'} via CLI submit (caller exits; worker has not run)")

    worker_out = json.loads(cli(
        db_path, "workflow-worker", "--tenant", tenant, "--project", project,
        "--workflow-id", wid, "--max-ticks", "200", "--poll-interval", "0.05"))
    check("Planner executed", worker_out.get("status") in ("COMPLETED", "FAILED"),
          f"worker {worker_out.get('worker_id')} drove workflow to {worker_out.get('status')}")

    wf = rows(db_path, "SELECT * FROM workflows WHERE workflow_id=?", (wid,))
    check("Workflow completed", len(wf) == 1 and wf[0]["status"] == "COMPLETED",
          f"persisted status={wf[0]['status'] if wf else 'MISSING'}")
    tasks = rows(db_path, "SELECT * FROM workflow_tasks WHERE workflow_id=?", (wid,))
    by_type = {t["task_type"] for t in tasks}
    for role in ("research", "architecture-analysis", "security-analysis", "report", "verification"):
        check(f"{role.split('-')[0].capitalize()} executed",
              any(t["task_type"] == role and t["status"] == "COMPLETED" for t in tasks),
              f"task row present and COMPLETED")
    agents = {t["agent_id"] for t in tasks if t["agent_id"]}
    check("Multiple agents executed", len(agents) >= 4, f"agents={sorted(agents)}")

    # --- Independent SHA-256 reality check against observation records.
    events = rows(db_path, "SELECT * FROM workflow_events WHERE workflow_id=?", (wid,))
    tool_uses = [json.loads(e["detail_json"]) for e in events if e["event_type"] == "tool_used"]
    reads = [d for d in tool_uses if d.get("capability") == "filesystem.read" and d.get("status") == "EXECUTED"]
    check("Real filesystem read", len(reads) >= 2,
          f"{len(reads)} EXECUTED filesystem.read events with receipts")
    matched = 0
    for d in reads:
        target = d.get("target", "")
        try:
            resolved = str(Path(target).resolve())
        except OSError:
            continue
        if resolved in expected and d.get("content_sha256") == expected[resolved]:
            matched += 1
    check("SHA-256 match", matched >= 2 and matched == len(
        [d for d in reads if str(Path(d.get('target', '')).resolve()) in expected]),
        f"{matched} NEXUS records match independently computed file hashes")

    # --- Lineage / handoff / adaptation / verification from persisted rows.
    arts = rows(db_path, "SELECT * FROM workflow_artifacts WHERE workflow_id=?", (wid,))
    by_id = {a["artifact_id"]: a for a in arts}
    kinds = {a["kind"] for a in arts}
    check("Artifact lineage",
          {"research_report", "architecture_plan", "security_report", "final_report",
           "verification_result"} <= kinds
          and all(json.loads(a["parent_artifacts_json"]) != [] or a["kind"] == "research_report" for a in arts)
          and all(pid in by_id for a in arts for pid in json.loads(a["parent_artifacts_json"])),
          f"kinds={sorted(kinds)}, all parents resolve")
    arch = [a for a in arts if a["kind"] == "architecture_plan"]
    research_ids = {a["artifact_id"] for a in arts if a["kind"] == "research_report"}
    check("Agent handoff",
          any(set(json.loads(a["parent_artifacts_json"])) & research_ids for a in arch),
          "architecture_plan descends from the research artifact")
    msgs = rows(db_path, "SELECT * FROM workflow_messages WHERE workflow_id=?", (wid,))
    types = {m["message_type"] for m in msgs}
    corr_q = {m["correlation_id"] for m in msgs if m["message_type"] == "QUESTION" and m["correlation_id"]}
    corr_a = {m["correlation_id"] for m in msgs if m["message_type"] == "ANSWER" and m["correlation_id"]}
    check("Agent messaging",
          "QUESTION" in types and "ANSWER" in types and "HANDOFF" in types and bool(corr_q & corr_a),
          f"{len(msgs)} messages, correlated Q/A pair present, HANDOFF present")
    dyn = rows(db_path, "SELECT * FROM workflow_tasks WHERE workflow_id=? AND dynamic=1", (wid,))
    check("Dynamic adaptation",
          len(dyn) >= 1 and all(t["status"] == "COMPLETED" and t["generated_reason"] for t in dyn),
          f"{len(dyn)} dynamic task(s): {(dyn[0]['generated_reason'] or '')[:80] if dyn else '-'}")
    ver = [a for a in arts if a["kind"] == "verification_result"]
    ver_ok = False
    if ver and ver[0]["reality"] == "VERIFIED" and ver[0]["content_path"]:
        try:
            content = json.loads(Path(ver[0]["content_path"]).read_text())
            vr = content.get("verification_result", {})
            ver_ok = bool(vr.get("all_passed")) and bool(vr.get("independent"))
        except (OSError, ValueError):
            ver_ok = False
    check("Independent verify", ver_ok, "VERIFIED artifact with all_passed + independent flags")
    check("Final state",
          all(t["status"] == "COMPLETED" for t in tasks) and wf[0]["status"] == "COMPLETED",
          f"{len(tasks)}/{len(tasks)} tasks COMPLETED in {time.time() - t0:.1f}s")

    width = max(len(name) for name, _, _ in CHECKS)
    print()
    all_ok = True
    for name, ok, detail in CHECKS:
        all_ok = all_ok and ok
        print(f"{name + ':':<{width + 2}}{'PASS' if ok else 'FAIL'}" + (f"  ({detail})" if detail else ""))
    print()
    print(f"Workspace: {ws}")
    print(f"Database:  {db_path}")
    print(f"RESULT: {'NEXUS_RUNTIME_REAL' if all_ok else 'NEXUS_RUNTIME_NOT_PROVEN'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
