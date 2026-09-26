"""Workflow rules and atomic services for the marketing review lifecycle.

Pure permission/transition checks are shared by transactional services. Every
write locks before reading, appends audit events, and commits or rolls back as
a unit. Domain errors carry machine-readable codes for the HTTP layer.
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
    record_version: int = 1

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


def _dest_assign(
    source: SubmissionStatus, *, reviewer_available: bool
) -> SubmissionStatus:
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
        (
            SubmissionStatus.PENDING_ASSIGNMENT,
            SubmissionStatus.UNDER_REVIEW,
            SubmissionStatus.CHANGES_REQUESTED,
        ),
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


def check_permission(
    action: Action, actor: UserRecord, submission: SubmissionRecord
) -> None:
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
            {
                "action": action.value,
                "assigned_reviewer_id": submission.assigned_reviewer_id,
            },
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
        return _dest_create(
            SubmissionStatus.UNDER_REVIEW, reviewer_available=reviewer_available
        )

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
        action,
        submission,
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
        if copy_text == prior_copy and asset_url == prior_asset_url:
            details = {"action": action.value, "reason": "no content changed"}
            raise InvalidTransitionError(
                "Resubmission must change copy or asset URL", details
            )

    if action == Action.ASSIGN:
        # Assignment to the already-assigned reviewer is a no-op → invalid.
        if (
            target_reviewer_id is not None
            and submission.assigned_reviewer_id == target_reviewer_id
        ):
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
        action,
        submission,
        reviewer_available=reviewer_available,
        target_reviewer_id=target_reviewer_id,
        feedback=feedback,
        copy_text=copy_text,
        asset_url=asset_url,
        prior_copy=prior_copy,
        prior_asset_url=prior_asset_url,
    )


__all__ = [
    "Action",
    "UserRecord",
    "SubmissionRecord",
    "DomainError",
    "NotFoundError",
    "ForbiddenError",
    "InvalidTransitionError",
    "ValidationError",
    "check_visibility",
    "check_permission",
    "check_transition",
    "permitted_actions",
    "validate_action",
]


# Transactional services: lock before reading ownership, versions, or reviewer load.
from datetime import datetime, timedelta
from functools import wraps
import json
import sqlite3
import uuid
from clearpath.models import Channel, Product
from clearpath.preflight import POLICY_VERSION, run_preflight


class VersionConflictError(DomainError):
    code = ErrorCode.VERSION_CONFLICT

    def __init__(self, current_record_version):
        super().__init__(
            "This submission changed. Reload and review the latest version before trying again.",
            {"current_record_version": current_record_version},
        )


class PreflightBlockedError(DomainError):
    code = ErrorCode.PREFLIGHT_BLOCKED

    def __init__(self, findings):
        super().__init__(
            "Approval is blocked by demo policy findings.", {"findings": findings}
        )


class DatabaseBusyError(DomainError):
    code = ErrorCode.DATABASE_BUSY

    def __init__(self):
        super().__init__("The database is busy. Please retry.", {"retry": True})


def transaction(fn):
    @wraps(fn)
    def wrapped(conn, *args, **kwargs):
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = fn(conn, *args, **kwargs)
            conn.commit()
            return result
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DatabaseBusyError() from exc
            raise
        except Exception:
            conn.rollback()
            raise

    return wrapped


def _iso(now):
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _user(conn, actor_id):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (actor_id,)).fetchone()
    if row is None:
        raise NotFoundError()
    return UserRecord(row["id"], Role(row["role"]))


def _least_loaded_reviewer(conn):
    row = conn.execute("""SELECT u.id, COUNT(s.id) AS load FROM users u
        LEFT JOIN submissions s ON s.assigned_reviewer_id = u.id AND s.status = 'UNDER_REVIEW'
        WHERE u.role = 'REVIEWER' GROUP BY u.id ORDER BY load, u.name, u.id LIMIT 1""").fetchone()
    return row["id"] if row else None


def _detail(conn, sid):
    row = conn.execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone()
    if row is None:
        raise NotFoundError()
    result = dict(row)
    version = conn.execute(
        "SELECT copy_text, asset_url FROM submission_versions WHERE submission_id = ? AND version_number = ?",
        (sid, row["current_version"]),
    ).fetchone()
    result.update(dict(version))
    return result


def _event(
    conn,
    sid,
    actor,
    event,
    now,
    *,
    source=None,
    dest=None,
    version=1,
    comment=None,
    metadata=None,
):
    from clearpath.notifications import fanout_event

    cur = conn.execute(
        """INSERT INTO audit_events
        (submission_id, actor_id, event_type, from_status, to_status, version_number, comment, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            sid,
            actor,
            event,
            source,
            dest,
            version,
            comment,
            json.dumps(metadata) if metadata else None,
            _iso(now),
        ),
    )
    fanout_event(
        conn,
        audit_event_id=cur.lastrowid,
        submission_id=sid,
        actor_id=actor,
        event_type=event,
        created_at=_iso(now),
        metadata=metadata,
    )


def _version(conn, sid, number, actor, copy_text, asset_url, now):
    conn.execute(
        """INSERT INTO submission_versions
        (id, submission_id, version_number, asset_url, copy_text, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), sid, number, asset_url, copy_text, actor, _iso(now)),
    )


@transaction
def create_submission(
    conn,
    actor_id,
    *,
    title,
    copy_text,
    channel,
    product,
    target_launch_date,
    asset_url=None,
    partner=None,
    now,
):
    actor = _user(conn, actor_id)
    if actor.role != Role.SUBMITTER:
        raise ForbiddenError("Only a submitter may create a submission")
    reviewer = _least_loaded_reviewer(conn)
    status = (
        SubmissionStatus.UNDER_REVIEW
        if reviewer
        else SubmissionStatus.PENDING_ASSIGNMENT
    )
    sid = str(uuid.uuid4())
    number = conn.execute(
        "SELECT MAX(CAST(SUBSTR(external_id, 4) AS INTEGER)) FROM submissions"
    ).fetchone()[0]
    external_id = f"CP-{max(number or 8908, 8908) + 1}"
    conn.execute(
        """INSERT INTO submissions
        (id, external_id, title, partner, channel, product, status, assigned_reviewer_id, submitter_id,
         target_launch_date, submitted_at, sla_breach_at, current_version, record_version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)""",
        (
            sid,
            external_id,
            title,
            partner,
            channel,
            product,
            status,
            reviewer,
            actor.id,
            target_launch_date,
            _iso(now),
            _iso(now + timedelta(hours=72)),
            _iso(now),
            _iso(now),
        ),
    )
    _version(conn, sid, 1, actor.id, copy_text, asset_url, now)
    _event(conn, sid, actor.id, "SUBMITTED", now, dest=status)
    if reviewer:
        _event(
            conn,
            sid,
            actor.id,
            "AUTO_ASSIGNED",
            now,
            dest=status,
            metadata={"assignee_id": reviewer, "assignment_method": "automatic"},
        )
    return _detail(conn, sid)


def _mutate(
    conn,
    actor_id,
    sid,
    action,
    expected_record_version,
    now,
    *,
    comment=None,
    reviewer_id=None,
    copy_text=None,
    asset_url=None,
):
    actor = _user(conn, actor_id)
    row = _detail(conn, sid)
    record = SubmissionRecord(
        id=sid,
        status=SubmissionStatus(row["status"]),
        submitter_id=row["submitter_id"],
        assigned_reviewer_id=row["assigned_reviewer_id"],
        current_version=row["current_version"],
        record_version=row["record_version"],
    )
    check_visibility(actor, record)
    check_permission(action, actor, record)
    if record.record_version != expected_record_version:
        raise VersionConflictError(record.record_version)
    assignee = record.assigned_reviewer_id
    if action == Action.ASSIGN:
        if _user(conn, reviewer_id).role != Role.REVIEWER:
            raise ValidationError("Assignment target must be a reviewer")
        assignee = reviewer_id
    if action == Action.RESUBMIT:
        assignee = assignee or _least_loaded_reviewer(conn)
    status = check_transition(
        action,
        record,
        reviewer_available=bool(assignee),
        target_reviewer_id=reviewer_id,
        feedback=comment,
        copy_text=copy_text,
        asset_url=asset_url,
        prior_copy=row["copy_text"],
        prior_asset_url=row["asset_url"],
    )
    if action == Action.APPROVE:
        preflight = run_preflight(
            Product(row["product"]), Channel(row["channel"]), row["copy_text"]
        )
        if not preflight["passed"]:
            raise PreflightBlockedError(preflight["findings"])
    version = record.current_version + (action == Action.RESUBMIT)
    if action == Action.RESUBMIT:
        _version(conn, sid, version, actor.id, copy_text, asset_url, now)
    event = {
        Action.ASSIGN: "REASSIGNED" if record.assigned_reviewer_id else "ASSIGNED",
        Action.RESUBMIT: "RESUBMITTED",
        Action.APPROVE: "APPROVED",
        Action.REJECT: "REJECTED",
        Action.REQUEST_CHANGES: "CHANGES_REQUESTED",
    }[action]
    metadata = None
    if action == Action.ASSIGN:
        metadata = {
            "assignee_id": assignee,
            "previous_assignee_id": record.assigned_reviewer_id,
            "assignment_method": "manual",
        }
    if action in (Action.APPROVE, Action.REJECT, Action.REQUEST_CHANGES):
        observed = run_preflight(
            Product(row["product"]), Channel(row["channel"]), row["copy_text"]
        )
        metadata = {
            "policy_version": POLICY_VERSION,
            "finding_ids": sorted({f["rule_id"] for f in observed["findings"]}),
        }
    decided = _iso(now) if action in (Action.APPROVE, Action.REJECT) else None
    conn.execute(
        """UPDATE submissions SET status = ?, assigned_reviewer_id = ?, current_version = ?,
        record_version = record_version + 1, decided_at = ?, updated_at = ? WHERE id = ?""",
        (status, assignee, version, decided, _iso(now), sid),
    )
    _event(
        conn,
        sid,
        actor.id,
        event,
        now,
        source=record.status,
        dest=status,
        version=version,
        comment=comment,
        metadata=metadata,
    )
    if action == Action.RESUBMIT and assignee and not record.assigned_reviewer_id:
        _event(
            conn,
            sid,
            actor.id,
            "AUTO_ASSIGNED",
            now,
            dest=status,
            version=version,
            metadata={"assignee_id": assignee, "assignment_method": "automatic"},
        )
    return _detail(conn, sid)


@transaction
def assign_submission(
    conn,
    actor_id,
    submission_id,
    *,
    reviewer_id,
    comment=None,
    expected_record_version,
    now,
):
    return _mutate(
        conn,
        actor_id,
        submission_id,
        Action.ASSIGN,
        expected_record_version,
        now,
        reviewer_id=reviewer_id,
        comment=comment,
    )


@transaction
def request_changes(
    conn, actor_id, submission_id, *, feedback, expected_record_version, now
):
    return _mutate(
        conn,
        actor_id,
        submission_id,
        Action.REQUEST_CHANGES,
        expected_record_version,
        now,
        comment=feedback,
    )


@transaction
def resubmit_submission(
    conn,
    actor_id,
    submission_id,
    *,
    copy_text,
    asset_url=None,
    expected_record_version,
    now,
):
    return _mutate(
        conn,
        actor_id,
        submission_id,
        Action.RESUBMIT,
        expected_record_version,
        now,
        copy_text=copy_text,
        asset_url=asset_url,
    )


@transaction
def approve_submission(
    conn, actor_id, submission_id, *, comment=None, expected_record_version, now
):
    return _mutate(
        conn,
        actor_id,
        submission_id,
        Action.APPROVE,
        expected_record_version,
        now,
        comment=comment,
    )


@transaction
def reject_submission(
    conn, actor_id, submission_id, *, reason, expected_record_version, now
):
    return _mutate(
        conn,
        actor_id,
        submission_id,
        Action.REJECT,
        expected_record_version,
        now,
        comment=reason,
    )
