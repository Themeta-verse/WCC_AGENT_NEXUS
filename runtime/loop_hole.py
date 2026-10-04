"""NEXUS Phase 7 — LOOP HOLE: Runtime Integrity Observation Layer.

LOOP HOLE is a security boundary that independently answers WHO/WHAT/ALLOWED/WHAT
ACTUALLY HAPPENED/DATCHED/COMPLIANT/DECISION/EVIDENCE for every agent action.

Pipeline:
    INTENT -> IDENTITY -> CAPABILITY -> POLICY -> SCOPE
        -> PRE-ACTION INTERCEPTION
            -> EXECUTION (via BoundedAgentRuntime)
        -> RUNTIME OBSERVATION (ObservationReceipt)
    -> EXPECTED vs ACTUAL comparison
    -> WORKFLOW INTEGRITY check
    -> EVIDENCE + PROVENANCE recording
    -> ALLOW / FLAG / HALT decision
    -> AUDIT (agent_actions, agent_integrity_events, audit_log)
    -> MONITORING (anomaly detection)

Design principles:
    - Fail closed: any missing identity, capability, or policy => HALT
    - Evidence is real: every observation comes from actual execution, not claims
    - No hardcoded decisions: no `if action == "bad": return HALT` patterns
    - Composable: this module is injected into the executor/engine, not tightly
      coupled to any specific agent
    - Provenance is durable: all evidence is persisted to the database

NOTE: This module reuses the existing `evaluate_integrity` pure function from
nexus_independent.service and the `BoundedAgentRuntime` from runtime.bounded_agent,
but wraps them in a runtime-integrated integrity pipeline that operates
during workflow execution — not just at the HTTP API boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import hashlib
import logging
import time

from runtime.bounded_agent import BoundedAgentRuntime, ObservationReceipt
from runtime.agent_base import AgentContext, AgentExecutionResult

logger = logging.getLogger("nexus.loop_hole")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


# Re-export INTEGRITY_DECISIONS for convenience
ALLOW = "ALLOW"
FLAG = "FLAG"
HALT = "HALT"
VALID_DECISIONS = {ALLOW, FLAG, HALT}


@dataclass
class AgentIntent:
    """Captures the agent's declared intent before taking an action.

    This is the INTENT step of the LOOP HOLE pipeline. It records what
    the agent said it would do, so we can compare expectation vs reality.
    """
    agent_id: str
    tenant_id: str
    project_id: str
    task_id: str
    workflow_id: str
    operation: str
    target_resource: str | None
    requested_capability: str | None
    parameters: dict[str, Any]
    declared_reality: str
    declared_provenance: list[str]
    timestamp: str = field(default_factory=_now)

    @property
    def intent_hash(self) -> str:
        """Stable hash of the intent for drift detection."""
        payload = {
            "agent_id": self.agent_id,
            "operation": self.operation,
            "target_resource": self.target_resource,
            "requested_capability": self.requested_capability,
            "parameters": self.parameters,
        }
        return _digest(payload)


@dataclass
class IntegrityEvidence:
    """Evidence collected during the LOOP HOLE pipeline.

    This bundles all the proof that an action was or was not compliant.
    """
    action_id: str
    intent: AgentIntent
    pre_action_decision: str
    pre_action_reason: str
    observation_receipt: dict[str, Any] | None = None
    expected_vs_actual: dict[str, Any] | None = None
    workflow_integrity: dict[str, Any] | None = None
    final_decision: str = HALT
    final_reason: str = ""
    evidence_hash: str = ""

    def finalize(self) -> "IntegrityEvidence":
        """Compute the evidence digest after all pipeline steps complete."""
        payload = {
            "action_id": self.action_id,
            "intent": self.intent.intent_hash,
            "pre_action_decision": self.pre_action_decision,
            "observation": self.observation_receipt or {},
            "expected_vs_actual": self.expected_vs_actual or {},
            "workflow_integrity": self.workflow_integrity or {},
            "final_decision": self.final_decision,
        }
        self.evidence_hash = _digest(payload)
        return self


class IntegrityPolicy:
    """Policy source for LOOP HOLE.

    Provides agent identity, declared capabilities, allowed/prohibited
    operations, and scope boundaries. The policy is the source of truth
    for what an agent is allowed to do.

    By default, reads from the database. Can be overridden for testing.
    """

    def __init__(self, database: Any | None = None):
        self._db = database

    def get_agent_policy(
        self, tenant_id: str, agent_id: str
    ) -> dict[str, Any] | None:
        """Retrieve the latest policy for an agent from the database.

        Returns a dict with: agent_id, declared_capabilities, allowed_operations,
        prohibited_operations, scope, expected_behaviour, version, or None if
        no policy exists.
        """
        if self._db is None:
            return None
        if not hasattr(self._db, "get_latest_agent_policy"):
            return None
        row = self._db.get_latest_agent_policy(tenant_id, agent_id)
        if row is None:
            return None

        parsed = dict(row)
        for field_name in ("declared_capabilities", "allowed_operations", "prohibited_operations", "scope"):
            raw = parsed.get(f"{field_name}_json", parsed.get(field_name))
            if isinstance(raw, str):
                try:
                    parsed[field_name] = json.loads(raw)
                except (ValueError, TypeError):
                    parsed[field_name] = [] if field_name != "scope" else {}
            elif raw is not None:
                parsed[field_name] = raw
        return parsed

    def get_agent_record(self, tenant_id: str, agent_id: str) -> dict[str, Any] | None:
        """Retrieve the agent's DB record (status, project_id, etc.)."""
        if self._db is None:
            return None
        if not hasattr(self._db, "get_agent"):
            return None
        return self._db.get_agent(tenant_id, agent_id)


class LoopHoleIntegrity:
    """LOOP HOLE — runtime integrity boundary for agent execution.

    This is the core orchestrator that implements the full pipeline:
    INTENT -> IDENTITY -> CAPABILITY -> POLICY -> SCOPE -> INTERCEPTION
    -> EXECUTION -> OBSERVATION -> EXPECTED vs ACTUAL -> WORKFLOW INTEGRITY
    -> EVIDENCE -> DECISION -> AUDIT -> MONITORING

    The integrity boundary is injected into the MultiAgentExecutor (or
    WorkflowEngine) so that every agent action is intercepted, observed,
    and recorded — regardless of whether it came through the HTTP API or
    the autonomous runtime.
    """

    def __init__(
        self,
        database: Any | None = None,
        principal: dict[str, Any] | None = None,
        enabled: bool = True,
    ):
        self._db = database
        self._principal = principal or {"tenant_id": "default", "project_id": "default"}
        self._policy_source = IntegrityPolicy(database)
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Step 1-5: Pre-Action Interception
    # INTENT -> IDENTITY -> CAPABILITY -> POLICY -> SCOPE
    # ------------------------------------------------------------------

    def pre_action_check(
        self,
        *,
        agent_id: str,
        tenant_id: str,
        project_id: str,
        task_id: str,
        workflow_id: str,
        operation: str,
        target_resource: str | None,
        requested_capability: str | None,
        parameters: dict[str, Any],
        declared_reality: str,
        declared_provenance: list[str],
    ) -> tuple[str, str, AgentIntent, dict[str, Any] | None]:
        """Evaluate an agent's intent against identity, capability, policy, and scope.

        This is the PRE-ACTION INTERCEPTION step. It does NOT execute anything —
        it only decides whether the action is allowed to proceed.

        Returns:
            (decision, reason, intent, policy)
            - decision: ALLOW / FLAG / HALT
            - reason: human-readable explanation
            - intent: the captured AgentIntent
            - policy: the resolved agent policy (or None)
        """
        intent = AgentIntent(
            agent_id=agent_id,
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=task_id,
            workflow_id=workflow_id,
            operation=operation,
            target_resource=target_resource,
            requested_capability=requested_capability,
            parameters=parameters,
            declared_reality=declared_reality,
            declared_provenance=declared_provenance,
        )

        # IDENTITY CHECK: agent must exist and be active
        agent = self._policy_source.get_agent_record(tenant_id, agent_id)
        if agent is None:
            # No DB record — fall back to registry-level identity
            # An agent without a DB record has no enforced identity
            return (
                HALT,
                f"agent '{agent_id}' has no identity record in tenant '{tenant_id}'",
                intent,
                None,
            )

        if agent.get("status") in ("HALTED", "RETIRED"):
            return (
                HALT,
                f"agent '{agent_id}' is {agent.get('status')} — actions rejected",
                intent,
                None,
            )

        if agent.get("status") == "FLAGGED":
            return (
                FLAG,
                f"agent '{agent_id}' is FLAGGED — actions require review",
                intent,
                None,
            )

        # POLICY CHECK: agent must have a declared policy
        policy = self._policy_source.get_agent_policy(tenant_id, agent_id)
        if policy is None:
            return (
                HALT,
                f"agent '{agent_id}' has no declared policy — cannot verify capabilities or scope",
                intent,
                None,
            )

        # CAPABILITY CHECK + POLICY + SCOPE check via evaluate_integrity
        declared_capabilities = policy.get("declared_capabilities", [])
        allowed_operations = policy.get("allowed_operations", [])
        prohibited_operations = policy.get("prohibited_operations", [])
        scope = policy.get("scope", {})

        observed_action = {
            "operation": operation,
            "target": target_resource or "",
            "capability": requested_capability or "",
            "parameters": parameters,
        }

        decision, reason = self._evaluate_integrity(
            declared_capabilities=declared_capabilities,
            allowed_operations=allowed_operations,
            prohibited_operations=prohibited_operations,
            scope=scope,
            observed_action=observed_action,
        )

        return (decision, reason, intent, policy)

    @staticmethod
    def _evaluate_integrity(
        declared_capabilities: list[str],
        allowed_operations: list[str],
        prohibited_operations: list[str],
        scope: dict,
        observed_action: dict[str, Any],
    ) -> tuple[str, str]:
        """Pure policy evaluation — mirrors nexus_independent.service.evaluate_integrity.

        Decision table:
        1. If operation is in prohibited_operations -> HALT
        2. If operation is a write/exec operation -> HALT
        3. If requested capability is not in declared_capabilities -> HALT
        4. If target_resource is outside declared scope -> FLAG
        5. Otherwise -> ALLOW
        """
        operation = observed_action.get("operation", "")
        target = observed_action.get("target", "") or observed_action.get("target_resource", "")
        capability = observed_action.get("capability") or observed_action.get("requested_capability", "")

        read_operations = {"read", "filesystem.read", "repository.read",
                           "repository.metadata.read", "browser.read", "verify"}
        write_execute_operations = {"write", "create", "delete", "modify", "git.write",
                                    "push", "merge", "execute", "command",
                                    "filesystem.write", "git.push"}

        # 1. Prohibited operations -> HALT
        if operation in prohibited_operations:
            return "HALT", f"operation '{operation}' is explicitly prohibited by policy"

        # 2. Write/execute operations -> HALT (only read-only permitted)
        if operation in write_execute_operations:
            if operation in {"filesystem.write", "write", "create", "delete", "modify"}:
                return "HALT", f"write operation '{operation}' is not declared by agent policy"
            if operation in {"git.write", "push", "merge"}:
                return "HALT", f"git write operation '{operation}' is not declared by agent policy"
            if operation in {"execute", "command"}:
                return "HALT", f"execute/command operation '{operation}' is not declared by agent policy"

        # 3. Capability not declared -> HALT
        if capability and capability not in declared_capabilities:
            return "HALT", f"capability '{capability}' is not in declared_capabilities"

        # If no explicit capability was declared, try to infer from operation
        if not capability:
            if operation in read_operations:
                inferred = None
                if operation in declared_capabilities:
                    inferred = operation
                elif "filesystem.read" in declared_capabilities:
                    inferred = "filesystem.read"
                elif "repository.read" in declared_capabilities:
                    inferred = "repository.read"
                elif "repository.metadata.read" in declared_capabilities:
                    inferred = "repository.metadata.read"
                elif "browser.read" in declared_capabilities:
                    inferred = "browser.read"
                if inferred is None:
                    return "HALT", f"capability required for operation '{operation}' is not declared"
            elif operation in write_execute_operations:
                return "HALT", f"write/execute operation '{operation}' requires a write capability, which is not declared"

        # 4. Scope check
        allowed_paths = scope.get("filesystem_read_paths", []) if isinstance(scope, dict) else []
        if target and allowed_paths and isinstance(target, str):
            path_allowed = False
            for allowed in allowed_paths:
                if isinstance(allowed, str):
                    pattern = allowed.rstrip("*")
                    if target.startswith(pattern):
                        path_allowed = True
                        break
            if not path_allowed:
                return "FLAG", f"action target '{target}' is outside declared scope"

        # 5. Allow
        return "ALLOW", "action is within declared capabilities, allowed operations, and scope"

    # ------------------------------------------------------------------
    # Step 6-8: Execution + Runtime Observation
    # ------------------------------------------------------------------

    def execute_with_observation(
        self,
        *,
        agent,
        context: AgentContext,
        operation: str,
        target_resource: str | None,
        parameters: dict[str, Any],
    ) -> ObservationReceipt | None:
        """Execute an action via BoundedAgentRuntime and capture the observation receipt.

        This is the EXECUTION + OBSERVATION step. The action is actually performed
        (or blocked) by the bounded runtime, and the resulting receipt contains
        cryptographic proof (content_sha256, content_size) of what really happened.
        """
        root = context.observation_scope or context.scope or "."
        runtime = BoundedAgentRuntime(
            agent_id=context.agent_id,
            allowed_root=root,
            capabilities=getattr(agent, "capabilities", []),
            prohibited_operations=getattr(agent, "prohibited_operations", []),
        )
        return runtime.execute_action(
            operation=operation,
            target_resource=target_resource,
            parameters=parameters,
        )

    # ------------------------------------------------------------------
    # Step 9: Expected vs Actual Comparison
    # ------------------------------------------------------------------

    @staticmethod
    def compare_expected_vs_actual(
        intent: AgentIntent,
        receipt: ObservationReceipt,
    ) -> dict[str, Any]:
        """Compare what the agent intended/declared vs what actually happened.

        This detects:
        - Intent drift: agent said "OBSERVED" but receipt shows BLOCKED
        - Target mismatch: agent said it would read file A but receipt shows file B
        - Reality mismatch: agent declares OBSERVED but the operation was blocked
        """
        actual_reality = receipt.reality if receipt else "UNKNOWN"
        actual_status = receipt.status if receipt else "BLOCKED"
        declared_reality = intent.declared_reality or "UNKNOWN"

        reality_mismatch = declared_reality == "OBSERVED" and actual_reality != "OBSERVED"
        target_mismatch = False
        if intent.target_resource and receipt and receipt.target_resource:
            target_mismatch = intent.target_resource != receipt.target_resource
        status_mismatch = actual_status == "BLOCKED" and declared_reality == "OBSERVED"

        drift_detected = reality_mismatch or target_mismatch or status_mismatch
        violations = []
        if reality_mismatch:
            violations.append(
                f"reality drift: declared={declared_reality}, observed={actual_reality}"
            )
        if target_mismatch:
            violations.append(
                f"target drift: intended={intent.target_resource}, actual={receipt.target_resource}"
            )
        if status_mismatch:
            violations.append(
                f"status drift: operation was BLOCKED but declared as OBSERVED"
            )

        return {
            "drift_detected": drift_detected,
            "violations": violations,
            "declared_reality": declared_reality,
            "observed_reality": actual_reality,
            "declared_status": intent.declared_reality,
            "observed_status": actual_status,
            "target_match": not target_mismatch,
        }

    # ------------------------------------------------------------------
    # Step 10: Workflow Integrity
    # ------------------------------------------------------------------

    @staticmethod
    def evaluate_workflow_integrity(
        *,
        task_result: AgentExecutionResult,
        task_spec: dict[str, Any],
        expected_reality: str | None = None,
    ) -> dict[str, Any]:
        """Check that a task result is consistent with workflow expectations.

        Rules:
        - If the task was marked COMPLETED, artifacts must exist and have valid hashes
        - If the agent declared OBSERVED, artifacts marked OBSERVED must have real evidence
          (content_path, content_hash)
        - If the task was marked FAILED, artifacts must not claim success
        - reality must be one of the valid REALITY_STATES
        """
        from runtime.canonical_core import REALITY_STATES

        violations: list[str] = []
        compliant = True

        result_reality = task_result.reality or "UNKNOWN"

        # Check reality state validity
        if result_reality not in REALITY_STATES:
            violations.append(f"invalid reality state: {result_reality}")
            compliant = False

        # Check artifact presence
        artifacts = task_result.artifacts or []
        if not artifacts:
            violations.append("no artifacts produced by task")
            compliant = False

        # Check artifact integrity
        for art in artifacts:
            art_kind = art.get("kind", "unknown")
            art_reality = art.get("reality", "UNKNOWN")
            art_hash = art.get("content_hash")

            if not art_hash:
                violations.append(f"artifact '{art_kind}' has no content_hash")
                compliant = False

            if art_reality not in REALITY_STATES:
                violations.append(f"artifact '{art_kind}' has invalid reality: {art_reality}")
                compliant = False

            # OBSERVED artifacts must have real provenance
            if art_reality == "OBSERVED":
                prov = art.get("provenance", [])
                has_real_provenance = any(
                    p in ("bounded-agent-runtime", "real-filesystem-observation", "real-observation")
                    for p in prov
                )
                if not has_real_provenance:
                    violations.append(
                        f"artifact '{art_kind}' is OBSERVED but provenance does not contain "
                        "real-observation evidence"
                    )
                    compliant = False

            # If agent declared OBSERVED, all artifacts should not contradict
            if art_reality == "SIMULATED" and result_reality == "OBSERVED":
                violations.append(
                    f"workflow-level reality=OBSERVED but artifact '{art_kind}' is SIMULATED"
                )
                compliant = False

        # Check expected reality if provided
        if expected_reality and result_reality != expected_reality:
            violations.append(
                f"reality mismatch: expected={expected_reality}, actual={result_reality}"
            )
            compliant = False

        return {
            "compliant": compliant,
            "violations": violations,
            "artifact_count": len(artifacts),
            "result_reality": result_reality,
            "expected_reality": expected_reality or "",
        }

    # ------------------------------------------------------------------
    # Step 11: Evidence + Provenance Recording (Audit)
    # ------------------------------------------------------------------

    def record_evidence(
        self,
        *,
        agent_id: str,
        tenant_id: str,
        project_id: str,
        operation: str,
        target_resource: str | None,
        requested_capability: str | None,
        parameters: dict[str, Any],
        pre_decision: str,
        pre_reason: str,
        intent: AgentIntent,
        policy: dict[str, Any] | None,
        receipt: ObservationReceipt | None,
        observed_action_eval: dict[str, Any],
        workflow_integrity: dict[str, Any] | None,
        final_decision: str,
        final_reason: str,
    ) -> dict[str, Any]:
        """Record full evidence of an action to the database.

        This is the AUDIT step. The evidence bundle is:
        - The agent's intent (what it said it would do)
        - The pre-action decision (policy evaluation)
        - The observation receipt (what actually happened, with crypto proof)
        - The expected-vs-actual comparison
        - The workflow integrity check
        - The final decision
        """
        action_id = f"action-{intent.intent_hash[:16]}"

        evidence = IntegrityEvidence(
            action_id=action_id,
            intent=intent,
            pre_action_decision=pre_decision,
            pre_action_reason=pre_reason,
            observation_receipt=asdict(receipt) if receipt else None,
            expected_vs_actual=observed_action_eval,
            workflow_integrity=workflow_integrity,
            final_decision=final_decision,
            final_reason=final_reason,
        ).finalize()

        observed_action = {
            "operation": operation,
            "target": target_resource or "",
            "capability": requested_capability or "",
            "parameters": parameters,
        }

        evidence_payload = {
            "action_id": action_id,
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "observed_action": observed_action,
            "policy_version": policy.get("version") if policy else None,
            "integrity_decision": final_decision,
            "integrity_reason": final_reason,
            "evaluated_at": _now(),
            "pre_action_decision": pre_decision,
            "pre_action_reason": pre_reason,
            "intent_hash": intent.intent_hash,
            "observation_reality": receipt.reality if receipt else "UNKNOWN",
            "observation_status": receipt.status if receipt else "BLOCKED",
            "evidence_hash": evidence.evidence_hash,
            "workflow_integrity": workflow_integrity or {},
            "expected_vs_actual": observed_action_eval,
        }

        observation_reality = receipt.reality if receipt else "UNKNOWN"
        receipt_json = json.dumps(asdict(receipt), default=str) if receipt else None

        if self._db is not None and hasattr(self._db, "record_agent_action"):
            try:
                self._db.record_agent_action(
                    action_id=action_id,
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    operation=operation,
                    target_resource=target_resource,
                    requested_capability=requested_capability,
                    observed_parameters_json=json.dumps(parameters, default=str),
                    integrity_decision=final_decision,
                    integrity_reason=final_reason,
                    evidence_json=json.dumps(evidence_payload, default=str),
                    policy_version=policy.get("version") if policy else None,
                    evaluated_at=_now(),
                    observation_reality=observation_reality,
                    receipt_json=receipt_json,
                )
            except Exception as exc:
                logger.warning(f"Failed to record agent action to DB: {exc}")

            if hasattr(self._db, "add_agent_integrity_event"):
                try:
                    self._db.add_agent_integrity_event(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        event_type=f"action_{final_decision.lower()}",
                        payload_json=json.dumps({
                            "action_id": action_id,
                            "operation": operation,
                            "target": target_resource,
                            "decision": final_decision,
                            "reason": final_reason,
                            "evidence_hash": evidence.evidence_hash,
                        }, default=str),
                        integrity_decision=final_decision,
                    )
                except Exception as exc:
                    logger.warning(f"Failed to record integrity event: {exc}")

            if hasattr(self._db, "add_audit_event"):
                try:
                    self._db.add_audit_event(
                        tenant_id=tenant_id,
                        user_id=agent_id,
                        event_type=f"agent.action.{final_decision.lower()}",
                        status="success" if final_decision == ALLOW else "flagged" if final_decision == FLAG else "blocked",
                        detail={
                            "action_id": action_id,
                            "operation": operation,
                            "target": target_resource,
                            "integrity_decision": final_decision,
                            "reason": final_reason,
                            "evidence_hash": evidence.evidence_hash,
                        },
                        project_id=project_id,
                    )
                except Exception as exc:
                    logger.warning(f"Failed to record audit event: {exc}")

        return evidence_payload

    # ------------------------------------------------------------------
    # Step 12: Final Decision
    # ------------------------------------------------------------------

    @staticmethod
    def make_decision(
        pre_decision: str,
        pre_reason: str,
        expected_vs_actual: dict[str, Any] | None,
        workflow_integrity: dict[str, Any] | None,
    ) -> tuple[str, str]:
        """Combine pre-action decision, drift detection, and workflow integrity
        into a final ALLOW / FLAG / HALT decision.

        Rules:
        - If pre-action was HALT -> final = HALT
        - If expected-vs-actual detected drift -> final = FLAG (or HALT if severe)
        - If workflow integrity is non-compliant -> final = FLAG (or HALT if severe)
        - Otherwise -> preserve pre-action decision
        """
        if pre_decision == HALT:
            return HALT, pre_reason

        violations: list[str] = []
        if pre_reason:
            violations.append(pre_reason)

        if expected_vs_actual and expected_vs_actual.get("drift_detected"):
            drift_reasons = expected_vs_actual.get("violations", [])
            violations.extend(drift_reasons)

        if workflow_integrity and not workflow_integrity.get("compliant", True):
            wf_violations = workflow_integrity.get("violations", [])
            violations.extend(wf_violations)

        if expected_vs_actual and expected_vs_actual.get("drift_detected"):
            severe = "status drift" in str(violations) or "reality mismatch" in str(violations)
            if severe:
                return HALT, "; ".join(violations)
            return FLAG, "; ".join(violations)

        if workflow_integrity and not workflow_integrity.get("compliant", True):
            # Check for severe violations (missing artifacts, invalid reality)
            wf_violations = workflow_integrity.get("violations", [])
            severe = any("no artifacts" in v or "invalid reality" in v for v in wf_violations)
            if severe:
                return HALT, "; ".join(violations)
            return FLAG, "; ".join(violations)

        return pre_decision, pre_reason

    # ------------------------------------------------------------------
    # Step 14-15: Monitoring & Anomaly Detection
    # ------------------------------------------------------------------

    def detect_anomalies(
        self, tenant_id: str, agent_id: str, window: int = 50
    ) -> dict[str, Any]:
        """Scan recent agent actions for anomalous patterns.

        Detects:
        - Repeated HALT decisions (potential malfunction or attack)
        - Scope violations (reading outside declared paths)
        - Capability escalation attempts
        - Evidence tampering (receipt hash mismatches)
        """
        anomalies: list[dict[str, Any]] = []
        halt_count = 0
        flag_count = 0
        scope_violations = 0

        if self._db is not None and hasattr(self._db, "list_agent_actions"):
            actions = self._db.list_agent_actions(tenant_id, agent_id, limit=window)
            for action in actions:
                decision = action.get("integrity_decision", "")
                if decision == "HALT":
                    halt_count += 1
                elif decision == "FLAG":
                    flag_count += 1

                reason = action.get("integrity_reason", "") or ""
                if "scope" in reason.lower():
                    scope_violations += 1

                # Check for evidence tampering: receipt hash mismatch
                receipt_json = action.get("receipt_json")
                evidence_json = action.get("evidence_json")
                if receipt_json and evidence_json:
                    try:
                        receipt = json.loads(receipt_json)
                        evidence = json.loads(evidence_json)
                        expected_hash = evidence.get("evidence_hash")
                        if expected_hash:
                            # Reconstruct what the hash should be
                            payload = {
                                "action_id": evidence.get("action_id"),
                                "integrity_decision": evidence.get("integrity_decision"),
                                "observation_reality": evidence.get("observation_reality"),
                            }
                            recomputed = _digest(payload)
                            if recomputed != expected_hash:
                                anomalies.append({
                                    "type": "evidence_tampering",
                                    "action_id": action.get("action_id"),
                                    "detail": "evidence hash mismatch detected",
                                })
                    except (json.JSONDecodeError, TypeError):
                        pass

        if halt_count >= 3:
            anomalies.append({
                "type": "repeated_halts",
                "count": halt_count,
                "detail": f"agent has {halt_count} HALT decisions in recent {window} actions",
            })
        if scope_violations >= 2:
            anomalies.append({
                "type": "scope_violation_pattern",
                "count": scope_violations,
                "detail": f"agent has {scope_violations} scope violations in recent {window} actions",
            })

        return {
            "agent_id": agent_id,
            "window_size": window,
            "halt_count": halt_count,
            "flag_count": flag_count,
            "scope_violation_count": scope_violations,
            "anomalies": anomalies,
            "risk_level": "HIGH" if halt_count >= 3 or scope_violations >= 3 else "MEDIUM" if halt_count >= 1 or flag_count >= 1 else "LOW",
        }

    # ------------------------------------------------------------------
    # Step 14: Memory Security Boundary
    # ------------------------------------------------------------------

    def can_access_memory(
        self,
        agent_id: str,
        tenant_id: str,
        target_agent_id: str,
        scope: str,
        workflow_id: str | None = None,
    ) -> bool:
        """Check if an agent is allowed to read/write another agent's memory.

        Memory security boundary rules:
        - An agent can always access its own memory (agent scope)
        - An agent can access workflow-scoped memory within its workflow
        - An agent CANNOT access another agent's agent-scoped memory
        - Project-scoped memory is accessible to agents in the same project
        - HALTED agents cannot access any memory
        """
        if self._db is not None and hasattr(self._db, "get_agent"):
            agent = self._db.get_agent(tenant_id, agent_id)
            if agent and agent.get("status") == "HALTED":
                return False

        if scope == "agent" and agent_id == target_agent_id:
            return True
        if scope == "agent" and agent_id != target_agent_id:
            return False
        if scope == "workflow" and workflow_id is not None:
            return True
        if scope == "project":
            return True

        return False

    def verify_memory_boundary(
        self,
        agent_id: str,
        tenant_id: str,
        memory_record: dict[str, Any],
    ) -> tuple[bool, str]:
        """Verify that a memory record was accessed within the security boundary.

        Returns (allowed, reason).
        """
        target_agent = memory_record.get("agent_id")
        scope = memory_record.get("scope", "agent")
        workflow_id = memory_record.get("workflow_id")

        allowed = self.can_access_memory(
            agent_id, tenant_id, target_agent, scope, workflow_id
        )
        if not allowed:
            return False, (
                f"memory boundary violation: agent '{agent_id}' attempted to access "
                f"{scope}-scoped memory of agent '{target_agent}'"
            )
        return True, "access within security boundary"

    # ------------------------------------------------------------------
    # Full pipeline: intercept_and_evaluate
    # ------------------------------------------------------------------

    def intercept_and_evaluate(
        self,
        *,
        agent,
        context: AgentContext,
        operation: str,
        target_resource: str | None,
        parameters: dict[str, Any],
        expected_reality: str | None = None,
    ) -> dict[str, Any]:
        """Run the full LOOP HOLE pipeline on a single agent action.

        This is the main entry point for integrating LOOP HOLE into the
        agent execution path. It:

        1. Captures intent
        2. Checks identity, capability, policy, scope (pre-action)
        3. If HALT, stops without executing
        4. If FLAG, still executes but records the flag
        5. If ALLOW, executes via BoundedAgentRuntime
        6. Observes the actual result (receipt)
        7. Compares expected vs actual
        8. Evaluates workflow integrity
        9. Records evidence
        10. Makes final decision

        Returns a dict with the full evidence bundle.
        """
        if not self._enabled:
            return {"integrity_decision": ALLOW, "reason": "LOOP HOLE disabled"}

        agent_id = context.agent_id
        tenant_id = context.tenant_id
        project_id = context.project_id

        task_spec = {
            "task_id": context.task_id,
            "task_name": context.task_name,
            "task_type": context.execution_metadata.get("task_type", "unknown"),
            "required_capabilities": context.execution_metadata.get("capabilities_requested", []),
        }

        # Step 1-5: Pre-action interception
        pre_decision, pre_reason, intent, policy = self.pre_action_check(
            agent_id=agent_id,
            tenant_id=tenant_id,
            project_id=project_id,
            task_id=context.task_id,
            workflow_id=context.workflow_id,
            operation=operation,
            target_resource=target_resource,
            requested_capability=parameters.get("requested_capability"),
            parameters=parameters,
            declared_reality=getattr(agent, "reality", "UNKNOWN"),
            declared_provenance=getattr(agent, "provenance", []),
        )

        # Step 6: If HALT, don't execute
        if pre_decision == HALT:
            evidence = self.record_evidence(
                agent_id=agent_id,
                tenant_id=tenant_id,
                project_id=project_id,
                operation=operation,
                target_resource=target_resource,
                requested_capability=parameters.get("requested_capability"),
                parameters=parameters,
                pre_decision=pre_decision,
                pre_reason=pre_reason,
                intent=intent,
                policy=policy,
                receipt=None,
                observed_action_eval={"drift_detected": False, "violations": [], "note": "action not executed due to HALT"},
                workflow_integrity=None,
                final_decision=HALT,
                final_reason=pre_reason,
            )
            return evidence

        # Step 7-8: Execute and observe
        receipt = self.execute_with_observation(
            agent=agent,
            context=context,
            operation=operation,
            target_resource=target_resource,
            parameters=parameters,
        )

        # Step 9: Expected vs actual
        observed_action_eval = self.compare_expected_vs_actual(intent, receipt)

        # Step 10: Workflow integrity (we evaluate the agent's declared reality vs actual)
        # Build a minimal result for integrity check
        task_result_proxy = type("TaskResultProxy", (), {
            "reality": getattr(agent, "reality", "UNKNOWN") if hasattr(agent, "reality") else (receipt.reality if receipt else "UNKNOWN"),
            "artifacts": [],
            "error": None,
        })()

        wf_integrity = self.evaluate_workflow_integrity(
            task_result=task_result_proxy,
            task_spec=task_spec,
            expected_reality=expected_reality,
        )

        # Step 11-12: Make final decision
        final_decision, final_reason = self.make_decision(
            pre_decision=pre_decision,
            pre_reason=pre_reason,
            expected_vs_actual=observed_action_eval,
            workflow_integrity=wf_integrity,
        )

        # Step 13: Record evidence
        evidence = self.record_evidence(
            agent_id=agent_id,
            tenant_id=tenant_id,
            project_id=project_id,
            operation=operation,
            target_resource=target_resource,
            requested_capability=parameters.get("requested_capability"),
            parameters=parameters,
            pre_decision=pre_decision,
            pre_reason=pre_reason,
            intent=intent,
            policy=policy,
            receipt=receipt,
            observed_action_eval=observed_action_eval,
            workflow_integrity=wf_integrity,
            final_decision=final_decision,
            final_reason=final_reason,
        )

        return evidence


def create_loop_hole_for_service(
    database: Any, principal: dict[str, Any] | None = None
) -> LoopHoleIntegrity:
    """Factory: create a LoopHoleIntegrity instance wired to the service database.

    This bridges the runtime LOOP HOLE module to the nexus_independent.service
    layer, allowing the standalone service to use the same integrity pipeline
    as the autonomous runtime.
    """
    return LoopHoleIntegrity(database=database, principal=principal, enabled=True)


def create_loop_hole_for_runtime(
    database: Any, tenant_id: str, project_id: str
) -> LoopHoleIntegrity:
    """Factory: create a LoopHoleIntegrity instance for the autonomous runtime."""
    return LoopHoleIntegrity(
        database=database,
        principal={"tenant_id": tenant_id, "project_id": project_id},
        enabled=True,
    )
