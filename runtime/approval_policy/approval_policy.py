"""NEXUS Approval Policy — the human-approval boundary as a capability-fabric policy.

``WorkspaceApprovalPolicy`` plugs ``runtime.workspace_auth.classify_consequential``
into the canonical fabric gate (``registry.policy``). The fabric consults it
BEFORE any connector executes:

- ALLOW            -> the connector executes normally
- DENY             -> honest BLOCKED refusal, connector never touched
- REQUIRES_APPROVAL -> honest BLOCKED refusal, connector never touched,
  with an explicit approval-required reason the operator/runtime can act on

Policy decisions are pure and side-effect free: path resolution only, no
filesystem mutation, no execution. Approval *granting* lives in the
AutonomousRuntime approval flow (request_approval / handle_approval_decision);
this policy only states what needs approval. It never escalates silently:
anything it does not explicitly ALLOW is refused.
"""
from __future__ import annotations

from typing import Any


def _fabric_policy_base():
    try:
        from runtime.capability_fabric import CapabilityPolicy, PolicyDecision
    except ImportError:  # pragma: no cover - top-level import style
        from capability_fabric import CapabilityPolicy, PolicyDecision  # type: ignore[no-redef]
    return CapabilityPolicy, PolicyDecision


_CapabilityPolicy, _PolicyDecision = _fabric_policy_base()


class WorkspaceApprovalPolicy(_CapabilityPolicy):
    """Fabric policy enforcing workspace authorization + approval boundaries.

    ``approval_source`` is an optional callable consulted ONLY on the
    REQUIRES_APPROVAL branch::

        approval_source(capability=..., workspace=..., target=..., argv=...,
                        task_id=...) -> approval_id (str) | None

    A returned approval id upgrades that single decision to ALLOW with the
    grant recorded in the reason. DENY is never overridden; containment and
    protected-path checks always run first. The default (None) preserves the
    hold-for-approval behavior. Grant lookups are installed per task
    execution by the executor from DB-verified approval rows (bound to
    workflow + task + exact operation), and removed afterwards.
    """

    policy_id: str = "workspace-approval-v1"

    def __init__(self, approval_source: Any = None) -> None:
        self.approval_source = approval_source

    def decide(self, *, request: Any, connector: Any) -> Any:
        from runtime.workspace_auth import (
            CONSEQUENTIAL_CAPABILITIES,
            classify_consequential,
        )

        capability = str(getattr(request, "capability", "") or "")
        if capability not in CONSEQUENTIAL_CAPABILITIES:
            # Reads, git inspection, and any non-consequential capability:
            # no approval boundary applies.
            return _PolicyDecision(
                decision="ALLOW",
                reason="non-consequential capability; no approval boundary applies",
                policy_id=self.policy_id,
            )
        raw_input = getattr(request, "input", None) or {}
        if not isinstance(raw_input, dict):
            raw_input = {}
        workspace = str(
            raw_input.get("workspace", "") or getattr(request, "scope", "") or ""
        )
        target = str(
            raw_input.get("path", "") or raw_input.get("target", "") or ""
        )
        argv = raw_input.get("argv", None)
        if argv is not None and not isinstance(argv, list):
            argv = None
        norm_argv = list(argv) if argv is not None else None
        decision, reason = classify_consequential(
            capability=capability,
            workspace=workspace,
            target=target,
            argv=norm_argv,
        )
        if decision == "REQUIRES_APPROVAL" and self.approval_source is not None:
            task_id = getattr(request, "task_id", None)
            try:
                grant = self.approval_source(
                    capability=capability, workspace=workspace,
                    target=target, argv=norm_argv, task_id=task_id)
            except Exception:
                grant = None
            if isinstance(grant, str) and grant.strip():
                return _PolicyDecision(
                    decision="ALLOW",
                    reason=(f"granted by human approval {grant.strip()}: {reason}"),
                    policy_id=self.policy_id,
                )
        return _PolicyDecision(
            decision=decision,
            reason=reason,
            policy_id=self.policy_id,
        )
