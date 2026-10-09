"""Publish one unranked ``live_exploratory`` release from persisted scorecards to the local board.

Mirrors ``local_stack.py seed`` for real runs: it builds the release documents from completed runs'
scorecards in PostgreSQL, drives the local reviewed lifecycle (draft -> validate -> review ->
approve -> publish) in the verified local release store with the local signing key, then mirrors
the signed snapshot into the PostgreSQL public catalog with the publisher-scoped DSN.

The release is labelled ``fixture_kind=live_exploratory`` and ``scope=exploratory``: unranked,
not calibrated, and never a ranked benchmark result. Checks that cannot be evidenced for such data
are recorded as ``not_applicable_exploratory`` receipts. Re-running for the same runs resumes or
replays the same release instead of creating a new one.

    uv run --locked --all-packages python scripts/publish_live_release.py --run-id <run-uuid>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import local_stack  # noqa: E402
from polycodebench_api.dev_fixture import _persistent_fixture_signer  # noqa: E402
from polycodebench_operations.live_release import (  # noqa: E402
    build_live_release,
    write_live_release,
)
from polycodebench_operations.release_sync import sync_publication  # noqa: E402
from polycodebench_persistence.database import Database  # noqa: E402
from polycodebench_persistence.public_releases import PostgresPublicReleaseCatalog  # noqa: E402
from polycodebench_publication.keyring import Keyring  # noqa: E402
from polycodebench_publication.releases import (  # noqa: E402
    ReleasePrincipal,
    ReleaseStore,
    ValidationEvidence,
)

PRINCIPAL = ReleasePrincipal("local-live-release", frozenset({"curator", "reviewer", "publisher"}))


def _setting(values: dict[str, str], name: str) -> str:
    value = os.environ.get(name) or values.get(name)
    if not value:
        raise SystemExit(f"{name} is required (environment or local .env)")
    return value


def publish(
    store: ReleaseStore,
    content: dict[str, Any],
    projection: dict[str, Any],
    evidence: list[dict[str, Any]],
    signing_key: Path,
    keyring: Path,
) -> dict[str, Any]:
    """Run the reviewed lifecycle to publication, resuming an interrupted earlier attempt."""
    prefix = "live-" + str(projection["cohort_digest"]).removeprefix("sha256:")[:24]
    doc = store.get(store.draft(PRINCIPAL, content, projection, f"{prefix}-draft")["id"])
    receipts = tuple(ValidationEvidence(**row) for row in evidence)
    while doc["state"] != "published":
        release_id, version = doc["id"], int(doc["version"])
        if doc["state"] == "draft":
            doc = store.validate(PRINCIPAL, release_id, receipts, version, f"{prefix}-validate")
        elif doc["state"] == "review_required" and not doc["review"]:
            doc = store.review(
                PRINCIPAL,
                release_id,
                "Live exploratory release: unranked, uncalibrated local results.",
                version,
                f"{prefix}-review",
            )
        elif doc["state"] == "review_required":
            doc = store.approve(
                PRINCIPAL,
                release_id,
                "Approve unranked live exploratory publication on the local board.",
                version,
                f"{prefix}-approve",
            )
        elif doc["state"] == "approved":
            signer = _persistent_fixture_signer(signing_key, keyring)
            doc = store.publish(
                PRINCIPAL,
                release_id,
                signer,
                int(store.current()["generation"]),
                version,
                f"{prefix}-publish-{version}",
            )
        else:
            raise SystemExit(f"release {release_id} is {doc['state']}; publish a successor instead")
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", type=UUID, action="append", required=True)
    parser.add_argument(
        "--source-database-env",
        default="PCB_MIGRATION_DATABASE_URL",
        help="variable holding a DSN that can read run, attempt and scorecard lineage",
    )
    parser.add_argument("--store", type=Path, default=local_stack.PUBLIC_RELEASE_STORE)
    parser.add_argument("--signing-key", type=Path, default=local_stack.PUBLIC_RELEASE_SIGNING_KEY)
    parser.add_argument("--keyring", type=Path, default=local_stack.PUBLIC_RELEASE_KEYRING)
    parser.add_argument("--target", help="catalog target (default PCB_PUBLICATION_TARGET)")
    parser.add_argument("--output-dir", type=Path, help="where the built documents are written")
    parser.add_argument("--no-sync", action="store_true", help="publish locally but do not mirror")
    args = parser.parse_args(argv)

    values = local_stack.read_env() if local_stack.ENV_PATH.exists() else {}
    source = Database(_setting(values, args.source_database_env))
    try:
        content, projection, evidence = build_live_release(source.engine, args.run_id)
    finally:
        source.dispose()
    output_dir = args.output_dir or ROOT / ".cache" / "live-releases" / str(args.run_id[0])
    paths = write_live_release(output_dir, content, projection, evidence)

    args.store.parent.mkdir(parents=True, exist_ok=True)
    store = ReleaseStore(args.store)
    doc = publish(store, content, projection, evidence, args.signing_key, args.keyring)
    result: dict[str, Any] = {
        "release_id": doc["id"],
        "fixture_kind": projection["fixture_kind"],
        "scope": projection["scope"],
        "entries": [entry["model_config_id"] for entry in content["entries"]],
        "key_id": doc["manifest"]["key_id"],
        "documents": paths,
    }
    if not args.no_sync:
        target = args.target or values.get("PCB_PUBLICATION_TARGET") or "local:board"
        publisher = Database(_setting(values, "PCB_PUBLISHER_DATABASE_URL"))
        try:
            result["sync"] = sync_publication(
                store,
                PostgresPublicReleaseCatalog(publisher.engine),
                keyring=Keyring.from_document(json.loads(args.keyring.read_text(encoding="utf-8"))),
                target=target,
            )
        finally:
            publisher.dispose()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
