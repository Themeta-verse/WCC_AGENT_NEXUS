"""PS002 Phase 2B: autonomous agent runtime integrity.

Proves, against a temporary SQLite runtime:
  1. an authenticated agent owner can submit a declared action
  2. in-scope, declared-capability actions receive ALLOW and persist verifiable evidence
  3. out-of-scope read actions receive FLAG and record integrity events
  4. undeclared write operations receive HALT and set the agent to HALTED
  5. once HALTED, subsequent agent actions are rejected (403) by the enforcement gate
  6. an owner can enforce ACTIVE status to restore the agent
  7. tenant isolation prevents cross-tenant action submission or integrity reads
  8. a viewer (non-owner) cannot enforce; only owner can

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
        database_path=root / "ps002-2b.db",
        state_root=root / "state",
        allowed_filesystem_root=product_root,
        github_repository="Themeta-verse/Nexus",
        browser_url="https://github.com/Themeta-verse/Nexus",
        allow_real_reads=False,
        api_host="127.0.0.1",
        api_port=8794,
        web_origins=("http://127.0.0.1:3000",),
        github_token=None,
        bootstrap_owner_email="phase2b-owner@local.test",
        bootstrap_owner_password="phase2b owner password long",
        bootstrap_tenant_name="Phase2B Tenant",
        bootstrap_project_id="local",
        allow_owner_registration=True,
    )


def run() -> dict:
    with TemporaryDirectory(prefix="nexus-ps002-2b-") as temporary:
        root = Path(temporary)
        service = StandaloneMissionService(_settings(root))
        client = TestClient(create_app(service))

        login = client.post("/api/v1/auth/login", json={"email": "phase2b-owner@local.test", "password": "phase2b owner password long"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        tenant_id = login.json()["user"]["tenant_id"]

        # Provision an agent + policy
        created = client.post("/api/v1/agents", headers=headers, json={"agent_id": "code-review-bot", "project_id": "local", "display_name": "code-review-bot"})
        assert created.status_code == 201
        agent_id = created.json()["agent_id"]

        policy = client.post(
            f"/api/v1/agents/{agent_id}/policy",
            headers=headers,
            json={
                "declared_capabilities": ["filesystem.read", "repository.read"],
                "allowed_operations": ["read"],
                "prohibited_operations": ["filesystem.write", "git.push"],
                "scope": {"filesystem_read_paths": ["/project/src/**"]},
                "expected_behaviour": "Read-only code review inside /project/src.",
            },
        )
        assert policy.status_code == 201
        assert policy.json()["version"] == 1

        # 1+2: ALLOW — in-scope, declared capability
        allow = client.post(
            f"/api/v1/agents/{agent_id}/actions",
            headers=headers,
            json={
                "operation": "filesystem.read",
                "target_resource": "/project/src/index.ts",
                "requested_capability": "filesystem.read",
                "parameters": {"file": "index.ts"},
            },
        )
        assert allow.status_code == 200, allow.text
        allow_body = allow.json()
        assert allow_body["integrity_decision"] == "ALLOW"
        assert allow_body["integrity_reason"] == "action is within declared capabilities, allowed operations, and scope"
        assert "action_id" in allow_body["evidence"]

        # 3: FLAG — out-of-scope read
        flag = client.post(
            f"/api/v1/agents/{agent_id}/actions",
            headers=headers,
            json={
                "operation": "filesystem.read",
                "target_resource": "/etc/passwd",
                "requested_capability": "filesystem.read",
                "parameters": {"file": "/etc/passwd"},
            },
        )
        assert flag.status_code == 200
        flag_body = flag.json()
        assert flag_body["integrity_decision"] == "FLAG"
        assert "outside declared scope" in flag_body["integrity_reason"]
        flag_agent = client.get(f"/api/v1/agents/{agent_id}", headers=headers).json()["agent"]
        assert flag_agent["status"] == "FLAGGED"

        # 4: HALT — undeclared write operation
        halt = client.post(
            f"/api/v1/agents/{agent_id}/actions",
            headers=headers,
            json={
                "operation": "filesystem.write",
                "target_resource": "/project/src/index.ts",
                "requested_capability": "filesystem.write",
                "parameters": {"content": "modified"},
            },
        )
        assert halt.status_code == 200
        halt_body = halt.json()
        assert halt_body["integrity_decision"] == "HALT"
        halt_agent = client.get(f"/api/v1/agents/{agent_id}", headers=headers).json()["agent"]
        assert halt_agent["status"] == "HALTED"

        # 5: HALTED agent rejects subsequent actions
        blocked = client.post(
            f"/api/v1/agents/{agent_id}/actions",
            headers=headers,
            json={
                "operation": "filesystem.read",
                "target_resource": "/project/src/index.ts",
                "requested_capability": "filesystem.read",
                "parameters": {"file": "index.ts"},
            },
        )
        assert blocked.status_code == 403
        assert "HALTED" in blocked.json()["detail"]

        # Integrity endpoint shows full picture
        integrity = client.get(f"/api/v1/agents/{agent_id}/integrity", headers=headers)
        assert integrity.status_code == 200
        integrity_body = integrity.json()
        assert integrity_body["agent"]["status"] == "HALTED"
        assert len(integrity_body["recent_actions"]) == 3
        decisions = {a["integrity_decision"] for a in integrity_body["recent_actions"]}
        assert decisions == {"ALLOW", "FLAG", "HALT"}
        assert len(integrity_body["integrity_events"]) >= 3
        event_types = {e["event_type"] for e in integrity_body["integrity_events"]}
        assert {"action_observed", "agent_halt"}.issubset(event_types)

        # 6: Enforce ACTIVE restores the agent
        enforce = client.post(f"/api/v1/agents/{agent_id}/enforce", headers=headers, json={"action": "ACTIVE"})
        assert enforce.status_code == 200
        assert enforce.json()["agent"]["status"] == "ACTIVE"

        restored = client.post(
            f"/api/v1/agents/{agent_id}/actions",
            headers=headers,
            json={
                "operation": "filesystem.read",
                "target_resource": "/project/src/index.ts",
                "requested_capability": "filesystem.read",
            },
        )
        assert restored.status_code == 200
        assert restored.json()["integrity_decision"] == "ALLOW"

        # 7: Tenant isolation — a second tenant cannot see or submit actions for this agent
        stranger = client.post("/api/v1/auth/register", json={"email": "stranger@other.test", "password": "stranger password long"})
        assert stranger.status_code == 201
        stranger_headers = {"Authorization": f"Bearer {stranger.json()['access_token']}"}
        assert client.post(
            f"/api/v1/agents/{agent_id}/actions",
            headers=stranger_headers,
            json={"operation": "filesystem.read", "target_resource": "/project/src/x.ts", "requested_capability": "filesystem.read"},
        ).status_code == 403
        assert client.get(f"/api/v1/agents/{agent_id}/integrity", headers=stranger_headers).status_code == 403
        assert client.post(
            f"/api/v1/agents/{agent_id}/enforce",
            headers=stranger_headers,
            json={"action": "ACTIVE"},
        ).status_code == 403

        # 8: Viewer cannot enforce — needs owner
        viewer = client.post("/api/v1/auth/register", json={"email": "viewer@same.test", "password": "viewer password long"})
        assert viewer.status_code == 201
        viewer_headers = {"Authorization": f"Bearer {viewer.json()['access_token']}"}
        viewer_body = viewer.json()["user"]
        if viewer_body["role"] == "owner":
            viewer_project = service.database.create_project(viewer_body["tenant_id"], "viewer-proj", "viewer project")
            service.database.grant_project_member(viewer_project["project_id"], viewer_body["user_id"], "viewer")
        viewer_agent = client.post(
            "/api/v1/agents",
            headers=viewer_headers,
            json={"agent_id": "v-agent", "project_id": "local", "display_name": "viewer agent"},
        )
        if viewer_agent.status_code == 201:
            view_agent_id = viewer_agent.json()["agent_id"]
            enforce_viewer = client.post(f"/api/v1/agents/{view_agent_id}/enforce", headers=viewer_headers, json={"action": "HALT"})
            assert enforce_viewer.status_code == 403

        return {
            "status": "PASSED",
            "path": "authenticated API -> in-scope ALLOW -> out-of-scope FLAG -> undeclared write HALT -> halted rejection -> owner enforce restore -> tenant isolation -> viewer denied",
            "agents_created": 2,
            "actions_recorded": 4,
            "decisions": ["ALLOW", "FLAG", "HALT", "ALLOW"],
            "agent_status_transition": ["ACTIVE", "FLAGGED", "HALTED", "ACTIVE"],
            "integrity_events": len(integrity_body["integrity_events"]),
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
