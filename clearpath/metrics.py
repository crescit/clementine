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


def _utc_comparable(column: str) -> str:
    """Normalize a stored UTC ISO timestamp for lexicographic comparison.

    Pads fractional seconds to six digits so ``…:00Z`` and ``…:00.000000Z``
    compare as the same instant. Common whole-second ``Z`` / ``+00:00`` forms
    take a cheap GLOB fast path; mixed fractional forms use the full pad.
    """
    normalized = f"REPLACE(REPLACE({column}, 'Z', ''), '+00:00', '')"
    full_pad = (
        f"(SUBSTR({column}, 1, 19) || '.' || "
        f"SUBSTR(SUBSTR({normalized}, 21) || '000000', 1, 6))"
    )
    return (
        f"(CASE "
        f"WHEN {column} GLOB '????-??-??T??:??:??Z' "
        f"THEN SUBSTR({column}, 1, 19) || '.000000' "
        f"WHEN {column} GLOB '????-??-??T??:??:??+00:00' "
        f"THEN SUBSTR({column}, 1, 19) || '.000000' "
        f"ELSE {full_pad} END)"
    )


def compute_metrics(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    actor_id: str | None = None,
    scope: str = "all_submissions",
) -> dict:
    """Return the §7 metrics envelope for the current persona's visible set.

    Aggregates in SQL so large corpora are not materialized and sorted in Python.
    Open and terminal work are scanned separately so status indexes apply, and
    timestamp normalization runs once per compared column via a subquery.
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

    now_s = _bind_ts(now)
    cutoff_7 = _bind_ts(now - timedelta(days=7))
    cutoff_30 = _bind_ts(now - timedelta(days=30))
    open_ph = ",".join("?" for _ in OPEN_STATUSES)
    terminal_ph = ",".join("?" for _ in TERMINAL_STATUSES)
    sla_n = _utc_comparable("sla_breach_at")
    decided_n = _utc_comparable("decided_at")
    submitted_n = _utc_comparable("submitted_at")

    open_sql = f"""
        SELECT
          COUNT(*) AS open_count,
          COALESCE(SUM(CASE WHEN {sla_n} <= ? THEN 1 ELSE 0 END), 0)
            AS sla_breached_count,
          COALESCE(SUM(CASE WHEN assigned_reviewer_id IS NULL THEN 1 ELSE 0 END), 0)
            AS unassigned_count
        FROM submissions
        WHERE status IN ({open_ph}){scope_sql}
    """
    terminal_sql = f"""
        SELECT
          COALESCE(SUM(CASE WHEN decided_n >= ? AND decided_n <= ? THEN 1 ELSE 0 END), 0)
            AS completed_last_7_days,
          AVG(CASE WHEN decided_n >= ? AND decided_n <= ?
            AND decided_n >= submitted_n
            THEN (julianday(decided_n) - julianday(submitted_n))
            ELSE NULL END)
            AS avg_turnaround_days,
          COALESCE(SUM(CASE WHEN decided_n >= ? AND decided_n <= ?
            AND decided_n >= submitted_n THEN 1 ELSE 0 END), 0)
            AS turnaround_sample_size
        FROM (
          SELECT
            {decided_n} AS decided_n,
            {submitted_n} AS submitted_n
          FROM submissions
          WHERE status IN ({terminal_ph})
            AND decided_at IS NOT NULL{scope_sql}
        )
    """
    open_row = conn.execute(
        open_sql, [now_s, *OPEN_STATUSES, *params]
    ).fetchone()
    terminal_row = conn.execute(
        terminal_sql,
        [
            cutoff_7,
            now_s,
            cutoff_30,
            now_s,
            cutoff_30,
            now_s,
            *TERMINAL_STATUSES,
            *params,
        ],
    ).fetchone()

    sample_size = int(terminal_row["turnaround_sample_size"])
    avg_raw = terminal_row["avg_turnaround_days"]
    avg_turnaround_days: float | None = None
    if sample_size > 0 and avg_raw is not None:
        avg_turnaround_days = round(float(avg_raw), 1)

    return {
        "open_count": int(open_row["open_count"]),
        "sla_breached_count": int(open_row["sla_breached_count"]),
        "unassigned_count": int(open_row["unassigned_count"]),
        "avg_turnaround_days": avg_turnaround_days,
        "turnaround_sample_size": sample_size,
        "completed_last_7_days": int(terminal_row["completed_last_7_days"]),
        "scope": scope,
        "as_of": _iso(now),
    }


def _bind_ts(dt: datetime) -> str:
    """UTC timestamp with fixed microsecond precision for SQL comparisons."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
