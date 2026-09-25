"""K4 acceptance tests for the demo policy preflight (clearpath/preflight.py).

The frozen demo policy (plan §6):
- CLAIM_001: any case-insensitive variant of "pre-approved" (forbidden).
- CLAIM_002: any case-insensitive variant of "guaranteed approval" (forbidden).
- DISC_001: personal-loan and credit-card copy must carry the exact credit
  approval disclosure "Subject to credit approval."
- DISC_002: mortgage-prequalification copy must carry the exact disclosure
  "Prequalification is not a commitment to lend."
- DISC_003: affiliate-channel copy must carry the exact partner disclosure
  "ClearPath may compensate this partner."

Offsets are zero-based, end-exclusive Unicode code points into the original
copy (not UTF-16 units), so emoji in copy never shift highlight spans.
"""

from clearpath.models import Channel, Product
from clearpath.preflight import (
    POLICY_VERSION,
    run_preflight,
    scan_copy,
)

# The seeded hero campaign CP-8904 (plan §8 walkthrough step 2).
HERO_COPY = (
    "You're pre-approved for ClearRewards. "
    "Explore rewards for everyday purchases."
)
HERO_PRODUCT = Product.CREDIT_CARD
HERO_CHANNEL = Channel.SOCIAL

# The corrected revision (plan §8 walkthrough step 5).
CORRECTED_COPY = (
    "You may be pre-qualified for ClearRewards. "
    "Explore rewards for everyday purchases. "
    "Subject to credit approval."
)


def _rule_ids(preflight: dict) -> set[str]:
    return {f["rule_id"] for f in preflight["findings"]}


# --- Hero v1: exactly the two expected rule IDs ------------------------------

def test_hero_v1_returns_exactly_claim001_and_disc001() -> None:
    result = run_preflight(HERO_PRODUCT, HERO_CHANNEL, HERO_COPY)
    assert result["passed"] is False
    assert _rule_ids(result) == {"CLAIM_001", "DISC_001"}
    # Exactly two findings, one per rule.
    assert len(result["findings"]) == 2
    for finding in result["findings"]:
        assert finding["severity"] == "BLOCKING"
        assert finding["rule_id"] in ("CLAIM_001", "DISC_001")


def test_hero_v1_policy_version_is_frozen() -> None:
    result = run_preflight(HERO_PRODUCT, HERO_CHANNEL, HERO_COPY)
    assert result["policy_version"] == POLICY_VERSION


# --- Corrected v2: passing copy ----------------------------------------------

def test_corrected_v2_passes() -> None:
    result = run_preflight(HERO_PRODUCT, HERO_CHANNEL, CORRECTED_COPY)
    assert result["passed"] is True
    assert result["findings"] == []


# --- Claim detection variants -------------------------------------------------

def test_claim001_matches_case_insensitive_and_spacing_variants() -> None:
    cases = [
        "PRE-APPROVED loans.",
        "Pre Approved loans.",
        "pre--approved loans.",
        "pre approved loans.",
        "PRE-APPROVED loans.",
        "pre-approved loans.",
    ]
    for copy in cases:
        findings = scan_copy(Product.CREDIT_CARD, Channel.EMAIL, copy)
        ids = {f["rule_id"] for f in findings}
        assert "CLAIM_001" in ids, f"CLAIM_001 not found in {copy!r}"


def test_claim002_matches_guaranteed_approval_variants() -> None:
    cases = [
        "guaranteed approval today.",
        "GUARANTEED APPROVAL today.",
        "guaranteed approval!",
    ]
    for copy in cases:
        findings = scan_copy(Product.CREDIT_CARD, Channel.EMAIL, copy)
        ids = {f["rule_id"] for f in findings}
        assert "CLAIM_002" in ids, f"CLAIM_002 not found in {copy!r}"


def test_benign_prequalified_copy_does_not_trigger_claim001() -> None:
    # "pre-qualified" is permitted; only "pre-approved" is forbidden.
    copy = "You may be pre-qualified for ClearRewards."
    findings = scan_copy(Product.CREDIT_CARD, Channel.SOCIAL, copy)
    assert "CLAIM_001" not in {f["rule_id"] for f in findings}


# --- Disclosure scoping --------------------------------------------------------

def test_disc001_required_for_personal_loan_and_credit_card() -> None:
    copy = "Get pre-approved for a loan today."
    for product in (Product.PERSONAL_LOAN, Product.CREDIT_CARD):
        ids = {f["rule_id"] for f in scan_copy(product, Channel.EMAIL, copy)}
        assert "DISC_001" in ids, f"DISC_001 missing for {product.value}"


def test_disc001_not_required_for_mortgage() -> None:
    copy = "Get pre-approved for your mortgage today."
    ids = {f["rule_id"] for f in scan_copy(Product.MORTGAGE_PREQUALIFICATION, Channel.EMAIL, copy)}
    assert "DISC_001" not in ids


def test_disc002_required_for_mortgage_only() -> None:
    copy = "Get pre-approved for a loan today."
    assert "DISC_002" in {
        f["rule_id"] for f in scan_copy(Product.MORTGAGE_PREQUALIFICATION, Channel.EMAIL, copy)
    }
    assert "DISC_002" not in {
        f["rule_id"] for f in scan_copy(Product.CREDIT_CARD, Channel.EMAIL, copy)
    }


def test_disc003_required_for_affiliate_channel_only() -> None:
    copy = "Get pre-approved for a loan today."
    assert "DISC_003" in {
        f["rule_id"] for f in scan_copy(Product.PERSONAL_LOAN, Channel.AFFILIATE, copy)
    }
    assert "DISC_003" not in {
        f["rule_id"] for f in scan_copy(Product.PERSONAL_LOAN, Channel.EMAIL, copy)
    }


def test_corrected_copy_satisfies_all_disclosures_for_hero_campaign() -> None:
    # The corrected revision carries the DISC_001 disclosure; with no forbidden
    # claim it must pass for the credit-card/social hero campaign.
    result = run_preflight(HERO_PRODUCT, HERO_CHANNEL, CORRECTED_COPY)
    assert result["passed"] is True


# --- Emoji offset semantics ----------------------------------------------------

def test_finding_offsets_are_unicode_code_points() -> None:
    # Copy with an emoji *before* the forbidden phrase. Finding offsets must be
    # Unicode code points (like JS Array.from semantics), so slicing the
    # code-point array at the finding offsets recovers the exact matched text.
    copy = "\U0001F680 Get pre-approved for a loan today."
    findings = scan_copy(Product.CREDIT_CARD, Channel.EMAIL, copy)
    claim = next(f for f in findings if f["rule_id"] == "CLAIM_001")
    start, end = claim["start"], claim["end"]
    # Offsets are Unicode code points (one element per code point).
    codepoints = list(copy)  # one element per Unicode code point
    phrase = "".join(codepoints[start:end])
    assert phrase == "pre-approved"
    # The finding offset points at the phrase in code-point space.
    assert start == copy.index("pre-approved")
    assert end == start + len("pre-approved")


def test_finding_matched_text_is_exact() -> None:
    copy = "PRE-APPROVED loans."
    findings = scan_copy(Product.CREDIT_CARD, Channel.EMAIL, copy)
    claim = next(f for f in findings if f["rule_id"] == "CLAIM_001")
    assert claim["matched_text"] == "PRE-APPROVED"
    assert copy[claim["start"]:claim["end"]] == "PRE-APPROVED"
