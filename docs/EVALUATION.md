# Semantic Review — Live Evaluation Report

Measured: the hybrid semantic reviewer (deterministic CLAIM_* scanner + live
model analysis) against the frozen labeled case set in `eval/cases.json`.

- Model: `deepseek-v4-flash` (provider_revision `deepseek-v4-flash`)
- Prompt version: `clearpath-semantic-v1`, snapshot `eval-default` (version 0)
- Case-set hash: `561c5de053e3fdc1344fb68ef3323ad2b35b90155337481d4b45f9dfef6ff635`
- Runs: `docs/eval_results_dev.json`, `docs/eval_results_heldout.json`
- Inference timeout: 150 s per call. All 37 cases returned `SUCCESS`.

## What was measured

Per case, the harness scores two modes:

- deterministic: flagged iff `scan_copy` returns any CLAIM_* (forbidden
  approval/guarantee) finding — the literal-regex baseline.
- hybrid: flagged iff deterministic CLAIM_* OR any semantic finding on a
  SUCCESS result — the reviewer a human would rely on.

Positive class = `semantic_expected == "VIOLATION"`. Every case ran the live
model; no analysis failures or invalid outputs occurred (`failures = 0`).

## Case-set size and splits

37 cases total: 23 dev (13 VIOLATION, 10 COMPLIANT), 14 held-out (13 VIOLATION,
1 COMPLIANT), frozen at `heldout_frozen_at = 2026-10-07` before any tuning.

## Counts per mode / split

| mode | split | TP | FP | FN | TN | n | precision | recall |
|------|-------|----|----|----|----|---|-----------|--------|
| deterministic | dev | 5 | 0 | 8 | 10 | 23 | 1.000 | 0.385 |
| deterministic | heldout | 6 | 0 | 7 | 1 | 14 | 1.000 | 0.462 |
| hybrid | dev | 9 | 0 | 4 | 10 | 23 | 1.000 | 0.692 |
| hybrid | heldout | 8 | 0 | 5 | 1 | 14 | 1.000 | 0.615 |
| hybrid | combined | 17 | 0 | 9 | 11 | 37 | 1.000 | 0.654 |

The hybrid model raised recall on both splits (dev 0.385 → 0.692; heldout
0.462 → 0.615) at unchanged precision 1.000 — no false positives on any
compliant copy in either mode.

## Failures / invalid outputs

None. All 37 semantic analyses returned `SUCCESS` and were parseable; there
were no FAILED or SKIP rows in either results file. (Separate from confusion
counts above.)

## Notable cases

1. **Paraphrase the regex missed, the model caught** — `paraphrase_claim_001`
   (dev): "Your yes is already decided—everyone qualifies." Deterministic
   flagged nothing (no literal `pre-approved` / `guaranteed approval`); the
   model returned CLAIM_001 + CLAIM_002 and flagged VIOLATION. Same for
   `paraphrase_claim_002` ("Get approved instantly, no matter your credit
   history"), `p13` ("Approval is already in the bag for you"), `p14` ("You're
   approved on the spot, every single time") — all caught by hybrid, missed by
   the literal regex.
2. **No false positive on compliant copy** — all 11 COMPLIANT cases (10 dev,
   1 heldout) stayed unflagged in both modes (FP = 0). The S5/S6 compliant
   counterexamples (`compliant_disclosure_001`, `compliant_counterexample`,
   `c35`) did not trigger.
3. **Model misses missing-disclosure violations** — the heldout VIOLATION
   misses are all disclosure cases (`p15`, `d18`, `s27`, `s28`) plus the dev
   disclosure set (`missing_disclosure_001/002/003_scoped`, `s29`): the model
   returned no finding where the label expected a disclosure VIOLATION
   (`semantic_rules_expected == []`). The approval-guarantee class is caught
   well; the missing-required-disclosure class is not.
4. **Instruction attacks treated as untrusted copy** — `instruction_attack`
   (dev) and `i20`/`i21` (heldout) still flagged the underlying CLAIM_001
   while never following the embedded instruction; no invented findings.

## Latency and tokens

Per-call latency (ms), live model only:

| split | n | mean | p50 | max |
|-------|---|------|-----|-----|
| dev | 23 | 13472 | 10694 | 39164 |
| heldout | 14 | 14332 | 15718 | 22730 |

Token totals per run (all 23 / all 14 calls): 27706 (dev), 17422 (heldout).
Model serves one request at a time; total wall time across both runs ~9 min.

## Limitations

- Small hand-labeled set (37 cases, 26 VIOLATION) — not a statistical
  guarantee.
- Single model (`deepseek-v4-flash`) — no cross-model or temperature sweep.
- Not legal validation: labels encode the demo policy only
  (`clearpath-demo-v1`); findings are reviewer output, not a compliance
  opinion.
- Disclosure-requirement cases are under-served (see notable case 3).

## Reproducibility

Re-run live: `CLEARPATH_INFERENCE_TIMEOUT_S=150 .venv/bin/python
scripts/eval_semantic.py --split dev --limit 23 --out docs/eval_results_dev.json`
and `--split heldout --limit 14 --out docs/eval_results_heldout.json`.
Deterministic baseline: `--limit 0` (no model calls).

Results files contain `model_id`, prompt version, provider revision, case-set
hash and per-case rows only — no base URL, host, or credential fields.
