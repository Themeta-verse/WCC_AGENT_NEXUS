/**
 * Agents — PS002 Phase 1 foundation viewer (registry + declared policy only).
 *
 * Every value rendered here originates from authenticated backend state.
 * Missing data renders UNKNOWN or explicit empty states ("No agents
 * registered.", "No active policy."). No actions, verdicts, timelines,
 * metrics, or scores exist in this phase.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Eye,
  GitCommit,
  LoaderCircle,
  Plus,
  ShieldAlert,
  TerminalSquare,
  UserCog,
} from "lucide-react";
import {
  Agent,
  AgentAction,
  AgentIntegrityEvent,
  AgentPolicy,
  nexusApi,
  ProductSession,
} from "@/lib/nexusApi";

const UNKNOWN = "UNKNOWN";

function text(value: unknown, fallback: string = UNKNOWN): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function strRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function StatusBadge({ state }: { state: string }) {
  const tone = state === "ACTIVE" ? "is-good" : state === "RETIRED" ? "is-blocked" : state === "FLAGGED" ? "is-attention" : state === "HALTED" ? "is-blocked" : "is-attention";
  return <span className={`status-badge ${tone}`}><strong>{state}</strong></span>;
}

function AgentCard({ agent, onSelect, selected }: { agent: Agent; onSelect: () => void; selected: boolean }) {
  return (
    <article className={`agent-card ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <div className="agent-card-header">
        <span className="agent-id">{agent.agent_id}</span>
        <StatusBadge state={agent.status} />
      </div>
      <p className="agent-name">{agent.display_name}</p>
      <p className="agent-meta">{agent.project_id} · {agent.created_at}</p>
    </article>
  );
}

function PolicyPanel({ policy, history }: { policy: AgentPolicy | null; history: AgentPolicy[] }) {
  if (!policy) return <p className="empty-state">No active policy.</p>;
  const caps = strArray(policy.declared_capabilities);
  const allowed = strArray(policy.allowed_operations);
  const prohibited = strArray(policy.prohibited_operations);
  const scope = strRecord(policy.scope);
  const listOrUnknown = (items: string[]) => items.length > 0 ? items : [UNKNOWN];
  return (
    <section className="integrity-panel" id="policy" aria-label="Active policy">
      <div className="section-heading"><h2>Active Policy v{policy.version}</h2><span className="mono-label">{policy.policy_id}</span></div>
      <p className="policy-behaviour"><span className="mono-label">EXPECTED BEHAVIOUR</span><span>{text(policy.expected_behaviour)}</span></p>
      <div className="policy-grid">
        <div><span className="mono-label">ALLOWED</span><ul>{listOrUnknown(caps).map((c, i) => <li key={i} className="allow-line">{c}</li>)}</ul></div>
        <div><span className="mono-label">ALLOWED OPERATIONS</span><ul>{listOrUnknown(allowed).map((o, i) => <li key={i} className="allow-line">{o}</li>)}</ul></div>
        <div><span className="mono-label">PROHIBITED</span><ul>{listOrUnknown(prohibited).map((o, i) => <li key={i} className="deny-line">{o}</li>)}</ul></div>
        <div><span className="mono-label">SCOPE</span><pre>{Object.keys(scope).length > 0 ? JSON.stringify(scope, null, 2) : UNKNOWN}</pre></div>
      </div>
      {history.length > 1 && (
        <p className="policy-behaviour"><span className="mono-label">VERSION HISTORY</span><span>{history.map((p) => `v${p.version}`).join(" · ")}</span></p>
      )}
    </section>
  );
}

function CreateAgentDialog({ open, onClose, onSubmit }: { open: boolean; onClose: () => void; onSubmit: (agentId: string | undefined, projectId: string, displayName: string) => void }) {
  if (!open) return null;
  const [agentId, setAgentId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true); setError(null);
    try { await onSubmit(agentId.trim() || undefined, projectId, displayName); onClose(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Agent creation failed"); }
    finally { setSubmitting(false); }
  };
  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Register New Agent</h3>
        <form onSubmit={handleSubmit}>
          <label>Agent ID (optional, slug from display name if blank)<input value={agentId} onChange={(e) => setAgentId(e.target.value)} placeholder="code-review-bot" /></label>
          <label>Project ID<input value={projectId} onChange={(e) => setProjectId(e.target.value)} required placeholder="project-uuid" /></label>
          <label>Display Name<input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required placeholder="Agent display name" /></label>
          {error && <p className="dialog-error">{error}</p>}
          <div className="dialog-actions"><button type="button" onClick={onClose}>Cancel</button><button type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={14} /> : "Register Agent"}</button></div>
        </form>
      </div>
    </div>
  );
}

function CreatePolicyDialog({ open, onClose, onSubmit, agentId }: { open: boolean; onClose: () => void; onSubmit: (payload: { declared_capabilities: string[]; allowed_operations: string[]; prohibited_operations: string[]; scope: Record<string, unknown>; expected_behaviour: string }) => void; agentId: string }) {
  if (!open) return null;
  const [declared, setDeclared] = useState("");
  const [allowed, setAllowed] = useState("");
  const [prohibited, setProhibited] = useState("");
  const [scope, setScope] = useState("");
  const [behaviour, setBehaviour] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true); setError(null);
    try {
      const payload = {
        declared_capabilities: declared.split("\n").map(s => s.trim()).filter(Boolean),
        allowed_operations: allowed.split("\n").map(s => s.trim()).filter(Boolean),
        prohibited_operations: prohibited.split("\n").map(s => s.trim()).filter(Boolean),
        scope: scope.trim() ? JSON.parse(scope) : {},
        expected_behaviour: behaviour,
      };
      await onSubmit(payload);
      onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Policy creation failed"); }
    finally { setSubmitting(false); }
  };
  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Declare Policy for {agentId}</h3>
        <form onSubmit={handleSubmit}>
          <label>Declared Capabilities (one per line)<textarea value={declared} onChange={(e) => setDeclared(e.target.value)} rows={3} placeholder={"filesystem.read\nrepository.read"} /></label>
          <label>Allowed Operations (one per line)<textarea value={allowed} onChange={(e) => setAllowed(e.target.value)} rows={2} placeholder="read" /></label>
          <label>Prohibited Operations (one per line)<textarea value={prohibited} onChange={(e) => setProhibited(e.target.value)} rows={2} placeholder={"filesystem.write\ngit.push"} /></label>
          <label>Scope (JSON)<textarea value={scope} onChange={(e) => setScope(e.target.value)} rows={2} placeholder={'{"filesystem_read_paths": ["/project/src/**"]}'} /></label>
          <label>Expected Behaviour<textarea value={behaviour} onChange={(e) => setBehaviour(e.target.value)} rows={2} placeholder="Read and analyze source code only." /></label>
          {error && <p className="dialog-error">{error}</p>}
          <div className="dialog-actions"><button type="button" onClick={onClose}>Cancel</button><button type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={14} /> : "Declare Policy"}</button></div>
        </form>
      </div>
    </div>
  );
}

function ActionForm({ open, onClose, agentId, onSubmit }: { open: boolean; onClose: () => void; agentId: string; onSubmit: (payload: { operation: string; target_resource?: string; requested_capability?: string; parameters?: Record<string, unknown> }) => Promise<void> }) {
  if (!open) return null;
  const [operation, setOperation] = useState("");
  const [target, setTarget] = useState("");
  const [capability, setCapability] = useState("");
  const [params, setParams] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true); setError(null);
    try {
      const payload: { operation: string; target_resource?: string; requested_capability?: string; parameters?: Record<string, unknown> } = { operation };
      if (target.trim()) payload.target_resource = target;
      if (capability.trim()) payload.requested_capability = capability;
      if (params.trim()) payload.parameters = JSON.parse(params);
      await onSubmit(payload);
      onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Action submission failed"); }
    finally { setSubmitting(false); }
  };
  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Record Manual Action for {agentId}</h3>
        <p className="dialog-hint">Manual administrative action submission (API claim, not runtime observation).</p>
        <form onSubmit={handleSubmit}>
          <label>Operation<input value={operation} onChange={(e) => setOperation(e.target.value)} required placeholder="filesystem.read" /></label>
          <label>Target Resource<input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="/project/src/index.ts" /></label>
          <label>Requested Capability<input value={capability} onChange={(e) => setCapability(e.target.value)} placeholder="filesystem.read" /></label>
          <label>Parameters (JSON)<textarea value={params} onChange={(e) => setParams(e.target.value)} rows={2} placeholder='{"file": "README.md"}' /></label>
          {error && <p className="dialog-error">{error}</p>}
          <div className="dialog-actions"><button type="button" onClick={onClose}>Cancel</button><button type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={14} /> : "Record Action"}</button></div>
        </form>
      </div>
    </div>
  );
}

function ObservationForm({ open, onClose, agentId, onSubmit }: { open: boolean; onClose: () => void; agentId: string; onSubmit: (payload: { operation: string; target_resource: string; requested_capability?: string; parameters?: Record<string, unknown>; observation_root: string }) => Promise<void> }) {
  if (!open) return null;
  const [operation, setOperation] = useState("filesystem.read");
  const [target, setTarget] = useState("");
  const [root, setRoot] = useState("");
  const [capability, setCapability] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true); setError(null);
    try {
      await onSubmit({ operation, target_resource: target, requested_capability: capability || undefined, parameters: {}, observation_root: root });
      onClose();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Observation failed"); }
    finally { setSubmitting(false); }
  };
  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Live Observation — {agentId}</h3>
        <p className="dialog-hint">The bounded runtime actually performs the operation and NEXUS observes the real result.</p>
        <form onSubmit={handleSubmit}>
          <label>Operation<input value={operation} onChange={(e) => setOperation(e.target.value)} required placeholder="filesystem.read" /></label>
          <label>Target Resource<input value={target} onChange={(e) => setTarget(e.target.value)} required placeholder="/project/src/index.ts" /></label>
          <label>Requested Capability<input value={capability} onChange={(e) => setCapability(e.target.value)} placeholder="filesystem.read" /></label>
          <label>Observation Root<input value={root} onChange={(e) => setRoot(e.target.value)} required placeholder="/project/src" />
            <small>Bounded sandbox root the runtime is permitted to access</small>
          </label>
          {error && <p className="dialog-error">{error}</p>}
          <div className="dialog-actions"><button type="button" onClick={onClose}>Cancel</button><button type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={14} /> : "Observe Real Action"}</button></div>
        </form>
      </div>
    </div>
  );
}

function IntegrityTimeline({ agentId }: { agentId: string }) {
  const [actions, setActions] = useState<AgentAction[]>([]);
  const [events, setEvents] = useState<AgentIntegrityEvent[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await nexusApi.getAgentIntegrity(agentId);
      setActions(res.recent_actions || []);
      setEvents(res.integrity_events || []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [agentId]);

  useEffect(() => { load(); }, [load]);

  const decisionTone = (d: string) => d === "ALLOW" ? "is-good" : d === "FLAG" ? "is-attention" : "is-blocked";
  return (
    <section className="integrity-panel" aria-label="Integrity timeline">
      <div className="section-heading"><h2>Integrity Timeline</h2><span className="mono-label">{actions.length} ACTIONS · {events.length} EVENTS</span></div>
      {loading && <p className="loading"><LoaderCircle className="spin" size={16} />Loading timeline...</p>}
      {!loading && actions.length === 0 && <p className="empty-state">No actions recorded yet.</p>}
      {!loading && actions.length > 0 && (
        <table className="timeline-table">
          <thead><tr><th>Decision</th><th>Reality</th><th>Operation</th><th>Target</th><th>Reason</th><th>Evidence</th><th>Timestamp</th></tr></thead>
          <tbody>
            {actions.map((a) => (
              <tr key={a.action_id}>
                <td><span className={`status-badge ${decisionTone(a.integrity_decision)}`}><strong>{a.integrity_decision}</strong></span></td>
                <td><span className={`status-badge ${a.observation_reality === "OBSERVED" ? "is-good" : "is-attention"}`}>{a.observation_reality}</span></td>
                <td>{a.operation}</td>
                <td>{text(a.target_resource, "-")}</td>
                <td>{text(a.integrity_reason, "-")}</td>
                <td><span className="mono-label">{a.evidence_json ? "✓" : "-"}</span></td>
                <td><span className="mono-label">{a.created_at}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!loading && events.length > 0 && (
        <div className="events-log">
          <h3>Integrity Events</h3>
          <ul>
            {events.map((e) => (
              <li key={e.event_id}><span className="mono-label">{e.event_type}</span><small>{e.created_at}</small></li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default function AgentsPage({ session, onBack }: { session: ProductSession; onBack: () => void }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [policy, setPolicy] = useState<AgentPolicy | null>(null);
  const [history, setHistory] = useState<AgentPolicy[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  const [createPolicyOpen, setCreatePolicyOpen] = useState(false);
  const [recordActionOpen, setRecordActionOpen] = useState(false);
  const [observeActionOpen, setObserveActionOpen] = useState(false);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    try { const res = await nexusApi.listAgents(); setAgents(res.agents); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to load agents"); }
    finally { setLoading(false); }
  }, []);

  const loadPolicy = useCallback(async (agentId: string) => {
    try {
      const [active, versions] = await Promise.all([
        nexusApi.getAgentPolicy(agentId).catch(() => ({ policy: null as AgentPolicy | null })),
        nexusApi.listAgentPolicies(agentId).catch(() => ({ policies: [] as AgentPolicy[] })),
      ]);
      setPolicy(active.policy);
      setHistory(versions.policies);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to load policy"); }
  }, []);

  const handleCreateAgent = async (agentId: string | undefined, projectId: string, displayName: string) => {
    await nexusApi.createAgent({ agent_id: agentId, project_id: projectId, display_name: displayName });
    await loadAgents();
  };

  const handleCreatePolicy = async (payload: { declared_capabilities: string[]; allowed_operations: string[]; prohibited_operations: string[]; scope: Record<string, unknown>; expected_behaviour: string }) => {
    if (!selectedAgent) return;
    await nexusApi.createAgentPolicy(selectedAgent.agent_id, payload);
    await loadPolicy(selectedAgent.agent_id);
  };

  const selectAgent = (agent: Agent) => {
    setSelectedAgent(agent);
    setPolicy(null);
    setHistory([]);
    loadPolicy(agent.agent_id);
  };

  const handleRecordAction = async (payload: { operation: string; target_resource?: string; requested_capability?: string; parameters?: Record<string, unknown> }) => {
    if (!selectedAgent) return;
    await nexusApi.submitAgentAction(selectedAgent.agent_id, payload);
  };

  const handleObserveAction = async (payload: { operation: string; target_resource: string; requested_capability?: string; parameters?: Record<string, unknown>; observation_root: string }) => {
    if (!selectedAgent) return;
    await nexusApi.observeAgentAction(selectedAgent.agent_id, payload);
  };

  const handleEnforce = async (status: "ACTIVE" | "FLAGGED" | "HALTED") => {
    if (!selectedAgent) return;
    await nexusApi.enforceAgent(selectedAgent.agent_id, status);
    setSelectedAgent({ ...selectedAgent, status });
  };

  useEffect(() => { loadAgents(); }, [loadAgents]);

  return (
    <main className="nexus-shell agents-page">
      <aside className="identity-rail" aria-label="Agents navigation">
        <div className="rail-top"><img className="nexus-logo" src="/assets/nexus-aperture-logo.png" alt="NEXUS aperture" /><div className="wordmark">NEXUS<span>IND</span></div></div>
        <div className="rail-coordinate">AGENTS / PS002</div>
         <nav className="rail-nav" aria-label="Agent sections">
            <a className="rail-link is-active" href="#agents"><TerminalSquare size={16} />Agents</a>
            <a className="rail-link" href="#policy"><ShieldAlert size={16} />Policy</a>
            <a className="rail-link" href="#observe"><Eye size={16} />Live Observation</a>
            <a className="rail-link" href="#actions"><GitCommit size={16} />Manual Action</a>
            <a className="rail-link" href="#integrity"><Activity size={16} />Integrity Timeline</a>
          </nav>
        <div className="rail-status"><span className="mono-label">TENANT SCOPE</span><strong>{session.user.tenant_id}</strong><small>{session.user.email} · {session.projects[0]?.project_id || UNKNOWN}</small></div>
        <button className="quiet-control rail-link" onClick={onBack}><UserCog size={16} />Back to Command Center</button>
        <div className="rail-footer">MERIDIAN / {new Date().getFullYear()}</div>
      </aside>

      <section className="main-canvas">
        <header className="topline">
          <div><span className="mono-label">AGENT REGISTRY</span><p>Persistent agent identities and their declared policies.</p></div>
          <div className="topline-actions">
            <button className="run-button" onClick={() => setCreateAgentOpen(true)}><Plus size={17} />Register Agent</button>
            {selectedAgent && <button className="run-button" onClick={() => setCreatePolicyOpen(true)}><ShieldAlert size={17} />{policy ? "Update Policy" : "Declare Policy"}</button>}
          </div>
        </header>
        {error && <p className="dialog-error">{error}</p>}

        <section className="agent-grid" id="agents" aria-label="Registered agents">
          <div className="section-heading"><h2>Agents</h2><span className="mono-label">{agents.length} REGISTERED</span></div>
          <div className="grid">{loading && agents.length === 0 ? <p className="loading"><LoaderCircle className="spin" size={20} />Loading agents...</p> : agents.length === 0 ? <p className="empty-state">No agents registered.</p> : agents.map((agent) => <AgentCard key={agent.agent_id} agent={agent} selected={selectedAgent?.agent_id === agent.agent_id} onSelect={() => selectAgent(agent)} />)}</div>
        </section>

        {selectedAgent && (
          <section className="integrity-panel" aria-label="Selected agent">
            <div className="section-heading"><h2>{text(selectedAgent.display_name)}</h2><span className="mono-label">{selectedAgent.agent_id} · {selectedAgent.tenant_id} · {selectedAgent.project_id}</span></div>
            <div className="agent-controls">
              <StatusBadge state={selectedAgent.status} />
              {selectedAgent.status !== "HALTED" && selectedAgent.status !== "RETIRED" && (
                <button className="quiet-control" onClick={() => handleEnforce("HALTED")}><ShieldAlert size={16} />Halt Agent</button>
              )}
              {selectedAgent.status !== "ACTIVE" && (
                <button className="quiet-control" onClick={() => handleEnforce("ACTIVE")}><Activity size={16} />Activate</button>
              )}
            </div>
          </section>
        )}

         {selectedAgent && policy && (
          <section className="section-actions">
            <button className="run-button" onClick={() => setObserveActionOpen(true)}><Eye size={16} />Live Observation</button>
            <button className="run-button" onClick={() => setRecordActionOpen(true)}><GitCommit size={16} />Record Manual Action</button>
          </section>
        )}

        {selectedAgent && <PolicyPanel policy={policy} history={history} />}
        {selectedAgent && <IntegrityTimeline agentId={selectedAgent.agent_id} />}

        <CreateAgentDialog open={createAgentOpen} onClose={() => setCreateAgentOpen(false)} onSubmit={handleCreateAgent} />
        <CreatePolicyDialog open={createPolicyOpen} onClose={() => setCreatePolicyOpen(false)} onSubmit={handleCreatePolicy} agentId={selectedAgent?.agent_id || ""} />
        {selectedAgent && <ObservationForm open={observeActionOpen} onClose={() => setObserveActionOpen(false)} agentId={selectedAgent.agent_id} onSubmit={handleObserveAction} />}
        {selectedAgent && <ActionForm open={recordActionOpen} onClose={() => setRecordActionOpen(false)} agentId={selectedAgent.agent_id} onSubmit={handleRecordAction} />}
      </section>
    </main>
  );
}
