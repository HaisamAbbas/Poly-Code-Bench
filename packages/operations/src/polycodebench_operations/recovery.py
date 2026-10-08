"""Backup and isolated-restore rehearsal (T 22.6, E2E-42).

``backup`` captures a consistent copy of one environment: a PostgreSQL custom-format dump, every
object of the hidden/internal/public buckets with its SHA-256, the publication store and the
public verification keyring. ``rehearse_restore`` restores that copy into a brand-new isolated
environment and proves it is a *usable* recovery, not just a copy:

1. referential integrity - every foreign key in the restored schema has zero orphans, and
   per-table row counts equal the backup manifest;
2. digest integrity - every ``verified`` artifact row resolves to a restored object whose bytes
   hash to the recorded ``content_digest`` and size;
3. replay - ten stratified scorecards are re-scored from their restored archive artifacts and
   must reproduce the archived canonical bytes and the scorecard row's gate/composite;
4. projection - the public release projection is rebuilt from restored scorecard rows and must
   equal the signed published projection; the manifest must verify against the keyring and the
   board pointer must reference available data;
5. timing - every step is timed; the reported recovery time is measured, not a target;
6. reclamation - the isolated environment is destroyed and checked to be gone.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    SealAccessEventDocument,
    SealedManifestDocumentV2,
    audit_document_digest,
    parse_audit_document,
    validate_sealed_manifest_transition,
)
from polycodebench_core.telemetry import MetricsRegistry
from polycodebench_persistence.benchmark_audit import PostgresBenchmarkAuditRepository
from polycodebench_persistence.object_store import (
    create_s3_compatible_client,
    object_store_addressing_style,
    object_store_provider,
    object_store_ram_role_name,
    object_store_region,
)
from polycodebench_publication.keyring import Keyring
from polycodebench_publication.releases import ReleaseStore, content_digest, digest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from polycodebench_operations import localenv
from polycodebench_operations.migrations import PERSISTENCE, run_sql_file
from polycodebench_operations.rehearsal_data import (
    BUNDLE_MEDIA_TYPE,
    ScorecardRow,
    parse_bundle,
    rehearsal_content,
    rehearsal_projection,
    replay_bundle,
    select_stratified,
)

VISIBILITIES = ("hidden", "internal", "public")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_MAX_BACKUP_OBJECTS = 1_000_000
_MAX_BACKUP_MANIFEST_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class EnvironmentSource:
    """Where one environment's data lives (local-docker target)."""

    postgres_container: str
    database: str
    database_url: str
    object_store_endpoint: str
    buckets: dict[str, str]
    object_store_access_key: str
    object_store_secret_key: str
    release_store_path: Path
    keyring_path: Path
    board: str = "local:board"


def _s3(
    endpoint: str | None,
    access_key: str | None = None,
    secret_key: str | None = None,
) -> Any:
    """Use explicit emulator keys, Alibaba ECS role credentials, or the AWS IAM chain."""

    if endpoint is None:
        if object_store_provider() == "alibaba_oss":
            raise RuntimeError("PCB_OBJECT_STORE_ENDPOINT is required for Alibaba OSS")
        return boto3.client("s3")
    provider = object_store_provider()
    if provider == "alibaba_oss":
        return create_s3_compatible_client(
            endpoint_url=endpoint,
            region_name=object_store_region(),
            addressing_style=object_store_addressing_style(),
            provider=provider,
            ram_role_name=object_store_ram_role_name(),
        )
    if not access_key or not secret_key:
        raise RuntimeError("local object-store credentials are required for an emulator endpoint")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=object_store_region(),
        config=Config(
            s3={"addressing_style": object_store_addressing_style()}, signature_version="s3v4"
        ),
    )
    return client


def _engine(url: str) -> Engine:
    return create_engine(make_url(url).set(drivername="postgresql+psycopg"), poolclass=NullPool)


def _sha256(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _table_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        tables = connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        ).scalars()
        return {
            table: int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())
            for table in list(tables)
        }


# ------------------------------------------------------------------------------- backup


def backup(source: EnvironmentSource, destination: Path) -> dict[str, Any]:
    started = time.perf_counter()
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "database.dump").open("wb") as dump_file:
        localenv.docker(
            "exec",
            source.postgres_container,
            "pg_dump",
            "-U",
            "polycodebench",
            "-d",
            source.database,
            "--format=custom",
            "--no-password",
            output_file=dump_file,
        )
    engine = _engine(source.database_url)
    try:
        counts = _table_counts(engine)
    finally:
        engine.dispose()
    client = _s3(
        source.object_store_endpoint,
        source.object_store_access_key,
        source.object_store_secret_key,
    )
    objects: list[dict[str, Any]] = []
    for visibility in VISIBILITIES:
        bucket = source.buckets[visibility]
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for entry in page.get("Contents", []):
                body = client.get_object(Bucket=bucket, Key=entry["Key"])["Body"].read()
                path = destination / "objects" / visibility / entry["Key"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                objects.append(
                    {
                        "visibility": visibility,
                        "key": entry["Key"],
                        "sha256": _sha256(body),
                        "size": len(body),
                    }
                )
    with (
        sqlite3.connect(source.release_store_path) as live,
        sqlite3.connect(destination / "releases.db") as copy,
    ):
        live.backup(copy)
    shutil.copyfile(source.keyring_path, destination / "keyring.json")
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {"database": source.database, "buckets": source.buckets, "board": source.board},
        "table_row_counts": counts,
        "objects": objects,
        "files": {
            name: _sha256((destination / name).read_bytes())
            for name in ("database.dump", "releases.db", "keyring.json")
        },
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    (destination / "backup-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


# ------------------------------------------------------------------------------- restore


@dataclass
class StepTimer:
    steps: list[dict[str, Any]] = field(default_factory=list)

    @contextmanager
    def step(self, name: str) -> Iterator[dict[str, Any]]:
        record: dict[str, Any] = {"step": name, "status": "running"}
        self.steps.append(record)
        started = time.perf_counter()
        try:
            yield record
            record["status"] = "passed" if record.get("status") == "running" else record["status"]
        except Exception as error:
            record["status"] = "failed"
            # Exception messages from drivers can contain object keys, source URLs, credentials,
            # or canary material. Evidence records retain the error class only.
            record["error"] = type(error).__name__
            safe_code = getattr(error, "safe_code", None)
            if isinstance(safe_code, str) and re.fullmatch(
                r"[a-z][a-z0-9_]{1,63}", safe_code, re.ASCII
            ):
                record["error_code"] = safe_code
            driver_error = getattr(error, "orig", None)
            diagnostic_code = getattr(driver_error, "sqlstate", None) or getattr(
                driver_error, "pgcode", None
            )
            if isinstance(diagnostic_code, str) and re.fullmatch(
                r"[0-9A-Z]{5}", diagnostic_code, re.ASCII
            ):
                record["diagnostic_code"] = diagnostic_code
            raise
        finally:
            record["seconds"] = round(time.perf_counter() - started, 3)


class RestoreVerificationError(RuntimeError):
    """A safe, stable recovery failure code without untrusted driver details."""

    def __init__(self, safe_code: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", safe_code, re.ASCII):
            raise ValueError("invalid recovery verification code")
        super().__init__(safe_code)
        self.safe_code = safe_code


def _verify_backup_files(backup_dir: Path, manifest: dict[str, Any]) -> dict[str, int]:
    """Fail closed on malformed manifests, path escapes, symlinks and byte mismatches."""
    if backup_dir.is_symlink():
        raise RuntimeError("backup directory cannot be a symbolic link")
    root = backup_dir.resolve(strict=True)
    manifest_path = root / "backup-manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > _MAX_BACKUP_MANIFEST_BYTES
    ):
        raise RuntimeError("backup manifest is not a bounded regular file")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise RuntimeError("unsupported backup manifest schema")
    if not isinstance(manifest.get("created_at"), str):
        raise RuntimeError("backup timestamp is invalid")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise RuntimeError("backup source inventory is invalid")
    buckets = source.get("buckets")
    if (
        not isinstance(buckets, dict)
        or set(buckets) != set(VISIBILITIES)
        or any(
            not isinstance(bucket, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", bucket, re.ASCII)
            for bucket in buckets.values()
        )
        or len(set(buckets.values())) != len(VISIBILITIES)
        or not isinstance(source.get("database"), str)
        or not source["database"]
        or not isinstance(source.get("board"), str)
    ):
        raise RuntimeError("backup source inventory is invalid")
    row_counts = manifest.get("table_row_counts")
    if not isinstance(row_counts, dict) or any(
        not isinstance(table, str) or type(count) is not int or count < 0
        for table, count in row_counts.items()
    ):
        raise RuntimeError("backup table-count inventory is invalid")

    files = manifest.get("files")
    required_files = {"database.dump", "releases.db", "keyring.json"}
    if not isinstance(files, dict) or set(files) != required_files:
        raise RuntimeError("backup manifest file inventory is invalid")

    seen_paths: set[str] = set()

    def safe_file(relative: PurePosixPath) -> Path:
        if (
            relative.is_absolute()
            or not relative.parts
            or any(
                part in {"", ".", ".."} or ":" in part or "\\" in part or "\x00" in part
                for part in relative.parts
            )
        ):
            raise RuntimeError("backup inventory contains an unsafe path")
        portable_name = relative.as_posix().casefold()
        if portable_name in seen_paths:
            raise RuntimeError("backup inventory contains colliding paths")
        seen_paths.add(portable_name)
        path = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RuntimeError("backup inventory contains a symbolic link")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise RuntimeError("backup inventory file is missing") from error
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise RuntimeError("backup inventory path is outside the backup directory")
        return resolved

    bytes_checked = 0
    for name, expected in files.items():
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or not _DIGEST.fullmatch(expected)
        ):
            raise RuntimeError("backup manifest contains an invalid file digest")
        path = safe_file(PurePosixPath(name))
        bytes_checked += path.stat().st_size
        if _sha256_file(path) != expected:
            raise RuntimeError("backup file digest mismatch")

    objects = manifest.get("objects")
    if not isinstance(objects, list) or len(objects) > _MAX_BACKUP_OBJECTS:
        raise RuntimeError("backup object inventory exceeds its supported bound")
    seen: set[tuple[str, str]] = set()
    for item in objects:
        if not isinstance(item, dict):
            raise RuntimeError("backup object inventory entry is invalid")
        visibility = item.get("visibility")
        key = item.get("key")
        expected = item.get("sha256")
        size = item.get("size")
        if (
            visibility not in VISIBILITIES
            or not isinstance(key, str)
            or not isinstance(expected, str)
            or not _DIGEST.fullmatch(expected)
            or type(size) is not int
            or size < 0
            or (visibility, key) in seen
        ):
            raise RuntimeError("backup object inventory entry is invalid")
        seen.add((visibility, key))
        object_path = safe_file(PurePosixPath("objects") / visibility / key)
        if object_path.stat().st_size != size or _sha256_file(object_path) != expected:
            raise RuntimeError("backup object size or digest mismatch")
        bytes_checked += size
    return {
        "files_checked": len(files),
        "objects_checked": len(objects),
        "bytes_checked": bytes_checked,
    }


def _read_backup_manifest(backup_dir: Path) -> dict[str, Any]:
    if backup_dir.is_symlink():
        raise RuntimeError("backup directory cannot be a symbolic link")
    manifest_path = backup_dir / "backup-manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > _MAX_BACKUP_MANIFEST_BYTES
    ):
        raise RuntimeError("backup manifest is not a bounded regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("backup manifest is unreadable or invalid") from error
    if not isinstance(manifest, dict):
        raise RuntimeError("backup manifest must be an object")
    return manifest


def _reference_values(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        keys = set(value)
        if keys == {"document_id", "digest", "kind"} or keys == {
            "artifact_id",
            "digest",
            "visibility",
            "media_type",
        }:
            yield value
        for child in value.values():
            yield from _reference_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _reference_values(child)


def _validate_audit_records(
    document_rows: list[dict[str, Any]], artifact_rows: list[dict[str, Any]]
) -> tuple[dict[str, AuditDocument], dict[str, int]]:
    """Reparse immutable audit bytes and prove every embedded reference survives restore."""
    documents: dict[str, AuditDocument] = {}
    stored_digests: dict[str, str] = {}
    for row in document_rows:
        document_id = str(row["id"])
        raw = {
            "id": document_id,
            "kind": row["kind"],
            "schema_version": row["schema_version"],
            "payload": row["payload"],
            "metadata": {
                "created_at": row["document_created_at"],
                "timestamp_precision": row["timestamp_precision"],
                "actor": row["created_by"],
                "trace_id": str(row["trace_id"]) if row["trace_id"] is not None else None,
                "row_version": row["document_row_version"],
            },
            "supersedes_id": str(row["supersedes_id"])
            if row["supersedes_id"] is not None
            else None,
        }
        try:
            document = parse_audit_document(json.dumps(raw, separators=(",", ":")))
        except (TypeError, ValueError) as error:
            raise RuntimeError("restored benchmark audit document is invalid") from error
        actual_digest = audit_document_digest(document)
        if (
            document.kind != row["kind"]
            or document.schema_version != row["schema_version"]
            or actual_digest != row["semantic_digest"]
            or document_id in documents
        ):
            raise RuntimeError("restored benchmark audit document identity or digest mismatch")
        documents[document_id] = document
        stored_digests[document_id] = actual_digest

    artifacts = {str(row["id"]): row for row in artifact_rows}
    document_refs = 0
    artifact_refs = 0
    for document in documents.values():
        for reference in _reference_values(document.payload.model_dump(mode="json")):
            if "document_id" in reference:
                document_refs += 1
                referenced = documents.get(reference["document_id"])
                if (
                    referenced is None
                    or referenced.kind != reference["kind"]
                    or stored_digests[reference["document_id"]] != reference["digest"]
                ):
                    raise RuntimeError("restored audit document reference is unresolved")
                continue

            artifact_refs += 1
            artifact = artifacts.get(reference["artifact_id"])
            expected_visibility = {
                "private": "hidden",
                "restricted": "internal",
                "public": "public",
            }.get(reference["visibility"])
            if (
                artifact is None
                or artifact["content_digest"] != reference["digest"]
                or artifact["visibility"] != expected_visibility
                or artifact["media_type"] != reference["media_type"]
                or artifact["status"] != "verified"
            ):
                raise RuntimeError("restored audit artifact reference is unresolved")

    sealed_transitions = 0
    legacy_sealed_manifests = 0
    for document in documents.values():
        if document.kind != "sealed_manifest":
            continue
        if not isinstance(document, SealedManifestDocumentV2):
            legacy_sealed_manifests += 1
            continue
        if document.supersedes_id is None:
            if document.payload.access_event_refs or document.payload.disclosure_state != "sealed":
                raise RuntimeError("restored initial sealed manifest has invalid exposure history")
            continue
        previous = documents.get(str(document.supersedes_id))
        if not isinstance(previous, SealedManifestDocumentV2):
            raise RuntimeError("restored sealed manifest predecessor is unavailable")
        event_refs = document.payload.access_event_refs
        if not event_refs:
            raise RuntimeError("restored sealed manifest has no access event")
        event = documents.get(str(event_refs[-1].document_id))
        if not isinstance(event, SealAccessEventDocument):
            raise RuntimeError("restored sealed manifest access event is unavailable")
        try:
            validate_sealed_manifest_transition(previous, document, event)
        except ValueError as error:
            raise RuntimeError("restored sealed manifest history is inconsistent") from error
        sealed_transitions += 1

    return documents, {
        "documents_checked": len(documents),
        "document_references_checked": document_refs,
        "artifact_references_checked": artifact_refs,
        "sealed_transitions_checked": sealed_transitions,
        "legacy_sealed_manifests_present": legacy_sealed_manifests,
    }


def _verify_benchmark_audit_state(engine: Engine) -> dict[str, Any]:
    row_counts = _table_counts(engine)
    required_tables = {
        "audit_document",
        "audit_event",
        "audit_run",
        "audit_query",
        "audit_checkpoint",
        "call_intent",
        "call_delivery",
        "monitor_slot",
        "monitor_slot_source",
        "monitor_alert_inbox",
    }
    if required_tables - row_counts.keys():
        raise RestoreVerificationError("benchmark_audit_schema_missing")
    with engine.connect() as connection:
        document_rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, kind, schema_version, semantic_digest, payload, supersedes_id, "
                    "created_by, tenant_id, document_created_at, timestamp_precision, trace_id, "
                    "document_row_version FROM audit_document ORDER BY id"
                )
            ).mappings()
        ]
        artifact_rows = [
            dict(row)
            for row in connection.execute(
                text("SELECT id, visibility, content_digest, media_type, status FROM artifact")
            ).mappings()
        ]

    documents, reference_report = _validate_audit_records(document_rows, artifact_rows)
    repository = PostgresBenchmarkAuditRepository(engine)
    attestations_checked = 0
    lifecycle_events_checked = 0
    tenant_ids = {str(row["id"]): row["tenant_id"] for row in document_rows}
    for document_id, document in documents.items():
        tenant_id = tenant_ids[document_id]
        if document.kind != "audit_attestation" or tenant_id is None:
            continue
        events = repository.list_attestation_lifecycle_events(document.id, tenant_id=tenant_id)
        attestations_checked += 1
        lifecycle_events_checked += len(events)

    tracked_tables = (
        "audit_document",
        "audit_event",
        "audit_run",
        "audit_query",
        "audit_checkpoint",
        "call_intent",
        "call_delivery",
        "budget_account",
        "budget_reservation",
        "budget_resource",
        "usage_record",
        "monitor_slot",
        "monitor_slot_source",
        "monitor_alert_inbox",
    )
    return {
        **reference_report,
        "attestations_checked": attestations_checked,
        "attestation_lifecycle_events_checked": lifecycle_events_checked,
        "persisted_evidence_row_counts": {
            name: row_counts[name] for name in tracked_tables if name in row_counts
        },
        # Retrieval plans currently have no durable index configuration or rebuild adapter.
        "retrieval_index_configuration": "not_persisted_or_rebuildable",
        "external_key_material": "references_preserved; provider_restore_unverified",
    }


def foreign_key_orphans(engine: Engine) -> dict[str, int]:
    """Orphan count per foreign key constraint (all must be zero)."""

    with engine.connect() as connection:
        constraints = connection.execute(
            text(
                """
                SELECT c.conname, c.conrelid::regclass::text AS child,
                       c.confrelid::regclass::text AS parent,
                       array_agg(ca.attname ORDER BY k.ord) AS child_cols,
                       array_agg(pa.attname ORDER BY k.ord) AS parent_cols
                FROM pg_constraint c
                CROSS JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY
                    AS k(child_attnum, parent_attnum, ord)
                JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = k.child_attnum
                JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.parent_attnum
                WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
                GROUP BY c.conname, c.conrelid, c.confrelid
                ORDER BY 1
                """
            )
        ).all()
        orphans: dict[str, int] = {}
        for name, child, parent, child_cols, parent_cols in constraints:
            not_null = " AND ".join(f'ch."{col}" IS NOT NULL' for col in child_cols)
            join = " AND ".join(
                f'p."{p}" = ch."{c}"' for c, p in zip(child_cols, parent_cols, strict=True)
            )
            orphans[f"{child}.{name}"] = int(
                connection.execute(
                    text(
                        f"SELECT count(*) FROM {child} ch WHERE {not_null} AND NOT EXISTS "
                        f"(SELECT 1 FROM {parent} p WHERE {join})"
                    )
                ).scalar_one()
            )
    return orphans


def verify_artifact_digests(
    engine: Engine,
    endpoint: str | None,
    buckets: dict[str, str],
    access_key: str | None = None,
    secret_key: str | None = None,
) -> dict[str, Any]:
    client = _s3(endpoint, access_key, secret_key)
    checked = missing = mismatched = 0
    failures: list[str] = []
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, visibility, storage_key, content_digest, size_bytes FROM artifact "
                "WHERE status = 'verified' ORDER BY id"
            )
        ).all()
    for artifact_id, visibility, key, expected, size in rows:
        checked += 1
        try:
            body = client.get_object(Bucket=buckets[visibility], Key=key)["Body"].read()
        except client.exceptions.NoSuchKey:
            missing += 1
            failures.append(f"{artifact_id}: object missing")
            continue
        if _sha256(body) != expected or len(body) != size:
            mismatched += 1
            failures.append(f"{artifact_id}: digest/size mismatch")
    return {
        "verified_artifacts_checked": checked,
        "missing_objects": missing,
        "digest_mismatches": mismatched,
        "failures": failures[:20],
    }


def scorecard_rows(engine: Engine) -> list[tuple[ScorecardRow, str, str, str]]:
    """(row, artifact visibility, storage key, artifact digest) for every bundled scorecard."""

    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT s.id, s.gate, s.composite, a.visibility, a.storage_key, a.content_digest, "
                "a.media_type FROM scorecard s JOIN artifact a ON a.id = s.artifact_id "
                "ORDER BY s.id"
            )
        ).all()
    rows: list[tuple[ScorecardRow, str, str, str]] = []
    for scorecard_id, gate, composite, visibility, key, artifact_digest, media in result:
        if media != BUNDLE_MEDIA_TYPE:
            continue
        rows.append(
            (
                # The stratum label lives in the archive bundle; _load_bundles fills it in.
                ScorecardRow(str(scorecard_id), "", gate, composite),
                visibility,
                key,
                artifact_digest,
            )
        )
    return rows


def _load_bundles(
    engine: Engine,
    endpoint: str | None,
    buckets: dict[str, str],
    access_key: str | None = None,
    secret_key: str | None = None,
) -> list[tuple[ScorecardRow, dict[str, Any]]]:
    client = _s3(endpoint, access_key, secret_key)
    loaded: list[tuple[ScorecardRow, dict[str, Any]]] = []
    for row, visibility, key, artifact_digest in scorecard_rows(engine):
        body = client.get_object(Bucket=buckets[visibility], Key=key)["Body"].read()
        if _sha256(body) != artifact_digest:
            raise RuntimeError(f"scorecard {row.scorecard_id}: archive digest mismatch")
        bundle = parse_bundle(body)
        loaded.append(
            (ScorecardRow(row.scorecard_id, bundle["stratum"], row.gate, row.composite), bundle)
        )
    return loaded


def _composite(total: Decimal | None) -> Decimal | None:
    return None if total is None else (total / Decimal(100)).quantize(Decimal("0.00000001"))


def replay_selected(
    bundles: list[tuple[ScorecardRow, dict[str, Any]]], *, count: int, seed: str
) -> dict[str, Any]:
    rows = [row for row, _ in bundles]
    by_id = {row.scorecard_id: bundle for row, bundle in bundles}
    selected = select_stratified(rows, count=count, seed=seed)
    results: list[dict[str, Any]] = []
    for row in selected:
        report, archived = replay_bundle(by_id[row.scorecard_id])
        total = archived.scorecard.total_score
        row_matches = row.gate == str(archived.scorecard.gate) and (
            row.composite == _composite(Decimal(str(total)) if total is not None else None)
        )
        results.append(
            {
                "scorecard_id": row.scorecard_id,
                "stratum": row.stratum,
                "gate": row.gate,
                "replay_matched": report.matched,
                "row_matches_archive": row_matches,
                "outcome_digest": report.outcome_digest,
                "checks": [check.name for check in report.checks if check.passed],
            }
        )
    return {
        "seed": seed,
        "requested": count,
        "available_scorecards": len(rows),
        "strata_available": sorted({row.stratum for row in rows}),
        "strata_selected": sorted({row.stratum for row in selected}),
        "replayed": results,
        "all_matched": len(results) == count
        and all(item["replay_matched"] and item["row_matches_archive"] for item in results),
    }


def rebuild_projection(
    bundles: list[tuple[ScorecardRow, dict[str, Any]]],
    release_store_path: Path,
    keyring_document: dict[str, Any],
    board: str,
) -> dict[str, Any]:
    store = ReleaseStore(release_store_path)
    keyring = Keyring.from_document(keyring_document)
    published = [doc for doc in store.list_public() if doc["state"] == "published"]
    pointer = store.current()
    if not published:
        raise RuntimeError("no published release to reconstruct")
    rows = [row for row, _ in bundles]
    results: list[dict[str, Any]] = []
    for doc in published:
        ids = set(doc["content"].get("scorecard_ids", []))
        members = [row for row in rows if row.scorecard_id in ids]
        cohort = doc["content"].get("cohort", {})
        rebuilt = rehearsal_projection(members, cohort)
        manifest = doc["manifest"]
        signature_ok, key_state = keyring.verify(manifest)
        results.append(
            {
                "release_id": doc["id"],
                "members_restored": len(members),
                "members_expected": len(ids),
                "signature_valid": signature_ok,
                "signing_key_state": key_state,
                "projection_digest_published": manifest["projection_digest"],
                "projection_digest_rebuilt": digest(rebuilt),
                "content_lists_restored_members": rehearsal_content(members)["scorecard_ids"]
                == sorted(ids),
                "content_digest_matches": content_digest(doc["content"], rebuilt)
                == manifest["content_digest"],
            }
        )
    pointer_release = pointer.get("release_id")
    return {
        "board": board,
        "pointer": pointer,
        "pointer_references_available_release": pointer_release is None
        or any(item["release_id"] == pointer_release for item in results),
        "releases": results,
        "all_rebuilt": all(
            item["signature_valid"]
            and item["members_restored"] == item["members_expected"]
            and item["projection_digest_published"] == item["projection_digest_rebuilt"]
            and item["content_digest_matches"]
            and item["content_lists_restored_members"]
            for item in results
        ),
    }


def verify_restored(
    timer: StepTimer,
    *,
    database_url: str,
    object_store_endpoint: str | None,
    buckets: dict[str, str],
    object_store_access_key: str | None = None,
    object_store_secret_key: str | None = None,
    release_store_path: Path,
    keyring_document: dict[str, Any],
    board: str,
    expected_row_counts: dict[str, int] | None,
    replay_count: int,
    seed: str,
) -> None:
    """Steps 1-4 of the module docstring against any restored database + object store.

    Shared by the local-docker rehearsal and ``pcb-ops restore verify`` (used after an AWS
    point-in-time restore, where ``object_store_endpoint`` is ``None``).
    """

    engine = _engine(database_url)
    try:
        with timer.step("referential integrity") as record:
            orphans = foreign_key_orphans(engine)
            counts = _table_counts(engine)
            count_mismatch = {
                table: {"backup": expected, "restored": counts.get(table)}
                for table, expected in (expected_row_counts or {}).items()
                if counts.get(table) != expected
            }
            record.update(
                foreign_keys_checked=len(orphans),
                orphan_rows=sum(orphans.values()),
                tables_checked=len(counts),
                rows_restored=sum(counts.values()),
                row_count_parity_checked=expected_row_counts is not None,
                row_count_mismatches=count_mismatch,
            )
            if sum(orphans.values()) or count_mismatch:
                raise RuntimeError("referential integrity or row-count parity failed")
        with timer.step("benchmark audit evidence and history") as record:
            record.update(_verify_benchmark_audit_state(engine))
        with timer.step("artifact digest integrity") as record:
            digests = verify_artifact_digests(
                engine,
                object_store_endpoint,
                buckets,
                object_store_access_key,
                object_store_secret_key,
            )
            record.update(digests)
            if digests["missing_objects"] or digests["digest_mismatches"]:
                raise RuntimeError("restored artifacts do not match recorded digests")
        with timer.step("replay stratified scorecards") as record:
            bundles = _load_bundles(
                engine,
                object_store_endpoint,
                buckets,
                object_store_access_key,
                object_store_secret_key,
            )
            replay = replay_selected(bundles, count=replay_count, seed=seed)
            record.update(replay)
            if not replay["all_matched"]:
                raise RuntimeError("scorecard replay did not reproduce archived outcomes")
        with timer.step("rebuild public projection") as record:
            projection = rebuild_projection(bundles, release_store_path, keyring_document, board)
            record.update(projection)
            if not (
                projection["all_rebuilt"] and projection["pointer_references_available_release"]
            ):
                raise RuntimeError("public projection could not be reconstructed")
    finally:
        engine.dispose()


def rehearse_restore(
    backup_dir: Path,
    *,
    work_dir: Path,
    replay_count: int = 10,
    seed: str = "pcb-restore-rehearsal-v1",
    registry: MetricsRegistry | None = None,
    start_environment: Callable[[], localenv.IsolatedEnvironment] = localenv.start,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    buckets: dict[str, str] = {}
    timer = StepTimer()
    report: dict[str, Any] = {
        "schema_version": 1,
        "target": "local-docker",
        "environment_class": "isolated local restore (production-shaped rehearsal, not staging)",
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    env: localenv.IsolatedEnvironment | None = None
    started = time.perf_counter()
    failure: str | None = None
    try:
        with timer.step("verify backup file digests") as record:
            manifest = _read_backup_manifest(backup_dir)
            record.update(_verify_backup_files(backup_dir, manifest))
            buckets = manifest["source"]["buckets"]
            report["backup_created_at"] = manifest["created_at"]
        with timer.step("provision isolated environment") as record:
            env = start_environment()
            record["rehearsal_id"] = env.rehearsal_id
            record["images"] = localenv.pinned_images()
        with timer.step("restore database"):
            run_sql_file(env.database_url, PERSISTENCE / "sql" / "provision_roles.sql")
            with (backup_dir / "database.dump").open("rb") as dump_file:
                localenv.docker(
                    "exec",
                    "-i",
                    env.postgres_container,
                    "pg_restore",
                    "-U",
                    "polycodebench",
                    "-d",
                    "polycodebench",
                    "--exit-on-error",
                    "--no-password",
                    input_file=dump_file,
                )
        with timer.step("restore objects") as record:
            client = _s3(
                env.object_store_endpoint,
                env.object_store_access_key,
                env.object_store_secret_key,
            )
            for bucket in buckets.values():
                client.create_bucket(Bucket=bucket)
            for item in manifest["objects"]:
                object_path = backup_dir / "objects" / item["visibility"] / item["key"]
                with object_path.open("rb") as body:
                    client.put_object(
                        Bucket=buckets[item["visibility"]], Key=item["key"], Body=body
                    )
            record["objects"] = len(manifest["objects"])
        with timer.step("restore publication store and keyring"):
            work_dir.mkdir(parents=True, exist_ok=True)
            restored_store = work_dir / "releases.db"
            shutil.copyfile(backup_dir / "releases.db", restored_store)
            keyring_document = json.loads((backup_dir / "keyring.json").read_text("utf-8"))
        verify_restored(
            timer,
            database_url=env.database_url,
            object_store_endpoint=env.object_store_endpoint,
            buckets=buckets,
            object_store_access_key=env.object_store_access_key,
            object_store_secret_key=env.object_store_secret_key,
            release_store_path=restored_store,
            keyring_document=keyring_document,
            board=manifest["source"]["board"],
            expected_row_counts=manifest["table_row_counts"],
            replay_count=replay_count,
            seed=seed,
        )
        report["recovery_time_seconds"] = round(time.perf_counter() - started, 3)
    except Exception as error:  # noqa: BLE001 - recorded, then re-raised by the caller's exit code
        failure = f"{type(error).__name__}: restore rehearsal failed"
        report["recovery_time_seconds"] = None
    finally:
        if env is not None:
            try:
                with timer.step("teardown and reclamation check") as record:
                    record.update(localenv.teardown(env))
            except Exception as error:  # noqa: BLE001 - cleanup status is retained without details
                failure = failure or f"{type(error).__name__}: isolated cleanup failed"
                report["recovery_time_seconds"] = None
    report["steps"] = timer.steps
    reclaimed = next(
        (step.get("reclaimed") for step in timer.steps if step["step"].startswith("teardown")),
        False,
    )
    report["resources_reclaimed"] = bool(reclaimed)
    report["passed"] = failure is None and bool(reclaimed)
    report["failure"] = failure
    report["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    if registry is not None:
        failed = sum(1 for step in timer.steps if step["status"] == "failed")
        registry.set("pcb_backup_integrity_failures", float(failed), target="local-docker")
        if report["passed"]:
            registry.set("pcb_restore_last_success_timestamp", time.time(), target="local-docker")
    return report


def summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report[key]
        for key in ("passed", "recovery_time_seconds", "resources_reclaimed", "failure")
    } | {"steps": [asdict_step(step) for step in report["steps"]]}


def asdict_step(step: dict[str, Any]) -> dict[str, Any]:
    return {"step": step["step"], "status": step["status"], "seconds": step.get("seconds")}


__all__ = ["EnvironmentSource", "backup", "rehearse_restore", "summary", "verify_restored"]
