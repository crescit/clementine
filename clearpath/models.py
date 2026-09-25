"""Shared domain models: enums, error codes, and request validation.

K2: enums + request validation (§4-6, §9) without route-specific logic.
Response/DTO models for the API (QueueItem, Detail, ...) land with K5-K6.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --- Enumerations (domain vocabulary from §4) ---------------------------------

class Role(StrEnum):
    SUBMITTER = "SUBMITTER"
    REVIEWER = "REVIEWER"


class Product(StrEnum):
    PERSONAL_LOAN = "PERSONAL_LOAN"
    CREDIT_CARD = "CREDIT_CARD"
    MORTGAGE_PREQUALIFICATION = "MORTGAGE_PREQUALIFICATION"


class Channel(StrEnum):
    AFFILIATE = "AFFILIATE"
    EMAIL = "EMAIL"
    SOCIAL = "SOCIAL"
    PAID_SEARCH = "PAID_SEARCH"
    WEBSITE = "WEBSITE"


class SubmissionStatus(StrEnum):
    PENDING_ASSIGNMENT = "PENDING_ASSIGNMENT"
    UNDER_REVIEW = "UNDER_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AuditEventType(StrEnum):
    SUBMITTED = "SUBMITTED"
    AUTO_ASSIGNED = "AUTO_ASSIGNED"
    ASSIGNED = "ASSIGNED"
    REASSIGNED = "REASSIGNED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    RESUBMITTED = "RESUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Severity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


# --- Canonical domain error codes (§9) ---------------------------------------

class ErrorCode:
    """Canonical machine-readable error codes from the API contract."""
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    PREFLIGHT_BLOCKED = "PREFLIGHT_BLOCKED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    DATABASE_BUSY = "DATABASE_BUSY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# --- Validation limits (§9) --------------------------------------------------

TITLE_MIN = 3
TITLE_MAX = 120
PARTNER_MAX = 120
COPY_MIN = 1
COPY_MAX = 20_000
COMMENT_MIN = 1
COMMENT_MAX = 2_000
ASSET_URL_MAX = 2_048
SCHEMES_ALLOWED = {"http", "https"}


def _trim(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# --- Request models (§9) -----------------------------------------------------

class IntakeRequest(BaseModel):
    """Create a new submission (owned by the submitter). Server supplies
    status, versions, deadlines, ids, and timestamps."""
    model_config = ConfigDict(extra="forbid")

    title: str
    partner: str | None = None
    channel: Channel
    product: Product
    target_launch_date: str
    asset_url: str | None = None
    copy_text: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not (TITLE_MIN <= len(value) <= TITLE_MAX):
            raise ValueError(
                f"title must be {TITLE_MIN}-{TITLE_MAX} characters"
            )
        return value

    @field_validator("partner")
    @classmethod
    def normalize_partner(cls, value: str | None) -> str | None:
        return _trim(value)

    @field_validator("copy_text")
    @classmethod
    def validate_copy(cls, value: str) -> str:
        value = value.strip()
        if not (COPY_MIN <= len(value) <= COPY_MAX):
            raise ValueError(
                f"copy_text must be {COPY_MIN}-{COPY_MAX} characters"
            )
        return value

    @field_validator("asset_url")
    @classmethod
    def validate_asset_url(cls, value: str | None) -> str | None:
        value = _trim(value)
        if value is None:
            return None
        if len(value) > ASSET_URL_MAX:
            raise ValueError(f"asset_url must be at most {ASSET_URL_MAX} characters")
        parts = urlsplit(value)
        if parts.scheme not in SCHEMES_ALLOWED or not parts.netloc:
            raise ValueError("asset_url must be an absolute http(s) URL")
        if parts.username is not None or parts.password is not None:
            raise ValueError("asset_url must not contain credentials")
        return value

    @field_validator("target_launch_date")
    @classmethod
    def validate_launch_date(cls, value: str) -> str:
        # ISO date-only; no earlier than current UTC date at intake.
        try:
            date = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("target_launch_date must be an ISO date") from exc
        if date.tzinfo is not None and date.tzinfo != timezone.utc:
            date = date.astimezone(timezone.utc)
        today = datetime.now(timezone.utc).date()
        if date.date() < today:
            raise ValueError("target_launch_date must not be in the past")
        return date.date().isoformat()

    @model_validator(mode="after")
    def partner_required_for_affiliate(self) -> "IntakeRequest":
        if self.channel == Channel.AFFILIATE:
            if self.partner is None:
                raise ValueError("partner is required for the affiliate channel")
            if len(self.partner) > PARTNER_MAX:
                raise ValueError(f"partner must be at most {PARTNER_MAX} characters")
        return self


class ExpectedRecordVersion(BaseModel):
    """Common field for mutations that require optimistic concurrency."""
    model_config = ConfigDict(extra="forbid")
    expected_record_version: int = Field(ge=1)


class AssignRequest(ExpectedRecordVersion):
    """Assign/reassign a reviewer. Any reviewer may perform this action."""
    reviewer_id: str
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str | None) -> str | None:
        return _trim(value)


class RequestChangesRequest(ExpectedRecordVersion):
    """Assigned reviewer asks the owning submitter for revisions."""
    comment: str

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        value = value.strip()
        if not (COMMENT_MIN <= len(value) <= COMMENT_MAX):
            raise ValueError(
                f"comment must be {COMMENT_MIN}-{COMMENT_MAX} characters"
            )
        return value


class ResubmitRequest(ExpectedRecordVersion):
    """Owning submitter revises copy after CHANGES_REQUESTED."""
    copy_text: str
    asset_url: str | None = None

    @field_validator("copy_text")
    @classmethod
    def validate_copy(cls, value: str) -> str:
        value = value.strip()
        if not (COPY_MIN <= len(value) <= COPY_MAX):
            raise ValueError(
                f"copy_text must be {COPY_MIN}-{COPY_MAX} characters"
            )
        return value

    @field_validator("asset_url")
    @classmethod
    def validate_asset_url(cls, value: str | None) -> str | None:
        value = _trim(value)
        if value is None:
            return None
        if len(value) > ASSET_URL_MAX:
            raise ValueError(f"asset_url must be at most {ASSET_URL_MAX} characters")
        parts = urlsplit(value)
        if parts.scheme not in SCHEMES_ALLOWED or not parts.netloc:
            raise ValueError("asset_url must be an absolute http(s) URL")
        if parts.username is not None or parts.password is not None:
            raise ValueError("asset_url must not contain credentials")
        return value


class ApproveRequest(ExpectedRecordVersion):
    """Assigned reviewer approves after zero blocking findings."""
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str | None) -> str | None:
        return _trim(value)


class RejectRequest(ExpectedRecordVersion):
    """Assigned reviewer rejects with a nonblank reason."""
    comment: str

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        value = value.strip()
        if not (COMMENT_MIN <= len(value) <= COMMENT_MAX):
            raise ValueError(
                f"comment must be {COMMENT_MIN}-{COMMENT_MAX} characters"
            )
        return value
