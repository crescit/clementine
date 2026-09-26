# Performance baseline and P0 implementation results

**Status: P0 optimizations implemented and re-measured.** Bounded queue summaries with pagination and SQL metric aggregation shipped on 2026-09-26. The historical unbounded baseline remains in [baseline-2026-09-25](benchmarks/baseline-2026-09-25/) for comparison. New evidence: [p0-2026-09-26](benchmarks/p0-2026-09-26/).

## Verdict

**Final verification:** after a timestamp-boundary correctness fix, the final 100k run measured queue p95 **120 ms**, metrics p95 **5.56 s**, and sampled peak RSS **125.4 MiB**. The initial P0 numbers below remain historical evidence, not the final metrics timing.

The unbounded queue response and Python-side metric materialization have been removed. The queue API meets the first-page latency target in this local run, but the UI also waits for metrics, which remain a bottleneck under concurrency. At 100k records, a default queue page returns **50 rows / ~27 KiB / 3 SELECTs** with concurrent p95 **122 ms**. Metrics still scan the corpus in SQL (no full Python materialization) and are much faster than baseline, but concurrent p95 at 100k (**3.7 s**) remains above the 250 ms engineering target — indexes and/or caching are the next lever. Write lifecycle latency is essentially unchanged (expected; P0 was read-path only).

## Initial P0 before → after (same machine class, loopback HTTP/1.1)

Baseline concurrency at 100k was reduced (queue×2, metrics×5). P0 re-measure uses **concurrency 10 and ≥200 requests** for queue and metrics on every corpus size. Compare “time to usable first page,” bytes, and SQL count — not identical-work throughput.

| Workload | Baseline 10k p95 | P0 10k p95 | Baseline 100k p95 | P0 100k p95 |
|---|---:|---:|---:|---:|
| Queue (first page / was full backlog) | 12,448 ms (10 clients, full backlog) | **87 ms** (10 clients, `limit=50`) | 7,483 ms (2 clients, full backlog) | **122 ms** (10 clients, `limit=50`) |
| Metrics | 10,948 ms (10 clients) | **396 ms** (10 clients) | 17,105 ms (5 clients) | **3,744 ms** (10 clients) |
| Queue sequential first page | 323 ms | **7 ms** | 3,407 ms | **33 ms** |

| Queue response shape | Baseline 10k | P0 10k | Baseline 100k | P0 100k |
|---|---:|---:|---:|---:|
| Rows returned | 6,006 | **50** | 60,006 | **50** |
| Uncompressed JSON (p50 / probe) | 10.46 MiB | **~27 KiB** | 105.04 MiB | **~27 KiB** |
| SELECTs (traced) | 6,008 | **3** | — | **3** |

Mandatory correctness: default ≤50 rows, max 100, no per-row content query — **met**. Target ≤64 KiB decoded JSON and ≤3 SELECTs on fixtures — **met**. Queue concurrent p95 ≤250 ms at 100k — **met (122 ms)**. Metrics concurrent p95 ≤250 ms at 100k — **not met (3.7 s)**. Browser/mobile re-measure was not run in the initial P0 pass; see the final verification below. Lifecycle p95 ≤1 s — **not met; unchanged writer path**.

### Deep pages (new; not in baseline)

Offset pagination is correct but deep offsets remain costly at 100k (middle/last page p95 ~0.9 s at concurrency 5). Documented limit; cursor pagination deferred.

### Write integrity (unchanged guarantees)

All lifecycle and contested-approval checks passed on 1k/10k/100k P0 runs. Exactly one approval wins contested writes; foreign keys and SQLite integrity OK.

## What changed

1. **`GET /api/submissions`** returns summary rows only (no content join), with `total` / `limit` / `offset` (`limit` default 50, max 100). Same visibility/filter/sort semantics.
2. **Queue UI** paginates with previous/next, range label (`1–50 of N`), filter-preserving links, out-of-range redirect, and persona/filter offset reset. Metrics stay persona-scoped, independent of page.
3. **`compute_metrics`** aggregates in one SQL statement (conditional counts + turnaround average), preserving prior business rules including negative-duration exclusion on turnaround only.
4. **Harness** requests `limit=50`, records row/total metadata, and runs focused queue/metrics phases (concurrency 1 and 10, ≥200 concurrent samples).

## Remaining limits (next pass)

- Metrics at 100k still do an O(N) SQL scan with timestamp normalization (`REPLACE`); profile `EXPLAIN QUERY PLAN` and consider indexes or a short-lived cache only with invalidation tests.
- Deep `OFFSET` pages; consider cursors if Product needs last-page browsing.
- Write path (external-ID aggregate, reviewer load) unchanged — four-step lifecycle p95 still multi-second at 100k.
- Browser/network profiles have now been re-run during final verification below; sustained mixed-user browser load remains untested.
- Indexes (conditional P1) not added in this pass.

## Reproduction and artifacts

Commands: [benchmark tooling guide](../scripts/performance/README.md).

- Baseline (pre-change): [baseline-2026-09-25](benchmarks/baseline-2026-09-25/)
- P0 re-measure: [environment](benchmarks/p0-2026-09-26/environment.json), [1k](benchmarks/p0-2026-09-26/results-1000.json), [10k](benchmarks/p0-2026-09-26/results-10000.json), [100k](benchmarks/p0-2026-09-26/results-100000.json), [SQL 10k](benchmarks/p0-2026-09-26/sql-10000.json), [SQL 100k](benchmarks/p0-2026-09-26/sql-100000.json)
- Implementation plan: [PERFORMANCE_PLAN.md](PERFORMANCE_PLAN.md)

## Final delivery verification — 2026-09-26 UTC

Final code fixes equivalent zero-fraction UTC timestamp comparisons at inclusive boundaries and preserves direct reviewer-helper scope. **152 tests pass**. The full local Chrome acceptance flow passes, including revisions/approval, permissions, safe rendering, stale-tab recovery, mobile overflow, pagination, detail return, filter/persona resets, and out-of-range recovery.

[Final 100k HTTP results](benchmarks/final-2026-09-26/results-100000.json): 200 requests each at concurrency 10 give queue p95 **119.75 ms** and metrics p95 **5,556.62 ms**. Queue response is **27,505 bytes**, 50 rows, and [3 SELECTs](benchmarks/final-2026-09-26/sql-100000.json). Sampled peak server RSS is **125.4 MiB**. All 20 four-step lifecycles and the one-winner/19-conflict decision check pass; no unexpected phase errors or integrity failures. Lifecycle p95 is 4.00 s. Metrics and deep offsets remain known scale limits. Final timestamp normalization adds work; do not quote the earlier 3.74 s metrics timing as the final result.

[Browser/network measurement](benchmarks/p0-2026-09-26/network-100000.json) on the P0 build **before the final timestamp correction** rendered 50 rows at 100k: cold unthrottled 907 ms, warm 168 ms, broadband 386 ms, mobile emulation 1,274 ms; about 0.09 MiB cold resource transfer and no timeouts. These are single exploratory visits, not percentiles. The corrected build passed functional browser checks; its network timing was not repeated. No sustained or public-host capacity claim is made.

The harness's `metrics_aggregate` query-plan entry is a simplified count proxy, not the full metric query; use the actual SQL trace for investigation. Historical benchmark directories are preserved. Final environment/source hashes and integrity evidence are in [final-2026-09-26](benchmarks/final-2026-09-26/).
