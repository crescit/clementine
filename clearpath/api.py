"""FastAPI application: routes, identity, static serving (K5-K6).

Reads are served directly from SQLite; mutations go through the workflow
service layer which owns the write transaction and domain checks. Identity
comes from `X-Demo-User-Id` (§5) — the backend resolves the user and never
trusts role/actor IDs in request JSON. Unknown/missing identity returns 401.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from clearpath import db, metrics as metrics_mod, seed, workflow
from clearpath.preflight import run_preflight
from clearpath.models import (
    ApproveRequest,
    AssignRequest,
    ErrorCode,
    IntakeRequest,
    RejectRequest,
    RequestChangesRequest,
    ResubmitRequest,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Error code -> HTTP status mapping (§9)
_STATUS_BY_CODE = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INVALID_TRANSITION: 409,
    ErrorCode.PREFLIGHT_BLOCKED: 409,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.DATABASE_BUSY: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}


def demo_mode_enabled() -> bool:
    return os.environ.get("DEMO_MODE", "true").lower() in {"1", "true", "yes"}


def _now() -> datetime:
    """One UTC clock per request (injected; tests may override via monkeypatch)."""
    return datetime.now(timezone.utc)


def _resolve_actor(conn: sqlite3.Connection, user_id: str | None):
    """Resolve the X-Demo-User-Id header to a user row.

    Raises HTTPException(401) when missing/unknown. Returns a sqlite3.Row.
    """
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": ErrorCode.UNAUTHENTICATED,
                "message": "Missing X-Demo-User-Id",
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
                "message": "Unknown demo user",
                "details": {"user_id": user_id},
            },
        )
    return row


def _error(
    code: str, message: str, status: int = 400, details: dict | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "details": details},
    )


def _map_domain_error(exc: Exception) -> JSONResponse:
    code = getattr(exc, "code", ErrorCode.INTERNAL_ERROR)
    status = _STATUS_BY_CODE.get(code, 500)
    message = getattr(exc, "message", None) or str(exc)
    details = getattr(exc, "details", None)
    return _error(code, message, status, details)


def _open() -> sqlite3.Connection:
    return db.connect()


def _users_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, role, display_title FROM users ORDER BY name"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "role": r["role"],
            "display_title": r["display_title"],
        }
        for r in rows
    ]


def _submission_row(conn: sqlite3.Connection, submission_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM submissions WHERE id = ?", (submission_id,)
    ).fetchone()


def _projection(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    version_row = conn.execute(
        "SELECT asset_url, copy_text FROM submission_versions "
        "WHERE submission_id = ? AND version_number = ?",
        (row["id"], row["current_version"]),
    ).fetchone()
    return {
        "id": row["id"],
        "external_id": row["external_id"],
        "title": row["title"],
        "partner": row["partner"],
        "channel": row["channel"],
        "product": row["product"],
        "status": row["status"],
        "assigned_reviewer_id": row["assigned_reviewer_id"],
        "submitter_id": row["submitter_id"],
        "target_launch_date": row["target_launch_date"],
        "asset_url": version_row["asset_url"] if version_row else None,
        "copy_text": version_row["copy_text"] if version_row else None,
        "submitted_at": row["submitted_at"],
        "sla_breach_at": row["sla_breach_at"],
        "decided_at": row["decided_at"],
        "current_version": row["current_version"],
        "record_version": row["record_version"],
    }


def _allowed_actions(actor_row, row) -> list[str]:
    if row["status"] in ("APPROVED", "REJECTED"):
        return []
    if actor_row["role"] == "SUBMITTER":
        return (
            ["RESUBMIT"]
            if row["status"] == "CHANGES_REQUESTED"
            and row["submitter_id"] == actor_row["id"]
            else []
        )
    actions = ["ASSIGN"]
    if (
        row["status"] == "UNDER_REVIEW"
        and row["assigned_reviewer_id"] == actor_row["id"]
    ):
        actions += ["REQUEST_CHANGES", "APPROVE", "REJECT"]
    return actions


def _preflight(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    version_row = conn.execute(
        "SELECT copy_text FROM submission_versions "
        "WHERE submission_id = ? AND version_number = ?",
        (row["id"], row["current_version"]),
    ).fetchone()
    copy_text = version_row["copy_text"] if version_row else ""
    from clearpath.preflight import run_preflight

    return run_preflight(row["product"], row["channel"], copy_text)


def _enforce_visible(
    conn: sqlite3.Connection, actor_row, submission_id: str
) -> sqlite3.Row:
    row = _submission_row(conn, submission_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": ErrorCode.NOT_FOUND, "message": "Submission not found"},
        )
    if actor_row["role"] != "REVIEWER" and row["submitter_id"] != actor_row["id"]:
        raise HTTPException(
            status_code=404,
            detail={
                "code": ErrorCode.NOT_FOUND,
                "message": "Submission not found or not visible",
            },
        )
    return row


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = db.get_database_path()
    now = datetime.now(timezone.utc)
    logger.info(
        "Initializing database at %s (demo_mode=%s)", db_path, demo_mode_enabled()
    )
    if demo_mode_enabled():
        seeded = seed.auto_seed(db_path, now)
        logger.info("Fresh DB seeded: %s", seeded)
    else:
        conn = db.connect()
        try:
            db.initialize_schema(conn)
        finally:
            conn.close()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="ClearPath Compliance Review", version="0.1.0", lifespan=lifespan
    )

    # Return HTTPException detail at the top level (not wrapped under "detail").
    @application.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {
                "field": ".".join(str(p) for p in e["loc"] if p != "body"),
                "message": e["msg"],
            }
            for e in exc.errors()
        ]
        return _error(
            ErrorCode.VALIDATION_ERROR,
            "Please check the highlighted fields.",
            422,
            {"fields": fields},
        )

    @application.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, exc: sqlite3.OperationalError):
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return _error(
                ErrorCode.DATABASE_BUSY, "The database is busy. Please retry.", 503
            )
        logger.error("Database operation failed", exc_info=exc)
        return _error(ErrorCode.INTERNAL_ERROR, "Unable to complete this request.", 500)

    @application.get("/api/config")
    def config():
        return {"demo_mode": demo_mode_enabled()}

    # --- Health -----------------------------------------------------------------
    @application.get("/api/health")
    def health():
        conn = None
        try:
            conn = db.connect()
            conn.execute("SELECT 1 FROM submissions LIMIT 1").fetchone()
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        finally:
            if conn is not None:
                conn.close()
        return {"status": "ok"}

    # --- Identity & reads ---------------------------------------------------------
    @application.get("/api/users")
    def users():
        conn = db.connect()
        try:
            return {"users": _users_rows(conn)}
        finally:
            conn.close()

    @application.get("/api/submissions")
    def list_submissions(
        mine: bool | None = None,
        reviewer: str | None = None,
        status: str | None = None,
        search: str | None = None,
        completed: bool | None = None,
        risk: bool = False,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            now = _now()
            where = []
            params: list = []
            if actor_row["role"] != "REVIEWER":
                where.append("submitter_id = ?")
                params.append(actor_row["id"])
            if mine:
                if actor_row["role"] == "REVIEWER":
                    where.append("assigned_reviewer_id = ?")
                    params.append(actor_row["id"])
                else:
                    where.append("submitter_id = ?")
                    params.append(actor_row["id"])
            if reviewer:
                if reviewer == "MINE":
                    where.append("assigned_reviewer_id = ?")
                    params.append(actor_row["id"])
                elif reviewer == "UNASSIGNED":
                    where.append("assigned_reviewer_id IS NULL")
                else:
                    where.append("assigned_reviewer_id = ?")
                    params.append(reviewer)
            if risk:
                where.append("sla_breach_at <= ?")
                params.append(
                    (now + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
                )
            if status:
                where.append("status = ?")
                params.append(status)
            if search is not None:
                where.append("(title LIKE ? OR partner LIKE ? OR external_id LIKE ?)")
                like = f"%{search}%"
                params.extend([like, like, like])
            if completed is True:
                where.append("status IN ('APPROVED', 'REJECTED')")
            else:
                where.append(
                    "status IN ('PENDING_ASSIGNMENT', 'UNDER_REVIEW', 'CHANGES_REQUESTED')"
                )

            sql = (
                "SELECT * FROM submissions "
                + ("WHERE " + " AND ".join(where) if where else "")
                + (
                    " ORDER BY decided_at DESC, external_id"
                    if completed
                    else " ORDER BY sla_breach_at, target_launch_date, submitted_at, external_id"
                )
            )
            rows = conn.execute(sql, params).fetchall()
            items = []
            for r in rows:
                item = _projection(conn, r)
                item["urgency"] = metrics_mod.urgency_bucket(now, r["sla_breach_at"])
                item["allowed_actions"] = _allowed_actions(actor_row, r)
                items.append(item)
            return {"submissions": items}
        finally:
            conn.close()

    @application.get("/api/submissions/{submission_id}")
    def submission_detail(
        submission_id: str,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            row = _enforce_visible(conn, actor_row, submission_id)
            detail = _projection(conn, row)
            detail["allowed_actions"] = _allowed_actions(actor_row, row)
            detail["preflight"] = _preflight(conn, row)
            return detail
        finally:
            conn.close()

    @application.get("/api/submissions/{submission_id}/history")
    def submission_history(
        submission_id: str,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            row = _enforce_visible(conn, actor_row, submission_id)
            versions = conn.execute(
                "SELECT version_number, asset_url, copy_text, created_by, created_at "
                "FROM submission_versions WHERE submission_id = ? ORDER BY version_number",
                (submission_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT actor_id, event_type, from_status, to_status, version_number, comment, metadata_json, created_at "
                "FROM audit_events WHERE submission_id = ? ORDER BY created_at, id",
                (submission_id,),
            ).fetchall()
            return {
                "submission_id": submission_id,
                "versions": [
                    {
                        **dict(v),
                        "preflight": run_preflight(
                            row["product"], row["channel"], v["copy_text"]
                        ),
                    }
                    for v in versions
                ],
                "events": [dict(e) for e in events],
            }
        finally:
            conn.close()

    @application.get("/api/metrics")
    def metrics(
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            return metrics_mod.compute_metrics(
                conn,
                _now(),
                actor_id=actor_row["id"] if actor_row["role"] == "SUBMITTER" else None,
                scope="own_submissions"
                if actor_row["role"] == "SUBMITTER"
                else "all_submissions",
            )
        finally:
            conn.close()

    # --- Mutations (via workflow service) ----------------------------------------
    @application.post("/api/submissions", status_code=201)
    def create_submission(
        payload: IntakeRequest,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            try:
                result = workflow.create_submission(
                    conn,
                    actor_row["id"],
                    title=payload.title,
                    copy_text=payload.copy_text,
                    channel=payload.channel,
                    product=payload.product,
                    target_launch_date=payload.target_launch_date,
                    asset_url=payload.asset_url,
                    partner=payload.partner,
                    now=_now(),
                )
            except workflow.DomainError as exc:
                return _map_domain_error(exc)
            return result
        finally:
            conn.close()

    @application.post("/api/submissions/{submission_id}/assign")
    def assign_submission(
        submission_id: str,
        payload: AssignRequest,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            try:
                result = workflow.assign_submission(
                    conn,
                    actor_row["id"],
                    submission_id,
                    reviewer_id=payload.reviewer_id,
                    comment=payload.comment,
                    expected_record_version=payload.expected_record_version,
                    now=_now(),
                )
            except workflow.DomainError as exc:
                return _map_domain_error(exc)
            return result
        finally:
            conn.close()

    @application.post("/api/submissions/{submission_id}/request-changes")
    def request_changes(
        submission_id: str,
        payload: RequestChangesRequest,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            try:
                result = workflow.request_changes(
                    conn,
                    actor_row["id"],
                    submission_id,
                    feedback=payload.comment,
                    expected_record_version=payload.expected_record_version,
                    now=_now(),
                )
            except workflow.DomainError as exc:
                return _map_domain_error(exc)
            return result
        finally:
            conn.close()

    @application.post("/api/submissions/{submission_id}/resubmit")
    def resubmit_submission(
        submission_id: str,
        payload: ResubmitRequest,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            try:
                result = workflow.resubmit_submission(
                    conn,
                    actor_row["id"],
                    submission_id,
                    copy_text=payload.copy_text,
                    asset_url=payload.asset_url,
                    expected_record_version=payload.expected_record_version,
                    now=_now(),
                )
            except workflow.DomainError as exc:
                return _map_domain_error(exc)
            return result
        finally:
            conn.close()

    @application.post("/api/submissions/{submission_id}/approve")
    def approve_submission(
        submission_id: str,
        payload: ApproveRequest,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            try:
                result = workflow.approve_submission(
                    conn,
                    actor_row["id"],
                    submission_id,
                    comment=payload.comment,
                    expected_record_version=payload.expected_record_version,
                    now=_now(),
                )
            except workflow.DomainError as exc:
                return _map_domain_error(exc)
            return result
        finally:
            conn.close()

    @application.post("/api/submissions/{submission_id}/reject")
    def reject_submission(
        submission_id: str,
        payload: RejectRequest,
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            try:
                result = workflow.reject_submission(
                    conn,
                    actor_row["id"],
                    submission_id,
                    reason=payload.comment,
                    expected_record_version=payload.expected_record_version,
                    now=_now(),
                )
            except workflow.DomainError as exc:
                return _map_domain_error(exc)
            return result
        finally:
            conn.close()

    @application.post("/api/demo/reset")
    def demo_reset(
        x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    ):
        """Demo-only reset: wipe all rows and reseed (requires a reviewer persona)."""
        conn = db.connect()
        try:
            actor_row = _resolve_actor(conn, x_demo_user_id)
            if actor_row["role"] != "REVIEWER":
                return _error(ErrorCode.FORBIDDEN, "Reset requires a reviewer", 403)
            if not demo_mode_enabled():
                return _error(
                    ErrorCode.FORBIDDEN, "Reset only available in demo mode", 403
                )
        finally:
            conn.close()
        seed.reset_database(db.get_database_path(), _now())
        return {"status": "reset", "seeded": True}

    # --- Static --------------------------------------------------------------------
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @application.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    return application


app = create_app()
