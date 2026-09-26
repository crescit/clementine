# Synthetic performance tests

These tools test the existing app. They do not optimize it, change production schema, or write to the normal demo database. Run from the repository root after `uv sync --frozen`.

## Complete HTTP baseline

```bash
uv run python -m scripts.performance.run \
  --records 1000 10000 100000 \
  --output docs/benchmarks/my-baseline
```

The output directory must not exist. The runner creates new databases, starts one local Uvicorn worker per corpus, sends real HTTP/1.1 requests using a connection pool, and terminates the server afterward. Databases/logs are ignored by Git; JSON evidence and manifests should be committed. Run on an otherwise idle machine. Do not run browser/network tests simultaneously with the HTTP load test.

Profiles include sequential/concurrent queue reads, metrics, detail/history, selective search, a 60% detail / 20% queue / 20% metrics mix, a fixed-arrival-rate run at 10,000 records, concurrent four-step review lifecycles, and 20 competing approvals of one version. A correctly rejected stale write is an expected 409, not a successful approval.

**Units matter:** a lifecycle is create → request changes → resubmit → approve (four writes). Its reported throughput/latency is per lifecycle. Other profiles report individual HTTP requests. Latency includes failures/timeouts. Fixed-arrival latency starts at its scheduled arrival; dropped arrivals and generator scheduling lag are reported. Closed-loop throughput is not a maximum-capacity or SLA guarantee.

The final checks verify completed lifecycle states, version/event counts, exactly one approval of the contested record, foreign keys, and SQLite integrity. The generator creates reproducible content/distributions for a seed and timestamp, not measured real-world activity.

## Browser/network measurement

Requires installed Google Chrome. After the HTTP run ends:

```bash
uv run --with playwright python -m scripts.performance.network \
  --database docs/benchmarks/my-baseline/scale-10000.db \
  --output docs/benchmarks/my-baseline/network-10000.json
```

The tool starts its own isolated server and measures time until the queue rows and metrics are ready. It records cold/warm cache behavior, transfer bytes, DOM nodes, and JavaScript errors. Network profiles use Chrome DevTools emulation: unthrottled loopback, 10 Mbps / 40 ms latency, and 1.6 Mbps / 150 ms latency. This is browser emulation, not physical WAN, packet-loss, multi-region, or cloud-host testing. Browser rendering is included; single visits are exploratory samples, not p95s.

## SQL diagnostics

```bash
uv run python -m scripts.performance.diagnose \
  --database docs/benchmarks/my-baseline/scale-10000.db \
  --output docs/benchmarks/my-baseline/sql-10000.json
```

Runs actual read-only API requests with SQLite tracing to count statements. Do not use this instrumentation for timing; it introduces overhead.

## View a larger synthetic workspace

```bash
uv run python -m scripts.performance.generate --database /tmp/clearpath-showcase.db --records 10000
DATABASE_PATH=/tmp/clearpath-showcase.db DEMO_MODE=false uv run uvicorn clearpath.api:app --port 8020
```

Open http://localhost:8020. This is a separate synthetic workspace. `DEMO_MODE=false` prevents accidental reset to the seven-record baseline; it does not add authentication. The existing UI still fetches the complete open queue, so a large backlog can load slowly. This limitation is what the benchmark measures.

For exact fixture reproduction, pass `--seed 42 --as-of <timestamp-from-manifest>` to the generator. Existing files are never overwritten. To rerun, choose a new database/output path.

Read `docs/PERFORMANCE.md` for results and prioritized implementation recommendations.
