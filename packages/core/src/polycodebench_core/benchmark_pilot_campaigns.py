"""Strict evidence summaries for the Prompt102 replacement, seal and monitor slice."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, model_validator

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    EntityRef,
    ImmutableArtifactRef,
    StrictAuditModel,
    UnsignedInteger,
)
from polycodebench_core.benchmark_pilot import PilotBenchmarkSlug, timestamp_as_datetime
from polycodebench_core.models import Digest, UtcTimestamp

Prompt102Mode = Literal[
    "genuine_human",
    "approved_model",
    "none",
    "fixture",
    "approved_live",
    "development_mock",
    "development",
    "approved_production",
    "trusted_authority",
]

PILOT_REPLACEMENTS_PER_BENCHMARK: Final = 4
PILOT_EXPECTED_REPLACEMENTS: Final = 12
PILOT_EXPECTED_SEALED_TASKS: Final = 6


class ReplacementCampaignCandidate(StrictAuditModel):
    """One candidate's immutable review and actual author/checker usage evidence."""

    benchmark_slug: PilotBenchmarkSlug
    competency_slice_id: str = Field(min_length=1, max_length=128)
    benchmark_ref: AuditDocumentRef
    plan_ref: AuditDocumentRef
    validation_ref: AuditDocumentRef
    task_ref: EntityRef
    source_id: str = Field(min_length=1, max_length=512)
    source_family_id: str = Field(min_length=1, max_length=512)
    rights_evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=64)
    ancestry_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=256)
    rights_state: Literal["approved", "denied", "pending"]
    validation_state: Literal["accepted", "pending", "rejected"]
    lineage_state: Literal[
        "verified_source_family", "verified_independent", "uncertain", "prohibited"
    ]
    author_ref: EntityRef
    checker_refs: tuple[EntityRef, ...] = Field(min_length=1, max_length=32)
    reviewer_ref: EntityRef
    authoring_mode: Literal["genuine_human", "approved_model"]
    authoring_evidence_ref: ImmutableArtifactRef
    checker_evidence_ref: ImmutableArtifactRef
    usage_evidence_ref: ImmutableArtifactRef
    plan_frozen_at: UtcTimestamp
    work_started_at: UtcTimestamp
    rounds_spent: UnsignedInteger
    cost_micro_usd_spent: UnsignedInteger
    wall_seconds_spent: UnsignedInteger
    plan_max_drafts: int = Field(ge=1, le=1_000)
    plan_max_rounds: int = Field(ge=1, le=8)
    plan_max_cost_micro_usd: UnsignedInteger
    plan_max_wall_seconds: int = Field(ge=1, le=604_800)
    source_quota_maximum: UnsignedInteger = Field(ge=1, le=1_000)

    @model_validator(mode="after")
    def candidate_evidence_is_scoped(self) -> ReplacementCampaignCandidate:
        if (
            self.benchmark_ref.kind != "benchmark_snapshot"
            or self.plan_ref.kind != "replacement_plan"
            or self.validation_ref.kind != "replacement_validation"
            or self.task_ref.entity_kind != "task_version"
        ):
            raise ValueError("replacement evidence must bind benchmark, plan, validation and task")
        participants = (self.author_ref, *self.checker_refs, self.reviewer_ref)
        if any(item.entity_kind != "reviewer" for item in participants):
            raise ValueError("replacement author, checkers and reviewer must be typed reviewers")
        if len({item.entity_id for item in participants}) != len(participants):
            raise ValueError("replacement author, checkers and reviewer must be independent")
        if len(set(self.rights_evidence_refs)) != len(self.rights_evidence_refs) or len(
            set(self.ancestry_refs)
        ) != len(self.ancestry_refs):
            raise ValueError("replacement rights and ancestry references must be unique")
        if any(
            ref.kind not in {"task_fingerprint", "match_evidence", "benchmark_snapshot"}
            for ref in self.ancestry_refs
        ):
            raise ValueError("replacement ancestry must cite task, match or benchmark evidence")
        artifacts = (
            self.authoring_evidence_ref,
            self.checker_evidence_ref,
            self.usage_evidence_ref,
        )
        if any(item.visibility != "private" for item in artifacts):
            raise ValueError("replacement work and usage evidence must remain private")
        if len({item.artifact_id for item in artifacts}) != len(artifacts):
            raise ValueError("replacement author, checker and usage evidence must be distinct")
        if timestamp_as_datetime(self.plan_frozen_at) >= timestamp_as_datetime(
            self.work_started_at
        ):
            raise ValueError("replacement plan must be frozen before candidate work starts")
        if self.rounds_spent > self.plan_max_rounds:
            raise ValueError("replacement candidate exceeds its frozen round cap")
        if self.cost_micro_usd_spent > self.plan_max_cost_micro_usd:
            raise ValueError("replacement candidate exceeds its frozen cost cap")
        if self.wall_seconds_spent > self.plan_max_wall_seconds:
            raise ValueError("replacement candidate exceeds its frozen wall-time cap")
        return self


class SealedTaskCampaignEvidence(StrictAuditModel):
    """Reference-only evidence for one screened, committed and disclosed private task."""

    task_ref: EntityRef
    manifest_ref: AuditDocumentRef
    screening_event_ref: AuditDocumentRef
    disclosure_event_ref: AuditDocumentRef
    commitment_digest: Digest
    owner_author_ref: EntityRef
    independent_reviewer_ref: EntityRef
    lineage_state: Literal["verified_independent", "uncertain", "not_reviewed"]
    local_screening_result: Literal["passed", "failed"]
    disclosure_result: Literal["authorized", "denied", "failed"]
    source_evidence_mode: Literal["none", "fixture", "approved_live"]
    model_evidence_mode: Literal["none", "development_mock", "approved_live"]
    key_provider_mode: Literal["development", "approved_production"]
    timestamp_provider_mode: Literal["development", "trusted_authority"]
    timestamp_receipt_ref: ImmutableArtifactRef | None
    lineage_evidence_ref: ImmutableArtifactRef
    screening_evidence_ref: ImmutableArtifactRef

    @model_validator(mode="after")
    def sealed_evidence_preserves_private_scope(self) -> SealedTaskCampaignEvidence:
        if (
            self.task_ref.entity_kind != "task_version"
            or self.manifest_ref.kind != "sealed_manifest"
            or self.screening_event_ref.kind != "seal_access_event"
            or self.disclosure_event_ref.kind != "seal_access_event"
        ):
            raise ValueError("sealed campaign refs must bind a task, manifest and access events")
        if self.screening_event_ref == self.disclosure_event_ref:
            raise ValueError("local screening and disclosure require distinct access events")
        if (
            self.owner_author_ref.entity_kind != "reviewer"
            or self.independent_reviewer_ref.entity_kind != "reviewer"
            or self.owner_author_ref.entity_id == self.independent_reviewer_ref.entity_id
        ):
            raise ValueError("sealed task author and independent reviewer must be distinct")
        artifacts = [self.lineage_evidence_ref, self.screening_evidence_ref]
        if self.timestamp_receipt_ref is not None:
            artifacts.append(self.timestamp_receipt_ref)
        if any(item.visibility != "private" for item in artifacts):
            raise ValueError("sealed lineage, screening and timestamp evidence must stay private")
        if self.timestamp_provider_mode == "trusted_authority" and (
            self.timestamp_receipt_ref is None
        ):
            raise ValueError("trusted timestamp mode requires an immutable authority receipt")
        return self

    @property
    def production_mode(self) -> bool:
        return (
            self.source_evidence_mode != "fixture"
            and self.model_evidence_mode != "development_mock"
            and self.key_provider_mode == "approved_production"
            and self.timestamp_provider_mode == "trusted_authority"
        )


class MonitorTickCampaignEvidence(StrictAuditModel):
    """One frozen changed-source monitor tick with bounded actual usage receipts."""

    policy_ref: AuditDocumentRef
    previous_source_ref: AuditDocumentRef
    changed_source_ref: AuditDocumentRef
    coverage_ref: AuditDocumentRef
    query_usage_ref: ImmutableArtifactRef
    alert_history_ref: ImmutableArtifactRef
    alert_refs: tuple[AuditDocumentRef, ...] = Field(max_length=128)
    slot_key: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._:-]+$")
    policy_frozen_at: UtcTimestamp
    search_started_at: UtcTimestamp
    rescan_state: Literal["complete", "partial", "failed"]
    query_units_reserved: UnsignedInteger = Field(ge=1)
    query_units_used: UnsignedInteger
    cost_micro_usd_reserved: UnsignedInteger
    cost_micro_usd_used: UnsignedInteger
    diagnostic_model_calls: Literal[0] = 0
    external_alert_deliveries: Literal[0] = 0

    @model_validator(mode="after")
    def monitor_tick_is_finite_and_private(self) -> MonitorTickCampaignEvidence:
        if (
            self.policy_ref.kind != "monitor_policy"
            or self.previous_source_ref.kind != "corpus_snapshot"
            or self.changed_source_ref.kind != "corpus_snapshot"
            or self.coverage_ref.kind != "coverage_manifest"
        ):
            raise ValueError("monitor tick must bind policy, corpus snapshots and coverage")
        if self.previous_source_ref == self.changed_source_ref:
            raise ValueError("monitor campaign must reference an actual changed source snapshot")
        if any(item.kind != "monitor_alert" for item in self.alert_refs):
            raise ValueError("monitor tick alert references must identify monitor alerts")
        if len(set(self.alert_refs)) != len(self.alert_refs):
            raise ValueError("monitor tick alert history must not duplicate alert references")
        if (
            self.query_usage_ref.visibility != "private"
            or self.alert_history_ref.visibility != "private"
        ):
            raise ValueError("monitor usage and alert-history evidence must remain private")
        if timestamp_as_datetime(self.policy_frozen_at) >= timestamp_as_datetime(
            self.search_started_at
        ):
            raise ValueError("monitor policy and source scope must precede the rescan")
        if self.query_units_used > self.query_units_reserved:
            raise ValueError("monitor actual query usage exceeds its reservation")
        if self.cost_micro_usd_used > self.cost_micro_usd_reserved:
            raise ValueError("monitor actual cost exceeds its reservation")
        return self


class Prompt102BenchmarkCount(StrictAuditModel):
    benchmark_slug: PilotBenchmarkSlug
    competency_slice_id: str | None
    observed: UnsignedInteger = Field(le=PILOT_EXPECTED_REPLACEMENTS)
    accepted: UnsignedInteger = Field(le=PILOT_EXPECTED_REPLACEMENTS)

    @model_validator(mode="after")
    def accepted_count_is_a_subset(self) -> Prompt102BenchmarkCount:
        if self.accepted > self.observed:
            raise ValueError("accepted replacement count cannot exceed observed candidates")
        return self


class Prompt102ModeCount(StrictAuditModel):
    axis: Literal["authoring", "source", "model", "key_provider", "timestamp_provider"]
    mode: Prompt102Mode
    reported_count: UnsignedInteger
    verified_count: UnsignedInteger

    @model_validator(mode="after")
    def mode_is_valid_for_axis(self) -> Prompt102ModeCount:
        allowed = {
            "authoring": {"genuine_human", "approved_model"},
            "source": {"none", "fixture", "approved_live"},
            "model": {"none", "development_mock", "approved_live"},
            "key_provider": {"development", "approved_production"},
            "timestamp_provider": {"development", "trusted_authority"},
        }
        if self.mode not in allowed[self.axis]:
            raise ValueError("campaign mode label does not belong to its evidence axis")
        if self.verified_count > self.reported_count:
            raise ValueError("verified mode count cannot exceed reported evidence")
        return self


class Prompt102CampaignReport(StrictAuditModel):
    status: Literal["blocked", "partial", "ready"]
    expected_replacements: Literal[12] = PILOT_EXPECTED_REPLACEMENTS
    observed_replacements: UnsignedInteger = Field(le=PILOT_EXPECTED_REPLACEMENTS)
    accepted_replacements: UnsignedInteger = Field(le=PILOT_EXPECTED_REPLACEMENTS)
    verified_replacement_candidates: UnsignedInteger = Field(le=PILOT_EXPECTED_REPLACEMENTS)
    replacements_by_benchmark: tuple[Prompt102BenchmarkCount, ...] = Field(
        min_length=3, max_length=3
    )
    mode_counts: tuple[Prompt102ModeCount, ...] = Field(min_length=12, max_length=12)
    replacement_evidence_verified: bool
    replacement_budgets_reconciled: bool
    expected_sealed_tasks: Literal[6] = PILOT_EXPECTED_SEALED_TASKS
    observed_sealed_tasks: UnsignedInteger = Field(le=PILOT_EXPECTED_SEALED_TASKS)
    locally_screened_tasks: UnsignedInteger = Field(le=PILOT_EXPECTED_SEALED_TASKS)
    authorized_disclosures: UnsignedInteger = Field(le=PILOT_EXPECTED_SEALED_TASKS)
    verified_sealed_tasks: UnsignedInteger = Field(le=PILOT_EXPECTED_SEALED_TASKS)
    production_mode_sealed_tasks: UnsignedInteger = Field(le=PILOT_EXPECTED_SEALED_TASKS)
    monitor_ticks: Literal[0, 1]
    changed_source_rescans: Literal[0, 1]
    monitor_evidence_verified: bool
    query_units_reserved: UnsignedInteger
    query_units_reported: UnsignedInteger
    query_units_verified: UnsignedInteger
    cost_micro_usd_reserved: UnsignedInteger
    cost_micro_usd_reported: UnsignedInteger
    cost_micro_usd_verified: UnsignedInteger
    monitor_alerts_observed: UnsignedInteger = Field(le=128)
    input_scope_digest: Digest
    this_readiness_check_dispatched_work: Literal[False] = False
    blockers: tuple[str, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def campaign_state_matches_evidence(self) -> Prompt102CampaignReport:
        if self.accepted_replacements > self.observed_replacements:
            raise ValueError("accepted replacement count cannot exceed observed candidates")
        if self.accepted_replacements > self.verified_replacement_candidates:
            raise ValueError("accepted replacements must have verified evidence")
        if self.verified_replacement_candidates > self.observed_replacements:
            raise ValueError("verified replacement count cannot exceed observed candidates")
        if sum(item.observed for item in self.replacements_by_benchmark) != (
            self.observed_replacements
        ):
            raise ValueError("replacement benchmark counts must reconcile to the observed total")
        if sum(item.accepted for item in self.replacements_by_benchmark) != (
            self.accepted_replacements
        ):
            raise ValueError("accepted benchmark counts must reconcile to the accepted total")
        if len({item.benchmark_slug for item in self.replacements_by_benchmark}) != 3:
            raise ValueError("campaign report requires one count for each benchmark")
        expected_modes = {
            "authoring": {"genuine_human", "approved_model"},
            "source": {"none", "fixture", "approved_live"},
            "model": {"none", "development_mock", "approved_live"},
            "key_provider": {"development", "approved_production"},
            "timestamp_provider": {"development", "trusted_authority"},
        }
        if len({(item.axis, item.mode) for item in self.mode_counts}) != 12 or any(
            {item.mode for item in self.mode_counts if item.axis == axis} != modes
            for axis, modes in expected_modes.items()
        ):
            raise ValueError("campaign report must preserve every declared execution mode")
        for axis in expected_modes:
            counts = [item for item in self.mode_counts if item.axis == axis]
            expected_reported = (
                self.observed_replacements if axis == "authoring" else self.observed_sealed_tasks
            )
            expected_verified = (
                self.verified_replacement_candidates
                if axis == "authoring"
                else self.verified_sealed_tasks
            )
            if (
                sum(item.reported_count for item in counts) != expected_reported
                or sum(item.verified_count for item in counts) != expected_verified
            ):
                raise ValueError("campaign mode counts must reconcile to evidence totals")
        if self.replacement_evidence_verified != (
            self.observed_replacements > 0
            and self.verified_replacement_candidates == self.observed_replacements
        ):
            raise ValueError("replacement verification flag must reconcile to verified records")
        if self.status == "ready" and (
            any(item.competency_slice_id is None for item in self.replacements_by_benchmark)
            or len({item.competency_slice_id for item in self.replacements_by_benchmark}) != 3
        ):
            raise ValueError("ready replacement campaign requires three frozen benchmark slices")
        if self.locally_screened_tasks > self.observed_sealed_tasks:
            raise ValueError("locally screened tasks cannot exceed observed sealed tasks")
        if self.authorized_disclosures > self.locally_screened_tasks:
            raise ValueError("authorized disclosures require local screening evidence")
        if self.verified_sealed_tasks > self.observed_sealed_tasks:
            raise ValueError("verified sealed tasks cannot exceed the observed count")
        if self.production_mode_sealed_tasks > self.verified_sealed_tasks:
            raise ValueError("production-mode tasks must be independently verified")
        if not self.query_units_verified <= self.query_units_reported <= self.query_units_reserved:
            raise ValueError("monitor query usage must reconcile from verified to reserved units")
        if (
            not self.cost_micro_usd_verified
            <= self.cost_micro_usd_reported
            <= (self.cost_micro_usd_reserved)
        ):
            raise ValueError("monitor cost must reconcile from verified to reserved amounts")
        if (self.status == "ready") != (not self.blockers):
            raise ValueError("campaign status must agree with its explicit blockers")
        if self.status == "ready" and (
            self.observed_replacements != PILOT_EXPECTED_REPLACEMENTS
            or self.accepted_replacements != PILOT_EXPECTED_REPLACEMENTS
            or self.verified_replacement_candidates != PILOT_EXPECTED_REPLACEMENTS
            or not self.replacement_evidence_verified
            or not self.replacement_budgets_reconciled
            or self.observed_sealed_tasks != PILOT_EXPECTED_SEALED_TASKS
            or self.locally_screened_tasks != PILOT_EXPECTED_SEALED_TASKS
            or self.authorized_disclosures != PILOT_EXPECTED_SEALED_TASKS
            or self.verified_sealed_tasks != PILOT_EXPECTED_SEALED_TASKS
            or self.production_mode_sealed_tasks != PILOT_EXPECTED_SEALED_TASKS
            or self.monitor_ticks != 1
            or self.changed_source_rescans != 1
            or not self.monitor_evidence_verified
            or self.query_units_verified != self.query_units_reported
            or self.cost_micro_usd_verified != self.cost_micro_usd_reported
        ):
            raise ValueError(
                "ready campaign requires every live replacement, seal and monitor gate"
            )
        return self
