"""Explicit evaluation scheduling is idempotent and commits its job atomically."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from polycodebench_core.canonical import canonical_document_bytes, canonical_document_digest
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.models import (
    attempt,
    audit_event,
    candidate,
    config_document,
    evaluation,
    run,
    stage_job,
    task,
    task_version,
)
from polycodebench_scoring.loader import load_scoring_policy
from polycodebench_services.task_packages import TaskPackageImporter
from sqlalchemy import create_engine, insert, select
from test_jobs_postgres import database as database
from test_persistence_postgres import _seed
from test_task_admission_postgres import _seed_artifact, _task_document

ROOT = Path(__file__).resolve().parents[1]
TASK_PACKAGE = ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"


def test_selected_completed_attempt_queues_one_frozen_evaluation(database) -> None:
    ids = _seed(database.engine, samples_per_task=1)
    imported = TaskPackageImporter().import_package(TASK_PACKAGE)
    domain = f"evaluation-schedule-{uuid4().hex}"
    manifest_id = _seed_artifact(
        database.engine, "internal", imported.manifest_digest, f"{domain}-manifest"
    )
    visible_id = _seed_artifact(
        database.engine, "internal", imported.visible_digest, f"{domain}-visible"
    )
    hidden_id = _seed_artifact(
        database.engine, "hidden", imported.hidden_digest, f"{domain}-hidden"
    )
    task_id = f"evaluation-schedule-{uuid4().hex[:16]}"
    task_doc = _task_document(
        task_id=task_id,
        report_digest="sha256:" + "a" * 64,
        visible_id=visible_id,
        hidden_id=hidden_id,
        imported=imported,
    )
    task_version_id = uuid4()
    task_digest = canonical_document_digest(task_doc)
    with database.engine.begin() as connection:
        connection.execute(
            insert(task).values(
                id=uuid4(),
                slug=task_id,
                family=task_doc.family,
                source_identity="evaluation-scheduler-test",
                primary_language=task_doc.primary_language,
            )
        )
        task_row_id = connection.execute(
            select(task.c.id).where(task.c.slug == task_id)
        ).scalar_one()
        connection.execute(
            insert(task_version).values(
                id=task_version_id,
                task_id=task_row_id,
                version=task_doc.version,
                digest=task_digest,
                manifest_artifact_id=manifest_id,
                visible_artifact_id=visible_id,
                hidden_artifact_id=hidden_id,
                language=task_doc.primary_language,
                family=task_doc.family,
                cluster_id=task_doc.cluster_id,
                stratum_id=task_doc.stratum_id,
                schema_version=1,
                document=task_doc.model_dump(mode="json"),
            )
        )

    policy = load_scoring_policy(ROOT / "config" / "scoring" / "pilot-v1.yaml")
    policy_body = canonical_document_bytes(policy)
    policy_digest = canonical_document_digest(policy)
    with database.engine.begin() as connection:
        policy_id = connection.execute(
            select(config_document.c.id).where(
                config_document.c.kind == "frozen_scoring_policy",
                config_document.c.digest == policy_digest,
            )
        ).scalar_one_or_none()
        if policy_id is None:
            policy_artifact = _seed_artifact(
                database.engine,
                "internal",
                "sha256:" + hashlib.sha256(policy_body).hexdigest(),
                f"{domain}-policy",
            )
            policy_id = uuid4()
            connection.execute(
                insert(config_document).values(
                    id=policy_id,
                    kind="frozen_scoring_policy",
                    version_label="pilot-v1-test",
                    digest=policy_digest,
                    canonical_artifact_id=policy_artifact,
                    schema_version=1,
                    document=policy.model_dump(mode="json"),
                )
            )

    candidate_body = b"solution.py\x00def top_words(text, limit): return []\n"
    candidate_digest = "sha256:" + hashlib.sha256(candidate_body).hexdigest()
    candidate_artifact = _seed_artifact(
        database.engine, "internal", candidate_digest, f"{domain}-candidate"
    )
    attempt_id = uuid4()
    run_id = uuid4()
    candidate_id = uuid4()
    with database.engine.begin() as connection:
        connection.execute(
            insert(run).values(
                id=run_id,
                campaign_id=ids["campaign"],
                config_document_id=ids["run_config"],
                task_set_id=ids["task_set"],
                model_revision_id=ids["model"],
                status="completed",
                created_by="evaluation-scheduler-test",
                row_version=0,
            )
        )
        connection.execute(
            insert(attempt).values(
                id=attempt_id,
                run_id=run_id,
                task_version_id=task_version_id,
                sample_index=0,
                seed=0,
                state="completed",
                candidate_artifact_id=candidate_artifact,
                row_version=0,
            )
        )
        connection.execute(
            insert(candidate).values(
                id=candidate_id,
                attempt_id=attempt_id,
                revision=1,
                payload_digest=candidate_digest,
                submission_kind="files",
                payload={"validity": "valid"},
                canonical_artifact_id=candidate_artifact,
            )
        )

    worker_engine = create_engine(
        database.engine.url,
        connect_args={"options": "-c role=pcb_solve_worker"},
        hide_parameters=True,
    )
    try:
        repository = PostgresJobRepository(worker_engine)
        scheduled = repository.schedule_evaluation(
            attempt_id=attempt_id,
            policy_config_id=policy_id,
            resource_class="local-grading-small",
            actor="local-evaluation-scheduler-test",
        )
        replayed = repository.schedule_evaluation(
            attempt_id=attempt_id,
            policy_config_id=policy_id,
            resource_class="local-grading-small",
            actor="local-evaluation-scheduler-test",
        )
    finally:
        worker_engine.dispose()

    assert scheduled.created is True
    assert replayed.created is False
    assert replayed.evaluation_id == scheduled.evaluation_id
    assert replayed.job_id == scheduled.job_id
    with database.engine.connect() as connection:
        evaluation_row = connection.execute(
            select(evaluation).where(evaluation.c.id == scheduled.evaluation_id)
        ).mappings().one()
        job_row = connection.execute(
            select(stage_job).where(stage_job.c.id == scheduled.job_id)
        ).mappings().one()
        audit_row = connection.execute(
            select(audit_event).where(
                audit_event.c.resource_type == "evaluation",
                audit_event.c.resource_id == str(scheduled.evaluation_id),
            )
        ).mappings().one()
    assert evaluation_row["state"] == "queued"
    assert evaluation_row["oracle_digest"] == canonical_document_digest(task_doc.oracle)
    assert job_row["state"] == "queued"
    assert job_row["stage"] == "evaluate"
    assert job_row["queue_class"] == "grading"
    assert job_row["resource_class"] == "local-grading-small"
    assert job_row["input_digest"] == scheduled.input_digest
    assert audit_row["action"] == "evaluation.schedule"
