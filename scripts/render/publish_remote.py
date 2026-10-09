"""Publish the local signed release store into the Render PostgreSQL public catalog.

The local store is the one ``scripts/publish_live_release.py`` writes (default
``.cache/polycodebench-local-verified-release-store.sqlite3``). Every signature, content digest and
public schema is verified against the local keyring first; only public release fields are copied,
using the ``pcb_render_publisher`` role created by ``bootstrap_remote.py``. Re-running is an
idempotent replay; the release pointer only advances.

    uv run --locked --all-packages python scripts/render/publish_remote.py --dry-run
    uv run --locked --all-packages python scripts/render/publish_remote.py

The DSN comes from PCB_RENDER_PUBLISHER_DATABASE_URL, or from ``.local/render/render-secrets.env``.
It is never printed. By default only ``live_exploratory`` current releases are accepted, so a
synthetic fixture cannot reach the public site by accident (``--allow-synthetic`` overrides).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "render"))

import local_stack  # noqa: E402
from bootstrap_remote import DEFAULT_SECRETS_FILE, read_secrets  # noqa: E402

DEFAULT_TARGET = "render:board"
DSN_NAME = "PCB_RENDER_PUBLISHER_DATABASE_URL"


def resolve_dsn(secrets_file: Path) -> str:
    value = os.environ.get(DSN_NAME) or read_secrets(secrets_file).get(DSN_NAME)
    if not value:
        raise SystemExit(f"{DSN_NAME} is not set and {secrets_file} has none; run bootstrap first")
    return value


def summarize(snapshots: list[dict[str, Any]], pointer: dict[str, Any]) -> dict[str, Any]:
    return {
        "releases": [
            {
                "release_id": row["id"],
                "state": row["state"],
                "fixture_kind": row["projection"].get("fixture_kind"),
                "key_id": row["manifest"].get("key_id"),
                "published_at": row["published_at"].isoformat(),
            }
            for row in snapshots
        ],
        "source_pointer": pointer,
    }


def check_kind(
    snapshots: list[dict[str, Any]], pointer: dict[str, Any], allow_synthetic: bool
) -> None:
    if allow_synthetic or pointer["release_id"] is None:
        return
    current = next((row for row in snapshots if row["id"] == pointer["release_id"]), None)
    kind = current["projection"].get("fixture_kind") if current else None
    if kind != "live_exploratory":
        raise SystemExit(
            f"the current release is fixture_kind={kind!r}, not live_exploratory; "
            "pass --allow-synthetic to publish it to the public site anyway"
        )


def run(args: argparse.Namespace) -> dict[str, Any]:
    from polycodebench_operations.release_sync import (
        sync_publication,
        verified_publication_snapshot,
    )
    from polycodebench_persistence.database import Database
    from polycodebench_persistence.public_releases import PostgresPublicReleaseCatalog
    from polycodebench_publication.keyring import Keyring
    from polycodebench_publication.releases import ReleaseStore

    if not args.store.exists() or not args.keyring.exists():
        raise SystemExit(
            "local release store or keyring not found; publish a release locally first"
        )
    store = ReleaseStore(args.store)
    keyring = Keyring.from_document(json.loads(args.keyring.read_text(encoding="utf-8")))
    snapshots, pointer = verified_publication_snapshot(
        store, keyring=keyring, source_target=args.source_target
    )
    check_kind(snapshots, pointer, args.allow_synthetic)
    result: dict[str, Any] = {
        "dry_run": bool(args.dry_run),
        "target": args.target,
        **summarize(snapshots, pointer),
    }
    if args.dry_run:
        return result
    database = Database(resolve_dsn(args.secrets_file))
    try:
        result["sync"] = sync_publication(
            store,
            PostgresPublicReleaseCatalog(database.engine),
            keyring=keyring,
            target=args.target,
            source_target=args.source_target,
        )
    finally:
        database.dispose()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, default=local_stack.PUBLIC_RELEASE_STORE)
    parser.add_argument("--keyring", type=Path, default=local_stack.PUBLIC_RELEASE_KEYRING)
    parser.add_argument(
        "--target", default=DEFAULT_TARGET, help="must equal PCB_PUBLICATION_TARGET"
    )
    parser.add_argument("--source-target", default="local:board")
    parser.add_argument("--secrets-file", type=Path, default=DEFAULT_SECRETS_FILE)
    parser.add_argument("--dry-run", action="store_true", help="verify only; no database access")
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except SystemExit:
        raise
    except Exception as error:  # driver text may include connection details
        print(f"publish failed: {type(error).__name__}: {str(error)[:200]}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
