"""Real-HTTP, isolated benchmark. Generates fresh databases, never touches demo state.

uv run python -m scripts.performance.run --records 1000 10000 100000 --output docs/benchmarks/baseline
"""

from __future__ import annotations
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
import httpx
from scripts.performance.generate import generate

ROOT = Path(__file__).resolve().parents[2]


def percentile(values, p):
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(len(values) * p) - 1)], 2)


def summary(name, samples, seconds, **extra):
    latencies = [s["ms"] for s in samples]
    good = [s for s in samples if s["ok"]]
    return dict(
        name=name,
        requests=len(samples),
        successes=len(good),
        errors=len(samples) - len(good),
        status_counts=dict(Counter(str(s["status"]) for s in samples)),
        duration_seconds=round(seconds, 3),
        successful_rps=round(len(good) / seconds, 2),
        latency_ms={
            p: percentile(latencies, v)
            for p, v in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1)]
        },
        decoded_response_bytes=sum(s["bytes"] for s in samples),
        response_bytes_p50=percentile([s["bytes"] for s in samples], 0.5),
        **extra,
    )


async def request(client, path, actor, *, method="GET", body=None, expected=(200,)):
    started = time.perf_counter()
    try:
        response = await client.request(
            method, path, headers={"X-Demo-User-Id": actor}, json=body
        )
        sample = dict(
            ms=(time.perf_counter() - started) * 1000,
            status=response.status_code,
            ok=response.status_code in expected,
            bytes=len(response.content),
        )
        return sample, response
    except httpx.HTTPError as exc:
        return dict(
            ms=(time.perf_counter() - started) * 1000,
            status=type(exc).__name__,
            ok=False,
            bytes=0,
        ), None


async def closed_phase(client, name, paths, actor, concurrency, count):
    samples = []
    counter = iter(range(count))

    async def worker():
        for i in counter:
            sample, _ = await request(client, paths[i % len(paths)], actor)
            samples.append(sample)

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return summary(
        name,
        samples,
        time.perf_counter() - started,
        concurrency=concurrency,
        mode="closed_loop",
        endpoints=paths,
    )


async def arrival_phase(client, paths, actor, rps=10, seconds=10, max_inflight=30):
    # Fixed offered rate; no hiding overload behind a fixed user population.
    samples = []
    tasks = set()
    rejected = 0
    lateness = []
    start = time.perf_counter()

    async def one(i, scheduled):
        sample, _ = await request(client, paths[i % len(paths)], actor)
        sample["ms"] = (time.perf_counter() - scheduled) * 1000
        samples.append(sample)

    for i in range(rps * seconds):
        scheduled = start + i / rps
        await asyncio.sleep(max(0, scheduled - time.perf_counter()))
        lateness.append((time.perf_counter() - scheduled) * 1000)
        if len(tasks) >= max_inflight:
            rejected += 1
            continue
        task = asyncio.create_task(one(i, scheduled))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    await asyncio.gather(*list(tasks))
    return summary(
        "mixed_fixed_arrival",
        samples,
        time.perf_counter() - start,
        mode="open_loop",
        offered_rps=rps,
        offered_requests=rps * seconds,
        load_generator_dropped=rejected,
        max_inflight=max_inflight,
        schedule_lag_p95_ms=percentile(lateness, 0.95),
        endpoints=paths,
    )


async def write_phase(client, submitter, count=40, concurrency=10):
    samples = []
    created = []
    failures = []
    counter = iter(range(count))

    async def worker():
        for i in counter:
            started = time.perf_counter()
            steps = []
            body = dict(
                title=f"Synthetic load lifecycle {uuid.uuid4().hex[:12]}",
                product="PERSONAL_LOAN",
                channel="WEBSITE",
                target_launch_date=datetime.now(timezone.utc).date().isoformat(),
                copy_text="Subject to credit approval.",
            )
            first, response = await request(
                client,
                "/api/submissions",
                submitter,
                method="POST",
                body=body,
                expected=(201,),
            )
            steps.append(first)
            if response is None or not first["ok"]:
                failures.append({"step": "create", "status": first["status"]})
            else:
                record = response.json()
                created.append(record["id"])
                reviewer = record["assigned_reviewer_id"]
                for action, actor, payload in [
                    (
                        "request-changes",
                        reviewer,
                        {"comment": "Synthetic review feedback"},
                    ),
                    (
                        "resubmit",
                        submitter,
                        {
                            "copy_text": "Revised synthetic campaign. Subject to credit approval."
                        },
                    ),
                    ("approve", reviewer, {"comment": "Synthetic version 2 approved"}),
                ]:
                    sample, response = await request(
                        client,
                        f"/api/submissions/{record['id']}/{action}",
                        actor,
                        method="POST",
                        body={
                            "expected_record_version": record["record_version"],
                            **payload,
                        },
                    )
                    steps.append(sample)
                    if response is None or not sample["ok"]:
                        failures.append({"step": action, "status": sample["status"]})
                        break
                    record = response.json()
            samples.append(
                dict(
                    ms=(time.perf_counter() - started) * 1000,
                    ok=len(steps) == 4 and all(s["ok"] for s in steps),
                    status="complete"
                    if len(steps) == 4 and all(s["ok"] for s in steps)
                    else "failed",
                    bytes=sum(s["bytes"] for s in steps),
                )
            )

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    result = summary(
        "write_lifecycle",
        samples,
        time.perf_counter() - started,
        concurrency=concurrency,
        unit="four-step lifecycle",
        planned_writes=count * 4,
        failures=failures,
    )
    return result, created


async def hot_record(client, submitter):
    sample, response = await request(
        client,
        "/api/submissions",
        submitter,
        method="POST",
        body=dict(
            title="Synthetic contested decision",
            product="PERSONAL_LOAN",
            channel="WEBSITE",
            target_launch_date=datetime.now(timezone.utc).date().isoformat(),
            copy_text="Subject to credit approval.",
        ),
        expected=(201,),
    )
    if response is None or not sample["ok"]:
        return {"name": "contested_decision", "setup_failed": sample}, None
    record = response.json()
    start = time.perf_counter()
    results = await asyncio.gather(
        *(
            request(
                client,
                f"/api/submissions/{record['id']}/approve",
                record["assigned_reviewer_id"],
                method="POST",
                body={"expected_record_version": 1},
                expected=(200, 409),
            )
            for _ in range(20)
        )
    )
    samples = [r[0] for r in results]
    result = summary(
        "contested_decision", samples, time.perf_counter() - start, concurrency=20
    )
    result["correct"] = (
        sum(s["status"] == 200 for s in samples) == 1
        and sum(s["status"] == 409 for s in samples) == 19
        and all(
            r is not None
            and (r.status_code == 200 or r.json().get("code") == "VERSION_CONFLICT")
            for _, r in results
        )
    )
    return result, record["id"]


def verify_writes(database, created, contested):
    conn = sqlite3.connect(database)
    failures = []
    for sid in created:
        state = conn.execute(
            "SELECT status,current_version,record_version FROM submissions WHERE id=?",
            (sid,),
        ).fetchone()
        versions = conn.execute(
            "SELECT COUNT(*) FROM submission_versions WHERE submission_id=?", (sid,)
        ).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE submission_id=?", (sid,)
        ).fetchone()[0]
        if state != ("APPROVED", 2, 4) or versions != 2 or events != 5:
            failures.append(
                {"id": sid, "state": state, "versions": versions, "events": events}
            )
    decisions = (
        conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE submission_id=? AND event_type='APPROVED'",
            (contested,),
        ).fetchone()[0]
        if contested
        else None
    )
    result = {
        "completed_lifecycles_verified": len(created) - len(failures),
        "failures": failures,
        "contested_approval_events": decisions,
        "foreign_key_errors": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }
    conn.close()
    return result


def query_plans(database):
    conn = sqlite3.connect(database)
    statements = {
        "queue": "SELECT * FROM submissions WHERE status IN ('PENDING_ASSIGNMENT','UNDER_REVIEW','CHANGES_REQUESTED') ORDER BY sla_breach_at,target_launch_date,submitted_at,external_id",
        "next_external_id": "SELECT MAX(CAST(SUBSTR(external_id,4) AS INTEGER)) FROM submissions",
        "reviewer_load": "SELECT u.id, COUNT(s.id) AS load FROM users u LEFT JOIN submissions s ON s.assigned_reviewer_id=u.id AND s.status='UNDER_REVIEW' WHERE u.role='REVIEWER' GROUP BY u.id ORDER BY load,u.name,u.id LIMIT 1",
    }
    result = {
        name: [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql)]
        for name, sql in statements.items()
    }
    conn.close()
    return result


async def measure(base, database, records):
    async with httpx.AsyncClient(
        base_url=base,
        timeout=60,
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=40),
        trust_env=False,
    ) as client:
        users = (await client.get("/api/users")).json()["users"]
        reviewer = next(u["id"] for u in users if u["name"] == "Sarah T.")
        submitter = next(u["id"] for u in users if u["name"] == "Jessica Lin")
        # Bootstrap detail ID directly from the isolated fixture, avoiding an extra giant queue response.
        conn = sqlite3.connect(database)
        sid = conn.execute(
            "SELECT id FROM submissions WHERE external_id='CP-8904'"
        ).fetchone()[0]
        conn.close()
        detail = f"/api/submissions/{sid}"
        phases = []

        async def add(result):
            phases.append(result)
            print(json.dumps({"records": records, **result}), flush=True)

        for path in ["/api/health", detail, "/api/metrics"]:
            await request(client, path, reviewer)  # unmeasured warmup
        large = records >= 100000
        await add(
            await closed_phase(
                client,
                "queue_sequential",
                ["/api/submissions"],
                reviewer,
                1,
                3 if large else 8,
            )
        )
        await add(
            await closed_phase(
                client,
                "queue_concurrent",
                ["/api/submissions"],
                reviewer,
                2 if large else 10,
                4 if large else 40,
            )
        )
        await add(
            await closed_phase(
                client,
                "metrics_concurrent",
                ["/api/metrics"],
                reviewer,
                5 if large else 10,
                15 if large else 60,
            )
        )
        await add(
            await closed_phase(
                client,
                "detail_concurrent",
                [detail, detail + "/history"],
                reviewer,
                20,
                200,
            )
        )
        await add(
            await closed_phase(
                client,
                "search_concurrent",
                ["/api/submissions?search=Campaign%200009"],
                reviewer,
                5,
                20,
            )
        )
        paths = [
            detail,
            detail + "/history",
            detail,
            "/api/submissions",
            "/api/metrics",
        ]
        await add(
            await closed_phase(
                client,
                "mixed_concurrent",
                paths,
                reviewer,
                3 if large else 10,
                15 if large else 100,
            )
        )
        if records == 10000:
            await add(await arrival_phase(client, paths, reviewer))
        write, created = await write_phase(client, submitter, count=20 if large else 40)
        await add(write)
        hot, contested = await hot_record(client, submitter)
        await add(hot)
        # Snapshot metrics after load explains the visible preview, not throughput causality.
        metrics = (
            await client.get("/api/metrics", headers={"X-Demo-User-Id": reviewer})
        ).json()
    return {
        "phases": phases,
        "integrity": verify_writes(database, created, contested),
        "query_plans": query_plans(database),
        "post_load_metrics": metrics,
    }


async def measure_with_resources(base, database, records, pid):
    memory = []

    async def sample_memory():
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ps", "-o", "rss=", "-p", str(pid), stdout=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                memory.append(int(stdout.strip()))
            except (OSError, ValueError):
                pass
            await asyncio.sleep(1)

    sampler = asyncio.create_task(sample_memory())
    try:
        result = await measure(base, database, records)
        result["server_peak_sampled_rss_mib"] = (
            round(max(memory) / 1024, 1) if memory else None
        )
        result["memory_sampling"] = (
            "server RSS sampled once per second; short peaks may be missed"
        )
        return result
    finally:
        sampler.cancel()
        try:
            await sampler
        except asyncio.CancelledError:
            pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(args):
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite results directory {output}")
    output.mkdir(parents=True)
    files = sorted(
        list((ROOT / "clearpath").glob("*.py")) + list((ROOT / "static").glob("*"))
    )
    hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
        if p.is_file()
    }
    manifest = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "httpx": httpx.__version__,
        "source_hashes": hashes,
        "transport": "HTTP/1.1 loopback TCP, persistent connections, no TLS",
        "server": "one Uvicorn worker, no reload, no access log",
        "limits": "same host runs server and generator; warm steady-state; not a cloud/free-tier benchmark",
    }
    (output / "environment.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for records in args.records:
        database = output / f"scale-{records}.db"
        fixture = generate(database, records)
        print(
            f"Generated {records:,} submissions ({fixture['database_bytes'] / 1024 / 1024:.1f} MiB)",
            flush=True,
        )
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "DATABASE_PATH": str(database),
            "DEMO_MODE": "false",
            "PYTHONPATH": str(ROOT),
        }
        with (output / f"server-{records}.log").open("w") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "clearpath.api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Server failed to start; see log")
                    try:
                        if (
                            httpx.get(
                                base + "/api/health", timeout=0.5, trust_env=False
                            ).status_code
                            == 200
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Server startup timed out")
                result = asyncio.run(
                    measure_with_resources(base, database, records, server.pid)
                )
                result["fixture"] = fixture
                result["server_pid"] = server.pid
                (output / f"results-{records}.json").write_text(
                    json.dumps(result, indent=2) + "\n"
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    changed = [
        name
        for name, digest in hashes.items()
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
    ]
    (output / "source_check.json").write_text(
        json.dumps({"application_files_changed_during_run": changed}, indent=2) + "\n"
    )
    print(f"Results saved to {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, nargs="+", default=[1000, 10000, 100000])
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
