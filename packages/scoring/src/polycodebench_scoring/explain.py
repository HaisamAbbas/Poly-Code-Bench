"""The scorecard explanation (Architecture 12.3).

Every published number resolves through a chain that this document spells out in full: the nominal
policy weight, the effective weight actually used, the exact arithmetic, the item value, and the
evidence references underneath it. ``policy_digest`` is recorded on every row, as Architecture 12.3
requires, because the scorecard's own policy digest is otherwise reachable only from its parent.
"""

from __future__ import annotations

from typing import Literal

from polycodebench_core.models import (
    Digest,
    EvaluationState,
    Gate,
    ScoreDimension,
    ScoreValue,
    Slug,
)
from pydantic import Field

from polycodebench_scoring.contracts import ScoringModel
from polycodebench_scoring.manifest import BlockingReason, DiagnosticItemView, EvidenceRef


class ItemExplanation(ScoringModel):
    kind: Literal["item_explanation"] = "item_explanation"
    dimension: ScoreDimension
    item_id: Slug
    group: Literal["dimension", "rubric_group", "judge_item", "scenario"]
    primary_owner: ScoreDimension
    applicable: bool
    status: Literal["measured", "not_applicable", "gated", "missing", "needs_review"]
    raw_value: ScoreValue | None
    nominal_weight_bp: int = Field(ge=0, le=10_000)
    effective_weight_bp: str = Field(min_length=1, max_length=80)
    effective_weight_bps: int = Field(ge=0, le=10_000)
    contribution: ScoreValue
    formula: str = Field(min_length=1, max_length=400)
    evidence_refs: tuple[EvidenceRef, ...] = ()
    policy_digest: Digest
    reasons: tuple[str, ...] = ()


class DimensionExplanation(ScoringModel):
    kind: Literal["dimension_explanation"] = "dimension_explanation"
    dimension: ScoreDimension
    applicable: bool
    nominal_weight_bp: int = Field(ge=0, le=10_000)
    effective_weight_bp: str = Field(min_length=1, max_length=80)
    effective_weight_bps: int = Field(ge=0, le=10_000)
    raw_value: ScoreValue | None
    contribution: ScoreValue
    formula: str = Field(min_length=1, max_length=400)
    item_count: int = Field(ge=0)
    reasons: tuple[str, ...] = ()


class IssueExplanation(ScoringModel):
    kind: Literal["issue_explanation"] = "issue_explanation"
    issue_key: str
    family: str
    penalty_kind: str
    owner: ScoreDimension
    severity: str
    confidence: str
    relation: str
    penalty_bp: int = Field(ge=0)
    counted: bool
    blocks_completion: bool
    collapsed_reports: int = Field(ge=1)
    tools: tuple[Slug, ...]
    reason: str = Field(min_length=1, max_length=300)
    evidence_refs: tuple[EvidenceRef, ...] = ()


class ScorecardExplanation(ScoringModel):
    """Everything needed to reproduce a displayed score without re-running anything."""

    kind: Literal["scorecard_explanation"] = "scorecard_explanation"
    score_schema_version: int = Field(ge=1)
    board: Literal["six_dimension", "correctness_only"]
    policy_id: Slug
    policy_digest: Digest
    ownership_policy_id: Slug
    ownership_policy_digest: Digest
    language_profile_version: str | None = None
    evidence_manifest_digest: Digest
    scorer_digest: Digest
    status: EvaluationState
    gate: Gate
    total_score: ScoreValue | None
    total_exact: str | None = Field(default=None, min_length=1, max_length=80)
    total_formula: str = Field(min_length=1, max_length=600)
    blocking: tuple[BlockingReason, ...] = ()
    dimensions: tuple[DimensionExplanation, ...]
    items: tuple[ItemExplanation, ...]
    issues: tuple[IssueExplanation, ...] = ()
    diagnostic_items: tuple[DiagnosticItemView, ...] = ()
    collapsed_duplicate_issues: int = Field(ge=0)
    ignored_issues: tuple[str, ...] = ()
