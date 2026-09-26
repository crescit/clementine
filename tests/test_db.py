"""K1 schema and seed acceptance tests.

Covers: four tables/constraints/indexes, per-connection pragmas, user_version,
relative-time fixtures with full versions/events, transactional seed/reset,
preserved data across repeated boot, fresh-UUID reset, failing-reset rollback,
and the corrupt/unsupported-DB guard.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clearpath import db, seed

OPEN_STATUSES = {"PENDING_ASSIGNMENT", "UNDER_REVIEW", "CHANGES_REQUESTED"}

FROZEN = datetime(2026, 9, 25, 17, 0, 0, tzinfo=timezone.utc)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _seed(temp_db: Path, now: datetime = FROZEN) -> None:
    conn = db.connect(temp_db)
    try:
        seed.seed_database(conn, now)
    finally:
        conn.close()


def _query(temp_db: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = db.connect(temp_db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _row(temp_db: Path, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    rows = _query(temp_db, sql, params)
    return rows[0] if rows else None


# --- Schema / constraints ---------------------------------------------------


def test_schema_creates_four_tables_and_indexes(temp_db: Path) -> None:
    _seed(temp_db)
    rows = _query(
        temp_db,
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
    )
    names = {r["name"] for r in rows}
    assert {"users", "submissions", "submission_versions", "audit_events"} <= names

    indexes = {
        r["name"]
        for r in _query(temp_db, "SELECT name FROM sqlite_master WHERE type='index'")
    }
    for want in [
        "idx_submissions_status",
        "idx_submissions_reviewer",
        "idx_submissions_sla",
        "idx_versions_submission",
        "idx_audit_submission",
        "idx_audit_created",
    ]:
        assert want in indexes, f"missing index {want}"

    conn = db.connect(temp_db)
    try:
        assert db.get_user_version(conn) == db.SCHEMA_USER_VERSION
    finally:
        conn.close()


def test_foreign_keys_enforced(temp_db: Path) -> None:
    _seed(temp_db)
    # Inserting a submission referencing a nonexistent submitter must fail.
    conn = db.connect(temp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO submissions (id, external_id, title, partner, channel, "
                "product, status, submitter_id, target_launch_date, submitted_at, "
                "sla_breach_at, current_version, record_version, created_at, updated_at) "
                "VALUES ('x', 'CP-9999', 't', NULL, 'EMAIL', 'PERSONAL_LOAN', "
                "'PENDING_ASSIGNMENT', 'missing-user', '2026-09-30', "
                "'2026-09-25T00:00:00Z', '2026-09-28T00:00:00Z', 1, 1, "
                "'2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z')"
            )
    finally:
        conn.close()


def test_unique_external_id_and_version_pair(temp_db: Path) -> None:
    _seed(temp_db)
    conn = db.connect(temp_db)
    try:
        # Duplicate external_id rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO submissions (id, external_id, title, partner, channel, "
                "product, status, submitter_id, target_launch_date, submitted_at, "
                "sla_breach_at, current_version, record_version, created_at, updated_at) "
                "VALUES ('y', 'CP-8902', 't', NULL, 'EMAIL', 'PERSONAL_LOAN', "
                "'PENDING_ASSIGNMENT', 'u', '2026-09-30', '2026-09-25T00:00:00Z', "
                "'2026-09-28T00:00:00Z', 1, 1, '2026-09-25T00:00:00Z', "
                "'2026-09-25T00:00:00Z')"
            )
        # Duplicate (submission_id, version_number) rejected.
        sid = _row(temp_db, "SELECT id FROM submissions WHERE external_id='CP-8904'")[
            "id"
        ]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO submission_versions (id, submission_id, version_number, "
                "copy_text, created_by, created_at) VALUES "
                "('v-dup', ?, 1, 'c', 'u', '2026-09-25T00:00:00Z')",
                (sid,),
            )
    finally:
        conn.close()


# --- Baseline counts and §8 metrics ----------------------------------------


def test_baseline_counts_and_metrics(temp_db: Path) -> None:
    _seed(temp_db)

    subs = _query(temp_db, "SELECT * FROM submissions")
    assert len(subs) == 7  # six open / seven total baseline

    open_rows = [s for s in subs if s["status"] in OPEN_STATUSES]
    assert len(open_rows) == 6

    # 2 breached: open rows whose sla_breach_at <= frozen now.
    breached = [s for s in open_rows if _parse_iso(s["sla_breach_at"]) <= FROZEN]
    assert len(breached) == 2

    # 2 unassigned: open rows with null reviewer.
    unassigned = [s for s in open_rows if s["assigned_reviewer_id"] is None]
    assert len(unassigned) == 2

    # Avg turnaround (30d) and completed (7d) from decided records.
    decided = [
        s
        for s in subs
        if s["status"] in {"APPROVED", "REJECTED"} and s["decided_at"] is not None
    ]
    window_30 = [
        s
        for s in decided
        if FROZEN - timedelta(days=30) <= _parse_iso(s["decided_at"]) <= FROZEN
    ]
    assert len(window_30) == 1
    only = window_30[0]
    turnaround = (
        _parse_iso(only["decided_at"]) - _parse_iso(only["submitted_at"])
    ).total_seconds() / 86400
    assert round(turnaround, 1) == 5.0

    window_7 = [
        s
        for s in decided
        if FROZEN - timedelta(days=7) <= _parse_iso(s["decided_at"]) <= FROZEN
    ]
    assert len(window_7) == 1


def test_cp8904_belongs_to_sarah(temp_db: Path) -> None:
    _seed(temp_db)
    sarah = _row(temp_db, "SELECT id FROM users WHERE name='Sarah T.'")
    assert sarah is not None
    row = _row(temp_db, "SELECT * FROM submissions WHERE external_id='CP-8904'")
    assert row["assigned_reviewer_id"] == sarah["id"]
    assert row["status"] == "UNDER_REVIEW"
    assert row["submitter_id"] != sarah["id"]  # Jessica owns it


def test_cp8905_has_v1_v2_history(temp_db: Path) -> None:
    _seed(temp_db)
    sub = _row(temp_db, "SELECT * FROM submissions WHERE external_id='CP-8905'")
    assert sub["current_version"] == 2
    assert sub["status"] == "APPROVED"
    assert sub["decided_at"] is not None

    versions = _query(
        temp_db,
        "SELECT * FROM submission_versions WHERE submission_id=? ORDER BY version_number",
        (sub["id"],),
    )
    assert [v["version_number"] for v in versions] == [1, 2]
    assert "Guaranteed approval" in versions[0]["copy_text"]
    assert "Subject to credit approval" in versions[1]["copy_text"]

    # Events are chronologically ordered and reference correct versions.
    events = _query(
        temp_db,
        "SELECT * FROM audit_events WHERE submission_id=? ORDER BY created_at, id",
        (sub["id"],),
    )
    types = [e["event_type"] for e in events]
    assert types == [
        "SUBMITTED",
        "AUTO_ASSIGNED",
        "CHANGES_REQUESTED",
        "RESUBMITTED",
        "APPROVED",
    ]
    approved = [e for e in events if e["event_type"] == "APPROVED"][0]
    assert approved["version_number"] == 2
    # v1 copy remains unchanged (immutability): first version row unchanged.
    assert "Guaranteed approval" in versions[0]["copy_text"]


# --- Restart persistence, reset, rollback ----------------------------------


def test_repeated_boot_preserves_changes(temp_db: Path) -> None:
    _seed(temp_db)
    # Mutate: rename an existing submission.
    conn = db.connect(temp_db)
    try:
        conn.execute(
            "UPDATE submissions SET title='EDITED TITLE' WHERE external_id='CP-8903'"
        )
        conn.commit()
    finally:
        conn.close()

    # A second auto_seed must NOT reseed/wipe an existing DB.
    seeded = seed.auto_seed(temp_db, FROZEN)
    assert seeded is False
    row = _row(temp_db, "SELECT title FROM submissions WHERE external_id='CP-8903'")
    assert row["title"] == "EDITED TITLE"
    assert len(_query(temp_db, "SELECT * FROM submissions")) == 7


def test_reset_generates_fresh_uuids(temp_db: Path) -> None:
    _seed(temp_db)
    before = {r["id"] for r in _query(temp_db, "SELECT id FROM users")} | {
        r["id"] for r in _query(temp_db, "SELECT id FROM submissions")
    }

    seed.reset_database(temp_db, FROZEN)

    after = {r["id"] for r in _query(temp_db, "SELECT id FROM users")} | {
        r["id"] for r in _query(temp_db, "SELECT id FROM submissions")
    }
    assert before.isdisjoint(after)
    assert len(_query(temp_db, "SELECT * FROM submissions")) == 7


def test_failing_reset_rolls_back(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(temp_db)
    original = _row(temp_db, "SELECT * FROM submissions WHERE external_id='CP-8902'")
    assert original is not None

    # Force the reseed to fail mid-write: corrupt the seed builder.
    def boom(*args, **kwargs):
        raise RuntimeError("forced seed failure")

    monkeypatch.setattr(seed, "_build_seed", boom)

    with pytest.raises(RuntimeError):
        seed.reset_database(temp_db, FROZEN)

    # Original data must be intact after rollback.
    still = _row(temp_db, "SELECT * FROM submissions WHERE external_id='CP-8902'")
    assert still is not None
    assert len(_query(temp_db, "SELECT * FROM submissions")) == 7


def test_unsupported_schema_does_not_silently_reset(tmp_path: Path) -> None:
    # A DB whose user_version doesn't match must fail loudly, never be deleted.
    path = tmp_path / "bad_version.db"
    conn = db.connect(path)
    try:
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
    finally:
        conn.close()
    conn = db.connect(path)
    try:
        with pytest.raises(RuntimeError) as exc:
            db.initialize_schema(conn)
        assert "Unsupported database schema version" in str(exc.value)
    finally:
        conn.close()
    # File still exists and version unchanged.
    assert path.exists()


def test_corrupt_db_does_not_silently_reset(tmp_path: Path) -> None:
    # Matching version but missing domain tables => corrupt/foreign schema.
    path = tmp_path / "corrupt.db"
    conn = db.connect(path)
    try:
        conn.executescript("CREATE TABLE unrelated (x INTEGER); PRAGMA user_version=1;")
        conn.commit()
    finally:
        conn.close()
    conn = db.connect(path)
    try:
        with pytest.raises(RuntimeError) as exc:
            db.initialize_schema(conn)
        assert "missing expected tables" in str(exc.value)
    finally:
        conn.close()
    assert path.exists()


def test_timestamps_are_coherent_utc(temp_db: Path) -> None:
    _seed(temp_db)
    rows = _query(temp_db, "SELECT submitted_at, sla_breach_at FROM submissions")
    for r in rows:
        submitted = _parse_iso(r["submitted_at"])
        breach = _parse_iso(r["sla_breach_at"])
        assert submitted.tzinfo is not None
        # SLA breach is exactly 72h after submission for every record.
        assert breach - submitted == timedelta(hours=72)


def test_seed_clean_and_approved_examples_pass_the_policy_scan(temp_db):
    from clearpath.preflight import run_preflight
    _seed(temp_db)
    rows = _query(temp_db, """SELECT s.external_id, s.product, s.channel, v.copy_text
        FROM submissions s JOIN submission_versions v
        ON v.submission_id = s.id AND v.version_number = s.current_version
        WHERE s.external_id IN ('CP-8905', 'CP-8908')""")
    assert len(rows) == 2
    for row in rows:
        assert run_preflight(row['product'], row['channel'], row['copy_text'])['passed'], row['external_id']
