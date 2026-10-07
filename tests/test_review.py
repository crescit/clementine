"""S3 acceptance tests: persisted review and approval integration.

Covers the five S3 acceptance guarantees:
1. Inference occurs outside SQLite write locks (Analyze never calls the
   provider while a write transaction is open).
2. Revisions / policy publication invalidate prior runs for approval.
3. A wrong reviewer cannot disposition, except, or approve.
4. A provider failure cannot appear clean (FAILED, never empty SUCCESS).
5. Frozen-baseline blockers remain non-bypassable even with a successful
   semantic analysis + dispositions.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from clearpath import api, db, review, seed, workflow
from clearpath.inference import InferenceError

# A provider that returns a fixed JSON body (or raises).
class FakeProvider:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return _resp(self.response)


def _resp(content):
    from clearpath.inference import ProviderResponse

    return ProviderResponse(
        content=content,
        latency_ms=12.3,
        token_usage={"prompt_tokens": 10, "completion_tokens": 5},
        provider_revision="fake-model",
    )


def _setup(temp_db, frozen_now):
    """Seed a DB and return (submitter_id, reviewer_a, reviewer_b)."""
    conn = db.connect(temp_db)
    try:
        seed.seed_database(conn, frozen_now)
        users = conn.execute(
            "SELECT id, role FROM users ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    reviewers = [u["id"] for u in users if u["role"] == "REVIEWER"]
    submitter = next(u["id"] for u in users if u["role"] == "SUBMITTER")
    return submitter, reviewers[0], reviewers[1]


def _create_submission(temp_db, frozen_now, submitter, copy_text="Subject to credit approval."):
    conn = db.connect(temp_db)
    try:
        rec = workflow.create_submission(
            conn,
            submitter,
            title="Review test",
            copy_text=copy_text,
            channel="WEBSITE",
            product="PERSONAL_LOAN",
            target_launch_date="2026-10-01",
            now=frozen_now,
        )
    finally:
        conn.close()
    return rec


def _snapshot_id(temp_db):
    conn = db.connect(temp_db)
    try:
        return conn.execute("SELECT id FROM policy_snapshots ORDER BY version LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def _gate(temp_db, submission_id, content_version, snapshot_id, reviewer_id):
    conn = db.connect(temp_db)
    try:
        return review.approval_gate(conn, submission_id, content_version, snapshot_id, reviewer_id)
    finally:
        conn.close()


def _monkeypatch_provider(monkeypatch, provider):
    monkeypatch.setattr(review, "build_provider", lambda: provider)


# --- 1. Inference occurs outside write locks ------------------------------------

def test_analyze_calls_provider_outside_write_lock(temp_db, frozen_now, monkeypatch):
    """BEGIN IMMEDIATE succeeds at inference time, proving no write txn is open."""
    submitter, reviewer_a, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)

    # Capture the DB write-lock state at the moment inference runs.
    original_run = review.run_inference
    lock_state = {}

    def captured_run_inference(copy_text, product, channel, snapshot):
        # A write txn open elsewhere would make BEGIN IMMEDIATE block/fail here.
        conn = db.connect(temp_db)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("COMMIT")
        conn.close()
        lock_state["write_txn_open_at_inference"] = False
        return original_run(copy_text, product, channel, snapshot)

    monkeypatch.setattr(review, "run_inference", captured_run_inference)
    _monkeypatch_provider(monkeypatch, FakeProvider(response='{"findings": []}'))

    result = review.analyze_submission(
        temp_db, rec["id"], reviewer_a, frozen_now
    )
    assert lock_state["write_txn_open_at_inference"] is False
    # Run persisted with a SUCCESS status.
    assert result["status"] == review.STATUS_SUCCESS


# --- 2. Revisions / policy publication invalidate runs --------------------------

def test_revision_invalidates_prior_run_for_approval(temp_db, frozen_now, monkeypatch):
    submitter, reviewer_a, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    _monkeypatch_provider(monkeypatch, FakeProvider(response='{"findings": []}'))
    run = review.analyze_submission(temp_db, rec["id"], reviewer_a, frozen_now)
    assert run["status"] == review.STATUS_SUCCESS

    # Bump the content version (a revision).
    conn = db.connect(temp_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE submissions SET current_version = current_version + 1, "
            "updated_at = ? WHERE id = ?",
            (frozen_now.isoformat().replace("+00:00", "Z"), rec["id"]),
        )
        conn.execute(
            "INSERT INTO submission_versions (id, submission_id, version_number, "
            "asset_url, copy_text, created_by, created_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?)",
            ("v-new", rec["id"], 2, "Subject to credit approval.",
             reviewer_a, frozen_now.isoformat().replace("+00:00", "Z")),
        )
        conn.commit()
    finally:
        conn.close()

    # Approval against the NEW content version must not be authorized by the old run.
    ok, attribution, err = _gate(temp_db, rec["id"], 2, _snapshot_id(temp_db), reviewer_a)
    assert ok is False
    assert err["code"] == review.SEMANTIC_GATE


def test_policy_publication_invalidates_prior_run_for_approval(temp_db, frozen_now, monkeypatch):
    submitter, reviewer_a, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    _monkeypatch_provider(monkeypatch, FakeProvider(response='{"findings": []}'))
    run = review.analyze_submission(temp_db, rec["id"], reviewer_a, frozen_now)
    assert run["status"] == review.STATUS_SUCCESS
    old_snapshot = _snapshot_id(temp_db)

    # Publish a new policy snapshot (bump active version).
    conn = db.connect(temp_db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO policy_snapshots (id, version, label, policy_hash, "
            "rules_json, published_by, published_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("snap-2", 2, "clearpath-demo-v2", "hash2", "[]",
             reviewer_a, frozen_now.isoformat().replace("+00:00", "Z"),
             frozen_now.isoformat().replace("+00:00", "Z")),
        )
        conn.execute(
            "UPDATE policy_state SET active_snapshot_id = ? WHERE id = 1",
            ("snap-2",),
        )
        conn.commit()
    finally:
        conn.close()

    # Approval against the new snapshot must not be authorized by the old run.
    ok, attribution, err = _gate(
        temp_db, rec["id"], 1, "snap-2", reviewer_a
    )
    assert ok is False
    assert err["code"] == review.SEMANTIC_GATE


# --- 3. Wrong reviewer cannot disposition / except / approve --------------------

def test_wrong_reviewer_cannot_disposition(temp_db, frozen_now, monkeypatch):
    submitter, reviewer_a, reviewer_b = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    _monkeypatch_provider(monkeypatch, FakeProvider(response=json.dumps({
        "findings": [{
            "rule_key": "CLAIM_001",
            "evidence_quote": "Subject to credit approval.",
            "explanation": "test",
            "suggested_revision": "fix",
            "occurrence": 1,
        }]
    })))
    run = review.analyze_submission(temp_db, rec["id"], reviewer_a, frozen_now)
    finding = run["findings"][0]

    # reviewer_b (not assigned) cannot disposition the finding.
    with pytest.raises(review.ReviewError):
        review.disposition_finding(
            temp_db, rec["id"], run["id"], finding["finding_id"],
            "ACKNOWLEDGED", "reason", reviewer_b, frozen_now
        )


def test_wrong_reviewer_cannot_approve(temp_db, frozen_now, monkeypatch):
    """Even with a clean current run, only the assigned reviewer may approve."""
    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "true")
    submitter, reviewer_a, reviewer_b = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    _monkeypatch_provider(monkeypatch, FakeProvider(response='{"findings": []}'))
    conn = db.connect(temp_db)
    try:
        assigned = conn.execute(
            "SELECT assigned_reviewer_id FROM submissions WHERE id = ?", (rec["id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    other = reviewer_b if assigned == reviewer_a else reviewer_a
    run = review.analyze_submission(temp_db, rec["id"], assigned, frozen_now)
    assert run["status"] == review.STATUS_SUCCESS

    conn = db.connect(temp_db)
    try:
        with pytest.raises(workflow.ForbiddenError):
            workflow.approve_submission(
                conn, other, rec["id"],
                expected_record_version=rec["record_version"], now=frozen_now,
            )
    finally:
        conn.close()


# --- 4. Provider failure cannot appear clean ------------------------------------

def test_provider_failure_is_failed_not_clean(temp_db, frozen_now, monkeypatch):
    submitter, reviewer_a, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    _monkeypatch_provider(monkeypatch, FakeProvider(
        error=InferenceError("PROVIDER_TIMEOUT", "timed out")
    ))
    result = review.analyze_submission(temp_db, rec["id"], reviewer_a, frozen_now)
    assert result["status"] == review.STATUS_FAILED
    assert result["failure_code"] == "PROVIDER_TIMEOUT"
    # Never an apparently-clean success.
    assert result["status"] != review.STATUS_SUCCESS


def test_invalid_provider_json_is_failed_not_clean(temp_db, frozen_now, monkeypatch):
    submitter, reviewer_a, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    _monkeypatch_provider(monkeypatch, FakeProvider(response="{not valid json"))
    result = review.analyze_submission(temp_db, rec["id"], reviewer_a, frozen_now)
    assert result["status"] == review.STATUS_FAILED
    assert result["failure_code"] == "INVALID_RESPONSE"


# --- 5. Frozen-baseline blockers remain non-bypassable ---------------------------

def test_preflight_blocker_not_bypassed_by_semantic_run(temp_db, frozen_now, monkeypatch):
    submitter, reviewer_a, _ = _setup(temp_db, frozen_now)
    # Copy that violates the frozen baseline preflight ("pre-approved").
    rec = _create_submission(temp_db, frozen_now, submitter, copy_text="You are pre-approved.")
    _monkeypatch_provider(monkeypatch, FakeProvider(response='{"findings": []}'))
    run = review.analyze_submission(temp_db, rec["id"], reviewer_a, frozen_now)
    assert run["status"] == review.STATUS_SUCCESS

    # Approval must still fail the deterministic preflight blocker.
    conn = db.connect(temp_db)
    try:
        with pytest.raises(workflow.PreflightBlockedError):
            workflow.approve_submission(
                conn, reviewer_a, rec["id"],
                expected_record_version=rec["record_version"], now=frozen_now,
            )
    finally:
        conn.close()


# --- 6. Gate enforced end to end when semantic mode is on ------------------------

def _assigned(temp_db, sid):
    conn = db.connect(temp_db)
    try:
        return conn.execute(
            "SELECT assigned_reviewer_id, record_version FROM submissions WHERE id = ?", (sid,)
        ).fetchone()
    finally:
        conn.close()


def _approve(temp_db, actor, sid, record_version, now):
    conn = db.connect(temp_db)
    try:
        return workflow.approve_submission(
            conn, actor, sid, expected_record_version=record_version, now=now
        )
    finally:
        conn.close()


def test_semantic_mode_blocks_approval_until_findings_dispositioned(temp_db, frozen_now, monkeypatch):
    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "true")
    submitter, _, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    reviewer, rv = _assigned(temp_db, rec["id"])

    # No analysis yet -> blocked.
    with pytest.raises(workflow.SemanticGateError):
        _approve(temp_db, reviewer, rec["id"], rv, frozen_now)

    _monkeypatch_provider(monkeypatch, FakeProvider(response=json.dumps({
        "findings": [{
            "rule_key": "CLAIM_001",
            "evidence_quote": "Subject to credit approval.",
            "explanation": "test",
            "suggested_revision": "fix",
            "occurrence": 1,
        }]
    })))
    run = review.analyze_submission(temp_db, rec["id"], reviewer, frozen_now)
    assert run["status"] == review.STATUS_SUCCESS and run["findings"]

    # Finding not dispositioned -> still blocked.
    with pytest.raises(workflow.SemanticGateError):
        _approve(temp_db, reviewer, rec["id"], rv, frozen_now)

    for f in run["findings"]:
        review.disposition_finding(
            temp_db, rec["id"], run["id"], f["finding_id"],
            "ACKNOWLEDGED", "reviewed", reviewer, frozen_now,
        )
    _, rv = _assigned(temp_db, rec["id"])
    result = _approve(temp_db, reviewer, rec["id"], rv, frozen_now)
    assert result is not None


def test_semantic_mode_failed_run_needs_manual_exception(temp_db, frozen_now, monkeypatch):
    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "true")
    submitter, _, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    reviewer, rv = _assigned(temp_db, rec["id"])
    _monkeypatch_provider(monkeypatch, FakeProvider(
        error=InferenceError("PROVIDER_TIMEOUT", "timed out")
    ))
    run = review.analyze_submission(temp_db, rec["id"], reviewer, frozen_now)
    assert run["status"] == review.STATUS_FAILED

    # A failed run never silently unlocks approval.
    with pytest.raises(workflow.SemanticGateError):
        _approve(temp_db, reviewer, rec["id"], rv, frozen_now)

    review.exception_submission(
        temp_db, rec["id"], run["id"], "provider down; manual review done", reviewer, frozen_now
    )
    _, rv = _assigned(temp_db, rec["id"])
    assert _approve(temp_db, reviewer, rec["id"], rv, frozen_now) is not None

def test_review_state_in_submission_detail(temp_db, frozen_now, monkeypatch):
    """S5: the submission detail `review` object tracks the semantic-run lifecycle."""
    monkeypatch.setenv("CLEARPATH_SEMANTIC_MODE", "true")
    submitter, _, _ = _setup(temp_db, frozen_now)
    rec = _create_submission(temp_db, frozen_now, submitter)
    sid = rec["id"]

    conn = db.connect(temp_db)
    try:
        row = api._submission_row(conn, sid)
        state = api._review_state(conn, row)
    finally:
        conn.close()

    # No analysis yet -> enabled, no run, not approvable, no error.
    assert state["enabled"] is True
    assert state["run"] is None
    assert state["approvable"] is False
    assert state["error"] is None
    assert state["stale"] is False

    _monkeypatch_provider(monkeypatch, FakeProvider(response=json.dumps({
        "findings": [{
            "rule_key": "CLAIM_001",
            "evidence_quote": "Subject to credit approval.",
            "explanation": "test",
            "suggested_revision": "fix",
            "occurrence": 1,
        }]
    })))
    run = review.analyze_submission(temp_db, sid, _assigned(temp_db, sid)[0], frozen_now)
    assert run["status"] == review.STATUS_SUCCESS and run["findings"]

    conn = db.connect(temp_db)
    try:
        row = api._submission_row(conn, sid)
        state = api._review_state(conn, row)
    finally:
        conn.close()
    assert state["run"] is not None
    assert state["run"]["id"] == run["id"]
    assert len(state["run"]["findings"]) == len(run["findings"])
    assert all(f["disposition"] is None for f in state["run"]["findings"])
    assert state["approvable"] is False
    assert state["error"] == "Every semantic finding must be dispositioned before approval."

    reviewer, _ = _assigned(temp_db, sid)
    for f in run["findings"]:
        review.disposition_finding(
            temp_db, sid, run["id"], f["finding_id"],
            "ACKNOWLEDGED", "reviewed", reviewer, frozen_now,
        )

    conn = db.connect(temp_db)
    try:
        row = api._submission_row(conn, sid)
        state = api._review_state(conn, row)
    finally:
        conn.close()
    assert all(f["disposition"] is not None for f in state["run"]["findings"])
    assert state["approvable"] is True
    assert state["error"] is None

    # Bump content_version -> the prior run becomes stale.
    conn = db.connect(temp_db)
    try:
        conn.execute("UPDATE submissions SET current_version = current_version + 1 WHERE id = ?", (sid,))
        conn.commit()
        row = api._submission_row(conn, sid)
        state = api._review_state(conn, row)
    finally:
        conn.close()
    assert state["stale"] is True
    assert state["approvable"] is False
    assert state["error"] is not None
