"""Metric boundary tests use a fixed clock and the actual seeded database."""

from datetime import datetime, timedelta, timezone

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


def test_negative_duration_excluded_from_turnaround_not_completions(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    decided = frozen_now - timedelta(days=2)
    # decided_at before submitted_at: still counts as a completion, not turnaround.
    conn.execute(
        "UPDATE submissions SET decided_at = ?, submitted_at = ? WHERE status = 'APPROVED'",
        (decided.isoformat(), (decided + timedelta(days=1)).isoformat()),
    )
    result = metrics.compute_metrics(conn, frozen_now)
    assert result["completed_last_7_days"] == 1
    assert result["turnaround_sample_size"] == 0
    assert result["avg_turnaround_days"] is None
    conn.close()


def test_mixed_timestamp_serialization_matches_boundaries(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    z_decision = (frozen_now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    offset_submitted = (frozen_now - timedelta(days=8)).isoformat()
    conn.execute(
        "UPDATE submissions SET decided_at = ?, submitted_at = ? WHERE status = 'APPROVED'",
        (z_decision, offset_submitted),
    )
    result = metrics.compute_metrics(conn, frozen_now)
    assert result["completed_last_7_days"] == 1
    assert result["turnaround_sample_size"] == 1
    assert result["avg_turnaround_days"] == 5.0
    conn.close()


def test_submitter_scope_isolates_metrics(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    submitter = conn.execute(
        "SELECT id FROM users WHERE role = 'SUBMITTER' LIMIT 1"
    ).fetchone()["id"]
    own = metrics.compute_metrics(
        conn, frozen_now, actor_id=submitter, scope="own_submissions"
    )
    team = metrics.compute_metrics(conn, frozen_now, scope="all_submissions")
    assert own["scope"] == "own_submissions"
    assert team["scope"] == "all_submissions"
    assert own["open_count"] == team["open_count"]
    assert own["as_of"] == team["as_of"]
    conn.close()


def test_zero_duration_included_and_future_decision_excluded(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    conn.execute(
        "UPDATE submissions SET decided_at = ?, submitted_at = ? WHERE status = 'APPROVED'",
        (frozen_now.isoformat(), frozen_now.isoformat()),
    )
    result = metrics.compute_metrics(conn, frozen_now)
    assert result["turnaround_sample_size"] == 1
    assert result["avg_turnaround_days"] == 0.0
    assert result["completed_last_7_days"] == 1
    future = frozen_now + timedelta(hours=1)
    conn.execute(
        "UPDATE submissions SET decided_at = ?, submitted_at = ? WHERE status = 'APPROVED'",
        (future.isoformat(), frozen_now.isoformat()),
    )
    result = metrics.compute_metrics(conn, frozen_now)
    assert result["turnaround_sample_size"] == 0
    assert result["completed_last_7_days"] == 0
    conn.close()


def test_sql_metrics_match_reference_on_seeded_fixture(temp_db, frozen_now):
    """Guard SQL aggregates against the previous Python semantics."""
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    rows = conn.execute(
        "SELECT id, status, assigned_reviewer_id, submitter_id, "
        "submitted_at, sla_breach_at, decided_at FROM submissions"
    ).fetchall()

    open_statuses = set(metrics.OPEN_STATUSES)
    terminal = set(metrics.TERMINAL_STATUSES)
    visible = [dict(r) for r in rows]
    open_items = [r for r in visible if r["status"] in open_statuses]
    terminal_items = [r for r in visible if r["status"] in terminal]
    sample = []
    completed = 0
    for it in terminal_items:
        if not it["decided_at"]:
            continue
        submitted = datetime.fromisoformat(it["submitted_at"]).astimezone(timezone.utc)
        decided = datetime.fromisoformat(it["decided_at"]).astimezone(timezone.utc)
        days = (decided - submitted).total_seconds() / 86400.0
        if frozen_now - timedelta(days=30) <= decided <= frozen_now and days >= 0:
            sample.append(days)
        if frozen_now - timedelta(days=7) <= decided <= frozen_now:
            completed += 1
    expected = {
        "open_count": len(open_items),
        "sla_breached_count": sum(
            1
            for it in open_items
            if metrics.urgency_bucket(frozen_now, it["sla_breach_at"]) == metrics.BREACHED
        ),
        "unassigned_count": sum(
            1 for it in open_items if it["assigned_reviewer_id"] is None
        ),
        "avg_turnaround_days": round(sum(sample) / len(sample), 1) if sample else None,
        "turnaround_sample_size": len(sample),
        "completed_last_7_days": completed,
    }
    got = metrics.compute_metrics(conn, frozen_now)
    for key, value in expected.items():
        assert got[key] == value, key
    conn.close()


def test_equivalent_fractional_timestamps_at_inclusive_boundaries(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    for days in (0, 7, 30):
        decided = frozen_now - timedelta(days=days)
        conn.execute(
            "UPDATE submissions SET decided_at = ?, submitted_at = ? WHERE status = 'APPROVED'",
            (decided.strftime('%Y-%m-%dT%H:%M:%S.000000Z'),
             (decided - timedelta(days=5)).isoformat()),
        )
        result = metrics.compute_metrics(conn, frozen_now)
        assert result['turnaround_sample_size'] == 1
        assert result['avg_turnaround_days'] == 5.0
        assert result['completed_last_7_days'] == int(days <= 7)
    # A genuinely future timestamp one microsecond away must remain excluded.
    conn.execute(
        "UPDATE submissions SET decided_at = ? WHERE status = 'APPROVED'",
        ((frozen_now + timedelta(microseconds=1)).isoformat(),),
    )
    result = metrics.compute_metrics(conn, frozen_now)
    assert result['completed_last_7_days'] == 0
    assert result['turnaround_sample_size'] == 0
    conn.close()


def test_direct_reviewer_metrics_preserve_assigned_or_unassigned_scope(temp_db, frozen_now):
    conn = db.connect(temp_db)
    seed.seed_database(conn, frozen_now)
    for reviewer in conn.execute("SELECT id FROM users WHERE role='REVIEWER'").fetchall():
        expected = metrics.queue(conn, frozen_now, actor_id=reviewer['id'])
        result = metrics.compute_metrics(conn, frozen_now, actor_id=reviewer['id'])
        assert result['open_count'] == len(expected)
    conn.close()
