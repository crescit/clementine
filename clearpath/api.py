"""FastAPI application: routes, identity, static serving.

K0: health check, static root, lifespan schema init.
Domain routes land in K5–K6.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from clearpath import db

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def demo_mode_enabled() -> bool:
    return os.environ.get("DEMO_MODE", "true").lower() in {"1", "true", "yes"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = db.get_database_path()
    logger.info("Initializing database at %s (demo_mode=%s)", db_path, demo_mode_enabled())
    conn = db.connect(db_path)
    try:
        db.initialize_schema(conn)
    finally:
        conn.close()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="ClearPath Compliance Review",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get("/api/health")
    def health():
        if not db.ping():
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable"},
            )
        return {"status": "ok"}

    @application.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    # Mount after explicit routes so / and /api/* are not swallowed.
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return application


app = create_app()
