"""Database connections, versioned schema, and transactions.

K1: domain tables (users, submissions, submission_versions, audit_events,
notifications), per-connection pragmas, WAL, user_version, and a
corrupt/unsupported-DB guard that never silently deletes or reseeds. One
connection per operation, closed reliably; no shared global connection.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_USER_VERSION = 3

_NOTIFICATIONS_DDL = """
CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT PRIMARY KEY,
    recipient_id    TEXT NOT NULL REFERENCES users(id),
    submission_id   TEXT NOT NULL REFERENCES submissions(id),
    audit_event_id  INTEGER NOT NULL REFERENCES audit_events(id),
    event_type      TEXT NOT NULL,
    headline        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    read_at         TEXT,
    UNIQUE (recipient_id, audit_event_id)
);
CREATE INDEX IF NOT EXISTS idx_notifications_inbox
    ON notifications(recipient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_unread
    ON notifications(recipient_id, read_at);
"""

_POLICY_DDL = """
CREATE TABLE IF NOT EXISTS permissions (
    user_id    TEXT NOT NULL REFERENCES users(id),
    capability TEXT NOT NULL CHECK (capability IN ('manage_policies')),
    granted_at TEXT NOT NULL,
    PRIMARY KEY (user_id, capability)
);

CREATE TABLE IF NOT EXISTS policy_snapshots (
    id            TEXT PRIMARY KEY,
    version       INTEGER NOT NULL UNIQUE CHECK (version >= 1),
    label         TEXT,
    policy_hash   TEXT NOT NULL,
    rules_json    TEXT NOT NULL,
    published_by  TEXT NOT NULL REFERENCES users(id),
    published_at  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_state (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    active_snapshot_id    TEXT REFERENCES policy_snapshots(id),
    current_draft_version INTEGER NOT NULL CHECK (current_draft_version >= 0),
    current_draft_hash    TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_rules (
    id               TEXT PRIMARY KEY,
    draft_version    INTEGER NOT NULL,
    rule_key         TEXT NOT NULL,
    title            TEXT NOT NULL,
    instructions     TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('semantic', 'required_disclosure')),
    required_literal TEXT,
    product          TEXT,
    channel          TEXT,
    enabled          INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id         TEXT NOT NULL REFERENCES users(id),
    event_type       TEXT NOT NULL CHECK (event_type IN
        ('DRAFT_SAVED', 'PUBLISHED', 'ENABLED', 'DISABLED')),
    draft_version    INTEGER,
    snapshot_version INTEGER,
    policy_hash      TEXT,
    metadata_json    TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_audit_created ON policy_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_policy_rules_version ON policy_rules(draft_version);
"""

_SCHEMA = """
CREATE TABLE users (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('SUBMITTER', 'REVIEWER')),
    display_title TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE submissions (
    id                  TEXT PRIMARY KEY,
    external_id         TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL,
    partner             TEXT,
    channel             TEXT NOT NULL CHECK (channel IN
        ('AFFILIATE', 'EMAIL', 'SOCIAL', 'PAID_SEARCH', 'WEBSITE')),
    product             TEXT NOT NULL CHECK (product IN
        ('PERSONAL_LOAN', 'CREDIT_CARD', 'MORTGAGE_PREQUALIFICATION')),
    status              TEXT NOT NULL CHECK (status IN
        ('PENDING_ASSIGNMENT', 'UNDER_REVIEW', 'CHANGES_REQUESTED',
         'APPROVED', 'REJECTED')),
    assigned_reviewer_id TEXT REFERENCES users(id),
    submitter_id         TEXT NOT NULL REFERENCES users(id),
    target_launch_date  TEXT NOT NULL,
    submitted_at        TEXT NOT NULL,
    sla_breach_at       TEXT NOT NULL,
    decided_at          TEXT,
    current_version     INTEGER NOT NULL CHECK (current_version >= 1),
    record_version      INTEGER NOT NULL CHECK (record_version >= 1),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE submission_versions (
    id             TEXT PRIMARY KEY,
    submission_id  TEXT NOT NULL REFERENCES submissions(id),
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    asset_url      TEXT,
    copy_text      TEXT NOT NULL,
    created_by     TEXT NOT NULL REFERENCES users(id),
    created_at     TEXT NOT NULL,
    UNIQUE (submission_id, version_number)
);

CREATE TABLE audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL REFERENCES submissions(id),
    actor_id      TEXT NOT NULL REFERENCES users(id),
    event_type    TEXT NOT NULL CHECK (event_type IN
        ('SUBMITTED', 'AUTO_ASSIGNED', 'ASSIGNED', 'REASSIGNED',
         'CHANGES_REQUESTED', 'RESUBMITTED', 'APPROVED', 'REJECTED')),
    from_status   TEXT,
    to_status     TEXT,
    version_number INTEGER,
    comment       TEXT,
    metadata_json TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE notifications (
    id              TEXT PRIMARY KEY,
    recipient_id    TEXT NOT NULL REFERENCES users(id),
    submission_id   TEXT NOT NULL REFERENCES submissions(id),
    audit_event_id  INTEGER NOT NULL REFERENCES audit_events(id),
    event_type      TEXT NOT NULL,
    headline        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    read_at         TEXT,
    UNIQUE (recipient_id, audit_event_id)
);

CREATE INDEX idx_submissions_status   ON submissions(status);
CREATE INDEX idx_submissions_reviewer ON submissions(assigned_reviewer_id);
CREATE INDEX idx_submissions_sla      ON submissions(sla_breach_at);
CREATE INDEX idx_submissions_submitted ON submissions(submitted_at);
CREATE INDEX idx_submissions_decided  ON submissions(decided_at);
CREATE INDEX idx_versions_submission  ON submission_versions(submission_id);
CREATE INDEX idx_audit_submission     ON audit_events(submission_id);
CREATE INDEX idx_audit_created        ON audit_events(created_at);
CREATE INDEX idx_notifications_inbox  ON notifications(recipient_id, created_at);
CREATE INDEX idx_notifications_unread ON notifications(recipient_id, read_at);
""" + _POLICY_DDL

_EXPECTED_TABLES = {
    "users",
    "submissions",
    "submission_versions",
    "audit_events",
    "notifications",
    "permissions",
    "policy_snapshots",
    "policy_state",
    "policy_rules",
    "policy_audit",
}

_V1_TABLES = {
    "users",
    "submissions",
    "submission_versions",
    "audit_events",
}


def get_database_path() -> Path:
    raw = os.environ.get("DATABASE_PATH", "./data/clearpath.db")
    return Path(raw).expanduser().resolve()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a short-lived connection with required pragmas. Caller must close."""
    path = db_path if db_path is not None else get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def enable_wal(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")


def get_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [str(r["name"]) for r in rows]


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add durable notifications and backfill from existing audit events."""
    from clearpath.notifications import backfill

    conn.executescript(_NOTIFICATIONS_DDL)
    backfill(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
    conn.commit()


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add policy/permission/audit tables without touching existing data."""
    conn.executescript(_POLICY_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
    conn.commit()


def initialize_schema(conn: sqlite3.Connection) -> bool:
    """Initialize a fresh DB with the versioned schema, or validate/migrate.

    Returns True when the schema was created from an empty DB, False when an
    existing supported DB was left untouched or migrated. Never deletes or
    reseeds a nonempty/unsupported/corrupt database — raises instead.
    """
    version = get_user_version(conn)
    tables = _table_names(conn)

    if version == 0 and not tables:
        enable_wal(conn)
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
        conn.commit()
        return True

    if version == 1:
        missing = _V1_TABLES - set(tables)
        if missing:
            raise RuntimeError(
                f"Database is missing expected tables: {sorted(missing)}. "
                "Do not delete the DB; restore a compatible file or use a fresh path."
            )
        _migrate_v1_to_v2(conn)
        _migrate_v2_to_v3(conn)
        return False

    if version == 2:
        # v2 databases carry the base domain tables; only the policy tables
        # are missing. Add them without deleting or reseeding existing data.
        _migrate_v2_to_v3(conn)
        return False

    if version != SCHEMA_USER_VERSION:
        raise RuntimeError(
            f"Unsupported database schema version {version}; "
            f"expected {SCHEMA_USER_VERSION}. Do not delete the DB; "
            "restore a compatible file or use a fresh path."
        )

    missing = _EXPECTED_TABLES - set(tables)
    if missing:
        raise RuntimeError(
            f"Database is missing expected tables: {sorted(missing)}. "
            "Do not delete the DB; restore a compatible file or use a fresh path."
        )

    return False


def ping(db_path: Path | None = None) -> bool:
    """Return True if the database can be opened and queried."""
    conn = connect(db_path)
    try:
        conn.execute("SELECT 1").fetchone()
        return True
    finally:
        conn.close()
