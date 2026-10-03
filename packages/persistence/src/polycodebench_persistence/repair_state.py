"""Durable self-repair state: ordered immutable rounds, deliveries, round-boundary checkpoints.

One transaction advances one run frontier: a round row (append-only, like candidates) plus its
first delivery, committed together with the run row's spend, selection state and digest under a
compare-and-swap on ``rounds_committed``. That run row IS the round-boundary checkpoint
(Technical Spec 9.3), so a controller that lost authority cannot advance the round counter.

Infrastructure recovery is expressed exactly as the protocol defines it: :meth:`record_redelivery`
inserts one immutable delivery row and adds its cost to the accumulated spend. It cannot insert a
round, cannot move ``rounds_committed`` and cannot subtract from spend, so recovering
infrastructure never grants another repair round and never erases spent budget (PCB-26-3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from polycodebench_core.application_errors import (
    InvalidState,
    NotFound,
    PersistenceConflict,
)
from polycodebench_core.repair_contracts import (
    PublicCaseResult,
    RepairCheckpoint,
    RepairProtocol,
    RepairRound,
    RepairRun,
    RepairSpend,
    checkpoint_of,
)
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    repair_delivery,
    repair_round,
    repair_run,
)


@dataclass(frozen=True)
class NewDelivery:
    """One model-call delivery for a round. Index 1 is the call itself; >1 is redelivery."""

    delivery_index: int
    input_tokens: int
    output_tokens: int
    cost_micros: int
    active_ms: int


class PostgresRepairRepository:
    """The durable half of a repair run. Pure round semantics stay in repair_contracts."""

    def __init__(self, engine: Engine) -> None:
        self._engine: Engine = engine

    # ----------------------------------------------------------------------------- creation

    def create_run(self, run: RepairRun) -> None:
        """Insert a run once, before any round. Replaying the same digest is idempotent."""
        if run.rounds:
            raise InvalidState("a repair run is created before its first round")
        try:
            with self._engine.begin() as connection:
                existing = (
                    connection.execute(
                        select(repair_run.c.run_digest).where(
                            repair_run.c.repair_run_id == run.repair_run_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    if existing["run_digest"] != run.digest:
                        raise PersistenceConflict("repair run id already bound to other state")
                    return
                _ = connection.execute(
                    insert(repair_run).values(
                        attempt_id=UUID(run.attempt_id),
                        repair_run_id=run.repair_run_id,
                        protocol_digest=run.protocol.digest,
                        protocol=run.protocol.model_dump(mode="json"),
                        public_case_ids=list(run.public_case_ids),
                        rounds_committed=0,
                        spend=run.spend.model_dump(mode="json"),
                        state=run.state,
                        selected_round_index=run.selected_round_index,
                        run_digest=run.digest,
                    )
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    # ---------------------------------------------------------------------- frontier advance

    def commit_round(
        self, run: RepairRun, *, expected_rounds: int, delivery: NewDelivery
    ) -> None:
        """Commit the newest round and the run frontier as one atomic round-boundary checkpoint."""
        if not run.rounds:
            raise InvalidState("a round to commit must exist in the run")
        newest = run.rounds[-1]
        if newest.round_index != expected_rounds:
            raise InvalidState("the committed round must be the expected frontier round")
        if delivery.delivery_index != 1 or newest.deliveries != 1:
            raise InvalidState(
                "a round commits with its first delivery; redelivery is recorded after commit"
            )
        try:
            with self._engine.begin() as connection:
                row = self._locked_run(connection, run.repair_run_id)
                self._check_frontier(row, expected_rounds)
                self._insert_round(connection, row["id"], newest)
                round_pk = self._round_pk(connection, row["id"], newest.round_index)
                self._insert_delivery_row(connection, round_pk, delivery)
                self._write_run_state(connection, row, run)
        except DBAPIError as error:
            raise map_database_error(error) from None

    def record_redelivery(
        self, run: RepairRun, *, round_index: int, delivery: NewDelivery
    ) -> None:
        """Infrastructure recovery: one more immutable delivery, more spend, same round frontier."""
        if not run.rounds or round_index != run.rounds[-1].round_index:
            raise InvalidState("redelivery applies to the frontier round only")
        try:
            with self._engine.begin() as connection:
                row = self._locked_run(connection, run.repair_run_id)
                self._check_frontier(row, len(run.rounds))
                round_pk = self._round_pk(connection, row["id"], round_index)
                self._insert_delivery_row(connection, round_pk, delivery)
                self._write_run_state(connection, row, run)
        except DBAPIError as error:
            raise map_database_error(error) from None

    def record_selection(self, run: RepairRun) -> None:
        """Persist the protocol-selected final round. Rounds are never touched."""
        if run.state != "complete" or run.selected_round_index is None:
            raise InvalidState("selection records only a completed run")
        try:
            with self._engine.begin() as connection:
                row = self._locked_run(connection, run.repair_run_id)
                self._check_frontier(row, len(run.rounds))
                self._write_run_state(connection, row, run)
        except DBAPIError as error:
            raise map_database_error(error) from None

    # --------------------------------------------------------------------------------- reads

    def load_run(self, repair_run_id: str) -> RepairRun:
        """Rebuild the run and re-verify its digest; drift is refused, never repaired."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(repair_run).where(repair_run.c.repair_run_id == repair_run_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise NotFound()
            round_rows = (
                connection.execute(
                    select(repair_round)
                    .where(repair_round.c.repair_run_id == row["id"])
                    .order_by(repair_round.c.round_index)
                )
                .mappings()
                .all()
            )
            counts = {
                round_pk: int(total)
                for round_pk, total in connection.execute(
                    select(repair_delivery.c.repair_round_id, func.count())
                    .select_from(repair_delivery)
                    .join(repair_round, repair_delivery.c.repair_round_id == repair_round.c.id)
                    .where(repair_round.c.repair_run_id == row["id"])
                    .group_by(repair_delivery.c.repair_round_id)
                ).all()
            }
        run = RepairRun(
            schema_version=1,
            kind="repair_run",
            repair_run_id=row["repair_run_id"],
            attempt_id=str(row["attempt_id"]),
            protocol=RepairProtocol.model_validate(row["protocol"]),
            public_case_ids=tuple(row["public_case_ids"]),
            rounds=tuple(_round(entry, counts.get(entry["id"], 1)) for entry in round_rows),
            spend=RepairSpend.model_validate(row["spend"]),
            state=row["state"],
            selected_round_index=row["selected_round_index"],
        )
        if run.digest != row["run_digest"]:
            raise PersistenceConflict("repair run rows do not match their recorded digest")
        return run

    def checkpoint(
        self,
        repair_run_id: str,
        *,
        pending_round_index: int | None = None,
        pending_delivery_ids: tuple[str, ...] = (),
    ) -> RepairCheckpoint:
        run = self.load_run(repair_run_id)
        return checkpoint_of(
            run,
            pending_round_index=pending_round_index,
            pending_delivery_ids=pending_delivery_ids,
        )

    # ----------------------------------------------------------------------------- internals

    def _locked_run(self, connection: Any, repair_run_id: str) -> Any:
        row = (
            connection.execute(
                select(repair_run)
                .where(repair_run.c.repair_run_id == repair_run_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound()
        return row

    def _check_frontier(self, row: Any, expected_rounds: int) -> None:
        if int(row["rounds_committed"]) != expected_rounds:
            raise PersistenceConflict("rounds advanced elsewhere; controller is stale")

    def _insert_round(self, connection: Any, run_pk: UUID, round: RepairRound) -> None:
        _ = connection.execute(
            insert(repair_round).values(
                repair_run_id=run_pk,
                round_index=round.round_index,
                candidate_revision=round.candidate_revision,
                candidate_digest=round.candidate_digest,
                prompt_digest=round.prompt_digest,
                request_digest=round.request_digest,
                feedback_digest=round.feedback_digest,
                state=round.state,
                public_results=[
                    result.model_dump(mode="json") for result in round.public_results
                ],
                input_tokens=round.input_tokens,
                output_tokens=round.output_tokens,
                cost_micros=round.cost_micros,
            )
        )

    def _round_pk(self, connection: Any, run_pk: UUID, round_index: int) -> UUID:
        row = connection.execute(
            select(repair_round.c.id).where(
                repair_round.c.repair_run_id == run_pk,
                repair_round.c.round_index == round_index,
            )
        ).scalar_one()
        return UUID(str(row))

    def _insert_delivery_row(
        self, connection: Any, round_pk: UUID, delivery: NewDelivery
    ) -> None:
        _ = connection.execute(
            insert(repair_delivery).values(
                repair_round_id=round_pk,
                delivery_index=delivery.delivery_index,
                input_tokens=delivery.input_tokens,
                output_tokens=delivery.output_tokens,
                cost_micros=delivery.cost_micros,
                active_ms=delivery.active_ms,
            )
        )

    def _write_run_state(self, connection: Any, row: Any, run: RepairRun) -> None:
        _ = connection.execute(
            update(repair_run)
            .where(repair_run.c.id == row["id"])
            .values(
                rounds_committed=len(run.rounds),
                spend=run.spend.model_dump(mode="json"),
                state=run.state,
                selected_round_index=run.selected_round_index,
                run_digest=run.digest,
                row_version=int(row["row_version"]) + 1,
            )
        )


def _round(row: Any, deliveries: int) -> RepairRound:
    return RepairRound(
        schema_version=1,
        kind="repair_round",
        round_index=int(row["round_index"]),
        candidate_revision=int(row["candidate_revision"]),
        candidate_digest=row["candidate_digest"],
        prompt_digest=row["prompt_digest"],
        request_digest=row["request_digest"],
        feedback_digest=row["feedback_digest"],
        deliveries=max(1, deliveries),
        state=row["state"],
        public_results=tuple(
            PublicCaseResult.model_validate(result) for result in row["public_results"]
        ),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cost_micros=int(row["cost_micros"]),
    )
