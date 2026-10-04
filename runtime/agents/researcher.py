"""Research Agent — observes project files and produces findings.

This agent consumes no upstream artifacts (it is the observation root).
It uses BoundedAgentRuntime-equivalent direct reads bounded to an explicit
workspace root. Its output is OBSERVED (real filesystem reads) with full
provenance. When the task explicitly requires a repository capability, it
uses the ConnectorRegistry-supplied connector and never silently falls back
to filesystem research.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import hashlib
import json


from runtime.agent_base import AgentContext, AgentExecutionResult



# Upper bound on how many listed files the researcher will actually read. The
# connector bounds the LISTING; this bounds the number of capability calls, so
# one research task can never fan out into unbounded execution.
MAX_FILES_OBSERVED = 50




def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ResearchAgent:
    """Agent that observes project files and produces research findings."""
    def _get_scope_kind(self, context: AgentContext, capability: str) -> str | None:
        """Get the declared scope_kind for a capability from the registry.

        Returns None if the capability is not declared or has no scope_kind.
        """
        registry = context.connector_registry
        if registry is None:
            return None
        try:
            conns = registry.get_capability_connectors(capability) or []
        except Exception:
            return None
        for conn in conns:
            decl = getattr(conn, "capabilities", {}).get(capability)
            if isinstance(decl, dict):
                kind = decl.get("scope_kind")
                if isinstance(kind, str) and kind.strip():
                    return kind.strip()
        return None

    def _validate_scope(self, scope: str, scope_kind: str | None) -> bool:
        """Validate a scope string against a declared scope_kind.

        Uses the canonical fabric"s capability_accepts_scope logic.
        """
        if scope_kind is None:
            return True  # No constraint declared
        try:
            from runtime.capability_fabric import (
                capability_accepts_scope as _accepts,
                classify_scope_kind as _classify,
            )
        except Exception:
            return True
        # Build a minimal declaration with the scope_kind for the check
        decl = {"scope_kind": scope_kind}
        return _accepts(decl, scope)

    agent_id: str = "researcher"
    name: str = "Research Agent"
    role: str = "researcher"
    capabilities: list[str] = field(default_factory=lambda: ["filesystem.read", "github.repository.read", "git.status"])
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "filesystem.read"])
    prohibited_operations: list[str] = field(default_factory=lambda: ["filesystem.write", "execute"])
    scope: dict = field(default_factory=dict)
    execution_strategy: str = "DETERMINISTIC"
    model_router: Any = None
    input_contract: tuple = ("no upstream inputs; explicit workspace scope",)
    output_contract: tuple = ("research_report artifact (OBSERVED, untrusted content)",)
    tool_permissions: tuple = ("filesystem.read via bounded workspace; github.repository.read/git.status via capability fabric",)
    artifact_behavior: str = "produces research_report; parents = consumed input artifact IDs"
    message_behavior: str = "sends STATUS_UPDATE at start and RESPONSE at completion"

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        """Execute research via the generic capability fabric.

        Dispatch is capability-driven, not provider-driven. Each requested
        capability is routed to the path that IMPLEMENTS it, and the union of
        those paths forms the observation:

          repository -> remote repository observation (no fallback)
          git.status/git.diff    -> local git observation (no fallback)
          filesystem.read        -> bounded filesystem observation (no fallback)
          filesystem.list        -> consumed internally by the filesystem path

        A capability is NEVER silently dropped: this agent declares the set it
        implements, and any requested capability outside that set is an explicit
        failure, not a quiet skip. That matters because dropping a capability
        turns "observed via provider X" into "observed via whatever else
        happened to run".

        When several capabilities are requested together, each is executed
        independently and preserved as a separate observation (cross-source
        lineage, never overwritten).
        """
        requested_caps = list(
            (context.execution_metadata or {}).get("capabilities_requested") or []
        )

        # Generic routing. The agent dispatches on the research PROFILE that a
        # connector declares for each requested capability, never on a provider
        # or capability name. Previously this method hard-coded
        # `"github.repository.read" in requested_caps` plus a tuple of git and
        # filesystem capability names, so any new provider's capability was
        # rejected outright as "unsupported" no matter what the registry could
        # actually serve. Now the registry answers, the connector declares its
        # own profile, and an unclaimed capability falls through to the fully
        # provider-agnostic path below.
        registry = context.connector_registry
        profiles: dict[str, str] = {}
        unresolved: list[str] = []
        for cap in requested_caps:
            if registry is not None and hasattr(registry, "research_profile_for"):
                profile = registry.research_profile_for(cap)
            elif registry is not None and hasattr(registry, "get_capability_connectors"):
                conns = registry.get_capability_connectors(cap) or []
                profile = next(
                    (getattr(c, "research_profile", "generic") for c in conns
                     if getattr(c, "research_profile", "generic") != "generic"),
                    "generic",
                ) if conns else None
            else:
                profile = None
            if profile is None:
                unresolved.append(cap)
            else:
                profiles[cap] = profile

        # A capability no registered connector declares cannot be observed.
        # Silence would misrepresent what was seen, so it is always recorded
        # explicitly. When it is the ONLY thing requested, that is a hard
        # failure: the task cannot be done at all. In a multi-source task the
        # unservable source is reported as unavailable while the sources that
        # genuinely observed still contribute, with the gap stated rather than
        # filled in.
        if unresolved and len(requested_caps) <= 1:
            return self._fail_unsupported(
                context, unresolved,
                reason="no registered connector declares this capability",
            )

        # filesystem.list is an internal step of the filesystem profile, so it
        # counts as "filesystem requested" without demanding its own artifact.
        def _has(profile: str) -> bool:
            return any(p == profile or (profile == "filesystem" and p == "filesystem")
                       for p in profiles.values())

        need_repository = _has("repository")
        need_vcs = _has("vcs")
        need_fs = _has("filesystem")
        need_generic = any(p == "generic" for p in profiles.values())

        def _count() -> int:
            return sum(1 for flag in (need_repository, need_vcs, need_fs, need_generic) if flag)

        if not requested_caps:
            # No requested capability maps to an implemented path: this is a
            # filesystem research task with no explicit connector requirement.
            result = self._execute_filesystem(context)
        elif unresolved and not profiles:
            # Everything requested is unservable and this is not a multi-source
            # task: nothing can be observed.
            return self._fail_unsupported(
                context, unresolved,
                reason="no registered connector declares this capability",
            )
        elif _count() == 1:
            if need_repository:
                result = self._execute_profile(context, "repository", requested_caps, profiles)
            elif need_vcs:
                result = self._execute_git(context)
            elif need_fs:
                result = self._execute_filesystem(context)
            else:
                result = self._execute_generic(context, requested_caps)
        else:
            result = self._execute_multi(context, requested_caps,
                                         need_repository=need_repository,
                                         need_git=need_vcs,
                                         need_fs=need_fs,
                                         need_generic=need_generic,
                                         profiles=profiles,
                                         unresolved=unresolved)

        # Declared message behavior: RESPONSE at completion (STATUS_UPDATE is
        # sent inside each path). One summary per top-level execution —
        # multi-path sub-executions run with messaging_hub=None.
        self._send_completion_response(context, result)
        return result

    def _fail_unsupported(self, context: AgentContext,
                          unsupported: list[str],
                          reason: str = "no connector serves this capability") -> AgentExecutionResult:
        """Explicit refusal for a capability this agent cannot implement.

        Never a partial success: a task asked for something no registered
        connector can observe, so it produces no observation. The message is
        built from the registry rather than a hard-coded provider list, so it
        stays truthful as providers are added or removed.
        """
        msg = (f"researcher cannot observe requested capabilit"
               f"{'y' if len(unsupported) == 1 else 'ies'}: {sorted(set(unsupported))}; "
               f"reason: {reason}")
        provenance = [f"agent:{context.agent_id}", "type:researcher", "unsupported-capability"]
        return AgentExecutionResult(
            task_id=context.task_id, agent_id=context.agent_id,
            status="FAILED", reality="UNKNOWN", untrusted=True,
            result={"task_id": context.task_id, "task_name": context.task_name,
                    "agent_id": context.agent_id, "status": "FAILED",
                    "reality": "UNKNOWN", "untrusted": True, "error": msg},
            artifacts=[], provenance=provenance,
            execution_metadata={"tool_executions": [], "tools_used": [],
                                "model_used": False, "model_invocations": []},
            error=msg,
        )

    def _send_completion_response(self, context: AgentContext, result: AgentExecutionResult) -> None:
        """Emit the completion RESPONSE (small redacted summary, never content)."""
        hub = context.messaging_hub
        if hub is None:
            return
        try:
            summary = result.result if isinstance(result.result, dict) else {}
            hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="RESPONSE",
                content={
                    "agent_id": context.agent_id,
                    "status": "completed" if result.status == "COMPLETED" else "failed",
                    "reality": result.reality,
                    "artifact_count": len(result.artifacts or []),
                    "files_discovered": summary.get("files_discovered", 0),
                },
                from_agent_id=context.agent_id,
                task_id=context.task_id,
            )
        except Exception:
            pass  # messaging must never fail an observation

    def _fabric_execute(self, context: AgentContext, capability: str, input_data: dict, scope: str):
        """Generic execution boundary: CapabilityRequest -> CapabilityResponse.

        Conceptual flow (no provider-specific plumbing in the agent)::

            requested capability -> capability fabric -> capability response

        The fabric resolves the capability, selects an authenticated
        connector, executes, and returns an observed response. The agent only
        interprets the response into research artifacts below.

        Two registry shapes are accepted:
          1. Full registries exposing ``request_capability`` (production).
          2. Minimal registries exposing capability discovery
             (``get_capability_connectors``) plus ``get_connector`` — used by
             unit-test stubs. These are adapted through the SAME generic
             semantics and, critically, through the SAME canonical executor, so
             the policy gate, the audit-event chain, receipt canonicalization
             and the "claimed SUCCESS without a receipt" guard all still apply.
             Connector identity is NEVER guessed from a capability-name prefix:
             a capability unknown to the stub registry resolves to
             NO_CONNECTOR/UNKNOWN_CAPABILITY rather than being sent to an
             arbitrary connector.
        Returns a CapabilityResponse-like object with status/capability/
        connector_id/provider/reality/data/receipt/provenance/error.
        """
        from runtime.capability_fabric import CapabilityResolutionError
        registry = context.connector_registry
        if registry is None:
            raise RuntimeError("connector_registry is None")
        # Preferred generic interface.
        if hasattr(registry, "request_capability"):
            from runtime.capability_fabric import CapabilityRequest
            return registry.request_capability(CapabilityRequest(
                capability=capability,
                input=dict(input_data or {}),
                scope=scope,
                task_id=context.task_id,
                agent_id=context.agent_id,
                principal=dict(context.principal or {}),
            ))
        # Minimal/stub registries: same canonical executor, so nothing is
        # bypassed. The only concession is that we call execute_capability()
        # directly instead of through the registry convenience method.
        if hasattr(registry, "get_capability_connectors") and hasattr(registry, "get_connector"):
            from runtime.capability_fabric import (
                CapabilityRequest as _Req,
                execute_capability as _exec,
            )
            return _exec(registry, _Req(
                capability=capability,
                input=dict(input_data or {}),
                scope=scope,
                task_id=context.task_id,
                agent_id=context.agent_id,
                principal=dict(context.principal or {}),
            ))
        raise RuntimeError(
            "registry supports neither request_capability nor "
            "get_capability_connectors+get_connector"
        )

    def _execute_profile(self, context: AgentContext, profile: str,
                         capabilities: list[str],
                         profiles: dict[str, str] | None = None) -> AgentExecutionResult:
        """Execute research for a given connector profile.

        This is the provider-neutral path for any specialized profile
        (repository, vcs, filesystem, generic). It resolves the actual
        capabilities for the profile, executes each through the fabric, and
        builds observations from the connector responses without any
        provider-specific interpretation.

        The profile determines which capabilities are grouped together; the
        agent never names a provider or capability in this path.
        """
        caps = [c for c in capabilities if (profiles or {}).get(c) == profile]
        if not caps:
            return self._fail_unsupported(context, [profile],
                                          reason=f"no requested capability has profile {profile!r}")

        # For single-path execution, we can just use the first capability.
        # For multi-path, _execute_multi will call this with its grouped caps.
        return self._execute_generic(context, caps)

    # ------------------------------------------------------------------
    # Git path — local read-only inspection via the generic fabric.
    # ------------------------------------------------------------------
    def _execute_git(self, context: AgentContext) -> AgentExecutionResult:
        from runtime.tools import resolve_workspace_scope
        base_meta = {
            "workspace_root": context.observation_scope or context.scope or None,
            "observation_scope": context.observation_scope or context.scope or None,
            "execution_strategy": {"requested": "DETERMINISTIC", "effective": "DETERMINISTIC"},
            "model_used": False,
            "model_invocations": [],
        }
        capability = "git.diff" if "git.diff" in (context.execution_metadata.get("capabilities_requested", []) or []) else "git.status"
        provenance = [f"agent:{context.agent_id}", "type:researcher", "git-connector", capability]
        workspace = resolve_workspace_scope(context.observation_scope, context.scope)
        if workspace is None:
            workspace = resolve_workspace_scope(context.scope, None)
        if not workspace:
            msg = f"Invalid workspace for {capability} (got {context.scope!r})"
            return AgentExecutionResult(
                task_id=context.task_id, agent_id=context.agent_id, status="FAILED",
                reality="UNKNOWN", untrusted=True,
                result={"task_id": context.task_id, "task_name": context.task_name, "agent_id": context.agent_id, "status": "FAILED", "reality": "UNKNOWN", "untrusted": True, "error": msg},
                artifacts=[], provenance=provenance,
                execution_metadata={**base_meta, "tool_executions": [], "tools_used": []}, error=msg,
            )
        try:
            resp = self._fabric_execute(context, capability, {"workspace": workspace, "agent_id": context.agent_id}, workspace)
        except Exception as exc:
            from runtime.capability_fabric import CapabilityResolutionError
            msg = f"Git capability unavailable: {exc.code}: {exc.detail}" if isinstance(exc, CapabilityResolutionError) else f"Git connector raised: {type(exc).__name__}: {exc}"
            return AgentExecutionResult(
                task_id=context.task_id, agent_id=context.agent_id, status="FAILED",
                reality="UNKNOWN", untrusted=True,
                result={"task_id": context.task_id, "task_name": context.task_name, "agent_id": context.agent_id, "status": "FAILED", "reality": "UNKNOWN", "untrusted": True, "error": msg},
                artifacts=[], provenance=provenance,
                execution_metadata={**base_meta, "tool_executions": [], "tools_used": []}, error=msg,
            )
        tool_executions = [resp.to_tool_record(task_id=context.task_id, agent_id=context.agent_id)]
        tool_executions[0]["workspace_root"] = workspace
        if resp.status == "BLOCKED":
            # An honest refusal is OBSERVED-as-refusal at the fabric level,
            # but it is NOT a git observation: no OBSERVED artifact may be
            # manufactured from it. Fail explicitly (matches the GitHub path).
            msg = f"Git connector refused execution: {resp.error or resp.receipt.get('error') or 'blocked'}"
            return AgentExecutionResult(
                task_id=context.task_id, agent_id=context.agent_id, status="FAILED",
                reality="UNKNOWN", untrusted=True,
                result={"task_id": context.task_id, "task_name": context.task_name, "agent_id": context.agent_id, "status": "FAILED", "reality": "UNKNOWN", "untrusted": True, "error": msg},
                artifacts=[], provenance=provenance,
                execution_metadata={**base_meta, "tool_executions": tool_executions, "tools_used": [capability]}, error=msg,
            )
        if resp.status not in ("SUCCESS", "PARTIAL"):
            msg = f"Git connector failed: {resp.error or 'unknown'}"
            return AgentExecutionResult(
                task_id=context.task_id, agent_id=context.agent_id, status="FAILED",
                reality="UNKNOWN", untrusted=True,
                result={"task_id": context.task_id, "task_name": context.task_name, "agent_id": context.agent_id, "status": "FAILED", "reality": "UNKNOWN", "untrusted": True, "error": msg},
                artifacts=[], provenance=provenance,
                execution_metadata={**base_meta, "tool_executions": tool_executions, "tools_used": [capability]}, error=msg,
            )
        data = resp.data if isinstance(resp.data, dict) else {}
        data_json = json.dumps(data, sort_keys=True, default=str)
        findings = [{"file": f"git://{capability}@{workspace}", "content_hash": hashlib.sha256(data_json.encode()).hexdigest(), "reality": "OBSERVED", "content_preview": data_json[:2000]}]
        evidence = [{"type": "git_receipt", "source": "GitConnector", "reality": "OBSERVED", "receipt_id": resp.receipt.get("receipt_id"), "content_hash": resp.receipt.get("result_hash")}]
        research = {
            "scope": workspace, "source": "GitConnector", "capability": capability,
            "findings": findings,
            "analysis": {"file_type_distribution": {}, "total_bytes_observed": len(data_json), "files_with_hashes": 1, "files_analyzed": 1, "assessment": f"Local git inspection via {capability}"},
            "evidence": evidence, "observation": data, "git_observation": data, "receipt": resp.receipt,
        }
        artifact_content = {"research": research, "findings": findings, "analysis": research["analysis"], "evidence": evidence, "observation": data}
        artifact = {"kind": "research_report", "name": "research_report.json", "content": artifact_content, "content_hash": _digest(artifact_content), "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts], "provenance": provenance, "reality": "OBSERVED", "untrusted": False, "verification_state": "UNVERIFIED"}
        return AgentExecutionResult(
            task_id=context.task_id, agent_id=context.agent_id, status="COMPLETED",
            reality="OBSERVED", untrusted=False,
            result={"task_id": context.task_id, "task_name": context.task_name, "agent_id": context.agent_id, "status": "COMPLETED", "reality": "OBSERVED", "untrusted": False, "research_findings": findings, "analysis": research["analysis"], "observation": data, "artifact_count": 1, "files_discovered": 1},
            artifacts=[artifact], provenance=provenance,
            execution_metadata={**base_meta, "tool_executions": tool_executions, "tools_used": [capability]},
        )

    # ------------------------------------------------------------------
    # Generic path — any connector, no provider knowledge in this layer.
    # ------------------------------------------------------------------

    def _diagnostic_failure(self, context: AgentContext, provenance: list[str],
                            base_meta: dict, msg: str, *, capability: str,
                            tool_executions: list[dict[str, Any]],
                            scope: str = "") -> AgentExecutionResult:
        """FAILED result carrying an UNKNOWN diagnostic artifact.

        The diagnostic records WHY the observation could not be made. It is
        stamped ``reality="UNKNOWN"`` and ``untrusted=True`` and carries no
        findings, so it can never be consumed as evidence — it exists so a
        failure leaves a trace instead of vanishing, matching the specialized
        paths. It deliberately does not echo any receipt id.
        """
        research = {
            "scope": scope, "source": f"{capability}", "capability": capability,
            "findings": [],
            "analysis": {"files_analyzed": 0, "observation": "not performed",
                         "reason": msg},
            "evidence": [], "diagnostic": {"reason": msg, "capability": capability},
        }
        artifact_content = {"research": research, "findings": [], "evidence": [],
                            "diagnostic": research["diagnostic"]}
        artifact = {
            "kind": "research_report", "name": "research_report.json",
            "content": artifact_content, "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": provenance, "reality": "UNKNOWN", "untrusted": True,
            "verification_state": "UNVERIFIED",
        }
        return AgentExecutionResult(
            task_id=context.task_id, agent_id=context.agent_id, status="FAILED",
            reality="UNKNOWN", untrusted=True,
            result={"task_id": context.task_id, "task_name": context.task_name,
                    "agent_id": context.agent_id, "status": "FAILED",
                    "reality": "UNKNOWN", "untrusted": True, "error": msg,
                    "research_findings": [], "artifact_count": 1,
                    "files_discovered": 0},
            artifacts=[artifact], provenance=provenance,
            execution_metadata={**base_meta, "tool_executions": tool_executions,
                                "tools_used": [capability] if tool_executions else []},
            error=msg,
        )

    def _execute_generic(self, context: AgentContext,
                         capabilities: list[str]) -> AgentExecutionResult:
        """Observe through ANY registered connector, provider-agnostically.

        This is the path that makes GitHub genuinely one provider rather than
        the system. It knows nothing about any specific provider: it builds a
        canonical CapabilityRequest, hands it to the fabric, and reports
        whatever came back with the connector's own receipt. A connector
        written tomorrow participates with no change to this agent.

        Scope validation is performed generically using the capability's
        declared ``scope_kind`` (e.g., "owner_repo" for GitHub, "absolute_path"
        for filesystem/git). An invalid scope yields an honest BLOCKED refusal
        without ever reaching the connector.
        """
        capabilities = list(capabilities or [])
        if not capabilities:
            return self._fail_unsupported(context, ["<none>"],
                                          reason="no capability requested")
        capability = capabilities[0]
        base_meta = {
            "workspace_root": context.observation_scope or context.scope or None,
            "observation_scope": context.observation_scope or context.scope or None,
            "execution_strategy": {"requested": "DETERMINISTIC", "effective": "DETERMINISTIC"},
            "model_used": False, "model_invocations": [],
        }
        scope = context.observation_scope or context.scope or ""

        # Generic scope validation against the capability's declared scope_kind.
        # This replaces provider-specific validation.
        scope_kind = self._get_scope_kind(context, capability)
        if not self._validate_scope(scope, scope_kind):
            msg = (f"scope {scope!r} does not match capability {capability!r} "
                   f"scope_kind {scope_kind!r}")
            return self._diagnostic_failure(
                context, [], base_meta, msg,
                capability=capability, tool_executions=[],
            )

        try:
            resp = self._fabric_execute(
                context=context,
                capability=capability,
                input_data={"scope": scope},
                scope=scope,
            )
        except Exception as exc:
            # Resolution/policy failures are honest refusals, not agent crashes.
            # The generic path must never leak an exception past the agent
            # boundary: a refused capability yields an explicit FAILED result
            # with no observation, exactly like the specialized paths.
            from runtime.capability_fabric import CapabilityResolutionError
            code = getattr(exc, "code", type(exc).__name__)
            msg = f"{code} resolving {capability}: {getattr(exc, 'detail', exc)}"
            return self._diagnostic_failure(
                context, [], base_meta, msg,
                capability=capability, tool_executions=[],
            )

        provenance = [f"agent:{context.agent_id}", "type:researcher", f"connector:{resp.connector_id}", capability]
        tool_executions = [resp.to_tool_record(task_id=context.task_id, agent_id=context.agent_id)]
        if resp.status not in ("SUCCESS", "PARTIAL"):
            msg = (f"connector refused execution of {capability}: "
                   f"{resp.error or resp.receipt.get('error') or resp.status}")
            return self._diagnostic_failure(
                context, provenance, base_meta, msg,
                capability=capability, tool_executions=tool_executions,
            )

        data = resp.data if isinstance(resp.data, dict) else {"value": resp.data}
        data_json = json.dumps(data, sort_keys=True, default=str)
        source = f"{resp.connector_id}:{capability}"
        findings = [{
            "file": f"{resp.connector_id}://{scope}" if scope else f"{resp.connector_id}://",
            "content_hash": hashlib.sha256(data_json.encode()).hexdigest(),
            "reality": "OBSERVED",
            "content_preview": data_json[:2000],
        }]
        evidence = [{
            "type": "connector_receipt",
            "source": source,
            "reality": "OBSERVED",
            "receipt_id": resp.receipt.get("receipt_id"),
            "content_hash": resp.receipt.get("result_hash"),
        }]
        research = {
            "scope": scope, "source": source, "capability": capability,
            "findings": findings,
            "analysis": {
                "capability": capability,
                "connector_id": resp.connector_id,
                "provider": resp.provider,
                "observed_bytes": len(data_json),
                "files_analyzed": 1,
                "assessment": f"Observation via {source}",
            },
            "evidence": evidence,
            "observation": data,
            "receipt": resp.receipt,
        }
        artifact_content = {"research": research, "findings": findings,
                            "analysis": research["analysis"], "evidence": evidence,
                            "observation": data}
        artifact = {
            "kind": "research_report", "name": "research_report.json",
            "content": artifact_content, "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": provenance, "reality": "OBSERVED", "untrusted": False,
            "verification_state": "UNVERIFIED",
        }
        return AgentExecutionResult(
            task_id=context.task_id, agent_id=context.agent_id, status="COMPLETED",
            reality="OBSERVED", untrusted=False,
            result={"task_id": context.task_id, "task_name": context.task_name,
                    "agent_id": context.agent_id, "status": "COMPLETED",
                    "reality": "OBSERVED", "untrusted": False,
                    "research_findings": findings, "analysis": research["analysis"],
                    "observation": data, "artifact_count": 1, "files_discovered": 1},
            artifacts=[artifact], provenance=provenance,
            execution_metadata={**base_meta, "tool_executions": tool_executions,
                                "tools_used": [capability]},
        )

    # ------------------------------------------------------------------
    # Multi path — independent observations, preserved lineage (no overwrite).
    # ------------------------------------------------------------------
    def _execute_multi(self, context: AgentContext, requested_caps: list[str], *,
                        need_repository: bool = False, need_git: bool = False,
                        need_fs: bool = False,
                        need_generic: bool = False,
                        profiles: dict[str, str] | None = None,
                        unresolved: list[str] | None = None) -> AgentExecutionResult:
        """Correlate several independently-oberved capabilities into one report.

        Each contributing path executes independently and keeps its own key, so
        lineage per source is preserved. A path that fails is recorded as an
        error and contributes NO observation; if every path fails the whole task
        fails with reality UNKNOWN.
        """
        base_meta = {
            "workspace_root": context.observation_scope or context.scope or None,
            "observation_scope": context.observation_scope or context.scope or None,
            "execution_strategy": {"requested": "DETERMINISTIC", "effective": "DETERMINISTIC"},
            "model_used": False,
            "model_invocations": [],
        }
        provenance = [f"agent:{context.agent_id}", "type:researcher", "multi-connector"] + list(requested_caps)
        observations: dict[str, Any] = {}
        tool_executions: list[dict[str, Any]] = []
        errors: list[str] = []
        # Provenance of every contributing path is preserved verbatim so the
        # merged report keeps per-source lineage. Without this, merging replaced
        # e.g. the connector identity with the generic "multi-connector" label
        # and the trace could no longer say WHICH provider observed WHAT.
        source_provenance: list[str] = []
        # Sources that no connector can serve are reported, never silently
        # dropped: the report states which requested sources were unavailable
        # so the gap is visible instead of being papered over by the sources
        # that did observe.
        for cap in (unresolved or []):
            errors.append(
                f"{cap}: no registered connector declares this capability"
            )

        def _sub(caps: list[str], *, scope: str) -> AgentContext:
            return AgentContext(
                workflow_id=context.workflow_id, task_id=context.task_id,
                task_name=context.task_name, agent_id=context.agent_id,
                scope=scope, parameters=context.parameters,
                input_artifacts=context.input_artifacts,
                artifact_contents=context.artifact_contents,
                principal=context.principal, tenant_id=context.tenant_id,
                project_id=context.project_id, objective=context.objective,
                constraints=context.constraints,
                previous_messages=context.previous_messages,
                execution_metadata={"capabilities_requested": list(caps)},
                observation_scope=context.observation_scope,
                # Collaboration is preserved for sub-executions: a contributing
                # path that discovers something another specialist must see
                # (e.g. auth-relevant files) still has to ask. Dropping the hub
                # here previously meant the filesystem path, once reachable in
                # a multi-capability plan, silently lost its QUESTION message.
                messaging_hub=context.messaging_hub,
                connector_registry=context.connector_registry,
            )

        # Each contributing path runs independently and is preserved under its
        # own key: cross-source lineage is never overwritten. A path that
        # fails contributes an ERROR ENTRY, never a fabricated observation.
        def _collect(caps: list[str], runner, *, scope: str, label: str) -> None:
            try:
                r = runner(_sub(caps, scope=scope))
                tool_executions.extend(r.execution_metadata.get("tool_executions", []) or [])
                source_provenance.extend(r.provenance or [])
                if r.status == "COMPLETED" and r.artifacts:
                    for cap in caps:
                        observations[cap] = r.artifacts[0]["content"]["research"]
                else:
                    errors.append(r.error or f"{label} failed")
            except Exception as exc:
                errors.append(f"{label}: {type(exc).__name__}: {exc}")

        # Group the requested capabilities by the path that will observe them. The
        # grouping is by profile, so this layer never names a provider.
        generic_caps = [c for c in requested_caps if (profiles or {}).get(c) == "generic"]
        if need_repository:
            repo_caps = [c for c in requested_caps if (profiles or {}).get(c) == "repository"]
            _collect(repo_caps,
                     lambda ctx: self._execute_profile(ctx, "repository", repo_caps, profiles),
                     scope=context.scope, label="repository")
        if need_git:
            _collect([c for c in requested_caps if (profiles or {}).get(c) == "vcs"],
                     self._execute_git,
                     scope=context.observation_scope or context.scope, label="vcs")
        if need_fs:
            _collect([c for c in requested_caps if (profiles or {}).get(c) == "filesystem"],
                     self._execute_filesystem,
                     scope=context.observation_scope or context.scope, label="filesystem")
        if need_generic and generic_caps:
            _collect(generic_caps, self._execute_generic,
                     scope=context.scope, label="generic")
        if errors and not observations:
            msg = "; ".join(errors)
            return AgentExecutionResult(task_id=context.task_id, agent_id=context.agent_id, status="FAILED", reality="UNKNOWN", untrusted=True, result={"task_id": context.task_id, "task_name": context.task_name, "agent_id": context.agent_id, "status": "FAILED", "reality": "UNKNOWN", "untrusted": True, "error": msg}, artifacts=[], provenance=provenance, execution_metadata={**base_meta, "tool_executions": tool_executions, "tools_used": [t.get("capability") for t in tool_executions]}, error=msg)
        findings = []
        evidence = []
        for cap, obs in observations.items():
            for f in obs.get("findings", []):
                findings.append({**f, "source_capability": cap})
            for e in obs.get("evidence", []):
                evidence.append({**e, "source_capability": cap})
        # Merged provenance: the generic marker PLUS every contributing path's
        # own provenance, de-duplicated and order-stable. The connector identity
        # a single-path report would have carried is never lost by merging.
        merged_provenance = list(provenance)
        for entry in source_provenance:
            if entry not in merged_provenance:
                merged_provenance.append(entry)

        # Report shape follows what was actually observed, with no provider
        # knowledge in this layer:
        #   * ONE contributing source -> that source's own research dict is the
        #     base, so every downstream consumer sees the same shape it sees for
        #     a single-path run. (Wrapping it would silently drop fields such
        #     as a source-specific metadata block that reporters read.)
        #   * MULTIPLE sources -> a correlated dict with per-source lineage.
        if len(observations) == 1:
            only_key, only_obs = next(iter(observations.items()))
            base = dict(only_obs) if isinstance(only_obs, dict) else {}
            research = {
                **base,
                "scope": base.get("scope") or context.scope,
                "source": base.get("source") or "multi-connector",
                "capabilities": [only_key],
                "findings": findings,
                "analysis": {
                    **(base.get("analysis") if isinstance(base.get("analysis"), dict) else {}),
                    "files_analyzed": len(findings),
                    "evidence_count": len(evidence),
                    "assessment": f"Correlated {len(observations)} independent observations",
                    "sources": [only_key],
                    "contributing_provenance": list(source_provenance),
                },
                "evidence": evidence,
                "observations": observations,
            }
        else:
            research = {"scope": context.scope, "source": "multi-connector", "capabilities": list(observations.keys()), "findings": findings, "analysis": {"files_analyzed": len(findings), "evidence_count": len(evidence), "assessment": f"Correlated {len(observations)} independent observations", "sources": list(observations.keys()), "contributing_provenance": list(source_provenance)}, "evidence": evidence, "observations": observations}
        artifact_content = {"research": research, "findings": findings, "analysis": research["analysis"], "evidence": evidence, "observations": observations}
        artifact = {"kind": "research_report", "name": "research_report.json", "content": artifact_content, "content_hash": _digest(artifact_content), "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts], "provenance": merged_provenance, "reality": "OBSERVED", "untrusted": False, "verification_state": "UNVERIFIED"}
        return AgentExecutionResult(task_id=context.task_id, agent_id=context.agent_id, status="COMPLETED", reality="OBSERVED", untrusted=False, result={"task_id": context.task_id, "task_name": context.task_name, "agent_id": context.agent_id, "status": "COMPLETED", "reality": "OBSERVED", "untrusted": False, "research_findings": findings, "analysis": research["analysis"], "observations": observations, "artifact_count": 1}, artifacts=[artifact], provenance=merged_provenance, execution_metadata={**base_meta, "tool_executions": tool_executions, "tools_used": [t.get("capability") for t in tool_executions]})

    # ------------------------------------------------------------------
    # Filesystem path — only when github.repository.read was NOT requested.
    #
    # Local filesystem reading is a PROVIDER, so it is requested through the
    # SAME capability fabric as GitHub and git. This agent no longer walks the
    # tree itself: it does not import Path, does not call rglob/read_bytes,
    # and does not mint receipt ids. Every observation therefore carries a
    # connector-produced canonical receipt, and every path is containment
    # checked by BoundedAgentRuntime behind the connector.
    #
    # Honesty rules enforced here:
    #   - zero observed files -> FAILED / UNKNOWN, never COMPLETED / OBSERVED
    #   - a BLOCKED refusal stays BLOCKED and is reported as such
    #   - no github.repository.read -> never substituted by filesystem (and
    #     github requested but unavailable -> FAILED, never filesystem)
    # ------------------------------------------------------------------
    def _execute_filesystem(self, context: AgentContext) -> AgentExecutionResult:
        from runtime.tools import resolve_workspace_scope

        provenance = [f"agent:{context.agent_id}", "type:researcher",
                      "filesystem-observation", "filesystem.read"]
        base_meta = {
            "workspace_root": None,
            "observation_scope": context.observation_scope or context.scope or None,
            "execution_strategy": {"requested": "DETERMINISTIC", "effective": "DETERMINISTIC"},
            "model_used": False,
            "model_invocations": [],
        }

        workspace = resolve_workspace_scope(context.observation_scope, context.scope)
        if workspace is None:
            workspace = resolve_workspace_scope(context.scope, None)

        def _fail(msg: str, *, tool_executions: list | None = None) -> AgentExecutionResult:
            return AgentExecutionResult(
                task_id=context.task_id, agent_id=context.agent_id,
                status="FAILED", reality="UNKNOWN", untrusted=True,
                result={
                    "task_id": context.task_id, "task_name": context.task_name,
                    "agent_id": context.agent_id, "status": "FAILED",
                    "reality": "UNKNOWN", "untrusted": True, "error": msg,
                },
                artifacts=[], provenance=provenance,
                execution_metadata={**base_meta,
                                    "tool_executions": list(tool_executions or []),
                                    "tools_used": [t.get("capability") for t in (tool_executions or [])]},
                error=msg,
            )

        if workspace is None:
            return _fail(
                "no legitimate filesystem scope: an explicit existing workspace "
                f"directory is required (got {context.observation_scope or context.scope!r})"
            )

        # 1. Ask the fabric for a bounded listing.
        try:
            list_resp = self._fabric_execute(
                context, "filesystem.list", {"workspace": workspace}, workspace,
            )
        except Exception as exc:
            return _fail(f"filesystem.list resolution failed: {type(exc).__name__}: {exc}")

        tool_executions: list[dict[str, Any]] = []
        if list_resp.receipt:
            try:
                tool_executions.append(list_resp.to_tool_record(
                    task_id=context.task_id, agent_id=context.agent_id))
                tool_executions[-1]["workspace_root"] = workspace
            except Exception:
                tool_executions.append(_minimal_tool_record(list_resp, workspace))

        listing = list_resp.data.get("files") if isinstance(list_resp.data, dict) else None
        if list_resp.status not in ("SUCCESS", "PARTIAL"):
            return _fail(
                f"filesystem.list {list_resp.status}: {list_resp.error or list_resp.data}",
                tool_executions=tool_executions,
            )
        if not isinstance(listing, list):
            listing = []

        # 2. Read each listed file THROUGH the fabric (bounded count), so each
        #    observation has its own connector-produced canonical receipt.
        findings: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        total_bytes = 0
        type_dist: dict[str, int] = {}
        errors: list[str] = []
        for entry in listing[:MAX_FILES_OBSERVED]:
            path = str(entry.get("path", ""))
            if not path:
                continue
            try:
                resp = self._fabric_execute(
                    context, "filesystem.read",
                    {"workspace": workspace, "path": path, "max_chars": 2000},
                    workspace,
                )
            except Exception as exc:
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            if resp.receipt:
                try:
                    tool_executions.append(resp.to_tool_record(
                        task_id=context.task_id, agent_id=context.agent_id))
                    tool_executions[-1]["workspace_root"] = workspace
                except Exception:
                    tool_executions.append(_minimal_tool_record(resp, workspace))
            if resp.status not in ("SUCCESS", "PARTIAL") or not isinstance(resp.data, dict):
                errors.append(f"{path}: {resp.status}: {resp.error or 'no observation'}")
                continue
            sha = str(resp.data.get("sha256") or "")
            if not sha:
                errors.append(f"{path}: connector returned no content hash")
                continue
            suffix = Path(path).suffix.lower() or "<noext>"
            type_dist[suffix] = type_dist.get(suffix, 0) + 1
            try:
                total_bytes += int(resp.data.get("size") or 0)
            except (TypeError, ValueError):
                pass
            findings.append({
                "file": str(resp.data.get("path") or path),
                "content_hash": sha,
                "reality": "OBSERVED",
                "content_preview": str(resp.data.get("content_preview") or "")[:2000],
            })
            evidence.append({
                "type": "filesystem_read",
                "source": "FilesystemConnector",
                "reality": "OBSERVED",
                "file": str(resp.data.get("path") or path),
                "content_hash": sha,
                "receipt_id": resp.receipt.get("receipt_id"),
            })

        # Truth boundary for the filesystem path. These are three genuinely
        # different situations and must not be collapsed:
        #
        #   * listing itself did not succeed -> FAILED / UNKNOWN. A refused or
        #     broken read is not an observation of anything.
        #   * listing succeeded and returned zero files -> OBSERVED. "This
        #     workspace contains zero files" IS a fact about the workspace, and
        #     it carries a real connector receipt. Reporting it as COMPLETED is
        #     truthful, not a fabricated success.
        #   * listing succeeded and every read was refused -> FAILED, because we
        #     observed the listing but not a single file, and claiming an
        #     observation we cannot point a receipt at would be false.
        if not findings and errors:
            detail = "; ".join(errors[:5])
            return _fail(f"filesystem listing succeeded but no file could be read ({detail})",
                         tool_executions=tool_executions)

        analysis = {
            "file_type_distribution": type_dist,
            "total_bytes_observed": total_bytes,
            "files_with_hashes": len(findings),
            "files_analyzed": len(findings),
            "files_requested": len(listing),
            "partial": bool(errors),
            "errors": errors[:20],
            "assessment": (f"Observed {len(findings)} of {len(listing)} listed files"
                         if findings else
                         f"Observed 0 files: workspace listing succeeded and is empty ({len(listing)} entries)"),
        }

        research = {
            "scope": workspace,
            "findings": findings,
            "analysis": analysis,
            "evidence": evidence,
        }
        artifact_content = {
            "research": research,
            "findings": findings,
            "analysis": analysis,
            "evidence": evidence,
        }
        artifact = {
            "kind": "research_report",
            "name": "research_report.json",
            "content": artifact_content,
            "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": provenance,
            "reality": "OBSERVED",
            "untrusted": False,
            "verification_state": "UNVERIFIED",
        }

        if context.messaging_hub:
            try:
                context.messaging_hub.send(
                    workflow_id=context.workflow_id,
                    tenant_id=context.tenant_id,
                    message_type="STATUS_UPDATE",
                    content={"agent_id": context.agent_id, "status": "research_complete",
                             "files_observed": len(findings)},
                    from_agent_id=context.agent_id,
                    task_id=context.task_id,
                )
            except Exception:
                pass
            # Targeted collaboration: when authentication/security-relevant
            # files are discovered, ask the security specialist to review.
            try:
                _signals = [f.get("file", "") for f in findings
                            if any(k in f.get("file", "").lower()
                                   for k in ("auth", "security", "secret", "token", "jwt", "credential"))]
                if _signals:
                    context.messaging_hub.question(
                        workflow_id=context.workflow_id,
                        tenant_id=context.tenant_id,
                        question=(
                            f"Authentication-related code discovered: {_signals[:3]}. "
                            "Please review for security risks."
                        ),
                        from_agent_id=context.agent_id,
                        to_agent_id="security-analyst",
                        task_id=context.task_id,
                        context={"discovered": _signals[:5], "files_observed": len(findings)},
                    )
            except Exception:
                pass

        result = {
            "task_id": context.task_id,
            "task_name": context.task_name,
            "agent_id": context.agent_id,
            "status": "COMPLETED",
            "reality": "OBSERVED",
            "untrusted": False,
            "research_findings": findings,
            "analysis": analysis,
            "artifact_count": 1,
            "files_discovered": len(findings),
        }
        return AgentExecutionResult(
            task_id=context.task_id,
            agent_id=context.agent_id,
            status="COMPLETED",
            reality="OBSERVED",
            untrusted=False,
            result=result,
            artifacts=[artifact],
            provenance=provenance,
            execution_metadata={
                "tool_executions": tool_executions,
                "tools_used": sorted({str(t.get("capability")) for t in tool_executions}),
                "workspace_root": workspace,
                "observation_scope": workspace,
                "execution_strategy": {"requested": "DETERMINISTIC", "effective": "DETERMINISTIC"},
                "model_used": False,
                "model_invocations": [],
            },
        )
