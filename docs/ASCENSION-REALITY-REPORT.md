# NEXUS — Independent Intelligence System Reality Report

## What NEXUS actually is

NEXUS is an independently runnable, authenticated, tenant-scoped, evidence-first personal intelligence runtime. Its executable path is API or CLI request, authorization, durable queue, separate worker, canonical `MissionComposer`, bounded provider execution, verification, and persisted project continuity.

## Implemented and verified

| Area | Implemented source | Executed / verified evidence |
|---|---|---|
| Runtime | `nexus_independent` API, service, CLI, and worker | Independent product, clean operator-process, and browser cockpit proofs passed |
| Database | SQLite WAL, durable queue, sessions, tenancy, memory, outcomes, checkpoints, audits, worker heartbeats | Real bounded read produced observed and verified records; restart/recovery passed |
| Authentication | Product-owned password hashing, bearer sessions, tenant/project roles, audit | Forged/expired session, tenant, project, role, injection, path, and duplicate-claim security checks passed |
| Capabilities | Direct GitHub read, bounded filesystem read, Chromium CDP read/extract contracts | Filesystem real-read execution and verification passed; providers remain read-only |
| Personal continuity | Project context, evidence-derived memory, outcomes, next action, annotate/retire/restore lifecycle | Ascension benchmark passed context, lifecycle, audit, and worker diagnostics checks |
| Cockpit | Authenticated React command center with objective, progress, discovered facts, blockers, next action, mission timeline, and memory controls | Browser-driven visible UI proof passed login, project creation, real mission, evidence, verified completion, refresh, and persisted timeline |
| Operations | Local CLI, health, diagnostics, backup/restore, Compose package, environment templates | Local commands and tests executed; Compose source exists but Docker was unavailable for container execution |

## Configured but not executed here

The Compose package defines separate API, worker, and frontend services with persistent SQLite state, health checks, a read-only workspace mount, and explicit environment configuration. Docker was not installed in this validation environment, so it is not marked container-executed.

## Explicit future work

PostgreSQL execution, migrations, and integration tests; model-provider invocation; durable scheduling; browser interaction; filesystem/repository writes; code execution; notifications; object storage; vector search; MFA; invitations; recovery email; secret-rotation UI; TLS termination; distributed rate limits; multi-host workers; and managed browser workers are not implemented. Their UI availability must remain absent or explicitly `UNAVAILABLE` until each is backed by an authority, provider, persistence, verification, and security path.

## Exact currently available capability surface

`repository.metadata.read`, `repository.read`, `filesystem.read`, and `browser.read` are the only declared product capabilities. They are read-only, scope-bounded, and tenant-authorized. Provider availability remains runtime-dependent: GitHub authentication is a product-managed optional secret, filesystem reads require a configured root, and browser reads require a local CDP endpoint.

## Exact independent startup

Use the commands in [Ascension Operations](ASCENSION-OPERATIONS.md). The product must be started as API, worker, and frontend processes. Owner bootstrap occurs once against the product database. `nexus "where were we?"` and `nexus "what should happen next?"` read the authenticated durable project context; ordinary objectives become durable read-only missions for the worker.

## Validation evidence

The complete historical benchmark sweep, independent product acceptance, security suite, real local end-to-end recovery proof, Ascension lifecycle/memory benchmark, three-process operator proof, browser-driven cockpit proof, CLI continuity proof, Python compilation, TypeScript check, frontend production build, source-coupling scan, secret scan, and whitespace checks passed before publication.

See [the architecture diagram](ASCENSION-ARCHITECTURE.md), [the forensic audit](ASCENSION-FORENSIC-AUDIT.md), and [the operator runbook](ASCENSION-OPERATIONS.md) for source paths and operating boundaries.
