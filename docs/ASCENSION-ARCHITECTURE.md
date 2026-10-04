# NEXUS Ascension Architecture

```mermaid
flowchart LR
    U[Owner / Operator] --> C[Command Center\nReact / Vite]
    U --> L[nexus CLI\nordinary-language front door]
    C -->|Bearer session| A[Authenticated FastAPI]
    L -->|authenticated context or queue request| S[StandaloneMissionService]
    A --> S
    S --> T[Tenant and project authorization]
    T --> D[(SQLite WAL\nproduct-owned state)]
    S --> Q[Durable mission queue]
    W[Separate worker] -->|lease / heartbeat / retry| Q
    W --> M[Canonical MissionComposer\nsole planner / executor / verifier]
    M --> P[Bounded read providers]
    P --> G[Direct GitHub REST\nread only]
    P --> F[Bounded filesystem\nread only]
    P --> B[Chromium CDP\nread/extract only]
    M --> E[Evidence / receipts / verification]
    E --> D
    D --> X[Memory / outcomes / checkpoints / audit]
    X --> S
    S --> C
```

## Boundary legend

| Boundary | Implemented behavior |
|---|---|
| Identity | Product-owned PBKDF2 password verifier, expiring opaque bearer sessions, revocation, tenant and project role checks |
| Execution | Only the durable worker invokes the existing canonical `MissionComposer` |
| Authority | The public capability surface is read-only; unsupported, consequential, or out-of-scope requests fail closed |
| Truth | Mission status, lifecycle, provider state, memory, outcomes, evidence, and UI are derived from persisted API data |
| Persistence | SQLite WAL with backups and guarded restore is implemented for single-host deployment |
| Future extension | PostgreSQL, model providers, schedules, browser interaction, writes, code execution, notifications, and multi-host workers require separate real implementations and verification |
