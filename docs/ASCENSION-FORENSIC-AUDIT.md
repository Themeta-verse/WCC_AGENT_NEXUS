# NEXUS Ascension Forensic Audit

This audit treats the Ascension directive as requirements, not authority over product security or unsupported capabilities. It reflects the canonical repository after the operator-owned runtime proof.

## Executable product path

| Surface | Source of truth | Current factual state |
|---|---|---|
| CLI | `nexus_independent/cli.py` | Real local bootstrap, migration, API, worker, health, backup, restore, recovery, and compatibility mission commands |
| API | `nexus_independent/api.py` | FastAPI authentication, project, mission, evidence, event, checkpoint, provider, capability, audit, and database-inspection routes |
| Runtime | `nexus_independent/service.py` | Authenticated tenant queue service delegating all planning, execution, verification, and checkpoints to `runtime.MissionComposer` |
| Persistence | `nexus_independent/database.py` | SQLite WAL schema with tenancy, sessions, projects, durable queue/events/evidence, observations, receipts, memory, outcomes, checkpoints, and audit records |
| Worker | `StandaloneMissionService.run_worker` | Separate durable polling process with leases, retries, pause/resume/cancel, recovery, and no consequential capability path |
| Capability fabric | `runtime/capability_registry.py` plus providers | Four structured read-only capabilities: repository metadata/read, browser read, and bounded filesystem read |
| User interface | `frontend/client/src/pages/Home.tsx` | Authenticated React command center with tenant projects, mission queue, durable status polling, evidence timeline, memory/outcome/audit summaries, and owner database facts |

## Verified current strength

The product has a real independently runnable local path: owner bootstrap, login, project creation, durable mission queue, a separate worker, a bounded filesystem `REAL_READ`, observed and verified evidence, SQLite projections, process restart, recovered continuation, and a browser-driven rendered UI proof. Existing security tests prove session, tenant, project, role, path, injection-shaped input, and duplicate-claim boundaries.

## Concrete Ascension gaps

| Requirement area | Actual gap | Product-safe implementation direction |
|---|---|---|
| Runtime truth | Health currently reports a fixed `HEALTHY` state rather than an explicit lifecycle classification | Derive `OFFLINE`, `STARTING`, `READY`, `DEGRADED`, `RECOVERING`, and `REQUIRES_ATTENTION` from database, worker, queue, and provider facts |
| Personal cognition | The UI accepts a mission but presents internals before a user-facing current objective, progress, discovered facts, and next action | Add authenticated project context derived only from persisted missions, outcomes, memory, and audit evidence; make inspection progressive disclosure |
| Memory controls | Evidence-derived memory is listable but not yet user-retirable, restorable, annotated, or summarized as “where were we?” | Preserve immutable evidence; add scoped soft-retire/restore and owner/operator notes with audit records and a derived project context endpoint |
| Operations | CLI diagnostics are incomplete and the UI does not expose one coherent runtime state model | Add source-backed diagnostics for health, database, workers, providers, capabilities, recovery, and project context |
| Deployment | No Dockerfile, compose topology, or production environment template exists | Add a portable API/worker/frontend Compose package for the tested SQLite single-host mode and health checks; do not claim multi-host production capability |
| Database portability | SQLite WAL is the actual runtime. `DATABASE_URL`, PostgreSQL migrations, and a production adapter are absent | Introduce URL-based storage configuration and PostgreSQL migration/adapter work as an explicit implementation track; retain SQLite as verified mode until a real PostgreSQL integration test passes |
| Models | No model provider is currently invoked by the standalone runtime | Add only a model-provider contract/status surface until a user-configured provider can be executed and verified; do not label model reasoning as available prematurely |
| Automation | Durable worker exists, but user-managed recurring automation definitions and scheduling are absent | Add explicit, cancellable, auditable schedule definitions only when a persistent host is configured; never use hidden background behavior |
| Tool fabric | Structured read providers are real. Writes, code execution, browser interaction, web research, notifications, and model calls are not | Keep those capabilities `UNAVAILABLE`; add extension contracts rather than fake buttons or unauthorized side effects |
| Portability hygiene | Product runtime no longer has hard-coded sandbox roots, but several historical benchmarks still reference extracted development paths | Isolate or make those test fixtures repository-relative; they are not on the product runtime path |

## Dead-code and dependency classification

The prior operator-proof change removed dead builder-branded login/OAuth helpers and the unused Builder dependency. The current production runtime has no discovered external development-platform import. Remaining references in documentation describe independence; CI string checks reject prohibited runtime coupling. Hard-coded extracted paths observed in historical benchmarks are test-fixture debt, not an API, worker, provider, or frontend runtime dependency.

## Implementation priority

1. **Runtime lifecycle and cockpit context** so the product tells the truth about readiness, objectives, progress, blockers, verified results, and next action.
2. **Scoped memory lifecycle and personal context** so “where were we?”, “what changed?”, and “what next?” use durable evidence rather than frontend placeholders.
3. **Diagnostics, portable Compose deployment, and operator configuration** so a user can run API, worker, frontend, backup, recovery, and health checks outside the construction environment.
4. **Database URL and PostgreSQL adapter/migrations** only with a real configured integration test; no portability claim before then.
5. **Automation, models, browser interaction, write/execute capabilities, and research** as explicit future providers behind authority, scope, and verification contracts.
