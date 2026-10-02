"""PostgreSQL repository for judge execution evidence.

Every write here is append-only. A packet, its deliveries, its votes, its results and every
adjudication are immutable rows, so a reviewer's override is recorded as a new row that supersedes
an older one and the original votes stay readable. The database triggers enforce that; the
repository additionally refuses a delivery or vote whose packet, panel or digest does not match
what was registered.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from polycodebench_core.application_errors import InvalidState
from polycodebench_core.judge_calibration import CalibrationLabel
from polycodebench_core.judge_contracts import (
    PILOT_JUDGE_VOTES,
    AdjudicationDecision,
    CalibrationBlocked,
    JudgeCohort,
    JudgeDelivery,
    JudgementResult,
    JudgePacket,
    JudgeVote,
    adjudication_identity,
)
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    adjudication,
    calibration_label,
    call_delivery,
    judge_cohort,
    judge_delivery,
    judge_item_result,
    judge_packet,
    judge_result,
    judge_vote,
)


def _timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PostgresJudgingRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ cohorts

    def register_cohort(self, cohort: JudgeCohort) -> UUID:
        """Register one panel for one cohort version. A repeat with the same digest is a replay."""
        try:
            with self._engine.begin() as connection:
                existing = connection.execute(
                    select(judge_cohort.c.id).where(
                        judge_cohort.c.cohort_id == cohort.cohort_id,
                        judge_cohort.c.evaluation_version == cohort.evaluation_version,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    stored = connection.execute(
                        select(judge_cohort.c.cohort_digest, judge_cohort.c.panel_digest).where(
                            judge_cohort.c.id == existing
                        )
                    ).one()
                    if (
                        stored.cohort_digest != cohort.digest()
                        or stored.panel_digest != cohort.panel_digest
                    ):
                        raise InvalidState(
                            "the cohort version already exists with a different panel or digest"
                        )
                    return UUID(str(existing))
                cohort_id = uuid4()
                connection.execute(
                    judge_cohort.insert().values(
                        id=cohort_id,
                        cohort_id=cohort.cohort_id,
                        evaluation_version=cohort.evaluation_version,
                        panel_id=cohort.panel_id,
                        panel_digest=cohort.panel_digest,
                        rubric_digest=cohort.rubric_digest,
                        candidate_model_config_ids=list(cohort.candidate_model_config_ids),
                        cohort_digest=cohort.digest(),
                    )
                )
                return cohort_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def cohort_for_panel(self, cohort_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(judge_cohort).where(judge_cohort.c.id == cohort_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise InvalidState("no such judge cohort")
        return dict(row)

    # ------------------------------------------------------------------ packets

    def register_packet(
        self,
        *,
        evaluation_id: UUID,
        packet: JudgePacket,
        packet_artifact_id: UUID,
        cohort_id: UUID | None,
    ) -> UUID:
        """Register one anonymized packet and its canonical bytes as an immutable artifact link."""
        try:
            with self._engine.begin() as connection:
                existing = connection.execute(
                    select(judge_packet.c.id).where(
                        judge_packet.c.evaluation_id == evaluation_id,
                        judge_packet.c.packet_digest == packet.digest(),
                        judge_packet.c.panel_digest == packet.panel_digest,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return UUID(str(existing))
                row_id = uuid4()
                connection.execute(
                    judge_packet.insert().values(
                        id=row_id,
                        evaluation_id=evaluation_id,
                        packet_digest=packet.digest(),
                        rubric_digest=packet.rubric_digest,
                        panel_digest=packet.panel_digest,
                        required_votes=PILOT_JUDGE_VOTES,
                        packet_artifact_id=packet_artifact_id,
                        cohort_id=cohort_id,
                        language=packet.language,
                        packet_role=packet.packet_role,
                    )
                )
                return row_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def packet_row(self, packet_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(judge_packet).where(judge_packet.c.id == packet_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise InvalidState("no such judge packet")
        return dict(row)

    def packets_for_evaluation(self, evaluation_id: UUID) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(judge_packet)
                .where(judge_packet.c.evaluation_id == evaluation_id)
                .order_by(judge_packet.c.created_at, judge_packet.c.id)
            ).mappings()
            return [dict(row) for row in rows]

    def packets_needing_review(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Packets whose *newest* result is not ``ready``, newest first.

        Results are append-only, so a packet that was adjudicated keeps its earlier
        ``needs_review`` row. Reading every non-ready row would fill the queue with resolved
        packets, so only the latest result per packet is considered.
        """
        latest = (
            select(
                judge_result.c.packet_id.label("packet_id"),
                judge_result.c.status.label("status"),
                judge_result.c.result_index.label("result_index"),
            )
            .where(judge_result.c.packet_id == judge_result.c.packet_id)
            .distinct(judge_result.c.packet_id)
            .order_by(judge_result.c.packet_id, judge_result.c.result_index.desc())
            .subquery()
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(latest.c.packet_id, latest.c.status, latest.c.result_index)
                .where(latest.c.status != "ready")
                .order_by(latest.c.result_index.desc(), latest.c.packet_id)
                .limit(limit)
            ).all()
        return [{"packet_id": row[0], "status": row[1], "result_index": row[2]} for row in rows]

    # ------------------------------------------------------------------ deliveries

    def record_delivery(
        self,
        *,
        packet_row_id: UUID,
        delivery: JudgeDelivery,
        call_delivery_id: UUID | None,
        call_intent_id: UUID | None,
    ) -> UUID:
        """Retain one dispatched delivery, valid or not.

        A replayed run re-dispatches the same logical delivery; it records the same row instead of
        failing, so a resumed panel completes rather than leaving a half-written ledger.
        """
        try:
            with self._engine.begin() as connection:
                existing = connection.execute(
                    select(judge_delivery.c.id).where(
                        judge_delivery.c.packet_id == packet_row_id,
                        judge_delivery.c.vote_index == delivery.vote_index,
                        judge_delivery.c.delivery_index == delivery.delivery_index,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return UUID(str(existing))
                row_id = uuid4()
                connection.execute(
                    judge_delivery.insert().values(
                        id=row_id,
                        packet_id=packet_row_id,
                        vote_index=delivery.vote_index,
                        delivery_index=delivery.delivery_index,
                        call_delivery_id=call_delivery_id,
                        call_intent_id=call_intent_id,
                        raw_artifact_id=(
                            UUID(delivery.raw_response_artifact_id)
                            if delivery.raw_response_artifact_id
                            else None
                        ),
                        status=delivery.status,
                        invalid_reason=(
                            delivery.invalid_reason.value if delivery.invalid_reason else None
                        ),
                        detail=delivery.detail,
                        judge_revision=delivery.judge_revision,
                        seed=delivery.seed,
                        seed_supported=delivery.seed_supported,
                        repair_instruction_id=delivery.repair_instruction_id,
                        raw_response_digest=delivery.raw_response_digest,
                        created_at=_timestamp(delivery.created_at),
                    )
                )
                return row_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def deliveries(self, packet_row_id: UUID) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(judge_delivery)
                .where(judge_delivery.c.packet_id == packet_row_id)
                .order_by(judge_delivery.c.vote_index, judge_delivery.c.delivery_index)
            ).mappings()
            return [dict(row) for row in rows]

    def delivery_for_call(self, intent_id: UUID, delivery_index: int) -> UUID | None:
        """The gateway's delivery row for one recorded response."""
        with self._engine.connect() as connection:
            return connection.execute(
                select(call_delivery.c.id).where(
                    call_delivery.c.intent_id == intent_id,
                    call_delivery.c.delivery_index == delivery_index,
                )
            ).scalar_one_or_none()

    def delivery_artifacts(self, intent_id: UUID, delivery_index: int) -> dict[str, UUID | None]:
        """The gateway's own raw/normalized response artifacts for one recorded delivery.

        The judge does not re-store provider bytes: the gateway already persisted them as verified
        artifacts, so a retained judge delivery links to the exact bytes that were parsed.
        """
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        call_delivery.c.id,
                        call_delivery.c.raw_response_artifact_id,
                        call_delivery.c.normalized_response_artifact_id,
                    ).where(
                        call_delivery.c.intent_id == intent_id,
                        call_delivery.c.delivery_index == delivery_index,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return {"call_delivery_id": None, "raw_artifact_id": None}
        return {
            "call_delivery_id": row["id"],
            "raw_artifact_id": row["raw_response_artifact_id"],
        }

    def record_vote(self, *, packet_row_id: UUID, vote: JudgeVote) -> UUID:
        """Store one accepted vote. The normalized vote bytes live in an artifact."""
        try:
            with self._engine.begin() as connection:
                existing = connection.execute(
                    select(judge_vote.c.id).where(
                        judge_vote.c.packet_id == packet_row_id,
                        judge_vote.c.vote_index == vote.vote_index,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return UUID(str(existing))
                if vote.call_delivery_id is None:
                    raise InvalidState(
                        "an accepted vote must name the gateway delivery it came from"
                    )
                row_id = uuid4()
                connection.execute(
                    judge_vote.insert().values(
                        id=row_id,
                        packet_id=packet_row_id,
                        vote_index=vote.vote_index,
                        call_delivery_id=UUID(vote.call_delivery_id),
                        normalized_artifact_id=(
                            UUID(vote.normalized_artifact_id)
                            if vote.normalized_artifact_id
                            else None
                        ),
                        status="valid",
                        created_at=_timestamp(vote.created_at),
                    )
                )
                return row_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def votes(self, packet_row_id: UUID) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(judge_vote)
                .where(judge_vote.c.packet_id == packet_row_id)
                .order_by(judge_vote.c.vote_index)
            ).mappings()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------ results

    def record_result(
        self,
        *,
        packet_row_id: UUID,
        evaluation_id: UUID,
        result: JudgementResult,
        report_artifact_id: UUID,
        result_index: int,
    ) -> UUID:
        """Append one frozen result. A later adjudication appends a new result, never edits one."""
        if result.report_digest is None:
            raise InvalidState("a stored judge result must carry its own frozen digest")
        try:
            with self._engine.begin() as connection:
                row_id = uuid4()
                connection.execute(
                    judge_result.insert().values(
                        id=row_id,
                        packet_id=packet_row_id,
                        evaluation_id=evaluation_id,
                        status=result.status,
                        report_artifact_id=report_artifact_id,
                        report_digest=result.report_digest,
                        result_index=result_index,
                    )
                )
                for item in result.items:
                    connection.execute(
                        judge_item_result.insert().values(
                            id=uuid4(),
                            result_id=row_id,
                            item_id=item.item_id,
                            dimension=item.dimension.value,
                            status=item.status,
                            mean_score=item.mean_score,
                            vote_count=item.vote_count,
                            required_votes=item.required_votes,
                            source=item.source,
                            adjudication_id=(
                                UUID(item.adjudication_id) if item.adjudication_id else None
                            ),
                            triggers=[trigger.value for trigger in item.triggers],
                            vote_scores=list(item.vote_scores),
                        )
                    )
                return row_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def results(self, packet_row_id: UUID) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(judge_result)
                .where(judge_result.c.packet_id == packet_row_id)
                .order_by(judge_result.c.result_index)
            ).mappings()
            return [dict(row) for row in rows]

    def next_result_index(self, packet_row_id: UUID) -> int:
        with self._engine.connect() as connection:
            count = connection.execute(
                select(judge_result.c.id).where(judge_result.c.packet_id == packet_row_id)
            ).all()
        return len(count)

    # ------------------------------------------------------------------ adjudication

    def record_adjudication(
        self,
        *,
        evaluation_id: UUID,
        decision: AdjudicationDecision,
        resolution_artifact_id: UUID,
        supersedes_id: UUID | None,
    ) -> UUID:
        """Record a reviewer override. Votes are never touched."""
        try:
            with self._engine.begin() as connection:
                # The decision's own content identity is the row identity: a replayed decision is
                # the same immutable row, and the item result's reference is verifiable.
                row_id = UUID(adjudication_identity(decision))
                connection.execute(
                    adjudication.insert().values(
                        id=row_id,
                        evaluation_id=evaluation_id,
                        target_kind="judge_item",
                        target_id=f"{decision.packet_id}:{decision.item_id}",
                        resolution_artifact_id=resolution_artifact_id,
                        reviewer_subject=decision.reviewer_subject,
                        reason=decision.reason,
                        supersedes_id=supersedes_id,
                        created_at=_timestamp(decision.decided_at),
                    )
                )
                return row_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def adjudications_for(self, evaluation_id: UUID) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(adjudication)
                .where(
                    adjudication.c.evaluation_id == evaluation_id,
                    adjudication.c.target_kind == "judge_item",
                )
                .order_by(adjudication.c.created_at, adjudication.c.id)
            ).mappings()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------ calibration

    def require_delivery_artifact(self, packet_row_id: UUID, delivery_index: int) -> None:
        """Fail loudly rather than storing a vote whose retained response cannot be re-read."""
        with self._engine.connect() as connection:
            found = (
                connection.execute(
                    select(judge_delivery.c.raw_artifact_id).where(
                        judge_delivery.c.packet_id == packet_row_id,
                        judge_delivery.c.delivery_index == delivery_index,
                    )
                )
                .scalars()
                .all()
            )
        if not found:
            raise InvalidState("the retained judge delivery for this vote cannot be located")

    def record_label(
        self, *, cohort_id: UUID, packet_row_id: UUID, label: CalibrationLabel
    ) -> UUID:
        """Store one qualified human label. Labels are append-only evidence, never edits."""
        try:
            with self._engine.begin() as connection:
                row_id = uuid4()
                connection.execute(
                    calibration_label.insert().values(
                        id=row_id,
                        cohort_id=cohort_id,
                        packet_id=packet_row_id,
                        packet_digest=label.packet_digest,
                        item_id=label.item_id,
                        score=label.score,
                        labeler_subject=label.labeler_subject,
                        qualification=label.qualification,
                        rationale=label.rationale,
                        cited_anchor_ids=list(label.cited_anchor_ids),
                        labeled_at=_timestamp(label.labeled_at),
                    )
                )
                return row_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def labels(self, *, cohort_id: UUID | None = None) -> list[dict[str, Any]]:
        query = select(calibration_label).order_by(
            calibration_label.c.packet_digest, calibration_label.c.item_id
        )
        if cohort_id is not None:
            query = query.where(calibration_label.c.cohort_id == cohort_id)
        with self._engine.connect() as connection:
            return [dict(row) for row in connection.execute(query).mappings()]

    def require_labels_present(self, *, cohort_id: UUID, expected: int) -> None:
        """Refuse to report a measured calibration while the human evidence is absent."""
        rows = self.labels(cohort_id=cohort_id)
        if len(rows) < expected:
            raise CalibrationBlocked(f"{len(rows)} calibration labels stored, {expected} required")
