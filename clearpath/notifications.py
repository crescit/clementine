"""Durable in-app notifications backed by SQLite.

Rows are written when audit events land. Read state is stored on the row
(`read_at`), not in the browser — shared demo state survives reloads and
persona switches on the same database.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

_HEADLINES = {
    "AUTO_ASSIGNED": "Assigned to you",
    "ASSIGNED": "Assigned to you",
    "REASSIGNED": "Reassigned to you",
    "RESUBMITTED": "Revision ready for review",
    "CHANGES_REQUESTED": "Changes requested on your campaign",
    "APPROVED": "Campaign approved",
    "REJECTED": "Campaign rejected",
}

_SUBMITTER_ASSIGNMENT_HEADLINE = "Reviewer assigned to your campaign"


def _iso_or_pass(value: str) -> str:
    return value


def recipients_for_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    actor_id: str,
    submission_id: str,
    metadata: dict | None,
) -> list[tuple[str, str]]:
    """Return (recipient_id, headline) pairs for an audit event."""
    meta = metadata or {}
    sub = conn.execute(
        "SELECT submitter_id, assigned_reviewer_id FROM submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    if sub is None:
        return []

    targets: list[tuple[str, str]] = []
    if event_type in {"AUTO_ASSIGNED", "ASSIGNED", "REASSIGNED"}:
        assignee = meta.get("assignee_id") or sub["assigned_reviewer_id"]
        if assignee and assignee != actor_id:
            targets.append((assignee, _HEADLINES[event_type]))
        # Marketers always get an ownership notice — including auto-assign on intake,
        # where the submitter is also the audit actor.
        if sub["submitter_id"]:
            targets.append((sub["submitter_id"], _SUBMITTER_ASSIGNMENT_HEADLINE))
    elif event_type == "RESUBMITTED":
        reviewer = sub["assigned_reviewer_id"]
        if reviewer and reviewer != actor_id:
            targets.append((reviewer, _HEADLINES[event_type]))
    elif event_type in {"CHANGES_REQUESTED", "APPROVED", "REJECTED"}:
        submitter = sub["submitter_id"]
        if submitter and submitter != actor_id:
            targets.append((submitter, _HEADLINES[event_type]))

    # Preserve first headline per recipient (assignee copy wins over submitter copy).
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for uid, headline in targets:
        if not uid or uid in seen:
            continue
        seen.add(uid)
        unique.append((uid, headline))
    return unique


def fanout_event(
    conn: sqlite3.Connection,
    *,
    audit_event_id: int,
    submission_id: str,
    actor_id: str,
    event_type: str,
    created_at: str,
    metadata: dict | None = None,
) -> int:
    """Insert durable notification rows for an audit event. Returns insert count."""
    created = 0
    for recipient_id, headline in recipients_for_event(
        conn,
        event_type=event_type,
        actor_id=actor_id,
        submission_id=submission_id,
        metadata=metadata,
    ):
        conn.execute(
            "INSERT OR IGNORE INTO notifications "
            "(id, recipient_id, submission_id, audit_event_id, event_type, "
            "headline, created_at, read_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                str(uuid.uuid4()),
                recipient_id,
                submission_id,
                audit_event_id,
                event_type,
                headline,
                _iso_or_pass(created_at),
            ),
        )
        created += conn.execute("SELECT changes()").fetchone()[0]
    return created


def backfill(conn: sqlite3.Connection) -> int:
    """Create missing notification rows from existing audit_events."""
    rows = conn.execute(
        "SELECT id, submission_id, actor_id, event_type, metadata_json, created_at "
        "FROM audit_events ORDER BY id"
    ).fetchall()
    total = 0
    for row in rows:
        metadata = None
        if row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = None
        total += fanout_event(
            conn,
            audit_event_id=row["id"],
            submission_id=row["submission_id"],
            actor_id=row["actor_id"],
            event_type=row["event_type"],
            created_at=row["created_at"],
            metadata=metadata,
        )
    return total


def list_for_user(
    conn: sqlite3.Connection, recipient_id: str, *, limit: int = 20
) -> list[dict]:
    rows = conn.execute(
        "SELECT n.id, n.submission_id, n.event_type, n.headline, n.created_at, "
        "n.read_at, n.audit_event_id, s.external_id, s.title, e.actor_id, "
        "e.comment, e.version_number "
        "FROM notifications n "
        "JOIN submissions s ON s.id = n.submission_id "
        "JOIN audit_events e ON e.id = n.audit_event_id "
        "WHERE n.recipient_id = ? "
        "ORDER BY n.created_at DESC, n.audit_event_id DESC "
        "LIMIT ?",
        (recipient_id, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "submission_id": row["submission_id"],
            "external_id": row["external_id"],
            "title": row["title"],
            "event_type": row["event_type"],
            "headline": row["headline"],
            "comment": row["comment"],
            "actor_id": row["actor_id"],
            "version_number": row["version_number"],
            "created_at": row["created_at"],
            "read_at": row["read_at"],
            "unread": row["read_at"] is None,
        }
        for row in rows
    ]


def unread_count(conn: sqlite3.Connection, recipient_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications "
        "WHERE recipient_id = ? AND read_at IS NULL",
        (recipient_id,),
    ).fetchone()
    return int(row["n"])


def mark_read(
    conn: sqlite3.Connection, recipient_id: str, notification_id: str, *, now: str
) -> bool:
    cur = conn.execute(
        "UPDATE notifications SET read_at = ? "
        "WHERE id = ? AND recipient_id = ? AND read_at IS NULL",
        (now, notification_id, recipient_id),
    )
    return cur.rowcount > 0


def mark_all_read(conn: sqlite3.Connection, recipient_id: str, *, now: str) -> int:
    cur = conn.execute(
        "UPDATE notifications SET read_at = ? "
        "WHERE recipient_id = ? AND read_at IS NULL",
        (now, recipient_id),
    )
    return cur.rowcount
