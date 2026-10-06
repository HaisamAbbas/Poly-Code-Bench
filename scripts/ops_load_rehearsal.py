"""Read-only load rehearsal for the public API (PCB-33-4; the A 15.3 target is NOT claimed).

By default, starts the ASGI app on loopback with a synthetic development release. With an
explicit ``--base-url`` and ``--confirm-target``, it instead discovers public routes from the
selected API's current published release and sends only GET requests to that target. Both modes
exercise API routes, not browser page rendering. Results describe only the selected host and
cannot establish that the proposed 300 ms cached p95 target is achieved in staging or production.

    uv run --offline --locked --all-packages python scripts/ops_load_rehearsal.py --evidence <file>
    uv run --offline --locked --all-packages python scripts/ops_load_rehearsal.py \
      --base-url <API-origin> --confirm-target --evidence <file>
"""

from __future__ import annotations

import argparse
import ipaddress
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
from urllib.parse import quote, urlencode, urlsplit

import uvicorn
from polycodebench_api.app import create_app
from polycodebench_api.dev_fixture import create_synthetic_release
from polycodebench_publication.releases import ReleaseStore


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not let a selected load-test origin redirect traffic to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ARG002
        return None


_HTTP = urllib.request.build_opener(_NoRedirect)
MAX_REQUESTS = 10_000
MAX_CONCURRENCY = 64


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(url: str, etag: str | None = None) -> tuple[int, float, str | None]:
    request = urllib.request.Request(url, headers={"If-None-Match": etag} if etag else {})
    started = time.perf_counter()
    try:
        with _HTTP.open(request, timeout=10) as response:  # noqa: S310 - explicit CLI target
            response.read()
            status, tag = response.status, response.headers.get("ETag")
    except urllib.error.HTTPError as error:
        status, tag = error.code, None
    except (urllib.error.URLError, TimeoutError):
        status, tag = 0, None
    return status, (time.perf_counter() - started) * 1000, tag


def _json_get(url: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with _HTTP.open(request, timeout=10) as response:  # noqa: S310 - explicit CLI target
            payload = json.loads(response.read())
            return response.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as error:
        return error.code, {}
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(
            "public API route discovery could not reach the selected origin"
        ) from error


def _external_routes(base: str, requested_release: str | None) -> tuple[str, list[str], str]:
    """Discover a read-only public API route mix from one published release."""

    status, index = _json_get(f"{base}/v1/releases?limit=200")
    if status != 200:
        raise RuntimeError(f"release discovery returned HTTP {status}")
    summaries = index.get("data")
    meta = index.get("meta")
    if not isinstance(summaries, list) or not isinstance(meta, dict):
        raise RuntimeError("release discovery response is missing data or metadata")
    release_id = requested_release or meta.get("current_release_id")
    if not isinstance(release_id, str) or not release_id:
        raise RuntimeError("the API has no current release; pass a published --release-id")
    summary = next(
        (
            item
            for item in summaries
            if isinstance(item, dict) and item.get("release_id") == release_id
        ),
        None,
    )
    if summary is None:
        status, release_envelope = _json_get(f"{base}/v1/releases/{quote(release_id, safe='')}")
        if status != 200 or not isinstance(release_envelope.get("data"), dict):
            raise RuntimeError(f"release {release_id!r} is not publicly available")
        summary = release_envelope["data"]
    if summary.get("state") != "published":
        raise RuntimeError("load rehearsal requires a published release")

    query = urlencode({"release": release_id})
    routes = [
        "/v1/releases?limit=200",
        f"/v1/releases/{quote(release_id, safe='')}",
        f"/v1/leaderboard?{query}&limit=200",
        f"/v1/tasks?{query}&limit=50",
    ]
    methodology_version = summary.get("methodology_version")
    if isinstance(methodology_version, str) and methodology_version:
        routes.append(f"/v1/methodology/{quote(methodology_version, safe='')}?{query}")

    status, board_envelope = _json_get(f"{base}/v1/leaderboard?{query}&limit=200")
    entries = board_envelope.get("data") if status == 200 else None
    status, tasks_envelope = _json_get(f"{base}/v1/tasks?{query}&limit=50")
    tasks = tasks_envelope.get("data") if status == 200 else None
    if isinstance(entries, list) and entries:
        first = entries[0]
        if isinstance(first, dict):
            model_id = first.get("model_config_id")
            if isinstance(model_id, str) and model_id:
                routes.append(f"/v1/models/{quote(model_id, safe='')}?{query}")
            languages = first.get("languages")
            if isinstance(languages, list) and languages and isinstance(languages[0], str):
                routes.append(f"/v1/languages/{quote(languages[0], safe='')}?{query}")
            evidence_url = first.get("evidence_url")
            if isinstance(evidence_url, str):
                evidence = urlsplit(evidence_url)
                if (
                    not evidence.scheme
                    and not evidence.netloc
                    and evidence.path.startswith("/v1/scorecards/")
                ):
                    routes.append(f"{evidence.path}?{query}")
        if len(entries) > 1 and all(
            isinstance(entry, dict) and isinstance(entry.get("model_config_id"), str)
            for entry in entries[:2]
        ):
            models = urlencode(
                [
                    ("release", release_id),
                    ("models", entries[0]["model_config_id"]),
                    ("models", entries[1]["model_config_id"]),
                ]
            )
            routes.append(f"/v1/compare?{models}")
    if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
        task_id = tasks[0].get("task_id")
        if isinstance(task_id, str) and task_id:
            encoded_task = quote(task_id, safe="")
            routes.append(f"/v1/tasks/{encoded_task}?{query}")
            routes.append(f"/v1/tasks/{encoded_task}/content?{query}")

    # Preserve order while avoiding duplicated paths in small or answer-only releases.
    return release_id, list(dict.fromkeys(routes)), str(summary.get("fixture_kind", "unknown"))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--requests", type=int, default=3000, help=f"load samples (1-{MAX_REQUESTS})"
    )
    parser.add_argument(
        "--concurrency", type=int, default=16, help=f"parallel requests (1-{MAX_CONCURRENCY})"
    )
    parser.add_argument(
        "--base-url",
        help="explicit public API origin to measure; unset starts the synthetic local API",
    )
    parser.add_argument("--release-id", help="pin a published release at --base-url")
    parser.add_argument(
        "--confirm-target",
        action="store_true",
        help="required with --base-url to make the selected target explicit",
    )
    args = parser.parse_args(argv)

    if not 1 <= args.requests <= MAX_REQUESTS:
        parser.error(f"--requests must be between 1 and {MAX_REQUESTS}")
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        parser.error(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
    if args.base_url and not args.confirm_target:
        parser.error("--base-url requires --confirm-target; only use an explicitly approved target")
    if args.release_id and not args.base_url:
        parser.error("--release-id is only valid with --base-url")

    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    directory: tempfile.TemporaryDirectory[str] | None = None
    if args.base_url:
        parsed_base = urlsplit(args.base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            parser.error("--base-url must be an HTTP(S) API origin")
        if parsed_base.username is not None or parsed_base.password is not None:
            parser.error("--base-url must not contain credentials")
        if parsed_base.path not in {"", "/"} or parsed_base.query or parsed_base.fragment:
            parser.error("--base-url must be an origin without a path, query, or fragment")
        try:
            is_loopback = ipaddress.ip_address(parsed_base.hostname or "").is_loopback
        except ValueError:
            is_loopback = (parsed_base.hostname or "").lower() == "localhost"
        if parsed_base.scheme != "https" and not is_loopback:
            parser.error("non-loopback API origins must use HTTPS")
        base = args.base_url.rstrip("/")
        target_mode = "explicit_api_origin"
        target_environment = (
            "local loopback API origin; no CDN"
            if is_loopback
            else "explicit API origin; destination omitted from evidence"
        )
    else:
        directory = tempfile.TemporaryDirectory(prefix="pcb-load-", ignore_cleanup_errors=True)
        store = ReleaseStore(Path(directory.name) / "releases.db")
        create_synthetic_release(store)
        app = create_app(store=store, cursor_key=b"\x01" * 32)
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        target_mode = "synthetic_local_asgi"
        target_environment = "local workstation, loopback, no CDN; synthetic development store"
        deadline = time.time() + 30
        while not server.started and time.time() < deadline:
            time.sleep(0.1)
        if not server.started:
            server.should_exit = True
            thread.join(timeout=10)
            directory.cleanup()
            raise RuntimeError("local ASGI server did not start within 30 seconds")

    try:
        release_id, routes, fixture_kind = _external_routes(base, args.release_id)
        if not routes:
            raise RuntimeError("the selected release produced no public API routes")
        warm_statuses = {route: _get(base + route)[0] for route in routes}
        failed_warmups = {route: status for route, status in warm_statuses.items() if status != 200}
        if failed_warmups:
            raise RuntimeError(f"public route warm-up failed: {failed_warmups}")
        etags = {route: _get(base + route)[2] for route in routes}

        def hit(index: int) -> tuple[str, str, int, float]:
            route = routes[index % len(routes)]
            revalidate = index % 4 == 3 and etags.get(route) is not None
            status, millis, _ = _get(base + route, etags[route] if revalidate else None)
            return route, "revalidate" if revalidate else "full", status, millis

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(hit, range(args.requests)))
        elapsed = time.perf_counter() - started
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        if directory is not None:
            directory.cleanup()

    latencies = [millis for *_, millis in results]
    errors = [status for _, _, status, _ in results if status not in (200, 304)]
    per_route = {}
    for route in routes:
        values = [millis for r, _, _, millis in results if r == route]
        per_route[route.split("?")[0]] = {
            "requests": len(values),
            "p50_ms": _percentile(values, 0.5) if values else None,
            "p95_ms": _percentile(values, 0.95) if values else None,
        }
    evidence: dict[str, Any] = {
        "schema_version": 2,
        "environment_class": target_environment,
        "target_mode": target_mode,
        "target_release_id": release_id,
        "release_fixture_kind": fixture_kind,
        "claim": (
            "read-only API route measurement only; does not establish browser page timing "
            "or the A 15.3 300 ms staging target"
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
                    "target_mode",
                    "release_fixture_kind",
                )
            },
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
