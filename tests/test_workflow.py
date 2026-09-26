"""Table-driven tests for the K2 domain rules: transition matrix (§5),
visibility, permission, content conditions, and request validation (§9).

The acceptance criteria are exercised explicitly:
- every action x source status x actor role,
- nonassigned reviewer (permission),
- foreign submitter (visibility),
- terminal reassignment (transition guard),
- empty rejection reason (content validation),
- immutable metadata / no extra states or role shortcuts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clearpath.models import (
    ApproveRequest,
    AssignRequest,
    Channel,
    ErrorCode,
    IntakeRequest,
    Product,
    RejectRequest,
    RequestChangesRequest,
    ResubmitRequest,
    Role,
    SubmissionStatus,
)
from clearpath.workflow import (
    Action,
    ForbiddenError,
    InvalidTransitionError,
    NotFoundError,
    SubmissionRecord,
    UserRecord,
    ValidationError,
    check_permission,
    check_transition,
    check_visibility,
    validate_action,
)

# --- Fixtures -----------------------------------------------------------------

R1 = "reviewer-1"
R2 = "reviewer-2"
S1 = "submitter-1"
S2 = "submitter-2"

ASSIGNED_REVIEWER = UserRecord(R1, Role.REVIEWER)
OTHER_REVIEWER = UserRecord(R2, Role.REVIEWER)
OWNER = UserRecord(S1, Role.SUBMITTER)
FOREIGN = UserRecord(S2, Role.SUBMITTER)


def sub(status: SubmissionStatus, *, assigned: str | None = R1) -> SubmissionRecord:
    return SubmissionRecord(
        id=f"sub-{status.value}",
        status=status,
        submitter_id=S1,
        assigned_reviewer_id=assigned,
        current_version=1,
    )


OPEN = [
    SubmissionStatus.PENDING_ASSIGNMENT,
    SubmissionStatus.UNDER_REVIEW,
    SubmissionStatus.CHANGES_REQUESTED,
]
TERMINAL = [SubmissionStatus.APPROVED, SubmissionStatus.REJECTED]
ALL = OPEN + TERMINAL


def feedback_for(action: Action) -> dict:
    """Per-action content arguments needed for a valid transition."""
    if action == Action.REQUEST_CHANGES:
        return {"feedback": "please revise the disclosure"}
    if action == Action.REJECT:
        return {"feedback": "violates lending policy"}
    if action == Action.RESUBMIT:
        return {"copy_text": "new copy", "prior_copy": "old copy"}
    if action == Action.ASSIGN:
        return {"target_reviewer_id": R2, "feedback": "reassigning workload"}
    return {}


# --- Transition matrix: every action x every source status ----------------------

# Expected outcomes per (action, source):
#   "valid"      -> transition succeeds, destination asserted,
#   "invalid"    -> InvalidTransitionError raised.
MATRIX: dict[Action, dict[SubmissionStatus, str]] = {
    Action.ASSIGN: {
        SubmissionStatus.PENDING_ASSIGNMENT: "valid",
        SubmissionStatus.UNDER_REVIEW: "valid",
        SubmissionStatus.CHANGES_REQUESTED: "valid",
        SubmissionStatus.APPROVED: "invalid",
        SubmissionStatus.REJECTED: "invalid",
    },
    Action.REQUEST_CHANGES: {
        SubmissionStatus.UNDER_REVIEW: "valid",
        SubmissionStatus.PENDING_ASSIGNMENT: "invalid",
        SubmissionStatus.CHANGES_REQUESTED: "invalid",
        SubmissionStatus.APPROVED: "invalid",
        SubmissionStatus.REJECTED: "invalid",
    },
    Action.RESUBMIT: {
        SubmissionStatus.CHANGES_REQUESTED: "valid",
        SubmissionStatus.PENDING_ASSIGNMENT: "invalid",
        SubmissionStatus.UNDER_REVIEW: "invalid",
        SubmissionStatus.APPROVED: "invalid",
        SubmissionStatus.REJECTED: "invalid",
    },
    Action.APPROVE: {
        SubmissionStatus.UNDER_REVIEW: "valid",
        SubmissionStatus.PENDING_ASSIGNMENT: "invalid",
        SubmissionStatus.CHANGES_REQUESTED: "invalid",
        SubmissionStatus.APPROVED: "invalid",
        SubmissionStatus.REJECTED: "invalid",
    },
    Action.REJECT: {
        SubmissionStatus.UNDER_REVIEW: "valid",
        SubmissionStatus.PENDING_ASSIGNMENT: "invalid",
        SubmissionStatus.CHANGES_REQUESTED: "invalid",
        SubmissionStatus.APPROVED: "invalid",
        SubmissionStatus.REJECTED: "invalid",
    },
}

# Expected destination status per valid (action, source).
DEST: dict[Action, dict[SubmissionStatus, SubmissionStatus]] = {
    Action.ASSIGN: {
        SubmissionStatus.PENDING_ASSIGNMENT: SubmissionStatus.UNDER_REVIEW,
        SubmissionStatus.UNDER_REVIEW: SubmissionStatus.UNDER_REVIEW,
        SubmissionStatus.CHANGES_REQUESTED: SubmissionStatus.CHANGES_REQUESTED,
    },
    Action.REQUEST_CHANGES: {
        SubmissionStatus.UNDER_REVIEW: SubmissionStatus.CHANGES_REQUESTED,
    },
    Action.RESUBMIT: {
        SubmissionStatus.CHANGES_REQUESTED: SubmissionStatus.UNDER_REVIEW,
    },
    Action.APPROVE: {
        SubmissionStatus.UNDER_REVIEW: SubmissionStatus.APPROVED,
    },
    Action.REJECT: {
        SubmissionStatus.UNDER_REVIEW: SubmissionStatus.REJECTED,
    },
}


@pytest.mark.parametrize("action", [a for a in Action if a != Action.CREATE])
def test_transition_matrix(action: Action) -> None:
    """Every action is valid exactly on the §5 source statuses."""
    for source in ALL:
        record = sub(source)
        args = feedback_for(action)
        if MATRIX[action][source] == "valid":
            dest = check_transition(action, record, **args)
            assert dest == DEST[action][source]
        else:
            with pytest.raises(InvalidTransitionError):
                check_transition(action, record, **args)


def test_create_destination_depends_on_reviewer_availability() -> None:
    record = sub(SubmissionStatus.UNDER_REVIEW)
    assert (
        check_transition(Action.CREATE, record, reviewer_available=True)
        == SubmissionStatus.UNDER_REVIEW
    )
    assert (
        check_transition(Action.CREATE, record, reviewer_available=False)
        == SubmissionStatus.PENDING_ASSIGNMENT
    )


def test_resubmit_destination_depends_on_reviewer_availability() -> None:
    record = sub(SubmissionStatus.CHANGES_REQUESTED)
    args = feedback_for(Action.RESUBMIT)
    assert (
        check_transition(Action.RESUBMIT, record, reviewer_available=True, **args)
        == SubmissionStatus.UNDER_REVIEW
    )
    assert (
        check_transition(Action.RESUBMIT, record, reviewer_available=False, **args)
        == SubmissionStatus.PENDING_ASSIGNMENT
    )


# --- Permission: every action x every actor ------------------------------------

PERMISSIONS: dict[Action, tuple[UserRecord, ...]] = {
    Action.CREATE: (OWNER, FOREIGN),  # any submitter (new record)
    Action.RESUBMIT: (OWNER,),  # only the owning submitter
    Action.ASSIGN: (ASSIGNED_REVIEWER, OTHER_REVIEWER),  # any reviewer
    Action.REQUEST_CHANGES: (ASSIGNED_REVIEWER,),  # assigned reviewer only
    Action.APPROVE: (ASSIGNED_REVIEWER,),  # assigned reviewer only
    Action.REJECT: (ASSIGNED_REVIEWER,),  # assigned reviewer only
}


@pytest.mark.parametrize(
    "action",
    [
        Action.CREATE,
        Action.ASSIGN,
        Action.REQUEST_CHANGES,
        Action.RESUBMIT,
        Action.APPROVE,
        Action.REJECT,
    ],
)
@pytest.mark.parametrize(
    "actor",
    [
        UserRecord(R1, Role.REVIEWER),
        UserRecord(R2, Role.REVIEWER),
        UserRecord(S1, Role.SUBMITTER),
        UserRecord(S2, Role.SUBMITTER),
    ],
)
def test_permission_matrix(action: Action, actor: UserRecord) -> None:
    """Only the §5-permitted actors may attempt each action."""
    allowed = PERMISSIONS[action]
    record = sub(SubmissionStatus.UNDER_REVIEW)
    if actor in allowed:
        check_permission(action, actor, record)  # must not raise
    else:
        with pytest.raises(ForbiddenError):
            check_permission(action, actor, record)


def test_nonassigned_reviewer_denied() -> None:
    """The assigned-reviewer-only actions reject an unassigned reviewer."""
    record = sub(SubmissionStatus.UNDER_REVIEW, assigned=R1)
    with pytest.raises(ForbiddenError):
        check_permission(Action.APPROVE, OTHER_REVIEWER, record)
    with pytest.raises(ForbiddenError):
        check_permission(Action.REQUEST_CHANGES, OTHER_REVIEWER, record)
    with pytest.raises(ForbiddenError):
        check_permission(Action.REJECT, OTHER_REVIEWER, record)


def test_foreign_submitter_denied() -> None:
    """Visibility: a submitter cannot act on another submitter's record."""
    record = sub(SubmissionStatus.CHANGES_REQUESTED)
    with pytest.raises(ForbiddenError):
        check_permission(Action.RESUBMIT, FOREIGN, record)
    with pytest.raises(NotFoundError):
        check_visibility(FOREIGN, record)


def test_reviewer_sees_all_and_submitter_sees_own() -> None:
    record = sub(SubmissionStatus.UNDER_REVIEW)
    check_visibility(ASSIGNED_REVIEWER, record)
    check_visibility(OTHER_REVIEWER, record)
    check_visibility(OWNER, record)
    with pytest.raises(NotFoundError):
        check_visibility(FOREIGN, record)


# --- Terminal protection -------------------------------------------------------


@pytest.mark.parametrize("terminal", TERMINAL)
@pytest.mark.parametrize(
    "action",
    [
        Action.ASSIGN,
        Action.RESUBMIT,
        Action.APPROVE,
        Action.REJECT,
        Action.REQUEST_CHANGES,
    ],
)
def test_terminal_records_reject_all_mutations(
    terminal: SubmissionStatus, action: Action
) -> None:
    record = sub(terminal)
    args = feedback_for(action)
    with pytest.raises(InvalidTransitionError):
        check_transition(action, record, **args)


def test_terminal_reassignment_rejected() -> None:
    """Terminal reassignment is explicitly guarded."""
    for terminal in TERMINAL:
        record = sub(terminal)
        with pytest.raises(InvalidTransitionError):
            check_transition(Action.ASSIGN, record, target_reviewer_id=R2)


# --- Content conditions (§5) ----------------------------------------------------


def test_request_changes_requires_nonblank_feedback() -> None:
    record = sub(SubmissionStatus.UNDER_REVIEW)
    with pytest.raises(ValidationError):
        check_transition(Action.REQUEST_CHANGES, record, feedback="   ")
    # blank (None) also rejected
    with pytest.raises(ValidationError):
        check_transition(Action.REQUEST_CHANGES, record, feedback=None)


def test_reject_requires_nonblank_reason() -> None:
    """Empty rejection reason is rejected (acceptance criterion)."""
    record = sub(SubmissionStatus.UNDER_REVIEW)
    with pytest.raises(ValidationError):
        check_transition(Action.REJECT, record, feedback=None)
    with pytest.raises(ValidationError):
        check_transition(Action.REJECT, record, feedback="   ")


def test_reassign_requires_reason_when_replacing_reviewer() -> None:
    record = sub(SubmissionStatus.UNDER_REVIEW, assigned=R1)
    # Replacing an existing reviewer without a reason is a validation error.
    with pytest.raises(ValidationError):
        check_transition(Action.ASSIGN, record, target_reviewer_id=R2)
    # With a reason it succeeds.
    dest = check_transition(
        Action.ASSIGN, record, target_reviewer_id=R2, feedback="rebalance queue"
    )
    assert dest == SubmissionStatus.UNDER_REVIEW


def test_assign_to_same_reviewer_is_invalid_transition() -> None:
    record = sub(SubmissionStatus.UNDER_REVIEW, assigned=R1)
    with pytest.raises(InvalidTransitionError):
        check_transition(Action.ASSIGN, record, target_reviewer_id=R1)


def test_resubmit_requires_content_change() -> None:
    """A no-op resubmit (same copy and asset) is invalid."""
    record = sub(SubmissionStatus.CHANGES_REQUESTED)
    with pytest.raises(InvalidTransitionError):
        check_transition(
            Action.RESUBMIT,
            record,
            copy_text="same",
            prior_copy="same",
            asset_url="https://example.com/a",
            prior_asset_url="https://example.com/a",
        )


def test_resubmit_requires_copy_or_asset_url() -> None:
    record = sub(SubmissionStatus.CHANGES_REQUESTED)
    with pytest.raises(ValidationError):
        check_transition(Action.RESUBMIT, record, copy_text=None, asset_url=None)


# --- Combined validate_action ---------------------------------------------------


def test_validate_action_approve_success() -> None:
    record = sub(SubmissionStatus.UNDER_REVIEW)
    assert (
        validate_action(Action.APPROVE, ASSIGNED_REVIEWER, record)
        == SubmissionStatus.APPROVED
    )


def test_validate_action_approve_wrong_reviewer() -> None:
    record = sub(SubmissionStatus.UNDER_REVIEW)
    with pytest.raises(ForbiddenError):
        validate_action(Action.APPROVE, OTHER_REVIEWER, record)


def test_validate_action_resubmit_foreign_submitter() -> None:
    record = sub(SubmissionStatus.CHANGES_REQUESTED)
    # Visibility is checked first: a foreign submitter cannot see the record.
    with pytest.raises(NotFoundError):
        validate_action(
            Action.RESUBMIT,
            FOREIGN,
            record,
            copy_text="new",
            prior_copy="old",
        )


def test_validate_action_assign_opens_pending() -> None:
    record = sub(SubmissionStatus.PENDING_ASSIGNMENT)
    dest = validate_action(
        Action.ASSIGN,
        ASSIGNED_REVIEWER,
        record,
        target_reviewer_id=R2,
        feedback="assign",
    )
    assert dest == SubmissionStatus.UNDER_REVIEW


# --- No extra states / no role shortcuts (immutable vocabulary) ------------------


def test_no_extra_enum_states() -> None:
    """The §4 status vocabulary is fixed; no extra states are introduced."""
    assert set(SubmissionStatus) == {
        SubmissionStatus.PENDING_ASSIGNMENT,
        SubmissionStatus.UNDER_REVIEW,
        SubmissionStatus.CHANGES_REQUESTED,
        SubmissionStatus.APPROVED,
        SubmissionStatus.REJECTED,
    }
    assert set(Role) == {Role.SUBMITTER, Role.REVIEWER}


def test_audit_event_vocabulary() -> None:
    from clearpath.models import AuditEventType

    assert set(AuditEventType) == {
        AuditEventType.SUBMITTED,
        AuditEventType.AUTO_ASSIGNED,
        AuditEventType.ASSIGNED,
        AuditEventType.REASSIGNED,
        AuditEventType.CHANGES_REQUESTED,
        AuditEventType.RESUBMITTED,
        AuditEventType.APPROVED,
        AuditEventType.REJECTED,
    }


# --- Request validation (§9) ------------------------------------------------------

LAUNCH_FUTURE = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
LAUNCH_PAST = "2020-01-01"


def intake(**kw):
    base = {
        "title": "ClearRewards Launch",
        "channel": Channel.SOCIAL,
        "product": Product.CREDIT_CARD,
        "target_launch_date": LAUNCH_FUTURE,
        "copy_text": "Explore rewards today.",
    }
    base.update(kw)
    return IntakeRequest(**base)


def test_intake_valid() -> None:
    r = intake()
    assert r.channel == Channel.SOCIAL
    assert r.product == Product.CREDIT_CARD
    assert r.target_launch_date == LAUNCH_FUTURE
    # empty optional strings normalize to null
    assert intake(partner="   ").partner is None
    assert intake(asset_url="   ").asset_url is None


@pytest.mark.parametrize("title", ["", "ab", "x" * 121])
def test_intake_title_length(title: str) -> None:
    with pytest.raises(Exception):
        intake(title=title)


@pytest.mark.parametrize("copy_text", ["", "x" * 20_001])
def test_intake_copy_length(copy_text: str) -> None:
    with pytest.raises(Exception):
        intake(copy_text=copy_text)


def test_affiliate_channel_requires_partner() -> None:
    with pytest.raises(Exception):
        intake(channel=Channel.AFFILIATE, partner=None)


def test_affiliate_partner_length() -> None:
    with pytest.raises(Exception):
        intake(channel=Channel.AFFILIATE, partner="p" * 121)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",  # disallowed scheme
        "https://user:pw@example.com/x",  # credentials
        "not a url",  # no scheme
    ],
)
def test_intake_rejects_bad_asset_url(url: str) -> None:
    with pytest.raises(Exception):
        intake(asset_url=url)


def test_intake_asset_url_too_long() -> None:
    with pytest.raises(Exception):
        intake(asset_url="https://example.com/" + "a" * 2_048)


def test_intake_rejects_past_launch_date() -> None:
    with pytest.raises(Exception):
        intake(target_launch_date=LAUNCH_PAST)


def test_intake_rejects_bad_launch_date() -> None:
    with pytest.raises(Exception):
        intake(target_launch_date="not-a-date")


def test_intake_forbids_unknown_fields() -> None:
    with pytest.raises(Exception):
        IntakeRequest(
            **{
                "title": "x",
                "channel": Channel.EMAIL,
                "product": Product.CREDIT_CARD,
                "target_launch_date": LAUNCH_FUTURE,
                "copy_text": "abc",
                "extra": 1,
            }
        )


def test_reject_requires_nonblank_comment() -> None:
    with pytest.raises(Exception):
        RejectRequest(expected_record_version=1, comment="   ")


def test_reject_comment_too_long() -> None:
    with pytest.raises(Exception):
        RejectRequest(expected_record_version=1, comment="x" * 2_001)


def test_request_changes_requires_comment() -> None:
    with pytest.raises(Exception):
        RequestChangesRequest(expected_record_version=1, comment="")


def test_assign_comment_optional() -> None:
    a = AssignRequest(expected_record_version=1, reviewer_id=R2, comment="   ")
    assert a.comment is None


def test_resubmit_url_validation() -> None:
    with pytest.raises(Exception):
        ResubmitRequest(
            expected_record_version=1,
            copy_text="new",
            asset_url="https://user:pw@example.com",
        )
    ok = ResubmitRequest(
        expected_record_version=1, copy_text="new", asset_url="https://example.com/a"
    )
    assert ok.asset_url == "https://example.com/a"


def test_expected_record_version_ge_1() -> None:
    with pytest.raises(Exception):
        ApproveRequest(expected_record_version=0, comment="ok")


# --- Error code vocabulary --------------------------------------------------------


def test_error_codes_canonical() -> None:
    """Domain errors carry the §9 canonical codes."""
    assert NotFoundError.code == ErrorCode.NOT_FOUND
    assert ForbiddenError.code == ErrorCode.FORBIDDEN
    assert InvalidTransitionError.code == ErrorCode.INVALID_TRANSITION
    assert ValidationError.code == ErrorCode.VALIDATION_ERROR
