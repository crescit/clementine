"""Regression tests: demo reset after analysis runs, and policy backfill on
migrated (pre-policy) databases."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from clearpath import db, seed


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Path to a DB that does not exist yet, so auto_seed takes the fresh path."""
    return tmp_path / "fresh.db"


def _insert_analysis_run(conn: sqlite3.Connection) -> None:
    user_id = conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    sub_id = conn.execute("SELECT id FROM submissions LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO analysis_runs (id, submission_id, content_version, "
        "content_hash, product, channel, snapshot_id, snapshot_version, "
        "snapshot_hash, status, prompt_version, schema_version, actor_id, "
        "created_at) VALUES ('r1', ?, 1, 'h', 'p', 'c', 's', 1, 'h', "
        "'SUCCESS', 'v', 'v', ?, 'now')",
        (sub_id, user_id),
    )
    conn.execute(
        "INSERT INTO finding_dispositions (run_id, finding_id, disposition, "
        "reason, actor_id, created_at) VALUES ('r1', 'f1', 'ACKNOWLEDGED', "
        "'ok', ?, 'now')",
        (user_id,),
    )
    conn.execute(
        "INSERT INTO manual_exceptions (id, submission_id, content_version, "
        "snapshot_id, run_id, reason, actor_id, created_at) "
        "VALUES ('e1', ?, 1, 's', 'r1', 'why', ?, 'now')",
        (sub_id, user_id),
    )
    conn.commit()


def test_reset_succeeds_after_analysis_runs(temp_db: Path, frozen_now: datetime):
    seed.auto_seed(temp_db, frozen_now)
    conn = db.connect(temp_db)
    try:
        _insert_analysis_run(conn)
    finally:
        conn.close()

    seed.reset_database(temp_db, frozen_now)

    conn = db.connect(temp_db)
    try:
        for table in ("analysis_runs", "finding_dispositions", "manual_exceptions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM policy_state").fetchone()[0] == 1
    finally:
        conn.close()


def test_auto_seed_backfills_policy_baseline_on_migrated_db(
    temp_db: Path, frozen_now: datetime
):
    seed.auto_seed(temp_db, frozen_now)
    conn = db.connect(temp_db)
    try:
        before = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        # Simulate a pre-policy database: data present, policy tables empty.
        for table in ("policy_audit", "policy_rules", "policy_state",
                      "policy_snapshots", "permissions"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    assert seed.auto_seed(temp_db, frozen_now) is False

    conn = db.connect(temp_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == before
        assert conn.execute("SELECT COUNT(*) FROM policy_state").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM policy_rules").fetchone()[0] > 0
        from clearpath import policies
        assert policies.get_state(conn)["active_snapshot_id"] is not None
    finally:
        conn.close()


def test_auto_seed_backfill_is_idempotent(temp_db: Path, frozen_now: datetime):
    seed.auto_seed(temp_db, frozen_now)
    seed.auto_seed(temp_db, frozen_now)
    conn = db.connect(temp_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM policy_snapshots").fetchone()[0] == 1
    finally:
        conn.close()
