"""Database connections, schema initialization, and transactions.

Full schema (users, submissions, versions, audit_events) lands in K1.
K0 provides connection helpers and a versioned schema init hook.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_USER_VERSION = 1

# Minimal bootstrap schema — extended in K1 with domain tables.
_BOOTSTRAP_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;
"""


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


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Initialize or validate schema. Never silently delete/reseed a nonempty DB."""
    version = get_user_version(conn)
    # Empty DB: sqlite reports user_version 0 and no application tables yet.
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()

    if version == 0 and not tables:
        enable_wal(conn)
        conn.executescript(_BOOTSTRAP_SCHEMA)
        conn.commit()
        return

    if version != SCHEMA_USER_VERSION:
        raise RuntimeError(
            f"Unsupported database schema version {version}; "
            f"expected {SCHEMA_USER_VERSION}. "
            "Do not delete the DB; restore a compatible file or use a fresh path."
        )


def ping(db_path: Path | None = None) -> bool:
    """Return True if the database can be opened and queried."""
    conn = connect(db_path)
    try:
        conn.execute("SELECT 1").fetchone()
        return True
    finally:
        conn.close()
