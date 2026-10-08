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
    "behavioral_audit_plan",
    "firewall_decision",
    "replacement_plan",
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
SealState = Literal["sealed", "authorized_disclosure", "public_exposed", "compromised", "retired"]

_StrictModel = ConfigDict(extra="forbid", strict=True, frozen=True)
NonEmpty = Annotated[str, Field(min_length=1, max_length=4096)]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]
SafeInteger = Annotated[int, Field(ge=-(2**53 - 1), le=2**53 - 1)]
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


class CanaryPolicyPayload(StrictAuditModel):
    marker_generation: AuditDocumentRef
    detection: AuditDocumentRef
    access: AuditDocumentRef
    exposure_rules: tuple[ShortText, ...]
    collision_checks: tuple[AuditDocumentRef, ...]
    interpretation_limits: tuple[ShortText, ...]


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


class CanaryPolicyDocument(_PayloadDocument):
    kind: Literal["canary_policy"] = "canary_policy"
    payload: CanaryPolicyPayload
    payload_model = CanaryPolicyPayload


class BehavioralAuditPlanDocument(_PayloadDocument):
    kind: Literal["behavioral_audit_plan"] = "behavioral_audit_plan"
    payload: BehavioralAuditPlanPayload
    payload_model = BehavioralAuditPlanPayload


class FirewallDecisionDocument(_PayloadDocument):
    kind: Literal["firewall_decision"] = "firewall_decision"
    payload: FirewallDecisionPayload
    payload_model = FirewallDecisionPayload


class ReplacementPlanDocument(_PayloadDocument):
    kind: Literal["replacement_plan"] = "replacement_plan"
    payload: ReplacementPlanPayload
    payload_model = ReplacementPlanPayload


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
    | CanaryPolicyDocument
    | BehavioralAuditPlanDocument
    | FirewallDecisionDocument
    | ReplacementPlanDocument
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
        BehavioralAuditPlanDocument,
        FirewallDecisionDocument,
        ReplacementPlanDocument,
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
