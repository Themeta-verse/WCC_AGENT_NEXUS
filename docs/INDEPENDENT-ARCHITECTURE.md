# NEXUS Independent Architecture

The repository has one authoritative mission execution path. The HTTP layer authenticates a product user and checks tenant/project membership. It then persists a mission in SQLite and returns `202 Accepted`. A separate worker claims the queue lease and calls `runtime.MissionComposer`. The worker does not contain a second planner, verifier, provider registry, or checkpoint format.

| Layer | Source of truth | Responsibility | Explicit boundary |
|---|---|---|---|
| Product API | `nexus_independent/api.py` | Authentication, tenant-scoped HTTP API, queue submission and query endpoints | No anonymous mission or tenant data access |
| Product service | `nexus_independent/service.py` | Authorization, queue lifecycle, worker dispatch, provider wiring, projections | Does not duplicate canonical execution |
| Canonical runtime | `runtime/mission_composer.py` | Mission planning, execution, verification, LocalStateStore checkpoints | Sole execution and verification engine |
| Persistence | `nexus_independent/database.py` | SQLite WAL schema, queue lease, evidence, memory, outcomes, checkpoint and audit projections | Every product projection is tenant and project scoped |
| Provider transport | `runtime/canonical_pilot.py` and `runtime/github_provider.py` | Direct GitHub REST reads; bounded filesystem and browser providers | Read-only endpoints only; product secret remains process-local |
| Command center | `frontend/client/src/` | Product sign-in, project selection, queue status polling, durable evidence views | Browser receives no provider token or database path |

## Dependency map

```text
React command center
  -> FastAPI /api/v1 (Bearer session)
    -> StandaloneMissionService (tenant role + project membership)
      -> NexusDatabase (SQLite WAL queue and durable projections)
      -> MissionComposer (canonical plan / execute / verify)
        -> GitHubReadProvider -> DirectGitHubAPIAdapter -> GitHub REST GET
        -> FilesystemReadProvider -> configured bounded root
        -> BrowserReadProvider -> configured local CDP endpoint
      -> LocalStateStore (canonical checkpoint state)
```

## Durable product state

SQLite contains the relational operational record: tenants, users, sessions, projects, memberships, mission queue, events, evidence, normalized observations, provider receipts, memory records, outcomes, checkpoints, and audit events. Canonical `LocalStateStore` remains the mission-recovery format. The service records the checkpoint path and checksum after canonical execution; the database never substitutes its own execution state for `MissionComposer` state.

## Memory and truth model

Memory records are append-only projections of canonical normalized observations. Each record carries project and tenant ownership, source provider, provenance receipt, confidence, freshness, and reality state. The implementation does not silently promote inferred or simulated material to verified observation. `outcomes` summarize canonical mission state and completion verification; `provider_receipts` retain the provider execution receipt for evidence inspection.

## Security model

Password hashes use PBKDF2-HMAC-SHA256 with a per-password salt and 600,000 iterations. Sessions are opaque random bearer values; only SHA-256 token hashes are stored. All protected operations resolve the session, validate tenant ownership, and validate project membership. Operator role is required to enqueue or control missions. Mission controls cannot alter an executing mission. API routes expose read-only providers only.

The direct GitHub REST adapter accepts the token only from `NEXUS_GITHUB_TOKEN`; it never reads a token from a request, returns it, or persists it. Repository responses are treated as untrusted data by the canonical adapter. Browser and filesystem providers retain their configured boundaries.
