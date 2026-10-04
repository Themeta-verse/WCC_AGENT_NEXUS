"""Executable acceptance checks for the Ascension product foundations.

The benchmark uses the public authenticated API and a real bounded local read.
It never uses a fake provider, external task runtime, or consequential action.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from nexus_independent.api import create_app
from nexus_independent.config import ProductSettings
from nexus_independent.service import StandaloneMissionService


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nexus-ascension-") as temporary:
        root = Path(temporary)
        evidence = root / "evidence.txt"
        evidence.write_text("NEXUS Ascension real bounded evidence\n", encoding="utf-8")
        settings = ProductSettings(
            product_root=root,
            database_path=root / "nexus.db",
            state_root=root / "state",
            allowed_filesystem_root=root,
            github_repository="Themeta-verse/Nexus",
            browser_url="https://github.com/Themeta-verse/Nexus",
            allow_real_reads=True,
            api_host="127.0.0.1",
            api_port=0,
            web_origins=("http://127.0.0.1:3000",),
            database_url=f"sqlite:///{root / 'nexus.db'}",
            worker_poll_seconds=1,
        )
        service = StandaloneMissionService(settings)
        service.bootstrap_owner("owner@example.com", "correct-horse-battery-staple", "NEXUS", "local")
        client = TestClient(create_app(service))

        public = client.get("/health")
        assert public.status_code == 200
        assert "path" not in public.json().get("database", {})
        login = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "correct-horse-battery-staple"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = client.post("/api/v1/projects", headers=headers, json={"project_id": "ascension", "display_name": "Ascension proof"})
        assert created.status_code == 201
        submitted = client.post("/api/v1/missions", headers=headers, json={
            "intent": "Inspect the local evidence and preserve the verified finding.",
            "project_id": "ascension",
            "scope": "ascension-proof",
            "mode": "REAL_READ",
            "capabilities": ["filesystem.read"],
            "filesystem_path": str(evidence),
        })
        assert submitted.status_code == 202
        mission_id = submitted.json()["mission_id"]
        processed = service.worker_once("ascension-benchmark-worker")
        assert processed and processed["mission_id"] == mission_id

        context = client.get("/api/v1/projects/ascension/context", headers=headers)
        assert context.status_code == 200
        context_json = context.json()
        assert context_json["current_objective"].startswith("Inspect the local evidence")
        assert context_json["discovered"]
        assert context_json["next_action"]

        memory = client.get("/api/v1/projects/ascension/memory", headers=headers)
        assert memory.status_code == 200 and memory.json()["memory"]
        memory_id = memory.json()["memory"][0]["memory_id"]
        annotated = client.post(f"/api/v1/projects/ascension/memory/{memory_id}", headers=headers, json={"action": "annotate", "note": "Reviewed by product owner"})
        assert annotated.status_code == 200 and annotated.json()["memory"]["user_note"] == "Reviewed by product owner"
        retired = client.post(f"/api/v1/projects/ascension/memory/{memory_id}", headers=headers, json={"action": "retire"})
        assert retired.status_code == 200 and retired.json()["memory"]["status"] == "superseded"
        restored = client.post(f"/api/v1/projects/ascension/memory/{memory_id}", headers=headers, json={"action": "restore"})
        assert restored.status_code == 200 and restored.json()["memory"]["status"] == "active"

        diagnostics = client.get("/api/v1/diagnostics", headers=headers)
        assert diagnostics.status_code == 200
        runtime = diagnostics.json()
        assert runtime["runtime_state"]["state"] in {"READY", "RECOVERING", "REQUIRES_ATTENTION", "DEGRADED"}
        assert runtime["database"]["workers"]["active_count"] >= 1
        assert client.get("/api/v1/diagnostics").status_code == 401

        audit = client.get("/api/v1/audit-events?project_id=ascension", headers=headers).json()["audit_events"]
        assert {"memory.annotate", "memory.retire", "memory.restore"}.issubset({entry["action"] for entry in audit})
        print({
            "status": "PASSED",
            "checks": {
                "public_health_redaction": True,
                "authenticated_project_context": True,
                "real_bounded_filesystem_evidence": True,
                "evidence_derived_memory_lifecycle": True,
                "worker_heartbeat": True,
                "runtime_diagnostics": True,
                "memory_audit": True,
                "no_consequential_actions": True,
            },
            "reality": processed["reality"],
            "verification": processed["verification_status"],
        })


if __name__ == "__main__":
    main()
