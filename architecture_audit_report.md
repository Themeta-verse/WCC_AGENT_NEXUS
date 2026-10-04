# NEXUS Architecture Audit Report
**Directive:** Architecture Reset / Restoration — "NEXUS is a general-purpose multi-agent automation and orchestration platform"
**Date:** 2026-09-26
**Mode:** Audit only — no code changes made.

---

## 1. CURRENT ARCHITECTURE

### 1.1 Repository Layout

```
E:\Nexus/
├── runtime/                    # Canonical NEXUS engine (49 modules)
│   ├── mission_composer.py     # MissionComposer — sole planner/executor/verifier
│   ├── canonical_core.py       # Core contracts + reality states
│   ├── persistent_fabric.py    # LocalStateStore + CapabilityProvider base
│   ├── action_ready.py         # Evidence normalization, reconciliation, freshness
│   ├── cognitive_os.py         # Graph views (world_model, work_graph, evidence_graph)
│   ├── canonical_pilot.py      # DirectGitHubAPIAdapter
│   ├── github_provider.py      # GitHubReadProvider
│   ├── browser_provider.py     # BrowserReadProvider
│   ├── filesystem_provider.py  # FilesystemReadProvider
│   ├── capability_registry.py  # CapabilityRegistry, CapabilityResolver
│   ├── personal_agent.py       # compile_agent_request, adversarial_content_is_data
│   ├── convergence_engine.py   # prompt_injection_defense, secret_scan
│   ├── living_loop.py          # Operating loop context, memory, continuity
│   ├── outcome_intelligence.py # Continuity projection, trajectory, opportunities
│   ├── omega2-omega10 series   # Engine variants
│   ├── transcendence_engine.py, meta_orchestrator.py, etc. (30+ more)
│   ├── agent_orchestrator.py   # NEW (PS002): Multi-agent dev orchestration
│   ├── bounded_agent.py        # NEW (PS002): Real filesystem observation
│   ├── agent_artifacts.py      # NEW (PS002): Typed artifact schemas
│   ├── model_router.py         # NEW (PS002): Provider/model routing
│   └── sandbox_runner.py       # NEW (PS002): Deferred sandbox execution
├── nexus_independent/          # Product API layer (authenticated)
│   ├── api.py                  # FastAPI — auth, missions, agents, evidence
│   ├── service.py              # StandaloneMissionService — auth, queue, workers
│   ├── database.py             # SQLite WAL schema
│   ├── schemas.py              # Pydantic schemas
│   └── cli.py                  # Bootstrap, migrate, serve, worker commands
├── frontend/
│   ├── client/src/
│   │   ├── App.tsx             # App shell (renders Home or Agents)
│   │   ├── pages/Home.tsx      # Meridian Operations Desk (35,634 bytes)
│   │   ├── pages/Agents.tsx    # NEW (PS002): Agent registry viewer (24,089 bytes)
│   │   ├── lib/nexusApi.ts     # API client (15,272 bytes)
│   │   ├── components/         # Shadcn/ui + Map.tsx visualization
│   │   └── hooks/              # useComposition, useMobile, usePersistFn
│   └── dist/                   # Build output
├── tests/                      # 70+ test/benchmark files
│   ├── omega10_mission_composer_benchmark.py    # Canonical MissionComposer tests
│   ├── ps002_agent_foundation.py                # PS002 agent registry tests
│   ├── ps002_agent_integrity.py                 # PS002 integrity evaluation tests
│   ├── ps002_agent_observation.py               # PS002 real observation tests
│   ├── agent_orchestrator_test.py               # PS002 multi-agent orchestration
│   ├── sandbox_runner_test.py
│   ├── model_router_test.py
│   └── ... (60+ more)
├── docs/                       # Architecture docs
│   ├── ASCENSION-ARCHITECTURE.md           # Mermaid diagram of product layers
│   ├── ASCENSION-FORENSIC-AUDIT.md         # Gap analysis
│   ├── INDEPENDENT-ARCHITECTURE.md         # Source dependency map
│   └── ... (7 docs total)
├── AGENTS.md                   # Project instructions
└── .kilo/
    └── worktrees/
        └── omniscient-vertebra/   # Git worktree at commit e3bdc22
```

### 1.2 Core Data Model

**MissionComposer** (`runtime/mission_composer.py:246`) produces a `package` dict containing:

| Key | Description |
|-----|-------------|
| `mission_type` | Engineered diagnosis, project audit, research, etc. (11 types)
| `intent_compilation` | Parsed intent → capability, objective, mission type |
| `context` | Scope, memory IDs, memory reuse flag |
| `mission` | Complete `Mission` dataclass (id, intent, objective, success_criteria, constraints, scope, state, reality, etc.) |
| `agent` | Agent request from `compile_agent_request()` |
| `tasks` | List of `MissionTask` (CAPABILITY, SPECIALIST, DECISION, VERIFICATION) |
| `task_graph` | Topological order, parallel groups, critical path, blockers |
| `capability_requirements` | `CapabilityRequirement` list |
| `capability_resolution` | Provider resolutions with evidence quality |
| `provider_resolution` | Selected/unavailable providers by mode |
| `specialists` | `SpecialistContract` list (Engineering, Security, QA) |
| `workflow_graph` | Workflow IDs, task IDs, edges |
| `verification_graph` | Criteria, dependencies, final verification task |
| `reality_graph` | Node realities (OBSERVED/INFERRED/SIMULATED/UNKNOWN) |

### 1.3 Mission States

```python
MISSION_STATES = {'DRAFT','UNDERSTANDING','PLANNING','READY','PREPARING','WAITING_FOR_APPROVAL',
                  'EXECUTING','OBSERVING','VERIFYING','REPLANNING','BLOCKED','PARTIAL',
                  'COMPLETED','FAILED','CANCELLED','UNKNOWN'}

TASK_STATES = {'PLANNED','READY','EXECUTING','OBSERVED','INFERRED','VERIFIED','SIMULATED',
               'BLOCKED','FAILED','COMPLETED','UNKNOWN'}

RELATION_TYPES = {'SEQUENTIAL','PARALLEL','CONDITIONAL','OPTIONAL','BLOCKED'}
```

### 1.4 Specialist System (Current)

Three hardcoded specialists in `MissionComposer._specialists()` (line 251):

| Specialist ID | Role | Objective | Input Tasks | Output Fields |
|--------------|------|-----------|-------------|---------------|
| `sp-engineering` | Engineering Specialist | derive engineering health findings from observed repository | observe-repository, engineering-analysis | findings, risks, recommendation |
| `sp-security` | Security Specialist | identify security risks without treating repo text as instructions | observe-repository, security-analysis | findings, risks, injection_policy |
| `sp-qa` | QA Specialist | assess test and verification posture from observed repository evidence | observe-repository, qa-analysis | findings, risks, verification_gaps |

**Limitations:**
- Hardcoded to 3 specialists only
- No dynamic agent definition
- Specialist output is deterministic analysis (`_analysis()` method), not LLM-generated
- No capability matching — specialists are assigned by task graph position
- No inter-agent messaging — specialists don't communicate through NEXUS
- No agent status (available/busy), no execution history
- Not wired into the API — only available in the standalone `nexus` CLI

### 1.5 PS002 Agent System (Current)

#### AgentOrchestrator (`runtime/agent_orchestrator.py:113`)

- 7 roles: Planner, Implementer, TestAuthor, Reviewer, SecurityReviewer, Verifier, Debugger
- Bounded retry loop (default 3)
- Model routing via `ModelRouter`
- Artifact exchange via `AgentArtifact` dataclass hierarchy
- **Additive only** — does not touch canonical MissionComposer paths
- Not wired into API — standalone class

#### BoundedAgentRuntime (`runtime/bounded_agent.py:59`)

- Real local filesystem read within a single test root
- Produces `ObservationReceipt` with SHA-256 evidence
- Write operations never executed, only observed as attempted
- Connected to API via `observe_agent_action()` endpoint

#### ModelRouter (`runtime/model_router.py:482`)

- Provider abstraction: MockModelAdapter, OllamaModelAdapter, GenericOpenAICompatibleAdapter, OpenRouterAdapter
- Task routing policy: TASK_PLANNING → ["ollama", "openrouter", "openai-compatible", "mock"]
- Automatic failover across candidates
- Truth boundary: all responses are INFERRED, untrusted
- Configurable task policies

#### Agent Artifacts (`runtime/agent_artifacts.py`)

Typed artifacts for inter-agent communication:
```python
AgentArtifact (base) → PlanArtifact, PatchArtifact, TestSuiteArtifact
                  → ReviewArtifact, SecurityAuditArtifact, VerificationArtifact
                  → DiagnosticArtifact, ExecutionResultArtifact
```

Each artifact has: `artifact_id`, `task_id`, `role`, `provider`, `model`, `reality` ("INFERRED"), `untrusted` (True), `parent_artifact_id`, `provenance`.

#### Agent Registry (Service Layer)

`nexus_independent/service.py` StandaloneMissionService methods (line 246-593):

| Method | Purpose |
|--------|---------|
| `create_agent()` | Register new agent with display name |
| `list_agents()` | List agents (tenant-scoped) |
| `get_agent()` | Get agent by ID |
| `create_agent_policy()` | Set declared capabilities, allowed/prohibited ops, scope |
| `get_agent_policy()` | Get current policy |
| `list_agent_policies()` | Historical policy versions |
| `record_agent_action()` | Record API-claim action with ALLOW/FLAG/HALT |
| `get_agent_actions()` | List actions |
| `get_agent_integrity()` | Get agent + policy + recent actions + integrity events |
| `observe_agent_action()` | Bridge to BoundedAgentRuntime → real observation |
| `enforce_agent()` | Manual FLAG/HALT/ACTIVE enforcement |

Database tables (in `nexus_independent/database.py`):
- `agents` — agent_id, display_name, type, project_id, status, created_at
- `agent_policies` — declared_capabilities_json, allowed_operations_json, prohibited_operations_json, scope_json, version, expected_behaviour
- `agent_actions` — action_id, agent_id, operation, target_resource, status, integrity_decision, evidence
- `agent_integrity_events` — event_id, agent_id, event_type, detail_json, integrity_decision, evaluated_at

API endpoints (in `nexus_independent/api.py:159-254`):
```
GET    /api/v1/agents
POST   /api/v1/agents                                  → create_agent
GET    /api/v1/agents/{agent_id}                       → get_agent
POST   /api/v1/agents/{agent_id}/policy                → create_agent_policy
GET    /api/v1/agents/{agent_id}/policy                → get_agent_policy
GET    /api/v1/agents/{agent_id}/policies              → list_agent_policies
POST   /api/v1/agents/{agent_id}/actions               → record_agent_action (API claim)
POST   /api/v1/agents/{agent_id}/observe               → observe_agent_action (real observation)
GET    /api/v1/agents/{agent_id}/actions               → get_agent_actions
GET    /api/v1/agents/{agent_id}/integrity             → get_agent_integrity
POST   /api/v1/agents/{agent_id}/enforce               → enforce_agent
```

Database schemas:
- `AgentCreateRequest` — agent_id (optional), project_id, display_name
- `AgentPolicyCreateRequest` — declared_capabilities, allowed_operations, prohibited_operations, scope, expected_behaviour
- `AgentActionSubmission` — operation, target_resource, requested_capability, parameters
- `AgentObservationRequest` — operation, target_resource, requested_capability, parameters, observation_root
- `AgentEnforceRequest` — action (FLAG/HALT/ACTIVE)

### 1.6 Workflow Engine (Current)

**Task graph infrastructure** exists in `MissionComposer._task_graph()` (line 198-219):
- Topological sort (Kahn's algorithm) for execution order
- Parallel groups by dependency level
- Critical path detection
- Cycle detection (returns BLOCKED)
- Missing dependency detection

**But:** The task graph is compiled into a mission package dict and executed entirely within MissionComposer.execute(). There is no separate persistent workflow engine. No API endpoint creates, starts, stops, or queries individual task graph nodes.

### 1.7 Persistence (Current)

| Storage | Scope | Content |
|---------|-------|---------|
| `LocalStateStore` (`runtime/persistent_fabric.py:127`) | Local JSON files | Canonical mission state snapshots, events, memories (idempotent) |
| `NexusDatabase` (`nexus_independent/database.py:1`) | SQLite WAL | Tenants, users, sessions, projects, mission queue, events, evidence, memory, outcomes, checkpoints, audit, agents, policies, actions |

**LocalStateStore** has: `save()`, `load()`, `append_event()`, `events()`, `reconstruct()`, `checkpoint()`, `remember()`, `memories()`, `retrieve()`, `reconcile_memory()`. Events are append-only with idempotency keys. Snapshots have checksums and schema version validation (fails closed on corruption).

**NexusDatabase** has ~50 methods covering: auth, project management, mission queue CRUD, worker lease/heartbeat, evidence storage, memory records, agent registry, policies, actions, integrity events, audit.

### 1.8 Verification (Current)

`MissionComposer` has a verification pipeline:
1. `CompletionCriteria` — success criteria statements + required task IDs
2. `CompletionEvidence` — evidence IDs, satisfied flag, reality
3. `CompletionVerification` — independent verification, status, authority

Verification checks provider verification status: `VERIFIED` if verification_state is `VERIFIED` or status is `VERIFIED`/`SUCCESS`.

### 1.9 Frontend (Current)

```
App.tsx → Home (Meridian Operations Desk)
        → Agents (PS002 Phase 1 — agent registry viewer)

Home.tsx shows:
- Mission queue (polling, 5s)
- Evidence timeline
- Memory/outcome/audit summaries
- Provider health
- Project selection
- Authentication modal

Agents.tsx shows:
- Agent registry table (list_agents)
- Policy viewer (view only)
- No actions, no verdicts, no metrics (Phase 1)
```

**No workflow graph visualization exists.** No agent collaboration view. No live execution monitoring. No artifact handoff view.

### 1.10 Existing Tests

| Test File | Lines | Status |
|-----------|-------|--------|
| `omega10_mission_composer_benchmark.py` | 67 | ✅ Full integration (real GitHub read + simulation) |
| `ps002_agent_foundation.py` | ~8.4KB | ✅ Agent registry CRUD |
| `ps002_agent_integrity.py` | ~10KB | ✅ ALLOW/FLAG/HALT scenarios |
| `ps002_agent_observation.py` | ~12KB | ✅ Real filesystem observation |
| `agent_orchestrator_test.py` | ~14.6KB | ✅ Multi-agent orchestration |
| `sandbox_runner_test.py` | ~11.4KB | ✅ Sandbox execution |
| `model_router_test.py` | ~8.3KB | ✅ Provider routing + failover |
| `agent_artifacts_test.py` | ~11KB | ✅ Artifact schemas + truth boundary |
| `final_transition_e2e.py` | ~5KB | ✅ Authenticated API → worker → SQLite → recovery |
| ... 60+ more benchmarks | | |

---

## 2. TARGET NEXUS ARCHITECTURE

### 2.1 Vision

```
                    NEXUS
         General-Purpose Multi-Agent Orchestration Platform
                    │
       ┌────────────┼────────────┬────────────┐
       │            │            │            │
     Agent A     Agent B      Agent C    ...  Agent N
       │            │            │            │
       └────────────┼────────────┼────────────┘
                    ▼
              Workflow Engine
         (task graph, state, routing)
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
    Artifact Fabric        Evidence Store
  (provenance, hashes)   (verification, receipts)
         │                     │
         ▼                     ▼
    Persistent Fabric (SQLite WAL)
                    │
                    ▼
          Human Control Layer
    (create, start, stop, inspect, approve)
```

And externally:
```
                    NEXUS
                      │
              Runtime events /
             structured output
                      │
                      ▼
                 GuardDog
              (security/integrity)
```

### 2.2 Core Components

```
┌─────────────────────────────────────────────────────────┐
│                    NEXUS Platform                        │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Workflow Engine                                          │
│  - WorkflowGraph (nodes, edges, dependencies)            │
│  - WorkflowExecutor (dispatch, retry, state tracking)     │
│  - Task matching (capability → available agent)           │
│  - Verification gates (before state transition)            │
│                                                         │
│  Agent Manager                                           │
│  - Agent registry (dynamic, not hardcoded)                │
│  - Agent lifecycle (register, status, capabilities)      │
│  - Model/provider routing per agent                       │
│                                                         │
│  Artifact Fabric                                         │
│  - Artifact store (files, JSON, reports, code)           │
│  - Provenance tracking (created_by, task_id, hashes)     │
│  - Handoff protocol (task completion → artifact → next)  │
│                                                         │
│  Evidence Store                                          │
│  - ExecutionReceipts                                      │
│  - Observations (provider responses with verification)    │
│  - Integrity events                                       │
│  - Verification results                                   │
│                                                         │
│  Communication Layer                                     │
│  - Messages between agents                              │
│  - Message routing through NEXUS                          │
│  - Message persistence (audit trail)                     │
│                                                         │
│  Persistent Fabric (SQLite WAL)                          │
│  - Workflows, Tasks, Agents, Artifacts, Messages          │
│  - Evidence, Receipts, Verification                       │
│  - Audit trail                                            │
│                                                         │
│  Human Control                                           │
│  - Authenticated API                                      │
│  - CLI / Web UI                                           │
│  - Start/stop/pause/resume workflows                     │
│  - Inspect state, artifacts, agents                       │
│  - Approve sensitive transitions                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 2.3 Workflow Graph Model

```
User Goal → Workflow Compilation → Task Graph

Task Graph:
  Nodes (Tasks):
    - id, name, type, role
    - required_capabilities
    - dependencies (parent task IDs)
    - parallelism (SEQUENTIAL, PARALLEL, CONDITIONAL)
    - retry_policy (max_attempts, backoff)
    - verification_gate (criteria before state transition)
    - assigned_agent (None = capability matching)
    - state (PENDING, READY, RUNNING, WAITING, COMPLETED, FAILED, BLOCKED, CANCELLED)
    - reality (OBSERVED, INFERRED, SIMULATED, UNKNOWN)

  Edges:
    - source_task_id → target_task_id
    - relation (DEPENDENCY, HANDOFF, VERIFICATION)
    - artifact_handoff (artifact_id transferred between tasks)

  Workflow State:
    - state (DRAFT, PLANNING, READY, EXECUTING, PAUSED, COMPLETED, FAILED, BLOCKED, CANCELLED)
    - current_phase (PLANNING, EXECUTING, VERIFYING, RECOVERY, COMPLETED)
    - start_time, end_time
    - assigned_agents (task_id → agent_id)
    - recovery_info (last_completed_task, failed_task, retry_count)
```

### 2.4 Agent Model

```
Agent:
  - agent_id (UUID)
  - name (human-readable)
  - role (function/purpose: researcher, engineer, reviewer, etc.)
  - provider/model (which LLM backend)
  - capabilities (list: filesystem.read, github.read, code.generate, test.write, etc.)
  - constraints (scope limits, prohibited operations, resource limits)
  - status (ACTIVE, FLAGGED, HALTED, RETIRED, BUSY, AVAILABLE)
  - current_task (task_id or None)
  - execution_history (list of task_id + outcome)
  - declared_at, last_active
```

Agents are **dynamically definable** via API. Capabilities are matched to task requirements.

### 2.5 Capability Matching

```
Task.requires_capabilities = [capability_name, ...]
Agent.declared_capabilities = [capability_name, ...]

WorkflowEngine.assign_task(task):
  available_agents = [a for a in agents if a.status in (ACTIVE, AVAILABLE) 
                      and a.current_task is None
                      and set(task.requires_capabilities) ⊆ set(a.declared_capabilities)]
  best_agent = select_by_priority(available_agents)
  return best_agent
```

### 2.6 Artifact Handoff

```
Agent A completes Task_1:
  → Produces Artifact (files, report, code)
  → Artifact stored in ArtifactFabric with provenance:
    {created_by: agent_A_id, task_id: task_1, workflow_id, content_sha256, parent_artifacts: []}
  → Artifact handed off to Task_2:
    Task_2.input_artifacts = [artifact_1_id]
  → Agent B assigned to Task_2:
    receives: original objective + artifact_1 + relevant evidence + task constraints
```

### 2.7 Truth Boundary (Preserved)

- All model/LLM outputs are `INFERRED` and `untrusted` (preserved from `ModelRouter`)
- Provider observations are `OBSERVED` (from real GitHub/browser/filesystem reads)
- Agent claims cannot constitute verification
- Independent verification gates before state transitions
- No writes without explicit authorization
- All content hashes for provenance verification

---

## 3. WHAT WE KEEP

### 3.1 Core Engine (No Changes Needed)

These components are the foundation of the general orchestration platform and should be preserved as-is:

| Component | File | Why Keep |
|-----------|------|----------|
| `MissionComposer` | `runtime/mission_consumer.py:246` | Core planning/execution/verification — the soul of NEXUS |
| `canonical_core.py` | Reality states, contracts | Provides `OBSERVED`/`INFERRED`/`SIMULATED`/`UNKNOWN` semantics, governance, capability contracts |
| `persistent_fabric.py` | LocalStateStore, CapabilityProvider, ExecutionReceipt, MemoryItem | Provides durable events, checkpoints, capability contracts, atomic JSON snapshots |
| `action_ready.py` | Evidence normalization, reconciliation, freshness, evidence_gate | Provides `normalize_observation`, `reconcile_sources`, `classify_freshness`, `content_digest` |
| `capability_registry.py` | CapabilityRegistry, CapabilityRecord, CapabilityResolver | Provides evidence-backed capability discovery, health, and resolution |
| `canonical_pilot.py` | DirectGitHubAPIAdapter | Read-only GitHub REST transport with verification |
| `github_provider.py` | GitHubReadProvider | Repository read capability |
| `browser_provider.py` | BrowserReadProvider | Browser page read capability |
| `filesystem_provider.py` | FilesystemReadProvider | Bounded filesystem read capability |
| `personal_agent.py` | compile_agent_request, adversarial_content_is_data | Intent compilation + prompt injection defense |
| `convergence_engine.py` | prompt_injection_defense, secret_scan | Security scanning |
| `cognitive_os.py` | Graph views (world_model, work_graph, evidence_graph, reality_graph) | Already provides workflow/evidence/reality graph visualizations |
| `outcome_intelligence.py` | continuity_projection, trajectory, bottleneck_analysis, opportunity_graph | Continuity and decision support |
| `living_loop.py` | context_package, project_state, knowledge_graph, explain | Operating loop integration |
| `local_control.py` | command_center, capability_ceiling, provider_health, doctor, self_test | CLI command center |

### 3.2 Existing Specialist System

The three specialists (`sp-engineering`, `sp-security`, `sp-qa`) in `MissionComposer._specialists()` represent a real multi-agent model — they are the **first concrete implementation** of specialized agent roles with:
- Defined roles and objectives
- Allowed capabilities
- Input task dependencies
- Expected output schemas
- Verification approaches

**Keep these as reference implementations** for how NEXUS specialists work. They demonstrate the pattern that a general agent system must generalize.

### 3.3 Provider Abstraction

| Component | File |
|-----------|------|
| ModelProvider ABC (abstract) | `runtime/model_router.py:88` |
| MockModelAdapter | `runtime/model_router.py:112` |
| OllamaModelAdapter | `runtime/model_router.py:199` |
| GenericOpenAICompatibleAdapter | `runtime/model_router.py:320` |
| OpenRouterAdapter | `runtime/model_router.py:450` |
| ModelRouter | `runtime/model_router.py:482` |

These provide a complete provider abstraction that can be reused for agent model routing. The truth boundary (`reality=INFERRED`, `untrusted=True`) is enforced.

### 3.4 Artifact System

`runtime/agent_artifacts.py` already provides typed artifacts:
- `AgentArtifact` base with provenance, identity, reality, untrusted flag
- `PlanArtifact`, `PatchArtifact`, `TestSuiteArtifact`, `ReviewArtifact`
- `SecurityAuditArtifact`, `VerificationArtifact`, `DiagnosticArtifact`
- `ExecutionResultArtifact`
- Role constants: `ROLE_PLANNER`, `ROLE_IMPLEMENTER`, `ROLE_TEST_AUTHOR`, etc.

**Keep and generalize** — these artifact types are reusable for any agent collaboration.

### 3.5 BoundedAgentRuntime

`runtime/bounded_agent.py` provides:
- Real local filesystem observation (actual file reads)
- `ObservationReceipt` with SHA-256 evidence
- Write blocking (never executes writes, only observes attempts)

**Keep and generalize** — this is the reference implementation for how a NEXUS agent should observe reality.

### 3.6 AgentOrchestrator

`runtime/agent_orchestrator.py` provides a concrete multi-agent orchestration pattern:
- Planner → Implementer → TestAuthor → Reviewer → SecurityReviewer → Verifier → Debugger
- Bounded retry loop
- Independent verification gate
- Artifact exchange between roles

**Keep as reference** — this is a domain-specific workflow (code development) that demonstrates the pattern. The general workflow engine should support arbitrary role graphs.

### 3.7 Agent Registry (PS002)

`nexus_independent/database.py` tables and `service.py` methods for agent registry, policies, actions, integrity events.

**Keep and generalize** — the registry, policies, action recording, and integrity evaluation are useful general infrastructure. PS002-specific ALLOW/FLAG/HALT enforcement logic stays modular.

### 3.8 Persistence Architecture

The dual-persistence model is sound:
- `LocalStateStore` (JSON) = canonical mission checkpoints + recovery format
- `NexusDatabase` (SQLite) = product state (tenants, queue, projections, audit)

**Keep both** — the LocalStateStore protocol should be preserved for canonical state, while SQLite provides durable product state.

---

## 4. WHAT WE REFACTOR

### 4.1 MissionComposer Integration

Currently, `MissionComposer.compose()` builds a task graph and `execute()` runs it in one call. For a general orchestration platform, NEXUS needs:

1. **Separate planning from execution** — `compose()` should produce a persistable workflow spec
2. **Persistent task graph** — tasks should be stored and queryable, not just an in-memory dict
3. **Task-level state transitions** — each task node should have independent state, persistable
4. **Agent assignment** — tasks should be assignable to dynamically registered agents, not hardcoded specialists

**Refactor:**
- Extract workflow/task graph persistence into a `WorkflowStore` or extend `NexusDatabase`
- Add task-level state machine (PENDING → READY → RUNNING → COMPLETED/FAILED/BLOCKED)
- Keep MissionComposer as the **planner + verifier** — it already does this well
- Add a `WorkflowExecutor` layer that orchestrates between MissionComposer output and agent execution

### 4.2 Specialist System Generalization

Currently, `_specialists()` returns 3 hardcoded `SpecialistContract` objects. The directive requires dynamic agents.

**Refactor:**
- Move specialist contracts to a dynamic registry (already partially done via `agents` table in PS002)
- Generalize `SpecialistContract` to `AgentSpecification` — any agent with a role, capabilities, constraints, and provider/model
- The MissionComposer's `_analysis()` method (deterministic specialist output) should be replaced by a pluggable "agent executor" that can run any agent (LLM-based, tool-based, or deterministic)
- Keep the 3 original specialists as **seed/default agents**

### 4.3 Workflow Engine Extraction

Currently, task graph compilation happens inline in `MissionComposer.compose()`. The `_task_graph()` function (line 198) is a topological sort utility that belongs in a dedicated workflow engine.

**Refactor:**
- Create a `WorkflowEngine` class that manages the lifecycle of workflow graphs
- It should: create workflows from compiled specs, dispatch tasks to available agents, track state, handle retries, coordinate handoffs
- Keep `MissionComposer` as the **planner** (produces workflow specs) and **verifier** (independent verification)
- The `WorkflowEngine` orchestrates execution between planning and verification

### 4.4 Artifact Handoff Protocol

`agent_artifacts.py` defines typed artifacts for the AgentOrchestrator, but there's no general artifact handoff protocol.

**Refactor:**
- Create a `ArtifactFabric` class that stores artifacts with full provenance
- Standardize the handoff: `task_complete → artifact_created → artifact_stored → next_task_notified`
- Use the existing `content_digest()` / `stable_value()` from `action_ready.py` for content hashing
- Use the existing `normalize_observation()` pattern for artifact provenance

### 4.5 Communication Layer

Agents currently don't communicate through NEXUS. The AgentOrchestrator exchanges artifacts directly.

**Refactor:**
- Create a `MessagingHub` class that routes messages between agents through NEXUS
- Messages reference artifacts and tasks
- All messages persisted for audit
- Use existing `Event` dataclass from `persistent_fabric.py`

### 4.6 API Surface Expansion

Currently, the API only has mission submission (async queue) and PS002 agent endpoints. The directive requires full workflow control.

**Refactor:**
- Add workflow management endpoints: `/api/v1/workflows`, `/api/v1/workflows/{id}/tasks`, etc.
- Add agent lifecycle: dynamic registration, status, capability declaration
- Add artifact endpoints: `/api/v1/artifacts/{id}`
- Add message endpoints: `/api/v1/messages`
- Add human control: `/api/v1/workflows/{id}/pause`, `resume`, `stop`, `cancel`
- Refactor PS002 agent endpoints to be under the general agent API

### 4.7 Task Graph Representation

Currently, `_task_graph()` returns a flat dict with order/parallel_groups. For a persistent workflow engine, tasks need individual state and identity.

**Refactor:**
- Create `WorkflowNode` and `WorkflowEdge` dataclasses (can reuse/extend `MissionTask`)
- Store nodes/edges in SQLite as first-class records
- Add task state machine transitions with audit
- Support conditional edges (edge fires only if task result meets criteria)

### 4.8 Evidence System Integration

The `action_ready.py` evidence functions (`normalize_observation`, `reconcile_sources`, `evidence_gate`) are excellent but only used within MissionComposer. They should be available to the general workflow engine.

**Refactor:**
- Extract evidence utilities into a `evidence_store` or `EvidenceFabric` class
- Make them callable from any workflow task
- Integrate with the artifact provenance system

---

## 5. WHAT WE ADD

### 5.1 Workflow Engine

**New module:** `runtime/workflow_engine.py`

```python
class WorkflowEngine:
    def __init__(self, database, composer, agent_registry):
        self.db = database
        self.composer = composer  # MissionComposer for planning + verification
        self.agents = agent_registry  # Dynamic agent registry
    
    def create_workflow(self, goal: str, scope: str, task_specs: list[TaskSpec]) -> str:
        """Compile a goal into a workflow graph and persist it."""
    
    def start_workflow(self, workflow_id: str) -> None:
        """Begin workflow execution."""
    
    def pause_workflow(self, workflow_id: str) -> None:
        """Pause workflow execution."""
    
    def resume_workflow(self, workflow_id: str) -> None:
        """Resume a paused workflow."""
    
    def cancel_workflow(self, workflow_id: str) -> None:
        """Cancel workflow execution."""
    
    def claim_next_task(self, agent_id: str) -> WorkflowTask | None:
        """Agent claims the next available task (capability matching)."""
    
    def complete_task(self, task_id: str, result: TaskResult) -> None:
        """Mark task complete, dispatch handoff, trigger next tasks."""
    
    def fail_task(self, task_id: str, error: str) -> None:
        """Mark task failed, attempt retry or fail workflow."""
    
    def get_workflow_state(self, workflow_id: str) -> WorkflowState:
        """Get current execution state."""
    
    def recover_workflow(self, workflow_id: str) -> None:
        """Recover workflow from persisted state after restart."""
```

**New dataclasses** (extend `MissionTask`):

```python
@dataclass
class WorkflowNode:
    node_id: str
    workflow_id: str
    task_id: str
    name: str
    kind: str           # ANALYSIS, IMPLEMENTATION, VERIFICATION, etc.
    required_capabilities: list[str]
    depends_on: list[str]       # parent node IDs
    assigned_agent: str | None   # agent_id
    state: str           # PENDING, READY, RUNNING, COMPLETED, FAILED, BLOCKED, CANCELLED
    reality: str          # OBSERVED, INFERRED, SIMULATED, UNKNOWN
    retry_count: int = 0
    max_retries: int = 3
    backoff_seconds: float = 0
    verification_gates: list[str] = field(default_factory=list)
    input_artifacts: list[str] = field(default_factory=list)  # artifact IDs
    output_artifacts: list[str] = field(default_factory=list)
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: dict = field(default_factory=dict)
    provenance: list[str] = field(default_factory=list)

@dataclass
class WorkflowEdge:
    edge_id: str
    workflow_id: str
    source_node_id: str
    target_node_id: str
    relation: str       # DEPENDENCY, HANDOFF, VERIFICATION
    artifact_handoff: str | None = None  # artifact_id transferred
    condition: str | None = None  # conditional transition
    provenance: list[str] = field(default_factory=list)

@dataclass
class Workflow:
    workflow_id: str
    name: str
    goal: str
    objective: str
    scope: str
    state: str          # DRAFT, PLANNING, READY, EXECUTING, PAUSED, COMPLETED, FAILED, BLOCKED, CANCELLED
    current_phase: str   # PLANNING, EXECUTING, VERIFYING, RECOVERY, COMPLETED
    task_nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    assigned_agents: dict = field(default_factory=dict)  # task_id → agent_id
    recovery_info: dict = field(default_factory=dict)
```

### 5.2 Dynamic Agent Registry

Extend the PS002 agent registry into a general-purpose system:

```python
# Extend existing Agent model:
Agent:
  + capabilities (list — already exists as declared_capabilities)
  + model/provider (new)
  + status: AVAILABLE | BUSY | OFFLINE (extend existing ACTIVE/FLAGGED/HALTED/RETIRED)
  + current_task (new)
  + execution_history (new)
  + last_active (new)
  + tools (new — what tools this agent can use)
```

### 5.3 Artifact Fabric

**New module:** `runtime/artifact_fabric.py`

```python
@dataclass
class Artifact:
    artifact_id: str
    workflow_id: str
    task_id: str
    agent_id: str
    kind: str              # FILE, REPORT, CODE, JSON, SCREENSHOT, etc.
    name: str
    content_digest: str    # SHA-256
    content_path: str | None  # path or URI
    content_size: int | None
    content_preview: str | None
    parent_artifacts: list[str]
    created_at: str
    created_by: str
    reality: str           # INFERRED (model), OBSERVED (real), etc.
    untrusted: bool        # True for model-generated
    verification_state: str
    provenance: list[str]

class ArtifactFabric:
    def store(self, artifact: Artifact) -> str:
        """Store artifact with provenance and return ID."""
    
    def retrieve(self, artifact_id: str) -> Artifact:
        """Retrieve artifact by ID."""
    
    def handoff(self, source_task_id, target_task_id, artifact_id) -> None:
        """Transfer artifact between tasks."""
    
    def verify(self, artifact_id: str) -> bool:
        """Verify artifact integrity (content hash)."""
    
    def lineage(self, artifact_id: str) -> list[str]:
        """Get provenance chain for an artifact."""
```

### 5.4 Communication Layer

**New module:** `runtime/messaging_hub.py`

```python
@dataclass
class Message:
    message_id: str
    workflow_id: str
    task_id: str
    from_agent: str
    to_agent: str
    message_type: str     # INFO, REQUEST, RESPONSE, HANDOFF, COMPLETE, ERROR
    content: dict
    artifact_refs: list[str]
    timestamp: str
    reality: str          # INFERRED
    untrusted: bool       # True
    provenance: list[str]

class MessagingHub:
    def send(self, message: Message) -> None:
        """Route message between agents."""
    
    def deliver(self, agent_id: str, messages: list[Message]) -> None:
        """Deliver pending messages to an agent."""
    
    def thread(self, workflow_id: str, task_id: str) -> list[Message]:
        """Get message thread for a task."""
```

### 5.5 Extended Task Graph Features

- **Conditional transitions:** Edge fires only if task result meets criteria
- **Failed-task recovery:** Automatic retry with backoff, then escalate or fail
- **Task reassignment:** Reassign failed/blocked tasks to different agents
- **Verification gates:** Task completion requires independent verification before state transition
- **Parallel execution:** Multiple READY tasks dispatched simultaneously
- **Wait conditions:** Task waits for another task or external event

### 5.6 Workflow Templates

Predefined workflow patterns:
- **Code Development:** Research → Plan → Implement → Test → Review → Verify → Deploy
- **Repository Analysis:** Observe → Engineering Analysis → Security Analysis → QA Analysis → Recommendation → Verify
- **Content Creation:** Research → Draft → Review → Revise → Final
- **Investigation:** Explore → Hypothesize → Test → Analyze → Conclude

Templates should be dynamically composable by the user.

### 5.7 CLI Extensions

Extend the existing `nexus` standalone CLI:

```
nexus workflow create "Build a portfolio website" --template code_development
nexus workflow start <workflow_id>
nexus workflow pause <workflow_id>
nexus workflow resume <workflow_id>
nexus workflow stop <workflow_id>
nexus workflow status <workflow_id>
nexus agents register --name "research-agent" --capabilities repository.read,knowledge.read
nexus tasks list --workflow <workflow_id>
nexus artifacts list --workflow <workflow_id>
nexus messages list --workflow <workflow_id>
```

### 5.8 UI Extensions

The frontend needs:
- **Workflow graph visualization** (reuse `cognitive_os.work_graph()` pattern)
- **Agent status panel** (live agent states: available, busy, offline)
- **Task list** with state transitions and assignment
- **Artifact browser** with provenance chain view
- **Message timeline** for agent communication
- **Evidence panel** showing verification results

The existing `Map.tsx` visualization already shows graph topology — extend this pattern.

---

## 6. WHAT WE ISOLATE AS PS002-SPECIFIC

### 6.1 PS002-Only Logic to Isolate

| PS002 Concept | File | Isolate As |
|---------------|------|-----------|
| `evaluate_integrity()` | `nexus_independent/service.py:50` | External security/integrity service (GuardDog) |
| ALLOW/FLAG/HALT decisions | `nexus_independent/service.py:50-119` | Move to external integrity layer |
| `enforce_agent()` (manual FLAG/HALT/ACTIVE) | `nexus_independent/service.py:572` | PS002-specific enforcement — keep modular |
| `observe_agent_action()` (real filesystem) | `nexus_independent/service.py:428` | Useful general observation tool — keep as bounded_agent |
| Agent integrity events | Database tables | Move to external event stream |
| `AgentObservationRequest.observation_root` | `schemas.py:71` | Keep (bounded observation is general) |

### 6.2 What Stays as General Infrastructure

| Component | Reason |
|-----------|--------|
| Agent registry (`agents` table) | General agent management |
| Agent identity (`agent_id`, `display_name`) | Fundamental to NEXUS |
| Agent capabilities (`declared_capabilities`) | Core to capability matching |
| Agent policies (`agent_policies` table) | General constraint management |
| Agent status (ACTIVE/FLAGGED/HALTED/RETIRED) | Keep HALTED as a state, generalize |
| Agent action records (`agent_actions` table) | Execution history is general |
| `BoundedAgentRuntime` | Real observation is useful for any agent |
| `ModelRouter` | Provider abstraction is essential |
| `AgentArtifact` hierarchy | Artifact types are reusable |
| `AgentOrchestrator` | Reference implementation for agent collaboration patterns |

### 6.3 PS002 Isolation Strategy

1. Move `evaluate_integrity()` to a new module: `runtime/integrity_engine.py` — but keep it optional/importable
2. Make ALLOW/FLAG/HALT decisions optional — the workflow engine emits events, an external GuardDog consumes them
3. Keep the database tables for agents, policies, and actions — these are general infrastructure
4. Add an `event_stream` concept: NEXUS emits structured runtime events that GuardDog (external) can observe
5. The `observe_agent_action()` endpoint stays as a bounded observation tool, not as a security control

---

## 7. MIGRATION PLAN

### Phase 0: Audit (Current — This Report)
- ✅ Git history inspection
- ✅ Worktree comparison
- ✅ Source code mapping
- ✅ Component analysis

### Phase 1: Workflow Engine Foundation (Week 1-2)

1. **Create `runtime/workflow_engine.py`** — WorkflowEngine class stub
2. **Extend database schema** — `workflows`, `workflow_tasks`, `workflow_edges`, `workflow_messages` tables
3. **Add workflow API endpoints** — CRUD for workflows, task state transitions
4. **Create `WorkflowNode`/`WorkflowEdge`/`Workflow` dataclasses** — extend existing MissionTask
5. **Wire MissionComposer as planner** — `WorkflowEngine.plan()` calls `MissionComposer.compose()`

**Goal:** Create a workflow from a user goal, persist it, and list it via API.

### Phase 2: Agent Integration (Week 2-3)

1. **Generalize agent registry** — extend PS002 agents to support model/provider, current_task, execution_history
2. **Add agent availability tracking** — AVAILABLE/BUSY/OFFLINE states
3. **Implement capability matching** — Task.requires_capabilities → available agent selection
4. **Create agent executor** — pluggable agent execution (LLM-based, tool-based, deterministic)
5. **Refactor AgentOrchestrator** — make it a default agent executor, not the only one

**Goal:** Dynamically register an agent with capabilities, have the workflow engine assign tasks to it.

### Phase 3: Artifact & Artifact Handoff (Week 3-4)

1. **Create `runtime/artifact_fabric.py`** — Artifact storage with provenance
2. **Extend `AgentArtifact`** — add workflow_id, task_id, content_digest
3. **Implement artifact handoff protocol** — task_complete → store artifact → next_task gets artifact
4. **Add artifact API endpoints** — store/retrieve/list artifacts
5. **Connect to evidence system** — use `content_digest()` from `action_ready.py`

**Goal:** Agent A completes a task, produces an artifact, artifact is handed to Agent B for the next task.

### Phase 4: Communication Layer (Week 4-5)

1. **Create `runtime/messaging_hub.py`** — Message routing between agents
2. **Add message persistence** — all messages stored in SQLite
3. **Implement message delivery** — agents receive messages via the hub
4. **Add messaging API** — send/list messages for a workflow/task
5. **Integrate with UI** — message timeline view

**Goal:** Agents communicate through NEXUS, messages are persisted and queryable.

### Phase 5: Live Execution & UI (Week 5-6)

1. **Implement WorkflowExecutor.run()** — dispatch tasks, track state, handle retries
2. **Add workflow lifecycle endpoints** — start/pause/resume/stop/cancel
3. **Create CLI commands** — `nexus workflow create/start/pause/resume/status`
4. **Extend frontend** — workflow graph visualization, agent panel, task list, artifact browser
5. **Add evidence/integrity emission** — structured events for external observation

**Goal:** End-to-end live demo: user defines a workflow, agents execute, user sees live progress.

### Phase 6: Live Demo (Week 6-7)

1. **Create demo workflow template** — "Build a portfolio website"
2. **Seed default agents** — research-agent, architecture-agent, engineering-agent, qa-agent
3. **Run end-to-end** — verify artifacts handoff, messages, state persistence
4. **Process restart** — verify workflow recovery from persisted state
5. **Document live demo** — instructions for running the canonical demo

**Goal:** Compelling live demonstration matching the directive's example.

### Phase 7: PS002 Separation (Concurrent throughout)

1. **Extract security logic** — move `evaluate_integrity()` to optional importable module
2. **Add event stream** — emit structured runtime events for external GuardDog observation
3. **Keep PS002 endpoints** — but mark them as PS002-specific (not core NEXUS)
4. **Add GuardDog adapter** — placeholder for external security observation

**Goal:** NEXUS emits enough structured information for an external GuardDog to observe it.

---

## Appendices

### Appendix A: Key File Locations

| Concept | File:Line |
|---------|-----------|
| MissionComposer class | `runtime/mission_consumer.py:246` |
| `_specialists()` | `runtime/mission_consumer.py:251` |
| `SpecialistContract` | `runtime/mission_consumer.py:112` |
| `MissionTask` | `runtime/mission_consumer.py:94` |
| `Mission` | `runtime/mission_consumer.py:163` |
| `_task_graph()` | `runtime/mission_consumer.py:198` |
| `compose()` | `runtime/mission_consumer.py:257` |
| `execute()` | `runtime/mission_consumer.py:299` |
| `REALITY_STATES` | `runtime/canonical_core.py:13` |
| `LocalStateStore` | `runtime/persistent_fabric.py:127` |
| `CapabilityProvider` | `runtime/persistent_fabric.py:201` |
| `ExecutionReceipt` | `runtime/persistent_fabric.py:93` |
| `normalize_observation` | `runtime/action_ready.py:72` |
| `reconcile_sources` | `runtime/action_ready.py:106` |
| `evidence_gate` | `runtime/action_ready.py:149` |
| `Content digest` | `runtime/action_ready.py:46` |
| `AgentOrchestrator` | `runtime/agent_orchestrator.py:113` |
| `BoundedAgentRuntime` | `runtime/bounded_agent.py:59` |
| `ObservationReceipt` | `runtime/bounded_agent.py:38` |
| `ModelRouter` | `runtime/model_router.py:482` |
| `AgentArtifact` | `runtime/agent_artifacts.py:58` |
| `StandaloneMissionService` | `nexus_independent/service.py:122` |
| `evaluate_integrity()` | `nexus_independent/service.py:50` |
| `record_agent_action()` | `nexus_independent/service.py:312` |
| `observe_agent_action()` | `nexus_independent/service.py:428` |
| Agent API endpoints | `nexus_independent/api.py:159-254` |
| CLI entry point | `nexus_independent/cli.py` (inferred from docs) |

### Appendix B: Capability Contracts

From `runtime/mission_consumer.py:64-65`:

```python
capability_contracts() = {
    'repository.read': {
        operations: ['READ', 'VERIFY'],
        authorization: 'CONFIRMED_READ_ONLY',
        risk: 'LOW_READ_ONLY',
        side_effects: False,
        verification: 'independent RepositoryObservation comparison',
        provider: 'github-read',
        health: 'VERIFIED',
    },
    'browser.read': {
        operations: ['READ', 'VERIFY'],
        authorization: 'CONFIRMED_BROWSER_READ',
        risk: 'LOW_READ_ONLY',
        side_effects: False,
        provider: 'browser-read',
    },
    'filesystem.read': {
        operations: ['READ', 'VERIFY'],
        authorization: 'CONFIRMED_LOCAL_READ',
        risk: 'LOW_LOCAL_READ',
        provider: 'filesystem-read',
    },
    'local.analysis': {
        operations: ['ANALYZE', 'VERIFY'],
        authorization: 'LOCAL_RUNTIME',
        risk: 'LOW',
    },
}
```

### Appendix C: Data Flow

```
User Goal → MissionComposer.compose()
            → Task Graph (CAPABILITY → SPECIALIST → DECISION → VERIFICATION)
            → CapabilityResolver.resolve()
            → Provider resolution (GitHubReadProvider, FilesystemReadProvider, etc.)
            
MissionComposer.execute()
            → Provider.invoke_health/request → CapabilityResponse
            → Specialist analysis (_analysis() for each specialist)
            → Decision record
            → CompletionVerification
            → LocalStateStore save() (atomic JSON snapshot)
            → Events (append-only, idempotent)
            → Checkpoint (verified snapshot)
            
PersistentFabric (LocalStateStore)
            ← snapshots: current.json (checksummed, schema-versioned)
            ← events: events.jsonl (append-only, idempotent)
            ← memory: memory.jsonl (append-only, retrievable, reconcilable)
            
Product Layer (NexusDatabase / SQLite WAL)
            ← tenants, users, sessions, projects, memberships
            ← mission queue (durable, lease-based)
            ← evidence, observations, receipts
            ← memory records, outcomes, checkpoints
            ← audit events
            ← agents, policies, actions, integrity events
```

### Appendix D: Test Inventory

**Core NEXUS tests (preserve):**
- `tests/omega10_mission_composer_benchmark.py` — full MissionComposer integration (real GitHub + simulation)
- `tests/litiving_intelligence_benchmark.py` — continuity projection
- `tests/action_ready_benchmark.py` — evidence normalization
- `tests/final_transition_e2e.py` — authenticated API to worker to SQLite to recovery
- `tests/capability_expansion_benchmark.py` — capability registry
- 30+ additional benchmarks

**PS002 tests (isolate as PS002-specific):**
- `tests/ps002_agent_foundation.py`
- `tests/ps002_agent_integrity.py`
- `tests/ps002_agent_observation.py`
- `tests/agent_orchestrator_test.py`
- `tests/agent_artifacts_test.py`
- `tests/sandbox_runner_test.py`
- `tests/model_router_test.py`
