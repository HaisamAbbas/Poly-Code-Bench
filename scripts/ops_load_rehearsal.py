"""Local load rehearsal of the read-only public API (PCB-33-4; A 15.3 target is NOT claimed).

Starts the real ASGI app (uvicorn, loopback) over a synthetic development release store, warms
each public route, then drives concurrent GET traffic and ETag revalidations and records
latency percentiles and error rate. This measures a developer workstation without a CDN; it is
regression evidence for the API path, not evidence that the proposed 300 ms cached p95 target
is achieved in staging or production.

    uv run --offline --locked --all-packages python scripts/ops_load_rehearsal.py --evidence <file>
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import statistics
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import uvicorn
from polycodebench_api.app import create_app
from polycodebench_api.dev_fixture import create_synthetic_release
from polycodebench_publication.releases import ReleaseStore


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(url: str, etag: str | None = None) -> tuple[int, float, str | None]:
    request = urllib.request.Request(url, headers={"If-None-Match": etag} if etag else {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback
            response.read()
            status, tag = response.status, response.headers.get("ETag")
    except urllib.error.HTTPError as error:
        status, tag = error.code, None
    return status, (time.perf_counter() - started) * 1000, tag


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=3000)
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="pcb-load-", ignore_cleanup_errors=True) as directory:
        store = ReleaseStore(Path(directory) / "releases.db")
        release_id = create_synthetic_release(store)
        app = create_app(store=store, cursor_key=b"\x01" * 32)
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while not server.started and time.time() < deadline:
            time.sleep(0.1)

        candidates = [
            "/healthz",
            "/v1/releases",
            f"/v1/releases/{release_id}",
            f"/v1/leaderboard?release_id={release_id}",
            f"/v1/tasks?release_id={release_id}",
            f"/v1/methodology/1?release_id={release_id}",
        ]
        routes = [route for route in candidates if _get(base + route)[0] == 200]
        etags = {route: _get(base + route)[2] for route in routes}

        def hit(index: int) -> tuple[str, str, int, float]:
            route = routes[index % len(routes)]
            revalidate = index % 4 == 3 and etags.get(route)
            status, millis, _ = _get(base + route, etags[route] if revalidate else None)
            return route, "revalidate" if revalidate else "full", status, millis

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(hit, range(args.requests)))
        elapsed = time.perf_counter() - started
        server.should_exit = True
        thread.join(timeout=10)

    latencies = [millis for *_, millis in results]
    errors = [status for _, _, status, _ in results if status not in (200, 304)]
    per_route = {}
    for route in routes:
        values = [millis for r, _, _, millis in results if r == route]
        per_route[route.split("?")[0]] = {
            "requests": len(values),
            "p50_ms": _percentile(values, 0.5),
            "p95_ms": _percentile(values, 0.95),
        }
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "environment_class": "local workstation, loopback, no CDN; synthetic development store",
        "claim": (
            "regression measurement only; the A 15.3 300 ms cached p95 target remains a target"
        ),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "requests": len(results),
        "concurrency": args.concurrency,
        "routes_exercised": [route.split("?")[0] for route in routes],
        "throughput_rps": round(len(results) / elapsed, 1),
        "latency_ms": {
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": round(max(latencies), 3),
            "mean": round(statistics.fmean(latencies), 3),
        },
        "revalidations_304": sum(
            1 for _, kind, status, _ in results if kind == "revalidate" and status == 304
        ),
        "revalidations": sum(1 for _, kind, *_ in results if kind == "revalidate"),
        "error_rate": round(len(errors) / len(results), 6),
        "errors": sorted(set(errors)),
        "per_route": per_route,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in (
                    "requests",
                    "throughput_rps",
                    "latency_ms",
                    "error_rate",
                    "routes_exercised",
                )
            },
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
