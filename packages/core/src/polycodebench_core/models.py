"""Strict, versioned domain contracts used to generate the public schemas."""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)

SAFE_JSON_INTEGER_MAX = 9_007_199_254_740_991
SIGNED_INT64_MIN = -(2**63)
SIGNED_INT64_MAX = 2**63 - 1
UNSIGNED_INT64_MAX = 2**64 - 1


def _ascii_keyed_json(value: dict[str, Any]) -> dict[str, Any]:
    if any(not key.isascii() for key in value):
        raise ValueError("mapping keys must be ASCII")
    return value


def _validate_digest(value: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("digest must be lowercase sha256:<64 hex characters>")
    return value


def _validate_seed(value: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", value):
        raise ValueError("seed must be a canonical unsigned decimal integer string")
    if int(value) > UNSIGNED_INT64_MAX:
        raise ValueError("seed exceeds the unsigned 64-bit range")
    return value


def _validate_money(value: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*|-[1-9][0-9]*)", value):
        raise ValueError("money must be a canonical decimal integer string")
    integer = int(value)
    if not SIGNED_INT64_MIN <= integer <= SIGNED_INT64_MAX:
        raise ValueError("money exceeds the signed 64-bit range")
    return value


def _validate_decimal_6(value: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{6}", value):
        raise ValueError("value must be a nonnegative fixed-point decimal with six places")
    return value


def _validate_score(value: str) -> str:
    _validate_decimal_6(value)
    if Decimal(value) > 100:
        raise ValueError("score must be within [0,100]")
    return value


def _validate_timestamp(value: str) -> str:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z",
        value,
    ):
        raise ValueError("timestamp must be RFC 3339 UTC and end in Z")
    from datetime import datetime

    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp is not a valid calendar time") from exc
    return value


def _validate_entity_id(value: str) -> str:
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        value,
    ):
        raise ValueError("entity ID must be a canonical lowercase UUIDv4")
    return value


def _validate_slug(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", value):
        raise ValueError("identifier must be a lowercase ASCII slug")
    return value


def _validate_relative_path(value: str) -> str:
    from polycodebench_core.identity import validate_relative_path

    return validate_relative_path(value)


Digest = Annotated[
    str,
    StringConstraints(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"),
    AfterValidator(_validate_digest),
]
EntityId = Annotated[
    str,
    StringConstraints(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
    AfterValidator(_validate_entity_id),
]
Seed64 = Annotated[
    str,
    StringConstraints(max_length=20, pattern=r"^(?:0|[1-9][0-9]*)$"),
    AfterValidator(_validate_seed),
]
MoneyMicros = Annotated[
    str,
    StringConstraints(max_length=20, pattern=r"^(?:0|[1-9][0-9]*|-[1-9][0-9]*)$"),
    AfterValidator(_validate_money),
]
Decimal6 = Annotated[
    str,
    StringConstraints(max_length=64, pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{6}$"),
    AfterValidator(_validate_decimal_6),
]
ScoreValue = Annotated[
    str,
    StringConstraints(
        max_length=10,
        pattern=r"^(?:100\.000000|(?:0|[1-9][0-9]?)\.[0-9]{6})$",
    ),
    AfterValidator(_validate_score),
]
UtcTimestamp = Annotated[
    str,
    StringConstraints(
        max_length=35,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$",
    ),
    AfterValidator(_validate_timestamp),
]
Slug = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$"),
    AfterValidator(_validate_slug),
]
RelativePath = Annotated[
    str,
    StringConstraints(min_length=1),
    AfterValidator(_validate_relative_path),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "format": "pcb-relative-posix-path",
            "description": (
                "UTF-8 relative POSIX path; rejects absolute, NUL, dot and parent components."
            ),
        }
    ),
]
SchemaVersion = Literal[1]


class ContractModel(BaseModel):
    """All public contracts reject extras and Python-side coercions."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset()
    schema_version: SchemaVersion


class ContractErrorCode(StrEnum):
    INVALID_DOCUMENT = "invalid_document"
    INVALID_REFERENCE = "invalid_reference"
    INVALID_RANGE = "invalid_range"
    INVALID_UTF8 = "invalid_utf8"
    DUPLICATE_KEY = "duplicate_key"
    UNSAFE_PATH = "unsafe_path"
    INVALID_CANONICAL_VALUE = "invalid_canonical_value"


class ContractError(ContractModel):
    kind: Literal["contract_error"]
    code: ContractErrorCode
    message: str = Field(min_length=1, max_length=512)
    path: str | None = None


class AttemptState(StrEnum):
    PLANNED = "planned"
    PREPARING = "preparing"
    SOLVING = "solving"
    FROZEN = "frozen"
    MODEL_FAILED = "model_failed"
    INFRA_BLOCKED = "infra_blocked"
    CANCELLED = "cancelled"


class EvaluationState(StrEnum):
    PENDING = "pending"
    EVALUATING = "evaluating"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    INFRA_BLOCKED = "infra_blocked"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"


class JobState(StrEnum):
    BLOCKED = "blocked"
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    DEAD = "dead"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class Gate(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class MeasurementStatus(StrEnum):
    MEASURED = "measured"
    GATED = "gated"
    NOT_APPLICABLE = "not_applicable"
    MISSING = "missing"
    NEEDS_REVIEW = "needs_review"
    INVALIDATED = "invalidated"


class Visibility(StrEnum):
    HIDDEN = "hidden"
    INTERNAL = "internal"
    PUBLIC = "public"


class FailureClass(StrEnum):
    MODEL = "model"
    CANDIDATE_RUNTIME = "candidate_runtime"
    INFRASTRUCTURE = "infrastructure"
    EVALUATOR = "evaluator"
    TASK_DEFECT = "task_defect"
    POLICY_VIOLATION = "policy_violation"
    CANCELLED = "cancelled"


class ReleaseState(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class StatusVocabulary(ContractModel):
    kind: Literal["status_vocabulary"]
    attempt_state: AttemptState | None
    evaluation_state: EvaluationState | None
    job_state: JobState | None
    gate: Gate | None
    measurement_status: MeasurementStatus | None
    visibility: Visibility | None
    failure_class: FailureClass | None
    release_state: ReleaseState | None


class ScoreDimension(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    EFFICIENCY = "efficiency"
    CODE_QUALITY = "code_quality"
    IDIOMATIC = "idiomatic"
    ROBUSTNESS = "robustness"


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    UNREVIEWED = "unreviewed"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskBundleRef(ContractModel):
    kind: Literal["task_bundle_ref"]
    artifact_id: EntityId
    digest: Digest
    visibility: Visibility


class TaskSource(ContractModel):
    kind: Literal["task_source"]
    source_kind: Slug
    immutable_revision: str = Field(min_length=1, max_length=256)
    issue_or_cve: str | None = None
    rights_record_id: Slug
    first_public_at: UtcTimestamp | None
    curated_at: UtcTimestamp
    date_confidence: Literal["verified", "estimated", "unknown"]


class TaskRuntime(ContractModel):
    kind: Literal["task_runtime"]
    image_digest: Digest
    language_plugin_id: Slug
    language_plugin_version: str = Field(min_length=1, max_length=64)
    build_recipe_digest: Digest
    test_recipe_digest: Digest
    resource_class: Slug


class TaskAcceptance(ContractModel):
    kind: Literal["task_acceptance"]
    required_test_group_ids: list[Slug]
    hard_condition_ids: list[Slug]
    required_outputs: list[RelativePath]
    protected_paths: list[RelativePath]

    @model_validator(mode="after")
    def unique_references(self) -> TaskAcceptance:
        for field_name in ("required_test_group_ids", "hard_condition_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must contain unique identifiers")
        return self


class TaskQualityPlan(ContractModel):
    kind: Literal["task_quality_plan"]
    applicable_dimensions: list[ScoreDimension]
    evidence_owners: dict[Slug, ScoreDimension]
    required_analyzers: list[Slug]
    performance_policy_id: Slug | None
    judge_policy_id: Slug | None

    @model_validator(mode="after")
    def unique_dimensions_and_analyzers(self) -> TaskQualityPlan:
        if len(self.applicable_dimensions) != len(set(self.applicable_dimensions)):
            raise ValueError("applicable_dimensions must be unique")
        if len(self.required_analyzers) != len(set(self.required_analyzers)):
            raise ValueError("required_analyzers must be unique")
        return self


class TaskOracle(ContractModel):
    kind: Literal["task_oracle"]
    test_version: Slug | None
    ground_truth_version: Slug | None
    reference_version: Slug | None


class ProtocolConstraints(ContractModel):
    kind: Literal["protocol_constraints"]
    protocol_id: Slug
    allowed_tools: list[Slug]
    public_test_feedback: bool
    hidden_feedback: bool
    network_policy: Literal["disabled", "allowlisted"]
    dependency_inventory_digest: Digest | None
    maximum_model_turns: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    maximum_tool_calls: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    maximum_wall_seconds: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]


class AdmissionReport(ContractModel):
    kind: Literal["admission_report"]
    reference_check: Literal["pass", "fail", "not_run"]
    faulty_check: Literal["pass", "fail", "not_run"]
    alternative_check: Literal["pass", "fail", "not_run"]
    flakiness_check: Literal["pass", "fail", "not_run"]
    reviewer_id: Slug | None
    reviewed_at: UtcTimestamp | None


class TaskVersion(ContractModel):
    kind: Literal["task_version"]
    task_id: Slug
    version: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    track: Literal["A", "B"]
    family: Literal[
        "bug_hunt",
        "codegen",
        "repo_repair",
        "repo_task",
        "self_repair",
        "repo_qa",
        "output_prediction",
        "test_prediction",
    ]
    primary_language: Slug
    secondary_languages: list[Slug]
    source: TaskSource
    cluster_id: Slug
    difficulty: Slug
    stratum_id: Slug
    visible_bundle: TaskBundleRef
    hidden_bundle: TaskBundleRef
    output_contract_digest: Digest
    runtime: TaskRuntime
    acceptance: TaskAcceptance
    quality_plan: TaskQualityPlan
    oracle: TaskOracle
    protocol_constraints: ProtocolConstraints
    admission_report: AdmissionReport

    @model_validator(mode="after")
    def valid_task_relations(self) -> TaskVersion:
        if self.primary_language in self.secondary_languages:
            raise ValueError("primary_language cannot also be secondary")
        if len(self.secondary_languages) != len(set(self.secondary_languages)):
            raise ValueError("secondary_languages must be unique")
        if self.visible_bundle.artifact_id == self.hidden_bundle.artifact_id:
            raise ValueError("visible and hidden bundles must use different artifacts")
        if self.visible_bundle.digest == self.hidden_bundle.digest:
            raise ValueError("visible and hidden bundles must have different digests")
        if self.visible_bundle.visibility == Visibility.HIDDEN:
            raise ValueError("visible bundle cannot be hidden")
        if self.hidden_bundle.visibility != Visibility.HIDDEN:
            raise ValueError("hidden bundle must have hidden visibility")
        return self


class SamplingConfig(ContractModel):
    kind: Literal["sampling_config"]
    samples_per_task: Annotated[int, Field(strict=True, ge=1, le=SAFE_JSON_INTEGER_MAX)]
    master_seed: Seed64
    temperature: Decimal6
    provider_seed_policy: Literal["pass_if_supported", "omit", "deterministic_mapping"]


class RunConfig(ContractModel):
    kind: Literal["run_config"]
    run_id: EntityId
    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"run_id"})
    task_set_digest: Digest
    model_config_digest: Digest
    harness_digest: Digest
    protocol_id: Slug
    sampling: SamplingConfig
    budget_profile: Slug
    evaluation_policy_digest: Digest
    hardware_class: Slug
    judge_panel_digest: Digest | None
    split: Slug


class Candidate(ContractModel):
    kind: Literal["candidate"]
    candidate_id: EntityId
    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"candidate_id", "frozen_at"})
    run_id: EntityId
    task_id: Slug
    task_version: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    sample_index: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    submission_kind: Literal["source_bundle", "unified_diff", "findings", "answer", "typed_value"]
    payload_digest: Digest
    artifact_ids: list[EntityId]
    frozen_at: UtcTimestamp | None

    @model_validator(mode="after")
    def unique_artifacts(self) -> Candidate:
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("artifact_ids must be unique")
        return self


class SourceLocation(ContractModel):
    kind: Literal["source_location"]
    path: RelativePath
    start_line: Annotated[int, Field(strict=True, ge=1, le=SAFE_JSON_INTEGER_MAX)]
    end_line: Annotated[int, Field(strict=True, ge=1, le=SAFE_JSON_INTEGER_MAX)]
    start_column: Annotated[int, Field(strict=True, ge=1, le=SAFE_JSON_INTEGER_MAX)] | None
    end_column: Annotated[int, Field(strict=True, ge=1, le=SAFE_JSON_INTEGER_MAX)] | None
    base_or_candidate_digest: Digest

    @model_validator(mode="after")
    def valid_span(self) -> SourceLocation:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if (
            self.start_line == self.end_line
            and self.start_column is not None
            and self.end_column is not None
            and self.end_column < self.start_column
        ):
            raise ValueError("end_column must not precede start_column on the same line")
        return self


class Observation(ContractModel):
    kind: Literal["observation"]
    check_id: Slug
    tool_digest: Digest
    candidate_digest: Digest
    status: MeasurementStatus
    value: (
        str
        | bool
        | Annotated[int, Field(strict=True, ge=-SAFE_JSON_INTEGER_MAX, le=SAFE_JSON_INTEGER_MAX)]
        | None
    )
    severity: Literal["critical", "high", "medium", "low"] | None
    confidence: Confidence | None
    location: SourceLocation | None
    baseline_relation: Literal["introduced", "worsened", "unfixed", "inherited", "unknown"] | None
    issue_key: Slug | None
    primary_owner: ScoreDimension | None
    raw_artifact_ids: list[EntityId]
    explanation: str | None

    @model_validator(mode="after")
    def valid_observation_state(self) -> Observation:
        if self.status == MeasurementStatus.MEASURED and self.value is None:
            raise ValueError("measured observation requires a value")
        if self.status != MeasurementStatus.MEASURED and self.value is not None:
            raise ValueError("only measured observations may contain a value")
        if self.baseline_relation is not None and self.issue_key is None:
            raise ValueError("baseline_relation requires issue_key")
        if len(self.raw_artifact_ids) != len(set(self.raw_artifact_ids)):
            raise ValueError("raw_artifact_ids must be unique")
        return self


class Artifact(ContractModel):
    kind: Literal["artifact"]
    artifact_id: EntityId
    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"artifact_id"})
    digest: Digest
    media_type: str = Field(min_length=1, max_length=255)
    byte_size: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    visibility: Visibility
    producing_stage: Slug
    retention_policy: Slug


class ScoreItem(ContractModel):
    kind: Literal["score_item"]
    dimension: ScoreDimension
    item_id: Slug
    applicable: bool
    primary_owner: ScoreDimension
    raw_value: ScoreValue | None
    effective_weight_bps: Annotated[int, Field(strict=True, ge=0, le=10_000)]
    gating_reason: Slug | None
    contribution: ScoreValue
    evidence_ids: list[EntityId]

    @model_validator(mode="after")
    def valid_applicability(self) -> ScoreItem:
        if not self.applicable and self.raw_value is not None:
            raise ValueError("non-applicable score item must have null raw_value")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        return self


class Scorecard(ContractModel):
    kind: Literal["scorecard"]
    scorecard_id: EntityId
    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"scorecard_id", "created_at"})
    task_id: Slug
    task_version: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    run_id: EntityId
    candidate_id: EntityId
    evidence_manifest_digest: Digest
    scoring_policy_digest: Digest
    scorer_digest: Digest
    gate: Gate
    status: EvaluationState
    total_score: ScoreValue | None
    items: list[ScoreItem]
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def valid_scorecard(self) -> Scorecard:
        item_keys = [(item.dimension, item.item_id) for item in self.items]
        if len(item_keys) != len(set(item_keys)):
            raise ValueError("score items must be unique per dimension and item_id")
        if self.status == EvaluationState.READY and self.gate == Gate.UNKNOWN:
            raise ValueError("unknown gate cannot produce a ready scorecard")
        if self.status == EvaluationState.READY and self.total_score is None:
            raise ValueError("ready scorecard requires total_score")
        if self.status != EvaluationState.READY and self.total_score is not None:
            raise ValueError("incomplete scorecard cannot contain total_score")
        if self.gate == Gate.FAIL and self.total_score not in (None, "0.000000"):
            raise ValueError("failed correctness gate requires a zero score")
        if self.gate == Gate.FAIL and any(item.contribution != "0.000000" for item in self.items):
            raise ValueError("failed correctness gate requires zero contribution for every item")
        if self.status == EvaluationState.READY:
            applicable_weight = sum(
                item.effective_weight_bps for item in self.items if item.applicable
            )
            non_applicable_weight = sum(
                item.effective_weight_bps for item in self.items if not item.applicable
            )
            if applicable_weight != 10_000 or non_applicable_weight != 0:
                raise ValueError("ready scorecard effective weights must sum to 10000 basis points")
        return self


class ProtocolDefinition(ContractModel):
    kind: Literal["protocol"]
    protocol_id: Slug
    version: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    mode: Literal["single_shot", "standard_agent"]
    allowed_tools: list[Slug]
    public_test_feedback: bool
    hidden_feedback: bool
    network_policy: Literal["disabled", "allowlisted"]
    maximum_turns: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    maximum_tool_calls: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    maximum_wall_seconds: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    maximum_input_context_tokens: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]

    @model_validator(mode="after")
    def valid_protocol(self) -> ProtocolDefinition:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("allowed_tools must be unique")
        if self.mode == "single_shot" and self.allowed_tools:
            raise ValueError("single_shot protocol cannot declare tools")
        return self


class PluginManifest(ContractModel):
    kind: Literal["plugin_manifest"]
    plugin_id: Slug
    plugin_kind: Literal[
        "suite_adapter",
        "language_plugin",
        "analyzer_plugin",
        "model_adapter",
        "sandbox_provider",
        "judge_adapter",
        "scoring_policy",
    ]
    api_version: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    plugin_version: str = Field(min_length=1, max_length=64)
    config_schema_digest: Digest | None
    capabilities: list[Slug]

    @model_validator(mode="after")
    def unique_capabilities(self) -> PluginManifest:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("capabilities must be unique")
        return self


class BudgetProfile(ContractModel):
    kind: Literal["budget_profile"]
    budget_profile_id: Slug
    max_cost_usd_micros: MoneyMicros
    max_input_tokens: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    max_output_tokens: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]

    @model_validator(mode="after")
    def budget_is_nonnegative(self) -> BudgetProfile:
        if int(self.max_cost_usd_micros) < 0:
            raise ValueError("budget reservations must be nonnegative")
        return self


class BundleFile(ContractModel):
    kind: Literal["bundle_file"]
    path: RelativePath
    digest: Digest
    byte_size: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    file_type: Literal["regular_file", "symlink"]
    executable: bool
    symlink_target: RelativePath | None

    @model_validator(mode="after")
    def valid_file_entry(self) -> BundleFile:
        from polycodebench_core.identity import validate_relative_path

        validate_relative_path(self.path)
        if self.file_type == "symlink" and self.symlink_target is None:
            raise ValueError("symlink entry requires symlink_target")
        if self.file_type == "regular_file" and self.symlink_target is not None:
            raise ValueError("regular file cannot declare symlink_target")
        if self.symlink_target is not None:
            validate_relative_path(self.symlink_target)
        return self


class BundleFileManifest(ContractModel):
    kind: Literal["bundle_file_manifest"]
    files: list[BundleFile]

    @model_validator(mode="after")
    def unique_sorted_files(self) -> BundleFileManifest:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("bundle manifest paths must be unique")
        if paths != sorted(paths, key=lambda path: path.encode("utf-8")):
            raise ValueError("bundle manifest files must be sorted by UTF-8 path bytes")
        return self


class ContractGraph(BaseModel):
    """Cross-document referential checks, deliberately not itself a wire model."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    tasks: tuple[TaskVersion, ...] = ()
    runs: tuple[RunConfig, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    observations: tuple[Observation, ...] = ()
    scorecards: tuple[Scorecard, ...] = ()
    protocols: tuple[ProtocolDefinition, ...] = ()
    task_set_digests: tuple[Digest, ...] = ()
    model_config_digests: tuple[Digest, ...] = ()
    harness_digests: tuple[Digest, ...] = ()
    evaluation_policy_digests: tuple[Digest, ...] = ()
    budget_profiles: tuple[BudgetProfile, ...] = ()
    judge_panel_digests: tuple[Digest, ...] = ()
    hardware_classes: tuple[Slug, ...] = ()

    @model_validator(mode="after")
    def resolve_references(self) -> ContractGraph:
        _unique_index(self.tasks, lambda item: (item.task_id, item.version), "task version")
        artifacts = _unique_index(self.artifacts, lambda item: item.artifact_id, "artifact")
        runs = _unique_index(self.runs, lambda item: item.run_id, "run")
        candidates = _unique_index(self.candidates, lambda item: item.candidate_id, "candidate")
        _unique_index(self.protocols, lambda item: item.protocol_id, "protocol")
        task_keys = {(task.task_id, task.version) for task in self.tasks}
        run_ids = set(runs)
        protocols = {protocol.protocol_id for protocol in self.protocols}
        budgets = {item.budget_profile_id for item in self.budget_profiles}
        for run in self.runs:
            _require_reference(
                run.protocol_id in protocols, "run_config.protocol_id", run.protocol_id
            )
            _require_reference(
                run.task_set_digest in self.task_set_digests,
                "run_config.task_set_digest",
                run.task_set_digest,
            )
            _require_reference(
                run.model_config_digest in self.model_config_digests,
                "run_config.model_config_digest",
                run.model_config_digest,
            )
            _require_reference(
                run.harness_digest in self.harness_digests,
                "run_config.harness_digest",
                run.harness_digest,
            )
            _require_reference(
                run.evaluation_policy_digest in self.evaluation_policy_digests,
                "run_config.evaluation_policy_digest",
                run.evaluation_policy_digest,
            )
            _require_reference(
                run.budget_profile in budgets, "run_config.budget_profile", run.budget_profile
            )
            _require_reference(
                run.hardware_class in self.hardware_classes,
                "run_config.hardware_class",
                run.hardware_class,
            )
            if run.judge_panel_digest is not None:
                _require_reference(
                    run.judge_panel_digest in self.judge_panel_digests,
                    "run_config.judge_panel_digest",
                    run.judge_panel_digest,
                )
        for task in self.tasks:
            for bundle in (task.visible_bundle, task.hidden_bundle):
                artifact = artifacts.get(bundle.artifact_id)
                _require_reference(
                    artifact is not None, "task.bundle.artifact_id", bundle.artifact_id
                )
                if artifact is not None:
                    _require_reference(
                        artifact.digest == bundle.digest, "task.bundle.digest", bundle.artifact_id
                    )
                    _require_reference(
                        artifact.visibility == bundle.visibility,
                        "task.bundle.visibility",
                        bundle.artifact_id,
                    )
        for candidate in self.candidates:
            _require_reference(candidate.run_id in run_ids, "candidate.run_id", candidate.run_id)
            _require_reference(
                (candidate.task_id, candidate.task_version) in task_keys,
                "candidate.task",
                candidate.task_id,
            )
            for artifact_id in candidate.artifact_ids:
                _require_reference(artifact_id in artifacts, "candidate.artifact_ids", artifact_id)
        for observation in self.observations:
            for artifact_id in observation.raw_artifact_ids:
                _require_reference(
                    artifact_id in artifacts, "observation.raw_artifact_ids", artifact_id
                )
            _require_reference(
                any(
                    candidate.payload_digest == observation.candidate_digest
                    for candidate in self.candidates
                ),
                "observation.candidate_digest",
                observation.candidate_digest,
            )
        for scorecard in self.scorecards:
            scorecard_candidate = candidates.get(scorecard.candidate_id)
            _require_reference(
                scorecard_candidate is not None,
                "scorecard.candidate_id",
                scorecard.candidate_id,
            )
            if scorecard_candidate is not None:
                _require_reference(
                    scorecard_candidate.run_id == scorecard.run_id,
                    "scorecard.run_id",
                    scorecard.run_id,
                )
                _require_reference(
                    scorecard_candidate.task_id == scorecard.task_id
                    and scorecard_candidate.task_version == scorecard.task_version,
                    "scorecard.task",
                    scorecard.task_id,
                )
            _require_reference(scorecard.run_id in run_ids, "scorecard.run_id", scorecard.run_id)
            _require_reference(
                (scorecard.task_id, scorecard.task_version) in task_keys,
                "scorecard.task",
                scorecard.task_id,
            )
            known_artifact_ids = set(artifacts)
            for item in scorecard.items:
                for evidence_id in item.evidence_ids:
                    _require_reference(
                        evidence_id in known_artifact_ids, "scorecard.evidence_ids", evidence_id
                    )
        return self


def _unique_index(items: tuple[Any, ...], key: Any, label: str) -> dict[Any, Any]:
    indexed: dict[Any, Any] = {}
    for item in items:
        value = key(item)
        if value in indexed:
            raise ValueError(f"duplicate {label}: {value}")
        indexed[value] = item
    return indexed


def _document_digest(model: ContractModel) -> str:
    from polycodebench_core.canonical import canonical_document_digest

    return canonical_document_digest(model)


def _require_reference(valid: bool, field_name: str, value: str) -> None:
    if not valid:
        from polycodebench_core.errors import InvalidReferenceError

        raise InvalidReferenceError(field_name, value)
