from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator


READ_CAPABILITIES = {"repository.read", "repository.metadata.read", "browser.read", "filesystem.read"}
AGENT_POLICY_CAPABILITIES = READ_CAPABILITIES | {"git.write", "filesystem.write", "write"}

AGENT_STATUSES = {"ACTIVE", "FLAGGED", "HALTED", "RETIRED"}
INTEGRITY_DECISIONS = {"ALLOW", "FLAG", "HALT"}

WORKFLOW_TASK_STATES = {"PENDING", "READY", "RUNNING", "WAITING", "COMPLETED", "FAILED", "BLOCKED", "CANCELLED", "AWAITING_APPROVAL"}
WORKFLOW_STATES = {"PENDING", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED", "AWAITING_APPROVAL"}
APPROVAL_STATES = {"PENDING", "APPROVED", "REJECTED", "CANCELLED"}


class MissionSubmission(BaseModel):
    intent: str = Field(min_length=3, max_length=4000)
    project_id: str = Field(default="local", min_length=1, max_length=100)
    scope: str = Field(default="Themeta-verse/Nexus", min_length=1, max_length=300)
    mode: Literal["REAL_READ", "SIMULATION"] = "SIMULATION"
    capabilities: list[str] | None = None
    repository_scope: str | None = None
    browser_url: str | None = None
    filesystem_path: str | None = None

    @field_validator("capabilities")
    @classmethod
    def supported_capabilities(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        unsupported = sorted(set(unique) - READ_CAPABILITIES)
        if unsupported:
            raise ValueError(f"unsupported or consequential capability request: {', '.join(unsupported)}")
        return unique


class AgentCreateRequest(BaseModel):
    agent_id: str | None = Field(default=None, min_length=1, max_length=100)
    project_id: str = Field(default="local", min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=160)


class AgentPolicyCreateRequest(BaseModel):
    declared_capabilities: list[str] = Field(min_length=1)
    allowed_operations: list[str] = Field(default_factory=list)
    prohibited_operations: list[str] = Field(default_factory=list)
    scope: dict = Field(default_factory=dict)
    expected_behaviour: str = Field(min_length=1, max_length=2000)

    @field_validator("declared_capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        unsupported = sorted(set(unique) - AGENT_POLICY_CAPABILITIES)
        if unsupported:
            raise ValueError(f"unsupported capability: {', '.join(unsupported)}")
        return unique


class AgentActionSubmission(BaseModel):
    operation: str = Field(min_length=1, max_length=100)
    target_resource: str | None = Field(default=None, max_length=500)
    requested_capability: str | None = None
    parameters: dict = Field(default_factory=dict)


class AgentObservationRequest(BaseModel):
    operation: str = Field(min_length=1, max_length=100)
    target_resource: str = Field(min_length=1, max_length=500)
    requested_capability: str | None = None
    parameters: dict = Field(default_factory=dict)
    observation_root: str = Field(min_length=1, max_length=500)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class OwnerSetupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)


class ProjectCreateRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=160)


class AgentEnforceRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in {"FLAG", "HALT", "ACTIVE"}:
            raise ValueError("action must be one of: FLAG, HALT, ACTIVE")
        return upper


class MemoryLifecycleRequest(BaseModel):
    action: Literal["retire", "restore", "annotate"]
    note: str | None = Field(default=None, max_length=1000)


class MissionResponse(BaseModel):
    mission_id: str
    project_id: str
    status: str
    reality: str
    verification_status: str
    action_state: str
    external_invocations: int
    queue: dict | None = None
    result: dict | None = None


class WorkflowTaskSpec(BaseModel):
    task_id: str = Field(min_length=1, max_length=100)
    task_type: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    agent_id: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    input_artifacts: list[str] = Field(default_factory=list)


class WorkflowAgentSpec(BaseModel):
    agent_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=100)
    capabilities: list[str] = Field(min_length=1)
    allowed_operations: list[str] = Field(default_factory=list)
    prohibited_operations: list[str] = Field(default_factory=list)
    scope: dict = Field(default_factory=dict)
    expected_behaviour: str = Field(default="", max_length=2000)


class WorkflowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=3, max_length=4000)
    scope: str = Field(default="Themeta-verse/Nexus", min_length=1, max_length=300)
    project_id: str = Field(default="local", min_length=1, max_length=100)
    task_specs: list[WorkflowTaskSpec] = Field(min_length=1)
    agents: list[WorkflowAgentSpec] = Field(default_factory=list)
    execution_mode: Literal["REAL_READ", "SIMULATION"] = "SIMULATION"


class WorkflowPlanRequest(BaseModel):
    """Request to auto-generate a workflow from a high-level objective."""
    objective: str = Field(min_length=3, max_length=4000)
    scope: str = Field(default="Themeta-verse/Nexus", min_length=1, max_length=300)
    project_id: str = Field(default="local", min_length=1, max_length=100)
    execution_mode: Literal["REAL_READ", "SIMULATION"] = "SIMULATION"
    constraints: dict = Field(default_factory=dict)


class PlanningValidationSchema(BaseModel):
    check: str
    passed: bool
    detail: str


class WorkflowResponse(BaseModel):
    workflow_id: str
    name: str
    objective: str
    scope: str
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    plan: dict | None = None
    result: dict | None = None


class WorkflowTaskResponse(BaseModel):
    task_id: str
    workflow_id: str
    task_type: str
    name: str
    agent_id: str | None
    status: str
    reality: str
    required_capabilities: list[str]
    depends_on: list[str]
    input_artifacts: list[str]
    output_artifacts: list[str]
    retry_count: int
    max_retries: int
    error: str | None
    result: dict | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


class WorkflowArtifactResponse(BaseModel):
    artifact_id: str
    workflow_id: str
    task_id: str | None
    agent_id: str | None
    kind: str
    name: str
    content_hash: str
    content_path: str | None
    content_size: int | None
    parent_artifacts: list[str]
    provenance: list[str]
    created_at: str
    reality: str
    untrusted: bool
    verification_state: str


class WorkflowEventResponse(BaseModel):
    event_id: str
    workflow_id: str
    task_id: str | None
    agent_id: str | None
    event_type: str
    detail: dict
    created_at: str


class WorkflowStateResponse(BaseModel):
    workflow: dict | None
    tasks: list[dict]
    artifacts: list[dict]
    events: list[dict]
    summary: dict


class WorkflowPlanResponse(BaseModel):
    """Response from the planner API — contains the generated plan for review."""
    workflow_id: str
    name: str
    objective: str
    scope: str
    execution_mode: str
    is_valid: bool
    task_specs: list[dict]
    agents: list[dict]
    plan: dict
    validations: list[PlanningValidationSchema]
    planned_at: str


class WorkflowMessageRequest(BaseModel):
    """Request to send a message within a workflow."""
    message_type: str = Field(min_length=1, max_length=100)
    content: dict = Field(default_factory=dict)
    to_agent_id: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None


class AutonomousRunRequest(BaseModel):
    """Request to execute an objective autonomously."""
    objective: str = Field(min_length=3, max_length=2000)
    scope: str = Field(min_length=1, max_length=300)
    template_type: str | None = None
    constraints: dict | None = None


class DynamicTaskRequest(BaseModel):
    """Request to dynamically add a task to an existing workflow."""
    task_type: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    agent_id: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    input_artifacts: list[str] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    parent_task_id: str | None = None
    reason: str | None = None


class ApprovalDecisionRequest(BaseModel):
    """Request to make an approval decision."""
    decision: str = Field(min_length=1)
    note: str | None = None


class AgentMemoryRequest(BaseModel):
    """Request to store an agent memory item."""
    scope: str = Field(min_length=1, max_length=50)
    source: str = Field(min_length=1, max_length=200)
    content: dict = Field(default_factory=dict)
    workflow_id: str | None = None
