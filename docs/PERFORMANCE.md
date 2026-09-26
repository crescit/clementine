# Performance baseline and implementation handoff

**Status: measured baseline complete; application optimizations NOT implemented.** The owner requested recommendations for a later implementation pass. Application source hashes were unchanged throughout the load run. Results were collected on 2026-09-25 Pacific (2026-09-26 UTC).

## Verdict

The current app preserves workflow correctness under the tested writes, but **the unbounded queue and Python-side metrics do not meet an interactive large-backlog target**. Do not present these results as proof that the app is ready for high-volume production. The measurements identify where to improve it. The original seven-campaign smoke test was not representative of scale.

## Measured baseline

One Python 3.12.13 Uvicorn worker, macOS arm64, 10 logical CPUs, HTTP/1.1 over loopback with persistent connections. Client and server share the development machine. This is a single local run, not an isolated cloud or free-tier benchmark. No performance changes or caching were introduced.

Each fixture contains 240 personas (40 reviewers, 200 submitters), all three products, all five channels, roughly 60% open work, varied copy around 0.5–2 KiB, revisions, and version-linked history. The old backlog spans 90 days deliberately; its operational metrics are synthetic, not team-performance claims.

| Initial submissions | Content versions | Audit events | Database | Sampled peak server RSS |
|---:|---:|---:|---:|---:|
| 1,000 | 1,220 | 2,883 | 3.2 MiB | 119.9 MiB |
| 10,000 | 12,200 | 28,893 | 31.0 MiB | 523.4 MiB |
| 100,000 | 122,000 | 288,993 | 310.5 MiB | 1070.7 MiB |

RSS was sampled once per second and can miss short peaks; it covers the whole run, not a particular endpoint. Fixture counts above precede load-created records.

| Workload | 1,000 records: p95 | 10,000 records: p95 | 100,000 records: p95 |
|---|---:|---:|---:|
| Queue, one client | 46 ms | 323 ms | 3,407 ms |
| Queue, concurrent | 1,260 ms | 12,448 ms | 7,483 ms |
| Metrics, concurrent | 1,115 ms | 10,948 ms | 17,105 ms |
| Detail/history, 20 clients | 92 ms | 81 ms | 101 ms |
| Selective search, 5 clients | 38 ms | 34 ms | 88 ms |
| Mixed reads, concurrent | 1,180 ms | 11,740 ms | 12,652 ms |
| Four-step review lifecycle, 10 clients | 121 ms | 478 ms | 3,811 ms |

Concurrency is intentionally bounded to avoid overwhelming the workstation: queue uses 10 clients at 1k/10k and **2 at 100k**; metrics uses 10 then **5 at 100k**; mixed reads uses 10 then **3 at 100k**. These columns therefore are not a same-concurrency scaling curve. The 100k queue has only 3 sequential and 4 concurrent samples, so its p95 is effectively the maximum, not a reliable tail estimate. Full counts and percentiles are in raw JSON.

| Queue response | Open rows returned | Uncompressed JSON per request |
|---|---:|---:|
| 1,000 submissions | 606 | 1.05 MiB |
| 10,000 submissions | 6,006 | 10.46 MiB |
| 100,000 submissions | 60,006 | 105.04 MiB |

At 10k, a burst of ten queue clients achieved only **0.85 successful queue requests/s**. Conversely, a paced mixed workload offered at 10 requests/s for 10 seconds completed all 100 requests (9.82/s including drain), p95 **322 ms**, no dropped arrivals, and 1.11 ms p95 scheduling lag. This demonstrates how burst concurrency can behave very differently from a modest arrival rate; neither profile establishes sustained maximum capacity. The mix is 60% detail/history, 20% queue, 20% metrics.

### Write integrity

All **100 four-step lifecycles** completed: create → request changes → resubmit → approve, representing 400 workflow writes across the three corpora. Post-run checks found the expected status, two content versions, and five events per lifecycle. There were no foreign-key errors and all SQLite integrity checks passed.

Each corpus also sent 20 approvals against one record/version concurrently: exactly **one approval committed and 19 returned VERSION_CONFLICT**, with one approval event in the database. These 409s are expected correctness results, not throughput wins.

At 100k, 20 lifecycles at concurrency 10 took 4.03 seconds (4.96 lifecycles/s, about 19.8 workflow writes/s for this short batch). This is not a sustained capacity claim. Writer and reader phases were separate: mixed read/write traffic, crashes, multi-process writers, and long soaks remain untested.

## Network and browser cost

Chrome DevTools emulates bandwidth/latency against the 10k fixture. “Ready” means all queue rows and metrics are rendered, not merely HTML loaded. Fresh contexts give cold caches; the warm measurement revisits within the same context. Each profile is **one exploratory visit**, not a browser p95.

| Profile | Time to usable queue | Transfer | Rendered rows |
|---|---:|---:|---:|
| unthrottled / cold_cache | 1.15 s | 10.53 MiB | 6,006 |
| unthrottled / warm_cache | 1.27 s | 10.50 MiB | 6,006 |
| broadband_10Mbps_40ms / cold_cache | 10.16 s | 10.53 MiB | 6,006 |
| mobile_1.6Mbps_150ms / cold_cache | 56.83 s | 10.53 MiB | 6,006 |

This is network emulation, not a physical WAN, packet loss, TLS handshake, DNS, CDN, multi-region, or public-host test. Warm static assets cannot solve a roughly 11 MB business response or thousands of DOM rows. No application compression changes were applied.

## Confirmed bottlenecks

1. **Unbounded queue plus one content query per row.** Actual API tracing at 10k returned 6,006 rows using **6,008 SELECTs**: identity, list, then one content lookup per row. Full ad copy and asset URLs are returned even though the queue displays neither. See `clearpath/api.py`: `list_submissions` and `_projection`.
2. **Metrics load and sort the entire visible corpus in Python.** `/api/metrics` executes only three SELECTs, but two materialize broad rows and `metrics.queue` sorts them. The final response is about 210 bytes while concurrent p95 reaches 10.9 seconds at 10k and 17.1 seconds at 100k. See `clearpath/metrics.py`: `compute_metrics` and `queue`.
3. **Sorting does not match an index.** EXPLAIN reports `USE TEMP B-TREE FOR ORDER BY` on the queue; it starts from the status index and sorts. See the raw query plans and `clearpath/db.py`.
4. **Intake does work proportional to the corpus under a writer lock.** `_least_loaded_reviewer` counts assigned work, while external-ID creation evaluates `MAX(CAST(SUBSTR(external_id,4) AS INTEGER))`. The plan reports a covering external-ID index search, but this is not an index on the numeric expression. Measure row visits/time before choosing a sequence or expression index. The 100k write slowdown is observed; this is a code/query-plan hypothesis for the cause, not a CPU profile.
5. **The browser renders the entire open queue.** The network report records DOM size and payload costs. Compression alone would not bound parsing, memory, or table layout.

## Prioritized implementation recommendations — not applied

### P0: bounded queue summaries and pagination

- Return only the metadata used by queue rows; fetch copy, URLs, and policy findings on the detail endpoint. Eliminate `_projection` per-row content queries from the list path.
- Default to 50 rows; validate a hard limit of 100. Return a pagination envelope and update `static/queue.js` with next/previous navigation and a visible result range. Keep metrics scoped to all visible records, independent of page/filter.
- Preserve ownership/search/status/reviewer/risk filters and stable SLA/launch/submitted/ID ordering. Use cursor pagination for large/deep queues; completed records need their own descending decision-date cursor. An indexed offset-based first step is acceptable if deep-page behavior is measured and documented.
- Add tests for tied timestamps, page traversal without duplicates, invalid limits/cursors, role visibility, completed ordering, and filters retained when returning from detail. Update the harness to request a fixed 50-row page explicitly; label the changed list semantics in before/after comparisons.

### P0: aggregate metrics in SQL

- Replace queue materialization/sorting with SQL `COUNT`/conditional aggregates and `AVG` over the visible scope. Use one captured UTC timestamp. Preserve the inclusive 7-day completion and 30-day turnaround windows, exclusion of future/negative durations, null average with zero sample, and submitter visibility.
- Keep calculations exact initially. Add a short-lived cache only if measurements justify it and mutation invalidation/freshness are tested. Do not hardcode attractive metric values.
- Extend boundary/visibility tests and compare against a simple reference implementation on randomized fixtures.

### P1: targeted indexes and shorter write transactions

- Inspect query plans after the query redesign. Consider an open-work partial index aligned to queue ordering, a completed decision-date index, and `(assigned_reviewer_id, status)` for reviewer load. Include submitter-scoped/filter paths in index validation.
- Implement safe additive index migration for existing databases, not only fresh creation. Avoid adding redundant indexes without measuring write cost.
- Replace the numeric external-ID aggregate with a transactionally allocated counter, or a measured expression-index alternative. Preserve uniqueness, seed/reset semantics, and atomic rollback. Never use row count as an ID.
- Retain `BEGIN IMMEDIATE`, stale-version checks, versioned content, and atomic audit writes. Do not disable integrity checks or add workers as a substitute for reducing work under the SQLite writer lock.

### P1: network delivery after payload reduction

- Enable negotiated gzip/Brotli where the hosting stack supports it; verify `Content-Encoding` and `Vary: Accept-Encoding` with real HTTP tests. Measure CPU as well as transferred bytes.
- Use versioned static asset URLs and deliberate cache headers. Existing ETag/conditional behavior should be measured; do not describe caching as completely absent. Keep business-data freshness separate from static caching.
- Rerun browser broadband/mobile profiles after pagination. Show actual wire sizes; do not substitute theoretical compressed sizes for measurements.

### P2: capacity and rollout validation

- Run at least three independent repetitions, longer fixed-arrival ramps, a 15–30 minute soak, and mixed read/write traffic. Report errors/timeouts, schedule lag, dropped arrivals, successful throughput, and all-request latency together.
- Include random record IDs, long histories, sparse/broad searches, uneven reviewer workloads, larger copy, and at least the expected peak dataset. Current detail tests reuse one small golden record and are warm-cache best cases.
- Separate load generator from server, fix CPU/RAM limits, test TLS/public hosting and cold starts, and capture server/client CPU, RSS, I/O, and database lock time. Current RSS is sampled, and client/server CPU contention is not isolated.
- If measured sustained write demand exceeds a single SQLite writer, evaluate a managed relational database. A database migration is not the first fix for an oversized list API.

## Suggested next-pass acceptance gates (targets, not achieved results)

At 100k records on the same documented resource budget:

- Queue page ≤50 rows, ≤64 KiB decoded JSON, and a bounded number of SELECTs (target ≤3 independent of corpus size).
- Queue and metrics p95 ≤250 ms at concurrency 10 over ≥200 requests per phase.
- Four-step lifecycle p95 ≤1 second at concurrency 10, with the existing integrity guarantees.
- Sampled server RSS ≤256 MiB in that test; report client RSS separately.
- Cold mobile-emulated queue usable within 3 seconds after payload reduction; repeat visits and report distribution.
- Zero unexpected errors, no lost audit/version records, and one winner for competing decisions. Record overload separately rather than masking errors with retries.

If a target fails, report it and profile the remaining bottleneck. These are engineering budgets to evaluate, not service-level promises.

## Reproduction, artifacts, and progress

Commands and workload definitions: [benchmark tooling guide](../scripts/performance/README.md). Run with fresh paths; generators refuse overwrite. Databases and runtime logs are ignored by Git. JSON evidence and source hashes are retained.

- [Environment and application source hashes](benchmarks/baseline-2026-09-25/environment.json)
- [1k results](benchmarks/baseline-2026-09-25/results-1000.json), [10k results](benchmarks/baseline-2026-09-25/results-10000.json), [100k results](benchmarks/baseline-2026-09-25/results-100000.json)
- [Actual SQL statement counts](benchmarks/baseline-2026-09-25/sql-10000.json)
- [Browser/network evidence](benchmarks/baseline-2026-09-25/network-10000.json)
- [No application edits during the run](benchmarks/baseline-2026-09-25/source_check.json)

Progress: inspected bottlenecks → built safe/reproducible synthetic fixtures → executed HTTP/concurrency/write-integrity baseline → traced real SQL → measured browser/network behavior → documented prioritized handoff. **No before/after improvement is claimed because the owner explicitly deferred implementation.**

To show a larger workspace, use the generator/server command in the tooling guide. Synthetic dashboard counts show the size and state of the fixture; the benchmark report is the evidence about throughput. Do not present synthetic completions or turnaround as real customer outcomes.
