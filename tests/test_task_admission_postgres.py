"""PostgreSQL admission registry checks (test evidence is not production admission)."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from polycodebench_core.canonical import canonical_digest, canonical_document_digest
from polycodebench_core.models import (
    AdmissionExecutionReport,
    AdmissionReport,
    TaskSet,
    TaskSetMember,
    TaskVersion,
)
from polycodebench_persistence.database import Database
from polycodebench_persistence.models import (
    artifact,
    audit_event,
    task_set,
    task_set_member,
    task_version,
)
from polycodebench_persistence.tasks import PostgresTaskRepository
from polycodebench_services.rbac import Principal, Role
from polycodebench_services.task_packages import ImportedTaskPackage, TaskPackageImporter
from polycodebench_services.tasks import TaskAdmissionService
from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Engine, make_url


def _database_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("task admission integration requires a disposable PostgreSQL test database")
    return value


@pytest.fixture(scope="module")
def database() -> Generator[Database, None, None]:
    instance = Database(_database_url())
    with instance.engine.connect() as connection:
        if connection.execute(text("SELECT to_regclass('public.task_set')")).scalar_one() is None:
            pytest.fail("PostgreSQL schema has not been migrated")
        if "document" not in task_set.c:
            pytest.fail("task-set manifest migration is not installed")
    yield instance
    instance.dispose()


def _task_document(
    *,
    task_id: str,
    report_digest: str,
    visible_id: UUID,
    hidden_id: UUID,
    imported: ImportedTaskPackage,
) -> TaskVersion:
    manifest = imported.manifest
    source = manifest.source
    admission = AdmissionReport(
        schema_version=1,
        kind="admission_report",
        profile_id="admission-v1-authored-fixture",
        report_digest=report_digest,
        execution_tier="local_fixture",
        reference_check="pass",
        faulty_check="pass",
        alternative_check="pass",
        flakiness_check="pass",
        rights_check="pass",
        disclosure_check="pass",
        reviewer_id=None,
        reviewed_at=None,
    )
    values = manifest.task.model_dump(mode="json", exclude={"methodology_label"})
    values["task_id"] = task_id
    task_document = {
        "schema_version": 1,
        "kind": "task_version",
        **values,
        "source": source.model_dump(mode="json"),
        "visible_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": str(visible_id),
            "digest": imported.visible_digest,
            "visibility": "internal",
        },
        "hidden_bundle": {
            "schema_version": 1,
            "kind": "task_bundle_ref",
            "artifact_id": str(hidden_id),
            "digest": imported.hidden_digest,
            "visibility": "hidden",
        },
        "output_contract_digest": manifest.task.output_contract_digest,
        "output_contract": manifest.output_contract.model_dump(mode="json"),
        "runtime": manifest.runtime.model_dump(mode="json"),
        "acceptance": manifest.acceptance.model_dump(mode="json"),
        "quality_plan": manifest.quality_plan.model_dump(mode="json"),
        "oracle": manifest.oracle.model_dump(mode="json"),
        "protocol_constraints": manifest.protocol_constraints.model_dump(mode="json"),
        "admission_report": admission.model_dump(mode="json"),
    }
    return TaskVersion.model_validate_json(json.dumps(task_document, ensure_ascii=False))


def _execution_report(imported: ImportedTaskPackage) -> AdmissionExecutionReport:
    # Reuse observed fixture outcomes as test data; database tests do not execute Docker.
    body = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json"
        ).read_text(encoding="utf-8")
    )
    body["package_digest"] = imported.package_digest
    body["report_digest"] = canonical_digest(
        {k: v for k, v in body.items() if k != "report_digest"}
    )
    return AdmissionExecutionReport.model_validate(body)


def _seed_artifact(engine: Engine, visibility: str, digest: str, domain: str) -> UUID:
    artifact_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(artifact).values(
                id=artifact_id,
                visibility=visibility,
                content_digest=digest,
                size_bytes=1,
                media_type="application/octet-stream",
                storage_key=f"test/{artifact_id}",
                encryption_domain=domain,
                status="provisional",
            )
        )
        connection.execute(
            update(artifact).where(artifact.c.id == artifact_id).values(status="verified")
        )
    return artifact_id


def test_task_registration_freeze_and_scored_split_denial(database: Database) -> None:
    imported = TaskPackageImporter().import_package(
        Path(__file__).resolve().parents[1] / "taskpacks" / "admission-smoke"
    )
    domain = f"p05-{uuid4().hex}"
    manifest_id = _seed_artifact(database.engine, "internal", imported.manifest_digest, domain)
    visible_id = _seed_artifact(database.engine, "internal", imported.visible_digest, domain)
    hidden_id = _seed_artifact(database.engine, "hidden", imported.hidden_digest, domain)
    task_id = f"p05-{uuid4().hex[:16]}"
    execution_report = _execution_report(imported)
    document = _task_document(
        task_id=task_id,
        report_digest=execution_report.report_digest,
        visible_id=visible_id,
        hidden_id=hidden_id,
        imported=imported,
    )
    principal = Principal(subject_id="local-prompt05-curator", roles=frozenset({Role.CURATOR}))
    repository = PostgresTaskRepository(database.engine)
    service = TaskAdmissionService(repository)
    version_id = service.freeze_task_version(
        principal=principal,
        document=document,
        execution_report=execution_report,
        manifest_digest=imported.manifest_digest,
        manifest_artifact_id=manifest_id,
        visible_artifact_id=visible_id,
        hidden_artifact_id=hidden_id,
        request_id=f"p05-{uuid4()}",
    )
    version_digest = canonical_document_digest(document)
    with database.engine.connect() as connection:
        version_row = (
            connection.execute(select(task_version).where(task_version.c.id == version_id))
            .mappings()
            .one()
        )
        assert version_row["frozen_at"] is not None
        assert version_row["digest"] == version_digest
        assert version_row["document"]["admission_report"]["execution_tier"] == "local_fixture"
        assert version_row["admission_evidence"]["report_digest"] == execution_report.report_digest

    task_set_doc = TaskSet(
        schema_version=1,
        kind="task_set",
        task_set_id=f"p05-fixture-{uuid4().hex[:12]}",
        version=1,
        split="fixture",
        split_seed="4096",
        scoring_policy_digest="sha256:" + "b" * 64,
        method_deviation_ids=["fixture-independent"],
        members=[
            TaskSetMember(
                schema_version=1,
                kind="task_set_member",
                task_digest=version_digest,
                cluster_id=document.cluster_id,
                stratum_id=document.stratum_id,
                sampling_weight_bp=10_000,
                earliest_public_at=document.source.first_public_at,
                exposure_confidence=document.source.date_confidence,
            )
        ],
        model_cutoffs=[],
    )
    from polycodebench_core.canonical import canonical_document_digest as digest_document

    taskset_digest = digest_document(task_set_doc)
    taskset_manifest_id = _seed_artifact(database.engine, "internal", taskset_digest, domain)
    taskset_id = service.create_task_set(
        principal=principal,
        document=task_set_doc,
        manifest_artifact_id=taskset_manifest_id,
        request_id=f"p05-{uuid4()}",
    )
    service.freeze_task_set(
        principal=principal,
        task_set_id=taskset_id,
        expected_digest=taskset_digest,
        request_id=f"p05-{uuid4()}",
    )
    with database.engine.connect() as connection:
        frozen_row = connection.execute(
            select(task_set.c.status, task_set.c.document).where(task_set.c.id == taskset_id)
        ).one()
        assert frozen_row.status == "frozen"
        assert frozen_row.document["split"] == "fixture"
        assert (
            connection.execute(
                select(task_set_member.c.task_version_id).where(
                    task_set_member.c.task_set_id == taskset_id
                )
            ).scalar_one()
            == version_id
        )
        assert connection.execute(
            select(audit_event.c.action).where(
                audit_event.c.resource_id == f"{task_set_doc.task_set_id}:1"
            )
        ).scalars().all() == ["taskset.create", "taskset.freeze"]

    scored = task_set_doc.model_copy(
        update={"task_set_id": f"scored-{uuid4().hex[:12]}", "split": "public_scored"}
    )
    with pytest.raises(ValueError, match="local fixture evidence"):
        service.create_task_set(
            principal=principal,
            document=scored,
            manifest_artifact_id=taskset_manifest_id,
            request_id=f"p05-{uuid4()}",
        )


def _registration(database: Database) -> tuple[PostgresTaskRepository, dict[str, object]]:
    imported = TaskPackageImporter().import_package(
        Path(__file__).resolve().parents[1] / "taskpacks/admission-smoke"
    )
    domain = f"p05-regression-{uuid4().hex}"
    manifest_id = _seed_artifact(database.engine, "internal", imported.manifest_digest, domain)
    visible_id = _seed_artifact(database.engine, "internal", imported.visible_digest, domain)
    hidden_id = _seed_artifact(database.engine, "hidden", imported.hidden_digest, domain)
    report = _execution_report(imported)
    document = _task_document(
        task_id=f"p05-{uuid4().hex}",
        report_digest=report.report_digest,
        visible_id=visible_id,
        hidden_id=hidden_id,
        imported=imported,
    )
    return PostgresTaskRepository(database.engine), {
        "actor_subject": "local-prompt05-curator",
        "document": document,
        "execution_report": report,
        "manifest_digest": imported.manifest_digest,
        "manifest_artifact_id": manifest_id,
        "visible_artifact_id": visible_id,
        "hidden_artifact_id": hidden_id,
        "request_id": str(uuid4()),
    }


@pytest.mark.parametrize("mutation", ["bundle_id", "snapshot", "production_tier"])
def test_task_registration_rejects_unbound_or_untrusted_admission(
    database: Database, mutation: str
) -> None:
    repository, arguments = _registration(database)
    document = arguments["document"].model_dump(mode="json")
    report = arguments["execution_report"].model_dump(mode="json")
    if mutation == "bundle_id":
        document["visible_bundle"]["artifact_id"] = str(uuid4())
    elif mutation == "snapshot":
        report["package_digest"] = "sha256:" + "f" * 64
    else:
        document["admission_report"]["execution_tier"] = "production_worker"
        document["admission_report"]["profile_id"] = "claimed-production"
        report["execution_tier"] = "production_worker"
        report["profile_id"] = "claimed-production"
    report["report_digest"] = canonical_digest(
        {k: v for k, v in report.items() if k != "report_digest"}
    )
    document["admission_report"]["report_digest"] = report["report_digest"]
    arguments["document"] = TaskVersion.model_validate_json(json.dumps(document))
    arguments["execution_report"] = AdmissionExecutionReport.model_validate(report)
    with pytest.raises(ValueError):
        repository.register_task_version(**arguments)
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                select(task_version.c.id).where(
                    task_version.c.document["task_id"].astext == document["task_id"]
                )
            ).first()
            is None
        )


@pytest.mark.parametrize("mutation", ["split", "document", "manifest"])
def test_taskset_freeze_rejects_altered_drafts(database: Database, mutation: str) -> None:
    repository, arguments = _registration(database)
    repository.register_task_version(**arguments)
    document = arguments["document"]
    draft = TaskSet(
        schema_version=1,
        kind="task_set",
        task_set_id=f"p05-draft-{uuid4().hex}",
        version=1,
        split="fixture",
        split_seed="4096",
        scoring_policy_digest="sha256:" + "b" * 64,
        method_deviation_ids=[],
        model_cutoffs=[],
        members=[
            TaskSetMember(
                schema_version=1,
                kind="task_set_member",
                task_digest=canonical_document_digest(document),
                cluster_id=document.cluster_id,
                stratum_id=document.stratum_id,
                sampling_weight_bp=10000,
                earliest_public_at=document.source.first_public_at,
                exposure_confidence=document.source.date_confidence,
            )
        ],
    )
    digest = canonical_document_digest(draft)
    manifest_id = _seed_artifact(database.engine, "internal", digest, f"p05-draft-{uuid4().hex}")
    set_id = repository.create_task_set_draft(
        actor_subject="local-prompt05-curator",
        document=draft,
        manifest_artifact_id=manifest_id,
        request_id=str(uuid4()),
    )
    changes = {"row_version": task_set.c.row_version + 1}
    if mutation == "split":
        changes["split"] = "public_scored"
    elif mutation == "document":
        body = draft.model_dump(mode="json")
        body["scoring_policy_digest"] = "sha256:" + "c" * 64
        changes["document"] = body
    else:
        changes["manifest_artifact_id"] = arguments["hidden_artifact_id"]
    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE pcb_curator"))
        connection.execute(update(task_set).where(task_set.c.id == set_id).values(**changes))
    with pytest.raises(ValueError):
        repository.freeze_task_set(
            actor_subject="local-prompt05-curator",
            task_set_id=set_id,
            expected_digest=digest,
            request_id=str(uuid4()),
        )
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                select(task_set.c.status).where(task_set.c.id == set_id)
            ).scalar_one()
            == "draft"
        )
        assert (
            connection.execute(
                select(task_set_member).where(task_set_member.c.task_set_id == set_id)
            ).first()
            is None
        )
