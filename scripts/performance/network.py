"""Browser-level latency/bandwidth emulation against isolated scale fixtures.

uv run --with playwright python -m scripts.performance.network --database PATH --output PATH
Creates its own local server; never targets a remote host or the ordinary demo.
"""

from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx
from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
from scripts.performance.run import ROOT, free_port


def run(args):
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.database.is_file():
        raise FileNotFoundError(args.database)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
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
        env={
            **os.environ,
            "DATABASE_PATH": str(args.database.resolve()),
            "DEMO_MODE": "false",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results = []
    try:
        for _ in range(100):
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
            raise RuntimeError("Server failed to start")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome", headless=True, args=["--disable-gpu"]
            )
            browser_version = browser.version
            profiles = [
                ("unthrottled", 0, -1, -1),
                ("broadband_10Mbps_40ms", 40, 10_000_000 / 8, 2_000_000 / 8),
                ("mobile_1.6Mbps_150ms", 150, 1_600_000 / 8, 750_000 / 8),
            ]
            for name, latency, down, up in profiles:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                session = context.new_cdp_session(page)
                session.send("Network.enable")
                session.send(
                    "Network.emulateNetworkConditions",
                    {
                        "offline": False,
                        "latency": latency,
                        "downloadThroughput": down,
                        "uploadThroughput": up,
                    },
                )
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                for visit in (
                    ["cold_cache", "warm_cache"]
                    if name == "unthrottled"
                    else ["cold_cache"]
                ):
                    start = time.perf_counter()
                    timed_out = False
                    page.goto(base, wait_until="domcontentloaded", timeout=120000)
                    try:
                        page.wait_for_function(
                            "document.getElementById('queue-state').hidden && document.getElementById('metrics').getAttribute('aria-busy') === 'false'",
                            timeout=120000,
                        )
                    except BrowserTimeout:
                        timed_out = True
                    elapsed = (time.perf_counter() - start) * 1000
                    measurements = page.evaluate("""() => ({
                        rows:document.querySelectorAll('#queue-body tr').length,
                        dom_nodes:document.getElementsByTagName('*').length,
                        navigation:performance.getEntriesByType('navigation').map(e=>({domContentLoaded_ms:e.domContentLoadedEventEnd,load_ms:e.loadEventEnd,transfer_bytes:e.transferSize})),
                        resources:performance.getEntriesByType('resource').map(e=>({path:new URL(e.name).pathname,duration_ms:e.duration,transfer_bytes:e.transferSize,encoded_bytes:e.encodedBodySize,decoded_bytes:e.decodedBodySize})),
                        notice:document.getElementById('notice').textContent
                    })""")
                    result = {
                        "profile": name,
                        "cache": visit,
                        "ready_ms": round(elapsed, 1),
                        "timed_out": timed_out,
                        "latency_ms": latency,
                        "download_bytes_per_second": down,
                        "upload_bytes_per_second": up,
                        "javascript_errors": list(errors),
                        **measurements,
                    }
                    results.append(result)
                    print(
                        json.dumps(
                            {
                                "profile": name,
                                "cache": visit,
                                "ready_ms": result["ready_ms"],
                                "rows": measurements["rows"],
                                "timed_out": timed_out,
                                "transfer_mib": round(
                                    sum(
                                        r["transfer_bytes"]
                                        for r in measurements["resources"]
                                    )
                                    / 1024
                                    / 1024,
                                    2,
                                ),
                            }
                        ),
                        flush=True,
                    )
                    args.output.write_text(
                        json.dumps(
                            {
                                "database": str(args.database),
                                "browser": browser_version,
                                "results": results,
                                "note": "Chrome DevTools bandwidth/latency emulation, not physical WAN or a production load balancer. App/backend and browser share a host. Single visit per profile; exploratory rather than percentile evidence.",
                            },
                            indent=2,
                        )
                        + "\n"
                    )
                context.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
