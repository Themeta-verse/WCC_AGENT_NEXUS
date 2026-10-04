/**
 * NEXUS WCC Demo API Client
 */
const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export interface DemoRunRequest {
  objective: string;
  workspace?: string;
  demoMode?: boolean;
}

export interface DemoSession {
  sessionId: string;
  status: string;
  stage: string;
  objective: string;
  workspace: string;
  demoMode: boolean;
  progress: number;
  result: any;
  needsApproval: boolean;
  approval: any;
  approvalStatus: string | null;
  createdAt: string;
}

export interface ApprovalDecision {
  decision: "APPROVE" | "REJECT";
}

export interface DemoRunResponse {
  sessionId: string;
  status: string;
}

export async function runDemo(request: DemoRunRequest): Promise<DemoRunResponse> {
  const response = await fetch(`${API_BASE}/demo/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to start demo");
  }
  
  return response.json();
}

export async function getDemoStatus(sessionId: string): Promise<any> {
  const response = await fetch(`${API_BASE}/demo/status/${sessionId}`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to get demo status");
  }
  
  return response.json();
}

export async function approveDemo(sessionId: string, decision: "APPROVE" | "REJECT"): Promise<any> {
  const response = await fetch(`${API_BASE}/demo/approve/${sessionId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to submit approval");
  }
  
  return response.json();
}

export async function listDemoSessions(): Promise<{ sessions: any[] }> {
  const response = await fetch(`${API_BASE}/demo/sessions`);
  return response.json();
}

export async function deleteDemoSession(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/demo/sessions/${sessionId}`, {
    method: "DELETE",
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to delete session");
  }
}