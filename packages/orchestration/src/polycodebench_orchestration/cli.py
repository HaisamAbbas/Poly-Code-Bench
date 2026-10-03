"""Scheduler operations for verified recovery and worker lifecycle administration."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import cast
from uuid import UUID

from polycodebench_core.application_errors import ServiceError
from polycodebench_core.telemetry import METRICS, configure_logging, serve_metrics
from polycodebench_persistence.database import Database
from polycodebench_persistence.jobs import REAPER_SECONDS, PostgresJobRepository
from sqlalchemy.exc import SQLAlchemyError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-scheduler")
    commands = parser.add_subparsers(dest="command", required=True)
    reap = commands.add_parser("reap", help="recover expired leases and report guest cleanup")
    reap.add_argument("--limit", type=int, default=100)
    reap.add_argument(
        "--watch", action="store_true", help="recover expired leases every 30 seconds"
    )
    worker = commands.add_parser(
        "worker-status", help="drain, disable, or resume a registered worker"
    )
    worker.add_argument("worker_id", type=UUID)
    worker.add_argument("status", choices=("active", "draining", "disabled"))
    cancel = commands.add_parser("cancel-attempt", help="cancel uncompleted work for one attempt")
    cancel.add_argument("attempt_id", type=UUID)
    cancel.add_argument("--reason", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(
        environment=os.environ.get("PCB_ENVIRONMENT", "dev"),
        role="scheduler",
        service_identity=os.environ.get("PCB_SERVICE_IDENTITY"),
        log_format="json" if os.environ.get("PCB_LOG_FORMAT") == "json" else "text",
        stream=sys.stderr,
    )
    database_url = os.environ.get("PCB_DATABASE_URL")
    actor = os.environ.get("PCB_SERVICE_IDENTITY")
    if not database_url or not actor:
        print("PCB_DATABASE_URL and PCB_SERVICE_IDENTITY are required", file=sys.stderr)
        return 2
    database: Database | None = None
    try:
        database = Database(database_url)
        repository = PostgresJobRepository(database.engine)
        if args.command == "reap":
            metrics_port = os.environ.get("PCB_METRICS_PORT")
            if args.watch and metrics_port:
                serve_metrics(METRICS, int(metrics_port))
            while True:
                rows = repository.reap_expired(limit=args.limit)
                if rows:
                    METRICS.inc("pcb_expired_leases_total", float(len(rows)))
                print(
                    json.dumps(
                        [
                            {
                                "job_id": str(row["job_id"]),
                                "state": row["state"],
                                "slot_ids": [
                                    str(slot_id)
                                    for slot_id in cast(tuple[UUID, ...], row["slot_ids"])
                                ],
                                "guest_ids": list(cast(tuple[str, ...], row["guest_ids"])),
                            }
                            for row in rows
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                if not args.watch:
                    break
                time.sleep(REAPER_SECONDS)
        elif args.command == "worker-status":
            changed = repository.set_worker_status(args.worker_id, status=args.status)
            print(
                json.dumps({"worker_id": str(args.worker_id), "changed": changed}, sort_keys=True)
            )
            return 0 if changed else 1
        else:
            cancelled = repository.cancel_scope(
                "attempt", args.attempt_id, actor=actor, reason=args.reason
            )
            print(
                json.dumps(
                    {"attempt_id": str(args.attempt_id), "cancelled_jobs": cancelled},
                    sort_keys=True,
                )
            )
        return 0
    except KeyboardInterrupt:
        return 130
    except ServiceError as error:
        print(f"scheduler error: {error.code}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"invalid scheduler configuration: {error}", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print("scheduler error: DEPENDENCY_UNAVAILABLE", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
