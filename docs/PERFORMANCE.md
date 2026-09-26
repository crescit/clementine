# Performance baseline and P0 implementation results

**Status: P0 shipped; metrics timestamp path optimized and re-measured.** Bounded queue summaries with pagination and SQL metric aggregation. Historical unbounded baseline: [baseline-2026-09-25](benchmarks/baseline-2026-09-25/). Latest metrics fix: [metrics-fix-2026-09-26](benchmarks/metrics-fix-2026-09-26/).

## Verdict

At 100k records (concurrency 10, ≥200 requests), the default queue page stays interactive (**128 ms** p95, 50 rows / ~27 KiB / 3 SELECTs). Metrics concurrent p95 is **266 ms** after a fast-path timestamp normalize — down from **5.56 s** with the correctness-oriented pad (and from **17.1 s** baseline). That is slightly above the 250 ms engineering budget; deep offsets and the write path remain separate limits.

## Metrics fix (after final verification pad)

The inclusive-boundary pad (`SUBSTR` / `REPLACE` on every comparison) preserved µs-correct comparisons but dominated CPU under concurrency. The current implementation:

- Uses a **GLOB fast path** for whole-second `Z` / `+00:00` forms (all generator fixtures), with the full six-digit pad only for mixed fractional forms
- Scans **open and terminal** rows in separate status-filtered queries so indexes apply and each timestamp is normalized once in a subquery

Boundary tests (including `.000000Z` vs whole-second and +1 µs exclusion) still pass. **152 tests** green.

| Metrics at 100k, concurrency 10, 200 requests | p95 |
|---|---:|
| Baseline (Python materialize) | 17,105 ms (5 clients) |
| Initial SQL aggregate | 3,744 ms |
| After boundary pad (final-2026-09-26) | 5,557 ms |
| **After fast-path + split scan** | **266 ms** |

Artifacts: [results](benchmarks/metrics-fix-2026-09-26/results-100000.json), [SQL trace](benchmarks/metrics-fix-2026-09-26/sql-100000.json) (metrics: 3 SELECTs — identity + open + terminal). Sequential metrics p95 **99 ms**. Queue concurrent p95 **128 ms** on the same run.

## Initial P0 before → after (historical)

Baseline concurrency at 100k was reduced (queue×2, metrics×5). P0 re-measure uses **concurrency 10 and ≥200 requests**. Compare “time to usable first page,” bytes, and SQL count — not identical-work throughput.

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

Mandatory correctness for queue bounds — **met**. Queue concurrent p95 ≤250 ms at 100k — **met**. Metrics ≤250 ms — **near miss at 266 ms** after the fast-path fix (was 5.56 s). Lifecycle p95 ≤1 s — **not met; unchanged writer path**.

### Deep pages

Offset pagination is correct but deep offsets remain costly at 100k (middle/last page p95 ~0.9–1.1 s at concurrency 5). Cursor pagination deferred.

### Write integrity

Lifecycle and contested-approval checks passed on measured runs. Exactly one approval wins contested writes; foreign keys and SQLite integrity OK.

## What changed

1. **`GET /api/submissions`** — summary rows, `total` / `limit` / `offset` (default 50, max 100).
2. **Queue UI** — previous/next, range label, filter-preserving links, out-of-range redirect, persona/filter offset reset.
3. **`compute_metrics`** — SQL aggregates with unchanged business rules; fast-path UTC normalize + split open/terminal scans.
4. **Harness** — explicit `limit=50`, row/total metadata, focused concurrent phases.

## Remaining limits

- Metrics still O(N) over visible rows; **266 ms** vs **250 ms** target — indexes or a short-lived cache (with write invalidation) if the budget must be met strictly.
- Deep `OFFSET` pages; write-path lifecycle latency; sustained/public-host capacity unclaimed.

## Reproduction and artifacts

Commands: [benchmark tooling guide](../scripts/performance/README.md).

- Baseline: [baseline-2026-09-25](benchmarks/baseline-2026-09-25/)
- P0 re-measure: [p0-2026-09-26](benchmarks/p0-2026-09-26/)
- Post-pad verification: [final-2026-09-26](benchmarks/final-2026-09-26/) (metrics p95 5.56 s — superseded)
- **Current metrics fix:** [metrics-fix-2026-09-26](benchmarks/metrics-fix-2026-09-26/)
- Plan: [PERFORMANCE_PLAN.md](PERFORMANCE_PLAN.md)

## Earlier final verification notes

[final-2026-09-26](benchmarks/final-2026-09-26/) recorded queue p95 **120 ms**, metrics p95 **5.56 s**, RSS **125.4 MiB**, and passing Chrome acceptance before the fast-path metrics change. Browser/network on the pre-pad P0 build: [network-100000.json](benchmarks/p0-2026-09-26/network-100000.json) (50 rows at 100k; exploratory single visits). Do not quote the 5.56 s metrics figure as current.
