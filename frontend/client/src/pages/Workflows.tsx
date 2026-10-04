/** NEXUS Phase 1 — Workflow Engine command desk. */
import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Clock3,
  Copy,
  ExternalLink,
  FileText,
  GitBranch,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Send,
  Square,
  Trash2,
  Workflow as WorkflowIcon,
  Zap,
} from "lucide-react";
import {
  Workflow,
  WorkflowArtifact,
  WorkflowEvent,
  WorkflowMessage,
   WorkflowSummaryState,
   WorkflowTrace,
   ApprovalInfo,
   AutonomousRunRequest,
   WorkerHeartbeat,
   AgentDescription,
   ToolSpec,
   ModelStatus,
   workflowApi,
} from "@/lib/nexusApi";

const statusColors: Record<string, string> = {
  PENDING: "is-attention",
  RUNNING: "is-good",
  COMPLETED: "is-good",
  FAILED: "is-blocked",
  PAUSED: "is-attention",
  CANCELLED: "is-blocked",
};

const taskStatusColors: Record<string, string> = {
  PENDING: "is-attention",
  READY: "is-info",
  RUNNING: "is-good",
  COMPLETED: "is-good",
  FAILED: "is-blocked",
  SKIPPED: "",
  PAUSED: "is-attention",
  BLOCKED: "is-blocked",
  CANCELLED: "is-blocked",
  AWAITING_APPROVAL: "is-awaiting",
};

function StatusMark({ state }: { state: string }) {
  const tone = statusColors[state] || "is-attention";
  return <span className={`status-mark ${tone}`} aria-hidden="true" />;
}

function TaskStatusMark({ state }: { state: string }) {
  const tone = taskStatusColors[state] || "is-attention";
  return <span className={`status-mark ${tone}`} aria-hidden="true" />;
}

function WorkflowCard({ workflow, onRefresh }: { workflow: Workflow; onRefresh: () => void }) {
  const [stateTasks, setStateTasks] = useState<WorkflowSummaryState["tasks"]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState<"tasks" | "messages" | "artifacts" | "events" | "autonomous">("tasks");
  const [fullState, setFullState] = useState<WorkflowSummaryState | null>(null);
  const [messages, setMessages] = useState<WorkflowMessage[]>([]);
  const [artifacts, setArtifacts] = useState<WorkflowArtifact[]>([]);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [trace, setTrace] = useState<WorkflowTrace | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ApprovalInfo[]>([]);
  const [stepping, setStepping] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [runningAutonomous, setRunningAutonomous] = useState(false);
  const [autonomousObjective, setAutonomousObjective] = useState("");

  const loadFullState = useCallback(async (silent = false) => {
    if (!expanded) return;
    if (!silent) setLoading(true);
    try {
      const state = await workflowApi.getWorkflowFullState(workflow.workflow_id);
      setFullState(state);
      setStateTasks(state.tasks || []);
      if (state.artifacts) setArtifacts(state.artifacts);
    } catch (e) {
      console.error(e);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [expanded, workflow.workflow_id]);

  const loadMessages = useCallback(async () => {
    try {
      const payload = await workflowApi.getWorkflowMessages(workflow.workflow_id);
      setMessages(payload.messages);
    } catch (e) {
      console.error(e);
    }
  }, [workflow.workflow_id]);

  const loadArtifacts = useCallback(async () => {
    try {
      const payload = await workflowApi.getWorkflowArtifacts(workflow.workflow_id);
      setArtifacts(payload.artifacts);
    } catch (e) {
      console.error(e);
    }
  }, [workflow.workflow_id]);

  const loadEvents = useCallback(async () => {
    try {
      const payload = await workflowApi.getWorkflowEvents(workflow.workflow_id, 50);
      setEvents(payload.events);
    } catch (e) {
      console.error(e);
    }
  }, [workflow.workflow_id]);

  const loadTrace = useCallback(async () => {
    try {
      const tr = await workflowApi.getExecutionTrace(workflow.workflow_id);
      setTrace(tr);
    } catch (e) {
      console.error(e);
    }
  }, [workflow.workflow_id]);

  const loadApprovals = useCallback(async () => {
    try {
      const list = await workflowApi.listApprovals(workflow.workflow_id);
      setApprovals(Array.isArray(list) ? list : (list as unknown as { approvals: ApprovalInfo[] }).approvals || []);
    } catch (e) {
      console.error(e);
    }
  }, [workflow.workflow_id]);

  const decideApproval = useCallback(async (approvalId: string, decision: "APPROVED" | "REJECTED") => {
    try {
      await workflowApi.decideApproval(approvalId, { decision });
      await loadApprovals();
      await loadTrace();
      await onRefresh();
    } catch (e) {
      console.error(e);
    }
  }, [loadApprovals, loadTrace, onRefresh]);

  const loadTabContent = useCallback(() => {
    if (activeTab === "messages") void loadMessages();
    if (activeTab === "artifacts") void loadArtifacts();
    if (activeTab === "events") void loadEvents();
    if (activeTab === "autonomous") { void loadTrace(); void loadApprovals(); }
  }, [activeTab, loadMessages, loadArtifacts, loadEvents, loadTrace, loadApprovals]);

  useEffect(() => { void loadFullState(); }, [loadFullState]);

  // Live control-center polling: while the workflow RUNs in the background
  // worker, refresh persisted state so closing/reopening the UI recovers it.
  useEffect(() => {
    if (!expanded || workflow.status !== "RUNNING") return;
    const timer = setInterval(() => { void loadFullState(true); }, 3000);
    return () => clearInterval(timer);
  }, [expanded, workflow.status, loadFullState]);

  useEffect(() => {
    void loadTabContent();
  }, [loadTabContent]);

  const toggleStart = async () => {
    setLoading(true);
    try {
      if (workflow.status === "PENDING" || workflow.status === "PAUSED") {
        await workflowApi.startWorkflow(workflow.workflow_id);
      } else if (workflow.status === "RUNNING") {
        await workflowApi.pauseWorkflow(workflow.workflow_id);
      }
    } catch (error) {
      console.error(error);
    } finally {
      setLoading(false);
      await onRefresh();
    }
  };

  const toggleCancel = async () => {
    setLoading(true);
    try {
      await workflowApi.cancelWorkflow(workflow.workflow_id);
    } catch (error) {
      console.error(error);
    } finally {
      setLoading(false);
      await onRefresh();
    }
  };

  const handleStep = async () => {
    setStepping(true);
    try {
      await workflowApi.stepWorkflow(workflow.workflow_id);
      await onRefresh();
      await loadFullState();
    } catch (error) {
      console.error(error);
    } finally {
      setStepping(false);
    }
  };

  const handleRecover = async () => {
    setRecovering(true);
    try {
      await workflowApi.recoverStuckTasks(workflow.workflow_id);
      await loadFullState();
    } catch (error) {
      console.error(error);
    } finally {
      setRecovering(false);
    }
  };

  const liveTasks = stateTasks.length > 0 ? stateTasks : [];
  const completedTasks = liveTasks.filter((t) => t.status === "COMPLETED").length;
  const failedTasks = liveTasks.filter((t) => t.status === "FAILED").length;
  const runningTasks = liveTasks.filter((t) => t.status === "RUNNING" || t.status === "READY").length;
  const dynamicCount = liveTasks.filter((t) => (t as { is_dynamic?: boolean }).is_dynamic).length;
  const stuckTasks = liveTasks.filter((t) => t.status === "RUNNING" && t.started_at && Date.now() - new Date(t.started_at).getTime() > 60000).length;
  const hasStuck = stuckTasks > 0;

  return (
    <article className="workflow-card">
      <div className="workflow-card-top">
        <div className="workflow-title">
          <WorkflowIcon size={18} />
          <h3>{workflow.name}</h3>
        </div>
        <span className="state-chip"><StatusMark state={workflow.status} />{workflow.status}</span>
        {workflow.execution_mode === "autonomous" && <span className="state-chip is-autonomous">Autonomous</span>}
      </div>

      <p className="workflow-description">{workflow.description}</p>

      <div className="workflow-meta">
        <span className="mono-label">ID: {workflow.workflow_id.slice(0, 16)}</span>
        <span className="mono-label">Created: {new Date(workflow.created_at).toLocaleString()}</span>
        {expanded && liveTasks.length > 0 && (
          <>
            <span className="mono-label">Tasks: {completedTasks}/{liveTasks.length} done</span>
            {failedTasks > 0 && <span className="mono-label is-blocked">{failedTasks} failed</span>}
            {runningTasks > 0 && <span className="mono-label is-good">{runningTasks} active</span>}
            {dynamicCount > 0 && <span className="mono-label">+{dynamicCount} dynamic</span>}
          </>
        )}
        {expanded && fullState && (
          <span className="mono-label">Messages: {fullState.summary?.total_messages || 0}</span>
        )}
      </div>

      {expanded && (
        <>
          <div className="workflow-tabs">
            <button className={`tab ${activeTab === "tasks" ? "is-active" : ""}`} onClick={() => setActiveTab("tasks")}>Tasks</button>
            <button className={`tab ${activeTab === "messages" ? "is-active" : ""}`} onClick={() => setActiveTab("messages")}>Messages</button>
            <button className={`tab ${activeTab === "artifacts" ? "is-active" : ""}`} onClick={() => setActiveTab("artifacts")}>Artifacts</button>
            <button className={`tab ${activeTab === "events" ? "is-active" : ""}`} onClick={() => setActiveTab("events")}>Events</button>
            <button className={`tab ${activeTab === "autonomous" ? "is-active" : ""}`} onClick={() => setActiveTab("autonomous")}>Autonomous</button>
          </div>

          <div className="tab-content">
            {activeTab === "tasks" && (
              liveTasks.length > 0
                ? liveTasks.map((task) => {
                  const detail = (trace?.tasks || []).find((t) => t.task_id === task.task_id);
                  const produced = (trace?.artifacts || []).filter((a) => a.task_id === task.task_id);
                  const related = (messages.length > 0 ? messages : (trace?.messages || [])).filter(
                    (m) => m.task_id === task.task_id || (task.agent_id && m.to_agent_id === task.agent_id));
                  const modeEvent = (trace?.model?.events || []).find(
                    (e) => e.event_type === "execution_strategy" && e.task_id === task.task_id);
                  const mode = (modeEvent?.detail as { effective?: string } | undefined)?.effective
                    || trace?.model?.execution_mode || "—";
                  const open = selectedTaskId === task.task_id;
                  return (
                    <div key={task.task_id}>
                      <div className="workflow-task-row" role="button" tabIndex={0}
                        onClick={() => { setSelectedTaskId(open ? null : task.task_id); if (!open && !trace) void loadTrace(); }}
                        onKeyDown={(e) => { if (e.key === "Enter") { setSelectedTaskId(open ? null : task.task_id); if (!open && !trace) void loadTrace(); } }}>
                        <span className="mono-label">{task.name}</span>
                        <span className="task-type">{task.task_type}</span>
                        <span className="state-chip"><TaskStatusMark state={task.status} />{task.status}</span>
                        {task.agent_id && <span className="mono-label">Agent: {task.agent_id.slice(0, 18)}</span>}
                        {(task.depends_on?.length ?? 0) > 0 && <span className="mono-label">← {task.depends_on.join(", ").slice(0, 40)}</span>}
                        {(task.input_artifacts?.length ?? 0) > 0 && <span className="mono-label">in: {(task.input_artifacts ?? []).join(", ").slice(0, 40)}</span>}
                        {(task.output_artifacts?.length ?? 0) > 0 && <span className="mono-label">out: {(task.output_artifacts ?? []).join(", ").slice(0, 40)}</span>}
                        {task.retry_count > 0 && <span className="mono-label">Retries: {task.retry_count}</span>}
                        {task.error && <span className="mono-label is-blocked">Error: {task.error.slice(0, 60)}</span>}
                      </div>
                      {open && (
                        <div className="workflow-task-detail">
                          <span className="mono-label">Agent: {task.agent_id || detail?.agent_id || "unassigned"}</span>
                          <span className="mono-label">Status: {task.status}</span>
                          <span className="mono-label">Worker: {detail?.worker_id || "— (engine-driven)"}</span>
                          <span className="mono-label">Attempts: {detail?.claim_count ?? "—"}</span>
                          <span className="mono-label">Execution: {detail?.last_execution_id || "—"}</span>
                          <span className="mono-label">Mode: {mode}</span>
                          <span className="mono-label">Inputs: {(detail?.input_artifacts || task.input_artifacts || []).join(", ") || "—"}</span>
                          <span className="mono-label">Outputs: {(detail?.output_artifacts || task.output_artifacts || []).join(", ") || "—"}</span>
                          <span className="mono-label">Artifacts: {produced.length > 0 ? produced.map((a) => `${a.name} [${a.reality}]`).join(", ") : "—"}</span>
                          <span className="mono-label">Messages: {related.length}</span>
                          <span className="mono-label">Started: {task.started_at ? new Date(task.started_at).toLocaleString() : "—"}</span>
                          <span className="mono-label">Completed: {task.completed_at ? new Date(task.completed_at).toLocaleString() : "—"}</span>
                          <span className="mono-label">Retries: {task.retry_count}</span>
                        </div>
                      )}
                    </div>
                  );
                })
                : <p className="empty-ledger">No tasks available.</p>
            )}
            {activeTab === "messages" && (
              messages.length > 0
                ? messages.map((msg) => (
                  <div key={msg.message_id} className="workflow-message-row">
                    <span className="mono-label">[{msg.message_type}]</span>
                    <span className="mono-label">From: {msg.from_agent_id || "system"}</span>
                    <span className="mono-label">To: {msg.to_agent_id || "all"}</span>
                    <span className="state-chip">{msg.processed_at ? "READ" : "UNREAD"}</span>
                    <span className="mono-label">{new Date(msg.created_at).toLocaleTimeString()}</span>
                  </div>
                ))
                : <p className="empty-ledger">No messages yet.</p>
            )}
            {activeTab === "artifacts" && (
              artifacts.length > 0
                ? artifacts.map((art) => {
                  const chain = (trace?.provenance_chain || []).find((p) => p.artifact_id === art.artifact_id);
                  const enriched = (trace?.artifacts || []).find((a) => a.artifact_id === art.artifact_id);
                  const open = selectedArtifactId === art.artifact_id;
                  return (
                    <div key={art.artifact_id}>
                      <div className="workflow-artifact-row" role="button" tabIndex={0}
                        onClick={() => { setSelectedArtifactId(open ? null : art.artifact_id); if (!open && !trace) void loadTrace(); }}
                        onKeyDown={(e) => { if (e.key === "Enter") { setSelectedArtifactId(open ? null : art.artifact_id); if (!open && !trace) void loadTrace(); } }}>
                        <FileText size={14} />
                        <span className="mono-label">{art.name}</span>
                        <span className="task-type">{art.artifact_type || art.kind}</span>
                        <span className="mono-label">Task: {art.task_id || "—"}</span>
                        <span className={`reality-badge reality-${(art.reality || "UNKNOWN").toLowerCase()}`}>{art.reality || "UNKNOWN"}</span>
                        <span className="mono-label">Provenance: {(art.provenance || []).join(", ") || "—"}</span>
                        {chain && chain.parents.length > 0 && <span className="mono-label">← parents: {chain.parents.map((p) => p.slice(0, 8)).join(", ")}</span>}
                        {chain && chain.consumed_by.length > 0 && <span className="mono-label">→ consumed by: {chain.consumed_by.map((c) => c.slice(0, 12)).join(", ")}</span>}
                      </div>
                      {open && (
                        <div className="workflow-task-detail">
                          <span className="mono-label">Producer: {enriched?.agent_id || chain?.producer || "—"}</span>
                          <span className="mono-label">Task: {art.task_id || enriched?.task_id || "—"}</span>
                          <span className="mono-label">Consumers: {((enriched?.consumed_by || chain?.consumed_by || []).join(", ")) || "—"}</span>
                          <span className="mono-label">Reality: {art.reality || "UNKNOWN"}</span>
                          <span className="mono-label">Hash: {(enriched?.content_hash || "").slice(0, 24) || "—"}</span>
                          <span className="mono-label">Verification: {art.verification_state || "UNVERIFIED"}</span>
                          <span className="mono-label">Provenance: {(art.provenance || []).join(", ") || "—"}</span>
                        </div>
                      )}
                    </div>
                  );
                })
                : <p className="empty-ledger">No artifacts produced yet.</p>
            )}
            {activeTab === "events" && (
              events.length > 0
                ? events.map((evt) => (
                  <div key={evt.event_id} className="workflow-event-row">
                    <span className="mono-label">[{evt.event_type}]</span>
                    <span className="mono-label">{new Date(evt.created_at).toLocaleTimeString()}</span>
                  </div>
                ))
                : <p className="empty-ledger">No events yet.</p>
            )}
            {activeTab === "autonomous" && (
              <div className="autonomous-panel">
                {trace ? (
                  <>
                    <h4>Execution Trace</h4>
                    <p><strong>Objective:</strong> {trace.objective}</p>
                    <p><strong>Scope:</strong> {trace.scope}</p>
                    <p><strong>Status:</strong> {trace.status}</p>
                    <p><strong>Tasks:</strong> {trace.total_tasks}</p>
                    {trace.finish_reason && <p><strong>Finish reason:</strong> {trace.finish_reason}</p>}
                    {trace.reality_breakdown && <p><strong>Reality:</strong> {Object.entries(trace.reality_breakdown).map(([k, v]) => `${k}=${v}`).join(" · ")}</p>}
                    {trace.tools_used && Object.keys(trace.tools_used).length > 0 && (
                      <p><strong>Tools used:</strong> {Object.entries(trace.tools_used).map(([k, v]) => `${k}×${v.count}`).join(" · ")}</p>
                    )}
                    <h5>Agent Chain</h5>
                    {trace.tasks.length > 0 ? (
                      trace.tasks.map((t) => {
                        const produced = (trace.artifacts || []).filter((a) => a.task_id === t.task_id);
                        const received = (trace.messages || []).filter((m) => m.to_agent_id === t.agent_id || m.task_id === t.task_id);
                        return (
                          <div key={t.task_id || t.name} className="workflow-task-row">
                            <span className="mono-label">{t.agent_id || "unassigned"} ↓ {t.task_id || t.name}</span>
                            <span className="task-type">{t.task_type}</span>
                            <span className="state-chip"><TaskStatusMark state={t.status || "UNKNOWN"} />{t.status}</span>
                            {produced.map((a) => (
                              <span key={a.artifact_id} className="mono-label">↓ artifact {a.name} [{a.reality}]</span>
                            ))}
                            {received.length > 0 && <span className="mono-label">✉ {received.length} msg</span>}
                            {t.is_dynamic && <span className="task-type">dynamic: {t.generated_reason?.slice(0, 40) || "—"}</span>}
                            {t.retry_count > 0 && <span className="mono-label">retries: {t.retry_count}</span>}
                          </div>
                        );
                      })
                    ) : <p className="empty-ledger">No tasks in trace yet.</p>}
                    <h5>Provenance chain ({(trace.provenance_chain || []).length})</h5>
                    {(trace.provenance_chain || []).map((p) => (
                      <div key={p.artifact_id} className="workflow-artifact-row">
                        <span className="mono-label">{p.producer || "?"} ↓ {p.kind}</span>
                        <span className={`reality-badge reality-${(p.reality || "unknown").toLowerCase()}`}>{p.reality}</span>
                        {p.parents.length > 0 && <span className="mono-label">← {p.parents.map((x) => x.slice(0, 8)).join(", ")}</span>}
                        {p.consumed_by.length > 0 && <span className="mono-label">→ {p.consumed_by.map((x) => x.slice(0, 12)).join(", ")}</span>}
                      </div>
                    ))}
                    {(trace.provenance_chain || []).length === 0 && <p className="empty-ledger">No provenance chain yet.</p>}
                    <h5>Q&amp;A ({(trace.messages || []).filter((m) => m.message_type === "QUESTION" || m.message_type === "ANSWER").length})</h5>
                    {(trace.messages || []).filter((m) => m.message_type === "QUESTION" || m.message_type === "ANSWER").map((m) => (
                      <div key={m.message_id} className="workflow-message-row">
                        <span className="mono-label">[{m.message_type}]</span>
                        <span className="mono-label">From: {m.from_agent_id || "system"}</span>
                        <span className="mono-label">To: {m.to_agent_id || "all"}</span>
                      </div>
                    ))}
                    <h5>Dynamic Tasks ({trace.dynamic_tasks.length})</h5>
                    {trace.dynamic_tasks.length > 0 ? (
                      trace.dynamic_tasks.map((dt) => (
                        <div key={dt.task_id} className="workflow-task-row">
                          <span className="mono-label">{dt.name}</span>
                          <span className="task-type">dynamic</span>
                          {dt.parent_task_id && <span className="mono-label">Parent: {dt.parent_task_id.slice(0, 12)}</span>}
                          <span className="mono-label">Reason: {dt.reason?.slice(0, 40) || "—"}</span>
                        </div>
                      ))
                    ) : <p className="empty-ledger">No dynamic tasks were created.</p>}
                    <h5>Approvals ({approvals.length > 0 ? approvals.length : trace.approvals.length})</h5>
                    {(approvals.length > 0 ? approvals : trace.approvals).length > 0 ? (
                      (approvals.length > 0 ? approvals : trace.approvals).map((ap) => (
                        <div key={ap.approval_id} className="workflow-task-row">
                          <span className="mono-label">{ap.operation}</span>
                          <span className="state-chip">{ap.status}</span>
                          <span className="mono-label">By: {ap.requested_by || "—"}</span>
                          {ap.status === "PENDING" && (
                            <>
                              <button className="quiet-control" onClick={() => void decideApproval(ap.approval_id, "APPROVED")}>Approve</button>
                              <button className="quiet-control" onClick={() => void decideApproval(ap.approval_id, "REJECTED")}>Reject</button>
                            </>
                          )}
                        </div>
                      ))
                    ) : <p className="empty-ledger">No approvals requested.</p>}
                    {(trace.workers || []).length > 0 && (
                      <>
                        <h5>Workers ({(trace.workers || []).length})</h5>
                        {(trace.workers || []).map((w) => (
                          <div key={w.worker_id} className="workflow-task-row">
                            <span className="mono-label">{w.worker_id.slice(0, 18)}</span>
                            <span className="state-chip">{w.liveness || w.status}</span>
                            {w.current_task_id && <span className="mono-label">task: {w.current_task_id.slice(0, 14)}</span>}
                          </div>
                        ))}
                      </>
                    )}
                    {(trace.recovery_events || []).length > 0 && (
                      <>
                        <h5>Recovery ({(trace.recovery_events || []).length})</h5>
                        {(trace.recovery_events || []).slice(0, 10).map((e) => (
                          <div key={String(e.event_id)} className="workflow-event-row">
                            <span className="mono-label">[{e.event_type}]</span>
                            <span className="mono-label">{e.task_id || "—"}</span>
                          </div>
                        ))}
                      </>
                    )}
                    <h5>Model runtime (Phase 7)</h5>
                    {(trace as unknown as { model?: { execution_mode: string; invocations: number; providers_used: string[]; failures: number; tool_requests: number; events: Array<{ event_id: string | number; event_type: string; task_id?: string | null; created_at: string; detail: Record<string, unknown> }>; router_status?: { provider: string; model: string } } }).model ? (
                      <>
                        <p><strong>Execution mode:</strong> {(trace as unknown as { model: { execution_mode: string } }).model.execution_mode}</p>
                        <p><strong>Provider/model:</strong> {(trace as unknown as { model: { router_status?: { provider: string; model: string } } }).model.router_status?.provider || "—"} / {(trace as unknown as { model: { router_status?: { provider: string; model: string } } }).model.router_status?.model || "—"} (secrets never exposed)</p>
                        <p><strong>Invocations:</strong> {(trace as unknown as { model: { invocations: number } }).model.invocations} · <strong>Tool requests:</strong> {(trace as unknown as { model: { tool_requests: number } }).model.tool_requests} · <strong>Failures/fallback:</strong> {(trace as unknown as { model: { failures: number } }).model.failures}</p>
                        {(trace as unknown as { model: { providers_used: string[] } }).model.providers_used.length > 0 && <p><strong>Providers used:</strong> {(trace as unknown as { model: { providers_used: string[] } }).model.providers_used.join(", ")}</p>}
                        {(trace as unknown as { model: { events: Array<{ event_id: string | number; event_type: string; task_id?: string | null; detail: Record<string, unknown> }> } }).model.events.slice(0, 8).map((e) => (
                          <div key={String(e.event_id)} className="workflow-event-row">
                            <span className="mono-label">[{e.event_type}]</span>
                            <span className="mono-label">{e.task_id || "—"}</span>
                            <span className="mono-label">{JSON.stringify(e.detail).slice(0, 120)}</span>
                          </div>
                        ))}
                        {(trace as unknown as { model: { events: Array<unknown> } }).model.events.length === 0 && <p className="empty-ledger">No model invocations — deterministic execution (honest fallback).</p>}
                      </>
                    ) : <p className="empty-ledger">No model section in trace yet.</p>}
                    <h5>Verification</h5>
                    <p>All completed: {trace.verification.all_completed ? "Yes" : "No"}</p>
                    <h5>Final Result</h5>
                    <pre className="mono-label">{JSON.stringify(trace.final_result, null, 2)}</pre>
                  </>
                ) : (
                  <div className="autonomous-input">
                    <textarea
                      value={autonomousObjective}
                      onChange={(e) => setAutonomousObjective(e.target.value)}
                      placeholder="Enter an objective for autonomous execution (e.g. 'Explore the Nexus repository and produce an architecture report')"
                      className="mono-input"
                      rows={3}
                    />
                    <button
                      className="primary-control"
                      disabled={runningAutonomous || !autonomousObjective.trim()}
                      onClick={async () => {
                        setRunningAutonomous(true);
                        try {
                          const resp = await workflowApi.runAutonomous({
                            objective: autonomousObjective,
                            scope: "Themeta-verse/Nexus",
                          });
                          const wfId = resp.workflow_id;
                          const tr = await workflowApi.getExecutionTrace(wfId);
                          setTrace(tr);
                          setExpanded(true);
                        } finally {
                          setRunningAutonomous(false);
                        }
                      }}
                    >
                      {runningAutonomous ? <LoaderCircle className="spin" size={15} /> : <Zap size={15} />}
                      Run Autonomously
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}

      <div className="workflow-actions">
        <button className="quiet-control" onClick={() => setExpanded(!expanded)}>
          <Search size={15} />{expanded ? "Hide" : "Inspect"} tasks
        </button>
        {["PENDING", "PAUSED", "RUNNING"].includes(workflow.status) && expanded && (
          <button className="quiet-control" disabled={loading || workflow.status !== "RUNNING"} onClick={handleStep}>
            {stepping ? <LoaderCircle className="spin" size={15} /> : <Zap size={15} />}
            Step once
          </button>
        )}
        {hasStuck && expanded && (
          <button className="quiet-control is-danger" disabled={recovering} onClick={handleRecover}>
            {recovering ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
            Recover stuck ({stuckTasks})
          </button>
        )}
        <button className="quiet-control" disabled={loading || !["PENDING", "PAUSED", "RUNNING"].includes(workflow.status)} onClick={toggleStart}>
          {loading ? <LoaderCircle className="spin" size={15} /> : workflow.status === "RUNNING" ? <Pause size={15} /> : <Play size={15} />}
          {workflow.status === "RUNNING" ? "Pause" : "Start"}
        </button>
        {["PENDING", "PAUSED", "RUNNING"].includes(workflow.status) && (
          <button className="quiet-control is-danger" disabled={loading} onClick={toggleCancel}>
            <Square size={15} />Cancel
          </button>
        )}
        <button className="quiet-control" onClick={() => window.open(`${import.meta.env.VITE_NEXUS_API_BASE_URL}/api/v1/workflows/${workflow.workflow_id}`, "_blank")}>
          <ExternalLink size={15} />View API
        </button>
      </div>
    </article>
  );
}

export default function WorkflowsPage({ session, onBack }: { session: { user: { email: string }; projects: Array<{ project_id: string; display_name: string; role: string }> }; onBack: () => void }) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [activeProject, setActiveProject] = useState(session.projects[0]?.project_id || "");
  const [error, setError] = useState<string | null>(null);
  const [workers, setWorkers] = useState<WorkerHeartbeat[]>([]);
  const [agents, setAgents] = useState<AgentDescription[]>([]);
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);

  const [newName, setNewName] = useState("");
  const [newObjective, setNewObjective] = useState("");
  const [newScope, setNewScope] = useState("");

  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await workflowApi.listWorkflows(activeProject);
      setWorkflows(payload.workflows);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load workflows");
    } finally {
      setLoading(false);
    }
  }, [activeProject]);

  useEffect(() => { void loadWorkflows(); }, [loadWorkflows]);

  useEffect(() => {
    (async () => {
      try {
        const [w, a, t, m] = await Promise.all([
          workflowApi.listWorkers().catch(() => ({ workers: [] as WorkerHeartbeat[] })),
          workflowApi.describeAgents().catch(() => ({ agents: [] as AgentDescription[] })),
          workflowApi.describeTools().catch(() => ({ tools: [] as ToolSpec[] })),
          workflowApi.modelStatus().catch(() => null),
        ]);
        setWorkers(w.workers || []);
        setAgents(a.agents || []);
        setTools(t.tools || []);
        if (m) setModelStatus(m);
      } catch (e) {
        console.error(e);
      }
    })();
  }, []);

  const handleCreate = async () => {
    if (!newName.trim() || !newObjective.trim()) {
      setError("Workflow name and objective are required");
      return;
    }
    setLoading(true);
    try {
      // Planner -> validated task graph -> persisted workflow (real API state).
      const plan = await workflowApi.planWorkflow(newObjective.trim(), newScope.trim() || activeProject || "local");
      const specs = (plan.plan?.task_specs || plan.plan?.tasks || []) as Array<{
        task_id: string; task_type: string; name: string; agent_id?: string | null;
        required_capabilities?: string[]; depends_on?: string[]; input_artifacts?: string[];
      }>;
      const plannedAgents = (plan.plan?.agents || []) as Array<{
        agent_id: string; name: string; role: string; capabilities: string[];
        allowed_operations?: string[]; prohibited_operations?: string[]; scope?: Record<string, unknown>; expected_behaviour?: string;
      }>;
      await workflowApi.createWorkflow({
        name: newName.trim(),
        objective: newObjective.trim(),
        scope: newScope.trim() || activeProject || "local",
        project_id: activeProject || undefined,
        task_specs: specs.map((t) => ({
          task_id: t.task_id,
          task_type: t.task_type,
          name: t.name,
          agent_id: t.agent_id ?? null,
          required_capabilities: t.required_capabilities ?? [],
          depends_on: t.depends_on ?? [],
          input_artifacts: t.input_artifacts ?? [],
        })),
        agents: plannedAgents.map((a) => ({
          agent_id: a.agent_id,
          name: a.name,
          role: a.role,
          capabilities: a.capabilities,
          allowed_operations: a.allowed_operations ?? [],
          prohibited_operations: a.prohibited_operations ?? [],
          scope: a.scope ?? {},
          expected_behaviour: a.expected_behaviour ?? "",
        })),
        execution_mode: "REAL_READ",
      });
      setShowCreate(false);
      setNewName("");
      setNewObjective("");
      setNewScope("");
      await loadWorkflows();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to create workflow");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="nexus-shell">
      <aside className="identity-rail" aria-label="NEXUS workflow command rail">
        <div className="rail-top">
          <img className="nexus-logo" src="/assets/nexus-aperture-logo.png" alt="NEXUS aperture symbol" />
          <div className="wordmark">NEXUS<span>WORKFLOWS</span></div>
        </div>
        <div className="rail-coordinate">WORKFLOW ENGINE / 01</div>
        <nav className="rail-nav" aria-label="Workflow sections">
          <button className={`rail-link is-active`}><Activity size={16} />Workflow desk</button>
          <button className={`rail-link`} onClick={onBack}><ArrowLeft size={16} />Back to desk</button>
        </nav>
        <div className="rail-status">
          <span className="mono-label">PROJECT</span>
          <strong>{session.projects.find((p) => p.project_id === activeProject)?.display_name || activeProject || "None"}</strong>
        </div>
        <div className="rail-footer">MERIDIAN / {new Date().getFullYear()}</div>
      </aside>

      <section className="main-canvas">
        <header className="topline">
          <div>
            <span className="mono-label">PHASE 4 — AUTONOMOUS WORKFLOW EXECUTION</span>
            <p>Planner → execution → agents → artifacts → messages → verification → persisted result.</p>
          </div>
          <div className="topline-actions">
            {session && <span className="identity-chip"><Activity size={14} />{session.user.email}</span>}
            <select value={activeProject} onChange={(e) => setActiveProject(e.target.value)} aria-label="Active project">
              {session.projects.map((p) => <option key={p.project_id} value={p.project_id}>{p.display_name}</option>)}
            </select>
            <button className="quiet-control" onClick={() => void loadWorkflows()} disabled={loading}>
              <RefreshCw size={15} className={loading ? "spin" : ""} />Refresh
            </button>
          </div>
        </header>

        <section className="workflow-toolbar">
          {!showCreate && (
            <button className="run-button" onClick={() => setShowCreate(true)}>
              <Plus size={17} />Create workflow
            </button>
          )}
          {error && <div className="error-line"><Activity size={16} />{error}</div>}
        </section>

        <section className="workflow-list" aria-label="Runtime status">
          <div className="workflow-card">
            <div className="workflow-card-top">
              <div className="workflow-title"><Activity size={16} /><h3>Runtime status</h3></div>
              <span className="state-chip">workers: {workers.length} · agents: {agents.length} · tools: {tools.length}</span>
            </div>
            <div className="workflow-meta">
              <span className="mono-label">WORKERS: {workers.length > 0 ? workers.map((w) => `${w.worker_id.slice(0, 14)}:${w.liveness || w.status}${w.current_task_id ? `→${w.current_task_id.slice(0, 8)}` : ""}`).join(" · ") : "no heartbeats yet (start API + workflow-worker)"}</span>
            </div>
            <div className="workflow-meta">
              <span className="mono-label">AGENTS: {agents.length > 0 ? agents.map((a) => `${a.agent_id}(${a.lifecycle})`).join(" · ") : "—"}</span>
            </div>
            <div className="workflow-meta">
              <span className="mono-label">TOOLS: {tools.length > 0 ? tools.map((t) => `${t.capability}${t.executable ? "*" : ""}`).join(" · ") : "—"} (* = executable)</span>
            </div>
            <div className="workflow-meta">
              <span className="mono-label">MODEL: {modelStatus ? `${modelStatus.execution_mode} · ${modelStatus.provider}/${modelStatus.model}` : "loading…"} (INFERRED only; OBSERVED = tools, VERIFIED = verifier)</span>
            </div>
          </div>
        </section>

        {showCreate && (
          <section className="workflow-create-panel">
            <h2>Create Workflow</h2>
            <div className="form-grid">
              <label>Workflow name</label>
              <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Code audit analysis" />

              <label>Objective</label>
              <textarea value={newObjective} onChange={(e) => setNewObjective(e.target.value)} placeholder="What should this workflow accomplish? (e.g. Analyze this repository and produce a security report)" />

              <label>Scope (workspace path or repository)</label>
              <input value={newScope} onChange={(e) => setNewScope(e.target.value)} placeholder="e.g. E:/Nexus or Themeta-verse/Nexus" />
              <small>The planner builds a validated multi-agent task graph from the objective; the workflow persists via the real API.</small>
            </div>
            <div className="form-actions">
              <button className="quiet-control" onClick={() => setShowCreate(false)}>Cancel</button>
              <button className="run-button" onClick={() => void handleCreate()} disabled={loading}>
                {loading ? <LoaderCircle className="spin" size={17} /> : <GitBranch size={17} />}
                {loading ? "Creating" : "Create"}
              </button>
            </div>
          </section>
        )}

        <section className="workflow-list" aria-label="Your workflows">
          {loading && <p className="empty-ledger">Loading workflows...</p>}
          {!loading && workflows.length === 0 && <p className="empty-ledger">{activeProject ? "No workflows in this project yet." : "Select a project to begin."}</p>}
          {!loading && workflows.map((workflow) => <WorkflowCard key={workflow.workflow_id} workflow={workflow} onRefresh={loadWorkflows} />)}
        </section>
      </section>
    </main>
  );
}
