"""NEXUS Connector Registry — CANONICAL generic connector registry.

Canonical ownership
-------------------
This module is the ONE canonical home of ``ConnectorRegistry``: the generic,
provider-independent registry that maps capability names to connectors.

Migration boundary
------------------
``runtime.github_provider`` previously defined ``ConnectorRegistry`` inline.
That definition is now a backwards-compatible alias of the class defined
here. New code MUST import from here::

    from runtime.connector_registry import ConnectorRegistry

No provider-specific logic may live in this module: no GitHub names, no git
names, no token handling, no per-provider branches. Providers register
themselves via :meth:`ConnectorRegistry.register`; agents discover them via
:meth:`ConnectorRegistry.discover` / :meth:`resolve_capability` and execute
via :meth:`request_capability`, which delegate to
``runtime.capability_fabric`` (imported lazily to avoid import cycles).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nexus.connector_registry")


@dataclass
class Connector:
    """Generic connector contract — the ONE canonical base class.

    Canonical ownership: this module. A connector wraps ANY external system
    (GitHub, git, filesystem, HTTP, database, ...) and is the only thing the
    capability fabric ever talks to.

    This base class was previously defined inside ``runtime.github_provider``,
    which put the generic contract inside a provider-specific module. It is
    re-exported from there for backwards compatibility, but new providers MUST
    import it from here so no provider module owns the architecture.

    No provider-specific logic may appear here: no provider names, no token
    handling, no per-provider branches. ``runtime.github_provider`` and
    ``runtime.git_connector`` both subclass this.
    """

    connector_id: str
    provider: str
    version: str
    capabilities: Dict[str, Dict[str, Any]]
    auth_state: str = "NOT_CONFIGURED"  # one of: NOT_CONFIGURED, CONFIGURED, CONNECTED, EXPIRED, REAUTH_REQUIRED, ERROR, REVOKED
    # (canonical vocabulary: runtime.capability_fabric.VALID_AUTH_STATES)
    # Which specialized research path this connector feeds, if any. This is
    # declared BY THE CONNECTOR so agents never hard-code provider capability
    # names in order to route: the agent dispatches on the profile, and a new
    # provider participates by declaring one. "generic" means the agent uses
    # its fully provider-agnostic observation path.
    research_profile: str = "generic"

    def health(self) -> Dict[str, Any]:
        """Return connector health status."""
        raise NotImplementedError

    def execute(self, operation: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an operation. Returns result dict or raises."""
        raise NotImplementedError

    def revoke(self) -> None:
        """Revoke this connector's connection/credentials."""
        raise NotImplementedError

    def refresh(self) -> Dict[str, Any]:
        """Refresh authentication state. Returns new auth state."""
        raise NotImplementedError

    def metadata(self) -> Dict[str, Any]:
        """Return connector metadata (name, description, etc.)."""
        raise NotImplementedError


class ConnectorRegistry:
    """Generic registry of capability-providing connectors.

    A connector is any object exposing::

        connector_id : str
        provider     : str
        auth_state   : str   # NOT_CONFIGURED | CONFIGURED | CONNECTED | ...
        capabilities : dict[str, dict]
        health()     -> dict
        execute(operation: str, input_data: dict) -> dict

    The registry never interprets provider identity: resolution is purely
    capability-name matching plus credential-usable gating (auth_state in
    runtime.capability_fabric.USABLE_AUTH_STATES). CONFIGURED means a
    credential is present but not yet validated live — selection is not
    validation, and a live failure surfaces as FAILED. NO_CREDENTIAL_REQUIRED
    marks a connector that authenticates by no credential at all (local git,
    local filesystem); its availability is a per-request property and an
    unusable environment surfaces as an honest BLOCKED, never AUTH_REQUIRED.
    """

    def __init__(self) -> None:
        self._connectors: Dict[str, Any] = {}
        self._capability_map: Dict[str, List[str]] = {}
        # Explicit, inspectable record of registration problems. A failed
        # registration must never silently make a capability disappear.
        self.registration_errors: List[Dict[str, Any]] = []
        # Optional explicit policy boundary (STEP 13). None means the
        # explicit default-allow policy (an ALLOW decision is still
        # produced and recorded per execution — never inferred).
        self.policy: Any = None
        # Optional audit event sink: callable(event_dict). Secret-free
        # events only; sink failures are logged, never raised.
        self.event_sink: Any = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, connector: Any) -> None:
        """Register a connector. Raises on invalid connector identity."""
        cid = getattr(connector, "connector_id", None)
        if not cid or not isinstance(cid, str):
            raise ValueError("connector must expose a non-empty str connector_id")
        if not isinstance(getattr(connector, "capabilities", None), dict):
            raise ValueError(f"connector {cid!r} must expose a capabilities dict")
        self._connectors[cid] = connector
        for cap_name in connector.capabilities:
            self._capability_map.setdefault(cap_name, [])
            if cid not in self._capability_map[cap_name]:
                self._capability_map[cap_name].append(cid)

    def register_safe(self, connector: Any, *, source: str = "") -> bool:
        """Register, recording failures instead of raising.

        Returns True on success. On failure the error is appended to
        :attr:`registration_errors` and logged — never swallowed silently.
        """
        try:
            self.register(connector)
            return True
        except Exception as exc:
            entry = {
                "connector_id": getattr(connector, "connector_id", "?"),
                "source": source,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            self.registration_errors.append(entry)
            logger.warning("connector registration failed: %s", entry)
            return False

    def unregister(self, connector_id: str) -> None:
        """Unregister a connector by id (no-op when unknown)."""
        connector = self._connectors.pop(connector_id, None)
        if connector is None:
            return
        for cap_name in getattr(connector, "capabilities", {}):
            self._capability_map[cap_name] = [
                cid for cid in self._capability_map.get(cap_name, [])
                if cid != connector_id
            ]

    # ------------------------------------------------------------------
    # Discovery (read-only, no execution)
    # ------------------------------------------------------------------

    def discover(self, capability: str) -> List[Dict[str, Any]]:
        """List connectors declaring ``capability`` with their health.

        A connector whose own ``health()`` raises is reported with an
        explicit ERROR health entry — never dropped silently.
        """
        results: List[Dict[str, Any]] = []
        for cid in self._capability_map.get(capability, []):
            conn = self._connectors[cid]
            try:
                health = conn.health()
            except Exception as exc:
                health = {
                    "status": "ERROR",
                    "reason": f"health() raised {type(exc).__name__}: {exc}",
                }
            results.append(
                {
                    "connector_id": cid,
                    "provider": getattr(conn, "provider", "unknown"),
                    "auth_state": getattr(conn, "auth_state", "UNKNOWN"),
                    "health": health,
                    "capabilities": list(getattr(conn, "capabilities", {}).keys()),
                }
            )
        return results

    def get_connector(self, connector_id: str) -> Optional[Any]:
        """Return the connector registered under ``connector_id`` (or None)."""
        return self._connectors.get(connector_id)

    def research_profile_for(self, capability: str) -> Optional[str]:
        """Return the research path profile a capability is served by.

        Agents dispatch on this instead of hard-coding provider capability
        names. Resolution is preference-based and generic: connectors declaring
        a non-"generic" profile win, because they provide richer observations.

        Returns ``None`` when NO connector declares the capability, which is
        deliberately distinct from ``"generic"``. Conflating the two would let
        an unservable capability look like a servable one and turn "nothing can
        observe this" into a silent generic attempt.
        """
        connectors = [self._connectors[cid] for cid in self._capability_map.get(capability, [])]
        if not connectors:
            return None
        for conn in connectors:
            profile = getattr(conn, "research_profile", "generic")
            if profile and profile != "generic":
                return profile
        return "generic"

    def get_capability_connectors(self, capability: str) -> List[Any]:
        """Return all live connector objects declaring ``capability``."""
        return [
            self._connectors[cid]
            for cid in self._capability_map.get(capability, [])
            if cid in self._connectors
        ]

    def connector_ids(self) -> List[str]:
        """Return all registered connector ids."""
        return list(self._connectors.keys())

    def capabilities(self) -> List[str]:
        """Return all known capability names."""
        return list(self._capability_map.keys())

    # ------------------------------------------------------------------
    # Generic capability fabric (provider-independent)
    # ------------------------------------------------------------------

    def resolve_for_scope(self, scope: str) -> List[Dict[str, Any]]:
        """Which capabilities can consume THIS scope? (generic, no provider logic)

        Answers the question "what can I do with the scope I was given" from
        the capabilities' own ``scope_kind`` declarations plus their auth
        usability, without naming or importing any provider. Returns records
        ordered by the canonical resolution preference, so a caller that picks
        the first usable entry behaves identically to ``resolve_capability``.

        This is what lets a planner choose capabilities from a scope string
        instead of hardcoding one capability per task type.
        """
        try:
            from runtime.capability_fabric import (
                USABLE_AUTH_STATES as _USABLE,
                _RESOLUTION_PREFERENCE as _PREF,
                capability_accepts_scope as _accepts,
                classify_scope_kind as _classify,
            )
        except ImportError:  # pragma: no cover - top-level import style
            return []
        out: List[Dict[str, Any]] = []
        for capability in sorted(self._capability_map):
            for cid in self._capability_map[capability]:
                conn = self._connectors.get(cid)
                if conn is None:
                    continue
                if not _accepts(getattr(conn, "capabilities", {}).get(capability), scope):
                    continue
                state = str(getattr(conn, "auth_state", "UNKNOWN"))
                out.append({
                    "capability": capability,
                    "connector_id": cid,
                    "provider": str(getattr(conn, "provider", "unknown")),
                    "auth_state": state,
                    "auth_usable": state in _USABLE,
                    "scope_kind": _classify(scope),
                })
        order = {state: idx for idx, state in enumerate(_PREF)}
        out.sort(key=lambda r: (order.get(r["auth_state"], len(_PREF)), r["capability"]))
        return out

    def resolve_capability(self, capability: str) -> Any:
        """Select the authorized connector for ``capability``.

        Delegates to ``runtime.capability_fabric.resolve_connector`` and
        raises ``CapabilityResolutionError`` with a structured code
        (UNKNOWN_CAPABILITY | NO_CONNECTOR | NOT_AUTHORIZED) — never None,
        never a silent fallback.
        """
        from runtime.capability_fabric import resolve_connector as _resolve
        return _resolve(self, capability)

    def resolve_detailed(self, capability: str) -> Any:
        """Negotiate capability -> connector WITHOUT executing.

        Returns a ``CapabilityResolution`` describing the selected
        connector, why it was selected, alternatives considered, and
        authentication usability. Never raises for resolution outcomes.
        """
        from runtime.capability_fabric import resolve_capability_detailed as _detailed
        return _detailed(self, capability)

    def describe_capabilities(self) -> Dict[str, Any]:
        """Runtime discovery surface (STEP 11): capabilities, providers,
        connectors, auth/health/availability, resolution priority.

        Secret-safe: health payloads pass through the fabric sanitizer
        (secret keys stripped, credential shapes scrubbed) and no credential
        material is ever included. ``last_validated_at`` is reported when
        the connector tracks it, else "".
        """
        try:
            from runtime.capability_fabric import sanitize_data as _sanitize
        except ImportError:  # pragma: no cover
            try:
                from capability_fabric import sanitize_data as _sanitize
            except ImportError:
                _sanitize = lambda v: v  # noqa: E731
        from runtime.capability_fabric import (
            USABLE_AUTH_STATES as _USABLE,
            _RESOLUTION_PREFERENCE as _PREF,
        )
        surface: Dict[str, Any] = {}
        for capability in sorted(self._capability_map):
            connectors = []
            for cid in self._capability_map[capability]:
                conn = self._connectors[cid]
                try:
                    health = _sanitize(conn.health())
                except Exception as exc:
                    health = {"status": "ERROR",
                              "reason": f"health() raised {type(exc).__name__}"}
                state = str(getattr(conn, "auth_state", "UNKNOWN"))
                connectors.append({
                    "connector_id": cid,
                    "provider": str(getattr(conn, "provider", "unknown")),
                    "version": str(getattr(conn, "version", "")),
                    "auth_state": state,
                    "auth_usable": state in _USABLE,
                    "health": health if isinstance(health, dict) else {"status": "UNKNOWN"},
                    "last_validated_at": str(getattr(conn, "last_validated_at", "") or ""),
                    "capabilities": sorted(getattr(conn, "capabilities", {}).keys()),
                })
            # Resolution order mirrors the canonical preference tuple in
            # runtime.capability_fabric rather than re-deriving it here, so
            # discovery output and actual selection can never disagree.
            order = [c["connector_id"] for c in connectors
                     if c["auth_state"] == _PREF[0]]
            for _pref in _PREF[1:]:
                order += [c["connector_id"] for c in connectors
                          if c["auth_state"] == _pref]
            order += [c["connector_id"] for c in connectors
                      if c["connector_id"] not in order]
            surface[capability] = {
                "connectors": connectors,
                "resolution_order": order,
                "selectable": [c["connector_id"] for c in connectors
                               if c["auth_state"] in _USABLE],
            }
        return surface

    def request_capability(self, request: Any) -> Any:
        """Execute a ``CapabilityRequest`` -> ``CapabilityResponse``.

        Accepts a ``CapabilityRequest`` (or a legacy dict with
        capability/input/scope/task_id/agent_id keys). Agents should prefer
        this over ``get_connector(id).execute(...)``.
        """
        from runtime.capability_fabric import (
            CapabilityRequest as _Req,
            execute_capability as _exec,
        )
        if isinstance(request, dict):
            request = _Req(
                capability=request.get("capability", ""),
                input=dict(request.get("input", {}) or {}),
                scope=request.get("scope", ""),
                task_id=request.get("task_id", ""),
                agent_id=request.get("agent_id", ""),
            )
        return _exec(self, request)

    def health_snapshot(self) -> Dict[str, Any]:
        """Capability -> connector auth/health states (generic discovery)."""
        snapshot: Dict[str, Any] = {}
        for cap, ids in self._capability_map.items():
            states = []
            for cid in ids:
                conn = self._connectors[cid]
                try:
                    health = conn.health()
                except Exception as exc:
                    health = {
                        "status": "ERROR",
                        "reason": f"health() raised {type(exc).__name__}: {exc}",
                    }
                states.append(
                    {
                        "connector_id": cid,
                        "auth_state": getattr(conn, "auth_state", "UNKNOWN"),
                        "health": health,
                    }
                )
            snapshot[cap] = states
        return snapshot
