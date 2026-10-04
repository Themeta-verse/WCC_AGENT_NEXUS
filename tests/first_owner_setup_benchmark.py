"""Executable acceptance for safe first-use owner setup and its fail-closed boundary."""
from __future__ import annotations

import os
import tempfile

from fastapi.testclient import TestClient

from nexus_independent.api import create_app
from nexus_independent.service import StandaloneMissionService


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nexus-first-owner-") as root:
        original = dict(os.environ)
        try:
            os.environ["NEXUS_DATA_ROOT"] = root
            os.environ.pop("NEXUS_BOOTSTRAP_OWNER_EMAIL", None)
            os.environ.pop("NEXUS_BOOTSTRAP_OWNER_PASSWORD", None)
            os.environ.pop("NEXUS_ALLOW_OWNER_REGISTRATION", None)
            service = StandaloneMissionService()
            client = TestClient(create_app(service))
            assert client.get("/health").json()["initial_owner_setup_available"] is True
            created = client.post("/api/v1/setup/owner", json={"email": "owner@example.test", "password": "first-owner-password"})
            assert created.status_code == 201, created.text
            session = created.json()
            assert session["user"]["email"] == "owner@example.test"
            assert session["projects"][0]["project_id"] == "local"
            assert client.get("/health").json()["initial_owner_setup_available"] is False
            duplicate = client.post("/api/v1/setup/owner", json={"email": "second@example.test", "password": "second-owner-password"})
            assert duplicate.status_code == 409, duplicate.text
            login = client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "first-owner-password"})
            assert login.status_code == 200, login.text
            assert client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "wrong-password"}).status_code == 401
            assert client.post("/api/v1/auth/register", json={"email": "disabled@example.test", "password": "disabled-owner-password"}).status_code == 403
            os.environ["NEXUS_ALLOW_OWNER_REGISTRATION"] = "true"
            registered_service = StandaloneMissionService()
            registered_client = TestClient(create_app(registered_service))
            registered = registered_client.post("/api/v1/auth/register", json={"email": "isolated@example.test", "password": "isolated-owner-password"})
            assert registered.status_code == 201, registered.text
            first_projects = client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "first-owner-password"}).json()["projects"]
            second_projects = registered.json()["projects"]
            assert first_projects[0]["project_id"] != second_projects[0]["project_id"]
        finally:
            os.environ.clear()
            os.environ.update(original)
    print('{"status":"passed","first_owner_setup":"passed","post_setup_lock":"passed","registration_default_disabled":"passed","isolated_owner_workspace":"passed","session":"passed"}')


if __name__ == "__main__":
    main()
