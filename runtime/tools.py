"""NEXUS Tool Abstraction — capability-bounded tool layer (Phase 6).

Every tool declares:
- capability name (e.g. "filesystem.read")
- required permission
- scope model (what it may touch)
- input validation
- output contract
- reality classification
- audit record (ObservationReceipt for executed/blocked actions)

Only filesystem.read is executable, via BoundedAgentRuntime inside an
explicit workspace root. All other capabilities are STAGED: they are
declared so planners/registry can reason about them, but any execution
attempt is refused with an auditable BLOCKED receipt. No unrestricted
execution exists anywhere in this module.

A workflow ID must NEVER become a filesystem scope: resolve_workspace_scope()
rejects workflow-id-like values and non-existent paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

from runtime.bounded_agent import BoundedAgentRuntime, ObservationReceipt


WORKFLOW_ID_PREFIXES = ("workflow-", "wf-", "task-", "dyn-")

TOOL_CAPABILITIES: tuple[str, ...] = (
    "filesystem.read",
    "filesystem.write",
    "filesystem.create",
    "filesystem.patch",
    "git.status",
    "git.diff",
    "process.execute",
    "http.request",
    "repository.inspect",
)

# Only these capabilities are executable. Everything else is STAGED.
# git.status / git.diff are read-only inspections (fixed argv, no shell,
# bounded cwd, timeout) — safe to execute. Writes are never executed by the
# tool layer: they require an explicit human approval that this runtime does
# not auto-grant, so every write attempt is refused with an auditable receipt.
EXECUTABLE_CAPABILITIES = frozenset({"filesystem.read", "git.status", "git.diff"})

# Capabilities that mutate state. They are STAGED in this runtime: execution
# requires an explicit capability grant AND a human approval record, neither
# of which the autonomous loop can mint for itself. See requires_approval().
WRITE_CAPABILITIES = frozenset({"filesystem.write", "filesystem.create", "filesystem.patch"})

STAGED_REASON = (
    "capability is staged: declared for planning but not executable in this runtime; "
    "refused without side effects"
)

WRITE_APPROVAL_REASON = (
    "write capability denied: filesystem mutations require an explicit capability "
    "grant and a human approval record (approval policy: deny-by-default; "
    "autonomous loop cannot self-approve); refused without side effects"
)


def requires_approval(capability: str) -> bool:
    """True when a capability needs explicit human approval before execution.

    Read-only inspections never require approval; all write capabilities do.
    The tool layer has no path to mint approvals, so writes are always
    refused here — a model can never bypass the tool layer to write.
    """
    return capability in WRITE_CAPABILITIES


def is_workflow_id_scope(value: str | None) -> bool:
    """True when a scope value looks like a workflow/task identity, not a path."""
    if not value or not isinstance(value, str):
        return True
    text = value.strip()
    if not text:
        return True
    lowered = text.lower()
    return any(lowered.startswith(p) for p in WORKFLOW_ID_PREFIXES)


def resolve_workspace_scope(candidate: str | None, fallback: str | None = None) -> str | None:
    """Resolve an explicit workspace root, refusing workflow IDs and missing paths.

    Returns the absolute workspace path, or None when no legitimate
    filesystem scope was provided. Never raises for bad input — callers
    must honestly report zero observations instead.
    """
    for value in (candidate, fallback):
        if not value or not isinstance(value, str):
            continue
        text = value.strip()
        if not text or is_workflow_id_scope(text):
            continue
        try:
            resolved = Path(text).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if resolved.exists():
            return str(resolved)
    return None


@dataclass(frozen=True)
class ToolSpec:
    """Declaration for a single tool capability."""
    capability: str
    permission: str
    scope_model: str
    executable: bool
    reality: str
    description: str = ""


TOOL_SPECS: dict[str, ToolSpec] = {
    "filesystem.read": ToolSpec(
        capability="filesystem.read",
        permission="read-only; bounded to an explicit workspace root",
        scope_model="workspace_root: absolute existing directory; workflow/tenant/project IDs are identities, never paths",
        executable=True,
        reality="OBSERVED",
        description="Read files inside the workspace root via BoundedAgentRuntime.",
    ),
    "filesystem.write": ToolSpec(
        capability="filesystem.write",
        permission="denied in this runtime without explicit grant + human approval",
        scope_model="no writable scope is issued",
        executable=False,
        reality="OBSERVED",
        description="Staged: write attempts are observed-as-blocked, never executed. Approval policy: deny-by-default.",
    ),
    "filesystem.create": ToolSpec(
        capability="filesystem.create",
        permission="denied in this runtime without explicit grant + human approval",
        scope_model="no writable scope is issued",
        executable=False,
        reality="OBSERVED",
        description="Staged: file creation is observed-as-blocked, never executed. Approval policy: deny-by-default.",
    ),
    "filesystem.patch": ToolSpec(
        capability="filesystem.patch",
        permission="denied in this runtime without explicit grant + human approval",
        scope_model="no writable scope is issued",
        executable=False,
        reality="OBSERVED",
        description="Staged: patch application is observed-as-blocked, never executed. Approval policy: deny-by-default.",
    ),
    "git.status": ToolSpec(
        capability="git.status",
        permission="read-only; bounded to the workspace root git repository",
        scope_model="workspace_root: absolute existing directory inside a git work tree; fixed argv, no shell, timeout",
        executable=True,
        reality="OBSERVED",
        description="Read-only `git status --short --branch` via BoundedAgentRuntime. No arguments required.",
    ),
    "git.diff": ToolSpec(
        capability="git.diff",
        permission="read-only; bounded to the workspace root git repository",
        scope_model="workspace_root: absolute existing directory inside a git work tree; optional single in-root path; fixed argv, no shell, timeout",
        executable=True,
        reality="OBSERVED",
        description="Read-only `git diff --no-color` via BoundedAgentRuntime. Optional arguments: {path?, max_chars?}.",
    ),
    "process.execute": ToolSpec(
        capability="process.execute",
        permission="denied in this runtime",
        scope_model="no executable scope is issued",
        executable=False,
        reality="OBSERVED",
        description="Staged: process execution is refused without side effects.",
    ),
    "http.request": ToolSpec(
        capability="http.request",
        permission="denied in this runtime",
        scope_model="no network scope is issued",
        executable=False,
        reality="OBSERVED",
        description="Staged: network requests are refused without side effects.",
    ),
    "repository.inspect": ToolSpec(
        capability="repository.inspect",
        permission="read-only metadata derived from filesystem.read evidence",
        scope_model="same workspace_root as filesystem.read",
        executable=False,
        reality="INFERRED",
        description="Staged as a direct tool; repository insight comes from filesystem.read evidence.",
    ),
}


def describe_tools() -> list[dict[str, Any]]:
    """Queryable tool inventory for planners, UI, and future external observers."""
    return [
        {
            "capability": spec.capability,
            "permission": spec.permission,
            "scope_model": spec.scope_model,
            "executable": spec.executable,
            "approval": "required" if requires_approval(spec.capability) else "not-applicable",
            "reality": spec.reality,
            "description": spec.description,
        }
        for spec in TOOL_SPECS.values()
    ]


def filesystem_read(
    *,
    agent_id: str,
    workspace_root: str,
    target_path: str,
    max_chars: int = 10000,
) -> tuple[ObservationReceipt, dict[str, Any] | None]:
    """Execute a bounded filesystem read with full audit.

    Returns (receipt, observation). Observation is None when blocked.
    Raises ValueError for invalid input (never touches the filesystem
    in that case); refuses non-executable misuse via receipt, not exception.
    """
    if not target_path or not isinstance(target_path, str):
        raise ValueError("target_path must be a non-empty string")
    if max_chars <= 0 or max_chars > 100000:
        raise ValueError("max_chars must be within 1..100000")
    runtime = BoundedAgentRuntime(
        agent_id=agent_id,
        allowed_root=workspace_root,
        capabilities=["filesystem.read"],
        prohibited_operations=["filesystem.write", "git.push"],
    )
    receipt = runtime.execute_action(
        operation="filesystem.read",
        target_resource=target_path,
        parameters={"max_chars": max_chars, "requested_capability": "filesystem.read"},
    )
    if receipt.status != "EXECUTED":
        return receipt, None
    # Re-read the exact resolved file the runtime observed (never the raw
    # caller string, which may be relative to another working directory).
    resolved = BoundedAgentRuntime(agent_id=agent_id, allowed_root=workspace_root)._resolve_within_root(target_path)
    if resolved is None:
        return receipt, None
    try:
        data = resolved.read_bytes()
    except OSError:
        return receipt, None
    text = data[:max_chars].decode("utf-8", "replace")
    return receipt, {
        "path": str(resolved),
        "size": len(data),
        "sha256": receipt.content_sha256,
        "text": text,
        "reality": "OBSERVED",
        "untrusted_content": True,
    }


def git_status(
    *,
    agent_id: str,
    workspace_root: str,
    timeout_seconds: int = 15,
    is_cancelled: Any = None,
) -> tuple[ObservationReceipt, dict[str, Any] | None]:
    """Execute a bounded read-only `git status` with full audit.

    Returns (receipt, observation). Observation is None when blocked.
    Never raises for git/environment problems — callers must honestly
    report zero observations instead.
    """
    runtime = BoundedAgentRuntime(
        agent_id=agent_id,
        allowed_root=workspace_root,
        capabilities=["git.status"],
        prohibited_operations=["filesystem.write", "git.push"],
    )
    receipt = runtime.execute_action(
        operation="git.status",
        target_resource=workspace_root,
        parameters={"timeout_seconds": timeout_seconds, "requested_capability": "git.status"},
        is_cancelled=is_cancelled,
    )
    if receipt.status != "EXECUTED":
        return receipt, None
    return receipt, {
        "command": "git status --short --branch",
        "output": (receipt.content_preview or ""),
        "sha256": receipt.content_sha256,
        "reality": "OBSERVED",
        "untrusted_content": True,
    }


def git_diff(
    *,
    agent_id: str,
    workspace_root: str,
    target_path: str | None = None,
    max_chars: int = 10000,
    timeout_seconds: int = 15,
    is_cancelled: Any = None,
) -> tuple[ObservationReceipt, dict[str, Any] | None]:
    """Execute a bounded read-only `git diff` with full audit.

    Returns (receipt, observation). Observation is None when blocked.
    Raises ValueError for invalid input (never touches git in that case).
    """
    if max_chars <= 0 or max_chars > 100000:
        raise ValueError("max_chars must be within 1..100000")
    runtime = BoundedAgentRuntime(
        agent_id=agent_id,
        allowed_root=workspace_root,
        capabilities=["git.diff"],
        prohibited_operations=["filesystem.write", "git.push"],
    )
    receipt = runtime.execute_action(
        operation="git.diff",
        target_resource=target_path or workspace_root,
        parameters={"max_chars": max_chars, "timeout_seconds": timeout_seconds,
                    "requested_capability": "git.diff"},
        is_cancelled=is_cancelled,
    )
    if receipt.status != "EXECUTED":
        return receipt, None
    return receipt, {
        "command": "git diff --no-color",
        "path": target_path or "",
        "output": (receipt.content_preview or ""),
        "sha256": receipt.content_sha256,
        "reality": "OBSERVED",
        "untrusted_content": True,
    }


def refuse_staged(
    *,
    agent_id: str,
    capability: str,
    target_resource: str = "",
) -> ObservationReceipt:
    """Produce an auditable BLOCKED receipt for a staged (non-executable) tool."""
    from datetime import datetime, timezone
    from hashlib import sha256
    import json

    now = datetime.now(timezone.utc).isoformat()
    reason = WRITE_APPROVAL_REASON if requires_approval(capability) else STAGED_REASON
    receipt_id = f"receipt-{capability}-staged-{sha256(target_resource.encode()).hexdigest()[:12]}"
    evidence_digest = sha256(json.dumps(
        {"receipt_id": receipt_id, "capability": capability, "status": "BLOCKED"},
        sort_keys=True,
    ).encode()).hexdigest()
    return ObservationReceipt(
        receipt_id=receipt_id,
        agent_id=agent_id,
        operation=capability,
        target_resource=target_resource,
        requested_capability=capability,
        execution_mode="STAGED",
        start_time=now,
        end_time=now,
        status="BLOCKED",
        reality="OBSERVED",
        reason=reason,
        evidence_digest=evidence_digest,
        provenance=["nexus-tool-layer", "staged-capability-refusal"],
    )
