"""Caller identity resolution.

Routes depend on `caller_id` (via FastAPI `Depends`), not on transport-specific
headers. Today the demo transport is `X-Demo-User-Id` persona impersonation.
Production SSO/JWT should replace only `extract_caller_id` — handlers and
role/ownership checks stay unchanged.

`DEMO_MODE` still only controls seeding/reset; it does not enable real auth.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from clearpath.models import ErrorCode


def extract_caller_id(
    x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    authorization: str | None = Header(default=None),
) -> str | None:
    """Return the caller's stable user id for this request.

    Priority:
    1. `Authorization: Bearer <token>` — production-shaped seam (opaque token
       mapped 1:1 to user id until real IdP validation is wired in).
    2. `X-Demo-User-Id` — demo persona switcher / local tooling.

    Swap this function for JWT/SSO verification when leaving the take-home;
    leave route signatures on `CallerId` alone.
    """
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer" and credential.strip():
            return credential.strip()
    return x_demo_user_id


CallerId = Annotated[str | None, Depends(extract_caller_id)]


def resolve_actor(conn: sqlite3.Connection, user_id: str | None) -> sqlite3.Row:
    """Load the user row for a resolved caller id. Raises HTTPException(401)."""
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": ErrorCode.UNAUTHENTICATED,
                "message": "Missing authenticated user",
            },
        )
    row = conn.execute(
        "SELECT id, name, role, display_title FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": ErrorCode.UNAUTHENTICATED,
                "message": "Unknown user",
                "details": {"user_id": user_id},
            },
        )
    return row
