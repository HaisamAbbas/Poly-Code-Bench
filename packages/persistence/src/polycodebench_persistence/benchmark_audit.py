"""Immutable audit-document storage and atomic audit-query budget/enqueue operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
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
    BehavioralAssessmentDocument,
    BehavioralAuditPlanDocumentV2,
    BehavioralMethodRegistryDocument,
    BehavioralObservationDocument,
    BehavioralTaskValidityDocument,
    CanaryObservationDocument,
    ImmutableArtifactRef,
    MatchEvidenceDocument,
    MatchEvidenceDocumentV2,
    MatchEvidencePayloadV2,
    ModelContextDocument,
    SealAccessEventDocument,
    SealedManifestDocumentV2,
    audit_document_digest,
    parse_audit_document,
    validate_audit_transition,
    validate_sealed_manifest_transition,
)
from polycodebench_core.canonical import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    artifact,
    attempt,
    audit_document,
    audit_event,
    audit_query,
    audit_run,
    budget_account,
    call_delivery,
    call_intent,
    campaign,
    config_document,
    model_revision,
    run,
    stage_job,
    stage_job_event,
    task_version,
    usage_record,
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
        if isinstance(document, SealAccessEventDocument):
            raise InvalidState("sealed access events must be appended with a manifest successor")
        try:
            with self._engine.begin() as connection:
                return self._save_document_in_connection(connection, document)
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def append_access_event(
        self,
        event: SealAccessEventDocument,
        successor: SealedManifestDocumentV2,
    ) -> None:
        """Atomically append sealed access evidence and the non-regressing manifest head."""
        expected_event_ref = AuditDocumentRef(
            document_id=event.id,
            digest=audit_document_digest(event),
            kind="seal_access_event",
        )
        if (
            successor.supersedes_id != event.payload.manifest_ref.document_id
            or expected_event_ref not in successor.payload.access_event_refs
        ):
            raise InvalidState("sealed access event and manifest successor are not bound")
        try:
            with self._engine.begin() as connection:
                previous = (
                    connection.execute(
                        select(audit_document.c.kind, audit_document.c.semantic_digest)
                        .where(audit_document.c.id == event.payload.manifest_ref.document_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    previous is None
                    or previous["kind"] != "sealed_manifest"
                    or previous["semantic_digest"] != event.payload.manifest_ref.digest
                ):
                    raise InvalidReference("sealed access event does not bind the current manifest")
                existing_successor = connection.execute(
                    select(audit_document.c.id).where(
                        audit_document.c.kind == "sealed_manifest",
                        audit_document.c.supersedes_id == event.payload.manifest_ref.document_id,
                    )
                ).scalar_one_or_none()
                if existing_successor is not None and existing_successor != successor.id:
                    raise PersistenceConflict("sealed manifest head already has a successor")
                self._save_document_in_connection(connection, event, allow_access_event=True)
                self._save_document_in_connection(connection, successor)
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    @classmethod
    def _save_document_in_connection(
        cls,
        connection: Any,
        document: AuditDocument,
        *,
        allow_access_event: bool = False,
    ) -> AuditDocumentWrite:
        if isinstance(document, SealAccessEventDocument) and not allow_access_event:
            raise InvalidState("sealed access event requires an atomic manifest successor")
        payload = document.payload.model_dump(mode="json")
        canonical_json_bytes(payload)
        digest = audit_document_digest(document)
        cls._validate_references(connection, document.payload)
        cls._validate_canary_observation(connection, document)
        cls._validate_sealed_manifest_document(connection, document)
        existing = (
            connection.execute(select(audit_document).where(audit_document.c.id == document.id))
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
                raise PersistenceConflict("audit document ID is already bound to other bytes")
            return AuditDocumentWrite(document.id, digest, False)

        same_content = connection.execute(
            select(audit_document.c.id).where(
                audit_document.c.kind == document.kind,
                audit_document.c.semantic_digest == digest,
            )
        ).scalar_one_or_none()
        if same_content is not None:
            raise PersistenceConflict("semantic audit document bytes already have another ID")
        cls._validate_behavioral_document(connection, document)
        if document.supersedes_id is not None:
            predecessor = connection.execute(
                select(audit_document.c.kind).where(audit_document.c.id == document.supersedes_id)
            ).scalar_one_or_none()
            if predecessor is None:
                raise InvalidReference("audit successor points to a missing document")
            if predecessor != document.kind:
                raise InvalidState("audit successor kind must match its predecessor")
        connection.execute(insert(audit_document).values(id=document.id, **expected))
        return AuditDocumentWrite(document.id, digest, True)

    @classmethod
    def _validate_behavioral_document(cls, connection: Any, document: AuditDocument) -> None:
        if isinstance(document, BehavioralTaskValidityDocument):
            task_rows = (
                connection.execute(
                    select(task_version.c.id, task_version.c.family).where(
                        task_version.c.id.in_(
                            (
                                document.payload.original_task_ref.entity_id,
                                document.payload.control_task_ref.entity_id,
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )
            if len(task_rows) != 2 or task_rows[0]["family"] != task_rows[1]["family"]:
                raise InvalidReference(
                    "behavioral validity requires two stored task versions in one family"
                )
            return

        if isinstance(document, BehavioralAuditPlanDocumentV2):
            payload = document.payload
            registry = cls._document_by_ref(connection, payload.method_registry_ref)
            if not isinstance(registry, BehavioralMethodRegistryDocument):
                raise InvalidReference("behavioral plan method registry is missing")
            method = next(
                (item for item in registry.payload.methods if item.method_id == payload.method_id),
                None,
            )
            if method is None:
                raise InvalidReference("behavioral plan method is not present in its registry")
            if payload.statistical_test not in method.statistical_tests:
                raise InvalidState(
                    "behavioral plan statistical test is not registered for its method"
                )
            run_row = (
                connection.execute(
                    select(
                        audit_run.c.state,
                        audit_run.c.dispatch_authorized,
                    ).where(audit_run.c.id == payload.audit_run_ref.entity_id)
                )
                .mappings()
                .one_or_none()
            )
            if run_row is None:
                raise InvalidReference("behavioral plan requires its separately budgeted audit run")
            if run_row["state"] not in {"draft", "planned"} or run_row["dispatch_authorized"]:
                raise InvalidState("behavioral preregistration must precede audit-run dispatch")
            prior_intent = connection.execute(
                select(call_intent.c.id)
                .where(call_intent.c.diagnostic_audit_run_id == payload.audit_run_ref.entity_id)
                .limit(1)
            ).scalar_one_or_none()
            if prior_intent is not None:
                raise InvalidState("behavioral plan cannot be frozen after diagnostic calls exist")
            for pair in payload.sample_pairs:
                task_rows = (
                    connection.execute(
                        select(task_version.c.id, task_version.c.family).where(
                            task_version.c.id.in_(
                                (
                                    pair.original_task_ref.entity_id,
                                    pair.control_task_ref.entity_id,
                                )
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                if len(task_rows) != 2 or task_rows[0]["family"] != task_rows[1]["family"]:
                    raise InvalidReference(
                        "behavioral sample pair must identify two stored tasks in one family"
                    )
                validity = cls._document_by_ref(connection, pair.validity_ref)
                if not isinstance(validity, BehavioralTaskValidityDocument):
                    raise InvalidReference("behavioral pair validity evidence is missing")
                if (
                    validity.payload.validity_state != "accepted"
                    or validity.payload.original_task_ref != pair.original_task_ref
                    or validity.payload.control_task_ref != pair.control_task_ref
                    or validity.payload.family_ref != pair.family_ref
                ):
                    raise InvalidState(
                        "behavioral pair lacks accepted matching semantic/difficulty review"
                    )
            return

        if isinstance(document, BehavioralObservationDocument):
            cls._validate_behavioral_observation(connection, document)
            return

        if isinstance(document, BehavioralAssessmentDocument):
            cls._validate_behavioral_assessment(connection, document)

    @classmethod
    def _validate_behavioral_observation(
        cls,
        connection: Any,
        document: BehavioralObservationDocument,
    ) -> None:
        payload = document.payload
        plan = cls._document_by_ref(connection, payload.plan_ref)
        if not isinstance(plan, BehavioralAuditPlanDocumentV2):
            raise InvalidReference("behavioral observations require a frozen v2 plan")
        plan_row = (
            connection.execute(
                select(audit_document.c.created_at).where(audit_document.c.id == plan.id)
            )
            .mappings()
            .one_or_none()
        )
        if plan_row is None:
            raise InvalidReference("behavioral preregistration row is missing")
        pair = next(
            (item for item in plan.payload.sample_pairs if item.pair_id == payload.pair_id),
            None,
        )
        slot = next(
            (
                item
                for item in plan.payload.model_slots
                if item.model_context_ref == payload.model_context_ref
            ),
            None,
        )
        if pair is None or slot is None:
            raise InvalidReference("behavioral observation task/model was not preregistered")
        expected_task = (
            pair.original_task_ref if payload.sample_role == "original" else pair.control_task_ref
        )
        if payload.task_ref != expected_task:
            raise InvalidState("behavioral observation differs from its frozen task assignment")
        if document.supersedes_id is not None:
            predecessor_row = (
                connection.execute(
                    select(audit_document)
                    .where(audit_document.c.id == document.supersedes_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if predecessor_row is None:
                raise InvalidReference("behavioral observation predecessor is missing")
            predecessor = cls._document_from_row(predecessor_row)
            if not isinstance(predecessor, BehavioralObservationDocument):
                raise InvalidReference(
                    "behavioral observation successor must follow an observation"
                )
            old = predecessor.payload
            if (
                old.dispatch_state != "ambiguous"
                or old.plan_ref != payload.plan_ref
                or old.pair_id != payload.pair_id
                or old.task_ref != payload.task_ref
                or old.sample_role != payload.sample_role
                or old.model_context_ref != payload.model_context_ref
                or old.call_intent_ref is None
                or payload.call_intent_ref != old.call_intent_ref
                or payload.request_digest != old.request_digest
                or payload.access_event_refs[: len(old.access_event_refs)] != old.access_event_refs
            ):
                raise InvalidState(
                    "behavioral successors may only resolve ambiguity without changing frozen scope"
                )

        if payload.dispatch_state == "not_dispatched":
            if payload.call_intent_ref is not None:
                raise InvalidState(
                    "undispatched diagnostic observations cannot have delivery evidence"
                )
            if payload.access_event_refs:
                denied_event = cls._document_by_ref(connection, payload.access_event_refs[0])
                if not isinstance(denied_event, SealAccessEventDocument):
                    raise InvalidReference("denied diagnostic access must be a sealed access event")
                event = denied_event.payload
                if (
                    event.operation != "remote_delivery"
                    or event.outcome != "denied"
                    or event.exposure != "none"
                    or event.recipient != slot.recipient
                    or event.payload_digest != payload.request_digest
                ):
                    raise InvalidState(
                        "blocked diagnostic event does not record an exact denied request"
                    )
            elif payload.request_digest is not None:
                raise InvalidState("diagnostic request digest requires its denied access event")
            return

        registry = cls._document_by_ref(connection, plan.payload.method_registry_ref)
        if not isinstance(registry, BehavioralMethodRegistryDocument):
            raise InvalidReference("diagnostic plan method registry is missing")
        method = next(
            (item for item in registry.payload.methods if item.method_id == plan.payload.method_id),
            None,
        )
        if (
            method is None
            or method.implementation_state != "available"
            or method.implementation_ref is None
            or plan.payload.statistical_test not in method.statistical_tests
        ):
            raise InvalidState("unsupported behavioral methods cannot dispatch diagnostic requests")
        model_context = cls._document_by_ref(connection, payload.model_context_ref)
        if not isinstance(model_context, ModelContextDocument):
            raise InvalidReference("behavioral model context is missing")
        if (
            not slot.stable_revision_verified
            or model_context.payload.pin_confidence != "verified_revision"
            or model_context.payload.model_revision is None
        ):
            raise InvalidState(
                "behavioral model calls require an independently pinned model revision"
            )

        assert payload.call_intent_ref is not None
        assert payload.request_digest is not None
        if not payload.access_event_refs:
            raise InvalidReference("diagnostic dispatch requires sealed access events")
        intent_row = (
            connection.execute(
                select(
                    call_intent.c.diagnostic_audit_run_id,
                    call_intent.c.request_digest,
                    call_intent.c.state,
                    call_intent.c.created_at,
                    call_intent.c.model_config_id,
                    call_intent.c.request_artifact_id,
                    attempt.c.task_version_id,
                    run.c.purpose,
                    run.c.audit_run_id,
                    run.c.config_document_id,
                    model_revision.c.provider,
                    model_revision.c.immutable_revision,
                    audit_run.c.dispatch_authorized,
                )
                .select_from(
                    call_intent.join(
                        audit_run, audit_run.c.id == call_intent.c.diagnostic_audit_run_id
                    )
                    .join(attempt, attempt.c.id == call_intent.c.attempt_id)
                    .join(run, run.c.id == attempt.c.run_id)
                    .join(model_revision, model_revision.c.id == run.c.model_revision_id)
                )
                .where(call_intent.c.id == payload.call_intent_ref.entity_id)
            )
            .mappings()
            .one_or_none()
        )
        if (
            intent_row is None
            or intent_row["diagnostic_audit_run_id"] != plan.payload.audit_run_ref.entity_id
            or intent_row["request_digest"] != payload.request_digest
            or intent_row["dispatch_authorized"] is not True
            or intent_row["created_at"] < plan_row["created_at"]
            or intent_row["task_version_id"] != payload.task_ref.entity_id
            or intent_row["purpose"] != "audit_diagnostic"
            or intent_row["audit_run_id"] != plan.payload.audit_run_ref.entity_id
            or intent_row["model_config_id"] != intent_row["config_document_id"]
            or intent_row["provider"] != model_context.payload.provider
            or intent_row["immutable_revision"] != model_context.payload.model_revision
        ):
            raise InvalidReference(
                "diagnostic gateway intent is unauthorized or mismatched to its frozen task/model"
            )
        decoding_config = (
            connection.execute(
                select(
                    config_document.c.canonical_artifact_id,
                    artifact.c.content_digest,
                    artifact.c.status,
                    artifact.c.visibility,
                )
                .select_from(
                    config_document.join(
                        artifact, artifact.c.id == config_document.c.canonical_artifact_id
                    )
                )
                .where(config_document.c.id == intent_row["model_config_id"])
            )
            .mappings()
            .one_or_none()
        )
        if (
            decoding_config is None
            or decoding_config["canonical_artifact_id"]
            != plan.payload.decoding_artifact_ref.artifact_id
            or decoding_config["content_digest"] != plan.payload.decoding_artifact_ref.digest
            or decoding_config["status"] != "verified"
            or decoding_config["visibility"] != "hidden"
        ):
            raise InvalidReference(
                "diagnostic gateway decoding config differs from preregistration"
            )
        request_artifact = (
            connection.execute(
                select(
                    artifact.c.content_digest,
                    artifact.c.status,
                    artifact.c.visibility,
                ).where(artifact.c.id == intent_row["request_artifact_id"])
            )
            .mappings()
            .one_or_none()
        )
        if (
            request_artifact is None
            or request_artifact["content_digest"] != payload.request_digest
            or request_artifact["status"] != "verified"
            or request_artifact["visibility"] != "hidden"
        ):
            raise InvalidReference("diagnostic request bytes must be verified and privately stored")

        for access_ref in payload.access_event_refs:
            access_event = cls._document_by_ref(connection, access_ref)
            if not isinstance(access_event, SealAccessEventDocument):
                raise InvalidReference("diagnostic dispatch requires sealed exposure events")
            event = access_event.payload
            if (
                event.operation != "remote_delivery"
                or event.outcome != "authorized"
                or event.exposure != "authorized_disclosure"
                or event.recipient != slot.recipient
                or event.payload_digest != payload.request_digest
            ):
                raise InvalidState(
                    "sealed exposure event does not match the authorized diagnostic request"
                )

        latest_usage = (
            select(
                usage_record.c.delivery_id,
                func.max(usage_record.c.settlement_revision).label("revision"),
            )
            .group_by(usage_record.c.delivery_id)
            .subquery()
        )
        delivery_rows = (
            connection.execute(
                select(
                    call_delivery.c.status,
                    call_delivery.c.raw_response_artifact_id,
                    call_delivery.c.normalized_response_artifact_id,
                    usage_record.c.input_tokens,
                    usage_record.c.output_tokens,
                    usage_record.c.actual_cost_micro_usd,
                    usage_record.c.estimated_cost_micro_usd,
                )
                .select_from(
                    call_delivery.outerjoin(
                        latest_usage, latest_usage.c.delivery_id == call_delivery.c.id
                    ).outerjoin(
                        usage_record,
                        (usage_record.c.delivery_id == latest_usage.c.delivery_id)
                        & (usage_record.c.settlement_revision == latest_usage.c.revision),
                    )
                )
                .where(call_delivery.c.intent_id == payload.call_intent_ref.entity_id)
                .order_by(call_delivery.c.delivery_index)
            )
            .mappings()
            .all()
        )
        if not delivery_rows:
            raise InvalidState("diagnostic gateway intent has no durable delivery record")
        if len(payload.access_event_refs) != len(delivery_rows):
            raise InvalidState("every diagnostic gateway delivery requires its own access event")
        delivery_states = {row["status"] for row in delivery_rows}
        has_uncertain_delivery = bool(delivery_states & {"ambiguous", "dispatching"})
        if payload.dispatch_state == "ambiguous" and not has_uncertain_delivery:
            raise InvalidState("ambiguous diagnostic observations require an uncertain delivery")
        if payload.dispatch_state == "authorized_dispatched" and (
            has_uncertain_delivery or not delivery_states & {"responded", "failed"}
        ):
            raise InvalidState(
                "final diagnostic dispatch status does not reconcile to delivery records"
            )
        if payload.outcome == "completed":
            if len([row for row in delivery_rows if row["status"] == "responded"]) != 1:
                raise InvalidState("completed diagnostic observation requires one settled response")
        responded = [row for row in delivery_rows if row["status"] == "responded"]
        if payload.response_artifact_ref is not None:
            response_ids = {
                artifact_id
                for row in delivery_rows
                if row["status"] == "responded"
                for artifact_id in (
                    row["raw_response_artifact_id"],
                    row["normalized_response_artifact_id"],
                )
                if artifact_id is not None
            }
            if payload.response_artifact_ref.artifact_id not in response_ids:
                raise InvalidReference(
                    "behavioral response does not match gateway response storage"
                )
        elif responded:
            raise InvalidReference(
                "received diagnostic responses cannot be omitted from the ledger"
            )

        costs = [
            row["actual_cost_micro_usd"]
            if row["actual_cost_micro_usd"] is not None
            else row["estimated_cost_micro_usd"]
            for row in delivery_rows
        ]
        cost_known = all(cost is not None for cost in costs)
        expected_cost = sum(cost for cost in costs if cost is not None) if cost_known else None
        expected_cost_state = (
            "unavailable"
            if not cost_known
            else "actual"
            if all(row["actual_cost_micro_usd"] is not None for row in delivery_rows)
            else "estimated"
        )
        if payload.cost_state != expected_cost_state or payload.cost_micro_usd != expected_cost:
            raise InvalidState("behavioral cost does not reconcile to gateway usage accounting")
        input_values = [row["input_tokens"] for row in delivery_rows]
        output_values = [row["output_tokens"] for row in delivery_rows]
        expected_input = (
            sum(input_values) if all(value is not None for value in input_values) else None
        )
        expected_output = (
            sum(output_values) if all(value is not None for value in output_values) else None
        )
        if payload.input_tokens != expected_input or payload.output_tokens != expected_output:
            raise InvalidState(
                "behavioral token counts do not reconcile to gateway usage accounting"
            )
        expected_usage_state = (
            "reported"
            if expected_input is not None and expected_output is not None
            else "partial"
            if expected_input is not None or expected_output is not None
            else "unavailable"
        )
        if payload.usage_state != expected_usage_state:
            raise InvalidState("behavioral usage missingness does not match gateway accounting")

    @classmethod
    def _validate_behavioral_assessment(
        cls,
        connection: Any,
        document: BehavioralAssessmentDocument,
    ) -> None:
        payload = document.payload
        plan = cls._document_by_ref(connection, payload.plan_ref)
        registry = cls._document_by_ref(connection, payload.method_registry_ref)
        if not isinstance(plan, BehavioralAuditPlanDocumentV2) or not isinstance(
            registry, BehavioralMethodRegistryDocument
        ):
            raise InvalidReference(
                "behavioral assessment requires its stored plan and method registry"
            )
        if payload.method_id != plan.payload.method_id:
            raise InvalidState("behavioral assessment method differs from the frozen plan")
        observations: list[BehavioralObservationDocument] = []
        for reference in payload.observation_refs:
            observation = cls._document_by_ref(connection, reference)
            if not isinstance(observation, BehavioralObservationDocument):
                raise InvalidReference("behavioral assessment contains a non-observation reference")
            successor_id = connection.execute(
                select(audit_document.c.id).where(
                    audit_document.c.kind == "behavioral_observation",
                    audit_document.c.supersedes_id == observation.id,
                )
            ).scalar_one_or_none()
            if successor_id is not None:
                raise InvalidState(
                    "behavioral assessment must reference the current observation head"
                )
            observations.append(observation)
        method = next(
            (item for item in registry.payload.methods if item.method_id == payload.method_id),
            None,
        )
        if method is None:
            raise InvalidReference("behavioral assessment method is not registered")
        unsupported = (
            method.implementation_state != "available" or method.implementation_ref is None
        )
        capability_blocked = any(
            item.payload.missing_reason in {"method_unsupported", "capability_missing"}
            for item in observations
        )
        if (unsupported or capability_blocked) and any(
            item.payload.dispatch_state != "not_dispatched" for item in observations
        ):
            raise InvalidState(
                "unsupported behavioral configurations cannot dispatch diagnostic requests"
            )
        pairs = {pair.pair_id: pair for pair in plan.payload.sample_pairs}
        slots = {slot.model_context_ref: slot for slot in plan.payload.model_slots}
        expected_units = {
            (pair.pair_id, task.entity_id, sample_role, slot.model_context_ref)
            for pair in plan.payload.sample_pairs
            for task, sample_role in (
                (pair.original_task_ref, "original"),
                (pair.control_task_ref, "control"),
            )
            for slot in plan.payload.model_slots
        }
        seen_units: set[tuple[Any, Any, str, AuditDocumentRef]] = set()
        outcome_counts = {"completed": 0, "failed": 0, "blocked": 0, "not_run": 0}
        panels: dict[AuditDocumentRef, dict[str, list[Any]]] = {
            context: {"original": [], "control": []} for context in slots
        }
        for observation in observations:
            item = observation.payload
            pair = pairs.get(item.pair_id)
            if pair is None or item.model_context_ref not in slots:
                raise InvalidState("behavioral assessment contains an unplanned sample/model")
            task = (
                pair.original_task_ref if item.sample_role == "original" else pair.control_task_ref
            )
            key = (item.pair_id, item.task_ref.entity_id, item.sample_role, item.model_context_ref)
            if item.task_ref != task or key not in expected_units or key in seen_units:
                raise InvalidState("behavioral assessment duplicates or changes a planned unit")
            if item.plan_ref != payload.plan_ref:
                raise InvalidState("behavioral assessment mixes observations from another plan")
            seen_units.add(key)
            outcome_counts[item.outcome] += 1
            if item.outcome == "completed":
                assert item.score is not None
                panels[item.model_context_ref][item.sample_role].append(Decimal(item.score))
        if seen_units != expected_units or len(payload.observation_refs) != len(observations):
            raise InvalidState(
                "behavioral assessment must retain every planned observation exactly once"
            )
        if len(set(payload.observation_refs)) != len(payload.observation_refs):
            raise InvalidState("behavioral assessment observation references must be unique")
        if payload.expected_units != len(expected_units):
            raise InvalidState("behavioral assessment denominator differs from its plan")
        expected_outcomes = (
            outcome_counts["completed"],
            outcome_counts["failed"],
            outcome_counts["blocked"],
            outcome_counts["not_run"],
        )
        if expected_outcomes != (
            payload.completed_units,
            payload.failed_units,
            payload.blocked_units,
            payload.not_run_units,
        ):
            raise InvalidState("behavioral assessment outcome counts do not reconcile")
        dispatched_model_calls = sum(
            len(item.payload.access_event_refs)
            for item in observations
            if item.payload.dispatch_state != "not_dispatched"
        )
        if payload.dispatched_model_calls != dispatched_model_calls:
            raise InvalidState("behavioral delivery count does not reconcile to access events")
        if len(payload.model_panels) != len(slots):
            raise InvalidState("behavioral assessment must contain one panel per model context")
        panels_by_context = {panel.model_context_ref: panel for panel in payload.model_panels}
        for context, slot in slots.items():
            panel = panels_by_context.get(context)
            if panel is None or panel.role != slot.role:
                raise InvalidState(
                    "behavioral performance panel differs from its frozen model slot"
                )
            original = [
                item
                for item in observations
                if item.payload.model_context_ref == context
                and item.payload.sample_role == "original"
            ]
            control = [
                item
                for item in observations
                if item.payload.model_context_ref == context
                and item.payload.sample_role == "control"
            ]
            if (
                panel.planned_original != len(original)
                or panel.completed_original != len(panels[context]["original"])
                or panel.mean_original_score != cls._behavioral_mean(panels[context]["original"])
                or panel.planned_control != len(control)
                or panel.completed_control != len(panels[context]["control"])
                or panel.mean_control_score != cls._behavioral_mean(panels[context]["control"])
            ):
                raise InvalidState("behavioral performance panel does not match its observations")

        any_dispatched = any(
            item.payload.dispatch_state != "not_dispatched" for item in observations
        )
        cost_unknown = any(item.payload.cost_state == "unavailable" for item in observations)
        input_unknown = any(
            item.payload.dispatch_state != "not_dispatched" and item.payload.input_tokens is None
            for item in observations
        )
        output_unknown = any(
            item.payload.dispatch_state != "not_dispatched" and item.payload.output_tokens is None
            for item in observations
        )
        expected_cost = (
            None if cost_unknown else sum(item.payload.cost_micro_usd or 0 for item in observations)
        )
        expected_input = (
            None if input_unknown else sum(item.payload.input_tokens or 0 for item in observations)
        )
        expected_output = (
            None
            if output_unknown
            else sum(item.payload.output_tokens or 0 for item in observations)
        )
        expected_cost_state = (
            "unavailable"
            if cost_unknown
            else "estimated"
            if any(item.payload.cost_state == "estimated" for item in observations)
            else "actual"
            if any_dispatched
            else "not_applicable"
        )
        if (
            payload.total_cost_state != expected_cost_state
            or payload.total_cost_micro_usd != expected_cost
            or payload.total_input_tokens != expected_input
            or payload.total_output_tokens != expected_output
        ):
            raise InvalidState("behavioral assessment cost and usage totals do not reconcile")
        over_cap = (
            len(plan.payload.sample_pairs) * 2 > plan.payload.budget.max_samples
            or dispatched_model_calls > plan.payload.budget.max_model_calls
            or (
                expected_cost is not None and expected_cost > plan.payload.budget.max_cost_micro_usd
            )
            or (
                expected_input is not None and expected_input > plan.payload.budget.max_input_tokens
            )
            or (
                expected_output is not None
                and expected_output > plan.payload.budget.max_output_tokens
            )
        )
        budget_unknown = (cost_unknown or input_unknown or output_unknown) and not over_cap
        expected_budget_state = (
            "over_cap" if over_cap else "unknown" if budget_unknown else "within_cap"
        )
        if payload.budget_state != expected_budget_state:
            raise InvalidState("behavioral assessment budget state does not reconcile")
        if outcome_counts["completed"] == len(expected_units):
            expected_assessment_state = "complete"
        elif (
            outcome_counts["completed"] == 0
            and outcome_counts["failed"] == 0
            and outcome_counts["not_run"] == 0
        ):
            expected_assessment_state = "blocked"
        else:
            expected_assessment_state = "partial"
        if over_cap and expected_assessment_state == "complete":
            expected_assessment_state = "partial"
        expected_inference_state = (
            "unsupported"
            if unsupported or capability_blocked
            else "calibration_blocked"
            if plan.payload.ground_truth_state == "unavailable"
            else "descriptive_only"
        )
        expected_calibration_state = (
            "blocked_no_ground_truth"
            if plan.payload.ground_truth_state == "unavailable"
            else "pending_validation"
        )
        expected_training_state = (
            "unavailable"
            if plan.payload.ground_truth_state == "owned_controlled"
            else "not_applicable"
        )
        expected_training_budget = (
            "unknown" if plan.payload.ground_truth_state == "owned_controlled" else "not_applicable"
        )
        family_counts = {
            split: len(
                {
                    pair.family_ref.entity_id
                    for pair in plan.payload.sample_pairs
                    if pair.split == split
                }
            )
            for split in ("calibration", "validation", "held_out_test")
        }
        if unsupported:
            expected_power_state = "unsupported"
            expected_power_limits = ("method_unsupported",)
        elif capability_blocked:
            expected_power_state = "unsupported"
            expected_power_limits = ("capability_missing",)
        elif any(count < 2 for count in family_counts.values()):
            expected_power_state = "insufficient_families"
            expected_power_limits = ("fewer_than_two_families_in_a_split",)
        else:
            expected_power_state = "not_estimated"
            expected_power_limits = ("power_analysis_not_available",)
        if (
            payload.assessment_state != expected_assessment_state
            or payload.inference_state != expected_inference_state
            or payload.calibration_state != expected_calibration_state
            or payload.inclusion_claim != "not_assessed"
            or payload.training_cost_state != expected_training_state
            or payload.training_cost_micro_usd is not None
            or payload.training_budget_state != expected_training_budget
            or payload.power_state != expected_power_state
            or payload.power_limit_codes != expected_power_limits
        ):
            raise InvalidState("behavioral assessment does not reconcile to its frozen outcomes")

    @staticmethod
    def _behavioral_mean(values: list[Any]) -> str | None:
        if not values:
            return None
        return str(
            (sum(values, Decimal(0)) / Decimal(len(values))).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
        )

    @classmethod
    def _validate_sealed_manifest_document(cls, connection: Any, document: AuditDocument) -> None:
        if not isinstance(document, SealedManifestDocumentV2):
            return
        payload = document.payload
        if document.supersedes_id is None:
            if payload.access_event_refs or payload.disclosure_state != "sealed":
                raise InvalidState(
                    "new sealed manifests must start sealed with empty access history"
                )
            return
        previous_row = (
            connection.execute(
                select(audit_document)
                .where(audit_document.c.id == document.supersedes_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if previous_row is None:
            raise InvalidReference("sealed manifest predecessor is missing")
        previous = cls._document_from_row(previous_row)
        if not isinstance(previous, SealedManifestDocumentV2):
            raise InvalidState("sealed v2 manifest cannot succeed an unversioned manifest")
        if (
            payload.access_event_refs[: len(previous.payload.access_event_refs)]
            != previous.payload.access_event_refs
        ):
            raise InvalidState("sealed manifest successor erased or reordered access history")
        new_event_refs = payload.access_event_refs[len(previous.payload.access_event_refs) :]
        if len(new_event_refs) != 1:
            raise InvalidState("sealed manifest successor must append exactly one access event")
        event_row = (
            connection.execute(
                select(audit_document).where(audit_document.c.id == new_event_refs[0].document_id)
            )
            .mappings()
            .one_or_none()
        )
        if event_row is None:
            raise InvalidReference("sealed manifest access event is missing")
        event = cls._document_from_row(event_row)
        if not isinstance(event, SealAccessEventDocument):
            raise InvalidReference("sealed manifest event reference is not an access event")
        try:
            validate_sealed_manifest_transition(previous, document, event)
        except ValueError as error:
            raise InvalidState(str(error)) from None

    @staticmethod
    def _document_from_row(row: Any) -> AuditDocument:
        trace_id = row["trace_id"]
        value = {
            "id": str(row["id"]),
            "kind": row["kind"],
            "schema_version": row["schema_version"],
            "payload": row["payload"],
            "supersedes_id": str(row["supersedes_id"])
            if row["supersedes_id"] is not None
            else None,
            "metadata": {
                "created_at": row["document_created_at"],
                "timestamp_precision": row["timestamp_precision"],
                "actor": row["created_by"],
                "trace_id": str(trace_id) if trace_id is not None else None,
                "row_version": row["document_row_version"],
            },
        }
        return parse_audit_document(canonical_json_bytes(value))

    @classmethod
    def _validate_canary_observation(cls, connection: Any, document: AuditDocument) -> None:
        if not isinstance(document, CanaryObservationDocument):
            return
        payload = document.payload
        if payload.observation in {"observed_verified", "observed_previously_published"}:
            for evidence_ref in (payload.source_ref, payload.source_review_ref):
                if evidence_ref is None:
                    raise InvalidReference("verified canary source review evidence is required")
                evidence = cls._document_by_ref(connection, evidence_ref)
                if not isinstance(evidence, (MatchEvidenceDocument, MatchEvidenceDocumentV2)):
                    raise InvalidReference("canary source review must reference match evidence")
                if evidence.payload.review_state != "accepted":
                    raise InvalidState("canary source and date evidence must be accepted")
                if any(
                    date_evidence not in evidence.payload.source_date_evidence
                    for date_evidence in payload.source_date_evidence
                ):
                    raise InvalidState(
                        "canary observation dates must match accepted review evidence"
                    )
                if (
                    evidence_ref == payload.source_ref
                    and isinstance(evidence.payload, MatchEvidencePayloadV2)
                    and evidence.payload.source_date_state != "verified"
                ):
                    raise InvalidState("canary source dates are not verified")
        if payload.external_query:
            if payload.access_event_ref is None:
                raise InvalidReference("external canary observation is missing its access event")
            event = cls._document_by_ref(connection, payload.access_event_ref)
            if not isinstance(event, SealAccessEventDocument):
                raise InvalidReference("external canary observation requires a seal access event")
            event_payload = event.payload
            if (
                event_payload.operation != "remote_query"
                or event_payload.outcome != "authorized"
                or event_payload.exposure != "authorized_disclosure"
            ):
                raise InvalidState("canary query event does not record authorized disclosure")
            manifest = cls._document_by_ref(connection, event_payload.manifest_ref)
            if not isinstance(manifest, SealedManifestDocumentV2):
                raise InvalidReference("canary query event must reference its sealed marker")
            if payload.marker_ref not in manifest.payload.encrypted_artifact_refs:
                raise InvalidReference("canary marker does not match the disclosed sealed manifest")

    @classmethod
    def _document_by_ref(cls, connection: Any, reference: AuditDocumentRef) -> AuditDocument:
        row = (
            connection.execute(
                select(audit_document).where(audit_document.c.id == reference.document_id)
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row["kind"] != reference.kind
            or row["semantic_digest"] != reference.digest
        ):
            raise InvalidReference("audit evidence reference is missing or changed")
        return cls._document_from_row(row)

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
                query_limit = _frozen_limit(limits, "max_query_units", "max_query_units_per_plan")
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
                        select(audit_run).where(audit_run.c.id == audit_run_id).with_for_update()
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
                    jobs = connection.execute(
                        select(stage_job.c.id, stage_job.c.shard_key)
                        .where(stage_job.c.audit_run_id == audit_run_id)
                        .order_by(stage_job.c.shard_key)
                    ).all()
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
