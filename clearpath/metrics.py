"""Queue urgency and metric calculations (K5, §7).

Pure helpers over a sqlite connection. The API layer owns identity checks;
these functions assume the caller has already applied visibility rules and
pass the rows to sort/aggregate.

Urgency buckets (§7):
- BREACHED  if now >= sla_breach_at
- DUE_24H   if remaining time is positive and <= 24 hours
- DUE_48H   if > 24 and <= 48 hours
- ON_TRACK  otherwise
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlite3

# --- Submission statuses (mirror workflow.SubmissionStatus) ------------------
OPEN_STATUSES = ("PENDING_ASSIGNMENT", "UNDER_REVIEW", "CHANGES_REQUESTED")
TERMINAL_STATUSES = ("APPROVED", "REJECTED")

# --- Urgency buckets ---------------------------------------------------------
BREACHED = "BREACHED"
DUE_24H = "DUE_24H"
DUE_48H = "DUE_48H"
ON_TRACK = "ON_TRACK"

_BUCKET_ORDER = {BREACHED: 0, DUE_24H: 1, DUE_48H: 2, ON_TRACK: 3}

_SELECT = (
    "SELECT id, external_id, title, partner, channel, product, status, "
    "assigned_reviewer_id, submitter_id, target_launch_date, submitted_at, "
    "sla_breach_at, decided_at, current_version, record_version, "
    "created_at, updated_at FROM submissions"
)


def urgency_bucket(now: datetime, sla_breach_at: str) -> str:
    """Classify a submission into one of the four §7 urgency buckets."""
    breach = datetime.fromisoformat(sla_breach_at).astimezone(timezone.utc)
    remaining = breach - now
    if remaining <= timedelta(0):
        return BREACHED
    hours = remaining.total_seconds() / 3600.0
    if hours <= 24:
        return DUE_24H
    if hours <= 48:
        return DUE_48H
    return ON_TRACK


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _sort_key(item: dict, now: datetime) -> tuple:
    """§7 sort: urgency bucket order, then earliest SLA, launch, submitted, ext id."""
    bucket = urgency_bucket(now, item["sla_breach_at"])
    return (
        _BUCKET_ORDER[bucket],
        item["sla_breach_at"],
        item["target_launch_date"],
        item["submitted_at"],
        item["external_id"],
    )


def queue(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    actor_id: str | None = None,
    mine: bool = False,
    status: str | None = None,
    search: str | None = None,
    completed: bool | None = None,
) -> list[dict]:
    """Return the open queue sorted per §7.

    Filters:
    - actor_id + mine=True  -> only rows the actor owns (submitter_id == actor_id)
    - actor_id + mine=False -> only rows the actor may review (assigned or any
      reviewer). Callers apply stricter reviewer rules; here we keep rows that
      are assigned to the actor OR are unassigned (open to any reviewer).
    - status                -> exact status value filter
    - search                -> case-insensitive LIKE on title, partner, external_id
    - completed             -> True: only terminal rows; False/None: only open rows
    """
    clauses = []
    params: list[str] = []

    if completed is True:
        clauses.append("status IN (?, ?)")
        params.extend(TERMINAL_STATUSES)
    else:
        clauses.append("status IN (?, ?, ?)")
        params.extend(OPEN_STATUSES)

    if actor_id is not None:
        if mine:
            clauses.append("submitter_id = ?")
            params.append(actor_id)
        else:
            clauses.append("(assigned_reviewer_id = ? OR assigned_reviewer_id IS NULL)")
            params.append(actor_id)

    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    if search is not None:
        clauses.append("(title LIKE ? OR partner LIKE ? OR external_id LIKE ?)")
        like = f"%{search}%"
        params.extend((like, like, like))

    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"{_SELECT} WHERE {where} ORDER BY submitted_at"
    rows = conn.execute(sql, params).fetchall()

    items = [_row_to_dict(r) for r in rows]
    items.sort(key=lambda it: _sort_key(it, now))
    return items


def compute_metrics(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    actor_id: str | None = None,
    scope: str = "all_submissions",
) -> dict:
    """Return the §7 metrics envelope for the current persona's visible set.

    Aggregates in SQL so large corpora are not materialized and sorted in Python.
    """
    clauses: list[str] = []
    params: list = []
    if scope == "own_submissions" and actor_id is not None:
        clauses.append("submitter_id = ?")
        params.append(actor_id)
    elif actor_id is not None:
        clauses.append("(assigned_reviewer_id = ? OR assigned_reviewer_id IS NULL)")
        params.append(actor_id)
    scope_sql = (" AND " + " AND ".join(clauses)) if clauses else ""

    # Stored timestamps are UTC. Pad fractional seconds to six places so
    # equivalent instants (including .000000) compare equally at boundaries.
    def ts(column: str) -> str:
        normalized = f"REPLACE(REPLACE({column}, 'Z', ''), '+00:00', '')"
        return (
            f"(SUBSTR({column}, 1, 19) || '.' || "
            f"SUBSTR(SUBSTR({normalized}, 21) || '000000', 1, 6))"
        )

    now_s = _bind_ts(now)
    cutoff_7 = _bind_ts(now - timedelta(days=7))
    cutoff_30 = _bind_ts(now - timedelta(days=30))
    open_ph = ",".join("?" for _ in OPEN_STATUSES)
    terminal_ph = ",".join("?" for _ in TERMINAL_STATUSES)

    sql = f"""
        SELECT
          COALESCE(SUM(CASE WHEN status IN ({open_ph}) THEN 1 ELSE 0 END), 0)
            AS open_count,
          COALESCE(SUM(CASE WHEN status IN ({open_ph})
            AND {ts("sla_breach_at")} <= ? THEN 1 ELSE 0 END), 0)
            AS sla_breached_count,
          COALESCE(SUM(CASE WHEN status IN ({open_ph})
            AND assigned_reviewer_id IS NULL THEN 1 ELSE 0 END), 0)
            AS unassigned_count,
          COALESCE(SUM(CASE WHEN status IN ({terminal_ph})
            AND decided_at IS NOT NULL
            AND {ts("decided_at")} >= ?
            AND {ts("decided_at")} <= ?
            THEN 1 ELSE 0 END), 0)
            AS completed_last_7_days,
          AVG(CASE WHEN status IN ({terminal_ph})
            AND decided_at IS NOT NULL
            AND {ts("decided_at")} >= ?
            AND {ts("decided_at")} <= ?
            AND {ts("decided_at")} >= {ts("submitted_at")}
            THEN (julianday({ts("decided_at")}) - julianday({ts("submitted_at")}))
            ELSE NULL END)
            AS avg_turnaround_days,
          COALESCE(SUM(CASE WHEN status IN ({terminal_ph})
            AND decided_at IS NOT NULL
            AND {ts("decided_at")} >= ?
            AND {ts("decided_at")} <= ?
            AND {ts("decided_at")} >= {ts("submitted_at")}
            THEN 1 ELSE 0 END), 0)
            AS turnaround_sample_size
        FROM submissions
        WHERE 1=1{scope_sql}
    """
    bind = [
        *OPEN_STATUSES,
        *OPEN_STATUSES,
        now_s,
        *OPEN_STATUSES,
        *TERMINAL_STATUSES,
        cutoff_7,
        now_s,
        *TERMINAL_STATUSES,
        cutoff_30,
        now_s,
        *TERMINAL_STATUSES,
        cutoff_30,
        now_s,
        *params,
    ]
    row = conn.execute(sql, bind).fetchone()
    sample_size = int(row["turnaround_sample_size"])
    avg_raw = row["avg_turnaround_days"]
    avg_turnaround_days: float | None = None
    if sample_size > 0 and avg_raw is not None:
        avg_turnaround_days = round(float(avg_raw), 1)

    return {
        "open_count": int(row["open_count"]),
        "sla_breached_count": int(row["sla_breached_count"]),
        "unassigned_count": int(row["unassigned_count"]),
        "avg_turnaround_days": avg_turnaround_days,
        "turnaround_sample_size": sample_size,
        "completed_last_7_days": int(row["completed_last_7_days"]),
        "scope": scope,
        "as_of": _iso(now),
    }


def _bind_ts(dt: datetime) -> str:
    """UTC timestamp with fixed microsecond precision for SQL comparisons."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
