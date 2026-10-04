# Independent Operations Runbook

## Runtime processes

NEXUS currently runs as two independent processes against the same persistent SQLite volume. The API accepts and exposes durable work; the worker owns queue claiming and canonical execution.

```bash
python -m nexus_independent.cli migrate
python -m nexus_independent.cli serve --host 0.0.0.0 --port 8787
python -m nexus_independent.cli worker --worker-id worker-primary
```

Run the frontend separately from `frontend/` with a production `VITE_NEXUS_API_BASE_URL`. Configure a reverse proxy or gateway for TLS and set `NEXUS_WEB_ORIGINS` to only the command-center origin. Do not expose the API directly with a wildcard CORS origin.

## Required environment

| Variable | Required | Purpose |
|---|---:|---|
| `NEXUS_DATA_ROOT` | Recommended | Persistent directory for `nexus.db` and canonical state checkpoints. |
| `NEXUS_BOOTSTRAP_OWNER_EMAIL` and `NEXUS_BOOTSTRAP_OWNER_PASSWORD` | First run only | Creates the first tenant owner, or use the explicit CLI bootstrap command. |
| `NEXUS_GITHUB_TOKEN` | Optional | Product-managed GitHub REST token for authenticated read quotas and private repository reads. |
| `NEXUS_ALLOWED_FILESYSTEM_ROOT` | Recommended | Absolute upper boundary for filesystem-read evidence. |
| `NEXUS_WEB_ORIGINS` | Deployment | Comma-separated browser origins allowed to call the API. |
| `NEXUS_ALLOW_REAL_READS` | Deployment | Enables `REAL_READ`; defaults must be consciously reviewed. |

## Backup and restore

Use the application command so SQLite produces a consistent backup including WAL state.

```bash
python -m nexus_independent.cli backup /secure/backups/nexus-$(date +%F).db
```

Stop both API and worker processes before restore. Restore is intentionally blocked unless `--confirm-restore` is present.

```bash
python -m nexus_independent.cli restore /secure/backups/nexus-2026-08-20.db --confirm-restore
```

## Database decision

SQLite WAL is the actual implemented database because the product currently uses a single API/worker host and needs an independently runnable local installation. It is not a claim of safe multi-host concurrency. A PostgreSQL migration is the next persistence milestone when the deployment requires multiple API or worker instances, high concurrent write throughput, managed backups, or multi-region durability. Until then, run one API process and one worker process against one persistent SQLite volume.

## Validation

```bash
PYTHONPATH=. python tests/independent_product_benchmark.py
PYTHONPATH=. python tests/final_transition_e2e.py
cd frontend && pnpm run check && pnpm run build
```

The final end-to-end test uses a real bounded local filesystem read and proves queue persistence, canonical execution, verified evidence, memory/outcome/checkpoint projection, service restart, and recovery without a Manus runtime. It does not claim a GitHub read unless `NEXUS_GITHUB_TOKEN` and the selected repository access are configured.
