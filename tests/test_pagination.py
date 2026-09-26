"""Pagination and summary-projection tests for GET /api/submissions."""

from __future__ import annotations

import uuid
from datetime import timedelta

from clearpath import db


def _headers(uid: str) -> dict[str, str]:
    return {"X-Demo-User-Id": uid}


def _users(client):
    users = client.get("/api/users").json()["users"]
    return {
        "submitter": next(u["id"] for u in users if u["role"] == "SUBMITTER"),
        "reviewer": next(u["id"] for u in users if u["role"] == "REVIEWER"),
    }


def _bulk_open(conn, submitter_id: str, reviewer_id: str, frozen_now, count: int):
    """Insert many open submissions with stable SLA ordering via external_id ties."""
    base = frozen_now - timedelta(days=10)
    for i in range(count):
        sid = str(uuid.uuid4())
        external_id = f"CP-P{i:04d}"
        # Identical SLA/launch/submitted so external_id alone orders pages.
        submitted = base.strftime("%Y-%m-%dT%H:%M:%SZ")
        sla = (base + timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
        launch = (frozen_now + timedelta(days=14)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO submissions ("
            "id, external_id, title, partner, channel, product, status, "
            "assigned_reviewer_id, submitter_id, target_launch_date, submitted_at, "
            "sla_breach_at, decided_at, current_version, record_version, "
            "created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                external_id,
                f"Pagination campaign {i:04d}",
                None,
                "WEBSITE",
                "PERSONAL_LOAN",
                "UNDER_REVIEW",
                reviewer_id,
                submitter_id,
                launch,
                submitted,
                sla,
                None,
                1,
                1,
                submitted,
                submitted,
            ),
        )
        conn.execute(
            "INSERT INTO submission_versions ("
            "id, submission_id, version_number, asset_url, copy_text, created_by, created_at"
            ") VALUES (?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                sid,
                1,
                None,
                f"Copy for pagination campaign {i:04d}. Subject to credit approval.",
                submitter_id,
                submitted,
            ),
        )
    conn.commit()


def test_queue_defaults_to_summary_page(seeded_client, frozen_now):
    ids = _users(seeded_client)
    response = seeded_client.get("/api/submissions", headers=_headers(ids["reviewer"]))
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"submissions", "total", "limit", "offset"}
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 6
    assert len(body["submissions"]) == 6
    row = body["submissions"][0]
    assert "copy_text" not in row and "asset_url" not in row
    assert "urgency" in row and "allowed_actions" in row
    assert "title" in row and "external_id" in row


def test_invalid_limit_and_offset_rejected(seeded_client):
    ids = _users(seeded_client)
    for params in [{"limit": 0}, {"limit": 101}, {"offset": -1}]:
        response = seeded_client.get(
            "/api/submissions", params=params, headers=_headers(ids["reviewer"])
        )
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"


def test_pagination_traverses_without_duplicates_or_gaps(seeded_client, frozen_now):
    ids = _users(seeded_client)
    conn = db.connect()
    # Seed already has 6 open rows; add enough for multi-page traversal.
    _bulk_open(conn, ids["submitter"], ids["reviewer"], frozen_now, 120)
    conn.close()

    collected = []
    offset = 0
    limit = 50
    total = None
    while True:
        response = seeded_client.get(
            "/api/submissions",
            params={"limit": limit, "offset": offset},
            headers=_headers(ids["reviewer"]),
        )
        assert response.status_code == 200
        body = response.json()
        if total is None:
            total = body["total"]
            assert total >= 126
        assert body["total"] == total
        assert body["limit"] == limit
        assert body["offset"] == offset
        page_ids = [s["id"] for s in body["submissions"]]
        collected.extend(page_ids)
        if offset + limit >= total:
            break
        offset += limit

    assert len(collected) == total
    assert len(set(collected)) == total


def test_middle_and_last_pages_and_empty_page(seeded_client, frozen_now):
    ids = _users(seeded_client)
    conn = db.connect()
    _bulk_open(conn, ids["submitter"], ids["reviewer"], frozen_now, 120)
    conn.close()
    first = seeded_client.get(
        "/api/submissions",
        params={"limit": 50, "offset": 0},
        headers=_headers(ids["reviewer"]),
    ).json()
    middle = seeded_client.get(
        "/api/submissions",
        params={"limit": 50, "offset": 50},
        headers=_headers(ids["reviewer"]),
    ).json()
    last = seeded_client.get(
        "/api/submissions",
        params={"limit": 50, "offset": 100},
        headers=_headers(ids["reviewer"]),
    ).json()
    empty = seeded_client.get(
        "/api/submissions",
        params={"limit": 50, "offset": 10_000},
        headers=_headers(ids["reviewer"]),
    ).json()
    assert len(first["submissions"]) == 50
    assert len(middle["submissions"]) == 50
    assert 0 < len(last["submissions"]) <= 50
    assert empty["submissions"] == []
    assert empty["total"] == first["total"]
    first_ids = {s["id"] for s in first["submissions"]}
    middle_ids = {s["id"] for s in middle["submissions"]}
    last_ids = {s["id"] for s in last["submissions"]}
    assert not (first_ids & middle_ids)
    assert not (middle_ids & last_ids)
    assert not (first_ids & last_ids)


def test_explicit_limit_100_supported(seeded_client, frozen_now):
    ids = _users(seeded_client)
    conn = db.connect()
    _bulk_open(conn, ids["submitter"], ids["reviewer"], frozen_now, 120)
    conn.close()
    body = seeded_client.get(
        "/api/submissions",
        params={"limit": 100},
        headers=_headers(ids["reviewer"]),
    ).json()
    assert body["limit"] == 100
    assert len(body["submissions"]) == 100


def test_completed_ordering_and_filters_preserve_total(seeded_client):
    ids = _users(seeded_client)
    completed = seeded_client.get(
        "/api/submissions",
        params={"completed": "true", "limit": 50},
        headers=_headers(ids["reviewer"]),
    ).json()
    assert completed["total"] == 1
    assert completed["submissions"][0]["status"] == "APPROVED"
    filtered = seeded_client.get(
        "/api/submissions",
        params={"status": "UNDER_REVIEW", "limit": 10},
        headers=_headers(ids["reviewer"]),
    ).json()
    assert filtered["total"] == len(filtered["submissions"])
    assert all(s["status"] == "UNDER_REVIEW" for s in filtered["submissions"])


def test_submitter_cannot_see_other_rows_or_counts(seeded_client, frozen_now):
    ids = _users(seeded_client)
    conn = db.connect()
    conn.execute(
        "INSERT INTO users VALUES ('other-marketer', 'Other Marketer', 'SUBMITTER', "
        "'Marketing', '2026-09-25T00:00:00Z')"
    )
    conn.commit()
    _bulk_open(conn, "other-marketer", ids["reviewer"], frozen_now, 30)
    conn.close()
    own = seeded_client.get(
        "/api/submissions", headers=_headers(ids["submitter"])
    ).json()
    foreign = seeded_client.get(
        "/api/submissions", headers=_headers("other-marketer")
    ).json()
    assert own["total"] == 6
    assert all(s["submitter_id"] == ids["submitter"] for s in own["submissions"])
    assert foreign["total"] == 30
    assert all(s["submitter_id"] == "other-marketer" for s in foreign["submissions"])
    assert seeded_client.get(
        "/api/metrics", headers=_headers("other-marketer")
    ).json()["open_count"] == 30


def test_timestamp_ties_break_on_external_id(seeded_client, frozen_now):
    ids = _users(seeded_client)
    conn = db.connect()
    _bulk_open(conn, ids["submitter"], ids["reviewer"], frozen_now, 5)
    conn.close()
    body = seeded_client.get(
        "/api/submissions",
        params={"search": "Pagination campaign", "limit": 50},
        headers=_headers(ids["reviewer"]),
    ).json()
    external_ids = [s["external_id"] for s in body["submissions"]]
    assert external_ids == sorted(external_ids)
