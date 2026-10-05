"""Verified local release snapshots are reduced to the public API allowlist."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_core.application_errors import InvalidState
from polycodebench_operations.release_sync import verified_publication_snapshot
from polycodebench_publication.keyring import Keyring
from polycodebench_publication.projections_query import ReleaseContent
from polycodebench_publication.releases import (
    ReleaseStore,
    SigningKey,
    canonical_bytes,
    content_digest,
    digest,
)


@pytest.fixture
def signed_source(tmp_path):
    store = ReleaseStore(tmp_path / "source.sqlite3")
    signer = SigningKey("sync-test-key", Ed25519PrivateKey.generate())
    keyring = Keyring().rotate(signer, at="2026-10-05T00:00:00+00:00")
    release_id = "release-sync-test"
    content = ReleaseContent(
        policy_digest=digest({"policy": "test"}),
        formula_version="formula-test-v1",
    ).model_dump(mode="json")
    projection = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest({"cohort": "test"}),
        "limitations": ["Synthetic test data; not benchmark results."],
        "metrics": [
            {
                "metric_id": "pass_rate",
                "value": "0.5",
                "interval_low": None,
                "interval_high": None,
                "coverage": "1",
                "conditional_on_pass": False,
            }
        ],
    }
    public_digest = content_digest(content, projection)
    manifest = {
        "schema_version": 1,
        "release_id": release_id,
        "content_digest": public_digest,
        "projection_digest": digest(projection),
        "validation_digest": digest({"validation": "internal"}),
        "approval_digest": digest({"approval": "internal"}),
        "predecessor": None,
        "algorithm": "Ed25519",
        "key_id": signer.key_id,
    }
    manifest["signature"] = base64.b64encode(
        signer.private_key.sign(canonical_bytes(manifest))
    ).decode("ascii")
    source_document = {
        "id": release_id,
        "version": 4,
        "state": "published",
        "content": content,
        "projection": projection,
        "content_digest": public_digest,
        "manifest": manifest,
        "validation": {"private": "validation-secret"},
        "review": {"actor": "private-reviewer"},
        "approval": {"actor": "private-approver"},
        "predecessor": "private-predecessor",
        "correction_reason": "private workflow text",
    }
    published_at = datetime(2026, 10, 5, tzinfo=UTC).isoformat()
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO releases(id, document) VALUES (?, ?)",
            (release_id, json.dumps(source_document)),
        )
        connection.execute(
            "INSERT INTO pointers(target, generation, release_id) VALUES (?, ?, ?)",
            ("local:board", 1, release_id),
        )
        connection.execute(
            "INSERT INTO audit(subject, action, release_id, occurred_at, request_id, digest) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "private-publisher",
                "publish",
                release_id,
                published_at,
                "test-publish",
                public_digest,
            ),
        )
    return store, keyring, source_document


def test_signed_release_sync_strips_private_workflow_and_actor_fields(signed_source):
    store, keyring, _ = signed_source

    snapshots, pointer = verified_publication_snapshot(store, keyring=keyring)

    assert pointer == {"generation": 1, "release_id": "release-sync-test"}
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot["id"] == "release-sync-test"
    assert snapshot["published_at"].tzinfo is not None
    assert not {"validation", "review", "approval", "predecessor", "correction_reason"} & set(
        snapshot
    )
    assert "private-publisher" not in json.dumps(snapshot, default=str)
    assert "private-reviewer" not in json.dumps(snapshot, default=str)


def test_signed_release_sync_rejects_a_tampered_manifest(signed_source):
    store, keyring, source = signed_source
    source["manifest"]["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    with store._connect() as connection:
        connection.execute(
            "UPDATE releases SET document=? WHERE id=?",
            (json.dumps(source), source["id"]),
        )

    with pytest.raises(InvalidState, match="signature verification failed"):
        verified_publication_snapshot(store, keyring=keyring)


def test_production_api_refuses_task_local_sqlite_release_storage(monkeypatch):
    from polycodebench_api.app import create_app

    monkeypatch.setenv("PCB_ENVIRONMENT", "production")
    monkeypatch.setenv("PCB_PUBLIC_RELEASE_BACKEND", "sqlite")

    with pytest.raises(RuntimeError, match="shared PostgreSQL release catalog"):
        create_app(cursor_key=b"production-config-test-key-000000000000")
