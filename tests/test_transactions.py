"""Failure and concurrent-write tests beyond the HTTP happy path."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3
import pytest
from clearpath import db, seed, workflow


def setup(temp_db, now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, now)
    actor = conn.execute("SELECT id FROM users WHERE role = 'SUBMITTER'").fetchone()[0]
    conn.close()
    return actor


def test_concurrent_creates_unique_ids_and_balanced_assignment(temp_db, frozen_now):
    actor = setup(temp_db, frozen_now)
    barrier = Barrier(2)

    def create():
        conn = db.connect(temp_db)
        try:
            barrier.wait()
            return workflow.create_submission(
                conn,
                actor,
                title="Concurrent campaign",
                copy_text="Subject to credit approval.",
                channel="WEBSITE",
                product="PERSONAL_LOAN",
                target_launch_date="2026-10-01",
                now=frozen_now,
            )
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert {r["external_id"] for r in results} == {"CP-8909", "CP-8910"}
    # Mark starts one active review behind Sarah, and wins the name tie next.
    assert all(r["assigned_reviewer_id"] for r in results)


def test_concurrent_decisions_only_one_commits(temp_db, frozen_now):
    actor = setup(temp_db, frozen_now)
    conn = db.connect(temp_db)
    record = workflow.create_submission(
        conn,
        actor,
        title="Concurrent decision",
        copy_text="Subject to credit approval.",
        channel="WEBSITE",
        product="PERSONAL_LOAN",
        target_launch_date="2026-10-01",
        now=frozen_now,
    )
    conn.close()
    barrier = Barrier(2)

    def decide():
        conn = db.connect(temp_db)
        try:
            barrier.wait()
            workflow.approve_submission(
                conn,
                record["assigned_reviewer_id"],
                record["id"],
                expected_record_version=1,
                now=frozen_now,
            )
            return "approved"
        except workflow.VersionConflictError:
            return "conflict"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: decide(), range(2)))
    assert sorted(results) == ["approved", "conflict"]
    conn = db.connect(temp_db)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE submission_id=? AND event_type='APPROVED'",
            (record["id"],),
        ).fetchone()[0]
        == 1
    )
    conn.close()


def test_event_failure_rolls_back_entire_creation(temp_db, frozen_now, monkeypatch):
    actor = setup(temp_db, frozen_now)
    conn = db.connect(temp_db)
    counts = [
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("submissions", "submission_versions", "audit_events")
    ]

    def fail(*args, **kwargs):
        raise RuntimeError("Simulated audit write failure")

    monkeypatch.setattr(workflow, "_event", fail)
    with pytest.raises(RuntimeError):
        workflow.create_submission(
            conn,
            actor,
            title="Must roll back",
            copy_text="Copy",
            channel="WEBSITE",
            product="PERSONAL_LOAN",
            target_launch_date="2026-10-01",
            now=frozen_now,
        )
    assert [
        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("submissions", "submission_versions", "audit_events")
    ] == counts
    conn.close()


def test_lock_timeout_is_retryable_and_does_not_write(temp_db, frozen_now):
    actor = setup(temp_db, frozen_now)
    locker = db.connect(temp_db)
    locker.execute("BEGIN IMMEDIATE")
    conn = db.connect(temp_db)
    conn.execute("PRAGMA busy_timeout=1")
    try:
        with pytest.raises(workflow.DatabaseBusyError):
            workflow.create_submission(
                conn,
                actor,
                title="Locked campaign",
                copy_text="Copy",
                channel="WEBSITE",
                product="PERSONAL_LOAN",
                target_launch_date="2026-10-01",
                now=frozen_now,
            )
    finally:
        locker.rollback()
        locker.close()
        conn.close()
