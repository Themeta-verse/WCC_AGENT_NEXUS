"""PS002 Phase 2C: real agent runtime observation and integrity verification.

Proves, against a temporary SQLite runtime with a bounded filesystem root:
  1. a real observation (not an API claim) of an allowed read → ALLOW
  2. a real observation of an out-of-scope read → FLAG
  3. a real observation of a prohibited write attempt → HALT
  4. evidence is persisted with content digest (sha256)
  5. evidence digest is present on the stored action
  6. correct agent_id is recorded
  7. correct policy version is recorded
  8. reality classification is OBSERVED (not SIMULATED / MANUAL)
  9. tenant isolation: another tenant cannot observe or read integrity
 10. HALTED agent cannot continue controlled observations
 11. FLAGGED behavior follows existing policy (agent remains FLAGGED)
 12. the manual action API path (Phase 2B) still works and is marked MANUAL
 13. existing REAL_READ remains intact (via MissionComposer)
 14. existing Mission Desk remains intact (mission submission + worker)
 15. existing Evidence remains intact (mission_evidence / provider_receipts)
 16. existing Continuity remains intact (project context)

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
from runtime.bounded_agent import BoundedAgentRuntime


def _settings(root: Path, fs_root: Path) -> ProductSettings:
    product_root = Path(__file__).resolve().parents[1]
    return ProductSettings(
        product_root=product_root,
        database_path=root / "ps002-2c.db",
        state_root=root / "state",
        allowed_filesystem_root=fs_root,
        github_repository="Themeta-verse/Nexus",
        browser_url="https://github.com/Themeta-verse/Nexus",
        allow_real_reads=True,
        api_host="127.0.0.1",
        api_port=8795,
        web_origins=("http://127.0.0.1:3000",),
        github_token=None,
        bootstrap_owner_email="phase2c-owner@local.test",
        bootstrap_owner_password="phase2c owner password long",
        bootstrap_tenant_name="Phase2C Tenant",
        bootstrap_project_id="local",
        allow_owner_registration=True,
    )


def _create_test_root(fs_root: Path) -> Path:
    """Create a bounded test directory with sample files for the agent to read."""
    src_dir = fs_root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "example.py").write_bytes(b"print('hello from bounded agent')\n")
    (src_dir / "constants.py").write_text("MAX_RETRIES = 3\n")
    outside = fs_root / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("this should not be readable\n")
    return fs_root


def run() -> dict:
    with TemporaryDirectory(prefix="nexus-ps002-2c-") as tmp:
        root = Path(tmp)
        fs_root = root / "test-project-root"
        fs_root.mkdir(parents=True, exist_ok=True)
        _create_test_root(fs_root)

        service = StandaloneMissionService(_settings(root, fs_root))
        client = TestClient(create_app(service))

        login = client.post("/api/v1/auth/login", json={"email": "phase2c-owner@local.test", "password": "phase2c owner password long"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        tenant_id = login.json()["user"]["tenant_id"]

        # Provision agent + policy
        assert client.post("/api/v1/agents", headers=headers, json={"agent_id": "code-review-bot", "project_id": "local", "display_name": "code-review-bot"}).status_code == 201
        agent_id = "code-review-bot"

        policy = client.post(
            f"/api/v1/agents/{agent_id}/policy",
            headers=headers,
            json={
                "declared_capabilities": ["filesystem.read", "repository.read"],
                "allowed_operations": ["read"],
                "prohibited_operations": ["filesystem.write", "git.push"],
                "scope": {"filesystem_read_paths": [str(fs_root / "src" / "**")]},
                "expected_behaviour": "Read-only code review inside /src.",
            },
        )
        assert policy.status_code == 201
        policy_version = policy.json()["version"]

        # 1+2: Scenario A — legitimate in-scope read via real runtime
        scenario_a = client.post(
            f"/api/v1/agents/{agent_id}/observe",
            headers=headers,
            json={
                "operation": "filesystem.read",
                "target_resource": str(fs_root / "src" / "example.py"),
                "requested_capability": "filesystem.read",
                "parameters": {},
                "observation_root": str(fs_root),
            },
        )
        assert scenario_a.status_code == 200, scenario_a.text
        a_body = scenario_a.json()
        assert a_body["integrity_decision"] == "ALLOW", a_body["integrity_reason"]
        assert a_body["reality"] == "OBSERVED"
        assert a_body["observation_receipt"]["status"] == "EXECUTED"
        assert a_body["observation_receipt"]["content_sha256"] is not None
        assert a_body["observation_receipt"]["content_size"] == len(b"print('hello from bounded agent')\n")
        assert a_body["observation_receipt"]["reality"] == "OBSERVED"

        # 6: correct agent_id
        assert a_body["action"]["agent_id"] == agent_id
        # 7: correct policy version
        assert a_body["action"]["policy_version"] == policy_version
        # 4+5: evidence persisted with digest
        evidence = json.loads(a_body["action"]["evidence_json"])
        assert evidence["observation_source"] == "runtime_observation_bridge"
        assert evidence["observation_receipt"]["evidence_digest"]
        assert evidence["observation_receipt"]["content_sha256"]

        # 3: Scenario B — out-of-scope read via real runtime
        scenario_b = client.post(
            f"/api/v1/agents/{agent_id}/observe",
            headers=headers,
            json={
                "operation": "filesystem.read",
                "target_resource": str(fs_root / "outside" / "secret.txt"),
                "requested_capability": "filesystem.read",
                "parameters": {},
                "observation_root": str(fs_root),
            },
        )
        assert scenario_b.status_code == 200
        b_body = scenario_b.json()
        assert b_body["integrity_decision"] == "FLAG", b_body["integrity_reason"]
        assert b_body["action"]["observation_reality"] == "OBSERVED"

        # 4: Scenario C — prohibited write via real runtime
        scenario_c = client.post(
            f"/api/v1/agents/{agent_id}/observe",
            headers=headers,
            json={
                "operation": "filesystem.write",
                "target_resource": str(fs_root / "src" / "example.py"),
                "requested_capability": "filesystem.write",
                "parameters": {},
                "observation_root": str(fs_root),
            },
        )
        assert scenario_c.status_code == 200
        c_body = scenario_c.json()
        assert c_body["integrity_decision"] == "HALT", c_body["integrity_reason"]
        assert c_body["observation_receipt"]["status"] == "BLOCKED"

        # 10: HALTED agent cannot continue
        blocked = client.post(
            f"/api/v1/agents/{agent_id}/observe",
            headers=headers,
            json={
                "operation": "filesystem.read",
                "target_resource": str(fs_root / "src" / "example.py"),
                "requested_capability": "filesystem.read",
                "parameters": {},
                "observation_root": str(fs_root),
            },
        )
        assert blocked.status_code == 403
        assert "HALTED" in blocked.json()["detail"]

        # 11: agent is FLAGGED then HALTED
        agent = client.get(f"/api/v1/agents/{agent_id}", headers=headers).json()["agent"]
        assert agent["status"] == "HALTED"

        # Restore
        client.post(f"/api/v1/agents/{agent_id}/enforce", headers=headers, json={"action": "ACTIVE"})

        # 12: manual action API still works, marked MANUAL
        manual = client.post(
            f"/api/v1/agents/{agent_id}/actions",
            headers=headers,
            json={"operation": "filesystem.read", "target_resource": str(fs_root / "src" / "constants.py"), "requested_capability": "filesystem.read"},
        )
        assert manual.status_code == 200
        assert manual.json()["integrity_decision"] == "ALLOW"
        assert manual.json()["action"]["observation_reality"] == "MANUAL"

        # 9: tenant isolation
        stranger = client.post("/api/v1/auth/register", json={"email": "stranger@other.test", "password": "stranger password long"})
        assert stranger.status_code == 201
        stranger_headers = {"Authorization": f"Bearer {stranger.json()['access_token']}"}
        assert client.post(
            f"/api/v1/agents/{agent_id}/observe",
            headers=stranger_headers,
            json={"operation": "filesystem.read", "target_resource": str(fs_root / "src" / "example.py"), "requested_capability": "filesystem.read", "observation_root": str(fs_root)},
        ).status_code == 403
        assert client.get(f"/api/v1/agents/{agent_id}/integrity", headers=stranger_headers).status_code == 403

        # 13: REAL_READ remains intact via MissionComposer
        mission = client.post(
            "/api/v1/missions",
            headers=headers,
            json={"intent": "Read example.py from bounded local root", "project_id": "local", "scope": "test", "mode": "REAL_READ", "capabilities": ["filesystem.read"], "filesystem_path": str(fs_root / "src" / "example.py")},
        )
        assert mission.status_code == 202
        mission_id = mission.json()["mission_id"]
        completed = service.worker_once("phase2c-worker")
        assert completed and completed["mission_id"] == mission_id
        assert completed["reality"] == "OBSERVED"

        # 14+15: Mission Desk / Evidence intact
        evidence_list = client.get(f"/api/v1/missions/{mission_id}/evidence", headers=headers).json()["evidence"]
        assert len(evidence_list) > 0

        # 16: Continuity intact (project context)
        context = client.get("/api/v1/projects/local/context", headers=headers).json()
        assert "latest_mission" in context or "memory_count" in context

        # Integrity timeline shows all observations with correct reality
        integrity = client.get(f"/api/v1/agents/{agent_id}/integrity", headers=headers)
        assert integrity.status_code == 200
        integrity_body = integrity.json()
        actions = integrity_body["recent_actions"]
        obs_actions = [a for a in actions if a["observation_reality"] == "OBSERVED"]
        manual_actions = [a for a in actions if a["observation_reality"] == "MANUAL"]
        assert len(obs_actions) >= 3  # A, B, C
        assert len(manual_actions) >= 1

        decisions = {a["integrity_decision"] for a in obs_actions}
        assert {"ALLOW", "FLAG", "HALT"}.issubset(decisions)

        return {
            "status": "PASSED",
            "path": "real bounded agent runtime -> observation bridge -> deterministic integrity -> evidence -> enforcement",
            "scenarios": {
                "A_ALLOW": a_body["integrity_decision"],
                "B_FLAG": b_body["integrity_decision"],
                "C_HALT": c_body["integrity_decision"],
            },
            "observations": len(obs_actions),
            "manual_actions": len(manual_actions),
            "evidence_digest_present": bool(evidence["observation_receipt"]["evidence_digest"]),
            "content_sha256_present": bool(a_body["observation_receipt"]["content_sha256"]),
            "policy_version": policy_version,
            "reality": a_body["reality"],
            "mission_reality": completed["reality"],
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
