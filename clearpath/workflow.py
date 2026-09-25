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
