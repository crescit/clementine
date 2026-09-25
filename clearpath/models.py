"""Shared request/response models and domain enums.

Full Pydantic models land with K2/K5/K6. K0 keeps a placeholder module.
"""

from __future__ import annotations

from enum import StrEnum


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
