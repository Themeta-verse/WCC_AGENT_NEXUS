"""NEXUS Multi-Agent Executor — dispatches workflow tasks to registered agents.

This executor replaces the simple WorkflowTaskExecutor. Instead of dispatching
based on task_type alone, it:
1. Resolves the agent assigned to each task (via capability matching in the engine)
2. Looks up the agent's implementation from the AgentRegistry
3. Calls the agent's execute() method with full context
4. Handles artifact production with full provenance

The agent boundary is real: each agent has a distinct identity, capabilities,
and execution strategy. The executor is just a bridge between the workflow
engine and the agent implementations.

Truth boundary preserved:
- Agents marked with real observation (BoundedAgentRuntime reads) produce OBSERVED artifacts
- Model/rule-based reasoning produces INFERRED artifacts (always untrusted)
- Only the VerificationAgent can produce VERIFIED artifacts (independent deterministic checks)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from pathlib import Path
import json
import hashlib
import logging

from runtime.agent_base import AgentContext, AgentExecutionResult
from runtime.agent_registry import AgentRegistry

logger = logging.getLogger("nexus.multi_agent_executor")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArtifactResolution:
    """Resolved artifact content from the database."""
    artifact_id: str
    kind: str
    name: str
    content_hash: str
    content: dict[str, Any] | str | None
    content_path: str | None
    parent_artifacts: list[str]
    provenance: list[str]


class MultiAgentExecutor:
    """Executes workflow tasks by dispatching to registered agents.

    The executor uses the AgentRegistry to find the right agent for each task,
    based on the agent's declared capabilities and the task's requirements.

    Architecture:
        WorkflowEngine -> MultiAgentExecutor -> AgentRegistry -> BaseAgent
                                          |
                                          -> Database (artifact resolution, storage)
                                          -> Events (emission)
    """

    def __init__(
        self,
        database: Any = None,
        agent_registry: AgentRegistry | None = None,
        settings: Any = None,
        principal: dict[str, Any] | None = None,
        messaging_hub: Any = None,
        model_router: Any | None = None,
        default_execution_strategy: str = "DETERMINISTIC",
        connector_registry: Any = None,
    ):
        self.database = database
        self.registry = agent_registry or AgentRegistry()
        self.settings = settings
        self.principal = principal or {}
        self.messaging_hub = messaging_hub
        # Shared ConnectorRegistry for capability-based external access
        # (GitHub, git, filesystem, or any future provider). Normally wired by
        # AutonomousRuntime / WorkflowEngine.set_executor and forwarded to every
        # AgentContext built below.
        #
        # When NOT supplied we fall back to the canonical process-wide registry
        # rather than leaving None. An absent registry used to mean agents had
        # no capability fabric at all and silently degraded to whatever private
        # path they carried — that hole is closed here: there is always ONE
        # authoritative registry, and agents always request capabilities through
        # it. AutonomousRuntime still passes the SAME object, so object identity
        # propagation is unaffected.
        if connector_registry is not None:
            self.connector_registry = connector_registry
        else:
            try:
                from runtime.capability_fabric import get_capability_registry
                self.connector_registry = get_capability_registry()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "canonical capability registry unavailable: %s: %s",
                    type(exc).__name__, exc,
                )
                self.connector_registry = None
        # Phase 7: optional shared model router + default strategy for all agents.
        self.model_router = model_router
        self.default_execution_strategy = default_execution_strategy

    def _get_messaging_hub(self) -> Any | None:
        """Get the messaging hub, creating one from the database if not set."""
        if self.messaging_hub is None and self.database is not None:
            from runtime.messaging_hub import MessagingHub
            self.messaging_hub = MessagingHub(self.database)
        return self.messaging_hub

    def execute_task(
        self,
        *,
        workflow_id: str,
        task_id: str,
        task_type: str,
        task_name: str,
        agent_id: str,
        capabilities: list[str],
        scope: str,
        observation_scope: str | None = None,
        input_artifacts: list[str],
        parameters: dict[str, Any] | None = None,
    ) -> AgentExecutionResult:
        """Execute a task by dispatching to the appropriate registered agent.

        This method:
        1. Resolves input artifacts from the database (real provenance)
        2. Looks up the agent instance from the registry
        3. Constructs an AgentContext with full execution information
        4. Calls the agent's execute() method
        5. Returns the result with artifacts and provenance
        """
        params = parameters or {}
        tenant_id = self.principal.get("tenant_id", "default")

        # Resolve workflow objective and constraints from plan
        objective = ""
        constraints = {}
        try:
            workflow = self.database.get_workflow(tenant_id, workflow_id)
            if workflow and workflow.get("plan_json"):
                plan = json.loads(workflow["plan_json"])
                objective = plan.get("objective", "")
                constraints = plan.get("constraints", {})
        except (Exception,):
            pass

        # Emit task started message
        hub = self._get_messaging_hub()
        if hub:
            hub.task_lifecycle(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                event="TASK_STARTED",
                agent_id=agent_id,
                task_id=task_id,
            )

        # Step 1: Resolve input artifacts from the database
        resolved_inputs: list[dict[str, Any]] = []
        artifact_contents: list[dict[str, Any]] = []

        if self.database and input_artifacts:
            for art_id in input_artifacts:
                # First try resolving by artifact_id directly
                artifact = self._resolve_artifact(art_id, tenant_id, workflow_id)
                if artifact:
                    resolved_inputs.append(artifact)
                    # Read content if content_path exists. Artifact files are
                    # JSON-serialized dicts — parse them so downstream agents
                    # receive real objects in `content`, not raw text.
                    if artifact.get("content_path"):
                        try:
                            raw = Path(artifact["content_path"]).read_text(encoding="utf-8")
                            try:
                                parsed: Any = json.loads(raw)
                            except (ValueError, TypeError):
                                parsed = raw
                            artifact_contents.append({
                                "artifact_id": artifact.get("artifact_id", art_id),
                                "input_ref": art_id,
                                "name": artifact["name"],
                                "kind": artifact["kind"],
                                "content_hash": artifact["content_hash"],
                                "content": parsed,
                                "provenance": artifact.get("provenance", []),
                            })
                        except (OSError, UnicodeDecodeError):
                            pass
                    elif artifact.get("content"):
                        # Artifact content stored inline
                        artifact_contents.append({
                            "artifact_id": artifact.get("artifact_id", art_id),
                            "input_ref": art_id,
                            "name": artifact["name"],
                            "kind": artifact["kind"],
                            "content_hash": artifact["content_hash"],
                            "content": artifact["content"],
                            "provenance": artifact.get("provenance", []),
                        })

        # Retrieve previous messages from the messaging hub: task-scoped AND
        # workflow-level (so targeted agent-to-agent QUESTION/ANSWER messages
        # sent under another task are visible). Read-only (mark_processed=False).
        previous_messages: list[dict[str, Any]] = []
        if hub:
            seen: set[str] = set()
            for batch in (
                hub.receive(
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    message_type=None,
                    limit=50,
                    mark_processed=False,
                ),
                hub.receive(
                    workflow_id=workflow_id,
                    tenant_id=tenant_id,
                    task_id=None,
                    message_type=None,
                    limit=50,
                    mark_processed=False,
                ),
            ):
                for msg in batch:
                    mid = msg.get("message_id")
                    if mid and mid in seen:
                        continue
                    if mid:
                        seen.add(mid)
                    previous_messages.append(msg)
            previous_messages.sort(key=lambda m: m.get("created_at") or "")

        # Add input artifact metadata (without content) to context
        # so agents know what they're consuming
        input_artifact_metadata = [
            {
                "artifact_id": a.get("artifact_id"),
                "kind": a.get("kind"),
                "name": a.get("name"),
                "content_hash": a.get("content_hash"),
                "reality": a.get("reality", "UNKNOWN"),
                "provenance": a.get("provenance", []),
            }
            for a in resolved_inputs
        ]

        # Step 2: Resolve agent parameters from the workflow plan
        # Phase 7: propagate execution strategy (task param > executor default > env).
        import os as _os
        full_params = dict(params)
        if "execution_strategy" not in full_params:
            full_params["execution_strategy"] = getattr(
                self, "default_execution_strategy",
                _os.getenv("NEXUS_EXECUTION_MODE", "DETERMINISTIC"),
            )

        # Step 3: Look up the agent instance
        agent_info = self.registry.get_agent(agent_id) if agent_id else None
        agent_instance = self.registry.get_agent_instance(agent_id) if agent_id else None

        if agent_instance is None:
            # Fallback: if no registered instance, check if task_type maps to a known agent
            agent_instance = self._lookup_fallback_agent(task_type, agent_id)
            if agent_instance is None:
                raise ValueError(f"No agent instance registered for agent_id='{agent_id}'")

        # Phase 8: inject the shared model router into the RESOLVED agent
        # instance (registered or fallback-created) so MODEL/HYBRID agents
        # reach a real provider through ModelRouter instead of silently
        # falling back to deterministic reasoning.
        if getattr(self, "model_router", None) is not None:
            try:
                agent_instance.model_router = self.model_router
            except Exception:
                pass

        # Step 4: Construct execution context with Phase 4 extensions
        context = AgentContext(
            workflow_id=workflow_id,
            task_id=task_id,
            task_name=task_name,
            agent_id=agent_id,
            scope=scope,
            parameters=full_params,
            input_artifacts=input_artifact_metadata,
            artifact_contents=artifact_contents,
            principal=self.principal,
            tenant_id=tenant_id,
            project_id=self.principal.get("project_id", ""),
            objective=objective,
            constraints=constraints,
            previous_messages=[
                {"message_type": m.get("message_type"), "content": m.get("content", {}), "from_agent_id": m.get("from_agent_id"), "created_at": m.get("created_at")}
                for m in previous_messages
            ],
            execution_metadata={
                "task_type": task_type,
                "capabilities_requested": capabilities,
                "scope": scope,
                "observation_scope": observation_scope,
                "input_artifact_count": len(resolved_inputs),
            },
            observation_scope=observation_scope,
            messaging_hub=hub,
            connector_registry=self.connector_registry,
        )

        # Step 5: Execute via the agent (with lifecycle tracking so the
        # registry knows who is BUSY vs AVAILABLE without hardcoding agents
        # in the engine).
        if hasattr(self.registry, "mark_task_started"):
            try:
                self.registry.mark_task_started(agent_id, task_id)
            except (AttributeError, TypeError):
                pass
        try:
            result = agent_instance.execute(context)
        except Exception:
            if hasattr(self.registry, "mark_task_finished"):
                try:
                    self.registry.mark_task_finished(agent_id, task_id, "FAILED")
                except (AttributeError, TypeError):
                    pass
            raise
        if hasattr(self.registry, "mark_task_finished"):
            try:
                self.registry.mark_task_finished(agent_id, task_id, getattr(result, "status", "COMPLETED"))
            except (AttributeError, TypeError):
                pass

        # Step 6: Enrich result with execution metadata
        if not result.provenance:
            result.provenance = [f"agent:{agent_id}", f"type:{agent_info.role if agent_info else 'unknown'}"]

        result.execution_metadata = {
            **getattr(result, "execution_metadata", {}),
            "task_type": task_type,
            "scope": scope,
            "observation_scope": observation_scope,
            "input_artifact_count": len(resolved_inputs),
            "capabilities_used": capabilities,
            "execution_time": _now(),
        }

        # Emit completion/failure message
        if hub and result.status == "COMPLETED":
            hub.task_lifecycle(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                event="TASK_COMPLETED",
                agent_id=agent_id,
                task_id=task_id,
                details={"artifact_count": len(result.artifacts)},
            )
        elif hub and result.status == "FAILED":
            hub.task_lifecycle(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                event="TASK_FAILED",
                agent_id=agent_id,
                task_id=task_id,
                details={"error": result.error or "unknown"},
            )

        return result

    def _resolve_artifact(self, artifact_id: str, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        """Resolve an artifact by ID, checking multiple lookup strategies."""
        if not self.database:
            return None

        # Try direct lookup by artifact_id
        artifact = self.database.get_artifact(tenant_id, artifact_id)
        if artifact:
            return self._normalize_artifact(artifact)

        # Try name-based resolution within the workflow
        workflow_artifacts = self.database.list_workflow_artifacts(tenant_id, workflow_id)
        for art in workflow_artifacts:
            if art.get("name") == artifact_id or art.get("kind") == artifact_id:
                return self._normalize_artifact(art)

        return None

    def _normalize_artifact(self, row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a database artifact row into a consistent format."""
        result = dict(row)
        if "parent_artifacts_json" in result:
            result["parent_artifacts"] = json.loads(result.pop("parent_artifacts_json"))
        return result

    def _lookup_fallback_agent(self, task_type: str, agent_id: str | None) -> Any:
        """Look up an agent instance by task type or agent_id as fallback."""
        # Map task types to default agent roles
        type_to_agent = {
            "research": ("researcher", "ResearchAgent"),
            "architecture-analysis": ("architect", "ArchitectureAgent"),
            "security-analysis": ("security-analyst", "SecurityAgent"),
            "verification": ("verifier", "VerificationAgent"),
            "report": ("reporter", "ReportAgent"),
            "implementation": ("implementer", "ArchitectureAgent"),
            "qa": ("qa-specialist", "VerificationAgent"),
        }

        if task_type in type_to_agent:
            default_id, agent_class_name = type_to_agent[task_type]
            agent_id = agent_id or default_id
            # Try to get from registry first
            instance = self.registry.get_agent_instance(agent_id)
            if instance:
                return instance
            # Create a default instance
            return self._create_default_agent(agent_id, agent_class_name)

        # Fallback to generic agent
        instance = self.registry.get_agent_instance(agent_id) if agent_id else None
        if instance:
            return instance
        return self._create_default_agent(agent_id or "generic-agent", "GenericAgent")

    def _create_default_agent(self, agent_id: str, class_name: str) -> Any:
        """Create a default agent instance when none is registered."""
        from runtime.agents.researcher import ResearchAgent
        from runtime.agents.architect import ArchitectureAgent
        from runtime.agents.security_analyst import SecurityAgent
        from runtime.agents.reporter import ReportAgent
        from runtime.agents.verifier import VerificationAgent
        from runtime.agents.generic import GenericAgent

        agents = {
            "ResearchAgent": ResearchAgent,
            "ArchitectureAgent": ArchitectureAgent,
            "SecurityAgent": SecurityAgent,
            "ReportAgent": ReportAgent,
            "VerificationAgent": VerificationAgent,
            "GenericAgent": GenericAgent,
        }

        cls = agents.get(class_name, GenericAgent)
        agent = cls()
        # Override agent_id if different from default
        if agent.agent_id != agent_id:
            agent.agent_id = agent_id
        return agent


def register_default_agents(registry: AgentRegistry) -> None:
    """Register the default set of NEXUS agents into the given registry.

    These agents cover the standard workflow pipeline:
    research → architecture-analysis → security-analysis → verification → report
    """
    from runtime.agents.researcher import ResearchAgent
    from runtime.agents.architect import ArchitectureAgent
    from runtime.agents.security_analyst import SecurityAgent
    from runtime.agents.reporter import ReportAgent
    from runtime.agents.verifier import VerificationAgent
    from runtime.agents.generic import GenericAgent

    # Researcher: filesystem read, produces OBSERVED evidence
    researcher = ResearchAgent()
    registry.register_agent(
        agent_id=researcher.agent_id,
        name=researcher.name,
        role=researcher.role,
        agent_type="SPECIALIST",
        capabilities=researcher.capabilities,
        allowed_operations=researcher.allowed_operations,
        prohibited_operations=researcher.prohibited_operations,
        expected_behaviour="Observes project files using BoundedAgentRuntime for real filesystem reads",
        instance=researcher,
    )

    # Architect: analyzes research, produces INFERRED plan
    architect = ArchitectureAgent()
    registry.register_agent(
        agent_id=architect.agent_id,
        name=architect.name,
        role=architect.role,
        agent_type="SPECIALIST",
        capabilities=architect.capabilities,
        allowed_operations=architect.allowed_operations,
        prohibited_operations=architect.prohibited_operations,
        expected_behaviour="Analyzes research findings and produces an architecture plan",
        instance=architect,
    )

    # Security Analyst: scans for security issues, produces INFERRED report
    security = SecurityAgent()
    registry.register_agent(
        agent_id=security.agent_id,
        name=security.name,
        role=security.role,
        agent_type="SPECIALIST",
        capabilities=security.capabilities,
        allowed_operations=security.allowed_operations,
        prohibited_operations=security.prohibited_operations,
        expected_behaviour="Performs deterministic security analysis of architecture plans",
        instance=security,
    )

    # Verifier: deterministic verification, can produce VERIFIED artifacts
    verifier = VerificationAgent()
    registry.register_agent(
        agent_id=verifier.agent_id,
        name=verifier.name,
        role=verifier.role,
        agent_type="SPECIALIST",
        capabilities=verifier.capabilities,
        allowed_operations=verifier.allowed_operations,
        prohibited_operations=verifier.prohibited_operations,
        expected_behaviour="Performs deterministic verification of workflow output artifacts",
        instance=verifier,
    )

    # Reporter: synthesizes final report, produces INFERRED synthesis
    reporter = ReportAgent()
    registry.register_agent(
        agent_id=reporter.agent_id,
        name=reporter.name,
        role=reporter.role,
        agent_type="SPECIALIST",
        capabilities=reporter.capabilities,
        allowed_operations=reporter.allowed_operations,
        prohibited_operations=reporter.prohibited_operations,
        expected_behaviour="Generates consolidated final report from upstream artifacts",
        instance=reporter,
    )

    # Generic: fallback for non-specialized tasks
    generic = GenericAgent()
    registry.register_agent(
        agent_id=generic.agent_id,
        name=generic.name,
        role=generic.role,
        agent_type="SPECIALIST",
        capabilities=generic.capabilities,
        allowed_operations=generic.allowed_operations,
        prohibited_operations=generic.prohibited_operations,
        expected_behaviour="Generic task executor for non-specialized task types",
        instance=generic,
    )
