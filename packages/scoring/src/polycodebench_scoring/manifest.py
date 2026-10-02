"""The scorer's evidence input: ``ValidatedEvidenceManifest`` (Technical Spec 14.1).

This module is deliberately the *only* thing the scorer reads besides the task and the policy. It
holds observations and normalized findings, never scores: security penalties, rubric item values
and the efficiency ratios all arrive as evidence and the policy converts them here, inside scoring.

Three rules are load-bearing and are enforced by construction rather than by convention:

* one canonical issue key, however many tools reported it (``tools`` is a set-like tuple);
* an item is ``measured`` or it has no value at all - never a neutral default;
* required evidence carries its own completeness verdict, so a missing analyzer is visibly missing
  instead of silently reading as "nothing found".
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import (
    Confidence,
    Digest,
    EntityId,
    ScoreDimension,
    ScoreValue,
    Slug,
    UtcTimestamp,
)
from pydantic import Field, model_validator

from polycodebench_scoring.contracts import ScoringModel

ItemStatus = Literal["measured", "not_applicable", "missing", "needs_review"]
EvidenceClass = Literal["artifact", "case", "analyzer_report", "workload", "judge_vote", "scenario"]
BlockingClass = Literal["required_evidence", "adjudication", "judge_votes", "infrastructure"]


class EvidenceRef(ScoringModel):
    """A pointer into permitted evidence. Digests, not row numbers, are the durable identity."""

    kind: Literal["evidence_ref"] = "evidence_ref"
    ref_type: EvidenceClass
    ref_id: str = Field(min_length=1, max_length=256)
    digest: Digest | None = None
    tool_id: Slug | None = None
    locator: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def artifact_needs_a_digest(self) -> EvidenceRef:
        if self.ref_type == "artifact" and self.digest is None:
            raise ValueError("an artifact reference must carry its digest")
        return self


class ScoringInvocation(ScoringModel):
    """Who asked for this score, recorded rather than read.

    ``scorer_digest`` and ``recorded_at`` are inputs. The scorer never hashes its own source and
    never asks the clock, so replay from an archive needs no special path: the same three values
    are simply supplied again.
    """

    kind: Literal["scoring_invocation"] = "scoring_invocation"
    run_id: EntityId
    candidate_id: EntityId
    recorded_at: UtcTimestamp
    scorer_digest: Digest


class GateVerdict(ScoringModel):
    """The correctness gate as recorded by evaluation (Technical Spec 8.2, 14.2)."""

    kind: Literal["gate_verdict"] = "gate_verdict"
    status: Literal["pass", "fail", "unknown"]
    reasons: tuple[str, ...] = ()
    failing_conditions: tuple[Slug, ...] = ()
    incomplete_conditions: tuple[Slug, ...] = ()

    @model_validator(mode="after")
    def status_matches_conditions(self) -> GateVerdict:
        if self.status == "fail" and not self.failing_conditions:
            raise ValueError("a failed gate must name the conditions that failed")
        if self.status == "unknown" and not self.incomplete_conditions:
            raise ValueError("an unknown gate must name the conditions that stayed incomplete")
        if self.status == "pass" and (self.failing_conditions or self.incomplete_conditions):
            raise ValueError("a passing gate has no failing or incomplete condition")
        return self


class RequiredEvidenceRecord(ScoringModel):
    """One required analyzer/scan and whether its evidence actually arrived."""

    kind: Literal["required_evidence_record"] = "required_evidence_record"
    analyzer_id: Slug
    required: bool
    status: Literal["complete", "incomplete", "missing", "not_applicable"]
    detail: str = Field(default="", max_length=400)
    evidence_refs: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def complete_requires_evidence(self) -> RequiredEvidenceRecord:
        if self.status == "complete" and not self.evidence_refs:
            raise ValueError("a complete required scan must reference its report")
        return self


class SecurityIssueEvidence(ScoringModel):
    """One canonical issue: exactly one composite owner, however many tools reported it."""

    kind: Literal["security_issue_evidence"] = "security_issue_evidence"
    issue_key: str = Field(min_length=1, max_length=200)
    family: str = Field(min_length=1, max_length=200)
    penalty_kind: str = Field(min_length=1, max_length=120)
    severity: Literal["critical", "high", "medium", "low"]
    confidence: Confidence
    relation: Literal[
        "introduced",
        "worsened",
        "unchanged_in_scope",
        "unchanged_out_of_scope",
        "resolved",
        "unknown",
    ]
    declared_owner: ScoreDimension | None = None
    tools: tuple[Slug, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    adjudication: Literal["confirmed", "unreviewed", "rejected"] | None = None
    explanation: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def canonical_issue_is_traceable(self) -> SecurityIssueEvidence:
        if not self.evidence_refs:
            raise ValueError("an issue must reference the evidence it was derived from")
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("tools must be unique on one canonical issue")
        if self.relation in {"introduced", "worsened", "unchanged_in_scope"} and not self.tools:
            raise ValueError("a counted issue must name at least one reporting tool")
        return self


class RubricItemEvidence(ScoringModel):
    """One weighted rubric item, measured or explicitly not applicable or missing."""

    kind: Literal["rubric_item_evidence"] = "rubric_item_evidence"
    item_id: Slug
    dimension: ScoreDimension
    weight_bp: int = Field(ge=1, le=10_000)
    status: ItemStatus
    score_bp: int | None = Field(default=None, ge=0, le=10_000)
    opportunities: int = Field(default=0, ge=0)
    source: Literal[
        "tool",
        "judge_votes",
        "adjudication",
        "scenario",
        "measurement",
        "gate",
    ]
    hard_acceptance: bool = False
    evidence_refs: tuple[EvidenceRef, ...] = ()
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def only_measured_items_carry_a_value(self) -> RubricItemEvidence:
        if self.status == "measured" and self.score_bp is None:
            raise ValueError("a measured item must carry score_bp")
        if self.status != "measured" and self.score_bp is not None:
            raise ValueError("only a measured item may carry score_bp")
        if self.status == "measured" and not self.evidence_refs:
            raise ValueError("a measured item must reference its evidence")
        return self

    def normalized(self) -> Decimal:
        """Item value in [0,1]; only meaningful for a measured item."""
        assert self.score_bp is not None
        return Decimal(self.score_bp) / Decimal(10_000)


class EfficiencyMeasurement(ScoringModel):
    """The efficiency dimension value WP-13 measured, plus the ratios it came from.

    Technical Spec 13.4 owns the transform, so the dimension *value* is an evaluator output, not
    something scoring recomputes from nothing. The ratios travel with it anyway, and when they are
    present the scorer re-derives the value from the policy's own breakpoints and refuses a
    disagreement - so a wrong efficiency number cannot reach a composite.
    """

    kind: Literal["efficiency_measurement"] = "efficiency_measurement"
    status: Literal["complete", "censored", "invalid", "incomplete"]
    value: ScoreValue | None = None
    time_ratio: str | None = None
    memory_ratio: str | None = None
    time_censored: Literal["zero_by_lower_bound", "insufficient_information"] | None = None
    memory_censored: Literal["zero_by_lower_bound", "insufficient_information"] | None = None
    workloads: int = Field(default=0, ge=0)
    evidence_refs: tuple[EvidenceRef, ...] = ()
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def measurement_is_complete_or_says_why(self) -> EfficiencyMeasurement:
        if self.status in {"complete", "censored"}:
            if self.value is None:
                raise ValueError("a usable efficiency measurement must carry its dimension value")
            if not self.evidence_refs:
                raise ValueError("a usable efficiency measurement must reference its measurements")
        if self.status in {"invalid", "incomplete"} and not self.reasons:
            raise ValueError("an unusable efficiency measurement must say why")
        if bool(self.time_ratio) != bool(self.memory_ratio):
            raise ValueError("time and memory ratios arrive as a pair")
        return self


class BlockingReason(ScoringModel):
    """A concrete reason this evaluation cannot be ranked yet."""

    kind: Literal["blocking_reason"] = "blocking_reason"
    reason_class: BlockingClass
    reference: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=400)


class DiagnosticItemView(ScoringModel):
    """A language-profile diagnostic item, reported but deliberately outside the composite."""

    kind: Literal["diagnostic_item_view"] = "diagnostic_item_view"
    item_id: Slug
    language_id: Slug
    weight_bp: int = Field(ge=1, le=10_000)
    status: ItemStatus
    score_bp: int | None = Field(default=None, ge=0, le=10_000)
    composite_weight_bp: Literal[0] = 0
    composite_owner: ScoreDimension | None = None
    opportunities: int = Field(default=0, ge=0)
    reasons: tuple[str, ...] = ()


class ValidatedEvidenceManifest(ScoringModel):
    """Everything the scorer is allowed to read. No clock, no network, no second attempt."""

    kind: Literal["validated_evidence_manifest"] = "validated_evidence_manifest"
    evaluation_id: EntityId
    task_id: Slug
    task_version: int = Field(gt=0)
    task_digest: Digest
    language_id: Slug
    candidate_digest: Digest
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"]
    invocation: ScoringInvocation
    gate: GateVerdict
    required_evidence: tuple[RequiredEvidenceRecord, ...] = ()
    applicability: tuple[ScoreDimension, ...]
    security_issues: tuple[SecurityIssueEvidence, ...] = ()
    security_unreviewed_relevant: tuple[str, ...] = ()
    efficiency: EfficiencyMeasurement | None = None
    items: tuple[RubricItemEvidence, ...]
    blocking: tuple[BlockingReason, ...] = ()
    diagnostic_items: tuple[DiagnosticItemView, ...] = ()
    advisory_snapshot_digest: Digest | None = None
    advisory_scans_applied: bool = False
    evidence_refs: tuple[EvidenceRef, ...] = ()
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def manifest_is_internally_consistent(self) -> ValidatedEvidenceManifest:
        dimensions = list(self.applicability)
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("applicability must be unique")
        if ScoreDimension.CORRECTNESS not in dimensions:
            raise ValueError("every code evaluation is at least correctness")
        non_correctness = [d for d in dimensions if d is not ScoreDimension.CORRECTNESS]
        if not non_correctness:
            raise ValueError(
                "a correctness-only task is classified outside the six-dimension board and must "
                "declare no quality dimension"
            )
        if self.advisory_scans_applied and self.advisory_snapshot_digest is None:
            raise ValueError("an applied advisory scan must carry its frozen snapshot digest")
        keys = [(item.dimension, item.item_id) for item in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError("an item is reported once per dimension")
        analyzers = [record.analyzer_id for record in self.required_evidence]
        if len(set(analyzers)) != len(analyzers):
            raise ValueError("a required analyzer is recorded once")
        diagnostics = [item.item_id for item in self.diagnostic_items]
        if len(set(diagnostics)) != len(diagnostics):
            raise ValueError("a diagnostic item is reported once")
        return self

    def item(self, dimension: ScoreDimension, item_id: str) -> RubricItemEvidence:
        for entry in self.items:
            if entry.dimension is dimension and entry.item_id == item_id:
                return entry
        raise KeyError(f"{dimension.value}/{item_id}")

    def applicable_quality_dimensions(self) -> tuple[ScoreDimension, ...]:
        return tuple(
            dimension
            for dimension in self.applicability
            if dimension is not ScoreDimension.CORRECTNESS
        )

    def item_value(self, item: RubricItemEvidence) -> Decimal:
        return Decimal(100) * item.normalized()

    def content_digest(self) -> str:
        """Order-insensitive canonical digest.

        Every repeated collection in a manifest is a *set* - analyzers, items, issues, diagnostic
        views, blocking reasons, evidence references. Two producers that emit the same evidence in a
        different order must produce the same digest, so the canonical payload sorts them. Nothing
        is discarded: a duplicate report is still a distinct entry and still changes this digest,
        which is exactly why a duplicate can never silently pass for the original.
        """
        payload = self.model_dump(mode="json", exclude={"kind", "schema_version"})
        for field_name in _UNORDERED_FIELDS:
            value = payload.get(field_name)
            if isinstance(value, list):
                payload[field_name] = sorted(value, key=lambda entry: _canonical_key(entry))
        payload["applicability"] = sorted(payload["applicability"])
        payload["security_unreviewed_relevant"] = sorted(payload["security_unreviewed_relevant"])
        return canonical_digest(
            {"kind": self.kind, "schema_version": self.schema_version, "payload": payload}
        )


#: Manifest collections whose order carries no meaning.
_UNORDERED_FIELDS = (
    "required_evidence",
    "security_issues",
    "items",
    "blocking",
    "diagnostic_items",
    "evidence_refs",
)


def _canonical_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
