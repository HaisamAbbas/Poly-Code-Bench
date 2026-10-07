"""Scorecard persistence and idempotent replay against a disposable PostgreSQL database."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from polycodebench_core.application_errors import PersistenceConflict
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.models import (
    EvaluationState,
    Gate,
    Scorecard,
    ScoreDimension,
    ScoreItem,
)
from polycodebench_persistence.database import Database
from polycodebench_persistence.models import (
    attempt,
    candidate,
    config_document,
    evaluation,
    run,
    task,
    task_version,
)
from polycodebench_persistence.scoring import (
    PostgresScoringRepository,
    ScorecardRecord,
    ScoreItemRecord,
)
from polycodebench_services.task_packages import TaskPackageImporter
from sqlalchemy import insert, make_url, select
from sqlalchemy.engine import Engine
from test_persistence_postgres import _seed
from test_task_admission_postgres import _seed_artifact, _task_document

ROOT = Path(__file__).resolve().parents[1]
TASK_PACKAGE = ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"


def _test_database_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("PCB_TEST_DATABASE_URL must use a disposable database containing 'test'")
    return value


@pytest.fixture(scope="module")
def database() -> Database:
    instance = Database(_test_database_url())
    yield instance
    instance.dispose()


def _frozen_evaluation(engine: Engine) -> dict[str, object]:
    ids = _seed(engine, samples_per_task=1)
    imported = TaskPackageImporter().import_package(TASK_PACKAGE)
    domain = f"scoring-persist-{uuid4().hex}"
    manifest_id = _seed_artifact(engine, "internal", imported.manifest_digest, f"{domain}-manifest")
    visible_id = _seed_artifact(engine, "internal", imported.visible_digest, f"{domain}-visible")
    hidden_id = _seed_artifact(engine, "hidden", imported.hidden_digest, f"{domain}-hidden")
    task_id = f"scoring-persist-{uuid4().hex[:16]}"
    task_document = _task_document(
        task_id=task_id,
        report_digest="sha256:" + "a" * 64,
        visible_id=visible_id,
        hidden_id=hidden_id,
        imported=imported,
    )
    task_digest = canonical_document_digest(task_document)
    task_row_id = uuid4()
    task_version_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(task).values(
                id=task_row_id,
                slug=task_id,
                family=task_document.family,
                source_identity="scorecard-postgres-test",
                primary_language=task_document.primary_language,
            )
        )
        connection.execute(
            insert(task_version).values(
                id=task_version_id,
                task_id=task_row_id,
                version=task_document.version,
                digest=task_digest,
                manifest_artifact_id=manifest_id,
                visible_artifact_id=visible_id,
                hidden_artifact_id=hidden_id,
                language=task_document.primary_language,
                family=task_document.family,
                cluster_id=task_document.cluster_id,
                stratum_id=task_document.stratum_id,
                schema_version=1,
                document=task_document.model_dump(mode="json"),
            )
        )

    policy_digest = "sha256:" + hashlib.sha256(f"{domain}-policy".encode()).hexdigest()
    policy_artifact_id = _seed_artifact(engine, "internal", policy_digest, "worker-config")
    policy_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(config_document).values(
                id=policy_id,
                kind="frozen_scoring_policy",
                version_label="scoring-postgres-test-v1",
                digest=policy_digest,
                canonical_artifact_id=policy_artifact_id,
                schema_version=1,
                document={},
            )
        )

    candidate_body_digest = "sha256:" + "d" * 64
    candidate_artifact_id = _seed_artifact(
        engine, "internal", candidate_body_digest, f"{domain}-candidate"
    )
    evidence_digest = "sha256:" + hashlib.sha256(f"{domain}-evidence".encode()).hexdigest()
    evidence_artifact_id = _seed_artifact(
        engine, "internal", evidence_digest, "evaluation-evidence"
    )
    outcome_digest = "sha256:" + hashlib.sha256(f"{domain}-outcome".encode()).hexdigest()
    outcome_artifact_id = _seed_artifact(engine, "internal", outcome_digest, "scoring-outcomes")
    attempt_id = uuid4()
    run_id = uuid4()
    candidate_id = uuid4()
    evaluation_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(run).values(
                id=run_id,
                campaign_id=ids["campaign"],
                config_document_id=ids["run_config"],
                task_set_id=ids["task_set"],
                model_revision_id=ids["model"],
                status="completed",
                created_by="scorecard-postgres-test",
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
                candidate_artifact_id=candidate_artifact_id,
                row_version=0,
            )
        )
        connection.execute(
            insert(candidate).values(
                id=candidate_id,
                attempt_id=attempt_id,
                revision=1,
                payload_digest=candidate_body_digest,
                submission_kind="files",
                payload={"validity": "valid"},
                canonical_artifact_id=candidate_artifact_id,
            )
        )
        connection.execute(
            insert(evaluation).values(
                id=evaluation_id,
                attempt_id=attempt_id,
                policy_config_id=policy_id,
                oracle_digest="sha256:" + "1" * 64,
                state="ready",
                gate="fail",
                evidence_manifest_id=evidence_artifact_id,
                row_version=0,
            )
        )
        assert (
            connection.execute(
                select(task_version.c.id).where(task_version.c.id == task_version_id)
            ).scalar_one()
            == task_version_id
        )

    return {
        "evaluation_id": evaluation_id,
        "task_id": task_document.task_id,
        "task_version": task_document.version,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "policy_digest": policy_digest,
        "evidence_artifact_id": evidence_artifact_id,
        "evidence_digest": evidence_digest,
        "outcome_artifact_id": outcome_artifact_id,
        "outcome_digest": outcome_digest,
    }


def _record(values: dict[str, object]) -> ScorecardRecord:
    scorecard_id = uuid4()
    scorer_digest = "sha256:" + "2" * 64
    evidence_manifest_digest = "sha256:" + "3" * 64
    item = ScoreItem(
        kind="score_item",
        schema_version=1,
        dimension=ScoreDimension.CORRECTNESS,
        item_id="gate",
        applicable=True,
        primary_owner=ScoreDimension.CORRECTNESS,
        raw_value="0.000000",
        effective_weight_bps=10_000,
        gating_reason="correctness-gate-failed",
        contribution="0.000000",
        evidence_ids=[],
    )
    card = Scorecard(
        kind="scorecard",
        schema_version=1,
        scorecard_id=str(scorecard_id),
        task_id=str(values["task_id"]),
        task_version=int(values["task_version"]),
        run_id=str(values["run_id"]),
        candidate_id=str(values["candidate_id"]),
        evidence_manifest_digest=evidence_manifest_digest,
        scoring_policy_digest=str(values["policy_digest"]),
        scorer_digest=scorer_digest,
        gate=Gate.FAIL,
        status=EvaluationState.READY,
        total_score="0.000000",
        items=[item],
        created_at="2026-10-07T08:30:00Z",
    )
    return ScorecardRecord(
        evaluation_id=UUID(str(values["evaluation_id"])),
        scorecard=card,
        evidence_artifact_id=UUID(str(values["evidence_artifact_id"])),
        evidence_artifact_digest=str(values["evidence_digest"]),
        artifact_id=UUID(str(values["outcome_artifact_id"])),
        artifact_digest=str(values["outcome_digest"]),
        items=(
            ScoreItemRecord(
                dimension=ScoreDimension.CORRECTNESS,
                item_id="gate",
                status="gated",
                applicable=True,
                raw_value="0.000000",
                effective_weight_bps=10_000,
                contribution="0.000000",
                reason="correctness gate failed",
                evidence_refs=(),
            ),
        ),
    )


def test_scorecard_write_replay_and_explanation_conflict(database: Database) -> None:
    values = _frozen_evaluation(database.engine)
    repository = PostgresScoringRepository(database.engine)
    record = _record(values)

    first = repository.store(record, actor="scoring-postgres-test")
    replay = repository.store(record, actor="scoring-postgres-test")

    assert first.created is True
    assert replay.created is False
    assert replay.scorecard_id == first.scorecard_id

    modified = record.model_copy(
        update={"items": (record.items[0].model_copy(update={"reason": "changed explanation"}),)}
    )
    with pytest.raises(PersistenceConflict, match="replay conflicts"):
        repository.store(modified, actor="scoring-postgres-test")
