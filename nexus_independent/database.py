"""Durable SQLite repository for product-owned NEXUS state.

The database owns tenancy, sessions, projects, queue state, and product records.
`runtime.MissionComposer` remains the only planner, executor, verifier, and
LocalStateStore checkpoint author.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
import base64
import hashlib
import json
import secrets
import shutil
import sqlite3
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _safe_slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return cleaned[:100] or "nexus"


def _hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("bootstrap and product passwords must contain at least 12 characters")
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return "pbkdf2_sha256$600000$%s$%s" % (
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


ALLOWED_REALITY_STATES = frozenset({"OBSERVED", "INFERRED", "VERIFIED", "UNVERIFIED", "UNKNOWN"})

ALLOWED_VERIFICATION_STATES = frozenset({"UNVERIFIED", "VERIFIED", "FAILED", "UNKNOWN"})


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class NexusDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def migrate(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner','operator','viewer')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id, expires_at);
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_memberships (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('owner','operator','viewer')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    scope TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    requested_capabilities_json TEXT NOT NULL,
                    submission_json TEXT,
                    status TEXT NOT NULL,
                    reality TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    action_state TEXT NOT NULL,
                    external_invocations INTEGER NOT NULL DEFAULT 0,
                    store_root TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS missions_project_created_idx ON missions(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS mission_queue (
                    mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('QUEUED','LEASED','COMPLETED','FAILED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mission_queue_claim_idx ON mission_queue(status, available_at, created_at);
                CREATE TABLE IF NOT EXISTS mission_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mission_events_mission_idx ON mission_events(mission_id, event_id);
                CREATE TABLE IF NOT EXISTS mission_evidence (
                    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    capability TEXT,
                    provider TEXT,
                    observation_id TEXT,
                    verification_state TEXT,
                    reality TEXT,
                    receipt_json TEXT,
                    observation_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mission_evidence_mission_idx ON mission_evidence(mission_id, evidence_id);
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
                    provider TEXT,
                    capability TEXT,
                    reality_state TEXT NOT NULL,
                    verification_state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observations_project_idx ON observations(project_id, observed_at DESC);
                CREATE TABLE IF NOT EXISTS provider_receipts (
                    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS provider_receipts_mission_idx ON provider_receipts(mission_id, receipt_id);
                CREATE TABLE IF NOT EXISTS memory_items (
                    memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
                    source TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    freshness_at TEXT NOT NULL,
                    reality_state TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','superseded','conflicted')),
                    supersedes_memory_id TEXT REFERENCES memory_items(memory_id),
                    conflict_key TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memory_items_project_idx ON memory_items(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS memory_items_conflict_idx ON memory_items(project_id, conflict_key, status);
                CREATE TABLE IF NOT EXISTS memory_links (
                    link_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    source_memory_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                    target_memory_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_memory_id, target_memory_id, relation)
                );
                CREATE TABLE IF NOT EXISTS outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT NOT NULL UNIQUE REFERENCES missions(mission_id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    reality_state TEXT NOT NULL,
                    verification_state TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS outcomes_project_idx ON outcomes(project_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    checkpoint_path TEXT NOT NULL,
                    checksum TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS checkpoints_mission_idx ON checkpoints(mission_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS audit_events (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT REFERENCES projects(project_id),
                    actor_user_id TEXT REFERENCES users(user_id),
                    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_events_project_idx ON audit_events(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    worker_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS worker_heartbeats_heartbeat_idx ON worker_heartbeats(heartbeat_at DESC);
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ACTIVE','FLAGGED','HALTED','RETIRED')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agents_tenant_idx ON agents(tenant_id, agent_id);
                CREATE INDEX IF NOT EXISTS agents_project_idx ON agents(project_id, agent_id);
                CREATE TABLE IF NOT EXISTS agent_policies (
                    policy_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    declared_capabilities_json TEXT NOT NULL,
                    allowed_operations_json TEXT NOT NULL,
                    prohibited_operations_json TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    expected_behaviour TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_policies_agent_idx ON agent_policies(agent_id, version DESC);
                CREATE TABLE IF NOT EXISTS agent_actions (
                    action_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    operation TEXT NOT NULL,
                    target_resource TEXT,
                    requested_capability TEXT,
                    observed_parameters_json TEXT NOT NULL,
                    observation_reality TEXT NOT NULL,
                    receipt_json TEXT,
                    integrity_decision TEXT NOT NULL CHECK(integrity_decision IN ('ALLOW','FLAG','HALT')),
                    integrity_reason TEXT,
                    evidence_json TEXT,
                    policy_version INTEGER,
                    evaluated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_actions_agent_idx ON agent_actions(agent_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS agent_actions_tenant_idx ON agent_actions(tenant_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS agent_integrity_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    integrity_decision TEXT CHECK(integrity_decision IN ('ALLOW','FLAG','HALT')),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_integrity_events_agent_idx ON agent_integrity_events(agent_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    name TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','PAUSED','COMPLETED','FAILED','CANCELLED')),
                    plan_json TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS workflows_tenant_idx ON workflows(tenant_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS workflows_project_idx ON workflows(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS workflow_tasks (
                    task_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    task_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    agent_id TEXT,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','READY','RUNNING','WAITING','COMPLETED','FAILED','BLOCKED','CANCELLED','AWAITING_APPROVAL')),
                    reality TEXT NOT NULL DEFAULT 'UNKNOWN',
                    required_capabilities_json TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL,
                    input_artifacts_json TEXT NOT NULL,
                    output_artifacts_json TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 3,
                    error TEXT,
                    result_json TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_tasks_workflow_idx ON workflow_tasks(workflow_id, created_at);
                CREATE INDEX IF NOT EXISTS workflow_tasks_agent_idx ON workflow_tasks(agent_id, status);
                CREATE TABLE IF NOT EXISTS workflow_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    task_id TEXT REFERENCES workflow_tasks(task_id) ON DELETE SET NULL,
                    agent_id TEXT,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    content_path TEXT,
                    content_size INTEGER,
                    parent_artifacts_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    reality TEXT NOT NULL DEFAULT 'INFERRED',
                    untrusted INTEGER NOT NULL DEFAULT 1,
                    verification_state TEXT NOT NULL DEFAULT 'UNVERIFIED'
                );
                CREATE INDEX IF NOT EXISTS workflow_artifacts_workflow_idx ON workflow_artifacts(workflow_id, created_at);
                CREATE INDEX IF NOT EXISTS workflow_artifacts_task_idx ON workflow_artifacts(task_id);
                CREATE TABLE IF NOT EXISTS workflow_events (
                    event_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    task_id TEXT REFERENCES workflow_tasks(task_id) ON DELETE SET NULL,
                    agent_id TEXT,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_events_workflow_idx ON workflow_events(workflow_id, created_at);
                CREATE TABLE IF NOT EXISTS workflow_messages (
                    message_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT,
                    task_id TEXT REFERENCES workflow_tasks(task_id) ON DELETE SET NULL,
                    from_agent_id TEXT,
                    to_agent_id TEXT,
                    message_type TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    correlation_id TEXT,
                    created_at TEXT NOT NULL,
                    processed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS workflow_messages_workflow_idx ON workflow_messages(workflow_id, created_at);
                CREATE INDEX IF NOT EXISTS workflow_messages_task_idx ON workflow_messages(task_id, created_at);
                CREATE INDEX IF NOT EXISTS workflow_messages_type_idx ON workflow_messages(message_type, created_at);
                CREATE TABLE IF NOT EXISTS workflow_approvals (
                    approval_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    task_id TEXT REFERENCES workflow_tasks(task_id) ON DELETE SET NULL,
                    requested_by TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    reason TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','CANCELLED')),
                    decided_by TEXT,
                    decided_at TEXT,
                    decision_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_approvals_workflow_idx ON workflow_approvals(workflow_id, created_at);
                CREATE INDEX IF NOT EXISTS workflow_approvals_status_idx ON workflow_approvals(status, created_at);
                CREATE TABLE IF NOT EXISTS agent_memory (
                    memory_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    workflow_id TEXT REFERENCES workflows(workflow_id) ON DELETE CASCADE,
                    scope TEXT NOT NULL CHECK(scope IN ('agent','project','workflow','task')),
                    source TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    truth TEXT NOT NULL DEFAULT 'INFERRED',
                    confidence TEXT NOT NULL DEFAULT 'UNVERIFIED',
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS agent_memory_agent_idx ON agent_memory(agent_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS agent_memory_workflow_idx ON agent_memory(workflow_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS agent_memory_scope_idx ON agent_memory(scope, created_at);
                """
            )
            self._ensure_column(db, "projects", "tenant_id", "TEXT")
            self._ensure_column(db, "missions", "tenant_id", "TEXT")
            self._ensure_column(db, "missions", "submission_json", "TEXT")
            self._ensure_column(db, "memory_items", "retired_at", "TEXT")
            self._ensure_column(db, "memory_items", "retired_by_user_id", "TEXT")
            self._ensure_column(db, "memory_items", "user_note", "TEXT")
            self._ensure_column(db, "memory_items", "updated_at", "TEXT")
            self._ensure_column(db, "mission_evidence", "agent_id", "TEXT")
            self._ensure_column(db, "provider_receipts", "agent_id", "TEXT")
            self._ensure_column(db, "mission_events", "agent_id", "TEXT")
            if "agent_actions" in {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
                self._ensure_column(db, "agent_actions", "operation", "TEXT")
                self._ensure_column(db, "agent_actions", "target_resource", "TEXT")
                self._ensure_column(db, "agent_actions", "requested_capability", "TEXT")
                self._ensure_column(db, "agent_actions", "observed_parameters_json", "TEXT")
                self._ensure_column(db, "agent_actions", "observation_reality", "TEXT")
                self._ensure_column(db, "agent_actions", "receipt_json", "TEXT")
                self._ensure_column(db, "agent_actions", "integrity_reason", "TEXT")
                self._ensure_column(db, "agent_actions", "policy_version", "INTEGER")
                self._ensure_column(db, "agent_actions", "evaluated_at", "TEXT")
                self._ensure_column(db, "agent_integrity_events", "integrity_decision", "TEXT")
                self._ensure_column(db, "workflow_tasks", "worker_id", "TEXT")
                self._ensure_column(db, "workflow_tasks", "claimed_at", "TEXT")
                self._ensure_column(db, "workflow_tasks", "output_artifacts_json", "TEXT")
                self._ensure_column(db, "workflow_messages", "project_id", "TEXT")
                self._ensure_column(db, "workflow_messages", "processed_at", "TEXT")
                self._ensure_column(db, "workflow_tasks", "parent_task_id", "TEXT")
                self._ensure_column(db, "workflow_tasks", "dynamic", "INTEGER DEFAULT 0")
                self._ensure_column(db, "workflow_tasks", "generated_reason", "TEXT")
                self._ensure_column(db, "workflow_artifacts", "provenance_json", "TEXT NOT NULL DEFAULT '[]'")
                # Phase 9 — autonomous execution fabric: durable worker registry
                # (lease tracking), claim attempt accounting, and artifact
                # execution linkage for crash-safe idempotency.
                self._ensure_column(db, "worker_heartbeats", "tenant_id", "TEXT")
                self._ensure_column(db, "worker_heartbeats", "project_id", "TEXT")
                self._ensure_column(db, "worker_heartbeats", "capabilities_json", "TEXT NOT NULL DEFAULT '[]'")
                self._ensure_column(db, "worker_heartbeats", "current_workflow_id", "TEXT")
                self._ensure_column(db, "worker_heartbeats", "current_task_id", "TEXT")
                self._ensure_column(db, "workflow_tasks", "claim_count", "INTEGER DEFAULT 0")
                self._ensure_column(db, "workflow_tasks", "last_execution_id", "TEXT")
                self._ensure_column(db, "workflow_artifacts", "execution_id", "TEXT")
            db.execute("UPDATE memory_items SET updated_at=created_at WHERE updated_at IS NULL")
            db.execute("CREATE INDEX IF NOT EXISTS projects_tenant_idx ON projects(tenant_id, project_id)")
            db.execute("CREATE INDEX IF NOT EXISTS missions_tenant_created_idx ON missions(tenant_id, created_at DESC)")
            now = utc_now()
            db.execute("INSERT OR IGNORE INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("legacy", "Legacy imported product state", now))
            db.execute("UPDATE projects SET tenant_id='legacy' WHERE tenant_id IS NULL OR tenant_id=''" )
            db.execute("UPDATE missions SET tenant_id=(SELECT tenant_id FROM projects WHERE projects.project_id=missions.project_id) WHERE tenant_id IS NULL OR tenant_id=''" )

    # ---- Identity and tenancy -------------------------------------------------

    def get_or_create_tenant(self, display_name: str) -> dict[str, Any]:
        tenant_id = _safe_slug(display_name)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", (tenant_id, display_name.strip() or "NEXUS", utc_now()))
            row = db.execute("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()
        return dict(row)

    def create_user(self, tenant_id: str, email: str, password: str, role: str = "owner") -> dict[str, Any]:
        normalized = email.strip().lower()
        if "@" not in normalized or len(normalized) > 320:
            raise ValueError("a valid email address is required")
        if role not in {"owner", "operator", "viewer"}:
            raise ValueError("invalid product role")
        now = utc_now()
        with self.connect() as db:
            existing = db.execute("SELECT * FROM users WHERE email=?", (normalized,)).fetchone()
            if existing:
                return dict(existing)
            user_id = f"user-{uuid.uuid4()}"
            db.execute(
                "INSERT INTO users(user_id, tenant_id, email, password_hash, role, active, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, tenant_id, normalized, _hash_password(password), role, 1, now, now),
            )
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row)

    def initial_owner_setup_available(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"] == 0

    def create_initial_owner(self, email: str, password: str, tenant_name: str = "NEXUS", project_id: str = "local") -> dict[str, Any]:
        """Atomically create the sole first owner; never serves as an account recovery bypass."""
        normalized = email.strip().lower()
        if "@" not in normalized or len(normalized) > 320:
            raise ValueError("a valid email address is required")
        password_hash = _hash_password(password)
        tenant_id = _safe_slug(tenant_name)
        safe_project = _safe_slug(project_id)
        now = utc_now()
        user_id = f"user-{uuid.uuid4()}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]:
                raise PermissionError("initial owner setup is unavailable after the first product account exists")
            db.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", (tenant_id, tenant_name.strip() or "NEXUS", now))
            db.execute(
                "INSERT INTO users(user_id, tenant_id, email, password_hash, role, active, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, tenant_id, normalized, password_hash, "owner", 1, now, now),
            )
            db.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", (safe_project, tenant_id, "Primary command center", now, now))
            db.execute("INSERT INTO project_memberships(project_id, user_id, role, created_at) VALUES(?,?,?,?)", (safe_project, user_id, "owner", now))
            user = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            project = db.execute("SELECT * FROM projects WHERE project_id=?", (safe_project,)).fetchone()
        return {"tenant": {"tenant_id": tenant_id, "display_name": tenant_name.strip() or "NEXUS"}, "user": dict(user), "project": dict(project)}

    def create_registered_owner(self, email: str, password: str) -> dict[str, Any]:
        """Create an isolated tenant owner without joining or revealing another tenant."""
        normalized = email.strip().lower()
        if "@" not in normalized or len(normalized) > 320:
            raise ValueError("a valid email address is required")
        password_hash = _hash_password(password)
        tenant_id = f"tenant-{uuid.uuid4().hex[:16]}"
        project_id = f"workspace-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        user_id = f"user-{uuid.uuid4()}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE email=?", (normalized,)).fetchone():
                raise ValueError("a product account already exists for this email; sign in instead")
            db.execute("INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", (tenant_id, "NEXUS workspace", now))
            db.execute(
                "INSERT INTO users(user_id, tenant_id, email, password_hash, role, active, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, tenant_id, normalized, password_hash, "owner", 1, now, now),
            )
            db.execute("INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)", (project_id, tenant_id, "Primary command center", now, now))
            db.execute("INSERT INTO project_memberships(project_id, user_id, role, created_at) VALUES(?,?,?,?)", (project_id, user_id, "owner", now))
            user = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            project = db.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        return {"tenant": {"tenant_id": tenant_id, "display_name": "NEXUS workspace"}, "user": dict(user), "project": dict(project)}

    def authenticate_password(self, email: str, password: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=? AND active=1", (email.strip().lower(),)).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            return None
        return dict(row)

    def create_session(self, user_id: str, hours: int) -> tuple[str, dict[str, Any]]:
        raw_token = secrets.token_urlsafe(32)
        now = utc_now()
        session = {
            "session_id": f"session-{uuid.uuid4()}",
            "user_id": user_id,
            "token_hash": hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            "created_at": now,
            "expires_at": utc_after(hours * 3600),
        }
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions(session_id, user_id, token_hash, created_at, expires_at) VALUES(?,?,?,?,?)",
                (session["session_id"], session["user_id"], session["token_hash"], session["created_at"], session["expires_at"]),
            )
        return raw_token, {key: value for key, value in session.items() if key != "token_hash"}

    def resolve_session(self, raw_token: str) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.connect() as db:
            row = db.execute(
                """SELECT u.user_id, u.tenant_id, u.email, u.role, u.active, s.session_id, s.expires_at
                   FROM sessions s JOIN users u ON u.user_id=s.user_id
                   WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.active=1""",
                (token_hash, utc_now()),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, raw_token: str) -> None:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.connect() as db:
            db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (utc_now(), token_hash))

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def set_user_password(self, user_id: str, password: str) -> dict[str, Any] | None:
        """Replace one user's password hash (PBKDF2, minimum 12 characters); nothing else changes."""
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "UPDATE users SET password_hash=?, updated_at=? WHERE user_id=?",
                (_hash_password(password), now, user_id),
            )
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def revoke_user_sessions(self, user_id: str) -> int:
        with self.connect() as db:
            cursor = db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (utc_now(), user_id))
            return cursor.rowcount

    def create_project(self, tenant_id: str, project_id: str, display_name: str) -> dict[str, Any]:
        safe_project = _safe_slug(project_id)
        now = utc_now()
        with self.connect() as db:
            existing = db.execute("SELECT * FROM projects WHERE project_id=?", (safe_project,)).fetchone()
            if existing and existing["tenant_id"] != tenant_id:
                raise PermissionError("project identifier is already owned by another tenant")
            db.execute(
                """INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(project_id) DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at""",
                (safe_project, tenant_id, display_name.strip() or safe_project, now, now),
            )
            row = db.execute("SELECT * FROM projects WHERE project_id=?", (safe_project,)).fetchone()
        return dict(row)

    def grant_project_member(self, project_id: str, user_id: str, role: str) -> None:
        if role not in {"owner", "operator", "viewer"}:
            raise ValueError("invalid project role")
        with self.connect() as db:
            db.execute(
                """INSERT INTO project_memberships(project_id, user_id, role, created_at) VALUES(?,?,?,?)
                   ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role""",
                (project_id, user_id, role, utc_now()),
            )

    def adopt_legacy_projects(self, tenant_id: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT project_id FROM projects WHERE tenant_id='legacy'").fetchall()
            project_ids = [row["project_id"] for row in rows]
            if project_ids:
                marks = ",".join("?" for _ in project_ids)
                db.execute(f"UPDATE projects SET tenant_id=?, updated_at=? WHERE project_id IN ({marks})", (tenant_id, utc_now(), *project_ids))
                db.execute(f"UPDATE missions SET tenant_id=? WHERE project_id IN ({marks})", (tenant_id, *project_ids))
        return project_ids

    def list_projects_for_user(self, user_id: str, tenant_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.project_id, p.display_name, p.created_at, p.updated_at, pm.role
                   FROM projects p JOIN project_memberships pm ON pm.project_id=p.project_id
                   WHERE pm.user_id=? AND p.tenant_id=? ORDER BY p.updated_at DESC, p.project_id""",
                (user_id, tenant_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def project_role(self, user_id: str, tenant_id: str, project_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT pm.role FROM project_memberships pm JOIN projects p ON p.project_id=pm.project_id
                   WHERE pm.user_id=? AND pm.project_id=? AND p.tenant_id=?""",
                (user_id, _safe_slug(project_id), tenant_id),
            ).fetchone()
        return row["role"] if row else None

    # ---- Agent foundation records (PS002 Phase 2A: identity + declared policy only) ----

    def create_agent(
        self,
        *,
        agent_id: str,
        tenant_id: str,
        project_id: str,
        display_name: str,
        status: str = "ACTIVE",
    ) -> dict[str, Any]:
        if status not in {"ACTIVE", "FLAGGED", "HALTED", "RETIRED"}:
            raise ValueError("invalid agent status")
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO agents(agent_id, tenant_id, project_id, display_name, status, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (agent_id, tenant_id, project_id, display_name, status, now, now),
            )
            row = db.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return dict(row)

    def get_agent(self, tenant_id: str, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM agents WHERE agent_id=? AND tenant_id=?", (agent_id, tenant_id)).fetchone()
        return dict(row) if row else None

    def list_agents(self, tenant_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if project_id:
                rows = db.execute("SELECT * FROM agents WHERE tenant_id=? AND project_id=? ORDER BY created_at DESC", (tenant_id, _safe_slug(project_id))).fetchall()
            else:
                rows = db.execute("SELECT * FROM agents WHERE tenant_id=? ORDER BY created_at DESC", (tenant_id,)).fetchall()
        return [dict(row) for row in rows]

    def create_agent_policy(
        self,
        *,
        policy_id: str,
        agent_id: str,
        tenant_id: str,
        declared_capabilities: list[str],
        allowed_operations: list[str],
        prohibited_operations: list[str],
        scope: dict,
        expected_behaviour: str,
        version: int,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO agent_policies(policy_id, agent_id, tenant_id, declared_capabilities_json, allowed_operations_json, prohibited_operations_json, scope_json, expected_behaviour, version, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    policy_id,
                    agent_id,
                    tenant_id,
                    json.dumps(declared_capabilities),
                    json.dumps(allowed_operations),
                    json.dumps(prohibited_operations),
                    json.dumps(scope),
                    expected_behaviour,
                    version,
                    now,
                ),
            )
            row = db.execute("SELECT * FROM agent_policies WHERE policy_id=?", (policy_id,)).fetchone()
        return dict(row)

    def get_latest_agent_policy(self, tenant_id: str, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_policies WHERE agent_id=? AND tenant_id=? ORDER BY version DESC LIMIT 1",
                (agent_id, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def list_agent_policies(self, tenant_id: str, agent_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM agent_policies WHERE agent_id=? AND tenant_id=? ORDER BY version DESC", (agent_id, tenant_id)).fetchall()
        return [dict(row) for row in rows]

    def update_agent_status(self, tenant_id: str, agent_id: str, status: str) -> dict[str, Any] | None:
        if status not in {"ACTIVE", "FLAGGED", "HALTED", "RETIRED"}:
            raise ValueError("invalid agent status")
        now = utc_now()
        with self.connect() as db:
            db.execute("UPDATE agents SET status=?, updated_at=? WHERE agent_id=? AND tenant_id=?", (status, now, agent_id, tenant_id))
            row = db.execute("SELECT * FROM agents WHERE agent_id=? AND tenant_id=?", (agent_id, tenant_id)).fetchone()
        return dict(row) if row else None

    def record_agent_action(
        self,
        *,
        action_id: str,
        agent_id: str,
        tenant_id: str,
        project_id: str,
        operation: str,
        target_resource: str | None,
        requested_capability: str | None,
        observed_parameters_json: str,
        integrity_decision: str,
        integrity_reason: str | None,
        evidence_json: str | None,
        policy_version: int | None,
        evaluated_at: str,
        # An agent action that does not declare what was actually observed has
        # observed nothing. Defaulting to OBSERVED stamped an observation
        # classification onto rows that carried no observation at all, which
        # the truth ledger then reported as if it were real. Absence of a
        # declaration is UNKNOWN, never OBSERVED.
        observation_reality: str = "UNKNOWN",
        receipt_json: str | None = None,
    ) -> dict[str, Any]:
        if observation_reality not in ALLOWED_REALITY_STATES:
            raise ValueError(
                f"invalid observation_reality '{observation_reality}': must be one of "
                f"{sorted(ALLOWED_REALITY_STATES)}"
            )
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO agent_actions(action_id, agent_id, tenant_id, project_id, operation, target_resource,
                    requested_capability, observed_parameters_json, observation_reality, receipt_json,
                    integrity_decision, integrity_reason, evidence_json, policy_version, evaluated_at, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    action_id,
                    agent_id,
                    tenant_id,
                    project_id,
                    operation,
                    target_resource,
                    requested_capability,
                    observed_parameters_json,
                    observation_reality,
                    receipt_json,
                    integrity_decision,
                    integrity_reason,
                    evidence_json,
                    policy_version,
                    evaluated_at,
                    now,
                ),
            )
            row = db.execute("SELECT * FROM agent_actions WHERE action_id=?", (action_id,)).fetchone()
        return dict(row)

    def get_agent_action(self, tenant_id: str, action_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT a.* FROM agent_actions a JOIN agents ag ON ag.agent_id=a.agent_id WHERE a.action_id=? AND a.tenant_id=?",
                (action_id, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def list_agent_actions(self, tenant_id: str, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_actions WHERE agent_id=? AND tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (agent_id, tenant_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_agent_integrity_event(
        self,
        tenant_id: str,
        agent_id: str,
        event_type: str,
        payload_json: str,
        integrity_decision: str | None = None,
    ) -> int:
        now = utc_now()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO agent_integrity_events(agent_id, tenant_id, event_type, payload_json, integrity_decision, created_at) VALUES(?,?,?,?,?,?)",
                (agent_id, tenant_id, event_type, payload_json, integrity_decision, now),
            )
            return cursor.lastrowid

    def list_agent_integrity_events(self, tenant_id: str, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_integrity_events WHERE agent_id=? AND tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (agent_id, tenant_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---- Mission and queue records ------------------------------------------

    def ensure_project(self, project_id: str, display_name: str | None = None) -> None:
        """Legacy helper retained for local migration only; authenticated code uses create_project."""
        safe_project = _safe_slug(project_id)
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(project_id) DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at""",
                (safe_project, "legacy", display_name or safe_project, now, now),
            )

    def create_mission(
        self,
        *,
        mission_id: str,
        tenant_id: str,
        project_id: str,
        scope: str,
        intent: str,
        mode: str,
        capabilities: list[str],
        store_root: str,
        submission: dict[str, Any],
        max_attempts: int,
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO missions(mission_id, tenant_id, project_id, scope, intent, mode, requested_capabilities_json, submission_json, status, reality, verification_status, action_state, store_root, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mission_id, tenant_id, project_id, scope, intent, mode, json.dumps(capabilities), json.dumps(submission), "QUEUED", "PLANNED", "PENDING", "PENDING", store_root, now, now),
            )
            db.execute(
                """INSERT INTO mission_queue(mission_id, status, attempts, max_attempts, available_at, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (mission_id, "QUEUED", 0, max_attempts, now, now, now),
            )
        self.add_event(mission_id, "mission_queued", {"mode": mode, "capabilities": capabilities, "scope": scope})

    def claim_next(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = utc_now()
        claimed: dict[str, Any] | None = None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = db.execute(
                "SELECT mission_id FROM mission_queue WHERE status='LEASED' AND lease_expires_at<?",
                (now,),
            ).fetchall()
            expired_ids = [row["mission_id"] for row in expired]
            if expired_ids:
                placeholders = ",".join("?" for _ in expired_ids)
                db.execute(f"UPDATE mission_queue SET status='QUEUED', lease_owner=NULL, lease_expires_at=NULL, available_at=?, updated_at=? WHERE mission_id IN ({placeholders})", (now, now, *expired_ids))
                db.execute(f"UPDATE missions SET status='QUEUED', updated_at=? WHERE mission_id IN ({placeholders})", (now, *expired_ids))
            candidate = db.execute(
                """SELECT q.mission_id FROM mission_queue q JOIN missions m ON m.mission_id=q.mission_id
                   WHERE q.status='QUEUED' AND m.status='QUEUED' AND q.available_at<=? ORDER BY q.created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if candidate:
                mission_id = candidate["mission_id"]
                updated = db.execute(
                    """UPDATE mission_queue SET status='LEASED', attempts=attempts+1, lease_owner=?, lease_expires_at=?, updated_at=?
                       WHERE mission_id=? AND status='QUEUED'""",
                    (worker_id, utc_after(lease_seconds), now, mission_id),
                )
                if updated.rowcount:
                    db.execute("UPDATE missions SET status='EXECUTING', updated_at=? WHERE mission_id=?", (now, mission_id))
                    claimed = self._mission_row(db.execute(self._mission_select() + " WHERE m.mission_id=?", (mission_id,)).fetchone())
        if claimed:
            self.add_event(claimed["mission_id"], "mission_executing", {"worker_id": worker_id, "attempt": claimed.get("queue", {}).get("attempts")})
        return claimed

    def finish_queue(self, mission_id: str, worker_id: str) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE mission_queue SET status='COMPLETED', lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE mission_id=? AND lease_owner=?""",
                (utc_now(), mission_id, worker_id),
            )
        self.add_event(mission_id, "queue_settled", {"worker_id": worker_id})

    def retry_or_fail_queue(self, mission_id: str, worker_id: str, error: str) -> bool:
        retry = False
        now = utc_now()
        with self.connect() as db:
            row = db.execute("SELECT attempts, max_attempts FROM mission_queue WHERE mission_id=? AND lease_owner=?", (mission_id, worker_id)).fetchone()
            if row is None:
                return False
            retry = row["attempts"] < row["max_attempts"]
            if retry:
                db.execute(
                    """UPDATE mission_queue SET status='QUEUED', lease_owner=NULL, lease_expires_at=NULL, available_at=?, last_error=?, updated_at=? WHERE mission_id=?""",
                    (utc_after(min(30, 2 ** row["attempts"])), error[:2000], now, mission_id),
                )
                db.execute("UPDATE missions SET status='QUEUED', error=?, updated_at=? WHERE mission_id=?", (error[:2000], now, mission_id))
            else:
                db.execute(
                    """UPDATE mission_queue SET status='FAILED', lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=? WHERE mission_id=?""",
                    (error[:2000], now, mission_id),
                )
                db.execute("UPDATE missions SET status='FAILED', reality='UNKNOWN', verification_status='FAILED', error=?, updated_at=? WHERE mission_id=?", (error[:2000], now, mission_id))
        self.add_event(mission_id, "mission_retry_scheduled" if retry else "mission_failed", {"worker_id": worker_id, "error": error[:2000]})
        return retry

    def mark_running(self, mission_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE missions SET status='EXECUTING', updated_at=? WHERE mission_id=?", (utc_now(), mission_id))
        self.add_event(mission_id, "mission_executing", {"compatibility": True})

    def save_result(self, mission_id: str, result: dict[str, Any]) -> None:
        mission = result.get("mission", {})
        verification = mission.get("verification", {}).get("completion_verification", {})
        execution = result.get("execution", {})
        packet = mission.get("action_packet", execution.get("action_packet", {})) or {}
        now = utc_now()
        with self.connect() as db:
            mission_row = db.execute("SELECT tenant_id, project_id FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            if mission_row is None:
                raise ValueError("mission does not exist")
            tenant_id, project_id = mission_row["tenant_id"], mission_row["project_id"]
            db.execute(
                """UPDATE missions SET status=?, reality=?, verification_status=?, action_state=?, external_invocations=?, result_json=?, error=NULL, updated_at=? WHERE mission_id=?""",
                (
                    mission.get("state", "UNKNOWN"),
                    mission.get("reality", "UNKNOWN"),
                    verification.get("status", "UNKNOWN"),
                    packet.get("state", "PENDING"),
                    int(execution.get("external_invocations", result.get("external_invocations", 0)) or 0),
                    json.dumps(result, default=str),
                    now,
                    mission_id,
                ),
            )
            db.execute("DELETE FROM mission_evidence WHERE mission_id=?", (mission_id,))
            normalized = execution.get("normalized_observations", []) or mission.get("normalized_observations", [])
            receipts = execution.get("receipts", []) or []
            receipt_by_provider = {receipt.get("provider"): receipt for receipt in receipts if isinstance(receipt, dict)}
            for observation in normalized:
                db.execute(
                    """INSERT INTO mission_evidence(mission_id, capability, provider, observation_id, verification_state, reality, receipt_json, observation_json, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        mission_id,
                        observation.get("capability"),
                        observation.get("provider"),
                        observation.get("observation_id"),
                        observation.get("verification_state"),
                        observation.get("reality"),
                        json.dumps(receipt_by_provider.get(observation.get("provider"), {}), default=str),
                        json.dumps(observation, default=str),
                        now,
                    ),
                )
                observation_id = observation.get("observation_id") or f"observation-{uuid.uuid4()}"
                db.execute(
                    """INSERT OR REPLACE INTO observations(observation_id, tenant_id, project_id, mission_id, provider, capability, reality_state, verification_state, payload_json, observed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (observation_id, tenant_id, project_id, mission_id, observation.get("provider"), observation.get("capability"), observation.get("reality", "UNKNOWN"), observation.get("verification_state", "UNKNOWN"), json.dumps(observation, default=str), now),
                )
                db.execute(
                    """INSERT INTO memory_items(memory_id, tenant_id, project_id, mission_id, source, content_json, provenance_json, confidence, freshness_at, reality_state, status, conflict_key, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"memory-{uuid.uuid4()}", tenant_id, project_id, mission_id, observation.get("provider") or "unknown-provider", json.dumps(observation, default=str), json.dumps(receipt_by_provider.get(observation.get("provider"), {}), default=str), "HIGH" if observation.get("verification_state") == "VERIFIED" else "MEDIUM", now, observation.get("reality", "UNKNOWN"), "active", observation.get("capability"), now),
                )
            db.execute("DELETE FROM provider_receipts WHERE mission_id=?", (mission_id,))
            for receipt in receipts:
                if isinstance(receipt, dict):
                    db.execute(
                        "INSERT INTO provider_receipts(tenant_id, project_id, mission_id, provider, receipt_json, created_at) VALUES(?,?,?,?,?,?)",
                        (tenant_id, project_id, mission_id, receipt.get("provider") or "unknown-provider", json.dumps(receipt, default=str), now),
                    )
            db.execute(
                """INSERT INTO outcomes(outcome_id, tenant_id, project_id, mission_id, state, reality_state, verification_state, summary_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(mission_id) DO UPDATE SET state=excluded.state, reality_state=excluded.reality_state, verification_state=excluded.verification_state, summary_json=excluded.summary_json, updated_at=excluded.updated_at""",
                (f"outcome-{mission_id}", tenant_id, project_id, mission_id, mission.get("state", "UNKNOWN"), mission.get("reality", "UNKNOWN"), verification.get("status", "UNKNOWN"), json.dumps({"intent": mission.get("intent"), "action_state": packet.get("state", "PENDING"), "external_invocations": execution.get("external_invocations", 0)}, default=str), now, now),
            )
        self.add_event(mission_id, "mission_completed", {"status": mission.get("state"), "verification": verification.get("status"), "external_invocations": execution.get("external_invocations", 0)})

    def save_failure(self, mission_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE missions SET status='FAILED', reality='UNKNOWN', verification_status='FAILED', error=?, updated_at=? WHERE mission_id=?", (error[:2000], utc_now(), mission_id))
        self.add_event(mission_id, "mission_failed", {"error": error[:2000]})

    def add_event(self, mission_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO mission_events(mission_id, event_type, payload_json, created_at) VALUES(?,?,?,?)", (mission_id, event_type, json.dumps(payload, default=str), utc_now()))

    def add_audit_event(self, tenant_id: str, actor_user_id: str | None, action: str, outcome: str, detail: dict[str, Any], project_id: str | None = None, mission_id: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_events(tenant_id, project_id, actor_user_id, mission_id, action, outcome, detail_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (tenant_id, project_id, actor_user_id, mission_id, action, outcome, json.dumps(detail, default=str), utc_now()),
            )

    def list_memory(self, tenant_id: str, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM memory_items WHERE tenant_id=? AND project_id=? ORDER BY created_at DESC LIMIT ?", (tenant_id, _safe_slug(project_id), limit)).fetchall()
        return [{**dict(row), "content": json.loads(row["content_json"]), "provenance": json.loads(row["provenance_json"])} for row in rows]

    def set_memory_lifecycle(self, tenant_id: str, project_id: str, memory_id: str, actor_user_id: str, action: str, note: str | None = None) -> dict[str, Any] | None:
        if action not in {"retire", "restore", "annotate"}:
            raise ValueError("unsupported memory lifecycle action")
        now = utc_now()
        with self.connect() as db:
            row = db.execute("SELECT * FROM memory_items WHERE memory_id=? AND tenant_id=? AND project_id=?", (memory_id, tenant_id, _safe_slug(project_id))).fetchone()
            if row is None:
                return None
            if action == "retire":
                db.execute("UPDATE memory_items SET status='superseded', retired_at=?, retired_by_user_id=?, user_note=COALESCE(?, user_note), updated_at=? WHERE memory_id=?", (now, actor_user_id, note, now, memory_id))
            elif action == "restore":
                db.execute("UPDATE memory_items SET status='active', retired_at=NULL, retired_by_user_id=NULL, user_note=COALESCE(?, user_note), updated_at=? WHERE memory_id=?", (note, now, memory_id))
            else:
                db.execute("UPDATE memory_items SET user_note=?, updated_at=? WHERE memory_id=?", (note or "", now, memory_id))
            updated = db.execute("SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
        result = dict(updated)
        result["content"] = json.loads(result["content_json"])
        result["provenance"] = json.loads(result["provenance_json"])
        return result

    def project_context(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        safe_project = _safe_slug(project_id)
        with self.connect() as db:
            missions = [self._mission_row(row) for row in db.execute(self._mission_select() + " WHERE m.tenant_id=? AND m.project_id=? ORDER BY m.updated_at DESC LIMIT 8", (tenant_id, safe_project)).fetchall()]
            memories = self.list_memory(tenant_id, safe_project, 8)
            outcomes = self.list_outcomes(tenant_id, safe_project, 5)
        latest = missions[0] if missions else None
        active = [mission for mission in missions if mission and mission.get("status") in {"QUEUED", "EXECUTING", "PAUSED"}]
        failed = [mission for mission in missions if mission and mission.get("status") in {"FAILED", "BLOCKED", "PARTIAL"}]
        if latest is None:
            next_action = "State an objective to create the first durable mission."
        elif active:
            next_action = "Monitor the active durable mission; the worker will persist evidence or an explicit blocker."
        elif failed:
            next_action = "Inspect the persisted failure evidence and decide whether to continue from the checkpoint or revise the objective."
        else:
            next_action = "Review the latest verified outcome, then continue from its checkpoint or state the next objective."
        return {
            "project_id": safe_project,
            "current_objective": latest.get("intent") if latest else None,
            "latest_mission": latest,
            "active_missions": active,
            "blockers": [{"mission_id": item["mission_id"], "status": item["status"], "error": item.get("error")} for item in failed],
            "discovered": [{"memory_id": item["memory_id"], "source": item["source"], "reality_state": item["reality_state"], "verification_state": item["content"].get("verification_state", "UNKNOWN"), "status": item["status"], "user_note": item.get("user_note")} for item in memories],
            "outcomes": outcomes,
            "next_action": next_action,
            "continuity": {"memory_count": len(memories), "mission_count": len(missions), "active_count": len(active), "blocker_count": len(failed)},
        }

    def list_outcomes(self, tenant_id: str, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM outcomes WHERE tenant_id=? AND project_id=? ORDER BY updated_at DESC LIMIT ?", (tenant_id, _safe_slug(project_id), limit)).fetchall()
        return [{**dict(row), "summary": json.loads(row["summary_json"])} for row in rows]

    def list_audit_events(self, tenant_id: str, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            if project_id:
                rows = db.execute("SELECT * FROM audit_events WHERE tenant_id=? AND project_id=? ORDER BY audit_id DESC LIMIT ?", (tenant_id, _safe_slug(project_id), limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM audit_events WHERE tenant_id=? ORDER BY audit_id DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]

    def record_checkpoint(self, tenant_id: str, project_id: str, mission_id: str, path: str, checksum: str | None, state: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO checkpoints(checkpoint_id, tenant_id, project_id, mission_id, checkpoint_path, checksum, state, created_at) VALUES(?,?,?,?,?,?,?,?)", (f"checkpoint-{uuid.uuid4()}", tenant_id, _safe_slug(project_id), mission_id, path, checksum, state, utc_now()))

    def list_checkpoints(self, tenant_id: str, mission_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM checkpoints WHERE tenant_id=? AND mission_id=? ORDER BY created_at DESC", (tenant_id, mission_id)).fetchall()
        return [dict(row) for row in rows]

    def control_mission(self, mission_id: str, state: str) -> bool:
        if state not in {"PAUSED", "QUEUED", "CANCELLED"}:
            raise ValueError("unsupported mission control state")
        now = utc_now()
        with self.connect() as db:
            mission = db.execute("SELECT status FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            if mission is None or mission["status"] == "EXECUTING":
                return False
            if state == "CANCELLED":
                db.execute("UPDATE mission_queue SET status='FAILED', lease_owner=NULL, lease_expires_at=NULL, last_error='cancelled by authenticated operator', updated_at=? WHERE mission_id=?", (now, mission_id))
            elif state == "QUEUED":
                db.execute("UPDATE mission_queue SET status='QUEUED', available_at=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE mission_id=?", (now, now, mission_id))
            db.execute("UPDATE missions SET status=?, updated_at=? WHERE mission_id=?", (state, now, mission_id))
        self.add_event(mission_id, f"mission_{state.lower()}", {"control_state": state})
        return True

    @staticmethod
    def _mission_select() -> str:
        return """SELECT m.*, q.status AS queue_status, q.attempts AS queue_attempts, q.max_attempts AS queue_max_attempts,
                         q.available_at AS queue_available_at, q.lease_owner AS queue_lease_owner, q.lease_expires_at AS queue_lease_expires_at, q.last_error AS queue_last_error
                  FROM missions m LEFT JOIN mission_queue q ON q.mission_id=m.mission_id"""

    def _mission_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["requested_capabilities"] = json.loads(result.pop("requested_capabilities_json"))
        raw_result = result.pop("result_json")
        result["result"] = json.loads(raw_result) if raw_result else None
        raw_submission = result.pop("submission_json", None)
        result["submission"] = json.loads(raw_submission) if raw_submission else None
        result["queue"] = {
            "status": result.pop("queue_status", None),
            "attempts": result.pop("queue_attempts", None),
            "max_attempts": result.pop("queue_max_attempts", None),
            "available_at": result.pop("queue_available_at", None),
            "lease_expires_at": result.pop("queue_lease_expires_at", None),
            "last_error": result.pop("queue_last_error", None),
        }
        result.pop("queue_lease_owner", None)
        return result

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(self._mission_select() + " WHERE m.mission_id=?", (mission_id,)).fetchone()
        return self._mission_row(row)

    def list_missions(self, project_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(self._mission_select() + " WHERE m.project_id=? ORDER BY m.created_at DESC LIMIT ?", (_safe_slug(project_id), limit)).fetchall()
        return [self._mission_row(row) for row in rows]

    def evidence(self, mission_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM mission_evidence WHERE mission_id=? ORDER BY evidence_id", (mission_id,)).fetchall()
        records = []
        for row in rows:
            item = dict(row)
            item["receipt"] = json.loads(item.pop("receipt_json") or "{}")
            item["observation"] = json.loads(item.pop("observation_json") or "{}")
            records.append(item)
        return records

    def events(self, mission_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM mission_events WHERE mission_id=? ORDER BY event_id", (mission_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def queue_health(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute("SELECT status, COUNT(*) AS count FROM mission_queue GROUP BY status").fetchall()
        counts = {row["status"].lower(): row["count"] for row in rows}
        return {"queued": counts.get("queued", 0), "leased": counts.get("leased", 0), "completed": counts.get("completed", 0), "failed": counts.get("failed", 0)}

    # ---- Phase 9: durable worker registry + lease model -------------------
    # Lifecycle: STARTING -> IDLE -> BUSY -> IDLE ... -> STOPPING -> STOPPED.
    # Liveness is computed, never stored: a worker whose heartbeat is older
    # than stale_seconds reports STALE (unless explicitly STOPPED). A stale
    # worker's unfinished claimed tasks are recoverable — worker disappearance
    # is a WORKER FAILURE, never an automatic TASK FAILURE.

    WORKER_LIFECYCLE_STATES = ("STARTING", "IDLE", "BUSY", "STOPPING", "STOPPED", "ACTIVE")

    def register_worker(
        self,
        worker_id: str,
        tenant_id: str = "default",
        project_id: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Durably register a worker (idempotent revive: STARTING, keeps started_at)."""
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO worker_heartbeats(
                       worker_id, status, details_json, started_at, heartbeat_at,
                       tenant_id, project_id, capabilities_json, current_workflow_id, current_task_id
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(worker_id) DO UPDATE SET
                       status='STARTING', heartbeat_at=excluded.heartbeat_at,
                       tenant_id=excluded.tenant_id, project_id=excluded.project_id,
                       capabilities_json=excluded.capabilities_json,
                       current_workflow_id=NULL, current_task_id=NULL""",
                (worker_id, "STARTING", json.dumps({"lifecycle": "registered"}), now, now,
                 tenant_id, project_id, json.dumps(capabilities or []), None, None),
            )
            row = db.execute("SELECT * FROM worker_heartbeats WHERE worker_id=?", (worker_id,)).fetchone()
        return self._normalize_worker_row(dict(row))

    def heartbeat_worker(
        self,
        worker_id: str,
        status: str = "ACTIVE",
        details: dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        capabilities: list[str] | None = None,
        current_workflow_id: str | None = None,
        current_task_id: str | None = None,
        clear_current_task: bool = False,
    ) -> None:
        """Renew a worker's lease. Preserves started_at and prior columns unless given."""
        now = utc_now()
        with self.connect() as db:
            existing = db.execute(
                "SELECT tenant_id, project_id, capabilities_json, current_workflow_id, current_task_id "
                "FROM worker_heartbeats WHERE worker_id=?", (worker_id,)).fetchone()
            if existing is None:
                db.execute(
                    """INSERT INTO worker_heartbeats(
                           worker_id, status, details_json, started_at, heartbeat_at,
                           tenant_id, project_id, capabilities_json, current_workflow_id, current_task_id
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (worker_id, status, json.dumps(details or {}), now, now,
                     tenant_id or "default", project_id or "",
                     json.dumps(capabilities or []), current_workflow_id, current_task_id),
                )
                return
            prev = dict(existing)
            db.execute(
                """UPDATE worker_heartbeats SET status=?, details_json=?, heartbeat_at=?,
                       tenant_id=?, project_id=?, capabilities_json=?,
                       current_workflow_id=?, current_task_id=?
                   WHERE worker_id=?""",
                (status, json.dumps(details or {}), now,
                 tenant_id if tenant_id is not None else prev["tenant_id"],
                 project_id if project_id is not None else prev["project_id"],
                 json.dumps(capabilities) if capabilities is not None else prev["capabilities_json"],
                 current_workflow_id if current_workflow_id is not None else prev["current_workflow_id"],
                 None if clear_current_task else (
                     current_task_id if current_task_id is not None else prev["current_task_id"]),
                 worker_id),
            )

    def stop_worker(self, worker_id: str, reason: str = "operator stop") -> bool:
        """Persist an orderly worker shutdown (STOPPED). Never reported STALE afterwards."""
        now = utc_now()
        with self.connect() as db:
            cur = db.execute(
                "UPDATE worker_heartbeats SET status='STOPPED', details_json=?, heartbeat_at=?, "
                "current_workflow_id=NULL, current_task_id=NULL WHERE worker_id=?",
                (json.dumps({"lifecycle": "stopped", "reason": reason}), now, worker_id),
            )
            return cur.rowcount > 0

    def _normalize_worker_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        try:
            out["details"] = json.loads(out.get("details_json") or "{}")
        except (ValueError, TypeError):
            out["details"] = {}
        try:
            out["capabilities"] = json.loads(out.get("capabilities_json") or "[]")
        except (ValueError, TypeError):
            out["capabilities"] = []
        out["last_heartbeat"] = out.get("heartbeat_at")
        return out

    @staticmethod
    def _worker_liveness(status: str, heartbeat_at: str | None, threshold: str) -> str:
        if status in ("STOPPED", "STOPPING"):
            return status
        if not heartbeat_at or heartbeat_at < threshold:
            return "STALE"
        return status

    def list_workers(
        self,
        tenant_id: str | None = None,
        stale_seconds: int = 30,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Durable worker registry with computed liveness (ACTIVE/IDLE/BUSY vs STALE vs STOPPED)."""
        threshold = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
        with self.connect() as db:
            if tenant_id:
                rows = db.execute(
                    "SELECT * FROM worker_heartbeats WHERE tenant_id=? OR tenant_id IS NULL "
                    "ORDER BY heartbeat_at DESC LIMIT ?",
                    (tenant_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM worker_heartbeats ORDER BY heartbeat_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        workers = []
        for row in rows:
            w = self._normalize_worker_row(dict(row))
            w["liveness"] = self._worker_liveness(w.get("status", ""), w.get("heartbeat_at"), threshold)
            workers.append(w)
        return workers

    def worker_health(self, stale_seconds: int = 15) -> dict[str, Any]:
        threshold = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
        with self.connect() as db:
            rows = db.execute("SELECT * FROM worker_heartbeats ORDER BY heartbeat_at DESC").fetchall()
        workers = [self._normalize_worker_row(dict(row)) for row in rows]
        for w in workers:
            w["liveness"] = self._worker_liveness(w.get("status", ""), w.get("heartbeat_at"), threshold)
        active = [w for w in workers if w["liveness"] in ("ACTIVE", "IDLE", "BUSY", "STARTING")]
        return {"status": "ACTIVE" if active else "UNAVAILABLE", "active_count": len(active), "workers": workers[:10], "stale_after_seconds": stale_seconds}

    def health(self) -> dict[str, Any]:
        self.migrate()
        with self.connect() as db:
            missions = db.execute("SELECT COUNT(*) AS count FROM missions").fetchone()["count"]
            projects = db.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"]
            users = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        return {"database": "sqlite", "path": str(self.path), "missions": missions, "projects": projects, "users": users, "queue": self.queue_health(), "workers": self.worker_health(), "status": "HEALTHY"}

    def provider_history(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        """Return persisted provider execution facts for one tenant only."""
        with self.connect() as db:
            rows = db.execute(
                """SELECT provider, receipt_json, created_at FROM provider_receipts
                   WHERE tenant_id=? ORDER BY receipt_id DESC""",
                (tenant_id,),
            ).fetchall()
        history: dict[str, dict[str, Any]] = {}
        for row in rows:
            provider = row["provider"]
            receipt = json.loads(row["receipt_json"] or "{}")
            current = history.setdefault(provider, {"last_execution": None, "last_successful_execution": None, "last_failure_state": None, "last_verification_state": "UNKNOWN"})
            if current["last_execution"] is None:
                current["last_execution"] = row["created_at"]
                current["last_verification_state"] = receipt.get("verification", "UNKNOWN")
            if receipt.get("status") in {"EXECUTED", "SUCCESS"} and current["last_successful_execution"] is None:
                current["last_successful_execution"] = row["created_at"]
            if receipt.get("failure_state") and current["last_failure_state"] is None:
                current["last_failure_state"] = receipt["failure_state"]
        return history

    def tenant_inspection(self, tenant_id: str) -> dict[str, Any]:
        """Return owner-authorized, tenant-scoped SQLite facts for operator inspection."""
        with self.connect() as db:
            counts = {
                "tenants": db.execute("SELECT COUNT(*) AS count FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "users": db.execute("SELECT COUNT(*) AS count FROM users WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "sessions": db.execute("SELECT COUNT(*) AS count FROM sessions s JOIN users u ON u.user_id=s.user_id WHERE u.tenant_id=?", (tenant_id,)).fetchone()["count"],
                "projects": db.execute("SELECT COUNT(*) AS count FROM projects WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "missions": db.execute("SELECT COUNT(*) AS count FROM missions WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "mission_queue": db.execute("SELECT COUNT(*) AS count FROM mission_queue q JOIN missions m ON m.mission_id=q.mission_id WHERE m.tenant_id=?", (tenant_id,)).fetchone()["count"],
                "mission_events": db.execute("SELECT COUNT(*) AS count FROM mission_events e JOIN missions m ON m.mission_id=e.mission_id WHERE m.tenant_id=?", (tenant_id,)).fetchone()["count"],
                "mission_evidence": db.execute("SELECT COUNT(*) AS count FROM mission_evidence e JOIN missions m ON m.mission_id=e.mission_id WHERE m.tenant_id=?", (tenant_id,)).fetchone()["count"],
                "observations": db.execute("SELECT COUNT(*) AS count FROM observations WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "provider_receipts": db.execute("SELECT COUNT(*) AS count FROM provider_receipts WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "memory_items": db.execute("SELECT COUNT(*) AS count FROM memory_items WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "outcomes": db.execute("SELECT COUNT(*) AS count FROM outcomes WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "checkpoints": db.execute("SELECT COUNT(*) AS count FROM checkpoints WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "audit_events": db.execute("SELECT COUNT(*) AS count FROM audit_events WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
            }
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]
            journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        return {"database": "sqlite", "tenant_id": tenant_id, "row_counts": counts, "integrity_check": integrity, "foreign_keys": bool(foreign_keys), "journal_mode": journal_mode}

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent SQLite backup through the database engine, including WAL state."""
        target = Path(destination).expanduser().resolve()
        if target == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            backup = sqlite3.connect(target)
            try:
                source.backup(backup)
            finally:
                backup.close()
        return target

    def restore_from(self, source_path: str | Path) -> Path:
        """Replace the live database from a verified backup; callers must stop API and workers first."""
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("backup source does not exist")
        if source == self.path.resolve():
            raise ValueError("restore source must differ from the live database")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, self.path)
        self.migrate()
        return self.path

    # ---- Workflows ------------------------------------------------------------

    def create_workflow(
        self,
        *,
        workflow_id: str,
        tenant_id: str,
        project_id: str,
        name: str,
        objective: str,
        scope: str,
        plan_json: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO workflows(workflow_id, tenant_id, project_id, name, objective, scope, status, plan_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (workflow_id, tenant_id, project_id, name, objective, scope, "PENDING", plan_json, now, now),
            )
            row = db.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        return dict(row)

    def get_workflow(self, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workflows WHERE workflow_id=? AND tenant_id=?",
                (workflow_id, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def list_workflows(self, tenant_id: str, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            if project_id:
                rows = db.execute(
                    "SELECT * FROM workflows WHERE tenant_id=? AND project_id=? ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, _safe_slug(project_id), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflows WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def update_workflow_status(self, tenant_id: str, workflow_id: str, status: str, error: str | None = None) -> bool:
        now = utc_now()
        with self.connect() as db:
            row = db.execute("SELECT * FROM workflows WHERE workflow_id=? AND tenant_id=?", (workflow_id, tenant_id)).fetchone()
            if row is None:
                return False
            current = row["status"]
            updates = {"status": status, "updated_at": now}
            if status in ("STARTED", "RUNNING"):
                updates["started_at"] = now
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                updates["completed_at"] = now
                updates["result_json"] = json.dumps({"final_state": status, "error": error}) if status != "COMPLETED" else None
            if error:
                updates["error"] = error
            db.execute(
                "UPDATE workflows SET status=:status, updated_at=:updated_at, started_at=COALESCE(started_at, :started), completed_at=COALESCE(completed_at, :completed), error=COALESCE(:error, error) WHERE workflow_id=:workflow_id AND tenant_id=:tenant_id",
                {
                    "status": status,
                    "updated_at": now,
                    "started": now if status in ("STARTED", "RUNNING") else None,
                    "completed": now if status in ("COMPLETED", "FAILED", "CANCELLED") else None,
                    "error": error,
                    "workflow_id": workflow_id,
                    "tenant_id": tenant_id,
                },
            )
            db.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        return True

    def save_workflow_plan(self, tenant_id: str, workflow_id: str, plan: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE workflows SET plan_json=?, updated_at=? WHERE workflow_id=? AND tenant_id=?",
                (json.dumps(plan), utc_now(), workflow_id, tenant_id),
            )

    def save_workflow_result(self, tenant_id: str, workflow_id: str, result: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE workflows SET result_json=?, updated_at=? WHERE workflow_id=? AND tenant_id=?",
                (json.dumps(result), utc_now(), workflow_id, tenant_id),
            )

    # ---- Workflow Tasks -------------------------------------------------------

    def create_workflow_task(
        self,
        *,
        task_id: str,
        workflow_id: str,
        tenant_id: str,
        project_id: str,
        task_type: str,
        name: str,
        agent_id: str | None,
        required_capabilities: list[str],
        depends_on: list[str],
        max_retries: int = 3,
        input_artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO workflow_tasks(
                    task_id, workflow_id, tenant_id, project_id, task_type, name, agent_id,
                    status, required_capabilities_json, depends_on_json, input_artifacts_json,
                    output_artifacts_json, max_retries, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id, workflow_id, tenant_id, project_id, task_type, name, agent_id,
                    "PENDING", json.dumps(required_capabilities), json.dumps(depends_on),
                    json.dumps(input_artifacts or []), json.dumps([]), max_retries, now, now,
                ),
            )
            row = db.execute("SELECT * FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
        return dict(row)

    def get_workflow_task(self, tenant_id: str, task_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_tasks WHERE task_id=? AND tenant_id=?",
                (task_id, tenant_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["required_capabilities"] = json.loads(result.pop("required_capabilities_json"))
        result["depends_on"] = json.loads(result.pop("depends_on_json"))
        result["input_artifacts"] = json.loads(result.pop("input_artifacts_json"))
        result["output_artifacts"] = json.loads(result.pop("output_artifacts_json"))
        result["error"] = result.get("error")
        return result

    def list_workflow_tasks(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_tasks WHERE workflow_id=? AND tenant_id=? ORDER BY created_at ASC",
                (workflow_id, tenant_id),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["required_capabilities"] = json.loads(result.pop("required_capabilities_json"))
            result["depends_on"] = json.loads(result.pop("depends_on_json"))
            result["input_artifacts"] = json.loads(result.pop("input_artifacts_json"))
            result["output_artifacts"] = json.loads(result.pop("output_artifacts_json"))
            results.append(result)
        return results

    def assign_task_to_agent(self, tenant_id: str, task_id: str, agent_id: str) -> bool:
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_tasks SET agent_id=?, updated_at=? WHERE task_id=? AND tenant_id=?",
                (agent_id, utc_now(), task_id, tenant_id),
            )
            return cur.rowcount > 0

    def update_task_status(
        self,
        tenant_id: str,
        task_id: str,
        status: str,
        reality: str | None = None,
        error: str | None = None,
        result_json: str | None = None,
    ) -> bool:
        now = utc_now()
        with self.connect() as db:
            # Phase 9 fix: params are appended in placeholder order. (The old
            # insert(2, ...) scheme silently cross-wrote error/completed_at/
            # result_json whenever more than one optional was present.)
            sets = ["status=?", "updated_at=?"]
            params: list[Any] = [status, now]
            if status in ("RUNNING",):
                sets.append("started_at=COALESCE(started_at, ?)")
                params.append(now)
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                sets.append("completed_at=COALESCE(completed_at, ?)")
                params.append(now)
            if status in ("FAILED", "BLOCKED") and error is not None:
                sets.append("error=?")
                params.append(error)
            if reality is not None:
                sets.append("reality=?")
                params.append(reality)
            if result_json is not None:
                sets.append("result_json=?")
                params.append(result_json)
            if status == "RUNNING":
                sets.append("retry_count=retry_count")
            params.extend([task_id, tenant_id])
            cur = db.execute(
                f"UPDATE workflow_tasks SET {', '.join(sets)} WHERE task_id=? AND tenant_id=?",
                params,
            )
            return cur.rowcount > 0

    def increment_task_retry(self, tenant_id: str, task_id: str) -> int:
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_tasks SET retry_count=retry_count+1, updated_at=? WHERE task_id=? AND tenant_id=?",
                (utc_now(), task_id, tenant_id),
            )
            row = db.execute("SELECT retry_count FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
            return row["retry_count"] if row else 0

    def add_output_artifact(self, tenant_id: str, task_id: str, artifact_id: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT output_artifacts_json FROM workflow_tasks WHERE task_id=? AND tenant_id=?", (task_id, tenant_id)).fetchone()
            if row is None:
                return False
            current = json.loads(row["output_artifacts_json"] or "[]")
            if artifact_id not in current:
                current.append(artifact_id)
            db.execute(
                "UPDATE workflow_tasks SET output_artifacts_json=?, updated_at=? WHERE task_id=?",
                (json.dumps(current), utc_now(), task_id),
            )
            return True

    # ---- Workflow Artifacts ---------------------------------------------------

    def create_artifact(
        self,
        *,
        artifact_id: str,
        workflow_id: str,
        tenant_id: str,
        project_id: str,
        task_id: str | None,
        agent_id: str | None,
        kind: str,
        name: str,
        content_hash: str,
        parent_artifacts: list[str],
        content_path: str | None = None,
        content_size: int | None = None,
        reality: str = "INFERRED",
        untrusted: bool = True,
        verification_state: str = "UNVERIFIED",
        provenance: list[str] | None = None,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        # Reality classification is producer-declared and must survive the full
        # chain agent -> executor -> engine -> database -> API -> frontend.
        # The database never invents a classification: it validates the
        # producer's value and rejects unknown states loudly instead of
        # silently coercing to OBSERVED.
        if reality not in ALLOWED_REALITY_STATES:
            raise ValueError(
                f"invalid reality '{reality}': must be one of {sorted(ALLOWED_REALITY_STATES)}"
            )
        if verification_state not in ALLOWED_VERIFICATION_STATES:
            raise ValueError(
                f"invalid verification_state '{verification_state}': "
                f"must be one of {sorted(ALLOWED_VERIFICATION_STATES)}"
            )
        now = utc_now()
        with self.connect() as db:
            # Phase 9 idempotency: a retried/recovered attempt that reproduces
            # the identical artifact (same task + kind + content hash) reuses
            # the existing row instead of duplicating it. A crash between
            # artifact production and task completion must not double effects.
            existing = db.execute(
                """SELECT * FROM workflow_artifacts
                   WHERE tenant_id=? AND workflow_id=? AND task_id=? AND kind=? AND content_hash=?
                   ORDER BY created_at ASC LIMIT 1""",
                (tenant_id, workflow_id, task_id, kind, content_hash),
            ).fetchone()
            if existing is not None:
                result = dict(existing)
                result["parent_artifacts"] = json.loads(result.pop("parent_artifacts_json"))
                result["provenance"] = json.loads(result.pop("provenance_json"))
                result["deduplicated"] = True
                return result
            db.execute(
                """INSERT INTO workflow_artifacts(
                    artifact_id, workflow_id, tenant_id, project_id, task_id, agent_id,
                    kind, name, content_hash, content_path, content_size, parent_artifacts_json,
                    provenance_json, created_at, reality, untrusted, verification_state, execution_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    artifact_id, workflow_id, tenant_id, project_id, task_id, agent_id,
                    kind, name, content_hash, content_path, content_size, json.dumps(parent_artifacts),
                    json.dumps(provenance or []), now, reality, 1 if untrusted else 0, verification_state,
                    execution_id,
                ),
            )
            row = db.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        result = dict(row)
        result["parent_artifacts"] = json.loads(result.pop("parent_artifacts_json"))
        result["provenance"] = json.loads(result.pop("provenance_json"))
        result["deduplicated"] = False
        return result

    def get_artifact(self, tenant_id: str, artifact_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND tenant_id=?",
                (artifact_id, tenant_id),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["parent_artifacts"] = json.loads(result.pop("parent_artifacts_json"))
        if "provenance_json" in result:
            result["provenance"] = json.loads(result.pop("provenance_json"))
        return result

    def list_workflow_artifacts(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_artifacts WHERE workflow_id=? AND tenant_id=? ORDER BY created_at ASC",
                (workflow_id, tenant_id),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["parent_artifacts"] = json.loads(result.pop("parent_artifacts_json"))
            if "provenance_json" in result:
                result["provenance"] = json.loads(result.pop("provenance_json"))
            results.append(result)
        return results

    def list_task_artifacts(self, tenant_id: str, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_artifacts WHERE task_id=? AND tenant_id=? ORDER BY created_at ASC",
                (task_id, tenant_id),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["parent_artifacts"] = json.loads(result.pop("parent_artifacts_json"))
            if "provenance_json" in result:
                result["provenance"] = json.loads(result.pop("provenance_json"))
            results.append(result)
        return results

    def get_artifact_lineage(
        self, tenant_id: str, artifact_id: str, max_depth: int = 20
    ) -> dict[str, Any]:
        """Return the durable provenance chain for one artifact.

        Walks parent_artifacts links (artifact IDs) from the database only —
        never fabricates links. Also lists downstream consumers: tasks whose
        output parent chain or artifact_consumed events reference this
        artifact, so the UI can render producer -> consumer chains.
        """
        root = self.get_artifact(tenant_id, artifact_id)
        if root is None:
            raise ValueError(f"artifact '{artifact_id}' not found")
        workflow_id = root["workflow_id"]
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        queue: list[tuple[str, int]] = [(artifact_id, 0)]
        while queue and len(chain) < max_depth:
            current_id, _depth = queue.pop(0)
            if current_id in seen:
                continue
            seen.add(current_id)
            current = self.get_artifact(tenant_id, current_id)
            if current is None:
                chain.append({"artifact_id": current_id, "missing": True})
                continue
            chain.append(current)
            for parent_id in current.get("parent_artifacts", []) or []:
                if parent_id and parent_id not in seen:
                    queue.append((parent_id, _depth + 1))
        # Downstream consumers: tasks producing artifacts that list this
        # artifact as a parent, plus tasks with artifact_consumed events.
        consumers: list[str] = []
        for art in self.list_workflow_artifacts(tenant_id, workflow_id):
            parents = art.get("parent_artifacts", []) or []
            if artifact_id in parents and art["artifact_id"] != artifact_id:
                consumers.append(art["task_id"])
        for evt in self.list_workflow_events(tenant_id, workflow_id, limit=500):
            if evt.get("event_type") != "artifact_consumed":
                continue
            detail = evt.get("detail", {}) or {}
            if detail.get("artifact_ref") in (root.get("name"), root.get("kind"), artifact_id):
                task_id = evt.get("task_id")
                if task_id and task_id not in consumers:
                    consumers.append(task_id)
        return {
            "artifact": root,
            "ancestors": [c for c in chain if c.get("artifact_id") != artifact_id],
            "consumed_by": sorted(c for c in consumers if c),
        }

    # ---- Workflow Events ------------------------------------------------------

    def add_workflow_event(
        self,
        *,
        event_id: str,
        workflow_id: str,
        tenant_id: str,
        project_id: str,
        event_type: str,
        detail: dict[str, Any],
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO workflow_events(
                    event_id, workflow_id, tenant_id, project_id, task_id, agent_id,
                    event_type, detail_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (event_id, workflow_id, tenant_id, project_id, task_id, agent_id, event_type, json.dumps(detail), now),
            )

    def list_workflow_events(self, tenant_id: str, workflow_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_events WHERE workflow_id=? AND tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (workflow_id, tenant_id, limit),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["detail"] = json.loads(result.pop("detail_json"))
            results.append(result)
        return list(reversed(results))

    def workflow_row_count(self, tenant_id: str) -> dict[str, int]:
        with self.connect() as db:
            counts = {
                "workflows": db.execute("SELECT COUNT(*) AS count FROM workflows WHERE tenant_id=?", (tenant_id,)).fetchone()["count"],
                "workflow_tasks": db.execute(
                    "SELECT COUNT(*) AS count FROM workflow_tasks t JOIN workflows w ON w.workflow_id=t.workflow_id WHERE w.tenant_id=?",
                    (tenant_id,),
                ).fetchone()["count"],
                "workflow_artifacts": db.execute(
                    "SELECT COUNT(*) AS count FROM workflow_artifacts a JOIN workflows w ON w.workflow_id=a.workflow_id WHERE w.tenant_id=?",
                    (tenant_id,),
                ).fetchone()["count"],
                 "workflow_events": db.execute(
                     "SELECT COUNT(*) AS count FROM workflow_events e JOIN workflows w ON w.workflow_id=e.workflow_id WHERE w.tenant_id=?",
                     (tenant_id,),
                 ).fetchone()["count"],
                 "workflow_messages": db.execute(
                     "SELECT COUNT(*) AS count FROM workflow_messages m JOIN workflows w ON w.workflow_id=m.workflow_id WHERE w.tenant_id=?",
                     (tenant_id,),
                 ).fetchone()["count"],
                 "workflow_approvals": db.execute(
                     "SELECT COUNT(*) AS count FROM workflow_approvals a JOIN workflows w ON w.workflow_id=a.workflow_id WHERE w.tenant_id=?",
                     (tenant_id,),
                 ).fetchone()["count"],
                 "agent_memory": db.execute(
                     "SELECT COUNT(*) AS count FROM agent_memory WHERE agent_id IN (SELECT agent_id FROM workflow_tasks WHERE workflow_id IN (SELECT workflow_id FROM workflows WHERE tenant_id=?))",
                     (tenant_id,),
                 ).fetchone()["count"],
             }
        return counts

    # ---- Workflow Task Claiming & Recovery ------------------------------------

    def claim_task(
        self,
        tenant_id: str,
        task_id: str,
        worker_id: str,
        execution_id: str | None = None,
    ) -> bool:
        """Atomically claim a READY task for a worker. Returns True if claimed.

        Single UPDATE gated on status='READY': exactly one competing worker
        can win (rowcount 1); all others get False. Each winning claim bumps
        claim_count and records its execution_id for attempt-level tracing.
        """
        now = utc_now()
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_tasks SET status='RUNNING', worker_id=?, claimed_at=?, updated_at=?, "
                "claim_count=COALESCE(claim_count,0)+1, last_execution_id=? "
                "WHERE task_id=? AND tenant_id=? AND status='READY'",
                (worker_id, now, now, execution_id, task_id, tenant_id),
            )
            return cur.rowcount > 0

    def set_task_execution(self, tenant_id: str, task_id: str, execution_id: str) -> bool:
        """Link an engine-driven execution attempt to a task (no claim path)."""
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_tasks SET last_execution_id=?, updated_at=? WHERE task_id=? AND tenant_id=?",
                (execution_id, utc_now(), task_id, tenant_id),
            )
            return cur.rowcount > 0

    def release_task(self, tenant_id: str, task_id: str, worker_id: str) -> bool:
        """Release a task claim. Terminal states are never rewound: COMPLETED,
        FAILED and CANCELLED rows keep their status and keep worker_id as the
        last-executor attribution (only the lease timestamp is cleared)."""
        now = utc_now()
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_tasks SET status=CASE "
                "WHEN status IN ('COMPLETED','FAILED','CANCELLED') THEN status ELSE 'READY' END, "
                "worker_id=CASE "
                "WHEN status IN ('COMPLETED','FAILED','CANCELLED') THEN worker_id ELSE NULL END, "
                "claimed_at=NULL, updated_at=? "
                "WHERE task_id=? AND tenant_id=? AND worker_id=?",
                (now, task_id, tenant_id, worker_id),
            )
            return cur.rowcount > 0

    def get_workflow_by_id(self, workflow_id: str) -> dict[str, Any] | None:
        """Tenant-agnostic workflow lookup for operator tooling (CLI status/trace)."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone()
        return dict(row) if row else None

    def list_stuck_tasks(self, tenant_id: str, stale_seconds: int = 30) -> list[dict[str, Any]]:
        """Find RUNNING tasks whose lease expired (stale claim or stale update).

        COALESCE covers engine-driven RUNNING rows that never carried a claim:
        a task nobody touched for stale_seconds is stuck regardless of origin.
        Recovery resets to READY — worker disappearance is never recorded as
        a task failure.
        """
        threshold = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)).isoformat()
        with self.connect() as db:
            rows = db.execute(
                """SELECT t.*, w.project_id FROM workflow_tasks t
                   JOIN workflows w ON w.workflow_id=t.workflow_id
                   WHERE t.tenant_id=? AND t.status='RUNNING'
                     AND (t.worker_id IS NOT NULL OR t.updated_at < ?)
                     AND COALESCE(t.claimed_at, t.updated_at) < ?
                   ORDER BY t.updated_at ASC""",
                (tenant_id, threshold, threshold),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            for fk in ("required_capabilities_json", "depends_on_json", "input_artifacts_json", "output_artifacts_json"):
                if fk in result:
                    result[fk.replace("_json", "")] = json.loads(result.pop(fk) or "[]")
            results.append(result)
        return results

    def reset_stuck_task(self, tenant_id: str, task_id: str) -> bool:
        """Reset a stuck task back to READY for re-claiming."""
        now = utc_now()
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_tasks SET status='READY', worker_id=NULL, claimed_at=NULL, "
                "updated_at=? WHERE task_id=? AND tenant_id=? AND status='RUNNING'",
                (now, task_id, tenant_id),
            )
            rowcount = cur.rowcount
            if rowcount:
                wf_row = db.execute("SELECT workflow_id, project_id FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
                wf_id = dict(wf_row).get("workflow_id", "unknown") if wf_row else "unknown"
                proj_id = dict(wf_row).get("project_id", "") if wf_row else ""
                db.execute(
                    """INSERT INTO workflow_events(
                        event_id, workflow_id, tenant_id, project_id, task_id, agent_id,
                        event_type, detail_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (f"evt-{uuid.uuid4()}", wf_id, tenant_id, proj_id, task_id, None, "task_recovered", json.dumps({"reason": "stuck_worker_recovery"}), utc_now()),
                )
            return rowcount > 0

    # ---- Workflow Messages -----------------------------------------------------

    def create_workflow_message(
        self,
        *,
        message_id: str,
        workflow_id: str,
        tenant_id: str,
        message_type: str,
        content: dict[str, Any],
        from_agent_id: str | None = None,
        to_agent_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as db:
            wf = db.execute("SELECT project_id FROM workflows WHERE workflow_id=? AND tenant_id=?", (workflow_id, tenant_id)).fetchone()
            real_project_id = project_id or (wf["project_id"] if wf else "")
            db.execute(
                """INSERT INTO workflow_messages(
                    message_id, workflow_id, tenant_id, project_id, task_id, from_agent_id,
                    to_agent_id, message_type, content_json, correlation_id, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, workflow_id, tenant_id, real_project_id, task_id, from_agent_id,
                 to_agent_id, message_type, json.dumps(content), correlation_id, now),
            )
            row = db.execute("SELECT * FROM workflow_messages WHERE message_id=?", (message_id,)).fetchone()
        result = dict(row)
        result["content"] = json.loads(result.pop("content_json"))
        return result

    def list_workflow_messages(
        self,
        tenant_id: str,
        workflow_id: str,
        message_type: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            if message_type and task_id:
                rows = db.execute(
                    "SELECT * FROM workflow_messages WHERE workflow_id=? AND tenant_id=? AND message_type=? AND task_id=? ORDER BY created_at DESC LIMIT ?",
                    (workflow_id, tenant_id, message_type, task_id, limit),
                ).fetchall()
            elif message_type:
                rows = db.execute(
                    "SELECT * FROM workflow_messages WHERE workflow_id=? AND tenant_id=? AND message_type=? ORDER BY created_at DESC LIMIT ?",
                    (workflow_id, tenant_id, message_type, limit),
                ).fetchall()
            elif task_id:
                rows = db.execute(
                    "SELECT * FROM workflow_messages WHERE workflow_id=? AND tenant_id=? AND task_id=? ORDER BY created_at DESC LIMIT ?",
                    (workflow_id, tenant_id, task_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflow_messages WHERE workflow_id=? AND tenant_id=? ORDER BY created_at DESC LIMIT ?",
                    (workflow_id, tenant_id, limit),
                ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["content"] = json.loads(result.pop("content_json"))
            results.append(result)
        return list(reversed(results))

    def mark_message_processed(self, tenant_id: str, message_id: str) -> bool:
        now = utc_now()
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_messages SET processed_at=? WHERE message_id=? AND tenant_id=?",
                (now, message_id, tenant_id),
            )
            return cur.rowcount > 0

    def list_unprocessed_messages(
        self,
        tenant_id: str,
        workflow_id: str,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            if task_id:
                rows = db.execute(
                    "SELECT * FROM workflow_messages WHERE workflow_id=? AND tenant_id=? AND task_id=? AND processed_at IS NULL ORDER BY created_at ASC LIMIT ?",
                    (workflow_id, tenant_id, task_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflow_messages WHERE workflow_id=? AND tenant_id=? AND processed_at IS NULL ORDER BY created_at ASC LIMIT ?",
                    (workflow_id, tenant_id, limit),
                ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["content"] = json.loads(result.pop("content_json"))
            results.append(result)
        return results

    # ---- Workflow Approvals (Phase 5) -----------------------------------------

    def create_approval_request(
        self,
        *,
        approval_id: str,
        workflow_id: str,
        tenant_id: str,
        project_id: str,
        task_id: str | None,
        requested_by: str,
        operation: str,
        reason: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO workflow_approvals(
                    approval_id, workflow_id, tenant_id, project_id, task_id,
                    requested_by, operation, reason, payload_json, status,
                    decided_by, decided_at, decision_note, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (approval_id, workflow_id, tenant_id, project_id, task_id,
                  requested_by, operation, reason, json.dumps(payload), "PENDING",
                  None, None, None, now, now)
            )
            row = db.execute("SELECT * FROM workflow_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        return dict(row)

    def get_approval(self, tenant_id: str, approval_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_approvals WHERE approval_id=? AND tenant_id=?",
                (approval_id, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def list_pending_approvals(self, tenant_id: str, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            if project_id:
                rows = db.execute(
                    "SELECT * FROM workflow_approvals WHERE tenant_id=? AND project_id=? AND status='PENDING' ORDER BY created_at ASC LIMIT ?",
                    (tenant_id, project_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM workflow_approvals WHERE tenant_id=? AND status='PENDING' ORDER BY created_at ASC LIMIT ?",
                    (tenant_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def decide_approval(self, tenant_id: str, approval_id: str, decision: str, decided_by: str, note: str | None = None) -> bool:
        now = utc_now()
        with self.connect() as db:
            cur = db.execute(
                "UPDATE workflow_approvals SET status=?, decided_by=?, decided_at=?, decision_note=?, updated_at=? WHERE approval_id=? AND tenant_id=?",
                (decision, decided_by, now, note, now, approval_id, tenant_id),
            )
            return cur.rowcount > 0

    # ---- Agent Memory (Phase 5) -----------------------------------------------

    def create_agent_memory(
        self,
        *,
        memory_id: str,
        agent_id: str,
        scope: str,
        source: str,
        content: dict[str, Any],
        workflow_id: str | None = None,
        task_id: str | None = None,
        truth: str = "INFERRED",
        confidence: str = "UNVERIFIED",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        import hashlib as _hash
        now = utc_now()
        content_json = json.dumps(content, sort_keys=True, default=str)
        content_hash = _hash.sha256(content_json.encode()).hexdigest()
        with self.connect() as db:
            db.execute(
                """INSERT INTO agent_memory(
                    memory_id, agent_id, workflow_id, scope, source,
                    content_json, content_hash, truth, confidence, created_at, expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (memory_id, agent_id, workflow_id, scope, source, content_json, content_hash, truth, confidence, now, expires_at),
            )
            row = db.execute("SELECT * FROM agent_memory WHERE memory_id=?", (memory_id,)).fetchone()
        result = dict(row)
        result["content"] = json.loads(result.pop("content_json"))
        return result

    def list_agent_memory(
        self,
        agent_id: str,
        scope: str | None = None,
        workflow_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self.connect() as db:
            conditions = ["agent_id=?"]
            params: list[Any] = [agent_id]
            if scope:
                conditions.append("scope=?")
                params.append(scope)
            if workflow_id:
                conditions.append("workflow_id=?")
                params.append(workflow_id)
            query = f"SELECT * FROM agent_memory WHERE {' AND '.join(conditions)} AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at DESC LIMIT ?"
            params.append(utc_now())
            params.append(limit)
            rows = db.execute(query, params).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["content"] = json.loads(result.pop("content_json"))
            results.append(result)
        return results

    def get_agent_memory(self, tenant_id: str, memory_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT m.* FROM agent_memory m WHERE m.memory_id=?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["content"] = json.loads(result.pop("content_json"))
        return result

    # ---- Dynamic Task Creation (Phase 5) --------------------------------------

    def create_dynamic_task(
        self,
        *,
        task_id: str,
        workflow_id: str,
        tenant_id: str,
        project_id: str,
        task_type: str,
        name: str,
        agent_id: str | None,
        required_capabilities: list[str],
        depends_on: list[str],
        input_artifacts: list[str],
        max_retries: int = 3,
        parent_task_id: str | None = None,
        generated_reason: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO workflow_tasks(
                    task_id, workflow_id, tenant_id, project_id, task_type, name, agent_id,
                    status, required_capabilities_json, depends_on_json, input_artifacts_json,
                    output_artifacts_json, max_retries, parent_task_id, dynamic, generated_reason,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id, workflow_id, tenant_id, project_id, task_type, name, agent_id,
                    "PENDING", json.dumps(required_capabilities), json.dumps(depends_on),
                    json.dumps(input_artifacts), json.dumps([]), max_retries,
                    parent_task_id, 1, generated_reason, now, now,
                ),
            )
            row = db.execute("SELECT * FROM workflow_tasks WHERE task_id=?", (task_id,)).fetchone()
        result = dict(row)
        result["required_capabilities"] = json.loads(result.pop("required_capabilities_json"))
        result["depends_on"] = json.loads(result.pop("depends_on_json"))
        result["input_artifacts"] = json.loads(result.pop("input_artifacts_json"))
        result["output_artifacts"] = json.loads(result.pop("output_artifacts_json"))
        return result

    def get_dynamic_tasks(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        """Get tasks created dynamically by the autonomous runtime."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM workflow_tasks WHERE workflow_id=? AND tenant_id=? AND dynamic=1 ORDER BY created_at ASC",
                (workflow_id, tenant_id),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["required_capabilities"] = json.loads(result.pop("required_capabilities_json"))
            result["depends_on"] = json.loads(result.pop("depends_on_json"))
            result["input_artifacts"] = json.loads(result.pop("input_artifacts_json"))
            result["output_artifacts"] = json.loads(result.pop("output_artifacts_json"))
            results.append(result)
        return results
