#!/usr/bin/env python3
"""Run the clean-clone, operator-owned NEXUS local product proof.

This is an executable operational test, not a fixture. It starts API, worker,
and command-center processes from the current repository; drives the product's
authenticated HTTP API; executes a bounded REAL_READ; inspects SQLite; then
restarts all services and continues from persisted state.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
def local_port(variable: str) -> int:
    configured = os.getenv(variable)
    if configured:
        return int(configured)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


API_PORT = local_port("NEXUS_OPERATOR_PROOF_API_PORT")
FRONTEND_PORT = local_port("NEXUS_OPERATOR_PROOF_FRONTEND_PORT")
API_BASE = f"http://127.0.0.1:{API_PORT}"
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"


def request(path: str, method: str = "GET", payload: dict[str, Any] | None = None, token: str | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    operation = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(operation, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8") or "{}")
    except urllib.error.URLError as error:
        return 0, {"detail": str(error.reason)}


def wait_for(path: str, expected: int = 200, seconds: int = 20) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, payload = request(path)
        last = payload
        if status == expected:
            return payload
        time.sleep(0.25)
    raise RuntimeError(f"service did not make {path} available: {last}")


def stop(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    for process in processes:
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=4)


@dataclass
class RuntimeProcesses:
    api: subprocess.Popen[str]
    worker: subprocess.Popen[str]
    frontend: subprocess.Popen[str]

    def all(self) -> list[subprocess.Popen[str]]:
        return [self.api, self.worker, self.frontend]


def start(env: dict[str, str], log_dir: Path) -> RuntimeProcesses:
    log_dir.mkdir(parents=True, exist_ok=True)
    api_log = (log_dir / "api.log").open("w", encoding="utf-8")
    worker_log = (log_dir / "worker.log").open("w", encoding="utf-8")
    frontend_log = (log_dir / "frontend.log").open("w", encoding="utf-8")
    api = subprocess.Popen([sys.executable, "-m", "nexus_independent.cli", "serve", "--host", "127.0.0.1", "--port", str(API_PORT)], cwd=ROOT, env=env, stdout=api_log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    worker = subprocess.Popen([sys.executable, "-m", "nexus_independent.cli", "worker", "--worker-id", "operator-proof-worker"], cwd=ROOT, env=env, stdout=worker_log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    frontend = subprocess.Popen(["pnpm", "exec", "vite", "--host", "127.0.0.1", "--port", str(FRONTEND_PORT)], cwd=ROOT / "frontend", env={**env, "VITE_NEXUS_API_BASE_URL": API_BASE}, stdout=frontend_log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    wait_for("/health")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(FRONTEND_URL, timeout=2) as response:
                if response.status == 200:
                    return RuntimeProcesses(api, worker, frontend)
        except urllib.error.URLError:
            time.sleep(0.25)
    stop([api, worker, frontend])
    raise RuntimeError("frontend did not start")


def sqlite_facts(database_path: Path) -> dict[str, Any]:
    with sqlite3.connect(database_path) as db:
        table_names = ("tenants", "users", "projects", "missions", "mission_queue", "mission_events", "mission_evidence", "observations", "provider_receipts", "memory_items", "outcomes", "checkpoints", "audit_events")
        rows = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in table_names}
        sample = db.execute("SELECT mission_id, status, reality, verification_status FROM missions ORDER BY created_at DESC LIMIT 1").fetchone()
        return {"integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0], "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0], "row_counts": rows, "latest_mission": dict(zip(("mission_id", "status", "reality", "verification_status"), sample)) if sample else None}


def run() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="nexus-operator-proof-") as temporary:
        root = Path(temporary)
        data_root = root / "data"
        password = f"OperatorProof-{uuid.uuid4().hex}-secure"
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "NEXUS_PRODUCT_ROOT": str(ROOT),
            "NEXUS_DATA_ROOT": str(data_root),
            "NEXUS_ALLOWED_FILESYSTEM_ROOT": str(ROOT),
            "NEXUS_ALLOW_REAL_READS": "true",
            "NEXUS_WEB_ORIGINS": FRONTEND_URL,
        }
        database_path = data_root / "nexus.db"
        bootstrap = subprocess.run([sys.executable, "-m", "nexus_independent.cli", "bootstrap", "--email", "owner@operator-proof.local", "--password", password, "--tenant", "Operator Proof", "--project-id", "local"], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
        processes = start(env, root / "logs-first")
        try:
            health = wait_for("/health")
            assert health["status"] in {"READY", "DEGRADED"}
            assert health["service"] == "nexus-independent"
            assert request("/api/v1/projects")[0] == 401
            assert request("/api/v1/auth/login", "POST", {"email": "owner@operator-proof.local", "password": "wrong-password"})[0] == 401
            login_status, login = request("/api/v1/auth/login", "POST", {"email": "owner@operator-proof.local", "password": password})
            assert login_status == 200
            token = login["access_token"]
            assert request("/api/v1/me", token="forged-session")[0] == 401
            project_status, project = request("/api/v1/projects", "POST", {"project_id": "operator-proof", "display_name": "Operator proof project"}, token)
            assert project_status == 201
            mission_status, mission = request("/api/v1/missions", "POST", {"intent": "Analyze the current state of this project and tell me the highest-value next action.", "project_id": project["project_id"], "scope": "local operator proof", "mode": "REAL_READ", "capabilities": ["filesystem.read"], "filesystem_path": str(ROOT / "README-INDEPENDENT.md")}, token)
            assert mission_status == 202
            mission_id = mission["mission_id"]
            deadline = time.monotonic() + 25
            completed: dict[str, Any] = mission
            while time.monotonic() < deadline:
                status, completed = request(f"/api/v1/missions/{mission_id}?include_result=true", token=token)
                assert status == 200
                if completed["status"] in {"COMPLETED", "PARTIAL", "FAILED", "BLOCKED"}:
                    break
                time.sleep(0.25)
            assert completed["status"] == "COMPLETED"
            assert completed["reality"] == "OBSERVED"
            assert completed["verification_status"] == "VERIFIED"
            assert completed["result"]["execution"]["writes_performed"] is False
            assert request(f"/api/v1/missions/{mission_id}/events", token=token)[1]["events"]
            assert request(f"/api/v1/missions/{mission_id}/evidence", token=token)[1]["evidence"]
            before_restart = sqlite_facts(database_path)
            assert before_restart["integrity_check"] == "ok"
            assert all(before_restart["row_counts"][table] > 0 for table in ("missions", "mission_events", "mission_evidence", "observations", "provider_receipts", "memory_items", "outcomes", "checkpoints", "audit_events"))
        finally:
            stop(processes.all())
        restarted = start(env, root / "logs-second")
        try:
            login_status, login = request("/api/v1/auth/login", "POST", {"email": "owner@operator-proof.local", "password": password})
            assert login_status == 200
            token = login["access_token"]
            status, continued = request(f"/api/v1/missions/{mission_id}/continue", "POST", token=token)
            assert status == 200 and continued["recovery"]["status"] == "RECOVERED"
            after_restart = sqlite_facts(database_path)
            assert after_restart["latest_mission"]["mission_id"] == mission_id
            return {"status": "PASSED", "repository": str(ROOT), "processes": [{"name": "api", "port": API_PORT, "health": f"{API_BASE}/health", "result": "HEALTHY"}, {"name": "worker", "port": None, "health": "durable queue polling", "result": "CLAIMED_AND_COMPLETED"}, {"name": "frontend", "port": FRONTEND_PORT, "health": FRONTEND_URL, "result": "HTTP_200"}], "database": {"path_created": database_path.exists(), **after_restart}, "mission": {"mission_id": mission_id, "status": completed["status"], "reality": completed["reality"], "verification": completed["verification_status"], "external_writes": False}, "restart": {"api": "RESTARTED", "worker": "RESTARTED", "frontend": "RESTARTED", "continuation": continued["recovery"]["status"]}, "bootstrap": json.loads(bootstrap.stdout)}
        finally:
            stop(restarted.all())


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
