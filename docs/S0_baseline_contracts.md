# S0 — Baseline and Contracts (2026-10-06-semantic-v1)

Status: FROZEN baseline for the ClearPath semantic-review upgrade series.
Revision: 2026-10-06-semantic-v1
Plan: semantic upgrade plan (internal, not tracked)
Card: S0 — Baseline and contracts
Dependency: none (baseline established first)

This document freezes the existing route, schema, and approval contracts that
the S-series upgrade must preserve. It records baseline evidence so downstream
cards (S1–S7) consume verified parent evidence instead of re-deriving it.

## Baseline verification evidence (actual, run 2026-10-06)

- Repository instructions: no AGENTS.md/.cursorrules present; README.md is the
  operational contract. The upgrade plans were read.
- Baseline tests: `uv run pytest` -> 157 passed, 1 warning (0.78s).
  Collected 157 across test_api (27), test_db (15), test_metrics (9),
  test_pagination (8), test_performance_tools (2), test_preflight (13),
  test_transactions (4), test_workflow (79).
- Board configuration inspected (board `clearpath`, kanban.db at
  the local board database): S-series cards S0..S7 created;
  S0 running, S1..S7 todo with parent-dependency gating.
- Coding agent verified as DeepSeek: task t_2610f00d carries
  `model_override=deepseek-v4-flash`, `provider_override=custom`, resolved to
  the OpenAI-compatible endpoint <private OpenAI-compatible endpoint>
  which serves model id `deepseek-v4-flash`, `supported_in_api: true`. The runtime evaluator model is a separate
  configuration decision (see Inference contract below).
- Runtime provider access confirmed WITHOUT printing secrets: TCP connect to
  the endpoint succeeded; /v1/models returned HTTP 200
  listing `deepseek-v4-flash`. API key presence confirmed non-empty in
  the server .env (VLLM_API_KEY); the key value was never
  printed or logged.
- Archived original-build tasks reconciled: K-task IDs t_2ecfae40 (K0),
  t_0f7799dc (K1), t_781f2642 (K2), t_70cc992c/t_8edd7a7d (K3-K4),
  t_9815f4a7 (K5-K6), t_0b5fe210 (K7-K12), t_3a485c55 (K13), t_bbd2c206
  (K14-K16) all map to features present in current source/tests. No delivered
  feature was missing; no reimplementation performed. Baseline schema version
  `user_version=2` matches current db.py.

## Frozen route contract (from clearpath/api.py, §9)

Public routes (no identity): `GET /` (queue HTML),
`GET /static/*` (StaticFiles), `GET /api/health`, `GET /api/users`,
`GET /api/config`. All business routes require `X-Demo-User-Id`
(resolved server-side via `CallerId`/`resolve_actor`; unknown/missing -> 401;
role/actor IDs in request JSON are never trusted).

Reads:
- `GET /api/notifications` (limit 1..50) -> `{notifications, unread_count}`
- `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all`
- `GET /api/submissions` filters: mine, reviewer, status, search, completed,
  risk, limit(1..100, default 50), offset(>=0) ->
  `{submissions:[QueueItem], total, limit, offset}`. Non-reviewers see only
  own rows; sort open by sla_breach_at, launch, submitted_at, external_id;
  completed by decided_at desc, external_id.
- `GET /api/submissions/{id}` -> Detail projection + allowed_actions + preflight
- `GET /api/submissions/{id}/history` ->
  `{submission_id, versions:[...with per-version preflight], events:[...]}`
- `GET /api/metrics` -> metrics keys per §7 (open_count, sla_breached_count,
  unassigned_count, avg_turnaround_days, turnaround_sample_size,
  completed_last_7_days, scope, as_of)

Mutations (201 on create, 200 on others; all require expected_record_version >= 1):
- `POST /api/submissions` (IntakeRequest) -> Detail (201)
- `POST /api/submissions/{id}/assign` (AssignRequest)
- `POST /api/submissions/{id}/request-changes` (RequestChangesRequest)
- `POST /api/submissions/{id}/resubmit` (ResubmitRequest)
- `POST /api/submissions/{id}/approve` (ApproveRequest)
- `POST /api/submissions/{id}/reject` (RejectRequest)
- `POST /api/demo/reset` -> `{status:reset, seeded:true}` (reviewer-only,
  DEMO_MODE only)

## Frozen schema contract (from clearpath/db.py, §4)

Tables: users, submissions, submission_versions, audit_events, notifications.
Enums: Role SUBMITTER|REVIEWER; Product PERSONAL_LOAN|CREDIT_CARD|
MORTGAGE_PREQUALIFICATION; Channel AFFILIATE|EMAIL|SOCIAL|PAID_SEARCH|WEBSITE;
Status PENDING_ASSIGNMENT|UNDER_REVIEW|CHANGES_REQUESTED|APPROVED|REJECTED;
AuditEventType SUBMITTED|AUTO_ASSIGNED|ASSIGNED|REASSIGNED|CHANGES_REQUESTED|
RESUBMITTED|APPROVED|REJECTED; Severity BLOCKING|WARNING.
Invariants: UUID string PKs for user/submission/version ids; integer
autoincrement PK for audit_events (stable insertion order); PRAGMA
foreign_keys=ON and busy_timeout=5000 on every connection; WAL at
initialization; user_version=2; nonempty unsupported/corrupt DB -> raise
(actionable log), never silently delete/reseed.

## Frozen approval + policy contract (from clearpath/workflow.py, preflight.py, §5-6)

Transition matrix per §5 (CREATE, ASSIGN/REASSIGN, REQUEST_CHANGES,
RESUBMIT, APPROVE, REJECT). Server revalidates every mutation inside a short
BEGIN IMMEDIATE transaction; record_version increments exactly once per
successful mutation; current_version increments only on resubmit. Stale
authorized write -> 409 VERSION_CONFLICT; invalid state -> 409
INVALID_TRANSITION; no generic status PATCH.

Policy scanner (preflight.py, §6) is the deterministic baseline:
- CLAIM_001 (BLOCKING, all products/channels): case-insensitive
  `\bpre[\s-]*approved\b`; guidance: use "pre-qualified".
- CLAIM_002 (BLOCKING, all products/channels): case-insensitive
  `\bguaranteed\s+approval\b`.
- DISC_001 (BLOCKING, PERSONAL_LOAN|CREDIT_CARD, all channels): requires
  literal "Subject to credit approval." (casefold + whitespace-collapse).
- DISC_002 (BLOCKING, MORTGAGE_PREQUALIFICATION, all channels): requires
  literal "Prequalification is not a commitment to lend."
- DISC_003 (BLOCKING, all products, AFFILIATE): requires literal
  "ClearPath may compensate this partner."
Forbidden-claim regexes remain code-owned safeguards; the policy-admin form
must NOT expose arbitrary regex editing or weaken these baseline checks.
Findings carry rule_id, severity, matched_text, start/end (zero-based,
end-exclusive Unicode code-point offsets; JS highlights via Array.from(copy)).

## Inference contract (for S2 runtime evaluator)

Endpoint: <private OpenAI-compatible endpoint> (OpenAI-compatible chat).
Model id verified: `deepseek-v4-flash`,
supported_in_api=true. Key source: the server .env
(VLLM_API_KEY, non-empty; value never printed). Model-name swap works only
where the endpoint/capabilities support it; provider-specific behavior belongs
in the narrow adapter. No browser-visible keys, no user-controlled inference
URLs. Request JSON bounded; strict Pydantic validation of every finding even in
JSON-mode. Per-finding output: known applicable rule_id, exact evidence quote,
explanation, suggested revision. Server owns source, stable finding identity,
severity (semantic findings = WARNING), and computed Unicode code-point
offsets. Reject unknown/out-of-scope rules, nonexistent evidence quotes, excess
findings, invalid fields, oversized responses, incomplete/truncated output.
Empty semantic result is a distinct success state (not proof of compliance);
model confidence is never a compliance gate. Inference runs outside any SQLite
write transaction; analysis is per-submission, not on queue reads or detail
fetches; duplicate/in-flight requests capped.

## Downstream consumption notes

- S1 (Policy storage/admin) must preserve the frozen schema + policy contract
  above and migrate existing DB without deleting/reseeding.
- S2 (Semantic adapter/validation) consumes this Inference contract + the
  12 labeled cases in docs/labeled_cases.md as its seed evaluation set.
- S6 extends those cases to >=30 and runs the deterministic-vs-hybrid harness.
- Any scope reduction must be explicit and must not remove validation,
  permission checks, auditability, failure handling, or required tests.
