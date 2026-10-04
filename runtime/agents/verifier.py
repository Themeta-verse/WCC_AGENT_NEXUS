"""Verification Agent — performs deterministic verification of workflow artifacts.

This agent performs the final verification step in a workflow. It checks that:
- All required artifact kinds are present
- Artifacts have correct content hashes
- Verification states are properly set

Its output is VERIFIED when deterministic checks pass, INFERRED otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import json

from runtime.agent_base import AgentContext, AgentExecutionResult


def _digest(value: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VerificationAgent:
    """Agent that performs deterministic verification of workflow output artifacts."""
    agent_id: str = "verifier"
    name: str = "Verification Agent"
    role: str = "verifier"
    capabilities: list[str] = field(default_factory=lambda: ["verify"])
    allowed_operations: list[str] = field(default_factory=lambda: ["read", "verify"])
    prohibited_operations: list[str] = field(default_factory=lambda: ["filesystem.write", "execute"])
    scope: dict = field(default_factory=dict)
    # Phase 7: DETERMINISTIC | MODEL | HYBRID.
    # The verifier NEVER trusts model output: independent deterministic checks
    # alone decide VERIFIED vs INFERRED. Model may only supply a summary.
    execution_strategy: str = "DETERMINISTIC"
    model_router: Any = None
    input_contract: tuple = ("final_report (or any upstream) artifact contents + hashes",)
    output_contract: tuple = ("verification_result artifact (VERIFIED if checks pass else INFERRED)",)
    tool_permissions: tuple = ("no direct tools; independent deterministic checks only",)
    artifact_behavior: str = "produces verification_result; parents = verified input artifact IDs"
    message_behavior: str = "sends STATUS_UPDATE at start and RESPONSE at completion"

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        """Execute verification: check that all required artifacts are present and well-formed."""
        checks: list[dict[str, Any]] = []
        all_passed = True

        if context.messaging_hub:
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="STATUS_UPDATE",
                content={
                    "agent_id": context.agent_id,
                    "status": "verifying_artifacts",
                    "input_artifact_count": len(context.input_artifacts),
                },
                from_agent_id=context.agent_id,
                task_id=context.task_id,
            )

        # Determine required artifact kinds based on what we received
        required_kinds = self._determine_required_kinds(context)
        produced_kinds = {a.get("kind") for a in context.input_artifacts if a.get("kind")}

        for kind in required_kinds:
            if kind in produced_kinds:
                checks.append({"artifact": kind, "check": "presence", "status": "PASS", "detail": f"{kind} artifact is present"})
            else:
                checks.append({"artifact": kind, "check": "presence", "status": "FAIL", "detail": f"{kind} artifact is missing"})
                all_passed = False

        # Verify content hashes are present
        for art in context.input_artifacts:
            if not art.get("content_hash"):
                checks.append({"artifact": art.get("kind", "unknown"), "check": "content_hash", "status": "FAIL", "detail": "content_hash missing"})
                all_passed = False
            else:
                checks.append({"artifact": art.get("kind", "unknown"), "check": "content_hash", "status": "PASS", "detail": f"content_hash={art['content_hash'][:16]}..."})

        # Content-integrity: recompute the hash over the resolved content and
        # compare with the claimed content_hash. Catches content modified
        # after hash creation, hash tampering, and artifact substitution.
        # The executor resolves content by artifact_id (falling back to
        # kind/name references); unresolvable content fails explicitly.
        def _resolve_content(art: dict[str, Any]) -> Any:
            by_id: dict[str, Any] = {}
            by_kind: dict[str, Any] = {}
            for entry in (context.artifact_contents or []):
                if entry.get("artifact_id"):
                    by_id[entry["artifact_id"]] = entry.get("content")
                if entry.get("kind"):
                    by_kind[entry["kind"]] = entry.get("content")
                if entry.get("name"):
                    by_kind[entry["name"]] = entry.get("content")
            content = by_id.get(art.get("artifact_id", ""))
            if content is None:
                content = by_kind.get(art.get("kind", ""))
            if content is None:
                # Loosely-keyed contents (unit-test style): first dict block.
                # The hash-recompute below remains the actual integrity gate.
                for entry in (context.artifact_contents or []):
                    if isinstance(entry.get("content"), dict):
                        content = entry.get("content")
                        break
            return content

        for art in context.input_artifacts:
            _kind = art.get("kind", "unknown")
            _content = _resolve_content(art)
            if _content is None:
                checks.append({"artifact": _kind, "check": "content_available", "status": "FAIL", "detail": "artifact content unavailable for hash verification"})
                all_passed = False
                continue
            checks.append({"artifact": _kind, "check": "content_available", "status": "PASS", "detail": "artifact content resolved"})
            if not art.get("content_hash"):
                continue  # already failed above on content_hash presence
            try:
                _recomputed = _digest(_content)
                if _recomputed == art.get("content_hash"):
                    checks.append({"artifact": _kind, "check": "content_hash_verified", "status": "PASS", "detail": f"content hash matches ({_recomputed[:16]}...)"})
                else:
                    checks.append({"artifact": _kind, "check": "content_hash_verified", "status": "FAIL", "detail": "content hash MISMATCH: content altered or substituted after hash creation"})
                    all_passed = False
            except Exception as _hexc:
                checks.append({"artifact": _kind, "check": "content_hash_verified", "status": "FAIL", "detail": f"hash recompute failed: {type(_hexc).__name__}"})
                all_passed = False

        # Verify artifact provenance
        for art in context.input_artifacts:
            if art.get("provenance"):
                checks.append({"artifact": art.get("kind", "unknown"), "check": "provenance", "status": "PASS", "detail": "provenance chain present"})
            else:
                checks.append({"artifact": art.get("kind", "unknown"), "check": "provenance", "status": "FAIL", "detail": "provenance chain missing"})
                all_passed = False

        # Verify reality states are valid
        for art in context.input_artifacts:
            reality = art.get("reality", "UNKNOWN")
            if reality in ("OBSERVED", "INFERRED", "VERIFIED"):
                checks.append({"artifact": art.get("kind", "unknown"), "check": "reality_state", "status": "PASS", "detail": f"reality={reality}"})
            else:
                checks.append({"artifact": art.get("kind", "unknown"), "check": "reality_state", "status": "FAIL", "detail": f"invalid reality={reality}"})
                all_passed = False

        # Connector-artifact validation: any consumed artifact claiming a
        # connector path is independently verified instead of passing on
        # presence alone. The generic capability verifier runs FIRST
        # (capability/response/receipt/hash/provenance/scope/lineage — no
        # provider fields); provider adapters add depth checks behind that
        # boundary. The verifier must NOT claim VERIFIED for a connector
        # proof unless the observed artifact/provenance is real.
        try:
            from runtime.capability_verifiers import (
                GenericCapabilityVerifier,
                verifier_adapters_for,
            )
        except ImportError:  # pragma: no cover - top-level import style
            from capability_verifiers import (
                GenericCapabilityVerifier,
                verifier_adapters_for,
            )
        try:
            # Provider-neutral adapter dispatch: for every input artifact,
            # select adapters purely by provenance markers, run the generic
            # verifier first, then each matching adapter. The agent never
            # names providers, capabilities, or check semantics — those live
            # in the registered adapters. New providers participate by
            # registering an adapter; no agent edit required.
            def _extract_research(content: Any) -> tuple[dict[str, Any], bool]:
                """Resolve content -> (research dict, is_synthesis).

                Handles direct research blocks and final_report synthesis
                (transitive GitHub lineage via the report's research section).
                Content plumbing only — no provider verdicts here.
                """
                if isinstance(content, str):
                    try:
                        content = json.loads(content)
                    except (ValueError, TypeError):
                        return {}, False
                if not isinstance(content, dict):
                    return {}, False
                if "research" in content and isinstance(content.get("research"), dict):
                    return content.get("research") or {}, False
                if "final_report" in content:
                    _fr = content.get("final_report") or {}
                    _sec = (_fr.get("sections") or {}).get("research") or {}
                    _chain = any("github" in str(e.get("source", "")).lower()
                                 for e in (_fr.get("evidence_chain") or []))
                    return {
                        "scope": _sec.get("repository") or _sec.get("scope") or "",
                        "findings": [{"file": f"github://{_sec.get('repository', '')}"}] if _sec.get("observation") else [],
                        "evidence": [{"type": "github_receipt"}] if (_chain or _sec.get("observation")) else [],
                        "github_metadata": _sec.get("observation"),
                        "observation": _sec.get("observation"),
                    }, True
                return {}, False

            _ctx_scope = (context.scope or context.observation_scope or "").strip()
            for art in context.input_artifacts:
                _adapters = verifier_adapters_for(art.get("provenance") or [])
                if not _adapters:
                    continue
                _content = _resolve_content(art)
                if _content is None and (context.artifact_contents or []):
                    for _c in (context.artifact_contents or []):
                        _cc = _c.get("content")
                        if isinstance(_cc, str):
                            try:
                                _cc = json.loads(_cc)
                            except (ValueError, TypeError):
                                continue
                        if isinstance(_cc, dict) and ("research" in _cc or "final_report" in _cc):
                            _content = _cc
                            break
                _research, _is_fr = _extract_research(_content)
                for _adapter in _adapters:
                    _exp_cap = _adapter.get("capability") or next(
                        (p for p in (art.get("provenance") or [])
                         if "." in p and ":" not in p), "")
                    _generic = GenericCapabilityVerifier.verify_artifact(
                        art, _content if isinstance(_content, dict) else {},
                        expected_capability=_exp_cap,
                        expected_scope=_ctx_scope if _adapter.get("scope_from", "context") == "context"
                        else str(_research.get("scope", "")),
                    )
                    if _generic is not None:
                        for _g in _generic:
                            checks.append({"artifact": art.get("kind", "unknown"), **_g})
                        if any(_g.get("status") != "PASS" for _g in _generic):
                            all_passed = False
                    _scope_for_adapter = (
                        _ctx_scope if _adapter.get("scope_from", "context") == "context"
                        else str(_research.get("scope", "")))
                    for _pc in _adapter["verify"](
                        _research,
                        expected_scope=_scope_for_adapter,
                        artifact_reality=art.get("reality", "UNKNOWN"),
                        is_synthesis=_is_fr,
                    ):
                        checks.append({"artifact": art.get("kind", "unknown"),
                                       "adapter": _adapter["name"], **_pc})
                        if _pc.get("status") != "PASS":
                            all_passed = False
        except Exception as _vexc:
            checks.append({"artifact": "verification", "check": "connector_validation_error", "status": "FAIL", "detail": f"adapter validation errored: {type(_vexc).__name__}"})
            all_passed = False
        # Scope-identity binding: receipts stamped with tenant/project at
        # execution time must match THIS execution's context. Catches
        # cross-tenant, cross-project, and cross-workflow evidence reuse.
        # Unstamped (legacy) receipts skip these checks — never invented.
        for art in context.input_artifacts:
            _kind = art.get("kind", "unknown")
            _content = _resolve_content(art)
            _receipt: dict[str, Any] = {}
            if isinstance(_content, dict):
                _research = _content.get("research")
                if isinstance(_research, dict) and isinstance(_research.get("receipt"), dict):
                    _receipt = _research["receipt"]
                elif isinstance(_content.get("receipt"), dict):
                    _receipt = _content["receipt"]
            if not _receipt:
                continue
            for _label, _expected in (("tenant_id", context.tenant_id), ("project_id", context.project_id)):
                _claimed = str(_receipt.get(_label, "") or "")
                _want = str(_expected or "")
                if _claimed and _want:
                    if _claimed == _want:
                        checks.append({"artifact": _kind, "check": f"receipt_{_label}_match", "status": "PASS", "detail": f"{_label}={_claimed}"})
                    else:
                        checks.append({"artifact": _kind, "check": f"receipt_{_label}_match", "status": "FAIL", "detail": f"cross-scope evidence: receipt {_label}={_claimed!r} != context {_want!r}"})
                        all_passed = False
            # Receipt-gated generic verification: ANY artifact whose
            # content carries a connector receipt is generically verified,
            # even if provenance markers were stripped. Stripping
            # provenance therefore cannot dodge the integrity contract.
            _generic_any = GenericCapabilityVerifier.verify_artifact(
                art, _content if isinstance(_content, dict) else {},
                expected_capability=str(_receipt.get("capability") or _receipt.get("operation") or ""),
                expected_scope="",
            )
            if _generic_any is not None:
                for _g in _generic_any:
                    _g = dict(_g)
                    _g["detail"] = f"[receipt-gated] {_g.get('detail', '')}"
                    checks.append({"artifact": _kind, **_g})
                    if _g.get("status") != "PASS":
                        all_passed = False
        # Parent linkage: declared parents must reference actually-consumed
        # inputs. Catches wrong-parent, substitution, and dangling lineage.
        # (Empty parents on root observations are legitimate.)
        _input_ids = {a.get("artifact_id") for a in context.input_artifacts if a.get("artifact_id")}
        for art in context.input_artifacts:
            _kind = art.get("kind", "unknown")
            _parents = art.get("parent_artifacts") or []
            if not _parents:
                continue
            _dangling = [p for p in _parents if p not in _input_ids]
            if _dangling:
                checks.append({"artifact": _kind, "check": "parent_link_valid", "status": "FAIL", "detail": f"parents reference unconsumed artifacts: {_dangling}"})
                all_passed = False
            else:
                checks.append({"artifact": _kind, "check": "parent_link_valid", "status": "PASS", "detail": f"{len(_parents)} parent link(s) resolve to consumed inputs"})

        # Phase 7: optional model summary (never influences pass/fail).
        model_used = False
        model_summary = ""
        model_events: list[dict[str, Any]] = []
        strategy_descriptor: dict[str, Any] = {"requested": self.execution_strategy, "effective": "DETERMINISTIC"}
        try:
            import os as _os
            _strategy = ((context.parameters.get("execution_strategy") if isinstance(context.parameters, dict) else None) or self.execution_strategy or _os.getenv("NEXUS_EXECUTION_MODE", "DETERMINISTIC"))
            _router = self.model_router
            if _router is None and _strategy.strip().upper() in ("MODEL", "HYBRID"):
                try:
                    from runtime.model_router import ModelRouter as _MR
                    _router = _MR()
                except Exception:
                    _router = None
            if _router is not None and _strategy.strip().upper() in ("MODEL", "HYBRID"):
                from runtime.model_agent_support import run_model_enhancement as _enhance
                _res = _enhance(
                    agent_id=context.agent_id, agent_role=self.role,
                    agent_capabilities=self.capabilities,
                    agent_allowed_operations=self.allowed_operations,
                    agent_prohibited_operations=self.prohibited_operations,
                    objective=context.objective or context.scope,
                    task_name=context.task_name, task_type="verification",
                    constraints=context.constraints,
                    input_artifacts=[dict(a) for a in context.input_artifacts],
                    artifact_contents=[dict(a) for a in context.artifact_contents],
                    previous_messages=[dict(m) for m in (context.previous_messages or [])],
                    observed_evidence=[],
                    workspace_root=context.observation_scope,
                    expected_output="Verification summary only; do not assert VERIFIED — deterministic checks decide.",
                    strategy_value=_strategy, router=_router,
                    execution_environment=((context.parameters.get("execution_environment", "LOCAL")
                        if isinstance(context.parameters, dict) else "LOCAL")),
                )
                strategy_descriptor = _res.get("strategy", strategy_descriptor)
                model_events = list(_res.get("events", []))
                if _res.get("model_used"):
                    model_used = True
                    model_summary = str(_res.get("model_text", ""))[:2000]
        except Exception:
            model_used = False

        verification = {
            "verification_id": f"verify-{context.task_id}",
            "workflow_id": context.workflow_id,
            "timestamp": _now(),
            "checks": checks,
            "all_passed": all_passed,
            "required_kinds": required_kinds,
            "produced_kinds": list(produced_kinds),
            "artifact_count": len(context.input_artifacts),
            # Independent deterministic verdict; model never decides.
            "independent": True,
            "model_consulted": model_used,
        }
        if model_used and model_summary:
            verification["model_summary"] = {"text": model_summary, "reality": "INFERRED", "untrusted": True}

        # If all checks pass, this is a VERIFIED result (independent deterministic validation)
        reality = "VERIFIED" if all_passed else "INFERRED"
        untrusted = not all_passed

        artifact_content = {
            "verification_result": verification,
            "reality": reality,
            "untrusted": untrusted,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "timestamp": _now(),
            "independent": True,
            "parent_artifact_hashes": [a.get("content_hash") for a in context.input_artifacts if a.get("content_hash")],
        }

        artifact = {
            "kind": "verification_result",
            "name": "verification_result.json",
            "content": artifact_content,
            "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": [f"agent:{context.agent_id}", "type:verifier", "deterministic-verification", "independent-check"],
        }

        # Truth boundary for the verification TASK outcome.
        #
        # The verifier ran and produced a verdict. But a verdict of "these
        # artifacts failed verification" is a FAILED verification, and
        # reporting it as COMPLETED let a workflow advance past an unverified
        # result as if it had been checked out. (The previous code had
        # `"COMPLETED" if all_passed else "COMPLETED"` — a degenerate ternary
        # with no FAILED path at all.)
        #
        # The determination itself is still independent and deterministic: only
        # the checks decide, never the model.
        task_status = "COMPLETED" if all_passed else "FAILED"
        result = {
            "task_id": context.task_id,
            "task_name": context.task_name,
            "agent_id": context.agent_id,
            "status": task_status,
            "reality": reality,
            "untrusted": untrusted,
            "verification": verification,
            "all_passed": all_passed,
            "checks_total": len(checks),
            "checks_passed": sum(1 for c in checks if c["status"] == "PASS"),
            "failed_checks": [c for c in checks if c["status"] != "PASS"],
        }
        failure_reason = None if all_passed else (
            f"verification failed: {sum(1 for c in checks if c['status'] != 'PASS')} of "
            f"{len(checks)} independent checks did not pass"
        )

        if context.messaging_hub:
            context.messaging_hub.send(
                workflow_id=context.workflow_id,
                tenant_id=context.tenant_id,
                message_type="RESPONSE",
                content={
                    "agent_id": context.agent_id,
                    "status": "completed" if all_passed else "failed",
                    "all_passed": all_passed,
                    "checks_total": len(checks),
                    "checks_passed": sum(1 for c in checks if c["status"] == "PASS"),
                },
                from_agent_id=context.agent_id,
                task_id=context.task_id,
            )

        return AgentExecutionResult(
            task_id=context.task_id,
            agent_id=context.agent_id,
            status=task_status,
            reality=reality,
            untrusted=untrusted,
            result=result,
            artifacts=[artifact],
            error=failure_reason,
            provenance=[f"agent:{context.agent_id}", "type:verifier", "deterministic-verification"],
            execution_metadata={
                "execution_strategy": strategy_descriptor,
                "model_used": model_used,
                "model_invocations": model_events,
                "independent": True,
            },
        )

    def _determine_required_kinds(self, context: AgentContext) -> list[str]:
        """Determine which artifact kinds are required based on workflow task types upstream."""
        # Build required kinds based on the kinds of artifacts we received
        # A verifier should verify whatever artifacts were passed to it
        kinds = list(set(a.get("kind") for a in context.input_artifacts if a.get("kind")))
        if not kinds:
            # Default verification set
            kinds = ["research_report", "architecture_plan", "security_report"]
        return kinds
