"""API smoke tests for K0 bootstrap + K5-K6 domain endpoint suites.

K0: health, root, static pages.
K5-K6: identity (X-Demo-User-Id), submission reads/writes, workflow
mutations, metrics, history, error envelope mapping (401/403/404/409/503).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from pathlib import Path

from clearpath import db

# --- K0 smoke ------------------------------------------------------------------


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_serves_index(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert b"Review Queue" in response.content


def test_static_pages_available(client: TestClient) -> None:
    for page in ["index.html", "submission.html", "submit.html"]:
        response = client.get(f"/static/{page}")
        assert response.status_code == 200, f"{page} failed"
        assert "text/html" in response.headers["content-type"], f"{page} not html"
    index = client.get("/static/index.html").content
    assert b"queue.js" in index
    # Each page wires its own native module script (queue.js / submission.js / submit.js).
    assert b'type="module"' in index
    assert b"submission.js" in client.get("/static/submission.html").content
    assert b"submit.js" in client.get("/static/submit.html").content
    # All pages import the shared common.js seam.
    for page in ["queue.js", "submission.js", "submit.js"]:
        assert b"common.js" in client.get(f"/static/{page}").content


def test_static_assets_available(client: TestClient) -> None:
    for asset in [
        "styles.css",
        "common.js",
        "queue.js",
        "submission.js",
        "submit.js",
    ]:
        response = client.get(f"/static/{asset}")
        assert response.status_code == 200, f"{asset} failed"


# --- K5-K6 helpers --------------------------------------------------------------


def _users(seeded_client: TestClient) -> dict[str, str]:
    resp = seeded_client.get("/api/users")
    assert resp.status_code == 200
    users = resp.json()["users"]
    submitter = next(u for u in users if u["role"] == "SUBMITTER")
    reviewers = [u for u in users if u["role"] == "REVIEWER"]
    assert reviewers, "expected at least 2 reviewers"
    return {
        "submitter": submitter["id"],
        "reviewer_a": reviewers[0]["id"],
        "reviewer_b": reviewers[1]["id"],
    }


def _create(seeded_client: TestClient, submitter: str, **overrides) -> dict:
    payload = {
        "title": "Spring Loan Campaign",
        "copy_text": "Subject to credit approval.",
        "channel": "WEBSITE",
        "product": "PERSONAL_LOAN",
        "target_launch_date": datetime.now(timezone.utc).date().isoformat(),
    }
    payload.update(overrides)
    resp = seeded_client.post(
        "/api/submissions", json=payload, headers={"X-Demo-User-Id": submitter}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Identity (§5) ---------------------------------------------------------------


def test_missing_identity_401(seeded_client: TestClient) -> None:
    resp = seeded_client.get("/api/submissions")
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


def test_unknown_identity_401(seeded_client: TestClient) -> None:
    resp = seeded_client.get(
        "/api/submissions", headers={"X-Demo-User-Id": "does-not-exist"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHENTICATED"


def test_bearer_identity_accepted(seeded_client: TestClient) -> None:
    """Production-shaped Authorization: Bearer uses the same user resolution."""
    ids = _users(seeded_client)
    resp = seeded_client.get(
        "/api/submissions",
        headers={"Authorization": f"Bearer {ids['reviewer_a']}"},
    )
    assert resp.status_code == 200
    assert "submissions" in resp.json()


def test_users_endpoint_lists_personas(seeded_client: TestClient) -> None:
    resp = seeded_client.get("/api/users")
    assert resp.status_code == 200
    users = resp.json()["users"]
    roles = {u["role"] for u in users}
    assert roles == {"SUBMITTER", "REVIEWER"}
    assert all(u["display_title"] for u in users)


# Integration tests exercise the real database, automatic assignment, and API.
def headers(uid):
    return {"X-Demo-User-Id": uid}


def get_record(client, sid, uid):
    response = client.get(f"/api/submissions/{sid}", headers=headers(uid))
    assert response.status_code == 200, response.text
    return response.json()


def act(client, record, uid, action, **body):
    return client.post(
        f"/api/submissions/{record['id']}/{action}",
        headers=headers(uid),
        json={"expected_record_version": record["record_version"], **body},
    )


def test_creation_auto_assignment_and_deadline(seeded_client):
    ids = _users(seeded_client)
    created = _create(
        seeded_client, ids["submitter"], channel="AFFILIATE", partner="Example Partner"
    )
    assert created["status"] == "UNDER_REVIEW"
    assert (
        created["assigned_reviewer_id"] == ids["reviewer_a"]
    )  # Mark has the smaller active workload.
    assert created["partner"] == "Example Partner"
    assert created["external_id"] == "CP-8909"
    assert (
        datetime.fromisoformat(created["sla_breach_at"])
        - datetime.fromisoformat(created["submitted_at"])
    ).total_seconds() == 72 * 3600
    history = seeded_client.get(
        f"/api/submissions/{created['id']}/history", headers=headers(ids["submitter"])
    ).json()
    assert [e["event_type"] for e in history["events"]] == [
        "SUBMITTED",
        "AUTO_ASSIGNED",
    ]
    assert all(
        e["version_number"] == 1 and e["actor_id"] == ids["submitter"]
        for e in history["events"]
    )


def test_visibility_includes_only_own_work_and_metrics(seeded_client):
    ids = _users(seeded_client)
    conn = db.connect()
    conn.execute(
        "INSERT INTO users VALUES ('other', 'Other Marketer', 'SUBMITTER', 'Marketing', '2026-09-25T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    foreign = _create(seeded_client, "other", title="Private campaign")
    for route in (
        f"/api/submissions/{foreign['id']}",
        f"/api/submissions/{foreign['id']}/history",
    ):
        assert (
            seeded_client.get(route, headers=headers(ids["submitter"])).status_code
            == 404
        )
    visible = seeded_client.get(
        "/api/submissions", headers=headers(ids["submitter"])
    ).json()["submissions"]
    assert len(visible) == 6 and all(
        s["submitter_id"] == ids["submitter"] for s in visible
    )
    assert (
        seeded_client.get("/api/metrics", headers=headers("other")).json()["open_count"]
        == 1
    )
    assert (
        seeded_client.get("/api/metrics", headers=headers(ids["reviewer_a"])).json()[
            "open_count"
        ]
        == 7
    )
    assert (
        act(
            seeded_client,
            foreign,
            ids["submitter"],
            "resubmit",
            copy_text="Changed copy",
        ).status_code
        == 404
    )


def test_golden_journey_preserves_versions_and_updates_metrics(seeded_client):
    ids = _users(seeded_client)
    record = _create(seeded_client, ids["submitter"], copy_text="You are pre-approved.")
    reviewer = record["assigned_reviewer_id"]
    blocked = act(seeded_client, record, reviewer, "approve")
    assert blocked.status_code == 409 and blocked.json()["code"] == "PREFLIGHT_BLOCKED"
    assert get_record(seeded_client, record["id"], reviewer)["record_version"] == 1
    response = act(
        seeded_client,
        record,
        reviewer,
        "request-changes",
        comment="Remove the claim and add disclosure.",
    )
    assert response.status_code == 200
    changed = response.json()
    assert changed["status"] == "CHANGES_REQUESTED"
    response = act(
        seeded_client,
        changed,
        ids["submitter"],
        "resubmit",
        copy_text="Explore our rewards. Subject to credit approval.",
    )
    assert response.status_code == 200
    revised = response.json()
    assert revised["current_version"] == 2
    assert revised["sla_breach_at"] == record["sla_breach_at"]
    assert revised["assigned_reviewer_id"] == reviewer
    assert (
        act(
            seeded_client, revised, reviewer, "approve", comment="Reviewed version 2"
        ).status_code
        == 200
    )
    final = get_record(seeded_client, record["id"], reviewer)
    assert final["status"] == "APPROVED" and final["allowed_actions"] == []
    history = seeded_client.get(
        f"/api/submissions/{record['id']}/history", headers=headers(reviewer)
    ).json()
    assert [v["copy_text"] for v in history["versions"]] == [
        "You are pre-approved.",
        "Explore our rewards. Subject to credit approval.",
    ]
    assert history["events"][-1]["version_number"] == 2
    metrics = seeded_client.get("/api/metrics", headers=headers(reviewer)).json()
    assert metrics["open_count"] == 6 and metrics["completed_last_7_days"] == 2


def test_reassignment_permissions_and_terminal_guard(seeded_client):
    ids = _users(seeded_client)
    record = _create(seeded_client, ids["submitter"])
    owner = record["assigned_reviewer_id"]
    other = next(ids[k] for k in ("reviewer_a", "reviewer_b") if ids[k] != owner)
    assert act(seeded_client, record, other, "approve").status_code == 403
    assert (
        act(seeded_client, record, other, "assign", reviewer_id=owner).json()["code"]
        == "INVALID_TRANSITION"
    )
    assert (
        act(seeded_client, record, other, "assign", reviewer_id=other).json()["code"]
        == "VALIDATION_ERROR"
    )
    response = act(
        seeded_client,
        record,
        other,
        "assign",
        reviewer_id=other,
        comment="Balancing workload",
    )
    assert response.status_code == 200
    record = response.json()
    assert act(seeded_client, record, owner, "approve").status_code == 403
    response = act(seeded_client, record, other, "reject", comment="Campaign cancelled")
    assert response.status_code == 200
    final = response.json()
    assert (
        act(
            seeded_client, final, other, "assign", reviewer_id=owner, comment="Reopen"
        ).json()["code"]
        == "INVALID_TRANSITION"
    )


def test_revision_must_change_content_and_url_can_be_removed(seeded_client):
    ids = _users(seeded_client)
    record = _create(
        seeded_client, ids["submitter"], asset_url="https://example.com/context"
    )
    changed = act(
        seeded_client,
        record,
        record["assigned_reviewer_id"],
        "request-changes",
        comment="Remove outdated link",
    ).json()
    unchanged = act(
        seeded_client,
        changed,
        ids["submitter"],
        "resubmit",
        copy_text=changed["copy_text"],
        asset_url=changed["asset_url"],
    )
    assert unchanged.status_code == 409
    revised = act(
        seeded_client,
        changed,
        ids["submitter"],
        "resubmit",
        copy_text=changed["copy_text"],
        asset_url=None,
    )
    assert revised.status_code == 200 and revised.json()["asset_url"] is None


def test_stale_write_never_overwrites(seeded_client):
    ids = _users(seeded_client)
    record = _create(seeded_client, ids["submitter"])
    assert (
        act(
            seeded_client, record, record["assigned_reviewer_id"], "approve"
        ).status_code
        == 200
    )
    stale = act(
        seeded_client,
        record,
        record["assigned_reviewer_id"],
        "reject",
        comment="Stale decision",
    )
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"


def test_filters_compose_and_completed_is_separate(seeded_client):
    ids = _users(seeded_client)
    created = _create(seeded_client, ids["submitter"], title="Needle")
    response = seeded_client.get(
        "/api/submissions",
        params={
            "status": "UNDER_REVIEW",
            "search": "needle",
            "reviewer": created["assigned_reviewer_id"],
        },
        headers=headers(ids["reviewer_a"]),
    )
    assert [s["id"] for s in response.json()["submissions"]] == [created["id"]]
    completed = seeded_client.get(
        "/api/submissions?completed=true", headers=headers(ids["reviewer_a"])
    ).json()["submissions"]
    assert len(completed) == 1 and completed[0]["status"] == "APPROVED"


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": " "},
        {"copy_text": " "},
        {"copy_text": "x" * 20001},
        {"asset_url": "javascript:alert(1)"},
        {"asset_url": "https://user:password@example.com"},
        {"channel": "AFFILIATE", "partner": " "},
        {"partner": "x" * 121},
        {"target_launch_date": "2020-01-01"},
        {"actor_id": "spoofed"},
    ],
)
def test_invalid_intake_is_normalized(seeded_client, overrides):
    ids = _users(seeded_client)
    body = dict(
        title="Valid title",
        copy_text="Copy",
        product="CREDIT_CARD",
        channel="SOCIAL",
        target_launch_date=datetime.now(timezone.utc).date().isoformat(),
    )
    body.update(overrides)
    response = seeded_client.post(
        "/api/submissions", json=body, headers=headers(ids["submitter"])
    )
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"]["fields"]


def test_reset_permission_disabled_mode_and_stale_ids(seeded_client, monkeypatch):
    ids = _users(seeded_client)
    record = _create(seeded_client, ids["submitter"])
    assert (
        seeded_client.post(
            "/api/demo/reset", headers=headers(ids["submitter"])
        ).status_code
        == 403
    )
    monkeypatch.setenv("DEMO_MODE", "false")
    assert (
        seeded_client.post(
            "/api/demo/reset", headers=headers(ids["reviewer_a"])
        ).status_code
        == 403
    )
    monkeypatch.setenv("DEMO_MODE", "true")
    assert (
        seeded_client.post(
            "/api/demo/reset", headers=headers(ids["reviewer_a"])
        ).status_code
        == 200
    )
    assert (
        seeded_client.get(
            "/api/submissions", headers=headers(ids["reviewer_a"])
        ).status_code
        == 401
    )
    fresh = _users(seeded_client)
    assert (
        seeded_client.get(
            f"/api/submissions/{record['id']}", headers=headers(fresh["reviewer_a"])
        ).status_code
        == 404
    )


def test_notifications_are_durable_and_role_scoped(seeded_client: TestClient) -> None:
    ids = _users(seeded_client)
    created = _create(
        seeded_client,
        ids["submitter"],
        title="Notify Me Loan",
        copy_text="Subject to credit approval. ClearPath may compensate this partner.",
        channel="AFFILIATE",
        partner="NotifyPartner",
        product="PERSONAL_LOAN",
    )
    assignee = created["assigned_reviewer_id"]
    assert assignee in {ids["reviewer_a"], ids["reviewer_b"]}

    reviewer_feed = seeded_client.get(
        "/api/notifications", headers=headers(assignee)
    )
    assert reviewer_feed.status_code == 200
    reviewer_notes = reviewer_feed.json()["notifications"]
    assert reviewer_feed.json()["unread_count"] >= 1
    assert any(
        n["submission_id"] == created["id"]
        and n["event_type"] in {"AUTO_ASSIGNED", "ASSIGNED"}
        and n["unread"]
        for n in reviewer_notes
    )

    submitter_feed = seeded_client.get(
        "/api/notifications", headers=headers(ids["submitter"])
    )
    assert submitter_feed.status_code == 200
    assert any(
        n["submission_id"] == created["id"] and n["unread"]
        for n in submitter_feed.json()["notifications"]
    )

    other = ids["reviewer_b"] if assignee == ids["reviewer_a"] else ids["reviewer_a"]
    other_feed = seeded_client.get("/api/notifications", headers=headers(other))
    assert other_feed.status_code == 200
    assert all(
        n["submission_id"] != created["id"]
        or n["event_type"] not in {"AUTO_ASSIGNED", "ASSIGNED"}
        for n in other_feed.json()["notifications"]
    )

    target = next(n for n in reviewer_notes if n["submission_id"] == created["id"])
    read = seeded_client.post(
        f"/api/notifications/{target['id']}/read", headers=headers(assignee)
    )
    assert read.status_code == 200
    assert read.json()["unread_count"] == reviewer_feed.json()["unread_count"] - 1

    # Read state is server-side: a fresh GET still shows read_at set.
    again = seeded_client.get("/api/notifications", headers=headers(assignee))
    updated = next(n for n in again.json()["notifications"] if n["id"] == target["id"])
    assert updated["unread"] is False
    assert updated["read_at"]

    # Mark-all clears remaining unread for this persona only.
    assert (
        seeded_client.post(
            "/api/notifications/read-all", headers=headers(assignee)
        ).status_code
        == 200
    )
    cleared = seeded_client.get("/api/notifications", headers=headers(assignee))
    assert cleared.json()["unread_count"] == 0
    submitter_still = seeded_client.get(
        "/api/notifications", headers=headers(ids["submitter"])
    )
    assert submitter_still.json()["unread_count"] >= 1


def test_notifications_require_identity(seeded_client: TestClient) -> None:
    assert seeded_client.get("/api/notifications").status_code == 401


# --- S1: policy administration -------------------------------------------------

def _policy_admin(seeded_client: TestClient, temp_db: Path) -> str:
    """Return the seeded user holding the manage_policies capability."""
    conn = db.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT user_id FROM permissions WHERE capability = 'manage_policies' LIMIT 1"
        ).fetchone()
        return str(row["user_id"]) if row else None
    finally:
        conn.close()


def _submitter_id(seeded_client: TestClient, temp_db: Path) -> str:
    """Return a seeded submitter id (no manage_policies)."""
    conn = db.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE role = 'SUBMITTER' LIMIT 1"
        ).fetchone()
        return str(row["id"]) if row else None
    finally:
        conn.close()


def test_policies_require_identity(seeded_client: TestClient) -> None:
    assert seeded_client.get("/api/policies").status_code == 401


def test_policies_overview_as_admin(seeded_client: TestClient, temp_db: Path) -> None:
    admin = _policy_admin(seeded_client, temp_db)
    assert admin is not None
    resp = seeded_client.get("/api/policies", headers={"X-Demo-User-Id": admin})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["active_version"] == 1
    assert body["active"]["version"] == 1
    assert len(body["draft"]) == 5


def test_draft_save_denied_for_submitter(
    seeded_client: TestClient, temp_db: Path,
) -> None:
    submitter = _submitter_id(seeded_client, temp_db)
    resp = seeded_client.put(
        "/api/policies/draft",
        headers={"X-Demo-User-Id": submitter},
        json={
            "rules": [{"rule_key": "R1", "title": "T", "instructions": "I", "kind": "semantic", "enabled": 1}],
            "expected_draft_version": 0,
        },
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "CAPABILITY_REQUIRED"


def test_draft_save_invalid_rules_rejected(
    seeded_client: TestClient, temp_db: Path,
) -> None:
    admin = _policy_admin(seeded_client, temp_db)
    assert admin is not None
    resp = seeded_client.put(
        "/api/policies/draft",
        headers={"X-Demo-User-Id": admin},
        json={
            "rules": [{"rule_key": "R1", "title": "T", "instructions": "I", "kind": "unknown_kind", "enabled": 1}],
            "expected_draft_version": 0,
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "INVALID_RULE"


def test_draft_save_stale_version_conflicts(
    seeded_client: TestClient, temp_db: Path,
) -> None:
    admin = _policy_admin(seeded_client, temp_db)
    assert admin is not None
    h = {"X-Demo-User-Id": admin}
    rules = [{"rule_key": "R1", "title": "T", "instructions": "I", "kind": "semantic", "enabled": 1}]

    # Advance the draft once (draft v0 -> v1), then save claiming v0 -> stale.
    seeded_client.put("/api/policies/draft", headers=h,
                      json={"rules": rules, "expected_draft_version": 0})
    stale = seeded_client.put(
        "/api/policies/draft", headers=h,
        json={"rules": rules, "expected_draft_version": 0},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "STALE_DRAFT"


def test_draft_save_and_publish_happy_path(
    seeded_client: TestClient, temp_db: Path,
) -> None:
    admin = _policy_admin(seeded_client, temp_db)
    assert admin is not None
    h = {"X-Demo-User-Id": admin}
    rules = [{"rule_key": "R1", "title": "T", "instructions": "I", "kind": "semantic", "enabled": 1}]

    saved = seeded_client.put(
        "/api/policies/draft", headers=h,
        json={"rules": rules, "expected_draft_version": 0},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_version"] == 1

    published = seeded_client.post(
        "/api/policies/publish", headers=h,
        json={"expected_draft_version": 1, "expected_active_version": 1},
    )
    assert published.status_code == 200, published.text
    assert published.json()["version"] == 2

    overview = seeded_client.get("/api/policies", headers=h).json()
    assert overview["state"]["active_version"] == 2
    assert overview["active"]["version"] == 2
    assert [r["rule_key"] for r in overview["active"]["rules"]] == ["R1"]


def test_publish_stale_draft_conflicts(
    seeded_client: TestClient, temp_db: Path,
) -> None:
    admin = _policy_admin(seeded_client, temp_db)
    assert admin is not None
    h = {"X-Demo-User-Id": admin}
    rules = [{"rule_key": "R1", "title": "T", "instructions": "I", "kind": "semantic", "enabled": 1}]

    # Advance the draft, then publish once (active v1 -> v2).
    seeded_client.put("/api/policies/draft", headers=h,
                      json={"rules": rules, "expected_draft_version": 0})
    first = seeded_client.post(
        "/api/policies/publish", headers=h,
        json={"expected_draft_version": 1, "expected_active_version": 1},
    )
    assert first.status_code == 200, first.text

    # A second publish claiming active is still v1 must conflict (STALE_PUBLISH).
    resp = seeded_client.post(
        "/api/policies/publish", headers=h,
        json={"expected_draft_version": 1, "expected_active_version": 1},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "STALE_PUBLISH"


def test_snapshot_detail_and_audit(
    seeded_client: TestClient, temp_db: Path,
) -> None:
    admin = _policy_admin(seeded_client, temp_db)
    assert admin is not None
    h = {"X-Demo-User-Id": admin}

    detail = seeded_client.get("/api/policies/snapshots/1", headers=h)
    assert detail.status_code == 200
    assert detail.json()["version"] == 1

    missing = seeded_client.get("/api/policies/snapshots/99", headers=h)
    assert missing.status_code == 404

    audit = seeded_client.get("/api/policies/audit", headers=h)
    assert audit.status_code == 200
    assert any(e["event_type"] == "PUBLISHED" and e["snapshot_version"] == 1 for e in audit.json()["events"])

