"""Phase 7 — Model/tool interface (request, validate, execute bounded tools).

The model may REQUEST a tool call using a structured format:

    {"tool": "filesystem.read", "arguments": {"path": "src/app.ts"}}

NEXUS validates the requested tool against the agent's capabilities, then the
existing BoundedAgentRuntime executes permitted filesystem operations.

Loop: MODEL -> tool request -> validate capability -> BoundedAgentRuntime ->
real observation -> tool result -> MODEL -> final result.

No arbitrary shell commands. Only executable capabilities in
runtime.tools.EXECUTABLE_CAPABILITIES may execute; everything else is refused
with an auditable BLOCKED receipt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import os

from runtime.model_router import ModelToolCall
from runtime.tools import (
    EXECUTABLE_CAPABILITIES,
    TOOL_SPECS,
    filesystem_read,
    git_diff,
    git_status,
    refuse_staged,
    resolve_workspace_scope,
)


# Arguments accepted per capability (bounded allowlist)
TOOL_ARGUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "filesystem.read": {
        "required": ["path"],
        "optional": ["max_chars"],
        "path_keys": ["path", "target", "file"],
    },
    "git.status": {
        "required": [],
        "optional": ["timeout_seconds"],
        "path_keys": [],
    },
    "git.diff": {
        "required": [],
        "optional": ["path", "max_chars", "timeout_seconds"],
        "path_keys": ["path", "target", "file"],
    },
}


def _extract_path(arguments: dict[str, Any]) -> str | None:
    for key in ("path", "target", "file", "target_resource", "filename"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@dataclass
class ToolValidation:
    allowed: bool
    reason: str
    capability: str = ""
    target: str = ""
    max_chars: int = 10000
    # Phase 8: known capability that is staged (declared for planning but not
    # executable). Staged calls are refused with an auditable BLOCKED receipt
    # (approval policy) rather than a bare REJECTED.
    staged: bool = False


@dataclass
class ToolExecutionRecord:
    capability: str
    target: str
    status: str  # EXECUTED | BLOCKED | REJECTED
    reality: str  # OBSERVED (always — refusal is itself observed)
    reason: str
    content_sha256: str | None = None
    content_size: int | None = None
    content_preview: str | None = None
    receipt_id: str | None = None
    observation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "target": self.target,
            "status": self.status,
            "reality": "OBSERVED",
            "reason": self.reason,
            "content_sha256": self.content_sha256,
            "content_size": self.content_size,
            "content_preview": (self.content_preview or "")[:500] if self.content_preview else None,
            "receipt_id": self.receipt_id,
        }


def validate_tool_call(
    call: ModelToolCall,
    *,
    agent_capabilities: list[str],
    agent_allowed_operations: list[str] | None = None,
    agent_prohibited_operations: list[str] | None = None,
    execution_environment: str = "LOCAL",
) -> ToolValidation:
    """Validate a model-requested tool call against agent capabilities.

    Rejects (never executes): unknown tools, tools outside the agent's
    declared capabilities, tools denied by the execution environment,
    malformed arguments, non-executable capabilities requested for
    execution, and prohibited operations.
    """
    from runtime.execution_environment import is_tool_allowed, max_chars_for, resolve_environment
    capability = (call.tool or "").strip()
    if not capability:
        return ToolValidation(allowed=False, reason="tool call has empty tool name", capability="", target="")
    if capability not in TOOL_SPECS:
        return ToolValidation(allowed=False, reason=f"unknown tool '{capability}'", capability=capability, target="")
    env = resolve_environment(execution_environment).value
    if not is_tool_allowed(env, capability):
        return ToolValidation(
            allowed=False,
            reason=f"tool '{capability}' denied by {env} execution environment policy",
            capability=capability,
            target=str((call.arguments or {}).get("path", (call.arguments or {}).get("target", ""))),
        )
    prohibited = set(agent_prohibited_operations or [])
    if capability in prohibited:
        return ToolValidation(allowed=False, reason=f"tool '{capability}' is prohibited for this agent", capability=capability, target="")
    # Capability enforcement: agent must declare the capability (or a wildcard)
    declared = set(agent_capabilities or [])
    allowed_ops = set(agent_allowed_operations or [])
    if capability not in declared and capability not in allowed_ops and "filesystem.read" not in declared:
        # Strict: model tools require explicit capability declaration.
        if capability not in declared:
            return ToolValidation(allowed=False, reason=f"tool '{capability}' not in agent capabilities {sorted(declared)}", capability=capability, target="")
    args = call.arguments or {}
    if capability == "filesystem.read":
        path = _extract_path(args)
        if not path:
            return ToolValidation(allowed=False, reason="filesystem.read requires a 'path' argument", capability=capability, target="")
        try:
            max_chars = int(args.get("max_chars", 10000))
        except (TypeError, ValueError):
            return ToolValidation(allowed=False, reason="filesystem.read 'max_chars' must be an integer", capability=capability, target=path)
        if max_chars <= 0 or max_chars > 100000:
            return ToolValidation(allowed=False, reason="filesystem.read 'max_chars' must be within 1..100000", capability=capability, target=path)
        if max_chars > max_chars_for(env):
            return ToolValidation(allowed=False, reason=f"filesystem.read 'max_chars' exceeds {env} ceiling of {max_chars_for(env)}", capability=capability, target=path)
        return ToolValidation(allowed=True, reason="validated", capability=capability, target=path, max_chars=max_chars)
    if capability in ("git.status", "git.diff"):
        path = _extract_path(args) or ""
        if capability == "git.diff" and args:
            unknown = set(args) - {"path", "target", "file", "max_chars", "timeout_seconds"}
            if unknown:
                return ToolValidation(allowed=False, reason=f"git.diff rejects unknown arguments: {sorted(unknown)}", capability=capability, target=path)
        if capability == "git.status" and args:
            unknown = set(args) - {"timeout_seconds"}
            if unknown:
                return ToolValidation(allowed=False, reason=f"git.status rejects unknown arguments: {sorted(unknown)}", capability=capability, target=path)
        try:
            max_chars = int(args.get("max_chars", 10000))
        except (TypeError, ValueError):
            return ToolValidation(allowed=False, reason=f"{capability} 'max_chars' must be an integer", capability=capability, target=path)
        if max_chars <= 0 or max_chars > 100000:
            return ToolValidation(allowed=False, reason=f"{capability} 'max_chars' must be within 1..100000", capability=capability, target=path)
        if max_chars > max_chars_for(env):
            return ToolValidation(allowed=False, reason=f"{capability} 'max_chars' exceeds {env} ceiling of {max_chars_for(env)}", capability=capability, target=path)
        return ToolValidation(allowed=True, reason="validated", capability=capability, target=path, max_chars=max_chars)
    # Staged (non-executable) capabilities are never executed via this path.
    # Writers additionally carry the explicit approval-policy refusal.
    from runtime.tools import requires_approval as _requires_approval
    if _requires_approval(capability):
        _reason = (
            f"capability '{capability}' denied: filesystem mutations require an explicit "
            f"capability grant and a human approval record (approval policy: deny-by-default; "
            f"refused without side effects)"
        )
    else:
        _reason = (
            f"capability '{capability}' is staged: declared for planning but not executable; "
            f"refused without side effects"
        )
    return ToolValidation(
        allowed=False,
        reason=_reason,
        capability=capability,
        target=str(args.get("path", args.get("target", ""))),
        staged=True,
    )


def execute_validated_tool(
    validation: ToolValidation,
    *,
    agent_id: str,
    workspace_root: str | None,
    is_cancelled: Any = None,
) -> ToolExecutionRecord:
    """Execute an already-validated tool call via the bounded runtime."""
    if is_cancelled is not None:
        try:
            if is_cancelled():
                return ToolExecutionRecord(
                    capability=validation.capability or "unknown",
                    target=validation.target,
                    status="BLOCKED",
                    reality="OBSERVED",
                    reason="cancelled before tool execution",
                )
        except Exception:
            pass
    if not validation.allowed:
        # Staged capabilities get an auditable BLOCKED refusal receipt
        # (explicit approval policy); everything else is REJECTED outright.
        if validation.staged:
            receipt = refuse_staged(agent_id=agent_id, capability=validation.capability,
                                    target_resource=validation.target)
            return ToolExecutionRecord(
                capability=validation.capability,
                target=validation.target,
                status="BLOCKED",
                reality="OBSERVED",
                reason=receipt.reason,
                receipt_id=receipt.receipt_id,
            )
        return ToolExecutionRecord(
            capability=validation.capability or "unknown",
            target=validation.target,
            status="REJECTED",
            reality="OBSERVED",
            reason=validation.reason,
        )
    if validation.capability not in EXECUTABLE_CAPABILITIES:
        receipt = refuse_staged(agent_id=agent_id, capability=validation.capability, target_resource=validation.target)
        return ToolExecutionRecord(
            capability=validation.capability,
            target=validation.target,
            status="BLOCKED",
            reality="OBSERVED",
            reason=receipt.reason,
            receipt_id=receipt.receipt_id,
        )
    # Bounded read-only tools — resolve workspace scope honestly (never a workflow ID)
    workspace = resolve_workspace_scope(workspace_root, None)
    if not workspace:
        return ToolExecutionRecord(
            capability=validation.capability,
            target=validation.target,
            status="BLOCKED",
            reality="OBSERVED",
            reason=f"no valid workspace root for bounded {validation.capability}; refusing rather than fabricating",
        )
    if validation.capability == "git.status":
        receipt, observation = git_status(agent_id=agent_id, workspace_root=workspace, is_cancelled=is_cancelled)
        if receipt.status != "EXECUTED" or observation is None:
            return ToolExecutionRecord(
                capability=validation.capability, target=validation.target,
                status="BLOCKED", reality="OBSERVED",
                reason=receipt.reason, receipt_id=receipt.receipt_id,
            )
        return ToolExecutionRecord(
            capability=validation.capability, target=validation.target,
            status="EXECUTED", reality="OBSERVED",
            reason="real bounded git status",
            content_sha256=receipt.content_sha256, content_size=receipt.content_size,
            content_preview=(observation.get("output", "") or "")[:1000],
            receipt_id=receipt.receipt_id, observation=observation,
        )
    if validation.capability == "git.diff":
        try:
            receipt, observation = git_diff(
                agent_id=agent_id, workspace_root=workspace,
                target_path=validation.target or None,
                max_chars=validation.max_chars, is_cancelled=is_cancelled)
        except ValueError as exc:
            return ToolExecutionRecord(
                capability=validation.capability, target=validation.target,
                status="REJECTED", reality="OBSERVED",
                reason=f"invalid tool arguments: {exc}",
            )
        if receipt.status != "EXECUTED" or observation is None:
            return ToolExecutionRecord(
                capability=validation.capability, target=validation.target,
                status="BLOCKED", reality="OBSERVED",
                reason=receipt.reason, receipt_id=receipt.receipt_id,
            )
        return ToolExecutionRecord(
            capability=validation.capability, target=validation.target,
            status="EXECUTED", reality="OBSERVED",
            reason="real bounded git diff",
            content_sha256=receipt.content_sha256, content_size=receipt.content_size,
            content_preview=(observation.get("output", "") or "")[:1000],
            receipt_id=receipt.receipt_id, observation=observation,
        )
    try:
        receipt, observation = filesystem_read(
            agent_id=agent_id,
            workspace_root=workspace,
            target_path=validation.target,
            max_chars=validation.max_chars,
        )
    except ValueError as exc:
        return ToolExecutionRecord(
            capability=validation.capability,
            target=validation.target,
            status="REJECTED",
            reality="OBSERVED",
            reason=f"invalid tool arguments: {exc}",
        )
    if receipt.status != "EXECUTED" or observation is None:
        return ToolExecutionRecord(
            capability=validation.capability,
            target=validation.target,
            status="BLOCKED",
            reality="OBSERVED",
            reason=receipt.reason,
            receipt_id=receipt.receipt_id,
        )
    return ToolExecutionRecord(
        capability=validation.capability,
        target=validation.target,
        status="EXECUTED",
        reality="OBSERVED",
        reason="real bounded filesystem read",
        content_sha256=receipt.content_sha256,
        content_size=receipt.content_size,
        content_preview=(observation.get("text", "") or "")[:1000],
        receipt_id=receipt.receipt_id,
        observation=observation,
    )


def execute_tool_call(
    call: ModelToolCall,
    *,
    agent_id: str,
    agent_capabilities: list[str],
    workspace_root: str | None,
    agent_allowed_operations: list[str] | None = None,
    agent_prohibited_operations: list[str] | None = None,
    is_cancelled: Any = None,
    execution_environment: str = "LOCAL",
) -> ToolExecutionRecord:
    """Validate + execute a model tool call in one step (audit-friendly)."""
    validation = validate_tool_call(
        call,
        agent_capabilities=agent_capabilities,
        agent_allowed_operations=agent_allowed_operations,
        agent_prohibited_operations=agent_prohibited_operations,
        execution_environment=execution_environment,
    )
    return execute_validated_tool(validation, agent_id=agent_id, workspace_root=workspace_root,
                                  is_cancelled=is_cancelled)


def default_tool_definitions_for(
    capabilities: list[str], execution_environment: str = "LOCAL",
) -> list[Any]:
    """Advertise only executable tools the agent is permitted to request.

    Tools denied by the execution environment are never advertised, so a
    model cannot even be tempted to request them (requests would be
    rejected at validation regardless).
    """
    from runtime.execution_environment import is_tool_allowed, resolve_environment
    from runtime.model_router import ToolDefinition
    env = resolve_environment(execution_environment).value
    defs: list[Any] = []
    declared = set(capabilities or [])
    if "filesystem.read" in declared:
        defs.append(ToolDefinition(
            name="filesystem.read",
            description="Read a file inside the bounded workspace root. Arguments: {path: string, max_chars?: int}.",
            parameters_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
            },
        ))
    if "git.status" in declared and is_tool_allowed(env, "git.status"):
        defs.append(ToolDefinition(
            name="git.status",
            description="Read-only git status of the workspace repository. No arguments.",
            parameters_schema={"type": "object", "properties": {}},
        ))
    if "git.diff" in declared and is_tool_allowed(env, "git.diff"):
        defs.append(ToolDefinition(
            name="git.diff",
            description="Read-only git diff of the workspace repository, optionally for one in-root path. Arguments: {path?: string, max_chars?: int}.",
            parameters_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
            },
        ))
    return defs
