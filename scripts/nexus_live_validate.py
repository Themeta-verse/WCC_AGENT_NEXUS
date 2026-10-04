"""NEXUS live machine validation (Phase 11).

Starts the REAL API server as a subprocess on a temp SQLite DB, then over
real HTTP verifies: /health, owner setup, auth, workflow plan/create/start,
background worker execution (server-side thread, no frontend), artifacts,
messages, lineage, trace, tools/workers endpoints, and restart recovery
(server restart -> state recovered from SQLite).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8931
BASE = f"http://127.0.0.1:{PORT}"
EMAIL = "live-owner@example.com"
PASSWORD = "live-owner-pass-12345"


def http(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw or "{}")
            except ValueError:
                return resp.status, {"_raw": raw}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except ValueError:
            return exc.code, {"_raw": raw}


def wait_for_health(proc: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early: {proc.poll()}")
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=5) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(1.0)
    raise RuntimeError("server did not become healthy in time")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nexus-live-"))
    workspace = tmp / "workspace"
    workspace.mkdir()
    (workspace / "auth_service.py").write_text("import jwt\nSECRET='live'\n")
    (workspace / "app.py").write_text("print('live')\n")
    db_path = tmp / "live.db"
    env = dict(os.environ)
    env["NEXUS_DATABASE_PATH"] = str(db_path)
    env["NEXUS_DATA_ROOT"] = str(tmp / "data")
    env["NEXUS_STATE_ROOT"] = str(tmp / "data" / "state")
    env["NEXUS_ALLOWED_FILESYSTEM_ROOT"] = str(tmp)
    env["NEXUS_API_PORT"] = str(PORT)
    env["NEXUS_ALLOW_OWNER_REGISTRATION"] = "true"

    proc = subprocess.Popen(
        [sys.executable, "-m", "nexus_independent.cli", "serve", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    results: dict[str, object] = {"base": BASE, "db": str(db_path)}
    try:
        wait_for_health(proc)
        code, health = http("GET", "/health")
        assert code == 200, f"/health -> {code} {health}"
        results["health"] = health.get("status")

        code, session = http("POST", "/api/v1/setup/owner", body={"email": EMAIL, "password": PASSWORD})
        if code == 409:
            code, session = http("POST", "/api/v1/auth/login", body={"email": EMAIL, "password": PASSWORD})
        assert code in (200, 201), f"owner setup/login -> {code} {session}"
        token = session["access_token"]
        results["auth"] = "OK"

        code, me = http("GET", "/api/v1/me", token=token)
        assert code == 200, f"/me -> {code} {me}"
        project_id = me["projects"][0]["project_id"]
        results["project"] = project_id

        code, plan = http("POST", "/api/v1/workflows/plan", token=token, body={
            "objective": "Analyze this repository and produce a security/architecture report.",
            "scope": str(workspace), "project_id": project_id, "execution_mode": "REAL_READ",
        })
        assert code == 200, f"plan -> {code} {plan}"
        assert plan["plan"]["is_valid"] is True
        results["plan_tasks"] = len(plan["plan"]["task_specs"])

        # Create workflow from task specs using the WorkflowCreateRequest shape.
        task_specs = [{
            "task_id": t["task_id"], "task_type": t["task_type"], "name": t["name"],
            "agent_id": t.get("agent_id"), "required_capabilities": t.get("required_capabilities", []),
            "depends_on": t.get("depends_on", []), "input_artifacts": t.get("input_artifacts", []),
        } for t in plan["plan"]["task_specs"]]
        agents = [{
            "agent_id": a["agent_id"], "name": a.get("name", a["agent_id"]), "role": a.get("role", a["agent_id"]),
            "capabilities": a.get("capabilities", ["knowledge.read"]),
            "allowed_operations": a.get("allowed_operations", ["read"]),
            "prohibited_operations": a.get("prohibited_operations", []),
            "scope": a.get("scope", {}), "expected_behaviour": a.get("expected_behaviour", ""),
        } for a in plan["plan"].get("agents", [])]
        code, created = http("POST", "/api/v1/workflows", token=token, body={
            "name": "Live validation", "objective": "Analyze this repository and produce a security/architecture report.",
            "scope": str(workspace), "project_id": project_id, "task_specs": task_specs, "agents": agents, "execution_mode": "REAL_READ",
        })
        assert code == 201, f"create -> {code} {created}"
        wid = created["workflow"]["workflow_id"]
        results["workflow"] = wid

        # Background execution: returns immediately, worker thread drives it.
        code, run = http("POST", f"/api/v1/workflows/{wid}/run", token=token)
        assert code == 200, f"run -> {code} {run}"
        assert run.get("worker_id"), f"no worker_id: {run}"
        results["worker"] = run["worker_id"]

        final = None
        for _ in range(120):
            time.sleep(1.0)
            code, state = http("GET", f"/api/v1/workflows/{wid}/state", token=token)
            assert code == 200, f"state -> {code} {state}"
            status = state.get("workflow", {}).get("status") or state.get("status")
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                final = state
                break
        assert final is not None, "workflow did not reach terminal state"
        status = final.get("workflow", {}).get("status")
        if status != "COMPLETED":
            code, tasks = http("GET", f"/api/v1/workflows/{wid}/tasks", token=token)
            code, events = http("GET", f"/api/v1/workflows/{wid}/events?limit=200", token=token)
            print(json.dumps({"TASKS": tasks, "EVENTS": events}, indent=2)[:6000])
        assert status == "COMPLETED", f"workflow status={status}"
        results["final_status"] = status
        results["summary"] = final.get("summary")

        code, arts = http("GET", f"/api/v1/workflows/{wid}/artifacts", token=token)
        assert code == 200 and len(arts["artifacts"]) >= 5, f"artifacts -> {code} {arts}"
        by_kind = {a["kind"]: a["reality"] for a in arts["artifacts"]}
        assert by_kind.get("research_report") == "OBSERVED", by_kind
        assert by_kind.get("verification_result") == "VERIFIED", by_kind
        results["artifacts"] = by_kind

        code, msgs = http("GET", f"/api/v1/workflows/{wid}/messages", token=token)
        assert code == 200 and len(msgs["messages"]) >= 5, f"messages -> {code}"
        types = {m["message_type"] for m in msgs["messages"]}
        assert "QUESTION" in types and "ANSWER" in types, types
        results["message_types"] = sorted(types)

        first_id = arts["artifacts"][0]["artifact_id"]
        code, lineage = http("GET", f"/api/v1/artifacts/{first_id}/lineage", token=token)
        assert code == 200 and lineage["artifact"]["artifact_id"] == first_id, f"lineage -> {code}"
        results["lineage"] = "OK"

        code, trace = http("GET", f"/api/v1/workflows/{wid}/trace", token=token)
        assert code == 200 and trace.get("finish_reason"), f"trace -> {code}"
        assert trace.get("tools_used", {}).get("filesystem.read", {}).get("count", 0) >= 1
        results["finish_reason"] = trace["finish_reason"]
        results["tools_used"] = {k: v["count"] for k, v in trace["tools_used"].items()}

        code, workers = http("GET", "/api/v1/workers", token=token)
        assert code == 200, f"workers -> {code}"
        results["workers_seen"] = len(workers.get("workers", []))
        code, tools = http("GET", "/api/v1/tools", token=token)
        assert code == 200 and len(tools.get("tools", [])) == 5, f"tools -> {code}"
        results["tools_declared"] = len(tools["tools"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Recovery: restart the server on the SAME database, state must survive.
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "nexus_independent.cli", "serve", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        wait_for_health(proc2)
        code, session = http("POST", "/api/v1/auth/login", body={"email": EMAIL, "password": PASSWORD})
        assert code == 200, f"re-login -> {code} {session}"
        token = session["access_token"]
        wid = results["workflow"]
        code, state = http("GET", f"/api/v1/workflows/{wid}/state", token=token)
        assert code == 200
        assert (state.get("workflow", {}).get("status")) == "COMPLETED", state
        results["recovery"] = "OK"
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc2.kill()

    print(json.dumps({"LIVE_VALIDATION": "PASS", **results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
