"""NEXUS Truth Boundary — the INTENT / EXECUTION / OBSERVATION / VERIFICATION ladder.

One rule governs every claim in NEXUS::

    INTENT != EXECUTION != OBSERVATION != VERIFICATION

- ``INFERRED`` — a plan, proposal, or model utterance ("I will create X",
  "I created the file"). Establishes nothing about the world.
- ``EXECUTED`` — a connector ran and returned a response. Recorded in the
  receipt (who, what, how long, which input digest). Still not an
  observation of resulting state.
- ``OBSERVED`` — the resulting state was re-read independently of the
  execution claim (file exists with SHA-256 Y; process exited 0 with
  captured stdout Z). Issued only on evidence, never on intent.
- ``VERIFIED`` — the independent VerificationAgent confirmed the observed
  state matches the expected state. Issued ONLY by the VerificationAgent
  through deterministic checks. No model, no agent, no reporter may issue
  it; no reporter may upgrade INFERRED to OBSERVED.

Helpers here let agents and tests assert the boundary instead of
re-describing it. The enforcement lives in the fabric (status/reality
mapping), the connectors (evidence or honest failure), and the verifier
(independent checks + VERIFIED authority).
"""
from __future__ import annotations

from typing import Any


LADDER = ("INFERRED", "EXECUTED", "OBSERVED", "VERIFIED")

# Who may legitimately produce each rung. Anything else is fabrication.
ISSUERS = {
    "INFERRED": ("any-agent", "any-model", "planner", "reporter"),
    "EXECUTED": ("connector", "capability-fabric"),
    "OBSERVED": ("connector", "coding-agent", "research-agent", "bounded-runtime"),
    "VERIFIED": ("verification-agent",),
}


def rank(rung: str) -> int:
    """Position on the ladder; unknown rungs rank below everything."""
    try:
        return LADDER.index(str(rung or "").strip().upper())
    except ValueError:
        return -1


def is_upgrade(from_rung: str, to_rung: str) -> bool:
    """True when ``to_rung`` claims more than ``from_rung`` establishes."""
    return rank(to_rung) > rank(from_rung)


def assert_no_upgrade(from_rung: str, to_rung: str, *, who: str = "") -> None:
    """Raise when a claim climbs the ladder without new evidence.

    Reporters, models, and non-verifier agents must never upgrade a rung;
    only connectors (EXECUTED/OBSERVED via re-reads) and the verifier
    (VERIFIED via independent checks) move claims upward.
    """
    if is_upgrade(from_rung, to_rung):
        actor = f" by {who}" if who else ""
        raise ValueError(
            f"truth-boundary violation{actor}: {from_rung} -> {to_rung} "
            f"without new evidence (INTENT != EXECUTION != OBSERVATION != VERIFICATION)")


def check_claim(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """Audit one claim dict for ladder violations.

    ``claim`` carries ``asserts`` (the rung claimed), ``evidence`` (the rung
    actually established), and ``issuer``. Returns check dicts in the
    canonical ``{check, status, detail}`` shape.
    """
    asserts = str(claim.get("asserts", "") or "")
    evidence = str(claim.get("evidence", "") or "")
    issuer = str(claim.get("issuer", "") or "")
    checks: list[dict[str, Any]] = []

    def _add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    _add("claim_rung_known", rank(asserts) >= 0, f"asserts={asserts}")
    _add("evidence_rung_known", rank(evidence) >= 0, f"evidence={evidence}")
    if rank(asserts) >= 0 and rank(evidence) >= 0:
        _add("no_upgrade_without_evidence", not is_upgrade(evidence, asserts),
             f"evidence={evidence} asserts={asserts}" if not is_upgrade(evidence, asserts)
             else f"{evidence} upgraded to {asserts} without new evidence")
    if asserts.strip().upper() == "VERIFIED":
        allowed = issuer in ISSUERS["VERIFIED"]
        _add("verified_authority", allowed,
             f"issuer={issuer}" if allowed else f"issuer={issuer} may not issue VERIFIED")
    if asserts.strip().upper() == "OBSERVED":
        _add("observed_needs_executor", issuer in ISSUERS["OBSERVED"] or issuer in ISSUERS["EXECUTED"],
             f"issuer={issuer}")
    return checks
