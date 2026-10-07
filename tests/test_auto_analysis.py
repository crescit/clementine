"""Auto-analysis on submission: scheduled after create/resubmit, never blocks
or fails the submission, and is off unless semantic mode is on."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from clearpath import auto_analysis, db, review

from test_api import headers
from test_review import FakeProvider

FINDING = {
    "findings": [
        {
            "rule_key": "CLAIM_002",
            "evidence_quote": "sure thing",
            "explanation": "Implies guaranteed approval.",
            "suggested_revision": "Remove it.",
            "occurrence": 1,
        }
    ]
}


def _ids(client):
    users = client.get("/api/users").json()["users"]
    submitter = next(u["id"] for u in users if u["role"] == "SUBMITTER")
    return submitter


def _payload(copy="Your approval is a sure thing. Subject to credit approval."):
    return {
        "title": "Auto test",
        "channel": "WEBSITE",
        "product": "PERSONAL_LOAN",
        "target_launch_date": "2026-10-30",
        "copy_text": copy,
    }


def _runs(submission_id):
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT status, actor_id, content_version FROM analysis_runs "
            "WHERE submission_id = ?",
            (submission_id,),
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture
def semantic(monkeypatch):
    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "true")
    provider = FakeProvider(response=json.dumps(FINDING))
    monkeypatch.setattr(review, "build_provider", lambda: provider)
    return provider


def test_submission_is_analyzed_automatically(seeded_client: TestClient, semantic):
    submitter = _ids(seeded_client)
    resp = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter))
    assert resp.status_code == 201
    runs = _runs(resp.json()["id"])
    assert [r["status"] for r in runs] == ["SUCCESS"]
    assert runs[0]["actor_id"] == submitter
    assert len(semantic.calls) == 1


def test_detail_reports_the_automatic_run(seeded_client: TestClient, semantic):
    submitter = _ids(seeded_client)
    sid = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter)).json()["id"]
    review_state = seeded_client.get(f"/api/submissions/{sid}", headers=headers(submitter)).json()["review"]
    assert review_state["run"]["findings"][0]["rule_key"] == "CLAIM_002"
    assert review_state["auto_pending"] is False


def test_resubmit_is_analyzed_again(seeded_client: TestClient, semantic):
    submitter = _ids(seeded_client)
    created = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter)).json()
    sid = created["id"]
    reviewer = created["assigned_reviewer_id"]
    seeded_client.post(
        f"/api/submissions/{sid}/request-changes",
        json={"comment": "fix", "expected_record_version": created["record_version"]},
        headers=headers(reviewer),
    )
    detail = seeded_client.get(f"/api/submissions/{sid}", headers=headers(submitter)).json()
    resp = seeded_client.post(
        f"/api/submissions/{sid}/resubmit",
        json={"copy_text": "Subject to credit approval.", "expected_record_version": detail["record_version"]},
        headers=headers(submitter),
    )
    assert resp.status_code == 200
    assert sorted(r["content_version"] for r in _runs(sid)) == [1, 2]


def test_provider_failure_never_fails_the_submission(seeded_client: TestClient, monkeypatch):
    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "true")
    monkeypatch.setattr(review, "build_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    submitter = _ids(seeded_client)
    resp = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter))
    assert resp.status_code == 201
    assert _runs(resp.json()["id"]) == []


def test_off_when_semantic_mode_off(seeded_client: TestClient, monkeypatch):
    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "false")
    provider = FakeProvider(response=json.dumps(FINDING))
    monkeypatch.setattr(review, "build_provider", lambda: provider)
    submitter = _ids(seeded_client)
    resp = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter))
    assert resp.status_code == 201
    assert provider.calls == []


def test_can_be_disabled_independently(seeded_client: TestClient, semantic, monkeypatch):
    monkeypatch.setenv("CLEARPATH_AUTO_ANALYZE", "false")
    submitter = _ids(seeded_client)
    resp = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter))
    assert resp.status_code == 201
    assert semantic.calls == []


def test_trigger_policy_is_pluggable():
    assert auto_analysis.should_run(auto_analysis.TRIGGER_SUBMITTED) in (True, False)
    assert {auto_analysis.TRIGGER_SUBMITTED, auto_analysis.TRIGGER_RESUBMITTED} <= auto_analysis.ENABLED_TRIGGERS


def test_unreachable_model_still_returns_201_and_clears_pending(seeded_client: TestClient, monkeypatch):
    from clearpath.inference import CONNECTION_ERROR, InferenceError

    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "true")
    down = FakeProvider(error=InferenceError(CONNECTION_ERROR, "service unavailable"))
    monkeypatch.setattr(review, "build_provider", lambda: down)
    submitter = _ids(seeded_client)
    resp = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter))
    assert resp.status_code == 201
    sid = resp.json()["id"]
    assert not auto_analysis.is_pending(sid, 1)
    state = seeded_client.get(f"/api/submissions/{sid}", headers=headers(submitter)).json()["review"]
    assert state["auto_pending"] is False
    assert all(r["status"] == "FAILED" for r in _runs(sid))


def test_queue_flags_submissions_being_analyzed(seeded_client: TestClient, semantic):
    submitter = _ids(seeded_client)
    sid = seeded_client.post("/api/submissions", json=_payload(), headers=headers(submitter)).json()["id"]

    def flags():
        items = seeded_client.get("/api/submissions", headers=headers(submitter)).json()["submissions"]
        return {i["id"]: i["analyzing"] for i in items}

    assert flags()[sid] is False  # finished (background task ran inline)
    auto_analysis._pending[(sid, 1)] = "2026-10-07T00:00:00+00:00"
    try:
        assert flags()[sid] is True
    finally:
        auto_analysis._pending.pop((sid, 1), None)
