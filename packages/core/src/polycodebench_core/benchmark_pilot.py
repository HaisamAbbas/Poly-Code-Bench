"""Strict evidence contracts for the bounded three-benchmark audit pilot."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Final, Literal
from uuid import UUID

from pydantic import Field, model_validator

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    ImmutableArtifactRef,
    StrictAuditModel,
    UnsignedInteger,
)
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import Decimal6, Digest, Seed64, UtcTimestamp

PilotBenchmarkSlug = Literal["humaneval", "mbpp", "swe-bench-verified"]
PilotSourceGroup = Literal["benchmark-repositories", "github", "hugging-face"]
PilotRelationLabel = Literal[
    "exact_component",
    "near_exact_component",
    "semantic_duplicate",
    "shared_family",
    "shared_concept",
    "boilerplate",
    "self_source",
    "no_substantive_match",
    "unknown",
]
PILOT_BENCHMARKS: tuple[tuple[PilotBenchmarkSlug, str, str], ...] = (
    ("humaneval", "6d43fb980f9fee3c892a914eda09951f772ad10d", "all"),
    ("mbpp", "a1e7371c5e006f4e8b314bd23d99220d2fe44c51", "test"),
    ("swe-bench-verified", "78f471bf655a3137b2e8a75af1501690ec009ec3", "test"),
)
PILOT_SAMPLE_PER_BENCHMARK = 100
MAX_CALIBRATION_BOOTSTRAP_FAMILY_DRAWS: Final = 20_000_000
PILOT_MAX_CANDIDATES_PER_TASK = 100
PILOT_MAX_CANDIDATES_PER_SOURCE = 20
PILOT_REQUIRED_SOURCE_GROUPS: tuple[PilotSourceGroup, ...] = (
    "benchmark-repositories",
    "github",
    "hugging-face",
)


class PilotDatasetReadiness(StrictAuditModel):
    """Evidence available for one frozen dataset slice, never inferred from catalog metadata."""

    benchmark_slug: Literal["humaneval", "mbpp", "swe-bench-verified"]
    revision: str | None
    split: str | None
    variant: Literal["official", "original", "sanitized", "verified"] | None
    sample_seed: Seed64 | None
    source_digest: Digest | None
    source_artifact_ref: ImmutableArtifactRef | None
    rights_evidence_digest: Digest | None
    eligible_ids: tuple[str, ...] = Field(max_length=10_000)
    selected_ids: tuple[str, ...] = Field(max_length=100)
    membership_digest: Digest | None
    plan_frozen_at: UtcTimestamp | None
    search_started_at: UtcTimestamp | None

    @model_validator(mode="after")
    def source_artifact_matches_declared_digest(self) -> PilotDatasetReadiness:
        if (
            self.source_artifact_ref is not None
            and self.source_digest is not None
            and self.source_artifact_ref.digest != self.source_digest
        ):
            raise ValueError("source artifact reference must bind the exact imported bytes digest")
        return self


class PilotSourceReadiness(StrictAuditModel):
    """One bounded source snapshot and its separately verified authorization/conformance."""

    source_group: Literal["benchmark-repositories", "github", "hugging-face"]
    snapshot_digest: Digest | None
    scope_evidence_digest: Digest | None
    max_requests: int = Field(ge=0, le=2_000)
    max_response_bytes: int = Field(ge=0, le=5_242_880)
    max_total_bytes: int = Field(ge=0, le=536_870_912)
    plan_frozen_at: UtcTimestamp | None
    search_started_at: UtcTimestamp | None


class PilotCalibrationSourceScope(StrictAuditModel):
    source_group: Literal["benchmark-repositories", "github", "hugging-face"]
    source_snapshot_ref: AuditDocumentRef

    @model_validator(mode="after")
    def source_snapshot_is_typed(self) -> PilotCalibrationSourceScope:
        if self.source_snapshot_ref.kind != "corpus_snapshot":
            raise ValueError("calibration scope references must identify corpus snapshots")
        return self


class PilotCalibrationHeldOutPair(StrictAuditModel):
    pair_digest: Digest
    family_digest: Digest


class PilotPreflight(StrictAuditModel):
    """Read-only preflight; ``ready`` is impossible without all finite-scope evidence."""

    status: Literal["blocked", "ready"]
    expected_benchmarks: Literal[3] = 3
    expected_tasks: Literal[300] = 300
    input_scope_digest: Digest
    review_plan_digest: Digest | None
    max_candidates_per_task: Literal[100] = 100
    max_candidates_per_source: Literal[20] = 20
    maximum_candidate_slots: Literal[30_000] = 30_000
    datasets_with_100_ids: int = Field(ge=0, le=3)
    datasets_with_verified_population: int = Field(ge=0, le=3)
    approved_source_snapshots: int = Field(ge=0, le=3)
    source_requests_capped: bool
    source_response_bytes_capped: bool
    independent_review_plan_frozen: bool
    independent_reviewer_roster_verified: bool
    this_preflight_dispatched_queries: Literal[False] = False
    missing_inputs: tuple[str, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def status_matches_evidence(self) -> PilotPreflight:
        if (self.status == "ready") != (not self.missing_inputs):
            raise ValueError("preflight readiness must agree with its explicit missing inputs")
        if self.status == "ready" and (
            self.datasets_with_100_ids != self.expected_benchmarks
            or self.datasets_with_verified_population != self.expected_benchmarks
            or self.approved_source_snapshots != 3
            or not self.source_requests_capped
            or not self.source_response_bytes_capped
            or not self.independent_review_plan_frozen
            or not self.independent_reviewer_roster_verified
        ):
            raise ValueError("ready pilot preflight requires all three samples and source scopes")
        return self


class PilotCalibrationPlan(StrictAuditModel):
    """Preregistered, family-held-out detector evaluation scope and acceptance gates."""

    plan_id: UUID
    frozen_at: UtcTimestamp
    sample_plan_digest: Digest
    detector_config_digest: Digest
    relation_rubric_digest: Digest
    approved_source_scopes: tuple[PilotCalibrationSourceScope, ...] = Field(
        min_length=3, max_length=32
    )
    held_out_pairs: tuple[PilotCalibrationHeldOutPair, ...] = Field(
        min_length=100, max_length=2_000
    )
    analysis_seed: Seed64
    bootstrap_replicates: int = Field(ge=2_000, le=20_000)
    minimum_precision_lower_95: Decimal6 = "0.950000"
    minimum_recall_lower_95: Decimal6 | None
    maximum_false_positive_rate_upper_95: Decimal6 | None
    maximum_unknown_fraction: Decimal6
    interval_method: Literal["family_cluster_percentile_sha256_v1"] = (
        "family_cluster_percentile_sha256_v1"
    )
    maximum_bootstrap_family_draws: Literal[20_000_000] = MAX_CALIBRATION_BOOTSTRAP_FAMILY_DRAWS
    plan_author_subject: str = Field(min_length=1, max_length=255)
    detector_operator_subject: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def held_out_scope_is_frozen(self) -> PilotCalibrationPlan:
        source_scopes = {
            (
                scope.source_group,
                scope.source_snapshot_ref.document_id,
                scope.source_snapshot_ref.digest,
            )
            for scope in self.approved_source_scopes
        }
        if len(source_scopes) != len(self.approved_source_scopes):
            raise ValueError("approved calibration source scopes must be unique")
        if not set(PILOT_REQUIRED_SOURCE_GROUPS) <= {
            scope.source_group for scope in self.approved_source_scopes
        }:
            raise ValueError(
                "calibration plans must freeze benchmark, GitHub and Hugging Face scopes"
            )
        pair_digests = [item.pair_digest for item in self.held_out_pairs]
        if len(set(pair_digests)) != len(pair_digests):
            raise ValueError("held-out pair digests must be unique")
        family_digests = {item.family_digest for item in self.held_out_pairs}
        if len(family_digests) < 30:
            raise ValueError("held-out pairs must cover at least 30 frozen task families")
        estimated_draws = self.bootstrap_replicates * (
            len(family_digests) + 4 * len(self.held_out_pairs)
        )
        if estimated_draws > self.maximum_bootstrap_family_draws:
            raise ValueError("calibration plan exceeds its frozen bootstrap work ceiling")
        if self.plan_author_subject == self.detector_operator_subject:
            raise ValueError("pilot plan author and detector operator must be distinct")
        thresholds = (
            self.minimum_precision_lower_95,
            self.minimum_recall_lower_95,
            self.maximum_false_positive_rate_upper_95,
            self.maximum_unknown_fraction,
        )
        if any(value is not None and Decimal(value) > 1 for value in thresholds):
            raise ValueError("calibration probability thresholds must be within [0,1]")
        return self

    @property
    def held_out_pair_digests(self) -> tuple[Digest, ...]:
        return tuple(item.pair_digest for item in self.held_out_pairs)

    @property
    def held_out_family_digests(self) -> tuple[Digest, ...]:
        return tuple(sorted({item.family_digest for item in self.held_out_pairs}))

    @property
    def digest(self) -> Digest:
        return canonical_digest(self.model_dump(mode="json"))


class PilotCalibrationObservation(StrictAuditModel):
    """One held-out pair, detector result, and two independently recorded human labels."""

    pair_digest: Digest
    family_digest: Digest
    evidence_ref: AuditDocumentRef
    source_snapshot_ref: AuditDocumentRef
    source_group: Literal["benchmark-repositories", "github", "hugging-face"]
    language: Literal[
        "python", "javascript", "typescript", "java", "go", "rust", "c", "cpp", "other"
    ]
    modality: Literal["text", "code", "repository", "image", "other"]
    control_kind: Literal["ordinary", "boilerplate", "self_source"]
    detector_config_digest: Digest
    candidate_prediction: Literal["positive", "negative", "unknown"]
    substantive_prediction: Literal["positive", "negative", "unknown"]
    predicted_at: UtcTimestamp
    reviewer_one_subject: str = Field(min_length=1, max_length=255)
    reviewer_one_vote: PilotRelationLabel
    reviewer_one_at: UtcTimestamp
    reviewer_one_evidence: ImmutableArtifactRef
    reviewer_two_subject: str = Field(min_length=1, max_length=255)
    reviewer_two_vote: PilotRelationLabel
    reviewer_two_at: UtcTimestamp
    reviewer_two_evidence: ImmutableArtifactRef
    adjudicator_subject: str | None = Field(min_length=1, max_length=255)
    adjudicator_vote: PilotRelationLabel | None
    adjudicated_at: UtcTimestamp | None
    adjudication_evidence: ImmutableArtifactRef | None

    @model_validator(mode="after")
    def independent_reviews_and_evidence(self) -> PilotCalibrationObservation:
        if self.evidence_ref.kind != "match_evidence":
            raise ValueError("calibration rows must bind immutable match evidence")
        if self.source_snapshot_ref.kind != "corpus_snapshot":
            raise ValueError("calibration rows must bind a pinned corpus snapshot")
        if self.pair_digest != self.evidence_ref.digest:
            raise ValueError("planned pair digest must bind its immutable match evidence")
        if self.reviewer_one_subject == self.reviewer_two_subject:
            raise ValueError("calibration labels require two distinct human reviewers")
        artifact_refs = [self.reviewer_one_evidence, self.reviewer_two_evidence]
        if any(item.visibility == "public" for item in artifact_refs):
            raise ValueError(
                "independent reviewer label artifacts must remain private or restricted"
            )
        if self.adjudication_evidence is not None:
            if self.adjudication_evidence.visibility == "public":
                raise ValueError("adjudication artifacts must remain private or restricted")
            artifact_refs.append(self.adjudication_evidence)
        if len({item.artifact_id for item in artifact_refs}) != len(artifact_refs):
            raise ValueError("each independent label must retain its own evidence artifact")
        if len({item.digest for item in artifact_refs}) != len(artifact_refs):
            raise ValueError("independent label artifacts must have distinct immutable digests")
        votes_differ = self.reviewer_one_vote != self.reviewer_two_vote
        adjudication = (
            self.adjudicator_subject,
            self.adjudicator_vote,
            self.adjudicated_at,
            self.adjudication_evidence,
        )
        if votes_differ:
            if any(value is None for value in adjudication):
                raise ValueError("conflicting labels require a third-party adjudication record")
            if self.adjudicator_subject in {
                self.reviewer_one_subject,
                self.reviewer_two_subject,
            }:
                raise ValueError("adjudicator must be independent of both reviewers")
        elif any(value is not None for value in adjudication):
            raise ValueError("adjudication is retained only for conflicting independent labels")
        if self.control_kind == "ordinary" and self.truth_relation in {
            "boilerplate",
            "self_source",
        }:
            raise ValueError("boilerplate and self-source labels must be declared controls")
        return self

    @property
    def truth_relation(self) -> PilotRelationLabel:
        if self.reviewer_one_vote == self.reviewer_two_vote:
            return self.reviewer_one_vote
        assert self.adjudicator_vote is not None
        return self.adjudicator_vote

    @property
    def truth(self) -> Literal["positive", "negative", "unknown"]:
        if self.truth_relation in {
            "exact_component",
            "near_exact_component",
            "semantic_duplicate",
        }:
            return "positive"
        if self.truth_relation == "unknown":
            return "unknown"
        return "negative"


class ClusterBootstrapMetric(StrictAuditModel):
    numerator: UnsignedInteger
    denominator: UnsignedInteger
    value: Decimal6 | None
    lower_95: Decimal6 | None
    upper_95: Decimal6 | None
    valid_replicates: int = Field(ge=0, le=20_000)
    total_replicates: int = Field(ge=2_000, le=20_000)
    null_reason: Literal["empty_denominator", "unstable_family_resamples"] | None
    interval_method: Literal["family_cluster_percentile_sha256_v1"] = (
        "family_cluster_percentile_sha256_v1"
    )

    @model_validator(mode="after")
    def estimate_and_bounds_are_consistent(self) -> ClusterBootstrapMetric:
        if self.numerator > self.denominator:
            raise ValueError("metric numerator cannot exceed its denominator")
        if self.denominator == 0:
            if any(value is not None for value in (self.value, self.lower_95, self.upper_95)):
                raise ValueError("empty metric denominators must not contain estimates")
            if self.null_reason != "empty_denominator":
                raise ValueError("empty metric denominators require an explicit reason")
        elif self.value is None:
            if (
                self.lower_95 is not None
                or self.upper_95 is not None
                or self.null_reason != "unstable_family_resamples"
            ):
                raise ValueError("unstable cluster intervals must preserve only the point estimate")
        elif (
            self.lower_95 is None
            or self.upper_95 is None
            or self.null_reason is not None
            or Decimal(self.lower_95) > Decimal(self.value)
            or Decimal(self.value) > Decimal(self.upper_95)
        ):
            raise ValueError("measured cluster intervals must be ordered around their estimate")
        return self


class PilotDetectorStratum(StrictAuditModel):
    axis: Literal["relation", "language", "modality", "source"]
    value: str = Field(min_length=1, max_length=128)
    observed_pairs: UnsignedInteger
    labeled_pairs: UnsignedInteger
    labeled_families: UnsignedInteger
    true_positive: UnsignedInteger
    false_positive: UnsignedInteger
    true_negative: UnsignedInteger
    false_negative: UnsignedInteger
    unknown_pairs: UnsignedInteger
    precision: ClusterBootstrapMetric
    recall: ClusterBootstrapMetric
    false_positive_rate: ClusterBootstrapMetric
    false_negative_rate: ClusterBootstrapMetric

    @model_validator(mode="after")
    def stratum_counts_reconcile(self) -> PilotDetectorStratum:
        scored = self.true_positive + self.false_positive + self.true_negative + self.false_negative
        if scored + self.unknown_pairs != self.observed_pairs:
            raise ValueError("stratum confusion and unknown counts must reconcile")
        if self.labeled_pairs < scored:
            raise ValueError("stratum labels must include every scored pair")
        if self.labeled_families > self.labeled_pairs:
            raise ValueError("stratum family counts cannot exceed labeled pairs")
        expected_metrics = (
            (self.true_positive, self.true_positive + self.false_positive),
            (self.true_positive, self.true_positive + self.false_negative),
            (self.false_positive, self.false_positive + self.true_negative),
            (self.false_negative, self.true_positive + self.false_negative),
        )
        actual_metrics = tuple(
            (item.numerator, item.denominator)
            for item in (
                self.precision,
                self.recall,
                self.false_positive_rate,
                self.false_negative_rate,
            )
        )
        if actual_metrics != expected_metrics:
            raise ValueError("stratum metrics must reconcile to their confusion counts")
        return self


class PilotDetectorCalibrationReport(StrictAuditModel):
    calibration_plan_digest: Digest
    detector_config_digest: Digest
    relation_rubric_digest: Digest
    measurement_state: Literal["unmeasured", "descriptive", "held_out_measured"]
    gate_status: Literal["blocked", "thresholds_met"]
    planned_pairs: int = Field(ge=100, le=2_000)
    observed_pairs: int = Field(ge=0, le=2_000)
    missing_pairs: int = Field(ge=0, le=2_000)
    labeled_pairs: int = Field(ge=0, le=2_000)
    labeled_families: int = Field(ge=0, le=2_000)
    unknown_pairs: int = Field(ge=0, le=2_000)
    source_snapshot_count: int = Field(ge=0, le=32)
    relation_counts: tuple[tuple[str, int], ...] = Field(max_length=9)
    source_counts: tuple[tuple[str, int], ...] = Field(max_length=3)
    strata: tuple[PilotDetectorStratum, ...] = Field(max_length=64)
    true_positive: int = Field(ge=0, le=2_000)
    false_positive: int = Field(ge=0, le=2_000)
    true_negative: int = Field(ge=0, le=2_000)
    false_negative: int = Field(ge=0, le=2_000)
    precision: ClusterBootstrapMetric
    recall: ClusterBootstrapMetric
    false_positive_rate: ClusterBootstrapMetric
    false_negative_rate: ClusterBootstrapMetric
    boilerplate_controls: int = Field(ge=0, le=2_000)
    boilerplate_candidate_hits: int = Field(ge=0, le=2_000)
    boilerplate_rejected: int = Field(ge=0, le=2_000)
    self_source_controls: int = Field(ge=0, le=2_000)
    self_source_candidate_hits: int = Field(ge=0, le=2_000)
    self_source_rejected: int = Field(ge=0, le=2_000)
    control_gate_met: bool
    reviewer_qualification_verified: bool
    calibration_plan_verified: bool
    source_snapshots_verified: bool
    independent_label_evidence_verified: bool
    precision_threshold_met: bool
    recall_threshold_met: bool
    false_positive_rate_threshold_met: bool
    unknown_fraction_threshold_met: bool
    scope_and_sample_gate_met: bool
    semantic_auto_admission_enabled: Literal[False] = False
    blockers: tuple[str, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def report_gate_is_fail_closed(self) -> PilotDetectorCalibrationReport:
        if self.observed_pairs + self.missing_pairs != self.planned_pairs:
            raise ValueError("observed and missing pairs must reconcile to the frozen plan")
        if self.labeled_pairs > self.observed_pairs or self.unknown_pairs > self.observed_pairs:
            raise ValueError("labeled and unknown pair counts cannot exceed observed pairs")
        if self.labeled_families > self.labeled_pairs:
            raise ValueError("labeled family count cannot exceed its pair count")
        scored_pairs = (
            self.true_positive + self.false_positive + self.true_negative + self.false_negative
        )
        if scored_pairs + self.unknown_pairs != self.observed_pairs:
            raise ValueError("known confusion counts and unknown pairs must reconcile")
        if self.boilerplate_candidate_hits > self.boilerplate_controls:
            raise ValueError("boilerplate candidate hits cannot exceed control observations")
        if self.boilerplate_rejected > self.boilerplate_candidate_hits:
            raise ValueError("boilerplate rejections cannot exceed candidate hits")
        if self.self_source_candidate_hits > self.self_source_controls:
            raise ValueError("self-source candidate hits cannot exceed control observations")
        if self.self_source_rejected > self.self_source_candidate_hits:
            raise ValueError("self-source rejections cannot exceed candidate hits")
        if (
            len({name for name, _ in self.relation_counts}) != len(self.relation_counts)
            or sum(count for _, count in self.relation_counts) != self.observed_pairs
        ):
            raise ValueError("relation counts must be unique and reconcile to observed pairs")
        if (
            len({name for name, _ in self.source_counts}) != len(self.source_counts)
            or sum(count for _, count in self.source_counts) != self.observed_pairs
        ):
            raise ValueError("source counts must be unique and reconcile to observed pairs")
        if len({(item.axis, item.value) for item in self.strata}) != len(self.strata):
            raise ValueError("detector metric strata must be unique by axis and value")
        if self.true_positive + self.false_positive > self.observed_pairs:
            raise ValueError("positive predictions must reconcile within observed pairs")
        if self.true_negative + self.false_negative > self.observed_pairs:
            raise ValueError("negative predictions must reconcile within observed pairs")
        expected_metrics = (
            (self.true_positive, self.true_positive + self.false_positive),
            (self.true_positive, self.true_positive + self.false_negative),
            (self.false_positive, self.false_positive + self.true_negative),
            (self.false_negative, self.true_positive + self.false_negative),
        )
        actual_metrics = tuple(
            (item.numerator, item.denominator)
            for item in (
                self.precision,
                self.recall,
                self.false_positive_rate,
                self.false_negative_rate,
            )
        )
        if actual_metrics != expected_metrics:
            raise ValueError("calibration metrics must reconcile to their confusion counts")
        if self.gate_status == "thresholds_met" and (
            self.blockers
            or not self.scope_and_sample_gate_met
            or not self.reviewer_qualification_verified
            or not self.calibration_plan_verified
            or not self.source_snapshots_verified
            or not self.independent_label_evidence_verified
            or not self.precision_threshold_met
            or not self.recall_threshold_met
            or not self.false_positive_rate_threshold_met
            or not self.unknown_fraction_threshold_met
            or not self.control_gate_met
        ):
            raise ValueError("calibration thresholds cannot pass while a required gate is blocked")
        if self.measurement_state == "unmeasured" and self.observed_pairs != 0:
            raise ValueError("unmeasured reports cannot contain observed calibration pairs")
        if self.gate_status == "blocked" and not self.blockers:
            raise ValueError("blocked calibration reports must name at least one blocker")
        return self


def timestamp_as_datetime(value: UtcTimestamp) -> datetime:
    """Convert a validated UTC timestamp for chronology comparisons."""
    return datetime.fromisoformat(value[:-1] + "+00:00")
