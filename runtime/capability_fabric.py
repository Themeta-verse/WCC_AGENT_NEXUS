"""NEXUS Universal Capability Execution Fabric.

CANONICAL OWNERSHIP
-------------------
This module is the ONE canonical home of:

- CapabilityRequest        (provider-independent execution request)
- CapabilityResponse       (provider-independent execution result)
- CapabilityResolutionError (typed discovery/authorization failure)
- resolve_connector        (capability -> authorized connector selection)
- execute_capability       (capability -> connector -> observed response)
- verify_capability_response (generic response/evidence verification)
- make_canonical_receipt / normalize_receipt (ONE receipt schema)

Migration boundary: ``runtime.persistent_fabric`` defines legacy
``CapabilityRequest`` / ``CapabilityResponse`` / ``ExecutionReceipt``
dataclasses used by the MissionComposer-era providers (GitHubReadProvider,
BrowserReadProvider, FilesystemReadProvider). Those are a SEPARATE,
provider-bound contract and must not be confused with the classes here.
New connector-based execution MUST use this module.

One canonical contract for ALL external capability execution:

  CapabilityRequest  -> ConnectorRegistry.resolve -> Connector.execute
                     -> CapabilityResponse (OBSERVED) -> tool record
                     -> receipt/provenance -> artifact -> verifier

GitHub is ONE provider behind this interface, not a special case.
New connectors (git, http, db, ...) register capabilities and are
discovered/authorized/executed/verified through the same path.

No secrets ever enter receipts, tool records, artifacts, or traces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import hashlib
import json
import logging
import re

logger = logging.getLogger("nexus.capability_fabric")


VALID_AUTH_STATES = frozenset({
    "NOT_CONFIGURED",  # no credential / no availability configured
    "CONFIGURED",      # credential present but NOT yet validated live
    "CONNECTED",       # credential validated against the external system
    "EXPIRED",
    "REAUTH_REQUIRED",
    "REVOKED",
    "ERROR",
    # For connectors that authenticate by NO credential at all (local git,
    # local filesystem). "Credential validated" is vacuous for these, so
    # claiming CONNECTED would overstate what was checked. Availability for
    # them is a PER-REQUEST property (is this workspace a git repo?) and
    # surfaces as an honest BLOCKED response when it is not — never as
    # AUTH_REQUIRED, because there is no credential to supply.
    "NO_CREDENTIAL_REQUIRED",
})

# Auth states whose connectors may be SELECTED for execution. Selection is
# NOT validation: CONFIGURED means "a credential string is present", and a
# live failure then surfaces honestly as FAILED (or 401 -> ERROR state) —
# never as silent absence. "Environment variable exists" is therefore never
# equated with "fully verified external connection": use auth_validated /
# health() / validate_auth() for ground truth.
USABLE_AUTH_STATES = frozenset({"CONNECTED", "CONFIGURED", "NO_CREDENTIAL_REQUIRED"})

# Canonical capability execution statuses. A failed capability stays failed:
# execute_capability() never maps these to SUCCESS and never falls back to
# another source (e.g. filesystem) for an explicitly requested capability.
VALID_STATUSES = frozenset({
    "SUCCESS",       # external system answered; evidence is OBSERVED
    "PARTIAL",       # external system partially answered; evidence is OBSERVED
    "BLOCKED",       # honest refusal (policy/scope); the refusal is OBSERVED
    "FAILED",        # attempted and failed; no evidence, reality UNKNOWN
    "UNAVAILABLE",   # capability known but no usable connector right now
    "AUTH_REQUIRED", # a connector exists but none is authenticated
    "UNKNOWN",       # indeterminate outcome; never treated as success
})

VALID_REALITIES = frozenset({"OBSERVED", "INFERRED", "VERIFIED", "UNVERIFIED", "UNKNOWN"})

# Capability-name shape: dotted lowercase path segments (e.g.
# "github.repository.read"). Requests violating this are rejected before
# any connector is touched.
CAPABILITY_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")

# ---------------------------------------------------------------------------
# Scope kinds — the generic bridge between a caller-supplied scope string and
# the capabilities that can consume it.
#
# A scope string carries no provider identity. "owner/repo" is not GitHub's
# invention; it is the shape a two-segment repository reference has. Declaring
# which SCOPE KIND a capability consumes lets capability selection stay generic:
# a caller asks "what can I do with THIS scope", the registry answers from the
# capabilities' own declarations, and no layer needs to know a provider name.
# ---------------------------------------------------------------------------

SCOPE_KINDS = frozenset({"owner_repo", "absolute_path", "any"})

_SCOPE_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def classify_scope_kind(scope: Any) -> str:
    """Classify a scope string into a canonical scope kind.

    Returns "owner_repo" for a two-segment repository reference, "absolute_path"
    for an existing directory, else "opaque". Purely structural: no provider is
    named, imported, or special-cased here.
    """
    if not isinstance(scope, str):
        return "opaque"
    text = scope.strip()
    if not text:
        return "opaque"
    if _SCOPE_OWNER_REPO_RE.fullmatch(text):
        return "owner_repo"
    from pathlib import Path as _Path
    try:
        if _Path(text).expanduser().is_dir():
            return "absolute_path"
    except (OSError, ValueError):
        return "opaque"
    return "opaque"


def capability_accepts_scope(declaration: Any, scope: str) -> bool:
    """True when a capability declaration can consume ``scope``.

    A declaration without an explicit ``scope_kind`` is treated as "any", which
    preserves backward compatibility for connectors that do not constrain scope.
    """
    kind = "any"
    if isinstance(declaration, dict):
        declared = declaration.get("scope_kind")
        if isinstance(declared, str) and declared.strip():
            kind = declared.strip()
    if kind not in SCOPE_KINDS:
        # An unrecognised scope_kind is not a silent "any": it matches nothing
        # so a malformed declaration cannot widen access.
        return False
    if kind == "any":
        return True
    return classify_scope_kind(scope) == kind

# Known credential shapes (PAT/OAuth prefixes, Bearer material). Used by
# sanitize_data (value scrubbing), scrub_error_text (diagnostic scrubbing),
# and the generic verifier (receipt value scanning). Synthetic test secrets
# such as TEST_GITHUB_TOKEN_123 are covered by KEY stripping; these cover
# VALUE shapes that must never persist.
_CREDENTIAL_VALUE_RES = (
    re.compile(r"ghp_[A-Za-z0-9]+"),
    re.compile(r"gho_[A-Za-z0-9]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"\bsk-[A-Za-z0-9\-]+"),
    re.compile(r"\bxox[abp]-[A-Za-z0-9\-]+"),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
)

# Canonical receipt schema. Every connector receipt MUST carry these keys
# (normalize_receipt() fills defaults for missing ones, but connectors should
# produce them directly). NEVER include secrets (tokens, credentials, URLs
# with embedded credentials).
CANONICAL_RECEIPT_FIELDS = (
    "receipt_id",      # stable unique id for this execution
    "connector_id",    # executing connector
    "provider",        # provider name (github, git, ...)
    "operation",       # capability/operation executed
    "capability",      # alias of operation (compat)
    "target",          # scope/target of the execution (owner/repo, path, ...)
    "status",          # canonical status (see VALID_STATUSES)
    "started_at",      # ISO-8601 start timestamp
    "completed_at",    # ISO-8601 end timestamp (started_at + duration)
    "duration_seconds",# float execution duration
    "result_hash",     # SHA-256 hex of the sanitized result data
    "input_digest",    # SHA-256 hex of the request input (provenance)
    "authentication",  # auth evidence label (never the credential itself)
    "error",           # error detail when failed, else None
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Canonical contract
# ---------------------------------------------------------------------------

@dataclass
class CapabilityRequest:
    """Provider-independent request for one capability execution."""
    capability: str
    input: dict[str, Any] = field(default_factory=dict)
    scope: str = ""
    task_id: str = ""
    agent_id: str = ""
    principal: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    # Model origin (Phase 15, additive/optional): set ONLY by the model
    # capability gate from runtime-known identity — never from model claims.
    # Empty means "not model-originated". Drives model:* provenance entries.
    requested_by_model: str = ""
    model_request_id: str = ""


@dataclass
class CapabilityResponse:
    """Provider-independent result of one capability execution.

    Canonical owner: this module. Status is one of VALID_STATUSES; reality
    is OBSERVED only for SUCCESS/PARTIAL/BLOCKED (a real external answer,
    including an honest refusal). FAILED/UNAVAILABLE/AUTH_REQUIRED/UNKNOWN
    always carry reality UNKNOWN and must never produce OBSERVED artifacts.
    """

    status: str  # SUCCESS | PARTIAL | BLOCKED | FAILED | UNAVAILABLE | AUTH_REQUIRED | UNKNOWN
    capability: str
    connector_id: str
    provider: str
    reality: str  # OBSERVED for real external responses, never INFERRED-as-observed
    data: dict[str, Any] | list[Any] = field(default_factory=dict)
    receipt: dict[str, Any] = field(default_factory=dict)
    provenance: list[str] = field(default_factory=list)
    error: str | None = None

    def to_tool_record(self, *, task_id: str = "", agent_id: str = "") -> dict[str, Any]:
        """Canonical first-class tool execution record (no provider branches)."""
        receipt = self.receipt or {}
        return {
            "capability": self.capability,
            "connector_id": self.connector_id,
            "provider": self.provider,
            "operation": receipt.get("operation", self.capability),
            "target": receipt.get("target", ""),
            "scope": receipt.get("target", ""),
            "status": self.status,
            "reality": self.reality,
            "result_hash": receipt.get("result_hash"),
            "content_sha256": receipt.get("result_hash"),
            "receipt_id": receipt.get("receipt_id"),
            "duration_seconds": receipt.get("duration_seconds"),
            "authentication": receipt.get("authentication", "UNKNOWN"),
            "task_id": task_id,
            "agent_id": agent_id,
        }


class CapabilityResolutionError(RuntimeError):
    """Structured discovery/authorization failure (not a generic exception)."""

    def __init__(self, code: str, capability: str, detail: str = ""):
        super().__init__(f"{code}: {capability}{(': ' + detail) if detail else ''}")
        self.code = code  # INVALID_REQUEST | UNKNOWN_CAPABILITY | NO_CONNECTOR | NOT_AUTHORIZED | AUTH_STATE | POLICY_DENY
        self.capability = capability
        self.detail = detail


# Explicit selection preference (deterministic, never accidental):
# validated connections first, configured-but-unvalidated second, then
# credential-free local connectors. CONFIGURED is usable but NOT equivalent to
# CONNECTED. NO_CREDENTIAL_REQUIRED ranks last: a validated credential
# connection is strictly more evidenced than a connector with no credential.
_RESOLUTION_PREFERENCE = ("CONNECTED", "CONFIGURED", "NO_CREDENTIAL_REQUIRED")

# Per-auth-state usability reason (generic; keyed off the canonical vocabulary,
# never off a provider name).
_AUTH_STATE_REASONS: dict[str, str] = {
    "CONNECTED": "validated connection",
    "CONFIGURED": "credential configured, validation pending (live call validates)",
    "NO_CREDENTIAL_REQUIRED": "no credential required; per-request availability surfaces as an honest refusal",
}


@dataclass
class ConnectorAlternative:
    """One candidate considered during resolution (selected or not)."""
    connector_id: str
    provider: str
    auth_state: str
    usable: bool
    reason: str


@dataclass
class CapabilityResolution:
    """Deterministic, explainable capability-resolution record.

    Produced BEFORE any execution. Tells exactly which connector was
    selected, why, what else was considered, and whether authentication is
    usable — without executing anything.
    """
    capability: str
    status: str  # RESOLVED | INVALID_REQUEST | UNKNOWN_CAPABILITY | NO_CONNECTOR | NOT_AUTHORIZED
    connector_id: str = ""
    provider: str = ""
    connector_state: str = ""
    auth_usable: bool = False
    selection_reason: str = ""
    alternatives: list[ConnectorAlternative] = field(default_factory=list)
    error_code: str = ""
    error_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "status": self.status,
            "connector_id": self.connector_id,
            "provider": self.provider,
            "connector_state": self.connector_state,
            "auth_usable": self.auth_usable,
            "selection_reason": self.selection_reason,
            "alternatives": [
                {"connector_id": a.connector_id, "provider": a.provider,
                 "auth_state": a.auth_state, "usable": a.usable, "reason": a.reason}
                for a in self.alternatives
            ],
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }


def resolve_capability_detailed(registry: Any, capability: str) -> CapabilityResolution:
    """Negotiate capability -> connector WITHOUT executing.

    Deterministic: among usable connectors (CONNECTED first, then
    CONFIGURED), the first in registration order wins; every candidate is
    recorded with its usability reason. Never raises for resolution
    outcomes — failures are explicit statuses, except a missing registry
    (NO_CONNECTOR) which is also returned, not raised. Malformed capability
    names return INVALID_REQUEST without touching any connector.
    """
    name = capability if isinstance(capability, str) else ""
    if not name or not name.strip() or not CAPABILITY_RE.fullmatch(name.strip()):
        return CapabilityResolution(
            capability=name, status="INVALID_REQUEST",
            error_code="INVALID_REQUEST",
            error_detail="capability must be a non-empty dotted capability path",
        )
    if registry is None:
        return CapabilityResolution(
            capability=name, status="NO_CONNECTOR",
            error_code="NO_CONNECTOR", error_detail="connector_registry is None",
        )
    try:
        candidates = list(registry.get_capability_connectors(name) or [])
    except Exception as exc:
        return CapabilityResolution(
            capability=name, status="NO_CONNECTOR",
            error_code="NO_CONNECTOR", error_detail=f"discovery failed: {type(exc).__name__}",
        )
    if not candidates:
        return CapabilityResolution(
            capability=name, status="UNKNOWN_CAPABILITY",
            error_code="UNKNOWN_CAPABILITY",
            error_detail="no connector declares this capability",
        )
    alternatives: list[ConnectorAlternative] = []
    for cand in candidates:
        cid = str(getattr(cand, "connector_id", "unknown"))
        prov = str(getattr(cand, "provider", "unknown"))
        state = str(getattr(cand, "auth_state", "UNKNOWN"))
        # Usability is decided from the canonical vocabulary, never from a
        # provider name and never from an inline re-statement of two states.
        if state in USABLE_AUTH_STATES:
            alternatives.append(ConnectorAlternative(
                cid, prov, state, True,
                _AUTH_STATE_REASONS.get(state, f"auth_state={state} is usable")))
        else:
            alternatives.append(ConnectorAlternative(
                cid, prov, state, False, f"auth_state={state} is not usable"))
    for preferred in _RESOLUTION_PREFERENCE:
        for alt in alternatives:
            if alt.usable and alt.auth_state == preferred:
                return CapabilityResolution(
                    capability=name, status="RESOLVED",
                    connector_id=alt.connector_id, provider=alt.provider,
                    connector_state=alt.auth_state, auth_usable=True,
                    selection_reason=f"selected {alt.connector_id} ({alt.auth_state}): {alt.reason}; "
                                     f"preference order {list(_RESOLUTION_PREFERENCE)}",
                    alternatives=alternatives,
                )
    states = ",".join(sorted({a.auth_state for a in alternatives}))
    return CapabilityResolution(
        capability=name, status="NOT_AUTHORIZED",
        error_code="NOT_AUTHORIZED",
        error_detail=f"auth_state in ({states}), need one of {sorted(USABLE_AUTH_STATES)}",
        alternatives=alternatives,
    )


def _scope_labels(request: CapabilityRequest) -> dict[str, str]:
    """Tenant/project identity labels bound to an execution (not secrets).

    Principal identity (tenant_id/project_id) is stamped into provenance and
    receipts so cross-context reuse (cross-tenant, cross-project, replayed
    into another workflow) is detectable at verification time. Absent
    principal identity means "unstamped" — verifiers skip those checks
    rather than inventing them.
    """
    labels: dict[str, str] = {}
    principal = getattr(request, "principal", None) or {}
    if isinstance(principal, dict):
        for key in ("tenant_id", "project_id"):
            value = principal.get(key)
            if isinstance(value, str) and value.strip():
                labels[key] = value.strip()
    return labels


def validate_request(request: CapabilityRequest) -> None:
    """Reject malformed requests BEFORE any connector is touched.

    Raises CapabilityResolutionError("INVALID_REQUEST", ...) for:
      - missing / empty / non-string capability
      - capability not matching the canonical dotted-path shape
      - non-dict input structure
    Required targets/scopes are capability-specific and enforced by the
    connector (surfacing as FAILED), not invented here.
    """
    if request is None:
        raise CapabilityResolutionError("INVALID_REQUEST", "", "request is None")
    capability = getattr(request, "capability", "")
    if not isinstance(capability, str) or not capability.strip():
        raise CapabilityResolutionError("INVALID_REQUEST", str(capability), "capability must be a non-empty string")
    if not CAPABILITY_RE.fullmatch(capability.strip()):
        raise CapabilityResolutionError("INVALID_REQUEST", capability, "capability must be a dotted capability path (lowercase letters, digits, '.', '_', '-')")
    if not isinstance(getattr(request, "input", None), dict):
        raise CapabilityResolutionError("INVALID_REQUEST", capability, "request input must be a dict")


# ---------------------------------------------------------------------------
# Generic resolution + execution (no provider imports here)
# ---------------------------------------------------------------------------

def normalize_status(status: Any) -> str:
    """Normalize a connector-reported status to the canonical contract.

    Unknown/empty values become UNKNOWN (never SUCCESS). Comparison is
    case-insensitive; surrounding whitespace is ignored.
    """
    text = str(status or "").strip().upper()
    if text in VALID_STATUSES:
        return text
    logger.warning("non-canonical capability status %r normalized to UNKNOWN", status)
    return "UNKNOWN"


def response_reality(status: str) -> str:
    """Canonical status -> reality mapping (single source of truth)."""
    if status in ("SUCCESS", "PARTIAL"):
        return "OBSERVED"
    if status == "BLOCKED":
        return "OBSERVED"  # honest refusal is still an observed fact
    return "UNKNOWN"


def discover_connectors(registry: Any, capability: str) -> list[dict[str, Any]]:
    """List connectors supporting a capability with health (generic)."""
    if registry is None:
        return []
    try:
        return list(registry.discover(capability) or [])
    except Exception as exc:
        # Explicit, logged degradation: callers see "no connectors" AND the
        # reason is on record instead of vanishing.
        logger.warning(
            "connector discovery failed for %r: %s: %s",
            capability, type(exc).__name__, exc,
        )
        return []


def resolve_connector(registry: Any, capability: str) -> Any:
    """Select the authorized connector for a capability.

    Implemented on top of resolve_capability_detailed (explicit preference:
    CONNECTED, then CONFIGURED). Raises CapabilityResolutionError with
    structured codes for non-resolved outcomes.
    """
    detailed = resolve_capability_detailed(registry, capability)
    if detailed.status != "RESOLVED":
        raise CapabilityResolutionError(
            detailed.error_code or "NO_CONNECTOR", capability, detailed.error_detail)
    if registry is None:  # unreachable (detailed covers it); defensive only
        raise CapabilityResolutionError("NO_CONNECTOR", capability, "connector_registry is None")
    try:
        live = list(registry.get_capability_connectors(capability) or [])
    except Exception as exc:
        raise CapabilityResolutionError("NO_CONNECTOR", capability, f"re-resolution failed: {type(exc).__name__}")
    for cand in live:
        if str(getattr(cand, "connector_id", "")) == detailed.connector_id:
            return cand
    raise CapabilityResolutionError("NO_CONNECTOR", capability, "selected connector vanished")


def make_canonical_receipt(
    *,
    connector_id: str,
    provider: str,
    operation: str,
    target: str,
    status: str,
    started_at: str,
    duration_seconds: float,
    result_hash: str | None,
    input_digest: str | None,
    authentication: str = "UNKNOWN",
    error: str | None = None,
    receipt_id: str = "",
    tenant_id: str = "",
    project_id: str = "",
    attempt: int = 0,
) -> dict[str, Any]:
    """Build ONE canonical receipt shape shared by all connectors.

    Secrets must never be passed in: only labels such as TOKEN_ACTIVE /
    NO_CREDENTIAL_REQUIRED / TOKEN_ABSENT belong in ``authentication``.
    ``tenant_id``/``project_id`` are identity labels (not secrets) binding
    the execution to its scope. ``attempt`` discriminates fabric-minted
    fallback ids across retry attempts (connector-provided ids pass
    through untouched).
    """
    from datetime import timedelta

    status = normalize_status(status)
    minted_suffix = f"-attempt{int(attempt)}" if int(attempt or 0) > 0 else ""
    completed_at = ""
    try:
        _start = datetime.fromisoformat(str(started_at))
        if _start.tzinfo is None:
            _start = _start.replace(tzinfo=timezone.utc)
        completed_at = (_start + timedelta(seconds=float(duration_seconds or 0.0))).isoformat()
    except (ValueError, TypeError, OverflowError):
        completed_at = ""
    return {
        "receipt_id": (receipt_id or f"receipt-{connector_id}-{operation}-{_digest(target + operation)[:12]}{minted_suffix}"),
        "connector_id": connector_id,
        "provider": provider,
        "operation": operation,
        "capability": operation,
        "target": target,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "timestamp": started_at,  # compat alias for pre-canonical readers
        "duration_seconds": duration_seconds,
        "result_hash": result_hash,
        "input_digest": input_digest,
        "authentication": authentication,
        "error": error,
        "tenant_id": tenant_id,
        "project_id": project_id,
    }


def normalize_receipt(receipt: dict[str, Any] | None, *, request: CapabilityRequest,
                      attempt: int = 0, authoritative_status: str = "") -> dict[str, Any]:
    """Enforce the canonical receipt schema on a connector-produced receipt.

    Missing keys are filled deterministically (never with secrets); unknown
    statuses are normalized to UNKNOWN. The connector's own values always
    win when present — this is a backstop, not a rewrite.

    ``authoritative_status`` is the caller's already-resolved response status.
    Passing it here avoids logging a spurious "non-canonical status" warning
    for a connector whose receipt simply omits ``status`` while the response
    itself carries a perfectly valid one. The receipt's own status, when
    present, still wins.
    """
    raw = dict(receipt or {})
    # Only normalize a status the receipt actually carries. An ABSENT receipt
    # status is not a malformed status and must not be logged as one.
    raw_status = raw.get("status")
    status = normalize_status(raw_status) if raw_status else ""
    effective_status = status
    if not status and authoritative_status:
        effective_status = normalize_status(authoritative_status)
    # Provider-neutral target derivation: explicit receipt target, then
    # request scope, then conventional input keys (owner_repo / workspace /
    # target are caller conventions, not provider logic).
    _inp = request.input if isinstance(request.input, dict) else {}
    target = (
        raw.get("target")
        or request.scope
        or _inp.get("owner_repo", "")
        or _inp.get("workspace", "")
        or _inp.get("target", "")
        or "unknown"
    )
    labels = _scope_labels(request)
    canonical = make_canonical_receipt(
        connector_id=str(raw.get("connector_id") or "unknown"),
        provider=str(raw.get("provider") or "unknown"),
        operation=str(raw.get("operation") or raw.get("capability") or request.capability),
        target=str(target),
        status=effective_status,
        started_at=str(raw.get("started_at") or raw.get("timestamp") or _now_iso()),
        duration_seconds=float(raw.get("duration_seconds") or 0.0),
        result_hash=raw.get("result_hash"),
        input_digest=raw.get("input_digest") or _digest(request.input or {}),
        authentication=str(raw.get("authentication") or "UNKNOWN"),
        error=raw.get("error"),
        receipt_id=str(raw.get("receipt_id") or ""),
        # Connector-stamped scope identity wins; otherwise bind the request's.
        tenant_id=str(raw.get("tenant_id") or labels.get("tenant_id", "")),
        project_id=str(raw.get("project_id") or labels.get("project_id", "")),
        attempt=attempt,
    )
    # Preserve recognized compat keys connectors already emit.
    for extra in ("success", "completed_at", "http_status"):
        if extra in raw:
            canonical[extra] = raw[extra]
    return canonical


def resolution_error_to_response(error: CapabilityResolutionError, *, request: CapabilityRequest) -> CapabilityResponse:
    """Convert a typed resolution failure into an explicit FAILED response.

    Code mapping: NOT_AUTHORIZED -> AUTH_REQUIRED, UNKNOWN_CAPABILITY /
    NO_CONNECTOR -> UNAVAILABLE. The failure stays a failure: reality is
    UNKNOWN and no evidence is fabricated.
    """
    status = "AUTH_REQUIRED" if error.code == "NOT_AUTHORIZED" else "UNAVAILABLE"
    receipt = make_canonical_receipt(
        connector_id="unresolved",
        provider="unresolved",
        operation=request.capability,
        target=request.scope or "unknown",
        status=status,
        started_at=_now_iso(),
        duration_seconds=0.0,
        result_hash=None,
        input_digest=_digest(request.input or {}),
        authentication="UNKNOWN",
        error=f"{error.code}: {error.detail or error.capability}",
    )
    return CapabilityResponse(
        status=status,
        capability=request.capability,
        connector_id="unresolved",
        provider="unresolved",
        reality="UNKNOWN",
        data={"error": f"{error.code}: {error.detail or error.capability}"},
        receipt=receipt,
        provenance=[f"agent:{request.agent_id}", f"capability:{request.capability}"],
        error=f"{error.code}: {error.detail or error.capability}",
    )


def sanitize_data(data: Any) -> Any:
    """Strip secret-bearing keys AND scrub credential-shaped values.

    Keys named token/access_token/secret/api_key (or containing clone_token)
    are dropped. String values matching known credential shapes (PAT/OAuth
    prefixes, Bearer material) are replaced with "[REDACTED]" — including
    inside nested structures. Applied to every connector payload before it
    becomes response data, so secrets cannot persist into artifacts even
    when a misbehaving connector emits them.
    """
    if isinstance(data, dict):
        return {
            k: sanitize_data(v)
            for k, v in data.items()
            if "clone_token" not in k.lower() and k.lower() not in ("token", "access_token", "secret", "api_key")
        }
    if isinstance(data, list):
        return [sanitize_data(v) for v in data]
    if isinstance(data, str) and data:
        for _rx in _CREDENTIAL_VALUE_RES:
            if _rx.search(data):
                return "[REDACTED]"
    return data


def scrub_error_text(text: Any) -> str:
    """Scrub credential shapes from diagnostic error strings.

    Exception messages flow into receipts and results as diagnostics (never
    evidence), but must still not carry credential material. Non-string
    input is stringified; credential-shaped substrings become [REDACTED].
    """
    out = str(text or "")
    for _rx in _CREDENTIAL_VALUE_RES:
        out = _rx.sub("[REDACTED]", out)
    return out


# ---------------------------------------------------------------------------
# STEP 13 — explicit policy boundary.
# Policy decides BEFORE execution whether a resolved capability may run.
# ALLOW executes; DENY and REQUIRES_APPROVAL both refuse WITHOUT executing
# (BLOCKED honest refusal, never success). Policy is never inferred from
# connector behavior: an explicit policy object (or the explicit default)
# produces an explicit decision for every execution.
# ---------------------------------------------------------------------------

@dataclass
class PolicyDecision:
    """Explicit allow/deny/approval decision for one capability execution."""
    decision: str  # ALLOW | DENY | REQUIRES_APPROVAL
    reason: str
    policy_id: str = "default-allow"


class CapabilityPolicy:
    """Base policy: override decide() to restrict execution.

    The default allows everything with an explicit reason (open platform
    default). Operators attach restrictive subclasses via
    ``registry.policy``. decide() must be pure and side-effect free — it
    never executes connectors.
    """

    policy_id: str = "default-allow"

    def decide(self, *, request: CapabilityRequest, connector: Any) -> PolicyDecision:
        return PolicyDecision(
            decision="ALLOW",
            reason="default allow: no restrictive policy configured",
            policy_id=self.policy_id,
        )


def decide_policy(registry: Any, request: CapabilityRequest, connector: Any) -> PolicyDecision:
    """Evaluate the registry's policy (or the explicit default)."""
    policy = getattr(registry, "policy", None)
    if policy is None:
        return CapabilityPolicy().decide(request=request, connector=connector)
    decision = policy.decide(request=request, connector=connector)
    if not isinstance(decision, PolicyDecision) or decision.decision not in ("ALLOW", "DENY", "REQUIRES_APPROVAL"):
        logger.warning("policy %r returned invalid decision; treating as DENY", policy)
        return PolicyDecision(decision="DENY", reason="policy returned an invalid decision",
                              policy_id=getattr(policy, "policy_id", "unknown"))
    return decision


# ---------------------------------------------------------------------------
# STEP 14 — audit event chain.
# Every execution emits structured, secret-free events when the registry
# carries an event_sink callable: requested -> resolved -> started ->
# completed/failed (+ policy outcome inside resolved). The sink is optional;
# failures of the sink itself are logged and never break execution.
# ---------------------------------------------------------------------------

def emit_capability_event(registry: Any, event_type: str, payload: dict[str, Any]) -> None:
    """Emit one audit event through the registry's sink (no secrets, ever)."""
    sink = getattr(registry, "event_sink", None)
    if sink is None:
        return
    event = {"event": event_type, "timestamp": _now_iso()}
    event.update(payload or {})
    try:
        sink(event)
    except Exception as exc:
        logger.warning("capability event sink failed: %s: %s", type(exc).__name__, exc)


def _event_base(request: CapabilityRequest, connector: Any = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "capability": request.capability,
        "scope": request.scope,
        "task_id": request.task_id,
        "agent_id": request.agent_id,
        "input_digest": _digest(request.input or {}),
    }
    if connector is not None:
        base["connector_id"] = str(getattr(connector, "connector_id", "unknown"))
        base["provider"] = str(getattr(connector, "provider", "unknown"))
    return base


# ---------------------------------------------------------------------------
# STEP 7 — observation freshness (metadata, never auto-invalidation).
# request.constraints["freshness_ttl_seconds"] declares how long the
# observation should be considered fresh. The receipt records observed_at /
# expires_at / freshness_policy so readers can distinguish "was observed"
# from "is currently fresh". Historical evidence is never invalidated by
# this mechanism; the verifier does not auto-expire anything.
# ---------------------------------------------------------------------------

def _apply_freshness(receipt: dict[str, Any], request: CapabilityRequest, started_iso: str) -> dict[str, Any]:
    ttl = (getattr(request, "constraints", None) or {}).get("freshness_ttl_seconds")
    receipt["observed_at"] = receipt.get("observed_at") or receipt.get("started_at") or started_iso
    try:
        ttl_seconds = float(ttl) if ttl is not None else 0.0
    except (TypeError, ValueError):
        ttl_seconds = 0.0
    if ttl_seconds > 0:
        try:
            _start = datetime.fromisoformat(str(started_iso))
            if _start.tzinfo is None:
                _start = _start.replace(tzinfo=timezone.utc)
            from datetime import timedelta as _td
            receipt["expires_at"] = (_start + _td(seconds=ttl_seconds)).isoformat()
            receipt["freshness_policy"] = f"ttl:{ttl_seconds:g}s"
        except (ValueError, TypeError, OverflowError):
            receipt["expires_at"] = ""
            receipt["freshness_policy"] = "ttl:uncomputable"
    else:
        receipt.setdefault("expires_at", "")
        receipt.setdefault("freshness_policy", "none")
    return receipt


def _finalize_receipt(receipt: dict[str, Any], request: CapabilityRequest,
                     started_iso: str, attempt: int, parent_receipt_id: str) -> dict[str, Any]:
    """Attach freshness metadata + attempt lineage to a finished receipt."""
    _apply_freshness(receipt, request, started_iso)
    receipt["attempt"] = attempt
    receipt["parent_receipt_id"] = parent_receipt_id
    return receipt


def _refusal_response(request: CapabilityRequest, connector: Any,
                      labels: dict[str, str], provenance: list[str],
                      started: str, attempt: int, parent_receipt_id: str,
                      *, error: str, registry: Any = None) -> CapabilityResponse:
    """Honest policy refusal: BLOCKED (observed refusal), never success,
    never executed. Emits the completion event for trace completeness."""
    receipt = _finalize_receipt(make_canonical_receipt(
        connector_id=str(getattr(connector, "connector_id", "unknown")),
        provider=str(getattr(connector, "provider", "unknown")),
        operation=request.capability,
        target=str(request.scope or "unknown"),
        status="BLOCKED",
        started_at=started,
        duration_seconds=0.0,
        result_hash=None,
        input_digest=_digest(request.input or {}),
        authentication="UNKNOWN",
        error=error,
        tenant_id=labels.get("tenant_id", ""),
        project_id=labels.get("project_id", ""),
        attempt=attempt,
    ), request, started, attempt, parent_receipt_id)
    if registry is not None:
        emit_capability_event(registry, "capability_execution_completed", {
            **_event_base(request, connector), "attempt": attempt,
            "receipt_id": receipt["receipt_id"], "status": "BLOCKED",
            "reality": "OBSERVED",
        })
    return CapabilityResponse(
        status="BLOCKED",
        capability=request.capability,
        connector_id=getattr(connector, "connector_id", "unknown"),
        provider=getattr(connector, "provider", "unknown"),
        reality="OBSERVED",
        data={"error": error, "refused": True},
        receipt=receipt,
        provenance=list(provenance),
        error=error,
    )


def _finish_attempt(registry: Any, request: CapabilityRequest, connector: Any,
                   receipt: dict[str, Any], status: str, reality: str,
                   started: str, attempt: int, parent_receipt_id: str) -> dict[str, Any]:
    """Finalize a receipt (freshness + attempt lineage) and emit the
    completion event. Returns the finalized receipt."""
    _finalize_receipt(receipt, request, started, attempt, parent_receipt_id)
    emit_capability_event(
        registry,
        "capability_execution_completed" if status in ("SUCCESS", "PARTIAL", "BLOCKED") else "capability_execution_failed",
        {**_event_base(request, connector), "attempt": attempt,
         "parent_receipt_id": parent_receipt_id,
         "receipt_id": receipt.get("receipt_id"), "status": status, "reality": reality},
    )
    return receipt


def execute_capability(registry: Any, request: CapabilityRequest,
                       *, _attempt: int = 1, _parent_receipt_id: str = "") -> CapabilityResponse:
    """Execute one capability through the generic interface.

    Agents should prefer this over get_connector(id).execute(...).
    Failures are explicit CapabilityResolutionError or FAILED responses —
    never silent fallback, never filesystem substitution for an explicitly
    requested external capability.

    Flow: validate -> negotiate (detailed resolution) -> policy gate ->
    execute -> canonicalize. Policy DENY / REQUIRES_APPROVAL refuse with an
    honest BLOCKED response WITHOUT touching the connector. Every stage
    emits audit events when the registry carries an event_sink.
    """
    emit_capability_event(registry, "capability_requested", _event_base(request))
    validate_request(request)
    detailed = resolve_capability_detailed(registry, request.capability)
    if detailed.status != "RESOLVED":
        emit_capability_event(registry, "capability_resolution_failed", {
            **_event_base(request), "resolution": detailed.to_dict()})
        raise CapabilityResolutionError(
            detailed.error_code or "NO_CONNECTOR", request.capability, detailed.error_detail)
    try:
        live = list(registry.get_capability_connectors(request.capability) or [])
    except Exception as exc:
        raise CapabilityResolutionError("NO_CONNECTOR", request.capability,
                                        f"re-resolution failed: {type(exc).__name__}")
    connector = next((c for c in live
                      if str(getattr(c, "connector_id", "")) == detailed.connector_id), None)
    if connector is None:
        raise CapabilityResolutionError("NO_CONNECTOR", request.capability,
                                        "selected connector vanished")
    started = _now_iso()
    labels = _scope_labels(request)
    provenance = [f"agent:{request.agent_id}", f"capability:{request.capability}"]
    if labels.get("tenant_id"):
        provenance.append(f"tenant:{labels['tenant_id']}")
    if labels.get("project_id"):
        provenance.append(f"project:{labels['project_id']}")
    # Model origin is runtime-bound (set by the gate, never model-claimed).
    if getattr(request, "requested_by_model", ""):
        provenance.append(f"model:{request.requested_by_model}")
    if getattr(request, "model_request_id", ""):
        provenance.append(f"model-request:{request.model_request_id}")
    policy = decide_policy(registry, request, connector)
    emit_capability_event(registry, "capability_resolved", {
        **_event_base(request, connector),
        "resolution": detailed.to_dict(),
        "policy": policy.decision, "policy_id": policy.policy_id,
        "policy_reason": policy.reason,
    })
    if policy.decision != "ALLOW":
        return _refusal_response(
            request, connector, labels, provenance, started, _attempt, _parent_receipt_id,
            error=f"policy {policy.decision}: {policy.reason} (policy_id={policy.policy_id})",
            registry=registry,
        )
    emit_capability_event(registry, "capability_execution_started", {
        **_event_base(request, connector), "attempt": _attempt,
        "parent_receipt_id": _parent_receipt_id,
    })
    try:
        raw = connector.execute(request.capability, dict(request.input or {}))
    except CapabilityResolutionError:
        raise
    except Exception as exc:
        _safe_error = scrub_error_text(f"{type(exc).__name__}: {exc}")
        _fail_inp = request.input if isinstance(request.input, dict) else {}
        receipt = make_canonical_receipt(
            connector_id=str(getattr(connector, "connector_id", "unknown")),
            provider=str(getattr(connector, "provider", "unknown")),
            operation=request.capability,
            target=str(request.scope or _fail_inp.get("owner_repo", "") or _fail_inp.get("workspace", "") or _fail_inp.get("target", "") or "unknown"),
            status="FAILED",
            started_at=started,
            duration_seconds=0.0,
            result_hash=None,
            input_digest=_digest(request.input or {}),
            authentication="UNKNOWN",
            error=_safe_error,
            tenant_id=labels.get("tenant_id", ""),
            project_id=labels.get("project_id", ""),
            attempt=_attempt,
        )
        receipt = _finish_attempt(registry, request, connector, receipt, "FAILED",
                                  "UNKNOWN", started, _attempt, _parent_receipt_id)
        return CapabilityResponse(
            status="FAILED",
            capability=request.capability,
            connector_id=getattr(connector, "connector_id", "unknown"),
            provider=getattr(connector, "provider", "unknown"),
            reality="UNKNOWN",
            data={"error": _safe_error},
            receipt=receipt,
            provenance=list(provenance),
            error=_safe_error,
        )
    if not isinstance(raw, dict):
        # A connector MUST return a result dict; anything else (None, list,
        # str) is an explicit failure, never silently coerced to success.
        receipt = make_canonical_receipt(
            connector_id=str(getattr(connector, "connector_id", "unknown")),
            provider=str(getattr(connector, "provider", "unknown")),
            operation=request.capability,
            target=str(request.scope or "unknown"),
            status="FAILED",
            started_at=started,
            duration_seconds=0.0,
            result_hash=None,
            input_digest=_digest(request.input or {}),
            authentication="UNKNOWN",
            error=f"connector returned {type(raw).__name__}, expected dict",
            tenant_id=labels.get("tenant_id", ""),
            project_id=labels.get("project_id", ""),
            attempt=_attempt,
        )
        receipt = _finish_attempt(registry, request, connector, receipt, "FAILED",
                                  "UNKNOWN", started, _attempt, _parent_receipt_id)
        return CapabilityResponse(
            status="FAILED",
            capability=request.capability,
            connector_id=getattr(connector, "connector_id", "unknown"),
            provider=getattr(connector, "provider", "unknown"),
            reality="UNKNOWN",
            data={"error": f"connector returned {type(raw).__name__}, expected dict"},
            receipt=receipt,
            provenance=list(provenance),
            error=f"connector returned {type(raw).__name__}, expected dict",
        )
    status = normalize_status(raw.get("status", "FAILED"))
    _RECEIPT_SIGNAL_KEYS = ("receipt_id", "result_hash", "connector_id", "operation",
                            "capability", "target", "status", "started_at", "timestamp")
    _raw_receipt = raw.get("receipt")
    if status in ("SUCCESS", "PARTIAL") and not (
            isinstance(_raw_receipt, dict) and any(k in _raw_receipt for k in _RECEIPT_SIGNAL_KEYS)):
        # A success claim without a receipt — or with a receipt-shaped dict
        # carrying zero integrity fields — is not evidence: no hash, no
        # provenance binding, nothing to verify. Explicit failure instead of
        # a minted receipt that could pass presence checks.
        receipt = make_canonical_receipt(
            connector_id=str(getattr(connector, "connector_id", "unknown")),
            provider=str(getattr(connector, "provider", "unknown")),
            operation=request.capability,
            target=str(request.scope or "unknown"),
            status="FAILED",
            started_at=started,
            duration_seconds=0.0,
            result_hash=None,
            input_digest=_digest(request.input or {}),
            authentication="UNKNOWN",
            error="connector claimed SUCCESS without a receipt",
            tenant_id=labels.get("tenant_id", ""),
            project_id=labels.get("project_id", ""),
            attempt=_attempt,
        )
        receipt = _finish_attempt(registry, request, connector, receipt, "FAILED",
                                  "UNKNOWN", started, _attempt, _parent_receipt_id)
        return CapabilityResponse(
            status="FAILED",
            capability=request.capability,
            connector_id=getattr(connector, "connector_id", "unknown"),
            provider=getattr(connector, "provider", "unknown"),
            reality="UNKNOWN",
            data={"error": "connector claimed SUCCESS without a receipt"},
            receipt=receipt,
            provenance=list(provenance),
            error="connector claimed SUCCESS without a receipt",
        )
    data = sanitize_data(raw.get("data", {}))
    receipt = normalize_receipt(dict(raw.get("receipt") or {}), request=request,
                                attempt=_attempt, authoritative_status=status)
    # The receipt status is the connector's own claim; the response status is
    # authoritative. Keep both visible but never let a receipt upgrade failure.
    receipt["status"] = status
    reality = response_reality(status)
    provenance = list(provenance) + [f"connector:{getattr(connector, 'connector_id', 'unknown')}"]
    receipt = _finish_attempt(registry, request, connector, receipt, status,
                              reality, started, _attempt, _parent_receipt_id)
    return CapabilityResponse(
        status=status,
        capability=request.capability,
        connector_id=getattr(connector, "connector_id", "unknown"),
        provider=getattr(connector, "provider", "unknown"),
        reality=reality,
        data=data,
        receipt=receipt,
        provenance=provenance,
        error=None if status in ("SUCCESS", "PARTIAL", "BLOCKED") else str((data.get("error") if isinstance(data, dict) else data) or "connector failed"),
    )


# ---------------------------------------------------------------------------
# STEP 6 — explicit retry with attempt lineage.
# Each attempt executes independently with its own receipt; receipts chain
# via parent_receipt_id ("" for the first attempt). Attempts are never
# overwritten: the caller receives every attempt plus the final outcome, so
# the verifier can determine exactly which attempt produced an artifact.
# Resolution/policy failures are NOT retried (re-resolution would be
# identical); only execution outcomes in `retry_on` are.
# ---------------------------------------------------------------------------

@dataclass
class CapabilityAttempt:
    """One recorded execution attempt within a retry sequence."""
    attempt: int
    status: str
    reality: str
    receipt_id: str
    parent_receipt_id: str
    error: str | None = None


def execute_with_retry(
    registry: Any,
    request: CapabilityRequest,
    *,
    max_attempts: int = 1,
    retry_on: tuple[str, ...] = ("FAILED", "UNKNOWN"),
) -> tuple[CapabilityResponse, list[CapabilityAttempt]]:
    """Execute with explicit, bounded retries and full attempt lineage.

    Returns (final_response, attempts). Stops at the first response whose
    status is not in retry_on, or when max_attempts is exhausted. A policy
    refusal (BLOCKED) is final — denial is not retried.
    """
    attempts: list[CapabilityAttempt] = []
    parent_receipt_id = ""
    attempt_no = 0
    response: CapabilityResponse | None = None
    total = max(1, int(max_attempts or 1))
    while attempt_no < total:
        attempt_no += 1
        response = execute_capability(
            registry, request, _attempt=attempt_no, _parent_receipt_id=parent_receipt_id)
        attempts.append(CapabilityAttempt(
            attempt=attempt_no,
            status=response.status,
            reality=response.reality,
            receipt_id=str(response.receipt.get("receipt_id", "")),
            parent_receipt_id=parent_receipt_id,
            error=response.error,
        ))
        parent_receipt_id = str(response.receipt.get("receipt_id", ""))
        if response.status not in retry_on:
            break
    assert response is not None
    return response, attempts


def verify_capability_response(
    response: CapabilityResponse,
    *,
    expected_capability: str = "",
    expected_scope: str = "",
    artifact_reality: str = "",
    artifact_provenance: list[str] | None = None,
    evidence_present: bool = True,
) -> list[dict[str, Any]]:
    """Generic verifier for ANY externally observed artifact (no provider fields).

    Checks 1-10 of the milestone: observation, reality, capability,
    connector, receipt, hash, provenance, scope, execution record, lineage.
    Provider-specific checks (e.g. github full_name) are extensions.
    """
    checks: list[dict[str, Any]] = []

    def _add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    _add("observation_exists", bool(response.data), "response data present" if response.data else "response data missing/empty")
    # Malformed payloads never count as evidence: data must be a mapping or list.
    _add("data_shape_valid", isinstance(response.data, (dict, list)), f"data type={type(response.data).__name__}")
    _add("status_canonical", response.status in VALID_STATUSES, f"status={response.status}")
    _add("reality_observed", response.reality == "OBSERVED", f"reality={response.reality}")
    _add(
        "reality_consistent_with_status",
        response.reality == response_reality(response.status) if response.status in VALID_STATUSES else response.reality == "UNKNOWN",
        f"status={response.status} reality={response.reality}",
    )
    _add("capability_present", bool(response.capability), f"capability={response.capability or '?'}")
    if expected_capability:
        _add("capability_match", response.capability == expected_capability, f"expected {expected_capability}, got {response.capability}")
    _add("connector_present", bool(response.connector_id), f"connector={response.connector_id or '?'}")
    _add("receipt_present", bool(response.receipt and response.receipt.get("receipt_id")), "receipt present" if response.receipt.get("receipt_id") else "receipt_id missing")
    # Receipt/response consistency: the receipt must describe THIS response,
    # not another execution (prevents receipt replay across capabilities).
    if response.receipt.get("receipt_id"):
        _add(
            "receipt_matches_connector",
            (not response.receipt.get("connector_id")) or response.receipt.get("connector_id") == response.connector_id,
            f"receipt.connector={response.receipt.get('connector_id')} response.connector={response.connector_id}",
        )
        _add(
            "receipt_matches_capability",
            (not response.receipt.get("capability")) or response.receipt.get("capability") == response.capability,
            f"receipt.capability={response.receipt.get('capability')} response.capability={response.capability}",
        )
        _add(
            "receipt_matches_provider",
            (not response.receipt.get("provider")) or response.receipt.get("provider") == response.provider,
            f"receipt.provider={response.receipt.get('provider')} response.provider={response.provider}",
        )
        _add(
            "receipt_matches_status",
            (not response.receipt.get("status")) or normalize_status(response.receipt.get("status")) == response.status,
            f"receipt.status={response.receipt.get('status')} response.status={response.status}",
        )
    if response.receipt.get("result_hash") and response.data:
        _add("result_hash_present", True, f"result_hash={str(response.receipt.get('result_hash'))[:16]}...")
    else:
        _add("result_hash_present", bool(response.receipt.get("result_hash")), "result_hash missing")
    # Result-hash integrity: recompute over the sanitized data and compare
    # when the receipt carries a well-formed hex digest. A mismatch proves
    # the evidence was altered after the connector executed.
    rh = str(response.receipt.get("result_hash", "") or "")
    if rh and response.data and all(c in "0123456789abcdef" for c in rh.lower()) and len(rh) >= 32:
        try:
            recomputed = _digest(sanitize_data(response.data))
            _add("result_hash_consistent", recomputed == rh.lower(), "receipt hash matches response data" if recomputed == rh.lower() else "receipt hash MISMATCH: evidence altered after execution")
        except Exception as exc:
            _add("result_hash_consistent", False, f"hash recompute failed: {type(exc).__name__}")
    _add("provenance_present", bool(response.provenance or artifact_provenance), "provenance present" if (response.provenance or artifact_provenance) else "provenance missing")
    # Input binding: the receipt must reference the request it answers, so a
    # receipt cannot be replayed across different requests undetected.
    # Presence is required; exact-digest equality is the requester's check
    # (only the requester holds the original input).
    _add("input_digest_present", bool(response.receipt.get("input_digest")), "input_digest present" if response.receipt.get("input_digest") else "input_digest missing")
    if expected_scope:
        target = str(response.receipt.get("target", ""))
        _add("scope_match", target.lower() == expected_scope.lower(), f"expected {expected_scope!r}, got {target!r}")
    _add("execution_record_present", bool(response.receipt.get("receipt_id") and response.capability), "tool record keys present")
    if artifact_reality:
        _add("lineage_reality", artifact_reality in ("OBSERVED", "INFERRED", "VERIFIED"), f"artifact reality={artifact_reality}")
    if evidence_present is False:
        _add("evidence_present", False, "no evidence")
    else:
        _add("evidence_present", True, "evidence present")
    # Tamper evidence: receipt hash must be a hex digest when present.
    _add("receipt_hash_shape", (not rh) or all(c in "0123456789abcdef" for c in rh.lower()[:16]), "hash shape ok")
    # Secret hygiene at the evidence boundary: no receipt field — key or
    # value — may carry raw credential material. Keys are matched by name;
    # values are scanned for known credential shapes (PAT/OAuth prefixes,
    # Bearer material). A flagged receipt fails verification explicitly.
    _suspicious = []
    for _k, _v in (response.receipt or {}).items():
        _kl = str(_k).lower()
        if _kl in ("token", "access_token", "secret", "api_key") or "clone_token" in _kl:
            _suspicious.append(f"key:{_k}")
            continue
        if isinstance(_v, str) and _v:
            for _rx in _CREDENTIAL_VALUE_RES:
                if _rx.search(_v):
                    _suspicious.append(f"value:{_k}")
                    break
    _add("no_secrets_in_receipt", not _suspicious, "receipt carries no secret-bearing keys or credential values" if not _suspicious else f"secret material in receipt: {_suspicious}")
    # Authentication-label hygiene: the receipt's authentication field must
    # be a short status label, never a credential value. Known credential
    # shapes (PAT/OAuth prefixes, Bearer material) fail explicitly.
    _auth_label = str(response.receipt.get("authentication", "") or "")
    _auth_leak = (
        _auth_label.lower().startswith("bearer ")
        or _auth_label.startswith(("ghp_", "gho_", "github_pat_", "sk-", "xoxp-", "xoxb-"))
        or (len(_auth_label) > 40 and _auth_label.replace("_", "").replace("-", "").isalnum())
    )
    _add("authentication_no_secret", not _auth_leak, "authentication is a status label" if not _auth_leak else "authentication field looks like credential material")
    return checks


_capability_registry = None


def initialize_capability_registry() -> Any:
    """Canonical ONE registry for the process.

    Built-in providers, in registration order (first registration wins ties
    only when auth states are equal — see _RESOLUTION_PREFERENCE):

      1. ``github``     — external REST provider (credential-gated)
      2. ``git``        — local read-only git inspection (no credential)
      3. ``filesystem`` — bounded local filesystem read/list (no credential)

    Reuses the GitHub singleton so the proven baseline keeps its object
    identity, then registers the credential-free local connectors once.
    Idempotent. Registration failures are recorded on the registry (and logged)
    instead of being swallowed: a missing connector must be explainable.
    """
    from runtime.github_provider import initialize_github_connector_registration
    _, registry = initialize_github_connector_registration()
    global _capability_registry
    if registry is None:
        raise RuntimeError("capability registry unavailable")
    _register_local_connectors(registry)
    _capability_registry = registry
    return registry


# Credential-free local connectors, declared as (module, attribute-pair, id).
# A generic tuple of provider specs — the fabric does not branch per provider.
_LOCAL_CONNECTOR_SPECS = (
    ("runtime.git_connector", "GitConnector", "GIT_CAPABILITIES", "git"),
    ("runtime.filesystem_connector", "FilesystemConnector", "FILESYSTEM_CAPABILITIES", "filesystem"),
)


def _register_local_connectors(registry: Any) -> None:
    """Register the credential-free local connectors exactly once each.

    Every failure is appended to ``registry.registration_errors`` and logged:
    a connector that failed to register must be explainable, never silently
    absent (which would surface as an unexplained UNKNOWN_CAPABILITY later).
    """
    import importlib

    for module_name, cls_name, caps_name, connector_id in _LOCAL_CONNECTOR_SPECS:
        try:
            existing = registry.get_connector(connector_id)
        except Exception as exc:
            logger.warning("%s connector lookup failed: %s: %s", connector_id, type(exc).__name__, exc)
            existing = None
        if existing is not None:
            continue
        try:
            module = importlib.import_module(module_name)
            connector_cls = getattr(module, cls_name)
            caps = dict(getattr(module, caps_name))
        except Exception as exc:
            entry = {
                "connector_id": connector_id,
                "source": "capability_fabric._register_local_connectors",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _record_registration_error(registry, entry)
            continue
        try:
            connector = connector_cls(
                connector_id=connector_id,
                provider=connector_id,
                version="1.0.0",
                capabilities=caps,
            )
        except Exception as exc:
            entry = {
                "connector_id": connector_id,
                "source": "capability_fabric._register_local_connectors",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _record_registration_error(registry, entry)
            continue
        if hasattr(registry, "register_safe"):
            registered = registry.register_safe(
                connector,
                source="capability_fabric._register_local_connectors",
            )
            if not registered:
                logger.warning("%s connector registration failed; see registry.registration_errors",
                               connector_id)
            continue
        # pragma: no cover - legacy registries without register_safe
        try:
            registry.register(connector)
        except Exception as exc:
            _record_registration_error(registry, {
                "connector_id": connector_id,
                "source": "capability_fabric._register_local_connectors",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })


def _record_registration_error(registry: Any, entry: dict[str, Any]) -> None:
    logger.warning("connector registration error: %s", entry)
    try:
        registry.registration_errors.append(entry)
    except Exception:
        pass


def get_capability_registry() -> Any:
    """Return the shared registry, initializing on first use."""
    global _capability_registry
    if _capability_registry is not None:
        return _capability_registry
    return initialize_capability_registry()


def response_from_legacy_execute(
    *,
    capability: str,
    scope: str,
    task_id: str,
    agent_id: str,
    legacy_result: dict[str, Any],
    connector_id: str = "",
    provider: str = "",
) -> CapabilityResponse:
    """Adapt a legacy conn.execute() dict into the canonical response.

    Used ONLY for stub/test registries exposing get_connector() without
    request_capability(). Production registries go through execute_capability().
    """
    status = normalize_status(legacy_result.get("status", "FAILED"))
    receipt = dict(legacy_result.get("receipt") or {})
    receipt.setdefault("connector_id", connector_id or "unknown")
    receipt.setdefault("provider", provider or "unknown")
    receipt.setdefault("operation", capability)
    receipt.setdefault("capability", capability)
    receipt.setdefault("timestamp", _now_iso())
    if not receipt.get("target"):
        receipt["target"] = scope
    receipt["status"] = status
    reality = response_reality(status)
    return CapabilityResponse(
        status=status,
        capability=capability,
        connector_id=receipt.get("connector_id", "unknown"),
        provider=receipt.get("provider", "unknown"),
        reality=reality,
        data=sanitize_data(legacy_result.get("data", {})),
        receipt=receipt,
        provenance=[f"agent:{agent_id}", f"capability:{capability}"],
    )
