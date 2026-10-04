"""Final-transition local independence acceptance test.

This test deliberately uses the real bounded filesystem provider rather than a
simulation. It does not need a GitHub token, browser session, network call, or
external platform runtime. Its evidence target is a real source file inside the
configured filesystem boundary.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from nexus_independent.api import create_app
from nexus_independent.config import ProductSettings
from nexus_independent.service import StandaloneMissionService


def run() -> dict:
    product_root = Path(__file__).resolve().parents[1]
    evidence_path = product_root / "README-INDEPENDENT.md"
    assert evidence_path.is_file()
    with TemporaryDirectory(prefix="nexus-final-transition-") as temporary:
        root = Path(temporary)
        settings = ProductSettings(
            product_root=product_root,
            database_path=root / "nexus.db",
            state_root=root / "state",
            allowed_filesystem_root=product_root,
            github_repository="Themeta-verse/Nexus",
            browser_url="https://github.com/Themeta-verse/Nexus",
            allow_real_reads=True,
            api_host="127.0.0.1",
            api_port=8791,
            web_origins=("http://127.0.0.1:3000",),
            github_token=None,
            bootstrap_owner_email="owner@local.test",
            bootstrap_owner_password="final transition owner password",
            bootstrap_tenant_name="Local Independence",
            bootstrap_project_id="independence",
        )
        service = StandaloneMissionService(settings)
        client = TestClient(create_app(service))
        login = client.post("/api/v1/auth/login", json={"email": "owner@local.test", "password": "final transition owner password"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = client.post(
            "/api/v1/missions",
            headers=headers,
            json={
                "intent": "Read the independent product README as bounded local evidence.",
                "project_id": "independence",
                "scope": "Themeta-verse/Nexus",
                "mode": "REAL_READ",
                "capabilities": ["filesystem.read"],
                "filesystem_path": str(evidence_path),
            },
        )
        assert created.status_code == 202
        mission_id = created.json()["mission_id"]
        completed = service.worker_once("local-independence-worker")
        assert completed and completed["mission_id"] == mission_id
        assert completed["status"] == "COMPLETED"
        assert completed["reality"] == "OBSERVED"
        assert completed["verification_status"] == "VERIFIED"
        assert completed["external_invocations"] >= 1
        assert completed["queue"]["status"] == "COMPLETED"

        mission = client.get(f"/api/v1/missions/{mission_id}?include_result=true", headers=headers)
        assert mission.status_code == 200
        result = mission.json()["result"]
        assert result["execution"]["writes_performed"] is False
        evidence = client.get(f"/api/v1/missions/{mission_id}/evidence", headers=headers)
        assert evidence.status_code == 200 and evidence.json()["evidence"]
        memory = client.get("/api/v1/projects/independence/memory", headers=headers)
        assert memory.status_code == 200 and memory.json()["memory"]
        outcomes = client.get("/api/v1/projects/independence/outcomes", headers=headers)
        assert outcomes.status_code == 200 and outcomes.json()["outcomes"][0]["verification_state"] == "VERIFIED"
        checkpoints = client.get(f"/api/v1/missions/{mission_id}/checkpoints", headers=headers)
        assert checkpoints.status_code == 200 and checkpoints.json()["checkpoints"]

        restarted = StandaloneMissionService(settings)
        principal = restarted.authenticate_bearer(login.json()["access_token"])
        assert principal
        recovered = restarted.recover(principal, mission_id)
        assert recovered and recovered["recovery"]["status"] == "RECOVERED"
        assert (settings.state_root / "independence" / "current.json").is_file()
        assert settings.database_path.is_file()
        return {
            "status": "PASSED",
            "path": "user -> authenticated API -> durable queue -> independent worker -> real filesystem.read -> evidence -> verification -> SQLite memory/outcome/checkpoint -> restart recovery",
            "reality": completed["reality"],
            "verification": completed["verification_status"],
            "writes_performed": result["execution"]["writes_performed"],
            "external_task_runtime_required": False,
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
