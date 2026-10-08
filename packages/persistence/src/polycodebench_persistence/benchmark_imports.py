"""Atomic persistence for immutable, artifact-backed benchmark imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from polycodebench_core.application_errors import (
    InvalidReference,
    InvalidState,
    PersistenceConflict,
)
from polycodebench_core.benchmark_audit_documents import (
    BenchmarkSnapshotDocument,
    BenchmarkSnapshotPayload,
    DocumentMetadata,
    EntityRef,
    audit_document_digest,
)
from polycodebench_core.benchmark_imports import (
    BenchmarkImportPlan,
    BenchmarkImportResult,
    ImportArtifactBindings,
    ImportItem,
)
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.benchmark_audit import PostgresBenchmarkAuditRepository
from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    artifact,
    audit_component,
    audit_document,
    benchmark_import_manifest,
    benchmark_item,
    benchmark_item_lineage,
    benchmark_registry,
    benchmark_snapshot,
)


@dataclass(frozen=True, slots=True)
class BenchmarkImportWrite:
    snapshot_id: UUID
    document_id: UUID
    membership_digest: str
    item_count: int
    created: bool


class PostgresBenchmarkImportRepository:
    """Commit a frozen import and all item/component references in one database transaction."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def persist(
        self,
        *,
        registry_id: UUID,
        plan: BenchmarkImportPlan,
        result: BenchmarkImportResult,
        artifacts: ImportArtifactBindings,
        actor: str,
    ) -> BenchmarkImportWrite:
        if plan.rights_state != "approved" or plan.rights_evidence_digest is None:
            raise InvalidState("external benchmark bytes require approved rights evidence")
        if (
            result.plan_digest != plan.plan_digest
            or result.source_digest != plan.source_digest
            or result.observed_source_digest != plan.source_digest
            or result.membership_digest != plan.membership_digest
            or tuple(item.item_id for item in result.items) != plan.selected_ids
        ):
            raise InvalidState("import result does not match its frozen plan and membership")
        if not actor or not actor.isascii() or len(actor) > 255:
            raise InvalidState("benchmark import actor is invalid")

        version = (
            f"{plan.revision}:{plan.variant}:{plan.parser_config_digest}:{plan.membership_digest}"
        )
        item_artifact_ids = dict(artifacts.item_artifact_ids)
        component_artifact_ids = {
            (item_id, key): artifact_id
            for item_id, key, artifact_id in artifacts.component_artifact_ids
        }
        expected_item_artifacts = {
            item.item_id for item in result.items if item.source_record is not None
        }
        expected_component_artifacts = {
            (item.item_id, component.component_key)
            for item in result.items
            for component in item.components
        }
        if set(item_artifact_ids) != expected_item_artifacts:
            raise InvalidState("raw item artifact bindings do not match retained source records")
        if set(component_artifact_ids) != expected_component_artifacts:
            raise InvalidState("component artifact bindings do not match parsed components")

        try:
            with self._engine.begin() as connection:
                registry = (
                    connection.execute(
                        select(benchmark_registry.c.slug, benchmark_registry.c.status)
                        .where(benchmark_registry.c.id == registry_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if registry is None or registry["slug"] != plan.benchmark_slug:
                    raise InvalidReference("benchmark registry slug does not match the import plan")
                if registry["status"] not in {"importable", "audit_conformant"}:
                    raise InvalidState(
                        "benchmark registry rights approval has not enabled importing"
                    )

                self._validate_import_artifacts(
                    connection,
                    plan,
                    result,
                    artifacts,
                    item_artifact_ids,
                    component_artifact_ids,
                )

                existing_snapshot = (
                    connection.execute(
                        select(benchmark_snapshot).where(
                            benchmark_snapshot.c.registry_id == registry_id,
                            benchmark_snapshot.c.version == version,
                            benchmark_snapshot.c.split == plan.split,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_snapshot is not None:
                    self._verify_existing(
                        connection,
                        snapshot=existing_snapshot,
                        registry_id=registry_id,
                        plan=plan,
                        result=result,
                        artifacts=artifacts,
                        version=version,
                        item_artifact_ids=item_artifact_ids,
                        component_artifact_ids=component_artifact_ids,
                    )
                    return BenchmarkImportWrite(
                        snapshot_id=existing_snapshot["id"],
                        document_id=existing_snapshot["document_id"],
                        membership_digest=plan.membership_digest,
                        item_count=100,
                        created=False,
                    )

                snapshot_id = uuid4()
                document = self._snapshot_document(
                    document_id=uuid4(),
                    registry_id=registry_id,
                    plan=plan,
                    result=result,
                    artifacts=artifacts,
                    actor=actor,
                    version=version,
                )
                document_digest = audit_document_digest(document)
                existing_document = (
                    connection.execute(
                        select(audit_document).where(
                            audit_document.c.kind == "benchmark_snapshot",
                            audit_document.c.semantic_digest == document_digest,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_document is not None:
                    if existing_document["payload"] != document.payload.model_dump(mode="json"):
                        raise PersistenceConflict(
                            "benchmark snapshot digest is bound to other bytes"
                        )
                    document_id = existing_document["id"]
                else:
                    PostgresBenchmarkAuditRepository._validate_references(
                        connection, document.payload
                    )
                    document_id = document.id
                    connection.execute(
                        insert(audit_document).values(
                            id=document.id,
                            kind=document.kind,
                            schema_version=document.schema_version,
                            semantic_digest=document_digest,
                            payload=document.payload.model_dump(mode="json"),
                            supersedes_id=document.supersedes_id,
                            created_by=document.metadata.actor,
                            document_created_at=document.metadata.created_at,
                            timestamp_precision=document.metadata.timestamp_precision,
                            trace_id=document.metadata.trace_id,
                            document_row_version=document.metadata.row_version,
                        )
                    )

                connection.execute(
                    insert(benchmark_snapshot).values(
                        id=snapshot_id,
                        registry_id=registry_id,
                        document_id=document_id,
                        version=version,
                        split=plan.split,
                        membership_digest=plan.membership_digest,
                        rights_state="approved",
                    )
                )
                connection.execute(
                    insert(benchmark_import_manifest).values(
                        id=uuid4(),
                        snapshot_id=snapshot_id,
                        source_uri=plan.source_uri,
                        source_member=plan.source_member,
                        revision=plan.revision,
                        variant=plan.variant,
                        importer_version=plan.importer_version,
                        parser_config=dict(plan.parser_config),
                        parser_config_digest=plan.parser_config_digest,
                        sample_seed=plan.sample_seed,
                        selected_membership=list(plan.selected_ids),
                        membership_digest=plan.membership_digest,
                        source_digest=plan.source_digest,
                        source_artifact_id=artifacts.source_artifact_id,
                        source_visibility=plan.source_visibility,
                        storage_visibility=plan.storage_visibility,
                        rights_state="approved",
                        rights_evidence_digest=plan.rights_evidence_digest,
                        rights_evidence_artifact_id=artifacts.rights_evidence_artifact_id,
                        result_state=result.state,
                        source_error_codes=list(result.source_error_codes),
                    )
                )
                self._insert_items(
                    connection,
                    snapshot_id=snapshot_id,
                    plan=plan,
                    result=result,
                    item_artifact_ids=item_artifact_ids,
                    component_artifact_ids=component_artifact_ids,
                )
                return BenchmarkImportWrite(
                    snapshot_id=snapshot_id,
                    document_id=document_id,
                    membership_digest=plan.membership_digest,
                    item_count=100,
                    created=True,
                )
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    @staticmethod
    def _validate_import_artifacts(
        connection: Any,
        plan: BenchmarkImportPlan,
        result: BenchmarkImportResult,
        artifacts: ImportArtifactBindings,
        item_artifact_ids: dict[str, UUID],
        component_artifact_ids: dict[tuple[str, str], UUID],
    ) -> None:
        expected: dict[UUID, tuple[str, str | None, str | None, bool]] = {}

        def add(
            artifact_id: UUID,
            digest: str,
            visibility: str | None,
            media_type: str | None = None,
            *,
            reject_public: bool = False,
        ) -> None:
            ref = (digest, visibility, media_type, reject_public)
            previous = expected.setdefault(artifact_id, ref)
            if previous != ref:
                raise InvalidState("one artifact ID is bound to conflicting import evidence")

        add(
            artifacts.source_artifact_id,
            plan.source_digest,
            plan.storage_visibility,
        )
        add(
            artifacts.rights_evidence_artifact_id,
            plan.rights_evidence_digest or "",
            None,
            reject_public=True,
        )
        for item in result.items:
            if item.source_record is not None:
                add(
                    item_artifact_ids[item.item_id],
                    item.source_record_digest or "",
                    plan.storage_visibility,
                )
            for component in item.components:
                add(
                    component_artifact_ids[(item.item_id, component.component_key)],
                    component.digest,
                    plan.storage_visibility,
                    component.media_type,
                )
            for link in item.family_links:
                add(
                    link.evidence_artifact_id,
                    link.evidence_digest,
                    None,
                )
        rows = (
            connection.execute(
                select(
                    artifact.c.id,
                    artifact.c.content_digest,
                    artifact.c.media_type,
                    artifact.c.status,
                    artifact.c.visibility,
                ).where(artifact.c.id.in_(tuple(expected)))
            )
            .mappings()
            .all()
        )
        actual = {row["id"]: row for row in rows}
        visibility_names = {"private": "hidden", "restricted": "internal"}
        for artifact_id, ref in expected.items():
            row = actual.get(artifact_id)
            digest, expected_visibility, media_type, reject_public = ref
            if (
                row is None
                or row["status"] != "verified"
                or row["content_digest"] != digest
                or (
                    expected_visibility is not None
                    and row["visibility"] != visibility_names[expected_visibility]
                )
                or (reject_public and row["visibility"] == "public")
                or (media_type is not None and row["media_type"] != media_type)
            ):
                raise InvalidReference(
                    "import artifact is missing, unverified, or has different bytes"
                )

    @staticmethod
    def _snapshot_document(
        *,
        document_id: UUID,
        registry_id: UUID,
        plan: BenchmarkImportPlan,
        result: BenchmarkImportResult,
        artifacts: ImportArtifactBindings,
        actor: str,
        version: str,
    ) -> BenchmarkSnapshotDocument:
        membership = tuple(
            EntityRef(
                entity_id=_stable_entity_id(f"item:{plan.plan_digest}:{item.item_id}"),
                entity_kind="benchmark_item",
                digest=item.source_record_digest,
            )
            for item in result.items
        )
        component_refs = [
            EntityRef(
                entity_id=_stable_entity_id(
                    f"component:{plan.plan_digest}:{item.item_id}:{component.component_key}"
                ),
                entity_kind=f"benchmark_component:{component.component_key}",
                digest=component.digest,
            )
            for item in result.items
            for component in item.components
        ]
        component_refs.extend(
            (
                EntityRef(
                    entity_id=artifacts.source_artifact_id,
                    entity_kind="benchmark_source_snapshot_artifact",
                    digest=plan.source_digest,
                ),
                EntityRef(
                    entity_id=artifacts.rights_evidence_artifact_id,
                    entity_kind="benchmark_rights_evidence_artifact",
                    digest=plan.rights_evidence_digest,
                ),
                EntityRef(
                    entity_id=_stable_entity_id(f"configuration:{plan.plan_digest}"),
                    entity_kind=f"benchmark_importer:{plan.importer_version}",
                    digest=plan.parser_config_digest,
                ),
            )
        )
        payload = BenchmarkSnapshotPayload(
            registry_ref=EntityRef(entity_id=registry_id, entity_kind="benchmark_registry"),
            version=version,
            split=plan.split,
            membership=membership,
            components=tuple(component_refs),
            upstream_rights=(),
            importer_refs=(),
        )
        created_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        metadata = DocumentMetadata(
            created_at=created_at,
            timestamp_precision="second",
            actor=actor,
            trace_id=None,
            row_version=0,
        )
        return BenchmarkSnapshotDocument(
            id=document_id,
            kind="benchmark_snapshot",
            schema_version=1,
            payload=payload,
            metadata=metadata,
        )

    def _insert_items(
        self,
        connection: Any,
        *,
        snapshot_id: UUID,
        plan: BenchmarkImportPlan,
        result: BenchmarkImportResult,
        item_artifact_ids: dict[str, UUID],
        component_artifact_ids: dict[tuple[str, str], UUID],
    ) -> None:
        item_ids: dict[str, UUID] = {}
        for item in result.items:
            item_id = _stable_entity_id(f"persisted-item:{snapshot_id}:{item.item_id}")
            item_ids[item.item_id] = item_id
            date_evidence = _date_evidence(item)
            connection.execute(
                insert(benchmark_item).values(
                    id=item_id,
                    snapshot_id=snapshot_id,
                    task_version_id=None,
                    item_key=item.item_id,
                    source_digest=item.source_record_digest,
                    original_artifact_id=item_artifact_ids.get(item.item_id),
                    membership_index=item.membership_index,
                    import_state=item.state,
                    error_codes=list(item.error_codes),
                    source_date_evidence=date_evidence,
                    source_urls=list(item.source_urls),
                    self_source_exposure=True,
                    source_public_exposure=item.source_public_exposure,
                    independent_duplicate_eligible=False,
                )
            )
            for component in item.components:
                connection.execute(
                    insert(audit_component).values(
                        id=_stable_entity_id(
                            f"persisted-component:{snapshot_id}:{item.item_id}:{component.component_key}"
                        ),
                        item_id=item_id,
                        component_key=component.component_key,
                        component_digest=component.digest,
                        content_artifact_id=component_artifact_ids[
                            (item.item_id, component.component_key)
                        ],
                        visibility=plan.storage_visibility,
                    )
                )
            for link in item.family_links:
                connection.execute(
                    insert(benchmark_item_lineage).values(
                        id=_stable_entity_id(
                            f"persisted-lineage:{snapshot_id}:{item.item_id}:"
                            f"{link.parent_benchmark_slug}:{link.parent_item_id}:{link.relation}"
                        ),
                        item_id=item_id,
                        parent_benchmark_slug=link.parent_benchmark_slug,
                        parent_item_key=link.parent_item_id,
                        relation=link.relation,
                        evidence_digest=link.evidence_digest,
                        evidence_artifact_id=link.evidence_artifact_id,
                    )
                )

    def _verify_existing(
        self,
        connection: Any,
        *,
        snapshot: Any,
        registry_id: UUID,
        plan: BenchmarkImportPlan,
        result: BenchmarkImportResult,
        artifacts: ImportArtifactBindings,
        version: str,
        item_artifact_ids: dict[str, UUID],
        component_artifact_ids: dict[tuple[str, str], UUID],
    ) -> None:
        if (
            snapshot["registry_id"] != registry_id
            or snapshot["version"] != version
            or snapshot["split"] != plan.split
            or snapshot["membership_digest"] != plan.membership_digest
            or snapshot["rights_state"] != "approved"
        ):
            raise PersistenceConflict("benchmark snapshot identity conflicts with the retry")
        manifest = (
            connection.execute(
                select(benchmark_import_manifest).where(
                    benchmark_import_manifest.c.snapshot_id == snapshot["id"]
                )
            )
            .mappings()
            .one_or_none()
        )
        expected_manifest = {
            "source_uri": plan.source_uri,
            "source_member": plan.source_member,
            "revision": plan.revision,
            "variant": plan.variant,
            "importer_version": plan.importer_version,
            "parser_config": dict(plan.parser_config),
            "parser_config_digest": plan.parser_config_digest,
            "sample_seed": plan.sample_seed,
            "selected_membership": list(plan.selected_ids),
            "membership_digest": plan.membership_digest,
            "source_digest": plan.source_digest,
            "source_artifact_id": artifacts.source_artifact_id,
            "source_visibility": plan.source_visibility,
            "storage_visibility": plan.storage_visibility,
            "rights_state": "approved",
            "rights_evidence_digest": plan.rights_evidence_digest,
            "rights_evidence_artifact_id": artifacts.rights_evidence_artifact_id,
            "result_state": result.state,
            "source_error_codes": list(result.source_error_codes),
        }
        if manifest is None or any(
            manifest[key] != value for key, value in expected_manifest.items()
        ):
            raise PersistenceConflict("benchmark import manifest differs from the prior retry")

        item_rows = (
            connection.execute(
                select(benchmark_item).where(benchmark_item.c.snapshot_id == snapshot["id"])
            )
            .mappings()
            .all()
        )
        if len(item_rows) != 100:
            raise PersistenceConflict("stored benchmark membership is incomplete")
        by_key = {str(row["item_key"]): row for row in item_rows}
        if set(by_key) != set(plan.selected_ids):
            raise PersistenceConflict("stored benchmark membership IDs differ from the retry")
        for item in result.items:
            row = by_key[item.item_id]
            if any(
                row[key] != value
                for key, value in {
                    "task_version_id": None,
                    "source_digest": item.source_record_digest,
                    "original_artifact_id": item_artifact_ids.get(item.item_id),
                    "membership_index": item.membership_index,
                    "import_state": item.state,
                    "error_codes": list(item.error_codes),
                    "source_date_evidence": _date_evidence(item),
                    "source_urls": list(item.source_urls),
                    "self_source_exposure": True,
                    "source_public_exposure": item.source_public_exposure,
                    "independent_duplicate_eligible": False,
                }.items()
            ):
                raise PersistenceConflict("stored benchmark item differs from the retry")
        item_db_ids = {row["id"] for row in item_rows}
        component_rows = (
            connection.execute(
                select(audit_component).where(audit_component.c.item_id.in_(item_db_ids))
            )
            .mappings()
            .all()
        )
        expected_components = {
            (by_key[item.item_id]["id"], component.component_key): (
                component.digest,
                component_artifact_ids[(item.item_id, component.component_key)],
                plan.storage_visibility,
            )
            for item in result.items
            for component in item.components
        }
        actual_components = {
            (row["item_id"], row["component_key"]): (
                row["component_digest"],
                row["content_artifact_id"],
                row["visibility"],
            )
            for row in component_rows
        }
        if actual_components != expected_components:
            raise PersistenceConflict("stored benchmark components differ from the retry")

        lineage_rows = (
            connection.execute(
                select(benchmark_item_lineage).where(
                    benchmark_item_lineage.c.item_id.in_(item_db_ids)
                )
            )
            .mappings()
            .all()
        )
        expected_lineage = {
            (
                by_key[item.item_id]["id"],
                link.parent_benchmark_slug,
                link.parent_item_id,
                link.relation,
                link.evidence_digest,
                link.evidence_artifact_id,
            )
            for item in result.items
            for link in item.family_links
        }
        actual_lineage = {
            (
                row["item_id"],
                row["parent_benchmark_slug"],
                row["parent_item_key"],
                row["relation"],
                row["evidence_digest"],
                row["evidence_artifact_id"],
            )
            for row in lineage_rows
        }
        if actual_lineage != expected_lineage:
            raise PersistenceConflict("stored benchmark family lineage differs from the retry")

        document = (
            connection.execute(
                select(
                    audit_document.c.semantic_digest,
                    audit_document.c.kind,
                    audit_document.c.payload,
                ).where(audit_document.c.id == snapshot["document_id"])
            )
            .mappings()
            .one_or_none()
        )
        if document is None or document["kind"] != "benchmark_snapshot":
            raise PersistenceConflict("stored snapshot document is missing or mistyped")
        expected_document = self._snapshot_document(
            document_id=snapshot["document_id"],
            registry_id=registry_id,
            plan=plan,
            result=result,
            artifacts=artifacts,
            actor="retry-ignored",
            version=version,
        )
        payload_digest = audit_document_digest(expected_document)
        expected_payload_value = expected_document.payload.model_dump(mode="json")
        if (
            document["semantic_digest"] != payload_digest
            or document["payload"] != expected_payload_value
        ):
            raise PersistenceConflict("stored snapshot document differs from the retry")


def _stable_entity_id(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"polycodebench:{value}")


def _date_evidence(item: ImportItem) -> dict[str, Any]:
    if item.source_date_value is not None:
        return {
            "state": "known",
            "value": item.source_date_value,
            "precision": item.source_date_precision,
            "raw": None,
        }
    return {"state": "unknown", "value": None, "precision": None, "raw": item.source_date_raw}
