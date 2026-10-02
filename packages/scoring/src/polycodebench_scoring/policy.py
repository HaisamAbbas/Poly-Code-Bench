"""The frozen scoring policy (Technical Spec 14.1, 14.3, 14.4; Architecture 8.3-8.5).

The policy *is* the configuration document. ``config/scoring/pilot-v1.yaml`` validates into
``FrozenScoringPolicy`` without a translation layer, so the file's canonical digest is the policy's
canonical digest: weights, the severity penalty table, the efficiency breakpoints, the anchors and
the rounding mode are all frozen in one file, before any candidate runs, and any edit to them
produces a new policy version rather than a quiet re-score.
"""

from __future__ import annotations

from typing import Literal

from polycodebench_core.models import Digest, ScoreDimension, Slug
from pydantic import Field, model_validator

from polycodebench_scoring.contracts import SCORE_SCHEMA_VERSION, ScoringModel

COMPOSITE_DIMENSIONS: tuple[ScoreDimension, ...] = (
    ScoreDimension.CORRECTNESS,
    ScoreDimension.SECURITY,
    ScoreDimension.EFFICIENCY,
    ScoreDimension.CODE_QUALITY,
    ScoreDimension.IDIOMATIC,
    ScoreDimension.ROBUSTNESS,
)
#: The fixed order used for every deterministic tie-break in this package.
DIMENSION_ORDER: dict[ScoreDimension, int] = {
    dimension: index for index, dimension in enumerate(COMPOSITE_DIMENSIONS)
}
TOTAL_BP = 10_000
SEVERITIES = ("critical", "high", "medium", "low")
QUALITY_GROUPS = (
    "naming_readability",
    "decomposition",
    "duplication",
    "unnecessary_complexity",
    "repository_style_consistency",
    "minimal_relevant_scope",
)


class CompositeWeights(ScoringModel):
    """Base weights in basis points. They sum to 10000 and are never edited per candidate."""

    kind: Literal["composite_weights"] = "composite_weights"
    correctness: int = Field(ge=1, le=10_000)
    security: int = Field(ge=0, le=10_000)
    efficiency: int = Field(ge=0, le=10_000)
    code_quality: int = Field(ge=0, le=10_000)
    idiomatic: int = Field(ge=0, le=10_000)
    robustness: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def weights_are_complete(self) -> CompositeWeights:
        total = sum(getattr(self, dimension.value) for dimension in COMPOSITE_DIMENSIONS)
        if total != TOTAL_BP:
            raise ValueError(f"composite weights must sum to {TOTAL_BP} basis points, got {total}")
        if self.correctness != 3000:
            raise ValueError("the pilot composite keeps 30 correctness points")
        return self

    def base_bp(self, dimension: ScoreDimension) -> int:
        return int(getattr(self, dimension.value))

    @property
    def quality_points_bp(self) -> int:
        return TOTAL_BP - self.correctness


class CodeCompositePolicy(ScoringModel):
    kind: Literal["code_composite"] = "code_composite"
    unit: Literal["basis_points"]
    weights: CompositeWeights
    failed_correctness_gate_composite: Literal[0] = 0
    missing_required_evidence: Literal["nonpublishable"] = "nonpublishable"
    unavailable_infrastructure_gate: Literal["unknown"] = "unknown"
    not_applicable_quality_redistribution: Literal["fixed_relative_quality_weights"] = (
        "fixed_relative_quality_weights"
    )


class SecurityPolicy(ScoringModel):
    """Severity penalties, one per canonical issue, floored at zero."""

    kind: Literal["security_policy"] = "security_policy"
    penalties: dict[str, int]
    counting: Literal["unique_confirmed_issue_primary_owner_only"] = (
        "unique_confirmed_issue_primary_owner_only"
    )
    advisory_snapshot_required: bool

    @model_validator(mode="after")
    def every_severity_has_a_penalty(self) -> SecurityPolicy:
        if set(self.penalties) != set(SEVERITIES):
            raise ValueError(f"penalties must cover exactly {list(SEVERITIES)}")
        if any(value <= 0 for value in self.penalties.values()):
            raise ValueError("a penalty must be positive")
        return self

    def penalty(self, severity: str) -> int:
        return int(self.penalties[severity])


class EfficiencyPolicy(ScoringModel):
    """The measured-performance transform (Technical Spec 13.4), restated as policy input."""

    kind: Literal["efficiency_policy"] = "efficiency_policy"
    warmups: int = Field(ge=0)
    paired_measured_iterations: int = Field(ge=1)
    workload_scales_minimum: int = Field(ge=1)
    single_thread_default: bool
    timing_breakpoint: int = Field(ge=2)
    memory_breakpoint: int = Field(ge=2)
    time_weight: int = Field(ge=0, le=10_000)
    memory_weight: int = Field(ge=0, le=10_000)
    benchmark_floor_and_censor_policy: str

    @model_validator(mode="after")
    def weights_are_complete(self) -> EfficiencyPolicy:
        if self.time_weight + self.memory_weight != TOTAL_BP:
            raise ValueError("efficiency component weights must sum to 10000 basis points")
        return self


class CodeQualityPolicy(ScoringModel):
    kind: Literal["code_quality_policy"] = "code_quality_policy"
    items: dict[str, int]
    anchors: tuple[str, ...]

    @model_validator(mode="after")
    def groups_are_complete(self) -> CodeQualityPolicy:
        if tuple(sorted(self.items)) != tuple(sorted(QUALITY_GROUPS)):
            raise ValueError(f"code quality groups must be exactly {list(QUALITY_GROUPS)}")
        if sum(self.items.values()) != TOTAL_BP:
            raise ValueError("code quality group weights must sum to 10000 basis points")
        if self.anchors != ("0.000000", "0.500000", "1.000000"):
            raise ValueError("the pilot anchors are 0, 0.5 and 1 exactly")
        return self


class IdiomaticPolicy(ScoringModel):
    """Idiomatic strength is the orthogonal language rubric plus declared residual judge items.

    Technical Spec 14.4 makes the language rubric the dimension and Technical Spec 15.1 keeps two
    residual idiom judgements in the same dimension, so the scorer needs a *declared* split of the
    dimension's 10000 basis points. It is a pilot parameter frozen here, before any candidate runs,
    rather than inferred from whichever items happened to be measured.

    The language rubric keeps its own relative weights and is renormalised into
    ``language_rubric_weight_bp`` (Architecture 8.7: "weights renormalized within that subset"), so
    the language profile stays valid on its own terms and the composite gets an exact integer split.
    """

    kind: Literal["idiomatic_policy"] = "idiomatic_policy"
    scoring_source: str
    profile_source_digest: Digest
    language_rubric_weight_bp: int = Field(ge=0, le=TOTAL_BP)
    residual_judge_weight_bp: int = Field(ge=0, le=TOTAL_BP)
    residual_judge_items: dict[str, int]
    full_language_profiles_are_diagnostic_only: Literal[True] = True
    scoring_items_require_single_primary_owner: Literal[True] = True

    @model_validator(mode="after")
    def blocks_span_the_dimension(self) -> IdiomaticPolicy:
        if self.language_rubric_weight_bp + self.residual_judge_weight_bp != TOTAL_BP:
            raise ValueError("the language rubric and residual judge weights must sum to 10000")
        if bool(self.residual_judge_items) != (self.residual_judge_weight_bp > 0):
            raise ValueError("residual judge items and their weight must appear together")
        if sum(self.residual_judge_items.values()) != self.residual_judge_weight_bp:
            raise ValueError("residual judge item weights must sum to residual_judge_weight_bp")
        return self


class RobustnessPolicy(ScoringModel):
    """Weighted frozen quality-only scenarios, plus declared residual judge items.

    The scenario split belongs to the frozen task, not to the scoring policy: the policy fixes how
    much of the dimension scenarios may claim and which residual judge item completes it.
    """

    kind: Literal["robustness_policy"] = "robustness_policy"
    scoring_source: str
    scenario_weight_bp: int = Field(ge=0, le=TOTAL_BP)
    residual_judge_weight_bp: int = Field(ge=0, le=TOTAL_BP)
    residual_judge_items: dict[str, int]
    duplicate_test_count_bonus: Literal[False] = False

    @model_validator(mode="after")
    def blocks_span_the_dimension(self) -> RobustnessPolicy:
        if self.scenario_weight_bp + self.residual_judge_weight_bp != TOTAL_BP:
            raise ValueError("scenario and residual judge weights must sum to 10000")
        if bool(self.residual_judge_items) != (self.residual_judge_weight_bp > 0):
            raise ValueError("residual judge items and their weight must appear together")
        if sum(self.residual_judge_items.values()) != self.residual_judge_weight_bp:
            raise ValueError("residual judge item weights must sum to residual_judge_weight_bp")
        return self


class JudgePolicy(ScoringModel):
    kind: Literal["judge_policy"] = "judge_policy"
    minimum_votes_per_applicable_item: int = Field(ge=1)
    model_must_differ_from_candidate: bool
    blinded: tuple[str, ...]
    human_and_judge_calibration: str


class RoundingPolicy(ScoringModel):
    """Explicit precision and rounding control, as required by [T4]."""

    kind: Literal["rounding_policy"] = "rounding_policy"
    mode: Literal["half_even"]
    places: Literal[6]


class FrozenScoringPolicy(ScoringModel):
    """The complete, frozen scoring configuration. Nothing here is candidate-dependent."""

    kind: Literal["frozen_scoring_policy"] = "frozen_scoring_policy"
    policy_id: Slug
    score_schema_version: int = Field(ge=1)
    status: str
    effective_for_scoring: bool
    calibration_status: str
    source: str
    evidence_ownership_policy_id: Slug
    owner_approval: str = Field(min_length=1, max_length=64)
    approved_by: Slug
    approved_on: str = Field(min_length=1, max_length=32)
    approval_scope: str = Field(min_length=1, max_length=200)
    rounding: RoundingPolicy
    code_composite: CodeCompositePolicy
    security: SecurityPolicy
    efficiency: EfficiencyPolicy
    code_quality: CodeQualityPolicy
    idiomatic: IdiomaticPolicy
    robustness: RobustnessPolicy
    judge: JudgePolicy

    def nominal_weight_bp(self, dimension: ScoreDimension) -> int:
        return self.code_composite.weights.base_bp(dimension)

    @model_validator(mode="after")
    def policy_is_scoring_shaped(self) -> FrozenScoringPolicy:
        if self.score_schema_version != SCORE_SCHEMA_VERSION:
            raise ValueError(
                f"policy targets score schema {self.score_schema_version}; this scorer implements "
                f"{SCORE_SCHEMA_VERSION}"
            )
        if self.owner_approval != "approved":
            raise ValueError("a scoring policy must record an owner approval")
        return self
