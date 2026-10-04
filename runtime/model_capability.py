"""NEXUS Model → Capability boundary (Phase 15).

ONE precise architectural jump: the model may REQUEST capabilities, but ONLY
the canonical fabric may EXECUTE them.

    MODEL (untrusted reasoning, INFERRED only)
      |
      | ModelCapabilityRequest (typed: capability/target/parameters/intent)
      v
    ModelCapabilityGate  (schema, allowlist, scope, secrets, identity binding)
      |
      | CapabilityRequest (runtime-bound: workflow/task/agent/tenant/project/
      |                  principal/scope + model provenance)
      v
    CapabilityFabric (negotiation -> policy -> connector -> receipt)

Hard rules enforced here:
- The model supplies ONLY capability / target / parameters / intent.
- Workflow/task/agent/tenant/project/principal/scope/model-id come from the
  RUNTIME context. Model-supplied identity, reality claims, connector names,
  providers, receipts, credentials, or shell material are REJECTED, never
  coerced or ignored-silently (rejection is explicit INVALID_REQUEST).
- The gate NEVER executes, NEVER touches connectors/registries/tokens, and
  NEVER labels anything OBSERVED or VERIFIED.
- Default is deny-all: without an explicit allowlist nothing is routable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import os

from runtime.capability_fabric import (
    _CREDENTIAL_VALUE_RES,
    CAPABILITY_RE,
    CapabilityRequest,
    CapabilityResolutionError,
    CapabilityResponse,
    emit_capability_event,
    execute_capability,
    scrub_error_text,
)


# Top-level model payload keys that are NEVER accepted. They attempt to
# smuggle execution authority (connector/provider), reality verdicts,
# identity (tenant/principal), or credentials past the boundary.
_FORBIDDEN_MODEL_KEYS = frozenset({
    "reality", "verified", "verification", "receipt", "provenance",
    "connector", "connector_id", "provider", "token", "principal",
    "tenant_id", "project_id", "authorization", "auth", "password",
    "secret", "api_key", "observed", "execute", "shell", "command",
    "credential", "credentials", "private_key",
})

# Parameter (argument-name) substrings that are never accepted.
_FORBIDDEN_PARAM_NAMES = ("token", "secret", "password", "api_key", "private_key",
                          "credential", "authorization", "auth_token", "clone_token")

_SHELL_METACHARS = (";", "|", "&", "$", "`", "(", ")", '"', "'", "\n", "\r", "\0")

_MAX_TARGET_LEN = 512
_MAX_PARAM_BYTES = 65536
_MAX_INTENT_LEN = 2000


@dataclass
class ModelCapabilityRequest:
    """What the MODEL may supply. Everything here is UNTRUSTED input."""
    capability: str = ""
    target: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    intent: str = ""
    request_id: str = ""  # optional; gate mints one when absent


@dataclass
class ModelRuntimeContext:
    """What the RUNTIME supplies. Trusted: built by the executor/operator,
    never by the model."""
    workflow_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    model_id: str = ""
    tenant_id: str = ""
    project_id: str = ""
    scope: str = ""  # allowed scope the model may not expand
    principal: dict[str, Any] = field(default_factory=dict)
    allow_cross_scope: bool = False

    @classmethod
    def from_agent_context(cls, context: Any, *, model_id: str) -> "ModelRuntimeContext":
        """Bridge from an executor-built AgentContext (trusted carrier)."""
        scope = getattr(context, "observation_scope", None) or getattr(context, "scope", "") or ""
        principal = getattr(context, "principal", None) or {}
        return cls(
            workflow_id=getattr(context, "workflow_id", "") or "",
            task_id=getattr(context, "task_id", "") or "",
            agent_id=getattr(context, "agent_id", "") or "",
            model_id=model_id,
            tenant_id=getattr(context, "tenant_id", "") or "",
            project_id=getattr(context, "project_id", "") or "",
            scope=scope,
            principal=dict(principal) if isinstance(principal, dict) else {},
        )


def _reject(capability: str, detail: str) -> CapabilityResolutionError:
    return CapabilityResolutionError("INVALID_REQUEST", capability or "", detail)


def _contains_credential_shape(text: str) -> bool:
    return any(rx.search(text) for rx in _CREDENTIAL_VALUE_RES)


def _check_target(target: Any) -> str:
    """Validate a model-supplied target (provider-neutral)."""
    if not isinstance(target, str) or not target.strip():
        raise _reject("", "model target must be a non-empty string")
    text = target.strip()
    if len(text) > _MAX_TARGET_LEN:
        raise _reject("", f"model target exceeds {_MAX_TARGET_LEN} chars")
    if any(ord(c) < 32 for c in text):
        raise _reject("", "model target contains control characters")
    if text.startswith("~"):
        raise _reject("", "model target must not use home-directory expansion")
    if any(m in text for m in _SHELL_METACHARS):
        raise _reject("", "model target contains shell metacharacters")
    segments = [seg for seg in text.replace("\\", "/").split("/") if seg]
    if ".." in segments:
        raise _reject("", "model target contains path traversal (..)")
    if _contains_credential_shape(text):
        raise _reject("", "model target contains credential-shaped material")
    # Workflow/task identities are not filesystem roots or external targets.
    lowered = text.lower()
    if lowered.startswith(("workflow-", "wf-", "task-", "dyn-")) and "/" not in text:
        raise _reject("", "model target must not be a bare workflow/task identity")
    return text


def _target_within_scope(target: str, scope: str) -> bool:
    """Provider-neutral scope containment (exact, path, or owner/repo prefix)."""
    if target == scope:
        return True
    if os.path.isabs(target) and os.path.isabs(scope):
        try:
            return os.path.commonpath(
                [os.path.normpath(target), os.path.normpath(scope)]
            ) == os.path.normpath(scope)
        except ValueError:
            return False
    if "/" in scope and target.startswith(scope.rstrip("/") + "/"):
        return True
    return False


class ModelCapabilityGate:
    """Narrow boundary between model reasoning and capability execution.

    The gate validates, never executes. It holds no registry, no connector,
    no credential — only an explicit capability allowlist and scope rules.
    Default is deny-all: ``allowed_capabilities`` must list routable
    capabilities or every request is rejected with an explicit reason.
    """

    gate_id: str = "model-capability-gate"

    def __init__(self, *, allowed_capabilities: list[str] | None = None) -> None:
        self.allowed_capabilities = [c for c in (allowed_capabilities or []) if c]

    def _require_context(self, ctx: ModelRuntimeContext) -> None:
        missing = [f for f in ("workflow_id", "task_id", "agent_id", "model_id",
                               "tenant_id", "project_id", "scope")
                   if not getattr(ctx, f, "")]
        if missing:
            raise _reject("", f"runtime context incomplete, missing: {missing}")
        if not isinstance(ctx.principal, dict):
            raise _reject("", "runtime principal must be a dict")

    def build_request(self, model_request: ModelCapabilityRequest | dict[str, Any],
                      ctx: ModelRuntimeContext) -> CapabilityRequest:
        """Validate a model request and bind runtime identity into a fabric
        CapabilityRequest. Raises CapabilityResolutionError (INVALID_REQUEST)
        with an explicit reason on ANY violation. Never executes."""
        self._require_context(ctx)
        raw: dict[str, Any] = (
            dict(model_request) if isinstance(model_request, dict)
            else {"capability": model_request.capability, "target": model_request.target,
                  "parameters": model_request.parameters, "intent": model_request.intent,
                  "request_id": model_request.request_id}
            if isinstance(model_request, ModelCapabilityRequest)
            else {}
        )
        if not isinstance(model_request, (dict, ModelCapabilityRequest)):
            raise _reject("", "model request must be a ModelCapabilityRequest or dict")
        # 1. Forbidden top-level keys: authority/reality/identity smuggling.
        smuggled = sorted(k for k in raw if k in _FORBIDDEN_MODEL_KEYS)
        if smuggled:
            raise _reject(str(raw.get("capability", "")),
                          f"model request carries forbidden keys: {smuggled}")
        # 2. Capability: well-formed AND explicitly allowlisted.
        capability = raw.get("capability", "")
        if not isinstance(capability, str) or not capability.strip():
            raise _reject("", "model request missing capability")
        capability = capability.strip()
        if not CAPABILITY_RE.fullmatch(capability):
            raise _reject(capability, "model capability is malformed")
        if capability not in self.allowed_capabilities:
            raise _reject(capability, f"capability not in model allowlist {self.allowed_capabilities}")
        # 3. Target: provider-neutral safety validation.
        target = _check_target(raw.get("target", ""))
        # 4. Scope binding: the model cannot expand scope.
        if not _target_within_scope(target, ctx.scope) and not ctx.allow_cross_scope:
            raise _reject(capability, f"model target {target!r} escapes runtime scope {ctx.scope!r}")
        # 5. Intent: required, bounded, secret-free.
        intent = raw.get("intent", "")
        if not isinstance(intent, str) or not intent.strip():
            raise _reject(capability, "model request missing intent")
        if len(intent) > _MAX_INTENT_LEN:
            raise _reject(capability, f"model intent exceeds {_MAX_INTENT_LEN} chars")
        if _contains_credential_shape(intent):
            raise _reject(capability, "model intent contains credential-shaped material")
        # 6. Parameters: dict, bounded, no secret names or shapes.
        parameters = raw.get("parameters", {})
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise _reject(capability, "model parameters must be a dict")
        import json as _json
        if len(_json.dumps(parameters, sort_keys=True, default=str)) > _MAX_PARAM_BYTES:
            raise _reject(capability, f"model parameters exceed {_MAX_PARAM_BYTES} bytes")
        for key, value in parameters.items():
            lowered = str(key).lower()
            if any(bad in lowered for bad in _FORBIDDEN_PARAM_NAMES):
                raise _reject(capability, f"model parameter name forbidden: {key!r}")
            if isinstance(value, str) and value and _contains_credential_shape(value):
                raise _reject(capability, f"model parameter {key!r} contains credential-shaped material")
        request_id = raw.get("request_id", "") or ""
        if not isinstance(request_id, str):
            raise _reject(capability, "model request_id must be a string")
        request_id = request_id.strip() or f"mreq-{ctx.model_id}-{ctx.task_id}"
        principal = dict(ctx.principal)
        principal.setdefault("tenant_id", ctx.tenant_id)
        principal.setdefault("project_id", ctx.project_id)
        return CapabilityRequest(
            capability=capability,
            input={"target": target, **{k: v for k, v in parameters.items() if k != "target"}},
            scope=ctx.scope,
            task_id=ctx.task_id,
            agent_id=ctx.agent_id,
            principal=principal,
            constraints={},
            requested_by_model=ctx.model_id,
            model_request_id=request_id,
        )


def _scrubbed_event_base(ctx: ModelRuntimeContext, model_request_id: str,
                         capability: str, target: str) -> dict[str, Any]:
    return {
        "model_id": ctx.model_id,
        "model_request_id": model_request_id,
        "capability": capability,
        "target": scrub_error_text(target),
        "workflow_id": ctx.workflow_id,
        "task_id": ctx.task_id,
        "agent_id": ctx.agent_id,
        "tenant_id": ctx.tenant_id,
        "project_id": ctx.project_id,
    }


def model_request_capability(registry: Any,
                             model_request: ModelCapabilityRequest | dict[str, Any],
                             ctx: ModelRuntimeContext,
                             *, gate: ModelCapabilityGate | None = None,
                             ) -> tuple[dict[str, Any], CapabilityResponse]:
    """Route a model request through gate -> fabric and return a SANITIZED
    summary plus the full fabric response.

    The summary exposes ONLY: status, capability, target, reality,
    receipt_id, attempt, connector/provider identity, model lineage, error.
    Never credentials, connector internals, raw secrets, or auth details.
    ``verified`` is always False here: verification happens downstream in
    the verifier, never in the gate or the model.
    """
    gate = gate or ModelCapabilityGate()
    try:
        request = gate.build_request(model_request, ctx)
    except CapabilityResolutionError as exc:
        emit_capability_event(registry, "model_request_rejected", {
            **_scrubbed_event_base(
                ctx,
                (model_request.get("request_id", "")
                 if isinstance(model_request, dict) else getattr(model_request, "request_id", "")) or "",
                (model_request.get("capability", "")
                 if isinstance(model_request, dict) else getattr(model_request, "capability", "")) or "",
                (model_request.get("target", "")
                 if isinstance(model_request, dict) else getattr(model_request, "target", "")) or "",
            ),
            "reason": scrub_error_text(str(exc)),
        })
        raise
    emit_capability_event(registry, "model_request", {
        **_scrubbed_event_base(ctx, request.model_request_id, request.capability,
                               request.input.get("target", "")),
        "allowlisted": True,
    })
    response = execute_capability(registry, request)
    summary = {
        "status": response.status,
        "capability": response.capability,
        "target": response.receipt.get("target", ""),
        "reality": response.reality,
        "receipt_id": response.receipt.get("receipt_id"),
        "attempt": response.receipt.get("attempt", 1),
        "connector_id": response.connector_id,
        "provider": response.provider,
        "model_request_id": request.model_request_id,
        "verified": False,
        "verification_note": "not verified: run the independent verifier on the artifact",
        "error": response.error,
    }
    return summary, response


# The model tool surface (req 6): builds a STRUCTURED request only. It does
# not execute anything itself; routing happens in model_request_capability.
# Operators expose this to models explicitly — it is NOT auto-registered in
# the model tool dispatch (no silent production-surface expansion).
def request_capability(capability: str, target: str,
                       parameters: dict[str, Any] | None = None,
                       intent: str = "") -> ModelCapabilityRequest:
    """Model-callable constructor: describe a capability need as a typed
    request. The runtime gate validates and routes it; this function alone
    performs no execution, no validation, and no observation."""
    return ModelCapabilityRequest(
        capability=capability or "",
        target=target or "",
        parameters=dict(parameters or {}),
        intent=intent or "",
    )
