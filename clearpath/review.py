"""Persisted semantic analysis runs, finding dispositions, and manual
exceptions — the S3 integration that makes semantic review part of the
approval gate.

When semantic mode is enabled, approval requires either a current successful
analysis run with every finding dispositioned by the assigned reviewer, or a
recorded manual exception for a failed analysis attempt on the current
content/policy snapshot. Deterministic blocking findings (the frozen baseline
preflight plus the configured exact-disclosure rules in the published
snapshot) always block approval regardless of semantic mode.

Inference never runs while a SQLite write transaction is open: the Analyze
route reads/authorizes and captures identity on a read connection, releases
it, calls the provider, then reopens a short write transaction to recheck
freshness (content version and policy snapshot unchanged) and store the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

import clearpath.db as db
import clearpath.policies as policies_mod

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"

# Canonical error codes surfaced through the API.
STALE_RUN = "STALE_RUN"
NOT_FOUND = "NOT_FOUND"
NOT_ASSIGNED = "NOT_ASSIGNED"
INVALID_DISPOSITION = "INVALID_DISPOSITION"
INVALID_EXCEPTION = "INVALID_EXCEPTION"
SEMANTIC_GATE = "SEMANTIC_GATE"
NOT_A_REVIEWER = "NOT_A_REVIEWER"
NO_ACTIVE_POLICY = "NO_ACTIVE_POLICY"
STALE_CONTENT = "STALE_CONTENT"
STALE_POLICY = "STALE_POLICY"
NO_FAILED_RUN = "NO_FAILED_RUN"

DISPOSITIONS = {"ACKNOWLEDGED", "DISMISSED"}


class ReviewError(Exception):
    """Domain error carrying an API code + HTTP status."""

    def __init__(
        self, code: str, message: str, status: int = 400, details: dict | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def semantic_mode_enabled() -> bool:
    """Semantic gate on/off. Explicit deterministic-only mode is the default;
    the demo (S5) enables semantic review via CLEARPATH_SEMANTIC_MODE."""
    return os.environ.get("CLEARPATH_SEMANTIC_MODE", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def content_hash(copy_text: str, asset_url: str | None = None) -> str:
    """Content identity for freshness checks. Never includes secrets."""
    payload = json.dumps(
        {"copy_text": copy_text, "asset_url": asset_url},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_provider():
    """Factory for the runtime evaluator adapter. Tests monkeypatch this."""
    from clearpath.inference import build_adapter

    return build_adapter()


def active_snapshot(conn):
    """Current published snapshot as a dict with parsed rules, or None."""
    import clearpath.policies as policies_mod

    state = policies_mod.get_state(conn)
    if not state or not state.get("active_snapshot_id"):
        return None
    return policies_mod.get_snapshot(conn, state["active_version"])


def applicable_semantic_rules(rules, product, channel):
    from clearpath import semantic

    return semantic.applicable_rules(rules, product, channel)


def run_inference(copy_text, product, channel, snapshot):
    """Run one semantic analysis against the published snapshot, entirely
    outside any write transaction. Returns the validated SemanticResult."""
    from clearpath import semantic

    rules = applicable_semantic_rules(snapshot["rules"], product, channel)
    input_ = semantic.SemanticInput(
        copy_text=copy_text,
        product=product,
        channel=channel,
        rules=rules,
        snapshot_id=snapshot["id"],
        snapshot_hash=snapshot["policy_hash"],
        snapshot_version=snapshot["version"],
    )
    provider = build_provider()
    return semantic.analyze(input_, provider)


def _finding_payload(findings) -> list[dict]:
    out = []
    for f in findings:
        out.append(
            {
                "finding_id": f.finding_id,
                "rule_key": f.rule_key,
                "severity": f.severity,
                "title": f.title,
                "evidence_quote": f.evidence_quote,
                "explanation": f.explanation,
                "suggested_revision": f.suggested_revision,
                "start": f.start,
                "end": f.end,
                "occurrence": f.occurrence,
            }
        )
    return out


def store_run(
    conn,
    *,
    submission_id,
    content_version,
    content_hash_value,
    product,
    channel,
    snapshot,
    result,
    actor_id,
    now_iso,
) -> dict:
    """Persist one analysis run inside an open write transaction."""
    run_id = str(uuid.uuid4())
    findings_json = json.dumps(_finding_payload(result.findings), separators=(",", ":"))
    conn.execute(
        """INSERT INTO analysis_runs
        (id, submission_id, content_version, content_hash, product, channel,
         snapshot_id, snapshot_version, snapshot_hash, status, findings_json,
         latency_ms, token_usage_json, provider_revision, prompt_version,
         schema_version, actor_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            submission_id,
            content_version,
            content_hash_value,
            product,
            channel,
            snapshot["id"],
            snapshot["version"],
            snapshot["policy_hash"],
            result.status,
            findings_json,
            result.latency_ms,
            json.dumps(result.token_usage or {}, separators=(",", ":")),
            result.provider_revision,
            result.prompt_version,
            result.schema_version,
            actor_id,
            now_iso,
        ),
    )
    return {
        "id": run_id,
        "status": result.status,
        "findings": json.loads(findings_json),
        "failure_code": result.failure_code,
        "failure_message": result.failure_message,
        "latency_ms": result.latency_ms,
        "provider_revision": result.provider_revision,
        "created_at": now_iso,
    }


def current_run(conn, submission_id, content_version, snapshot_id, status):
    """Most recent run matching submission/content/snapshot/status, or None."""
    row = conn.execute(
        """SELECT * FROM analysis_runs
        WHERE submission_id = ? AND content_version = ? AND snapshot_id = ?
          AND status = ?
        ORDER BY created_at DESC LIMIT 1""",
        (submission_id, content_version, snapshot_id, status),
    ).fetchone()
    return dict(row) if row else None


def list_runs(conn, submission_id):
    """All runs for a submission, newest first, as dicts."""
    rows = conn.execute(
        "SELECT * FROM analysis_runs WHERE submission_id = ? ORDER BY created_at DESC, id DESC",
        (submission_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["findings"] = json.loads(d["findings_json"] or "[]")
        d.pop("findings_json", None)
        out.append(d)
    return out


def run_dispositions(conn, run_id):
    rows = conn.execute(
        "SELECT * FROM finding_dispositions WHERE run_id = ? ORDER BY created_at, rowid",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_disposition(
    conn, *, run_id, finding_id, disposition, reason, actor_id, now_iso
) -> None:
    conn.execute(
        """INSERT INTO finding_dispositions
        (run_id, finding_id, disposition, reason, actor_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, finding_id) DO UPDATE SET
          disposition = excluded.disposition,
          reason = excluded.reason,
          actor_id = excluded.actor_id,
          created_at = excluded.created_at""",
        (run_id, finding_id, disposition, reason, actor_id, now_iso),
    )


def manual_exception(conn, submission_id, content_version, snapshot_id, run_id):
    row = conn.execute(
        """SELECT * FROM manual_exceptions
        WHERE submission_id = ? AND content_version = ? AND snapshot_id = ?
          AND run_id = ?
        ORDER BY created_at DESC LIMIT 1""",
        (submission_id, content_version, snapshot_id, run_id),
    ).fetchone()
    return dict(row) if row else None


def record_exception(
    conn, *, submission_id, content_version, snapshot_id, run_id, reason, actor_id, now_iso
) -> str:
    exc_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO manual_exceptions
        (id, submission_id, content_version, snapshot_id, run_id, reason,
         actor_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (exc_id, submission_id, content_version, snapshot_id, run_id, reason, actor_id, now_iso),
    )
    return exc_id


def deterministic_findings(rules, product, channel, copy_text):
    """Exact-disclosure findings from the *published* snapshot rules.

    These are server-enforced and always block approval, independent of
    semantic mode. The frozen baseline preflight stays non-bypassable too.
    """
    findings = []
    normalized = _normalize(copy_text)
    for rule in rules or []:
        if not int(bool(rule.get("enabled", 1))):
            continue
        if rule.get("kind") != "required_disclosure":
            continue
        if rule.get("product") is not None and rule["product"] != product:
            continue
        if rule.get("channel") is not None and rule["channel"] != channel:
            continue
        literal = rule.get("required_literal")
        if not literal:
            continue
        if literal.casefold() not in normalized:
            findings.append(
                {
                    "rule_key": rule.get("rule_key"),
                    "severity": "BLOCKING",
                    "title": rule.get("title") or "Missing required disclosure",
                    "evidence_quote": literal,
                    "start": None,
                    "end": None,
                }
            )
    return findings


def _normalize(text):
    return " ".join((text or "").casefold().split())


def approval_gate(conn, submission_id, content_version, snapshot_id, assigned_reviewer_id):
    """Return (ok, attribution, error). attribution is the semantic evidence
    attached to the approval audit trail when ok."""
    run = current_run(conn, submission_id, content_version, snapshot_id, STATUS_SUCCESS)
    if run:
        findings = json.loads(run["findings_json"] or "[]")
        dispositions = run_dispositions(conn, run["id"])
        disposed = {}
        for d in dispositions:
            if d["actor_id"] != assigned_reviewer_id:
                continue
            disposed[d["finding_id"]] = d["disposition"]
        missing = [f["finding_id"] for f in findings if f["finding_id"] not in disposed]
        if missing:
            return (False, None, {
                "code": SEMANTIC_GATE,
                "message": "Every semantic finding must be dispositioned by the "
                           "assigned reviewer before approval.",
                "details": {"undispositioned": missing},
            })
        return (True, {
            "analysis_run_id": run["id"],
            "dispositions": [
                {"finding_id": d["finding_id"], "disposition": d["disposition"]}
                for d in dispositions if d["actor_id"] == assigned_reviewer_id
            ],
        }, None)

    failed = current_run(conn, submission_id, content_version, snapshot_id, STATUS_FAILED)
    if failed:
        exc = manual_exception(conn, submission_id, content_version, snapshot_id, failed["id"])
        if exc and exc["actor_id"] == assigned_reviewer_id:
            return (True, {
                "exception_run_id": failed["id"],
                "exception_reason": exc["reason"],
                "exception_actor": exc["actor_id"],
            }, None)
        return (False, None, {
            "code": SEMANTIC_GATE,
            "message": "The latest semantic analysis failed; record a manual "
                       "exception (as the assigned reviewer) to proceed, or "
                       "re-run analysis.",
        })

    return (False, None, {
        "code": SEMANTIC_GATE,
        "message": "A current successful semantic analysis with every finding "
                   "dispositioned (or a recorded manual exception for a failed "
                   "attempt) is required before approval.",
    })


# --- Orchestration (S3 API integration) -----------------------------------------
# Inference never runs while a SQLite write transaction is open. analyze_submission
# reads/authorizes/captures on a read connection, releases it, calls the provider,
# then reopens a short write transaction to recheck freshness and store the run.

def _authorize_reviewer(conn, submission_id, actor_id):
    """Only the assigned reviewer may analyze, disposition, or except."""
    actor = conn.execute(
        "SELECT id, role FROM users WHERE id = ?", (actor_id,)
    ).fetchone()
    if actor is None or actor["role"] != "REVIEWER":
        raise ReviewError(
            NOT_A_REVIEWER, "Only a reviewer may perform review actions", 403
        )
    sub = conn.execute(
        "SELECT assigned_reviewer_id FROM submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    if sub is None:
        raise ReviewError(NOT_FOUND, "Submission not found", 404)
    if sub["assigned_reviewer_id"] != actor_id:
        raise ReviewError(
            NOT_ASSIGNED,
            "This submission is not assigned to you",
            403,
            {"assigned_reviewer_id": sub["assigned_reviewer_id"]},
        )


def analyze_submission(db_path, submission_id, actor_id, now_iso, authorize=True):
    """Run one semantic analysis, persisting the run with freshness checks.

    Returns the stored run dict. Raises ReviewError on auth / staleness.
    """
    # Phase 1 — read/authorize/capture on a read connection, then release.
    read = db.connect(db_path)
    try:
        if authorize:
            _authorize_reviewer(read, submission_id, actor_id)
        row = read.execute(
            "SELECT s.current_version, v.copy_text, v.asset_url, s.product, s.channel "
            "FROM submissions s "
            "JOIN submission_versions v ON v.submission_id = s.id "
            "   AND v.version_number = s.current_version "
            "WHERE s.id = ?",
            (submission_id,),
        ).fetchone()
        snapshot = active_snapshot(read)
        if snapshot is None:
            raise ReviewError(
                NO_ACTIVE_POLICY,
                "No active policy snapshot is published; cannot analyze.",
                409,
            )
        content_version = int(row["current_version"])
        content_hash_value = content_hash(row["copy_text"], row["asset_url"])
        copy_text = row["copy_text"]
        asset_url = row["asset_url"]
        product = row["product"]
        channel = row["channel"]
    finally:
        read.close()

    # Phase 2 — inference, entirely outside any write transaction.
    result = run_inference(copy_text, product, channel, snapshot)

    # Phase 3 — short write txn: recheck freshness, store the run.
    write = db.connect(db_path)
    try:
        write.execute("BEGIN IMMEDIATE")
        _recheck_freshness(write, submission_id, content_version, snapshot["id"])
        stored = store_run(
            write,
            submission_id=submission_id,
            content_version=content_version,
            content_hash_value=content_hash_value,
            product=product,
            channel=channel,
            snapshot=snapshot,
            result=result,
            actor_id=actor_id,
            now_iso=now_iso,
        )
        write.commit()
    except Exception:
        write.rollback()
        raise
    finally:
        write.close()
    return stored


def disposition_finding(db_path, submission_id, run_id, finding_id, disposition, reason, actor_id, now_iso):
    """Record one reviewer disposition for a finding on the assigned submission."""
    conn = db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _authorize_reviewer(conn, submission_id, actor_id)
        run = conn.execute(
            "SELECT submission_id, status, findings_json FROM analysis_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run is None or run["submission_id"] != submission_id:
            raise ReviewError(NOT_FOUND, "Analysis run not found for this submission", 404)
        if run["status"] != STATUS_SUCCESS:
            raise ReviewError(
                INVALID_DISPOSITION,
                "Findings can only be dispositioned on a successful analysis run",
                400,
            )
        findings = json.loads(run["findings_json"] or "[]")
        known = {f.get("finding_id") for f in findings}
        if finding_id not in known:
            raise ReviewError(
                INVALID_DISPOSITION,
                "Unknown finding id for this run",
                400,
                {"finding_id": finding_id, "known": sorted(known)},
            )
        record_disposition(
            conn,
            run_id=run_id,
            finding_id=finding_id,
            disposition=disposition,
            reason=reason,
            actor_id=actor_id,
            now_iso=now_iso,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"run_id": run_id, "finding_id": finding_id, "disposition": disposition, "reason": reason, "actor_id": actor_id}


def exception_submission(db_path, submission_id, run_id, reason, actor_id, now_iso):
    """Record an explicit manual-review exception for a failed analysis."""
    conn = db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _authorize_reviewer(conn, submission_id, actor_id)
        run = conn.execute(
            "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None or run["submission_id"] != submission_id:
            raise ReviewError(NOT_FOUND, "Analysis run not found for this submission", 404)
        if run["status"] != STATUS_FAILED:
            raise ReviewError(
                INVALID_EXCEPTION,
                "A manual exception requires a failed analysis run",
                400,
            )
        sub = conn.execute(
            "SELECT current_version FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        snap_state = policies_mod.get_state(conn)
        if (
            int(run["content_version"]) != int(sub["current_version"])
            or run["snapshot_id"] != snap_state.get("active_snapshot_id")
        ):
            raise ReviewError(
                STALE_RUN,
                "This failed run is stale; re-run analysis before excepting",
                409,
            )
        exc_id = record_exception(
            conn,
            submission_id=submission_id,
            content_version=int(run["content_version"]),
            snapshot_id=run["snapshot_id"],
            run_id=run_id,
            reason=reason,
            actor_id=actor_id,
            now_iso=now_iso,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"exception_id": exc_id, "run_id": run_id, "reason": reason, "actor_id": actor_id}


def _recheck_freshness(conn, submission_id, content_version, snapshot_id):
    sub = conn.execute(
        "SELECT current_version FROM submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    if sub is None:
        raise ReviewError(NOT_FOUND, "Submission not found", 404)
    if int(sub["current_version"]) != content_version:
        raise ReviewError(
            STALE_CONTENT,
            "Content changed during analysis; re-run on the current version",
            409,
            {"current_version": int(sub["current_version"]), "expected": content_version},
        )
    active_id = policies_mod.get_state(conn).get("active_snapshot_id")
    if active_id != snapshot_id:
        raise ReviewError(
            STALE_POLICY,
            "The policy snapshot changed during analysis; re-run on the current snapshot",
            409,
            {"active_snapshot_id": active_id},
        )
