# NEXUS Operator-Owned Runtime Reality Report

**Canonical commit:** `9a955bb` at the time the operator-owned proof was completed. This report records executed evidence rather than intended architecture.

## A. What NEXUS Actually Is Now

NEXUS is a source-owned, evidence-first local product composed of a Python runtime, FastAPI service, SQLite persistence layer, separate durable worker, canonical `MissionComposer`, bounded read providers, and a React/Vite command center. Its one canonical mission path is:

> User → command center → product authentication → tenant/project authorization → SQLite queue → worker → `MissionComposer` → bounded provider → evidence → verification → memory/outcome/checkpoint/audit persistence → command center.

`MissionComposer` remains the only planner, executor, verifier, and canonical checkpoint writer. No additional orchestrator, memory system, or database abstraction was introduced.

## B. What Runs Independently

The repository runs with standard Python and Node tooling. The executable `nexus_independent` CLI supplies `migrate`, `bootstrap`, `serve`, `worker`, `health`, `backup`, `restore`, `recover`, and compatibility `run` commands. The React/Vite client is in `frontend/` and talks to `VITE_NEXUS_API_BASE_URL`.

`scripts/operator_runtime_proof.py` launched the real API, worker, and frontend as separate local processes. It used dynamically allocated localhost ports and reported `HEALTHY` API, `CLAIMED_AND_COMPLETED` worker, and `HTTP_200` frontend states. It did not use a hosted preview, a generated runtime state file, or an external task runtime.

## C. What Database Is Actually Used

The implemented database is **SQLite with WAL enabled**. It is not PostgreSQL.

The operator process proof created a temporary SQLite database and confirmed `PRAGMA integrity_check = ok` and `journal_mode = wal`. After a real completed bounded read it inspected these durable row counts:

| Durable record | Count |
|---|---:|
| Tenants | 2 |
| Users | 1 |
| Projects | 2 |
| Missions / queue records | 1 / 1 |
| Mission events / evidence | 6 / 1 |
| Observations / provider receipts | 1 / 1 |
| Memory items / outcomes | 1 / 1 |
| Checkpoints / audit events | 1 / 6 |

The canonical database also enforces foreign keys, transactions, indexes, lease-based queue claims, retry state, backup, guarded restore, and tenant-scoped reads. SQLite is the tested single-host operating mode.

## D. What Authentication Actually Exists

Authentication is enforced by the backend. The product stores PBKDF2-SHA256 password hashes with a per-user salt and 600,000 rounds, issues opaque bearer sessions, stores only token hashes, expires sessions, supports logout revocation, and resolves a request principal from the server-side session record.

Tenant and project checks are executed before mission, memory, evidence, outcome, checkpoint, audit, and control requests. Owners can create projects and inspect tenant-scoped database facts. Operators can enqueue and control eligible project missions; viewers cannot execute missions or create projects.

## E. What Providers Actually Work

| Provider | Actual product state | Evidence boundary |
|---|---|---|
| `filesystem.read` | **Executed and verified** in the operator proof | Explicit allowed root only; path escapes are blocked; content is untrusted data; no writes |
| GitHub read | Direct HTTPS REST transport integrated; public reads may work subject to availability; `NEXUS_GITHUB_TOKEN` supports authenticated/private reads | Read-only, product-managed host credential only, no `gh` subprocess in product path |
| Browser read | Implemented capability requiring a configured local CDP endpoint | Read-only; not exercised in the local operator proof |
| Simulation | Callable and labelled `SIMULATED` | Never represented as observed or verified external evidence |

No GitHub write, merge, pull-request, deployment, payment, connector activation, or other consequential provider is exposed.

## F. What the Frontend Actually Controls

The command center uses product-owned login and bearer sessions. An owner can create a project from the rendered UI, choose a tenant-scoped project, enter a natural-language mission command, select only API-advertised read capabilities, choose simulation or real read, and submit a durable queue record.

It polls the authenticated API for live mission state, shows API-derived provider state, durable history, event timeline, evidence count, memory, outcomes, audit events, checkpoints, and owner-authorized SQLite facts. It exposes pause, resume, cancel, and **continue from checkpoint** only through the authenticated API. It does not display a static `COMPLETED`, `VERIFIED`, or provider-ready state in place of an API response.

`scripts/operator_ui_proof.mjs` drove Chromium against the rendered product. It logged in, created `ui-operator-proof`, submitted a **REAL_READ** bounded filesystem mission, refreshed the workspace, and passed only after the UI showed `mission_queued`, `mission_executing`, `mission_completed`, `OBSERVED`, and `VERIFIED` from product API state.

## G. What Survives Restart

The process proof stopped API, worker, and frontend; restarted all three from the repository; logged in again; and called the authenticated continuation route. The mission identifier, completed state, observed reality, verified result, evidence, memory, outcome, checkpoint, audit records, and SQLite integrity survived. The continuation result was `RECOVERED`.

The continuation control recovers persisted canonical state. It does not fabricate a new execution or silently re-run a completed mission.

## H. What Still Depends on External Services

The local core does not require a hosted preview, external task runtime, external storage, OAuth service, or development-platform plugin. It still depends on ordinary operator-controlled host services when those capabilities are used:

| Dependency | Why it exists |
|---|---|
| Product host filesystem | SQLite file, checkpoints, logs, and bounded filesystem-read evidence |
| GitHub HTTPS API | Repository read capability; private reads require product-managed `NEXUS_GITHUB_TOKEN` |
| Chromium CDP endpoint | Optional browser-read capability |
| Network and TLS terminator | Required only when the operator deploys beyond localhost |

## I. What Remains Unimplemented

The following are not implemented and are not represented as active capabilities: PostgreSQL, multi-host workers, distributed leases, MFA, invitation workflow, password recovery, secret rotation UI, TLS termination, distributed rate limiting, email delivery, and a continuously operated browser worker. The current supported deployment model is a single persistent host running one API/worker/database volume.

## J. Security Results

| Security control | Executed result |
|---|---|
| Missing, forged, invalid, expired, and logged-out sessions | Rejected by backend authentication |
| Tenant and project escape | Rejected with authorization checks |
| Owner/operator/viewer enforcement | Verified: operators cannot create projects; viewers cannot submit missions |
| Unsupported consequential capability | Rejected by request validation |
| Prompt-injection-shaped mission input | Treated as mission data; no external write occurred |
| Filesystem escape | `/etc/passwd` attempt blocked by the explicit-root boundary; no outside evidence persisted |
| Queue duplication | Two concurrent workers produced exactly one claim event for the target mission |
| SQLite integrity | `PRAGMA integrity_check = ok` in the operator proof |
| Secret and source hygiene | No credential-pattern match; no tracked database, environment, build, or generated runtime-state file |
| Product-identity leakage | Dead builder login/OAuth helpers and unused Builder dependency removed; no runtime-critical external-platform reference remains |

The focused `tests/operator_security_hardening.py`, standalone product benchmark, real-read restart test, full historical benchmark suite, frontend type check, and production build all passed locally.

## K. Clean-Clone Startup Commands

```bash
git clone https://github.com/Themeta-verse/Nexus.git
cd Nexus
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-standalone.txt

cd frontend && pnpm install --frozen-lockfile && cd ..

export NEXUS_DATA_ROOT="$HOME/.local/share/nexus"
export NEXUS_ALLOWED_FILESYSTEM_ROOT="$PWD"
export NEXUS_WEB_ORIGINS="http://127.0.0.1:3000"
export NEXUS_ALLOW_REAL_READS=true
python -m nexus_independent.cli migrate
python -m nexus_independent.cli bootstrap \
  --email owner@example.com \
  --password 'choose-a-unique-password-of-at-least-12-characters' \
  --tenant NEXUS \
  --project-id local

# Terminal 1
python -m nexus_independent.cli serve --host 127.0.0.1 --port 8787

# Terminal 2
python -m nexus_independent.cli worker --worker-id worker-primary

# Terminal 3
cd frontend
printf 'VITE_NEXUS_API_BASE_URL=http://127.0.0.1:8787\n' > .env.local
pnpm dev
```

For a repeatable operator proof, run `PYTHONPATH=. python scripts/operator_runtime_proof.py`. For a browser-driven UI proof, follow [`OPERATOR-RUNTIME-PROOF.md`](OPERATOR-RUNTIME-PROOF.md).

## L. GitHub Ownership

The canonical repository is [`Themeta-verse/Nexus`](https://github.com/Themeta-verse/Nexus). The update was pushed to `main` as commit [`9a955bb`](https://github.com/Themeta-verse/Nexus/commit/9a955bb), **`feat: prove operator-owned NEXUS runtime`**. The local branch and `origin/main` matched at the verification point and the working tree was clean.

The repository preflight found no tracked secret pattern, no generated database or runtime-state file, no active builder plugin, and no runtime-critical external-platform coupling. At the immediate post-push check, GitHub returned no Actions run for this commit; local execution completed the workflow-equivalent checks. CI should be rechecked in GitHub after workflow dispatch is confirmed.

## M. Final Reality Matrix

| Product claim | Implemented | Callable | Authorized | Executed | Observed | Verified | Persisted | Evidence |
|---|---|---|---|---|---|---|---|---|
| Product-owned API and authentication | Yes | Yes | Backend sessions | Yes | Yes | Yes | Sessions/audit | Product and security tests |
| Tenant/project authorization | Yes | Yes | Role and membership checks | Yes | Yes | Yes | Projects/memberships/audit | Security test |
| Durable queue and worker | Yes | Yes | Operator/owner mission rights | Yes | Yes | Yes | Queue/events/leases | Process proof |
| Bounded filesystem REAL_READ | Yes | Yes | Explicit allowed root | Yes | Yes | Yes | Evidence/observation/receipt/memory/outcome/checkpoint | Process and browser UI proof |
| Direct GitHub REST read | Yes | Yes | Read-only; token for private access | Historical direct path exercised | Availability-dependent | Receipt validation | Evidence/receipts when used | Historical regression |
| Command-center real state | Yes | Yes | Authenticated tenant session | Yes | API-derived | API-derived | Product database | Browser UI proof |
| Restart and continuation | Yes | Yes | Operator role | Yes | Recovered state | Canonical recovery | SQLite plus checkpoint state | Process proof |
| Consequential actions | No | No | No | No | No | No | No | Explicit API/provider boundary |

The product passes the stated independence criterion for its implemented local single-host scope: an operator can obtain the repository, install it, initialize SQLite, bootstrap an owner, start API/worker/frontend, authenticate, create a project, submit and observe a real bounded mission through the UI, inspect persisted evidence and verification, stop the processes, restart them, log in again, and continue from persisted state.
