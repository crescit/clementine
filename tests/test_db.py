"""Database bootstrap tests. Full schema/seed coverage lands in K1."""

from __future__ import annotations

from pathlib import Path

from clearpath import db


def test_initialize_schema_sets_user_version(temp_db: Path) -> None:
    conn = db.connect(temp_db)
    try:
        assert db.get_user_version(conn) == db.SCHEMA_USER_VERSION
    finally:
        conn.close()


def test_repeated_init_preserves_db(temp_db: Path) -> None:
    conn = db.connect(temp_db)
    try:
        db.initialize_schema(conn)
        assert db.get_user_version(conn) == db.SCHEMA_USER_VERSION
    finally:
        conn.close()


def test_ping_succeeds(temp_db: Path) -> None:
    assert db.ping(temp_db) is True
