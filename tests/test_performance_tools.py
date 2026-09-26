"""Correctness checks for benchmark fixtures and reported measurements."""

import sqlite3
from datetime import datetime, timezone
import pytest
from scripts.performance.generate import generate
from scripts.performance.run import percentile, summary


def test_scale_fixture_reproducible_and_safe(tmp_path):
    now = datetime(2026, 9, 25, 17, tzinfo=timezone.utc)
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    first = generate(a, 100, now=now)
    second = generate(b, 100, now=now)
    assert first["counts"] == second["counts"]
    conn_a = sqlite3.connect(a)
    conn_b = sqlite3.connect(b)
    assert (
        conn_a.execute("SELECT * FROM submissions ORDER BY id").fetchall()
        == conn_b.execute("SELECT * FROM submissions ORDER BY id").fetchall()
    )
    assert not conn_a.execute("PRAGMA foreign_key_check").fetchall()
    assert first["counts"]["submissions"] == 100
    assert first["counts"]["users"] == 240
    conn_a.close()
    conn_b.close()
    with pytest.raises(FileExistsError):
        generate(a, 100, now=now)


def test_nearest_rank_percentile_and_error_accounting():
    assert percentile([10, 20, 30, 40], 0.95) == 40
    assert percentile([], 0.5) is None
    result = summary(
        "test",
        [
            {"ms": 10, "ok": True, "status": 200, "bytes": 100},
            {"ms": 1000, "ok": False, "status": 503, "bytes": 20},
        ],
        2,
    )
    assert result["successful_rps"] == 0.5
    assert result["errors"] == 1
    assert (
        result["latency_ms"]["p95"] == 1000
    )  # failures cannot vanish from latency stats
    assert result["decoded_response_bytes"] == 120
