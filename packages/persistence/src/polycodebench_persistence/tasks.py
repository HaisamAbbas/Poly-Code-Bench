"""Transactional registration and freezing for validated task versions and sets."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.models import AdmissionExecutionReport, TaskSet, TaskVersion
from polycodebench_core.tasksets import package_snapshot_digest, validate_cluster_split_assignments
from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    artifact,
    audit_event,
    task,
    task_set,
    task_set_member,
    task_version,
)


class PostgresTaskRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register_task_version(
        self,
        *,
        actor_subject: str,
        document: TaskVersion,
        execution_report: AdmissionExecutionReport,
        manifest_digest: str,
        manifest_artifact_id: UUID,
        visible_artifact_id: UUID,
        hidden_artifact_id: UUID,
        request_id: str,
    ) -> UUID:
        if not actor_subject:
            raise ValueError("task actor identity is required")
        self._validate_admission(document, execution_report, manifest_digest)
        report = document.admission_report
        if (
            str(visible_artifact_id) != document.visible_bundle.artifact_id
            or str(hidden_artifact_id) != document.hidden_bundle.artifact_id
        ):
            raise ValueError("task bundle artifact IDs must match the frozen task contract")
        task_version_id = uuid4()
        digest = canonical_document_digest(document)
        source_identity = document.source.immutable_revision
        try:
            with self._engine.begin() as connection:
                expected_artifacts = (
                    (manifest_artifact_id, "internal", manifest_digest),
                    (
                        visible_artifact_id,
                        document.visible_bundle.visibility.value,
                        document.visible_bundle.digest,
                    ),
                    (hidden_artifact_id, "hidden", document.hidden_bundle.digest),
                )
                for artifact_id, visibility, expected_digest in expected_artifacts:
                    row = connection.execute(
                        select(artifact.c.visibility, artifact.c.content_digest, artifact.c.status)
                        .where(artifact.c.id == artifact_id)
                        .with_for_update(read=True)
                    ).one_or_none()
                    if row is None or row.status != "verified" or row.visibility != visibility:
                        raise ValueError(
                            "task artifacts must exist, be verified, and use required visibility"
                        )
                    if expected_digest is not None and row.content_digest != expected_digest:
                        raise ValueError(
                            "task bundle artifact digest does not match the task contract"
                        )

                existing_task = (
                    connection.execute(
                        select(task).where(task.c.slug == document.task_id).with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_task is None:
                    task_id = uuid4()
                    connection.execute(
                        insert(task).values(
                            id=task_id,
                            slug=document.task_id,
                            family=document.family,
                            source_identity=source_identity,
                            primary_language=document.primary_language,
                            row_version=0,
                        )
                    )
                else:
                    task_id = UUID(str(existing_task["id"]))
                    if (
                        existing_task["family"] != document.family
                        or existing_task["source_identity"] != source_identity
                        or existing_task["primary_language"] != document.primary_language
                    ):
                        raise ValueError("task identity fields cannot change between versions")
                connection.execute(
                    insert(task_version).values(
                        id=task_version_id,
                        task_id=task_id,
                        version=document.version,
                        digest=digest,
                        manifest_artifact_id=manifest_artifact_id,
                        visible_artifact_id=visible_artifact_id,
                        hidden_artifact_id=hidden_artifact_id,
                        language=document.primary_language,
                        family=document.family,
                        cluster_id=document.cluster_id,
                        stratum_id=document.stratum_id,
                        first_public_at=_parse_timestamp(document.source.first_public_at),
                        frozen_at=datetime.now(UTC),
                        schema_version=document.schema_version,
                        document=document.model_dump(mode="json"),
                        admission_evidence=execution_report.model_dump(mode="json"),
                    )
                )
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=actor_subject,
                        action="task.version.freeze",
                        resource_type="task_version",
                        resource_id=f"{document.task_id}:{document.version}",
                        before_digest=None,
                        after_digest=digest,
                        request_id=request_id,
                        details={
                            "execution_tier": report.execution_tier,
                            "admission_report_digest": report.report_digest,
                            "artifact_ids": [
                                str(manifest_artifact_id),
                                str(visible_artifact_id),
                                str(hidden_artifact_id),
                            ],
                        },
                    )
                )
            return task_version_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def create_task_set_draft(
        self,
        *,
        actor_subject: str,
        document: TaskSet,
        manifest_artifact_id: UUID,
        request_id: str,
    ) -> UUID:
        if not actor_subject:
            raise ValueError("task actor identity is required")
        task_set_id = uuid4()
        digest = canonical_document_digest(document)
        try:
            with self._engine.begin() as connection:
                self._validate_members(connection, document, lock_clusters=False)
                manifest = connection.execute(
                    select(artifact.c.visibility, artifact.c.content_digest, artifact.c.status)
                    .where(artifact.c.id == manifest_artifact_id)
                    .with_for_update(read=True)
                ).one_or_none()
                if (
                    manifest is None
                    or manifest.visibility != "internal"
                    or manifest.status != "verified"
                    or manifest.content_digest != digest
                ):
                    raise ValueError(
                        "task-set manifest must be a verified matching internal artifact"
                    )
                connection.execute(
                    insert(task_set).values(
                        id=task_set_id,
                        name=document.task_set_id,
                        version=document.version,
                        digest=digest,
                        split=document.split,
                        status="draft",
                        manifest_artifact_id=manifest_artifact_id,
                        document=document.model_dump(mode="json"),
                        row_version=0,
                    )
                )
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=actor_subject,
                        action="taskset.create",
                        resource_type="task_set",
                        resource_id=f"{document.task_set_id}:{document.version}",
                        before_digest=None,
                        after_digest=digest,
                        request_id=request_id,
                        details={"split": document.split, "member_count": len(document.members)},
                    )
                )
            return task_set_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def freeze_task_set(
        self,
        *,
        actor_subject: str,
        task_set_id: UUID,
        expected_digest: str,
        request_id: str,
    ) -> None:
        if not actor_subject:
            raise ValueError("task actor identity is required")
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        select(task_set).where(task_set.c.id == task_set_id).with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ValueError("task set does not exist")
                document = TaskSet.model_validate(row["document"])
                if (
                    canonical_document_digest(document) != row["digest"]
                    or document.split != row["split"]
                    or document.task_set_id != row["name"]
                    or document.version != row["version"]
                ):
                    raise ValueError("task-set draft differs from its digest or frozen identity")
                manifest = connection.execute(
                    select(artifact.c.visibility, artifact.c.content_digest, artifact.c.status)
                    .where(artifact.c.id == row["manifest_artifact_id"])
                    .with_for_update(read=True)
                ).one_or_none()
                if (
                    manifest is None
                    or manifest.status != "verified"
                    or manifest.visibility != "internal"
                    or manifest.content_digest != row["digest"]
                ):
                    raise ValueError(
                        "task-set manifest must be a verified matching internal artifact"
                    )
                if row["status"] == "frozen":
                    if row["digest"] == expected_digest:
                        return
                    raise ValueError("frozen task set cannot be changed")
                if row["status"] != "draft" or row["digest"] != expected_digest:
                    raise ValueError("task set status or expected digest does not match")
                self._validate_members(connection, document, lock_clusters=True)
                now = datetime.now(UTC)
                for member in document.members:
                    task_row = connection.execute(
                        select(
                            task_version.c.id, task_version.c.stratum_id, task_version.c.cluster_id
                        ).where(task_version.c.digest == member.task_digest)
                    ).one()
                    connection.execute(
                        insert(task_set_member).values(
                            task_set_id=task_set_id,
                            task_version_id=task_row.id,
                            stratum_id=member.stratum_id,
                            sampling_weight_bp=member.sampling_weight_bp,
                        )
                    )
                changed = connection.execute(
                    update(task_set)
                    .where(
                        task_set.c.id == task_set_id,
                        task_set.c.status == "draft",
                        task_set.c.row_version == row["row_version"],
                    )
                    .values(status="frozen", frozen_at=now, row_version=row["row_version"] + 1)
                ).rowcount
                if changed != 1:
                    raise ValueError("task set changed during freeze")
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=actor_subject,
                        action="taskset.freeze",
                        resource_type="task_set",
                        resource_id=f"{row['name']}:{row['version']}",
                        before_digest=expected_digest,
                        after_digest=expected_digest,
                        request_id=request_id,
                        details={"split": row["split"], "member_count": len(document.members)},
                    )
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    @staticmethod
    def _validate_admission(
        document: TaskVersion, execution_report: AdmissionExecutionReport, manifest_digest: str
    ) -> None:
        report = document.admission_report
        if report.execution_tier != "local_fixture":
            raise ValueError("production admission requires a trusted worker evidence authority")
        if (
            report.profile_id != "admission-v1-authored-fixture"
            or document.source.source_kind != "authored_fixture"
        ):
            raise ValueError("only authored fixture admission is supported")
        observed = AdmissionExecutionReport.model_validate_json(execution_report.model_dump_json())
        if (
            not observed.passed
            or observed.execution_tier != report.execution_tier
            or observed.profile_id != report.profile_id
            or observed.report_digest != report.report_digest
            or observed.runtime_image_digest != document.runtime.image_digest
            or observed.package_digest
            != package_snapshot_digest(
                manifest_digest, document.visible_bundle.digest, document.hidden_bundle.digest
            )
        ):
            raise ValueError("admission evidence does not match the frozen task snapshot")
        for name in ("reference", "faulty", "alternative", "flakiness", "rights", "disclosure"):
            if getattr(observed.checks, name) != getattr(report, f"{name}_check"):
                raise ValueError("admission summary contradicts its observed evidence")

    @staticmethod
    def _validate_members(
        connection: Connection, document: TaskSet, *, lock_clusters: bool
    ) -> None:
        clusters = sorted({member.cluster_id for member in document.members})
        if lock_clusters:
            for cluster_id in clusters:
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:cluster_id, 0))"),
                    {"cluster_id": cluster_id},
                )
        for member in document.members:
            row = (
                connection.execute(
                    select(task_version).where(task_version.c.digest == member.task_digest)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError("task set references an unregistered task version")
            if row["cluster_id"] != member.cluster_id or row["stratum_id"] != member.stratum_id:
                raise ValueError(
                    "task set cluster/stratum membership differs from frozen task data"
                )
            version = TaskVersion.model_validate_json(json.dumps(row["document"]))
            if canonical_document_digest(version) != member.task_digest:
                raise ValueError("task version document does not match its frozen digest")
            if (
                str(row["visible_artifact_id"]) != version.visible_bundle.artifact_id
                or str(row["hidden_artifact_id"]) != version.hidden_bundle.artifact_id
            ):
                raise ValueError("task bundle artifact IDs must match the frozen task contract")
            report = version.admission_report
            if report.execution_tier != "local_fixture":
                raise ValueError(
                    "production admission requires a trusted worker evidence authority"
                )
            if report.execution_tier == "local_fixture" and document.split != "fixture":
                raise ValueError(
                    "local fixture evidence cannot enter a scored or held-out task set"
                )
            manifest = connection.execute(
                select(artifact.c.content_digest, artifact.c.status, artifact.c.visibility).where(
                    artifact.c.id == row["manifest_artifact_id"]
                )
            ).one_or_none()
            if (
                manifest is None
                or manifest.status != "verified"
                or manifest.visibility != "internal"
            ):
                raise ValueError("task manifest must be a verified internal artifact")
            observed = AdmissionExecutionReport.model_validate_json(
                json.dumps(row["admission_evidence"])
            )
            PostgresTaskRepository._validate_admission(
                version, observed, str(manifest.content_digest)
            )
            if (
                version.source.first_public_at != member.earliest_public_at
                or version.source.date_confidence != member.exposure_confidence
            ):
                raise ValueError("task-set exposure metadata must match the frozen source record")

        if not clusters:
            raise ValueError("task set has no cluster membership")
        existing_assignments = connection.execute(
            select(task_version.c.cluster_id, task_set.c.split)
            .select_from(
                task_set_member.join(task_set, task_set_member.c.task_set_id == task_set.c.id).join(
                    task_version, task_version.c.id == task_set_member.c.task_version_id
                )
            )
            .where(
                task_set.c.status == "frozen",
                task_version.c.cluster_id.in_(clusters),
            )
        ).all()
        assignments = [(str(row.cluster_id), str(row.split)) for row in existing_assignments]
        assignments.extend((cluster_id, document.split) for cluster_id in clusters)
        validate_cluster_split_assignments(assignments)


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
