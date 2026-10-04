"""NEXUS Filesystem Connector — bounded local filesystem reads behind the
generic capability fabric.

Why this connector exists
-------------------------
``runtime/agents/researcher.py`` used to walk the workspace itself
(``Path.rglob`` + ``read_bytes``) and then mint connector-shaped tool records
with hand-written ``receipt-filesystem-<hash>`` ids. That was a truth-boundary
violation: the receipts looked canonical but were produced by neither canonical
path, the evidence entries claimed ``source="BoundedAgentRuntime"`` while
BoundedAgentRuntime was never used, and the traversal skipped the bounded
runtime's containment check (no symlink resolution, no in-root verification).

This module removes that second path. Local filesystem reading is a PROVIDER,
so it implements the same canonical Connector contract as GitHub and git:

    CapabilityRequest -> ConnectorRegistry.resolve -> FilesystemConnector.execute
                       -> CapabilityResponse(OBSERVED) -> canonical receipt
                       -> tool record -> artifact -> verifier

Execution delegates to :mod:`runtime.tools`, which is backed by
``BoundedAgentRuntime`` — so every path is containment-checked against an
explicit workspace root and every read carries a real ObservationReceipt.

Auth model
----------
NO credential is involved: auth_state is ``NO_CREDENTIAL_REQUIRED``. Whether a
given workspace is readable is a PER-REQUEST property; an unusable path or an
out-of-root target is an honest BLOCKED refusal (reality OBSERVED — the refusal
is the fact), never a fabricated observation and never AUTH_REQUIRED.

This module contains no GitHub logic and no researcher logic: it is one
provider behind a generic interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
import hashlib
import json

try:
    from runtime.connector_registry import Connector
except ImportError:  # pragma: no cover - top-level import style
    from connector_registry import Connector


FILESYSTEM_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "filesystem.read": {
        "description": "Bounded read of one file inside an explicit workspace root",
        "risk": "LOW",
        "verification": "OBSERVED",
        # An explicit existing workspace directory.
        "scope_kind": "absolute_path",
    },
    "filesystem.list": {
        "description": "Bounded listing of files inside an explicit workspace root",
        "risk": "LOW",
        "verification": "OBSERVED",
        "scope_kind": "absolute_path",
    },
}

# Bound the listing so a large tree cannot turn one capability call into an
# unbounded scan. These are hard execution limits, not preferences.
MAX_LISTED_FILES = 200
MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv",
                       ".pytest_cache", ".nexus_product"})


def _now():
    return datetime.now(timezone.utc)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _canonical_digest(value: Any) -> str:
    """Digest exactly as runtime.capability_fabric._digest, without a hard
    dependency on the fabric (avoids an import cycle at module load)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class FilesystemConnector(Connector):
    """Local filesystem reads behind the generic Connector contract."""

    # Declared without arguments and fully populated in __post_init__, but the
    # base dataclass has no defaults for these fields, so instantiating this
    # connector directly raised TypeError. Defaults make the connector
    # constructible on its own, which the architecture tests rely on.
    connector_id: str = "filesystem"
    provider: str = "filesystem"
    version: str = "1.0.0"
    capabilities: Dict[str, Dict[str, Any]] = field(default_factory=lambda: dict(FILESYSTEM_CAPABILITIES))
    auth_state: str = "NO_CREDENTIAL_REQUIRED"

    def __post_init__(self):
        self.connector_id = "filesystem"
        self.provider = "filesystem"
        self.version = "1.0.0"
        self.capabilities = FILESYSTEM_CAPABILITIES
        # No credential exists to validate — see module docstring.
        self.auth_state = "NO_CREDENTIAL_REQUIRED"
        # Declared here so agents route to their filesystem research path
        # without naming this provider or any of its capabilities.
        self.research_profile = "filesystem"

    # ------------------------------------------------------------------
    # Contract
    # ------------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return {
            "status": self.auth_state,
            "capabilities": list(self.capabilities.keys()),
            "authentication": "NO_CREDENTIAL_REQUIRED",
            "credential_required": False,
            "transport": "local-filesystem",
            "availability": "per-request: an explicit existing workspace root is required",
            "limits": {
                "max_listed_files": MAX_LISTED_FILES,
                "max_file_bytes": MAX_FILE_BYTES,
            },
        }

    def execute(self, operation: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        from runtime.tools import filesystem_read, resolve_workspace_scope

        capability = (operation or "").strip()
        if capability not in self.capabilities:
            raise ValueError(f"Unknown filesystem capability: {capability}")
        if self.auth_state != "NO_CREDENTIAL_REQUIRED":
            raise RuntimeError(f"FilesystemConnector unavailable: auth_state={self.auth_state}")

        start = _now()
        agent_id = str(input_data.get("agent_id", "") or "unknown")
        workspace = str(input_data.get("workspace", "") or input_data.get("target", "") or "").strip()
        if not workspace:
            raise ValueError("workspace is required for filesystem read")

        # Reject a non-legitimate scope BEFORE touching the filesystem. A
        # workflow/task identity is an identity, never a path.
        root = resolve_workspace_scope(workspace, None)
        if root is None:
            return self._refusal(
                capability, workspace, start,
                error=f"workspace scope {workspace!r} is not an explicit existing directory",
                result_hash=None,
            )

        if capability == "filesystem.read":
            target = str(input_data.get("path", "") or "").strip()
            if not target:
                return self._refusal(
                    capability, root, start,
                    error="path is required for filesystem.read",
                    result_hash=None,
                )
            receipt, observation = filesystem_read(
                agent_id=agent_id,
                workspace_root=root,
                target_path=target,
                max_chars=int(input_data.get("max_chars", 2000) or 2000),
            )
            if observation is None:
                return self._refusal(
                    capability, root, start,
                    error=str(getattr(receipt, "reason", "") or "filesystem.read was not executed"),
                    result_hash=None,
                    auth_reason=str(getattr(receipt, "reason", "")),
                )
            data = {
                "path": observation.get("path"),
                "size": observation.get("size"),
                "sha256": observation.get("sha256"),
                "content_preview": observation.get("text", "")[:2000],
                "workspace": root,
                "reality": "OBSERVED",
                "untrusted_content": True,
            }
            return self._success(capability, root, start, data, agent_id)

        # filesystem.list
        listing = self._list(root)
        data = {"workspace": root, "files": listing, "file_count": len(listing),
                "truncated": len(listing) >= MAX_LISTED_FILES, "reality": "OBSERVED"}
        return self._success(capability, root, start, data, agent_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _list(self, root: str) -> List[Dict[str, Any]]:
        """Bounded listing. Every candidate is containment-checked against the
        resolved root (symlinks resolved first), so a link cannot escape."""
        resolved_root = Path(root).resolve()
        out: List[Dict[str, Any]] = []
        try:
            entries = sorted(resolved_root.rglob("*"))
        except OSError:
            return out
        for entry in entries:
            if len(out) >= MAX_LISTED_FILES:
                break
            try:
                resolved = entry.resolve()
                if resolved != resolved_root and resolved_root not in resolved.parents:
                    continue  # symlink escape
                if any(part in SKIP_DIRS for part in resolved.parts):
                    continue
                if not resolved.is_file():
                    continue
                size = resolved.stat().st_size
            except (OSError, ValueError):
                continue
            if size > MAX_FILE_BYTES:
                continue
            out.append({
                "path": str(resolved),
                "size": size,
                "suffix": resolved.suffix.lower() or "<noext>",
            })
        return out

    def _success(self, capability: str, target: str, start: datetime,
                 data: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
        duration = (_now() - start).total_seconds()
        return {
            "status": "SUCCESS",
            "data": data,
            "receipt": self._receipt(capability, target, start, duration,
                                     result_hash=_hash(data), error=None),
            "authentication": "NO_CREDENTIAL_REQUIRED",
        }

    def _refusal(self, capability: str, target: str, start: datetime, *,
                 error: str, result_hash: str | None,
                 auth_reason: str = "") -> Dict[str, Any]:
        """An honest refusal: BLOCKED, which the fabric maps to reality
        OBSERVED (the refusal itself is the observed fact). Never SUCCESS."""
        duration = (_now() - start).total_seconds()
        return {
            "status": "BLOCKED",
            "data": {"error": error, "refused": True, "workspace": target},
            "receipt": self._receipt(capability, target, start, duration,
                                     result_hash=result_hash, error=error),
            "authentication": "NO_CREDENTIAL_REQUIRED",
        }

    def _receipt(self, operation: str, target: str, start: datetime,
                 duration: float, *, result_hash: str | None,
                 error: str | None) -> Dict[str, Any]:
        """Canonical receipt shape (runtime.capability_fabric
        .CANONICAL_RECEIPT_FIELDS). Never contains secrets."""
        try:
            completed_at = (start + timedelta(seconds=float(duration or 0.0))).isoformat()
        except (ValueError, TypeError, OverflowError):
            completed_at = ""
        # Per-execution uniqueness; result_hash still binds content.
        start_compact = "".join(c for c in start.isoformat() if c.isalnum())[:20]
        return {
            "receipt_id": f"receipt-filesystem-{operation}-{_hash(target + operation)[:12]}-{start_compact}",
            "connector_id": self.connector_id,
            "provider": self.provider,
            "operation": operation,
            "capability": operation,
            "timestamp": start.isoformat(),
            "started_at": start.isoformat(),
            "completed_at": completed_at,
            "target": target,
            "status": "SUCCESS" if error is None else "BLOCKED",
            "success": error is None,
            "duration_seconds": duration,
            "result_hash": result_hash,
            "input_digest": _canonical_digest({"operation": operation, "target": target}),
            "authentication": "NO_CREDENTIAL_REQUIRED",
            "error": error,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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
            "limits": {"max_listed_files": MAX_LISTED_FILES, "max_file_bytes": MAX_FILE_BYTES},
        }