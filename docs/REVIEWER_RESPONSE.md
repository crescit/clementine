# Semantic Review Upgrade — Summary

## What changed

The reviewer now optionally runs a hybrid semantic pass in addition to the
existing deterministic CLAIM_* scanner. When `CLEARPATH_SEMANTIC_MODE=true`,
submission copy is sent to an OpenAI-compatible chat model for analysis; the
literal regex scanner still runs underneath. Findings that a regex alone would
miss (e.g. paraphrased approval/guarantee claims) surface as semantic findings,
each with a disposition form (Acknowledge/Dismiss). Acknowledging a finding
opens the approval gate; the submitter sees findings read-only until then.

## How to see it

1. Set `CLEARPATH_SEMANTIC_MODE=true` plus `CLEARPATH_INFERENCE_BASE_URL`,
   `CLEARPATH_INFERENCE_MODEL`, `CLEARPATH_INFERENCE_API_KEY`, and
   `CLEARPATH_INFERENCE_TIMEOUT_S=150` (key only in private `.env` / host
   secrets). Run locally: `uv run uvicorn clearpath.api:app --reload`.
2. Open the policies page as Sarah T., open CP-8903, and press **Analyze** —
   it returns a CLAIM_002 finding. Acknowledge it to open **Approve**.
3. Open CP-8908 as Mark Davis and **Analyze** — it stays compliant.
   Screenshots: `docs/screenshots/semantic-*.png`.

## Evidence

- [Live evaluation](EVALUATION.md) — 37-case run: hybrid recall dev 0.385 →
  0.692, heldout 0.462 → 0.615, precision 1.000, 17 TP / 0 FP.
- [Verification](VERIFICATION.md) — regression suite and real-browser
  walkthrough.
- Screenshots under `docs/screenshots/semantic-*.png`.

## Limitations

- The evaluation is a small hand-labeled set (37 cases, 26 VIOLATION), not a
  statistical guarantee.
- Measured on a single model (`deepseek-v4-flash`); no cross-model or
  temperature sweep.
- Not legal validation: labels encode the demo policy only; findings are
  reviewer output, not a compliance opinion.
- Missing-required-disclosure cases are under-served by the model (see
  EVALUATION.md notable case 3).
- Semantic analysis adds latency (~13-14 s mean per call, single-model serial)
  and needs a reachable OpenAI-compatible endpoint; it is off by default.
