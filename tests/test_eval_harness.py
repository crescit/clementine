"""S6b acceptance tests for the deterministic vs hybrid evaluation harness.

Hermetic: uses a fake provider (no network, no model calls). Covers:
- correct confusion counts (TP/FP/FN/TN) on a tiny fixture
- analysis failures counted separately, never as TN
- --split filtering (dev/heldout/all)
- output JSON schema keys
- --limit 0 dry run works without a model
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clearpath.inference import InferenceError, ProviderResponse
from clearpath.semantic import STATUS_FAILED, STATUS_SUCCESS

from scripts.eval_semantic import (
    CaseOutcome,
    build_report,
    build_snapshot,
    deterministic_flag,
    load_cases,
    run_case,
    score_cases,
    score_cases_by_split,
)

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "cases.json"

COMPLIANT_COPY = "You may be pre-qualified. Subject to credit approval."
CLAIM_COPY = "You're pre-approved."


def _case(id, expected, copy, split="dev") -> dict:
    return {
        "id": id,
        "split": split,
        "product": "PERSONAL_LOAN",
        "channel": "EMAIL",
        "copy": copy,
        "semantic_expected": expected,
    }


class CopyAwareProvider:
    """Fake provider that flags CLAIM_001 when the copy mentions pre-approved."""

    def __init__(self, latency_ms=1.0):
        self.latency_ms = latency_ms
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        content = messages[1]["content"]
        # The COPY is the untrusted-data segment after the marker; the rule
        # instructions (which mention 'pre-approved') must not affect scoring.
        copy = content.split("COPY (untrusted data):", 1)[1]
        if "pre-approved" in copy:
            body = {"findings": [{
                "rule_key": "CLAIM_001",
                "evidence_quote": "pre-approved",
                "explanation": "implied guarantee",
                "suggested_revision": "use 'may qualify'",
            }]}
        else:
            body = {"findings": []}
        return ProviderResponse(
            content=json.dumps(body),
            latency_ms=self.latency_ms,
            token_usage={"total_tokens": 5},
            provider_revision="fake-model",
        )


# --- Confusion counts ---------------------------------------------------------

def test_confusion_counts_on_tiny_fixture() -> None:
    snapshot = build_snapshot()
    provider = CopyAwareProvider()
    cases = [
        _case("tp", "VIOLATION", CLAIM_COPY),        # det T, sem finding -> TP
        _case("fp", "COMPLIANT", CLAIM_COPY),        # det T, sem finding -> FP
        _case("fn", "VIOLATION", COMPLIANT_COPY),    # det F, sem empty      -> FN
        _case("tn", "COMPLIANT", COMPLIANT_COPY),    # det F, sem empty      -> TN
    ]
    outcomes = [run_case(c, snapshot, provider) for c in cases]
    metrics = score_cases(outcomes)

    # Deterministic and hybrid both see the same flags here.
    for mode in ("deterministic", "hybrid"):
        m = metrics[mode]
        assert m["tp"] == 1
        assert m["fp"] == 1
        assert m["fn"] == 1
        assert m["tn"] == 1
        assert m["precision"] == 1 / 2
        assert m["recall"] == 1 / 2

    # Hybrid runs all succeeded (none excluded as failures).
    assert metrics["hybrid"]["failures"] == 0
    assert metrics["hybrid"]["total_tokens"] == 20  # 4 cases * 5 tokens
    assert metrics["hybrid"]["latency_max"] == 1.0


def test_deterministic_flag_from_scan_copy() -> None:
    from clearpath.models import Channel, Product

    assert deterministic_flag(Product.PERSONAL_LOAN, Channel.EMAIL, CLAIM_COPY) is True
    assert deterministic_flag(Product.PERSONAL_LOAN, Channel.EMAIL, COMPLIANT_COPY) is False


# --- Failures counted separately, never as TN -------------------------------

def test_failures_counted_separately_not_tn() -> None:
    snapshot = build_snapshot()

    class SelectiveProvider:
        """Fail on compliant copies, succeed on the claim copy."""
        def chat(self, messages):
            copy = messages[1]["content"].split("COPY (untrusted data):", 1)[1]
            if "pre-approved" in copy:
                return ProviderResponse(
                    content='{"findings": []}', latency_ms=1.0,
                    token_usage={"total_tokens": 5}, provider_revision="fake-model")
            raise InferenceError(code="PROVIDER_TIMEOUT", message="timed out")

    provider = SelectiveProvider()
    cases = [
        _case("fail_viol", "VIOLATION", COMPLIANT_COPY),
        _case("fail_comp", "COMPLIANT", COMPLIANT_COPY),
        _case("ok", "VIOLATION", CLAIM_COPY),
    ]
    outcomes = [run_case(c, snapshot, provider) for c in cases]

    assert all(oc.status == STATUS_FAILED for oc in outcomes[:2])
    assert outcomes[0].hybrid is None  # failures never get a hybrid flag
    assert outcomes[1].hybrid is None

    metrics = score_cases(outcomes)
    hyb = metrics["hybrid"]
    # Only the SUCCESS case enters hybrid confusion; the two failures are
    # separate and never counted as TN/TP.
    assert hyb["failures"] == 2
    assert hyb["n"] == 1
    assert hyb["tp"] == 1
    assert hyb["fp"] == 0
    assert hyb["fn"] == 0
    assert hyb["tn"] == 0


def test_invalid_output_is_failure_not_clean() -> None:
    snapshot = build_snapshot()
    provider = CopyAwareProvider()
    # A malformed response must surface as FAILED and count as a failure,
    # not be silently treated as a clean/empty result.
    case = _case("bad", "COMPLIANT", COMPLIANT_COPY)

    class BadProvider:
        def chat(self, messages):
            return ProviderResponse(content="{not json", latency_ms=1.0)

    oc = run_case(case, snapshot, BadProvider())
    assert oc.status == STATUS_FAILED
    assert oc.hybrid is None

    metrics = score_cases([oc])
    assert metrics["hybrid"]["failures"] == 1
    assert metrics["hybrid"]["n"] == 0  # excluded from confusion entirely


# --- Split filtering -----------------------------------------------------------

def test_score_cases_by_split_filters_dev_heldout() -> None:
    snapshot = build_snapshot()
    provider = CopyAwareProvider()
    cases = [
        _case("dev_a", "VIOLATION", CLAIM_COPY, split="dev"),
        _case("held_b", "COMPLIANT", COMPLIANT_COPY, split="heldout"),
    ]
    outcomes = [run_case(c, snapshot, provider) for c in cases]
    by_split = score_cases_by_split(outcomes)

    assert by_split["deterministic"]["dev"]["n"] == 1
    assert by_split["deterministic"]["heldout"]["n"] == 1
    assert by_split["deterministic"]["all"]["n"] == 2

    # dev_a is a hybrid TP; held_b is a hybrid TN.
    assert by_split["hybrid"]["dev"]["tp"] == 1
    assert by_split["hybrid"]["heldout"]["tn"] == 1
    assert by_split["hybrid"]["all"]["tp"] == 1
    assert by_split["hybrid"]["all"]["tn"] == 1


def test_split_filtering_on_case_set() -> None:
    cases = load_cases(CASES_PATH)
    dev = [c for c in cases if c["split"] == "dev"]
    held = [c for c in cases if c["split"] == "heldout"]
    assert len(dev) + len(held) == len(cases)
    assert all(c["split"] in ("dev", "heldout") for c in cases)


# --- Output schema keys --------------------------------------------------------

def test_report_schema_keys() -> None:
    snapshot = build_snapshot()
    cases = [_case("c1", "VIOLATION", CLAIM_COPY)]
    provider = CopyAwareProvider()
    outcomes = [run_case(c, snapshot, provider) for c in cases]
    report = build_report(cases, outcomes, snapshot, "fake-model", "fake-rev", CASES_PATH)

    for key in ("model_id", "prompt_version", "provider_revision", "timestamp",
                "case_set", "snapshot", "metrics", "cases"):
        assert key in report, f"missing output key {key}"
    assert report["model_id"] == "fake-model"
    assert report["provider_revision"] == "fake-rev"
    assert report["prompt_version"] == "clearpath-semantic-v1"
    assert "hash" in report["case_set"]

    row = report["cases"][0]
    for key in ("id", "expected", "deterministic", "hybrid", "status", "rule_keys", "latency_ms"):
        assert key in row, f"missing per-case key {key}"
    assert row["id"] == "c1"
    assert row["expected"] == "VIOLATION"
    assert row["hybrid"] is True
    assert row["status"] == STATUS_SUCCESS
    assert row["rule_keys"] == ["CLAIM_001"]
    assert row["latency_ms"] == 1.0


# --- Dry run without a model ---------------------------------------------------

def test_limit_zero_dry_run_needs_no_model() -> None:
    # run_case with provider=None is the --limit 0 path: deterministic only.
    snapshot = build_snapshot()
    case = _case("c1", "VIOLATION", CLAIM_COPY)
    oc = run_case(case, snapshot, provider=None)
    assert oc.status == "SKIP"
    assert oc.hybrid is None
    assert oc.deterministic is True  # CLAIM_* present


def test_snapshot_is_eval_default() -> None:
    snapshot = build_snapshot()
    assert snapshot["id"] == "eval-default"
    assert snapshot["version"] == 0
    assert snapshot["hash"]
    assert any(r["rule_key"] == "CLAIM_001" for r in snapshot["rules"])
