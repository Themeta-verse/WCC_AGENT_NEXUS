"""WCC Demo API Routes — Demo mode endpoints for WCC Launchpad 30."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import asyncio
import uuid
from datetime import datetime

router = APIRouter()

# In-memory demo session storage (for demo purposes)
demo_sessions = {}

class DemoRunRequest(BaseModel):
    objective: str
    workspace: Optional[str] = None
    demoMode: bool = True

class ApprovalDecision(BaseModel):
    decision: str  # "APPROVE" or "REJECT"

# In-memory demo session storage
demo_sessions = {}

class DemoRunRequest(BaseModel):
    objective: str
    workspace: Optional[str] = None
    demoMode: bool = True

class ApprovalDecision(BaseModel):
    decision: str  # "APPROVE" or "REJECT"

@router.post("/run")
async def run_demo(request: DemoRunRequest):
    """Start a demo workflow."""
    if not request.objective:
        raise HTTPException(status_code=400, detail="Objective is required")

    session_id = f"demo-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    
    demo_sessions[session_id] = {
        "sessionId": session_id,
        "status": "started",
        "stage": "planning",
        "objective": request.objective,
        "workspace": request.workspace or "/tmp/nexus_wcc_demo",
        "demoMode": True,
        "progress": 0,
        "result": None,
        "needsApproval": False,
        "approval": None,
        "approvalStatus": None,
        "createdAt": datetime.now().isoformat(),
    }

    # Start async workflow
    asyncio.create_task(run_demo_workflow(session_id, request.objective, request.workspace or "/tmp/nexus_wcc_demo", True))

    return {"sessionId": session_id, "status": "started"}

@router.get("/status/{sessionId}")
async def get_demo_status(sessionId: str):
    session = demo_sessions.get(sessionId)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

@router.post("/approve/{sessionId}")
async def approve_demo(sessionId: str, decision: ApprovalDecision):
    session = demo_sessions.get(sessionId)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if decision.decision == "APPROVE":
        session.approvalStatus = "APPROVED"
    elif decision.decision == "REJECT":
        session.approvalStatus = "REJECTED"
    else:
        raise HTTPException(status_code=400, detail="Decision must be APPROVE or REJECT")
    
    return {"success": True, "approvalStatus": session.approvalStatus}

async def run_demo_workflow(session_id: str, objective: str, workspace: str, demoMode: bool):
    session = demo_sessions.get(session_id)
    if not session:
        return

    try:
        stages = ["planning", "proposal", "policy", "approval", "execution", "evidence", "verification"]
        
        for i, stage in enumerate(stages):
            session["stage"] = stage
            session["progress"] = round(((i + 1) / len(stages)) * 100)
            
            # Simulate work
            await asyncio.sleep(0.8)
            
            # At approval stage, wait for human decision
            if stage == "approval":
                session["needsApproval"] = True
                session["approval"] = {
                    "operation": "filesystem.file.write",
                    "target": "calculator.py",
                    "reason": "Creating new file with calculator implementation requires human approval"
                }
                
                # Wait for approval (polling-based)
                # For demo, we'll auto-approve after a short delay
                for _ in range(20):  # Wait up to 20 seconds
                    await asyncio.sleep(1)
                    if session.get("approvalStatus") in ["APPROVED", "REJECTED"]:
                        break
        
        # Final result
        session["status"] = "completed"
        session["stage"] = "verification"
        session["progress"] = 100
        session["result"] = {
            "status": "VERIFIED",
            "verified": True,
            "tasks": 4,
            "artifacts": 4,
            "verification": [
                { "artifact_id": "calculator.py", "status": "VERIFIED", "reality": "VERIFIED" },
                { "artifact_id": "test_calculator.py", "status": "VERIFIED", "reality": "VERIFIED" },
            ],
            "evidence": {
                "filesCreated": 2,
                "commandsRun": 3,
                "testsRun": 4,
                "testsPassed": 4,
                "receipts": 4,
                "verification": "PASS"
            }
        }
    except Exception as error:
        demo_sessions[session_id]["status"] = "failed"
        demo_sessions[session_id]["error"] = str(error)

@router.get("/sessions")
async def list_sessions():
    return {"sessions": list(demo_sessions.values())}

@router.delete("/sessions/{sessionId}")
async def delete_session(sessionId: str):
    if sessionId in demo_sessions:
        del demo_sessions[sessionId]
        return {"success": True}
    raise HTTPException(status_code=404, detail="Session not found")