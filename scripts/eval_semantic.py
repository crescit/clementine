"""S6b — deterministic vs hybrid evaluation harness (hermetic).

Runnable script that scores the deterministic scanner alone against the
deterministic scanner + semantic model, over the frozen case set in
eval/cases.json. No live model calls in tests: the pure scoring functions and
`run_case` are importable and exercised with a fake provider.

CLI: .venv/bin/python scripts/eval_semantic.py [--split dev|heldout|all]
        [--out docs/eval_results.json] [--limit N]

Per case:
- deterministic flagged = any CLAIM_* finding from clearpath.preflight.scan_copy
- hybrid flagged      = deterministic CLAIM_* OR any semantic finding on a
                        SUCCESS result
- positive class      = semantic_expected == "VIOLATION"

Analysis failures (semantic status != SUCCESS) are counted SEPARATELY and
excluded from hybrid TP/FP — never treated as clean. Output JSON carries the
model id, prompt_version, provider_revision, timestamp and case-set hash, and
never prints the API key or base URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from clearpath.models import Channel, Product
from clearpath.preflight import scan_copy
from clearpath.semantic import STATUS_SUCCESS

# --- Snapshot -----------------------------------------------------------------
SNAPSHOT_ID = "eval-default"
SNAPSHOT_VERSION = 0

# --- Case outcome -------------------------------------------------------------


@dataclass
class CaseOutcome:
    id: str
    expected: str  # "VIOLATION" or "COMPLIANT"
    deterministic: bool  # any CLAIM_* finding from scan_copy
    hybrid: bool | None  # None when the semantic analysis failed / was skipped
    status: str  # semantic status: SUCCESS / FAILED / SKIP
    split: str = "all"  # dev / heldout / all
    rule_keys: list[str] = field(default_factory=list)  # semantic finding keys
    latency_ms: float | None = None
    tokens: int | None = None
    provider_revision: str | None = None


# --- Pure scoring -------------------------------------------------------------


def _ratio(num: int, den: int) -> float | None:
    """Precision/recall ratio, null when the denominator is zero."""
    if den <= 0:
        return None
    return num / den


def score_cases(outcomes: list[CaseOutcome]) -> dict:
    """Score a set of outcomes into per-mode confusion metrics (pure).

    deterministic mode: every case participates (scan_copy is deterministic).
    hybrid mode: only SUCCESS runs participate in confusion; FAILED/SKIP runs
    are counted as `failures` and excluded from TP/FP (never clean).
    """
    det = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    hyb = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    failures = 0
    latencies: list[float] = []
    total_tokens = 0

    for oc in outcomes:
        positive = oc.expected == "VIOLATION"

        # Deterministic mode: flag = any CLAIM_* finding.
        if oc.deterministic and positive:
            det["tp"] += 1
        elif oc.deterministic and not positive:
            det["fp"] += 1
        elif not oc.deterministic and positive:
            det["fn"] += 1
        else:
            det["tn"] += 1

        # Hybrid mode: analysis failures never enter the confusion table.
        if oc.status != STATUS_SUCCESS:
            failures += 1
            continue
        flag = oc.hybrid if oc.hybrid is not None else oc.deterministic
        if flag and positive:
            hyb["tp"] += 1
        elif flag and not positive:
            hyb["fp"] += 1
        elif not flag and positive:
            hyb["fn"] += 1
        else:
            hyb["tn"] += 1

        if oc.latency_ms is not None:
            latencies.append(oc.latency_ms)
        if oc.tokens is not None:
            total_tokens += oc.tokens

    def _metrics(c: dict) -> dict:
        n = c["tp"] + c["fp"] + c["fn"] + c["tn"]
        return {
            "tp": c["tp"],
            "fp": c["fp"],
            "fn": c["fn"],
            "tn": c["tn"],
            "n": n,
            "precision": _ratio(c["tp"], c["tp"] + c["fp"]),
            "recall": _ratio(c["tp"], c["tp"] + c["fn"]),
        }

    deterministic = _metrics(det)
    hybrid = _metrics(hyb)
    hybrid["failures"] = failures
    if latencies:
        hybrid["latency_p50"] = statistics.median(latencies)
        hybrid["latency_max"] = max(latencies)
    else:
        hybrid["latency_p50"] = None
        hybrid["latency_max"] = None
    hybrid["total_tokens"] = total_tokens
    return {"deterministic": deterministic, "hybrid": hybrid}


def score_cases_by_split(outcomes: list[CaseOutcome]) -> dict:
    """Score by split (dev / heldout / all), keyed by mode then split."""
    grouped: dict[str, list[CaseOutcome]] = {"dev": [], "heldout": []}
    for oc in outcomes:
        grouped.setdefault(oc.split, []).append(oc)
    grouped["all"] = outcomes

    metrics: dict[str, dict] = {"deterministic": {}, "hybrid": {}}
    for split, group in grouped.items():
        scored = score_cases(group)
        metrics["deterministic"][split] = scored["deterministic"]
        metrics["hybrid"][split] = scored["hybrid"]
    return metrics


# --- Deterministic scan ---------------------------------------------------------


def deterministic_flag(product: Product, channel: Channel, copy_text: str) -> bool:
    """True when scan_copy finds any CLAIM_* (forbidden approval claim)."""
    return any(f["rule_id"].startswith("CLAIM") for f in scan_copy(product, channel, copy_text))


# --- Snapshot / input ---------------------------------------------------------


def build_snapshot():
    """The eval snapshot: canonical default rules (policies.py) with id
    'eval-default', a hash over the canonical rule JSON, and version 0."""
    from clearpath import policies

    rules = policies.BASELINE_RULES
    return {
        "id": SNAPSHOT_ID,
        "hash": policies.policy_hash(rules),
        "version": SNAPSHOT_VERSION,
        "rules": rules,
    }


def build_input(case: dict, snapshot: dict):
    """Build the SemanticInput for one case against the eval-default snapshot."""
    from clearpath import semantic

    rules = semantic.applicable_rules(snapshot["rules"], case["product"], case["channel"])
    return semantic.SemanticInput(
        copy_text=case["copy"],
        product=case["product"],
        channel=case["channel"],
        rules=rules,
        snapshot_id=snapshot["id"],
        snapshot_hash=snapshot["hash"],
        snapshot_version=snapshot["version"],
    )


def run_case(case: dict, snapshot: dict, provider=None) -> CaseOutcome:
    """Score one case. provider is None in dry-run / deterministic-only mode."""
    product = Product(case["product"])
    channel = Channel(case["channel"])
    det = deterministic_flag(product, channel, case["copy"])

    if provider is None:
        return CaseOutcome(
            id=case["id"], expected=case["semantic_expected"], deterministic=det,
            hybrid=None, status="SKIP", split=case["split"], rule_keys=[],
            latency_ms=None, tokens=None, provider_revision=None,
        )

    from clearpath import semantic

    result = semantic.analyze(build_input(case, snapshot), provider)
    if result.status != STATUS_SUCCESS:
        return CaseOutcome(
            id=case["id"], expected=case["semantic_expected"], deterministic=det,
            hybrid=None, status=result.status, split=case["split"], rule_keys=[],
            latency_ms=result.latency_ms, tokens=_total_tokens(result.token_usage),
            provider_revision=result.provider_revision,
        )

    keys = [f.rule_key for f in result.findings]
    return CaseOutcome(
        id=case["id"], expected=case["semantic_expected"], deterministic=det,
        hybrid=det or bool(keys), status=result.status, split=case["split"],
        rule_keys=keys, latency_ms=result.latency_ms,
        tokens=_total_tokens(result.token_usage), provider_revision=result.provider_revision,
    )


def _total_tokens(token_usage: dict | None) -> int | None:
    if not token_usage:
        return None
    total = 0
    for key in ("total_tokens", "prompt_tokens", "completion_tokens"):
        if key in token_usage:
            total += token_usage[key]
    return total


# --- I/O / report ---------------------------------------------------------------


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data["cases"]


def case_set_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(cases: list[dict], outcomes: list[CaseOutcome], snapshot: dict,
                 model_id: str | None, provider_revision: str | None, cases_path: Path) -> dict:
    rows = []
    for oc in outcomes:
        rows.append({
            "id": oc.id,
            "expected": oc.expected,
            "deterministic": oc.deterministic,
            "hybrid": oc.hybrid,
            "status": oc.status,
            "rule_keys": oc.rule_keys,
            "latency_ms": oc.latency_ms,
        })
    return {
        "model_id": model_id,
        "prompt_version": _prompt_version(),
        "provider_revision": provider_revision,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "case_set": {"path": str(cases_path), "hash": case_set_hash(cases_path)},
        "snapshot": {"id": snapshot["id"], "version": snapshot["version"], "hash": snapshot["hash"]},
        "metrics": score_cases_by_split(outcomes),
        "cases": rows,
    }


def _prompt_version() -> str:
    from clearpath import semantic

    return semantic.PROMPT_VERSION


def _split_outcomes(cases: list[dict], snapshot: dict, provider) -> list[CaseOutcome]:
    return [run_case(case, snapshot, provider) for case in cases]


# --- CLI -----------------------------------------------------------------------


def build_provider():
    """Factory for the runtime evaluator adapter; reads server env only."""
    from clearpath import inference as inf

    return inf.build_adapter()


def _model_id(provider) -> str | None:
    try:
        return provider.config.model_id
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic vs hybrid evaluation harness")
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="all")
    parser.add_argument("--out", default=str(Path("docs/eval_results.json")))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    cases_path = repo / "eval" / "cases.json"
    snapshot = build_snapshot()

    cases = load_cases(cases_path)
    if args.split != "all":
        cases = [c for c in cases if c["split"] == args.split]
    if args.limit:
        cases = cases[: args.limit]

    # --limit 0 is a dry run: no model calls (works without a provider).
    provider = None
    model_id = None
    if args.limit != 0:
        provider = build_provider()
        model_id = _model_id(provider)

    outcomes = _split_outcomes(cases, snapshot, provider)
    provider_revision = _provider_revision(outcomes)

    report = build_report(cases, outcomes, snapshot, model_id, provider_revision, cases_path)
    out_path = repo / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    m = report["metrics"]["hybrid"]["all"]
    print(f"wrote {out_path}")
    print(f"cases={len(cases)} hybrid all: tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']} "
          f"failures={m['failures']} precision={m['precision']} recall={m['recall']}")


def _provider_revision(outcomes: list[CaseOutcome]) -> str | None:
    """Surface the first non-null provider_revision from any run."""
    for oc in outcomes:
        if oc.provider_revision is not None:
            return oc.provider_revision
    return None


if __name__ == "__main__":
    main()
