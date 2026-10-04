"""NEXUS Connector Fabric — generic connector abstraction for external API integration.

This module provides a generic Connector abstraction that can wrap any
external API provider (GitHub, GitLab, etc.) while preserving the
existing evidence/provenance/verification model.

Do NOT integrate Loophole into this runtime boundary.
Loophole remains a completely separate external project.
"""


from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import hashlib
import json
import logging

logger = logging.getLogger("nexus.github_provider")

import os

try:
    from canonical_pilot import DirectGitHubAPIAdapter, analyze_repository, verify_recommendation
    from canonical_core import core_id, RepositoryObservation
    from persistent_fabric import CapabilityProvider, CapabilityRequest, CapabilityResponse, ExecutionReceipt
except ImportError:
    from .canonical_pilot import DirectGitHubAPIAdapter, analyze_repository, verify_recommendation
    from .canonical_core import core_id, RepositoryObservation
    from .persistent_fabric import CapabilityProvider, CapabilityRequest, CapabilityResponse, ExecutionReceipt

# Canonical generic connector contract + registry. BOTH live in
# connector_registry.py — they are architecture, not GitHub. These aliases
# preserve `github_provider.Connector` / `github_provider.ConnectorRegistry`
# imports so no consumer breaks; the canonical definitions are the only ones.
try:
    from runtime.connector_registry import Connector, ConnectorRegistry
except ImportError:  # pragma: no cover - top-level `github_provider` import style
    from connector_registry import Connector, ConnectorRegistry


# Canonical GitHubConnector registration
_github_connector: GitHubConnector | None = None
_github_registry: ConnectorRegistry | None = None


def initialize_github_connector_registration() -> tuple[GitHubConnector | None, ConnectorRegistry | None]:
    """Canonical one-time registration of GitHubConnector with ConnectorRegistry.

    Creates the GitHubConnector (which resolves NEXUS_GITHUB_TOKEN from the environment)
    and registers it with a new ConnectorRegistry.  If the token is absent the connector
    remains unavailable (auth_state=NOT_CONFIGURED) and is still registered so that discovery
    honestly reports NOT_CONFIGURED rather than being missing entirely.

    Returns (connector, registry).  Either may be None if registration could not complete.
    """
    global _github_connector, _github_registry
    if _github_connector is not None:
        # Refresh token from environment if the cached connector is not
        # connected but a token is now available (e.g. first import happened
        # before NEXUS_GITHUB_TOKEN was exported). Never prints the token.
        try:
            import os as _os
            _env_token = _os.getenv("NEXUS_GITHUB_TOKEN")
            if _github_connector.auth_state not in ("CONNECTED", "CONFIGURED") and _env_token and _env_token.strip():
                _github_connector.token = _env_token
                # A newly seen token string is CONFIGURED, not CONNECTED:
                # presence is not proof of validity (see GitHubConnector).
                _github_connector.auth_state = "CONFIGURED"
        except Exception as exc:
            # Explicit: a failed refresh must not silently keep a stale state.
            logger.warning("github connector token refresh failed: %s: %s", type(exc).__name__, exc)
        return _github_connector, _github_registry

    from runtime.github_provider import GitHubConnector, ConnectorRegistry, GITHUB_CAPABILITIES

    registry = ConnectorRegistry()
    connector = GitHubConnector(
        token=None,
        connector_id="github",
        provider="github",
        version="1.0.0",
        capabilities=GITHUB_CAPABILITIES,
    )
    registry.register(connector)
    _github_connector = connector
    _github_registry = registry
    return connector, registry


# =============================================================================
# Connector abstraction + ConnectorRegistry — canonical definitions both
# live in runtime/connector_registry.py. Both are the GENERIC contract, not
# GitHub's, so no provider module may own them. They are re-exported here so
# existing imports keep working:
#     from runtime.github_provider import Connector, ConnectorRegistry  # OK
# New providers MUST import them from runtime.connector_registry directly.
# =============================================================================


def now():
    return datetime.now(timezone.utc)


# =============================================================================
# Capability definitions for GitHub connector
# =============================================================================


GITHUB_CAPABILITIES = {
    "github.repository.read": {
        "description": "Read repository metadata and content",
        "risk": "LOW",
        "verification": "OBSERVED",
        # Generic scope kind (see runtime.capability_fabric.SCOPE_KINDS): a
        # two-segment repository reference. The planner uses this to pick a
        # capability from a scope without naming any provider.
        "scope_kind": "owner_repo",
    },
    "github.repository.list": {
        "description": "List repositories in an organization or user",
        "risk": "LOW",
        "verification": "OBSERVED",
        "scope_kind": "any",
    },
    "github.repository.commits.read": {
        "description": "Read recent commits from a repository",
        "risk": "LOW",
        "verification": "OBSERVED",
        "scope_kind": "owner_repo",
    },
    "github.repository.issues.read": {
        "description": "Read issues from a repository",
        "risk": "LOW",
        "verification": "OBSERVED",
        "scope_kind": "owner_repo",
    },
}

READ_OPERATIONS = {"repository.read", "repository.metadata.read", "repository.branch.read", "repository.commits.read", "repository.tree.read", "repository.readme.read", "repository.issues.read", "repository.pull_requests.read", "repository.health.read"}
WRITE_OPERATIONS = {"repository.write", "repository.delete", "repository.deploy", "repository.merge", "repository.pull_request.create", "repository.settings.modify"}




def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class GitHubReadProvider(CapabilityProvider):
    """LEGACY / benchmark-only GitHub read provider (MissionComposer era).

    Canonical production path: ``GitHubConnector`` (below) executed through
    ``runtime.capability_fabric.execute_capability`` with a
    ``runtime.capability_fabric.CapabilityRequest``. No production agent or
    workflow routes through this class.

    Retained because MissionComposer, omega9_bridge, nexus_independent
    service fallbacks, and several benchmark scripts instantiate it. It
    speaks the legacy ``persistent_fabric.CapabilityRequest`` contract, not
    the canonical ``capability_fabric.CapabilityRequest`` contract.
    """

    name = "github-read"
    provider_health = "AVAILABLE"

    def __init__(self, adapter: DirectGitHubAPIAdapter | None = None):
        self.adapter = adapter or DirectGitHubAPIAdapter()
        self.receipts: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        return {
            "status": self.provider_health,
            "availability": True,
            "operations": sorted(READ_OPERATIONS),
            "write_operations": [],
            "transport": "direct-github-rest",
            "authentication": "PRODUCT_MANAGED_TOKEN" if bool(self.adapter.token) else "PUBLIC_READ_ONLY",
            "limitations": ["only bounded read endpoints are exposed", "authentication token is process-local and never returned"],
        }

    def discover(self, request: CapabilityRequest | None = None) -> dict[str, Any]:
        return {"provider": self.name, "health": self.provider_health, "operations": sorted(READ_OPERATIONS), "write_operations": [], "provenance": "direct GitHub REST adapter", "authentication": "PRODUCT_MANAGED_TOKEN" if bool(self.adapter.token) else "PUBLIC_READ_ONLY"}

    def validate(self, request: CapabilityRequest) -> dict[str, Any]:
        errors = []
        if request.capability not in {"github-read", "repository-read"}:
            errors.append("unsupported capability")
        if request.operation not in READ_OPERATIONS:
            errors.append("operation is not read-only or not supported")
        if request.scope.count("/") != 1:
            errors.append("scope must be owner/repository")
        if request.execution_mode not in {"REAL_READ", "SIMULATION", "DRY_RUN"}:
            errors.append("invalid execution mode")
        if request.authorization not in {"CONFIRMED_READ_ONLY", "READ_ONLY_AUTHORIZED"} and request.execution_mode == "REAL_READ":
            errors.append("read authorization evidence required")
        if request.governance not in {"READ_ONLY", "PREPARE_ONLY", "CONFIRM_READ_ONLY"}:
            errors.append("governance does not permit this operation")
        return {"valid": not errors, "errors": errors, "provider": self.name, "operation": request.operation}

    def prepare(self, request: CapabilityRequest) -> dict[str, Any]:
        check = self.validate(request)
        return {"status": "PREPARED" if check["valid"] else "BLOCKED", "validation": check, "side_effects": False}

    def _receipt(self, request: CapabilityRequest, response: CapabilityResponse, start: str, observation: RepositoryObservation | None = None) -> dict[str, Any]:
        end = now()
        raw = observation.raw if observation is not None else {}
        receipt = ExecutionReceipt(core_id("execution"), request.request_id, self.name, request.operation, start, end, response.status, False, response.outputs, response.observations, response.verification, request.authorization, ["github-read-provider", "direct-github-rest"])
        data = asdict(receipt)
        data.update({"capability": request.capability, "scope": request.scope, "inputs_hash": content_hash(request.inputs), "output_reference": content_hash(raw) if raw else None, "reality": response.reality, "failure_state": None if response.status in {"SUCCESS", "EXECUTED"} else response.reason})
        self.receipts.append(data)
        return data

    def execute(self, request: CapabilityRequest) -> dict[str, Any]:
        check = self.validate(request)
        start = now()
        if not check["valid"]:
            response = CapabilityResponse(request.request_id, "BLOCKED", "UNKNOWN", {}, [], "UNKNOWN", self.name, "; ".join(check["errors"]))
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        if request.execution_mode in {"SIMULATION", "DRY_RUN"}:
            response = CapabilityResponse(request.request_id, "EXECUTED", "SIMULATED", {"repository": request.scope}, [{"source": "github-read-simulation", "reality": "SIMULATED"}], "UNVERIFIED", self.name, "simulation mode; no GitHub access")
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        observation = self.adapter.observe(request.scope)
        response = CapabilityResponse(request.request_id, "EXECUTED" if observation.status in {"SUCCESS", "PARTIAL"} else "FAILED", "OBSERVED" if observation.status in {"SUCCESS", "PARTIAL"} else "UNKNOWN", {"observation": asdict(observation)}, [{"source": "direct-github-rest", "reality": "OBSERVED", "scope": request.scope, "observed_at": now()}], "UNVERIFIED", self.name, "real read-only GitHub observation")
        return {"response": asdict(response), "receipt": self._receipt(request, response, start, observation), "observation": asdict(observation)}

    def observe(self, request: CapabilityRequest) -> dict[str, Any]:
        return self.execute(request)

    def verify(self, request: CapabilityRequest, bundle: dict[str, Any]) -> dict[str, Any]:
        observation = bundle.get("observation")
        if not observation:
            return {"status": "UNKNOWN", "verification_state": "UNVERIFIED", "independent": True, "reason": "no observation"}
        result = analyze_repository(RepositoryObservation(**observation))
        verification = verify_recommendation(result, RepositoryObservation(**observation))
        return {"analysis": result, "verification": asdict(verification), "evidence_chain": result.get("evidence_chain", []), "reality": "INFERRED"}

    def invoke_metadata(self, request: CapabilityRequest) -> dict[str, Any]:
        check = self.validate(request)
        start = now()
        if not check["valid"]:
            response = CapabilityResponse(request.request_id, "BLOCKED", "UNKNOWN", {}, [], "UNKNOWN", self.name, "; ".join(check["errors"]))
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        if request.execution_mode in {"SIMULATION", "DRY_RUN"}:
            response = CapabilityResponse(request.request_id, "EXECUTED", "SIMULATED", {"repository": request.scope}, [{"source": "github-read-metadata-simulation", "reality": "SIMULATED"}], "UNVERIFIED", self.name, "simulation mode; no GitHub access")
            return {"response": asdict(response), "receipt": self._receipt(request, response, start)}
        observation = self.adapter.observe_metadata(request.scope)
        response = CapabilityResponse(request.request_id, "EXECUTED" if observation.status == "SUCCESS" else "FAILED", "OBSERVED" if observation.status == "SUCCESS" else "UNKNOWN", {"observation": asdict(observation)}, [{"source": "direct-github-rest-metadata", "reality": "OBSERVED", "scope": request.scope, "observed_at": now()}], "UNVERIFIED", self.name, "real metadata-only read")
        bundle = {"response": asdict(response), "receipt": self._receipt(request, response, start, observation), "observation": asdict(observation)}
        if observation.status == "SUCCESS":
            bundle["verification"] = self.verify_metadata(request, bundle)
            bundle["freshness"] = {"observed_at": now(), "source": "GitHub REST API metadata endpoint", "scope": request.scope, "content_hash": content_hash(bundle["observation"].get("raw", {})), "state": "CURRENT"}
        return bundle

    def verify_metadata(self, request: CapabilityRequest, bundle: dict[str, Any]) -> dict[str, Any]:
        observation = bundle.get("observation") or {}
        metadata = (observation.get("raw") or {}).get("metadata")
        verified = bool(metadata) and observation.get("status") == "SUCCESS" and observation.get("reality") == "OBSERVED"
        return {"status": "VERIFIED" if verified else "UNKNOWN", "verification_state": "VERIFIED" if verified else "UNVERIFIED", "independent": True, "authority": "RepositoryObservation metadata integrity", "method": "metadata schema and scope comparison", "depth": "METADATA_ONLY", "what_it_proves": ["repository identity and metadata endpoint response"] if verified else [], "what_it_does_not_prove": ["branch health", "recent commits", "tree/test depth", "README or issue state", "deep repository health"]}

    def invoke_health(self, request: CapabilityRequest) -> dict[str, Any]:
        bundle = self.execute(request)
        if "observation" not in bundle:
            return bundle
        bundle["verification"] = self.verify(request, bundle)
        bundle["freshness"] = {"observed_at": bundle["observation"].get("raw", {}).get("metadata", {}).get("updated_at") or now(), "source": "GitHub REST API", "scope": request.scope, "content_hash": content_hash(bundle["observation"].get("raw", {})), "state": "CURRENT"}
        bundle["capability_status"] = {operation: "REAL_READ_VERIFIED" for operation in READ_OPERATIONS}
        bundle["capability_status"].update({"repository.write": "UNAVAILABLE", "repository.delete": "UNAVAILABLE", "repository.deploy": "UNAVAILABLE"})
        return bundle


def detect_change(previous: dict | None, current: dict) -> dict[str, Any]:
    if not previous:
        return {"status": "NEW", "changed_fields": list(current.keys()), "evidence": "current observation only"}
    fields = ("repository", "status", "scope", "raw")
    changed = [field for field in fields if previous.get(field) != current.get(field)]
    return {"status": "CHANGED" if changed else "UNCHANGED", "changed_fields": changed, "evidence": "typed observation comparison; no difference invented"}


# =============================================================================
# GitHub Connector Implementation
# =============================================================================


import time

import httpx

# NOTE: GITHUB_CAPABILITIES / READ_OPERATIONS / WRITE_OPERATIONS / now /
# content_hash are defined in THIS module (above) — no self-import needed.
# (A previous revision imported them from runtime.github_provider, i.e. from
# itself; removed as part of the canonical-registry pass.)
# DirectGitHubAPIAdapter and ExecutionReceipt are already imported at the top
# of this module (both `runtime.*` and top-level styles handled there).


GITHUB_API_BASE = "https://api.github.com"


@dataclass
class GitHubConnector(Connector):
    """Concrete connector that implements the generic Connector contract
    for the GitHub REST API.

    Uses a Personal Access Token supplied via the
    ``NEXUS_GITHUB_TOKEN`` environment variable.

    Authentication semantics (truthful — a credential string is never
    equated with a verified connection):

      - ``NOT_CONFIGURED`` — no credential present.
      - ``CONFIGURED``     — a non-blank credential string is present but has
        NOT yet been validated against GitHub. Selectable for execution (the
        live call itself is the validation); failure surfaces as FAILED.
      - ``CONNECTED``      — the credential was validated by a live check
        (``health()`` 200, ``validate_auth()``, or a successful ``execute()``).
      - ``EXPIRED`` / ``ERROR`` / ``REVOKED`` — validated negatively or
        withdrawn; never selectable until reconfigured/revalidated.
      - ``auth_validated`` is True ONLY after live validation, and
        ``auth_detail()`` exposes the full picture (labels only).
      - The token itself is NEVER printed, logged, persisted in receipts,
        artifacts, tool records, or error messages.
    """

    token: str | None = None
    # True only after a live API check accepted the credential. Never set
    # from the mere presence of a token string.
    auth_validated: bool = False
    last_validated_at: str | None = None

    def __post_init__(self):
        self.connector_id = "github"
        self.provider = "github"
        self.version = "1.0.0"
        self.capabilities = GITHUB_CAPABILITIES
        # Resolve token from env if not provided
        if self.token is None:
            self.token = os.getenv("NEXUS_GITHUB_TOKEN")
        # Presence of a token string is CONFIGURED — never CONNECTED.
        # CONNECTED is earned exclusively by live validation (health 200,
        # validate_auth, or a successful execute).
        self.auth_state = (
            "CONFIGURED"
            if self.token is not None and self.token.strip()
            else "NOT_CONFIGURED"
        )
        # A freshly configured credential has NOT been validated yet.
        self.auth_validated = False
        self.last_validated_at = None
        # Declared here so agents route to their repository research path
        # without naming this provider or any of its capabilities.
        self.research_profile = "repository"

    def _auth_headers(self) -> Dict[str, str]:
        """Build redacted-safe GitHub API headers (never logs the token)."""
        headers: Dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "NEXUS-Independent/0.2",
        }
        if self.token and self.token.strip():
            # Bearer works for OAuth (gho_) and fine-grained PATs; classic
            # PATs also accept Bearer. Never log this header.
            headers["Authorization"] = f"Bearer {self.token.strip()}"
        return headers

    def health(self) -> Dict[str, Any]:
        """Return connector health status.

        If no usable credential is configured, health reports the honest
        state (NOT_CONFIGURED / EXPIRED / REVOKED / ERROR).
        If a credential is configured but unvalidated, health performs the
        live check: success promotes the connector to CONNECTED.
        """
        if self.auth_state not in ("CONNECTED", "CONFIGURED"):
            return {
                "status": self.auth_state,
                "capabilities": list(self.capabilities.keys()),
                "authentication": "TOKEN_ABSENT" if self.auth_state == "NOT_CONFIGURED" else "INACTIVE",
            }
        try:
            client = httpx.Client(timeout=10.0)
            response = client.get(
                f"{GITHUB_API_BASE}/repos/Themeta-verse/Nexus",
                headers=self._auth_headers(),
            )
            if response.status_code == 200:
                data = response.json()
                # Live 200 with this credential: record validation evidence
                # AND promote to CONNECTED (validation earned, not assumed).
                self.auth_state = "CONNECTED"
                self.auth_validated = True
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    self.last_validated_at = _dt.now(_tz.utc).isoformat()
                except Exception:
                    pass
                return {
                    "status": "CONNECTED",
                    "capabilities": list(self.capabilities.keys()),
                    "authentication": "TOKEN_ACTIVE",
                    "auth_validated": True,
                    "rate_limit_remaining": int(
                        response.headers.get("X-RateLimit-Remaining", "0")
                    ),
                }
            elif response.status_code == 401:
                return {
                    "status": "ERROR",
                    "capabilities": list(self.capabilities.keys()),
                    "authentication": "INVALID_TOKEN",
                    "reason": "GitHub API returned 401 Unauthorized",
                }
            else:
                return {
                    "status": "ERROR",
                    "capabilities": list(self.capabilities.keys()),
                    "authentication": "API_ERROR",
                    "reason": f"GitHub API returned {response.status_code}",
                }
        except Exception as e:
            return {
                "status": "ERROR",
                "capabilities": list(self.capabilities.keys()),
                "authentication": "CONNECTION_FAILED",
                "reason": f"Failed to reach GitHub API: {type(e).__name__}: {e}",
            }

    def execute(self, operation: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a GitHub API operation.

        Maps a capability name to the corresponding REST endpoint.

        Supported operations:
            - github.repository.read        -> GET /repos/{owner}/{repo}
            - github.repository.list        -> GET /user/repos (or /orgs/{org}/repos)
            - github.repository.commits.read -> GET /repos/{owner}/{repo}/commits
            - github.repository.issues.read -> GET /repos/{owner}/{repo}/issues

        Selectable in CONFIGURED or CONNECTED state; a CONFIGURED call that
        succeeds promotes the connector to CONNECTED, one rejected with 401
        demotes it to ERROR. Either way the outcome is honest.
        """
        if self.auth_state not in ("CONNECTED", "CONFIGURED"):
            raise RuntimeError(
                f"GitHubConnector not connected: auth_state={self.auth_state}"
            )

        capability = operation.strip()
        if capability not in self.capabilities:
            raise ValueError(f"Unknown GitHub capability: {capability}")

        start_dt = now()
        # Accept both "owner_repo" (canonical) and "scope" (generic) for
        # provider-neutral invocation. The generic fabric passes "scope".
        owner_repo = (input_data.get("owner_repo") or input_data.get("scope") or "").strip()
        if capability == "github.repository.read" and not owner_repo:
            raise ValueError("owner_repo (owner/repo) is required for github.repository.read")

        headers = self._auth_headers()
        client = httpx.Client(timeout=30.0)

        try:
            if capability == "github.repository.read":
                result = self._repo_read(client, headers, owner_repo)
            elif capability == "github.repository.list":
                result = self._repo_list(client, headers, input_data.get("filter", "owner"), input_data)
            elif capability == "github.repository.commits.read":
                result = self._commits_read(client, headers, owner_repo, input_data.get("per_page", 10))
            elif capability == "github.repository.issues.read":
                result = self._issues_read(client, headers, owner_repo, input_data.get("state", "open"))
            else:
                raise ValueError(f"Unsupported operation: {capability}")

            duration = (now() - start_dt).total_seconds()

            # Build receipt (NO secrets!)
            receipt = self._make_receipt(
                operation,
                result,
                start_dt.isoformat(),
                duration,
                capability,
                input_data,
                owner_repo,
            )

            # A live 200 with this credential IS validation evidence:
            # promote to CONNECTED (earned, not assumed).
            self.auth_state = "CONNECTED"
            self.auth_validated = True
            try:
                from datetime import datetime as _dt, timezone as _tz
                self.last_validated_at = _dt.now(_tz.utc).isoformat()
            except Exception:
                pass

            # Return the data without exposing the token
            return {
                "status": "SUCCESS",
                "data": result,
                "receipt": receipt,
                "authentication": "TOKEN_ACTIVE",
            }

        except httpx.HTTPStatusError as e:
            duration = (now() - start_dt).total_seconds()
            if e.response.status_code == 401:
                # The credential was actively rejected: stop claiming CONNECTED.
                self.auth_state = "ERROR"
                self.auth_validated = False
            receipt = self._make_receipt(
                operation,
                {"error": e.response.text},
                start_dt.isoformat(),
                duration,
                capability,
                input_data,
                status=e.response.status_code,
            )
            return {
                "status": "FAILED",
                "data": {"error": e.response.text},
                "receipt": receipt,
                "authentication": "TOKEN_ACTIVE",
            }
        except httpx.ConnectError as e:
            duration = (now() - start_dt).total_seconds()
            receipt = self._make_receipt(
                operation,
                {"error": f"Connection failed: {e}"},
                start_dt.isoformat(),
                duration,
                capability,
                input_data,
                status=None,
            )
            return {
                "status": "FAILED",
                "data": {"error": f"Connection failed: {e}"},
                "receipt": receipt,
                "authentication": "TOKEN_ACTIVE",
            }
        except Exception as e:
            duration = (now() - start_dt).total_seconds()
            receipt = self._make_receipt(
                operation,
                {"error": str(e)},
                start_dt.isoformat(),
                duration,
                capability,
                input_data,
                status=None,
            )
            return {
                "status": "FAILED",
                "data": {"error": str(e)},
                "receipt": receipt,
                "authentication": "TOKEN_ACTIVE",
            }

    # --- Individual API operations ---

    def _repo_read(self, client: httpx.Client, headers: dict, owner_repo: str) -> dict:
        """GET /repos/{owner}/{repo} ' repository metadata."""
        response = client.get(f"{GITHUB_API_BASE}/repos/{owner_repo}", headers=headers)
        response.raise_for_status()
        return response.json()

    def _repo_list(
        self, client: httpx.Client, headers: dict, repo_filter: str = "owner", input_data: Dict[str, Any] | None = None
    ) -> dict:
        """GET /user/repos or /orgs/{org}/repos ' list repositories."""
        input_data = input_data or {}
        if repo_filter == "owner":
            response = client.get(f"{GITHUB_API_BASE}/user/repos", headers=headers)
        else:
            org = input_data.get("org", "")
            if not org:
                raise ValueError("Organization name required for org-scoped repo list")
            response = client.get(f"{GITHUB_API_BASE}/orgs/{org}/repos", headers=headers)
        response.raise_for_status()
        return response.json()

    def _commits_read(
        self,
        client: httpx.Client,
        headers: dict,
        owner_repo: str,
        per_page: int,
    ) -> dict:
        """GET /repos/{owner}/{repo}/commits ' recent commits."""
        response = client.get(
            f"{GITHUB_API_BASE}/repos/{owner_repo}/commits",
            params={"per_page": per_page},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    def _issues_read(
        self, client: httpx.Client, headers: dict, owner_repo: str = "", state: str = "open"
    ) -> dict:
        """GET /repos/{owner}/{repo}/issues ' issues list."""
        repo = (owner_repo or "").strip()
        if not repo:
            raise ValueError("owner_repo (owner/repo) is required for issues read")
        response = client.get(
            f"{GITHUB_API_BASE}/repos/{repo}/issues",
            params={"state": state},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    def revoke(self) -> None:
        """Revoke this connector's connection/credentials.

        Clears the token and resets auth_state to NOT_CONFIGURED.
        """
        if self.token:
            # Secure zeroisation ' overwrite the string object
            self.token = None  # type: ignore
        self.auth_state = "REVOKED"
        self.auth_validated = False

    def auth_detail(self) -> Dict[str, Any]:
        """Truthful authentication picture (no secrets, labels only)."""
        return {
            "connector_id": self.connector_id,
            "auth_state": self.auth_state,
            "credential_configured": bool(self.token and self.token.strip()),
            "auth_validated": self.auth_validated,
            "last_validated_at": self.last_validated_at,
            "note": (
                "credential configured but NOT yet validated against GitHub"
                if self.auth_state == "CONFIGURED"
                else ("credential validated against GitHub" if self.auth_validated else "no usable credential")
            ),
        }

    def validate_auth(self) -> bool:
        """Validate the configured credential against the live GitHub API.

        Returns True only on an authenticated 200 response, and records
        ``auth_validated``/``last_validated_at``. Returns False (without
        raising, without touching ``auth_state`` semantics beyond ERROR
        mapping) when validation cannot succeed. Never exposes the token.
        """
        if self.token is None or not self.token.strip():
            self.auth_state = "NOT_CONFIGURED"
            self.auth_validated = False
            return False
        try:
            client = httpx.Client(timeout=10.0)
            response = client.get(
                f"{GITHUB_API_BASE}/user",
                headers=self._auth_headers(),
            )
        except Exception as exc:
            logger.warning("github auth validation unreachable: %s: %s", type(exc).__name__, exc)
            self.auth_validated = False
            return False
        if response.status_code == 200:
            self.auth_state = "CONNECTED"
            self.auth_validated = True
            try:
                from datetime import datetime as _dt, timezone as _tz
                self.last_validated_at = _dt.now(_tz.utc).isoformat()
            except Exception:
                self.last_validated_at = None
            return True
        if response.status_code == 401:
            self.auth_state = "ERROR"
        self.auth_validated = False
        return False

    def refresh(self) -> Dict[str, Any]:
        """Refresh authentication state.

        Re-validates the token against the GitHub API (same Bearer scheme
        as live execution — never the legacy `token` scheme, never logged).

        Returns:
            dict with new auth_state and any relevant metadata.
        """
        if self.token is None or not self.token.strip():
            self.auth_state = "NOT_CONFIGURED"
            self.auth_validated = False
            return {"auth_state": "NOT_CONFIGURED"}
        if self.validate_auth():
            return {"auth_state": "CONNECTED", "scope": "full_repo", "auth_validated": True}
        if self.auth_state == "ERROR":
            return {"auth_state": "ERROR", "reason": "Token invalid after refresh", "auth_validated": False}
        self.auth_state = "EXPIRED"
        self.auth_validated = False
        return {"auth_state": "EXPIRED", "auth_validated": False}

    def metadata(self) -> Dict[str, Any]:
        """Return connector metadata."""
        return {
            "connector_id": self.connector_id,
            "provider": self.provider,
            "version": self.version,
            "capabilities": list(self.capabilities.keys()),
            "auth_state": self.auth_state,
            "supports": [
                "repository.read",
                "repository.list",
                "commits.read",
                "issues.read",
            ],
        }

    # --- Receipt generation ---

    def _make_receipt(self,
        operation: str,
        result: dict,
        start: str,
        duration: float,
        capability: str,
        input_data: Dict[str, Any],
        owner_repo: str,
        status: int | None = None,
    ) -> dict[str, Any]:
        """Create a durable tool/connector receipt (canonical schema).

        Emits the ONE canonical receipt shape defined by
        ``runtime.capability_fabric.CANONICAL_RECEIPT_FIELDS`` (same shape
        the Git connector produces): receipt_id, connector_id, provider,
        operation/capability, target, status, started_at (+ timestamp
        compat alias), duration_seconds, result_hash, input_digest,
        authentication label, error. No secrets ever included.

        The result_hash is computed over the SANITIZED result so the
        generic verifier can recompute it from persisted evidence and
        detect post-execution tampering.
        """
        try:
            from runtime.capability_fabric import sanitize_data as _sanitize, _digest as _inp_digest
        except ImportError:  # pragma: no cover - top-level import style
            try:
                from capability_fabric import sanitize_data as _sanitize, _digest as _inp_digest
            except ImportError:
                _sanitize = lambda v: v  # noqa: E731
                import hashlib as _hl, json as _js
                _inp_digest = lambda v: _hl.sha256(_js.dumps(v, sort_keys=True, default=str).encode()).hexdigest()
        sanitized = _sanitize(result or {})
        # Per-execution uniqueness: identical repeated calls must still yield
        # independent receipts (STEP 6 attempt lineage). The timestamp suffix
        # distinguishes attempts; result_hash still binds content.
        _start_compact = "".join(c for c in str(start) if c.isalnum())[:20]
        receipt_id = f"receipt-{self.connector_id}-{capability}-{content_hash(sanitized or {})[:12]}-{_start_compact}"
        # Safely determine target - use the resolved owner_repo (which accepts scope)
        target = owner_repo or "unknown"
        # Safely hash the SANITIZED result data (no secrets)
        try:
            result_hash = content_hash(sanitized) if sanitized else None
        except Exception:
            result_hash = None
        try:
            input_digest = _inp_digest(dict(input_data or {}))
        except Exception:
            input_digest = None
        ok = status is None or status < 400
        canonical_status = "SUCCESS" if ok else "FAILED"
        try:
            from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
            _s = _dt2.fromisoformat(start)
            if _s.tzinfo is None:
                _s = _s.replace(tzinfo=_tz2.utc)
            completed_at = (_s + _td2(seconds=float(duration or 0.0))).isoformat()
        except (ValueError, TypeError, OverflowError):
            completed_at = ""

        receipt = {
            "receipt_id": receipt_id,
            "connector_id": self.connector_id,
            "provider": self.provider,
            "operation": operation,
            "capability": capability,
            "timestamp": start,
            "started_at": start,
            "completed_at": completed_at,
            "target": target,
            "status": canonical_status,
            "http_status": status,
            "success": ok,
            "duration_seconds": duration,
            "result_hash": result_hash,
            "input_digest": input_digest,
            "authentication": "TOKEN_ACTIVE" if ok else "TOKEN_ACTIVE_BUT_FAILED",
            "auth_validated": self.auth_validated,
            "error": None if ok else str((result or {}).get("error", f"HTTP {status}")),
        }
        return receipt
