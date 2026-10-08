"""Immutable scorecard persistence behind the dedicated scorer database role."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidReference,
    InvalidState,
    PersistenceConflict,
)
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.models import Scorecard, ScoreDimension, TaskVersion
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    artifact,
    attempt,
    audit_event,
    config_document,
    evaluation,
    run,
    score_item,
    scorecard,
    task_version,
)
from polycodebench_persistence.models import (
    candidate as candidate_table,
)

ItemStatus = Literal["measured", "not_applicable", "gated", "missing", "needs_review"]


class ScoreItemRecord(BaseModel):
    """Database-shaped explanation fields that accompany one canonical score item."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dimension: ScoreDimension
    item_id: str = Field(min_length=1, max_length=160)
    status: ItemStatus
    applicable: bool
    raw_value: str | None
    effective_weight_bps: int = Field(ge=0, le=10_000)
    contribution: str
    reason: str | None
    evidence_refs: tuple[dict[str, object], ...]


class ScorecardRecord(BaseModel):
    """A scorecard, its explanation rows, and the private archived outcome artifact."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    evaluation_id: UUID
    scorecard: Scorecard
    evidence_artifact_id: UUID
    evidence_artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_id: UUID
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    items: tuple[ScoreItemRecord, ...]

    @model_validator(mode="after")
    def item_rows_match_scorecard(self) -> ScorecardRecord:
        expected = {(item.dimension, item.item_id): item for item in self.scorecard.items}
        actual = {(item.dimension, item.item_id): item for item in self.items}
        if len(actual) != len(self.items) or set(actual) != set(expected):
            raise ValueError("persisted score item rows do not match the scorecard")
        for key, row in actual.items():
            source = expected[key]
            if (
                row.applicable != source.applicable
                or row.raw_value != source.raw_value
                or row.effective_weight_bps != source.effective_weight_bps
                or row.contribution != source.contribution
            ):
                raise ValueError("persisted score item values disagree with the scorecard")
        return self


@dataclass(frozen=True)
class ScorecardWriteResult:
    scorecard_id: UUID
    created: bool


class PostgresScoringRepository:
    """Persist internal outcomes only after validating their durable evaluation lineage."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def pending_evaluation_ids(self, *, limit: int = 100) -> tuple[UUID, ...]:
        """Return a bounded, oldest-first batch of completed evaluations without scorecards."""
        if not 1 <= limit <= 1_000:
            raise ValueError("pending scoring batch limit must be in [1,1000]")
        evidence = artifact.alias("pending_evaluation_evidence")
        policy_artifact = artifact.alias("pending_evaluation_policy_artifact")
        already_scored = select(scorecard.c.id).where(
            scorecard.c.evaluation_id == evaluation.c.id
        ).exists()
        statement = (
            select(evaluation.c.id)
            .select_from(
                evaluation.join(evidence, evidence.c.id == evaluation.c.evidence_manifest_id)
                .join(config_document, config_document.c.id == evaluation.c.policy_config_id)
                .join(
                    policy_artifact,
                    policy_artifact.c.id == config_document.c.canonical_artifact_id,
                )
            )
            .where(
                evaluation.c.state.in_(("ready", "failed")),
                evidence.c.status == "verified",
                evidence.c.visibility == "internal",
                evidence.c.encryption_domain == "evaluation-evidence",
                config_document.c.kind == "frozen_scoring_policy",
                policy_artifact.c.status == "verified",
                policy_artifact.c.visibility == "internal",
                policy_artifact.c.encryption_domain == "worker-config",
                policy_artifact.c.content_digest == config_document.c.digest,
                ~already_scored,
            )
            .order_by(evaluation.c.created_at, evaluation.c.id)
            .limit(limit)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).scalars().all()
        return tuple(UUID(str(value)) for value in rows)

    def has_scorecard(self, evaluation_id: UUID) -> bool:
        with self._engine.connect() as connection:
            existing_id = connection.execute(
                select(scorecard.c.id)
                .where(scorecard.c.evaluation_id == evaluation_id)
                .limit(1)
            ).scalar_one_or_none()
        return existing_id is not None

    @contextmanager
    def evaluation_lock(self, evaluation_id: UUID) -> Iterator[None]:
        """Serialize scoring attempts for one evaluation across scorer processes."""
        lock_key = f"pcb.scoring:{evaluation_id}"
        with self._engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            ).scalar_one()
            connection.commit()
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": lock_key},
                ).scalar_one()
                connection.commit()

    def store(self, record: ScorecardRecord, *, actor: str) -> ScorecardWriteResult:
        if not actor or not actor.isascii() or len(actor) > 255:
            raise InvalidState("scoring actor identity is invalid")
        card = record.scorecard
        evidence_artifact = artifact.alias("evaluation_evidence_artifact")
        outcome_artifact = artifact.alias("scoring_outcome_artifact")
        policy_artifact = artifact.alias("frozen_scoring_policy_artifact")
        try:
            with self._engine.begin() as connection:
                evaluation_row = (
                    connection.execute(
                        select(
                            evaluation.c.state,
                            evaluation.c.gate,
                            evaluation.c.evidence_manifest_id,
                            evaluation.c.policy_config_id,
                            evidence_artifact.c.status.label("evidence_artifact_status"),
                            evidence_artifact.c.visibility.label("evidence_artifact_visibility"),
                            evidence_artifact.c.encryption_domain.label("evidence_artifact_domain"),
                            evidence_artifact.c.content_digest.label("evidence_artifact_digest"),
                            evaluation.c.attempt_id,
                            task_version.c.document.label("task_document"),
                            task_version.c.digest.label("task_digest"),
                            config_document.c.kind.label("policy_kind"),
                            config_document.c.digest.label("policy_digest"),
                            policy_artifact.c.status.label("policy_artifact_status"),
                            policy_artifact.c.visibility.label("policy_artifact_visibility"),
                            policy_artifact.c.encryption_domain.label("policy_artifact_domain"),
                            policy_artifact.c.content_digest.label("policy_artifact_digest"),
                            outcome_artifact.c.status.label("outcome_artifact_status"),
                            outcome_artifact.c.visibility.label("outcome_artifact_visibility"),
                            outcome_artifact.c.encryption_domain.label("outcome_artifact_domain"),
                            outcome_artifact.c.content_digest.label("outcome_artifact_digest"),
                            candidate_table.c.id.label("candidate_id"),
                            run.c.id.label("run_id"),
                        )
                        .select_from(
                            evaluation.join(attempt, attempt.c.id == evaluation.c.attempt_id)
                            .join(task_version, task_version.c.id == attempt.c.task_version_id)
                            .join(run, run.c.id == attempt.c.run_id)
                            .join(
                                evidence_artifact,
                                evidence_artifact.c.id == evaluation.c.evidence_manifest_id,
                            )
                            .join(
                                config_document,
                                config_document.c.id == evaluation.c.policy_config_id,
                            )
                            .join(
                                policy_artifact,
                                policy_artifact.c.id == config_document.c.canonical_artifact_id,
                            )
                            .join(
                                candidate_table,
                                (candidate_table.c.attempt_id == attempt.c.id)
                                & (candidate_table.c.revision == 1),
                            )
                            .join(outcome_artifact, outcome_artifact.c.id == record.artifact_id)
                        )
                        .where(evaluation.c.id == record.evaluation_id)
                        .with_for_update(of=evaluation)
                    )
                    .mappings()
                    .one_or_none()
                )
                if evaluation_row is None:
                    raise InvalidReference("scoring evaluation or its frozen lineage is missing")
                if (
                    evaluation_row["state"] not in {"ready", "failed"}
                    or evaluation_row["evidence_manifest_id"] is None
                    or evaluation_row["evidence_manifest_id"] != record.evidence_artifact_id
                    or evaluation_row["evidence_artifact_status"] != "verified"
                    or evaluation_row["evidence_artifact_visibility"] != "internal"
                    or evaluation_row["evidence_artifact_domain"] != "evaluation-evidence"
                    or evaluation_row["evidence_artifact_digest"] != record.evidence_artifact_digest
                    or evaluation_row["gate"] != card.gate.value
                    or evaluation_row["outcome_artifact_status"] != "verified"
                    or evaluation_row["outcome_artifact_visibility"] != "internal"
                    or evaluation_row["outcome_artifact_domain"] != "scoring-outcomes"
                    or evaluation_row["outcome_artifact_digest"] != record.artifact_digest
                ):
                    raise InvalidState("scorecard is not bound to a completed evaluation artifact")
                if (
                    evaluation_row["policy_kind"] != "frozen_scoring_policy"
                    or evaluation_row["policy_artifact_status"] != "verified"
                    or evaluation_row["policy_artifact_visibility"] != "internal"
                    or evaluation_row["policy_artifact_domain"] != "worker-config"
                    or evaluation_row["policy_artifact_digest"] != evaluation_row["policy_digest"]
                    or card.scoring_policy_digest != evaluation_row["policy_digest"]
                ):
                    raise InvalidState("scorecard policy identity differs from the evaluation")
                try:
                    task = TaskVersion.model_validate_json(
                        json.dumps(evaluation_row["task_document"]), strict=True
                    )
                except (TypeError, ValueError):
                    raise InvalidState("frozen scoring task document is invalid") from None
                if canonical_document_digest(task) != evaluation_row["task_digest"]:
                    raise InvalidState("frozen scoring task digest is not canonical")
                if (
                    (card.task_id, card.task_version) != (task.task_id, task.version)
                    or card.run_id != str(evaluation_row["run_id"])
                    or card.candidate_id != str(evaluation_row["candidate_id"])
                ):
                    raise InvalidState("scorecard task, run, or candidate identity disagrees")

                existing = (
                    connection.execute(
                        select(
                            scorecard.c.id,
                            scorecard.c.artifact_id,
                            scorecard.c.gate,
                            scorecard.c.composite,
                            scorecard.c.evidence_digest,
                            scorecard.c.scorer_digest,
                        ).where(
                            scorecard.c.evaluation_id == record.evaluation_id,
                            scorecard.c.scorer_digest == card.scorer_digest,
                            scorecard.c.evidence_digest == card.evidence_manifest_digest,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    existing_id = UUID(str(existing["id"]))
                    existing_items = (
                        connection.execute(
                            select(
                                score_item.c.dimension,
                                score_item.c.item_id,
                                score_item.c.status,
                                score_item.c.applicable,
                                score_item.c.raw_value,
                                score_item.c.effective_weight,
                                score_item.c.contribution,
                                score_item.c.reason,
                                score_item.c.evidence_refs,
                            )
                            .where(score_item.c.scorecard_id == existing_id)
                            .order_by(score_item.c.dimension, score_item.c.item_id)
                        )
                        .mappings()
                        .all()
                    )
                    expected_items = sorted(
                        record.items, key=lambda item: (item.dimension.value, item.item_id)
                    )
                    items_match = len(existing_items) == len(expected_items) and all(
                        stored["dimension"] == expected.dimension.value
                        and stored["item_id"] == expected.item_id
                        and stored["status"] == expected.status
                        and stored["applicable"] == expected.applicable
                        and stored["raw_value"] == _unit_score(expected.raw_value)
                        and stored["effective_weight"]
                        == Decimal(expected.effective_weight_bps) / 10_000
                        and stored["contribution"] == _unit_score(expected.contribution)
                        and stored["reason"] == expected.reason
                        and stored["evidence_refs"] == list(expected.evidence_refs)
                        for stored, expected in zip(existing_items, expected_items, strict=True)
                    )
                    if (
                        existing_id != UUID(card.scorecard_id)
                        or UUID(str(existing["artifact_id"])) != record.artifact_id
                        or existing["gate"] != card.gate.value
                        or existing["composite"] != _unit_score(card.total_score)
                        or existing["scorer_digest"] != card.scorer_digest
                        or existing["evidence_digest"] != card.evidence_manifest_digest
                        or not items_match
                    ):
                        raise PersistenceConflict("scorecard replay conflicts with stored content")
                    return ScorecardWriteResult(existing_id, False)

                scorecard_id = UUID(card.scorecard_id)
                connection.execute(
                    insert(scorecard).values(
                        id=scorecard_id,
                        evaluation_id=record.evaluation_id,
                        scorer_digest=card.scorer_digest,
                        evidence_digest=card.evidence_manifest_digest,
                        artifact_id=record.artifact_id,
                        gate=card.gate.value,
                        composite=_unit_score(card.total_score),
                    )
                )
                connection.execute(
                    insert(score_item),
                    [
                        {
                            "id": uuid4(),
                            "scorecard_id": scorecard_id,
                            "dimension": item.dimension.value,
                            "item_id": item.item_id,
                            "status": item.status,
                            "applicable": item.applicable,
                            "raw_value": _unit_score(item.raw_value),
                            "effective_weight": Decimal(item.effective_weight_bps) / 10_000,
                            "contribution": _unit_score(item.contribution),
                            "reason": item.reason,
                            "evidence_refs": list(item.evidence_refs),
                        }
                        for item in record.items
                    ],
                )
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=actor,
                        action="scorecard.persist",
                        resource_type="scorecard",
                        resource_id=str(scorecard_id),
                        before_digest=None,
                        after_digest=card.evidence_manifest_digest,
                        request_id=f"scorecard-{scorecard_id}",
                        details={
                            "evaluation_id": str(record.evaluation_id),
                            "gate": card.gate.value,
                            "scoring_policy_digest": card.scoring_policy_digest,
                            "status": card.status.value,
                            "composite": card.total_score,
                        },
                    )
                )
                return ScorecardWriteResult(scorecard_id, True)
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None


def _unit_score(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value) / 100


__all__ = [
    "PostgresScoringRepository",
    "ScorecardRecord",
    "ScorecardWriteResult",
    "ScoreItemRecord",
]
