"""Metric boundary tests use a fixed clock and the actual seeded database."""

from datetime import timedelta
from clearpath import db, metrics, seed


def test_baseline_metrics_and_urgency_boundaries(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    result = metrics.compute_metrics(conn, frozen_now)
    assert {
        k: result[k]
        for k in (
            "open_count",
            "sla_breached_count",
            "unassigned_count",
            "avg_turnaround_days",
            "turnaround_sample_size",
            "completed_last_7_days",
        )
    } == dict(
        open_count=6,
        sla_breached_count=2,
        unassigned_count=2,
        avg_turnaround_days=5.0,
        turnaround_sample_size=1,
        completed_last_7_days=1,
    )
    for hours, expected in [
        (0, "BREACHED"),
        (24, "DUE_24H"),
        (24.01, "DUE_48H"),
        (48, "DUE_48H"),
        (48.01, "ON_TRACK"),
    ]:
        assert (
            metrics.urgency_bucket(
                frozen_now, (frozen_now + timedelta(hours=hours)).isoformat()
            )
            == expected
        )
    conn.close()


def test_turnaround_window_is_30_days_and_completions_7_days(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    for days, sample_count in [(30, 1), (30.01, 0), (-1, 0)]:
        decision = frozen_now - timedelta(days=days)
        conn.execute(
            "UPDATE submissions SET decided_at = ?, submitted_at = ? WHERE status = 'APPROVED'",
            (decision.isoformat(), (decision - timedelta(days=4)).isoformat()),
        )
        result = metrics.compute_metrics(conn, frozen_now)
        assert result["turnaround_sample_size"] == sample_count
        assert result["avg_turnaround_days"] == (4.0 if sample_count else None)
        assert result["completed_last_7_days"] == 0
    conn.close()
