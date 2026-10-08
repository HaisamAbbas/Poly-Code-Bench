"""Immutable audit-document storage and atomic audit-query budget/enqueue operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Literal, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from polycodebench_core.application_errors import (
    InvalidReference,
    InvalidState,
    OptimisticVersionConflict,
    PersistenceConflict,
)
from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    AuditDocumentRef,
    AuditPlanDocument,
    AuditRunState,
    BehavioralAssessmentDocument,
    BehavioralAuditPlanDocumentV2,
    BehavioralMethodRegistryDocument,
    BehavioralObservationDocument,
    BehavioralTaskValidityDocument,
    BenchmarkHealthDocumentV2,
    BenchmarkSnapshotDocument,
    CanaryObservationDocument,
    CoverageManifestDocument,
    DerivedBenchmarkManifestDocument,
    FirewallDecisionDocumentV2,
    FirewallPolicyDocumentV2,
    FirewallScopeDocument,
    ImmutableArtifactRef,
    MatchEvidenceDocument,
    MatchEvidenceDocumentV2,
    MatchEvidencePayloadV2,
    ModelContextDocument,
    MonitorAlertDocument,
    MonitorPolicyDocumentV2,
    QueryManifestDocument,
    ReplacementPlanDocumentV2,
    ReplacementSourceMetadataDocument,
    ReplacementValidationDocument,
    RiskAssessmentDocumentV2,
    RiskPolicyDocument,
    RiskPolicyDocumentV2,
    SealAccessEventDocument,
    SealedManifestDocumentV2,
    TemporalAssessmentDocumentV2,
    audit_document_digest,
    benchmark_health_discontinuities,
    benchmark_health_sample_digest,
    parse_audit_document,
    validate_audit_transition,
    validate_sealed_manifest_transition,
)
from polycodebench_core.benchmark_firewall import (
    build_firewall_decision,
    validate_derived_family_split_consistency,
    validate_replacement_draft_budget,
)
from polycodebench_core.canonical import (
    canonical_document_digest,
    canonical_json_bytes,
)
from polycodebench_core.models import AdmissionExecutionReport
from polycodebench_core.models import TaskVersion as TaskVersionContract
from polycodebench_core.monitor_schedule import monitor_retry_at, monitor_slot_identity
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
    benchmark_import_manifest,
    benchmark_item,
    benchmark_snapshot,
    budget_account,
    call_delivery,
    call_intent,
    campaign,
    config_document,
    match_candidate,
    model_revision,
    monitor_alert_inbox,
    monitor_slot,
    monitor_slot_source,
    risk_assessment,
    run,
    stage_job,
    stage_job_event,
    task_version,
    temporal_assessment,
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


@dataclass(frozen=True)
class MonitorSlotWrite:
    slot_id: UUID
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
        cls._validate_firewall_document(connection, document)
        cls._validate_monitor_document(connection, document)
        cls._validate_benchmark_health_document(connection, document)
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
        if isinstance(document, MonitorAlertDocument):
            connection.execute(
                insert(monitor_alert_inbox),
                [
                    {
                        "alert_document_id": document.id,
                        "recipient_subject": subject,
                    }
                    for subject in document.payload.recipient_subjects
                ],
            )
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
    def _validate_firewall_document(cls, connection: Any, document: AuditDocument) -> None:
        if (
            isinstance(
                document,
                (
                    FirewallPolicyDocumentV2,
                    FirewallScopeDocument,
                    ReplacementSourceMetadataDocument,
                    ReplacementPlanDocumentV2,
                    ReplacementValidationDocument,
                    DerivedBenchmarkManifestDocument,
                ),
            )
            and document.supersedes_id is not None
        ):
            raise InvalidState("Prompt95 evidence is immutable; create a new version or draft")

        if isinstance(document, ReplacementSourceMetadataDocument):
            if (
                document.payload.rights_state == "approved"
                and not document.payload.rights_evidence_refs
            ):
                raise InvalidState("approved replacement sources require reviewed rights evidence")
            return

        if isinstance(document, FirewallPolicyDocumentV2):
            policy_payload = document.payload
            snapshot = cls._document_by_ref(connection, policy_payload.benchmark_ref)
            plan = cls._document_by_ref(connection, policy_payload.audit_plan_ref)
            if not isinstance(snapshot, BenchmarkSnapshotDocument) or not isinstance(
                plan, AuditPlanDocument
            ):
                raise InvalidReference(
                    "firewall policy requires an official snapshot and audit plan"
                )
            if plan.payload.benchmark_ref != policy_payload.benchmark_ref:
                raise InvalidState("firewall policy audit plan differs from its official snapshot")
            if not {unit.source_ref for unit in policy_payload.required_scope} <= set(
                plan.payload.source_plan
            ):
                raise InvalidState("firewall policy scope exceeds its frozen source plan")
            return

        if isinstance(document, ReplacementPlanDocumentV2):
            replacement_plan = document.payload
            metadata = cls._document_by_ref(connection, replacement_plan.source_metadata_ref)
            if not isinstance(metadata, ReplacementSourceMetadataDocument):
                raise InvalidReference("replacement plan source-family metadata is missing")
            if metadata.payload.rights_state != "approved":
                raise InvalidState(
                    "replacement work is blocked until source-family rights are approved"
                )
            approved_sources = set(metadata.payload.approved_source_ids)
            if (
                not {quota.source_id for quota in replacement_plan.source_quotas}
                <= approved_sources
            ):
                raise InvalidState("replacement quotas exceed approved source-family metadata")
            if not set(replacement_plan.seed_ancestry_refs) <= set(metadata.payload.source_refs):
                raise InvalidState(
                    "replacement seed ancestry is outside the approved source family"
                )
            return

        if isinstance(document, FirewallScopeDocument):
            scope_payload = document.payload
            policy = cls._document_by_ref(connection, scope_payload.policy_ref)
            plan = cls._document_by_ref(connection, scope_payload.audit_ref)
            if not isinstance(policy, FirewallPolicyDocumentV2) or not isinstance(
                plan, AuditPlanDocument
            ):
                raise InvalidReference("firewall scope requires its frozen policy and plan")
            if policy.payload.audit_plan_ref != scope_payload.audit_ref:
                raise InvalidState("firewall scope is outside its preregistered plan")
            if scope_payload.task_ref not in plan.payload.task_refs:
                raise InvalidReference("firewall task was not included in its frozen audit plan")
            required = {unit.scope_key: unit for unit in policy.payload.required_scope}
            outcomes = {item.scope_key: item for item in scope_payload.outcomes}
            if set(outcomes) != set(required):
                raise InvalidState("firewall outcomes must cover every frozen scope unit exactly")
            for scope_key, outcome in outcomes.items():
                unit = required[scope_key]
                evidence = [cls._document_by_ref(connection, ref) for ref in outcome.evidence_refs]
                if outcome.state == "no_match":
                    coverage_docs = [
                        item for item in evidence if isinstance(item, CoverageManifestDocument)
                    ]
                    if not coverage_docs:
                        raise InvalidReference(
                            "no-match outcomes require finite coverage manifests"
                        )
                    for coverage in coverage_docs:
                        coverage_payload = coverage.payload
                        if (
                            not coverage_payload.planned_queries
                            or set(coverage_payload.planned_queries)
                            != set(coverage_payload.executed_queries)
                            or coverage_payload.outages
                            or coverage_payload.unsupported_modalities
                            or coverage_payload.truncation
                            or unit.source_ref not in coverage_payload.eligible_sources
                        ):
                            raise InvalidState(
                                "no-match coverage is incomplete or outside the policy"
                            )
                        for query_ref in coverage_payload.planned_queries:
                            query = cls._document_by_ref(connection, query_ref)
                            if not isinstance(query, QueryManifestDocument):
                                raise InvalidReference(
                                    "coverage manifest query reference is invalid"
                                )
                            query_payload = query.payload
                            query_row = (
                                connection.execute(
                                    select(audit_query.c.state, audit_query.c.audit_run_id)
                                    .select_from(
                                        audit_query.join(
                                            audit_run,
                                            audit_run.c.id == audit_query.c.audit_run_id,
                                        )
                                    )
                                    .where(
                                        audit_query.c.query_document_id == query.id,
                                        audit_run.c.plan_document_id
                                        == scope_payload.audit_ref.document_id,
                                    )
                                )
                                .mappings()
                                .one_or_none()
                            )
                            if (
                                query_payload.audit_ref != scope_payload.audit_ref
                                or query_payload.task_ref != scope_payload.task_ref
                                or query_payload.connector_snapshot != unit.source_ref
                                or unit.component_ref not in query_payload.component_refs
                                or query_row is None
                                or query_row["state"] != "complete"
                            ):
                                raise InvalidState(
                                    "no-match evidence requires a completed query "
                                    "for this exact scope"
                                )
                            unresolved_candidates = connection.execute(
                                select(match_candidate.c.id)
                                .where(
                                    match_candidate.c.audit_run_id == query_row["audit_run_id"],
                                    match_candidate.c.task_version_id
                                    == scope_payload.task_ref.entity_id,
                                    match_candidate.c.source_document_id
                                    == unit.source_ref.document_id,
                                    match_candidate.c.state.not_in(("rejected", "superseded")),
                                )
                                .limit(1)
                            ).scalar_one_or_none()
                            if unresolved_candidates is not None:
                                raise InvalidState(
                                    "no-match scope contains an unresolved or accepted candidate"
                                )
                elif outcome.state == "match":
                    match_docs = [
                        item for item in evidence if isinstance(item, MatchEvidenceDocumentV2)
                    ]
                    if not match_docs:
                        raise InvalidReference("match outcomes require reviewed v2 match evidence")
                    if not any(
                        item.payload.task_ref == scope_payload.task_ref
                        and item.payload.source_snapshot_ref == unit.source_ref
                        and unit.component_ref in item.payload.component_refs
                        and item.payload.relation == outcome.relation
                        and item.payload.review_state == "accepted"
                        for item in match_docs
                    ):
                        raise InvalidState(
                            "match finding does not bind the exact task/source scope"
                        )
            return

        if isinstance(document, ReplacementValidationDocument):
            validation_payload = document.payload
            plan = cls._document_by_ref(connection, validation_payload.plan_ref)
            if not isinstance(plan, ReplacementPlanDocumentV2):
                raise InvalidReference("replacement validation requires a preregistered v2 plan")
            source_metadata = cls._document_by_ref(connection, plan.payload.source_metadata_ref)
            if not isinstance(source_metadata, ReplacementSourceMetadataDocument):
                raise InvalidReference("replacement source-family metadata is missing")
            if (
                validation_payload.source_family_id != source_metadata.payload.source_family_id
                or validation_payload.source_id not in source_metadata.payload.approved_source_ids
                or validation_payload.draft_index > plan.payload.budgets.max_drafts
                or validation_payload.author_ref not in plan.payload.authors
                or not set(validation_payload.checker_refs) <= set(plan.payload.checkers)
                or validation_payload.difficulty_policy_ref != plan.payload.difficulty_policy_ref
                or source_metadata.payload.rights_state != "approved"
            ):
                raise InvalidState(
                    "replacement validation exceeds its preregistered plan or rights"
                )
            if validation_payload.exposure_scope_ref is not None:
                exposure_scope = cls._document_by_ref(
                    connection, validation_payload.exposure_scope_ref
                )
                if not isinstance(exposure_scope, FirewallScopeDocument):
                    raise InvalidReference("replacement exposure evidence is not a firewall scope")
                if exposure_scope.payload.task_ref != validation_payload.task_ref:
                    raise InvalidState("replacement exposure scope belongs to a different task")
                if (
                    plan.payload.exposure_policy_ref.kind != "audit_plan"
                    or exposure_scope.payload.audit_ref != plan.payload.exposure_policy_ref
                ):
                    raise InvalidState(
                        "replacement exposure scope does not match its frozen exposure policy"
                    )
                exposure_policy = cls._document_by_ref(
                    connection, exposure_scope.payload.policy_ref
                )
                if not isinstance(exposure_policy, FirewallPolicyDocumentV2):
                    raise InvalidReference("replacement exposure policy is not a firewall policy")
                complete_scope = all(
                    outcome.state in {"no_match", "match"}
                    for outcome in exposure_scope.payload.outcomes
                )
                prohibited_match = any(
                    outcome.state == "match"
                    and outcome.relation in exposure_policy.payload.prohibited_relations
                    for outcome in exposure_scope.payload.outcomes
                )
                expected_exposure_state = (
                    "prohibited"
                    if prohibited_match
                    else "within_policy"
                    if complete_scope
                    else "unknown"
                )
                if validation_payload.exposure_state != expected_exposure_state:
                    raise InvalidState(
                        "replacement exposure state contradicts its complete firewall scope"
                    )
            elif validation_payload.review_state == "accepted":
                raise InvalidState("accepted replacement requires task-specific exposure evidence")
            connection.execute(
                select(audit_document.c.id).where(audit_document.c.id == plan.id).with_for_update()
            ).scalar_one()
            prior_validation_payloads = (
                connection.execute(
                    select(audit_document.c.payload).where(
                        audit_document.c.kind == "replacement_validation",
                        audit_document.c.supersedes_id.is_(None),
                        audit_document.c.payload["plan_ref"]["document_id"].astext == str(plan.id),
                    )
                )
                .scalars()
                .all()
            )
            prior_source_count = sum(
                payload.get("source_id") == validation_payload.source_id
                for payload in prior_validation_payloads
            )
            try:
                validate_replacement_draft_budget(
                    plan=plan.payload,
                    source_id=validation_payload.source_id,
                    existing_total=len(prior_validation_payloads),
                    existing_for_source=prior_source_count,
                )
            except ValueError as error:
                raise InvalidState(str(error)) from error
            candidate = (
                connection.execute(
                    select(task_version)
                    .where(task_version.c.id == validation_payload.task_ref.entity_id)
                    .with_for_update(read=True)
                )
                .mappings()
                .one_or_none()
            )
            if candidate is None:
                raise InvalidReference("replacement validation task version is not registered")
            task_document = TaskVersionContract.model_validate(candidate["document"])
            execution = AdmissionExecutionReport.model_validate(candidate["admission_evidence"])
            plan_created = connection.execute(
                select(audit_document.c.created_at).where(audit_document.c.id == plan.id)
            ).scalar_one_or_none()
            plan_preregistered_at = datetime.fromisoformat(
                plan.payload.preregistered_at.replace("Z", "+00:00")
            )
            manifest = connection.execute(
                select(artifact.c.content_digest, artifact.c.status, artifact.c.visibility).where(
                    artifact.c.id == candidate["manifest_artifact_id"]
                )
            ).one_or_none()
            from polycodebench_core.tasksets import package_snapshot_digest

            if (
                execution.execution_tier != "production_worker"
                or not execution.passed
                or execution.report_digest != task_document.admission_report.report_digest
                or execution.runtime_image_digest != task_document.runtime.image_digest
                or canonical_document_digest(task_document) != candidate["digest"]
                or plan_created is None
                or candidate["frozen_at"] is None
                or plan_preregistered_at > plan_created
                or plan_preregistered_at > candidate["frozen_at"]
                or candidate["frozen_at"] < plan_created
                or manifest is None
                or manifest.status != "verified"
                or manifest.visibility != "internal"
                or execution.package_digest
                != package_snapshot_digest(
                    str(manifest.content_digest),
                    task_document.visible_bundle.digest,
                    task_document.hidden_bundle.digest,
                )
                or task_document.source.rights_record_id != source_metadata.payload.rights_record_id
                or task_document.source.source_kind != validation_payload.source_id
            ):
                raise InvalidState(
                    "replacement requires complete trusted-worker, rights and oracle "
                    "admission evidence"
                )
            parents = (
                connection.execute(
                    select(task_version.c.cluster_id).where(
                        task_version.c.id.in_(
                            [item.entity_id for item in validation_payload.parent_task_refs]
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(parents) != len(validation_payload.parent_task_refs):
                raise InvalidReference("replacement ancestry includes an unknown task version")
            if plan.payload.mode == "source_family_transformation":
                if any(
                    cluster != source_metadata.payload.source_family_id for cluster in parents
                ) or (
                    candidate["cluster_id"] != source_metadata.payload.source_family_id
                    or validation_payload.family_relation != "source_family"
                ):
                    raise InvalidState(
                        "transformed tasks must retain their source-family split ancestry"
                    )
            elif (
                source_metadata.payload.source_family_id not in parents
                or candidate["cluster_id"] in parents
                or validation_payload.family_relation != "independent_prospective"
            ):
                raise InvalidState(
                    "prospective tasks require a separately reviewed independent family"
                )
            return

        if isinstance(document, FirewallDecisionDocumentV2):
            decision = document.payload
            policy = cls._document_by_ref(connection, decision.policy_ref)
            scope = cls._document_by_ref(connection, decision.scope_ref)
            risk = cls._document_by_ref(connection, decision.risk_assessment_ref)
            validation = cls._document_by_ref(connection, decision.validity_ref)
            temporal = None
            if decision.temporal_ref is not None:
                temporal = cls._document_by_ref(connection, decision.temporal_ref)
            if (
                not isinstance(policy, FirewallPolicyDocumentV2)
                or not isinstance(scope, FirewallScopeDocument)
                or not isinstance(risk, RiskAssessmentDocumentV2)
                or not isinstance(validation, ReplacementValidationDocument)
                or (temporal is not None and not isinstance(temporal, TemporalAssessmentDocumentV2))
            ):
                raise InvalidReference("firewall decision references incompatible evidence")
            risk_row = connection.execute(
                select(risk_assessment.c.task_version_id, risk_assessment.c.state).where(
                    risk_assessment.c.document_id == risk.id
                )
            ).one_or_none()
            if (
                risk_row is None
                or risk_row.task_version_id != decision.task_ref.entity_id
                or risk_row.state != risk.payload.state
            ):
                raise InvalidReference("firewall risk assessment is not persisted for this task")
            if temporal is not None:
                temporal_task = connection.execute(
                    select(temporal_assessment.c.task_version_id).where(
                        temporal_assessment.c.document_id == temporal.id
                    )
                ).scalar_one_or_none()
                if temporal_task != decision.task_ref.entity_id:
                    raise InvalidReference(
                        "firewall temporal assessment is not persisted for this task"
                    )
            expected = build_firewall_decision(
                policy=policy,
                scope=scope,
                risk=risk,
                validation=validation,
                temporal=temporal,
                reviewer=decision.reviewer,
            )
            if document.supersedes_id is None:
                if decision.resolution_reason is not None or decision != expected:
                    raise InvalidState(
                        "root firewall decisions must equal the frozen evidence result"
                    )
            else:
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
                    raise InvalidReference("firewall decision predecessor is missing")
                previous = cls._document_from_row(previous_row)
                if not isinstance(previous, FirewallDecisionDocumentV2):
                    raise InvalidState("firewall v2 decisions can only succeed v2 decisions")
                prior = previous.payload
                old_refs = {
                    prior.scope_ref,
                    prior.risk_assessment_ref,
                    prior.validity_ref,
                    *(() if prior.temporal_ref is None else (prior.temporal_ref,)),
                    *prior.exposure_refs,
                }
                new_refs = {
                    decision.scope_ref,
                    decision.risk_assessment_ref,
                    decision.validity_ref,
                    *(() if decision.temporal_ref is None else (decision.temporal_ref,)),
                    *decision.exposure_refs,
                }
                if (
                    prior.result != "review"
                    or prior.task_ref != decision.task_ref
                    or prior.policy_ref != decision.policy_ref
                    or prior.reviewer.entity_id == decision.reviewer.entity_id
                    or not old_refs <= new_refs
                    or not old_refs < new_refs
                    or decision.resolution_reason is None
                    or decision.model_copy(update={"resolution_reason": None}) != expected
                ):
                    raise InvalidState(
                        "firewall successors need independent review and new "
                        "non-regressing evidence"
                    )
            return

        if isinstance(document, DerivedBenchmarkManifestDocument):
            manifest_payload = document.payload
            official = cls._document_by_ref(connection, manifest_payload.official_snapshot_ref)
            if not isinstance(official, BenchmarkSnapshotDocument):
                raise InvalidReference("derived benchmark must bind an official snapshot")
            snapshot_row = (
                connection.execute(
                    select(benchmark_snapshot).where(
                        benchmark_snapshot.c.document_id == official.id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                snapshot_row is None
                or snapshot_row["membership_digest"] != manifest_payload.official_membership_digest
                or snapshot_row["split"] != manifest_payload.entries[0].split
                or any(item.split != snapshot_row["split"] for item in manifest_payload.entries)
            ):
                raise InvalidState("derived manifest does not preserve the official split/version")
            related_snapshot_document_ids = (
                connection.execute(
                    select(benchmark_snapshot.c.document_id).where(
                        benchmark_snapshot.c.registry_id == snapshot_row["registry_id"],
                        benchmark_snapshot.c.version == snapshot_row["version"],
                    )
                )
                .scalars()
                .all()
            )
            related_rows = (
                connection.execute(
                    select(audit_document).where(
                        audit_document.c.kind == "derived_benchmark_manifest",
                        audit_document.c.payload["official_snapshot_ref"]["document_id"].astext.in_(
                            [str(item) for item in related_snapshot_document_ids]
                        ),
                    )
                )
                .mappings()
                .all()
            )
            related_manifests = [
                existing_document.payload
                for row in related_rows
                if isinstance(
                    existing_document := cls._document_from_row(row),
                    DerivedBenchmarkManifestDocument,
                )
            ]
            try:
                validate_derived_family_split_consistency(
                    candidate=manifest_payload,
                    related_manifests=related_manifests,
                )
            except ValueError as error:
                raise InvalidState(str(error)) from error
            official_ids = {item.entity_id for item in official.payload.membership}
            entry_ids = {item.original_item_ref.entity_id for item in manifest_payload.entries}
            if official_ids != entry_ids:
                raise InvalidState(
                    "derived manifest must account for every official item exactly once"
                )
            import_row = connection.execute(
                select(benchmark_import_manifest.c.result_state).where(
                    benchmark_import_manifest.c.snapshot_id == snapshot_row["id"]
                )
            ).scalar_one_or_none()
            item_rows = (
                connection.execute(
                    select(benchmark_item.c.source_digest, benchmark_item.c.import_state).where(
                        benchmark_item.c.snapshot_id == snapshot_row["id"]
                    )
                )
                .mappings()
                .all()
            )
            if (
                import_row != "complete"
                or not item_rows
                or any(row["import_state"] != "imported" for row in item_rows)
            ):
                raise InvalidState(
                    "derived manifests require a complete rights-approved official import"
                )
            official_digests = sorted(
                item.digest for item in official.payload.membership if item.digest is not None
            )
            imported_digests = sorted(row["source_digest"] for row in item_rows)
            if official_digests != imported_digests:
                raise InvalidState(
                    "official membership evidence differs from imported source bytes"
                )
            for entry in manifest_payload.entries:
                if entry.disposition != "replaced":
                    continue
                if entry.validation_ref is None:
                    raise InvalidReference("derived replacements require their independent review")
                validation = cls._document_by_ref(connection, entry.validation_ref)
                if not isinstance(validation, ReplacementValidationDocument):
                    raise InvalidReference("derived replacements require their independent review")
                if (
                    validation.payload.task_ref != entry.replacement_task_ref
                    or validation.payload.review_state != "accepted"
                    or validation.payload.oracle_state != "independently_verified"
                    or validation.payload.source_family_id != entry.source_family_id
                ):
                    raise InvalidState(
                        "derived replacement lacks accepted validity and oracle evidence"
                    )
                derived_cluster = connection.execute(
                    select(task_version.c.cluster_id).where(
                        task_version.c.id == entry.replacement_task_ref.entity_id
                    )
                ).scalar_one_or_none()
                if derived_cluster != entry.derived_source_family_id:
                    raise InvalidState(
                        "derived source-family mapping differs from the admitted task lineage"
                    )
            return

    @classmethod
    def _validate_benchmark_health_document(cls, connection: Any, document: AuditDocument) -> None:
        if document.kind == "benchmark_health" and not isinstance(
            document, BenchmarkHealthDocumentV2
        ):
            raise InvalidState("legacy health documents are historical and cannot be written")
        if not isinstance(document, BenchmarkHealthDocumentV2):
            return
        if document.supersedes_id is not None:
            raise InvalidState("benchmark-health points are immutable snapshots, not successors")

        payload = document.payload
        scope = payload.scope
        membership = cls._document_by_ref(connection, scope.membership_ref)
        plan = cls._document_by_ref(connection, scope.plan_ref)
        policy = cls._document_by_ref(connection, scope.policy_ref)
        if (
            not isinstance(membership, BenchmarkSnapshotDocument)
            or not isinstance(plan, AuditPlanDocument)
            or not isinstance(policy, (RiskPolicyDocument, RiskPolicyDocumentV2))
        ):
            raise InvalidReference(
                "health scope requires its benchmark snapshot, plan and risk policy"
            )
        if (
            plan.payload.benchmark_ref != scope.membership_ref
            or plan.payload.policy != scope.policy_ref
            or plan.payload.model_context != scope.context_ref
        ):
            raise InvalidState("health snapshot differs from its frozen plan, policy or context")
        if scope.context_ref is not None and not isinstance(
            cls._document_by_ref(connection, scope.context_ref), ModelContextDocument
        ):
            raise InvalidReference("health model context reference is unavailable")

        population_by_id = {item.entity_id: item for item in membership.payload.membership}
        tasks_by_id = {item.entity_id: item for item in plan.payload.task_refs}
        if (
            len(population_by_id) != len(membership.payload.membership)
            or len(tasks_by_id) != len(plan.payload.task_refs)
            or any(population_by_id.get(task_id) != task for task_id, task in tasks_by_id.items())
            or scope.population_task_count != len(population_by_id)
            or scope.selected_task_count != len(tasks_by_id)
            or not tasks_by_id
        ):
            raise InvalidState(
                "health counts or selected task versions differ from frozen membership"
            )
        is_census = set(tasks_by_id) == set(population_by_id)
        expected_mode = "census" if is_census else "sampled"
        if (
            scope.sampling_mode != expected_mode
            or scope.sample_digest != benchmark_health_sample_digest(plan.payload.task_refs)
        ):
            raise InvalidState("health sampling label or digest differs from its frozen task set")
        if not is_census and plan.payload.sample_design.get("method") != scope.sampling_method:
            raise InvalidState("sampled health scope must use its frozen plan sampling method")
        expected_sampling_digest = (
            "sha256:" + hashlib.sha256(canonical_json_bytes(plan.payload.sample_design)).hexdigest()
        )
        expected_method_digest = (
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(sorted(plan.payload.methods))).hexdigest()
        )
        if (
            scope.sampling_design_digest != expected_sampling_digest
            or scope.method_digest != expected_method_digest
        ):
            raise InvalidState("health sampling or scan-method definition differs from its plan")
        allowed_sources = set(plan.payload.source_plan)
        if set(scope.source_refs) != allowed_sources:
            raise InvalidState("health source window differs from the frozen audit plan")

        assessments: list[RiskAssessmentDocumentV2] = []
        assessed_tasks: set[UUID] = set()
        accepted_exact_tasks: set[UUID] = set()
        accepted_semantic_tasks: set[UUID] = set()
        status_counts = {name: 0 for name in ("complete", "partial", "unknown", "blocked")}
        tier_counts = {name: 0 for name in ("low", "medium", "high", "insufficient")}
        tier_map = {
            "low_observed": "low",
            "medium_observed": "medium",
            "high_observed": "high",
            "insufficient_evidence": "insufficient",
        }
        observed_values: list[Decimal] = []
        for assessment_ref in payload.assessment_refs:
            assessment = cls._document_by_ref(connection, assessment_ref)
            if not isinstance(assessment, RiskAssessmentDocumentV2):
                raise InvalidReference("health assessments must use risk schema v2")
            result = assessment.payload
            if (
                result.plan_ref != scope.plan_ref
                or result.policy_ref != scope.policy_ref
                or result.context_ref != scope.context_ref
                or result.task_ref.entity_id not in tasks_by_id
                or tasks_by_id[result.task_ref.entity_id] != result.task_ref
                or result.task_ref.entity_id in assessed_tasks
            ):
                raise InvalidState("health assessment is duplicated or outside its frozen cohort")
            assessed_tasks.add(result.task_ref.entity_id)
            assessments.append(assessment)
            if result.scope_state == "blocked":
                status_counts["blocked"] += 1
            elif result.scope_state == "partial":
                status_counts["partial"] += 1
            elif result.scope_state == "not_run" or result.state == "insufficient_evidence":
                status_counts["unknown"] += 1
            else:
                status_counts["complete"] += 1
            if result.state not in tier_map:
                raise InvalidState("health risk assessment contains a non-reportable tier")
            tier_counts[tier_map[result.state]] += 1
            if result.observed_index.value is not None:
                observed_values.append(Decimal(result.observed_index.value))

            for evidence_ref in result.accepted_evidence:
                evidence = cls._document_by_ref(connection, evidence_ref)
                if evidence_ref.kind != "match_evidence":
                    continue
                if not isinstance(evidence, MatchEvidenceDocumentV2):
                    raise InvalidReference("health match evidence must use reviewed schema v2")
                evidence_payload = evidence.payload
                if (
                    evidence_payload.review_state != "accepted"
                    or evidence_payload.task_ref != result.task_ref
                    or evidence_payload.target_benchmark_ref != scope.membership_ref
                    or evidence_payload.retrieval_plan_ref != scope.plan_ref
                    or evidence_payload.source_snapshot_ref not in allowed_sources
                ):
                    raise InvalidState("health risk evidence falls outside its reviewed cohort")
                if evidence_payload.source_lineage == "official_self_import":
                    continue
                if evidence_payload.relation == "exact_component":
                    accepted_exact_tasks.add(result.task_ref.entity_id)
                elif evidence_payload.relation == "semantic_duplicate":
                    accepted_semantic_tasks.add(result.task_ref.entity_id)

        expected_unscanned = len(tasks_by_id) - len(assessed_tasks)
        expected_statuses = {
            "selected_tasks": len(tasks_by_id),
            **status_counts,
            "unscanned": expected_unscanned,
        }
        if payload.metrics.assessment_states.model_dump() != expected_statuses:
            raise InvalidState("health assessment-state totals do not reconcile to persisted rows")
        expected_tiers = {"assessed_tasks": len(assessed_tasks), **tier_counts}
        if payload.metrics.risk_tiers.model_dump() != expected_tiers:
            raise InvalidState("health risk-tier totals do not reconcile to persisted assessments")
        if payload.metrics.exact_duplicate_prevalence.numerator != len(accepted_exact_tasks):
            raise InvalidState("health exact-duplicate total differs from accepted evidence")
        if payload.metrics.semantic_duplicate_prevalence.numerator != len(accepted_semantic_tasks):
            raise InvalidState("health semantic-duplicate total differs from accepted evidence")
        if payload.metrics.duplicate_union_prevalence.numerator != len(
            accepted_exact_tasks | accepted_semantic_tasks
        ):
            raise InvalidState("health duplicate union must count overlapping tasks only once")

        if observed_values:
            with localcontext() as context:
                context.prec = 28
                mean_value = (
                    sum(observed_values, Decimal(0)) / Decimal(len(observed_values))
                ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
            expected_mean = f"{mean_value:.6f}"
        else:
            expected_mean = None
        mean = payload.metrics.mean_observed_risk
        if (
            mean.eligible_tasks != len(observed_values)
            or mean.missing_tasks != len(tasks_by_id) - len(observed_values)
            or mean.value != expected_mean
        ):
            raise InvalidState("health mean risk does not reconcile to eligible persisted scores")

        if scope.context_ref is None and payload.temporal_refs:
            raise InvalidState(
                "model-agnostic health snapshots cannot include temporal assessments"
            )
        for temporal_ref in payload.temporal_refs:
            temporal = cls._document_by_ref(connection, temporal_ref)
            if not isinstance(temporal, TemporalAssessmentDocumentV2) or (
                temporal.payload.model_context_ref != scope.context_ref
            ):
                raise InvalidReference("health temporal evidence is outside the frozen context")

        for coverage_ref in payload.coverage_refs:
            coverage = cls._document_by_ref(connection, coverage_ref)
            if not isinstance(coverage, CoverageManifestDocument):
                raise InvalidReference("health coverage references must bind coverage manifests")
            coverage_payload = coverage.payload
            if not set(coverage_payload.eligible_sources) <= allowed_sources or not set(
                coverage_payload.executed_queries
            ) <= set(coverage_payload.planned_queries):
                raise InvalidState("health coverage exceeds its planned source/query scope")
            for query_ref in coverage_payload.planned_queries:
                query = cls._document_by_ref(connection, query_ref)
                if not isinstance(query, QueryManifestDocument) or (
                    query.payload.audit_ref != scope.plan_ref
                    or query.payload.task_ref not in plan.payload.task_refs
                    or query.payload.connector_snapshot not in allowed_sources
                ):
                    raise InvalidState("health coverage query is outside its frozen plan")

        trend_points: list[BenchmarkHealthDocumentV2] = []
        for trend_ref in payload.trend_refs:
            point = cls._document_by_ref(connection, trend_ref)
            if not isinstance(point, BenchmarkHealthDocumentV2):
                raise InvalidReference("health trends require versioned prior health points")
            trend_points.append(point)
        expected_breaks = tuple(
            sorted(
                {
                    reason
                    for point in trend_points
                    for reason in benchmark_health_discontinuities(scope, point.payload.scope)
                }
            )
        )
        if payload.discontinuity_reasons != expected_breaks:
            raise InvalidState("health trend discontinuity reasons do not match linked scopes")
        expected_trend_state = (
            "initial" if not trend_points else "discontinuity" if expected_breaks else "comparable"
        )
        if payload.trend_state != expected_trend_state:
            raise InvalidState("health trend label differs from the linked cohort scopes")

    @classmethod
    def _validate_monitor_document(cls, connection: Any, document: AuditDocument) -> None:
        if document.kind == "monitor_policy" and not isinstance(document, MonitorPolicyDocumentV2):
            raise InvalidState("legacy monitor policies are historical and cannot reserve work")
        if isinstance(document, MonitorPolicyDocumentV2):
            policy_payload = document.payload
            plan = cls._document_by_ref(connection, policy_payload.plan_ref)
            if not isinstance(plan, AuditPlanDocument):
                raise InvalidReference("monitor policy plan reference is unavailable")
            if document.supersedes_id is None and policy_payload.policy_version != 1:
                raise InvalidState("root monitor policy must start at version one")
            if policy_payload.benchmark_ref != plan.payload.benchmark_ref:
                raise InvalidState("monitor policy benchmark differs from its frozen audit plan")
            if plan.payload.visibility == "public":
                raise InvalidState("public audit plans cannot enable monitoring")
            if not set(policy_payload.task_refs) <= set(plan.payload.task_refs):
                raise InvalidState("monitor task selection exceeds the frozen audit plan")
            if not {item.source_ref for item in policy_payload.source_rate_limits} <= set(
                plan.payload.source_plan
            ):
                raise InvalidState("monitor source selection exceeds the frozen audit plan")
            query_limit = _frozen_limit(
                plan.payload.limits, "max_query_units", "max_query_units_per_plan"
            )
            storage_limit = _frozen_limit(
                plan.payload.limits, "max_storage_bytes", "max_storage_bytes_per_plan"
            )
            if (
                query_limit is None
                or policy_payload.max_query_units_per_slot > query_limit
                or storage_limit is None
                or policy_payload.max_storage_bytes_per_slot > storage_limit
            ):
                raise InvalidState("monitor slot reservations exceed frozen audit-plan limits")
            if document.supersedes_id is not None:
                previous_row = (
                    connection.execute(
                        select(audit_document).where(audit_document.c.id == document.supersedes_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                if previous_row is None:
                    raise InvalidReference("monitor policy successor predecessor is missing")
                previous = cls._document_from_row(previous_row)
                if not isinstance(previous, MonitorPolicyDocumentV2):
                    raise InvalidReference("monitor policy successor must reference policy v2")
                if (
                    previous.payload.benchmark_ref != policy_payload.benchmark_ref
                    or policy_payload.policy_version != previous.payload.policy_version + 1
                    or previous.payload.state == "retired"
                ):
                    raise InvalidState(
                        "monitor policy successor must advance one version in a non-retired scope"
                    )
            return

        if not isinstance(document, MonitorAlertDocument):
            return
        if document.supersedes_id is not None:
            raise InvalidState("monitor alerts are immutable events and cannot have successors")

        payload = document.payload
        policy = cls._document_by_ref(connection, payload.policy_ref)
        if not isinstance(policy, MonitorPolicyDocumentV2):
            raise InvalidReference("monitor alert requires a stored versioned policy")
        if policy.payload.state != "approved":
            raise InvalidState("monitor alerts require an approved policy version")
        if payload.recipient_subjects != policy.payload.in_app_recipients:
            raise InvalidState(
                "monitor alert recipients differ from the frozen in-app recipient list"
            )
        plan = cls._document_by_ref(connection, policy.payload.plan_ref)
        if not isinstance(plan, AuditPlanDocument):
            raise InvalidReference("monitor alert audit plan is unavailable")
        allowed_tasks = set(policy.payload.task_refs)
        allowed_sources = {item.source_ref for item in policy.payload.source_rate_limits}

        def validate_coverage_scope(coverage: CoverageManifestDocument) -> None:
            eligible_sources = set(coverage.payload.eligible_sources)
            if not eligible_sources <= allowed_sources:
                raise InvalidState("monitor coverage evidence exceeds the frozen source scope")

        if payload.alert_type in {"new_exposure", "risk_increase"}:
            assert payload.previous_assessment_ref is not None
            assert payload.assessment_ref is not None
            previous = cls._document_by_ref(connection, payload.previous_assessment_ref)
            current = cls._document_by_ref(connection, payload.assessment_ref)
            if not isinstance(previous, RiskAssessmentDocumentV2) or not isinstance(
                current, RiskAssessmentDocumentV2
            ):
                raise InvalidReference("risk alerts require versioned risk assessments")
            if (
                current.payload.plan_ref != policy.payload.plan_ref
                or current.payload.task_ref not in allowed_tasks
                or current.payload.policy_ref != plan.payload.policy
                or current.payload.context_ref != plan.payload.model_context
            ):
                raise InvalidState("monitor risk alert falls outside the frozen plan/task scope")
            if (
                previous.payload.task_ref != current.payload.task_ref
                or previous.payload.plan_ref != current.payload.plan_ref
                or previous.payload.context_ref != current.payload.context_ref
                or previous.payload.policy_ref != current.payload.policy_ref
                or previous.payload.calibration_state != current.payload.calibration_state
                or payload.task_ref != current.payload.task_ref
                or current.supersedes_id != previous.id
            ):
                raise InvalidState(
                    "monitor risk alert must bind one task's linear assessment successor"
                )
            if payload.alert_type == "new_exposure":
                assert payload.evidence_ref is not None
                evidence = cls._document_by_ref(connection, payload.evidence_ref)
                if (
                    not isinstance(evidence, MatchEvidenceDocumentV2)
                    or evidence.payload.review_state != "accepted"
                    or evidence.payload.source_date_state != "verified"
                    or evidence.payload.source_lineage
                    not in {"independent_copy", "mirror_or_derived"}
                    or evidence.payload.relation
                    in {"shared_concept", "no_substantive_match", "unresolved"}
                    or evidence.payload.task_ref != payload.task_ref
                    or evidence.payload.target_benchmark_ref != policy.payload.benchmark_ref
                    or evidence.payload.retrieval_plan_ref != policy.payload.plan_ref
                    or evidence.payload.source_snapshot_ref not in allowed_sources
                    or payload.evidence_ref not in current.payload.accepted_evidence
                ):
                    raise InvalidState(
                        "new-exposure alert requires accepted evidence in its successor assessment"
                    )
            else:
                old_score = previous.payload.observed_index.value
                new_score = current.payload.observed_index.value
                if (
                    old_score is None
                    or new_score is None
                    or Decimal(new_score) <= Decimal(old_score)
                ):
                    raise InvalidState("risk-increase alert requires a measured increasing score")
            return

        if payload.alert_type == "source_outage":
            assert payload.coverage_ref is not None
            assert payload.source_ref is not None
            coverage = cls._document_by_ref(connection, payload.coverage_ref)
            if payload.source_ref not in allowed_sources:
                raise InvalidState("source-outage alert falls outside the frozen source scope")
            if not isinstance(coverage, CoverageManifestDocument):
                raise InvalidReference("source-outage alert requires coverage manifest evidence")
            validate_coverage_scope(coverage)
            if payload.source_ref not in coverage.payload.eligible_sources or not any(
                item.get("source_ref") == payload.source_ref.model_dump(mode="json")
                and item.get("state") in {"unavailable", "outage"}
                for item in coverage.payload.outages
            ):
                raise InvalidState("source-outage alert requires matching outage coverage evidence")
            return

        if payload.alert_type == "stale_scan":
            assert payload.coverage_ref is not None
            coverage = cls._document_by_ref(connection, payload.coverage_ref)
            if not isinstance(coverage, CoverageManifestDocument):
                raise InvalidReference("stale-scan alert requires coverage manifest evidence")
            validate_coverage_scope(coverage)
            return

        if payload.alert_type in {"evidence_dispute", "evidence_correction"}:
            assert payload.evidence_ref is not None
            evidence = cls._document_by_ref(connection, payload.evidence_ref)
            if not isinstance(evidence, MatchEvidenceDocumentV2):
                raise InvalidReference("evidence-change alert requires versioned match evidence")
            if (
                evidence.payload.task_ref not in allowed_tasks
                or evidence.payload.source_snapshot_ref not in allowed_sources
                or evidence.payload.target_benchmark_ref != policy.payload.benchmark_ref
                or evidence.payload.retrieval_plan_ref != policy.payload.plan_ref
            ):
                raise InvalidState("evidence-change alert falls outside the frozen monitor scope")
            if payload.alert_type == "evidence_dispute":
                if evidence.payload.review_state not in {"disputed", "superseded"}:
                    raise InvalidState("dispute alert requires disputed or superseded evidence")
            else:
                if evidence.supersedes_id is None or evidence.payload.review_state not in {
                    "accepted",
                    "rejected",
                    "disputed",
                }:
                    raise InvalidState("correction alert requires reviewed successor evidence")
                previous_row = (
                    connection.execute(
                        select(audit_document).where(audit_document.c.id == evidence.supersedes_id)
                    )
                    .mappings()
                    .one_or_none()
                )
                if previous_row is None:
                    raise InvalidReference("corrected evidence predecessor is missing")
                previous_evidence = cls._document_from_row(previous_row)
                if (
                    not isinstance(previous_evidence, MatchEvidenceDocumentV2)
                    or previous_evidence.payload.task_ref != evidence.payload.task_ref
                ):
                    raise InvalidState("corrected evidence must retain its task identity")
            return

        if payload.alert_type == "policy_discontinuity":
            if payload.previous_policy_ref is None:
                raise InvalidReference("policy discontinuity alert is missing its predecessor")
            previous = cls._document_by_ref(connection, payload.previous_policy_ref)
            if (
                not isinstance(previous, MonitorPolicyDocumentV2)
                or policy.supersedes_id != previous.id
                or policy.payload.benchmark_ref != previous.payload.benchmark_ref
            ):
                raise InvalidState("policy discontinuity must bind the current policy successor")
            previous_plan = cls._document_by_ref(connection, previous.payload.plan_ref)
            current_plan = cls._document_by_ref(connection, policy.payload.plan_ref)
            if not isinstance(previous_plan, AuditPlanDocument) or not isinstance(
                current_plan, AuditPlanDocument
            ):
                raise InvalidReference("policy discontinuity plans are unavailable")
            expected_reasons = {"policy_scope"}
            previous_sources = {item.source_ref for item in previous.payload.source_rate_limits}
            current_sources = {item.source_ref for item in policy.payload.source_rate_limits}
            if previous_sources != current_sources:
                expected_reasons.add("corpus_snapshot")
            if (
                previous_plan.payload.methods != current_plan.payload.methods
                or previous_plan.payload.policy != current_plan.payload.policy
            ):
                expected_reasons.add("method_version")
            if set(payload.discontinuity_reasons) != expected_reasons:
                raise InvalidState(
                    "policy alert discontinuity reasons differ from the stored versions"
                )
            return

        if payload.alert_type == "seal_compromise":
            assert payload.sealed_manifest_ref is not None
            sealed = cls._document_by_ref(connection, payload.sealed_manifest_ref)
            if not isinstance(sealed, SealedManifestDocumentV2) or (
                sealed.payload.disclosure_state != "compromised"
            ):
                raise InvalidState("seal-compromise alert requires a compromised manifest")

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

    def reserve_monitor_slot(
        self,
        *,
        policy_ref: AuditDocumentRef,
        slot_key: str,
        scheduled_at: datetime,
        refresh_kind: Literal["incremental", "full"],
        source_refs: tuple[AuditDocumentRef, ...],
        query_units: int,
        retry_reserve_units: int,
        reserved_storage_bytes: int,
        reserved_cost_micro_usd: int = 0,
        missed_slots_before: int = 0,
        should_dispatch: bool = True,
    ) -> MonitorSlotWrite:
        """Persist one idempotent tick and serialize its per-source daily quota reservation."""
        if (
            policy_ref.kind != "monitor_policy"
            or not slot_key.isascii()
            or not slot_key
            or len(slot_key) > 32
            or scheduled_at.tzinfo is None
            or scheduled_at.utcoffset() is None
        ):
            raise InvalidState("monitor slot identity and scheduled time are invalid")
        if any(
            type(value) is not int or value < 0
            for value in (
                query_units,
                retry_reserve_units,
                reserved_storage_bytes,
                reserved_cost_micro_usd,
                missed_slots_before,
            )
        ):
            raise InvalidState("monitor slot reservations and missed count must be nonnegative")
        source_ids = [item.document_id for item in source_refs]
        if len(source_ids) != len(set(source_ids)) or any(
            item.kind != "corpus_snapshot" for item in source_refs
        ):
            raise InvalidState("monitor slot sources must be unique corpus snapshots")
        if not should_dispatch and (
            source_refs
            or query_units
            or retry_reserve_units
            or reserved_storage_bytes
            or reserved_cost_micro_usd
        ):
            raise InvalidState("a missed monitor tick cannot reserve work or budget")
        try:
            with self._engine.begin() as connection:
                policy_row = (
                    connection.execute(
                        select(audit_document)
                        .where(
                            audit_document.c.id == policy_ref.document_id,
                            audit_document.c.kind == "monitor_policy",
                            audit_document.c.schema_version == 2,
                            audit_document.c.semantic_digest == policy_ref.digest,
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if policy_row is None:
                    raise InvalidReference("monitor slot policy reference is missing or changed")
                policy_doc = self._document_from_row(policy_row)
                if not isinstance(policy_doc, MonitorPolicyDocumentV2):
                    raise InvalidReference("monitor slot requires a versioned monitor policy")
                policy = policy_doc.payload
                if policy.state != "approved":
                    raise InvalidState("only an owner-approved monitor policy can reserve a slot")
                if reserved_storage_bytes > policy.max_storage_bytes_per_slot:
                    raise InvalidState("monitor slot exceeds its frozen storage reservation")
                if reserved_cost_micro_usd > policy.max_cost_micro_usd_per_slot:
                    raise InvalidState("monitor slot exceeds its frozen cost reservation")
                allowed_refs = tuple(item.source_ref for item in policy.source_rate_limits)
                selected_refs = tuple(ref for ref in allowed_refs if ref in set(source_refs))
                if selected_refs != source_refs:
                    raise InvalidState("monitor slot source order or scope differs from its policy")
                zone_day = scheduled_at.astimezone(ZoneInfo(policy.timezone)).date()
                try:
                    expected_key, expected_at = monitor_slot_identity(policy, zone_day)
                except ValueError as error:
                    raise InvalidState(
                        "monitor slot date does not match the frozen policy schedule"
                    ) from error
                if slot_key != expected_key or scheduled_at.astimezone(UTC) != expected_at:
                    raise InvalidState(
                        "monitor slot key or instant differs from the frozen schedule"
                    )
                if (
                    should_dispatch
                    and refresh_kind == "full"
                    and set(source_refs) != set(allowed_refs)
                ):
                    raise InvalidState("full monitor refresh must cover every approved source")
                attempts = policy.max_retries + 1
                expected_units = (
                    len(policy.task_refs) * len(source_refs) * policy.query_units_per_task_source
                )
                if should_dispatch:
                    if (
                        query_units != expected_units
                        or retry_reserve_units != expected_units * attempts
                        or retry_reserve_units > policy.max_query_units_per_slot
                    ):
                        raise InvalidState("monitor slot query reservation violates its frozen cap")
                elif query_units != 0 or retry_reserve_units != 0:
                    raise InvalidState("missed monitor tick cannot reserve query units")

                existing = (
                    connection.execute(
                        select(monitor_slot)
                        .where(
                            monitor_slot.c.policy_document_id == policy_ref.document_id,
                            monitor_slot.c.slot_key == slot_key,
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                expected_values = {
                    "scheduled_at": scheduled_at.astimezone(UTC),
                    "local_day": zone_day,
                    "refresh_kind": refresh_kind,
                    "source_document_ids": [str(item.document_id) for item in source_refs],
                    "query_units": query_units,
                    "retry_reserve_units": retry_reserve_units,
                    "reserved_storage_bytes": reserved_storage_bytes,
                    "reserved_cost_micro_usd": reserved_cost_micro_usd,
                    "missed_slots_before": missed_slots_before,
                    "max_attempts": attempts,
                }
                if existing is not None:
                    if any(existing[name] != value for name, value in expected_values.items()):
                        raise PersistenceConflict(
                            "monitor slot replay changed its frozen reservation"
                        )
                    return MonitorSlotWrite(existing["id"], False, existing["row_version"])

                state = "planned" if should_dispatch else "missed"
                slot_id = connection.execute(
                    insert(monitor_slot)
                    .values(
                        policy_document_id=policy_ref.document_id,
                        slot_key=slot_key,
                        scheduled_at=scheduled_at.astimezone(UTC),
                        local_day=zone_day,
                        refresh_kind=expected_values["refresh_kind"],
                        source_document_ids=expected_values["source_document_ids"],
                        state=state,
                        dispatch_authorized=False,
                        query_units=query_units,
                        retry_reserve_units=retry_reserve_units,
                        reserved_storage_bytes=reserved_storage_bytes,
                        reserved_cost_micro_usd=reserved_cost_micro_usd,
                        missed_slots_before=missed_slots_before,
                        attempts=0,
                        max_attempts=attempts,
                    )
                    .returning(monitor_slot.c.id)
                ).scalar_one()
                if should_dispatch:
                    units_per_source = (
                        len(policy.task_refs) * policy.query_units_per_task_source * attempts
                    )
                    source_limits = {
                        item.source_ref: item.max_query_units_per_local_day
                        for item in policy.source_rate_limits
                    }
                    for source_ref in source_refs:
                        already_reserved = connection.execute(
                            select(
                                func.coalesce(
                                    func.sum(monitor_slot_source.c.reserved_query_units), 0
                                )
                            ).where(
                                monitor_slot_source.c.policy_document_id == policy_ref.document_id,
                                monitor_slot_source.c.source_document_id == source_ref.document_id,
                                monitor_slot_source.c.local_day == zone_day,
                            )
                        ).scalar_one()
                        if already_reserved + units_per_source > source_limits[source_ref]:
                            raise InvalidState("monitor source daily rate limit would be exceeded")
                        connection.execute(
                            insert(monitor_slot_source).values(
                                slot_id=slot_id,
                                policy_document_id=policy_ref.document_id,
                                source_document_id=source_ref.document_id,
                                local_day=zone_day,
                                reserved_query_units=units_per_source,
                            )
                        )
                return MonitorSlotWrite(slot_id, True, 0)
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def record_monitor_slot_failure(
        self,
        *,
        slot_id: UUID,
        expected_row_version: int,
        error_code: Literal[
            "source_unavailable",
            "rate_limited",
            "connector_error",
            "coverage_incomplete",
            "budget_exhausted",
            "authorization_required",
        ],
        failed_at: datetime,
        actor_subject: str,
    ) -> MonitorSlotWrite:
        """Record one dispatched failure and a capped retry time in the durable slot row."""
        if (
            failed_at.tzinfo is None
            or failed_at.utcoffset() is None
            or type(expected_row_version) is not int
            or expected_row_version < 0
            or not actor_subject
            or len(actor_subject) > 255
            or not actor_subject.isascii()
            or error_code
            not in {
                "source_unavailable",
                "rate_limited",
                "connector_error",
                "coverage_incomplete",
                "budget_exhausted",
                "authorization_required",
            }
        ):
            raise InvalidState("monitor failure event identity is invalid")
        try:
            with self._engine.begin() as connection:
                slot = (
                    connection.execute(
                        select(monitor_slot).where(monitor_slot.c.id == slot_id).with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if slot is None:
                    raise InvalidReference("monitor slot does not exist")
                if slot["row_version"] != expected_row_version:
                    raise OptimisticVersionConflict("monitor slot row version changed")
                if (
                    slot["state"] not in {"queued", "running"}
                    or not slot["dispatch_authorized"]
                    or slot["audit_run_id"] is None
                ):
                    raise InvalidState("only an authorized dispatched monitor slot can fail")
                run_row = (
                    connection.execute(
                        select(audit_run.c.state, audit_run.c.dispatch_authorized).where(
                            audit_run.c.id == slot["audit_run_id"]
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    run_row is None
                    or not run_row["dispatch_authorized"]
                    or run_row["state"] in {"draft", "planned", "complete", "cancelled"}
                ):
                    raise InvalidState("monitor failure is not attached to a dispatched audit run")

                failed_attempt = slot["attempts"] + 1
                policy_row = (
                    connection.execute(
                        select(audit_document).where(
                            audit_document.c.id == slot["policy_document_id"]
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if policy_row is None:
                    raise InvalidReference("monitor slot policy document is missing")
                policy_doc = self._document_from_row(policy_row)
                if not isinstance(policy_doc, MonitorPolicyDocumentV2):
                    raise InvalidReference("monitor retry requires a versioned policy")
                retry_at = monitor_retry_at(
                    completed_attempts=failed_attempt,
                    max_retries=slot["max_attempts"] - 1,
                    now=failed_at,
                    timezone_name=policy_doc.payload.timezone,
                    local_day=slot["local_day"],
                )
                if retry_at is not None:
                    state = "retry_wait"
                    action = "benchmark_audit.monitor.retry_scheduled"
                else:
                    retry_at = None
                    state = "failed"
                    action = "benchmark_audit.monitor.failed"
                after = {
                    "state": state,
                    "attempts": failed_attempt,
                    "next_retry_at": retry_at.isoformat() if retry_at is not None else None,
                    "last_error_code": error_code,
                }
                after_digest = "sha256:" + hashlib.sha256(canonical_json_bytes(after)).hexdigest()
                connection.execute(
                    update(monitor_slot)
                    .where(
                        monitor_slot.c.id == slot_id,
                        monitor_slot.c.row_version == expected_row_version,
                    )
                    .values(
                        state=state,
                        attempts=failed_attempt,
                        next_retry_at=retry_at,
                        last_error_code=error_code,
                        row_version=expected_row_version + 1,
                    )
                )
                connection.execute(
                    insert(audit_event).values(
                        actor_subject=actor_subject,
                        action=action,
                        resource_type="monitor_slot",
                        resource_id=str(slot_id),
                        after_digest=after_digest,
                        request_id=f"monitor-{slot_id}-attempt-{failed_attempt}",
                        details={
                            "attempt": failed_attempt,
                            "max_attempts": slot["max_attempts"],
                            "error_code": error_code,
                            "next_retry_at": after["next_retry_at"],
                        },
                    )
                )
                return MonitorSlotWrite(slot_id, False, expected_row_version + 1)
        except (InvalidReference, InvalidState, OptimisticVersionConflict):
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
