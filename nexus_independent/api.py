from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .schemas import (
    LoginRequest,
    MemoryLifecycleRequest,
    MissionSubmission,
    OwnerSetupRequest,
    ProjectCreateRequest,
    AgentCreateRequest,
    AgentPolicyCreateRequest,
    AgentActionSubmission,
    AgentEnforceRequest,
    AgentObservationRequest,
    WorkflowCreateRequest,
    WorkflowPlanRequest,
    WorkflowMessageRequest,
    AutonomousRunRequest,
    DynamicTaskRequest,
    ApprovalDecisionRequest,
    AgentMemoryRequest,
)
from .service import StandaloneMissionService


def create_app(service: StandaloneMissionService | None = None) -> FastAPI:
    runtime = service or StandaloneMissionService()
    app = FastAPI(title="NEXUS Independent API", version="0.2.0", description="Authenticated evidence-first NEXUS mission runtime.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime.settings.web_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    def bearer_token(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer authentication is required")
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer authentication is required")
        return token

    def principal(token: str = Depends(bearer_token)) -> dict:
        identity = runtime.authenticate_bearer(token)
        if identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session is invalid, expired, or revoked")
        return identity

    def public_mission(mission: dict) -> dict:
        hidden = {"tenant_id", "store_root", "submission"}
        return {key: value for key, value in mission.items() if key not in hidden}

    @app.get("/health")
    def health() -> dict:
        return runtime.public_health()

    @app.get("/api/v1/health")
    def authenticated_health(identity: dict = Depends(principal)) -> dict:
        del identity
        return runtime.health()

    @app.post("/api/v1/auth/login")
    def login(credentials: LoginRequest) -> dict:
        session = runtime.login(credentials.email, credentials.password)
        if not session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
        return session

    @app.get("/api/v1/setup/status")
    def setup_status() -> dict:
        return runtime.setup_status()

    @app.post("/api/v1/setup/owner", status_code=status.HTTP_201_CREATED)
    def setup_owner(request: OwnerSetupRequest) -> dict:
        try:
            return runtime.setup_initial_owner(request.email, request.password)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="initial owner setup is unavailable for this runtime") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
    def register_owner(request: OwnerSetupRequest) -> dict:
        try:
            return runtime.register_owner_workspace(request.email, request.password)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner workspace registration is disabled for this runtime") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(token: str = Depends(bearer_token)) -> Response:
        runtime.logout(token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/me")
    def current_user(identity: dict = Depends(principal)) -> dict:
        return {"user": identity, "projects": runtime.list_projects(identity)}

    @app.get("/api/v1/projects")
    def list_projects(identity: dict = Depends(principal)) -> dict:
        return {"projects": runtime.list_projects(identity)}

    @app.post("/api/v1/projects", status_code=status.HTTP_201_CREATED)
    def create_project(request: ProjectCreateRequest, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.create_project(identity, request.project_id, request.display_name)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/missions")
    def list_missions(project_id: str, limit: int = Query(default=30, ge=1, le=100), identity: dict = Depends(principal)) -> dict:
        try:
            return {"project_id": project_id, "missions": [public_mission(mission) for mission in runtime.list_missions(identity, project_id, limit)]}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/memory")
    def project_memory(project_id: str, limit: int = Query(default=100, ge=1, le=200), identity: dict = Depends(principal)) -> dict:
        try:
            return {"project_id": project_id, "memory": runtime.list_memory(identity, project_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/memory/{memory_id}")
    def update_memory(project_id: str, memory_id: str, request: MemoryLifecycleRequest, identity: dict = Depends(principal)) -> dict:
        try:
            memory = runtime.update_memory(identity, project_id, memory_id, request.action, request.note)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if memory is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory record not found")
        return {"memory": memory}

    @app.get("/api/v1/projects/{project_id}/context")
    def project_context(project_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.project_context(identity, project_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/outcomes")
    def project_outcomes(project_id: str, limit: int = Query(default=100, ge=1, le=200), identity: dict = Depends(principal)) -> dict:
        try:
            return {"project_id": project_id, "outcomes": runtime.list_outcomes(identity, project_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/audit-events")
    def audit_events(project_id: str | None = None, limit: int = Query(default=100, ge=1, le=200), identity: dict = Depends(principal)) -> dict:
        try:
            return {"audit_events": runtime.list_audit_events(identity, project_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/capabilities")
    def capabilities(identity: dict = Depends(principal)) -> dict:
        return {"capabilities": runtime.capabilities(identity)}

    @app.get("/api/v1/providers")
    def providers(identity: dict = Depends(principal)) -> dict:
        return {"providers": runtime.providers(identity)}

    @app.post("/api/v1/agents", status_code=status.HTTP_201_CREATED)
    def create_agent(request: AgentCreateRequest, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.create_agent(identity, request)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.get("/api/v1/agents")
    def list_agents(project_id: str | None = None, identity: dict = Depends(principal)) -> dict:
        try:
            return {"agents": runtime.list_agents(identity, project_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/agents/{agent_id}")
    def get_agent(agent_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            agent = runtime.get_agent(identity, agent_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
        return {"agent": agent}

    @app.post("/api/v1/agents/{agent_id}/policy", status_code=status.HTTP_201_CREATED)
    def create_agent_policy(agent_id: str, request: AgentPolicyCreateRequest, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.create_agent_policy(identity, agent_id, request)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.get("/api/v1/agents/{agent_id}/policy")
    def get_agent_policy(agent_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            policy = runtime.get_agent_policy(identity, agent_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if policy is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="policy not found")
        return {"policy": policy}

    @app.get("/api/v1/agents/{agent_id}/policies")
    def list_agent_policies(agent_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return {"policies": runtime.list_agent_policies(identity, agent_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/agents/{agent_id}/actions")
    def record_agent_action(agent_id: str, request: AgentActionSubmission, identity: dict = Depends(principal)) -> dict:
        try:
            result = runtime.record_agent_action(identity, agent_id, request)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return result

    @app.post("/api/v1/agents/{agent_id}/observe")
    def observe_agent_action(agent_id: str, request: AgentObservationRequest, identity: dict = Depends(principal)) -> dict:
        """Observe a real action performed by a bounded local agent runtime.

        Unlike the manual actions endpoint, this causes the bounded runtime
        to actually execute or attempt the operation and observes the real
        result. The observation receipt contains cryptographic evidence.
        """
        try:
            result = runtime.observe_agent_action(identity, agent_id, request)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return result

    @app.get("/api/v1/agents/{agent_id}/actions")
    def get_agent_actions(agent_id: str, limit: int = Query(default=100, ge=1, le=500), identity: dict = Depends(principal)) -> dict:
        try:
            return {"actions": runtime.get_agent_actions(identity, agent_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/agents/{agent_id}/integrity")
    def get_agent_integrity(agent_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            integrity = runtime.get_agent_integrity(identity, agent_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if integrity is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
        return integrity

    @app.post("/api/v1/agents/{agent_id}/enforce")
    def enforce_agent(agent_id: str, request: AgentEnforceRequest, identity: dict = Depends(principal)) -> dict:
        try:
            result = runtime.enforce_agent(identity, agent_id, request.action)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
        return {"agent": result}

    @app.get("/api/v1/operator/database")
    def database_inspection(identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.database_inspection(identity)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/diagnostics")
    def diagnostics(identity: dict = Depends(principal)) -> dict:
        return runtime.diagnostics(identity)

    @app.post("/api/v1/missions", status_code=status.HTTP_202_ACCEPTED)
    def create_mission(submission: MissionSubmission, identity: dict = Depends(principal)) -> dict:
        try:
            return public_mission(runtime.enqueue_mission(identity, submission))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}")
    def get_mission(mission_id: str, include_result: bool = False, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id, include_result=include_result)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if mission is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        return public_mission(mission)

    @app.get("/api/v1/missions/{mission_id}/evidence")
    def get_evidence(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id)
            if mission is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
            return {"mission_id": mission_id, "evidence": runtime.mission_evidence(identity, mission_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}/events")
    def get_events(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id)
            if mission is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
            return {"mission_id": mission_id, "events": runtime.mission_events(identity, mission_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}/checkpoints")
    def checkpoints(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id)
            if mission is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
            return {"mission_id": mission_id, "checkpoints": runtime.mission_checkpoints(identity, mission_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/missions/{mission_id}/control/{control}")
    def control_mission(mission_id: str, control: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.control_mission(identity, mission_id, control)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if mission is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        return public_mission(mission)

    @app.post("/api/v1/missions/{mission_id}/recover")
    def recover(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            result = runtime.recover(identity, mission_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        result["mission"] = public_mission(result["mission"])
        return result

    @app.post("/api/v1/missions/{mission_id}/continue")
    def continue_mission(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            result = runtime.continue_mission(identity, mission_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        result["mission"] = public_mission(result["mission"])
        return result

    # ---- Workflow Engine -------------------------------------------------------

    @app.post("/api/v1/workflows", status_code=status.HTTP_201_CREATED)
    def create_workflow(request: WorkflowCreateRequest, identity: dict = Depends(principal)) -> dict:
        try:
            return {"workflow": runtime.create_workflow(identity, request)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/plan")
    def plan_workflow(request: WorkflowPlanRequest, identity: dict = Depends(principal)) -> dict:
        """Generate a validated workflow plan from a high-level objective.

        The planner classifies the objective, matches available agents via
        capability resolution, generates a dependency-aware task graph, validates
        ordering and agent availability, and returns the plan for review before
        execution.
        """
        try:
            result = runtime.plan_workflow(identity, request)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"plan": result}

    @app.get("/api/v1/workflows/{workflow_id}")
    def get_workflow(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            workflow = runtime.get_workflow(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if workflow is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")
        return {"workflow": workflow}

    @app.get("/api/v1/workflows")
    def list_workflows(project_id: str | None = None, limit: int = Query(default=50, ge=1, le=200), identity: dict = Depends(principal)) -> dict:
        try:
            return {"workflows": runtime.list_workflows(identity, project_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/start")
    def start_workflow(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.start_workflow(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/pause")
    def pause_workflow(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            if runtime.pause_workflow(identity, workflow_id):
                return {"workflow_id": workflow_id, "status": "PAUSED"}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found")

    @app.post("/api/v1/workflows/{workflow_id}/resume")
    def resume_workflow(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.resume_workflow(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/cancel")
    def cancel_workflow(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.cancel_workflow(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/workflows/{workflow_id}/tasks")
    def get_workflow_tasks(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return {"tasks": runtime.list_workflow_tasks(identity, workflow_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/workflows/{workflow_id}/artifacts")
    def get_workflow_artifacts(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return {"artifacts": runtime.list_workflow_artifacts(identity, workflow_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/workflows/{workflow_id}/events")
    def get_workflow_events(workflow_id: str, limit: int = Query(default=100, ge=1, le=500), identity: dict = Depends(principal)) -> dict:
        try:
            return {"events": runtime.list_workflow_events(identity, workflow_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str, identity: dict = Depends(principal)) -> dict:
        artifact = runtime.get_workflow_artifact(identity, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
        return {"artifact": artifact}

    @app.get("/api/v1/artifacts/{artifact_id}/lineage")
    def get_artifact_lineage(artifact_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.get_artifact_lineage(identity, artifact_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/workflows/{workflow_id}/state")
    def get_workflow_state(workflow_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.get_workflow_state(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/workflows/{workflow_id}/messages")
    def get_workflow_messages(
        workflow_id: str,
        message_type: str | None = None,
        task_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return {"messages": runtime.list_workflow_messages(
                identity, workflow_id, message_type=message_type, task_id=task_id, limit=limit
            )}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/messages", status_code=status.HTTP_201_CREATED)
    def post_workflow_message(
        workflow_id: str,
        request: WorkflowMessageRequest,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.send_workflow_message(
                identity, workflow_id,
                message_type=request.message_type,
                content=request.content,
                to_agent_id=request.to_agent_id,
                task_id=request.task_id,
                correlation_id=request.correlation_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/recover")
    def recover_stuck_tasks(
        workflow_id: str,
        stale_seconds: int = Query(default=30, ge=1, le=3600),
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.recover_stuck_tasks(identity, workflow_id, stale_seconds)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/step")
    def step_workflow(
        workflow_id: str,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.run_workflow_once(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/run-autonomous")
    def run_autonomous(
        request: AutonomousRunRequest,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.run_autonomous(
                identity, request.objective, request.scope,
                template_type=request.template_type, constraints=request.constraints,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/run")
    def run_workflow(
        workflow_id: str,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.run_workflow(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/v1/workflows/{workflow_id}/add-task")
    def add_dynamic_task(
        workflow_id: str,
        request: DynamicTaskRequest,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.add_dynamic_task(identity, workflow_id, request.model_dump())
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/workflows/{workflow_id}/trace")
    def get_execution_trace(
        workflow_id: str,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.get_execution_trace(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/approvals")
    def list_approvals(
        workflow_id: str | None = None,
        identity: dict = Depends(principal),
    ) -> list[dict]:
        try:
            return runtime.list_approvals(identity, workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/approvals/{approval_id}/decide")
    def decide_approval(
        approval_id: str,
        request: ApprovalDecisionRequest,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.decide_approval(identity, approval_id, request.decision, request.note)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/workers")
    def list_workers(identity: dict = Depends(principal)) -> dict:
        try:
            return {"workers": runtime.list_worker_heartbeats(identity)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/agents/registry/describe")
    def describe_agents(identity: dict = Depends(principal)) -> dict:
        try:
            engine = runtime._workflow_engine()
            registry = getattr(engine, "_agent_registry", None)
            if registry is None or not hasattr(registry, "describe_agents"):
                return {"agents": []}
            return {"agents": registry.describe_agents()}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/tools")
    def describe_tools(identity: dict = Depends(principal)) -> dict:
        try:
            from runtime.tools import describe_tools as _describe_tools
            return {"tools": _describe_tools()}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    @app.get("/api/v1/models/status")
    def model_status(identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.model_status()
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/approvals/{approval_id}")
    def get_approval(
        approval_id: str,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            approval = runtime.database.get_approval(identity.get("tenant_id", "default"), approval_id)
            if approval is None:
                raise ValueError("approval not found")
            return approval
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/v1/agents/{agent_id}/memory")
    def get_agent_memory(
        agent_id: str,
        scope: str | None = None,
        workflow_id: str | None = None,
        identity: dict = Depends(principal),
    ) -> list[dict]:
        try:
            return runtime.get_agent_memory(identity, agent_id, scope=scope, workflow_id=workflow_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/agents/{agent_id}/memory")
    def store_agent_memory(
        agent_id: str,
        request: AgentMemoryRequest,
        identity: dict = Depends(principal),
    ) -> dict:
        try:
            return runtime.store_agent_memory(
                identity, agent_id, request.scope, request.source, request.content,
                workflow_id=request.workflow_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    return app


app = create_app()
