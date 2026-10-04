"""Product orchestration around the canonical read-only MissionComposer.

This layer owns authentication, tenant authorization, database queueing, and
worker lifecycle. It intentionally does not duplicate the canonical mission
planner, provider semantics, verifier, or LocalStateStore checkpoint format.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4
from dataclasses import asdict
import hashlib
import json
import logging
import re
import time

logger = logging.getLogger("nexus.service")

from runtime.canonical_pilot import DirectGitHubAPIAdapter
from runtime.filesystem_provider import FilesystemReadProvider
from runtime.github_provider import GitHubReadProvider
from runtime.mission_composer import MissionComposer
from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy, WorkflowSpec

from .config import ProductSettings
from .database import NexusDatabase, _safe_slug, utc_now
from .schemas import (
    MissionSubmission,
    AgentCreateRequest,
    AgentPolicyCreateRequest,
    AgentActionSubmission,
    AgentObservationRequest,
    AgentEnforceRequest,
    WorkflowCreateRequest,
    WorkflowAgentSpec,
    WorkflowPlanRequest,
)


PROJECT_ROLE_RANK = {"viewer": 1, "operator": 2, "owner": 3}


def _parse_policy_row(policy: dict[str, Any] | None) -> dict[str, Any] | None:
    """Present stored policy JSON columns as typed arrays/objects for API clients."""
    if policy is None:
        return None
    parsed = dict(policy)
    for field in ("declared_capabilities", "allowed_operations", "prohibited_operations", "scope"):
        raw = parsed.get(f"{field}_json", parsed.get(field))
        if isinstance(raw, str):
            try:
                parsed[field] = json.loads(raw)
            except (ValueError, TypeError):
                parsed[field] = [] if field != "scope" else {}
        elif raw is not None and field not in parsed:
            parsed[field] = raw
    for stale in ("declared_capabilities_json", "allowed_operations_json", "prohibited_operations_json", "scope_json"):
        parsed.pop(stale, None)
    return parsed


def evaluate_integrity(
    declared_capabilities: list[str],
    allowed_operations: list[str],
    prohibited_operations: list[str],
    scope: dict,
    observed_action: dict[str, Any],
) -> tuple[str, str]:
    """Deterministic policy comparison. Returns (decision, reason).

    Decision table (pure function, no LLM, no heuristics):

    1. If the operation is in prohibited_operations      -> HALT
    2. If the operation is not a read capability            -> HALT  (only read-only actions permitted)
    3. If the required capability is not in declared_capabilities -> HALT
    4. If target_resource does not match scope patterns     -> FLAG
    5. Otherwise                                            -> ALLOW
    """
    operation = observed_action.get("operation", "")
    target = observed_action.get("target", "") or observed_action.get("target_resource", "")
    capability = observed_action.get("capability") or observed_action.get("requested_capability", "")

    read_operations = {"read", "filesystem.read", "repository.read", "repository.metadata.read", "browser.read"}
    write_execute_operations = {"write", "create", "delete", "modify", "git.write", "push", "merge", "execute", "command"}

    if operation in prohibited_operations:
        return "HALT", f"operation '{operation}' is explicitly prohibited by policy"

    if operation in write_execute_operations:
        if operation in {"filesystem.write", "write", "create", "delete", "modify"}:
            return "HALT", f"write operation '{operation}' is not declared by agent policy"
        if operation in {"git.write", "push", "merge"}:
            return "HALT", f"git write operation '{operation}' is not declared by agent policy"
        if operation == "execute" or operation == "command":
            return "HALT", f"execute/command operation '{operation}' is not declared by agent policy"

    if capability and capability not in declared_capabilities:
        return "HALT", f"capability '{capability}' is not in declared_capabilities"

    if not capability:
        inferred = None
        if operation in read_operations:
            inferred = operation if operation in declared_capabilities else (
                "filesystem.read" if "filesystem.read" in declared_capabilities else
                "repository.read" if "repository.read" in declared_capabilities else
                "repository.metadata.read" if "repository.metadata.read" in declared_capabilities else
                "browser.read" if "browser.read" in declared_capabilities else
                None
            )
        elif inferred is None and operation in {"filesystem.write", "write"}:
            return "HALT", "write operation requires filesystem.write which is not declared"
        if inferred is None:
            return "HALT", f"capability required for operation '{operation}' is not declared"

    allowed_paths = scope.get("filesystem_read_paths", []) if isinstance(scope, dict) else []
    scope_violation = False
    if target and allowed_paths and isinstance(target, str):
        path_allowed = False
        for allowed in allowed_paths:
            if isinstance(allowed, str):
                pattern = allowed.rstrip("*")
                if target.startswith(pattern):
                    path_allowed = True
                    break
        if not path_allowed:
            scope_violation = True

    if scope_violation:
        return "FLAG", f"action target '{target}' is outside declared scope"

    return "ALLOW", "action is within declared capabilities, allowed operations, and scope"


class StandaloneMissionService:
    def __init__(self, settings: ProductSettings | None = None):
        self.settings = settings or ProductSettings.from_env()
        if self.settings.database_url and not self.settings.database_url.startswith("sqlite://"):
            raise ValueError("DATABASE_URL is configured for an unavailable engine; this product build currently executes only sqlite:// URLs")
        self.settings.ensure_directories()
        self.database = NexusDatabase(self.settings.database_path)
        self.database.migrate()
        self._bootstrap_from_settings()

    @staticmethod
    def _safe_project_id(project_id: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]", "-", project_id).strip("-").lower() or "local"

    @staticmethod
    def _public_identity(identity: dict[str, Any]) -> dict[str, Any]:
        return {key: identity[key] for key in ("user_id", "tenant_id", "email", "role") if key in identity}

    def _bootstrap_from_settings(self) -> None:
        email = self.settings.bootstrap_owner_email
        password = self.settings.bootstrap_owner_password
        if bool(email) != bool(password):
            raise ValueError("NEXUS_BOOTSTRAP_OWNER_EMAIL and NEXUS_BOOTSTRAP_OWNER_PASSWORD must be supplied together")
        if email and password:
            self.bootstrap_owner(email, password, self.settings.bootstrap_tenant_name, self.settings.bootstrap_project_id)

    def bootstrap_owner(self, email: str, password: str, tenant_name: str = "NEXUS", project_id: str = "local") -> dict[str, Any]:
        tenant = self.database.get_or_create_tenant(tenant_name)
        user = self.database.create_user(tenant["tenant_id"], email, password, role="owner")
        adopted = self.database.adopt_legacy_projects(tenant["tenant_id"])
        project = self.database.create_project(tenant["tenant_id"], self._safe_project_id(project_id), "Primary command center")
        self.database.grant_project_member(project["project_id"], user["user_id"], "owner")
        for legacy_project in adopted:
            self.database.grant_project_member(legacy_project, user["user_id"], "owner")
        return {"tenant": tenant, "user": self._public_identity(user), "project": project, "adopted_projects": adopted}

    def setup_status(self) -> dict[str, Any]:
        return {"initial_owner_setup_available": self.database.initial_owner_setup_available(), "owner_registration_available": self.settings.allow_owner_registration}

    def setup_initial_owner(self, email: str, password: str) -> dict[str, Any]:
        created = self.database.create_initial_owner(email, password, self.settings.bootstrap_tenant_name, self.settings.bootstrap_project_id)
        session = self.login(email, password)
        if session is None:
            raise RuntimeError("initial owner session could not be created")
        self.database.add_audit_event(created["tenant"]["tenant_id"], created["user"]["user_id"], "auth.initial_owner_setup", "success", {"project_id": created["project"]["project_id"]})
        return session

    def register_owner_workspace(self, email: str, password: str) -> dict[str, Any]:
        if not self.settings.allow_owner_registration:
            raise PermissionError("owner registration is disabled by product configuration")
        created = self.database.create_registered_owner(email, password)
        session = self.login(email, password)
        if session is None:
            raise RuntimeError("owner workspace session could not be created")
        self.database.add_audit_event(created["tenant"]["tenant_id"], created["user"]["user_id"], "auth.owner_workspace_registration", "success", {"project_id": created["project"]["project_id"]})
        return session

    def login(self, email: str, password: str) -> dict[str, Any] | None:
        user = self.database.authenticate_password(email, password)
        if not user:
            return None
        token, session = self.database.create_session(user["user_id"], self.settings.session_hours)
        self.database.add_audit_event(user["tenant_id"], user["user_id"], "auth.login", "success", {"session_id": session["session_id"]})
        return {"access_token": token, "token_type": "bearer", "expires_at": session["expires_at"], "user": self._public_identity(user), "projects": self.database.list_projects_for_user(user["user_id"], user["tenant_id"])}

    def authenticate_bearer(self, token: str) -> dict[str, Any] | None:
        identity = self.database.resolve_session(token)
        return self._public_identity(identity) if identity else None

    def logout(self, token: str) -> None:
        identity = self.database.resolve_session(token)
        self.database.revoke_session(token)
        if identity:
            self.database.add_audit_event(identity["tenant_id"], identity["user_id"], "auth.logout", "success", {"session_id": identity["session_id"]})

    def reset_owner_password(self, email: str, new_password: str) -> dict[str, Any]:
        """Development-host owner recovery: rotate one owner's password and revoke its sessions.

        This is intentionally NOT exposed over HTTP. It is only reachable through the
        local CLI, which additionally gates on an explicit environment flag plus a
        confirmation flag. Tenants, projects, missions, and evidence are untouched.
        """
        user = self.database.get_user_by_email(email)
        if user is None or not user.get("active"):
            raise ValueError("no active product account exists for this email")
        if user.get("role") != "owner":
            raise ValueError("password recovery is available only for tenant owners")
        updated = self.database.set_user_password(user["user_id"], new_password)
        if updated is None:
            raise ValueError("no active product account exists for this email")
        revoked = self.database.revoke_user_sessions(user["user_id"])
        self.database.add_audit_event(
            updated["tenant_id"],
            updated["user_id"],
            "auth.owner_password_reset",
            "success",
            {"initiated_via": "local-dev-recovery-cli", "sessions_revoked": revoked},
        )
        return {"user": self._public_identity(updated), "sessions_revoked": revoked}

    def list_projects(self, principal: dict[str, Any]) -> list[dict[str, Any]]:
        return self.database.list_projects_for_user(principal["user_id"], principal["tenant_id"])

    def create_project(self, principal: dict[str, Any], project_id: str, display_name: str) -> dict[str, Any]:
        if principal["role"] != "owner":
            raise PermissionError("only a tenant owner can create a project")
        project = self.database.create_project(principal["tenant_id"], self._safe_project_id(project_id), display_name)
        self.database.grant_project_member(project["project_id"], principal["user_id"], "owner")
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "project.create", "success", {"display_name": project["display_name"]}, project_id=project["project_id"])
        return project

    def _require_project_role(self, principal: dict[str, Any], project_id: str, minimum: str = "viewer") -> str:
        role = self.database.project_role(principal["user_id"], principal["tenant_id"], self._safe_project_id(project_id))
        if not role or PROJECT_ROLE_RANK[role] < PROJECT_ROLE_RANK[minimum]:
            raise PermissionError("project membership does not permit this request")
        return role

    def _require_agent_access(self, principal: dict[str, Any], agent_id: str, minimum: str = "viewer") -> dict[str, Any]:
        agent = self.database.get_agent(principal["tenant_id"], agent_id)
        if agent is None:
            raise PermissionError("agent not found")
        self._require_project_role(principal, agent["project_id"], minimum)
        return agent

    def create_agent(self, principal: dict[str, Any], request: AgentCreateRequest) -> dict[str, Any]:
        if principal["role"] not in {"owner", "operator"}:
            raise PermissionError("only tenant owner or operator can create agents")
        project_id = self._safe_project_id(request.project_id)
        self._require_project_role(principal, project_id, "operator")
        raw_id = (request.agent_id or request.display_name or "").strip()
        if not raw_id:
            raise ValueError("agent identifier or display name is required")
        agent_id = _safe_slug(raw_id)
        if self.database.get_agent(principal["tenant_id"], agent_id) is not None:
            raise ValueError(f"agent identifier already exists: {agent_id}")
        try:
            agent = self.database.create_agent(
                agent_id=agent_id,
                tenant_id=principal["tenant_id"],
                project_id=project_id,
                display_name=request.display_name.strip(),
                status="ACTIVE",
            )
        except Exception as exc:
            if "UNIQUE" in str(exc) or "PRIMARY" in str(exc):
                raise ValueError(f"agent identifier already exists: {agent_id}") from exc
            raise
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "agent.create", "success", {"agent_id": agent_id, "display_name": request.display_name}, project_id=project_id)
        return agent

    def list_agents(self, principal: dict[str, Any], project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            safe_project = self._safe_project_id(project_id)
            self._require_project_role(principal, safe_project, "viewer")
            return self.database.list_agents(principal["tenant_id"], safe_project)
        memberships = self.database.list_projects_for_user(principal["user_id"], principal["tenant_id"])
        if not memberships:
            raise PermissionError("project membership does not permit this request")
        return self.database.list_agents(principal["tenant_id"])

    def get_agent(self, principal: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
        return self._require_agent_access(principal, agent_id, "viewer")

    def create_agent_policy(self, principal: dict[str, Any], agent_id: str, request: AgentPolicyCreateRequest) -> dict[str, Any]:
        agent = self._require_agent_access(principal, agent_id, "operator")
        existing_policies = self.database.list_agent_policies(principal["tenant_id"], agent_id)
        version = (max((p["version"] for p in existing_policies), default=0) + 1) if existing_policies else 1
        policy_id = f"policy-{uuid4()}"
        policy = self.database.create_agent_policy(
            policy_id=policy_id,
            agent_id=agent_id,
            tenant_id=principal["tenant_id"],
            declared_capabilities=request.declared_capabilities,
            allowed_operations=request.allowed_operations,
            prohibited_operations=request.prohibited_operations,
            scope=request.scope,
            expected_behaviour=request.expected_behaviour,
            version=version,
        )
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "agent.policy.create", "success", {"agent_id": agent_id, "policy_id": policy_id, "version": version}, project_id=agent["project_id"])
        return _parse_policy_row(policy) or policy

    def get_agent_policy(self, principal: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
        self._require_agent_access(principal, agent_id, "viewer")
        return _parse_policy_row(self.database.get_latest_agent_policy(principal["tenant_id"], agent_id))

    def list_agent_policies(self, principal: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
        self._require_agent_access(principal, agent_id, "viewer")
        return [_parse_policy_row(policy) or policy for policy in self.database.list_agent_policies(principal["tenant_id"], agent_id)]

    def record_agent_action(self, principal: dict[str, Any], agent_id: str, request: AgentActionSubmission) -> dict[str, Any]:
        agent = self._require_agent_access(principal, agent_id, "viewer")
        if agent["status"] == "HALTED":
            self.database.add_audit_event(
                principal["tenant_id"], principal["user_id"],
                "agent.action.rejected", "halted",
                {"agent_id": agent_id, "reason": "agent is HALTED"},
                project_id=agent["project_id"],
            )
            raise PermissionError("agent is HALTED; subsequent actions are rejected")

        policy = self.database.get_latest_agent_policy(principal["tenant_id"], agent_id)
        declared_capabilities = json.loads(policy["declared_capabilities_json"]) if policy else []
        allowed_operations = json.loads(policy["allowed_operations_json"]) if policy else []
        prohibited_operations = json.loads(policy["prohibited_operations_json"]) if policy else []
        scope = json.loads(policy["scope_json"]) if policy else {}

        observed_action = {
            "operation": request.operation,
            "target": request.target_resource,
            "capability": request.requested_capability,
            "parameters": request.parameters,
        }

        decision, reason = evaluate_integrity(
            declared_capabilities=declared_capabilities,
            allowed_operations=allowed_operations,
            prohibited_operations=prohibited_operations,
            scope=scope,
            observed_action=observed_action,
        )

        action_id = f"action-{uuid4()}"
        evidence = {
            "action_id": action_id,
            "agent_id": agent_id,
            "tenant_id": principal["tenant_id"],
            "observed_action": observed_action,
            "policy_version": policy["version"] if policy else None,
            "integrity_decision": decision,
            "integrity_reason": reason,
            "evaluated_at": utc_now(),
        }

        action = self.database.record_agent_action(
            action_id=action_id,
            agent_id=agent_id,
            tenant_id=principal["tenant_id"],
            project_id=agent["project_id"],
            operation=request.operation,
            target_resource=request.target_resource,
            requested_capability=request.requested_capability,
            observed_parameters_json=json.dumps(request.parameters, default=str),
            integrity_decision=decision,
            integrity_reason=reason,
            evidence_json=json.dumps(evidence, default=str),
            policy_version=policy["version"] if policy else None,
            evaluated_at=evidence["evaluated_at"],
            observation_reality="MANUAL",
        )

        self.database.add_agent_integrity_event(
            principal["tenant_id"],
            agent_id,
            "action_observed",
            json.dumps({
                "action_id": action_id,
                "operation": request.operation,
                "target": request.target_resource,
                "integrity_decision": decision,
                "reason": reason,
            }, default=str),
            integrity_decision=decision,
        )

        if decision in {"FLAG", "HALT"}:
            new_status = "FLAGGED" if decision == "FLAG" else "HALTED"
            self.database.update_agent_status(principal["tenant_id"], agent_id, new_status)
            self.database.add_audit_event(
                principal["tenant_id"], principal["user_id"],
                "agent.integrity_violation", decision.lower(),
                {"agent_id": agent_id, "action_id": action_id, "decision": decision, "reason": reason},
                project_id=agent["project_id"],
            )
            self.database.add_agent_integrity_event(
                principal["tenant_id"], agent_id,
                f"agent_{decision.lower()}" if decision == "HALT" else "agent_flagged",
                json.dumps({"action_id": action_id, "new_status": new_status, "reason": reason}, default=str),
                integrity_decision=decision,
            )

        return {
            "action": action,
            "integrity_decision": decision,
            "integrity_reason": reason,
            "evidence": evidence,
        }

    def get_agent_actions(self, principal: dict[str, Any], agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self._require_agent_access(principal, agent_id, "viewer")
        rows = self.database.list_agent_actions(principal["tenant_id"], agent_id, limit)
        return [{**row, "observed_parameters": json.loads(row["observed_parameters_json"]), "evidence": json.loads(row["evidence_json"]) if row.get("evidence_json") else None} for row in rows]

    def get_agent_integrity(self, principal: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
        agent = self._require_agent_access(principal, agent_id, "viewer")
        policy = self.database.get_latest_agent_policy(principal["tenant_id"], agent_id)
        parsed_policy = _parse_policy_row(policy) if policy else None
        actions = self.get_agent_actions(principal, agent_id, limit=20)
        events = self.database.list_agent_integrity_events(principal["tenant_id"], agent_id, limit=20)
        return {
            "agent": agent,
            "policy": parsed_policy,
            "recent_actions": actions,
            "integrity_events": events,
        }

    def observe_agent_action(self, principal: dict[str, Any], agent_id: str, request: AgentObservationRequest) -> dict[str, Any]:
        """Observe a real action performed by a bounded local agent runtime.

        Unlike record_agent_action (which ingests an API claim), this method
        causes the BoundedAgentRuntime to actually perform or attempt the
        operation, then observes the real result. The observation receipt
        contains cryptographic evidence (content hash, size) that proves
        what genuinely occurred.
        """
        from runtime.bounded_agent import BoundedAgentRuntime

        agent = self._require_agent_access(principal, agent_id, "operator")
        if agent["status"] == "HALTED":
            self.database.add_audit_event(
                principal["tenant_id"], principal["user_id"],
                "agent.action.rejected", "halted",
                {"agent_id": agent_id, "reason": "agent is HALTED"},
                project_id=agent["project_id"],
            )
            raise PermissionError("agent is HALTED; subsequent actions are rejected")

        policy = self.database.get_latest_agent_policy(principal["tenant_id"], agent_id)
        declared_capabilities = json.loads(policy["declared_capabilities_json"]) if policy else []
        allowed_operations = json.loads(policy["allowed_operations_json"]) if policy else []
        prohibited_operations = json.loads(policy["prohibited_operations_json"]) if policy else []
        scope = json.loads(policy["scope_json"]) if policy else {}

        # Actually execute the action via the bounded runtime.
        # The runtime reads /project/src files inside the configured root,
        # or is blocked for out-of-root / write operations.
        root = request.observation_root
        runtime = BoundedAgentRuntime(
            agent_id=agent_id,
            allowed_root=root,
            capabilities=declared_capabilities,
            prohibited_operations=prohibited_operations,
        )
        receipt = runtime.execute_action(
            operation=request.operation,
            target_resource=request.target_resource,
            parameters={**request.parameters, "requested_capability": request.requested_capability or ""},
        )
        receipt_dict = asdict(receipt)

        observed_action = {
            "operation": receipt.operation,
            "target": receipt.target_resource,
            "capability": receipt.requested_capability,
            "parameters": request.parameters,
            "execution_status": receipt.status,
        }

        decision, reason = evaluate_integrity(
            declared_capabilities=declared_capabilities,
            allowed_operations=allowed_operations,
            prohibited_operations=prohibited_operations,
            scope=scope,
            observed_action=observed_action,
        )

        # If the operation was blocked by the runtime (out-of-root, write prohibited),
        # the integrity decision must reflect that as HALT (since the capability was not declared).
        if receipt.status == "BLOCKED" and decision == "ALLOW":
            if request.operation in {"filesystem.write", "git.push", "push", "merge", "execute", "command"}:
                decision = "HALT"
                reason = f"operation '{request.operation}' is explicitly prohibited by policy"
            elif not request.target_resource.startswith(root):
                decision = "FLAG"
                reason = f"action target '{request.target_resource}' is outside declared scope"

        action_id = f"action-{uuid4()}"
        evidence = {
            "action_id": action_id,
            "agent_id": agent_id,
            "tenant_id": principal["tenant_id"],
            "observed_action": observed_action,
            "policy_version": policy["version"] if policy else None,
            "integrity_decision": decision,
            "integrity_reason": reason,
            "evaluated_at": utc_now(),
            "observation_receipt": receipt_dict,
            "observation_source": "runtime_observation_bridge",
        }

        action = self.database.record_agent_action(
            action_id=action_id,
            agent_id=agent_id,
            tenant_id=principal["tenant_id"],
            project_id=agent["project_id"],
            operation=request.operation,
            target_resource=request.target_resource,
            requested_capability=request.requested_capability,
            observed_parameters_json=json.dumps(request.parameters, default=str),
            integrity_decision=decision,
            integrity_reason=reason,
            evidence_json=json.dumps(evidence, default=str),
            policy_version=policy["version"] if policy else None,
            evaluated_at=evidence["evaluated_at"],
            observation_reality="OBSERVED",
            receipt_json=json.dumps(receipt_dict, default=str),
        )

        self.database.add_agent_integrity_event(
            principal["tenant_id"],
            agent_id,
            "action_observed",
            json.dumps({
                "action_id": action_id,
                "operation": request.operation,
                "target": request.target_resource,
                "integrity_decision": decision,
                "reason": reason,
                "observation_source": "runtime_observation_bridge",
                "receipt_id": receipt.receipt_id,
                "content_sha256": receipt.content_sha256,
            }, default=str),
            integrity_decision=decision,
        )

        if decision in {"FLAG", "HALT"}:
            new_status = "FLAGGED" if decision == "FLAG" else "HALTED"
            self.database.update_agent_status(principal["tenant_id"], agent_id, new_status)
            self.database.add_audit_event(
                principal["tenant_id"], principal["user_id"],
                "agent.integrity_violation", decision.lower(),
                {"agent_id": agent_id, "action_id": action_id, "decision": decision, "reason": reason},
                project_id=agent["project_id"],
            )
            self.database.add_agent_integrity_event(
                principal["tenant_id"], agent_id,
                f"agent_{decision.lower()}" if decision == "HALT" else "agent_flagged",
                json.dumps({"action_id": action_id, "new_status": new_status, "reason": reason}, default=str),
                integrity_decision=decision,
            )

        return {
            "action": action,
            "integrity_decision": decision,
            "integrity_reason": reason,
            "evidence": evidence,
            "observation_receipt": receipt_dict,
            "reality": receipt.reality,
        }

    def enforce_agent(self, principal: dict[str, Any], agent_id: str, action: str) -> dict[str, Any]:
        action_upper = action.strip().upper()
        if action_upper not in {"FLAG", "HALT", "ACTIVE"}:
            raise ValueError("action must be one of: FLAG, HALT, ACTIVE")
        agent = self._require_agent_access(principal, agent_id, "owner")
        new_status = {"FLAG": "FLAGGED", "HALT": "HALTED", "ACTIVE": "ACTIVE"}[action_upper]
        updated = self.database.update_agent_status(principal["tenant_id"], agent_id, new_status)
        if updated is None:
            raise PermissionError("agent not found")
        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "agent.enforce", "success",
            {"agent_id": agent_id, "action": action_upper, "new_status": new_status},
            project_id=agent["project_id"],
        )
        self.database.add_agent_integrity_event(
            principal["tenant_id"], agent_id,
            "enforcement",
            json.dumps({"action": action_upper, "new_status": new_status, "initiated_by": principal["user_id"]}, default=str),
            integrity_decision=action_upper if action_upper != "ACTIVE" else None,
        )
        return updated

    def _mission_for_principal(self, principal: dict[str, Any], mission_id: str, minimum: str = "viewer") -> dict[str, Any] | None:
        record = self.database.get_mission(mission_id)
        if record is None:
            return None
        if record.get("tenant_id") != principal["tenant_id"]:
            raise PermissionError("mission belongs to another tenant")
        self._require_project_role(principal, record["project_id"], minimum)
        return record

    def _store_root(self, project_id: str) -> Path:
        root = self.settings.state_root / self._safe_project_id(project_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _composer(self) -> MissionComposer:
        """Legacy MissionComposer mission path (enqueue_mission API).

        Uses the LEGACY provider-bound contract (GitHubReadProvider, etc.).
        Production workflow execution (engine -> executor -> agents) does NOT
        route through here — it uses the canonical connector registry +
        capability fabric. Retained for the mission API and benchmarks.
        """
        composer = MissionComposer()
        composer.providers["github-read"] = GitHubReadProvider(
            DirectGitHubAPIAdapter(
                token=self.settings.github_token,
                api_base=self.settings.github_api_base,
                timeout=self.settings.github_timeout_seconds,
            )
        )
        composer.provider = composer.providers["github-read"]
        composer.providers["filesystem-read"] = FilesystemReadProvider([self.settings.allowed_filesystem_root])
        return composer

    def _get_tenant_id(self) -> str:
        """Get the tenant ID for this runtime instance."""
        return self.settings.tenant_id if hasattr(self.settings, "tenant_id") else "standalone"

    def _artifacts_root(self) -> str | None:
        """Get the root directory for storing workflow artifact content."""
        if hasattr(self.settings, "allowed_filesystem_root"):
            return self.settings.allowed_filesystem_root
        return None

    def enqueue_mission(self, principal: dict[str, Any], submission: MissionSubmission) -> dict[str, Any]:
        if submission.mode == "REAL_READ" and not self.settings.allow_real_reads:
            raise PermissionError("REAL_READ is disabled by standalone product configuration")
        project_id = self._safe_project_id(submission.project_id)
        self._require_project_role(principal, project_id, "operator")
        store_root = self._store_root(project_id)
        mission_id = f"mission-{uuid4()}"
        self.database.create_mission(
            mission_id=mission_id,
            tenant_id=principal["tenant_id"],
            project_id=project_id,
            scope=submission.scope,
            intent=submission.intent,
            mode=submission.mode,
            capabilities=submission.capabilities or [],
            store_root=str(store_root),
            submission=submission.model_dump(mode="json"),
            max_attempts=self.settings.queue_max_attempts,
        )
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "mission.enqueue", "success", {"mode": submission.mode, "capabilities": submission.capabilities or []}, project_id=project_id, mission_id=mission_id)
        return self.get_mission(principal, mission_id, include_result=False) or {"mission_id": mission_id, "status": "QUEUED"}

    def _execute_record(self, record: dict[str, Any]) -> dict[str, Any]:
        submission = MissionSubmission.model_validate(record.get("submission") or {})
        composer = self._composer()
        package = composer.compose_capability_mission(
            submission.intent,
            scope=submission.scope,
            mode=submission.mode,
            browser_url=submission.browser_url or self.settings.browser_url,
            filesystem_path=submission.filesystem_path or str(self.settings.product_root / "README.md"),
            capabilities=submission.capabilities,
            store_root=record["store_root"],
            repository_scope=submission.repository_scope or self.settings.github_repository,
        )
        package["mission"]["mission_id"] = record["mission_id"]
        return composer.execute_capability_mission(package, record["store_root"], submission.mode)

    def worker_once(self, worker_id: str | None = None) -> dict[str, Any] | None:
        worker_id = worker_id or f"worker-{uuid4()}"
        self.database.heartbeat_worker(worker_id, "ACTIVE", {"queue": self.database.queue_health()})
        record = self.database.claim_next(worker_id, self.settings.worker_lease_seconds)
        if record is None:
            return None
        mission_id = record["mission_id"]
        try:
            result = self._execute_record(record)
            self.database.save_result(mission_id, result)
            checkpoint = Path(record["store_root"]) / "current.json"
            if checkpoint.exists():
                checksum = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
                self.database.record_checkpoint(record["tenant_id"], record["project_id"], mission_id, str(checkpoint), checksum, result.get("mission", {}).get("state", "UNKNOWN"))
            self.database.finish_queue(mission_id, worker_id)
            return self.database.get_mission(mission_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.database.retry_or_fail_queue(mission_id, worker_id, error)
            return self.database.get_mission(mission_id)

    def run_worker(self, worker_id: str | None = None, once: bool = False) -> int:
        worker_id = worker_id or f"worker-{uuid4()}"
        processed = 0
        while True:
            result = self.worker_once(worker_id)
            if result:
                processed += 1
            if once:
                return processed
            if not result:
                self.database.heartbeat_worker(worker_id, "ACTIVE", {"queue": self.database.queue_health(), "idle": True})
                time.sleep(self.settings.worker_poll_seconds)

    def submit_and_execute(self, principal: dict[str, Any], submission: MissionSubmission) -> dict[str, Any]:
        """CLI-only compatibility helper; API clients must use the durable queue."""
        queued = self.enqueue_mission(principal, submission)
        self.worker_once("cli-inline-worker")
        return self.get_mission(principal, queued["mission_id"], include_result=True) or queued

    def get_mission(self, principal: dict[str, Any], mission_id: str, include_result: bool = False) -> dict[str, Any] | None:
        record = self._mission_for_principal(principal, mission_id)
        if record is None:
            return None
        if not include_result:
            record.pop("result", None)
        return record

    def list_missions(self, principal: dict[str, Any], project_id: str, limit: int = 30) -> list[dict[str, Any]]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        records = self.database.list_missions(safe_project, limit)
        for record in records:
            record.pop("result", None)
        return records

    def mission_evidence(self, principal: dict[str, Any], mission_id: str) -> list[dict[str, Any]]:
        if not self._mission_for_principal(principal, mission_id):
            return []
        return self.database.evidence(mission_id)

    def mission_events(self, principal: dict[str, Any], mission_id: str) -> list[dict[str, Any]]:
        if not self._mission_for_principal(principal, mission_id):
            return []
        return self.database.events(mission_id)

    def recover(self, principal: dict[str, Any], mission_id: str) -> dict[str, Any] | None:
        record = self._mission_for_principal(principal, mission_id, "operator")
        if record is None:
            return None
        composer = self._composer()
        recovery = composer.recover(record["store_root"], record["scope"])
        self.database.add_event(mission_id, "standalone_recovery_checked", recovery)
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "mission.recover", "success", {"recovery_status": recovery.get("status")}, project_id=record["project_id"], mission_id=mission_id)
        return {"mission": self.get_mission(principal, mission_id, include_result=False), "recovery": recovery}

    def continue_mission(self, principal: dict[str, Any], mission_id: str) -> dict[str, Any] | None:
        """Recover the persisted canonical state; never fabricate or re-run a completed mission."""
        recovered = self.recover(principal, mission_id)
        if recovered is None:
            return None
        mission = recovered["mission"]
        self.database.add_event(mission_id, "mission_continued", {"recovery_status": recovered["recovery"].get("status"), "execution_restarted": False})
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "mission.continue", "success", {"recovery_status": recovered["recovery"].get("status")}, project_id=mission["project_id"], mission_id=mission_id)
        return recovered

    def control_mission(self, principal: dict[str, Any], mission_id: str, control: str) -> dict[str, Any] | None:
        record = self._mission_for_principal(principal, mission_id, "operator")
        if record is None:
            return None
        state = {"pause": "PAUSED", "resume": "QUEUED", "cancel": "CANCELLED"}.get(control)
        if state is None:
            raise ValueError("unknown mission control")
        if not self.database.control_mission(mission_id, state):
            raise ValueError("mission cannot be controlled while executing or after it is missing")
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], f"mission.{control}", "success", {"state": state}, project_id=record["project_id"], mission_id=mission_id)
        return self.get_mission(principal, mission_id, include_result=False)

    def list_memory(self, principal: dict[str, Any], project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        return self.database.list_memory(principal["tenant_id"], safe_project, limit)

    def update_memory(self, principal: dict[str, Any], project_id: str, memory_id: str, action: str, note: str | None = None) -> dict[str, Any] | None:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "operator")
        record = self.database.set_memory_lifecycle(principal["tenant_id"], safe_project, memory_id, principal["user_id"], action, note)
        if record:
            self.database.add_audit_event(principal["tenant_id"], principal["user_id"], f"memory.{action}", "success", {"memory_id": memory_id, "note_present": bool(note)}, project_id=safe_project)
        return record

    def project_context(self, principal: dict[str, Any], project_id: str) -> dict[str, Any]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        return self.database.project_context(principal["tenant_id"], safe_project)

    def list_outcomes(self, principal: dict[str, Any], project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        return self.database.list_outcomes(principal["tenant_id"], safe_project, limit)

    def list_audit_events(self, principal: dict[str, Any], project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if project_id:
            self._require_project_role(principal, self._safe_project_id(project_id), "viewer")
        return self.database.list_audit_events(principal["tenant_id"], project_id, limit)

    def mission_checkpoints(self, principal: dict[str, Any], mission_id: str) -> list[dict[str, Any]]:
        record = self._mission_for_principal(principal, mission_id, "viewer")
        if record is None:
            return []
        return self.database.list_checkpoints(principal["tenant_id"], mission_id)

    def capabilities(self, principal: dict[str, Any]) -> list[dict[str, Any]]:
        provider_state = self.providers(principal)
        contracts = [
            ("repository.metadata.read", "github-read", "read-only"),
            ("repository.read", "github-read", "read-only"),
            ("browser.read", "browser-read", "read-only"),
            ("filesystem.read", "filesystem-read", "bounded-read-only"),
        ]
        return [{"capability": capability, "provider": provider, "risk": risk, "side_effects": False, "status": provider_state.get(provider, {}).get("status", "UNAVAILABLE"), "availability": bool(provider_state.get(provider, {}).get("availability")), "authorization": "READ_ONLY_AUTHORIZED" if provider_state.get(provider, {}).get("availability") else "NOT_AVAILABLE"} for capability, provider, risk in contracts]

    def providers(self, principal: dict[str, Any]) -> dict[str, Any]:
        history = self.database.provider_history(principal["tenant_id"])
        composer = self._composer()
        providers: dict[str, Any] = {}
        for name, provider in composer.providers.items():
            if not hasattr(provider, "health"):
                continue
            current = provider.health()
            providers[name] = {**current, "identity": name, "authorization": "READ_ONLY_AUTHORIZED" if current.get("availability") else "NOT_AVAILABLE", "risk": "LOW_READ_ONLY", "side_effects": False, "execution_state": "EXECUTED" if history.get(name, {}).get("last_execution") else "NOT_EXECUTED", **history.get(name, {})}
        return providers

    def database_inspection(self, principal: dict[str, Any]) -> dict[str, Any]:
        if principal["role"] != "owner":
            raise PermissionError("only a tenant owner can inspect product database facts")
        counts = self.database.workflow_row_count(principal["tenant_id"])
        base = self.database.tenant_inspection(principal["tenant_id"])
        base["row_counts"]["workflows"] = counts["workflows"]
        base["row_counts"]["workflow_tasks"] = counts["workflow_tasks"]
        base["row_counts"]["workflow_artifacts"] = counts["workflow_artifacts"]
        base["row_counts"]["workflow_events"] = counts["workflow_events"]
        return base

    def health(self) -> dict[str, Any]:
        composer = self._composer()
        provider_health = {name: provider.health() for name, provider in composer.providers.items() if hasattr(provider, "health")}
        database = self.database.health()
        queue = database["queue"]
        workers = database["workers"]
        if database["users"] == 0:
            lifecycle = "REQUIRES_ATTENTION"
            reason = "No product owner exists. Bootstrap an owner before mission access is possible."
        elif queue["failed"]:
            lifecycle = "REQUIRES_ATTENTION"
            reason = "One or more missions are in a durable failed state and require operator review."
        elif (queue["queued"] or queue["leased"]) and workers["active_count"] == 0:
            lifecycle = "DEGRADED"
            reason = "Durable mission work is waiting but no active worker heartbeat is present."
        elif queue["leased"]:
            lifecycle = "RECOVERING"
            reason = "A worker holds an active lease; execution or recovery is in progress."
        else:
            lifecycle = "READY"
            reason = "Database is healthy and no blocked durable mission requires action."
        return {
            "service": "nexus-independent",
            "status": lifecycle,
            "runtime_state": {"state": lifecycle, "reason": reason, "configured_database_url": self.settings.database_url or f"sqlite:///{self.settings.database_path}", "database_engine": "sqlite", "database_portability": "SQLITE_EXECUTED__POSTGRESQL_UNAVAILABLE"},
            "database": database,
            "providers": provider_health,
            "real_reads_enabled": self.settings.allow_real_reads,
            "authorization_boundary": "authenticated tenant members may invoke read-only providers only; consequential operations are not exposed",
            "authentication": {"mode": "product-owned-session-bearer", "bootstrap_owner_configured": bool(self.settings.bootstrap_owner_email and self.settings.bootstrap_owner_password), "session_hours": self.settings.session_hours},
            "queue": {"worker_command": "nexus-independent worker", "lease_seconds": self.settings.worker_lease_seconds, "max_attempts": self.settings.queue_max_attempts, "worker_health": workers},
            "github": {"transport": "direct-github-rest", "authentication": "PRODUCT_MANAGED_TOKEN" if self.settings.github_token else "PUBLIC_READ_ONLY"},
        }

    def public_health(self) -> dict[str, Any]:
        """Return readiness only; tenant and host details require product authentication."""
        private = self.health()
        return {
            "service": "nexus-independent",
            "status": private["status"],
            "database": {"status": "HEALTHY"},
            "authorization_boundary": "product authentication and tenant membership are required for mission data",
            "initial_owner_setup_available": self.database.initial_owner_setup_available(),
            "owner_registration_available": self.settings.allow_owner_registration,
        }

    def diagnostics(self, principal: dict[str, Any]) -> dict[str, Any]:
        del principal
        return self.health()

    def model_status(self) -> dict[str, Any]:
        """Phase 7: redacted model runtime status (no secrets, safe for UI/traces)."""
        from runtime.model_router import ModelRouter
        from runtime.model_strategy import ExecutionStrategy
        import os as _os
        router = ModelRouter()
        status = router.redacted_status()
        requested = (_os.getenv("NEXUS_EXECUTION_MODE", "DETERMINISTIC") or "DETERMINISTIC").strip().upper()
        if requested not in ("DETERMINISTIC", "MODEL", "HYBRID"):
            requested = "DETERMINISTIC"
        effective = requested if (requested in ("MODEL", "HYBRID") and status.get("is_configured")) else "DETERMINISTIC"
        if effective == "DETERMINISTIC" and not status.get("is_configured"):
            provider_label = "NOT_CONFIGURED"
        else:
            provider_label = status.get("provider", "NOT_CONFIGURED")
        return {
            "execution_mode": effective,
            "requested_mode": requested,
            "provider": provider_label,
            "model": status.get("model", "default"),
            "is_configured": bool(status.get("is_configured")),
            "fallback_provider": status.get("fallback_provider", ""),
            "registered_providers": status.get("registered_providers", []),
            "reality_boundary": "model outputs are always INFERRED and untrusted; never OBSERVED",
            "secret_exposure": "none: API keys never enter artifacts, messages, logs, traces, or responses",
        }

    # ---- Workflow Engine ------------------------------------------------------

    def _workflow_engine(self, principal: dict[str, Any] | None = None) -> WorkflowEngine:
        """Create a WorkflowEngine instance with the current service's database and composer.

        The executor is bound to the requesting principal's tenant/project so
        artifact resolution, workflow reads, and message persistence use the
        same tenant scope as the workflow rows themselves.
        """
        from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
        from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
        from runtime.agent_registry import AgentRegistry
        from runtime.messaging_hub import MessagingHub

        # Create or retrieve agent registry for this tenant
        tenant_id = (principal or {}).get("tenant_id") or self._get_tenant_id()
        project_id = (principal or {}).get("project_id", "")
        registry = getattr(self, "_agent_registry", None)
        if registry is None:
            registry = AgentRegistry()
            register_default_agents(registry)
            self._agent_registry = registry

        artifact_root = self._artifacts_root()
        messaging_hub = MessagingHub(self.database)

        engine = WorkflowEngine(
            database=self.database,
            composer=self._composer(),
            policy=WorkflowExecutionPolicy(
                max_retries_default=self.settings.queue_max_attempts,
                timeout_seconds_default=300,
                fail_on_agent_not_available=False,
                auto_retry_on_failure=True,
            ),
            agent_registry=registry,
            artifacts_root=artifact_root,
            messaging_hub=messaging_hub,
        )

        # Canonical capability registry: exactly one per process (github+git).
        # Wired here so EVERY engine/executor (CLI, API, worker, tests) carries
        # the same registry object even before AutonomousRuntime is built.
        # initialize_capability_registry() is idempotent (process singleton),
        # so this never creates a duplicate instance. Init failure is logged
        # explicitly — the executor then honestly reports unavailable
        # capabilities instead of silently lacking a registry.
        try:
            from runtime.capability_fabric import initialize_capability_registry as _init_caps
            _gh_reg = _init_caps()
            _gh_conn = _gh_reg.get_connector("github") if hasattr(_gh_reg, "get_connector") else None
        except Exception as exc:
            logger.warning("capability registry init failed: %s: %s", type(exc).__name__, exc)
            _gh_conn, _gh_reg = None, None

        # Set up the multi-agent executor with messaging hub
        _executor = MultiAgentExecutor(
            database=self.database,
            agent_registry=registry,
            settings=self.settings,
            principal={"tenant_id": tenant_id, "project_id": project_id},
            messaging_hub=messaging_hub,
            connector_registry=_gh_reg,
        )
        engine.set_executor(
            _executor,
            agent_registry=registry,
            connector_registry=_gh_reg,
        )
        # Keep the single registry reachable from the engine for diagnostics.
        try:
            engine.connector_registry = _gh_reg
        except Exception as exc:
            logger.warning("engine connector_registry wiring failed: %s: %s", type(exc).__name__, exc)

        return engine

    def create_workflow(self, principal: dict[str, Any], request: WorkflowCreateRequest) -> dict[str, Any]:
        """Create a new workflow from a specification."""
        requested_project = getattr(request, "project_id", None) or "local"
        safe_project = self._safe_project_id(requested_project)
        self._require_project_role(principal, safe_project, "operator")

        spec = WorkflowSpec(
            name=request.name,
            objective=request.objective,
            scope=request.scope,
            task_specs=[t.model_dump(mode="json") for t in request.task_specs],
            agents=[a.model_dump(mode="json") for a in request.agents],
            execution_mode=request.execution_mode,
        )

        engine = self._workflow_engine(principal)

        # Execute through the real multi-agent stack (registry-dispatched
        # specialist agents with messaging + observation scope). The legacy
        # WorkflowTaskExecutor is intentionally NOT used here: it has no
        # messaging hub, no observation_scope, and no agent registry.
        from runtime.multi_agent_executor import MultiAgentExecutor
        engine.set_executor(MultiAgentExecutor(
            database=self.database,
            agent_registry=engine._agent_registry,
            settings=self.settings,
            principal=principal,
            messaging_hub=engine.messaging_hub,
        ), agent_registry=engine._agent_registry)

        workflow = engine.create_workflow(
            tenant_id=principal["tenant_id"],
            project_id=safe_project,
            spec=spec,
        )

        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "workflow.created", "success",
            {"workflow_id": workflow["workflow_id"], "name": request.name, "task_count": len(spec.task_specs)},
            project_id=safe_project,
        )
        return workflow

    def plan_workflow(self, principal: dict[str, Any], request: WorkflowPlanRequest) -> dict[str, Any]:
        """Generate a validated workflow plan from a high-level objective.

        Uses the WorkflowPlanner to classify the objective, match agents from
        the AgentRegistry, generate a dependency-aware task graph, and validate.

        The plan is returned for review — it is NOT persisted or executed yet.
        """
        safe_project = self._safe_project_id(request.project_id)
        self._require_project_role(principal, safe_project, "operator")

        from runtime.workflow_planner import WorkflowPlanner
        from runtime.workflow_engine import WorkflowSpec

        engine = self._workflow_engine(principal)
        planner = WorkflowPlanner(agent_registry=engine._agent_registry)

        planned = planner.plan(
            objective=request.objective,
            scope=request.scope,
            constraints=request.constraints,
            tenant_id=principal["tenant_id"],
            execution_mode=request.execution_mode,
            project_id=safe_project,
        )

        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "workflow.planned",
            "success" if planned.is_valid else "validation_failed",
            {"workflow_id": planned.workflow_id, "template_type": planned.plan.get("template_type"), "valid": planned.is_valid, "task_count": len(planned.task_specs)},
            project_id=safe_project,
        )

        return planned.to_dict()

    def get_workflow(self, principal: dict[str, Any], workflow_id: str) -> dict[str, Any] | None:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            return None
        self._require_project_role(principal, workflow["project_id"], "viewer")
        return self._serialize_workflow(workflow)

    def list_workflows(self, principal: dict[str, Any], project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        safe_project = self._safe_project_id(project_id) if project_id else None
        if safe_project:
            self._require_project_role(principal, safe_project, "viewer")
        rows = self.database.list_workflows(principal["tenant_id"], safe_project, limit)
        return [self._serialize_workflow(w) for w in rows]

    def start_workflow(self, principal: dict[str, Any], workflow_id: str) -> dict[str, Any]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")

        engine = self._workflow_engine(principal)
        from runtime.multi_agent_executor import MultiAgentExecutor
        engine.set_executor(MultiAgentExecutor(
            database=self.database,
            agent_registry=engine._agent_registry,
            settings=self.settings,
            principal=principal,
            messaging_hub=engine.messaging_hub,
        ), agent_registry=engine._agent_registry)

        engine.start_workflow(principal["tenant_id"], workflow["project_id"], workflow_id)
        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "workflow.started", "success",
            {"workflow_id": workflow_id},
            project_id=workflow["project_id"],
        )
        return self.get_workflow_state(principal, workflow_id)

    def pause_workflow(self, principal: dict[str, Any], workflow_id: str) -> bool:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")
        engine = self._workflow_engine(principal)
        engine.pause_workflow(principal["tenant_id"], workflow_id)
        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "workflow.paused", "success",
            {"workflow_id": workflow_id},
            project_id=workflow["project_id"],
        )
        return True

    def resume_workflow(self, principal: dict[str, Any], workflow_id: str) -> dict[str, Any]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")
        engine = self._workflow_engine(principal)
        from runtime.multi_agent_executor import MultiAgentExecutor
        engine.set_executor(MultiAgentExecutor(
            database=self.database,
            agent_registry=engine._agent_registry,
            settings=self.settings,
            principal=principal,
            messaging_hub=engine.messaging_hub,
        ), agent_registry=engine._agent_registry)
        engine.resume_workflow(principal["tenant_id"], workflow["project_id"], workflow_id)
        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "workflow.resumed", "success",
            {"workflow_id": workflow_id},
            project_id=workflow["project_id"],
        )
        return self.get_workflow_state(principal, workflow_id)

    def cancel_workflow(self, principal: dict[str, Any], workflow_id: str) -> dict[str, Any]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")
        engine = self._workflow_engine(principal)
        engine.cancel_workflow(principal["tenant_id"], workflow["project_id"], workflow_id)
        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "workflow.cancelled", "success",
            {"workflow_id": workflow_id},
            project_id=workflow["project_id"],
        )
        return self.get_workflow_state(principal, workflow_id)

    def get_workflow_state(self, principal: dict[str, Any], workflow_id: str) -> dict[str, Any]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "viewer")
        engine = self._workflow_engine(principal)
        return engine.get_workflow_state(principal["tenant_id"], workflow_id)

    def list_workflow_tasks(self, principal: dict[str, Any], workflow_id: str) -> list[dict[str, Any]]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "viewer")
        return self.database.list_workflow_tasks(principal["tenant_id"], workflow_id)

    def list_workflow_artifacts(self, principal: dict[str, Any], workflow_id: str) -> list[dict[str, Any]]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "viewer")
        return self.database.list_workflow_artifacts(principal["tenant_id"], workflow_id)

    def list_workflow_events(self, principal: dict[str, Any], workflow_id: str, limit: int = 100) -> list[dict[str, Any]]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "viewer")
        return self.database.list_workflow_events(principal["tenant_id"], workflow_id, limit)

    def list_workflow_messages(
        self,
        principal: dict[str, Any],
        workflow_id: str,
        message_type: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "viewer")
        return self.database.list_workflow_messages(
            principal["tenant_id"], workflow_id,
            message_type=message_type, task_id=task_id, limit=limit,
        )

    def send_workflow_message(
        self,
        principal: dict[str, Any],
        workflow_id: str,
        message_type: str,
        content: dict[str, Any],
        *,
        to_agent_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")

        from runtime.messaging_hub import MessagingHub
        hub = MessagingHub(self.database)
        return hub.send(
            workflow_id=workflow_id,
            tenant_id=principal["tenant_id"],
            message_type=message_type,
            content=content,
            from_agent_id=f"user:{principal['user_id']}",
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def recover_stuck_tasks(
        self,
        principal: dict[str, Any],
        workflow_id: str,
        stale_seconds: int = 30,
    ) -> dict[str, Any]:
        """Recover tasks stuck on dead workers."""
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")

        engine = self._workflow_engine(principal)
        recovered = engine.recover_stuck_tasks(principal["tenant_id"], workflow_id, stale_seconds)
        self.database.add_audit_event(
            principal["tenant_id"], principal["user_id"],
            "workflow.recover_stuck_tasks", "success",
            {"workflow_id": workflow_id, "recovered": recovered},
            project_id=workflow["project_id"],
        )
        return {"recovered": recovered, "workflow_id": workflow_id}

    def run_workflow_once(
        self,
        principal: dict[str, Any],
        workflow_id: str,
    ) -> dict[str, Any]:
        """Execute one step of a workflow (for manual polling)."""
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")

        engine = self._workflow_engine(principal)
        state = engine.step(principal["tenant_id"], workflow["project_id"], workflow_id)
        return state

    def run_workflow(self, principal: dict[str, Any], workflow_id: str) -> dict[str, Any]:
        """Start autonomous execution of a workflow.

        Non-blocking: starts/stops the workflow and launches a background
        worker thread that runs the workflow to completion. The HTTP request
        returns immediately with the workflow_id.
        """
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")

        engine = self._workflow_engine(principal)

        # Build autonomous runtime + worker
        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        from runtime.workflow_worker import WorkflowWorker, WorkerConfig
        from runtime.workflow_planner import WorkflowPlanner

        planner = WorkflowPlanner(agent_registry=engine._agent_registry)
        autonomous_runtime = AutonomousRuntime(
            database=self.database,
            engine=engine,
            executor=engine.executor,
            agent_registry=engine._agent_registry,
            messaging_hub=engine.messaging_hub,
            planner=planner,
            config=AutonomousConfig(
                tenant_id=principal["tenant_id"],
                project_id=workflow["project_id"] or principal.get("project_id", "local"),
            ),
        )

        worker = WorkflowWorker(
            database=self.database,
            engine=engine,
            executor=engine.executor,
            agent_registry=engine._agent_registry,
            messaging_hub=engine.messaging_hub,
            config=WorkerConfig(
                worker_id=f"worker-{workflow_id[:12]}",
                tenant_id=principal["tenant_id"],
                poll_interval_seconds=1.0,
                stop_on_idle=False,
                idle_limit=3,
            ),
            autonomous_runtime=autonomous_runtime,
        )

        # Start the workflow if PENDING
        engine.start_workflow(principal["tenant_id"], workflow["project_id"] or "local", workflow_id)

        # Launch worker in background thread
        import threading
        thread = threading.Thread(
            target=worker.execute_workflow,
            args=(principal["tenant_id"], workflow["project_id"] or "local", workflow_id),
            daemon=True,
        )
        thread.start()

        return {
            "workflow_id": workflow_id,
            "status": "RUNNING",
            "worker_id": worker.config.worker_id,
            "message": "Autonomous execution started in background",
        }

    def get_artifact_lineage(self, principal: dict[str, Any], artifact_id: str) -> dict[str, Any]:
        artifact = self.database.get_artifact(principal["tenant_id"], artifact_id)
        if artifact is None:
            raise ValueError("artifact not found")
        workflow = self.database.get_workflow(principal["tenant_id"], artifact["workflow_id"])
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "viewer")
        return self.database.get_artifact_lineage(principal["tenant_id"], artifact_id)

    def get_workflow_artifact(
        self, principal: dict[str, Any], artifact_id: str) -> dict[str, Any] | None:
        artifact = self.database.get_artifact(principal["tenant_id"], artifact_id)
        if artifact is None:
            return None
        workflow = self.database.get_workflow(principal["tenant_id"], artifact["workflow_id"])
        if workflow is None:
            return None
        self._require_project_role(principal, workflow["project_id"], "viewer")
        return artifact

    def _serialize_workflow(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        if result.get("plan_json"):
            result["plan"] = json.loads(result.pop("plan_json"))
        if result.get("result_json"):
            result["result"] = json.loads(result.pop("result_json"))
        result.pop("plan_json", None)
        result.pop("result_json", None)
        return result

    # ---- Phase 5: Autonomous Runtime ------------------------------------------

    def _autonomous_runtime(self, tenant_id: str, project_id: str) -> Any:
        """Create an AutonomousRuntime bound to the service's engine and database."""
        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        engine = self._workflow_engine({"tenant_id": tenant_id, "project_id": project_id})
        messaging_hub = engine.messaging_hub
        from runtime.workflow_planner import WorkflowPlanner
        planner = WorkflowPlanner(agent_registry=engine._agent_registry)
        return AutonomousRuntime(
            database=self.database,
            engine=engine,
            messaging_hub=messaging_hub,
            planner=planner,
            config=AutonomousConfig(tenant_id=tenant_id, project_id=project_id),
        )

    def run_autonomous(self, principal: dict[str, Any], objective: str, scope: str,
                       template_type: str | None = None, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a high-level objective autonomously from plan to completion.

        Non-blocking: plans the workflow, creates it, starts it, and launches a
        background worker to drive execution. Returns immediately with workflow_id.
        """
        tenant_id = principal.get("tenant_id", "default")
        project_id = self._safe_project_id(principal.get("project_id", "local"))
        self._require_project_role(principal, project_id, "operator")

        runtime = self._autonomous_runtime(tenant_id, project_id)

        # Plan and create the workflow
        planned = runtime._plan_objective(objective, scope, template_type, None, constraints or {})
        workflow = self._workflow_engine(principal).create_workflow(
            tenant_id, project_id, runtime._planner_to_spec(planned)
        )
        workflow_id = workflow["workflow_id"]

        # Save the plan
        self.database.save_workflow_plan(tenant_id, workflow_id, {
            "objective": objective,
            "scope": scope,
            "template_type": template_type or "repository_analysis",
            "constraints": constraints or {},
            "planned_tasks": planned.get("task_specs", []),
        })

        # Launch background execution
        return self.run_workflow(principal, workflow_id)

    def get_execution_trace(self, principal: dict[str, Any], workflow_id: str) -> dict[str, Any]:
        """Get the complete execution trace for a workflow."""
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "viewer")
        engine = self._workflow_engine(principal)
        return engine.get_execution_trace(principal["tenant_id"], workflow_id)

    def add_dynamic_task(self, principal: dict[str, Any], workflow_id: str,
                         request: dict[str, Any]) -> dict[str, Any]:
        """Add a dynamically-created task to an existing workflow."""
        workflow = self.database.get_workflow(principal["tenant_id"], workflow_id)
        if workflow is None:
            raise ValueError("workflow not found")
        self._require_project_role(principal, workflow["project_id"], "operator")
        engine = self._workflow_engine(principal)
        return engine.add_dynamic_task(
            principal["tenant_id"], workflow["project_id"], workflow_id,
            task_type=request.get("task_type", "research"),
            name=request.get("name", "Dynamic Task"),
            agent_id=request.get("agent_id"),
            required_capabilities=request.get("required_capabilities", []),
            depends_on=request.get("depends_on", []),
            input_artifacts=request.get("input_artifacts", []),
            parameters=request.get("parameters", {}),
            parent_task_id=request.get("parent_task_id"),
            generated_reason=request.get("reason", "Dynamically added via API"),
        )

    def list_approvals(self, principal: dict[str, Any], workflow_id: str | None = None) -> list[dict[str, Any]]:
        """List pending approval requests."""
        tenant_id = principal.get("tenant_id", "default")
        if workflow_id:
            return self.database.list_pending_approvals(tenant_id, principal.get("project_id", "local"))
        all_approvals = []
        for wf in self.database.list_workflows(tenant_id):
            all_approvals.extend(self.database.list_pending_approvals(tenant_id, wf.get("project_id", "")))
        return all_approvals

    def decide_approval(self, principal: dict[str, Any], approval_id: str, decision: str, note: str | None = None) -> dict[str, Any]:
        """Make an approval decision (APPROVED/REJECTED/CANCELLED).

        The decision resumes the gated task: APPROVED re-arms it to READY so
        the next worker pass continues execution; REJECTED fails it safely
        with the persisted reason; CANCELLED cancels it. The worker loop
        pauses on AWAITING_APPROVAL and resumes automatically afterwards.
        """
        tenant_id = principal.get("tenant_id", "default")
        approval = self.database.get_approval(tenant_id, approval_id)
        if approval is None:
            raise ValueError("approval not found")
        self._require_project_role(principal, approval["project_id"], "owner")
        if not self.database.decide_approval(tenant_id, approval_id, decision, principal["user_id"], note):
            raise ValueError("could not update approval")
        self._apply_approval_decision(tenant_id, approval, decision, principal["user_id"], note)
        return self.database.get_approval(tenant_id, approval_id)

    def _apply_approval_decision(
        self,
        tenant_id: str,
        approval: dict[str, Any],
        decision: str,
        decided_by: str,
        note: str | None = None,
    ) -> None:
        """Transition the gated task after an approval decision (shared by API + CLI)."""
        from runtime.messaging_hub import MessagingHub
        task_id = approval.get("task_id")
        workflow_id = approval.get("workflow_id")
        project_id = approval.get("project_id", "")
        if not task_id or not workflow_id:
            return
        task = self.database.get_workflow_task(tenant_id, task_id)
        if task is None or task.get("status") != "AWAITING_APPROVAL":
            return
        hub = MessagingHub(self.database)
        if decision == "APPROVED":
            self.database.update_task_status(tenant_id, task_id, "READY")
            hub.task_lifecycle(workflow_id=workflow_id, tenant_id=tenant_id, event="TASK_STARTED",
                               agent_id="approval-gate", task_id=task_id,
                               details={"approved": True, "approval_id": approval.get("approval_id"),
                                        "operation": approval.get("operation"), "decided_by": decided_by})
        elif decision == "REJECTED":
            reason = f"approval rejected by {decided_by}: {note or approval.get('reason', '')}".strip()
            self.database.update_task_status(tenant_id, task_id, "FAILED", error=reason)
        elif decision == "CANCELLED":
            self.database.update_task_status(tenant_id, task_id, "CANCELLED")
        else:
            return
        event_type = {"APPROVED": "approval_granted", "REJECTED": "approval_rejected"}.get(
            decision, "approval_rejected")
        self.database.add_workflow_event(
            event_id=f"evt-{uuid4().hex[:12]}",
            workflow_id=workflow_id, tenant_id=tenant_id, project_id=project_id,
            task_id=task_id, agent_id="approval-gate", event_type=event_type,
            detail={"approval_id": approval.get("approval_id"), "decision": decision,
                    "decided_by": decided_by, "note": note},
        )

    def get_agent_memory(self, principal: dict[str, Any], agent_id: str,
                         scope: str | None = None, workflow_id: str | None = None) -> list[dict[str, Any]]:
        """Retrieve memory for an agent."""
        return self.database.list_agent_memory(agent_id, scope=scope, workflow_id=workflow_id)

    def run_workflow_worker(
        self,
        tenant_id: str,
        project_id: str,
        workflow_id: str | None = None,
        worker_id: str | None = None,
        max_ticks: int = 500,
        poll_interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Terminal-driven background worker for autonomous workflows.

        This is the headless execution path: no frontend, no per-step HTTP
        calls. The worker loops engine.step() + observe/adapt over persisted
        SQLite state until the workflow reaches a terminal state, so closing
        the UI never stops execution and a restart resumes from the database.
        """
        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
        from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
        from runtime.agent_registry import AgentRegistry
        from runtime.messaging_hub import MessagingHub
        from runtime.workflow_planner import WorkflowPlanner
        from runtime.workflow_worker import WorkflowWorker, WorkerConfig

        registry = getattr(self, "_agent_registry", None)
        if registry is None:
            registry = AgentRegistry()
            register_default_agents(registry)
            self._agent_registry = registry
        hub = MessagingHub(self.database)
        # Same canonical singleton as above (idempotent — no duplicate
        # registry). Logged on failure; see note at the first init site.
        try:
            from runtime.capability_fabric import initialize_capability_registry as _init_caps2
            _gh_reg2 = _init_caps2()
            _gh_conn2 = _gh_reg2.get_connector("github") if hasattr(_gh_reg2, "get_connector") else None
        except Exception as exc:
            logger.warning("capability registry init failed: %s: %s", type(exc).__name__, exc)
            _gh_conn2, _gh_reg2 = None, None
        engine = WorkflowEngine(
            database=self.database,
            composer=self._composer(),
            policy=WorkflowExecutionPolicy(
                max_retries_default=self.settings.queue_max_attempts,
                fail_on_agent_not_available=False,
                auto_retry_on_failure=True,
            ),
            agent_registry=registry,
            artifacts_root=self._artifacts_root(),
            messaging_hub=hub,
        )
        executor = MultiAgentExecutor(
            database=self.database,
            agent_registry=registry,
            settings=self.settings,
            principal={"tenant_id": tenant_id, "project_id": project_id},
            messaging_hub=hub,
            connector_registry=_gh_reg2,
        )
        engine.set_executor(executor, agent_registry=registry, connector_registry=_gh_reg2)
        try:
            engine.connector_registry = _gh_reg2
        except Exception as exc:
            logger.warning("engine connector_registry wiring failed: %s: %s", type(exc).__name__, exc)
        engine.messaging_hub = hub
        planner = WorkflowPlanner(agent_registry=registry)
        autonomous = AutonomousRuntime(
            database=self.database,
            engine=engine,
            executor=executor,
            agent_registry=registry,
            messaging_hub=hub,
            planner=planner,
            config=AutonomousConfig(tenant_id=tenant_id, project_id=project_id),
        )
        worker = WorkflowWorker(
            database=self.database,
            engine=engine,
            executor=executor,
            agent_registry=registry,
            messaging_hub=hub,
            autonomous_runtime=autonomous,
            config=WorkerConfig(
                worker_id=worker_id or f"workflow-worker-{tenant_id[:8]}",
                tenant_id=tenant_id,
                poll_interval_seconds=poll_interval_seconds,
                stop_on_idle=False,
                auto_recover_stuck=True,
            ),
        )
        if workflow_id:
            state = worker.execute_workflow(tenant_id, project_id, workflow_id, max_ticks=max_ticks)
            return {"workflow_id": workflow_id, "status": state.get("status"), "worker_id": worker.config.worker_id}
        completed: list[dict[str, Any]] = []
        for wf in self.database.list_workflows(tenant_id, project_id, limit=50):
            if wf["status"] in ("RUNNING", "PENDING"):
                if wf["status"] == "PENDING":
                    engine.start_workflow(tenant_id, project_id, wf["workflow_id"])
                state = worker.execute_workflow(tenant_id, project_id, wf["workflow_id"], max_ticks=max_ticks)
                completed.append({"workflow_id": wf["workflow_id"], "status": state.get("status")})
        return {"worker_id": worker.config.worker_id, "workflows": completed}

    def list_worker_heartbeats(self, principal: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
        """Worker liveness for the control-center UI (durable registry + computed liveness)."""
        tenant_id = principal.get("tenant_id", "default")
        return self.database.list_workers(tenant_id, stale_seconds=60, limit=limit)

    # ---- Phase 9: headless control-plane helpers (CLI + API worker paths) ----
    # Same durable backend as the authenticated API; no second execution
    # implementation. Used by `nexus-independent worker/workflow/approval ...`
    # and by tests proving frontend-independent execution.

    def _headless_stack(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        """Build the full autonomous stack over this service's database."""
        from runtime.autonomous_runtime import AutonomousRuntime, AutonomousConfig
        from runtime.workflow_engine import WorkflowExecutionPolicy
        from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
        from runtime.agent_registry import AgentRegistry
        from runtime.messaging_hub import MessagingHub
        from runtime.workflow_planner import WorkflowPlanner
        from runtime.workflow_worker import WorkflowWorker, WorkerConfig

        registry = getattr(self, "_agent_registry", None)
        if registry is None:
            registry = AgentRegistry()
            register_default_agents(registry)
            self._agent_registry = registry
        hub = MessagingHub(self.database)
        engine = self._workflow_engine({"tenant_id": tenant_id, "project_id": project_id})
        planner = WorkflowPlanner(agent_registry=registry)
        runtime = AutonomousRuntime(
            database=self.database, engine=engine,
            executor=engine.executor, agent_registry=registry,
            messaging_hub=hub, planner=planner,
            config=AutonomousConfig(tenant_id=tenant_id, project_id=project_id,
                                    poll_interval_seconds=0.05, max_iterations=500),
        )
        worker = WorkflowWorker(
            database=self.database, engine=engine, executor=engine.executor,
            agent_registry=registry, messaging_hub=hub, autonomous_runtime=runtime,
            config=WorkerConfig(worker_id=f"cli-worker-{tenant_id[:8]}", tenant_id=tenant_id,
                                poll_interval_seconds=0.5, stop_on_idle=False,
                                auto_recover_stuck=True),
        )
        return {"registry": registry, "hub": hub, "engine": engine,
                "planner": planner, "runtime": runtime, "worker": worker}

    def cli_worker_list(self, tenant_id: str, stale_seconds: int = 30) -> list[dict[str, Any]]:
        return self.database.list_workers(tenant_id, stale_seconds=stale_seconds)

    def cli_worker_stop(self, worker_id: str, reason: str = "cli stop") -> dict[str, Any]:
        stopped = self.database.stop_worker(worker_id, reason=reason)
        return {"worker_id": worker_id, "stopped": stopped}

    def _apply_execution_strategy(self, stack: dict[str, Any], strategy: str) -> str:
        """Pin DETERMINISTIC/MODEL/HYBRID across executor + registered agents."""
        requested = (strategy or "DETERMINISTIC").strip().upper()
        if requested not in ("DETERMINISTIC", "MODEL", "HYBRID"):
            requested = "DETERMINISTIC"
        try:
            stack["executor"].default_execution_strategy = requested
        except Exception:
            pass
        try:
            for _reg in list(getattr(stack["registry"], "_agents", {}).values()):
                try:
                    _reg.instance.execution_strategy = requested
                except Exception:
                    pass
        except Exception:
            pass
        return requested

    def cli_workflow_submit(
        self,
        tenant_id: str,
        project_id: str,
        objective: str,
        scope: str,
        environment: str = "LOCAL",
    ) -> dict[str, Any]:
        """Objective -> plan -> persist -> start. No execution: a worker (any
        process, e.g. `workflow-worker`) picks the persisted workflow up."""
        from runtime.execution_environment import resolve_environment
        from runtime.workflow_engine import WorkflowSpec
        env = resolve_environment(environment).value
        now = utc_now()
        with self.database.connect() as db:
            db.execute("INSERT OR IGNORE INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
                       (tenant_id, tenant_id, now))
            db.execute("INSERT OR IGNORE INTO projects(project_id, tenant_id, display_name, created_at, updated_at)"
                       " VALUES(?,?,?,?,?)", (project_id, tenant_id, project_id, now, now))
            db.commit()
        stack = self._headless_stack(tenant_id, project_id)
        planned = stack["planner"].plan(
            objective=objective, scope=scope,
            constraints={"execution_environment": env},
            tenant_id=tenant_id, execution_mode="REAL_READ", project_id=project_id)
        if not planned.is_valid:
            raise ValueError("planner produced an invalid workflow")
        wf = stack["engine"].create_workflow(
            tenant_id, project_id,
            WorkflowSpec(name=planned.name, objective=planned.objective, scope=planned.scope,
                         task_specs=planned.task_specs, agents=planned.agents,
                         execution_mode=planned.execution_mode,
                         execution_environment=env))
        wid = wf["workflow_id"]
        stack["engine"].start_workflow(tenant_id, project_id, wid)
        return {"workflow_id": wid, "status": "RUNNING",
                "template": planned.plan.get("template_type"),
                "tasks": len(planned.task_specs),
                "execution_environment": env}

    def cli_workflow_run(
        self,
        tenant_id: str,
        project_id: str,
        objective: str,
        scope: str,
        max_ticks: int = 300,
        poll_interval_seconds: float = 0.5,
        strategy: str = "DETERMINISTIC",
        export_dir: str | None = None,
        environment: str = "LOCAL",
    ) -> dict[str, Any]:
        """Objective -> plan -> persist -> start -> headless worker -> terminal state."""
        import time as _time
        started = _time.time()
        stack = self._headless_stack(tenant_id, project_id)
        requested = self._apply_execution_strategy(stack, strategy)
        submitted = self.cli_workflow_submit(tenant_id, project_id, objective, scope,
                                             environment=environment)
        wid = submitted["workflow_id"]
        state = stack["worker"].execute_workflow(tenant_id, project_id, wid,
                                                 max_ticks=max_ticks, poll_interval=poll_interval_seconds)
        summary = self.cli_workflow_summary(tenant_id, project_id, wid)
        summary["worker_id"] = stack["worker"].config.worker_id
        summary["template"] = submitted["template"]
        summary["strategy_requested"] = requested
        summary["duration_seconds"] = round(_time.time() - started, 1)
        if export_dir:
            summary["exported"] = self.cli_export_final_artifacts(tenant_id, wid, export_dir)
        return summary

    def cli_export_final_artifacts(self, tenant_id: str, workflow_id: str, export_dir: str) -> list[str]:
        """Copy the final report + verification artifacts to a human-facing directory."""
        import shutil
        from pathlib import Path as _Path
        dest = _Path(export_dir)
        dest.mkdir(parents=True, exist_ok=True)
        exported: list[str] = []
        for art in self.database.list_workflow_artifacts(tenant_id, workflow_id):
            if art.get("kind") not in ("final_report", "verification_result"):
                continue
            src = art.get("content_path")
            if not src or not _Path(src).is_file():
                continue
            target = dest / f"{art['kind']}.json"
            shutil.copyfile(src, target)
            exported.append(str(target))
        return exported

    def cli_workflow_summary(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Human-facing execution summary, built strictly from persisted state."""
        stack = self._headless_stack(tenant_id, project_id)
        trace = stack["engine"].get_execution_trace(tenant_id, workflow_id)
        tasks = trace.get("tasks", [])
        agents_seen: dict[str, str] = {}
        for t in tasks:
            aid = t.get("agent_id") or "unassigned"
            if t.get("status") == "COMPLETED":
                agents_seen[aid] = "done"
            elif aid not in agents_seen:
                agents_seen[aid] = t.get("status") or "UNKNOWN"
        tools = trace.get("tools_used", {}) or {}
        observations = sum(v.get("count", 0) for v in tools.values())
        verification = "UNKNOWN"
        ver_arts = [a for a in trace.get("artifacts", []) if a.get("kind") == "verification_result"]
        if ver_arts:
            verification = "VERIFIED" if ver_arts[0].get("reality") == "VERIFIED" else ver_arts[0].get("reality", "UNKNOWN")
        final_paths = [a.get("content_path") for a in trace.get("artifacts", [])
                       if a.get("kind") == "final_report" and a.get("content_path")]
        return {
            "objective": trace.get("objective", ""),
            "workflow_id": workflow_id,
            "status": trace.get("status", ""),
            "agents": [{"agent_id": aid, "state": st} for aid, st in sorted(agents_seen.items())],
            "tasks_completed": sum(1 for t in tasks if t.get("status") == "COMPLETED"),
            "tasks_failed": sum(1 for t in tasks if t.get("status") == "FAILED"),
            "tasks_total": len(tasks),
            "observations": observations,
            "tools": {k: v.get("count", 0) for k, v in sorted(tools.items())},
            "artifacts": len(trace.get("artifacts", [])),
            "messages": len(trace.get("messages", [])),
            "dynamic_tasks": len(trace.get("dynamic_tasks", [])),
            "verification": verification,
            "model": trace.get("model", {}),
            "final_artifact": final_paths[0] if final_paths else None,
            "finish_reason": trace.get("finish_reason", ""),
        }

    def cli_execution_record(
        self,
        tenant_id: str,
        project_id: str,
        workflow_id: str,
        worker_id: str | None = None,
        strategy: str = "DETERMINISTIC",
    ) -> dict[str, Any]:
        """Machine-readable execution record (nexus-run.json), from runtime rows only.

        Canonical shape is NexusRunResult; legacy top-level aliases
        (selected_agents, workspace, tool_calls, ...) are kept for
        compatibility with existing consumers.
        """
        stack = self._headless_stack(tenant_id, project_id)
        trace = stack["engine"].get_execution_trace(tenant_id, workflow_id)
        state = stack["engine"].get_workflow_state(tenant_id, workflow_id)
        wf = state.get("workflow", {})
        tool_calls: list[dict[str, Any]] = []
        for e in state.get("events", []):
            if e.get("event_type") != "tool_used":
                continue
            d = e.get("detail", {}) or {}
            tool_calls.append({
                "capability": d.get("capability"), "target": d.get("target"),
                "status": d.get("status"), "reality": d.get("reality"),
                "content_sha256": d.get("content_sha256"), "receipt_id": d.get("receipt_id"),
                "workspace_root": d.get("workspace_root"), "task_id": e.get("task_id"),
                "agent_id": e.get("agent_id"), "timestamp": e.get("created_at"),
            })
        verification_detail: dict[str, Any] = {}
        for a in trace.get("artifacts", []):
            if a.get("kind") == "verification_result" and a.get("content_path"):
                try:
                    from pathlib import Path as _P
                    verification_detail = json.loads(_P(a["content_path"]).read_text())
                except (OSError, ValueError):
                    verification_detail = {}
                break
        from runtime.nexus_result import NexusRunResult
        result = NexusRunResult.from_trace(
            trace=trace, workflow=wf, tool_calls=tool_calls,
            verification=verification_detail, worker_id=worker_id, strategy=strategy)
        record = result.to_dict()
        # Legacy aliases for existing consumers (tests, --record readers).
        record["workspace"] = record.get("workspace", "")
        record["selected_agents"] = record.get("agents_used", {})
        record["tool_calls"] = (record.get("execution_trace", {}) or {}).get("tool_calls", [])
        record["artifacts"] = record.get("artifacts_produced", [])
        record["messages"] = (record.get("execution_trace", {}) or {}).get("messages", [])
        record["dynamic_tasks"] = (record.get("execution_trace", {}) or {}).get("dynamic_tasks", [])
        record["verification"] = record.get("verification_result", {})
        record["workers"] = (record.get("execution_trace", {}) or {}).get("workers", [])
        record["recovery_events"] = (record.get("execution_trace", {}) or {}).get("recovery_events", [])
        record["model"] = (record.get("execution_trace", {}) or {}).get("model", {})
        record["tasks"] = record.get("tasks_executed", [])
        record["created_at"] = wf.get("created_at")
        record["updated_at"] = wf.get("updated_at")
        return record

    def cli_workflow_cancel(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Hard stop: CANCELLED is terminal; the worker will not resume it."""
        stack = self._headless_stack(tenant_id, project_id)
        result = stack["engine"].cancel_workflow(tenant_id, project_id, workflow_id)
        return {"workflow_id": workflow_id, "status": (result or {}).get("status", "CANCELLED")}

    def cli_workflow_resume(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Resume a PAUSED (or retryable FAILED) workflow to RUNNING for the next worker pass."""
        stack = self._headless_stack(tenant_id, project_id)
        try:
            result = stack["engine"].resume_workflow(tenant_id, project_id, workflow_id)
        except ValueError as exc:
            raise ValueError(str(exc))
        status = (result or {}).get("status", "RUNNING")
        return {"workflow_id": workflow_id, "status": status}

    def cli_workflow_inspect(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Structured workflow detail for `inspect`: every node backed by a persisted row."""
        stack = self._headless_stack(tenant_id, project_id)
        trace = stack["engine"].get_execution_trace(tenant_id, workflow_id)
        state = stack["engine"].get_workflow_state(tenant_id, workflow_id)
        messages = trace.get("messages", []) or []
        artifacts = trace.get("artifacts", []) or []
        task_rows = {t.get("task_id"): t for t in state.get("tasks", [])}
        strategies: dict[str, str] = {}
        for e in state.get("events", []) or []:
            if e.get("event_type") == "execution_strategy" and e.get("task_id"):
                detail = e.get("detail", {}) or {}
                strategies[e["task_id"]] = str(detail.get("effective") or "DETERMINISTIC")
        task_detail = []
        for t in trace.get("tasks", []):
            tid = t.get("task_id")
            row = task_rows.get(tid, {})
            produced = [a for a in artifacts if a.get("task_id") == tid]
            received = [m for m in messages
                        if m.get("task_id") == tid or m.get("to_agent_id") == t.get("agent_id")]
            task_detail.append({
                "task_id": tid, "task_type": t.get("task_type"), "name": t.get("name"),
                "agent_id": t.get("agent_id"), "status": t.get("status"),
                "worker_id": t.get("worker_id"), "attempt": t.get("claim_count", 0),
                "execution_id": t.get("last_execution_id"),
                "inputs": t.get("input_artifacts", row.get("input_artifacts", [])),
                "outputs": t.get("output_artifacts", row.get("output_artifacts", [])),
                "artifacts": [{"artifact_id": a.get("artifact_id"), "kind": a.get("kind"),
                               "reality": a.get("reality")} for a in produced],
                "messages": len(received),
                "started_at": t.get("started_at"), "completed_at": t.get("completed_at"),
                "retry_count": t.get("retry_count", 0),
                "execution_mode": strategies.get(tid or "", "DETERMINISTIC"),
                "error": t.get("error"),
            })
        artifact_detail = []
        for a in artifacts:
            artifact_detail.append({
                "artifact_id": a.get("artifact_id"), "kind": a.get("kind"),
                "producer": a.get("agent_id"), "task_id": a.get("task_id"),
                "consumers": a.get("consumed_by", []),
                "reality": a.get("reality"), "content_hash": a.get("content_hash"),
                "provenance": a.get("provenance", []),
                "verification_state": a.get("verification_state", "UNVERIFIED"),
            })
        return {
            "workflow_id": workflow_id, "objective": trace.get("objective", ""),
            "status": trace.get("status", ""), "scope": trace.get("scope", ""),
            "execution_environment": trace.get("execution_environment", "LOCAL"),
            "finish_reason": trace.get("finish_reason", ""),
            "tasks": task_detail,
            "artifacts": artifact_detail,
            "approvals": trace.get("approvals", []),
            "workers": trace.get("workers", []),
            "verification": trace.get("verification", {}),
        }

    def cli_workflow_logs(
        self, tenant_id: str, project_id: str, workflow_id: str,
        limit: int = 100, event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Workflow event log for `logs`: newest last, details truncated for terminals."""
        stack = self._headless_stack(tenant_id, project_id)
        state = stack["engine"].get_workflow_state(tenant_id, workflow_id)
        out = []
        for e in state.get("events", []) or []:
            if event_type and e.get("event_type") != event_type:
                continue
            detail = e.get("detail", {}) or {}
            out.append({"timestamp": e.get("created_at"), "event_type": e.get("event_type"),
                        "task_id": e.get("task_id"), "agent_id": e.get("agent_id"),
                        "detail": str(detail)[:300]})
        return out[-limit:]

    def daemon_tick(
        self, tenant_id: str, project_id: str, worker_id: str,
        max_ticks_per_workflow: int = 50, poll_interval_seconds: float = 0.5,
    ) -> dict[str, Any]:
        """One daemon cycle: recover stuck work, then advance every RUNNING/PENDING workflow.

        The worker identity persists across ticks (registration revive keeps
        started_at), so leases, heartbeats and recovery attribute correctly.
        """
        from runtime.workflow_worker import WorkflowWorker, WorkerConfig
        stack = self._headless_stack(tenant_id, project_id)
        worker = WorkflowWorker(
            database=self.database, engine=stack["engine"], executor=stack["engine"].executor,
            agent_registry=stack["registry"], messaging_hub=stack["hub"],
            autonomous_runtime=stack["runtime"],
            config=WorkerConfig(worker_id=worker_id, tenant_id=tenant_id,
                                poll_interval_seconds=poll_interval_seconds,
                                stop_on_idle=False, auto_recover_stuck=True))
        worker.register()
        ran: list[dict[str, Any]] = []
        for wf in self.database.list_workflows(tenant_id, project_id, limit=50):
            if wf.get("status") not in ("RUNNING", "PENDING"):
                continue
            wid = wf["workflow_id"]
            try:
                recovered = stack["engine"].recover_stuck_tasks(tenant_id, wid, 30)
            except Exception:
                recovered = 0
            try:
                state = worker.execute_workflow(tenant_id, project_id, wid,
                                                max_ticks=max_ticks_per_workflow,
                                                poll_interval=poll_interval_seconds)
                ran.append({"workflow_id": wid, "status": state.get("status"),
                            "recovered": recovered})
            except Exception as exc:
                ran.append({"workflow_id": wid, "status": f"ERROR: {exc}", "recovered": recovered})
        worker.heartbeat()
        return {"worker_id": worker_id, "workflows": ran}

    def cli_workflow_list(self, tenant_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.database.list_workflows(tenant_id, project_id, limit=100)
        return [{"workflow_id": w.get("workflow_id"), "name": w.get("name"),
                 "objective": (w.get("objective") or "")[:120], "status": w.get("status"),
                 "created_at": w.get("created_at"), "updated_at": w.get("updated_at")}
                for w in rows]

    def cli_agents(self) -> list[dict[str, Any]]:
        engine = self._workflow_engine(None)
        registry = getattr(engine, "_agent_registry", None)
        if registry is None or not hasattr(registry, "describe_agents"):
            return []
        return registry.describe_agents()

    def cli_workflow_status(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        stack = self._headless_stack(tenant_id, project_id)
        state = stack["engine"].get_workflow_state(tenant_id, workflow_id)
        wf = state.get("workflow", {})
        summary = state.get("summary", {})
        return {"workflow_id": workflow_id, "status": wf.get("status"),
                "objective": wf.get("objective"), "scope": wf.get("scope"),
                "summary": summary,
                "tasks": [{"task_id": t.get("task_id"), "task_type": t.get("task_type"),
                           "status": t.get("status"), "agent_id": t.get("agent_id"),
                           "worker_id": t.get("worker_id")} for t in state.get("tasks", [])]}

    def cli_workflow_recover(
        self, tenant_id: str, project_id: str, workflow_id: str, stale_seconds: int = 30,
    ) -> dict[str, Any]:
        stack = self._headless_stack(tenant_id, project_id)
        recovered = stack["engine"].recover_stuck_tasks(tenant_id, workflow_id, stale_seconds)
        return {"workflow_id": workflow_id, "recovered": recovered}

    def cli_workflow_trace(self, tenant_id: str, project_id: str, workflow_id: str) -> dict[str, Any]:
        stack = self._headless_stack(tenant_id, project_id)
        return stack["engine"].get_execution_trace(tenant_id, workflow_id)

    def cli_approval_list(self, tenant_id: str, project_id: str) -> list[dict[str, Any]]:
        return self.database.list_pending_approvals(tenant_id, project_id)

    def cli_approval_decide(
        self, tenant_id: str, approval_id: str, decision: str, note: str | None = None,
    ) -> dict[str, Any]:
        approval = self.database.get_approval(tenant_id, approval_id)
        if approval is None:
            raise ValueError("approval not found")
        if not self.database.decide_approval(tenant_id, approval_id, decision, "cli-operator", note):
            raise ValueError("could not update approval")
        self._apply_approval_decision(tenant_id, approval, decision, "cli-operator", note)
        return self.database.get_approval(tenant_id, approval_id)

    def store_agent_memory(self, principal: dict[str, Any], agent_id: str,
                           scope: str, source: str, content: dict[str, Any],
                           workflow_id: str | None = None, trust: str = "INFERRED") -> dict[str, Any]:
        """Store a memory item for an agent."""
        from canonical_core import core_id
        memory_id = f"mem-{core_id('mem')}"
        return self.database.create_agent_memory(
            memory_id=memory_id,
            agent_id=agent_id,
            scope=scope,
            source=source,
            content=content,
            workflow_id=workflow_id,
            truth=trust,
            confidence="UNVERIFIED" if trust != "OBSERVED" else "VERIFIED",
        )
