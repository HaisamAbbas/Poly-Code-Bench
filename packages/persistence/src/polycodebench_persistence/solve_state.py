"""Durable solve state: ordered events, atomic workspace/transcript checkpoints, candidates.

One transaction, one row lock on the attempt, one compare-and-swap on the last event sequence. A
controller that lost its authority (stale worker, cancelled attempt) cannot advance the
transcript, and a checkpoint row always names the workspace and transcript that were committed
with it, bound together by a digest that a restorer re-verifies.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidState,
    LeaseLost,
    NotFound,
    PersistenceConflict,
)
from polycodebench_core.model_contracts import stable_json_bytes
from polycodebench_core.solve_contracts import CheckpointMismatch
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    attempt,
    attempt_checkpoint,
    attempt_event,
    candidate,
)

ACTIVE_ATTEMPT_STATES = frozenset({"queued", "running"})


@dataclass(frozen=True)
class NewEvent:
    kind: str
    payload_artifact_id: UUID


@dataclass(frozen=True)
class NewCheckpoint:
    workspace_manifest_id: UUID
    transcript_manifest_id: UUID
    workspace_digest: str
    transcript_digest: str
    protocol_digest: str
    accumulated_budget: dict[str, Any]
    pending_call_ids: list[str]


@dataclass(frozen=True)
class CheckpointRow:
    attempt_id: UUID
    event_seq: int
    workspace_manifest_id: UUID
    transcript_manifest_id: UUID
    workspace_digest: str
    transcript_digest: str
    protocol_digest: str
    accumulated_budget: dict[str, Any]
    pending_call_ids: list[str]
    binding_digest: str


@dataclass(frozen=True)
class EventRow:
    event_seq: int
    kind: str
    payload_artifact_id: UUID


@dataclass(frozen=True)
class CandidateRow:
    candidate_id: UUID
    attempt_id: UUID
    revision: int
    payload_digest: str
    submission_kind: str
    payload: dict[str, Any]
    canonical_artifact_id: UUID


def binding_digest(
    attempt_id: UUID,
    event_seq: int,
    protocol_digest: str,
    workspace_digest: str,
    transcript_digest: str,
) -> str:
    body = stable_json_bytes(
        {
            "attempt_id": str(attempt_id),
            "event_seq": event_seq,
            "protocol_digest": protocol_digest,
            "workspace_digest": workspace_digest,
            "transcript_digest": transcript_digest,
        }
    )
    return "sha256:" + hashlib.sha256(body).hexdigest()


class PostgresSolveRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def last_sequence(self, attempt_id: UUID) -> int:
        """Highest committed event sequence, or -1 when nothing has been committed."""
        with self._engine.connect() as connection:
            value = connection.execute(
                select(func.max(attempt_event.c.event_seq)).where(
                    attempt_event.c.attempt_id == attempt_id
                )
            ).scalar_one()
        return -1 if value is None else int(value)

    def commit(
        self,
        attempt_id: UUID,
        *,
        expected_last_seq: int,
        events: Sequence[NewEvent],
        checkpoint: NewCheckpoint | None,
        dispatch_allowed: Callable[[], bool] | None = None,
    ) -> list[int]:
        """Append events and (optionally) the checkpoint covering them, atomically.

        The sequence compare-and-swap makes a duplicate or stale controller fail instead of
        forking the transcript. Checkpoints always sit on the last sequence of their batch.
        """
        if not events:
            raise InvalidState("a commit needs at least one event")
        if dispatch_allowed is not None and not dispatch_allowed():
            raise LeaseLost()
        try:
            with self._engine.begin() as connection:
                state = connection.execute(
                    select(attempt.c.state).where(attempt.c.id == attempt_id).with_for_update()
                ).scalar_one_or_none()
                if state is None:
                    raise NotFound()
                if state not in ACTIVE_ATTEMPT_STATES:
                    raise InvalidState("the attempt is no longer active")
                current = connection.execute(
                    select(func.max(attempt_event.c.event_seq)).where(
                        attempt_event.c.attempt_id == attempt_id
                    )
                ).scalar_one()
                current = -1 if current is None else int(current)
                if current != expected_last_seq:
                    raise PersistenceConflict("transcript advanced elsewhere; controller is stale")
                sequences = []
                for offset, event in enumerate(events, start=1):
                    seq = expected_last_seq + offset
                    connection.execute(
                        insert(attempt_event).values(
                            id=uuid4(),
                            attempt_id=attempt_id,
                            event_seq=seq,
                            event_kind=event.kind,
                            payload_artifact_id=event.payload_artifact_id,
                        )
                    )
                    sequences.append(seq)
                if checkpoint is not None:
                    last = sequences[-1]
                    connection.execute(
                        insert(attempt_checkpoint).values(
                            id=uuid4(),
                            attempt_id=attempt_id,
                            event_seq=last,
                            workspace_manifest_id=checkpoint.workspace_manifest_id,
                            transcript_manifest_id=checkpoint.transcript_manifest_id,
                            accumulated_budget=checkpoint.accumulated_budget,
                            pending_call_ids=checkpoint.pending_call_ids,
                            protocol_digest=checkpoint.protocol_digest,
                            workspace_digest=checkpoint.workspace_digest,
                            transcript_digest=checkpoint.transcript_digest,
                            binding_digest=binding_digest(
                                attempt_id,
                                last,
                                checkpoint.protocol_digest,
                                checkpoint.workspace_digest,
                                checkpoint.transcript_digest,
                            ),
                        )
                    )
                return sequences
        except DBAPIError as error:
            raise map_database_error(error) from None

    def latest_checkpoint(self, attempt_id: UUID) -> CheckpointRow | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(attempt_checkpoint)
                    .where(attempt_checkpoint.c.attempt_id == attempt_id)
                    .order_by(attempt_checkpoint.c.event_seq.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        checkpoint = CheckpointRow(
            attempt_id=row["attempt_id"],
            event_seq=int(row["event_seq"]),
            workspace_manifest_id=row["workspace_manifest_id"],
            transcript_manifest_id=row["transcript_manifest_id"],
            workspace_digest=row["workspace_digest"],
            transcript_digest=row["transcript_digest"],
            protocol_digest=row["protocol_digest"],
            accumulated_budget=dict(row["accumulated_budget"]),
            pending_call_ids=list(row["pending_call_ids"]),
            binding_digest=row["binding_digest"],
        )
        expected = binding_digest(
            checkpoint.attempt_id,
            checkpoint.event_seq,
            checkpoint.protocol_digest,
            checkpoint.workspace_digest,
            checkpoint.transcript_digest,
        )
        if expected != checkpoint.binding_digest:
            raise CheckpointMismatch("checkpoint binding digest does not verify")
        return checkpoint

    def events(self, attempt_id: UUID, *, upto_seq: int | None = None) -> list[EventRow]:
        query = select(attempt_event).where(attempt_event.c.attempt_id == attempt_id)
        if upto_seq is not None:
            query = query.where(attempt_event.c.event_seq <= upto_seq)
        with self._engine.connect() as connection:
            rows = connection.execute(query.order_by(attempt_event.c.event_seq)).mappings().all()
        return [
            EventRow(int(r["event_seq"]), str(r["event_kind"]), r["payload_artifact_id"])
            for r in rows
        ]

    def freeze_candidate(
        self,
        attempt_id: UUID,
        *,
        payload_digest: str,
        submission_kind: str,
        payload: dict[str, Any],
        canonical_artifact_id: UUID,
        expected_last_seq: int | None = None,
        dispatch_allowed: Callable[[], bool] | None = None,
        revision: int = 1,
    ) -> CandidateRow:
        """Record one candidate revision of an attempt (revision 1 is the solve session's one
        candidate; self-repair rounds freeze later revisions). Replaying the same bytes returns
        the row; a different digest for the same revision is a conflict, never an overwrite."""
        if revision < 1:
            raise InvalidState("candidate revisions are positive")
        if dispatch_allowed is not None and not dispatch_allowed():
            raise LeaseLost()
        try:
            with self._engine.begin() as connection:
                state = connection.execute(
                    select(attempt.c.state).where(attempt.c.id == attempt_id).with_for_update()
                ).scalar_one_or_none()
                if state is None:
                    raise NotFound()
                if expected_last_seq is not None:
                    current = connection.execute(
                        select(func.max(attempt_event.c.event_seq)).where(
                            attempt_event.c.attempt_id == attempt_id
                        )
                    ).scalar_one()
                    if (-1 if current is None else int(current)) != expected_last_seq:
                        raise PersistenceConflict(
                            "transcript advanced elsewhere; controller is stale"
                        )
                existing = (
                    connection.execute(
                        select(candidate).where(
                            candidate.c.attempt_id == attempt_id,
                            candidate.c.revision == revision,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    if existing["payload_digest"] != payload_digest:
                        raise PersistenceConflict("attempt already has a different candidate")
                    return _candidate(existing)
                if state not in ACTIVE_ATTEMPT_STATES:
                    raise InvalidState("the attempt is no longer active")
                candidate_id = uuid4()
                connection.execute(
                    insert(candidate).values(
                        id=candidate_id,
                        attempt_id=attempt_id,
                        revision=revision,
                        payload_digest=payload_digest,
                        submission_kind=submission_kind,
                        payload=payload,
                        canonical_artifact_id=canonical_artifact_id,
                    )
                )
                version = connection.execute(
                    select(attempt.c.row_version).where(attempt.c.id == attempt_id)
                ).scalar_one()
                connection.execute(
                    update(attempt)
                    .where(attempt.c.id == attempt_id)
                    .values(candidate_artifact_id=canonical_artifact_id, row_version=version + 1)
                )
                row = (
                    connection.execute(select(candidate).where(candidate.c.id == candidate_id))
                    .mappings()
                    .one()
                )
                return _candidate(row)
        except DBAPIError as error:
            raise map_database_error(error) from None

    def candidate_for(self, attempt_id: UUID, *, revision: int = 1) -> CandidateRow | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(candidate).where(
                        candidate.c.attempt_id == attempt_id, candidate.c.revision == revision
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _candidate(row)


def _candidate(row: Any) -> CandidateRow:
    return CandidateRow(
        candidate_id=row["id"],
        attempt_id=row["attempt_id"],
        revision=int(row["revision"]),
        payload_digest=row["payload_digest"],
        submission_kind=row["submission_kind"],
        payload=dict(row["payload"]),
        canonical_artifact_id=row["canonical_artifact_id"],
    )
