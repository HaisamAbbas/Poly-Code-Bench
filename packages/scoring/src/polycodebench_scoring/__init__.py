"""PolyCodeBench deterministic scoring (Technical Spec 14, WP-15).

``score_evaluation`` is a pure function of a frozen task, a frozen policy and validated evidence.
It performs no filesystem mutation, no network access, no provider call, no database read and no
task execution, and it never reads a clock: its recorded timestamp and scorer-source digest are
inputs, so replaying an archived evaluation reproduces the archived bytes exactly.
"""

from .arithmetic import effective_weights, efficiency_value, integer_weights, quantize_score
from .contracts import SCORE_SCHEMA_VERSION, ScoringModel
from .errors import ScoringConfigError, ScoringRefusalCode, ScoringRefused
from .explain import (
    DimensionExplanation,
    IssueExplanation,
    ItemExplanation,
    ScorecardExplanation,
)
from .manifest import (
    BlockingReason,
    DiagnosticItemView,
    EfficiencyMeasurement,
    EvidenceRef,
    GateVerdict,
    RequiredEvidenceRecord,
    RubricItemEvidence,
    ScoringInvocation,
    SecurityIssueEvidence,
    ValidatedEvidenceManifest,
)
from .ownership import (
    DistinctConsequence,
    EvidenceOwnership,
    OwnedIssue,
    OwnershipLedger,
    OwnershipRule,
    resolve_owners,
)
from .policy import (
    COMPOSITE_DIMENSIONS,
    TOTAL_BP,
    CodeCompositePolicy,
    CodeQualityPolicy,
    CompositeWeights,
    EfficiencyPolicy,
    FrozenScoringPolicy,
    IdiomaticPolicy,
    JudgePolicy,
    RobustnessPolicy,
    RoundingPolicy,
    SecurityPolicy,
)
from .replay import ReplayCheck, ReplayReport, replay_outcome, replay_scorecard
from .scorer import ScoringOutcome, score_evaluation

__all__ = [
    "COMPOSITE_DIMENSIONS",
    "SCORE_SCHEMA_VERSION",
    "TOTAL_BP",
    "BlockingReason",
    "CodeCompositePolicy",
    "CodeQualityPolicy",
    "CompositeWeights",
    "DiagnosticItemView",
    "DimensionExplanation",
    "DistinctConsequence",
    "EfficiencyMeasurement",
    "EfficiencyPolicy",
    "EvidenceOwnership",
    "EvidenceRef",
    "FrozenScoringPolicy",
    "GateVerdict",
    "IdiomaticPolicy",
    "IssueExplanation",
    "ItemExplanation",
    "JudgePolicy",
    "OwnedIssue",
    "OwnershipLedger",
    "OwnershipRule",
    "ReplayCheck",
    "ReplayReport",
    "RequiredEvidenceRecord",
    "RobustnessPolicy",
    "RoundingPolicy",
    "RubricItemEvidence",
    "ScorecardExplanation",
    "ScoringConfigError",
    "ScoringInvocation",
    "ScoringModel",
    "ScoringOutcome",
    "ScoringRefusalCode",
    "ScoringRefused",
    "SecurityIssueEvidence",
    "SecurityPolicy",
    "ValidatedEvidenceManifest",
    "efficiency_value",
    "effective_weights",
    "integer_weights",
    "quantize_score",
    "replay_outcome",
    "replay_scorecard",
    "resolve_owners",
    "score_evaluation",
]
