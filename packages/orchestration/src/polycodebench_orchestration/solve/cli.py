"""``pcb-solve``: inspect a solve attempt and list the installed protocols."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from polycodebench_core.application_errors import ServiceError
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_persistence.solve_state import PostgresSolveRepository
from polycodebench_services.solve_protocols import load_protocol_directory, protocol_digest

from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.solve.inspection import inspect_attempt

EXIT_OK, EXIT_VALIDATION, EXIT_INFRA = 0, 2, 5


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-solve")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="read-only account of one solve attempt")
    inspect.add_argument("attempt_id", type=UUID)
    protocols = commands.add_parser("protocols", help="list installed solve protocols")
    protocols.add_argument("--directory", type=Path, default=Path("config/protocols"))
    return parser


def _inspect(attempt_id: UUID) -> int:
    required = (
        "PCB_DATABASE_URL",
        "PCB_OBJECT_STORE_ENDPOINT",
        "PCB_BUCKET_HIDDEN",
        "PCB_BUCKET_INTERNAL",
        "PCB_BUCKET_PUBLIC",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print("missing configuration: " + ", ".join(missing), file=sys.stderr)
        return EXIT_VALIDATION
    database = Database(os.environ["PCB_DATABASE_URL"])
    try:
        store = S3ArtifactStore.from_environment(
            endpoint_url=os.environ["PCB_OBJECT_STORE_ENDPOINT"],
            buckets={
                "hidden": os.environ["PCB_BUCKET_HIDDEN"],
                "internal": os.environ["PCB_BUCKET_INTERNAL"],
                "public": os.environ["PCB_BUCKET_PUBLIC"],
            },
        )
        artifacts = ArtifactRepository(database.engine, store, max_upload_bytes=512 * 1024**2)
        report = inspect_attempt(
            PostgresSolveRepository(database.engine),
            ArtifactResponseStore(
                artifacts, owner="pcb-solve-inspect", encryption_domain="solve-session"
            ),
            attempt_id,
        )
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return EXIT_OK
    finally:
        database.dispose()


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "protocols":
            protocols = load_protocol_directory(args.directory)
            print(
                json.dumps(
                    {
                        pid: {
                            "digest": protocol_digest(p),
                            "mode": p.mode,
                            "tools": p.tools,
                            "budget": p.budget.model_dump(),
                            "context_ceiling_tokens": p.context.max_input_context_tokens,
                        }
                        for pid, p in protocols.items()
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return EXIT_OK
        return _inspect(args.attempt_id)
    except ServiceError as error:
        print(error.code, file=sys.stderr)
        return EXIT_INFRA if error.status_code >= 500 else EXIT_VALIDATION
    except ValueError as error:
        print(f"invalid input: {type(error).__name__}", file=sys.stderr)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
