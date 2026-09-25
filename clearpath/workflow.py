"""Pure domain rules for the ClearPath workflow (§5, §6) — no DB access.

K2: visibility, permission, and the transition matrix as pure logic that
routes (K6) and the transactional workflow layer (K3) both rely on. Every
check is a pure function over small immutable records so it is fully
testable without a database.

Error classes carry the canonical machine-readable `code` strings from §9
so HTTP mapping stays in the route layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from clearpath.models import ErrorCode, Role, SubmissionStatus


class Action(StrEnum):
    CREATE = "CREATE"
    ASSIGN = "ASSIGN"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    RESUBMIT = "RESUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"


# --- Domain records (pure views, no DB) --------------------------------------

@dataclass(frozen=True)
class UserRecord:
    id: str
    role: Role


@dataclass(frozen=True)
class SubmissionRecord:
    """Minimal read-only view of a submission for rule evaluation."""
    id: str
    status: SubmissionStatus
    submitter_id: str
    assigned_reviewer_id: str | None = None
    current_version: int = 1

    @property
    def is_terminal(self) -> bool:
        return self.status in (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED)

    @property
    def is_open(self) -> bool:
        return not self.is_terminal


# --- Domain errors -----------------------------------------------------------

class DomainError(Exception):
    """Base for domain-rule failures carrying a canonical §9 code."""

    code: str = ErrorCode.INTERNAL_ERROR
    message: str = ""
    details: dict | None = None

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    code = ErrorCode.NOT_FOUND

    def __init__(self) -> None:
        super().__init__("Submission not found or not visible to this actor")


class ForbiddenError(DomainError):
    code = ErrorCode.FORBIDDEN

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, details)


class InvalidTransitionError(DomainError):
    code = ErrorCode.INVALID_TRANSITION

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, details)


class ValidationError(DomainError):
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, details)


# --- Transition matrix (§5) ---------------------------------------------------
# action -> (allowed source statuses, destination resolver).
# Destination may depend on reviewer availability, resolved by the caller.

def _dest_assign(source: SubmissionStatus, *, reviewer_available: bool) -> SubmissionStatus:
    # Pending becomes UNDER_REVIEW; other open states stay the same.
    if source == SubmissionStatus.PENDING_ASSIGNMENT:
        return SubmissionStatus.UNDER_REVIEW
    return source


def _dest_create(
    source: SubmissionStatus, *, reviewer_available: bool
) -> SubmissionStatus:
    # New record: UNDER_REVIEW normally, PENDING_ASSIGNMENT if no reviewers.
    return (
        SubmissionStatus.UNDER_REVIEW
        if reviewer_available
        else SubmissionStatus.PENDING_ASSIGNMENT
    )


def _dest_resubmit(
    source: SubmissionStatus, *, reviewer_available: bool
) -> SubmissionStatus:
    # Owning submitter revises: back to review, or pending if no reviewer yet.
    return (
        SubmissionStatus.UNDER_REVIEW
        if reviewer_available
        else SubmissionStatus.PENDING_ASSIGNMENT
    )


# Transitions that never apply to an existing record (CREATE is a new record).
Destination = Callable[[SubmissionStatus, bool], SubmissionStatus]

_TRANSITIONS: dict[Action, tuple[tuple[SubmissionStatus, ...], Destination]] = {
    Action.ASSIGN: (
        (SubmissionStatus.PENDING_ASSIGNMENT, SubmissionStatus.UNDER_REVIEW, SubmissionStatus.CHANGES_REQUESTED),
        _dest_assign,
    ),
    Action.REQUEST_CHANGES: (
        (SubmissionStatus.UNDER_REVIEW,),
        lambda source, *, reviewer_available: SubmissionStatus.CHANGES_REQUESTED,
    ),
    Action.RESUBMIT: (
        (SubmissionStatus.CHANGES_REQUESTED,),
        _dest_resubmit,
    ),
    Action.APPROVE: (
        (SubmissionStatus.UNDER_REVIEW,),
        lambda source, *, reviewer_available: SubmissionStatus.APPROVED,
    ),
    Action.REJECT: (
        (SubmissionStatus.UNDER_REVIEW,),
        lambda source, *, reviewer_available: SubmissionStatus.REJECTED,
    ),
}


# --- Visibility (§5) ---------------------------------------------------------

def check_visibility(actor: UserRecord, submission: SubmissionRecord) -> None:
    """Reviewers see all submissions; submitters see only their own."""
    if actor.role == Role.REVIEWER:
        return
    if submission.submitter_id != actor.id:
        raise NotFoundError()


# --- Permission (§5) ---------------------------------------------------------

def _permitted_actions(actor: UserRecord, submission: SubmissionRecord) -> set[Action]:
    if actor.role == Role.SUBMITTER:
        return {Action.CREATE, Action.RESUBMIT}
    return {Action.ASSIGN, Action.REQUEST_CHANGES, Action.APPROVE, Action.REJECT}


def check_permission(action: Action, actor: UserRecord, submission: SubmissionRecord) -> None:
    """Role and ownership checks for a *specific* action."""
    # CREATE: only a submitter creates their own work.
    if action == Action.CREATE:
        if actor.role != Role.SUBMITTER:
            raise ForbiddenError(
                "Only a submitter may create a submission",
                {"action": action.value, "actor_role": actor.role.value},
            )
        return

    # RESUBMIT: only the owning submitter may revise their work.
    if action == Action.RESUBMIT:
        if actor.role != Role.SUBMITTER or submission.submitter_id != actor.id:
            raise ForbiddenError(
                "Only the owning submitter may revise a submission",
                {"action": action.value},
            )
        return

    # ASSIGN: any reviewer may assign/reassign.
    if action == Action.ASSIGN:
        if actor.role != Role.REVIEWER:
            raise ForbiddenError(
                "Only a reviewer may assign a reviewer",
                {"action": action.value, "actor_role": actor.role.value},
            )
        return

    # REQUEST_CHANGES / APPROVE / REJECT: only the *assigned* reviewer.
    if actor.role != Role.REVIEWER:
        raise ForbiddenError(
            "Only a reviewer may perform this action",
            {"action": action.value, "actor_role": actor.role.value},
        )
    if submission.assigned_reviewer_id != actor.id:
        raise ForbiddenError(
            "Only the assigned reviewer may perform this action",
            {"action": action.value, "assigned_reviewer_id": submission.assigned_reviewer_id},
        )


def permitted_actions(actor: UserRecord, submission: SubmissionRecord) -> set[Action]:
    """Set of actions this actor may *attempt* on a submission (no transition
    or content checks). Useful for UI hints and tests."""
    return _permitted_actions(actor, submission)


# --- Transition rules (§5) ---------------------------------------------------

def check_transition(
    action: Action,
    submission: SubmissionRecord,
    *,
    reviewer_available: bool = True,
    target_reviewer_id: str | None = None,
    feedback: str | None = None,
    copy_text: str | None = None,
    asset_url: str | None = None,
    prior_copy: str | None = None,
    prior_asset_url: str | None = None,
) -> SubmissionStatus:
    """Validate the transition matrix and content conditions. Returns the
    destination status on success; raises on any violation."""
    if action == Action.CREATE:
        # CREATE acts on a new record; no prior state to guard.
        return _dest_create(SubmissionStatus.UNDER_REVIEW, reviewer_available=reviewer_available)

    if submission.is_terminal:
        raise InvalidTransitionError(
            "Terminal submissions cannot be edited or reassigned",
            {"action": action.value, "status": submission.status.value},
        )

    if action not in _TRANSITIONS:
        raise InvalidTransitionError(
            "Action not supported",
            {"action": action.value},
        )

    sources, dest_fn = _TRANSITIONS[action]
    if submission.status not in sources:
        raise InvalidTransitionError(
            f"Action {action.value} is not allowed from status {submission.status.value}",
            {"action": action.value, "source": submission.status.value},
        )

    # Content conditions per §5.
    _check_content(
        action, submission,
        target_reviewer_id=target_reviewer_id,
        feedback=feedback,
        copy_text=copy_text,
        asset_url=asset_url,
        prior_copy=prior_copy,
        prior_asset_url=prior_asset_url,
    )

    return dest_fn(submission.status, reviewer_available=reviewer_available)


def _check_content(
    action: Action,
    submission: SubmissionRecord,
    *,
    target_reviewer_id: str | None,
    feedback: str | None,
    copy_text: str | None,
    asset_url: str | None,
    prior_copy: str | None,
    prior_asset_url: str | None,
) -> None:
    details: dict | None = None

    if action == Action.REQUEST_CHANGES:
        # Nonblank feedback required.
        if not feedback or not feedback.strip():
            details = {"action": action.value, "reason": "feedback required"}
            raise ValidationError("Feedback is required to request changes", details)

    if action == Action.REJECT:
        # Nonblank rejection reason required.
        if not feedback or not feedback.strip():
            details = {"action": action.value, "reason": "comment required"}
            raise ValidationError("A rejection reason is required", details)

    if action == Action.RESUBMIT:
        # Owning submitter must supply changed copy or asset URL.
        if not copy_text and not asset_url:
            details = {"action": action.value, "reason": "copy or asset_url required"}
            raise ValidationError("A revision must change copy or asset URL", details)
        if copy_text is not None and prior_copy is not None and copy_text == prior_copy \
                and (asset_url is None or (prior_asset_url is not None and asset_url == prior_asset_url)):
            details = {"action": action.value, "reason": "no content changed"}
            raise InvalidTransitionError("Resubmission must change copy or asset URL", details)

    if action == Action.ASSIGN:
        # Assignment to the already-assigned reviewer is a no-op → invalid.
        if target_reviewer_id is not None and submission.assigned_reviewer_id == target_reviewer_id:
            details = {"action": action.value, "reason": "reviewer already assigned"}
            raise InvalidTransitionError(
                "Reviewer is already assigned to this submission", details
            )
        # Reassigning away from an existing reviewer requires a reason.
        if submission.assigned_reviewer_id is not None and not (feedback or "").strip():
            details = {"action": action.value, "reason": "reassignment reason required"}
            raise ValidationError(
                "A reason is required when reassigning a reviewer", details
            )


def validate_action(
    action: Action,
    actor: UserRecord,
    submission: SubmissionRecord,
    *,
    reviewer_available: bool = True,
    target_reviewer_id: str | None = None,
    feedback: str | None = None,
    copy_text: str | None = None,
    asset_url: str | None = None,
    prior_copy: str | None = None,
    prior_asset_url: str | None = None,
) -> SubmissionStatus:
    """One-stop pure check: visibility, permission, then transition.

    Returns the destination status on success. Raises on any violation."""
    check_visibility(actor, submission)
    check_permission(action, actor, submission)
    return check_transition(
        action, submission,
        reviewer_available=reviewer_available,
        target_reviewer_id=target_reviewer_id,
        feedback=feedback,
        copy_text=copy_text,
        asset_url=asset_url,
        prior_copy=prior_copy,
        prior_asset_url=prior_asset_url,
    )


__all__ = [
    "Action", "UserRecord", "SubmissionRecord", "DomainError", "NotFoundError",
    "ForbiddenError", "InvalidTransitionError", "ValidationError",
    "check_visibility", "check_permission", "check_transition",
    "permitted_actions", "validate_action",
]


# =============================================================================
# K3 transactional layer — service functions own the write transaction.
# The API layer (K6) maps raised DomainError codes to HTTP; reads never call
# these. Each function opens a single write transaction on `conn`.
# =============================================================================

from datetime import datetime
from clearpath.models import Channel, Product
from clearpath.preflight import POLICY_VERSION, run_preflight


class VersionConflictError(DomainError):
    code = ErrorCode.VERSION_CONFLICT

    def __init__(self, current_record_version: int) -> None:
        super().__init__(
            "This submission changed. Review the latest version before trying again.",
            {"current_record_version": current_record_version},
        )


class PreflightBlockedError(DomainError):
    code = ErrorCode.PREFLIGHT_BLOCKED

    def __init__(self, findings: list[dict]) -> None:
        super().__init__(
            "Approval is blocked by demo policy findings.",
            {"findings": findings},
        )


class DatabaseBusyError(DomainError):
    code = ErrorCode.DATABASE_BUSY

    def __init__(self) -> None:
        super().__init__(
            "The database is busy. Retry the request.",
            {"retry": True},
        )


import json as _json
import sqlite3 as _sqlite3
import uuid as _uuid


def _load_user(conn: _sqlite3.Connection, actor_id: str) -> UserRecord | None:
    row = conn.execute(
        "SELECT id, role FROM users WHERE id = ?", (actor_id,)
    ).fetchone()
    if row is None:
        return None
    return UserRecord(id=row["id"], role=Role(row["role"]))


def _load_submission(
    conn: _sqlite3.Connection, submission_id: str
) -> tuple[SubmissionRecord, _sqlite3.Row] | None:
    row = conn.execute(
        "SELECT * FROM submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    if row is None:
        return None
    record = SubmissionRecord(
        id=row["id"],
        status=SubmissionStatus(row["status"]),
        submitter_id=row["submitter_id"],
        assigned_reviewer_id=row["assigned_reviewer_id"],
        current_version=row["current_version"],
        record_version=row["record_version"],
    )
    return record, row


def _write_event(
    conn: _sqlite3.Connection,
    submission_id: str,
    actor_id: str,
    event_type: str,
    *,
    from_status: SubmissionStatus | None = None,
    to_status: SubmissionStatus | None = None,
    version_number: int | None = None,
    comment: str | None = None,
    metadata: dict | None = None,
    now: datetime,
) -> None:
    conn.execute(
        "INSERT INTO audit_events (submission_id, actor_id, event_type, "
        "from_status, to_status, version_number, comment, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            submission_id,
            actor_id,
            event_type,
            from_status.value if from_status else None,
            to_status.value if to_status else None,
            version_number,
            comment,
            _json.dumps(metadata) if metadata else None,
            _iso(now),
        ),
    )


def _bump_record_version(
    conn: _sqlite3.Connection, submission_id: str, now: datetime
) -> int:
    """Increment record_version exactly once and return the new value."""
    cur = conn.execute(
        "UPDATE submissions SET record_version = record_version + 1, updated_at = ? "
        "WHERE id = ? RETURNING record_version",
        (_iso(now), submission_id),
    ).fetchone()
    return int(cur[0])


def _least_loaded_reviewer(conn: _sqlite3.Connection) -> str | None:
    """Least-loaded reviewer: fewest assigned UNDER_REVIEW submissions, then
    name ascending, then id. Returns None when no reviewers exist."""
    row = conn.execute(
        "SELECT u.id, u.name, COUNT(s.id) AS load "
        "FROM users u "
        "LEFT JOIN submissions s ON s.assigned_reviewer_id = u.id "
        "  AND s.status = 'UNDER_REVIEW' "
        "WHERE u.role = 'REVIEWER' "
        "GROUP BY u.id, u.name "
        "ORDER BY load ASC, u.name ASC, u.id ASC "
        "LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def create_submission(
    conn: _sqlite3.Connection,
    actor_id: str,
    *,
    title: str,
    copy_text: str,
    channel: Channel,
    product: Product,
    target_launch_date: str,
    asset_url: str | None = None,
    now: datetime,
) -> dict:
    """Create a new submission (submitter must be a SUBMITTER).

    Writes v1, least-loaded auto-assignment, SUBMITTED event, and AUTO_ASSIGNED
    event when a reviewer was assigned. Returns the new submission detail."""
    actor = _load_user(conn, actor_id)
    if actor is None:
        raise NotFoundError()
    if actor.role != Role.SUBMITTER:
        raise ForbiddenError("Only a submitter may create a submission")

    reviewer_id = _least_loaded_reviewer(conn)
    status = (
        SubmissionStatus.UNDER_REVIEW if reviewer_id
        else SubmissionStatus.PENDING_ASSIGNMENT
    )
    sid = str(_uuid.uuid4())
    conn.execute(
        "INSERT INTO submissions (id, external_id, title, partner, channel, product, "
        "status, assigned_reviewer_id, submitter_id, target_launch_date, "
        "submitted_at, decided_at, current_version, record_version, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, 1, ?, ?)",
        (
            sid, _next_external_id(conn, now), title, None, channel.value, product.value,
            status.value, reviewer_id, actor.id, target_launch_date,
            _iso(now), _iso(now), _iso(now),
        ),
    )
    # Content lives in submission_versions (append-only v1); the submissions
    # table carries only metadata and version counters.
    conn.execute(
        "INSERT INTO submission_versions (id, submission_id, version_number, "
        "asset_url, copy_text, created_by, created_at) "
        "VALUES (?, ?, 1, ?, ?, ?, ?)",
        (str(_uuid.uuid4()), sid, asset_url, copy_text, actor.id, _iso(now)),
    )
    _write_event(
        conn, sid, actor.id, "SUBMITTED",
        to_status=status, version_number=1, now=now,
    )
    if reviewer_id:
        _write_event(
            conn, sid, actor.id, "AUTO_ASSIGNED",
            from_status=SubmissionStatus.PENDING_ASSIGNMENT,
            to_status=SubmissionStatus.UNDER_REVIEW, version_number=1,
            metadata={"assignee_id": reviewer_id, "assignment_method": "automatic"},
            now=now,
        )
    return _detail(conn, sid)


def assign_submission(
    conn: _sqlite3.Connection,
    actor_id: str,
    submission_id: str,
    *,
    reviewer_id: str,
    comment: str,
    expected_record_version: int,
    now: datetime,
) -> dict:
    """Explicitly assign/reassign a submission to a reviewer.

    Any reviewer may assign; the record version increments exactly once."""
    actor = _load_user(conn, actor_id)
    if actor is None:
        raise NotFoundError()
    if actor.role != Role.REVIEWER:
        raise ForbiddenError("Only a reviewer may assign a submission")
    loaded = _load_submission(conn, submission_id)
    if loaded is None:
        raise NotFoundError()
    record, row = loaded
    if record.record_version != expected_record_version:
        raise VersionConflictError(record.record_version)
    target = _load_user(conn, reviewer_id)
    if target is None or target.role != Role.REVIEWER:
        raise ForbiddenError("Assignment target must be a reviewer")

    old_status = record.status
    if old_status == SubmissionStatus.PENDING_ASSIGNMENT:
        new_status = SubmissionStatus.UNDER_REVIEW
    else:
        new_status = old_status
    conn.execute(
        "UPDATE submissions SET assigned_reviewer_id = ?, status = ?, updated_at = ? "
        "WHERE id = ?",
        (reviewer_id, new_status.value, _iso(now), submission_id),
    )
    _bump_record_version(conn, submission_id, now)
    _write_event(
        conn, submission_id, actor.id,
        "ASSIGNED" if old_status == SubmissionStatus.PENDING_ASSIGNMENT else "REASSIGNED",
        from_status=old_status, to_status=new_status,
        comment=comment, now=now,
        metadata={
            "assignee_id": reviewer_id,
            "assignment_method": "manual",
            "previous_assignee_id": record.assigned_reviewer_id,
        },
    )
    return _detail(conn, submission_id)


def request_changes(
    conn: _sqlite3.Connection,
    actor_id: str,
    submission_id: str,
    *,
    feedback: str,
    expected_record_version: int,
    now: datetime,
) -> dict:
    """Assigned reviewer requests changes, moving the record to CHANGES_REQUESTED."""
    actor = _load_user(conn, actor_id)
    if actor is None:
        raise NotFoundError()
    if actor.role != Role.REVIEWER:
        raise ForbiddenError("Only a reviewer may request changes")
    loaded = _load_submission(conn, submission_id)
    if loaded is None:
        raise NotFoundError()
    record, row = loaded
    if record.status != SubmissionStatus.UNDER_REVIEW:
        raise InvalidTransitionError()
    if record.assigned_reviewer_id != actor.id:
        raise ForbiddenError("Only the assigned reviewer may request changes")
    if record.record_version != expected_record_version:
        raise VersionConflictError(record.record_version)

    conn.execute(
        "UPDATE submissions SET status = ?, updated_at = ? WHERE id = ?",
        (SubmissionStatus.CHANGES_REQUESTED.value, _iso(now), submission_id),
    )
    _bump_record_version(conn, submission_id, now)
    _write_event(
        conn, submission_id, actor.id, "CHANGES_REQUESTED",
        from_status=record.status, to_status=SubmissionStatus.CHANGES_REQUESTED,
        comment=feedback, now=now,
    )
    return _detail(conn, submission_id)


def resubmit_submission(
    conn: _sqlite3.Connection,
    actor_id: str,
    submission_id: str,
    *,
    copy_text: str,
    asset_url: str | None,
    expected_record_version: int,
    now: datetime,
) -> dict:
    """Owning submitter revises copy after CHANGES_REQUESTED.

    current_version increments (vN+1); status returns to UNDER_REVIEW and the
    assigned reviewer is kept. A reviewer may be auto-assigned if none existed."""
    actor = _load_user(conn, actor_id)
    if actor is None:
        raise NotFoundError()
    if actor.role != Role.SUBMITTER:
        raise ForbiddenError("Only a submitter may revise a submission")
    loaded = _load_submission(conn, submission_id)
    if loaded is None:
        raise NotFoundError()
    record, row = loaded
    if record.submitter_id != actor.id:
        raise ForbiddenError("Only the owning submitter may revise this submission")
    if record.status != SubmissionStatus.CHANGES_REQUESTED:
        raise InvalidTransitionError()
    if record.record_version != expected_record_version:
        raise VersionConflictError(record.record_version)

    next_version = record.current_version + 1
    assignee = record.assigned_reviewer_id or _least_loaded_reviewer(conn)
    # Content is append-only: write the new version row, then bump counters.
    conn.execute(
        "INSERT INTO submission_versions (id, submission_id, version_number, "
        "asset_url, copy_text, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(_uuid.uuid4()), submission_id, next_version, asset_url, copy_text,
         actor.id, _iso(now)),
    )
    conn.execute(
        "UPDATE submissions SET status = ?, assigned_reviewer_id = ?, "
        "current_version = ?, updated_at = ? WHERE id = ?",
        (SubmissionStatus.UNDER_REVIEW.value, assignee, next_version,
         _iso(now), submission_id),
    )
    _bump_record_version(conn, submission_id, now)
    _write_event(
        conn, submission_id, actor.id, "RESUBMITTED",
        from_status=SubmissionStatus.CHANGES_REQUESTED,
        to_status=SubmissionStatus.UNDER_REVIEW,
        version_number=next_version, comment=copy_text, now=now,
    )
    if assignee and record.assigned_reviewer_id is None:
        _write_event(
            conn, submission_id, actor.id, "AUTO_ASSIGNED",
            to_status=SubmissionStatus.UNDER_REVIEW, version_number=next_version,
            metadata={"assignee_id": assignee, "assignment_method": "automatic"},
            now=now,
        )
    return _detail(conn, submission_id)


def approve_submission(
    conn: _sqlite3.Connection,
    actor_id: str,
    submission_id: str,
    *,
    comment: str | None,
    expected_record_version: int,
    now: datetime,
) -> dict:
    """Assigned reviewer approves. Runs the K4 preflight gate inside the
    mutation: any blocking finding prevents approval (PREFLIGHT_BLOCKED) and
    nothing is written."""
    actor = _load_user(conn, actor_id)
    if actor is None:
        raise NotFoundError()
    if actor.role != Role.REVIEWER:
        raise ForbiddenError("Only a reviewer may approve")
    loaded = _load_submission(conn, submission_id)
    if loaded is None:
        raise NotFoundError()
    record, row = loaded
    if record.status != SubmissionStatus.UNDER_REVIEW:
        raise InvalidTransitionError()
    if record.assigned_reviewer_id != actor.id:
        raise ForbiddenError("Only the assigned reviewer may approve")
    if record.record_version != expected_record_version:
        raise VersionConflictError(record.record_version)

    channel = Channel(row["channel"])
    product = Product(row["product"])
    version_row = _current_version(conn, submission_id, record.current_version)
    if version_row is None:
        raise InvalidTransitionError()
    preflight = run_preflight(product, channel, version_row["copy_text"])
    if not preflight["passed"]:
        raise PreflightBlockedError(preflight["findings"])

    conn.execute(
        "UPDATE submissions SET status = ?, decided_at = ?, updated_at = ? WHERE id = ?",
        (SubmissionStatus.APPROVED.value, _iso(now), _iso(now), submission_id),
    )
    _bump_record_version(conn, submission_id, now)
    _write_event(
        conn, submission_id, actor.id, "APPROVED",
        from_status=record.status, to_status=SubmissionStatus.APPROVED,
        comment=comment, now=now,
        metadata={"policy_version": POLICY_VERSION},
    )
    return _detail(conn, submission_id)


def reject_submission(
    conn: _sqlite3.Connection,
    actor_id: str,
    submission_id: str,
    *,
    reason: str,
    expected_record_version: int,
    now: datetime,
) -> dict:
    """Assigned reviewer rejects, moving the record to REJECTED (terminal)."""
    actor = _load_user(conn, actor_id)
    if actor is None:
        raise NotFoundError()
    if actor.role != Role.REVIEWER:
        raise ForbiddenError("Only a reviewer may reject")
    loaded = _load_submission(conn, submission_id)
    if loaded is None:
        raise NotFoundError()
    record, row = loaded
    if record.status != SubmissionStatus.UNDER_REVIEW:
        raise InvalidTransitionError()
    if record.assigned_reviewer_id != actor.id:
        raise ForbiddenError("Only the assigned reviewer may reject")
    if record.record_version != expected_record_version:
        raise VersionConflictError(record.record_version)

    conn.execute(
        "UPDATE submissions SET status = ?, decided_at = ?, updated_at = ? WHERE id = ?",
        (SubmissionStatus.REJECTED.value, _iso(now), _iso(now), submission_id),
    )
    _bump_record_version(conn, submission_id, now)
    _write_event(
        conn, submission_id, actor.id, "REJECTED",
        from_status=record.status, to_status=SubmissionStatus.REJECTED,
        comment=reason, now=now,
    )
    return _detail(conn, submission_id)


def _next_external_id(conn: _sqlite3.Connection, now: datetime) -> str:
    """Transactional external-id generator: starts at CP-8909 (baseline) and
    derives the next numeric suffix while holding the write transaction."""
    row = conn.execute(
        "SELECT external_id FROM submissions ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return f"CP-{8909:04d}"
    suffix = int(row["external_id"].split("-")[1])
    return f"CP-{suffix + 1:04d}"


def _detail(conn: _sqlite3.Connection, submission_id: str) -> dict:
    """Read-only projection of a submission for the caller.

    Copy/asset URL live in submission_versions (append-only); the current
    version is the one referenced by submissions.current_version.
    """
    row = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    version_row = _current_version(conn, submission_id, row["current_version"])
    return {
        "id": row["id"],
        "external_id": row["external_id"],
        "title": row["title"],
        "channel": row["channel"],
        "product": row["product"],
        "status": row["status"],
        "assigned_reviewer_id": row["assigned_reviewer_id"],
        "submitter_id": row["submitter_id"],
        "target_launch_date": row["target_launch_date"],
        "asset_url": version_row["asset_url"] if version_row else None,
        "copy_text": version_row["copy_text"] if version_row else None,
        "submitted_at": row["submitted_at"],
        "decided_at": row["decided_at"],
        "current_version": row["current_version"],
        "record_version": row["record_version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _current_version(
    conn: _sqlite3.Connection, submission_id: str, version_number: int
) -> _sqlite3.Row | None:
    """Fetch the content row for a specific content version."""
    return conn.execute(
        "SELECT * FROM submission_versions WHERE submission_id = ? AND version_number = ?",
        (submission_id, version_number),
    ).fetchone()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
