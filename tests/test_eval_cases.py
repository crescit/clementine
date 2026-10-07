"""S6 acceptance tests for the labeled evaluation case set (eval/cases.json).

Every case's `deterministic_expected` must equal the actual output of
clearpath.preflight.scan_copy (sorted rule_key list). The suite enforces the
card contract: >=30 cases, >=10 held-out (each frozen with an ISO
heldout_frozen_at), the 12 frozen seed cases stay "dev", each required
category has >=3 cases, and both VIOLATION and COMPLIANT appear >=10 times.
"""

from __future__ import annotations

import json
from pathlib import Path

from clearpath.models import Channel, Product
from clearpath.preflight import scan_copy

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "cases.json"

REQUIRED_CATEGORIES = {
    "paraphrase",
    "compliant_qualifier_or_negation",
    "product_channel_scope",
    "disclosure",
    "instruction_attack",
    "unicode",
}

# The 12 frozen seed cases from docs/labeled_cases.md must remain in "dev".
SEED_IDS = {
    "literal_claim_001",
    "paraphrase_claim_001",
    "compliant_negation_claim_001",
    "literal_claim_002",
    "paraphrase_claim_002",
    "compliant_disclosure_001",
    "missing_disclosure_001_scoped",
    "missing_disclosure_002_scoped",
    "missing_disclosure_003_scoped",
    "instruction_attack",
    "unicode_repeated_evidence",
    "compliant_counterexample",
}


def _load_cases() -> list[dict]:
    data = json.loads(CASES_PATH.read_text())
    return data["cases"]


def test_at_least_thirty_cases() -> None:
    cases = _load_cases()
    assert len(cases) >= 30


def test_at_least_ten_held_out_cases_with_frozen_date() -> None:
    held_out = [c for c in _load_cases() if c["split"] == "heldout"]
    assert len(held_out) >= 10
    for case in held_out:
        assert case["heldout_frozen_at"], f"{case['id']} missing heldout_frozen_at"
        # ISO date-only (YYYY-MM-DD); the datetime parser rejects anything else.
        import datetime

        datetime.date.fromisoformat(case["heldout_frozen_at"])


def test_seed_cases_stay_dev() -> None:
    by_split = {}
    for case in _load_cases():
        by_split.setdefault(case["split"], []).append(case["id"])
    dev_ids = set(by_split["dev"])
    assert SEED_IDS.issubset(dev_ids), "all frozen seed cases must remain in dev"


def test_unique_ids() -> None:
    ids = [c["id"] for c in _load_cases()]
    assert len(ids) == len(set(ids)), "case ids must be unique"


def test_valid_enums() -> None:
    valid_products = {p.value for p in Product}
    valid_channels = {c.value for c in Channel}
    for case in _load_cases():
        assert case["split"] in ("dev", "heldout"), case["id"]
        assert case["category"] in REQUIRED_CATEGORIES, case["id"]
        assert case["product"] in valid_products, case["id"]
        assert case["channel"] in valid_channels, case["id"]
        assert case["semantic_expected"] in ("VIOLATION", "COMPLIANT"), case["id"]
        assert isinstance(case["deterministic_expected"], list), case["id"]
        assert isinstance(case["semantic_rules_expected"], list), case["id"]


def test_each_category_has_at_least_three_cases() -> None:
    counts: dict[str, int] = {}
    for case in _load_cases():
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    for category in REQUIRED_CATEGORIES:
        assert counts.get(category, 0) >= 3, f"category {category} needs >=3 cases"


def test_mixes_violation_and_compliant() -> None:
    counts = {"VIOLATION": 0, "COMPLIANT": 0}
    for case in _load_cases():
        counts[case["semantic_expected"]] += 1
    assert counts["VIOLATION"] >= 10
    assert counts["COMPLIANT"] >= 10


def test_deterministic_expected_matches_scan_copy_for_every_case() -> None:
    for case in _load_cases():
        product = Product(case["product"])
        channel = Channel(case["channel"])
        actual = sorted(f["rule_id"] for f in scan_copy(product, channel, case["copy"]))
        assert (
            actual == case["deterministic_expected"]
        ), f"{case['id']}: scan_copy={actual!r} expected={case['deterministic_expected']!r}"
