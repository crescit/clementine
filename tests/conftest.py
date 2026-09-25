"""Shared pytest fixtures. Tests use temporary DBs and an injected UTC clock."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clearpath import db
from clearpath.api import create_app


@pytest.fixture
def frozen_now() -> datetime:
    """Injectable UTC clock for relative-time assertions (expanded in later cards)."""
    return datetime(2026, 9, 25, 17, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    conn = db.connect(path)
    try:
        db.initialize_schema(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def client(temp_db: Path) -> Generator[TestClient, None, None]:
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
