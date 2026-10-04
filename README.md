# NEXUS Independent

NEXUS is an evidence-first, independently runnable personal intelligence runtime. Its product path is **authenticated API → tenant-scoped durable queue → separate worker → canonical `MissionComposer` → evidence, verification, checkpoints, and SQLite projections**. The repository contains the runtime, API, worker, browser command center, local persistence, and executable acceptance tests.

> **Truth boundary:** NEXUS exposes only bounded read capabilities. A queued mission is not represented as completed until its canonical execution and verification result are persisted. Simulations remain labelled `SIMULATED`; provider failure remains explicit.

## What is implemented

| Product surface | Implemented behavior |
|---|---|
| Runtime and worker | Durable SQLite queue with lease, retry, recovery, pause, resume, and cancel controls. The worker is a separate process. |
| Security and tenancy | Product-owned PBKDF2 password verification, opaque expiring bearer sessions, tenant-scoped projects, role checks, and persisted audit records. |
| Mission system | The existing canonical `runtime.MissionComposer` remains the sole planning, execution, verification, and checkpoint engine. |
| Providers | Direct GitHub REST read transport, bounded filesystem reads, and existing browser-read support. No GitHub write, deploy, merge, or connector action is exposed. |
| Persistence | SQLite WAL tables for users, tenants, sessions, projects, queue records, events, evidence, observations, provider receipts, memory, outcomes, checkpoints, and audit records. |
| Command center | React/Vite client with product login, tenant project scope, queue status polling, mission composition, evidence history, and API-derived memory/outcome/audit/provider summaries. |

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-standalone.txt

# Create the schema and the first owner. Choose a password of at least 12 characters.
python -m nexus_independent.cli migrate
python -m nexus_independent.cli bootstrap \
  --email owner@example.com \
  --password 'replace-with-a-long-owner-password' \
  --tenant 'NEXUS' \
  --project-id local

# Terminal 1: API. Terminal 2: durable worker.
python -m nexus_independent.cli serve --host 127.0.0.1 --port 8787
python -m nexus_independent.cli worker
```

For the browser client, copy `frontend/.env.example` to `frontend/.env.local`, set `VITE_NEXUS_API_BASE_URL`, then run `pnpm install && pnpm dev` inside `frontend/`.

## Product configuration

`NEXUS_GITHUB_TOKEN` is optional for public read endpoints and should be injected only by the product host when authenticated GitHub reads are needed. It is never accepted through mission inputs or returned in receipts. `NEXUS_ALLOWED_FILESYSTEM_ROOT` bounds filesystem evidence. `NEXUS_WEB_ORIGINS` must name the command-center origin in a deployed environment.

Read [`docs/ASCENSION-FORENSIC-AUDIT.md`](docs/ASCENSION-FORENSIC-AUDIT.md) for the current source-grounded gap map, [`docs/ASCENSION-OPERATIONS.md`](docs/ASCENSION-OPERATIONS.md) for the cockpit, CLI, container, lifecycle, and database boundary, [`docs/INDEPENDENT-ARCHITECTURE.md`](docs/INDEPENDENT-ARCHITECTURE.md) for the source dependency map, [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for independent operations and backup/restore, [`docs/OPERATOR-RUNTIME-PROOF.md`](docs/OPERATOR-RUNTIME-PROOF.md) for the clean-clone three-process and browser command-center proof, and [`README-INDEPENDENT.md`](README-INDEPENDENT.md) for endpoint detail.

## Verified local acceptance

`tests/final_transition_e2e.py` runs a **real bounded filesystem read** through authenticated API submission, the durable queue, an independent worker, verification, SQLite memory/outcome/checkpoint projections, service restart, and recovery. `scripts/operator_runtime_proof.py` starts the real API, worker, and frontend as separate local processes, inspects the actual SQLite records, restarts all processes, and continues from persisted state. Both assert `writes_performed: false`.

## Current limitations

SQLite WAL is the implemented and tested single-host durability layer. Horizontal multi-host workers and PostgreSQL migration are not yet implemented, so production deployment should use one persistent host for the API/worker/database volume until that migration is complete. This product does not yet provide MFA, user invitation workflows, email recovery, secret rotation UI, TLS termination, distributed rate limits, or a continuously operated browser worker.
