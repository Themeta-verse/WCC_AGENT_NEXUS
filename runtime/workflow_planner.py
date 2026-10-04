"""NEXUS Phase 3 — WorkflowPlanner: autonomous planner for multi-agent workflows.

The Planner accepts a high-level user objective and produces a validated,
executable workflow/task graph using the existing AgentRegistry capabilities.
It does NOT execute tasks — that is the WorkflowEngine's job. The Planner
plans; the Engine executes; agents perform work; ArtifactFabric stores results;
Verification validates.

The planner is deterministic — it parses objective keywords using simple
pattern matching and maps them to predefined task templates. No LLM is used.

Truth boundary preserved:
- Planning output is INFERRED and untrusted
- No fabricated execution claims
- All capability matching is validated against the AgentRegistry
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import hashlib
import json
import re


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass
class PlannedTask:
    """A planned task within a workflow graph."""
    task_id: str
    name: str
    task_type: str
    required_capabilities: list[str]
    depends_on: list[str]
    input_artifacts: list[str]
    output_artifacts: list[str]
    agent_id: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_spec(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "required_capabilities": self.required_capabilities,
            "depends_on": self.depends_on,
            "input_artifacts": self.input_artifacts,
            "parameters": self.parameters,
        }


@dataclass
class PlanningValidation:
    """Result of a planning validation check."""
    check: str
    passed: bool
    detail: str


@dataclass
class PlannedWorkflow:
    """A complete planned workflow ready for execution by the WorkflowEngine.

    This is the output of planning. It contains the full task graph, agent
    assignments, and validation results. It is treated as INFERRED and untrusted
    until independently verified through execution.
    """
    workflow_id: str
    name: str
    objective: str
    scope: str
    task_specs: list[dict[str, Any]]
    agents: list[dict[str, Any]]
    execution_mode: str
    plan: dict[str, Any]
    validations: list[PlanningValidation]
    planned_at: str = field(default_factory=_now)

    @property
    def is_valid(self) -> bool:
        """All validations must pass for the plan to be considered valid."""
        return all(v.passed for v in self.validations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "objective": self.objective,
            "scope": self.scope,
            "execution_mode": self.execution_mode,
            "task_specs": self.task_specs,
            "agents": self.agents,
            "plan": self.plan,
            "validations": [{"check": v.check, "passed": v.passed, "detail": v.detail} for v in self.validations],
            "is_valid": self.is_valid,
            "planned_at": self.planned_at,
        }


# ---------------------------------------------------------------------------
# Task Templates: Maps objective categories to task sequences with
# dependency structure (index-based) and parallel execution groups.
#
# Dependencies: list of (from_index, to_index) tuples.
#   Task at to_index depends on task at from_index and consumes its output artifacts.
#
# Parallel groups: list of lists of task indices that can execute concurrently.
#   Groups must be ordered such that all dependencies appear in earlier groups.
# ---------------------------------------------------------------------------

TASK_TEMPLATES: dict[str, dict[str, Any]] = {
    "repository_analysis": {
        "name": "Repository Analysis Workflow",
        "description": "Research repository, analyze architecture and security, generate report",
        "tasks": [
            {
                "task_type": "research",
                "name": "Research repository",
                "required_capabilities": ["filesystem.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe project files to produce real evidence",
            },
            {
                "task_type": "architecture-analysis",
                "name": "Architecture analysis",
                "required_capabilities": [],
                "output_artifacts": ["architecture_plan"],
                "parameters": {},
                "rationale": "Derive architecture plan from research findings",
            },
            {
                "task_type": "security-analysis",
                "name": "Security analysis",
                "required_capabilities": [],
                "output_artifacts": ["security_report"],
                "parameters": {},
                "rationale": "Scan architecture plan for security issues",
            },
            {
                "task_type": "report",
                "name": "Generate final report",
                "required_capabilities": [],
                "output_artifacts": ["final_report"],
                "parameters": {},
                "rationale": "Synthesize research, architecture, and security findings",
            },
            {
                "task_type": "verification",
                "name": "Verify results",
                "required_capabilities": [],
                "output_artifacts": ["verification_result"],
                "parameters": {},
                "rationale": "Independently verify all upstream artifacts",
            },
        ],
        "dependencies": [(0, 1), (0, 2), (0, 3), (1, 3), (2, 3), (3, 4)],
        "parallel_groups": [[0], [1, 2], [3], [4]],
    },
    "repository_audit": {
        "name": "Repository Audit Workflow",
        "description": "Research, engineering audit, security audit, documentation audit, recommendation, verification",
        "tasks": [
            {
                "task_type": "research",
                "name": "Research repository",
                "required_capabilities": ["filesystem.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe project files for audit evidence",
            },
            {
                "task_type": "engineering-analysis",
                "name": "Engineering audit",
                "required_capabilities": [],
                "output_artifacts": ["engineering_analysis"],
                "parameters": {},
                "rationale": "Audit engineering health from observed evidence",
            },
            {
                "task_type": "security-analysis",
                "name": "Security audit",
                "required_capabilities": [],
                "output_artifacts": ["security_report"],
                "parameters": {},
                "rationale": "Audit security from observed evidence",
            },
            {
                "task_type": "qa-analysis",
                "name": "Documentation audit",
                "required_capabilities": [],
                "output_artifacts": ["qa_report"],
                "parameters": {},
                "rationale": "Audit documentation from observed evidence",
            },
            {
                "task_type": "report",
                "name": "Compose audit recommendation",
                "required_capabilities": [],
                "output_artifacts": ["final_report"],
                "parameters": {},
                "rationale": "Compose final audit recommendation",
            },
            {
                "task_type": "verification",
                "name": "Verify audit results",
                "required_capabilities": [],
                "output_artifacts": ["verification_result"],
                "parameters": {},
                "rationale": "Verify audit criteria independently",
            },
        ],
        "dependencies": [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
        "parallel_groups": [[0], [1], [2], [3], [4], [5]],
    },
    "repository_health": {
        "name": "Repository Health Workflow",
        "description": "Research, engineering/security/QA analysis in parallel, recommendation, verification",
        "tasks": [
            {
                "task_type": "research",
                "name": "Research repository",
                "required_capabilities": ["filesystem.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe repository for health evidence",
            },
            {
                "task_type": "engineering-analysis",
                "name": "Engineering analysis",
                "required_capabilities": [],
                "output_artifacts": ["engineering_analysis"],
                "parameters": {},
                "rationale": "Derive engineering health from research",
            },
            {
                "task_type": "security-analysis",
                "name": "Security analysis",
                "required_capabilities": [],
                "output_artifacts": ["security_report"],
                "parameters": {},
                "rationale": "Identify security risks from evidence",
            },
            {
                "task_type": "qa-analysis",
                "name": "QA analysis",
                "required_capabilities": [],
                "output_artifacts": ["qa_report"],
                "parameters": {},
                "rationale": "Assess test and verification posture",
            },
            {
                "task_type": "report",
                "name": "Compose recommendation",
                "required_capabilities": [],
                "output_artifacts": ["final_report"],
                "parameters": {},
                "rationale": "Compose evidence-based recommendation",
            },
            {
                "task_type": "verification",
                "name": "Verify mission criteria",
                "required_capabilities": [],
                "output_artifacts": ["verification_result"],
                "parameters": {},
                "rationale": "Independently verify completion criteria",
            },
        ],
        "dependencies": [(0, 1), (0, 2), (0, 3), (1, 4), (2, 4), (3, 4), (4, 5)],
        "parallel_groups": [[0], [1, 2, 3], [4], [5]],
    },
    "document_analysis": {
        "name": "Document Analysis Workflow",
        "description": "Research, report, verification",
        "tasks": [
            {
                "task_type": "research",
                "name": "Analyze document",
                "required_capabilities": ["filesystem.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe document content",
            },
            {
                "task_type": "report",
                "name": "Generate analysis report",
                "required_capabilities": [],
                "output_artifacts": ["final_report"],
                "parameters": {},
                "rationale": "Synthesize analysis into report",
            },
            {
                "task_type": "verification",
                "name": "Verify results",
                "required_capabilities": [],
                "output_artifacts": ["verification_result"],
                "parameters": {},
                "rationale": "Verify analysis evidence",
            },
        ],
        "dependencies": [(0, 1), (1, 2)],
        "parallel_groups": [[0], [1], [2]],
    },
    "decision_support": {
        "name": "Decision Support Workflow",
        "description": "Research, compare options, report, verification",
        "tasks": [
            {
                "task_type": "research",
                "name": "Research options",
                "required_capabilities": ["filesystem.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe evidence for decision options",
            },
            {
                "task_type": "report",
                "name": "Compare options and decide",
                "required_capabilities": [],
                "output_artifacts": ["final_report"],
                "parameters": {},
                "rationale": "Compare options using available evidence",
            },
            {
                "task_type": "verification",
                "name": "Verify decision evidence",
                "required_capabilities": [],
                "output_artifacts": ["verification_result"],
                "parameters": {},
                "rationale": "Verify decision is traceable to evidence",
            },
        ],
        "dependencies": [(0, 1), (1, 2)],
        "parallel_groups": [[0], [1], [2]],
    },
    "simple_research": {
        "name": "Simple Research Workflow",
        "description": "Research a topic and report findings",
        "tasks": [
            {
                "task_type": "research",
                "name": "Research topic",
                "required_capabilities": ["filesystem.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe relevant evidence",
            },
            {
                "task_type": "report",
                "name": "Generate report",
                "required_capabilities": [],
                "output_artifacts": ["final_report"],
                "parameters": {},
                "rationale": "Synthesize findings",
            },
            {
                "task_type": "verification",
                "name": "Verify findings",
                "required_capabilities": [],
                "output_artifacts": ["verification_result"],
                "parameters": {},
                "rationale": "Verify research evidence",
            },
        ],
        "dependencies": [(0, 1), (1, 2)],
        "parallel_groups": [[0], [1], [2]],
    },
    "multi_source_correlation": {
        "name": "Multi-Source Correlation Workflow",
        "description": "Independent GitHub + git + filesystem observations, correlated without overwriting",
        "tasks": [
            {
                "task_type": "research",
                "name": "GitHub observation",
                "required_capabilities": ["filesystem.read", "github.repository.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe remote repository via connector fabric",
            },
            {
                "task_type": "research",
                "name": "Git inspection",
                "required_capabilities": ["git.status"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe local git state via connector fabric",
            },
            {
                "task_type": "research",
                "name": "Workspace observation",
                "required_capabilities": ["filesystem.read"],
                "output_artifacts": ["research_report"],
                "parameters": {},
                "rationale": "Observe local workspace files",
            },
            {
                "task_type": "report",
                "name": "Correlate observations",
                "required_capabilities": [],
                "output_artifacts": ["final_report"],
                "parameters": {},
                "rationale": "Correlate independent observations into inference",
            },
            {
                "task_type": "verification",
                "name": "Verify correlation",
                "required_capabilities": [],
                "output_artifacts": ["verification_result"],
                "parameters": {},
                "rationale": "Independently verify each observation and the correlation",
            },
        ],
        "dependencies": [(0, 3), (1, 3), (2, 3), (3, 4)],
        "parallel_groups": [[0, 1, 2], [3], [4]],
    },
}

# Mapping from objective keywords to template keys (checked in order)
OBJECTIVE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("multi_source_correlation", ["correlate", "multi-source", "multi source", "inspect the repository metadata, inspect", "observations"]),
    ("repository_audit", ["audit", "compliance", "assessment"]),
    ("decision_support", ["compare", "options", "decision", "choose", "which", "best"]),
    ("document_analysis", ["document", "readme", "docs", "file contents", "local file", "filesystem"]),
    ("repository_health", ["health", "engineering risk", "unresolved risk", "diagnos", "improve", "project review", "project state", "state of", "review current"]),
    ("repository_analysis", ["repository", "repo", "github", "branch", "commit", "tree", "codebase", "code base", "analyze repository", "analyze repo"]),
]

# Mapping from task_type to capabilities
TASK_TYPE_CAPABILITIES: dict[str, list[str]] = {
    "research": ["filesystem.read"],
    "architecture-analysis": [],
    "security-analysis": [],
    "engineering-analysis": [],
    "qa-analysis": [],
    "report": [],
    "verification": [],
}

# Mapping from task_type to preferred agent role for fallback selection
TASK_TYPE_AGENT_ROLE: dict[str, str] = {
    "research": "researcher",
    "architecture-analysis": "architect",
    "security-analysis": "security-analyst",
    "engineering-analysis": "architect",
    "qa-analysis": "qa-specialist",
    "report": "reporter",
    "verification": "verifier",
}


def _is_scope_bound(capability: str, connector_registry: Any) -> bool:
    """True when a capability declaration constrains its scope kind.

    Used when merging template capabilities with scope-resolved ones: a
    scope-BOUND capability that the registry says cannot accept this scope is
    dropped (it could only produce a guaranteed failure), while an unbound
    capability is preserved (its applicability is not scope-dependent).
    """
    if connector_registry is None:
        return False
    try:
        from runtime.capability_fabric import capability_accepts_scope as _accepts
    except ImportError:  # pragma: no cover
        return False
    for connector_id in connector_registry.connector_ids():
        connector = connector_registry.get_connector(connector_id)
        declaration = (getattr(connector, "capabilities", {}) or {}).get(capability)
        if isinstance(declaration, dict) and declaration.get("scope_kind"):
            return not _accepts(declaration, "")
    return False


class PlanningError(Exception):
    """Raised when planning fails due to invalid input or missing capabilities."""


class WorkflowPlanner:
    """Autonomous workflow planner that produces validated task graphs.

    The planner classifies the objective, selects an appropriate task template,
    matches tasks to agents via the AgentRegistry, validates the graph for
    cycles and missing dependencies, and returns a PlannedWorkflow.

    Architecture rule: The Planner plans. It does NOT execute. Execution is
    delegated to the WorkflowEngine after the plan is reviewed.
    """

    def __init__(self, agent_registry: Any = None, connector_registry: Any = None):
        from runtime.agent_registry import AgentRegistry
        self.registry = agent_registry if agent_registry is not None else AgentRegistry()
        # Canonical connector registry, used ONLY to ask which capabilities can
        # consume the requested scope. When absent the planner falls back to the
        # canonical process registry; it never guesses capability names.
        if connector_registry is not None:
            self.connector_registry = connector_registry
        else:
            try:
                from runtime.capability_fabric import get_capability_registry
                self.connector_registry = get_capability_registry()
            except Exception:  # pragma: no cover - defensive
                self.connector_registry = None

    def _observation_capabilities_for_scope(self, scope: str) -> list[str]:
        """Pick OBSERVATION capabilities from the SCOPE, generically.

        Hardcoding one capability per task type was wrong: a research task scoped
        to a two-segment repository reference cannot be served by a local
        filesystem read, and asking for one produced an honest-but-useless
        failure. Instead this asks the connector registry which declared
        capabilities accept this scope, preferring capabilities that are
        actually selectable (auth-usable), and returns their NAMES only — no
        provider is named, imported or special-cased in this planner.

        Order is the canonical resolution preference, so the first usable entry
        is the one capability resolution would pick anyway. Unusable-but-declared
        capabilities are still returned (appended) when nothing is usable, so the
        refusal is explicit at execution time instead of being silently dropped.
        """
        if self.connector_registry is None:
            return []
        try:
            entries = self.connector_registry.resolve_for_scope(scope) or []
        except Exception:
            return []
        usable: list[str] = []
        declared: list[str] = []
        for entry in entries:
            name = str(entry.get("capability") or "")
            if not name or name in declared:
                continue
            declared.append(name)
            if entry.get("auth_usable") and name not in usable:
                usable.append(name)
        return usable or declared

    def _classify_objective(self, objective: str) -> str:
        """Classify a high-level objective into a template category.

        Uses deterministic keyword matching — no LLM.
        Checks more specific templates first (audit before general analysis).
        """
        text = (objective or "").lower()

        for template_key, keywords in OBJECTIVE_KEYWORDS:
            if any(kw in text for kw in keywords):
                return template_key

        # Default: simple research
        return "simple_research"

    def _select_agent_for_task(
        self,
        task_type: str,
        required_capabilities: list[str],
        tenant_id: str | None = None,
    ) -> tuple[str | None, list[str]]:
        """Select an agent from the registry for a task type.

        Returns (agent_id, capabilities_used).
        Tries capability matching first, then falls back to task-type-based role matching.
        """
        # Try capability-based selection first (with and without tenant)
        if required_capabilities:
            agent = self.registry.select_agent(
                required_capabilities=required_capabilities,
                tenant_id=tenant_id,
            )
            if agent is None and tenant_id:
                # Fallback: try without tenant scope
                agent = self.registry.select_agent(
                    required_capabilities=required_capabilities,
                    tenant_id=None,
                )
            if agent is not None:
                return agent.agent_id, required_capabilities

        # Try task-type-based role matching (with and without tenant)
        preferred_role = TASK_TYPE_AGENT_ROLE.get(task_type)
        if preferred_role:
            for a in self.registry.list_agents(tenant_id=tenant_id, status="ACTIVE"):
                if a.role == preferred_role:
                    return a.agent_id, a.capabilities
            # Fallback without tenant
            if tenant_id:
                for a in self.registry.list_agents(tenant_id=None, status="ACTIVE"):
                    if a.role == preferred_role:
                        return a.agent_id, a.capabilities

        # Try any active agent whose capabilities match the task type's capabilities
        task_caps = TASK_TYPE_CAPABILITIES.get(task_type, [])
        if task_caps:
            for a in self.registry.list_agents(tenant_id=tenant_id, status="ACTIVE"):
                if a.matches_capabilities(task_caps):
                    return a.agent_id, task_caps
            if tenant_id:
                for a in self.registry.list_agents(tenant_id=None, status="ACTIVE"):
                    if a.matches_capabilities(task_caps):
                        return a.agent_id, task_caps

        # Fallback: any active agent
        candidates = self.registry.list_agents(tenant_id=tenant_id, status="ACTIVE")
        if not candidates and tenant_id:
            candidates = self.registry.list_agents(tenant_id=None, status="ACTIVE")
        if candidates:
            a = candidates[0]
            return a.agent_id, a.capabilities

        return None, []

    def _apply_scope_capabilities(self, task_specs: list[dict[str, Any]], scope: str) -> None:
        """Rewrite research-task capabilities from the SCOPE, generically.

        This used to be a provider-specific exception duplicated in two call
        sites: "if the template is repository_* AND the scope looks like
        owner/repo, append the literal name 'github.repository.read'". That
        hardcoded one provider into a generic planner, and still left every
        OTHER template with an owner/repo scope asking for filesystem.read — a
        capability that cannot consume a repository reference at all, so the
        research task could only fail.

        Now the registry is asked which declared capabilities accept this scope.
        Any provider works, including ones added after this code was written.

        The result is intersected with the capabilities the SELECTED AGENT
        actually implements. Requesting every scope-matching capability would ask
        one research task to run four providers, three of which the agent has no
        implementation for — guaranteed failures, not observations. So a plan
        asks for what can actually be executed here.
        """
        if not scope:
            return
        scope_caps = self._observation_capabilities_for_scope(scope)
        if not scope_caps:
            return
        for spec in task_specs:
            if spec.get("task_type") != "research":
                continue
            agent_id, agent_caps = self._select_agent_for_task(
                task_type="research",
                required_capabilities=[],
                tenant_id=spec.get("tenant_id"),
            )
            if agent_id and not spec.get("agent_id"):
                spec["agent_id"] = agent_id
            # Intersect with what the agent implements, keeping the registry's
            # preference order. An agent that declares no capability list is
            # treated as unconstrained (the full scope set is allowed).
            if agent_caps:
                allowed = set(agent_caps)
                usable = [c for c in scope_caps if c in allowed]
                if not usable:
                    # The agent cannot observe this scope at all. Say so
                    # explicitly rather than planning a guaranteed failure.
                    spec["required_capabilities"] = []
                    spec["scope_unobservable_by_agent"] = True
                    continue
                scope_caps_for_task = usable
            else:
                scope_caps_for_task = scope_caps
            existing = list(spec.get("required_capabilities") or [])
            # Scope-consistent capabilities first (they are what can actually
            # observe this scope), then template-declared extras that are not
            # scope-bound, preserving template intent without contradiction.
            merged = list(scope_caps_for_task)
            for cap in existing:
                if cap not in merged and not _is_scope_bound(cap, self.connector_registry):
                    merged.append(cap)
            spec["required_capabilities"] = merged

    def _build_task_specs(
        self,
        template: dict[str, Any],
        task_id_prefix: str,
    ) -> list[dict[str, Any]]:
        """Build task specs with dependency-aware input/output artifact wiring.

        Task IDs are assigned sequentially: {prefix}-0, {prefix}-1, etc.
        Dependencies use index-based references (from_idx, to_idx).
        """
        # First pass: create basic task specs
        task_specs: list[dict[str, Any]] = []
        for i, task_def in enumerate(template["tasks"]):
            task_id = f"{task_id_prefix}-{i}"
            task_specs.append({
                "task_id": task_id,
                "name": task_def["name"],
                "task_type": task_def["task_type"],
                "agent_id": None,
                "required_capabilities": list(task_def["required_capabilities"]),
                "depends_on": [],
                "input_artifacts": [],
                "output_artifacts": list(task_def["output_artifacts"]),
                "parameters": dict(task_def.get("parameters", {})),
                "rationale": task_def.get("rationale", ""),
            })

        # Second pass: wire up dependencies and input artifacts
        for from_idx, to_idx in template["dependencies"]:
            from_id = f"{task_id_prefix}-{from_idx}"
            to_id = f"{task_id_prefix}-{to_idx}"
            to_spec = task_specs[to_idx]
            from_spec = task_specs[from_idx]

            # Add dependency
            if from_id not in to_spec["depends_on"]:
                to_spec["depends_on"].append(from_id)

            # Wire input artifacts from upstream outputs
            for art_name in from_spec["output_artifacts"]:
                if art_name not in to_spec["input_artifacts"]:
                    to_spec["input_artifacts"].append(art_name)

        return task_specs

    def _build_parallel_groups(
        self,
        template: dict[str, Any],
        task_id_prefix: str,
    ) -> list[list[str]]:
        """Convert index-based parallel groups to task ID-based groups."""
        result = []
        for group in template["parallel_groups"]:
            resolved = [f"{task_id_prefix}-{idx}" for idx in group]
            result.append(resolved)
        return result

    def plan(
        self,
        objective: str,
        scope: str,
        constraints: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        execution_mode: str = "REAL_READ",
        project_id: str = "",
    ) -> PlannedWorkflow:
        """Plan a workflow from a high-level objective.

        Args:
            objective: High-level user objective (e.g. "Analyze this repository and produce an architecture and security report")
            scope: Scope of the objective (e.g. "Themeta-verse/Nexus")
            constraints: Optional constraints dict
            tenant_id: Optional tenant scope for agent selection
            execution_mode: Execution mode for the workflow
            project_id: Project ID for the workflow

        Returns:
            PlannedWorkflow with validated task graph

        Raises:
            PlanningError: If planning fails (missing capabilities, invalid objective, etc.)
        """
        if not objective or not objective.strip():
            raise PlanningError("objective must not be empty")

        constraints = constraints or {}
        from runtime.execution_environment import resolve_environment
        execution_environment = resolve_environment(
            constraints.get("execution_environment")).value
        template_key = self._classify_objective(objective)
        template = TASK_TEMPLATES[template_key]

        from runtime.canonical_core import core_id
        workflow_id = f"workflow-{core_id('wf')}"
        task_id_prefix = "task"

        # Build task graph from template
        task_specs = self._build_task_specs(template, task_id_prefix)

        # Observation capabilities are chosen from the SCOPE, generically.
        self._apply_scope_capabilities(task_specs, scope)

        parallel_groups = self._build_parallel_groups(template, task_id_prefix)

        # Select agents for each task
        agent_specs: list[dict[str, Any]] = []
        for spec in task_specs:
            task_type = spec["task_type"]
            required_caps = list(spec["required_capabilities"])

            agent_id, caps_used = self._select_agent_for_task(
                task_type=task_type,
                required_capabilities=required_caps,
                tenant_id=tenant_id,
            )

            spec["agent_id"] = agent_id
            # Preserve the planned requirement verbatim. Overwriting it with
            # the selected agent's capabilities would inject github.read into
            # tasks that never requested it (or drop it from tasks that did),
            # causing silent fallback or false BLOCKED. The engine matches
            # required subset against declared capabilities honestly.
            spec["required_capabilities"] = required_caps

            if agent_id:
                agent_info = self.registry.get_agent(agent_id)
                if agent_info:
                    agent_spec = {
                        "agent_id": agent_info.agent_id,
                        "name": agent_info.name,
                        "role": agent_info.role,
                        "capabilities": agent_info.capabilities,
                        "allowed_operations": agent_info.allowed_operations,
                        "prohibited_operations": agent_info.prohibited_operations,
                        "scope": {"project_id": project_id},
                        "expected_behaviour": agent_info.expected_behaviour or f"{agent_info.name} agent",
                    }
                    if not any(a.get("agent_id") == agent_spec["agent_id"] for a in agent_specs):
                        agent_specs.append(agent_spec)

        # Add generic fallback agent if not already present and no agent was assigned to any task
        has_generic = any(a.get("role") == "generic" for a in agent_specs)
        if not has_generic:
            for a in self.registry.list_agents(tenant_id=tenant_id, status="ACTIVE"):
                if a.role == "generic":
                    agent_specs.append({
                        "agent_id": a.agent_id,
                        "name": a.name,
                        "role": a.role,
                        "capabilities": a.capabilities,
                        "allowed_operations": a.allowed_operations,
                        "prohibited_operations": a.prohibited_operations,
                        "scope": {"project_id": project_id},
                        "expected_behaviour": a.expected_behaviour or "Generic task executor",
                    })
                    break

        # Phase E: stamp the bounded environment into every task's parameters
        # (persisted through plan_to_workflow_spec -> engine plan_json) so
        # agents and the tool layer enforce the same policy at execution.
        for spec in task_specs:
            params = spec.get("parameters", {}) or {}
            params.setdefault("execution_environment", execution_environment)
            spec["parameters"] = params

        # Build the plan dict (for inspection before execution)
        task_order = [s["task_id"] for s in task_specs]
        plan = {
            "workflow_id": workflow_id,
            "name": template["name"],
            "objective": objective,
            "scope": scope,
            "template_type": template_key,
            "execution_mode": execution_mode,
            "execution_environment": execution_environment,
            "task_order": task_order,
            "tasks": [s for s in task_specs],
            "parallel_groups": parallel_groups,
            "agents": agent_specs,
            "reality_model": {
                "research": "OBSERVED",
                "analysis": "INFERRED",
                "report": "INFERRED",
                "verification": "VERIFIED",
            },
            "constraints": constraints,
        }

        # Validate the plan
        validations = self._validate_plan(task_specs, parallel_groups, template, execution_mode)

        return PlannedWorkflow(
            workflow_id=workflow_id,
            name=template["name"],
            objective=objective,
            scope=scope,
            task_specs=task_specs,
            agents=agent_specs,
            execution_mode=execution_mode,
            plan=plan,
            validations=validations,
            planned_at=_now(),
        )

    def plan_with_template(
        self,
        objective: str,
        scope: str,
        template_type: str,
        constraints: dict[str, Any] | None,
    ) -> "PlannedWorkflow":
        """Plan a workflow using a specific template type.

        Unlike plan(), which auto-classifies the objective, this method
        uses the explicitly specified template.
        """
        constraints = constraints or {}
        if template_type not in TASK_TEMPLATES:
            raise PlanningError(f"unknown template type: {template_type}")
        from runtime.execution_environment import resolve_environment as _resolve_env2
        _template_env = _resolve_env2(constraints.get("execution_environment")).value

        template = TASK_TEMPLATES[template_type]
        from runtime.canonical_core import core_id
        workflow_id = f"workflow-{core_id('wf')}"
        task_id_prefix = "task"

        task_specs = self._build_task_specs(template, task_id_prefix)
        self._apply_scope_capabilities(task_specs, scope)
        parallel_groups = self._build_parallel_groups(template, task_id_prefix)

        for i, spec in enumerate(task_specs):
            task_type = spec["task_type"]
            required_caps = list(spec["required_capabilities"])
            agent_id, caps_used = self._select_agent_for_task(
                task_type=task_type,
                required_capabilities=required_caps,
                tenant_id=constraints.get("tenant_id"),
            )
            spec["agent_id"] = agent_id
            # Preserve planned requirements (see plan() comment).
            spec["required_capabilities"] = required_caps
            if agent_id:
                agent_info = self.registry.get_agent(agent_id)
                if agent_info:
                    agent_spec = {
                        "agent_id": agent_info.agent_id,
                        "name": agent_info.name,
                        "role": agent_info.role,
                        "capabilities": agent_info.capabilities,
                        "allowed_operations": agent_info.allowed_operations,
                        "prohibited_operations": agent_info.prohibited_operations,
                        "scope": {"project_id": constraints.get("project_id", "")},
                        "expected_behaviour": agent_info.expected_behaviour or f"{agent_info.name} agent",
                    }
                    if not any(a.get("agent_id") == agent_spec["agent_id"] for a in task_specs):
                        spec.setdefault("_agent_specs", []).append(agent_spec)

        agent_specs = []
        for spec in task_specs:
            for a in spec.pop("_agent_specs", []):
                if not any(existing.get("agent_id") == a.get("agent_id") for existing in agent_specs):
                    agent_specs.append(a)

        has_generic = any(a.get("role") == "generic" for a in agent_specs)
        if not has_generic:
            for a in self.registry.list_agents(tenant_id=constraints.get("tenant_id"), status="ACTIVE"):
                if a.role == "generic":
                    agent_specs.append({
                        "agent_id": a.agent_id,
                        "name": a.name,
                        "role": a.role,
                        "capabilities": a.capabilities,
                        "allowed_operations": a.allowed_operations,
                        "prohibited_operations": a.prohibited_operations,
                        "scope": {"project_id": constraints.get("project_id", "")},
                        "expected_behaviour": a.expected_behaviour or "Generic task executor",
                    })
                    break

        for spec in task_specs:
            params = spec.get("parameters", {}) or {}
            params.setdefault("execution_environment", _template_env)
            spec["parameters"] = params

        plan = {
            "template_type": template_type,
            "workflow_id": workflow_id,
            "name": template["name"],
            "objective": objective,
            "scope": scope,
            "execution_mode": constraints.get("execution_mode", "SIMULATION"),
            "execution_environment": _template_env,
            "tasks": task_specs,
            "agents": agent_specs,
            "parallel_groups": parallel_groups,
        }

        validations = self._validate_plan(task_specs, parallel_groups, template, execution_mode=plan["execution_mode"])

        return PlannedWorkflow(
            workflow_id=workflow_id,
            name=template["name"],
            objective=objective,
            scope=scope,
            task_specs=task_specs,
            agents=agent_specs,
            execution_mode=plan["execution_mode"],
            plan=plan,
            validations=validations,
            planned_at=_now(),
        )

    def _validate_plan(
        self,
        task_specs: list[dict[str, Any]],
        parallel_groups: list[list[str]],
        template: dict[str, Any],
        execution_mode: str = "REAL_READ",
    ) -> list[PlanningValidation]:
        """Run all validation checks on the planned workflow."""
        validations: list[PlanningValidation] = []

        # 1. Every task has an available agent
        missing_agents = [s for s in task_specs if s["agent_id"] is None]
        validations.append(PlanningValidation(
            check="agent_assignment",
            passed=len(missing_agents) == 0,
            detail=f"All {len(task_specs)} tasks have assigned agents" if not missing_agents
            else f"Tasks without agents: {[m['task_id'] for m in missing_agents]}",
        ))

        task_ids = {t["task_id"] for t in task_specs}

        # 2. Dependency graph is acyclic (Kahn's algorithm)
        indegree = {t["task_id"]: 0 for t in task_specs}
        children: dict[str, list[str]] = {t["task_id"]: [] for t in task_specs}

        for t in task_specs:
            for dep in t["depends_on"]:
                if dep in task_ids:
                    indegree[t["task_id"]] += 1
                    children[dep].append(t["task_id"])

        order: list[str] = []
        ready = [tid for tid, deg in indegree.items() if deg == 0]
        while ready:
            node = ready.pop(0)
            order.append(node)
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)

        has_cycle = len(order) != len(task_specs)
        validations.append(PlanningValidation(
            check="dependency_acyclic",
            passed=not has_cycle,
            detail=f"Topological sort: {len(order)}/{len(task_specs)} tasks ordered" + (" (cycle detected)" if has_cycle else ""),
        ))

        # 3. All dependencies reference existing tasks
        missing_deps = []
        for t in task_specs:
            for dep in t["depends_on"]:
                if dep not in task_ids:
                    missing_deps.append((t["task_id"], dep))

        validations.append(PlanningValidation(
            check="dependencies_exist",
            passed=len(missing_deps) == 0,
            detail="All dependencies reference existing tasks" if not missing_deps
            else f"Missing: {missing_deps[:5]}",
        ))

        # 4. Parallel groups respect dependencies (no forward references)
        gid_map: dict[str, int] = {}
        for gid, group in enumerate(parallel_groups):
            for tid in group:
                if tid in task_ids:
                    gid_map[tid] = gid

        group_order_ok = True
        for t in task_specs:
            for dep in t["depends_on"]:
                if dep in gid_map and t["task_id"] in gid_map:
                    if gid_map[dep] >= gid_map[t["task_id"]]:
                        group_order_ok = False
                        break

        validations.append(PlanningValidation(
            check="parallel_groups_valid",
            passed=group_order_ok,
            detail="Parallel groups respect dependency ordering" if group_order_ok
            else "Parallel group ordering violates dependencies",
        ))

        # 5. Input artifacts resolve to upstream outputs
        all_output_artifacts: set[str] = set()
        for t in task_specs:
            all_output_artifacts.update(t["output_artifacts"])

        unresolved_inputs = []
        for t in task_specs:
            for inp in t["input_artifacts"]:
                if inp not in all_output_artifacts and t["depends_on"]:
                    unresolved_inputs.append((t["task_id"], inp))

        validations.append(PlanningValidation(
            check="input_artifacts_resolvable",
            passed=len(unresolved_inputs) == 0,
            detail="All input artifacts resolve to upstream outputs" if not unresolved_inputs
            else f"Unresolved: {unresolved_inputs[:5]}",
        ))

        # 6. Execution mode is valid
        valid_modes = {"REAL_READ", "SIMULATION", "DRY_RUN"}
        validations.append(PlanningValidation(
            check="execution_mode_valid",
            passed=execution_mode in valid_modes,
            detail=f"Execution mode '{execution_mode}' is supported",
        ))

        return validations

    def plan_to_workflow_spec(self, planned: PlannedWorkflow) -> "WorkflowSpec":
        """Convert a PlannedWorkflow into a WorkflowSpec for the WorkflowEngine."""
        from runtime.execution_environment import resolve_environment
        from runtime.workflow_engine import WorkflowSpec
        return WorkflowSpec(
            name=planned.name,
            objective=planned.objective,
            scope=planned.scope,
            task_specs=planned.task_specs,
            agents=planned.agents,
            execution_mode=planned.execution_mode,
            execution_environment=resolve_environment(
                planned.plan.get("execution_environment")).value,
        )
