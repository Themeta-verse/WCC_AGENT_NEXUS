"""NEXUS Git Connector — second real provider behind the generic fabric.

Pattern differs from GitHub: local subprocess (no network, no token),
bounded to a workspace git checkout via BoundedAgentRuntime. Exposes:

  git.status -> `git status --short --branch` observation
  git.diff   -> `git diff --no-color` observation (optional path)

Auth model: NO credential is involved, so auth_state is
NO_CREDENTIAL_REQUIRED (canonical vocabulary, runtime.capability_fabric) rather
than CONNECTED — "credential validated" is vacuous for a local connector and
claiming it would overstate what was checked. Availability is a PER-REQUEST
property (is this workspace a git work tree?) decided at execution time; a
non-git directory is an honest BLOCKED OBSERVED refusal, not an auth failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict
import hashlib
import json


GIT_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "git.status": {
        "description": "Read-only git status inspection",
        "risk": "LOW",
        "verification": "OBSERVED",
        # A git work tree is addressed by an existing directory.
        "scope_kind": "absolute_path",
    },
    "git.diff": {
        "description": "Read-only git diff inspection",
        "risk": "LOW",
        "verification": "OBSERVED",
        "scope_kind": "absolute_path",
    },
}


def _now():
    return datetime.now(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


try:
    from runtime.connector_registry import Connector
except ImportError:  # pragma: no cover - top-level import style
    from connector_registry import Connector


@dataclass
class GitConnector(Connector):
    """Local git inspection behind the generic Connector contract."""

    def __post_init__(self):
        self.connector_id = "git"
        self.provider = "git"
        self.version = "1.0.0"
        self.capabilities = GIT_CAPABILITIES
        # No credential exists, so nothing can be "validated". The honest
        # canonical state is NO_CREDENTIAL_REQUIRED. Whether a given
        # workspace is actually a git work tree is decided per request at
        # execute() time and surfaces as an honest BLOCKED refusal.
        self.auth_state = "NO_CREDENTIAL_REQUIRED"
        # Declared here so agents route to their VCS research path without
        # naming this provider or any of its capabilities.
        self.research_profile = "vcs"

    def health(self) -> Dict[str, Any]:
        return {
            "status": self.auth_state,
            "capabilities": list(self.capabilities.keys()),
            "authentication": "NO_CREDENTIAL_REQUIRED",
            "credential_required": False,
            "transport": "local-subprocess",
            "availability": "per-request: workspace must be inside a git work tree",
        }

    def execute(self, operation: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        from runtime.tools import git_status as _git_status, git_diff as _git_diff

        capability = (operation or "").strip()
        if capability not in self.capabilities:
            raise ValueError(f"Unknown git capability: {capability}")
        if self.auth_state != "NO_CREDENTIAL_REQUIRED":
            raise RuntimeError(f"GitConnector unavailable: auth_state={self.auth_state}")

        start = _now()
        workspace = str(input_data.get("workspace", "") or input_data.get("target", "") or "").strip()
        if not workspace:
            raise ValueError("workspace is required for git inspection")
        agent_id = str(input_data.get("agent_id", "researcher"))

        if capability == "git.status":
            receipt, obs = _git_status(agent_id=agent_id, workspace_root=workspace)
        else:
            receipt, obs = _git_diff(
                agent_id=agent_id,
                workspace_root=workspace,
                target_path=input_data.get("path"),
                max_chars=int(input_data.get("max_chars", 10000)),
            )

        status = "SUCCESS" if getattr(receipt, "status", "") == "EXECUTED" else "BLOCKED"
        data: Dict[str, Any] = {}
        if obs is not None:
            data = dict(obs)
            data["workspace"] = workspace
        else:
            data = {"error": getattr(receipt, "reason", "blocked"), "workspace": workspace}

        duration = (_now() - start).total_seconds()
        result_hash = _hash(data) if data else None
        try:
            from runtime.capability_fabric import _digest as _inp_digest
        except ImportError:  # pragma: no cover - top-level import style
            from capability_fabric import _digest as _inp_digest
        try:
            input_digest = _inp_digest({"workspace": workspace, "capability": capability})
        except Exception:
            input_digest = None
        # Canonical receipt shape — identical keys to GitHubConnector._make_receipt
        # (see runtime.capability_fabric.CANONICAL_RECEIPT_FIELDS). No secrets.
        try:
            from datetime import timedelta as _td
            completed_at = (start + _td(seconds=float(duration or 0.0))).isoformat()
        except (ValueError, TypeError, OverflowError):
            completed_at = ""
        # Per-execution uniqueness (STEP 6): identical repeated inspections
        # yield independent receipts; result_hash still binds content.
        _start_compact = "".join(c for c in start.isoformat() if c.isalnum())[:20]
        receipt_dict = {
            "receipt_id": f"receipt-git-{capability}-{_hash(workspace + capability)[:12]}-{_start_compact}",
            "connector_id": self.connector_id,
            "provider": self.provider,
            "operation": capability,
            "capability": capability,
            "timestamp": start.isoformat(),
            "started_at": start.isoformat(),
            "completed_at": completed_at,
            "target": workspace,
            "status": status,
            "success": status == "SUCCESS",
            "duration_seconds": duration,
            "result_hash": result_hash,
            "input_digest": input_digest,
            "authentication": "NO_CREDENTIAL_REQUIRED",
            "error": None if status == "SUCCESS" else data.get("error", "blocked"),
        }
        return {
            "status": status,
            "data": data,
            "receipt": receipt_dict,
            "authentication": "NO_CREDENTIAL_REQUIRED",
        }

    def revoke(self) -> None:
        self.auth_state = "REVOKED"

    def refresh(self) -> Dict[str, Any]:
        if self.auth_state == "REVOKED":
            return {"auth_state": "REVOKED"}
        self.auth_state = "NO_CREDENTIAL_REQUIRED"
        return {"auth_state": "NO_CREDENTIAL_REQUIRED", "auth_validated": None,
                "note": "no credential exists to validate"}

    def metadata(self) -> Dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "provider": self.provider,
            "version": self.version,
            "capabilities": list(self.capabilities.keys()),
            "auth_state": self.auth_state,
        }
