"""Atomic run/attempt creation and idempotency using PostgreSQL transactions."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    IdempotencyConflict,
    InvalidReference,
    InvalidState,
    OptimisticVersionConflict,
    PersistenceConflict,
    PersistenceUnavailable,
)
from polycodebench_core.canonical import canonical_digest, sha256_bytes
from polycodebench_core.identity import derive_sample_seed
from polycodebench_core.model_planning import ModelConfig, cost_bound
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    attempt,
    audit_event,
    audit_run,
    budget_account,
    budget_resource,
    campaign,
    config_document,
    endpoint_registration,
    idempotency_record,
    model_revision,
    run,
    stage_job,
    stage_job_event,
    task_set,
    task_set_member,
    task_version,
)

IDEMPOTENCY_TTL = timedelta(days=7)
MAX_ATTEMPTS_PER_RUN = 100_000


def _logical_key(scope_type: str, scope_id: UUID, key: str) -> str:
    return sha256_bytes(f"{scope_type}:{scope_id}:{key}".encode("ascii"))


def _task_resource_class(document: object) -> str:
    """Route a solve job to the resource class frozen into its task version."""
    runtime = document.get("runtime") if isinstance(document, Mapping) else None
    resource_class = runtime.get("resource_class") if isinstance(runtime, Mapping) else None
    if not isinstance(resource_class, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{0,63}", resource_class
    ):
        raise InvalidState("frozen task has no valid runtime resource class")
    return resource_class


class PostgresRunRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        database_role: Literal["pcb_operator", "pcb_submission_approver"] | None = None,
    ) -> None:
        if database_role not in {None, "pcb_operator", "pcb_submission_approver"}:
            raise ValueError("unsupported database role for run repository")
        self._engine = engine
        self._database_role = database_role

    def get_run_summary(self, run_id: UUID) -> Mapping[str, object] | None:
        """Return only lifecycle counters and budget state suitable for the owning submitter."""
        with self._engine.connect() as connection:
            self._set_database_role(connection)
            status = connection.execute(
                select(run.c.status).where(run.c.id == run_id)
            ).scalar_one_or_none()
            if status is None:
                return None
            attempt_counts = {
                str(state): int(count)
                for state, count in connection.execute(
                    select(attempt.c.state, func.count())
                    .where(attempt.c.run_id == run_id)
                    .group_by(attempt.c.state)
                ).all()
            }
            solve_job_counts = {
                str(state): int(count)
                for state, count in connection.execute(
                    select(stage_job.c.state, func.count())
                    .select_from(stage_job.join(attempt, stage_job.c.attempt_id == attempt.c.id))
                    .where(attempt.c.run_id == run_id, stage_job.c.stage == "solve")
                    .group_by(stage_job.c.state)
                ).all()
            }
            budget = (
                connection.execute(
                    select(
                        budget_account.c.hard_limit_micro_usd,
                        budget_account.c.spent_confirmed,
                        budget_account.c.reserved_open,
                        budget_account.c.uncertain_committed,
                    ).where(
                        budget_account.c.scope_kind == "run",
                        budget_account.c.scope_id == str(run_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
        result: dict[str, object] = {
            "status": str(status),
            "attempt_counts": attempt_counts,
            "solve_job_counts": solve_job_counts,
        }
        if budget is not None:
            result["budget_micro_usd"] = {
                "limit": int(budget["hard_limit_micro_usd"]),
                "spent": int(budget["spent_confirmed"]),
                "reserved": int(budget["reserved_open"]),
                "uncertain": int(budget["uncertain_committed"]),
            }
        return result

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
                self._set_database_role(connection)
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

    def _set_database_role(self, connection: Connection) -> None:
        if self._database_role is not None:
            # The value is constrained to this module's fixed compile-time role allowlist.
            connection.exec_driver_sql(f"SET LOCAL ROLE {self._database_role}")

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
        requested_purpose = request.get("purpose")
        purpose = "representative" if requested_purpose is None else requested_purpose
        max_cost = request.get("max_cost_micro_usd")
        if not isinstance(purpose, str) or purpose not in {
            "representative",
            "challenge",
            "audit_diagnostic",
        }:
            raise InvalidState("run purpose is invalid")
        raw_audit_run_id = request.get("audit_run_id")
        audit_run_id = UUID(str(raw_audit_run_id)) if raw_audit_run_id is not None else None
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
        audit_budget_account_id: UUID | None = None
        if purpose == "audit_diagnostic":
            if audit_run_id is None:
                raise InvalidState("audit diagnostic runs require approved audit metadata")
            approved_audit = (
                connection.execute(
                    select(audit_run)
                    .where(audit_run.c.id == audit_run_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                approved_audit is None
                or not approved_audit["dispatch_authorized"]
                or approved_audit["state"] not in {"planned", "queued"}
                or approved_audit["campaign_id"] != campaign_id
            ):
                raise InvalidState(
                    "audit diagnostic run lacks approved campaign-bound audit metadata"
                )
            account = (
                connection.execute(
                    select(
                        budget_account.c.id,
                        budget_account.c.parent_account_id,
                        budget_account.c.hard_limit_micro_usd,
                    ).where(
                        budget_account.c.scope_kind == "audit_run",
                        budget_account.c.scope_id == str(audit_run_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                account is None
                or account["parent_account_id"] != campaign_row["budget_account_id"]
            ):
                raise InvalidState("audit diagnostic run requires a campaign-parented audit budget")
            if max_cost is not None and max_cost > account["hard_limit_micro_usd"]:
                raise InvalidState("diagnostic run cap exceeds the frozen audit budget")
            audit_budget_account_id = cast(UUID, account["id"])
        elif audit_run_id is not None:
            raise InvalidState("only audit diagnostic runs may reference an audit run")
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
                select(task_version.c.id, task_version.c.digest, task_version.c.document)
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
        requested_max_attempts = request.get("max_attempts", MAX_ATTEMPTS_PER_RUN)
        if (
            not isinstance(requested_max_attempts, int)
            or isinstance(requested_max_attempts, bool)
            or requested_max_attempts < 1
            or requested_max_attempts > MAX_ATTEMPTS_PER_RUN
            or attempt_count > requested_max_attempts
        ):
            raise InvalidState("requested run exceeds the configured attempt creation limit")

        if purpose == "audit_diagnostic" and max_cost is None:
            raise InvalidState("audit diagnostic runs require explicit cost and token caps")
        if max_cost is not None:
            PostgresRunRepository._validate_approved_submission_plan(
                connection,
                campaign_row=cast(Mapping[str, object], campaign_row),
                run_config=config_document_value,
                revision_id=model_revision_id,
                endpoint_id=UUID(str(request["endpoint_registration_id"])),
                max_cost_micro_usd=max_cost,
                max_input_tokens=request["max_input_tokens"],
                max_output_tokens=request["max_output_tokens"],
            )

        run_id = uuid4()
        connection.execute(
            insert(run).values(
                id=run_id,
                campaign_id=campaign_id,
                config_document_id=config_id,
                task_set_id=task_set_id,
                model_revision_id=model_revision_id,
                audit_run_id=audit_run_id,
                purpose=purpose,
                status="queued",
                created_by=subject_id,
            )
        )
        if max_cost is not None:
            campaign_budget_id = campaign_row["budget_account_id"]
            connection.execute(
                insert(budget_account).values(
                    id=uuid4(),
                    scope_kind="run",
                    scope_id=str(run_id),
                    parent_account_id=audit_budget_account_id or campaign_budget_id,
                    hard_limit_micro_usd=max_cost,
                )
            )
            run_budget_id = connection.execute(
                select(budget_account.c.id).where(
                    budget_account.c.scope_kind == "run",
                    budget_account.c.scope_id == str(run_id),
                )
            ).scalar_one()
            connection.execute(
                insert(budget_resource),
                [
                    {
                        "account_id": run_budget_id,
                        "resource": "input_tokens",
                        "hard_limit": request["max_input_tokens"],
                    },
                    {
                        "account_id": run_budget_id,
                        "resource": "output_tokens",
                        "hard_limit": request["max_output_tokens"],
                    },
                ],
            )
        attempt_ids: list[UUID] = []
        attempt_rows: list[dict[str, object]] = []
        solve_job_rows: list[dict[str, object]] = []
        solve_job_events: list[dict[str, object]] = []
        provider_key = str(request.get("endpoint_registration_id") or "system")
        for member in members:
            resource_class = _task_resource_class(member["document"])
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
                logical_key = _logical_key("attempt", attempt_id, "solve")
                input_digest = canonical_digest(
                    {
                        "kind": "solve_job_input",
                        "schema_version": 1,
                        "attempt_id": str(attempt_id),
                        "run_config_digest": config_row["digest"],
                        "task_version_id": str(member["id"]),
                        "task_version_digest": member["digest"],
                        "sample_index": sample_index,
                        "sample_seed": str(seed),
                    }
                )
                job_id = uuid4()
                solve_job_rows.append(
                    {
                        "id": job_id,
                        "attempt_id": attempt_id,
                        "stage": "solve",
                        "shard_key": str(sample_index),
                        "input_digest": input_digest,
                        "logical_key": logical_key,
                        "state": "queued",
                        "required": True,
                        "queue_class": "solve",
                        "resource_class": resource_class,
                        "fairness_campaign_id": campaign_id,
                        "provider_key": provider_key,
                        "priority": 0,
                        "max_deliveries": 3,
                    }
                )
                solve_job_events.append(
                    {
                        "id": uuid4(),
                        "job_id": job_id,
                        "event_seq": 1,
                        "event_kind": "job_created",
                        "actor": subject_id,
                        "fence": None,
                        "details": {"stage": "solve", "logical_key": logical_key},
                    }
                )
        connection.execute(insert(attempt), attempt_rows)
        # Attempts and their first executable stage commit together. A queued run can
        # therefore never be visible without durable solve work for every sample.
        connection.execute(insert(stage_job), solve_job_rows)
        connection.execute(insert(stage_job_event), solve_job_events)
        return run_id, attempt_ids

    @staticmethod
    def _validate_approved_submission_plan(
        connection: Connection,
        *,
        campaign_row: Mapping[str, object],
        run_config: object,
        revision_id: UUID,
        endpoint_id: UUID,
        max_cost_micro_usd: object,
        max_input_tokens: object,
        max_output_tokens: object,
    ) -> None:
        """Bind a reviewed run to its exact approved endpoint and a strict capped model config."""
        if (
            not isinstance(max_cost_micro_usd, int)
            or isinstance(max_cost_micro_usd, bool)
            or not 1 <= max_cost_micro_usd <= 5_000_000
            or not isinstance(max_input_tokens, int)
            or isinstance(max_input_tokens, bool)
            or max_input_tokens < 0
            or not isinstance(max_output_tokens, int)
            or isinstance(max_output_tokens, bool)
            or max_output_tokens < 0
        ):
            raise InvalidState("submission run budget is invalid")
        campaign_budget_id = campaign_row.get("budget_account_id")
        if campaign_budget_id is None:
            raise InvalidState("submission campaign must have a budget account")
        campaign_account_exists = connection.execute(
            select(budget_account.c.hard_limit_micro_usd).where(
                budget_account.c.id == campaign_budget_id
            )
        ).scalar_one_or_none()
        if campaign_account_exists is None:
            raise InvalidState("submission campaign budget is unavailable")
        if not isinstance(run_config, dict):
            raise InvalidState("resolved run configuration is invalid")
        model_digest = run_config.get("model_config_digest")
        if not isinstance(model_digest, str):
            raise InvalidState("run configuration does not pin a model configuration")
        model_doc = connection.execute(
            select(config_document.c.document).where(
                config_document.c.kind == "model_config",
                config_document.c.digest == model_digest,
            )
        ).scalar_one_or_none()
        if not isinstance(model_doc, dict):
            raise InvalidState("pinned model configuration is unavailable")
        try:
            model_config = ModelConfig.model_validate_json(json.dumps(model_doc), strict=True)
            bound = cost_bound(
                model_config,
                model_config.declared_capabilities,
                request_bytes=0,
            )
        except (TypeError, ValueError):
            raise InvalidState("approved model configuration is invalid") from None
        if (
            not model_config.strict_money_cap
            or model_config.cost_policy != "provider_bound"
            or not bound.strict_cap_eligible
            or model_config.endpoint_id != endpoint_id
        ):
            raise InvalidState("approved model configuration is not strictly budgeted")
        revision = (
            connection.execute(select(model_revision).where(model_revision.c.id == revision_id))
            .mappings()
            .one_or_none()
        )
        if (
            revision is None
            or revision["provider"] != model_doc.get("provider_kind")
            or revision["name"] != model_doc.get("model")
            or revision["endpoint_registration_id"] != endpoint_id
        ):
            raise InvalidState("model revision does not match the approved configuration")
        endpoint_status = connection.execute(
            select(endpoint_registration.c.approval_status).where(
                endpoint_registration.c.id == endpoint_id
            )
        ).scalar_one_or_none()
        if endpoint_status != "approved":
            raise InvalidState("approved model endpoint is unavailable")

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
