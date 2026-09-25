"""Demo policy constants and scanner (K4, §6).

Exact fictional ClearPath demo policies, frozen as `clearpath-demo-v1`.
The scanner assists review; passing does not approve a request. Findings
carry zero-based, end-exclusive Unicode code-point offsets into the original
copy, preserving the original matching substring for forbidden claims.
Missing disclosures have matched_text/start/end set to null.
"""

from __future__ import annotations

import re

from clearpath.models import Channel, Product, Severity

POLICY_VERSION = "clearpath-demo-v1"

# Rule table: (id, scope products, scope channels, kind, severity, title, message)
# kind "forbid" -> detection regex; kind "disclosure" -> required literal text.
# Scope None means "all products/channels".

DISCLOSURE_TEXT: dict[str, str] = {
    "DISC_001": "Subject to credit approval.",
    "DISC_002": "Prequalification is not a commitment to lend.",
    "DISC_003": "ClearPath may compensate this partner.",
}

# Regexes for forbidden claims. Word boundaries use ASCII \b; the demo copy is
# plain marketing text, so \b is sufficient and keeps matches predictable.
FORBID_REGEX: dict[str, str] = {
    "CLAIM_001": r"\bpre[\s-]*approved\b",
    "CLAIM_002": r"\bguaranteed\s+approval\b",
}

# (rule_id, products, channels, title, message). Products/channels are None
# for "all". Disclosure rules are scoped per §6.
_RULES: list[tuple[str, set[Product] | None, set[Channel] | None, str, str]] = [
    ("CLAIM_001", None, None, "Restricted approval claim",
     "Demo policy requires pre-qualified; confirm offer accuracy."),
    ("CLAIM_002", None, None, "Guaranteed approval claim",
     "Remove the guarantee; approval remains subject to review."),
    ("DISC_001", {Product.PERSONAL_LOAN, Product.CREDIT_CARD}, None,
     "Missing credit approval disclosure",
     "Add the exact demo disclosure: Subject to credit approval."),
    ("DISC_002", {Product.MORTGAGE_PREQUALIFICATION}, None,
     "Missing mortgage prequalification disclosure",
     "Add the exact demo disclosure: Prequalification is not a commitment to lend."),
    ("DISC_003", None, {Channel.AFFILIATE},
     "Missing partner disclosure",
     "Add the exact demo partner disclosure: ClearPath may compensate this partner."),
]


def _normalize_for_disclosure(text: str) -> str:
    """Casefold and collapse whitespace for disclosure presence checks."""
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _rule_applies(rule, product: Product, channel: Channel) -> bool:
    if rule[1] is not None and product not in rule[1]:
        return False
    if rule[2] is not None and channel not in rule[2]:
        return False
    return True


def _codepoint_offset(text: str, utf16_index: int) -> int:
    """Convert a UTF-16 string index to a Unicode code-point offset.

    Python string indexing is UTF-16 code-unit based, but the demo policy
    requires finding offsets to be Unicode code points so the JS frontend can
    slice highlights with Array.from(copy) semantics (emoji never shift spans).
    """
    return sum(1 for char in text[:utf16_index])


def scan_copy(
    product: Product, channel: Channel, copy_text: str
) -> list[dict]:
    """Run the frozen demo policy against pasted copy.

    Returns findings as dicts: rule_id, severity, title, message, matched_text,
    start, end. For forbidden claims every nonoverlapping occurrence is
    returned; offsets are zero-based, end-exclusive Unicode code points into the
    original copy. Missing disclosures return matched_text/start/end = None.
    """
    findings: list[dict] = []
    for rule_id, products, channels, title, message in _RULES:
        if not _rule_applies((rule_id, products, channels, title, message), product, channel):
            continue

        if rule_id.startswith("CLAIM"):
            regex = re.compile(FORBID_REGEX[rule_id], re.IGNORECASE)
            for match in regex.finditer(copy_text):
                matched = match.group(0)
                findings.append({
                    "rule_id": rule_id,
                    "severity": Severity.BLOCKING.value,
                    "title": title,
                    "matched_text": matched,
                    "start": _codepoint_offset(copy_text, match.start()),
                    "end": _codepoint_offset(copy_text, match.end()),
                    "message": message,
                })
        elif rule_id.startswith("DISC"):
            normalized = _normalize_for_disclosure(copy_text)
            if DISCLOSURE_TEXT[rule_id].casefold() not in normalized:
                findings.append({
                    "rule_id": rule_id,
                    "severity": Severity.BLOCKING.value,
                    "title": title,
                    "matched_text": None,
                    "start": None,
                    "end": None,
                    "message": message,
                })
    return findings


def run_preflight(
    product: Product, channel: Channel, copy_text: str
) -> dict:
    """Return the §6 preflight envelope: policy_version, passed, findings."""
    findings = scan_copy(product, channel, copy_text)
    return {
        "policy_version": POLICY_VERSION,
        "passed": not findings,
        "findings": findings,
    }


__all__ = [
    "POLICY_VERSION", "DISCLOSURE_TEXT", "FORBID_REGEX", "scan_copy",
    "run_preflight",
]
