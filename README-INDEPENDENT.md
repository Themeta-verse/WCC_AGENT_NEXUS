# NEXUS Independent Product Runtime

This repository contains a standalone product boundary around the canonical NEXUS mission engine. The product runs as standard Python processes with FastAPI, SQLite, direct HTTPS provider calls, and configured local provider dependencies. It does **not** require a Manus task runtime, Manus database, Manus OAuth, or generated artifact to accept, persist, execute, verify, recover, and present an evidence mission.

## Canonical product path

`authenticated API → SQLite tenant/project authorization → durable mission queue → worker → MissionComposer → read-only providers → verification/reconciliation → LocalStateStore + SQLite → secured evidence views`

`runtime.MissionComposer` remains the only mission compiler, executor, verifier, and canonical checkpoint writer. The independent product layer owns product identity, project membership, sessions, queue scheduling, direct provider configuration, and HTTP access. It does not create a second mission engine.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEXUS_PRODUCT_ROOT` | repository root | Product source and default filesystem boundary. |
| `NEXUS_DATA_ROOT` | `.nexus_product` | Default SQLite and LocalStateStore root. |
| `NEXUS_DATABASE_PATH` | `<data-root>/nexus.db` | SQLite product database. |
| `NEXUS_STATE_ROOT` | `<data-root>/state` | Canonical mission checkpoint directories. |
| `NEXUS_ALLOWED_FILESYSTEM_ROOT` | product root | Bound for filesystem-read operations. |
| `NEXUS_GITHUB_REPOSITORY` | `Themeta-verse/Nexus` | Default repository read scope. |
| `NEXUS_GITHUB_TOKEN` | unset | Optional product-managed GitHub token. Keep it only in the host secret manager or process environment. |
| `NEXUS_GITHUB_API_BASE` | `https://api.github.com` | Direct GitHub REST API base. |
| `NEXUS_GITHUB_TIMEOUT_SECONDS` | `20` | Bounded direct HTTPS read timeout. |
| `NEXUS_ALLOW_REAL_READS` | `true` | Enables the proven real read provider path only. |
| `NEXUS_API_HOST` / `NEXUS_API_PORT` | `127.0.0.1` / `8787` | API bind address. |
| `NEXUS_WEB_ORIGINS` | local port 3000 origins | Comma-separated allowed command-center origins. |
| `NEXUS_BOOTSTRAP_OWNER_EMAIL` | unset | Optional first-run product owner email. Must be supplied with its password. |
| `NEXUS_BOOTSTRAP_OWNER_PASSWORD` | unset | Optional first-run product owner password. It must be at least 12 characters and is never stored in plaintext. |
| `NEXUS_BOOTSTRAP_TENANT` / `NEXUS_BOOTSTRAP_PROJECT` | `NEXUS` / `local` | First tenant and command-center project. |
| `NEXUS_WORKER_LEASE_SECONDS` | `90` | SQLite lease duration for one worker claim. |
| `NEXUS_WORKER_POLL_SECONDS` | `2` | Idle worker queue polling interval. |
| `NEXUS_QUEUE_MAX_ATTEMPTS` | `3` | Maximum retry count for unexpected worker exceptions. |
| `NEXUS_SESSION_HOURS` | `12` | Product session lifetime. |

## First-run bootstrap and local execution

Install the product into a normal Python environment, bootstrap the owner, then run the API and queue worker as separate processes. Choose the password locally; it must not be placed in source control, browser configuration, mission input, or a command history shared with others.

```bash
python3 -m pip install -r requirements-standalone.txt

python3 -m nexus_independent.cli bootstrap \
  --email "owner@example.com" \
  --password "choose-a-unique-12-plus-character-password" \
  --tenant "My NEXUS" \
  --project-id "local"

python3 -m nexus_independent.cli serve --host 127.0.0.1 --port 8787
# Separate terminal:
python3 -m nexus_independent.cli worker
```

The API accepts a mission as `QUEUED` and returns immediately. The worker atomically leases a mission, sets it to `EXECUTING`, delegates to the existing canonical engine, stores evidence and verification, then settles the queue record. If a worker stops after a lease expires, a later worker can safely reclaim the mission because every exposed capability remains read-only.

For a local one-shot development run, authenticate through the CLI and invoke the same queue path:

```bash
python3 -m nexus_independent.cli run "Quick check repository identity" \
  --email "owner@example.com" \
  --password "choose-a-unique-12-plus-character-password" \
  --mode REAL_READ --capability repository.metadata.read
```

## Product security model

Passwords are represented by salted PBKDF2-SHA256 verifiers. Opaque bearer sessions are stored only as SHA-256 token hashes in SQLite; the browser command center uses session storage for its active token and does not embed it in application configuration. An authenticated caller can see only tenant projects where it has a project membership. Viewers can read evidence; operators and owners can enqueue bounded read-only missions; only tenant owners can create projects.

Direct GitHub reads use HTTPS, a validated `owner/repository` scope, a bounded timeout, a GitHub API version header, and only the established metadata, branch, commit, tree, README, issue, and pull-request read endpoints. When `NEXUS_GITHUB_TOKEN` is configured, it is injected as a product-owned bearer credential at the process boundary. It is never accepted from the web client, mission request, database, mission receipt, event, health payload, or product log. No repository write, deletion, deployment, merge, settings modification, or pull-request creation endpoint exists.

Repository, browser, and filesystem content remain untrusted data. The canonical injection-as-data handling and independent verification path remain active after the provider transport migration.

## API contract

`GET /health` remains available for process health only. Every product and mission endpoint below requires `Authorization: Bearer <product-session-token>` except login.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/auth/login` | Creates a product-owned bearer session from the owner/member credential. |
| `POST /api/v1/auth/logout` | Revokes the current bearer session. |
| `GET /api/v1/me` | Returns the authenticated user and only their tenant projects. |
| `GET /api/v1/projects` | Lists tenant projects allowed by membership. |
| `POST /api/v1/projects` | Creates a project for an authenticated tenant owner. |
| `POST /api/v1/missions` | Validates tenant operator access and enqueues a governed read-only mission; returns `202 Accepted`. |
| `GET /api/v1/projects/{project_id}/missions` | Returns history only for a permitted project. |
| `GET /api/v1/missions/{mission_id}` | Returns a mission only after tenant and project membership checks. |
| `GET /api/v1/missions/{mission_id}/evidence` | Returns verified evidence only after tenant and project membership checks. |
| `GET /api/v1/missions/{mission_id}/events` | Returns durable queue and mission events only after tenant and project membership checks. |
| `POST /api/v1/missions/{mission_id}/recover` | Runs a read-only canonical recovery check for an operator or owner. |

The static React command center polls the authenticated, tenant-scoped mission list every two seconds while a mission is non-terminal and every eight seconds otherwise. It is therefore showing durable queue and worker state, not a simulated progress indicator or a browser-only task state.

## Persistence and recovery

SQLite owns tenant, user, session, project membership, product mission, queue, event, and normalized evidence records. The canonical `LocalStateStore` remains the authoritative mission checkpoint format under the configured state root. Recovery checks the product mission record and calls the existing canonical `MissionComposer` recovery method against the matching checkpoint. These layers intentionally complement one another rather than duplicate mission execution state.

## Production deployment note

Run the API and at least one `nexus-independent worker` process on product-controlled persistent infrastructure. Configure `NEXUS_GITHUB_TOKEN` in that host’s secret manager if private-repository access or higher API limits are needed. Configure `NEXUS_WEB_ORIGINS` to exact HTTPS command-center origins, place the API behind TLS, and inject initial owner credentials only for bootstrap or use the `bootstrap` CLI locally. Do not expose the API before an owner has been created and an HTTPS origin has been configured.
