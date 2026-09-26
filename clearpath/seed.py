"""Deterministic relative-time fixtures and demo reset (K1).

All timestamps are derived from a single captured `now` (UTC), so the demo
stays coherent regardless of presentation date. Reset is transactional: wipe
all rows and reseed in one write; on failure it rolls back and the previous
state is preserved. Fresh UUIDs are generated on every seed/reset.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clearpath import db

SLA_WINDOW = timedelta(hours=72)

# Persona display identities (IDs are fresh UUIDs per seed).
_PERSONAS = [
    ("Sarah T.", "REVIEWER", "Compliance Lead"),
    ("Mark Davis", "REVIEWER", "Compliance Analyst"),
    ("Jessica Lin", "SUBMITTER", "Partnerships"),
]


def _iso(dt: datetime) -> str:
    """Serialize UTC datetime as ISO 8601 with a trailing Z."""
    assert dt.tzinfo is not None
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_only(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _meta(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Seed data. Timestamps are relative offsets applied to the captured `now`.
# ---------------------------------------------------------------------------


def _build_seed(now: datetime) -> dict:
    """Return the baseline rows (users, submissions, versions, events)."""
    now = now.astimezone(timezone.utc)

    user_ids = {}
    users = []
    for name, role, title in _PERSONAS:
        uid = str(uuid.uuid4())
        user_ids[name] = uid
        users.append((uid, name, role, title, _iso(now - timedelta(days=30))))

    submissions = []
    versions = []
    events = []

    def add_submission(
        external_id: str,
        title: str,
        partner: str | None,
        channel: str,
        product: str,
        status: str,
        reviewer: str | None,
        submitted_delta: timedelta,
        launch_delta: timedelta,
        decided_delta: timedelta | None,
    ) -> tuple[str, str, datetime]:
        sid = str(uuid.uuid4())
        submitted_at = now + submitted_delta
        sla_breach_at = submitted_at + SLA_WINDOW
        decided_at = (now + decided_delta) if decided_delta is not None else None
        reviewer_id = user_ids[reviewer] if reviewer else None
        submissions.append(
            (
                sid,
                external_id,
                title,
                partner,
                channel,
                product,
                status,
                reviewer_id,
                user_ids["Jessica Lin"],
                _date_only(now + launch_delta),
                _iso(submitted_at),
                _iso(sla_breach_at),
                _iso(decided_at) if decided_at else None,
                0,  # current_version filled after versions are inserted
                0,  # record_version filled after events
                _iso(submitted_at),
                _iso(decided_at or submitted_at),
            ),
        )
        return sid, title, submitted_at

    def add_version(
        sid: str,
        title: str,
        copy_text: str,
        created_at: datetime,
        author: str,
        vnum: int,
    ) -> None:
        versions.append(
            (
                str(uuid.uuid4()),
                sid,
                vnum,
                None,
                copy_text,
                user_ids[author],
                _iso(created_at),
            ),
        )

    def add_event(
        sid: str,
        actor: str,
        event_type: str,
        created_at: datetime,
        from_status: str | None,
        to_status: str | None,
        version_number: int | None,
        comment: str | None,
        metadata: dict,
    ) -> None:
        events.append(
            (
                sid,
                user_ids[actor],
                event_type,
                from_status,
                to_status,
                version_number,
                comment,
                _meta(metadata),
                _iso(created_at),
            ),
        )

    # --- CP-8902 / CreditKarma — Fall Promo (pending, unassigned) ---
    sid, title, t = add_submission(
        "CP-8902",
        "CreditKarma — Fall Promo",
        "CreditKarma",
        "AFFILIATE",
        "PERSONAL_LOAN",
        "PENDING_ASSIGNMENT",
        None,
        timedelta(days=-15),
        timedelta(days=2),
        None,
    )
    add_version(
        sid,
        title,
        "Pre-approved personal loans up to $25,000. Apply today.",
        t,
        "Jessica Lin",
        1,
    )
    add_event(
        sid,
        "Jessica Lin",
        "SUBMITTED",
        t,
        None,
        "PENDING_ASSIGNMENT",
        1,
        None,
        {"channel": "AFFILIATE", "product": "PERSONAL_LOAN"},
    )

    # --- CP-8903 / Q4 First-Time Buyer (under review, Sarah) ---
    sid, title, t = add_submission(
        "CP-8903",
        "Q4 First-Time Buyer",
        None,
        "EMAIL",
        "MORTGAGE_PREQUALIFICATION",
        "UNDER_REVIEW",
        "Sarah T.",
        timedelta(days=-4),
        timedelta(days=4),
        None,
    )
    add_version(
        sid,
        title,
        "You're pre-approved for a mortgage. Start your home search today.",
        t,
        "Jessica Lin",
        1,
    )
    add_event(
        sid,
        "Jessica Lin",
        "SUBMITTED",
        t,
        None,
        "PENDING_ASSIGNMENT",
        1,
        None,
        {"channel": "EMAIL", "product": "MORTGAGE_PREQUALIFICATION"},
    )
    add_event(
        sid,
        "Jessica Lin",
        "AUTO_ASSIGNED",
        t,
        "PENDING_ASSIGNMENT",
        "UNDER_REVIEW",
        1,
        None,
        {"previous_reviewer": None, "assignment_method": "automatic"},
    )

    # --- CP-8904 / ClearRewards Launch (under review, Sarah; walkthrough flow) ---
    sid, title, t = add_submission(
        "CP-8904",
        "ClearRewards Launch",
        None,
        "SOCIAL",
        "CREDIT_CARD",
        "UNDER_REVIEW",
        "Sarah T.",
        timedelta(days=-2),
        timedelta(days=3),
        None,
    )
    add_version(
        sid,
        title,
        "You're pre-approved for ClearRewards. Explore rewards for everyday purchases.",
        t,
        "Jessica Lin",
        1,
    )
    add_event(
        sid,
        "Jessica Lin",
        "SUBMITTED",
        t,
        None,
        "PENDING_ASSIGNMENT",
        1,
        None,
        {"channel": "SOCIAL", "product": "CREDIT_CARD"},
    )
    add_event(
        sid,
        "Jessica Lin",
        "AUTO_ASSIGNED",
        t,
        "PENDING_ASSIGNMENT",
        "UNDER_REVIEW",
        1,
        None,
        {"previous_reviewer": None, "assignment_method": "automatic"},
    )

    # --- CP-8905 / NerdWallet Debt Consolidation (approved, Mark) ---
    sid, title, t = add_submission(
        "CP-8905",
        "NerdWallet Debt Consolidation",
        "NerdWallet",
        "AFFILIATE",
        "PERSONAL_LOAN",
        "APPROVED",
        "Mark Davis",
        timedelta(days=-8),
        timedelta(days=1),
        timedelta(days=-3),
    )
    add_version(
        sid,
        title,
        "Guaranteed approval for debt consolidation loans.",
        t,
        "Jessica Lin",
        1,
    )
    add_event(
        sid,
        "Jessica Lin",
        "SUBMITTED",
        t,
        None,
        "PENDING_ASSIGNMENT",
        1,
        None,
        {"channel": "AFFILIATE", "product": "PERSONAL_LOAN"},
    )
    add_event(
        sid,
        "Jessica Lin",
        "AUTO_ASSIGNED",
        t,
        "PENDING_ASSIGNMENT",
        "UNDER_REVIEW",
        1,
        None,
        {"previous_reviewer": None, "assignment_method": "automatic"},
    )
    # Request changes on v1 at -6d.
    req_t = now + timedelta(days=-6)
    add_event(
        sid,
        "Mark Davis",
        "CHANGES_REQUESTED",
        req_t,
        "UNDER_REVIEW",
        "CHANGES_REQUESTED",
        1,
        "Replace the guarantee with accurate prequalification language.",
        {"policy_version": "clearpath-demo-v1"},
    )
    # Corrected v2 resubmitted, then approved at -3d.
    resub_t = now + timedelta(days=-3)
    add_version(
        sid,
        title,
        "Pre-qualified debt consolidation loans. Subject to credit approval. ClearPath may compensate this partner.",
        resub_t,
        "Jessica Lin",
        2,
    )
    add_event(
        sid,
        "Jessica Lin",
        "RESUBMITTED",
        resub_t,
        "CHANGES_REQUESTED",
        "UNDER_REVIEW",
        2,
        None,
        {"version": 2},
    )
    add_event(
        sid,
        "Mark Davis",
        "APPROVED",
        resub_t,
        "UNDER_REVIEW",
        "APPROVED",
        2,
        "Approved after corrected copy.",
        {"policy_version": "clearpath-demo-v1", "version": 2},
    )

    # --- CP-8906 / ClearRewards Brand Terms (pending, unassigned) ---
    sid, title, t = add_submission(
        "CP-8906",
        "ClearRewards Brand Terms",
        None,
        "PAID_SEARCH",
        "CREDIT_CARD",
        "PENDING_ASSIGNMENT",
        None,
        timedelta(days=-1),
        timedelta(days=6),
        None,
    )
    add_version(
        sid,
        title,
        "ClearRewards card — earn rewards on every purchase.",
        t,
        "Jessica Lin",
        1,
    )
    add_event(
        sid,
        "Jessica Lin",
        "SUBMITTED",
        t,
        None,
        "PENDING_ASSIGNMENT",
        1,
        None,
        {"channel": "PAID_SEARCH", "product": "CREDIT_CARD"},
    )

    # --- CP-8907 / LendingTree Rate Table (changes requested, Mark) ---
    sid, title, t = add_submission(
        "CP-8907",
        "LendingTree Rate Table",
        "LendingTree",
        "AFFILIATE",
        "PERSONAL_LOAN",
        "CHANGES_REQUESTED",
        "Mark Davis",
        timedelta(days=-1),
        timedelta(days=5),
        None,
    )
    add_version(
        sid,
        title,
        "Rates as low as 5.99% APR. Get your best offer. Subject to credit approval.",
        t,
        "Jessica Lin",
        1,
    )
    add_event(
        sid,
        "Jessica Lin",
        "SUBMITTED",
        t,
        None,
        "PENDING_ASSIGNMENT",
        1,
        None,
        {"channel": "AFFILIATE", "product": "PERSONAL_LOAN"},
    )
    add_event(
        sid,
        "Jessica Lin",
        "AUTO_ASSIGNED",
        t,
        "PENDING_ASSIGNMENT",
        "UNDER_REVIEW",
        1,
        None,
        {"previous_reviewer": None, "assignment_method": "automatic"},
    )
    add_event(
        sid,
        "Mark Davis",
        "CHANGES_REQUESTED",
        t,
        "UNDER_REVIEW",
        "CHANGES_REQUESTED",
        1,
        "Add the partner disclosure required for affiliate copy.",
        {"policy_version": "clearpath-demo-v1"},
    )

    # --- CP-8908 / Spring Mortgage Preview (under review, Mark) ---
    sid, title, t = add_submission(
        "CP-8908",
        "Spring Mortgage Preview",
        None,
        "WEBSITE",
        "MORTGAGE_PREQUALIFICATION",
        "UNDER_REVIEW",
        "Mark Davis",
        timedelta(hours=-12),
        timedelta(days=8),
        None,
    )
    add_version(
        sid, title, "Pre-qualify for a mortgage in minutes. Prequalification is not a commitment to lend.", t, "Jessica Lin", 1
    )
    add_event(
        sid,
        "Jessica Lin",
        "SUBMITTED",
        t,
        None,
        "PENDING_ASSIGNMENT",
        1,
        None,
        {"channel": "WEBSITE", "product": "MORTGAGE_PREQUALIFICATION"},
    )
    add_event(
        sid,
        "Jessica Lin",
        "AUTO_ASSIGNED",
        t,
        "PENDING_ASSIGNMENT",
        "UNDER_REVIEW",
        1,
        None,
        {"previous_reviewer": None, "assignment_method": "automatic"},
    )

    # Finalize per-submission version/event counts. Version rows are
    # (version_id, sid, vnum, asset_url, copy_text, created_by, created_at).
    version_counts: dict[str, int] = {}
    for _vid, sid, vnum, *_rest in versions:
        version_counts[sid] = max(version_counts.get(sid, 0), vnum)
    event_counts: dict[str, int] = {}
    for sid, *_rest in events:
        event_counts[sid] = event_counts.get(sid, 0) + 1

    submissions = [list(row) for row in submissions]
    for row in submissions:
        sid = row[0]
        row[13] = version_counts[sid]  # current_version
        row[14] = event_counts[sid]  # record_version

    return {
        "users": users,
        "submissions": submissions,
        "versions": versions,
        "events": events,
    }


# ---------------------------------------------------------------------------
# Insert / reset
# ---------------------------------------------------------------------------


def _insert_seed(conn: sqlite3.Connection, now: datetime) -> None:
    """Insert baseline fixtures. No commit — caller owns the transaction."""
    seed = _build_seed(now)
    for uid, name, role, title, created in seed["users"]:
        conn.execute(
            "INSERT INTO users (id, name, role, display_title, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, name, role, title, created),
        )
    for row in seed["submissions"]:
        conn.execute(
            "INSERT INTO submissions (id, external_id, title, partner, channel, "
            "product, status, assigned_reviewer_id, submitter_id, "
            "target_launch_date, submitted_at, sla_breach_at, decided_at, "
            "current_version, record_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    for row in seed["versions"]:
        conn.execute(
            "INSERT INTO submission_versions (id, submission_id, version_number, "
            "asset_url, copy_text, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    for row in seed["events"]:
        conn.execute(
            "INSERT INTO audit_events (submission_id, actor_id, event_type, "
            "from_status, to_status, version_number, comment, metadata_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )


def seed_database(conn: sqlite3.Connection, now: datetime) -> None:
    """Insert baseline fixtures into an empty, initialized schema."""
    _insert_seed(conn, now)
    conn.commit()


def reset_database(db_path: Path | str, now: datetime) -> None:
    """Demo-only reset: wipe all rows and reseed with fresh UUIDs in one write.

    On any failure the transaction rolls back, preserving the previous state.
    """
    conn = db.connect(Path(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("audit_events", "submission_versions", "submissions", "users"):
            conn.execute(f"DELETE FROM {table}")
        _insert_seed(conn, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def auto_seed(db_path: Path | str, now: datetime) -> bool:
    """Initialize and seed a fresh DB. Returns True when seeded, False when the
    existing DB was preserved (restart path)."""
    conn = db.connect(Path(db_path))
    try:
        created = db.initialize_schema(conn)
        if created:
            seed_database(conn, now)
        return created
    finally:
        conn.close()
