# Product decisions

## Product thesis

The prompt establishes an Excel/email review bottleneck, but does not establish its root cause. This product tests the hypothesis that incomplete intake, unowned requests, scattered feedback, and repeated revision review consume reviewer time. It reduces coordination work around the decision while preserving a human decision-maker.

The product supports all three named products (personal loans, credit cards, mortgage prequalification) and five channels (affiliate, email, social, paid search, website). Internal marketers submit affiliate copy on behalf of partners. The assignment does not require source Excel files, email exports, or importing old records.

## Scope choices

- **Structured text intake:** makes the reviewed artifact explicit. Images, page layouts, landing pages, and linked terms are not inspected. URL changes do not imply that future external content is approved.
- **Immediate assignment:** route to the reviewer with the fewest active reviews, breaking ties by name/id. Waiting-on-marketer work does not consume this reviewer workload count. Initial seed backlog remains unassigned for manual triage.
- **Elapsed-time review target:** 72 hours is an explicit demo assumption. The clock includes marketer revision time and is never reset by resubmission. It is not a measure of reviewer labor.
- **Human decision plus deterministic preflight:** five fictional policies illustrate repetitive checks without claiming legal accuracy or regulatory completeness. A clean scan is not approval. No LLM credentials or unpredictable output are needed for evaluation.
- **Immutable content versions:** review history stays attached to the exact copy, with actor, time, version, decision comment, and observed policy findings. Optimistic concurrency prevents one review from overwriting another.
- **One small service:** plain HTML and JavaScript, FastAPI, and SQLite keep the deploy reproducible within the timebox. No distributed services or build pipeline for frontend assets.
- **Shared demo personas:** lets an evaluator experience both sides of the workflow quickly. These are not authenticated users. All sample content and metrics are synthetic.

## How to evaluate actual throughput

Before rollout, observe the existing process and establish a baseline by product/channel. Pilot with a small review team and comparable campaign complexity; do not infer causality from the seeded dashboard.

Primary measures: completed reviews per reviewer-week, end-to-end median and p90 turnaround, reviewer handling time per campaign, and revision cycles per submission. Guardrails: policy misses found in audit, reopens after approval, first-pass intake completeness, and marketer wait time. Separate time waiting on marketers from time waiting on compliance before attributing delays.

The demo calculates completion volume, elapsed turnaround, active backlog, unassigned work, and target breaches from its database. It does not yet calculate handling time, p90, or quality outcomes. Instrumenting those and validating policy coverage would be the next step.

## Adoption and next step

Pilot one product/channel with a named compliance owner. Require new requests through structured intake; migrate only active backlog with human validation. Train marketers on complete copy and reviewers on assignment/revision states. Compare results against the baseline, interview both roles, and expand only if throughput improves without degrading quality.

Production priorities: SSO and real authorization, durable storage/backups, curated versioned policies with legal ownership, notifications, attachment review, and validated migration from spreadsheets/email. National operation makes jurisdiction and policy applicability a substantive production requirement; this demo deliberately does not invent that legal coverage.
