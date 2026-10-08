"""Local, database-free adversarial checks for restore bundle validation."""

from __future__ import annotations

import hashlib
import io
import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    audit_document_digest,
    parse_audit_document,
)
from polycodebench_core.telemetry import MetricsRegistry
from polycodebench_operations.cli import _restore_verify
from polycodebench_operations.localenv import docker as localenv_docker
from polycodebench_operations.recovery import (
    RestoreVerificationError,
    StepTimer,
    _validate_audit_records,
    _verify_backup_files,
    rehearse_restore,
)


def _digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _bundle(root: Path) -> dict[str, object]:
    (root / "objects" / "internal").mkdir(parents=True)
    file_bodies = {
        "database.dump": b"synthetic database dump",
        "releases.db": b"synthetic release store",
        "keyring.json": b"{}",
    }
    for name, body in file_bodies.items():
        (root / name).write_bytes(body)
    key = "scorecards/one.json"
    object_body = b"synthetic private scorecard"
    object_path = root / "objects" / "internal" / key
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(object_body)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "created_at": "2026-10-09T00:00:00+00:00",
        "source": {
            "database": "polycodebench",
            "buckets": {"hidden": "hidden", "internal": "internal", "public": "public"},
            "board": "local:test-board",
        },
        "table_row_counts": {"audit_document": 1, "audit_event": 2},
        "objects": [
            {
                "visibility": "internal",
                "key": key,
                "sha256": _digest(object_body),
                "size": len(object_body),
            }
        ],
        "files": {name: _digest(body) for name, body in file_bodies.items()},
    }
    (root / "backup-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _audit_row(*, unresolved_ref: bool = False) -> dict[str, object]:
    document = {
        "id": "11111111-1111-4111-8111-111111111111",
        "kind": "benchmark_snapshot",
        "schema_version": 1,
        "payload": {
            "registry_ref": {
                "entity_id": "22222222-2222-4222-8222-222222222222",
                "entity_kind": "benchmark_registry",
                "digest": None,
            },
            "version": "v1",
            "split": "test",
            "membership": [],
            "components": [],
            "upstream_rights": (
                [
                    {
                        "document_id": "33333333-3333-4333-8333-333333333333",
                        "digest": _digest(b"missing"),
                        "kind": "corpus_snapshot",
                    }
                ]
                if unresolved_ref
                else []
            ),
            "importer_refs": [],
        },
        "metadata": {
            "created_at": "2026-10-08T12:00:00Z",
            "timestamp_precision": "second",
            "actor": "restore-test",
            "trace_id": None,
            "row_version": 0,
        },
        "supersedes_id": None,
    }
    parsed = parse_audit_document(json.dumps(document))
    return _stored_audit_row(parsed)


def _stored_audit_row(parsed: AuditDocument) -> dict[str, object]:
    return {
        "id": parsed.id,
        "kind": parsed.kind,
        "schema_version": parsed.schema_version,
        "semantic_digest": audit_document_digest(parsed),
        "payload": parsed.payload.model_dump(mode="json"),
        "supersedes_id": None,
        "created_by": parsed.metadata.actor,
        "tenant_id": None,
        "document_created_at": parsed.metadata.created_at,
        "timestamp_precision": parsed.metadata.timestamp_precision,
        "trace_id": None,
        "document_row_version": parsed.metadata.row_version,
    }


def _document_from_audit_row(row: dict[str, object]) -> AuditDocument:
    return parse_audit_document(
        json.dumps(
            {
                "id": str(row["id"]),
                "kind": row["kind"],
                "schema_version": row["schema_version"],
                "payload": row["payload"],
                "metadata": {
                    "created_at": row["document_created_at"],
                    "timestamp_precision": row["timestamp_precision"],
                    "actor": row["created_by"],
                    "trace_id": row["trace_id"],
                    "row_version": row["document_row_version"],
                },
                "supersedes_id": row["supersedes_id"],
            }
        )
    )


def test_restore_bundle_checks_digests_and_sizes_without_loading_objects_whole(
    tmp_path: Path,
) -> None:
    manifest = _bundle(tmp_path)

    report = _verify_backup_files(tmp_path, manifest)

    assert report["files_checked"] == 3
    assert report["objects_checked"] == 1
    assert report["bytes_checked"] > 0


def test_audit_restore_reparses_documents_and_checks_semantic_digest() -> None:
    row = _audit_row()
    _, report = _validate_audit_records([row], [])
    assert report["documents_checked"] == 1
    assert report["document_references_checked"] == 0

    row["semantic_digest"] = _digest(b"tampered")
    with pytest.raises(RuntimeError, match="identity or digest mismatch"):
        _validate_audit_records([row], [])


def test_audit_restore_rejects_missing_document_references() -> None:
    with pytest.raises(RuntimeError, match="reference is unresolved"):
        _validate_audit_records([_audit_row(unresolved_ref=True)], [])


def test_audit_restore_resolves_private_artifacts_and_rejects_missing_artifacts() -> None:
    snapshot = _document_from_audit_row(_audit_row())
    snapshot_ref = {
        "document_id": str(snapshot.id),
        "digest": audit_document_digest(snapshot),
        "kind": snapshot.kind,
    }
    query_artifact_id = "44444444-4444-4444-8444-444444444444"
    query_digest = _digest(b"private query")
    query = parse_audit_document(
        json.dumps(
            {
                "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "kind": "query_manifest",
                "schema_version": 1,
                "payload": {
                    "audit_ref": snapshot_ref,
                    "task_ref": {
                        "entity_id": "55555555-5555-4555-8555-555555555555",
                        "entity_kind": "task_version",
                        "digest": None,
                    },
                    "component_refs": [],
                    "query_type": "exact",
                    "query_artifact_ref": {
                        "artifact_id": query_artifact_id,
                        "digest": query_digest,
                        "visibility": "private",
                        "media_type": "application/json",
                    },
                    "connector_snapshot": snapshot_ref,
                    "limits": {"max_query_units": 1},
                    "disclosure_authorization": None,
                },
                "metadata": {
                    "created_at": "2026-10-08T12:00:00Z",
                    "timestamp_precision": "second",
                    "actor": "restore-test",
                    "trace_id": None,
                    "row_version": 0,
                },
                "supersedes_id": None,
            }
        )
    )
    artifact_row = {
        "id": query_artifact_id,
        "visibility": "hidden",
        "content_digest": query_digest,
        "media_type": "application/json",
        "status": "verified",
    }

    audit_rows = [_stored_audit_row(snapshot), _stored_audit_row(query)]
    _, report = _validate_audit_records(audit_rows, [artifact_row])

    assert report["artifact_references_checked"] == 1
    artifact_row["media_type"] = "text/plain"
    with pytest.raises(RuntimeError, match="artifact reference is unresolved"):
        _validate_audit_records(audit_rows, [artifact_row])
    with pytest.raises(RuntimeError, match="artifact reference is unresolved"):
        _validate_audit_records(audit_rows, [])


@pytest.mark.parametrize(
    "key", ["../outside.json", "folder/../../outside.json", r"..\outside.json"]
)
def test_restore_bundle_rejects_traversal_before_provisioning(tmp_path: Path, key: str) -> None:
    manifest = _bundle(tmp_path)
    objects = manifest["objects"]
    assert isinstance(objects, list)
    objects[0]["key"] = key
    (tmp_path / "backup-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    started = False

    def should_not_start() -> None:
        nonlocal started
        started = True
        raise AssertionError("untrusted backup must be rejected before Docker provisioning")

    report = rehearse_restore(
        tmp_path,
        work_dir=tmp_path / "work",
        start_environment=should_not_start,  # type: ignore[arg-type]
    )

    assert not started
    assert report["passed"] is False
    assert report["recovery_time_seconds"] is None
    assert report["resources_reclaimed"] is False
    failed = next(step for step in report["steps"] if step["status"] == "failed")
    assert failed["step"] == "verify backup file digests"
    assert key not in json.dumps(report)


def test_restore_bundle_rejects_duplicate_and_mismatched_objects(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)
    objects = manifest["objects"]
    assert isinstance(objects, list)
    objects.append(dict(objects[0]))
    with pytest.raises(RuntimeError, match="invalid"):
        _verify_backup_files(tmp_path, manifest)

    objects.pop()
    objects[0]["size"] = 1
    with pytest.raises(RuntimeError, match="size or digest mismatch"):
        _verify_backup_files(tmp_path, manifest)


def test_restore_bundle_rejects_visibility_bucket_aliases_and_portable_path_collisions(
    tmp_path: Path,
) -> None:
    manifest = _bundle(tmp_path)
    source = manifest["source"]
    assert isinstance(source, dict)
    buckets = source["buckets"]
    assert isinstance(buckets, dict)
    buckets["public"] = buckets["internal"]
    with pytest.raises(RuntimeError, match="source inventory"):
        _verify_backup_files(tmp_path, manifest)

    buckets["public"] = "public"
    objects = manifest["objects"]
    assert isinstance(objects, list)
    objects.append(
        {
            **objects[0],
            "key": "SCORECARDS/ONE.JSON",
        }
    )
    with pytest.raises(RuntimeError, match="colliding paths"):
        _verify_backup_files(tmp_path, manifest)


def test_step_timer_does_not_copy_sensitive_exception_messages() -> None:
    timer = StepTimer()
    with pytest.raises(RuntimeError):
        with timer.step("restore"):
            raise RuntimeError("private canary https://source.invalid/secret-token")

    assert timer.steps[0]["error"] == "RuntimeError"
    assert "canary" not in json.dumps(timer.steps)
    assert "secret-token" not in json.dumps(timer.steps)


def test_step_timer_keeps_only_a_safe_database_diagnostic_code() -> None:
    timer = StepTimer()
    error = RuntimeError("private query parameter")
    error.orig = SimpleNamespace(sqlstate="42703", detail="private source ref")  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        with timer.step("database"):
            raise error

    assert timer.steps[0]["diagnostic_code"] == "42703"
    assert "private" not in json.dumps(timer.steps)


def test_step_timer_exposes_stable_recovery_codes_without_exception_text() -> None:
    timer = StepTimer()
    with pytest.raises(RestoreVerificationError):
        with timer.step("audit recovery"):
            raise RestoreVerificationError("benchmark_audit_schema_missing")

    assert timer.steps[0]["error_code"] == "benchmark_audit_schema_missing"


def test_local_docker_helper_streams_database_dump_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_file = io.BytesIO(b"database dump")
    output_file = io.BytesIO()
    seen: dict[str, object] = {}

    def fake_run(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        seen["arguments"] = arguments
        seen.update(kwargs)
        output_file.write(b"streamed dump")
        return SimpleNamespace(returncode=0, stdout=None, stderr=b"")

    monkeypatch.setattr("polycodebench_operations.localenv.subprocess.run", fake_run)

    result = localenv_docker(
        "exec", "postgres", "pg_restore", input_file=input_file, output_file=output_file
    )

    assert result == b""
    assert seen["stdin"] is input_file
    assert seen["stdout"] is output_file
    assert "input" not in seen
    assert output_file.getvalue() == b"streamed dump"


def test_restore_verify_cli_redacts_driver_messages_in_stdout_and_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_with_sensitive_detail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private canary source-token-123")

    monkeypatch.setattr(
        "polycodebench_operations.recovery.verify_restored", fail_with_sensitive_detail
    )
    keyring = tmp_path / "keyring.json"
    keyring.write_text("{}", encoding="utf-8")
    evidence = tmp_path / "restore-report.json"
    args = Namespace(
        database_url="postgresql://unused",
        object_store_endpoint=None,
        bucket_hidden="hidden",
        bucket_internal="internal",
        bucket_public="public",
        release_store=tmp_path / "releases.db",
        keyring=keyring,
        board="local:test",
        replay_count=1,
        seed="test",
        evidence=evidence,
    )

    assert _restore_verify(args, MetricsRegistry()) == 1
    stdout = capsys.readouterr().out
    report = evidence.read_text(encoding="utf-8")
    assert "RuntimeError" in stdout
    assert "RuntimeError" in report
    assert "canary" not in stdout + report
    assert "source-token-123" not in stdout + report


def test_restore_bundle_rejects_symlinked_inventory_files(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)
    target = tmp_path / "outside.json"
    target.write_bytes(b"outside")
    linked = tmp_path / "database.dump"
    linked.unlink()
    try:
        linked.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem does not allow creating symlinks")

    with pytest.raises(RuntimeError, match="symbolic link"):
        _verify_backup_files(tmp_path, manifest)
