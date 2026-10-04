# NEXUS Operator-Owned Runtime Proof

This runbook proves the frozen canonical path without a hosted preview, generated runtime state, or external task runtime:

> User → NEXUS frontend → authenticated API → tenant/project authorization → durable SQLite queue → worker → `MissionComposer` → bounded provider → evidence → verification → memory/outcome/checkpoint/audit persistence → NEXUS UI.

## Clean-clone local process proof

From a clean repository clone, create the virtual environment, install the documented runtime dependencies, and prepare the browser client.

```bash
git clone https://github.com/Themeta-verse/Nexus.git
cd Nexus
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-standalone.txt

cd frontend
pnpm install --frozen-lockfile
cd ..
```

The following command starts **three independent local processes**, creates an owner, executes a real bounded filesystem read, inspects SQLite, stops the processes, restarts them, and continues from the persisted checkpoint.

```bash
PYTHONPATH=. python scripts/operator_runtime_proof.py
```

The script reports the dynamically selected local ports, health URL, API/worker/frontend startup state, SQLite row counts, `PRAGMA integrity_check`, real mission evidence state, and restart continuation state. It uses a temporary product data directory and leaves no runtime state in the repository.

## Manual operator startup

For a persistent local product instance, set an owner-controlled data directory and explicitly bound filesystem root.

```bash
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

Open `http://127.0.0.1:3000`, sign in, create a project, choose **Real read**, and submit a command. The command center polls the authenticated API while a mission is active and displays the persisted mission timeline, evidence count, provider state, memory, outcome, audit, checkpoint, and owner-authorized SQLite facts.

## Browser-driven command-center proof

The browser proof drives the actual rendered login, project creation, command, capability selection, queue submission, and API-derived completion state. It requires Chromium on the operator host and a separately started local API, worker, and frontend.

```bash
NEXUS_UI_PROOF_URL=http://127.0.0.1:3000 \
NEXUS_UI_PROOF_EMAIL=owner@example.com \
NEXUS_UI_PROOF_PASSWORD='your-owner-password' \
node scripts/operator_ui_proof.mjs
```

The proof passes only when the rendered UI contains the created project plus the real `mission_queued`, `mission_executing`, `mission_completed`, `OBSERVED`, and `VERIFIED` states obtained from the NEXUS API. It does not substitute fixture JSON for UI state.

## Identity audit

| Classification | Handling |
|---|---|
| Runtime-critical external-platform dependency | Removed. Runtime configuration, API, worker, SQLite, provider transport, and command center are product-owned. |
| Product-brand leakage | Removed dead builder login and OAuth helper components plus the unused Builder dependency. |
| Development-only generated artifact rules | Removed obsolete generated builder and preview ignore rules. |
| Historical documentation or enforcement text | Retained only where it describes independence or where CI rejects prohibited external-runtime coupling. It is not imported by product code. |

## Verified security boundaries

`tests/operator_security_hardening.py` exercises backend authentication, invalid and expired session rejection, role checks, tenant and project isolation, unsupported consequential capability rejection, injection-shaped mission input, filesystem escape rejection, duplicate worker claims, and no-external-write behavior. It does not enable a consequential provider or bypass approval.

## Current operational boundary

SQLite WAL with a single API/worker host is implemented and tested. PostgreSQL, multi-host workers, MFA, invitations, password recovery, TLS termination, distributed rate limits, and a continuously operated browser worker remain unimplemented. Do not represent these as active capabilities.
