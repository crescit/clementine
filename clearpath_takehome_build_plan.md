# ClearPath Compliance Review — Implementation and Kanban Plan

> **Status: Ready for task creation.** This document is the implementation contract for Hermes and DeepSeek. Revision: `2026-09-25-multipage-v2`. Updated against the supplied 24-hour brief and Josh’s explicit HTML-pages/no-npm requirement.
>
> **Deliverable:** A working, publicly reachable demo URL **and** an accessible GitHub repository. Working locally, screenshots, or a container image alone do not satisfy the assignment.
>
> **Frontend contract:** Plain `index.html` plus separate HTML pages, shared CSS, and browser-native JavaScript, following the serving/navigation pattern in `ai_3d_mvp`. No TypeScript, npm/npx, package manager, frontend build step, or SPA router.
>
> **Execution:** Build the required scope below in dependency order. Hermes owns task sequencing and evidence; DeepSeek implements one ready card at a time. Do not interpret optional production ideas as required features.

## 1. Brief, assumptions, and success

### Facts supplied by the assignment

- ClearPath Financial is fictional and operates nationally.
- Products are personal loans, credit cards, and mortgage prequalification.
- Marketing uses multiple channels, including affiliate partners.
- Compliance marketing review currently uses Excel and email and bottlenecks growth.
- There are 24 hours to deliver a deployed URL and GitHub repository; ngrok is acceptable.

### Product assumptions, not supplied facts

- Internal marketers submit on behalf of both ClearPath and affiliate partners. External affiliate users are out of scope.
- Missing intake information, unclear ownership, scattered feedback, and repeated revision review plausibly contribute to the bottleneck. These are hypotheses to explain in the presentation, not results of customer research.
- A 72-hour elapsed-time review target and least-loaded reviewer assignment are reasonable demo policies.
- All names, campaign IDs, copy, operational history, and numbers below are synthetic. Do not claim the company has an 11-day average or that the sample campaigns came from the prompt.
- Text advertisements are the supported review artifact. A URL can give optional context, but the app reviews the pasted copy. It does not inspect linked pages, images, PDFs, layouts, or terms hidden elsewhere.
- Compliance rules are deliberately fictional ClearPath demo policies, not statements of federal/state law or legal advice. No claim of regulatory completeness.

### Product outcome

Help a small compliance team process work with less coordination overhead:

| Bottleneck hypothesis | Required behavior | Evidence in demo |
|---|---|---|
| Incomplete or scattered intake | Structured intake with required copy and metadata | Submit a complete campaign in one form |
| Unowned requests | Automatic assignment plus explicit reassignment | New request immediately has an owner |
| Reviewers cannot prioritize | SLA-ranked queue, filters, ownership, launch dates | Oldest breached campaign is first |
| Feedback separated from asset | Review decisions with comments beside copy | Request changes on Version 1 |
| Confusion about corrected content | Immutable versions and version-linked history | Compare Version 1 and Version 2 |
| Status chasing | Persona-specific queue and visible next action | Marketer sees feedback and resubmits |
| Repetitive checks | Explainable policy preflight | Highlight a claim and a missing disclosure |
| No throughput visibility | Actual database-derived metrics | Approval updates open count and completions |

Success is a working lifecycle with a credible operating model. Do not invent measured productivity improvements. Explain that rollout would measure completion volume, turnaround, rework, and reviewer effort against an observed baseline.

## 2. Scope and time budget

### Required within 24 hours

- Queue, structured intake, review workspace, revisions, history, persona switcher.
- Automatic assignment, explicit reassignment, server-enforced permissions.
- Deterministic demo-policy checks and server-side approval gate.
- Transactional writes, stale-edit protection, consistent errors.
- Reproducible seed/reset, tests, responsive and keyboard-usable UI.
- Local launch, Docker launch, real deployed URL, accessible GitHub repo, concise README.

### Explicitly excluded

Production login/SSO, external affiliate accounts, real email, notifications, email/Excel import, file uploads, OCR/PDF annotation, live site crawling, AI/LLM review, bulk approval, configurable policy editor, legal jurisdiction engine, separate analytics page, saved drafts, deletion/reopening, WebSockets, workers, queues/brokers, microservices, Postgres, ORM, React, TypeScript/TSX, JSX, Tailwind, Node runtime/tooling, npm/npx/pnpm/yarn, package.json, node_modules, Vite/Webpack, frontend compilation/bundling, SPA/hash routing, and separate frontend containers.

Freeform discussion comments and text diffs are optional; decision comments and selectable full versions are required. There is no separate commenting endpoint in the required scope.

### 24-hour execution schedule

These are planning allocations, not guarantees. Start the clock at implementation kickoff.

| Cards | Hours | Gate |
|---|---:|---|
| K0 | 1.0 | Skeleton, tests, local and Docker boot |
| K1–K4 | 4.5 | Data, workflow, atomic writes, preflight |
| K5–K6 | 2.0 | Complete API golden path |
| K7–K8 | 4.0 | Queue and working review workspace |
| K9–K11 | 2.5 | Intake, revision, reset; full browser lifecycle |
| K12 | 1.5 | Focused visual/accessibility pass |
| K13 | 2.0 | Failure and regression checks |
| K14–K15 | 2.0 | Documentation and final deployment verification |
| K16 | 0.5 | Human presentation check |
| Reserved buffer | 4.0 | Deployment/account issues and fixes |
| **Total** | **24.0** | **Both deliverable links verified** |

At K0, identify the hosting route and repository access. By hour 12, publish a bootable checkpoint if hosting is available; by hour 20, freeze feature work and spend remaining time on the lifecycle, defects, deployment, and handoff. Do not defer discovering hosting/account blockers to K15.

Cut in this order if behind: decorative animation, optional diffs/comments, additional filters beyond those specified, extra docs beyond the required concise files. Never cut persistent writes, version history, permissions, tests of the lifecycle, or either deliverable. Optional features are not on the critical path.

## 3. Stack and repository contract

Use Python 3.12, FastAPI, Uvicorn, Pydantic request/response models, stdlib `sqlite3`, vanilla HTML/CSS/JS, pytest, httpx for API tests, uv, and Docker Compose. Resolve compatible versions once, commit `uv.lock`, and use frozen installs afterward. Python dependencies use uv only. Frontend files are served exactly as authored: no Node installation, npm/npx command, package.json, node_modules, transpiler, bundler, or generated frontend dist directory, including for tests or deployment.

One process serves API and same-origin static assets. Run one Uvicorn worker with local SQLite storage. No remote services are required to evaluate locally.

```text
clearpath/
  __init__.py
  api.py          # routes, HTTP errors, identity, static serving
  models.py       # shared request/response models and enums
  db.py           # connections, schema, transactions
  workflow.py     # permissions, transitions, assignment, mutations
  preflight.py    # demo policy constants and scanner
  metrics.py      # queue urgency and metric calculations
  seed.py         # deterministic relative-time fixtures and reset
static/
  index.html      # queue and metrics
  submission.html # review detail, history, inline revision form
  submit.html     # new submission form
  styles.css      # shared design tokens and page styles
  common.js       # fetch helper, persona header, safe shared UI helpers
  queue.js        # index.html behavior
  submission.js   # submission.html behavior
  submit.js       # submit.html behavior
tests/
  conftest.py
  test_api.py
  test_workflow.py
  test_preflight.py
  test_db.py
  test_metrics.py
docs/
  ARCHITECTURE.md
  PRODUCT_DECISIONS.md
  VERIFICATION.md
pyproject.toml
uv.lock
Dockerfile
.dockerignore
docker-compose.yml
.env.example
.gitignore
README.md
```

Use native `<script type="module" src="/static/queue.js">` (and the matching file per page), with relative imports from `common.js`. Pages contain their own semantic HTML structure and share small helpers; do not build a generic renderer or client-side router. Routes parse input and map errors; workflow functions own transactions. Tests use temporary database paths and an injected UTC clock, never the demo database.

### HTML page serving and navigation

The inspected reference `/Users/josh/Documents/Projects/ai_3d_mvp` serves `static/index.html`, separate pages such as `orders.html`, and native JavaScript through FastAPI `StaticFiles`. Follow that structural pattern without importing its authentication, commerce features, or unrelated libraries. The reference is informational; implementation must not require it at runtime.

- `GET /` serves `static/index.html` via `FileResponse`; mount only the `static/` directory at `/static`.
- `/static/index.html` is the queue, `/static/submission.html?id=<uuid>` is detail, and `/static/submit.html` is intake. All must return real HTML directly on fresh load.
- Use ordinary `<a href>` links and normal full-page browser navigation. Read IDs and filters with `URLSearchParams`; do not use `#/queue`, `pushState` routing, SPA fallbacks, or a frontend dev server.
- Queue filters are query parameters on `index.html`; persist the last queue query in session storage for the Back to queue link. Never treat a user-provided return URL as an unrestricted redirect.
- Persona selection lives in local storage and initializes on every page. A persona switch refreshes the current page’s data and actions; no client-side route system is needed.
- Successful intake navigates to the detail HTML page. Review/revision actions can refresh data within that page through `fetch`; navigating between product surfaces loads a different HTML document.
- Shared styles/header conventions keep the three pages consistent. There is no build or copy-assets step.

Configuration: `DATABASE_PATH` (default `./data/clearpath.db`), `DEMO_MODE` (default true for this take-home). Honor the host's `PORT` at launch (default 8000). `.env.example` documents these; the README must state how to export them because merely creating a `.env` does not load it automatically. Do not claim production mode/auth exists when `DEMO_MODE=false`; this switch only disables reset and automatic demo seeding.

## 4. Authoritative domain and data model

### Enumerations

- Roles: `SUBMITTER`, `REVIEWER`. Sarah's “Compliance Lead” is a display title, not a third permission role.
- Products: `PERSONAL_LOAN`, `CREDIT_CARD`, `MORTGAGE_PREQUALIFICATION`.
- Channels: `AFFILIATE`, `EMAIL`, `SOCIAL`, `PAID_SEARCH`, `WEBSITE`.
- States: `PENDING_ASSIGNMENT`, `UNDER_REVIEW`, `CHANGES_REQUESTED`, `APPROVED`, `REJECTED`.

**No `DRAFT` or `RESUBMITTED` state.** Intake submits immediately. Resubmission is an event that returns work directly to review, avoiding an extra manual handoff. This supersedes the earlier plan's seven-state machine.

### Tables

Use UUID strings for user, submission, and content-version IDs. Submission UUIDs prevent an old browser tab from mutating a newly seeded record after reset. Human-readable IDs are separate. Use an integer autoincrement primary key for audit events so events written at the same timestamp have a stable insertion order.

| Table | Required fields |
|---|---|
| `users` | `id`, `name`, `role`, `display_title`, `created_at` |
| `submissions` | `id`, unique `external_id`, `title`, `partner` nullable, `channel`, `product`, `status`, nullable `assigned_reviewer_id`, `submitter_id`, `target_launch_date`, `submitted_at`, `sla_breach_at`, nullable `decided_at`, `current_version`, `record_version`, `created_at`, `updated_at` |
| `submission_versions` | `id`, `submission_id`, `version_number`, nullable `asset_url`, `copy_text`, `created_by`, `created_at` |
| `audit_events` | `id`, `submission_id`, `actor_id`, `event_type`, nullable `from_status`, `to_status`, `version_number`, nullable `comment`, `metadata_json`, `created_at` |

Timestamps are UTC with timezone information, consistently serialized as ISO 8601 `Z`; launch date is ISO date-only. Database constraints enforce enums, positive version numbers, foreign keys, and unique `(submission_id, version_number)`. Index queue status/reviewer/SLA columns and submission IDs in version/event tables.

Content versions and audit events are append-only through application APIs. This is an application audit trail, not a cryptographically tamper-proof or certified regulatory archive. Reset is the explicit demo-only exception.

Metadata (`title`, `partner`, `channel`, `product`, launch date) is fixed after intake. Only copy and asset URL change on revision. The approved artifact is the exact stored copy/version; a mutable external URL does not establish approval of future content at that URL.

Use `PRAGMA foreign_keys=ON` and a 5-second busy timeout on every connection; enable WAL at initialization. One connection per operation, closed reliably; no shared global connection. Initialize a versioned schema (`PRAGMA user_version=1`). If a nonempty DB has an unsupported schema or is corrupt, fail with an actionable log; never silently delete/reseed it. Create and seed only a fresh DB; restart preserves existing work. Reset is explicit.

Generate external IDs transactionally: start new intake at CP-8909 for the baseline, derive the next numeric suffix while holding the write transaction, and enforce uniqueness. Do not use row count.

## 5. Identity, visibility, and workflow

`GET /api/users` provides personas; the frontend defaults to Sarah and remembers the selected ID in local storage. Requests to business endpoints include `X-Demo-User-Id`. The backend resolves the user and never trusts role, actor ID, or submitter ID supplied in request JSON. Unknown/missing identity returns 401. If reset invalidates a selected persona ID, reload users and select Sarah.

All reviewers can read all submissions. Submitters can read only their own, including metrics and version history. Inaccessible records return 404. Tests must introduce a second submitter to verify ownership even though the UI seeds only Jessica. Reviewers may assign/reassign work; **only the currently assigned reviewer may request changes, approve, or reject**. Jessica alone creates and revises her work. Sarah and Mark have the same role permissions.

The persona switcher is intentionally public impersonation for synthetic demo data. State clearly in the UI and README: “Demo workspace · fictional data · shared state.” This is not authentication. Production identity would replace this mechanism; the workflow's role/ownership checks are still enforced server-side.

### Transition and permission matrix

| Action | Actor | Source | Destination | Conditions and side effects |
|---|---|---|---|---|
| Create/submit | Submitter | New record | `UNDER_REVIEW` normally; `PENDING_ASSIGNMENT` if no reviewers | Create v1, attempt auto-assignment, create history |
| Assign/reassign | Any reviewer | Any open state | Pending becomes `UNDER_REVIEW`; other states stay the same | Target must be a reviewer; record old/new owner and reason |
| Request changes | Assigned reviewer | `UNDER_REVIEW` | `CHANGES_REQUESTED` | Nonblank feedback required |
| Resubmit | Owning submitter | `CHANGES_REQUESTED` | `UNDER_REVIEW`, or pending if no reviewer available | Create vN+1; retain owner, otherwise auto-assign; require changed copy or URL |
| Approve | Assigned reviewer | `UNDER_REVIEW` | `APPROVED` | Recompute preflight inside mutation; zero blocking findings; capture decision details |
| Reject | Assigned reviewer | `UNDER_REVIEW` | `REJECTED` | Nonblank reason required |

All other transitions are invalid. Terminal records cannot be edited or reassigned. Reassigning changes-requested work preserves that status and its feedback. Assignment to the existing reviewer returns 409 `INVALID_TRANSITION`; no event or version increment. Reassignment reason is required when replacing an existing reviewer. Initial assignment reason is optional.

### Automatic assignment

Choose the reviewer with the fewest assigned `UNDER_REVIEW` submissions, breaking ties by `name` ascending then `id`. Pending requests and requests waiting on marketers do not count as reviewer workload. All reviewers are eligible for every product in this demo; no specialty routing or availability model.

Compute load and write assignment inside the creation/resubmission transaction. New intake normally goes directly into review. Pending seed records represent historical work imported conceptually from the old process and are intentionally left for manual triage. There is no import feature.

### Transaction and conflict contract

Every existing-record mutation requires `expected_record_version >= 1`. `record_version` starts at 1 and increments exactly once per successful API mutation, including reassignment. `current_version` increments only on resubmit. Creation and demo reset do not accept an expected record version.

Use a short `BEGIN IMMEDIATE` write transaction. Resolve identity, load the record, check visibility/permission, compare expected record version, validate transition and content, write the row/version/events, then commit. Any failure rolls back everything. A stale authorized request returns 409 `VERSION_CONFLICT`; state violations return 409 `INVALID_TRANSITION`. No check/write gap outside the transaction. Two requests with the same expected version yield one success and one conflict. Convert persistent DB lock timeout to 503 `DATABASE_BUSY` with a retry message.

Audit event types: `SUBMITTED`, `AUTO_ASSIGNED`, `ASSIGNED`, `REASSIGNED`, `CHANGES_REQUESTED`, `RESUBMITTED`, `APPROVED`, `REJECTED`. Creation may write two events within its single mutation; resubmit may also auto-assign if needed. Every event identifies the content version, actor, UTC timestamp, and transition. Assignment metadata includes previous/new owner and `assignment_method`; an automatic event uses the initiating submitter as actor and identifies the automatic method explicitly. Decision metadata includes `policy_version` and preflight finding IDs observed at decision time. No generic status PATCH endpoint.

## 6. Preflight: exact demo policy contract

The scanner assists review; passing does not approve a request. Label its panel “Demo policy checks” and show “Checks pasted copy only. Human review still required.” Do not assert that “pre-approved” is universally illegal or that “pre-qualified” is always the correct legal replacement.

Store data-driven rules in `preflight.py`, versioned as `clearpath-demo-v1`. Freeze the policy during this take-home. Avoid generic Member FDIC/Equal Housing Lender checks: the brief establishes neither bank status nor applicability.

| ID | Scope | Detection | Severity | Guidance |
|---|---|---|---|---|
| `CLAIM_001` | All products/channels | Case-insensitive `\bpre[\s-]*approved\b` | `BLOCKING` | Demo policy requires “pre-qualified”; confirm this describes the actual offer |
| `CLAIM_002` | All products/channels | Case-insensitive `\bguaranteed\s+approval\b` | `BLOCKING` | Remove the guarantee; approval remains subject to review |
| `DISC_001` | Personal loan and credit card; all channels | Missing literal “Subject to credit approval.” | `BLOCKING` | Add the exact demo disclosure |
| `DISC_002` | Mortgage prequalification; all channels | Missing literal “Prequalification is not a commitment to lend.” | `BLOCKING` | Add the exact demo disclosure |
| `DISC_003` | All products; affiliate channel only | Missing literal “ClearPath may compensate this partner.” | `BLOCKING` | Add the exact demo partner disclosure |

For disclosure presence, casefold and collapse whitespace; otherwise require the literal text including punctuation. Forbidden matches preserve the original matching substring. Return all nonoverlapping occurrences per rule with zero-based, end-exclusive offsets into the original copy. Specify offsets as Unicode code points; JS highlighting must use `Array.from(copy)` so emoji do not shift spans. Missing disclosures have `matched_text`, `start`, and `end` set to null. Do not return invented text locations.

```json
{
  "policy_version": "clearpath-demo-v1",
  "passed": false,
  "findings": [{
    "rule_id": "CLAIM_001",
    "severity": "BLOCKING",
    "title": "Restricted approval claim",
    "matched_text": "pre-approved",
    "start": 4,
    "end": 16,
    "message": "Demo policy requires pre-qualified; confirm offer accuracy."
  }]
}
```

That example corresponds to “Get pre-approved …”. Preflight is synchronous on reads and approval; no job infrastructure. Approval with findings returns 409 `PREFLIGHT_BLOCKED` and structured findings even if the browser enabled the button. No override in required scope. Intake and resubmission may contain findings so reviewers can provide feedback. Historical versions show their own scan, labeled with the current frozen policy; decision events preserve what was recorded when decided.

## 7. SLA, sorting, filters, and metrics

Capture one UTC `now` per request and inject it for tests. New work gets `sla_breach_at = submitted_at + 72 hours`. Age and end-to-end turnaround start at first submission, not at each revision. The clock does not reset or pause while waiting on marketing; explain that this demo measures total elapsed time and does not isolate reviewer effort. Terminal work has no active SLA badge.

Open = pending + under review + changes requested. Label rows “Waiting on compliance” or “Waiting on marketer” to make the next owner clear. Urgency:

- `BREACHED` if `now >= sla_breach_at`.
- `DUE_24H` if remaining time is positive and <=24 hours.
- `DUE_48H` if >24 and <=48 hours.
- `ON_TRACK` otherwise.

Sort open queue by urgency bucket in that order, then earliest SLA deadline, earliest launch date, oldest `submitted_at`, external ID. Completed view sorts by `decided_at` descending then external ID. Reviewer queue defaults to all open work; “Mine” matches selected reviewer, “Unassigned” matches null owner, “SLA risk” means breached or due within 24 hours. Jessica's default is her open work; her empty state offers “New submission.” Completed work is reachable through an explicit Open/Completed/All selector. Search title, partner, and external ID case-insensitively; compose it with other filters.

Metrics use the current persona's visible records, independent of search/queue filters; label this scope. Compute in SQL/service code, never hardcode UI values:

| Metric | Definition |
|---|---|
| Open | Count of open states |
| SLA breached | Open records whose deadline <= now |
| Unassigned | Open records with null reviewer |
| Avg turnaround · last 30d | Mean `(decided_at - submitted_at)` in elapsed days for approved/rejected records decided in `[now-30d, now]`; one decimal; null/“—” with no sample |
| Completed · last 7d | Count of approved/rejected records decided in `[now-7d, now]` |

The metrics response keys are `open_count`, `sla_breached_count`, `unassigned_count`, `avg_turnaround_days` (number or null), `turnaround_sample_size`, `completed_last_7_days`, `scope` (`all_submissions` or `own_submissions`), and `as_of` (UTC timestamp). These show operational visibility, not proof that the tool caused improvement. Waiting-on-marketer time and reviewer time can be separated in future analysis.

## 8. Seed contract and five-minute demo

Seed only fictional content. Use a captured `seed_now` on fresh initialization/reset; relative offsets stay coherent regardless of presentation date. Tests freeze the clock. Reset is structurally deterministic but creates fresh UUIDs and timestamps relative to the reset. Do not promise byte-for-byte identical IDs/timestamps.

Personas: Sarah T. (reviewer, Compliance Lead), Mark Davis (reviewer, Compliance Analyst), Jessica Lin (submitter, Partnerships). Jessica owns every baseline submission.

| ID / title | Product / channel | State / reviewer | Submitted | Launch | Purpose |
|---|---|---|---|---|---|
| CP-8902 / CreditKarma — Fall Promo | Personal loan / affiliate | Pending / none | -15d | +2d | First queue item, breached and unowned |
| CP-8903 / Q4 First-Time Buyer | Mortgage prequalification / email | Under review / Sarah | -4d | +4d | Second breached item, missing mortgage disclosure |
| CP-8904 / ClearRewards Launch | Credit card / social | Under review / Sarah | -2d | +3d | Main request-changes/revision/approval flow |
| CP-8905 / NerdWallet Debt Consolidation | Personal loan / affiliate | Approved / Mark | -8d | +1d | Historical v1 changes requested, corrected v2 approved at -3d |
| CP-8906 / ClearRewards Brand Terms | Credit card / paid search | Pending / none | -1d | +6d | Manual assignment example |
| CP-8907 / LendingTree Rate Table | Personal loan / affiliate | Changes requested / Mark | -1d | +5d | Feedback to add partner disclosure |
| CP-8908 / Spring Mortgage Preview | Mortgage prequalification / website | Under review / Mark | -12h | +8d | Clean copy that can be approved |

Seed corresponding versions and plausible, chronologically ordered events for every record. At captured baseline: **6 open, 2 breached, 2 unassigned, 5.0 days average turnaround (n=1), 1 completed in last 7 days**. Derive and assert these values rather than hardcoding them in the UI. Record dates advance naturally after seeding; exact boundary counts may change over time.

CP-8904 Version 1, exactly:

> You're pre-approved for ClearRewards. Explore rewards for everyday purchases.

Expected findings: `CLAIM_001`, `DISC_001`.

Corrected Version 2, exactly:

> You may be pre-qualified for ClearRewards. Explore rewards for everyday purchases. Subject to credit approval.

Expected findings: none. Put corrected demo copy in the README walkthrough so evaluators do not have to guess the magic disclosure. Do not preload it as an automatic production feature.

### Golden walkthrough

1. Open deployed root URL as Sarah. See synthetic demo label, 6 open, 2 breached, and CP-8902 first.
2. Open CP-8904, owned by Sarah. See v1 inline, highlighted claim, missing disclosure, and timeline.
3. Enter “Replace the approval claim with accurate prequalification language and add: Subject to credit approval.” Request changes. Owner remains Sarah; status becomes `CHANGES_REQUESTED`.
4. Switch to Jessica without leaving the record. Feedback and revision form appear. Paste corrected copy, submit revision.
5. v2 is created, status goes directly to `UNDER_REVIEW`, Sarah remains assigned. Select v1 to verify original content/feedback remain visible.
6. Switch to Sarah, select current v2, see passing checks, approve. History records approved v2; the request moves out of open queue and metrics recalculate.
7. Optionally create a new affiliate request as Jessica with the two required demo disclosures. Show automatic assignment. Reset to baseline.

Also verify Mark cannot decide Sarah's work until explicitly reassigned. Reviewer switching must not silently change ownership.

## 9. API contract

All business routes require `X-Demo-User-Id`; `/`, static assets, `/api/health`, and `/api/users` are public. Use same-origin requests, no wildcard CORS. Domain JSON models reject unknown fields. Never return SQL traces.

| Method / path | Input | Success |
|---|---|---|
| `GET /api/health` | None | 200 `{status:"ok"}` only if DB can be read; otherwise 503 |
| `GET /api/users` | None | 200 `{items:[{id,name,role,display_title}]}` |
| `GET /api/submissions` | See filters below | 200 `{items:[QueueItem],total,limit,offset}` |
| `GET /api/submissions/{id}` | UUID path | 200 `Detail` |
| `GET /api/metrics` | Persona identity | 200 metrics defined in §7, plus sample size and scope |
| `POST /api/submissions` | Intake body below | 201 `Detail` |
| `POST /api/submissions/{id}/assign` | `expected_record_version`, `reviewer_id`, optional `comment` | 200 `Detail` |
| `POST /api/submissions/{id}/request-changes` | `expected_record_version`, `comment` | 200 `Detail` |
| `POST /api/submissions/{id}/resubmit` | `expected_record_version`, `copy_text`, nullable `asset_url` | 200 `Detail` |
| `POST /api/submissions/{id}/approve` | `expected_record_version`, optional `comment` | 200 `Detail` |
| `POST /api/submissions/{id}/reject` | `expected_record_version`, `comment` | 200 `Detail` |
| `POST /api/demo/reset` | `{confirm:true}`; reviewer persona; DEMO_MODE enabled | 200 `{reset:true}` |

Intake body: `title`, nullable `partner`, `channel`, `product`, `target_launch_date`, nullable `asset_url`, `copy_text`. Server supplies status, submitter, versions, owner, deadlines, IDs, and timestamps.

Validation: trim outer whitespace; title 3–120 characters; partner max 120 and required/nonblank for affiliate; copy 1–20,000 characters; comment when required 1–2,000 characters; asset URL max 2,048 characters, optional, only http/https without credentials; launch date required and no earlier than current UTC date at intake. Past launch dates may remain on existing work and do not block revisions. Empty optional strings normalize to null. UI labels exact limits and date interpretation. No rich HTML input.

Queue parameters: `view=open|completed|all` (default open), `filter=all|mine|unassigned|sla_risk` (default all), optional `status` (enum), `search` (max 200 characters), `limit` (default 50, max 100), `offset` (default 0). Filters combine with AND, apply permissions before counts, then deterministic sorting and pagination. For submitters, `mine` means their submissions; other filters remain restricted to their own records. UI offers load more if `total` exceeds loaded records.

`QueueItem` includes ID, external ID, title, partner, channel/product, status, submitter, reviewer summary or null, submitted/launch/SLA dates, `age_hours`, nullable `sla_status`, current/record versions, and waiting-on label. `Detail` returns `{submission, versions, audit_events, preflight, allowed_actions}`. Versions contain full copy/URL and provenance in ascending version order, sufficient for selection without another endpoint. Timeline sorts by creation time then integer event ID, preserving insertion order for events with identical timestamps. Current preflight corresponds to current version; each historical version also includes its own computed preflight. `allowed_actions` is derived server-side for the selected persona; the server revalidates every mutation.

Successful mutations return refreshed detail; refresh queue/metrics when returning to queue. UUIDs go in API paths; external IDs are display/search identifiers.

### Error contract

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "This submission changed. Review the latest version before trying again.",
    "details": {"current_record_version": 3}
  }
}
```

Map malformed JSON/request validation to 422 `VALIDATION_ERROR` with field errors; missing/unknown identity to 401 `UNAUTHENTICATED`; wrong role/decision owner to 403 `FORBIDDEN`; missing or invisible resource to 404 `NOT_FOUND`; state/no-content-change conflicts to 409 `INVALID_TRANSITION`; stale writes to 409 `VERSION_CONFLICT`; blocked approval to 409 `PREFLIGHT_BLOCKED`; DB busy to 503 `DATABASE_BUSY`. FastAPI's default validation envelope must be normalized. Unexpected errors return generic 500 `INTERNAL_ERROR` and are logged server-side without dumping submitted copy.

## 10. UX and frozen visual direction

### Queue

Desktop header: ClearPath / Compliance, Review Queue, persona switcher, New submission for submitter, Reset demo for reviewers. Compact metrics strip; Open/Completed/All selector, filters, search; dense table showing external ID/title, partner, product/channel, status, owner, age, launch, SLA. Use readable labels instead of raw enum strings. Above the table, one short sentence explains that the queue prioritizes work approaching or past the review target.

### Workspace

Two columns on desktop: submitted copy and preflight on the left; metadata, next action, owner/assignment control, launch and SLA on the right. Full-width audit timeline below. Back-to-queue link preserves filters. Version selector makes current versus historical selection explicit. Decision/revision actions are disabled while viewing historical content with an instruction to return to current version. No decision can accidentally refer to an old selected version.

Request changes/reject require labeled text input with inline validation. Approve stays disabled with an explanation while blocking findings exist. Unassigned records offer “Assign to me” or reviewer selection. Nonowners see who owns the next decision and may explicitly reassign. Terminal record shows decision outcome, actor, version, time, and reason where supplied.

### Intake and revision

Use a page/inline form, not a complex modal. Intake fields follow §9 with helpful affiliate/disclosure labels. On revision show latest change request and current content; keep feedback visible while editing. Retain unsaved text on validation/network/conflict errors. On success navigate to current detail and announce the status. No draft persistence is promised.

### Frozen tokens

Inspected `/Users/josh/Documents/Projects/personal_site/index.html` on September 25, 2026. Its dark palette, system fonts, periwinkle accents, and focus treatment are the reference. Implementation must not depend on access to that repository. Adapt its spacious marketing layout to a compact operations interface; no design discovery blocker remains.

```css
:root {
  color-scheme: dark;
  --bg: #0a0f18;
  --surface: #141c2c;
  --surface-raised: #172032;
  --ink: #e8edf7;
  --text-2: #b7c2d3;
  --muted: #98a7bd;
  --accent: #8fa8de;
  --accent-ink: #adc0ea;
  --line: rgba(178,198,232,.14);
  --success: #86d6af;
  --warning: #efc078;
  --danger: #f19b9b;
  --radius: 8px;
  --radius-s: 6px;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
```

Use a 4/8/12/16/24/32px spacing scale, body 14–16px, headings 24–28px, metadata >=12px, max content width ~1280px. Solid surfaces, subtle borders, modest status pills, no external fonts, large gradients, glass effects, or theme switcher. Check actual contrast for final color pairings; color alone never conveys status.

At ~390px, stack workspace columns and render queue rows as readable cards or a deliberately scrollable labeled table with primary identity/status visible. At 1280px, queue and detail should scan quickly. Semantic labels, keyboard activation, visible 2px accent focus outline, skip link, real buttons, `aria-live` status messages, reduced-motion support, and minimum ~40px touch controls.

### Required browser behavior

- Real HTML URLs `/static/index.html`, `/static/submit.html`, and `/static/submission.html?id=<uuid>` work with ordinary links, back/forward, direct load, and refresh. `/` serves the queue. Preserve queue filters as specified in §3; missing/invalid detail IDs render a useful error with a queue link.
- Loading, empty, no-results, network-error, 404, forbidden, and success states are intentionally rendered. Empty search results offer Clear filters.
- Disable an action while its request is pending. Existing-record duplicate requests cannot duplicate decisions because of OCC. For creation, do not automatically retry an ambiguous network failure: tell the user to check their queue first. Full creation idempotency infrastructure is out of scope.
- On 409, retain draft feedback/copy, fetch latest detail, explain the conflict, and require a fresh click after review; do not auto-replay the action.
- Abort or discard outdated responses on page unload/persona changes so another persona's previous response never renders. Confirm before discarding unsaved form text on navigation/persona switch.
- Render all user text via `textContent`/safe DOM nodes. Highlight with text nodes and `<mark>`; never interpolate submitted text into `innerHTML`. Parameterize SQL. Never fetch the submitted URL from the server or embed it as an iframe. Optional link opens with `noopener noreferrer` after scheme validation.

## 11. Demo reset and runtime reliability

Reset is visible only for reviewers and available only in demo mode. Confirmation copy: “Reset this shared demo? This removes all demo changes for every visitor.” API requires `confirm:true`; reset all data and reseed in one transaction using the same schema, without dropping tables or switching database files. Reset is not a workflow audit event because it destroys the demo history by design.

After reset, clear cached entities, reload personas, select Sarah, and return to queue. Another visitor's old record UUID returns 404 and the UI explains that the demo may have been reset. Shared public state is an explicit limitation; do not build per-visitor tenancy during this take-home. Final verification resets once and leaves the demo in baseline state.

Persistent mounted SQLite storage is required for the container deployment. Restart must preserve a newly created request. Never reseed on each app startup or silently recover corrupt storage. Keep the database, WAL files, caches, secrets, and `.venv` out of Git/Docker build context. Basic request/body size validation and same-origin operation suffice for synthetic demo scope; do not describe the public demo as suitable for confidential campaigns.

## 12. Test and verification gates

Tests must verify observable behavior and database invariants, not merely mirror functions. A card is done only when its targeted tests plus the existing suite pass. Run the full suite at integration gates; do not add an arbitrary coverage target.

| Area | Required evidence |
|---|---|
| Schema/seed | Fresh init, repeated init preserves writes, constraints/foreign keys, expected baseline counts, relative timestamps, coherent history, atomic reset |
| Workflow | Every allowed transition, forbidden source states, terminal protection, required feedback/rejection reason, no-op revision rejected |
| Permissions | Wrong role, nonowner submitter read/write, nonassigned reviewer decision, explicit reassignment, unknown identity |
| Assignment | Least-loaded reviewer, tie-breaker, no-reviewer fallback, resubmit retains owner, waiting-on-marketer excluded from load |
| Transactions/OCC | Stale mutation leaves no writes; two independent connections submit same version and only one wins; simulated audit insert failure rolls back submission/version changes |
| Versions/audit | v1 unchanged after v2; events reference correct versions/actors; approval records v2/policy; assignment increments record version only |
| Preflight | Both phrases, mixed case, spacing/hyphen variant, missing/scoped disclosure, multiple matches, emoji offsets, exact corrected copy passes; forbidden claim cannot be approved through direct API |
| Queue/metrics | SLA boundary at 0/24/48h, terminal exclusion, changes-requested included, priority ties, combined filters/search/pagination, persona scope, null average, 7d/30d boundaries |
| API | All endpoints, normalized 422s, missing identity, role/owner errors, 404, OCC errors, disabled reset, blank/oversized inputs, dangerous URL schemes |
| Integration | API golden flow from baseline; create then assigned; reset baseline; no duplicate events from repeated decision clicks |
| Browser/manual | Full walkthrough, version selection, create, reassignment, persona change, back/refresh, error handling, keyboard, 390px/1280px, no console errors |
| Delivery | Docker cold start, persistence through restart, local clean install, deployed health and golden flow, accessible repository and README links |

Python API/integration tests are required. A recorded manual browser checklist in `docs/VERIFICATION.md` satisfies UI verification. If automation is useful, use an already available browser tool or Python tooling; never add npm/npx or a JavaScript test/build dependency. HTTP tests must fetch all three real HTML pages and their referenced local JS/CSS successfully. Record commands, results, date, tested commit, tested URL, and known limitations. Do not report manual/browser/deployed checks as passed unless actually performed.

## 13. Kanban execution rules

For a new board, each K-card below can be a separate task. The existing ClearPath Hermes board uses the eight-task grouping below (seven pending tasks plus the scaffold task that was blocked by conflicting generated instructions); do not create duplicate K-card tasks. Include this document or its repository path **and the same plan revision** in every agent task. Each card's acceptance criteria include the relevant contract sections; task summaries cannot override them.

### Existing Hermes board mapping

These task IDs keep the existing dependency chain. Each group must satisfy all mapped K-card checks; the K-cards remain the detailed implementation checklist. Board task bodies point to this revision at `/Users/josh/Documents/Projects/promptarmortakehome/clearpath_takehome_build_plan.md`. Older workflow-draft attachments and generated TypeScript/DDD-monorepo instructions are superseded by this revision. Domain logic stays cleanly separated in Python modules; extra packages/ports/services are not required.

| Existing task ID | Corrected task | K-card coverage |
|---|---|---|
| `t_2ecfae40` | Scaffold Python/FastAPI and plain HTML pages | K0 |
| `t_0f7799dc` | Build SQLite schema and coherent demo seed | K1 |
| `t_781f2642` | Define Python domain rules and validation | K2 |
| `t_70cc992c` | Implement transactional workflow and policy checks | K3–K4 |
| `t_9815f4a7` | Build FastAPI endpoints, queue, and metrics | K5–K6 |
| `t_0b5fe210` | Build the multipage HTML/CSS/JS experience | K7–K12 |
| `t_3a485c55` | Verify single-container deployment and persistence | K13 plus §14 container checks |
| `t_bbd2c206` | Finalize docs, GitHub, live demo, and evidence | K14–K16 |

Implement in that order in the project directory; use each parent’s committed result. Scratch task directories are not separate app roots. Do not edit an outdated plan attachment instead of this project plan. Record task ID and mapped K-card IDs in completion evidence.

Suggested columns: Backlog → Ready → In progress → Verify → Done; use Blocked with a stated missing dependency when necessary. Work in progress limit: one implementation card. No simultaneous agents editing shared files. A later card may be started only once dependencies pass. K14 may be drafted during earlier work and finalized after K13; K15 hosting discovery begins at K0 even though final acceptance comes last.

Task template:

```text
ID / title:
Plan revision:
Dependencies:
Goal and contract sections:
Files expected to change:
In scope:
Out of scope:
Acceptance checks (observable):
Verification commands/manual steps:
Completion evidence: commit SHA, changed files, checks/results, limitations
```

Global rules for Hermes/DeepSeek:

1. Follow this stack, enums, policy constants, contracts, and permissions exactly. The frontend is three real HTML pages plus CSS/native JS; TypeScript, npm/npx, Node tooling, frontend package manifests, and SPA routing are prohibited in every task, including scaffolding, tests, and Docker. Do not add features or redesign workflow during implementation.
2. Inspect repository instructions before work. Keep edits inside the assigned card and required integration fixes.
3. All UI buttons must reach real endpoints; no mocked success, hardcoded counts, or hidden in-memory persistence in the delivered app.
4. Use the smallest implementation that meets acceptance criteria. Resolve ordinary implementation choices autonomously and note them briefly. Escalate only genuine product/security/contract contradictions, quoting the conflicting sections; unrelated ready work may continue.
5. Run applicable checks, inspect failures, fix them, and report actual evidence. “Code written” is not completion.
6. Commit each verified card with its K-number. Do not claim a commit or deployment happened if permissions/tools prevented it. Initialize Git at K0 if absent; never include secrets or demo DB files.
7. Keep a short card handoff: changed files, endpoints/contracts touched, verification, unresolved issue. Hermes verifies against this plan before moving Done.
8. No destructive Git resets or unrelated refactors. Existing local work must be preserved.
9. Begin with usable layout; reserve K12 for polish. Preserve time for real deployment and a clean walkthrough.

### K0 — Bootstrap and delivery preflight · 1h

**Dependencies:** none. **Contract:** §2–3, §14. **Files:** pyproject/lock, package skeleton, static shell, tests/conftest, Docker/Compose, ignore files, env example.

Implement app factory/lifespan, minimal schema initialization hook, public health route, root FileResponse/static mount and the three HTML page skeletons, Python test setup, one-process container, writable data volume, and launch configuration. Establish hosting choice (persistent container preferred; local ngrok fallback) and whether GitHub credentials/repository exist. Initialize Git if needed.

**Acceptance:** `uv sync` succeeds; `uv run pytest` passes; root, all three HTML pages, their JS/CSS, and health return 200 locally and in container; no frontend package manifest or compile command exists; container listens on `0.0.0.0`; repo excludes DB/secrets; hosting/GitHub prerequisites are recorded. Skeleton tests are replaced/extended as domain tables arrive. Missing credentials are surfaced now, not concealed until final card.

### K1 — Schema and coherent seed · 1.5h

**Depends on:** K0. **Contract:** §4, §8, §11. **Files:** db.py, seed.py, test_db.py.

Implement four tables/constraints/indexes, per-connection pragmas, user_version, relative-time fixtures with full versions/events, transactional seed/reset service (HTTP later).

**Acceptance:** six open/seven total baseline and exact §8 metrics at frozen clock; CP-8904 belongs to Sarah; CP-8905 has v1/v2 history; repeated boot preserves changes; reset generates fresh entity UUIDs; failing reset rolls back; unsupported/corrupt DB does not silently reset. All timestamps/events are coherent.

### K2 — Domain rules and validation · 0.75h

**Depends on:** K1. **Contract:** §4–6, §9. **Files:** models.py, workflow.py, test_workflow.py.

Implement enums, request validation, domain errors, visibility/permission and transition rules without route-specific logic.

**Acceptance:** table-driven tests cover every action/source/role including nonassigned reviewer, foreign submitter, terminal reassignment, empty rejection reason, and immutable metadata. No extra states or role shortcuts.

### K3 — Atomic workflow and automatic assignment · 1.5h

**Depends on:** K2. **Contract:** §5, §9. **Files:** workflow.py, db.py, test_workflow.py.

Implement create, assign/reassign, request changes, resubmit, approve/reject transaction paths, least-loaded assignment, OCC, versions, and events. Approval scanner integration is completed at K4 before any API is exposed.

**Acceptance:** create yields v1 and owner; no-reviewer case becomes pending; request/resubmit returns directly to review with same owner; no-op revision rejected; UUID/external ID unique; one successful stale-version race; forced audit failure rolls back all writes; record version increments once per action. No production route bypasses service rules.

### K4 — Explainable policy checks and approval gate · 0.75h

**Depends on:** K3. **Contract:** §6, §8. **Files:** preflight.py, workflow.py, test_preflight.py, test_workflow.py.

Implement exact policy table, structured findings and code-point offsets, historical scan support, server-side approval gate and decision metadata.

**Acceptance:** hero v1 returns exactly the two expected rule IDs; provided v2 passes; product/channel scoping and emoji offsets tested; direct service approval with blockers fails without mutation; passing preflight alone never changes status.

### K5 — Read API, queue, and metrics · 1h

**Depends on:** K4. **Contract:** §7, §9. **Files:** api.py, models.py, metrics.py, test_api.py, test_metrics.py.

Implement users, list/detail/metrics, identity and read visibility, filters/sorting/pagination, detail versions/history/allowed actions.

**Acceptance:** CP-8902 ranks first; baseline metric assertions pass at frozen time; Jessica sees only own data in every endpoint; filters and scope labels match contract; empty/no-sample values correct; health tests DB availability. Response models stable for frontend work.

### K6 — Mutation API and complete API journey · 1h

**Depends on:** K5. **Contract:** §5–6, §9, §11. **Files:** api.py, models.py, test_api.py.

Expose create/assign/request-changes/resubmit/approve/reject/reset and normalize domain/Pydantic errors.

**Acceptance:** one API integration test executes full hero journey with role switches, v1/v2/history assertions, approval, metric updates, reset. Additional tests reject forged actor/role fields, missing record version, blocked approval, forbidden owner, and disabled reset. All mutations return the agreed envelopes/statuses.

### K7 — Queue, navigation, personas · 1.5h

**Depends on:** K6. **Contract:** §7–10. **Files:** static/index.html, common.js, queue.js, styles.css.

Build queue HTML, shared API/persona helpers, ordinary links to submit.html and submission.html, identity persistence, queue, metrics, URL-query filters, search, completed view, loading/empty/error states. Apply base visual tokens now.

**Acceptance:** real API drives rows/counts; filters combine; completed CP-8905 is accessible; selection follows a normal link to submission.html?id=<uuid>; refresh/back retain useful context; stale persona fetches are discarded; 390px layout usable. No fake buttons or hardcoded metrics.

### K8 — Review workspace and ownership · 2.5h

**Depends on:** K7. **Contract:** §5–6, §9–10. **Files:** static assets.

Build submission.html and submission.js with full version selector, safe copy highlighting, preflight, assignment/reassignment, decision forms, immutable timeline, terminal display, pending/error/conflict handling.

**Acceptance:** Sarah requests changes on CP-8904 through real API; Mark cannot decide Sarah's work; pending work can be assigned; rejection requires reason; old versions clearly read-only; blocked approval explains why; action failure preserves entered text; timeline shows version/actor/time/feedback.

### K9 — Submitter and revision loop · 1h

**Depends on:** K8. **Contract:** §5, §8–10. **Files:** static assets.

Extend index.html and submission.html with own submissions experience, latest feedback, inline copy/URL revision form, persona-switch continuity, v2 success handling.

**Acceptance:** exact golden path through approved v2 works in browser; historical v1 remains selectable; switching persona keeps context and correct actions; unchanged revision is rejected clearly; no artificial “assign again” handoff after resubmit.

### K10 — Structured intake · 1h

**Depends on:** K9. **Contract:** §4–5, §9–10. **Files:** static assets; API fixes only if needed.

Build submit.html and submit.js with all intake fields, affiliate condition, validation/errors, pending submit state, and normal navigation to submission.html?id=<uuid> on success.

**Acceptance:** valid new affiliate request appears with assigned reviewer and v1/history; invalid fields display actionable messages; launch/date and URL rules honored; simulated ambiguous network failure does not auto-create a duplicate. New work changes queue/metrics.

### K11 — Reset and evaluator ergonomics · 0.5h

**Depends on:** K10. **Contract:** §8, §11. **Files:** static assets; reset integration tests if needed.

Wire reset confirmation, shared-state label, success announcement, cache/persona refresh.

**Acceptance:** completed walkthrough resets to baseline without server restart; another stale record returns 404 and useful UI; reviewer-only reset control; cancel has no effect; baseline is ready for repeated presentation.

### K12 — Focused visual and accessibility pass · 1.5h

**Depends on:** K11. **Contract:** §10. **Files:** primarily styles.css and semantic HTML.

Refine hierarchy, table density, context/actions, findings, timeline, forms, focus, contrast, mobile stacking, and readable dates/statuses using frozen tokens.

**Acceptance:** inspect queue/intake/detail at 390px and 1280px; keyboard completes request-changes and revision; labels/focus/live errors work; no clipped action buttons or body overflow; no console errors. No new product features or design-reference dependency.

### K13 — Integration and failure verification · 2h

**Depends on:** K12. **Contract:** §12. **Files:** targeted fixes/tests, docs/VERIFICATION.md.

Run complete automated suite and manual/browser checklist; verify clean boot, restart persistence, OCC race, rapid clicks/persona switching, invalid data, unreachable API handling, and full lifecycle.

**Acceptance:** all required checks actually pass, no lost revisions/duplicate decisions, no hidden unauthorized actions, no normal-user-path server exceptions. Record results and remaining limitations; fix blockers before deployment sign-off. Never delete a corrupt DB as a “repair.”

### K14 — Evaluator docs · 0.75h

**Depends on:** K13 for finalization; draft earlier. **Contract:** §1, §8, §14–15. **Files:** README, docs/ARCHITECTURE.md, docs/PRODUCT_DECISIONS.md.

Write concise problem/outcome, deliverable links, tested quickstarts, personas, exact corrected demo copy, five-minute journey, architecture, fictional policies, assumptions, omissions, shared state and persistence limits. Describe throughput metrics without claiming measured uplift.

**Acceptance:** someone with only README can launch and complete walkthrough; every implemented claim matches behavior; production improvements clearly future work; no placeholder URL remains after K15.

### K15 — Publish and verify both deliverables · 1.25h

**Depends on:** K13–K14; hosting discovery at K0. **Contract:** §14–15. **Files:** deployment config/README/verification evidence.

Push final code to the intended GitHub repository, deploy that commit, verify from outside the local app process, and complete deployed golden path. Record commit and real URLs. Publication is part of the requested build; handle environment-required permissions/credentials explicitly.

**Acceptance:** repository link opens for evaluator and contains complete source/lock/docs; deployed root and health work; persistent restart verified; fresh browser runs full hero path and intake; reset leaves baseline; README includes demo and repo links. For ngrok, record process/tunnel keepalive requirements and verify the public URL; never claim an inactive or ephemeral URL is durable hosting.

### K16 — Final human presentation check · 0.5h

**Depends on:** K15. **Owner:** Josh with agent-prepared evidence.

Run the five-minute demo once, inspect copy and spacing, confirm assumptions/tradeoffs are explainable, and open both links as an evaluator would. Agent supplies a concise handoff even if human review happens later.

**Acceptance:** no broken route or placeholder, fictional policies unmistakable, exact approved version identifiable, reset works, and discussion can connect each feature to reduced coordination/rework. Human review pending must be labeled honestly; it must not prevent completing authorized implementation/deployment work.

## 14. Deployment and repository runbook

### Local path

```bash
uv sync --frozen
uv run pytest
uv run uvicorn clearpath.api:app --reload
```

Open `http://localhost:8000`. No credentials or services needed. Automatic seed initialization happens only for a fresh DB with demo mode enabled. Tests use isolated temporary DBs.

### Container path

```bash
docker compose up --build -d
```

Compose maps port 8000, sets `DATABASE_PATH=/data/clearpath.db`, mounts a named volume at `/data`, and enables demo mode. Run as a nonroot user with writable `/data`. Install locked runtime dependencies, bind `0.0.0.0`, run one worker, omit `--reload`. Docker healthcheck uses Python stdlib HTTP client if curl is absent. A start command may expand `${PORT:-8000}` explicitly; JSON exec-form does not expand shell variables by itself.

Restart and confirm a test submission persists. `docker compose down` keeps volume data; do not instruct evaluators to delete volumes for normal startup/reset.

### Hosted route

At K0 choose an available container host with a persistent volume, or the explicit allowed fallback: run the local app/container and expose port 8000 with ngrok. No mandatory vendor in this plan; choose using available access, not a new architecture. For ngrok, keep both process and tunnel alive during evaluation, test any interstitial behavior, document limitations, and recheck the URL immediately before submitting. A real accessible URL is required; unavailable credentials are a concrete blocker, not license to invent one.

Use the same locked application and seed path as local; store DB outside image/static root. Verify root, `/api/health`, frontend assets, persona calls, mutations, and refresh over the public origin. Verify all three direct HTML URLs and their native JS/CSS assets over the public origin. No cross-origin API config or frontend build should be needed.

### GitHub handoff

Commit source and lockfile, exclude secrets/runtime DB, push the final verified branch, and confirm the evaluator can access it (public repo or explicitly granted private access). README includes live demo and repository URLs near the top, tested Python/Docker prerequisites, and the final walkthrough. Record the deployed commit SHA in verification notes. Do not create a circular docs-only commit requirement: state which application commit was tested if final changes only add evidence/links.

## 15. Final definition of done and presentation

Required handoff:

```text
Live demo: <verified public URL>
GitHub repo: <verified accessible URL>
Deployed application commit: <SHA>
Verification: <test result and deployed walkthrough result>
Known limits: synthetic shared demo; persona impersonation; pasted-copy policy checks
```

A hiring evaluator can open the URL without account setup, understand the workflow, prioritize urgent work, request changes, revise as Jessica, approve as Sarah, inspect approved v2 and untouched v1, create an automatically assigned request, view completed work, reset, clone, and run locally.

For the interview, explain:

- Why structured intake, ownership, and linked feedback address the stated bottleneck.
- Which assumptions are unvalidated and what you would ask the compliance team next: request volume, review steps, policy owners, artifact formats, channel/jurisdiction needs, and review-time distribution.
- Why immutable versions, transactions, and explicit state transitions protect the approval record.
- How metrics would evaluate a pilot: observed turnaround/completions/rework compared with an actual baseline, plus reviewer time measurements the demo does not yet capture.
- What production work comes next: real identity/access controls, governed legal policies, managed immutable assets, richer review collaboration, durable notifications, operational monitoring/backups, and migration to PostgreSQL when workload requires it.

The final product remains one application, one SQLite database, one container, and a complete human review loop. The assignment is finished only when both links work and the deployed workflow has been verified.
