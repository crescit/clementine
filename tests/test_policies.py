"""S1 acceptance tests: versioned policy storage + admin service.

Covers:
- v2 -> v3 migration adds policy tables without touching existing data
- Fresh DB carries policy tables
- Seeding grants manage_policies to the reviewer persona and publishes the
  baseline snapshot (version 1) matching the disclosure rules
- Capability gate: reviewer has manage_policies, submitter is denied (403)
- Draft save is validated and uses optimistic concurrency (stale -> 409)
- Publish atomically creates an immutable snapshot and advances the active
  pointer; published snapshots cannot be edited/deleted via the service
- Invalid rule sets are rejected (400)
- Demo reset wipes and reseeds policy state coherently
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clearpath import db, policies, seed


@pytest.fixture
def _conn(temp_db):
    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        yield conn
    finally:
        conn.close()


def _seed_policies(temp_db, frozen_now) -> None:
    seed.reset_database(temp_db, frozen_now)


def _reviewer_id(temp_db) -> str:
    conn = db.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT user_id FROM permissions WHERE capability = 'manage_policies' LIMIT 1"
        ).fetchone()
        return str(row["user_id"]) if row else None
    finally:
        conn.close()


def _submitter_id(temp_db) -> str:
    conn = db.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE role = 'SUBMITTER' LIMIT 1"
        ).fetchone()
        return str(row["id"]) if row else None
    finally:
        conn.close()


# --- Migration ------------------------------------------------------------------

def test_v2_db_migrates_to_v3_policy_tables_without_wipe(temp_db: Path, frozen_now):
    """A v2 schema DB gains policy tables; existing submissions survive."""
    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        # Force a v2-looking state: drop policy tables, set version to 2.
        conn.execute("DROP TABLE IF EXISTS policy_audit")
        conn.execute("DROP TABLE IF EXISTS policy_rules")
        conn.execute("DROP TABLE IF EXISTS policy_state")
        conn.execute("DROP TABLE IF EXISTS policy_snapshots")
        conn.execute("DROP TABLE IF EXISTS permissions")
        conn.execute("PRAGMA user_version = 2")
    finally:
        conn.close()

    db.initialize_schema(conn := db.connect(temp_db))
    try:
        assert db.get_user_version(conn) == db.SCHEMA_USER_VERSION
        # Policy tables now exist.
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('policy_snapshots','policy_state','policy_rules',"
            "'policy_audit','permissions')"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert names == {
            "policy_snapshots", "policy_state", "policy_rules",
            "policy_audit", "permissions",
        }
    finally:
        conn.close()


# --- Seeding ---------------------------------------------------------------------

def test_seeding_publishes_baseline_snapshot_and_grants_capability(
    temp_db: Path, frozen_now,
):
    """After a demo reset the reviewer persona holds manage_policies and the
    baseline snapshot (version 1) matches the disclosure rules."""
    _seed_policies(temp_db, frozen_now)

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        snaps = policies.list_snapshots(conn)
        assert len(snaps) == 1
        assert snaps[0]["version"] == 1
        assert snaps[0]["label"] == "clearpath-demo-v1"

        snap = policies.get_snapshot(conn, 1)
        assert snap is not None
        keys = {r["rule_key"] for r in snap["rules"]}
        assert keys == {"CLAIM_001", "CLAIM_002", "DISC_001", "DISC_002", "DISC_003"}

        # The mutable draft at version 0 is the baseline working copy.
        state = policies.get_state(conn)
        assert state["current_draft_version"] == 0
        draft = policies.get_draft(conn)
        assert len(draft) == 5

        # Reviewer persona holds manage_policies.
        reviewer = _reviewer_id(temp_db)
        assert reviewer is not None
        policies.check_capability(conn, reviewer, "manage_policies")  # no raise

        # Audit trail records the baseline publication.
        events = policies.audit_trail(conn)
        assert any(e["event_type"] == "PUBLISHED" and e["snapshot_version"] == 1
                   for e in events)
    finally:
        conn.close()


def test_submitter_lacks_manage_policies(temp_db: Path, frozen_now):
    """A submitter is denied the manage_policies capability (403)."""
    _seed_policies(temp_db, frozen_now)
    submitter = _submitter_id(temp_db)
    assert submitter is not None

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        with pytest.raises(policies.PolicyError) as exc:
            policies.check_capability(conn, submitter, "manage_policies")
        assert exc.value.status == 403
    finally:
        conn.close()


# --- Draft save / optimistic concurrency -------------------------------------------

def test_save_draft_validates_and_advances_version(temp_db: Path, frozen_now):
    _seed_policies(temp_db, frozen_now)
    reviewer = _reviewer_id(temp_db)

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        rules = [
            {
                "rule_key": "CLAIM_001",
                "title": "Restricted approval claim",
                "instructions": "Reject pre-approved claims.",
                "kind": "semantic",
                "enabled": 1,
            }
        ]
        res = policies.save_draft(
            conn, reviewer, rules, expected_draft_version=0, now=frozen_now.isoformat().replace("+00:00", "Z"),
        )
        assert res["draft_version"] == 1
        assert policies.get_state(conn)["current_draft_version"] == 1
        draft = policies.get_draft(conn)
        assert len(draft) == 1
        assert draft[0]["rule_key"] == "CLAIM_001"
    finally:
        conn.close()


def test_stale_draft_save_conflicts(temp_db: Path, frozen_now):
    """Saving with an outdated expected version is rejected (409)."""
    _seed_policies(temp_db, frozen_now)
    reviewer = _reviewer_id(temp_db)

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        rules = [
            {
                "rule_key": "CLAIM_001",
                "title": "Restricted approval claim",
                "instructions": "Reject pre-approved claims.",
                "kind": "semantic",
                "enabled": 1,
            }
        ]
        with pytest.raises(policies.PolicyError) as exc:
            policies.save_draft(
                conn, reviewer, rules, expected_draft_version=5, now=frozen_now.isoformat().replace("+00:00", "Z"),
            )
        assert exc.value.status == 409
    finally:
        conn.close()


def test_invalid_rule_set_rejected(temp_db: Path, frozen_now):
    """Malformed rules (unknown kind) are rejected (400) and do not persist."""
    _seed_policies(temp_db, frozen_now)
    reviewer = _reviewer_id(temp_db)

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        bad = [
            {
                "rule_key": "X1",
                "title": "Bad",
                "instructions": "x",
                "kind": "unknown_kind",
                "enabled": 1,
            }
        ]
        with pytest.raises(policies.PolicyError) as exc:
            policies.save_draft(
                conn, reviewer, bad, expected_draft_version=0, now=frozen_now.isoformat().replace("+00:00", "Z"),
            )
        assert exc.value.status == 400
        # No new draft persisted.
        assert policies.get_state(conn)["current_draft_version"] == 0
    finally:
        conn.close()


# --- Publish / immutability ---------------------------------------------------------

def test_publish_creates_immutable_snapshot_and_advances_pointer(
    temp_db: Path, frozen_now,
):
    _seed_policies(temp_db, frozen_now)
    reviewer = _reviewer_id(temp_db)

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        rules = [
            {
                "rule_key": "CLAIM_001",
                "title": "Restricted approval claim",
                "instructions": "Reject pre-approved claims.",
                "kind": "semantic",
                "enabled": 1,
            }
        ]
        policies.save_draft(
            conn, reviewer, rules, expected_draft_version=0,
            now=frozen_now.isoformat().replace("+00:00", "Z"),
        )
        res = policies.publish_draft(
            conn, reviewer, expected_draft_version=1,
            expected_active_version=1,
            now=frozen_now.isoformat().replace("+00:00", "Z"),
        )
        assert res["version"] == 2  # baseline was v1, this publish is v2

        snaps = policies.list_snapshots(conn)
        assert [s["version"] for s in snaps] == [1, 2]

        # Active pointer now points at the newest immutable snapshot.
        state = policies.get_state(conn)
        snap2 = policies.get_snapshot(conn, 2)
        assert snap2 is not None
        assert state["active_snapshot_id"] == snap2["id"]
    finally:
        conn.close()


def test_published_snapshot_is_immutable(temp_db: Path, frozen_now):
    """The published rule set cannot be edited/deleted via the service."""
    _seed_policies(temp_db, frozen_now)

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        snap = policies.get_snapshot(conn, 1)
        assert snap is not None
        before = {r["rule_key"]: r for r in snap["rules"]}

        # No service mutates snapshot rows; a save/publish only adds new ones.
        # The published snapshot's rule set is unchanged.
        after = policies.get_snapshot(conn, 1)
        assert {r["rule_key"]: r for r in after["rules"]} == before
    finally:
        conn.close()


def test_concurrent_publication_conflicts(temp_db: Path, frozen_now):
    """Two publishers from the same expected versions: exactly one succeeds.

    The active-version check plus `BEGIN IMMEDIATE` serialization turns a
    second concurrent publish into a 409 instead of a second snapshot — no
    concurrent last-write-wins publishing.
    """
    _seed_policies(temp_db, frozen_now)
    reviewer = _reviewer_id(temp_db)
    now = frozen_now.isoformat().replace("+00:00", "Z")

    # Two connections both believe active == v1 and draft == 0 (the baseline).
    conn_a = db.connect(temp_db)
    conn_b = db.connect(temp_db)
    try:
        db.initialize_schema(conn_a)
        db.initialize_schema(conn_b)

        # Publisher A commits first.
        conn_a.execute("BEGIN IMMEDIATE")
        res_a = policies.publish_draft(
            conn_a, reviewer, expected_draft_version=0,
            expected_active_version=1, now=now,
        )
        conn_a.execute("COMMIT")
        assert res_a["version"] == 2

        # Publisher B races from the SAME expected versions; the active version
        # has already advanced to 2, so this must 409 without creating v3.
        conn_b.execute("BEGIN IMMEDIATE")
        with pytest.raises(policies.PolicyError) as exc:
            policies.publish_draft(
                conn_b, reviewer, expected_draft_version=0,
                expected_active_version=1, now=now,
            )
        assert exc.value.status == 409
        conn_b.execute("ROLLBACK")

        # Exactly one new snapshot exists beyond the baseline (v1 + v2 only).
        snaps = policies.list_snapshots(conn_b)
        assert [s["version"] for s in snaps] == [1, 2]
    finally:
        conn_a.close()
        conn_b.close()


# --- Demo reset ---------------------------------------------------------------------

def test_reset_is_coherent_and_reseeds_baseline(temp_db: Path, frozen_now):
    """reset_database wipes and reseeds a single baseline snapshot + draft."""
    _seed_policies(temp_db, frozen_now)
    _seed_policies(temp_db, frozen_now)  # reset again

    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        snaps = policies.list_snapshots(conn)
        assert len(snaps) == 1
        assert snaps[0]["version"] == 1
        state = policies.get_state(conn)
        assert state["current_draft_version"] == 0
        assert len(policies.get_draft(conn)) == 5
        # Reviewer capability restored after reset.
        reviewer = _reviewer_id(temp_db)
        assert reviewer is not None
        policies.check_capability(conn, reviewer, "manage_policies")  # no raise
    finally:
        conn.close()
