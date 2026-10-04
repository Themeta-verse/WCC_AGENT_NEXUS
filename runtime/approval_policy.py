"""Approval policy for NEXUS consequential operations.

This module implements the approval gate that sits between agent proposals
and actual execution. Consequential operations require explicit human approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from runtime.workspace_auth import classify_consequential


@dataclass
class PolicyDecision:
    decision: Literal["ALLOW", "DENY", "REQUIRES_APPROVAL"]
    reason: str
    policy_id: str = "workspace-approval-v1"


class WorkspaceApprovalPolicy:
    """Policy that enforces approval for consequential operations.

    The approval_source callback is called when a consequential operation
    requires approval. It should return the approval ID if approved,
    or None/empty if not approved.
    """

    policy_id: str = "workspace-approval-v1"

    def __init__(self, approval_source: Any = None) -> None:
        self.approval_source = approval_source

    def decide(self, *, request: Any, connector: Any) -> PolicyDecision:
        from runtime.workspace_auth import classify_consequential

        capability = str(getattr(request, "capability", "") or "")
        if capability not in CONSEQUENTIAL_CAPABILITIES:
            # Reads, git inspection, and any non-consequential capability:
            # no approval boundary applies.
            return PolicyDecision(
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
            try:
                task_id = getattr(request, "task_id", None)
                grant = self.approval_source(
                    capability=capability,
                    workspace=workspace,
                    target=target,
                    argv=norm_argv,
                    task_id=task_id,
                )
            except Exception:
                grant = None

            if isinstance(grant, str) and grant.strip():
                return PolicyDecision(
                    decision="ALLOW",
                    reason=f"granted by human approval {grant.strip()}: {reason}",
                    policy_id=self.policy_id,
                )

        return PolicyDecision(
            decision=decision,
            reason=reason,
            policy_id=self.policy_id,
        )


# Re-export for convenience
from runtime.workspace_auth import CONSEQUENTIAL_CAPABILITIES  # noqa: F401