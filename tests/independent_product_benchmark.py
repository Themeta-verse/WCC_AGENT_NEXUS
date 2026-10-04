"""Independent product regression benchmark for queue, tenancy, and direct reads.

The benchmark deliberately executes a simulation mission only. It uses a local
HTTP fixture for the direct GitHub adapter and never invokes an external write,
browser, remote credential, or external task capability.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from nexus_independent.api import create_app
from nexus_independent.config import ProductSettings
from nexus_independent.service import StandaloneMissionService
from runtime.canonical_pilot import DirectGitHubAPIAdapter


class FixtureResponse:
    status_code = 200

    def json(self) -> dict:
        return {"name": "fixture", "default_branch": "main", "visibility": "public"}


def run() -> dict:
    with TemporaryDirectory(prefix="nexus-independent-") as temp:
        root = Path(temp)
        settings = ProductSettings(
            product_root=Path(__file__).resolve().parents[1],
            database_path=root / "product.db",
            state_root=root / "state",
            allowed_filesystem_root=Path(__file__).resolve().parents[1],
            github_repository="Themeta-verse/Nexus",
            browser_url="https://github.com/Themeta-verse/Nexus",
            allow_real_reads=False,
            api_host="127.0.0.1",
            api_port=8787,
            web_origins=("http://127.0.0.1:3000",),
            github_token="benchmark-product-managed-token",
            bootstrap_owner_email="owner@example.test",
            bootstrap_owner_password="benchmark owner password",
            bootstrap_tenant_name="Benchmark Tenant",
            bootstrap_project_id="benchmark",
        )
        service = StandaloneMissionService(settings)
        client = TestClient(create_app(service))

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["database"]["status"] == "HEALTHY"
        assert "queue" not in health.json()["database"]
        private_health = service.health()
        assert private_health["github"]["transport"] == "direct-github-rest"
        assert private_health["github"]["authentication"] == "PRODUCT_MANAGED_TOKEN"

        assert client.get("/api/v1/projects").status_code == 401
        failed_login = client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "incorrect"})
        assert failed_login.status_code == 401
        login = client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "benchmark owner password"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["projects"][0]["project_id"] == "benchmark"
        authenticated_health = client.get("/api/v1/health", headers=headers)
        assert authenticated_health.status_code == 200
        assert authenticated_health.json()["database"]["queue"]["queued"] == 0

        blocked = client.post("/api/v1/missions", headers=headers, json={"intent": "Quick repository identity", "project_id": "benchmark", "mode": "REAL_READ", "capabilities": ["repository.metadata.read"]})
        assert blocked.status_code == 403
        created = client.post("/api/v1/missions", headers=headers, json={"intent": "Quick repository identity", "project_id": "benchmark", "mode": "SIMULATION", "capabilities": ["repository.metadata.read"]})
        assert created.status_code == 202
        queued = created.json()
        mission_id = queued["mission_id"]
        assert queued["status"] == "QUEUED"
        assert queued["queue"]["status"] == "QUEUED"
        assert queued["external_invocations"] == 0

        worker_result = service.worker_once("benchmark-worker")
        assert worker_result and worker_result["mission_id"] == mission_id
        assert worker_result["status"] == "PARTIAL"
        assert worker_result["queue"]["status"] == "COMPLETED"

        record = client.get(f"/api/v1/missions/{mission_id}?include_result=true", headers=headers)
        assert record.status_code == 200
        assert record.json()["result"]["execution"]["writes_performed"] is False
        events = client.get(f"/api/v1/missions/{mission_id}/events", headers=headers)
        assert events.status_code == 200
        assert {event["event_type"] for event in events.json()["events"]} >= {"mission_queued", "mission_executing", "mission_completed", "queue_settled"}
        memory = client.get("/api/v1/projects/benchmark/memory", headers=headers)
        assert memory.status_code == 200
        assert len(memory.json()["memory"]) >= 1
        outcomes = client.get("/api/v1/projects/benchmark/outcomes", headers=headers)
        assert outcomes.status_code == 200
        assert outcomes.json()["outcomes"][0]["mission_id"] == mission_id
        checkpoints = client.get(f"/api/v1/missions/{mission_id}/checkpoints", headers=headers)
        assert checkpoints.status_code == 200
        assert checkpoints.json()["checkpoints"][0]["state"] == "PARTIAL"
        audit_events = client.get("/api/v1/audit-events?project_id=benchmark", headers=headers)
        assert audit_events.status_code == 200
        assert any(event["action"] == "mission.enqueue" for event in audit_events.json()["audit_events"])
        capability_response = client.get("/api/v1/capabilities", headers=headers)
        assert capability_response.status_code == 200 and len(capability_response.json()["capabilities"]) == 4
        provider_response = client.get("/api/v1/providers", headers=headers)
        assert provider_response.status_code == 200 and "github-read" in provider_response.json()["providers"]
        github_provider = provider_response.json()["providers"]["github-read"]
        assert github_provider["identity"] == "github-read"
        assert github_provider["side_effects"] is False
        assert github_provider["execution_state"] in {"EXECUTED", "NOT_EXECUTED"}
        assert client.get("/api/v1/capabilities", headers=headers).json()["capabilities"][0]["authorization"] in {"READ_ONLY_AUTHORIZED", "NOT_AVAILABLE"}
        database_inspection = client.get("/api/v1/operator/database", headers=headers)
        assert database_inspection.status_code == 200
        assert database_inspection.json()["database"] == "sqlite"
        assert database_inspection.json()["integrity_check"] == "ok"
        assert database_inspection.json()["row_counts"]["mission_evidence"] >= 1

        controlled = client.post("/api/v1/missions", headers=headers, json={"intent": "Pause before evidence", "project_id": "benchmark", "mode": "SIMULATION", "capabilities": ["repository.metadata.read"]})
        assert controlled.status_code == 202
        controlled_id = controlled.json()["mission_id"]
        paused = client.post(f"/api/v1/missions/{controlled_id}/control/pause", headers=headers)
        assert paused.status_code == 200 and paused.json()["status"] == "PAUSED"
        assert service.worker_once("paused-worker") is None
        resumed = client.post(f"/api/v1/missions/{controlled_id}/control/resume", headers=headers)
        assert resumed.status_code == 200 and resumed.json()["status"] == "QUEUED"
        cancelled = client.post(f"/api/v1/missions/{controlled_id}/control/cancel", headers=headers)
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
        assert service.worker_once("cancelled-worker") is None

        restarted = StandaloneMissionService(settings)
        principal = restarted.authenticate_bearer(token)
        assert principal
        recovered = restarted.recover(principal, mission_id)
        assert recovered and recovered["recovery"]["status"] in {"RECOVERED", "PARTIAL", "COMPLETED", "UNKNOWN"}
        continued = client.post(f"/api/v1/missions/{mission_id}/continue", headers=headers)
        assert continued.status_code == 200
        assert continued.json()["mission"]["mission_id"] == mission_id
        assert any(event["event_type"] == "mission_continued" for event in client.get(f"/api/v1/missions/{mission_id}/events", headers=headers).json()["events"])
        assert settings.database_path.exists()
        assert (settings.state_root / "benchmark" / "current.json").exists()

        operator = service.database.create_user(principal["tenant_id"], "operator@example.test", "operator benchmark password", role="operator")
        service.database.grant_project_member("benchmark", operator["user_id"], "operator")
        operator_login = client.post("/api/v1/auth/login", json={"email": "operator@example.test", "password": "operator benchmark password"})
        assert operator_login.status_code == 200
        operator_headers = {"Authorization": f"Bearer {operator_login.json()['access_token']}"}
        assert client.post("/api/v1/projects", headers=operator_headers, json={"project_id": "forbidden", "display_name": "Forbidden"}).status_code == 403
        assert client.get("/api/v1/operator/database", headers=operator_headers).status_code == 403
        assert client.get("/api/v1/me", headers={"Authorization": "Bearer malformed-session-token"}).status_code == 401

        second = service.bootstrap_owner("other@example.test", "another benchmark password", "Other Tenant", "other-project")
        second_login = client.post("/api/v1/auth/login", json={"email": "other@example.test", "password": "another benchmark password"})
        assert second_login.status_code == 200
        forbidden = client.get(f"/api/v1/missions/{mission_id}", headers={"Authorization": f"Bearer {second_login.json()['access_token']}"})
        assert forbidden.status_code == 403

        captured: dict = {}

        def fixture_http(url: str, *, headers: dict, timeout: int) -> FixtureResponse:
            captured.update({"url": url, "headers": headers, "timeout": timeout})
            return FixtureResponse()

        adapter = DirectGitHubAPIAdapter(token="not-returned", request_fn=fixture_http)
        metadata = adapter.observe_metadata("fixture/repo")
        assert metadata.status == "SUCCESS"
        assert captured["url"].endswith("/repos/fixture/repo")
        assert captured["headers"]["Authorization"] == "Bearer not-returned"
        assert metadata.provenance["adapter"] == "direct-github-rest"

        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/v1/me", headers=headers).status_code == 401
        return {
            "status": "PASSED",
            "checks": {
                "sqlite_database": True,
                "product_owned_authentication": True,
                "tenant_project_isolation": True,
                "api_submission_is_queued": True,
                "worker_execution_and_events": True,
                "restart_recovery": True,
                "project_scoped_memory_and_outcomes": True,
                "checkpoint_and_audit_persistence": True,
                "pause_resume_cancel_controls": True,
                "real_reads_configuration_gate": True,
                "direct_github_rest_transport": True,
                "no_external_side_effects": True,
            },
            "runtime": "nexus_independent queue -> MissionComposer -> LocalStateStore + SQLite",
            "external_task_runtime_required": False,
        }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
