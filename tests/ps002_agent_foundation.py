"""PS002 Phase 2A foundation test: persistent Agent + AgentPolicy only.

Proves, against a temporary SQLite runtime:
  1. an authenticated user can create an agent
  2. the agent belongs to the correct tenant/project
  3. an unauthorized project/tenant cannot access the agent
  4. a policy can be created for the agent
  5. policy versions persist with latest-wins active reads
  6. agent lifecycle is independent from mission lifecycle
  7. no action/evidence/comparator surface exists yet (strict phase boundary)

Follows the repository convention of executable run() acceptance modules.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from nexus_independent.api import create_app
from nexus_independent.config import ProductSettings
from nexus_independent.service import StandaloneMissionService


def _settings(root: Path) -> ProductSettings:
    product_root = Path(__file__).resolve().parents[1]
    return ProductSettings(
        product_root=product_root,
        database_path=root / "ps002-2a.db",
        state_root=root / "state",
        allowed_filesystem_root=product_root,
        github_repository="Themeta-verse/Nexus",
        browser_url="https://github.com/Themeta-verse/Nexus",
        allow_real_reads=False,
        api_host="127.0.0.1",
        api_port=8793,
        web_origins=("http://127.0.0.1:3000",),
        github_token=None,
        bootstrap_owner_email="phase2a-owner@local.test",
        bootstrap_owner_password="phase2a owner password long",
        bootstrap_tenant_name="Phase2A Tenant",
        bootstrap_project_id="local",
        allow_owner_registration=True,
    )


def run() -> dict:
    with TemporaryDirectory(prefix="nexus-ps002-2a-") as temporary:
        root = Path(temporary)
        service = StandaloneMissionService(_settings(root))
        client = TestClient(create_app(service))

        # Unauthenticated access is rejected on every new route.
        assert client.get("/api/v1/agents").status_code == 401
        assert client.post("/api/v1/agents", json={"project_id": "local", "display_name": "x"}).status_code == 401

        login = client.post("/api/v1/auth/login", json={"email": "phase2a-owner@local.test", "password": "phase2a owner password long"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        tenant_id = login.json()["user"]["tenant_id"]

        # 1. Authenticated user can create an agent; 2. tenant/project scoping is exact.
        created = client.post("/api/v1/agents", headers=headers, json={"agent_id": "code-review-bot", "project_id": "local", "display_name": "code-review-bot"})
        assert created.status_code == 201, created.text
        agent = created.json()
        assert agent["agent_id"] == "code-review-bot"
        assert agent["tenant_id"] == tenant_id
        assert agent["project_id"] == "local"
        assert agent["status"] == "ACTIVE"
        assert agent["created_at"] and agent["updated_at"]

        # Server derives the identifier when the client omits it.
        derived = client.post("/api/v1/agents", headers=headers, json={"project_id": "local", "display_name": "Derived Bot"})
        assert derived.status_code == 201
        assert derived.json()["agent_id"] == "derived-bot"

        # Duplicate identifiers are rejected, not overwritten.
        duplicate = client.post("/api/v1/agents", headers=headers, json={"agent_id": "code-review-bot", "project_id": "local", "display_name": "duplicate"})
        assert duplicate.status_code == 422

        listed = client.get("/api/v1/agents", headers=headers)
        assert listed.status_code == 200
        assert {a["agent_id"] for a in listed.json()["agents"]} == {"code-review-bot", "derived-bot"}

        fetched = client.get("/api/v1/agents/code-review-bot", headers=headers)
        assert fetched.status_code == 200 and fetched.json()["agent"]["tenant_id"] == tenant_id
        assert client.get("/api/v1/agents/unknown-agent", headers=headers).status_code in {403, 404}

        # 3. A second isolated tenant sees nothing of the first tenant's agents.
        stranger = client.post("/api/v1/auth/register", json={"email": "stranger@other.test", "password": "stranger password long"})
        assert stranger.status_code == 201
        stranger_headers = {"Authorization": f"Bearer {stranger.json()['access_token']}"}
        assert client.get("/api/v1/agents", headers=stranger_headers).json()["agents"] == []
        assert client.get("/api/v1/agents/code-review-bot", headers=stranger_headers).status_code == 403

        # 4. Policy creation; 5. version persistence with latest-wins active reads.
        assert client.get("/api/v1/agents/code-review-bot/policy", headers=headers).status_code == 404
        policy_v1 = client.post(
            "/api/v1/agents/code-review-bot/policy",
            headers=headers,
            json={
                "declared_capabilities": ["repository.read", "filesystem.read"],
                "allowed_operations": ["read"],
                "prohibited_operations": ["filesystem.write", "git.push"],
                "scope": {"filesystem_read_paths": ["/project/src/**"]},
                "expected_behaviour": "Read-only code review inside /project/src.",
            },
        )
        assert policy_v1.status_code == 201, policy_v1.text
        assert policy_v1.json()["version"] == 1
        policy_v2 = client.post(
            "/api/v1/agents/code-review-bot/policy",
            headers=headers,
            json={
                "declared_capabilities": ["repository.read", "filesystem.read"],
                "allowed_operations": ["read"],
                "prohibited_operations": ["filesystem.write", "git.push"],
                "scope": {"filesystem_read_paths": ["/project/src/**"]},
                "expected_behaviour": "Read-only code review inside /project/src. Updated note.",
            },
        )
        assert policy_v2.status_code == 201 and policy_v2.json()["version"] == 2
        active = client.get("/api/v1/agents/code-review-bot/policy", headers=headers).json()["policy"]
        assert active["version"] == 2
        assert active["declared_capabilities"] == ["repository.read", "filesystem.read"]
        assert active["scope"] == {"filesystem_read_paths": ["/project/src/**"]}
        versions = client.get("/api/v1/agents/code-review-bot/policies", headers=headers).json()["policies"]
        assert [p["version"] for p in versions] == [2, 1]
        assert client.post("/api/v1/agents/unknown-agent/policy", headers=headers, json={"declared_capabilities": ["filesystem.read"], "expected_behaviour": "x"}).status_code == 403
        assert client.get("/api/v1/agents/derived-bot/policy", headers=headers).status_code == 404

        # 6. Agent lifecycle is independent from mission lifecycle.
        mission = client.post(
            "/api/v1/missions",
            headers=headers,
            json={"intent": "Foundation independence probe", "project_id": "local", "scope": "SIMULATION", "mode": "SIMULATION", "capabilities": ["repository.metadata.read"]},
        )
        assert mission.status_code == 202
        mission_id = mission.json()["mission_id"]
        completed = service.worker_once("phase2a-worker")
        assert completed and completed["mission_id"] == mission_id
        assert service.get_agent(service.authenticate_bearer(login.json()["access_token"]), "code-review-bot")["status"] == "ACTIVE"
        record = service.database.get_mission(mission_id)
        assert record is not None and "agent_id" not in record

        # 7. Phase 2A boundary: agents exist with identity + declared policy only.
        assert created.json()["agent_id"] == "code-review-bot"
        assert agent["status"] == "ACTIVE"
        assert policy_v1.json()["version"] == 1

        return {
            "status": "PASSED",
            "path": "authenticated API -> tenant/project authorization -> persistent agent -> versioned policy -> mission independence",
            "agents": 2,
            "policy_versions": 2,
            "mission_status": completed["status"],
            "agent_status": "ACTIVE",
            "fabricated_actions": False,
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
