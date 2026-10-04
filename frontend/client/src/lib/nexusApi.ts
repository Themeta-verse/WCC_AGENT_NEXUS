/* Meridian command-center API boundary: authenticated product sessions, tenant-scoped data, and durable queue status only. */
export type Capability = "repository.metadata.read" | "repository.read" | "browser.read" | "filesystem.read";

export type Project = {
  project_id: string;
  display_name: string;
  role: "owner" | "operator" | "viewer";
  created_at?: string;
  updated_at?: string;
};

export type ProductUser = {
  user_id: string;
  tenant_id: string;
  email: string;
  role: "owner" | "operator" | "viewer";
};

export type ProductSession = {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: ProductUser;
  projects: Project[];
};

export type MissionQueue = {
  status: "QUEUED" | "LEASED" | "COMPLETED" | "FAILED" | string | null;
  attempts?: number | null;
  max_attempts?: number | null;
  available_at?: string | null;
  lease_expires_at?: string | null;
  last_error?: string | null;
};

export type MissionSummary = {
  mission_id: string;
  project_id: string;
  status: string;
  reality: string;
  verification_status: string;
  action_state: string;
  external_invocations: number;
  queue?: MissionQueue;
  result?: Record<string, unknown> | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type ProductHealth = {
  status: string;
  service: string;
  database: { status: string; missions: number; projects: number; users?: number; path?: string; queue?: Record<string, number> };
  providers: Record<string, { status: string; availability?: boolean; limitations?: string[]; authentication?: string }>;
  real_reads_enabled: boolean;
  authorization_boundary: string;
  authentication?: { mode: string; bootstrap_owner_configured: boolean; session_hours: number };
  queue?: { worker_command: string; lease_seconds: number; max_attempts: number };
  github?: { transport: string; authentication: string };
  runtime_state?: { state: string; reason: string; configured_database_url?: string; database_engine?: string; database_portability?: string };
  initial_owner_setup_available?: boolean;
  owner_registration_available?: boolean;
};

export type MemoryItem = { memory_id: string; mission_id?: string; source: string; confidence: string; freshness_at: string; reality_state: string; status: string; user_note?: string | null; retired_at?: string | null; content: Record<string, unknown> };
export type Outcome = { outcome_id: string; mission_id: string; state: string; reality_state: string; verification_state: string; updated_at: string; summary: Record<string, unknown> };
export type AuditEvent = { audit_id: number; action: string; outcome: string; mission_id?: string; created_at: string; detail: Record<string, unknown> };
export type ProviderState = { identity?: string; status: string; availability?: boolean; limitations?: string[]; authentication?: string; authorization?: string; risk?: string; side_effects?: boolean; execution_state?: string; last_execution?: string | null; last_successful_execution?: string | null; last_failure_state?: string | null; last_verification_state?: string };
export type MissionEvent = { event_id: number; event_type: string; payload: Record<string, unknown>; created_at: string };
export type MissionEvidence = { evidence_id: number; capability?: string; provider?: string; observation_id?: string; verification_state?: string; reality?: string; created_at: string };
export type DatabaseInspection = { database: "sqlite"; tenant_id: string; row_counts: Record<string, number>; integrity_check: string; foreign_keys: boolean; journal_mode: string };
export type ProjectContext = { project_id: string; current_objective: string | null; latest_mission: MissionSummary | null; active_missions: MissionSummary[]; blockers: Array<{ mission_id: string; status: string; error?: string | null }>; discovered: Array<{ memory_id: string; source: string; reality_state: string; verification_state: string; status: string; user_note?: string | null }>; outcomes: Outcome[]; next_action: string; continuity: { memory_count: number; mission_count: number; active_count: number; blocker_count: number } };

export type Agent = {
  agent_id: string;
  tenant_id: string;
  project_id: string;
  display_name: string;
  status: "ACTIVE" | "FLAGGED" | "HALTED" | "RETIRED";
  created_at: string;
  updated_at: string;
};

export type AgentPolicy = {
  policy_id: string;
  agent_id: string;
  tenant_id: string;
  declared_capabilities: string[];
  allowed_operations: string[];
  prohibited_operations: string[];
  scope: Record<string, unknown>;
  expected_behaviour: string;
  version: number;
  created_at: string;
};

export type AgentAction = {
  action_id: string;
  agent_id: string;
  tenant_id: string;
  project_id: string;
  operation: string;
  target_resource: string | null;
  requested_capability: string | null;
  observed_parameters_json: string;
  observation_reality: "OBSERVED" | "MANUAL";
  receipt_json: string | null;
  integrity_decision: "ALLOW" | "FLAG" | "HALT";
  integrity_reason: string | null;
  evidence_json: string | null;
  policy_version: number | null;
  evaluated_at: string;
  created_at: string;
};

export type AgentIntegrityEvent = {
  event_id: string;
  agent_id: string;
  tenant_id: string;
  event_type: string;
  payload_json: string;
  integrity_decision: string | null;
  created_at: string;
};

export type AgentIntegrityResponse = {
  agent: Agent;
  policy: AgentPolicy | null;
  recent_actions: AgentAction[];
  integrity_events: AgentIntegrityEvent[];
};



const configuredBase = import.meta.env.VITE_NEXUS_API_BASE_URL?.replace(/\/$/, "");
const runtimeBaseStorageKey = "nexus.product.api-base.v1";
export const apiBase = configuredBase || "";
const sessionStorageKey = "nexus.product.session.v1";

export function getApiBase(): string {
  if (typeof window !== "undefined") {
    const override = window.localStorage.getItem(runtimeBaseStorageKey)?.trim().replace(/\/$/, "");
    if (override) return override;
  }
  return configuredBase || "";
}

export function configureApiBase(value: string): string {
  const normalized = value.trim().replace(/\/$/, "");
  if (!/^https?:\/\//.test(normalized)) throw new Error("Enter a complete runtime URL beginning with http:// or https://");
  window.localStorage.setItem(runtimeBaseStorageKey, normalized);
  return normalized;
}

export function hasApiBaseOverride(): boolean {
  if (typeof window === "undefined") return false;
  return Boolean(window.localStorage.getItem(runtimeBaseStorageKey)?.trim());
}

export function clearApiBaseOverride(): void {
  window.localStorage.removeItem(runtimeBaseStorageKey);
}

export function readProductSession(): ProductSession | null {
  try {
    const raw = window.sessionStorage.getItem(sessionStorageKey);
    return raw ? (JSON.parse(raw) as ProductSession) : null;
  } catch {
    return null;
  }
}

export function clearProductSession(): void {
  window.sessionStorage.removeItem(sessionStorageKey);
}

export function persistProductSession(session: ProductSession): ProductSession {
  window.sessionStorage.setItem(sessionStorageKey, JSON.stringify(session));
  return session;
}

async function request<T>(path: string, init?: RequestInit, authenticated = false): Promise<T> {
  const base = getApiBase();
  if (!base) throw new Error("NEXUS runtime is not connected. Add the URL of your independently running API before signing in.");
  const session = authenticated ? readProductSession() : null;
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string> || {}) };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  const response = await fetch(`${base}${path}`, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && authenticated) clearProductSession();
  if (!response.ok) throw new Error(payload.detail || `API request failed (${response.status})`);
  return payload as T;
}

export const nexusApi = {
  health: () => request<ProductHealth>("/health"),
  authenticatedHealth: () => request<ProductHealth>("/api/v1/health", undefined, true),
  setupOwner: async (email: string, password: string) => persistProductSession(await request<ProductSession>("/api/v1/setup/owner", { method: "POST", body: JSON.stringify({ email, password }) })),
  registerOwner: async (email: string, password: string) => persistProductSession(await request<ProductSession>("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ email, password }) })),
  login: async (email: string, password: string) => persistProductSession(await request<ProductSession>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) })),
  logout: async () => {
    try {
      await request<void>("/api/v1/auth/logout", { method: "POST" }, true);
    } finally {
      clearProductSession();
    }
  },
  me: () => request<{ user: ProductUser; projects: Project[] }>("/api/v1/me", undefined, true),
  listProjects: () => request<{ projects: Project[] }>("/api/v1/projects", undefined, true),
  createProject: (projectId: string, displayName: string) => request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ project_id: projectId, display_name: displayName }) }, true),
  listMissions: (projectId: string) => request<{ project_id: string; missions: MissionSummary[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/missions`, undefined, true),
  listMemory: (projectId: string) => request<{ project_id: string; memory: MemoryItem[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/memory`, undefined, true),
  updateMemory: (projectId: string, memoryId: string, action: "retire" | "restore" | "annotate", note?: string) => request<{ memory: MemoryItem }>(`/api/v1/projects/${encodeURIComponent(projectId)}/memory/${encodeURIComponent(memoryId)}`, { method: "POST", body: JSON.stringify({ action, note }) }, true),
  projectContext: (projectId: string) => request<ProjectContext>(`/api/v1/projects/${encodeURIComponent(projectId)}/context`, undefined, true),
  listOutcomes: (projectId: string) => request<{ project_id: string; outcomes: Outcome[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/outcomes`, undefined, true),
  listAuditEvents: (projectId?: string) => request<{ audit_events: AuditEvent[] }>(`/api/v1/audit-events${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`, undefined, true),
  listCapabilities: () => request<{ capabilities: Array<{ capability: string; provider: string; risk: string; side_effects: boolean }> }>("/api/v1/capabilities", undefined, true),
  listProviders: () => request<{ providers: Record<string, ProviderState> }>("/api/v1/providers", undefined, true),
  databaseInspection: () => request<DatabaseInspection>("/api/v1/operator/database", undefined, true),
  diagnostics: () => request<ProductHealth>("/api/v1/diagnostics", undefined, true),
  missionEvents: (missionId: string) => request<{ mission_id: string; events: MissionEvent[] }>(`/api/v1/missions/${encodeURIComponent(missionId)}/events`, undefined, true),
  missionEvidence: (missionId: string) => request<{ mission_id: string; evidence: MissionEvidence[] }>(`/api/v1/missions/${encodeURIComponent(missionId)}/evidence`, undefined, true),
  listCheckpoints: (missionId: string) => request<{ mission_id: string; checkpoints: Array<{ checkpoint_id: string; state: string; created_at: string }> }>(`/api/v1/missions/${encodeURIComponent(missionId)}/checkpoints`, undefined, true),
  controlMission: (missionId: string, control: "pause" | "resume" | "cancel") => request<MissionSummary>(`/api/v1/missions/${encodeURIComponent(missionId)}/control/${control}`, { method: "POST" }, true),
  continueMission: (missionId: string) => request<{ mission: MissionSummary; recovery: Record<string, unknown> }>(`/api/v1/missions/${encodeURIComponent(missionId)}/continue`, { method: "POST" }, true),
  submitMission: (payload: { intent: string; project_id: string; scope: string; mode: "REAL_READ" | "SIMULATION"; capabilities: Capability[] }) =>
    request<MissionSummary>("/api/v1/missions", { method: "POST", body: JSON.stringify(payload) }, true),

  // Agent foundation API (PS002 Phase 2A: identity + declared policy only)
  createAgent: (payload: { agent_id?: string; project_id: string; display_name: string }) => request<Agent>("/api/v1/agents", { method: "POST", body: JSON.stringify(payload) }, true),
  listAgents: (projectId?: string) => request<{ agents: Agent[] }>(`/api/v1/agents${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`, undefined, true),
  getAgent: (agentId: string) => request<{ agent: Agent }>(`/api/v1/agents/${encodeURIComponent(agentId)}`, undefined, true),
  createAgentPolicy: (agentId: string, payload: { declared_capabilities: string[]; allowed_operations: string[]; prohibited_operations: string[]; scope: Record<string, unknown>; expected_behaviour: string }) => request<AgentPolicy>(`/api/v1/agents/${encodeURIComponent(agentId)}/policy`, { method: "POST", body: JSON.stringify(payload) }, true),
  getAgentPolicy: (agentId: string) => request<{ policy: AgentPolicy }>(`/api/v1/agents/${encodeURIComponent(agentId)}/policy`, undefined, true),
  listAgentPolicies: (agentId: string) => request<{ policies: AgentPolicy[] }>(`/api/v1/agents/${encodeURIComponent(agentId)}/policies`, undefined, true),

  // PS002 Phase 2B: autonomous agent runtime integrity
  submitAgentAction: (agentId: string, payload: { operation: string; target_resource?: string; requested_capability?: string; parameters?: Record<string, unknown> }) =>
    request<{ action: AgentAction; agent: Agent; integrity_decision: string; integrity_reason: string | null; evidence: Record<string, unknown> }>(`/api/v1/agents/${encodeURIComponent(agentId)}/actions`, { method: "POST", body: JSON.stringify(payload) }, true),
  getAgentActions: (agentId: string) => request<{ actions: AgentAction[] }>(`/api/v1/agents/${encodeURIComponent(agentId)}/actions`, undefined, true),
  getAgentIntegrity: (agentId: string) => request<AgentIntegrityResponse>(`/api/v1/agents/${encodeURIComponent(agentId)}/integrity`, undefined, true),
  enforceAgent: (agentId: string, action: "ACTIVE" | "FLAGGED" | "HALTED") => request<{ agent: Agent }>(`/api/v1/agents/${encodeURIComponent(agentId)}/enforce`, { method: "POST", body: JSON.stringify({ action }) }, true),

  // PS002 Phase 2C: real runtime observation bridge
   observeAgentAction: (agentId: string, payload: { operation: string; target_resource: string; requested_capability?: string; parameters?: Record<string, unknown>; observation_root: string }) =>
    request<{ action: AgentAction; integrity_decision: string; integrity_reason: string | null; evidence: Record<string, unknown>; observation_receipt: Record<string, unknown>; reality: string }>(`/api/v1/agents/${encodeURIComponent(agentId)}/observe`, { method: "POST", body: JSON.stringify(payload) }, true),
};

// ===== NEXUS Phase 1: Workflow Engine API =====

export type WorkflowAgent = {
  id: string;
  type: "SPECIALIST" | "LLM" | "HUMAN";
  name: string;
  capabilities: string[];
  instruction: string;
};

export type WorkflowTaskSpec = {
  id: string;
  name: string;
  type: string;
  description: string;
  requires_capabilities: string[];
  input_artifacts: string[];
  parameters: Record<string, unknown>;
  depends_on: string[];
};

export type WorkflowDefinition = {
  name: string;
  description: string;
  agents: WorkflowAgent[];
  task_graph: WorkflowTaskSpec[];
};

export type Workflow = {
  workflow_id: string;
  project_id: string | null;
  name: string;
  description: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "PAUSED" | "CANCELLED" | "AWAITING_APPROVAL";
  execution_mode: string;
  created_at: string;
  updated_at: string;
};

export type WorkflowSummary = Workflow;

export type WorkflowTask = {
  id: string;
  name: string;
  description: string;
  type: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED" | "PAUSED";
  agent_id: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  retries: number;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
};

export type WorkflowArtifact = {
  artifact_id: string;
  task_id: string | null;
  workflow_id: string;
  name: string;
  artifact_type: string;
  kind?: string;
  content: Record<string, unknown>;
  provenance: string[];
  reality: string;
  verification_state: string;
  created_at: string;
  // Present on engine-trace artifacts (absent on older backends — UI guards).
  agent_id?: string | null;
  content_hash?: string | null;
  content_path?: string | null;
  consumed_by?: string[];
  parent_artifacts?: string[];
};

export type WorkflowEvent = {
  event_id: number;
  workflow_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  actor_id: string | null;
  created_at: string;
};

export type WorkflowState = {
  workflow_id: string;
  status: string;
  current_tasks: WorkflowTask[];
  completed_tasks: number;
  failed_tasks: number;
  total_tasks: number;
};

export type WorkflowMessage = {
  message_id: string;
  workflow_id: string;
  tenant_id: string;
  project_id: string | null;
  task_id: string | null;
  from_agent_id: string | null;
  to_agent_id: string | null;
  message_type: string;
  content: Record<string, unknown>;
  correlation_id: string | null;
  created_at: string;
  processed_at: string | null;
};

export type WorkflowSummaryState = {
  workflow_id?: string;
  status?: string;
  workflow?: Record<string, unknown> | null;
  summary: {
    total_tasks: number;
    completed: number;
    failed: number;
    running: number;
    pending?: number;
    ready?: number;
    blocked?: number;
    total_messages?: number;
    total_artifacts?: number;
  };
  tasks: Array<{
    task_id: string;
    task_type: string;
    name: string;
    status: string;
    agent_id: string | null;
    retry_count: number;
    max_retries: number;
    error: string | null;
    started_at: string | null;
    completed_at: string | null;
    depends_on: string[];
    required_capabilities: string[];
    output_artifacts: string[];
    input_artifacts?: string[];
  }>;
  messages: WorkflowMessage[];
  events: WorkflowEvent[];
  artifacts?: WorkflowArtifact[];
};

export type WorkerHeartbeat = {
  worker_id: string;
  status: string;
  last_heartbeat: string;
  details_json?: string;
  // Phase 9 fabric: computed liveness + lease assignment (present on new backends).
  liveness?: string;
  current_workflow_id?: string | null;
  current_task_id?: string | null;
  capabilities?: string[];
};

export type AgentDescription = {
  agent_id: string;
  name: string;
  role: string;
  agent_type: string;
  capabilities: string[];
  allowed_operations: string[];
  prohibited_operations: string[];
  input_contract: string[];
  output_contract: string[];
  tool_permissions: string[];
  artifact_behavior: string;
  message_behavior: string;
  status: string;
  lifecycle: string;
  current_task: string | null;
  execution_history: Array<Record<string, unknown>>;
};

export type ToolSpec = {
  capability: string;
  permission: string;
  scope_model: string;
  executable: boolean;
  reality: string;
  description: string;
};

export type ArtifactLineage = {
  artifact: WorkflowArtifact;
  ancestors: WorkflowArtifact[];
  consumed_by: string[];
};

export type WorkflowMessageRequest = {
  message_type: string;
  content: Record<string, unknown>;
  to_agent_id?: string | null;
  task_id?: string | null;
  correlation_id?: string | null;
};

export type StepResult = {
  workflow_id: string;
  status: string;
  total_tasks: number;
  completed: number;
  failed: number;
  running: number;
  pending: number;
  ready: number;
  blocked: number;
  by_status?: Record<string, number>;
  completed_tasks?: string[];
  failed_tasks?: string[];
  running_tasks?: string[];
  artifacts_produced?: number;
  artifact_ids?: string[];
  artifacts_consumed_refs?: string[];
  messages_exchanged?: number;
  recent_message_types?: string[];
  events_emitted?: number;
  recent_event_types?: string[];
  dynamic_tasks?: number;
  dynamic_task_ids?: string[];
  progress_percent?: number;
  tasks?: WorkflowTask[];
};

export type WorkflowTrace = {
  workflow_id: string;
  objective: string;
  scope: string;
  total_tasks: number;
  status: string;
  finish_reason?: string;
  tools_used?: Record<string, { count: number; targets: string[]; receipts?: string[] }>;
  reality_breakdown?: Record<string, number>;
  observed?: string[];
  inferred?: string[];
  verified?: string[];
  provenance_chain?: Array<{
    artifact_id: string;
    kind: string;
    producer: string | null;
    task_id: string | null;
    parents: string[];
    consumed_by: string[];
    reality: string;
  }>;
  planning: {
    plan: Record<string, unknown>;
    template_type: string | null;
    validations: Array<{ check: string; passed: boolean; detail?: string }>;
  };
  execution_environment?: string;
  model?: TraceModelSection;
  tasks: Array<{
    task_id: string | null;
    task_type: string | null;
    name: string;
    status: string | null;
    agent_id: string | null;
    worker_id?: string | null;
    claim_count?: number;
    last_execution_id?: string | null;
    started_at?: string | null;
    completed_at?: string | null;
    retry_count: number;
    max_retries: number;
    error: string | null;
    depends_on: string[];
    required_capabilities: string[];
    input_artifacts?: string[];
    output_artifacts: string[];
    is_dynamic: boolean;
    parent_task_id: string | null;
    generated_reason: string | null;
  }>;
  dynamic_tasks: Array<{
    task_id: string;
    name: string;
    reason: string | null;
    parent_task_id: string | null;
    created_at: string | null;
    creator: string;
  }>;
  agents: Record<string, { agent_id: string; tasks: string[]; messages_sent?: number; messages_received?: number }>;
  messages: WorkflowMessage[];
  artifacts: WorkflowArtifact[];
  retries: Array<{
    task_id: string | null;
    retry_count: number;
    detail: Record<string, unknown>;
    timestamp: string | null;
  }>;
  approvals: Array<{
    approval_id: string;
    operation: string;
    reason: string | null;
    status: string;
    requested_by: string | null;
    created_at: string | null;
  }>;
  verification: {
    checks: Array<{ task_id: string | null; status: string | null; error: string | null }>;
    all_completed: boolean;
  };
  // Phase 9 fabric observability (absent on older backends — always optional).
  workers?: Array<{
    worker_id: string;
    status: string;
    liveness?: string;
    current_workflow_id?: string | null;
    current_task_id?: string | null;
    last_heartbeat?: string | null;
  }>;
  recovery_events?: Array<{
    event_id: string | number;
    event_type: string;
    task_id?: string | null;
    agent_id?: string | null;
    detail?: Record<string, unknown>;
    created_at?: string | null;
  }>;
  approvals_history?: Array<{
    event_type: string;
    task_id?: string | null;
    detail?: Record<string, unknown>;
    created_at?: string | null;
  }>;
  final_result: {
    status: string | null;
    completed: number;
    failed: number;
    total: number;
    dynamic_tasks: number;
    artifacts: number;
    messages: number;
  };
  all_events: WorkflowEvent[];
};

export type AutonomousRunRequest = {
  objective: string;
  scope: string;
  template_type?: string | null;
  constraints?: Record<string, unknown> | null;
};

export type DynamicTaskRequest = {
  task_type: string;
  name: string;
  agent_id?: string | null;
  required_capabilities?: string[];
  depends_on?: string[];
  input_artifacts?: string[];
  parameters?: Record<string, unknown>;
  parent_task_id?: string | null;
  reason?: string | null;
};

export type ApprovalDecisionRequest = {
  decision: string;
  note?: string | null;
};

export type ApprovalInfo = {
  approval_id: string;
  workflow_id: string;
  tenant_id: string;
  project_id: string;
  task_id: string | null;
  requested_by: string;
  operation: string;
  reason: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

// Phase 7: model runtime visibility (redacted — no secrets ever reach the UI).
export type ModelStatus = {
  execution_mode: string;
  requested_mode: string;
  provider: string;
  model: string;
  is_configured: boolean;
  fallback_provider: string;
  registered_providers: string[];
  reality_boundary: string;
  secret_exposure: string;
};

export type TraceModelSection = {
  execution_mode: string;
  invocations: number;
  providers_used: string[];
  failures: number;
  tool_requests: number;
  events: Array<{ event_id: string | number; event_type: string; task_id?: string | null; agent_id?: string | null; created_at: string; detail: Record<string, unknown> }>;
  router_status?: { execution_mode: string; provider: string; model: string; is_configured: boolean };
};

export const workflowApi = {
  listWorkflows: (projectId?: string) =>
    request<{ workflows: WorkflowSummary[] }>(`/api/v1/workflows${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`, undefined, true),

  createWorkflow: (payload: { name: string; objective: string; scope: string; project_id?: string; task_specs: Array<{ task_id: string; task_type: string; name: string; agent_id?: string | null; required_capabilities?: string[]; depends_on?: string[]; input_artifacts?: string[] }>; agents?: Array<{ agent_id: string; name: string; role: string; capabilities: string[]; allowed_operations?: string[]; prohibited_operations?: string[]; scope?: Record<string, unknown>; expected_behaviour?: string }>; execution_mode?: "REAL_READ" | "SIMULATION" }) =>
    request<{ workflow: Workflow }>("/api/v1/workflows", { method: "POST", body: JSON.stringify(payload) }, true),

  planWorkflow: (objective: string, scope: string, options?: { template_type?: string; agents?: WorkflowAgent[]; constraints?: Record<string, unknown> }) =>
    request<{ workflow: Workflow; plan: Record<string, unknown> }>("/api/v1/workflows/plan", { method: "POST", body: JSON.stringify({ objective, scope, ...options }) }, true),

  getWorkflow: (workflowId: string) =>
    request<{ workflow: Workflow }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}`, undefined, true),

  startWorkflow: (workflowId: string) =>
    request<{ workflow_id: string; status: string }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/start`, { method: "POST" }, true),

  pauseWorkflow: (workflowId: string) =>
    request<{ workflow_id: string; status: string }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/pause`, { method: "POST" }, true),

  resumeWorkflow: (workflowId: string) =>
    request<{ workflow_id: string; status: string }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/resume`, { method: "POST" }, true),

  cancelWorkflow: (workflowId: string) =>
    request<{ workflow_id: string; status: string }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/cancel`, { method: "POST" }, true),

  getWorkflowTasks: (workflowId: string) =>
    request<{ tasks: WorkflowTask[] }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/tasks`, undefined, true),

  getWorkflowArtifacts: (workflowId: string) =>
    request<{ artifacts: WorkflowArtifact[] }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/artifacts`, undefined, true),

  getWorkflowEvents: (workflowId: string, limit?: number) =>
    request<{ events: WorkflowEvent[] }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/events${limit ? `?limit=${limit}` : ""}`, undefined, true),

  getWorkflowState: (workflowId: string) =>
    request<WorkflowState>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/state`, undefined, true),

  getWorkflowFullState: (workflowId: string) =>
    request<WorkflowSummaryState>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/state`, undefined, true),

  getWorkflowMessages: (workflowId: string, params?: { message_type?: string; task_id?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.message_type) qs.set("message_type", params.message_type);
    if (params?.task_id) qs.set("task_id", params.task_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const query = qs.toString();
    return request<{ messages: WorkflowMessage[] }>(
      `/api/v1/workflows/${encodeURIComponent(workflowId)}/messages${query ? `?${query}` : ""}`, undefined, true);
  },

  sendWorkflowMessage: (workflowId: string, payload: WorkflowMessageRequest) =>
    request<{ message: WorkflowMessage }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/messages`, { method: "POST", body: JSON.stringify(payload) }, true),

  recoverStuckTasks: (workflowId: string, staleSeconds: number = 30) =>
    request<{ recovered: number; workflow_id: string }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/recover?stale_seconds=${staleSeconds}`, { method: "POST" }, true),

  stepWorkflow: (workflowId: string) =>
    request<StepResult>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/step`, { method: "POST" }, true),

  getArtifact: (artifactId: string) =>
    request<{ artifact: WorkflowArtifact }>(`/api/v1/artifacts/${encodeURIComponent(artifactId)}`, undefined, true),

  getArtifactLineage: (artifactId: string) =>
    request<ArtifactLineage>(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/lineage`, undefined, true),

  listWorkers: () =>
    request<{ workers: WorkerHeartbeat[] }>("/api/v1/workers", undefined, true),

  describeAgents: () =>
    request<{ agents: AgentDescription[] }>("/api/v1/agents/registry/describe", undefined, true),

  describeTools: () =>
    request<{ tools: ToolSpec[] }>("/api/v1/tools", undefined, true),

  runAutonomous: (payload: AutonomousRunRequest) =>
    request<{ workflow_id: string; status: string; summary: Record<string, unknown>; trace: WorkflowTrace }>(
      "/api/v1/workflows/run-autonomous", { method: "POST", body: JSON.stringify(payload) }, true
    ),

  addDynamicTask: (workflowId: string, payload: DynamicTaskRequest) =>
    request<{ task_id: string; status: string }>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/add-task`,
      { method: "POST", body: JSON.stringify(payload) }, true),

  getExecutionTrace: (workflowId: string) =>
    request<WorkflowTrace>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/trace`, undefined, true),

  listApprovals: (workflowId?: string) =>
    request<ApprovalInfo[]>(`/api/v1/approvals${workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : ""}`, undefined, true),

  getApproval: (approvalId: string) =>
    request<ApprovalInfo>(`/api/v1/approvals/${encodeURIComponent(approvalId)}`, undefined, true),

  decideApproval: (approvalId: string, payload: ApprovalDecisionRequest) =>
    request<ApprovalInfo>(`/api/v1/approvals/${encodeURIComponent(approvalId)}/decide`,
      { method: "POST", body: JSON.stringify(payload) }, true),

  modelStatus: () =>
    request<ModelStatus>("/api/v1/models/status", undefined, true),
};
