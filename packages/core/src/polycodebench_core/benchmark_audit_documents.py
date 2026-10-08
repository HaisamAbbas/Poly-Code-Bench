"""Strict, immutable contracts for canonical benchmark-audit evidence documents."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, ClassVar, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from polycodebench_core.canonical import (
    canonical_envelope,
    canonical_envelope_bytes,
    canonical_json_bytes,
    parse_json_strict,
    sha256_bytes,
)
from polycodebench_core.models import Decimal6, Digest, Seed64, UtcTimestamp

AuditKind = Literal[
    "benchmark_snapshot",
    "task_fingerprint",
    "corpus_snapshot",
    "audit_plan",
    "query_manifest",
    "coverage_manifest",
    "match_evidence",
    "model_context",
    "risk_policy",
    "risk_assessment",
    "temporal_assessment",
    "sealed_manifest",
    "canary_policy",
    "seal_access_event",
    "canary_observation",
    "behavioral_audit_plan",
    "behavioral_method_registry",
    "behavioral_task_validity",
    "behavioral_observation",
    "behavioral_assessment",
    "firewall_policy",
    "firewall_scope",
    "firewall_decision",
    "replacement_source_metadata",
    "replacement_plan",
    "replacement_validation",
    "derived_benchmark_manifest",
    "monitor_policy",
    "benchmark_health",
    "audit_attestation",
]

AuditRunState = Literal[
    "draft",
    "planned",
    "queued",
    "scanning",
    "verifying",
    "assessing",
    "review_required",
    "complete",
    "partial",
    "blocked",
    "cancelled",
]
SourceQueryState = Literal[
    "planned", "queued", "running", "complete", "truncated", "failed", "blocked", "cancelled"
]
MatchRelation = Literal[
    "exact_component",
    "near_exact_component",
    "semantic_duplicate",
    "shared_family",
    "shared_concept",
    "no_substantive_match",
    "unresolved",
]
EvidenceState = Literal[
    "proposed", "verified", "review_required", "accepted", "rejected", "disputed", "superseded"
]
RiskState = Literal[
    "low_observed", "medium_observed", "high_observed", "insufficient_evidence", "not_applicable"
]
TemporalState = Literal[
    "post_declared_cutoff",
    "pre_cutoff_exposure_detected",
    "interval_overlap",
    "unknown_cutoff",
    "unknown_source_time",
    "mutable_model_context",
]
TemporalExplanationCode = Literal[
    "revision_not_pinned",
    "model_update_not_proven_before_cutoff",
    "cutoff_unknown",
    "no_accepted_source_date",
    "exposure_precedes_cutoff",
    "exposure_follows_cutoff",
    "source_cutoff_intervals_overlap",
    "source_interval_incomplete",
    "unverified_source_could_precede_cutoff",
]
FirewallState = Literal["admit", "review", "reject"]
FirewallReason = Literal[
    "scope_incomplete",
    "prohibited_overlap",
    "validity_rejected",
    "validity_pending",
    "rights_denied",
    "rights_pending",
    "high_risk_rejected",
    "risk_scope_incomplete",
    "high_risk_review",
    "temporal_unresolved",
    "temporal_exposure_detected",
    "reviewer_not_independent",
]
SealState = Literal["sealed", "authorized_disclosure", "public_exposed", "compromised", "retired"]

_StrictModel = ConfigDict(extra="forbid", strict=True, frozen=True)
NonEmpty = Annotated[str, Field(min_length=1, max_length=4096)]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]
SafeInteger = Annotated[int, Field(ge=-(2**53 - 1), le=2**53 - 1)]
_SAFE_ACCESS_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$", re.ASCII)
UnsignedInteger = Annotated[int, Field(ge=0, le=2**53 - 1)]


class StrictAuditModel(BaseModel):
    model_config = _StrictModel


class ImmutableArtifactRef(StrictAuditModel):
    artifact_id: UUID
    digest: Digest
    visibility: Literal["private", "restricted", "public"]
    media_type: ShortText


class AuditDocumentRef(StrictAuditModel):
    document_id: UUID
    digest: Digest
    kind: AuditKind


class EntityRef(StrictAuditModel):
    entity_id: UUID
    entity_kind: ShortText
    digest: Digest | None = None


class TimestampEvidence(StrictAuditModel):
    """A source date with its real precision; date-only sources stay date-only."""

    value: str
    precision: Literal["day", "second", "millisecond", "microsecond", "nanosecond"]
    uncertainty: ShortText | None
    source_ref: AuditDocumentRef | ImmutableArtifactRef | None

    @model_validator(mode="after")
    def value_matches_declared_precision(self) -> TimestampEvidence:
        if self.precision == "day":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.value):
                raise ValueError("day precision requires an ISO date without fabricated time")
            try:
                date.fromisoformat(self.value)
            except ValueError as error:
                raise ValueError("source date is not a valid calendar date") from error
            return self
        fractional_digits = {
            "second": 0,
            "millisecond": 3,
            "microsecond": 6,
            "nanosecond": 9,
        }[self.precision]
        match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z", self.value)
        if match is None or len(match.group(2) or "") != fractional_digits:
            raise ValueError("UTC timestamp precision does not match its declared precision")
        try:
            datetime.fromisoformat(self.value[:-1] + "+00:00")
        except ValueError as error:
            raise ValueError("timestamp is not a valid UTC calendar time") from error
        return self


def timestamp_evidence_bounds_ns(evidence: TimestampEvidence) -> tuple[int, int]:
    """Return inclusive nanosecond bounds without promoting date-only values to midnight."""
    if evidence.precision == "day":
        value = date.fromisoformat(evidence.value)
        seconds = (value - date(1970, 1, 1)).days * 86_400
        start = seconds * 1_000_000_000
        return start, start + 86_400 * 1_000_000_000 - 1
    whole_text, _, fractional = evidence.value.partition(".")
    whole_text = whole_text.removesuffix("Z")
    whole = datetime.fromisoformat(whole_text)
    seconds = (
        (whole.date() - date(1970, 1, 1)).days * 86_400
        + whole.hour * 3_600
        + whole.minute * 60
        + whole.second
    )
    nanoseconds = int(fractional.removesuffix("Z").ljust(9, "0")) if fractional else 0
    point = seconds * 1_000_000_000 + nanoseconds
    return point, point


class DocumentMetadata(StrictAuditModel):
    created_at: UtcTimestamp
    timestamp_precision: Literal["second", "millisecond", "microsecond", "nanosecond"]
    actor: ShortText
    trace_id: UUID | None
    row_version: UnsignedInteger

    @model_validator(mode="after")
    def timestamp_precision_matches(self) -> DocumentMetadata:
        digits = len(self.created_at.partition(".")[2].removesuffix("Z"))
        expected = {"second": 0, "millisecond": 3, "microsecond": 6, "nanosecond": 9}[
            self.timestamp_precision
        ]
        if digits != expected:
            raise ValueError("created_at precision does not match timestamp_precision")
        return self


class DecimalMeasurement(StrictAuditModel):
    value: Decimal6 | None
    null_reason: (
        Literal["not_run", "unavailable", "not_applicable", "insufficient_coverage", "withheld"]
        | None
    )

    @model_validator(mode="after")
    def null_value_has_reason(self) -> DecimalMeasurement:
        if (self.value is None) != (self.null_reason is not None):
            raise ValueError("nullable decimal values require a reason exactly when null")
        if self.value is not None and Decimal(self.value) > Decimal("100.000000"):
            raise ValueError("audit measurements must be within [0,100]")
        return self


class BenchmarkSnapshotPayload(StrictAuditModel):
    registry_ref: EntityRef
    version: ShortText
    split: ShortText
    membership: tuple[EntityRef, ...]
    components: tuple[EntityRef, ...]
    upstream_rights: tuple[AuditDocumentRef, ...]
    importer_refs: tuple[AuditDocumentRef, ...]


class TaskFingerprintPayload(StrictAuditModel):
    task_ref: EntityRef
    component_refs: tuple[EntityRef, ...]
    normalization_version: ShortText
    tokenizer_version: ShortText
    code_parser_version: ShortText | None
    embedding_version: ShortText | None
    feature_versions: dict[str, ShortText]
    exact_digest: Digest
    normalized_digests: dict[str, Digest]
    code_digests: dict[str, Digest]
    private_vectors: tuple[ImmutableArtifactRef, ...]
    private_features: tuple[ImmutableArtifactRef, ...]


class CorpusSnapshotPayload(StrictAuditModel):
    source_ref: AuditDocumentRef
    version: ShortText
    acquisition_scope: dict[str, Any]
    content_root: Digest
    index_digest: Digest
    date_coverage: tuple[TimestampEvidence, ...]
    rights: tuple[AuditDocumentRef, ...]
    exclusions: tuple[str, ...]
    extraction_policy: AuditDocumentRef


class AuditPlanPayload(StrictAuditModel):
    benchmark_ref: AuditDocumentRef
    task_refs: tuple[EntityRef, ...]
    sample_design: dict[str, Any]
    model_context: AuditDocumentRef | None
    source_plan: tuple[AuditDocumentRef, ...]
    methods: tuple[ShortText, ...]
    policy: AuditDocumentRef
    limits: dict[str, UnsignedInteger]
    visibility: Literal["private", "restricted", "public"]
    seed: Seed64


class QueryManifestPayload(StrictAuditModel):
    audit_ref: AuditDocumentRef
    task_ref: EntityRef
    component_refs: tuple[EntityRef, ...]
    query_type: ShortText
    query_artifact_ref: ImmutableArtifactRef
    connector_snapshot: AuditDocumentRef
    limits: dict[str, UnsignedInteger]
    disclosure_authorization: AuditDocumentRef | None


class CoverageManifestPayload(StrictAuditModel):
    planned_queries: tuple[AuditDocumentRef, ...]
    executed_queries: tuple[AuditDocumentRef, ...]
    eligible_sources: tuple[AuditDocumentRef, ...]
    counts: dict[str, UnsignedInteger]
    time_windows: tuple[TimestampEvidence, ...]
    outages: tuple[dict[str, Any], ...]
    unsupported_modalities: tuple[ShortText, ...]
    truncation: tuple[dict[str, Any], ...]
    freshness: DecimalMeasurement


class MatchEvidencePayload(StrictAuditModel):
    task_ref: EntityRef
    source_ref: AuditDocumentRef
    matching_spans: tuple[ImmutableArtifactRef, ...]
    features: tuple[ImmutableArtifactRef, ...]
    component_relation: MatchRelation
    source_date_evidence: tuple[TimestampEvidence, ...]
    verifier: EntityRef | None
    confidence: DecimalMeasurement
    review_state: EvidenceState


class MatchSpanV2(StrictAuditModel):
    """Byte offsets bind one claimed source span to one immutable task component."""

    component_ref: EntityRef
    component_artifact_ref: ImmutableArtifactRef
    source_start_byte: int = Field(ge=0, le=2**53 - 1)
    source_end_byte: int = Field(ge=1, le=2**53 - 1)
    source_span_digest: Digest
    field: Literal["question", "answer", "solution", "constraint", "context", "other"]
    content_class: Literal["substantive", "boilerplate", "mixed", "unknown"]
    comparison: Literal["exact_bytes", "text_nfc_lf_preserve_v1"]

    @model_validator(mode="after")
    def span_is_bound(self) -> MatchSpanV2:
        if self.source_end_byte <= self.source_start_byte:
            raise ValueError("match evidence spans must have positive byte length")
        if (
            self.component_ref.digest is None
            or self.component_ref.digest != self.component_artifact_ref.digest
        ):
            raise ValueError("match span component digest must bind its immutable artifact")
        return self


class MatchEvidencePayloadV2(StrictAuditModel):
    """Versioned evidence record; content integrity alone never establishes source trust."""

    task_ref: EntityRef
    target_benchmark_ref: AuditDocumentRef
    retrieval_plan_ref: AuditDocumentRef
    retrieval_result_digest: Digest
    candidate_hit_digest: Digest
    source_snapshot_ref: AuditDocumentRef
    source_document_ref: ImmutableArtifactRef
    source_revision: ShortText
    source_task_ref: EntityRef | None
    source_benchmark_ref: AuditDocumentRef | None
    source_lineage: Literal[
        "independent_copy", "mirror_or_derived", "official_self_import", "unknown"
    ]
    component_refs: tuple[EntityRef, ...] = Field(min_length=1, max_length=16)
    matching_spans: tuple[MatchSpanV2, ...] = Field(min_length=1, max_length=64)
    relation: MatchRelation
    answer_relationship: Literal["same", "equivalent", "different", "unknown", "not_applicable"]
    source_date_state: Literal["verified", "unverified", "unknown", "not_applicable"]
    source_date_evidence: tuple[TimestampEvidence, ...] = Field(max_length=32)
    rights_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=16)
    normalizer_version: ShortText
    parser_version: ShortText | None
    rubric_digest: Digest
    review_state: EvidenceState
    author_subject: ShortText | None
    reviewer_subjects: tuple[ShortText, ...] = Field(max_length=16)
    review_record_ref: ImmutableArtifactRef | None
    counter_evidence_refs: tuple[AuditDocumentRef, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def evidence_scope_is_consistent(self) -> MatchEvidencePayloadV2:
        if self.task_ref.entity_kind != "task_version":
            raise ValueError("match evidence must bind a task version")
        if self.target_benchmark_ref.kind != "benchmark_snapshot":
            raise ValueError("match evidence requires the target benchmark snapshot")
        if self.retrieval_plan_ref.kind != "audit_plan":
            raise ValueError("match evidence must bind its frozen retrieval plan")
        if self.source_snapshot_ref.kind != "corpus_snapshot":
            raise ValueError("match evidence requires the source corpus snapshot")
        if any(item.kind != "corpus_snapshot" for item in self.rights_refs):
            raise ValueError("source rights references must bind corpus snapshot documents")
        component_ids = [item.entity_id for item in self.component_refs]
        if len(set(component_ids)) != len(component_ids):
            raise ValueError("match evidence component references must be unique")
        if any(
            item.entity_kind != "audit_component" or item.digest is None
            for item in self.component_refs
        ):
            raise ValueError("match evidence components require immutable audit-component digests")
        if any(span.component_ref not in self.component_refs for span in self.matching_spans):
            raise ValueError("match spans must bind a declared task component")
        span_keys = [
            (span.component_ref.entity_id, span.source_start_byte, span.source_end_byte)
            for span in self.matching_spans
        ]
        if len(set(span_keys)) != len(span_keys):
            raise ValueError("match spans must not repeat component/source offsets")
        if self.source_task_ref is not None and (
            self.source_task_ref.entity_kind != "task_version"
        ):
            raise ValueError("source task provenance must identify a task version")
        if self.source_task_ref is not None and (
            self.source_task_ref.entity_id == self.task_ref.entity_id
            and self.source_lineage != "official_self_import"
        ):
            raise ValueError("same-task source evidence must be classified as official self-import")
        if self.source_benchmark_ref == self.target_benchmark_ref and (
            self.source_lineage != "official_self_import"
        ):
            raise ValueError(
                "same-benchmark source evidence must be classified as official self-import"
            )
        if self.source_lineage == "official_self_import" and (
            self.source_task_ref is None and self.source_benchmark_ref is None
        ):
            raise ValueError("self-import classification requires benchmark/task provenance")
        if self.source_date_state == "verified" and not self.source_date_evidence:
            raise ValueError("verified source dates require timestamp evidence")
        for evidence in self.source_date_evidence:
            if evidence.source_ref not in {self.source_document_ref, self.source_snapshot_ref}:
                raise ValueError(
                    "source date evidence must cite the bound source artifact or snapshot"
                )
        if self.answer_relationship == "unknown" and self.review_state == "accepted":
            raise ValueError("accepted match evidence requires a resolved answer relationship")
        if self.review_state == "accepted" and self.source_date_state == "unverified":
            raise ValueError("accepted match evidence cannot rely on an unverified source date")
        if self.source_lineage in {"official_self_import", "unknown"} and (
            self.review_state == "accepted"
        ):
            raise ValueError(
                "self-import or unknown lineage cannot be accepted as an independent match"
            )
        if any(span.content_class != "substantive" for span in self.matching_spans) and (
            self.review_state == "accepted"
        ):
            raise ValueError(
                "boilerplate, mixed or unknown spans cannot be accepted as substantive"
            )
        if self.relation in {"unresolved", "no_substantive_match"} and (
            self.review_state == "accepted"
        ):
            raise ValueError("unresolved or no-match relations cannot be accepted as duplicates")
        if self.review_state == "accepted" and (
            self.author_subject is None
            or self.review_record_ref is None
            or not self.reviewer_subjects
        ):
            raise ValueError(
                "accepted match evidence requires an author and immutable review record"
            )
        if len(set(self.reviewer_subjects)) != len(self.reviewer_subjects):
            raise ValueError("match evidence reviewer identities must be unique")
        if self.author_subject is not None and self.author_subject in self.reviewer_subjects:
            raise ValueError("a match author cannot review or approve their own evidence")
        return self


class RiskPolicyPayload(StrictAuditModel):
    component_mapping: dict[str, str]
    weights: dict[str, Decimal6]
    thresholds: dict[str, Decimal6]
    required_scope: tuple[str, ...]
    applicability: dict[str, Literal["required", "optional", "not_applicable"]]
    missingness: dict[str, ShortText]
    claim_wording: dict[str, ShortText]


class RiskAssessmentPayload(StrictAuditModel):
    plan_ref: AuditDocumentRef
    task_ref: EntityRef
    context_ref: AuditDocumentRef | None
    accepted_evidence: tuple[AuditDocumentRef, ...]
    policy_ref: AuditDocumentRef
    observed_index: DecimalMeasurement
    lower_bound: Decimal6
    upper_bound: Decimal6
    coverage: DecimalMeasurement
    state: RiskState
    explanation: ShortText

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> RiskAssessmentPayload:
        lower = Decimal(self.lower_bound)
        upper = Decimal(self.upper_bound)
        if lower > upper or upper > Decimal("100.000000"):
            raise ValueError("risk assessment lower bound exceeds upper bound")
        if self.observed_index.value is not None and not (
            lower <= Decimal(self.observed_index.value) <= upper
        ):
            raise ValueError("observed risk index must lie within its bounds")
        return self


RiskSignalKey = Literal[
    "publication_age",
    "web_exposure",
    "duplication",
    "corpus_overlap_evidence",
    "popularity",
    "synthetic_similarity",
    "model_familiarity",
    "leakage_history",
]
RiskSignalRole = Literal["M", "C", "E", "L", "context", "diagnostic"]
RiskComponentKey = Literal["M", "C", "E", "L"]
RiskEvidenceState = Literal["accepted", "candidate_only", "rejected", "disputed", "none"]
RiskObservationState = Literal["observed", "unknown", "not_applicable"]
RiskSignalBasis = Literal[
    "exact_or_semantic_duplicate",
    "question_only_overlap",
    "distinctive_solution_overlap",
    "shared_family",
    "concept_or_boilerplate",
    "verified_corpus_overlap",
    "verified_public_substantive",
    "public_metadata_only",
    "verified_private_scope",
    "corroborated_task_model_report",
    "completed_scope_no_match",
    "context_only",
    "behavioral_diagnostic_only",
]

OBSERVED_RISK_SIGNAL_LAYOUT: tuple[
    tuple[RiskSignalKey, RiskSignalRole, Literal["required", "optional"], str], ...
] = (
    (
        "publication_age",
        "context",
        "optional",
        "Earliest evidenced substantive public artifact age.",
    ),
    ("web_exposure", "E", "required", "Accepted substantive copies in declared public sources."),
    ("duplication", "M", "required", "Reviewed exact, near or semantic substantive overlap."),
    ("corpus_overlap_evidence", "C", "required", "Verified corpus/source overlap evidence."),
    ("popularity", "context", "optional", "Timestamped popularity counts; contextual only."),
    (
        "synthetic_similarity",
        "M",
        "required",
        "Verified resemblance or lineage to synthetic items.",
    ),
    (
        "model_familiarity",
        "diagnostic",
        "optional",
        "Controlled behavioral familiarity diagnostic.",
    ),
    ("leakage_history", "L", "required", "Corroborated task/model-specific leakage report."),
)


class RiskSignalDefinition(StrictAuditModel):
    signal: RiskSignalKey
    role: RiskSignalRole
    applicability: Literal["required", "optional"]
    definition: ShortText


class RiskSignalObservation(StrictAuditModel):
    """A traceable signal observation; context and behavior never score by themselves."""

    signal: RiskSignalKey
    state: RiskObservationState
    evidence_state: RiskEvidenceState
    basis: RiskSignalBasis | None
    normalized_value: Decimal6 | None
    raw_value: ShortText | None
    raw_interval: tuple[Decimal6, Decimal6] | None
    raw_unit: Literal["days", "count"] | None
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(max_length=32)
    author_ref: EntityRef | None
    review_ref: AuditDocumentRef | None
    reviewer_ref: EntityRef | None
    configuration_ref: AuditDocumentRef | None
    observed_at: UtcTimestamp | None
    source_lineage_refs: tuple[AuditDocumentRef, ...] = Field(max_length=32)
    verification_state: Literal["verified", "unverified", "not_applicable"]
    rights_state: Literal["approved", "unknown", "not_applicable"]
    unknown_reason: ShortText | None

    @model_validator(mode="after")
    def observation_has_a_valid_basis(self) -> RiskSignalObservation:
        if len({(item.kind, item.document_id) for item in self.source_lineage_refs}) != len(
            self.source_lineage_refs
        ):
            raise ValueError("signal lineage references must be unique")
        if self.state == "unknown":
            if self.evidence_state == "accepted":
                raise ValueError("unknown signals cannot claim accepted evidence")
            if (
                self.normalized_value is not None
                or self.raw_value is not None
                or self.raw_interval is not None
                or self.raw_unit is not None
                or self.basis
            ):
                raise ValueError("unknown signal observations cannot contain a measured value")
            if self.unknown_reason is None:
                raise ValueError("unknown signal observations require a reason")
            return self
        if self.state == "not_applicable":
            if self.evidence_state == "accepted":
                raise ValueError("not-applicable signals cannot claim accepted evidence")
            if (
                self.normalized_value is not None
                or self.raw_value is not None
                or self.raw_interval is not None
                or self.raw_unit is not None
                or self.basis
            ):
                raise ValueError("not-applicable signals cannot contain a measured value")
            if self.unknown_reason is None:
                raise ValueError("not-applicable signals require a reason")
            return self
        if self.unknown_reason is not None or self.basis is None or self.configuration_ref is None:
            raise ValueError("observed signals require a basis and pinned configuration")
        if self.observed_at is None or not self.evidence_refs:
            raise ValueError("observed signals require evidence and an observation timestamp")
        if self.evidence_state == "accepted":
            if (
                not self.evidence_refs
                or self.author_ref is None
                or self.review_ref is None
                or self.reviewer_ref is None
                or self.verification_state != "verified"
                or self.rights_state != "approved"
            ):
                raise ValueError(
                    "accepted signals require verified, rights-approved independent review"
                )
            if (
                self.author_ref.entity_kind != "reviewer"
                or self.reviewer_ref.entity_kind != "reviewer"
            ):
                raise ValueError("accepted signals require human reviewer identities")
            if self.author_ref.entity_id == self.reviewer_ref.entity_id:
                raise ValueError("a signal author cannot independently review their own evidence")
        if self.signal in {"publication_age", "popularity"}:
            if self.basis != "context_only" or self.normalized_value is not None:
                raise ValueError("publication age and popularity are context-only signals")
            if self.raw_interval is None or self.raw_interval[0] > self.raw_interval[1]:
                raise ValueError("context-only signals require an ordered raw-value interval")
            expected_unit = "days" if self.signal == "publication_age" else "count"
            if self.raw_unit != expected_unit:
                raise ValueError("context-only signal interval has the wrong measurement unit")
            if self.signal == "popularity" and self.raw_value is None:
                raise ValueError("popularity observations must identify the timestamped count type")
        elif self.signal == "model_familiarity":
            if self.basis != "behavioral_diagnostic_only":
                raise ValueError("model familiarity must remain a separate behavioral diagnostic")
            if (
                self.raw_value is not None
                or self.raw_interval is not None
                or self.raw_unit is not None
            ):
                raise ValueError("behavioral diagnostics cannot be substituted with context values")
            if self.normalized_value is not None and Decimal(self.normalized_value) > Decimal(
                "1.000000"
            ):
                raise ValueError("behavioral diagnostic values must be within [0,1]")
        else:
            if self.raw_interval is not None or self.raw_unit is not None:
                raise ValueError("scored and diagnostic signals cannot carry context intervals")
            if self.basis == "completed_scope_no_match":
                if (
                    self.normalized_value != "0.000000"
                    or self.evidence_state != "none"
                    or not self.evidence_refs
                    or self.verification_state != "verified"
                    or self.rights_state != "approved"
                ):
                    raise ValueError(
                        "no-match observations require verified scope evidence and zero value"
                    )
                return self
            expected = {
                "duplication": {
                    "exact_or_semantic_duplicate": "1.000000",
                    "question_only_overlap": "0.600000",
                    "distinctive_solution_overlap": "0.800000",
                    "shared_family": "0.300000",
                    "concept_or_boilerplate": "0.000000",
                },
                "synthetic_similarity": {
                    "exact_or_semantic_duplicate": "1.000000",
                    "question_only_overlap": "0.600000",
                    "distinctive_solution_overlap": "0.800000",
                    "shared_family": "0.300000",
                    "concept_or_boilerplate": "0.000000",
                },
                "corpus_overlap_evidence": {"verified_corpus_overlap": "1.000000"},
                "web_exposure": {
                    "verified_public_substantive": "1.000000",
                    "public_metadata_only": "0.300000",
                    "verified_private_scope": "0.000000",
                },
                "leakage_history": {"corroborated_task_model_report": "1.000000"},
            }[self.signal]
            expected_value = expected.get(self.basis)
            if expected_value is None or self.normalized_value != expected_value:
                raise ValueError("signal basis does not match its frozen normalized value")
            if self.raw_value is not None:
                raise ValueError(
                    "scored signals use the frozen normalized value, not free-form values"
                )
            if self.evidence_state == "none":
                raise ValueError("positive or reviewed signals require an explicit evidence state")
        if (
            self.evidence_state == "accepted"
            and self.signal
            in {
                "duplication",
                "corpus_overlap_evidence",
                "web_exposure",
                "synthetic_similarity",
                "leakage_history",
            }
            and not self.evidence_refs
        ):
            raise ValueError("scored signals require evidence references")
        return self


class RiskComponentCoverage(StrictAuditModel):
    component: RiskComponentKey
    planned_units: UnsignedInteger
    complete_units: UnsignedInteger
    failed_units: UnsignedInteger
    blocked_units: UnsignedInteger
    truncated_units: UnsignedInteger
    pending_reviews: UnsignedInteger
    scope_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def counts_fit_the_frozen_scope(self) -> RiskComponentCoverage:
        if self.planned_units == 0:
            raise ValueError("required risk components need a nonempty planned scope")
        if self.complete_units > self.planned_units:
            raise ValueError("complete units cannot exceed planned units")
        if (
            self.complete_units + self.failed_units + self.blocked_units + self.truncated_units
            > self.planned_units
        ):
            raise ValueError("risk coverage outcome counts exceed the planned scope")
        if self.pending_reviews > self.complete_units:
            raise ValueError("pending reviews cannot exceed completed query units")
        return self


class RiskPolicyPayloadV2(StrictAuditModel):
    policy_version: Literal["observed-risk-v1"]
    formula: Literal["50*M+25*C+15*E+10*L"]
    component_weights: dict[RiskComponentKey, Decimal6]
    tier_thresholds: dict[Literal["low", "medium"], Decimal6]
    signal_definitions: tuple[RiskSignalDefinition, ...] = Field(min_length=8, max_length=8)
    required_components: tuple[RiskComponentKey, ...] = Field(min_length=4, max_length=4)
    missingness_policy: Literal["unknown_components_null_bounds_include_unavailable_weight"]
    calibration_state: Literal["proposed", "validated"]
    calibration_evidence_refs: tuple[AuditDocumentRef, ...] = Field(max_length=16)
    claim_policy: Literal["heuristic_only_no_probability_or_cleanliness_claim"]

    @model_validator(mode="after")
    def policy_is_the_frozen_v1_heuristic(self) -> RiskPolicyPayloadV2:
        if self.component_weights != {
            "M": "50.000000",
            "C": "25.000000",
            "E": "15.000000",
            "L": "10.000000",
        }:
            raise ValueError("observed-risk-v1 requires the frozen M/C/E/L weights")
        if self.tier_thresholds != {"low": "25.000000", "medium": "60.000000"}:
            raise ValueError("observed-risk-v1 requires the frozen tier thresholds")
        expected = tuple(
            RiskSignalDefinition(
                signal=signal,
                role=role,
                applicability=applicability,
                definition=definition,
            )
            for signal, role, applicability, definition in OBSERVED_RISK_SIGNAL_LAYOUT
        )
        if self.signal_definitions != expected:
            raise ValueError("observed-risk-v1 signal descriptors cannot be changed in place")
        if self.required_components != ("M", "C", "E", "L"):
            raise ValueError("observed-risk-v1 requires all four score components")
        if self.calibration_state == "validated" and not self.calibration_evidence_refs:
            raise ValueError("validated risk policies require calibration evidence references")
        if self.calibration_state == "proposed" and self.calibration_evidence_refs:
            raise ValueError("proposed risk policies cannot claim calibration evidence")
        return self


class RiskComponentAssessment(StrictAuditModel):
    component: RiskComponentKey
    availability: Literal["measured", "unknown"]
    coverage_state: Literal["complete", "partial", "unavailable"]
    value: Decimal6 | None
    points: Decimal6
    missing_weight: Decimal6
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(max_length=64)
    coverage_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=32)
    reason_codes: tuple[ShortText, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def component_bounds_are_valid(self) -> RiskComponentAssessment:
        value = None if self.value is None else Decimal(self.value)
        points = Decimal(self.points)
        missing_weight = Decimal(self.missing_weight)
        weight = {
            "M": Decimal("50.000000"),
            "C": Decimal("25.000000"),
            "E": Decimal("15.000000"),
            "L": Decimal("10.000000"),
        }[self.component]
        if self.availability == "unknown":
            if value is not None or points != 0 or missing_weight != weight:
                raise ValueError("unknown risk components require a reason and unresolved weight")
        elif value is None or not Decimal(0) <= value <= Decimal(1):
            raise ValueError("measured risk components require a value within [0,1]")
        elif (value * weight).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) != points:
            raise ValueError("risk component points must equal the frozen weight times its value")
        if not Decimal(0) <= points <= weight or not Decimal(0) <= missing_weight <= weight:
            raise ValueError("risk component points and missing weight must lie within [0,100]")
        if self.coverage_state == "complete" and missing_weight != 0:
            raise ValueError("complete component coverage cannot retain unresolved weight")
        if self.coverage_state != "complete" and missing_weight != weight:
            raise ValueError("incomplete component coverage adds its full policy weight to bounds")
        return self


class RiskIndexMeasurement(StrictAuditModel):
    value: Decimal6 | None
    null_reason: Literal["scope_incomplete", "policy_uncalibrated", "not_run"] | None

    @model_validator(mode="after")
    def null_index_has_one_explicit_reason(self) -> RiskIndexMeasurement:
        if (self.value is None) != (self.null_reason is not None):
            raise ValueError("ineligible observed indices require an explicit null reason")
        if self.value is not None and Decimal(self.value) > Decimal("100.000000"):
            raise ValueError("observed risk index must be within [0,100]")
        return self


class RiskAssessmentPayloadV2(StrictAuditModel):
    plan_ref: AuditDocumentRef
    task_ref: EntityRef
    context_ref: AuditDocumentRef | None
    policy_ref: AuditDocumentRef
    scope_state: Literal["complete", "partial", "blocked", "not_run"]
    calibration_state: Literal["proposed", "validated"]
    signal_observations: tuple[RiskSignalObservation, ...] = Field(min_length=8, max_length=8)
    component_coverage: tuple[RiskComponentCoverage, ...] = Field(min_length=4, max_length=4)
    components: tuple[RiskComponentAssessment, ...] = Field(min_length=4, max_length=4)
    accepted_evidence: tuple[AuditDocumentRef, ...] = Field(max_length=256)
    calculated_lower_bound: Decimal6
    calculated_upper_bound: Decimal6
    observed_index: RiskIndexMeasurement
    coverage: DecimalMeasurement
    state: RiskState
    explanation_codes: tuple[ShortText, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def assessment_is_complete_and_bounded(self) -> RiskAssessmentPayloadV2:
        signal_keys = tuple(item.signal for item in self.signal_observations)
        expected_signals = tuple(item[0] for item in OBSERVED_RISK_SIGNAL_LAYOUT)
        if len(set(signal_keys)) != len(signal_keys) or set(signal_keys) != set(expected_signals):
            raise ValueError(
                "risk assessment must retain all eight signal descriptors exactly once"
            )
        for collection, label in (
            (self.component_coverage, "coverage"),
            (self.components, "component assessment"),
        ):
            keys = tuple(item.component for item in collection)
            if len(set(keys)) != 4 or set(keys) != {"M", "C", "E", "L"}:
                raise ValueError(
                    f"risk assessment must retain all four {label} groups exactly once"
                )
        lower = Decimal(self.calculated_lower_bound)
        upper = Decimal(self.calculated_upper_bound)
        if not Decimal(0) <= lower <= upper <= Decimal(100):
            raise ValueError("risk assessment bounds must be ordered within [0,100]")
        component_lower = sum((Decimal(item.points) for item in self.components), Decimal(0))
        component_missing = sum(
            (Decimal(item.missing_weight) for item in self.components), Decimal(0)
        )
        if component_lower != lower or min(Decimal(100), lower + component_missing) != upper:
            raise ValueError("risk bounds must reconcile exactly to the component ledger")
        observed = self.observed_index.value
        if observed is not None:
            if self.scope_state != "complete" or self.calibration_state != "validated":
                raise ValueError(
                    "a tier-eligible observed index requires complete scope and calibration"
                )
            if not lower <= Decimal(observed) <= upper:
                raise ValueError("observed index must lie within its calculated bounds")
            if lower != upper or Decimal(observed) != lower:
                raise ValueError("eligible observed index requires a fully measured exact score")
            expected_state: RiskState = (
                "low_observed"
                if Decimal(observed) < Decimal("25.000000")
                else "medium_observed"
                if Decimal(observed) < Decimal("60.000000")
                else "high_observed"
            )
            if self.state != expected_state:
                raise ValueError("risk tier must match the frozen threshold boundaries")
        if self.state in {"low_observed", "medium_observed"} and observed is None:
            raise ValueError("low and medium tiers require a calibrated complete observed index")
        if self.state == "high_observed" and observed is None and lower < Decimal("60.000000"):
            raise ValueError("partial high tier requires a lower bound at or above 60")
        if self.state == "insufficient_evidence" and observed is not None:
            raise ValueError("insufficient evidence cannot expose a calibrated index")
        if self.state == "not_applicable":
            raise ValueError("observed-risk-v1 requires all four score components")
        if observed is None:
            if lower >= Decimal("60.000000") and self.state != "high_observed":
                raise ValueError("high observed lower bounds cannot be downgraded to insufficient")
            if lower < Decimal("60.000000") and self.state == "high_observed":
                raise ValueError("partial high tier requires a lower bound at or above 60")
        expected_null_reason = (
            "not_run"
            if self.scope_state == "not_run"
            else "scope_incomplete"
            if self.scope_state != "complete"
            else "policy_uncalibrated"
        )
        if observed is None and self.observed_index.null_reason != expected_null_reason:
            raise ValueError("ineligible observed index has the wrong null reason")
        expected_coverage = Decimal(
            sum(1 for item in self.components if item.coverage_state == "complete") * 25
        )
        if self.coverage.value is None or Decimal(self.coverage.value) != expected_coverage:
            raise ValueError("risk coverage must report the completed component-scope percentage")
        expected_accepted_refs = {
            (ref.kind, ref.document_id)
            for observation in self.signal_observations
            if observation.evidence_state == "accepted"
            for ref in observation.evidence_refs
        }
        actual_accepted_refs = {(ref.kind, ref.document_id) for ref in self.accepted_evidence}
        if actual_accepted_refs != expected_accepted_refs:
            raise ValueError("assessment accepted evidence must match its accepted signal ledger")
        if self.scope_state == "complete" and any(
            item.coverage_state != "complete" for item in self.components
        ):
            raise ValueError(
                "complete risk scope requires complete, reviewed coverage in every group"
            )
        return self


class TemporalAssessmentPayload(StrictAuditModel):
    artifact_ref: ImmutableArtifactRef
    source_chronology: tuple[TimestampEvidence, ...]
    model_revision_ref: AuditDocumentRef | None
    cutoff_evidence: tuple[TimestampEvidence, ...]
    uncertainty: tuple[ShortText, ...]
    exposure_paths: tuple[AuditDocumentRef, ...]
    status: TemporalState


ChronologyEvent = Literal[
    "artifact_creation",
    "git_commit",
    "archive_capture",
    "upstream_public_exposure",
    "corpus_inclusion",
    "benchmark_publication",
    "model_training_cutoff",
    "model_update",
    "task_disclosure",
    "evaluation",
]
ChronologyBasis = Literal[
    "owner_claim",
    "git_commit_metadata",
    "download_metadata",
    "archive_capture",
    "trusted_timestamp_receipt",
    "local_signed_receipt",
    "verified_upstream_record",
    "provider_declared",
]


class ChronologyInterval(StrictAuditModel):
    event: ChronologyEvent
    earliest: TimestampEvidence | None
    latest: TimestampEvidence | None
    basis: ChronologyBasis
    derivation: Literal[
        "direct_source_record",
        "owner_assertion",
        "commit_metadata",
        "archive_capture_time",
        "download_metadata",
        "commitment_receipt_time",
        "provider_declaration",
        "derived",
    ]
    verification_state: Literal["verified", "unverified", "unknown"]
    evidence_ref: AuditDocumentRef | ImmutableArtifactRef | None
    unknown_reason: ShortText | None

    @model_validator(mode="after")
    def interval_preserves_provenance_and_uncertainty(self) -> ChronologyInterval:
        if self.verification_state == "unknown":
            if self.earliest is not None or self.latest is not None or self.unknown_reason is None:
                raise ValueError("unknown chronology requires a reason and no fabricated dates")
            return self
        if self.earliest is None and self.latest is None:
            raise ValueError("known chronology requires at least one interval boundary")
        if self.evidence_ref is None or self.unknown_reason is not None:
            raise ValueError(
                "dated chronology requires an evidence reference and no unknown reason"
            )
        if self.earliest is not None and self.latest is not None:
            earliest_bounds = timestamp_evidence_bounds_ns(self.earliest)
            latest_bounds = timestamp_evidence_bounds_ns(self.latest)
            if earliest_bounds[0] > latest_bounds[1]:
                raise ValueError("chronology earliest boundary is later than its latest boundary")
        if (
            self.basis
            in {
                "owner_claim",
                "git_commit_metadata",
                "download_metadata",
                "local_signed_receipt",
                "provider_declared",
            }
            and self.verification_state == "verified"
        ):
            raise ValueError("self-reported or local chronology cannot be independently verified")
        if self.basis in {
            "archive_capture",
            "trusted_timestamp_receipt",
            "local_signed_receipt",
        } and (self.earliest is not None or self.latest is None):
            raise ValueError("capture and timestamp receipts provide an upper bound only")
        if self.event == "upstream_public_exposure" and self.basis in {
            "local_signed_receipt",
            "trusted_timestamp_receipt",
        }:
            raise ValueError("a commitment receipt does not prove public exposure")
        expected_derivation = {
            "owner_claim": "owner_assertion",
            "git_commit_metadata": "commit_metadata",
            "download_metadata": "download_metadata",
            "archive_capture": "archive_capture_time",
            "trusted_timestamp_receipt": "commitment_receipt_time",
            "local_signed_receipt": "commitment_receipt_time",
            "provider_declared": "provider_declaration",
        }.get(self.basis)
        if expected_derivation is not None and self.derivation != expected_derivation:
            raise ValueError("chronology derivation must preserve its source semantics")
        return self


class ModelContextPayload(StrictAuditModel):
    provider: ShortText
    model_alias: ShortText | None
    model_revision: ShortText | None
    weights_digest: Digest | None
    pin_confidence: Literal[
        "verified_revision",
        "verified_weight_digest",
        "provider_declared",
        "mutable_alias",
        "unknown",
    ]
    pin_evidence_refs: tuple[AuditDocumentRef, ...] = Field(max_length=32)
    training_cutoff: ChronologyInterval | None
    cutoff_confidence: Literal["verified", "provider_declared", "unknown"]
    update_history: tuple[ChronologyInterval, ...] = Field(max_length=64)
    retrieval_mode: Literal[
        "none",
        "provider_retrieval",
        "tool_augmented",
        "unknown",
    ]
    retrieval_policy_ref: AuditDocumentRef | None
    tool_policy_ref: AuditDocumentRef | None
    prior_delivery_refs: tuple[AuditDocumentRef, ...] = Field(max_length=64)
    recorded_at: UtcTimestamp

    @model_validator(mode="after")
    def model_identity_and_cutoff_are_consistent(self) -> ModelContextPayload:
        if self.pin_confidence == "verified_revision":
            if self.model_revision is None or not self.pin_evidence_refs:
                raise ValueError("verified model revisions require a revision and pin evidence")
        elif self.pin_confidence == "verified_weight_digest":
            if self.weights_digest is None or not self.pin_evidence_refs:
                raise ValueError("verified weight pins require a digest and pin evidence")
        elif self.pin_confidence == "provider_declared":
            if self.model_revision is None or not self.pin_evidence_refs:
                raise ValueError(
                    "provider-declared pins require a revision and declaration evidence"
                )
        elif self.pin_confidence == "mutable_alias":
            if (
                self.model_alias is None
                or self.model_revision is not None
                or self.weights_digest is not None
            ):
                raise ValueError(
                    "mutable aliases cannot claim an immutable revision or weight digest"
                )
        elif self.model_revision is not None or self.weights_digest is not None:
            raise ValueError("unknown model pins cannot carry an immutable revision or digest")
        if self.cutoff_confidence == "unknown":
            if self.training_cutoff is not None:
                raise ValueError("unknown training cutoff cannot carry a date interval")
        elif self.training_cutoff is None or self.training_cutoff.event != "model_training_cutoff":
            raise ValueError("declared training cutoffs require a model cutoff interval")
        elif self.training_cutoff.earliest is None or self.training_cutoff.latest is None:
            raise ValueError("model cutoff intervals require both uncertainty boundaries")
        elif self.cutoff_confidence == "verified" and (
            self.training_cutoff.verification_state != "verified"
            or self.training_cutoff.basis != "verified_upstream_record"
        ):
            raise ValueError(
                "verified cutoff confidence requires an independently verified provider record"
            )
        elif self.cutoff_confidence == "provider_declared" and (
            self.training_cutoff.basis != "provider_declared"
            or self.training_cutoff.verification_state != "unverified"
        ):
            raise ValueError("provider-declared cutoffs must retain their unverified source class")
        if any(item.event != "model_update" for item in self.update_history):
            raise ValueError("model update history can contain only model-update chronology")
        if self.retrieval_mode == "unknown" and self.retrieval_policy_ref is not None:
            raise ValueError("unknown retrieval policy cannot claim a policy reference")
        if self.retrieval_mode != "unknown" and self.retrieval_policy_ref is None:
            raise ValueError("known retrieval modes require their policy evidence")
        if self.retrieval_mode == "tool_augmented" and self.tool_policy_ref is None:
            raise ValueError("tool-augmented model contexts require a tool policy reference")
        return self


class LocalTimestampReceipt(StrictAuditModel):
    schema_version: Literal[1]
    adapter_version: Literal["local-ed25519-v1"]
    commitment: Digest
    received_at: TimestampEvidence
    algorithm: Literal["Ed25519"]
    key_ref: EntityRef
    public_key_b64: ShortText
    trust_label: Literal["local_non_independent"]
    signature_b64: ShortText

    @model_validator(mode="after")
    def receipt_has_local_signing_identity(self) -> LocalTimestampReceipt:
        if self.key_ref.entity_kind != "signing_key":
            raise ValueError("timestamp receipts require a signing-key identity reference")
        return self


class TrustedTimestampProof(StrictAuditModel):
    provider: ShortText
    adapter_version: ShortText
    protocol: Literal["rfc3161", "provider_signed_v1"]
    commitment: Digest
    token_ref: ImmutableArtifactRef
    receipt_time: TimestampEvidence | None
    certificate_refs: tuple[AuditDocumentRef, ...] = Field(max_length=16)
    verifier_ref: AuditDocumentRef | None
    verification_state: Literal["pending", "verified", "unavailable", "invalid"]

    @model_validator(mode="after")
    def verified_timestamp_requires_verification_evidence(self) -> TrustedTimestampProof:
        if self.token_ref.visibility != "private":
            raise ValueError("timestamp authority tokens must remain private artifacts")
        if self.verification_state == "verified" and (
            self.receipt_time is None or not self.certificate_refs or self.verifier_ref is None
        ):
            raise ValueError(
                "verified authority receipts require time, certificates and verifier evidence"
            )
        return self


class HidingCommitmentPayload(StrictAuditModel):
    scheme: Literal["sha256-salted-v1"]
    commitment: Digest
    nonce_ref: ImmutableArtifactRef
    local_receipt: LocalTimestampReceipt | None
    trusted_proof: TrustedTimestampProof | None

    @model_validator(mode="after")
    def hiding_nonce_is_private_and_receipts_are_bound(self) -> HidingCommitmentPayload:
        if self.nonce_ref.visibility != "private":
            raise ValueError("timestamp commitment nonce artifacts must remain private")
        if self.local_receipt is not None and self.local_receipt.commitment != self.commitment:
            raise ValueError("local timestamp receipt is bound to a different commitment")
        if self.trusted_proof is not None and self.trusted_proof.commitment != self.commitment:
            raise ValueError("trusted timestamp proof is bound to a different commitment")
        return self


class TemporalAssessmentPayloadV2(StrictAuditModel):
    artifact_ref: ImmutableArtifactRef
    model_context_ref: AuditDocumentRef | None
    model_context_snapshot: ModelContextPayload | None
    chronology: tuple[ChronologyInterval, ...] = Field(max_length=128)
    exposure_paths: tuple[AuditDocumentRef, ...] = Field(max_length=64)
    hiding_commitment: HidingCommitmentPayload | None
    status: TemporalState
    explanation_code: TemporalExplanationCode
    claim_qualifier_codes: tuple[
        Literal[
            "no_originality_claim",
            "not_training_proof",
            "provider_declared_cutoff",
            "provider_declared_pin",
            "insufficient_evidence",
        ],
        ...,
    ] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def status_has_supporting_evidence_and_qualifiers(self) -> TemporalAssessmentPayloadV2:
        if self.model_context_ref is not None and self.model_context_ref.kind != "model_context":
            raise ValueError("temporal assessment must bind a model_context document")
        if (self.model_context_ref is None) != (self.model_context_snapshot is None):
            raise ValueError(
                "model context reference and immutable context snapshot must appear together"
            )
        if self.model_context_ref is not None and self.model_context_snapshot is not None:
            expected_digest = sha256_bytes(
                canonical_envelope_bytes(
                    "model_context", self.model_context_snapshot.model_dump(mode="json")
                )
            )
            if self.model_context_ref.digest != expected_digest:
                raise ValueError("model context reference digest does not match its snapshot")
        accepted_exposure = any(
            item.event == "upstream_public_exposure" and item.verification_state == "verified"
            for item in self.chronology
        )
        if self.status in {
            "pre_cutoff_exposure_detected",
            "post_declared_cutoff",
            "interval_overlap",
        } and (self.model_context_ref is None or not accepted_exposure):
            raise ValueError(
                "classified temporal states require a model context and verified exposure"
            )
        classified_statuses = {
            "pre_cutoff_exposure_detected",
            "post_declared_cutoff",
            "interval_overlap",
        }
        if self.status in classified_statuses and (
            "no_originality_claim" not in self.claim_qualifier_codes
        ):
            raise ValueError("temporal classifications cannot imply originality")
        if self.status in classified_statuses and (
            "not_training_proof" not in self.claim_qualifier_codes
        ):
            raise ValueError("temporal evidence cannot claim model training")
        from polycodebench_core.audit_temporal import evaluate_temporal_eligibility

        result = evaluate_temporal_eligibility(self.chronology, self.model_context_snapshot)
        if (
            self.status != result.status
            or self.explanation_code != result.explanation_code
            or self.claim_qualifier_codes != result.claim_qualifier_codes
        ):
            raise ValueError(
                "temporal status, explanation and qualifiers must match the interval evaluator"
            )
        return self


class SealedManifestPayload(StrictAuditModel):
    encrypted_artifact_refs: tuple[ImmutableArtifactRef, ...]
    commitment: Digest
    key_version: ShortText
    access_policy: AuditDocumentRef
    retention_policy: AuditDocumentRef
    disclosure_state: SealState


class SealedManifestPayloadV2(StrictAuditModel):
    """Envelope metadata; key material and plaintext digests never belong here."""

    tenant_id: UUID
    encrypted_artifact_refs: tuple[ImmutableArtifactRef, ...] = Field(min_length=1, max_length=1)
    wrapped_key_ref: ImmutableArtifactRef
    hiding_commitment: HidingCommitmentPayload
    encryption_algorithm: Literal["AES-256-GCM"]
    key_wrapping_algorithm: ShortText
    key_provider: ShortText
    key_version: ShortText
    recovery_ref: ShortText | None
    nonce_length_bytes: Literal[12]
    access_policy: AuditDocumentRef
    retention_policy: AuditDocumentRef
    access_event_refs: tuple[AuditDocumentRef, ...]
    disclosure_state: SealState

    @model_validator(mode="after")
    def envelope_artifacts_are_private_and_history_is_typed(self) -> SealedManifestPayloadV2:
        if len(self.encrypted_artifact_refs) != 1:
            raise ValueError("each sealed manifest must bind exactly one per-artifact data key")
        if any(ref.visibility == "public" for ref in self.encrypted_artifact_refs):
            raise ValueError("sealed ciphertext artifacts must not be public")
        if self.wrapped_key_ref.visibility != "private":
            raise ValueError("wrapped data keys must be stored as private artifacts")
        if any(ref.kind != "seal_access_event" for ref in self.access_event_refs):
            raise ValueError("sealed access history must reference seal_access_event documents")
        if not self.access_event_refs and self.disclosure_state != "sealed":
            raise ValueError("a disclosed sealed manifest must retain its access event history")
        return self


class CanaryPolicyPayload(StrictAuditModel):
    marker_generation: AuditDocumentRef
    detection: AuditDocumentRef
    access: AuditDocumentRef
    exposure_rules: tuple[ShortText, ...]
    collision_checks: tuple[AuditDocumentRef, ...]
    interpretation_limits: tuple[ShortText, ...]


class CanaryCollisionCheck(StrictAuditModel):
    scope_ref: AuditDocumentRef
    checked_items: UnsignedInteger
    method_version: Literal["exact-bytes-v1"]
    result: Literal["no_collision"]

    @model_validator(mode="after")
    def collision_check_has_a_real_local_scope(self) -> CanaryCollisionCheck:
        if self.scope_ref.kind != "corpus_snapshot":
            raise ValueError("canary collision checks require a pinned local corpus snapshot")
        if self.checked_items == 0:
            raise ValueError("canary collision checks cannot claim success over an empty corpus")
        return self


class CanaryPolicyPayloadV2(StrictAuditModel):
    marker_refs: tuple[ImmutableArtifactRef, ...] = Field(min_length=1)
    marker_entropy_bits: Literal[256]
    detection_method: Literal["exact_bytes"]
    collision_checks: tuple[CanaryCollisionCheck, ...] = Field(min_length=1)
    detection: AuditDocumentRef
    access: AuditDocumentRef
    exposure_rules: tuple[ShortText, ...]
    local_first: Literal[True]
    interpretation_limits: tuple[
        Literal["absence_is_not_clean", "observed_disclosure_is_not_training_proof"], ...
    ]

    @model_validator(mode="after")
    def markers_are_private_and_limits_are_explicit(self) -> CanaryPolicyPayloadV2:
        if any(
            ref.visibility != "private"
            or ref.media_type != "application/vnd.polycodebench.sealed-artifact"
            for ref in self.marker_refs
        ):
            raise ValueError("canary markers must be envelope-encrypted private artifacts")
        if set(self.interpretation_limits) != {
            "absence_is_not_clean",
            "observed_disclosure_is_not_training_proof",
        }:
            raise ValueError("canary policy must preserve both interpretation limits")
        if len(self.collision_checks) != len(self.marker_refs):
            raise ValueError("every private canary marker requires a collision-check result")
        return self


class SealAccessEventPayload(StrictAuditModel):
    manifest_ref: AuditDocumentRef
    tenant_id: UUID
    actor: EntityRef
    operation: Literal[
        "local_screening",
        "candidate_delivery",
        "remote_query",
        "remote_delivery",
        "public_publication",
        "key_rotation",
    ]
    purpose: ShortText
    recipient: ShortText | None
    payload_digest: Digest | None
    authorization_ref: AuditDocumentRef | None
    event_time: TimestampEvidence
    outcome: Literal["authorized", "denied", "completed", "failed"]
    exposure: Literal["none", "authorized_disclosure", "public_exposed", "compromised"]

    @model_validator(mode="after")
    def disclosure_is_authorized_and_exactly_bound(self) -> SealAccessEventPayload:
        if self.manifest_ref.kind != "sealed_manifest":
            raise ValueError("sealed access event must reference a sealed_manifest")
        if not _SAFE_ACCESS_LABEL.fullmatch(self.purpose):
            raise ValueError("access purpose must be a bounded identifier, not free-form text")
        if self.recipient is not None and not _SAFE_ACCESS_LABEL.fullmatch(self.recipient):
            raise ValueError("access recipient must be a bounded identifier")
        if self.exposure != "none":
            if (
                self.outcome == "denied"
                or self.authorization_ref is None
                or self.recipient is None
                or self.payload_digest is None
            ):
                raise ValueError(
                    "exposure events require authorization, recipient and payload digest"
                )
        if self.outcome != "denied" and self.authorization_ref is None:
            raise ValueError("permitted access events require an authorization reference")
        if (
            self.operation == "local_screening"
            and self.outcome != "denied"
            and self.payload_digest is None
        ):
            raise ValueError("authorized local screening must record its exact payload digest")
        if self.operation in {"remote_query", "remote_delivery", "public_publication"}:
            if self.outcome != "denied" and self.exposure == "none":
                raise ValueError("authorized remote operations must record exposure")
            if self.outcome not in {"denied", "authorized"}:
                raise ValueError(
                    "remote disclosure events must record authorization before dispatch"
                )
        if self.operation == "public_publication" and self.outcome != "denied":
            if self.exposure != "public_exposed":
                raise ValueError("public publication must monotonically mark public exposure")
        if self.operation in {"candidate_delivery", "remote_query", "remote_delivery"}:
            if self.outcome != "denied" and self.exposure != "authorized_disclosure":
                raise ValueError("private recipient delivery must record authorized disclosure")
            if self.outcome not in {"denied", "authorized"}:
                raise ValueError(
                    "private delivery events must record authorization before dispatch"
                )
        if self.operation == "local_screening" and self.recipient is None:
            raise ValueError("local screening must identify its scoped worker recipient")
        if self.operation == "local_screening" and self.outcome != "denied":
            if self.outcome != "authorized" or self.exposure != "authorized_disclosure":
                raise ValueError("authorized local screening must advance exposure history")
        if self.operation == "key_rotation" and self.exposure != "none":
            raise ValueError("key rotation does not itself disclose plaintext")
        if self.operation == "key_rotation" and self.outcome not in {"denied", "authorized"}:
            raise ValueError("key rotation must record authorization before changing its wrapper")
        return self


class CanaryObservationPayload(StrictAuditModel):
    marker_ref: ImmutableArtifactRef
    coverage_ref: AuditDocumentRef
    observation: Literal[
        "observed_verified",
        "observed_previously_published",
        "not_observed_in_scope",
        "unverified_observation",
    ]
    exact_match: bool
    source_ref: AuditDocumentRef | None
    source_date_evidence: tuple[TimestampEvidence, ...]
    prior_publication: Literal["not_previously_published", "previously_published", "unknown"]
    source_review_ref: AuditDocumentRef | None
    external_query: bool
    access_event_ref: AuditDocumentRef | None
    interpretation_limits: tuple[
        Literal["absence_is_not_clean", "observed_disclosure_is_not_training_proof"], ...
    ]

    @model_validator(mode="after")
    def observation_never_claims_training_or_cleanliness(self) -> CanaryObservationPayload:
        if (
            self.marker_ref.visibility != "private"
            or self.marker_ref.media_type != "application/vnd.polycodebench.sealed-artifact"
        ):
            raise ValueError("canary marker reference must be a sealed private artifact")
        if self.coverage_ref.kind != "coverage_manifest":
            raise ValueError("canary observation must bind a declared coverage scope")
        if set(self.interpretation_limits) != {
            "absence_is_not_clean",
            "observed_disclosure_is_not_training_proof",
        }:
            raise ValueError("canary observation must preserve interpretation limits")
        verified = self.observation in {"observed_verified", "observed_previously_published"}
        if verified and (
            not self.exact_match
            or self.source_ref is None
            or not self.source_date_evidence
            or self.prior_publication == "unknown"
            or self.source_review_ref is None
        ):
            raise ValueError(
                "verified canary observations require exact match, source and date review"
            )
        if (
            self.observation == "observed_verified"
            and self.prior_publication != "not_previously_published"
        ):
            raise ValueError("verified disclosure requires review of prior deliberate publication")
        if (
            self.observation == "observed_previously_published"
            and self.prior_publication != "previously_published"
        ):
            raise ValueError("previously published canaries must be labeled separately")
        if self.observation == "not_observed_in_scope" and (
            self.exact_match or self.source_ref is not None or self.source_date_evidence
        ):
            raise ValueError("no-hit observations cannot carry matching source evidence")
        if self.observation == "unverified_observation" and not self.exact_match:
            raise ValueError("unverified observations require an observed candidate match")
        if self.exact_match and (
            self.source_ref is None or self.source_ref.kind != "match_evidence"
        ):
            raise ValueError("exact canary matches must bind reviewed match evidence")
        if self.access_event_ref is not None and self.access_event_ref.kind != "seal_access_event":
            raise ValueError("external canary query must reference a sealed access event")
        if self.external_query != (self.access_event_ref is not None):
            raise ValueError("external canary queries require their disclosure event reference")
        if self.source_review_ref is not None and self.source_review_ref.kind != "match_evidence":
            raise ValueError("canary source/date review must reference match evidence")
        return self


class BehavioralAuditPlanPayload(StrictAuditModel):
    method_applicability: dict[str, Literal["applicable", "unsupported", "not_applicable"]]
    original_sets: tuple[AuditDocumentRef, ...]
    control_sets: tuple[AuditDocumentRef, ...]
    reference_models: tuple[AuditDocumentRef, ...]
    target_models: tuple[AuditDocumentRef, ...]
    samples: tuple[EntityRef, ...]
    seed: Seed64
    budgets: dict[str, UnsignedInteger]
    tests: tuple[ShortText, ...]
    decision_rules: dict[str, ShortText]


class BehavioralMethodDefinition(StrictAuditModel):
    method_id: ShortText
    method_version: ShortText
    source_url: ShortText
    method_family: Literal["performance_generalization", "likelihood_based", "approved_custom"]
    required_capabilities: tuple[
        Literal[
            "per_sample_scores",
            "reference_models",
            "stable_model_revision",
            "token_likelihoods",
            "owned_training_manifest",
        ],
        ...,
    ]
    statistical_tests: tuple[
        Literal[
            "constat_reference_corrected_performance",
            "paired_family_bootstrap",
            "registered_custom",
        ],
        ...,
    ] = Field(min_length=1, max_length=16)
    implementation_state: Literal["available", "unsupported", "not_pinned"]
    implementation_ref: ImmutableArtifactRef | None
    assumptions: tuple[ShortText, ...] = Field(min_length=1, max_length=32)
    limitations: tuple[ShortText, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def method_source_and_implementation_are_pinned(self) -> BehavioralMethodDefinition:
        if not self.source_url.startswith("https://"):
            raise ValueError("behavioral method sources must use HTTPS")
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("behavioral method capabilities must be unique")
        if not self.statistical_tests or len(set(self.statistical_tests)) != len(
            self.statistical_tests
        ):
            raise ValueError("behavioral methods must declare unique statistical tests")
        if self.implementation_state == "available":
            if self.implementation_ref is None or self.implementation_ref.visibility == "public":
                raise ValueError(
                    "available methods require a pinned non-public implementation artifact"
                )
        elif self.implementation_ref is not None:
            raise ValueError("unsupported methods cannot claim an executable implementation")
        return self


class BehavioralMethodRegistryPayload(StrictAuditModel):
    registry_version: ShortText
    methods: tuple[BehavioralMethodDefinition, ...] = Field(min_length=1, max_length=32)
    interpretation_limits: tuple[
        Literal["performance_gap_is_not_training_inclusion", "no_universal_probability"], ...
    ]

    @model_validator(mode="after")
    def method_ids_are_unique_and_limits_are_fixed(self) -> BehavioralMethodRegistryPayload:
        method_ids = [method.method_id for method in self.methods]
        if len(method_ids) != len(set(method_ids)):
            raise ValueError("behavioral method IDs must be unique within a registry")
        if len(set(self.interpretation_limits)) != 2 or set(self.interpretation_limits) != {
            "performance_gap_is_not_training_inclusion",
            "no_universal_probability",
        }:
            raise ValueError("behavioral registry must retain its interpretation limits")
        return self


class BehavioralTaskValidityPayload(StrictAuditModel):
    original_task_ref: EntityRef
    control_task_ref: EntityRef
    family_ref: EntityRef
    semantic_relation: Literal["semantically_equivalent", "same_distribution", "invalid", "unknown"]
    difficulty_gap: Decimal6
    permitted_difficulty_gap: Decimal6
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=16)
    author_ref: EntityRef
    reviewer_ref: EntityRef | None
    validity_state: Literal["accepted", "pending", "rejected"]
    validity_limits: tuple[
        Literal["semantic_equivalence_is_reviewed", "difficulty_match_is_measured"], ...
    ]

    @model_validator(mode="after")
    def validity_is_independently_reviewed(self) -> BehavioralTaskValidityPayload:
        if (
            self.original_task_ref.entity_kind != "task_version"
            or self.control_task_ref.entity_kind != "task_version"
            or self.original_task_ref.entity_id == self.control_task_ref.entity_id
        ):
            raise ValueError(
                "behavioral validity must bind distinct original/control task versions"
            )
        if self.family_ref.entity_kind != "task_family":
            raise ValueError("behavioral validity must bind a stable task family")
        if len(set(self.validity_limits)) != 2 or set(self.validity_limits) != {
            "semantic_equivalence_is_reviewed",
            "difficulty_match_is_measured",
        }:
            raise ValueError("behavioral validity must retain semantic and difficulty limits")
        if Decimal(self.difficulty_gap) < 0 or Decimal(self.permitted_difficulty_gap) < 0:
            raise ValueError("behavioral difficulty gaps cannot be negative")
        if self.validity_state == "accepted":
            if (
                self.semantic_relation not in {"semantically_equivalent", "same_distribution"}
                or Decimal(self.difficulty_gap) > Decimal(self.permitted_difficulty_gap)
                or self.reviewer_ref is None
                or self.reviewer_ref.entity_kind != "reviewer"
                or self.author_ref.entity_kind != "reviewer"
                or self.author_ref.entity_id == self.reviewer_ref.entity_id
            ):
                raise ValueError(
                    "accepted controls require valid semantics, matched difficulty, "
                    "and independent review"
                )
        return self


class BehavioralSamplePair(StrictAuditModel):
    pair_id: UUID
    original_task_ref: EntityRef
    control_task_ref: EntityRef
    family_ref: EntityRef
    split: Literal["calibration", "validation", "held_out_test"]
    validity_ref: AuditDocumentRef

    @model_validator(mode="after")
    def sample_pair_is_bound(self) -> BehavioralSamplePair:
        if (
            self.original_task_ref.entity_kind != "task_version"
            or self.control_task_ref.entity_kind != "task_version"
            or self.original_task_ref.entity_id == self.control_task_ref.entity_id
        ):
            raise ValueError("behavioral pairs require distinct original/control tasks")
        if self.family_ref.entity_kind != "task_family":
            raise ValueError("behavioral pairs require a task-family identity")
        if self.validity_ref.kind != "behavioral_task_validity":
            raise ValueError("behavioral pairs require reviewed semantic/difficulty evidence")
        return self


class BehavioralModelSlot(StrictAuditModel):
    model_context_ref: AuditDocumentRef
    role: Literal["target", "reference"]
    recipient: ShortText
    capabilities: tuple[
        Literal[
            "per_sample_scores",
            "reference_models",
            "stable_model_revision",
            "token_likelihoods",
            "owned_training_manifest",
        ],
        ...,
    ]
    stable_revision_verified: bool

    @model_validator(mode="after")
    def model_context_is_pinned(self) -> BehavioralModelSlot:
        if self.model_context_ref.kind != "model_context":
            raise ValueError("behavioral model slots must bind an exact model-context document")
        if not _SAFE_ACCESS_LABEL.fullmatch(self.recipient):
            raise ValueError("behavioral recipients must be bounded identifiers")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("model capability declarations must be unique")
        if self.stable_revision_verified and "stable_model_revision" not in self.capabilities:
            raise ValueError("stable revision claims require the matching declared capability")
        return self


class BehavioralBudget(StrictAuditModel):
    max_model_calls: UnsignedInteger
    max_samples: UnsignedInteger
    max_input_tokens: UnsignedInteger
    max_output_tokens: UnsignedInteger
    max_cost_micro_usd: UnsignedInteger
    max_training_cost_micro_usd: UnsignedInteger


class BehavioralAuditPlanPayloadV2(StrictAuditModel):
    method_registry_ref: AuditDocumentRef
    method_id: ShortText
    audit_run_ref: EntityRef
    sample_pairs: tuple[BehavioralSamplePair, ...] = Field(min_length=3, max_length=10_000)
    model_slots: tuple[BehavioralModelSlot, ...] = Field(min_length=2, max_length=64)
    prompt_artifact_ref: ImmutableArtifactRef
    tool_policy_artifact_ref: ImmutableArtifactRef
    decoding_artifact_ref: ImmutableArtifactRef
    grading_artifact_ref: ImmutableArtifactRef
    exposure_policy_ref: AuditDocumentRef
    seed: Seed64
    budget: BehavioralBudget
    statistical_test: Literal[
        "constat_reference_corrected_performance",
        "paired_family_bootstrap",
        "registered_custom",
    ]
    alpha: Decimal6
    minimum_effect: Decimal6
    target_power: Decimal6
    bootstrap_replicates: UnsignedInteger
    multiplicity: Literal["none", "holm", "bonferroni", "benjamini_hochberg"]
    planned_hypotheses: UnsignedInteger
    decision_rule: Literal["adjusted_p_below_alpha_and_effect_at_least_minimum"]
    ground_truth_state: Literal["owned_controlled", "unavailable"]
    training_manifest_ref: ImmutableArtifactRef | None
    exposed_family_refs: tuple[EntityRef, ...] = Field(max_length=10_000)
    unexposed_family_refs: tuple[EntityRef, ...] = Field(max_length=10_000)
    preregistered_at: UtcTimestamp
    interpretation_limits: tuple[
        Literal[
            "self_report_is_not_evidence",
            "performance_gap_is_not_training_inclusion",
            "no_universal_probability",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def preregistration_is_complete_and_family_disjoint(self) -> BehavioralAuditPlanPayloadV2:
        if self.method_registry_ref.kind != "behavioral_method_registry":
            raise ValueError("behavioral plans require a pinned method registry")
        if self.audit_run_ref.entity_kind != "audit_run":
            raise ValueError("behavioral plan must bind its separately budgeted audit run")
        if self.exposure_policy_ref.kind not in {"audit_plan", "monitor_policy"}:
            raise ValueError("behavioral exposure policy must be an audit or monitor plan")
        for artifact_ref in (
            self.prompt_artifact_ref,
            self.tool_policy_artifact_ref,
            self.decoding_artifact_ref,
            self.grading_artifact_ref,
        ):
            if artifact_ref.visibility != "private":
                raise ValueError("behavioral prompts and configurations must remain private")
        contexts = [slot.model_context_ref for slot in self.model_slots]
        recipients = [slot.recipient for slot in self.model_slots]
        if len(contexts) != len(set(contexts)) or len(recipients) != len(set(recipients)):
            raise ValueError("behavioral model slots and recipients must be unique")
        if not any(slot.role == "target" for slot in self.model_slots) or not any(
            slot.role == "reference" for slot in self.model_slots
        ):
            raise ValueError("behavioral plan requires target and reference model cohorts")
        pair_ids = [pair.pair_id for pair in self.sample_pairs]
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("behavioral pair IDs must be unique")
        task_ids = [
            task.entity_id
            for pair in self.sample_pairs
            for task in (pair.original_task_ref, pair.control_task_ref)
        ]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("behavioral task samples cannot be reused across pairs")
        families_by_split: dict[str, set[UUID]] = {
            "calibration": set(),
            "validation": set(),
            "held_out_test": set(),
        }
        for pair in self.sample_pairs:
            families_by_split[pair.split].add(pair.family_ref.entity_id)
        if any(not families for families in families_by_split.values()):
            raise ValueError("calibration, validation and held-out test require separate families")
        split_families = [families_by_split[name] for name in families_by_split]
        if any(
            left & right
            for index, left in enumerate(split_families)
            for right in split_families[index + 1 :]
        ):
            raise ValueError("behavioral calibration/validation/test families must be disjoint")
        expected_samples = len(self.sample_pairs) * 2
        expected_calls = expected_samples * len(self.model_slots)
        if (
            self.budget.max_samples < expected_samples
            or self.budget.max_model_calls < expected_calls
            or self.planned_hypotheses < 1
        ):
            raise ValueError("behavioral sample/call budgets do not cover the frozen plan")
        if not Decimal("0") < Decimal(self.alpha) < Decimal("1"):
            raise ValueError("behavioral alpha must be strictly between zero and one")
        if not Decimal("0") < Decimal(self.target_power) < Decimal("1"):
            raise ValueError("behavioral target power must be strictly between zero and one")
        if Decimal(self.minimum_effect) < 0 or self.bootstrap_replicates < 1:
            raise ValueError("behavioral effect threshold and bootstrap count are invalid")
        if (
            self.statistical_test
            in {"constat_reference_corrected_performance", "paired_family_bootstrap"}
            and self.bootstrap_replicates < 2_000
        ):
            raise ValueError(
                "registered family-level bootstrap methods require at least 2,000 resamples"
            )
        if self.ground_truth_state == "owned_controlled":
            if (
                self.training_manifest_ref is None
                or self.training_manifest_ref.visibility != "private"
                or not self.exposed_family_refs
                or not self.unexposed_family_refs
                or self.budget.max_training_cost_micro_usd == 0
            ):
                raise ValueError(
                    "controlled calibration requires private owned training evidence and cap"
                )
            exposed = {item.entity_id for item in self.exposed_family_refs}
            unexposed = {item.entity_id for item in self.unexposed_family_refs}
            if any(
                item.entity_kind != "task_family"
                for item in (*self.exposed_family_refs, *self.unexposed_family_refs)
            ):
                raise ValueError("controlled exposure labels must identify task families")
            if exposed & unexposed:
                raise ValueError("controlled training families must be exposure-disjoint")
            planned_families = {pair.family_ref.entity_id for pair in self.sample_pairs}
            if not planned_families <= exposed | unexposed:
                raise ValueError("controlled training evidence must label every planned family")
        elif (
            self.training_manifest_ref is not None
            or self.exposed_family_refs
            or self.unexposed_family_refs
            or self.budget.max_training_cost_micro_usd != 0
        ):
            raise ValueError("unavailable ground truth cannot claim training manifests or budget")
        if len(set(self.interpretation_limits)) != 3 or set(self.interpretation_limits) != {
            "self_report_is_not_evidence",
            "performance_gap_is_not_training_inclusion",
            "no_universal_probability",
        }:
            raise ValueError("behavioral plans must preserve every interpretation limit")
        return self


class BehavioralObservationPayload(StrictAuditModel):
    plan_ref: AuditDocumentRef
    pair_id: UUID
    task_ref: EntityRef
    sample_role: Literal["original", "control"]
    model_context_ref: AuditDocumentRef
    outcome: Literal["completed", "failed", "blocked", "not_run"]
    dispatch_state: Literal["not_dispatched", "authorized_dispatched", "ambiguous"]
    call_intent_ref: EntityRef | None
    request_digest: Digest | None
    access_event_refs: tuple[AuditDocumentRef, ...] = Field(max_length=8)
    response_artifact_ref: ImmutableArtifactRef | None
    score: Decimal6 | None
    score_source: Literal["grader", "structured_metric", "human_review"] | None
    likelihood_state: Literal["available", "unavailable", "not_requested"]
    likelihood_value: Decimal6 | None
    confidence_state: Literal["available", "unavailable", "not_requested"]
    confidence_value: Decimal6 | None
    cost_state: Literal["actual", "estimated", "unavailable", "not_applicable"]
    cost_micro_usd: UnsignedInteger | None
    usage_state: Literal["reported", "partial", "unavailable", "not_applicable"]
    input_tokens: UnsignedInteger | None
    output_tokens: UnsignedInteger | None
    missing_reason: (
        Literal[
            "method_unsupported",
            "capability_missing",
            "budget_exhausted",
            "model_error",
            "infrastructure_failure",
            "cancelled",
            "not_scheduled",
            "review_pending",
        ]
        | None
    )

    @model_validator(mode="after")
    def observation_preserves_exposure_and_missingness(self) -> BehavioralObservationPayload:
        if (
            self.plan_ref.kind != "behavioral_audit_plan"
            or self.model_context_ref.kind != "model_context"
        ):
            raise ValueError(
                "behavioral observations must bind their frozen plan and model context"
            )
        if self.task_ref.entity_kind != "task_version":
            raise ValueError("behavioral observations must bind an immutable task version")
        if self.score is not None and not Decimal("0") <= Decimal(self.score) <= Decimal("1"):
            raise ValueError("behavioral score must lie within [0,1]")
        if (self.score is None) != (self.score_source is None):
            raise ValueError("behavioral scores require their actual measured source")
        for state, value, label in (
            (self.likelihood_state, self.likelihood_value, "likelihood"),
            (self.confidence_state, self.confidence_value, "confidence"),
        ):
            if (state == "available") != (value is not None):
                raise ValueError(f"{label} values must be present only when actually available")
            if (
                label == "confidence"
                and value is not None
                and not (Decimal("0") <= Decimal(value) <= Decimal("1"))
            ):
                raise ValueError("observed confidence values must lie within [0,1]")
        dispatched = self.dispatch_state != "not_dispatched"
        if dispatched != (self.call_intent_ref is not None and self.request_digest is not None):
            raise ValueError(
                "dispatched diagnostic calls must bind their gateway intent and request digest"
            )
        if not dispatched and self.call_intent_ref is not None:
            raise ValueError("undispatched diagnostics cannot reference a gateway call intent")
        if self.call_intent_ref is not None and self.call_intent_ref.entity_kind != "call_intent":
            raise ValueError("diagnostic gateway references must identify call intents")
        if bool(self.access_event_refs) != (self.request_digest is not None):
            raise ValueError(
                "every attempted diagnostic delivery must bind its exact access events"
            )
        if dispatched and not self.access_event_refs:
            raise ValueError("every dispatched model request requires its sealed exposure events")
        if not dispatched and len(self.access_event_refs) > 1:
            raise ValueError("a denied, undispatched request must have exactly one access event")
        if any(ref.kind != "seal_access_event" for ref in self.access_event_refs):
            raise ValueError("diagnostic access refs must identify sealed access events")
        if len(set(self.access_event_refs)) != len(self.access_event_refs):
            raise ValueError("diagnostic access-event references must be unique")
        if self.outcome == "completed":
            if (
                self.dispatch_state == "not_dispatched"
                or self.response_artifact_ref is None
                or self.response_artifact_ref.visibility != "private"
                or self.score is None
                or self.missing_reason is not None
            ):
                raise ValueError(
                    "completed observations require a private response and measured score"
                )
        else:
            if self.outcome in {"blocked", "not_run"} and dispatched:
                raise ValueError("blocked and not-run observations cannot claim model dispatch")
            if self.score is not None:
                raise ValueError("incomplete observations cannot carry a scored result")
            if self.response_artifact_ref is not None and (
                self.outcome != "failed"
                or not dispatched
                or self.response_artifact_ref.visibility != "private"
            ):
                raise ValueError(
                    "only failed dispatched calls can retain an unscored private response"
                )
            if self.missing_reason is None:
                raise ValueError(
                    "failed, blocked and not-run observations require a missingness reason"
                )
        if (self.cost_state in {"actual", "estimated"}) != (self.cost_micro_usd is not None):
            raise ValueError("behavioral cost values must identify actual or estimated accounting")
        if self.cost_state == "unavailable" and self.cost_micro_usd is not None:
            raise ValueError("unavailable behavioral costs cannot carry an invented amount")
        present_tokens = (self.input_tokens is not None, self.output_tokens is not None)
        if self.usage_state == "reported" and not all(present_tokens):
            raise ValueError("reported behavioral usage requires both token counts")
        if self.usage_state == "partial" and not any(present_tokens):
            raise ValueError("partial behavioral usage requires at least one token count")
        if self.usage_state == "unavailable" and any(present_tokens):
            raise ValueError("unavailable behavioral usage cannot carry token counts")
        if not dispatched and (
            self.cost_state != "not_applicable"
            or self.usage_state != "not_applicable"
            or self.cost_micro_usd is not None
            or any(present_tokens)
        ):
            raise ValueError("undispatched observations cannot claim model usage or cost")
        if dispatched and (
            self.cost_state == "not_applicable" or self.usage_state == "not_applicable"
        ):
            raise ValueError("dispatched outcomes must preserve available or missing usage state")
        return self


class BehavioralModelPerformance(StrictAuditModel):
    model_context_ref: AuditDocumentRef
    role: Literal["target", "reference"]
    planned_original: UnsignedInteger
    completed_original: UnsignedInteger
    mean_original_score: Decimal6 | None
    planned_control: UnsignedInteger
    completed_control: UnsignedInteger
    mean_control_score: Decimal6 | None

    @model_validator(mode="after")
    def panel_denominators_are_consistent(self) -> BehavioralModelPerformance:
        if self.model_context_ref.kind != "model_context":
            raise ValueError("behavioral performance panels must bind model contexts")
        if (
            self.completed_original > self.planned_original
            or self.completed_control > self.planned_control
            or (self.completed_original == 0) != (self.mean_original_score is None)
            or (self.completed_control == 0) != (self.mean_control_score is None)
        ):
            raise ValueError("behavioral panel counts and descriptive means disagree")
        return self


class BehavioralAssessmentPayload(StrictAuditModel):
    plan_ref: AuditDocumentRef
    method_registry_ref: AuditDocumentRef
    method_id: ShortText
    observation_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=1_000_000)
    expected_units: UnsignedInteger
    dispatched_model_calls: UnsignedInteger
    completed_units: UnsignedInteger
    failed_units: UnsignedInteger
    blocked_units: UnsignedInteger
    not_run_units: UnsignedInteger
    model_panels: tuple[BehavioralModelPerformance, ...] = Field(min_length=1, max_length=64)
    total_cost_state: Literal["actual", "estimated", "unavailable", "not_applicable"]
    total_cost_micro_usd: UnsignedInteger | None
    training_cost_state: Literal["unavailable", "not_applicable"]
    training_cost_micro_usd: UnsignedInteger | None
    training_budget_state: Literal["unknown", "not_applicable"]
    total_input_tokens: UnsignedInteger | None
    total_output_tokens: UnsignedInteger | None
    budget_state: Literal["within_cap", "over_cap", "unknown"]
    assessment_state: Literal["complete", "partial", "blocked"]
    inference_state: Literal["unsupported", "calibration_blocked", "descriptive_only"]
    power_state: Literal["unsupported", "insufficient_families", "not_estimated"]
    power_limit_codes: tuple[
        Literal[
            "method_unsupported",
            "capability_missing",
            "task_validity_not_accepted",
            "fewer_than_two_families_in_a_split",
            "power_analysis_not_available",
        ],
        ...,
    ] = Field(min_length=1, max_length=4)
    calibration_state: Literal["blocked_no_ground_truth", "pending_validation"]
    inclusion_claim: Literal["not_assessed"]
    interpretation_limits: tuple[
        Literal[
            "self_report_is_not_evidence",
            "performance_gap_is_not_training_inclusion",
            "no_universal_probability",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def assessment_preserves_all_planned_outcomes(self) -> BehavioralAssessmentPayload:
        if self.plan_ref.kind != "behavioral_audit_plan":
            raise ValueError("behavioral assessment must bind its preregistered plan")
        if self.method_registry_ref.kind != "behavioral_method_registry":
            raise ValueError("behavioral assessment must bind its method registry")
        if self.expected_units != len(self.observation_refs):
            raise ValueError("every planned sample/model pair requires a durable outcome")
        if (
            self.completed_units + self.failed_units + self.blocked_units + self.not_run_units
            != self.expected_units
        ):
            raise ValueError("behavioral outcome counts must reconcile to the frozen denominator")
        if self.assessment_state == "complete" and (
            self.failed_units or self.blocked_units or self.not_run_units
        ):
            raise ValueError("complete behavioral assessments cannot hide missing outcomes")
        if self.inclusion_claim != "not_assessed":
            raise ValueError("behavioral performance cannot claim training-set inclusion")
        if len(set(self.power_limit_codes)) != len(self.power_limit_codes):
            raise ValueError("behavioral power limits must be unique")
        if self.power_state == "unsupported" and not set(self.power_limit_codes) & {
            "method_unsupported",
            "capability_missing",
            "task_validity_not_accepted",
        }:
            raise ValueError("unsupported behavioral power requires an explicit blocker")
        if self.power_state == "insufficient_families" and (
            "fewer_than_two_families_in_a_split" not in self.power_limit_codes
        ):
            raise ValueError("insufficient behavioral power must identify the split limitation")
        if self.power_state == "not_estimated" and (
            "power_analysis_not_available" not in self.power_limit_codes
        ):
            raise ValueError("unestimated behavioral power must identify the missing analysis")
        if len(set(self.interpretation_limits)) != 3 or set(self.interpretation_limits) != {
            "self_report_is_not_evidence",
            "performance_gap_is_not_training_inclusion",
            "no_universal_probability",
        }:
            raise ValueError("behavioral assessment must retain every interpretation limit")
        if len({panel.model_context_ref for panel in self.model_panels}) != len(self.model_panels):
            raise ValueError("behavioral performance panels must be unique per model context")
        if (self.total_cost_state in {"actual", "estimated"}) != (
            self.total_cost_micro_usd is not None
        ):
            raise ValueError(
                "behavioral aggregate cost needs its actual/estimated availability state"
            )
        if self.budget_state == "within_cap" and (
            self.total_cost_state == "unavailable"
            or self.total_input_tokens is None
            or self.total_output_tokens is None
        ):
            raise ValueError("unknown usage cannot be reported within a frozen budget cap")
        if (self.training_cost_state == "unavailable") != (
            self.training_budget_state == "unknown"
        ) or self.training_cost_micro_usd is not None:
            raise ValueError("controlled training compute remains explicitly unaccounted")
        return self


class FirewallScopeUnit(StrictAuditModel):
    scope_key: ShortText
    source_ref: AuditDocumentRef
    component_ref: EntityRef
    modality: Literal["text", "code", "image", "audio", "video", "repository"]

    @model_validator(mode="after")
    def scope_unit_is_bound(self) -> FirewallScopeUnit:
        if self.source_ref.kind != "corpus_snapshot":
            raise ValueError("firewall source scope must bind immutable corpus snapshots")
        if self.component_ref.entity_kind != "audit_component" or self.component_ref.digest is None:
            raise ValueError("firewall scope components must identify task components")
        return self


class FirewallScopeOutcome(StrictAuditModel):
    scope_key: ShortText
    state: Literal["no_match", "match", "unresolved", "failed", "truncated", "unsupported"]
    relation: MatchRelation | None
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def scope_outcome_has_support(self) -> FirewallScopeOutcome:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("firewall scope evidence references must be unique")
        if self.state == "no_match":
            if self.relation != "no_substantive_match" or not any(
                ref.kind == "coverage_manifest" for ref in self.evidence_refs
            ):
                raise ValueError("a no-match outcome requires completed finite-scope coverage")
        elif self.state == "match":
            if self.relation in {None, "no_substantive_match", "unresolved"} or not any(
                ref.kind == "match_evidence" for ref in self.evidence_refs
            ):
                raise ValueError("a match outcome requires resolved match evidence")
        elif self.state == "unresolved":
            if self.relation != "unresolved":
                raise ValueError("unresolved scope outcomes must retain their unresolved relation")
        elif self.relation is not None:
            raise ValueError("failed, truncated and unsupported scope cannot claim a relation")
        return self


class FirewallPolicyPayloadV2(StrictAuditModel):
    benchmark_ref: AuditDocumentRef
    audit_plan_ref: AuditDocumentRef
    policy_version: ShortText
    required_scope: tuple[FirewallScopeUnit, ...] = Field(min_length=1, max_length=10_000)
    prohibited_relations: tuple[MatchRelation, ...] = Field(min_length=1, max_length=7)
    require_temporal_review: bool
    high_risk_action: Literal["review", "reject"]

    @model_validator(mode="after")
    def firewall_policy_is_finite(self) -> FirewallPolicyPayloadV2:
        if (
            self.benchmark_ref.kind != "benchmark_snapshot"
            or self.audit_plan_ref.kind != "audit_plan"
        ):
            raise ValueError("firewall policy must bind an official snapshot and finite audit plan")
        keys = [unit.scope_key for unit in self.required_scope]
        if len(keys) != len(set(keys)):
            raise ValueError("firewall scope keys must be unique")
        if len(set(self.prohibited_relations)) != len(self.prohibited_relations):
            raise ValueError("firewall prohibited relations must be unique")
        if "unresolved" in self.prohibited_relations:
            raise ValueError("unresolved evidence must be reviewed, not classified as a match")
        return self


class FirewallScopePayload(StrictAuditModel):
    task_ref: EntityRef
    policy_ref: AuditDocumentRef
    audit_ref: AuditDocumentRef
    outcomes: tuple[FirewallScopeOutcome, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def scope_outcomes_are_unique_and_typed(self) -> FirewallScopePayload:
        if self.task_ref.entity_kind != "task_version":
            raise ValueError("firewall scope must bind an immutable task version")
        if self.policy_ref.kind != "firewall_policy" or self.audit_ref.kind != "audit_plan":
            raise ValueError("firewall scope must bind its policy and finite audit plan")
        keys = [item.scope_key for item in self.outcomes]
        if len(keys) != len(set(keys)):
            raise ValueError("firewall scope outcomes cannot repeat a planned unit")
        return self


class ReplacementSourceMetadataPayload(StrictAuditModel):
    source_family_id: ShortText
    rights_record_id: ShortText
    approved_source_ids: tuple[ShortText, ...] = Field(min_length=1, max_length=256)
    source_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=256)
    rights_state: Literal["approved", "denied", "pending"]
    rights_evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=64)
    curator_ref: EntityRef
    reviewer_ref: EntityRef
    reviewed_at: UtcTimestamp

    @model_validator(mode="after")
    def source_family_metadata_is_reviewed(self) -> ReplacementSourceMetadataPayload:
        if (
            len(set(self.approved_source_ids)) != len(self.approved_source_ids)
            or len(set(self.source_refs)) != len(self.source_refs)
            or len(set(self.rights_evidence_refs)) != len(self.rights_evidence_refs)
        ):
            raise ValueError("source-family evidence references must be unique")
        if (
            self.curator_ref.entity_kind != "reviewer"
            or self.reviewer_ref.entity_kind != "reviewer"
            or self.curator_ref.entity_id == self.reviewer_ref.entity_id
        ):
            raise ValueError("source-family metadata requires an independent human reviewer")
        return self


class ReplacementSourceQuota(StrictAuditModel):
    source_id: ShortText
    maximum_drafts: UnsignedInteger


class ReplacementBudgets(StrictAuditModel):
    max_drafts: Annotated[int, Field(ge=1, le=1_000)]
    max_rounds: Annotated[int, Field(ge=1, le=8)]
    max_cost_micro_usd: Annotated[int, Field(ge=1, le=2**53 - 1)]
    max_wall_seconds: Annotated[int, Field(ge=1, le=604_800)]


class ReplacementPlanPayloadV2(StrictAuditModel):
    source_metadata_ref: AuditDocumentRef
    mode: Literal["source_family_transformation", "independent_prospective"]
    seed_ancestry_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=256)
    competency_brief: ImmutableArtifactRef
    authors: tuple[EntityRef, ...] = Field(min_length=1, max_length=32)
    checkers: tuple[EntityRef, ...] = Field(min_length=1, max_length=32)
    generator_config_ref: ImmutableArtifactRef
    checker_config_ref: ImmutableArtifactRef
    difficulty_policy_ref: ImmutableArtifactRef
    exposure_policy_ref: AuditDocumentRef
    source_quotas: tuple[ReplacementSourceQuota, ...] = Field(min_length=1, max_length=256)
    budgets: ReplacementBudgets
    preregistered_at: UtcTimestamp

    @model_validator(mode="after")
    def replacement_work_is_preregistered_and_bounded(self) -> ReplacementPlanPayloadV2:
        if self.source_metadata_ref.kind != "replacement_source_metadata":
            raise ValueError("replacement plans require approved source-family metadata")
        if self.exposure_policy_ref.kind not in {"audit_plan", "monitor_policy"}:
            raise ValueError("replacement plans must freeze a recognized exposure policy")
        if any(
            ref.visibility != "private"
            for ref in (
                self.competency_brief,
                self.generator_config_ref,
                self.checker_config_ref,
                self.difficulty_policy_ref,
            )
        ):
            raise ValueError("replacement briefs, configs and difficulty policies must be private")
        author_ids = {item.entity_id for item in self.authors}
        checker_ids = {item.entity_id for item in self.checkers}
        if any(item.entity_kind != "reviewer" for item in (*self.authors, *self.checkers)):
            raise ValueError("replacement authors and checkers must be identified reviewers")
        if author_ids & checker_ids:
            raise ValueError("a replacement author cannot independently check their own work")
        if len(author_ids) != len(self.authors) or len(checker_ids) != len(self.checkers):
            raise ValueError("replacement authors and checkers must be unique")
        if len({item.source_id for item in self.source_quotas}) != len(self.source_quotas):
            raise ValueError("replacement source quotas must be unique")
        if sum(item.maximum_drafts for item in self.source_quotas) < self.budgets.max_drafts:
            raise ValueError("approved source-family quotas must cover the frozen draft cap")
        if self.generator_config_ref.digest == self.checker_config_ref.digest:
            raise ValueError("replacement generator and independent checker configs must differ")
        allowed_seed_kinds = {"task_fingerprint", "match_evidence", "benchmark_snapshot"}
        if any(ref.kind not in allowed_seed_kinds for ref in self.seed_ancestry_refs):
            raise ValueError(
                "replacement seed ancestry must cite task, match or benchmark evidence"
            )
        return self


class ReplacementValidationPayload(StrictAuditModel):
    plan_ref: AuditDocumentRef
    task_ref: EntityRef
    source_family_id: ShortText
    source_id: ShortText
    draft_index: Annotated[int, Field(ge=1, le=1_000)]
    family_relation: Literal["source_family", "independent_prospective", "unknown"]
    parent_task_refs: tuple[EntityRef, ...] = Field(min_length=1, max_length=256)
    author_ref: EntityRef
    checker_refs: tuple[EntityRef, ...] = Field(min_length=1, max_length=32)
    reviewer_ref: EntityRef
    correctness_state: Literal["verified", "invalid", "pending"]
    rights_state: Literal["approved", "denied", "pending"]
    fidelity_state: Literal["valid", "invalid", "pending"]
    oracle_state: Literal["independently_verified", "failed", "pending"]
    difficulty_state: Literal["calibrated", "invalid", "pending"]
    lineage_state: Literal[
        "verified_source_family", "verified_independent", "uncertain", "prohibited"
    ]
    template_review_state: Literal["accepted", "rejected", "pending", "not_applicable"]
    exposure_state: Literal["within_policy", "prohibited", "unknown"]
    exposure_scope_ref: AuditDocumentRef | None
    test_artifact_ref: ImmutableArtifactRef
    oracle_artifact_ref: ImmutableArtifactRef
    difficulty_policy_ref: ImmutableArtifactRef
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=128)
    review_state: Literal["accepted", "pending", "rejected"]
    reason_codes: tuple[
        Literal[
            "correctness_invalid",
            "rights_denied",
            "fidelity_invalid",
            "oracle_failed",
            "difficulty_invalid",
            "lineage_prohibited",
            "template_review_rejected",
            "exposure_prohibited",
            "evidence_pending",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def replacement_validation_requires_independent_checks(self) -> ReplacementValidationPayload:
        if self.plan_ref.kind != "replacement_plan" or self.task_ref.entity_kind != "task_version":
            raise ValueError("replacement validation must bind a plan and immutable task version")
        if self.exposure_scope_ref is not None and self.exposure_scope_ref.kind != "firewall_scope":
            raise ValueError("replacement exposure evidence must reference a firewall scope")
        if self.exposure_state in {"within_policy", "prohibited"} and (
            self.exposure_scope_ref is None
        ):
            raise ValueError(
                "resolved replacement exposure requires a task-specific firewall scope"
            )
        if any(item.entity_kind != "task_version" for item in self.parent_task_refs):
            raise ValueError("replacement ancestry must identify immutable task versions")
        all_reviewers = (self.author_ref, *self.checker_refs, self.reviewer_ref)
        if any(item.entity_kind != "reviewer" for item in all_reviewers):
            raise ValueError("replacement author, checker and reviewer identities must be typed")
        reviewer_ids = [item.entity_id for item in all_reviewers]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("replacement author, checkers and reviewer must be independent")
        if any(
            artifact.visibility != "private"
            for artifact in (
                self.test_artifact_ref,
                self.oracle_artifact_ref,
                self.difficulty_policy_ref,
            )
        ):
            raise ValueError("replacement tests, oracle and difficulty evidence stay private")
        if len(set(self.checker_refs)) != len(self.checker_refs) or len(
            set(self.evidence_refs)
        ) != len(self.evidence_refs):
            raise ValueError("replacement checkers and evidence references must be unique")
        if (
            self.family_relation == "source_family"
            and self.lineage_state != "verified_source_family"
        ):
            raise ValueError("source-family replacements must retain their reviewed ancestry")
        if self.family_relation == "independent_prospective" and (
            self.lineage_state != "verified_independent" or self.template_review_state != "accepted"
        ):
            raise ValueError(
                "independent prospective tasks require accepted template/lineage review"
            )
        if self.family_relation == "unknown" and self.lineage_state != "uncertain":
            raise ValueError("unknown family ancestry cannot claim independent lineage")
        hard_failures = {
            "correctness_invalid": self.correctness_state == "invalid",
            "rights_denied": self.rights_state == "denied",
            "fidelity_invalid": self.fidelity_state == "invalid",
            "oracle_failed": self.oracle_state == "failed",
            "difficulty_invalid": self.difficulty_state == "invalid",
            "lineage_prohibited": self.lineage_state == "prohibited",
            "template_review_rejected": self.template_review_state == "rejected",
            "exposure_prohibited": self.exposure_state == "prohibited",
        }
        expected_reasons = tuple(code for code, failed in hard_failures.items() if failed)
        all_verified = (
            self.correctness_state == "verified"
            and self.rights_state == "approved"
            and self.fidelity_state == "valid"
            and self.oracle_state == "independently_verified"
            and self.difficulty_state == "calibrated"
            and self.lineage_state in {"verified_source_family", "verified_independent"}
            and self.template_review_state in {"accepted", "not_applicable"}
            and self.exposure_state == "within_policy"
        )
        expected_state: Literal["accepted", "pending", "rejected"] = (
            "rejected" if expected_reasons else "accepted" if all_verified else "pending"
        )
        expected_reasons = expected_reasons or (() if all_verified else ("evidence_pending",))
        if self.review_state != expected_state or self.reason_codes != expected_reasons:
            raise ValueError("replacement review state must be derived from its independent gates")
        return self


class FirewallDecisionPayload(StrictAuditModel):
    task_ref: EntityRef
    audit_ref: AuditDocumentRef
    validity_refs: tuple[AuditDocumentRef, ...]
    rights_refs: tuple[AuditDocumentRef, ...]
    lineage_refs: tuple[AuditDocumentRef, ...]
    exposure_refs: tuple[AuditDocumentRef, ...]
    policy_ref: AuditDocumentRef
    result: FirewallState
    reasons: tuple[ShortText, ...]
    reviewer: EntityRef


class ReplacementPlanPayload(StrictAuditModel):
    seed_ancestry: tuple[AuditDocumentRef, ...]
    competency_brief: ImmutableArtifactRef
    authors: tuple[EntityRef, ...]
    checkers: tuple[EntityRef, ...]
    validity: AuditDocumentRef
    difficulty: DecimalMeasurement
    exposure: AuditDocumentRef
    source_quotas: dict[str, UnsignedInteger]
    budgets: dict[str, UnsignedInteger]


class FirewallDecisionPayloadV2(StrictAuditModel):
    task_ref: EntityRef
    policy_ref: AuditDocumentRef
    scope_ref: AuditDocumentRef
    risk_assessment_ref: AuditDocumentRef
    validity_ref: AuditDocumentRef
    temporal_ref: AuditDocumentRef | None
    exposure_refs: tuple[AuditDocumentRef, ...]
    result: FirewallState
    reasons: tuple[FirewallReason, ...]
    reviewer: EntityRef
    resolution_reason: ShortText | None

    @model_validator(mode="after")
    def firewall_decision_binds_required_evidence(self) -> FirewallDecisionPayloadV2:
        if self.task_ref.entity_kind != "task_version":
            raise ValueError("firewall decisions must bind immutable task versions")
        expected_kinds = {
            "policy_ref": "firewall_policy",
            "scope_ref": "firewall_scope",
            "risk_assessment_ref": "risk_assessment",
            "validity_ref": "replacement_validation",
        }
        for field_name, expected_kind in expected_kinds.items():
            if getattr(self, field_name).kind != expected_kind:
                raise ValueError(f"firewall {field_name} must reference {expected_kind}")
        if self.temporal_ref is not None and self.temporal_ref.kind != "temporal_assessment":
            raise ValueError("firewall temporal evidence must reference a temporal assessment")
        if any(ref.kind != "match_evidence" for ref in self.exposure_refs):
            raise ValueError("firewall exposure findings must reference match evidence")
        if len(set(self.exposure_refs)) != len(self.exposure_refs):
            raise ValueError("firewall exposure references must be unique")
        if self.reviewer.entity_kind != "reviewer":
            raise ValueError("firewall decisions require an identified human reviewer")
        expected_result: FirewallState = (
            "reject"
            if "prohibited_overlap" in self.reasons
            or "validity_rejected" in self.reasons
            or "rights_denied" in self.reasons
            or "high_risk_rejected" in self.reasons
            else "review"
            if self.reasons
            else "admit"
        )
        if self.result != expected_result:
            raise ValueError("firewall result must follow its enumerated evidence blockers")
        return self


class DerivedBenchmarkEntry(StrictAuditModel):
    original_item_ref: EntityRef
    original_task_ref: EntityRef | None
    derived_item_ref: EntityRef
    replacement_task_ref: EntityRef | None
    disposition: Literal["retained", "replaced", "excluded"]
    source_family_id: ShortText
    derived_source_family_id: ShortText | None
    split: ShortText
    original_competency: ShortText
    derived_competency: ShortText | None
    original_difficulty: ShortText
    derived_difficulty: ShortText | None
    validation_ref: AuditDocumentRef | None
    oracle_mapping_ref: ImmutableArtifactRef | None
    reason: ShortText | None

    @model_validator(mode="after")
    def derived_entry_is_complete(self) -> DerivedBenchmarkEntry:
        if (
            self.original_item_ref.entity_kind != "benchmark_item"
            or self.derived_item_ref.entity_kind != "derived_benchmark_item"
        ):
            raise ValueError("derived entries must bind original and derived membership identities")
        if (
            self.original_task_ref is not None
            and self.original_task_ref.entity_kind != "task_version"
        ):
            raise ValueError("mapped original tasks must identify immutable task versions")
        if self.disposition == "retained":
            if (
                self.replacement_task_ref is not None
                or self.validation_ref is not None
                or self.oracle_mapping_ref is not None
            ):
                raise ValueError("retained items cannot claim replacement artifacts")
            if self.derived_difficulty != self.original_difficulty:
                raise ValueError("retained items must preserve their official difficulty")
            if (
                self.derived_source_family_id != self.source_family_id
                or self.derived_competency != self.original_competency
            ):
                raise ValueError("retained items must preserve family and competency")
        elif self.disposition == "replaced":
            if (
                self.replacement_task_ref is None
                or self.replacement_task_ref.entity_kind != "task_version"
                or self.validation_ref is None
                or self.validation_ref.kind != "replacement_validation"
                or self.oracle_mapping_ref is None
                or self.oracle_mapping_ref.visibility != "private"
                or self.derived_difficulty is None
                or self.derived_source_family_id is None
                or self.derived_competency is None
            ):
                raise ValueError("replacements require independent validity and oracle mapping")
        elif (
            self.replacement_task_ref is not None
            or self.validation_ref is not None
            or self.oracle_mapping_ref is not None
            or self.derived_source_family_id is not None
            or self.derived_competency is not None
            or self.derived_difficulty is not None
        ):
            raise ValueError("excluded items cannot carry replacement membership")
        if (self.disposition == "excluded") != (self.reason is not None):
            raise ValueError("exclusions require a reason and included items cannot claim one")
        return self


class DerivedDistributionCount(StrictAuditModel):
    dimension: Literal["source_family", "split", "competency", "difficulty"]
    label: ShortText
    official_items: UnsignedInteger
    derived_items: UnsignedInteger


class DerivedBenchmarkManifestPayload(StrictAuditModel):
    official_snapshot_ref: AuditDocumentRef
    official_membership_digest: Digest
    derived_version: ShortText
    entries: tuple[DerivedBenchmarkEntry, ...] = Field(min_length=1, max_length=1_000_000)
    derived_membership_digest: Digest
    change_summary_ref: ImmutableArtifactRef
    sampling_policy_ref: ImmutableArtifactRef
    distribution: tuple[DerivedDistributionCount, ...] = Field(min_length=4, max_length=100_000)
    official_metric_label: ShortText
    derived_metric_label: ShortText
    comparability: Literal["separate_labels_no_automatic_comparison"]
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def derived_manifest_preserves_official_membership(self) -> DerivedBenchmarkManifestPayload:
        if self.official_snapshot_ref.kind != "benchmark_snapshot":
            raise ValueError("derived manifests must preserve an immutable official snapshot")
        if any(
            artifact.visibility != "private"
            for artifact in (self.change_summary_ref, self.sampling_policy_ref)
        ):
            raise ValueError("derived change and sampling artifacts must remain private")
        if self.official_metric_label == self.derived_metric_label:
            raise ValueError("official and derived metrics require distinct labels")
        original_item_ids = [item.original_item_ref.entity_id for item in self.entries]
        derived_item_ids = [item.derived_item_ref.entity_id for item in self.entries]
        if len(original_item_ids) != len(set(original_item_ids)):
            raise ValueError("derived manifests must account for each original item once")
        if len(derived_item_ids) != len(set(derived_item_ids)):
            raise ValueError("derived membership identities must be unique")
        family_splits: dict[str, set[str]] = {}
        for entry in self.entries:
            family_splits.setdefault(entry.source_family_id, set()).add(entry.split)
            if entry.derived_source_family_id is not None:
                family_splits.setdefault(entry.derived_source_family_id, set()).add(entry.split)
        if any(len(splits) > 1 for splits in family_splits.values()):
            raise ValueError("source-family variants cannot cross official splits")
        expected_counts: dict[tuple[str, str], list[int]] = {}
        for dimension, original_name, derived_name in (
            ("source_family", "source_family_id", "derived_source_family_id"),
            ("split", "split", "split"),
            ("competency", "original_competency", "derived_competency"),
            ("difficulty", "original_difficulty", "derived_difficulty"),
        ):
            for entry in self.entries:
                original_label = getattr(entry, original_name)
                original_count = expected_counts.setdefault((dimension, original_label), [0, 0])
                original_count[0] += 1
                if entry.disposition != "excluded":
                    derived_label = getattr(entry, derived_name)
                    if derived_label is None:
                        raise ValueError(
                            "included derived items require complete distribution labels"
                        )
                    derived_count = expected_counts.setdefault((dimension, derived_label), [0, 0])
                    derived_count[1] += 1
        supplied_counts = {
            (item.dimension, item.label): (item.official_items, item.derived_items)
            for item in self.distribution
        }
        if len(supplied_counts) != len(self.distribution):
            raise ValueError("derived distribution dimensions and labels must be unique")
        if supplied_counts != {
            key: (counts[0], counts[1]) for key, counts in expected_counts.items()
        }:
            raise ValueError("derived distribution counts do not match manifest entries")
        derived_ids = sorted(
            str(item.derived_item_ref.entity_id)
            for item in self.entries
            if item.disposition != "excluded"
        )
        expected = sha256_bytes(canonical_json_bytes(derived_ids))
        if self.derived_membership_digest != expected:
            raise ValueError("derived membership digest does not match its immutable entries")
        return self


class MonitorPolicyPayload(StrictAuditModel):
    benchmarks: tuple[AuditDocumentRef, ...]
    source_schedule: dict[str, ShortText]
    caps: dict[str, UnsignedInteger]
    credential_scope: tuple[ShortText, ...]
    alert_routes: tuple[ShortText, ...]
    staleness: dict[str, UnsignedInteger]
    retry_rules: dict[str, UnsignedInteger]
    stop_rules: tuple[ShortText, ...]


class BenchmarkHealthPayload(StrictAuditModel):
    membership_ref: AuditDocumentRef
    assessment_refs: tuple[AuditDocumentRef, ...]
    policy_ref: AuditDocumentRef
    context_ref: AuditDocumentRef | None
    coverage: DecimalMeasurement
    unknown_denominators: dict[str, UnsignedInteger]
    trend_refs: tuple[AuditDocumentRef, ...]
    descriptive_metrics: dict[str, DecimalMeasurement]


class AuditAttestationPayload(StrictAuditModel):
    benchmark_ref: AuditDocumentRef
    scan_ref: AuditDocumentRef
    policy_ref: AuditDocumentRef
    coverage_ref: AuditDocumentRef
    claim_digests: tuple[Digest, ...]
    model_context: AuditDocumentRef | None
    issued_at: UtcTimestamp
    issued_at_precision: Literal["second", "millisecond", "microsecond", "nanosecond"]
    expires_at: UtcTimestamp
    expires_at_precision: Literal["second", "millisecond", "microsecond", "nanosecond"]
    review: AuditDocumentRef
    signature_algorithm: ShortText
    key_ref: EntityRef
    revocation_refs: tuple[AuditDocumentRef, ...]

    @model_validator(mode="after")
    def validity_window_is_ordered(self) -> AuditAttestationPayload:
        for timestamp, precision in (
            (self.issued_at, self.issued_at_precision),
            (self.expires_at, self.expires_at_precision),
        ):
            digits = len(timestamp.partition(".")[2].removesuffix("Z"))
            expected = {
                "second": 0,
                "millisecond": 3,
                "microsecond": 6,
                "nanosecond": 9,
            }[precision]
            if digits != expected:
                raise ValueError("attestation timestamp precision does not match its value")
        issued = datetime.fromisoformat(self.issued_at[:-1] + "+00:00")
        expires = datetime.fromisoformat(self.expires_at[:-1] + "+00:00")
        if expires <= issued:
            raise ValueError("attestation expiry must be later than issue time")
        return self


class _PayloadDocument(StrictAuditModel):
    id: UUID
    kind: AuditKind
    schema_version: int = Field(ge=1, le=2)
    payload: Any
    metadata: DocumentMetadata
    supersedes_id: UUID | None = None

    payload_model: ClassVar[type[StrictAuditModel]]
    expected_schema_version: ClassVar[int] = 1

    @model_validator(mode="before")
    @classmethod
    def parse_typed_payload(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("payload"), dict):
            payload_model = cls.payload_model
            value = dict(value)
            value["payload"] = payload_model.model_validate_json(
                canonical_json_bytes(value["payload"])
            )
        return value

    @model_validator(mode="after")
    def kind_and_payload_match(self) -> _PayloadDocument:
        if self.schema_version != type(self).expected_schema_version:
            raise ValueError("document schema version does not match its payload contract")
        if self.kind != type(self).model_fields["kind"].default:
            raise ValueError("document kind does not match its payload contract")
        if not isinstance(self.payload, self.payload_model):
            raise ValueError("payload does not match the strict kind-specific contract")
        _validate_nested_json(self.payload.model_dump(mode="json"))
        if self.id.version != 4:
            raise ValueError("audit document row identity must be UUIDv4")
        return self


class BenchmarkSnapshotDocument(_PayloadDocument):
    kind: Literal["benchmark_snapshot"] = "benchmark_snapshot"
    payload: BenchmarkSnapshotPayload
    payload_model = BenchmarkSnapshotPayload


class TaskFingerprintDocument(_PayloadDocument):
    kind: Literal["task_fingerprint"] = "task_fingerprint"
    payload: TaskFingerprintPayload
    payload_model = TaskFingerprintPayload


class CorpusSnapshotDocument(_PayloadDocument):
    kind: Literal["corpus_snapshot"] = "corpus_snapshot"
    payload: CorpusSnapshotPayload
    payload_model = CorpusSnapshotPayload


class AuditPlanDocument(_PayloadDocument):
    kind: Literal["audit_plan"] = "audit_plan"
    payload: AuditPlanPayload
    payload_model = AuditPlanPayload


class QueryManifestDocument(_PayloadDocument):
    kind: Literal["query_manifest"] = "query_manifest"
    payload: QueryManifestPayload
    payload_model = QueryManifestPayload


class CoverageManifestDocument(_PayloadDocument):
    kind: Literal["coverage_manifest"] = "coverage_manifest"
    payload: CoverageManifestPayload
    payload_model = CoverageManifestPayload


class MatchEvidenceDocument(_PayloadDocument):
    kind: Literal["match_evidence"] = "match_evidence"
    payload: MatchEvidencePayload
    payload_model = MatchEvidencePayload


class MatchEvidenceDocumentV2(_PayloadDocument):
    kind: Literal["match_evidence"] = "match_evidence"
    payload: MatchEvidencePayloadV2
    payload_model = MatchEvidencePayloadV2
    expected_schema_version: ClassVar[int] = 2


class RiskPolicyDocument(_PayloadDocument):
    kind: Literal["risk_policy"] = "risk_policy"
    payload: RiskPolicyPayload
    payload_model = RiskPolicyPayload


class RiskPolicyDocumentV2(_PayloadDocument):
    kind: Literal["risk_policy"] = "risk_policy"
    payload: RiskPolicyPayloadV2
    payload_model = RiskPolicyPayloadV2
    expected_schema_version: ClassVar[int] = 2


class RiskAssessmentDocument(_PayloadDocument):
    kind: Literal["risk_assessment"] = "risk_assessment"
    payload: RiskAssessmentPayload
    payload_model = RiskAssessmentPayload


class RiskAssessmentDocumentV2(_PayloadDocument):
    kind: Literal["risk_assessment"] = "risk_assessment"
    payload: RiskAssessmentPayloadV2
    payload_model = RiskAssessmentPayloadV2
    expected_schema_version: ClassVar[int] = 2


class ModelContextDocument(_PayloadDocument):
    kind: Literal["model_context"] = "model_context"
    payload: ModelContextPayload
    payload_model = ModelContextPayload


class TemporalAssessmentDocument(_PayloadDocument):
    kind: Literal["temporal_assessment"] = "temporal_assessment"
    payload: TemporalAssessmentPayload
    payload_model = TemporalAssessmentPayload


class TemporalAssessmentDocumentV2(_PayloadDocument):
    kind: Literal["temporal_assessment"] = "temporal_assessment"
    payload: TemporalAssessmentPayloadV2
    payload_model = TemporalAssessmentPayloadV2
    expected_schema_version: ClassVar[int] = 2


class SealedManifestDocument(_PayloadDocument):
    kind: Literal["sealed_manifest"] = "sealed_manifest"
    payload: SealedManifestPayload
    payload_model = SealedManifestPayload


class SealedManifestDocumentV2(_PayloadDocument):
    kind: Literal["sealed_manifest"] = "sealed_manifest"
    payload: SealedManifestPayloadV2
    payload_model = SealedManifestPayloadV2
    expected_schema_version: ClassVar[int] = 2


class CanaryPolicyDocument(_PayloadDocument):
    kind: Literal["canary_policy"] = "canary_policy"
    payload: CanaryPolicyPayload
    payload_model = CanaryPolicyPayload


class CanaryPolicyDocumentV2(_PayloadDocument):
    kind: Literal["canary_policy"] = "canary_policy"
    payload: CanaryPolicyPayloadV2
    payload_model = CanaryPolicyPayloadV2
    expected_schema_version: ClassVar[int] = 2


class SealAccessEventDocument(_PayloadDocument):
    kind: Literal["seal_access_event"] = "seal_access_event"
    payload: SealAccessEventPayload
    payload_model = SealAccessEventPayload


class CanaryObservationDocument(_PayloadDocument):
    kind: Literal["canary_observation"] = "canary_observation"
    payload: CanaryObservationPayload
    payload_model = CanaryObservationPayload


class BehavioralAuditPlanDocument(_PayloadDocument):
    kind: Literal["behavioral_audit_plan"] = "behavioral_audit_plan"
    payload: BehavioralAuditPlanPayload
    payload_model = BehavioralAuditPlanPayload


class BehavioralAuditPlanDocumentV2(_PayloadDocument):
    kind: Literal["behavioral_audit_plan"] = "behavioral_audit_plan"
    payload: BehavioralAuditPlanPayloadV2
    payload_model = BehavioralAuditPlanPayloadV2
    expected_schema_version: ClassVar[int] = 2


class BehavioralMethodRegistryDocument(_PayloadDocument):
    kind: Literal["behavioral_method_registry"] = "behavioral_method_registry"
    payload: BehavioralMethodRegistryPayload
    payload_model = BehavioralMethodRegistryPayload


class BehavioralTaskValidityDocument(_PayloadDocument):
    kind: Literal["behavioral_task_validity"] = "behavioral_task_validity"
    payload: BehavioralTaskValidityPayload
    payload_model = BehavioralTaskValidityPayload


class BehavioralObservationDocument(_PayloadDocument):
    kind: Literal["behavioral_observation"] = "behavioral_observation"
    payload: BehavioralObservationPayload
    payload_model = BehavioralObservationPayload


class BehavioralAssessmentDocument(_PayloadDocument):
    kind: Literal["behavioral_assessment"] = "behavioral_assessment"
    payload: BehavioralAssessmentPayload
    payload_model = BehavioralAssessmentPayload


class FirewallDecisionDocument(_PayloadDocument):
    kind: Literal["firewall_decision"] = "firewall_decision"
    payload: FirewallDecisionPayload
    payload_model = FirewallDecisionPayload


class FirewallDecisionDocumentV2(_PayloadDocument):
    kind: Literal["firewall_decision"] = "firewall_decision"
    payload: FirewallDecisionPayloadV2
    payload_model = FirewallDecisionPayloadV2
    expected_schema_version: ClassVar[int] = 2


class FirewallPolicyDocumentV2(_PayloadDocument):
    kind: Literal["firewall_policy"] = "firewall_policy"
    payload: FirewallPolicyPayloadV2
    payload_model = FirewallPolicyPayloadV2
    expected_schema_version: ClassVar[int] = 2


class FirewallScopeDocument(_PayloadDocument):
    kind: Literal["firewall_scope"] = "firewall_scope"
    payload: FirewallScopePayload
    payload_model = FirewallScopePayload


class ReplacementSourceMetadataDocument(_PayloadDocument):
    kind: Literal["replacement_source_metadata"] = "replacement_source_metadata"
    payload: ReplacementSourceMetadataPayload
    payload_model = ReplacementSourceMetadataPayload


class ReplacementPlanDocument(_PayloadDocument):
    kind: Literal["replacement_plan"] = "replacement_plan"
    payload: ReplacementPlanPayload
    payload_model = ReplacementPlanPayload


class ReplacementPlanDocumentV2(_PayloadDocument):
    kind: Literal["replacement_plan"] = "replacement_plan"
    payload: ReplacementPlanPayloadV2
    payload_model = ReplacementPlanPayloadV2
    expected_schema_version: ClassVar[int] = 2


class ReplacementValidationDocument(_PayloadDocument):
    kind: Literal["replacement_validation"] = "replacement_validation"
    payload: ReplacementValidationPayload
    payload_model = ReplacementValidationPayload


class DerivedBenchmarkManifestDocument(_PayloadDocument):
    kind: Literal["derived_benchmark_manifest"] = "derived_benchmark_manifest"
    payload: DerivedBenchmarkManifestPayload
    payload_model = DerivedBenchmarkManifestPayload


class MonitorPolicyDocument(_PayloadDocument):
    kind: Literal["monitor_policy"] = "monitor_policy"
    payload: MonitorPolicyPayload
    payload_model = MonitorPolicyPayload


class BenchmarkHealthDocument(_PayloadDocument):
    kind: Literal["benchmark_health"] = "benchmark_health"
    payload: BenchmarkHealthPayload
    payload_model = BenchmarkHealthPayload


class AuditAttestationDocument(_PayloadDocument):
    kind: Literal["audit_attestation"] = "audit_attestation"
    payload: AuditAttestationPayload
    payload_model = AuditAttestationPayload


AuditDocument = (
    BenchmarkSnapshotDocument
    | TaskFingerprintDocument
    | CorpusSnapshotDocument
    | AuditPlanDocument
    | QueryManifestDocument
    | CoverageManifestDocument
    | MatchEvidenceDocument
    | MatchEvidenceDocumentV2
    | RiskPolicyDocument
    | RiskPolicyDocumentV2
    | RiskAssessmentDocument
    | RiskAssessmentDocumentV2
    | ModelContextDocument
    | TemporalAssessmentDocument
    | TemporalAssessmentDocumentV2
    | SealedManifestDocument
    | SealedManifestDocumentV2
    | CanaryPolicyDocument
    | CanaryPolicyDocumentV2
    | SealAccessEventDocument
    | CanaryObservationDocument
    | BehavioralAuditPlanDocument
    | BehavioralAuditPlanDocumentV2
    | BehavioralMethodRegistryDocument
    | BehavioralTaskValidityDocument
    | BehavioralObservationDocument
    | BehavioralAssessmentDocument
    | FirewallDecisionDocument
    | FirewallDecisionDocumentV2
    | FirewallPolicyDocumentV2
    | FirewallScopeDocument
    | ReplacementSourceMetadataDocument
    | ReplacementPlanDocument
    | ReplacementPlanDocumentV2
    | ReplacementValidationDocument
    | DerivedBenchmarkManifestDocument
    | MonitorPolicyDocument
    | BenchmarkHealthDocument
    | AuditAttestationDocument
)

_DOCUMENT_MODELS: dict[str, type[_PayloadDocument]] = {
    model.model_fields["kind"].default: model
    for model in (
        BenchmarkSnapshotDocument,
        TaskFingerprintDocument,
        CorpusSnapshotDocument,
        AuditPlanDocument,
        QueryManifestDocument,
        CoverageManifestDocument,
        MatchEvidenceDocument,
        RiskPolicyDocument,
        RiskAssessmentDocument,
        ModelContextDocument,
        TemporalAssessmentDocument,
        SealedManifestDocument,
        CanaryPolicyDocument,
        SealAccessEventDocument,
        CanaryObservationDocument,
        BehavioralAuditPlanDocument,
        BehavioralMethodRegistryDocument,
        BehavioralTaskValidityDocument,
        BehavioralObservationDocument,
        BehavioralAssessmentDocument,
        FirewallDecisionDocument,
        FirewallPolicyDocumentV2,
        FirewallScopeDocument,
        ReplacementSourceMetadataDocument,
        ReplacementPlanDocument,
        ReplacementValidationDocument,
        DerivedBenchmarkManifestDocument,
        MonitorPolicyDocument,
        BenchmarkHealthDocument,
        AuditAttestationDocument,
    )
}
_VERSIONED_DOCUMENT_MODELS: dict[tuple[str, int], type[_PayloadDocument]] = {
    ("match_evidence", 2): MatchEvidenceDocumentV2,
    ("risk_policy", 2): RiskPolicyDocumentV2,
    ("risk_assessment", 2): RiskAssessmentDocumentV2,
    ("temporal_assessment", 2): TemporalAssessmentDocumentV2,
    ("sealed_manifest", 2): SealedManifestDocumentV2,
    ("canary_policy", 2): CanaryPolicyDocumentV2,
    ("behavioral_audit_plan", 2): BehavioralAuditPlanDocumentV2,
    ("firewall_decision", 2): FirewallDecisionDocumentV2,
    ("firewall_policy", 2): FirewallPolicyDocumentV2,
    ("replacement_plan", 2): ReplacementPlanDocumentV2,
}


def _validate_nested_json(value: Any) -> None:
    """Apply the shared canonical JSON rules to flexible, explicitly scoped maps."""
    canonical_json_bytes(value)


def parse_audit_document(data: bytes | str) -> AuditDocument:
    """Parse canonical JSON, reject duplicate keys, and validate the declared document kind."""
    raw = parse_json_strict(data)
    if not isinstance(raw, dict):
        raise ValueError("audit document must be a JSON object")
    kind = raw.get("kind")
    schema_version = raw.get("schema_version")
    if not isinstance(kind, str) or type(schema_version) is not int:
        raise ValueError("unknown or missing audit document kind")
    model = (
        _DOCUMENT_MODELS.get(kind)
        if schema_version == 1
        else _VERSIONED_DOCUMENT_MODELS.get((kind, schema_version))
    )
    if model is None:
        raise ValueError("unknown audit document kind/schema version")
    try:
        return cast(AuditDocument, model.model_validate_json(data))
    except ValidationError as error:
        raise ValueError(f"invalid {kind} document: {error}") from error


def audit_document_value(document: AuditDocument) -> dict[str, Any]:
    """Return semantic canonical content; row identity and operational metadata are excluded."""
    return canonical_envelope(
        document.kind,
        document.payload.model_dump(mode="json"),
        document.schema_version,
    )


def audit_document_bytes(document: AuditDocument) -> bytes:
    return canonical_json_bytes(audit_document_value(document))


def audit_document_digest(document: AuditDocument) -> str:
    return "sha256:" + hashlib.sha256(audit_document_bytes(document)).hexdigest()


def validate_sealed_manifest_transition(
    previous: SealedManifestDocumentV2,
    successor: SealedManifestDocumentV2,
    appended_event: SealAccessEventDocument,
) -> None:
    """Enforce one-event linear history, immutable task identity and monotonic exposure."""
    old = previous.payload
    new = successor.payload
    if successor.supersedes_id != previous.id:
        raise ValueError("sealed manifest successor must point to the previous version")
    if previous.id == successor.id or previous.schema_version != 2 or successor.schema_version != 2:
        raise ValueError("sealed manifest transition requires distinct v2 documents")
    immutable_identity = (
        old.tenant_id,
        old.encrypted_artifact_refs,
        old.hiding_commitment,
        old.encryption_algorithm,
        old.nonce_length_bytes,
        old.access_policy,
        old.retention_policy,
    )
    next_identity = (
        new.tenant_id,
        new.encrypted_artifact_refs,
        new.hiding_commitment,
        new.encryption_algorithm,
        new.nonce_length_bytes,
        new.access_policy,
        new.retention_policy,
    )
    if immutable_identity != next_identity:
        raise ValueError("sealed manifest successor changed task identity or policy")
    if new.access_event_refs[: len(old.access_event_refs)] != old.access_event_refs:
        raise ValueError("sealed manifest successor erased or reordered access history")
    new_event_refs = new.access_event_refs[len(old.access_event_refs) :]
    expected_event_ref = AuditDocumentRef(
        document_id=appended_event.id,
        digest=audit_document_digest(appended_event),
        kind="seal_access_event",
    )
    if new_event_refs != (expected_event_ref,):
        raise ValueError("sealed manifest successor must append exactly its access event")
    if (
        appended_event.payload.manifest_ref.document_id != previous.id
        or appended_event.payload.manifest_ref.digest != audit_document_digest(previous)
        or appended_event.payload.tenant_id != old.tenant_id
    ):
        raise ValueError("access event is not bound to the previous tenant manifest")
    ranks: dict[str, int] = {
        "sealed": 0,
        "authorized_disclosure": 1,
        "public_exposed": 2,
        "compromised": 3,
        "retired": 4,
    }
    event_state: SealState = (
        "sealed" if appended_event.payload.exposure == "none" else appended_event.payload.exposure
    )
    expected_state = (
        old.disclosure_state if ranks[old.disclosure_state] >= ranks[event_state] else event_state
    )
    if new.disclosure_state != expected_state:
        raise ValueError("manifest disclosure state does not reflect its access event")
    if old.disclosure_state == "retired":
        raise ValueError("retired sealed manifests cannot be superseded")
    wrapping_changed = (
        old.wrapped_key_ref != new.wrapped_key_ref
        or old.key_provider != new.key_provider
        or old.key_version != new.key_version
        or old.key_wrapping_algorithm != new.key_wrapping_algorithm
        or old.recovery_ref != new.recovery_ref
    )
    authorized_rotation = (
        appended_event.payload.operation == "key_rotation"
        and appended_event.payload.outcome != "denied"
    )
    if wrapping_changed and (
        old.wrapped_key_ref == new.wrapped_key_ref
        or (old.key_provider, old.key_version, old.key_wrapping_algorithm)
        == (new.key_provider, new.key_version, new.key_wrapping_algorithm)
    ):
        raise ValueError("key rotation must replace wrapped-key bytes and version metadata")
    if wrapping_changed != authorized_rotation:
        raise ValueError("only an authorized key-rotation event may replace wrapped keys")


_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"planned", "blocked", "cancelled"}),
    "planned": frozenset({"queued", "blocked", "cancelled"}),
    "queued": frozenset({"scanning", "blocked", "cancelled"}),
    "scanning": frozenset({"verifying", "blocked", "cancelled"}),
    "verifying": frozenset({"assessing", "blocked", "cancelled"}),
    "assessing": frozenset({"review_required", "partial", "blocked", "cancelled"}),
    "review_required": frozenset({"complete", "partial", "blocked", "cancelled"}),
    "blocked": frozenset({"queued", "scanning", "verifying", "assessing", "cancelled"}),
    "complete": frozenset(),
    "partial": frozenset(),
    "cancelled": frozenset(),
}
_QUERY_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"queued", "blocked", "cancelled"}),
    "queued": frozenset({"running", "blocked", "cancelled"}),
    "running": frozenset({"complete", "truncated", "failed", "blocked", "cancelled"}),
    "blocked": frozenset({"queued", "cancelled"}),
    "complete": frozenset(),
    "truncated": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def validate_audit_transition(
    state_machine: Literal["run", "query"],
    previous: AuditRunState | SourceQueryState,
    current: AuditRunState | SourceQueryState,
) -> None:
    """Reject state changes outside the immutable audit run/query state machines."""
    transitions = _RUN_TRANSITIONS if state_machine == "run" else _QUERY_TRANSITIONS
    allowed = transitions.get(previous, frozenset())
    if current not in allowed:
        raise ValueError(f"invalid audit state transition: {previous} -> {current}")
