"""Atomic run/attempt creation and idempotency using PostgreSQL transactions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    IdempotencyConflict,
    InvalidReference,
    InvalidState,
    OptimisticVersionConflict,
    PersistenceConflict,
    PersistenceUnavailable,
)
from polycodebench_core.identity import derive_sample_seed
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    attempt,
    audit_event,
    campaign,
    config_document,
    idempotency_record,
    model_revision,
    run,
    task_set,
    task_set_member,
    task_version,
)

IDEMPOTENCY_TTL = timedelta(days=7)
MAX_ATTEMPTS_PER_RUN = 100_000


class PostgresRunRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_idempotently(
        self,
        *,
        subject_id: str,
        route: str,
        idempotency_key: str,
        request_digest: str,
        request: Mapping[str, object],
    ) -> Mapping[str, object]:
        now = datetime.now(UTC)
        record_id = uuid4()
        try:
            with self._engine.begin() as connection:
                claimed = connection.execute(
                    pg_insert(idempotency_record)
                    .values(
                        id=record_id,
                        subject=subject_id,
                        route=route,
                        key=idempotency_key,
                        request_digest=request_digest,
                        state="in_progress",
                        expires_at=now + IDEMPOTENCY_TTL,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            idempotency_record.c.subject,
                            idempotency_record.c.route,
                            idempotency_record.c.key,
                        ]
                    )
                    .returning(idempotency_record.c.id)
                ).scalar_one_or_none()

                if claimed is None:
                    existing = (
                        connection.execute(
                            select(idempotency_record)
                            .where(
                                idempotency_record.c.subject == subject_id,
                                idempotency_record.c.route == route,
                                idempotency_record.c.key == idempotency_key,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing is None:
                        raise PersistenceUnavailable()
                    if existing["expires_at"] <= now:
                        connection.execute(
                            delete(idempotency_record).where(
                                idempotency_record.c.id == existing["id"]
                            )
                        )
                        claimed = connection.execute(
                            pg_insert(idempotency_record)
                            .values(
                                id=record_id,
                                subject=subject_id,
                                route=route,
                                key=idempotency_key,
                                request_digest=request_digest,
                                state="in_progress",
                                expires_at=now + IDEMPOTENCY_TTL,
                            )
                            .on_conflict_do_nothing()
                            .returning(idempotency_record.c.id)
                        ).scalar_one_or_none()
                    else:
                        if existing["request_digest"] != request_digest:
                            raise IdempotencyConflict()
                        if existing["state"] != "completed":
                            raise PersistenceUnavailable("idempotent request is still in progress")
                        response = dict(existing["response_payload"])
                        response["replayed"] = True
                        return response
                    if claimed is None:
                        raise PersistenceUnavailable()

                run_id, attempt_ids = self._create_run_and_attempts(connection, request, subject_id)
                response_payload: dict[str, object] = {
                    "run_id": str(run_id),
                    "attempt_ids": [str(item) for item in attempt_ids],
                }
                connection.execute(
                    update(idempotency_record)
                    .where(idempotency_record.c.id == record_id)
                    .values(state="completed", response_code=202, response_payload=response_payload)
                )
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=subject_id,
                        action="run.create",
                        resource_type="run",
                        resource_id=str(run_id),
                        before_digest=None,
                        after_digest=request_digest,
                        request_id=idempotency_key,
                        details={
                            "attempt_count": len(attempt_ids),
                            "request_digest": request_digest,
                        },
                    )
                )
                return response_payload
        except (
            IdempotencyConflict,
            InvalidReference,
            InvalidState,
            PersistenceConflict,
            PersistenceUnavailable,
        ):
            raise
        except DBAPIError as error:
            mapped = map_database_error(error)
            raise mapped from None

    @staticmethod
    def _create_run_and_attempts(
        connection: Connection,
        request: Mapping[str, object],
        subject_id: str,
    ) -> tuple[UUID, list[UUID]]:
        campaign_id = UUID(str(request["campaign_id"]))
        config_id = UUID(str(request["config_document_id"]))
        task_set_id = UUID(str(request["task_set_id"]))
        model_revision_id = UUID(str(request["model_revision_id"]))
        raw_samples_per_task = request["samples_per_task"]
        master_seed = request["master_seed"]
        if (
            not isinstance(raw_samples_per_task, int)
            or isinstance(raw_samples_per_task, bool)
            or raw_samples_per_task < 1
            or not isinstance(master_seed, str)
        ):
            raise InvalidState("run sampling values are invalid")
        samples_per_task = raw_samples_per_task

        campaign_row = (
            connection.execute(select(campaign).where(campaign.c.id == campaign_id))
            .mappings()
            .one_or_none()
        )
        if campaign_row is None:
            raise InvalidReference("campaign does not exist")
        if campaign_row["status"] not in {"draft", "planned"}:
            raise InvalidState("campaign cannot accept a run in its current state")
        config_row = (
            connection.execute(select(config_document).where(config_document.c.id == config_id))
            .mappings()
            .one_or_none()
        )
        if config_row is None or config_row["kind"] != "run_config":
            raise InvalidReference("run configuration reference is invalid")
        task_set_row = (
            connection.execute(select(task_set).where(task_set.c.id == task_set_id))
            .mappings()
            .one_or_none()
        )
        if task_set_row is None or task_set_row["status"] != "frozen":
            raise InvalidState("run requires an existing frozen task set")
        config_document_value = config_row["document"]
        sampling = (
            config_document_value.get("sampling")
            if isinstance(config_document_value, dict)
            else None
        )
        if (
            not isinstance(sampling, dict)
            or sampling.get("task_set_digest") != task_set_row["digest"]
            or sampling.get("samples_per_task") != samples_per_task
            or sampling.get("master_seed") != master_seed
        ):
            raise InvalidState("run request does not match its immutable run configuration")
        revision_row = connection.execute(
            select(model_revision.c.id).where(model_revision.c.id == model_revision_id)
        ).first()
        if revision_row is None:
            raise InvalidReference("model revision does not exist")
        members = (
            connection.execute(
                select(task_version.c.id, task_version.c.digest)
                .select_from(
                    task_set_member.join(
                        task_version, task_set_member.c.task_version_id == task_version.c.id
                    )
                )
                .where(task_set_member.c.task_set_id == task_set_id)
                .order_by(task_version.c.id)
            )
            .mappings()
            .all()
        )
        if not members:
            raise InvalidState("run requires a non-empty frozen task set")
        attempt_count = len(members) * samples_per_task
        if attempt_count > MAX_ATTEMPTS_PER_RUN:
            raise InvalidState("requested run exceeds the configured attempt creation limit")

        run_id = uuid4()
        connection.execute(
            insert(run).values(
                id=run_id,
                campaign_id=campaign_id,
                config_document_id=config_id,
                task_set_id=task_set_id,
                model_revision_id=model_revision_id,
                status="queued",
                created_by=subject_id,
            )
        )
        attempt_ids: list[UUID] = []
        attempt_rows: list[dict[str, object]] = []
        for member in members:
            for sample_index in range(samples_per_task):
                attempt_id = uuid4()
                seed = derive_sample_seed(master_seed, member["digest"], sample_index)
                attempt_ids.append(attempt_id)
                attempt_rows.append(
                    {
                        "id": attempt_id,
                        "run_id": run_id,
                        "task_version_id": member["id"],
                        "sample_index": sample_index,
                        "seed": Decimal(seed),
                        "state": "queued",
                    }
                )
        connection.execute(insert(attempt), attempt_rows)
        return run_id, attempt_ids

    def update_campaign_status(
        self,
        campaign_id: UUID,
        *,
        expected_version: int,
        new_status: str,
        actor_subject: str,
        request_id: str,
    ) -> int:
        try:
            with self._engine.begin() as connection:
                updated = connection.execute(
                    update(campaign)
                    .where(campaign.c.id == campaign_id, campaign.c.row_version == expected_version)
                    .values(status=new_status, row_version=expected_version + 1)
                    .returning(campaign.c.row_version)
                ).scalar_one_or_none()
                if updated is None:
                    raise OptimisticVersionConflict()
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=actor_subject,
                        action="campaign.status.update",
                        resource_type="campaign",
                        resource_id=str(campaign_id),
                        request_id=request_id,
                        before_digest=None,
                        after_digest=None,
                        details={"new_status": new_status, "row_version": updated},
                    )
                )
                return int(updated)
        except OptimisticVersionConflict:
            raise
        except DBAPIError as error:
            mapped = map_database_error(error)
            raise mapped from None
