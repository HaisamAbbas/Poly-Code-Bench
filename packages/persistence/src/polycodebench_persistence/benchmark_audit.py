"""Immutable audit-document storage and atomic audit-query budget/enqueue operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidReference,
    InvalidState,
    OptimisticVersionConflict,
    PersistenceConflict,
)
from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    AuditDocumentRef,
    AuditRunState,
    ImmutableArtifactRef,
    audit_document_digest,
    validate_audit_transition,
)
from polycodebench_core.canonical import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    artifact,
    audit_document,
    audit_event,
    audit_query,
    audit_run,
    budget_account,
    campaign,
    stage_job,
    stage_job_event,
)


def _frozen_limit(limits: Any, canonical: str, configured: str) -> int | None:
    """Read a plan limit under either the contract or catalog configuration name."""
    if not isinstance(limits, dict):
        return None
    values = [limits[key] for key in (canonical, configured) if key in limits]
    if not values or any(type(value) is not int or value < 0 for value in values):
        return None
    if len(values) == 2 and values[0] != values[1]:
        return None
    return cast(int, values[0])


class AuditQueryEnqueueSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    query_index: int = Field(ge=0, le=9_007_199_254_740_991)
    query_document_id: UUID
    logical_call_key: str = Field(min_length=1, max_length=255)
    reserved_units: int = Field(ge=1, le=9_007_199_254_740_991)


@dataclass(frozen=True)
class AuditDocumentWrite:
    document_id: UUID
    digest: str
    created: bool


@dataclass(frozen=True)
class AuditRunWrite:
    audit_run_id: UUID
    created: bool
    row_version: int


@dataclass(frozen=True)
class AuditQueryEnqueue:
    job_ids: tuple[UUID, ...]
    created: bool
    row_version: int


class PostgresBenchmarkAuditRepository:
    """PostgreSQL audit persistence; no method dispatches a remote source or model call."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save_document(self, document: AuditDocument) -> AuditDocumentWrite:
        """Insert immutable semantic bytes, enforcing references and successor kind."""
        payload = document.payload.model_dump(mode="json")
        canonical_json_bytes(payload)
        digest = audit_document_digest(document)
        try:
            with self._engine.begin() as connection:
                self._validate_references(connection, document.payload)
                existing = (
                    connection.execute(
                        select(audit_document).where(audit_document.c.id == document.id)
                    )
                    .mappings()
                    .one_or_none()
                )
                expected = {
                    "kind": document.kind,
                    "schema_version": document.schema_version,
                    "semantic_digest": digest,
                    "payload": payload,
                    "supersedes_id": document.supersedes_id,
                    "created_by": document.metadata.actor,
                    "document_created_at": document.metadata.created_at,
                    "timestamp_precision": document.metadata.timestamp_precision,
                    "trace_id": document.metadata.trace_id,
                    "document_row_version": document.metadata.row_version,
                }
                if existing is not None:
                    if any(existing[key] != value for key, value in expected.items()):
                        raise PersistenceConflict(
                            "audit document ID is already bound to other bytes"
                        )
                    return AuditDocumentWrite(document.id, digest, False)

                same_content = connection.execute(
                    select(audit_document.c.id).where(
                        audit_document.c.kind == document.kind,
                        audit_document.c.semantic_digest == digest,
                    )
                ).scalar_one_or_none()
                if same_content is not None:
                    raise PersistenceConflict(
                        "semantic audit document bytes already have another ID"
                    )
                if document.supersedes_id is not None:
                    predecessor = (
                        connection.execute(
                            select(audit_document.c.kind).where(
                                audit_document.c.id == document.supersedes_id
                            )
                        )
                        .scalar_one_or_none()
                    )
                    if predecessor is None:
                        raise InvalidReference("audit successor points to a missing document")
                    if predecessor != document.kind:
                        raise InvalidState("audit successor kind must match its predecessor")
                connection.execute(insert(audit_document).values(id=document.id, **expected))
                return AuditDocumentWrite(document.id, digest, True)
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def create_audit_run(
        self,
        *,
        plan_document_id: UUID,
        idempotency_key: str,
        reserved_query_units: int,
        reserved_storage_bytes: int,
        actor: str,
        campaign_id: UUID | None = None,
    ) -> AuditRunWrite:
        """Atomically create a blocked-by-default run and its zero-dollar budget account."""
        if not idempotency_key or len(idempotency_key) > 255 or not idempotency_key.isascii():
            raise InvalidState("audit run idempotency key must be bounded non-empty ASCII")
        if (
            type(reserved_query_units) is not int
            or reserved_query_units < 0
            or type(reserved_storage_bytes) is not int
            or reserved_storage_bytes < 0
        ):
            raise InvalidState("audit run reservations must be nonnegative integers")
        if not actor or not actor.isascii() or len(actor) > 255:
            raise InvalidState("audit run actor is invalid")
        try:
            with self._engine.begin() as connection:
                plan = (
                    connection.execute(
                        select(audit_document)
                        .where(audit_document.c.id == plan_document_id)
                        .with_for_update(read=True)
                    )
                    .mappings()
                    .one_or_none()
                )
                if plan is None or plan["kind"] != "audit_plan":
                    raise InvalidReference("audit run requires a stored audit_plan document")
                limits = plan["payload"].get("limits", {})
                query_limit = _frozen_limit(
                    limits, "max_query_units", "max_query_units_per_plan"
                )
                storage_limit = _frozen_limit(
                    limits, "max_storage_bytes", "max_storage_bytes_per_plan"
                )
                diagnostic_cost_limit = _frozen_limit(
                    limits,
                    "max_diagnostic_cost_micro_usd",
                    "max_diagnostic_cost_micro_usd_per_plan",
                )
                if not isinstance(query_limit, int) or reserved_query_units > query_limit:
                    raise InvalidState("audit query reservation exceeds the frozen plan limit")
                if not isinstance(storage_limit, int) or reserved_storage_bytes > storage_limit:
                    raise InvalidState("audit storage reservation exceeds the frozen plan limit")
                if campaign_id is not None:
                    campaign_row = (
                        connection.execute(
                            select(campaign.c.budget_account_id, campaign.c.status)
                            .where(campaign.c.id == campaign_id)
                            .with_for_update(read=True)
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if campaign_row is None or campaign_row["status"] not in {"draft", "planned"}:
                        raise InvalidReference("audit campaign is unavailable")
                    campaign_budget_id = campaign_row["budget_account_id"]
                else:
                    campaign_budget_id = None

                existing = (
                    connection.execute(
                        select(audit_run)
                        .where(
                            audit_run.c.plan_document_id == plan_document_id,
                            audit_run.c.idempotency_key == idempotency_key,
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    if any(
                        existing[key] != value
                        for key, value in (
                            ("campaign_id", campaign_id),
                            ("reserved_query_units", reserved_query_units),
                            ("reserved_storage_bytes", reserved_storage_bytes),
                        )
                    ):
                        raise PersistenceConflict(
                            "audit run replay differs from its frozen reservations"
                        )
                    return AuditRunWrite(existing["id"], False, existing["row_version"])

                run_id = uuid4()
                connection.execute(
                    insert(audit_run).values(
                        id=run_id,
                        plan_document_id=plan_document_id,
                        campaign_id=campaign_id,
                        idempotency_key=idempotency_key,
                        state="planned",
                        dispatch_authorized=False,
                        reserved_query_units=reserved_query_units,
                        reserved_storage_bytes=reserved_storage_bytes,
                        row_version=0,
                    )
                )
                connection.execute(
                    insert(budget_account).values(
                        id=uuid4(),
                        scope_kind="audit_run",
                        scope_id=str(run_id),
                        parent_account_id=campaign_budget_id,
                        hard_limit_micro_usd=diagnostic_cost_limit or 0,
                    )
                )
                connection.execute(
                    insert(audit_event).values(
                        actor_subject=actor,
                        action="benchmark_audit.run.create",
                        resource_type="audit_run",
                        resource_id=str(run_id),
                        after_digest=plan["semantic_digest"],
                        request_id=f"audit-run-{run_id}",
                        details={
                            "plan_document_id": str(plan_document_id),
                            "reserved_query_units": reserved_query_units,
                            "reserved_storage_bytes": reserved_storage_bytes,
                            "dispatch_authorized": False,
                        },
                    )
                )
                return AuditRunWrite(run_id, True, 0)
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def transition_audit_run(
        self,
        *,
        audit_run_id: UUID,
        expected_row_version: int,
        target_state: AuditRunState,
        actor: str,
    ) -> int:
        """CAS a legal run transition without changing its plan, reservations or scope."""
        if expected_row_version < 0 or not actor or not actor.isascii() or len(actor) > 255:
            raise InvalidState("audit transition identity is invalid")
        try:
            with self._engine.begin() as connection:
                current = (
                    connection.execute(
                        select(audit_run.c.state, audit_run.c.dispatch_authorized)
                        .where(audit_run.c.id == audit_run_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None:
                    raise InvalidReference("audit run does not exist")
                validate_audit_transition("run", current["state"], target_state)
                if target_state == "queued" and not current["dispatch_authorized"]:
                    raise InvalidState(
                        "audit run cannot queue without explicit dispatch authorization"
                    )
                changed = connection.execute(
                    update(audit_run)
                    .where(
                        audit_run.c.id == audit_run_id,
                        audit_run.c.row_version == expected_row_version,
                    )
                    .values(
                        state=target_state,
                        row_version=audit_run.c.row_version + 1,
                    )
                ).rowcount
                if changed != 1:
                    raise OptimisticVersionConflict("audit run changed during transition")
                return expected_row_version + 1
        except (InvalidReference, InvalidState, OptimisticVersionConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def enqueue_queries(
        self,
        *,
        audit_run_id: UUID,
        expected_row_version: int,
        queries: tuple[AuditQueryEnqueueSpec, ...],
        actor: str,
    ) -> AuditQueryEnqueue:
        """Reserve finite query units and create query rows/jobs in one transaction."""
        if not queries or not actor or not actor.isascii() or len(actor) > 255:
            raise InvalidState("audit query enqueue requires queries and an actor")
        if len({query.query_index for query in queries}) != len(queries):
            raise InvalidState("audit query indexes must be unique")
        if len({query.logical_call_key for query in queries}) != len(queries):
            raise InvalidState("audit logical call keys must be unique")
        ordered = tuple(sorted(queries, key=lambda query: query.query_index))
        try:
            with self._engine.begin() as connection:
                run_row = (
                    connection.execute(
                        select(audit_run)
                        .where(audit_run.c.id == audit_run_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if run_row is None:
                    raise InvalidReference("audit run does not exist")
                existing = (
                    connection.execute(
                        select(audit_query)
                        .where(audit_query.c.audit_run_id == audit_run_id)
                        .order_by(audit_query.c.query_index)
                    )
                    .mappings()
                    .all()
                )
                if existing:
                    expected = [
                        (
                            query.query_index,
                            query.query_document_id,
                            query.logical_call_key,
                            query.reserved_units,
                        )
                        for query in ordered
                    ]
                    actual = [
                        (
                            row["query_index"],
                            row["query_document_id"],
                            row["logical_call_key"],
                            row["reserved_units"],
                        )
                        for row in existing
                    ]
                    if expected != actual:
                        raise PersistenceConflict("audit query enqueue replay changed its plan")
                    jobs = (
                        connection.execute(
                            select(stage_job.c.id, stage_job.c.shard_key)
                            .where(stage_job.c.audit_run_id == audit_run_id)
                            .order_by(stage_job.c.shard_key)
                        )
                        .all()
                    )
                    jobs_by_index = {int(job.shard_key): job.id for job in jobs}
                    if len(jobs_by_index) != len(existing) or set(jobs_by_index) != {
                        row["query_index"] for row in existing
                    }:
                        raise PersistenceConflict(
                            "audit query rows and queued jobs are incomplete or inconsistent"
                        )
                    return AuditQueryEnqueue(
                        tuple(jobs_by_index[row["query_index"]] for row in existing),
                        False,
                        run_row["row_version"],
                    )

                if run_row["state"] != "queued" or not run_row["dispatch_authorized"]:
                    raise InvalidState("audit queries require an authorized queued audit run")
                if run_row["row_version"] != expected_row_version:
                    raise OptimisticVersionConflict("audit run row version changed")
                reserved = sum(query.reserved_units for query in ordered)
                if reserved > run_row["reserved_query_units"]:
                    raise InvalidState("query enqueue exceeds the atomic run reservation")
                plan_document_id = run_row["plan_document_id"]
                campaign_id = run_row["campaign_id"]
                plan_document = (
                    connection.execute(
                        select(audit_document.c.kind, audit_document.c.semantic_digest).where(
                            audit_document.c.id == plan_document_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if plan_document is None or plan_document["kind"] != "audit_plan":
                    raise InvalidReference("audit run plan document is missing")
                query_documents: dict[UUID, Any] = {}
                for query in ordered:
                    document = (
                        connection.execute(
                            select(audit_document)
                            .where(audit_document.c.id == query.query_document_id)
                            .with_for_update(read=True)
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if document is None or document["kind"] != "query_manifest":
                        raise InvalidReference("audit query requires a stored query_manifest")
                    query_audit_ref = document["payload"].get("audit_ref", {})
                    if (
                        query_audit_ref.get("document_id") != str(plan_document_id)
                        or query_audit_ref.get("kind") != plan_document["kind"]
                        or query_audit_ref.get("digest") != plan_document["semantic_digest"]
                    ):
                        raise InvalidState("query manifest is not bound to this audit plan")
                    query_documents[query.query_document_id] = document

                job_ids: list[UUID] = []
                query_rows: list[dict[str, object]] = []
                job_rows: list[dict[str, object]] = []
                event_rows: list[dict[str, object]] = []
                for query in ordered:
                    document = query_documents[query.query_document_id]
                    job_id = uuid4()
                    digest = hashlib.sha256(
                        f"audit_run:{audit_run_id}:query:{query.query_index}".encode("ascii")
                    ).hexdigest()
                    logical_job_key = f"sha256:{digest}"
                    job_ids.append(job_id)
                    query_rows.append(
                        {
                            "id": uuid4(),
                            "audit_run_id": audit_run_id,
                            "query_document_id": query.query_document_id,
                            "query_index": query.query_index,
                            "logical_call_key": query.logical_call_key,
                            "state": "queued",
                            "reserved_units": query.reserved_units,
                            "row_version": 0,
                        }
                    )
                    job_rows.append(
                        {
                            "id": job_id,
                            "audit_run_id": audit_run_id,
                            "stage": "audit_query",
                            "shard_key": str(query.query_index),
                            "input_digest": document["semantic_digest"],
                            "logical_key": logical_job_key,
                            "state": "queued",
                            "required": True,
                            "queue_class": "benchmark_audit",
                            "resource_class": "default",
                            "fairness_campaign_id": campaign_id,
                            "provider_key": "benchmark_audit",
                            "priority": 0,
                            "max_deliveries": 3,
                        }
                    )
                    event_rows.append(
                        {
                            "id": uuid4(),
                            "job_id": job_id,
                            "event_seq": 1,
                            "event_kind": "job_created",
                            "actor": actor,
                            "details": {
                                "stage": "audit_query",
                                "query_index": query.query_index,
                                "logical_key": logical_job_key,
                            },
                        }
                    )
                connection.execute(insert(audit_query), query_rows)
                connection.execute(insert(stage_job), job_rows)
                connection.execute(insert(stage_job_event), event_rows)
                changed = connection.execute(
                    update(audit_run)
                    .where(
                        audit_run.c.id == audit_run_id,
                        audit_run.c.row_version == expected_row_version,
                    )
                    .values(row_version=audit_run.c.row_version + 1)
                ).rowcount
                if changed != 1:
                    raise OptimisticVersionConflict("audit run row version changed during enqueue")
                return AuditQueryEnqueue(tuple(job_ids), True, expected_row_version + 1)
        except (InvalidReference, InvalidState, PersistenceConflict, OptimisticVersionConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    @staticmethod
    def _validate_references(connection: Any, payload: BaseModel) -> None:
        def visit(value: Any) -> None:
            if isinstance(value, AuditDocumentRef):
                row = (
                    connection.execute(
                        select(audit_document.c.kind, audit_document.c.semantic_digest).where(
                            audit_document.c.id == value.document_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    row is None
                    or row["kind"] != value.kind
                    or row["semantic_digest"] != value.digest
                ):
                    raise InvalidReference("audit document reference is missing or has changed")
            elif isinstance(value, ImmutableArtifactRef):
                row = (
                    connection.execute(
                        select(
                            artifact.c.content_digest,
                            artifact.c.media_type,
                            artifact.c.status,
                            artifact.c.visibility,
                        ).where(artifact.c.id == value.artifact_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                expected_visibility = {
                    "private": "hidden",
                    "restricted": "internal",
                    "public": "public",
                }[value.visibility]
                if (
                    row is None
                    or row["content_digest"] != value.digest
                    or row["media_type"] != value.media_type
                    or row["status"] != "verified"
                    or row["visibility"] != expected_visibility
                ):
                    raise InvalidReference("audit artifact reference is missing or not verified")
            elif isinstance(value, BaseModel):
                for field_value in value.__dict__.values():
                    visit(field_value)
            elif isinstance(value, dict):
                for field_value in value.values():
                    visit(field_value)
            elif isinstance(value, (tuple, list)):
                for field_value in value:
                    visit(field_value)

        visit(payload)
