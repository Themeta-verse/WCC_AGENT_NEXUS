"""NEXUS Capability Verifiers — generic verification boundary + provider adapters.

Architecture::

    GenericCapabilityVerifier        (canonical: capability/response/receipt/
                                      hash/provenance/scope/lineage — NO
                                      provider-specific fields)
          |
          +-- GitHubCapabilityVerifier (github full_name/id/scope/github://)
          +-- GitCapabilityVerifier    (git workspace/git:// observation)
          +-- future providers...

The production VerificationAgent MUST run the generic verifier first for any
artifact carrying a connector receipt; provider adapters add OPTIONAL
depth checks behind that boundary. Provider checks never replace generic
checks, and no provider check may upgrade a FAILED generic verdict.

Adapters are PLUGGABLE: register_verifier_adapter() adds new providers
without touching the agent or the generic verifier. The agent selects
adapters purely by artifact provenance markers.
"""
from __future__ import annotations

from typing import Any, Callable


# ---------------------------------------------------------------------------
# Pluggable provider-adapter registry (STEP 9).
# Each adapter declares provenance markers it handles and a verify function
# with the SAME signature as the built-in adapters. The VerificationAgent
# runs generic checks first, then every matching adapter — order between
# adapters follows registration order (built-ins first).
# ---------------------------------------------------------------------------

_VERIFIER_ADAPTERS: list[dict[str, Any]] = []


def register_verifier_adapter(*, name: str, markers: tuple[str, ...],
                              verify: Callable[..., list[dict[str, Any]]],
                              capability: str = "",
                              scope_from: str = "context") -> None:
    """Register a provider-specific verification adapter (test or product).

    ``markers`` are provenance substrings; ``verify`` must accept
    (research: dict, *, expected_scope: str, artifact_reality: str,
    is_synthesis: bool = False) and return check dicts. ``capability`` is
    the expected capability for the adapter's generic check ("" = derive
    from the artifact's first dotted provenance token). ``scope_from`` is
    "context" (verify against the execution scope) or "research" (verify
    against the observation's own scope). Names must be unique;
    re-registering a name replaces the adapter deterministically.
    """
    if scope_from not in ("context", "research"):
        raise ValueError(f"scope_from must be 'context' or 'research', got {scope_from!r}")
    unregister_verifier_adapter(name)
    _VERIFIER_ADAPTERS.append({"name": name, "markers": tuple(markers), "verify": verify,
                               "capability": capability, "scope_from": scope_from})


def unregister_verifier_adapter(name: str) -> bool:
    """Remove an adapter by name. Returns True when one was removed."""
    before = len(_VERIFIER_ADAPTERS)
    _VERIFIER_ADAPTERS[:] = [a for a in _VERIFIER_ADAPTERS if a["name"] != name]
    return len(_VERIFIER_ADAPTERS) != before


def verifier_adapters_for(provenance: list[str]) -> list[dict[str, Any]]:
    """Return adapters whose markers match the artifact provenance, in order."""
    prov = list(provenance or [])
    return [a for a in _VERIFIER_ADAPTERS
            if any(m in prov for m in a["markers"])]


def _get_capability_fabric():
    try:
        from runtime import capability_fabric as _cf
    except ImportError:  # pragma: no cover - top-level import style
        import capability_fabric as _cf  # type: ignore[no-redef]
    return _cf


def response_from_artifact(
    artifact_meta: dict[str, Any],
    content: dict[str, Any] | None,
) -> Any | None:
    """Reconstruct a CapabilityResponse from a persisted connector artifact.

    Returns None when the artifact carries no connector receipt (legacy /
    non-connector artifacts) — the caller then falls back to adapter-only
    checks instead of inventing a response.
    """
    cf = _get_capability_fabric()
    content = content if isinstance(content, dict) else {}
    research = content.get("research") if isinstance(content.get("research"), dict) else {}
    receipt = None
    for candidate in (
        (research or {}).get("receipt"),
        content.get("receipt"),
    ):
        if isinstance(candidate, dict) and candidate.get("receipt_id"):
            receipt = candidate
            break
    if receipt is None:
        return None
    capability = str(
        receipt.get("capability") or receipt.get("operation") or ""
    )
    status = cf.normalize_status(receipt.get("status"))
    observation = (
        (research or {}).get("observation")
        or (research or {}).get("github_metadata")
        or (research or {}).get("git_observation")
        or content.get("observation")
        or {}
    )
    data = observation if isinstance(observation, dict) else {"observation": observation}
    if status in ("SUCCESS", "PARTIAL") and not data:
        # Receipt claims success but no evidence survived — do not upgrade.
        status = "UNKNOWN"
    return cf.CapabilityResponse(
        status=status,
        capability=capability,
        connector_id=str(receipt.get("connector_id") or "unknown"),
        provider=str(receipt.get("provider") or "unknown"),
        reality=cf.response_reality(status),
        data=cf.sanitize_data(data),
        receipt=dict(receipt),
        provenance=list(artifact_meta.get("provenance") or []),
        error=None if status in ("SUCCESS", "PARTIAL", "BLOCKED") else str(receipt.get("error") or "connector artifact without success"),
    )


class GenericCapabilityVerifier:
    """Canonical verifier: NO provider-specific fields, ever."""

    @staticmethod
    def verify_artifact(
        artifact_meta: dict[str, Any],
        content: dict[str, Any] | None,
        *,
        expected_capability: str = "",
        expected_scope: str = "",
    ) -> list[dict[str, Any]] | None:
        """Run generic checks for a receipt-bearing artifact.

        Returns the check list, or None when the artifact has no connector
        receipt (caller uses adapter/legacy checks instead).
        """
        cf = _get_capability_fabric()
        response = response_from_artifact(artifact_meta, content)
        if response is None:
            return None
        return cf.verify_capability_response(
            response,
            expected_capability=expected_capability or response.capability,
            expected_scope=expected_scope,
            artifact_reality=str(artifact_meta.get("reality", "")),
            artifact_provenance=list(artifact_meta.get("provenance") or []),
            evidence_present=True,
        )


class GitHubCapabilityVerifier:
    """OPTIONAL GitHub depth checks — run only AFTER generic verification."""

    @staticmethod
    def verify(
        research: dict[str, Any],
        *,
        expected_scope: str,
        artifact_reality: str,
        is_synthesis: bool = False,
    ) -> list[dict[str, Any]]:
        """GitHub-specific evidence checks (moved intact from the agent).

        ``is_synthesis`` covers final_report-style artifacts. A synthesis is
        INFERRED by construction and never observed GitHub itself, so it may
        only claim that it *carries* upstream GitHub lineage. That claim is
        verified here, not assumed.
        """
        checks: list[dict[str, Any]] = []

        def _add(check: str, ok: bool, detail: str) -> None:
            checks.append({"check": check, "status": "PASS" if ok else "FAIL", "detail": detail})

        evidence = research.get("evidence", []) if isinstance(research, dict) else []
        obs = (research.get("github_metadata") or research.get("observation")) if isinstance(research, dict) else None
        has_observation_data = (
            isinstance(obs, dict) and bool(obs.get("full_name")) and bool(obs.get("id"))
        )

        if is_synthesis:
            # Previously this branch was:
            #   _add("github_reality_observed", True, "final_report synthesis
            #         carries github lineage ...")
            # which auto-passed EVERY final_report that reached this adapter,
            # regardless of whether it carried any GitHub lineage at all. A
            # report that synthesized from nothing verified clean.
            #
            # The honest check is whether the lineage is actually present. The
            # artifact's own reality stays INFERRED; only the *upstream*
            # observation is OBSERVED, and only when real observation data
            # exists.
            if has_observation_data:
                _add(
                    "github_synthesis_lineage_present", True,
                    f"final_report carries github lineage via observation "
                    f"{obs.get('full_name')!r} (id={obs.get('id')!r}); synthesis itself remains INFERRED",
                )
            else:
                _add(
                    "github_synthesis_lineage_present", False,
                    "final_report synthesis carries no github observation "
                    "(missing full_name/id); upstream lineage is UNKNOWN, so it cannot be verified",
                )
        elif artifact_reality == "OBSERVED":
            _add("github_reality_observed", True, "github artifact reality==OBSERVED")
        else:
            _add("github_reality_observed", False, f"github reality={artifact_reality} (expected OBSERVED)")
        if evidence:
            _add("github_evidence_present", True, f"{len(evidence)} github evidence item(s)")
        else:
            _add("github_evidence_present", False, "no github receipt/evidence")
        if has_observation_data:
            exp = (expected_scope or "").strip()
            if exp and str(obs.get("full_name", "")).lower() == exp.lower():
                _add("github_scope_match", True, f"scope={obs.get('full_name')}")
            else:
                _add("github_scope_match", False, f"scope mismatch: expected {exp!r}, observed {obs.get('full_name')!r}")
        else:
            _add("github_observation_data", False, "artifact lacks real GitHub observation data (full_name/id)")
        findings = research.get("findings", []) if isinstance(research, dict) else []
        if findings and any(str(f.get("file", "")).startswith("github://") for f in findings):
            _add("github_no_filesystem_fallback", True, "findings reference github:// observation")
        else:
            _add("github_no_filesystem_fallback", False, "findings do not prove a github:// observation (possible filesystem fallback)")
        return checks


class GitCapabilityVerifier:
    """OPTIONAL git depth checks — run only AFTER generic verification."""

    @staticmethod
    def verify(
        research: dict[str, Any],
        *,
        expected_scope: str,
        artifact_reality: str,
        is_synthesis: bool = False,
    ) -> list[dict[str, Any]]:
        """Git-specific evidence checks. ``is_synthesis`` is accepted for
        uniform adapter dispatch and ignored (git has no synthesis form)."""
        checks: list[dict[str, Any]] = []

        def _add(check: str, ok: bool, detail: str) -> None:
            checks.append({"check": check, "status": "PASS" if ok else "FAIL", "detail": detail})

        if artifact_reality == "OBSERVED":
            _add("git_reality_observed", True, "git artifact reality==OBSERVED")
        else:
            _add("git_reality_observed", False, f"git reality={artifact_reality} (expected OBSERVED)")
        evidence = research.get("evidence", []) if isinstance(research, dict) else []
        if evidence and any("git" in str(e.get("type", "")).lower() for e in evidence):
            _add("git_evidence_present", True, f"{len(evidence)} git evidence item(s)")
        else:
            _add("git_evidence_present", False, "no git receipt/evidence")
        findings = research.get("findings", []) if isinstance(research, dict) else []
        if findings and any(str(f.get("file", "")).startswith("git://") for f in findings):
            _add("git_no_filesystem_fallback", True, "findings reference git:// observation")
        else:
            _add("git_no_filesystem_fallback", False, "findings do not prove a git:// observation (possible filesystem fallback)")
        scope = str(research.get("scope", "")) if isinstance(research, dict) else ""
        if expected_scope and scope and scope == expected_scope:
            _add("git_scope_match", True, f"workspace={scope}")
        elif expected_scope and scope:
            _add("git_scope_match", False, f"workspace mismatch: expected {expected_scope!r}, observed {scope!r}")
        else:
            _add("git_scope_match", True, "no workspace expectation")
        return checks


# Built-in provider adapters (registered once at import; the agent never
# hardcodes provider branches — it dispatches via verifier_adapters_for).
register_verifier_adapter(
    name="github",
    markers=("github.repository.read", "github-connector"),
    verify=GitHubCapabilityVerifier.verify,
    capability="github.repository.read",
    scope_from="context",
)
register_verifier_adapter(
    name="git",
    markers=("git-connector", "git.status", "git.diff"),
    verify=GitCapabilityVerifier.verify,
    capability="",
    scope_from="research",
)
