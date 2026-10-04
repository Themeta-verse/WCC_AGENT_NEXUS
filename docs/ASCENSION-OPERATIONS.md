# NEXUS Ascension Operations

## Current independent runtime

The supported product runtime is an authenticated FastAPI service, a separate durable worker, SQLite WAL state, and a static React command center. `runtime.MissionComposer` remains the only planning, execution, verification, and checkpoint engine.

The runtime lifecycle is API-derived. `READY` means no current failed mission requires attention. `RECOVERING` means a worker lease is active. `DEGRADED` means queued work has no fresh worker heartbeat. `REQUIRES_ATTENTION` means an owner has not been bootstrapped or persisted failed missions need operator review.

## Local operator flow

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-standalone.txt

export NEXUS_DATA_ROOT="$PWD/.nexus_product"
export NEXUS_ALLOWED_FILESYSTEM_ROOT="$PWD"
python -m nexus_independent.cli migrate
python -m nexus_independent.cli bootstrap \
  --email owner@example.com --password 'replace-with-a-unique-12-plus-character-password'

# Terminal 1
nexus serve --host 0.0.0.0 --port 8787
# Terminal 2
nexus worker --worker-id primary-worker
# Terminal 3
cd frontend && cp .env.example .env.local && pnpm install && pnpm dev --host 0.0.0.0 --port 3000
```

The browser client is served at `http://localhost:3000`; set `VITE_NEXUS_API_BASE_URL` to the API origin in `frontend/.env.local` when using a different address.

## Ordinary-language CLI

The installed `nexus` command maps only supported request classes into the canonical runtime:

```bash
export NEXUS_CLI_EMAIL=owner@example.com
export NEXUS_CLI_PASSWORD='replace-with-the-owner-password'

nexus "analyze this project"
nexus "where were we?"
nexus "what changed?"
nexus "what should happen next?"
nexus continue
```

Context questions read durable tenant-scoped state. Other objectives create a durable, read-only mission for the separately running worker. `nexus` does not create an implicit scheduler, write files, change repositories, or impersonate a model provider.

## Containers

The repository includes a single-host SQLite Compose topology:

```bash
cp .env.production.example .env.production
# Set an owner by running the bootstrap command once after `docker compose up -d`.
docker compose up -d --build
docker compose exec api nexus bootstrap --email owner@example.com --password 'replace-with-a-unique-12-plus-character-password'
docker compose ps
```

It starts API, worker, and frontend as separate services and stores SQLite plus checkpoints in the `nexus_data` volume. The mounted workspace is read-only and is the explicitly bounded filesystem evidence root. Docker was not available in the validation environment, so the Compose package is source-validated but not container-executed here.

## Database configuration

`NEXUS_DATABASE_URL` has explicit precedence. A conventional `DATABASE_URL` is considered only for `sqlite://`, `postgres://`, and `postgresql://`; unrelated process configuration is ignored. The executed engine is **SQLite**. A PostgreSQL URL fails closed with an explicit unavailable-engine error in this build. PostgreSQL requires a real adapter, migration runner, and integration suite before it can be called implemented.

## Personal continuity

The authenticated project-context route derives current objective, active progress, explicit blockers, evidence-derived discovered facts, outcomes, and the next action from persisted database records. Memory controls are soft lifecycle controls: annotate, retire, and restore. They do not alter provider evidence; every change is tenant-scoped and audited.
