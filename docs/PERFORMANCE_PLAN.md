# Performance improvement plan

**Status: feasibility review and implementation handoff only. No application optimizations have been applied.** Checked against the current API, queue UI, metrics implementation, schema initialization, tests, and [measured baseline](PERFORMANCE.md).

## Recommendation and time budget

Assume **one implementer with 3–4 focused hours**, an installed development environment, and the existing fixtures/tools. This is an estimate, not a delivery guarantee. The highest-impact scope is **bounded queue summaries with working pagination, plus SQL metric aggregation**, followed by focused correctness and performance checks.

The original plan was a useful backlog but too large for that window: half a day of preparation, cursor navigation, schema migrations, ID allocation changes, compression, and a sustained capacity study would turn it into multiple sessions. Do not require every aspirational performance target to pass before documenting a useful, correct improvement.

| Priority | Change | Expected benefit and confidence | Budget |
|---|---|---|---|
| P0 | Summary-only queue, 50-row default, API + UI pagination together | Highest: bounds response size, content queries, Python objects, and DOM rows. Bottleneck directly measured. | 75–105 min |
| P0 | SQL metrics with unchanged semantics | High: removes full-corpus Python materialization/sorting. Bottleneck directly measured. | 35–50 min |
| Required | Focused harness updates, regression checks, before/after evidence | Makes the improvement reviewable; protects visibility and workflow integrity. | 45–60 min |
| Setup | Confirm clean baseline, contract and fixture availability | Avoids redoing the completed baseline. | 10–15 min |
| Conditional P1 | One measured index improvement | May help remaining sorting/scans; query-plan dependent. Includes migration/tests. | Separate 30–60 min |
| Later | Writer profiling, sequence redesign, caching, full capacity study | Useful only after the dominant read work is removed or measured separately. | Separate session |

Core estimate: **165–230 minutes**. Reserve validation time instead of filling the window with optional features. If new compatibility or timestamp issues exceed the budget, reduce scope rather than weaken tests.

**90-minute fallback:** implement and verify SQL metrics as one complete change, then document the remaining queue work. This is a smaller improvement, not a resolution of the large queue/network bottleneck. Never ship a truncated queue without navigation.

**6–8-hour stretch:** complete the core, profile and add justified indexes, repeat measurements, and run a mixed read/write smoke test. Full sustained capacity validation on a public host remains a separate deliverable.

## What the evidence establishes

| Observation | Baseline evidence | Interpretation |
|---|---|---|
| Queue returns the whole backlog and fetches content per row | At 10k: 6,006 rows, 6,008 SELECTs, 10.46 MiB JSON. At 100k: 105.04 MiB JSON. | Confirmed primary bottleneck; pagination and omission of content are complementary fixes. |
| Metrics load and sort broad rows in Python | Three SELECTs, about 210 B response; concurrent p95 17.1 s at 100k. | Query count alone is misleading. Aggregate in SQLite without building Python queues. |
| Queue sorts outside its selected index | `USE TEMP B-TREE FOR ORDER BY` | Worth reassessing after pagination; does not establish which replacement index will win. |
| Writes slow as the corpus grows | Four-step lifecycle p95 3.81 s at 100k, concurrency 10. | Reviewer-load and numeric-ID scans are suspects, not proven causes. Profile before redesigning them. |
| Browser downloads and renders thousands of rows | 10k mobile-emulated cold visit: 56.83 s, 10.53 MiB transfer. | Bound application data first. Compression cannot eliminate row rendering or per-row SQL. |

The queue UI currently awaits queue, metrics, and config in one `Promise.all`; either slow read can delay the visible workspace. Both P0 fixes therefore matter to perceived speed. Detail/history is already much faster in the tested small-history case and is not the first optimization target.

Baseline limitations: single local run, client and server share a workstation, 100k concurrency differs across profiles, and the 100k queue has very few samples. Write integrity passed the tested cases; mixed reads/writes, long soaks, crash recovery, and production capacity were not established.

## Core implementation contract

### 1. Bounded queue summaries and complete navigation

Files: [clearpath/api.py](../clearpath/api.py), [static/queue.js](../static/queue.js), and the queue HTML/CSS as needed.

**API**

- Use an explicit list projection from `submissions`. Do not call the content-fetching `_projection` for each list row and do not join content merely to discard it. Detail responses retain full copy, URLs, history, and review controls.
- The UI consumes `id`, `external_id`, `title`, `partner`, `channel`, `product`, `status`, `assigned_reviewer_id`, `target_launch_date`, `sla_breach_at`, and `decided_at`. Select additional ordering or compatibility metadata deliberately; audit tests/callers before removing current summary fields such as `urgency` or `allowed_actions`.
- Keep the existing **`submissions` response key**, adding `total`, `limit`, and `offset`. Do not rename it to `items` unnecessarily. Proposed contract: `limit=50` by default, integer range 1–100; `offset=0` by default, nonnegative integer. Reject invalid values with the normal API validation response.
- Fetch one bounded page and one exact count using the **same parameterized visibility/filter predicates**. Together with identity resolution, target at most three SELECTs regardless of corpus size. An exact count still scans qualifying data; bounded query count is not constant-time execution.
- Preserve submitter ownership and reviewer/mine/status/search/risk/completed behavior. A submitter must never see another submitter's rows **or counts**.
- Preserve active ordering: `sla_breach_at, target_launch_date, submitted_at, external_id`; completed ordering: `decided_at DESC, external_id`. The unique external ID breaks timestamp ties.

**Choose offset pagination for this short pass.** It is simpler to integrate with current GET filters and previous/next links. Measure the first, middle, and last pages at 100k. Deep offsets may remain slow; document this and defer cursor pagination until it is justified. Cursors require filter binding, both sort directions, and backward navigation, increasing implementation and test scope.

A stable sort prevents duplicates across pages of an unchanged dataset. Concurrent decisions or new submissions can move records between offset pages; do not claim snapshot-consistent browsing. If count/page consistency within one response is needed, use a short read transaction, never a transaction spanning user navigation.

**UI**

- Update API and UI together. Add accessible previous/next links, disabled boundary states, and a range such as “1–50 of 6,006 campaigns.” Use `total`, not page length, for the matching campaign count.
- Add pagination parameters to the current query allowlist. Preserve filters in navigation and the saved detail-back URL; reset offset on filter, active/completed, and persona changes.
- Keep dashboard metrics scoped to all records visible to that persona, independent of queue page and queue filters. Preserve the “unassigned overall” distinction.
- Handle an empty dataset and a now-out-of-range page explicitly, with a route back to the first page. Do not show a misleading range or trap users after records change status.
- Keep the current safe text rendering, mobile layout, error state, and detail workflow.

**Required verification:** a fixture with more than 100 matching records; first/middle/last/empty pages; no duplicates or missing IDs when traversing an unchanged fixture; timestamp ties; completed ordering; combined filters; submitter count/row isolation; invalid limits/offsets; filter reset and detail return. Verify explicit `limit=100` remains supported: the 50-row target is for the default page, not all valid requests.

### 2. SQL metrics with exact business meaning

File: [clearpath/metrics.py](../clearpath/metrics.py). Change `compute_metrics` to perform conditional counts and duration aggregation directly. The existing `queue` helper has its own callers/semantics; do not broadly rewrite it merely because metrics currently calls it.

- Preserve response keys, `scope`, `as_of`, and one captured UTC `now` per request.
- Open, breached, and unassigned counts include only open statuses; a deadline exactly at `now` is breached.
- Completion count includes terminal decisions in the inclusive interval `[now - 7 days, now]`.
- Turnaround includes terminal decisions in `[now - 30 days, now]`, excluding null decision dates and negative elapsed durations. Preserve elapsed days, sample size, and `null` average for an empty sample.
- **Negative-duration exclusion applies to turnaround, not the completion count.** Do not silently change the latter's existing semantics.
- Preserve persona scope. The API passes the submitter's ID for own-submission metrics; reviewers see the team aggregate. Check direct helper callers as well.
- Preserve one-decimal rounding behavior; aggregate the duration in SQL and retain Python rounding where appropriate. Test rounding boundaries and numeric precision rather than assuming SQL `ROUND` matches Python `round`.

**Timestamp trap:** fixtures/tests include ISO timestamps with both `Z` and `+00:00`, and fractional seconds are possible. Blind text comparisons can change boundary behavior. Use a consistent time representation for comparisons/duration arithmetic; SQLite date conversion is a candidate, but verify its precision at the supported boundaries. Do not introduce a full timestamp migration in this pass. Date conversion may limit index use; correctness comes first, and a SQL scan can still avoid the much larger Python allocation/sort cost.

Test exact and just-outside 7/30-day boundaries, future decisions, zero and negative durations, empty samples, mixed timestamp serialization, scope, and rounding. Compare against the existing Python semantics with a fixed clock and deterministic varied fixtures. This is an O(N) aggregate unless plans prove otherwise; describe the gain as reduced processing/allocation, not constant-time metrics.

## Verification within the short window

The current runner hardcodes workload sizes/concurrency; it does **not** already provide a targeted 200-request, concurrency-10 pagination profile. Include the small harness change in the estimate, rather than presenting nonexistent command flags as available.

1. Preserve the committed baseline directory. Record application revision/source hashes, environment, corpus manifest, and exact workload configuration in a new output directory. Use isolated fixtures, never the normal demo database.
2. Extend the runner to request `limit=50` explicitly, record returned row count/total/bytes, and support focused queue/metrics phases at concurrency 1 and 10, with at least 200 requests per concurrent phase. Update diagnostics to inspect the actual revised SQL, not just their existing hardcoded unbounded query.
3. Run the existing suite plus new pagination/metrics tests. Run browser smoke coverage including more than one page, a submitter, completed work, mobile layout, and detail return. Retain lifecycle and competing-approval checks.
4. Benchmark 10k and 100k first-page queue and metrics on an otherwise idle machine. Sample filtered and deep pages separately. Keep browser/network testing separate from HTTP load so they do not contend.
5. Re-run the existing cold/warm, broadband, and mobile browser profiles. Repeat the mobile visit three times if time permits; report each result or range, not a credible p95 from three visits. Include the 100k fixture if claiming the 100k browser target.
6. Update `docs/PERFORMANCE.md` with measured before/after values and remaining limits. Save machine-readable artifacts; label unrun checks explicitly.

**Comparison caveat:** the old queue returns the full backlog; the new endpoint returns a page. This is an intentional change in work per response. Compare “time to usable first page,” bytes, row count, and SQL count together. Do not present a request-throughput ratio as identical-work speedup. Historical 100k timings also use different concurrency; a matched old/new run is needed for a precise latency ratio.

### Mandatory correctness versus performance targets

| Check | Requirement or target | If unmet |
|---|---|---|
| Visibility, navigation, metrics, audit/version integrity | Mandatory: tests pass; no lost records or hidden remaining pages | Fix or revert the affected change; do not declare completion. |
| Queue bounds | Mandatory: default at most 50 rows, validated max 100, no content query per row | Fix before accepting queue work. |
| Default queue payload / SQL count | Target ≤64 KiB decoded JSON and ≤3 SELECTs on benchmark fixtures | Inspect fields/count queries; report actual values. Arbitrary long input may exceed a fixture byte budget. |
| Queue and metrics p95 at 100k | Target ≤250 ms, concurrency 10, ≥200 requests each | Profile remaining SQL before adding caches or workers. |
| Whole-run sampled server RSS | Target ≤256 MiB, separate from client memory | Attribute peaks to phases; do not claim pagination alone guarantees this. |
| Mobile-emulated cold queue | Target usable within 3 s at 100k | Inspect remaining requests/bytes/CPU; distinguish local emulation from public hosting. |
| Four-step lifecycle p95 | Later target ≤1 s at concurrency 10; correctness remains mandatory now | Document remaining writer bottleneck; read fixes need not improve isolated write timing. |
| HTTP failures | Zero unexpected errors in the tested workload; deliberate stale-write 409s counted separately | Investigate; never hide failures with retries or omit them from latency. |

These are engineering budgets, not achieved results or service-level guarantees. A documented miss on an aspirational latency target should guide the next pass; it is not a reason to start an unbounded optimization cycle within a fixed session.

## Conditional follow-up: spend time only where measurements point

### Indexes after the new queries exist

Inspect `EXPLAIN QUERY PLAN` for first/deep pages, completed work, broad search, submitter scope, and reviewer filters. Candidate directions are an open-work partial index matching the sort, a completed sort index, and `(assigned_reviewer_id, status)` for reviewer load. Existing single-column indexes may already help; add only demonstrably useful indexes and compare write latency/storage cost.

Two feasibility constraints matter:

- `initialize_schema` creates indexes only for a fresh database and otherwise validates/returns. Editing `_SCHEMA` alone will not upgrade existing installations. An additive index update must cover existing databases, preserve startup/seed return semantics, and be tested on fresh and populated files. Measure index-build startup cost at 100k.
- Search uses `%term%` across title, partner, and external ID. An ordinary index is not a general fix for leading-wildcard substring searches. Preserve search semantics initially; defer FTS and its migration/product decisions.

### Write throughput

Measure intake SQL and lock wait separately before blaming the external-ID aggregate. A transactional counter introduces migration, seed/reset, rollback, and uniqueness work; it is **not** a short-window default. An expression index may be a smaller alternative if plans and timings justify it. Keep `BEGIN IMMEDIATE`, optimistic record versions, and atomic content/audit updates.

### Network delivery

After bounding data, evaluate negotiated gzip through the existing stack if transfer is still material. Verify actual encoding, `Vary`, wire bytes, and CPU. Defer Brotli dependencies, CDN work, and static asset fingerprinting unless measurements show they are worth the setup. Existing conditional caching should be measured, not described as absent. Keep persona-specific business data fresh and isolated.

### Sustained capacity and hosting

A later study should use at least three repeated runs, fixed-arrival ramps, a 15–30-minute mixed read/write soak, uneven reviewer loads, random detail/history sizes, and explicit errors, dropped arrivals, scheduling lag, server/client resource use, and lock waits. Separate generator and server when establishing capacity. Test the actual hosting resource limits, TLS, and cold starts before making public-host claims.

Do not introduce multiple SQLite writer processes, migrate to Postgres, or cache metrics without evidence that the remaining bottleneck warrants it. Free-tier availability/persistence and demo identity are separate deployment concerns; a fast local benchmark does not resolve them.

## Handoff and definition of done

Use two coherent implementation changes: **queue API + UI + tests**, and **SQL metrics + tests**; SQL metrics can go first if an early, independently verifiable result is valuable. Follow with the focused benchmark/documentation update. Avoid separate API/UI releases that leave pagination inaccessible.

The core pass is complete when both P0 changes work, mandatory correctness checks pass, reproducible measurements are saved, and target misses/unrun checks are clearly documented. The final report should say what improved, what was measured, and what still limits scale. Synthetic dashboard totals demonstrate a large fixture; they do not prove real compliance-team productivity or production capacity.

References: [baseline and raw evidence](PERFORMANCE.md), [benchmark tooling](../scripts/performance/README.md), [metric boundary tests](../tests/test_metrics.py), [schema initialization](../clearpath/db.py).
