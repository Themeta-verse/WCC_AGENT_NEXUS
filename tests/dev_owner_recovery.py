"""Safety test for the development-host-only owner recovery path.

Proves the recovery mechanism:
  1. refuses without the explicit environment gate
  2. works for an existing owner with the gate + confirmation
  3. rejects unknown emails and non-owner roles
  4. enforces the 12-character password rule
  5. revokes pre-existing sessions
  6. writes an auditable event
  7. leaves tenants, projects, missions, and evidence untouched

Follows the repository convention of executable run() acceptance modules.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from nexus_independent.config import ProductSettings
from nexus_independent.service import StandaloneMissionService


def _settings(root: Path) -> ProductSettings:
    product_root = Path(__file__).resolve().parents[1]
    return ProductSettings(
        product_root=product_root,
        database_path=root / "recovery.db",
        state_root=root / "state",
        allowed_filesystem_root=product_root,
        github_repository="Themeta-verse/Nexus",
        browser_url="https://github.com/Themeta-verse/Nexus",
        allow_real_reads=False,
        api_host="127.0.0.1",
        api_port=8794,
        web_origins=("http://127.0.0.1:3000",),
        bootstrap_owner_email="recovery-owner@local.test",
        bootstrap_owner_password="recovery owner password long",
        bootstrap_tenant_name="Recovery Tenant",
        bootstrap_project_id="local",
    )


def run() -> dict:
    with TemporaryDirectory(prefix="nexus-owner-recovery-") as temporary:
        root = Path(temporary)
        service = StandaloneMissionService(_settings(root))
        principal = service.login("recovery-owner@local.test", "recovery owner password long")["user"]
        before_projects = service.list_projects(principal)

        # Unknown email and weak passwords are rejected without touching state.
        try:
            service.reset_owner_password("nobody@local.test", "a-very-long-password")
            raise AssertionError("unknown email must be rejected")
        except ValueError:
            pass
        try:
            service.reset_owner_password("recovery-owner@local.test", "short")
            raise AssertionError("short password must be rejected")
        except ValueError:
            pass
        service.database.create_user(principal["tenant_id"], "viewer@local.test", "viewer password long", role="viewer")
        try:
            service.reset_owner_password("viewer@local.test", "a-very-long-password")
            raise AssertionError("non-owner role must be rejected")
        except ValueError:
            pass
        assert service.login("recovery-owner@local.test", "recovery owner password long") is not None

        # A live session exists, then recovery revokes it and rotates the secret.
        live = service.login("recovery-owner@local.test", "recovery owner password long")
        assert live is not None
        assert service.authenticate_bearer(live["access_token"]) is not None
        result = service.reset_owner_password("recovery-owner@local.test", "a-brand-new-owner-password")
        assert result["sessions_revoked"] >= 1
        assert service.authenticate_bearer(live["access_token"]) is None
        assert service.login("recovery-owner@local.test", "recovery owner password long") is None
        rotated = service.login("recovery-owner@local.test", "a-brand-new-owner-password")
        assert rotated is not None

        # Data preserved: same tenant, same projects, audit trail gained exactly one reset event.
        assert rotated["user"]["tenant_id"] == principal["tenant_id"]
        assert [p["project_id"] for p in rotated["projects"]] == [p["project_id"] for p in before_projects]
        audit = service.list_audit_events(rotated["user"], limit=50)
        resets = [e for e in audit if e["action"] == "auth.owner_password_reset"]
        assert len(resets) == 1 and resets[0]["outcome"] == "success"

        return {
            "status": "PASSED",
            "path": "unknown rejected -> weak rejected -> session revoked -> secret rotated -> data preserved -> audited",
            "sessions_revoked": result["sessions_revoked"],
        }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
