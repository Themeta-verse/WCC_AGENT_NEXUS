/**
 * Meridian Operations Desk — evidence-first, technical editorial control room.
 * The secure extension keeps the original philosophy: identity before access,
 * tenant scope before data, and observed queue state before execution claims.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  CircleAlert,
  Clock3,
  Database,
  ExternalLink,
  FileSearch,
  Globe2,
  KeyRound,
  LoaderCircle,
  LogOut,
  Radar,
  ShieldCheck,
  TerminalSquare,
  UserRound,
} from "lucide-react";
import { apiBase, AuditEvent, Capability, clearApiBaseOverride, clearProductSession, configureApiBase, DatabaseInspection, getApiBase, hasApiBaseOverride, MemoryItem, MissionEvidence, MissionEvent, MissionSummary, nexusApi, Outcome, persistProductSession, ProductHealth, ProductSession, ProjectContext, ProviderState, readProductSession } from "@/lib/nexusApi";
import AgentsPage from "./Agents";
import WorkflowsPage from "./Workflows";

const capabilities: Array<{ id: Capability; label: string; detail: string; icon: typeof Globe2 }> = [
  { id: "repository.metadata.read", label: "Metadata", detail: "one bounded repository identity read", icon: Radar },
  { id: "repository.read", label: "Repository health", detail: "full read-only health evidence", icon: FileSearch },
  { id: "browser.read", label: "Browser context", detail: "read-only Chromium CDP evidence", icon: Globe2 },
  { id: "filesystem.read", label: "Local evidence", detail: "bounded source filesystem read", icon: Database },
];

const defaultIntent = "Analyze the current state of this project and tell me the highest-value next action.";
const terminalStatuses = new Set(["COMPLETED", "PARTIAL", "FAILED", "BLOCKED"]);

type ViewMode = "command-center" | "agents" | "workflows";

function StatusMark({ state }: { state: string }) {
  const normalized = state.toLowerCase();
  const tone = normalized.includes("verified") || normalized.includes("healthy") || normalized.includes("completed") || normalized.includes("ready") || normalized.includes("observed") || normalized.includes("authenticated")
    ? "is-good"
    : normalized.includes("failed") || normalized.includes("blocked") || normalized.includes("unavailable")
      ? "is-blocked"
      : "is-attention";
  return <span className={`status-mark ${tone}`} aria-hidden="true" />;
}

function EvidenceStamp({ state, label }: { state: string; label: string }) {
  const locked = state.toLowerCase().includes("locked") || state.toLowerCase().includes("auth");
  return <span className={`evidence-stamp ${locked ? "is-locked" : ""}`}><span className="aperture-mini" aria-hidden="true"><i /><b /></span><StatusMark state={state} />{label}</span>;
}

function CapabilityRow({ item, checked, onToggle, status, disabled }: { item: typeof capabilities[number]; checked: boolean; onToggle: () => void; status?: string; disabled?: boolean }) {
  const Icon = item.icon;
  const providerState = status || "UNAVAILABLE";
  return (
    <label className={`capability-row ${checked ? "is-selected" : ""} ${disabled ? "is-disabled" : ""}`}>
      <input type="checkbox" checked={checked} onChange={onToggle} disabled={disabled} />
      <span className="capability-icon"><Icon size={15} strokeWidth={1.8} /></span>
      <span className="capability-copy"><strong>{item.label}</strong><small>{item.detail}</small></span>
      <span className="capability-state"><StatusMark state={providerState} />{providerState.toLowerCase()}</span>
    </label>
  );
}

function MissionLine({ mission }: { mission: MissionSummary }) {
  const queueState = mission.queue?.status || mission.status;
  return (
    <article className="mission-line">
      <div className="mission-line-top"><span className="mono-label">{mission.mission_id.slice(0, 18)}</span><span className="state-chip"><StatusMark state={mission.status} />{mission.status}</span></div>
      <p>{queueState} · {mission.reality} · {mission.verification_status}</p>
      {!terminalStatuses.has(mission.status) && <div className="execution-meter"><span /></div>}
    </article>
  );
}

function LoginPanel({ onAuthenticated, runtimeReady, setupAvailable, registrationAvailable, healthError, onRuntimeConfigured }: { onAuthenticated: (session: ProductSession) => void; runtimeReady: boolean; setupAvailable: boolean; registrationAvailable: boolean; healthError: string | null; onRuntimeConfigured: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [runtimeUrl, setRuntimeUrl] = useState(() => getApiBase());
  const [screen, setScreen] = useState<"signin" | "setup" | "register" | "connect">(runtimeReady ? (setupAvailable ? "setup" : registrationAvailable ? "register" : "signin") : "connect");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (runtimeReady && screen === "connect" && getApiBase()) setScreen(setupAvailable ? "setup" : registrationAvailable ? "register" : "signin");
  }, [registrationAvailable, runtimeReady, screen, setupAvailable]);
  const signIn = async () => {
    setSubmitting(true); setError(null);
    try { onAuthenticated(await nexusApi.login(email, password)); }
    catch (cause) { setError(cause instanceof Error && cause.message.includes("invalid email or password") ? "No matching product account or password. Existing accounts can only be recovered locally by the product owner; browser resets are intentionally unavailable." : cause instanceof Error ? cause.message : "Authentication could not be completed."); }
    finally { setSubmitting(false); }
  };
  const setupOwner = async () => {
    setSubmitting(true); setError(null);
    try { onAuthenticated(await nexusApi.setupOwner(email, password)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "First owner setup could not be completed."); }
    finally { setSubmitting(false); }
  };
  const registerOwner = async () => {
    setSubmitting(true); setError(null);
    try { onAuthenticated(await nexusApi.registerOwner(email, password)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Owner workspace registration could not be completed."); }
    finally { setSubmitting(false); }
  };
  const connectRuntime = () => {
    setError(null);
    try { configureApiBase(runtimeUrl); onRuntimeConfigured(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Runtime URL could not be saved."); }
  };
  const isSetup = screen === "setup";
  const isRegistration = screen === "register";
  return (
    <section className="login-panel" aria-labelledby="login-title">
      <div className="login-copy"><span className="mono-label">{runtimeReady ? "PRODUCT SECURITY / REQUIRED" : "RUNTIME CONNECTION / REQUIRED"}</span><h2 id="login-title">{screen === "connect" ? "Connect your NEXUS runtime first." : isSetup ? "Create the first product owner." : isRegistration ? "Create your owner workspace." : "Authenticate before the ledger becomes visible."}</h2><p>{screen === "connect" ? "This interface is a cockpit, not a hosted API. Add the HTTPS URL of the independently running NEXUS API; no credentials are sent until the runtime responds." : isSetup ? "This one-time action is available only while the product database has no users. It creates the owner, primary project, and a scoped session." : isRegistration ? "This configured self-service path creates a new isolated tenant, owner, and primary project. It cannot access any existing NEXUS workspace." : "The command center reads tenant-scoped projects and mission receipts only after product-owned session authentication."}</p>{healthError && <p className="runtime-explanation">{healthError}</p>}</div>
      <div className="login-form">
        {screen === "connect" ? <label>Runtime API URL<input value={runtimeUrl} onChange={(event) => setRuntimeUrl(event.target.value)} type="url" autoComplete="url" placeholder="https://nexus-api.your-domain.example" onKeyDown={(event) => event.key === "Enter" && connectRuntime()} /></label> : <><label>Email<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" placeholder="owner@example.com" /></label><label>{isSetup || isRegistration ? "New password (12+ characters)" : "Password"}<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete={isSetup || isRegistration ? "new-password" : "current-password"} placeholder={isSetup || isRegistration ? "Choose a product password" : "Your product password"} onKeyDown={(event) => event.key === "Enter" && void (isSetup ? setupOwner() : isRegistration ? registerOwner() : signIn())} /></label></>}
        {error && <p className="login-error"><CircleAlert size={15} />{error}</p>}
        {screen === "connect" ? <button className="run-button" disabled={!runtimeUrl.trim()} onClick={connectRuntime}><Radar size={17} />Connect runtime</button> : <button className="run-button" disabled={submitting || !email || !password || ((isSetup || isRegistration) && password.length < 12)} onClick={() => void (isSetup ? setupOwner() : isRegistration ? registerOwner() : signIn())}>{submitting ? <LoaderCircle className="spin" size={17} /> : <KeyRound size={17} />}{submitting ? "Verifying identity" : isSetup ? "Create owner workspace" : isRegistration ? "Create isolated workspace" : "Open secure workspace"}</button>}
        {runtimeReady && screen !== "connect" && <button className="login-link" onClick={() => setScreen("connect")}>Change runtime URL</button>}
        {!runtimeReady && hasApiBaseOverride() && <button className="login-link" onClick={() => { clearApiBaseOverride(); setRuntimeUrl(getApiBase()); onRuntimeConfigured(); }}>Saved runtime URL unreachable — use default runtime URL</button>}
        {runtimeReady && !setupAvailable && !registrationAvailable && <p className="recovery-note">Owner registration is disabled for this runtime. For recovery, run the documented local owner bootstrap command on the API host; web reset is deliberately not exposed.</p>}
      </div>
    </section>
  );
}

export default function Home() {
  const [health, setHealth] = useState<ProductHealth | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [session, setSession] = useState<ProductSession | null>(() => readProductSession());
  const [activeProject, setActiveProject] = useState("");
  const [projectName, setProjectName] = useState("");
  const [intent, setIntent] = useState(defaultIntent);
  const [mode, setMode] = useState<"REAL_READ" | "SIMULATION">("SIMULATION");
  const [selected, setSelected] = useState<Capability[]>(["repository.metadata.read"]);
  const [submitting, setSubmitting] = useState(false);
  const [mission, setMission] = useState<MissionSummary | null>(null);
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [capabilityStates, setCapabilityStates] = useState<Array<{ capability: string; provider: string; risk: string; side_effects: boolean }>>([]);
  const [providerStates, setProviderStates] = useState<Record<string, ProviderState>>({});
  const [timeline, setTimeline] = useState<MissionEvent[]>([]);
  const [evidence, setEvidence] = useState<MissionEvidence[]>([]);
  const [databaseInspection, setDatabaseInspection] = useState<DatabaseInspection | null>(null);
  const [checkpointCount, setCheckpointCount] = useState(0);
  const [projectContext, setProjectContext] = useState<ProjectContext | null>(null);
  const [memoryNote, setMemoryNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("command-center");
  const [routeHash, setRouteHash] = useState(() => typeof window !== "undefined" && window.location.hash ? window.location.hash : "#mission");
  useEffect(() => {
    const onHashChange = () => setRouteHash(window.location.hash || "#mission");
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const selectedCount = selected.length;
  const latestMission = mission ?? missions[0] ?? null;
  const watchingExecution = Boolean(latestMission && !terminalStatuses.has(latestMission.status));
  const runtimeReady = Boolean(health && !healthError);

  const refresh = useCallback(async () => {
    try {
      const nextHealth = await (session ? nexusApi.authenticatedHealth() : nexusApi.health());
      setHealth(nextHealth); setHealthError(null);
      if (session && activeProject) {
        const [nextMissions, nextMemory, nextOutcomes, nextAudit, nextCapabilities, nextProviders, nextContext] = await Promise.all([
          nexusApi.listMissions(activeProject), nexusApi.listMemory(activeProject), nexusApi.listOutcomes(activeProject),
          nexusApi.listAuditEvents(activeProject), nexusApi.listCapabilities(), nexusApi.listProviders(), nexusApi.projectContext(activeProject),
        ]);
        setMissions(nextMissions.missions);
        setMemory(nextMemory.memory); setOutcomes(nextOutcomes.outcomes); setAuditEvents(nextAudit.audit_events);
        setCapabilityStates(nextCapabilities.capabilities); setProviderStates(nextProviders.providers);
        setProjectContext(nextContext);
        setMission((current) => current ? nextMissions.missions.find((item) => item.mission_id === current.mission_id) ?? current : null);
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Independent API unavailable";
      setHealth(null);
      setHealthError(message);
      if (message.includes("401")) { clearProductSession(); setSession(null); setMissions([]); setMission(null); setProjectContext(null); }
    }
  }, [activeProject, session]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (session && !activeProject && session.projects[0]) setActiveProject(session.projects[0].project_id);
  }, [activeProject, session]);
  useEffect(() => {
    if (!session || !activeProject) return;
    const interval = window.setInterval(() => { void refresh(); }, watchingExecution ? 2000 : 8000);
    return () => window.clearInterval(interval);
  }, [activeProject, refresh, session, watchingExecution]);

  useEffect(() => {
    if (!session || session.user.role !== "owner") { setDatabaseInspection(null); return; }
    void nexusApi.databaseInspection().then(setDatabaseInspection).catch(() => setDatabaseInspection(null));
  }, [session, missions.length, auditEvents.length]);

  useEffect(() => {
    if (!session || !latestMission) { setTimeline([]); setEvidence([]); return; }
    void Promise.all([nexusApi.missionEvents(latestMission.mission_id), nexusApi.missionEvidence(latestMission.mission_id)])
      .then(([eventPayload, evidencePayload]) => { setTimeline(eventPayload.events); setEvidence(evidencePayload.evidence); })
      .catch(() => { setTimeline([]); setEvidence([]); });
  }, [latestMission?.mission_id, latestMission?.updated_at, session]);

  const toggleCapability = (capability: Capability) => setSelected((current) => current.includes(capability) ? current.filter((item) => item !== capability) : [...current, capability]);
  const onAuthenticated = (nextSession: ProductSession) => { setSession(nextSession); setActiveProject(nextSession.projects[0]?.project_id || ""); setError(null); };
  const logout = async () => { await nexusApi.logout(); setSession(null); setMissions([]); setMission(null); setProjectContext(null); setActiveProject(""); };

  const queueMission = async () => {
    if (!session) { setError("Authenticate before creating a governed mission."); return; }
    if (!activeProject) { setError("Select a tenant project before creating a mission."); return; }
    if (!intent.trim() || selected.length === 0) { setError("Name an outcome and select at least one evidence capability."); return; }
    setSubmitting(true); setError(null);
    try {
      const created = await nexusApi.submitMission({ intent: intent.trim(), project_id: activeProject, scope: "Themeta-verse/Nexus", mode, capabilities: selected });
      setMission(created); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Mission could not be queued."); }
    finally { setSubmitting(false); }
  };
  const controlLatestMission = async (control: "pause" | "resume" | "cancel") => {
    if (!latestMission) return;
    setError(null);
    try { setMission(await nexusApi.controlMission(latestMission.mission_id, control)); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Mission control could not be applied."); }
  };
  const createProject = async () => {
    if (!projectName.trim()) return;
    setError(null);
    try {
      const project = await nexusApi.createProject(projectName.trim(), projectName.trim());
      setSession((current) => current ? persistProductSession({ ...current, projects: [project, ...current.projects.filter((item) => item.project_id !== project.project_id)] }) : current);
      setActiveProject(project.project_id); setProjectName("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Project could not be created."); }
  };
  const continueLatestMission = async () => {
    if (!latestMission) return;
    setError(null);
    try { const continued = await nexusApi.continueMission(latestMission.mission_id); setMission(continued.mission); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Persisted mission state could not be recovered."); }
  };
  const updateMemory = async (item: MemoryItem, action: "retire" | "restore" | "annotate") => {
    if (!activeProject) return;
    setError(null);
    try { await nexusApi.updateMemory(activeProject, item.memory_id, action, memoryNote || undefined); setMemoryNote(""); await refresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Memory lifecycle update could not be applied."); }
  };
  useEffect(() => {
    if (!session || !latestMission || terminalStatuses.has(latestMission.status)) { setCheckpointCount(0); return; }
    void nexusApi.listCheckpoints(latestMission.mission_id).then((payload) => setCheckpointCount(payload.checkpoints.length)).catch(() => setCheckpointCount(0));
  }, [latestMission?.mission_id, latestMission?.status, session]);

  if (session && viewMode === "agents") {
    return <AgentsPage session={session} onBack={() => setViewMode("command-center")} />;
  }

  if (session && viewMode === "workflows") {
    return <WorkflowsPage session={session} onBack={() => setViewMode("command-center")} />;
  }

  return (
    <main className="nexus-shell">
      <aside className="identity-rail" aria-label="NEXUS product identity and runtime state">
        <div className="rail-top"><img className="nexus-logo" src="/assets/nexus-aperture-logo.png" alt="NEXUS aperture symbol" /><div className="wordmark">NEXUS<span>IND</span></div></div>
        <div className="rail-coordinate">PRODUCT / 02</div>
        <nav className="rail-nav" aria-label="Command center sections"><a className={`rail-link ${viewMode === "command-center" && (routeHash === "#mission" || routeHash === "") ? "is-active" : ""}`} href="#mission" onClick={() => setViewMode("command-center")}><TerminalSquare size={16} />Mission desk</a><a className={`rail-link ${viewMode === "command-center" && routeHash === "#evidence" ? "is-active" : ""}`} href="#evidence"><ShieldCheck size={16} />Evidence</a><a className={`rail-link ${viewMode === "command-center" && routeHash === "#history" ? "is-active" : ""}`} href="#history"><Clock3 size={16} />Continuity</a>{session && <button className={`rail-link quiet-control ${viewMode === "agents" ? "is-active" : ""}`} onClick={() => setViewMode("agents")}><ShieldCheck size={16} />Agents</button>}
          {session && <button className={`rail-link quiet-control ${viewMode === "workflows" ? "is-active" : ""}`} onClick={() => setViewMode("workflows")}><Activity size={16} />Workflows</button>}</nav>
        <div className="rail-status"><span className="mono-label">RUNTIME</span><strong>{health?.status || "OFFLINE"}</strong><small>{session ? "Authenticated tenant scope" : "Identity required for tenant data"}</small></div>
        <div className="rail-footer">MERIDIAN / {new Date().getFullYear()}</div>
      </aside>

      <section className="main-canvas">
        <header className="topline">
          <div><span className="mono-label">INDEPENDENT COMMAND CENTER</span><p>Evidence before action. Identity before access.</p></div>
          <div className="topline-actions">
            {session ? <span className="identity-chip"><UserRound size={14} />{session.user.email}</span> : <span className="identity-chip is-locked"><KeyRound size={14} />LOCKED</span>}
            {session && <button className="quiet-control" onClick={() => void logout()}><LogOut size={15} />Sign out</button>}
            <button className="quiet-control" onClick={() => void refresh()}><Activity size={15} />Refresh state</button>
            <a className="api-link" href={`${getApiBase() || apiBase}/docs`} target="_blank" rel="noreferrer">API <ExternalLink size={14} /></a>
          </div>
        </header>

        <section className="hero-panel" id="mission">
          <img src="/assets/nexus-meridian-hero.jpg" alt="Abstract technical survey background" /><div className="hero-veil" />
          <div className="hero-content"><div className="hero-locator"><EvidenceStamp state={health?.runtime_state?.state || "OFFLINE"} label="runtime truth" /><span>PERSONAL INTELLIGENCE / 02.1</span></div><h1>What should<br />NEXUS accomplish?</h1><p>{projectContext?.current_objective || "State an objective. NEXUS will queue only authorized evidence work and preserve what it learns."}</p><div className="hero-facts"><span><StatusMark state={health?.runtime_state?.state || health?.status || "OFFLINE"} />{health?.runtime_state?.state || health?.status || "API unavailable"}</span><span>{projectContext?.continuity.active_count ?? 0} active / {projectContext?.continuity.blocker_count ?? 0} blocked</span><span>{health?.runtime_state?.reason || "runtime state not yet observed"}</span></div><div className="hero-provenance"><span>OBSERVATION SOURCE / SQLITE + CHECKPOINTS</span><span>CONSEQUENTIAL ACTION / NOT EXPOSED</span></div></div>
        </section>

        {session && <section className="cockpit-context" aria-label="Current project intelligence"><div className="section-heading"><div><div className="heading-line"><EvidenceStamp state={projectContext?.latest_mission?.verification_status || "UNKNOWN"} label="current intelligence" /><span className="mono-label">NOW / DISCOVERED / NEXT</span></div><h2>{projectContext?.current_objective || "No current objective in this project."}</h2></div><span className="selection-count">{projectContext?.continuity.mission_count ?? 0} durable missions</span></div><div className="cockpit-grid"><article><span className="mono-label">NEXUS IS DOING</span><strong>{projectContext?.active_missions.length ? projectContext.active_missions.map((item) => item.status).join(" · ") : "Awaiting an objective"}</strong><p>{watchingExecution ? "Worker state is refreshed from the authenticated API every two seconds." : "No active queue record currently requires execution."}</p></article><article><span className="mono-label">DISCOVERED</span><strong>{projectContext?.discovered.length ?? 0} persisted facts</strong><p>{projectContext?.discovered[0] ? `${projectContext.discovered[0].source} · ${projectContext.discovered[0].reality_state} · ${projectContext.discovered[0].verification_state}` : "No project evidence has been retained yet."}</p></article><article><span className="mono-label">BLOCKERS</span><strong>{projectContext?.blockers.length ? projectContext.blockers.map((item) => item.status).join(" · ") : "None observed"}</strong><p>{projectContext?.blockers[0]?.error || "Failures remain explicit and are never converted into completion."}</p></article><article><span className="mono-label">NEXT</span><strong>{projectContext?.next_action || "Authenticate and select a project."}</strong><p>Derived from durable missions, memory, outcomes, and queue state—not a client-side prediction.</p></article></div></section>}

        {!session ? <LoginPanel onAuthenticated={onAuthenticated} runtimeReady={runtimeReady} setupAvailable={Boolean(health?.initial_owner_setup_available)} registrationAvailable={Boolean(health?.owner_registration_available)} healthError={healthError} onRuntimeConfigured={() => void refresh()} /> : <section className="mission-composer" aria-labelledby="mission-title">
          <div className="section-heading"><div><div className="heading-line"><EvidenceStamp state={health?.authorization_boundary ? "AUTHENTICATED" : "UNKNOWN"} label="tenant scope" /><span className="mono-label">01 / OBJECTIVE</span></div><h2 id="mission-title">Tell NEXUS what you want accomplished.</h2></div><span className="selection-count">{selectedCount.toString().padStart(2, "0")} evidence capabilities</span></div>
          <div className="tenant-toolbar"><span className="mono-label">TENANT SCOPE</span><select value={activeProject} onChange={(event) => setActiveProject(event.target.value)} aria-label="Active tenant project">{session.projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.display_name} · {project.role}</option>)}</select>{session.user.role === "owner" && <span className="project-create"><input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="new project" aria-label="New project name" /><button onClick={() => void createProject()} disabled={!projectName.trim()}>create project</button></span>}<span className="live-indicator"><StatusMark state={watchingExecution ? "EXECUTING" : health?.status || "UNKNOWN"} />{watchingExecution ? "live worker watch / 2s" : "durable status sync / 8s"}</span></div>
          <div className="composer-grid"><div className="intent-column"><label htmlFor="mission-intent">Mission command</label><textarea id="mission-intent" value={intent} onChange={(event) => setIntent(event.target.value)} spellCheck={false} /><div className="mode-switch" role="group" aria-label="Mission mode"><button className={mode === "SIMULATION" ? "is-active" : ""} onClick={() => setMode("SIMULATION")}>Simulation</button><button className={mode === "REAL_READ" ? "is-active" : ""} onClick={() => setMode("REAL_READ")}>Real read</button></div><p className="helper-text">Submission creates a durable queue record. A separate read-only worker claims, executes, verifies, and persists the mission.</p></div>
            <div className="capabilities-column" id="evidence"><span className="column-kicker">EVIDENCE SET</span>{capabilities.map((item) => <CapabilityRow key={item.id} item={item} checked={selected.includes(item.id)} onToggle={() => toggleCapability(item.id)} status={providerStates[capabilityStates.find((state) => state.capability === item.id)?.provider || ""]?.status} />)}</div></div>
          {error && <div className="error-line"><CircleAlert size={16} />{error}</div>}
          <div className="composer-footer"><div><span className="mono-label">AUTHORIZATION</span><strong>READ-ONLY / TENANT-SCOPED / NO SIDE EFFECTS</strong></div><button className="run-button" disabled={submitting} onClick={() => void queueMission()}>{submitting ? <LoaderCircle className="spin" size={17} /> : <ArrowUpRight size={17} />}{submitting ? "Queueing mission" : "Queue governed mission"}</button></div>
        </section>}

        <section className="result-strip" aria-live="polite"><div className="result-object"><img src="/assets/nexus-verification-object.png" alt="NEXUS verification aperture seal" /><span className="seal-coordinate">V / 01</span></div><div><div className="heading-line result-heading"><EvidenceStamp state={latestMission?.status || (session ? "NOT STARTED" : "LOCKED")} label="latest mission" /><span className="mono-label">LIVE STATE</span></div><h2>{latestMission ? latestMission.status : session ? "No mission in this project." : "Workspace is locked."}</h2><p>{latestMission ? `${latestMission.queue?.status || latestMission.status} · ${latestMission.reality} · ${latestMission.verification_status} · ${latestMission.external_invocations} external calls` : session ? "Submit a command to create a durable mission." : "Sign in to reveal only your tenant-scoped durable history."}</p></div><div className="result-boundary"><EvidenceStamp state={latestMission?.action_state || "NOT STARTED"} label="action state" /><strong>{latestMission?.action_state || "NOT STARTED"}</strong><small>{watchingExecution ? "Worker execution is being observed." : "No consequential operation is exposed."}</small>{session && latestMission && latestMission.status !== "EXECUTING" && !terminalStatuses.has(latestMission.status) && <span className="mission-controls"><button onClick={() => void controlLatestMission(latestMission.status === "PAUSED" ? "resume" : "pause")}>{latestMission.status === "PAUSED" ? "resume" : "pause"}</button><button onClick={() => void controlLatestMission("cancel")}>cancel</button></span>}{session && latestMission && terminalStatuses.has(latestMission.status) && <span className="mission-controls"><button onClick={() => void continueLatestMission()}>continue from checkpoint</button></span>}</div></section>

        {session && <section className="reality-grid" aria-label="Persisted project intelligence">
          <article><div className="heading-line"><EvidenceStamp state={memory[0]?.reality_state || "UNKNOWN"} label="memory" /><span className="mono-label">PROJECT-SCOPED</span></div><strong>{memory.length}</strong><p>{memory[0] ? `${memory[0].source} · ${memory[0].confidence} · ${memory[0].reality_state}` : "No persisted observation memory yet."}</p></article>
          <article><div className="heading-line"><EvidenceStamp state={outcomes[0]?.verification_state || "UNKNOWN"} label="outcomes" /><span className="mono-label">MISSION PROJECTIONS</span></div><strong>{outcomes.length}</strong><p>{outcomes[0] ? `${outcomes[0].state} · ${outcomes[0].verification_state}` : "No persisted outcome yet."}</p></article>
          <article><div className="heading-line"><EvidenceStamp state={auditEvents.length ? "OBSERVED" : "UNKNOWN"} label="audit" /><span className="mono-label">TENANT-SCOPED</span></div><strong>{auditEvents.length}</strong><p>{auditEvents[0] ? `${auditEvents[0].action} · ${auditEvents[0].outcome}` : "No product audit event yet."}</p></article>
          <article><div className="heading-line"><EvidenceStamp state={Object.values(providerStates).some((provider) => provider.availability) ? "AVAILABLE" : "UNAVAILABLE"} label="fabric" /><span className="mono-label">CAPABILITIES / PROVIDERS</span></div><strong>{capabilityStates.length} / {Object.keys(providerStates).length}</strong><p>{capabilityStates[0] ? `${capabilityStates[0].capability} · ${capabilityStates[0].risk}` : "Provider fabric not available."} {checkpointCount ? `· ${checkpointCount} checkpoint(s)` : ""}</p></article>
        </section>}
        {session && memory.length > 0 && <section className="memory-lifecycle" aria-label="Inspectable project memory"><div className="section-heading"><div><div className="heading-line"><EvidenceStamp state="PERSISTED" label="memory control" /><span className="mono-label">MEMORY / INSPECTABLE / EDITABLE</span></div><h2>Retain evidence without making it permanent by accident.</h2></div></div><div className="memory-control-row"><input value={memoryNote} onChange={(event) => setMemoryNote(event.target.value)} placeholder="Optional operator note for this memory change" aria-label="Memory lifecycle note" /></div><div className="memory-control-grid">{memory.slice(0, 4).map((item) => <article key={item.memory_id}><span className="mono-label">{item.source} / {item.status}</span><strong>{item.reality_state} · {String(item.content.verification_state || "UNKNOWN")}</strong><p>{item.user_note || `Recorded ${item.freshness_at}`}</p><div className="mission-controls">{item.status === "active" ? <button onClick={() => void updateMemory(item, "retire")}>retire</button> : <button onClick={() => void updateMemory(item, "restore")}>restore</button>}<button onClick={() => void updateMemory(item, "annotate")}>annotate</button></div></article>)}</div></section>}
        {session && latestMission && <section className="mission-timeline" aria-label="API-derived mission timeline"><div className="section-heading"><div><div className="heading-line"><EvidenceStamp state={latestMission.verification_status} label="mission ledger" /><span className="mono-label">02 / EXECUTION TRACE</span></div><h2>Queue, evidence, and verification timeline.</h2></div><span className="selection-count">{timeline.length.toString().padStart(2, "0")} events</span></div><div className="timeline-grid">{timeline.length ? timeline.slice(-6).map((event) => <article key={event.event_id}><span className="mono-label">{event.created_at}</span><strong>{event.event_type}</strong><p>{JSON.stringify(event.payload)}</p></article>) : <p className="empty-ledger">No persisted mission events returned by the API.</p>}</div><div className="timeline-foot"><span>{evidence.length} evidence record(s)</span><span>{evidence.map((item) => item.verification_state || "UNKNOWN").join(" · ") || "NO EVIDENCE"}</span></div></section>}
      </section>

      <aside className="inspection-pane" id="history" aria-label="Evidence and continuity inspection"><div className="inspection-header"><div className="heading-line"><EvidenceStamp state={session ? "AUTHENTICATED" : "AUTH REQUIRED"} label="ledger access" /><span className="mono-label">03 / INSPECTION</span></div><h2>Reality ledger</h2></div><div className="runtime-fact"><span>Database</span><strong>{health?.database?.status || "UNAVAILABLE"}</strong><small>{databaseInspection ? `${databaseInspection.row_counts.missions} mission rows · ${databaseInspection.journal_mode.toUpperCase()} · integrity ${databaseInspection.integrity_check}` : session ? `${missions.length} visible project records` : "Tenant records hidden until sign-in"}</small></div><div className="runtime-fact"><span>Boundary</span><strong>{session ? health?.authorization_boundary || "UNKNOWN" : "AUTH REQUIRED"}</strong><small>Consequential operations are not exposed by the API.</small></div><div className="inspection-visual"><img src="/assets/nexus-evidence-pattern.jpg" alt="Inspection crop from the NEXUS evidence ledger" /><span className="artifact-tag">INSPECTION CROP / PROVENANCE LEDGER</span><span className="artifact-coordinate">SOURCE REF / 03-14</span></div><div className="history-head"><span className="mono-label">PERSISTED MISSIONS</span><span>{session ? missions.length : "—"}</span></div><div className="mission-history">{session && missions.length ? missions.slice(0, 5).map((item) => <MissionLine key={item.mission_id} mission={item} />) : <p className="empty-ledger">{session ? (healthError ? "API not connected. Start the independent runtime to read continuity." : "No durable mission records for this project yet.") : "Authenticate to view only the mission history allowed by your project membership."}</p>}</div><div className="api-note"><Radar size={15} /><span>API target<br /><code>{getApiBase() || apiBase}</code></span></div></aside>
    </main>
  );
}
