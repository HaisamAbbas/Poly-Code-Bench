"""Strict, immutable contracts for canonical benchmark-audit evidence documents."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from polycodebench_core.canonical import canonical_envelope, canonical_json_bytes, parse_json_strict
from polycodebench_core.models import Decimal6, Digest, Seed64, UtcTimestamp

AuditKind = Literal[
    "benchmark_snapshot",
    "task_fingerprint",
    "corpus_snapshot",
    "audit_plan",
    "query_manifest",
    "coverage_manifest",
    "match_evidence",
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
    null_reason: Literal[
        "not_run", "unavailable", "not_applicable", "insufficient_coverage", "withheld"
    ] | None

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


class TemporalAssessmentPayload(StrictAuditModel):
    artifact_ref: ImmutableArtifactRef
    source_chronology: tuple[TimestampEvidence, ...]
    model_revision_ref: AuditDocumentRef | None
    cutoff_evidence: tuple[TimestampEvidence, ...]
    uncertainty: tuple[ShortText, ...]
    exposure_paths: tuple[AuditDocumentRef, ...]
    status: TemporalState


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
    schema_version: Literal[1]
    payload: Any
    metadata: DocumentMetadata
    supersedes_id: UUID | None = None

    payload_model: ClassVar[type[StrictAuditModel]]

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


class RiskPolicyDocument(_PayloadDocument):
    kind: Literal["risk_policy"] = "risk_policy"
    payload: RiskPolicyPayload
    payload_model = RiskPolicyPayload


class RiskAssessmentDocument(_PayloadDocument):
    kind: Literal["risk_assessment"] = "risk_assessment"
    payload: RiskAssessmentPayload
    payload_model = RiskAssessmentPayload


class TemporalAssessmentDocument(_PayloadDocument):
    kind: Literal["temporal_assessment"] = "temporal_assessment"
    payload: TemporalAssessmentPayload
    payload_model = TemporalAssessmentPayload


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
    | RiskPolicyDocument
    | RiskAssessmentDocument
    | TemporalAssessmentDocument
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


def _validate_nested_json(value: Any) -> None:
    """Apply the shared canonical JSON rules to flexible, explicitly scoped maps."""
    canonical_json_bytes(value)


def parse_audit_document(data: bytes | str) -> AuditDocument:
    """Parse canonical JSON, reject duplicate keys, and validate the declared document kind."""
    raw = parse_json_strict(data)
    if not isinstance(raw, dict):
        raise ValueError("audit document must be a JSON object")
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _DOCUMENT_MODELS:
        raise ValueError("unknown or missing audit document kind")
    model = _DOCUMENT_MODELS[kind]
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
