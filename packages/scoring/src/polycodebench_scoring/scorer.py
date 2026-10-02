"""The pure scorer (Technical Spec 14).

``score_evaluation`` is a function of three frozen inputs and nothing else. It opens no file, reads
no database, calls no provider, executes no task and reads no clock: it cannot, because it imports
none of those. The only impure part of scoring lives at the edge, in
:mod:`polycodebench_scoring.loader` and :mod:`polycodebench_scoring.cli`.

The order of operations is the specification's order and it is load-bearing:

1. identity and frozen-applicability agreement;
2. required-evidence completeness;
3. canonical issue ownership;
4. correctness gating;
5. dimension values;
6. redistribution, contributions and the composite.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import ClassVar, Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import (
    EvaluationState,
    Gate,
    Scorecard,
    ScoreDimension,
    ScoreItem,
)
from polycodebench_plugins_api import FrozenTask, LanguageProfile

from polycodebench_scoring.arithmetic import (
    HUNDRED,
    ScoringRefusedInput,
    decimal_from_fraction,
    dimension_share,
    effective_weights,
    efficiency_value,
    integer_weights,
    quantize_score,
    split_exact,
    split_integer,
)
from polycodebench_scoring.contracts import ScoringModel
from polycodebench_scoring.errors import ScoringRefusalCode, ScoringRefused
from polycodebench_scoring.explain import (
    DimensionExplanation,
    IssueExplanation,
    ItemExplanation,
    ScorecardExplanation,
)
from polycodebench_scoring.manifest import (
    BlockingReason,
    RubricItemEvidence,
    ValidatedEvidenceManifest,
)
from polycodebench_scoring.ownership import EvidenceOwnership, OwnershipLedger, resolve_owners
from polycodebench_scoring.policy import (
    COMPOSITE_DIMENSIONS,
    DIMENSION_ORDER,
    FrozenScoringPolicy,
)

RUBRIC_DIMENSIONS = (
    ScoreDimension.CODE_QUALITY,
    ScoreDimension.IDIOMATIC,
    ScoreDimension.ROBUSTNESS,
)
SCALAR_DIMENSIONS = (
    ScoreDimension.CORRECTNESS,
    ScoreDimension.SECURITY,
    ScoreDimension.EFFICIENCY,
)
SCALAR_ITEM_IDS = {
    ScoreDimension.CORRECTNESS: "gate",
    ScoreDimension.SECURITY: "confirmed_issue_penalties",
    ScoreDimension.EFFICIENCY: "runtime_and_memory",
}
_INFRA_CLASSES = frozenset({"infrastructure"})


class ScoringOutcome(ScoringModel):
    """A canonical scorecard plus the explanation that reproduces it."""

    kind: str = "scoring_outcome"
    score_schema_version: int
    scorecard: Scorecard
    explanation: ScorecardExplanation
    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset(
        {"scorecard_digest", "scorecard_content_digest", "outcome_digest"}
    )


@dataclass(frozen=True)
class _Dimension:
    """One dimension's resolved value and the items that produced it."""

    dimension: ScoreDimension
    applicable: bool
    raw_value: Decimal | None
    items: tuple[RubricItemEvidence, ...]
    reasons: tuple[str, ...]
    gating_reason: str | None = None


def score_evaluation(
    task: FrozenTask,
    policy: FrozenScoringPolicy,
    evidence: ValidatedEvidenceManifest,
    *,
    ownership: EvidenceOwnership,
    profile: LanguageProfile | None = None,
) -> ScoringOutcome:
    """Score one frozen evaluation. Pure: no I/O, no clock, no model, no task execution.

    ``ownership`` and ``profile`` are keyword-only because they are frozen configuration that
    accompanies the policy rather than evidence; the three positional inputs are exactly the ones
    the specification's entry point names.
    """
    if policy.evidence_ownership_policy_id != ownership.policy_id:
        raise ScoringRefused(
            ScoringRefusalCode.POLICY_INVALID,
            f"policy {policy.policy_id!r} expects ownership policy "
            f"{policy.evidence_ownership_policy_id!r}, not {ownership.policy_id!r}",
        )
    _check_identity(task, evidence)
    applicable_quality = _check_applicability(task, evidence)
    board = "six_dimension" if applicable_quality else "correctness_only"

    blocking: list[BlockingReason] = list(_required_evidence_blocks(task, policy, evidence))
    ledger = resolve_owners(
        evidence.security_issues,
        ownership,
        task_overrides=_task_overrides(task),
        penalty_for_severity=policy.security.penalty,
    )
    blocking.extend(_issue_blocks(ledger, evidence))

    gate_failed = evidence.gate.status == "fail"
    dimensions = _resolve_dimensions(
        policy, profile, evidence, ledger, applicable_quality, gate_failed
    )
    blocking.extend(_item_blocks(dimensions))

    # A failed correctness gate is a complete, publishable answer of zero - not missing evidence.
    # An unknown gate is the opposite: nothing can be said about this attempt.
    if evidence.gate.status == "unknown":
        blocking.append(
            BlockingReason(
                reason_class="infrastructure",
                reference="gate.unknown",
                detail="; ".join(evidence.gate.incomplete_conditions)
                or "execution or grading did not complete",
            )
        )

    exact = effective_weights(policy.code_composite.weights, applicable_quality)
    integers = integer_weights(exact)

    score_items: list[ScoreItem] = []
    item_rows: list[ItemExplanation] = []
    dimension_rows: list[DimensionExplanation] = []
    total = Decimal(0)
    total_terms: list[str] = []

    for dimension in COMPOSITE_DIMENSIONS:
        resolved = dimensions[dimension]
        weight = exact.get(dimension, Fraction(0))
        counted = (
            resolved.applicable
            and resolved.gating_reason is None
            and weight > 0
            and resolved.raw_value is not None
        )
        value = resolved.raw_value if counted else None
        contribution = (
            dimension_share(weight, value or Decimal(0)) if value is not None else Decimal(0)
        )
        if value is not None:
            total += contribution
            total_terms.append(f"{_weight_text(weight)}bp*{quantize_score(value)}/10000")
        dimension_rows.append(
            DimensionExplanation(
                dimension=dimension,
                applicable=resolved.applicable,
                nominal_weight_bp=policy.nominal_weight_bp(dimension),
                effective_weight_bp=_weight_text(weight),
                effective_weight_bps=integers.get(dimension, 0),
                raw_value=quantize_score(value) if value is not None else None,
                contribution=quantize_score(contribution),
                formula=_dimension_formula(resolved, weight),
                item_count=len(resolved.items),
                reasons=resolved.reasons,
            )
        )
        rows = (
            _scalar_rows(policy, resolved, weight, integers.get(dimension, 0), contribution)
            if dimension in SCALAR_DIMENSIONS
            else _rubric_rows(policy, resolved, weight, integers.get(dimension, 0))
        )
        for explanation_row, score_item in rows:
            item_rows.append(explanation_row)
            score_items.append(score_item)

    blocking.sort(key=lambda reason: (reason.reason_class, reason.reference))
    status = _status_for(blocking)
    publishable = not blocking
    total_value = quantize_score(total) if publishable else None
    if gate_failed:
        total = Decimal(0)
        total_value = "0.000000" if publishable else None

    scorecard = Scorecard(
        kind="scorecard",
        schema_version=1,
        scorecard_id=_scorecard_id(evidence, policy),
        task_id=evidence.task_id,
        task_version=evidence.task_version,
        run_id=evidence.invocation.run_id,
        candidate_id=evidence.invocation.candidate_id,
        evidence_manifest_digest=evidence.content_digest(),
        scoring_policy_digest=policy.content_digest(),
        scorer_digest=evidence.invocation.scorer_digest,
        gate=Gate.UNKNOWN if evidence.gate.status == "unknown" else Gate(evidence.gate.status),
        status=status,
        total_score=total_value,
        items=score_items,
        created_at=evidence.invocation.recorded_at,
    )

    explanation = ScorecardExplanation(
        score_schema_version=policy.score_schema_version,
        board=board,  # type: ignore[arg-type]
        policy_id=policy.policy_id,
        policy_digest=policy.content_digest(),
        ownership_policy_id=ownership.policy_id,
        ownership_policy_digest=ownership.content_digest(),
        language_profile_version=profile.profile_version if profile else None,
        evidence_manifest_digest=evidence.content_digest(),
        scorer_digest=evidence.invocation.scorer_digest,
        status=status,
        gate=scorecard.gate,
        total_score=total_value,
        total_exact=None if total_value is None else format(total, "f"),
        total_formula=_total_formula(board, total_terms, total, publishable, blocking),
        blocking=tuple(blocking),
        dimensions=tuple(dimension_rows),
        items=tuple(item_rows),
        issues=tuple(_issue_rows(ledger)),
        diagnostic_items=evidence.diagnostic_items,
        collapsed_duplicate_issues=ledger.collapsed_duplicates,
        ignored_issues=ledger.ignored_reports,
    )
    return ScoringOutcome(
        score_schema_version=policy.score_schema_version,
        scorecard=scorecard,
        explanation=explanation,
    )


# --------------------------------------------------------------------------------------- checks


def _check_identity(task: FrozenTask, evidence: ValidatedEvidenceManifest) -> None:
    mismatches = [
        name
        for name, expected, actual in (
            ("task_id", task.task_id, evidence.task_id),
            ("task_version", task.task_version, evidence.task_version),
            ("task_digest", task.task_digest, evidence.task_digest),
            ("language_id", task.primary_language, evidence.language_id),
        )
        if expected != actual
    ]
    if mismatches:
        raise ScoringRefused(
            ScoringRefusalCode.IDENTITY_MISMATCH,
            f"evidence was produced for a different task in {mismatches}",
        )


def _check_applicability(
    task: FrozenTask, evidence: ValidatedEvidenceManifest
) -> tuple[ScoreDimension, ...]:
    declared = {ScoreDimension.CORRECTNESS, *task.applicable_dimensions}
    supplied = set(evidence.applicability)
    if declared != supplied:
        raise ScoringRefused(
            ScoringRefusalCode.APPLICABILITY_DISAGREEMENT,
            f"frozen task applicability {sorted(d.value for d in declared)} does not match the "
            f"manifest {sorted(d.value for d in supplied)}; applicability never changes per "
            "candidate",
        )
    return tuple(
        dimension
        for dimension in COMPOSITE_DIMENSIONS
        if dimension in supplied and dimension is not ScoreDimension.CORRECTNESS
    )


def _task_overrides(task: FrozenTask) -> dict[str, ScoreDimension]:
    """Per-task ownership overrides, which exist only because they were frozen before the run."""
    overrides: dict[str, ScoreDimension] = {}
    declared = task.quality.get("evidence_owners") if isinstance(task.quality, dict) else None
    if isinstance(declared, dict):
        for family, owner in declared.items():
            if isinstance(family, str) and isinstance(owner, str):
                overrides[family] = ScoreDimension(owner)
    return overrides


def _required_evidence_blocks(
    task: FrozenTask,
    policy: FrozenScoringPolicy,
    evidence: ValidatedEvidenceManifest,
) -> tuple[BlockingReason, ...]:
    blocks: list[BlockingReason] = list(evidence.blocking)
    records = {record.analyzer_id: record for record in evidence.required_evidence}
    for analyzer_id in task.required_analyzers:
        record = records.get(analyzer_id)
        if record is None:
            blocks.append(
                BlockingReason(
                    reason_class="required_evidence",
                    reference=f"analyzer.{analyzer_id}",
                    detail="no evidence record was produced for a required analyzer",
                )
            )
        elif record.status != "complete":
            blocks.append(
                BlockingReason(
                    reason_class="required_evidence",
                    reference=f"analyzer.{analyzer_id}",
                    detail=record.detail or f"required analyzer reported {record.status}",
                )
            )
    if (
        ScoreDimension.SECURITY in evidence.applicability
        and policy.security.advisory_snapshot_required
        and evidence.advisory_scans_applied
        and evidence.advisory_snapshot_digest is None
    ):
        blocks.append(
            BlockingReason(
                reason_class="required_evidence",
                reference="security.advisory_snapshot",
                detail="an advisory scan was applied without a frozen snapshot digest",
            )
        )
    return tuple(blocks)


def _issue_blocks(
    ledger: OwnershipLedger, evidence: ValidatedEvidenceManifest
) -> tuple[BlockingReason, ...]:
    blocks = [
        BlockingReason(
            reason_class="adjudication",
            reference=f"issue.{issue.issue_key}",
            detail=issue.reason,
        )
        for issue in ledger.blocking()
    ]
    blocks.extend(
        BlockingReason(
            reason_class="adjudication",
            reference=f"issue.{issue_key}",
            detail="an issue was reported as awaiting adjudication and no outcome was recorded",
        )
        for issue_key in evidence.security_unreviewed_relevant
    )
    return tuple(blocks)


def _item_blocks(dimensions: dict[ScoreDimension, _Dimension]) -> tuple[BlockingReason, ...]:
    blocks: list[BlockingReason] = []
    for dimension in RUBRIC_DIMENSIONS:
        for item in dimensions[dimension].items:
            if item.status in {"missing", "needs_review"}:
                blocks.append(
                    BlockingReason(
                        reason_class="judge_votes"
                        if item.source in {"judge_votes", "adjudication"}
                        else "required_evidence",
                        reference=f"{dimension.value}.{item.item_id}",
                        detail="; ".join(item.reasons) or f"item status is {item.status}",
                    )
                )
    return tuple(blocks)


def _status_for(blocking: list[BlockingReason]) -> EvaluationState:
    if not blocking:
        return EvaluationState.READY
    if all(reason.reason_class in _INFRA_CLASSES for reason in blocking):
        return EvaluationState.INFRA_BLOCKED
    return EvaluationState.NEEDS_REVIEW


# --------------------------------------------------------------------------- dimension values


def _resolve_dimensions(
    policy: FrozenScoringPolicy,
    profile: LanguageProfile | None,
    evidence: ValidatedEvidenceManifest,
    ledger: OwnershipLedger,
    applicable_quality: tuple[ScoreDimension, ...],
    gate_failed: bool,
) -> dict[ScoreDimension, _Dimension]:
    resolved: dict[ScoreDimension, _Dimension] = {}

    resolved[ScoreDimension.CORRECTNESS] = _Dimension(
        ScoreDimension.CORRECTNESS,
        True,
        Decimal(100),
        (),
        tuple(evidence.gate.reasons),
        "gated_by_correctness" if gate_failed else None,
    )

    if ScoreDimension.SECURITY in applicable_quality:
        penalties = ledger.counted_penalty(ScoreDimension.SECURITY)
        awaiting = tuple(
            issue.issue_key for issue in ledger.blocking() if issue.owner is ScoreDimension.SECURITY
        )
        resolved[ScoreDimension.SECURITY] = _Dimension(
            ScoreDimension.SECURITY,
            True,
            None if awaiting else Decimal(max(0, 100 - penalties)),
            (),
            (
                (
                    f"{len(awaiting)} security claims await adjudication: {', '.join(awaiting)}"
                    if awaiting
                    else f"deducted {penalties} basis points of confirmed owned penalties"
                ),
            ),
            "gated_by_correctness" if gate_failed else None,
        )
    else:
        resolved[ScoreDimension.SECURITY] = _Dimension(
            ScoreDimension.SECURITY,
            False,
            None,
            (),
            ("predeclared not_applicable; its nominal weight is redistributed",),
        )

    if ScoreDimension.EFFICIENCY in applicable_quality:
        resolved[ScoreDimension.EFFICIENCY] = _efficiency_dimension(policy, evidence, gate_failed)
    else:
        resolved[ScoreDimension.EFFICIENCY] = _Dimension(
            ScoreDimension.EFFICIENCY,
            False,
            None,
            (),
            ("predeclared not_applicable; its nominal weight is redistributed",),
        )

    for dimension in RUBRIC_DIMENSIONS:
        items = tuple(item for item in evidence.items if item.dimension is dimension)
        if dimension not in applicable_quality:
            if items:
                raise ScoringRefused(
                    ScoringRefusalCode.APPLICABILITY_DISAGREEMENT,
                    f"{dimension.value} is predeclared not_applicable but {len(items)} items were "
                    "supplied for it",
                )
            resolved[dimension] = _Dimension(
                dimension,
                False,
                None,
                (),
                ("predeclared not_applicable; its nominal weight is redistributed",),
            )
            continue
        if not items:
            raise ScoringRefused(
                ScoringRefusalCode.APPLICABILITY_DISAGREEMENT,
                f"{dimension.value} is applicable but no item evidence was supplied",
            )
        _check_item_contract(policy, profile, dimension, items)
        value = _weighted_dimension_value(items)
        if value is None:
            unresolved = [
                item.item_id for item in items if item.status in {"missing", "needs_review"}
            ]
            if not unresolved:
                raise ScoringRefused(
                    ScoringRefusalCode.APPLICABILITY_DISAGREEMENT,
                    f"{dimension.value} has no applicable item; the frozen task plan and the "
                    "manifest disagree about applicability",
                )
            # The dimension applies and its items exist; only their evidence has not arrived.
            # That blocks completion and is reported as such - it is never read as a zero.
            resolved[dimension] = _Dimension(
                dimension,
                True,
                None,
                items,
                (f"unresolved items: {', '.join(sorted(unresolved))}",),
                "gated_by_correctness" if gate_failed else None,
            )
            continue
        resolved[dimension] = _Dimension(
            dimension,
            True,
            value,
            items,
            (),
            "gated_by_correctness" if gate_failed else None,
        )
    return resolved


def _efficiency_dimension(
    policy: FrozenScoringPolicy,
    evidence: ValidatedEvidenceManifest,
    gate_failed: bool,
) -> _Dimension:
    measurement = evidence.efficiency
    if measurement is None:
        raise ScoringRefused(
            ScoringRefusalCode.EFFICIENCY_INPUT_INVALID,
            "efficiency is applicable but the manifest carries no measurement",
        )
    if measurement.status in {"invalid", "incomplete"} or measurement.value is None:
        raise ScoringRefused(
            ScoringRefusalCode.EFFICIENCY_INPUT_INVALID,
            "; ".join(measurement.reasons) or f"performance lane is {measurement.status}",
        )
    reasons = measurement.reasons
    if measurement.time_ratio is not None and measurement.memory_ratio is not None:
        try:
            derived = efficiency_value(measurement, policy.efficiency)
        except ScoringRefusedInput as error:
            raise ScoringRefused(ScoringRefusalCode.EFFICIENCY_INPUT_INVALID, str(error)) from error
        if str(derived) != measurement.value:
            raise ScoringRefused(
                ScoringRefusalCode.EFFICIENCY_SCORE_MISMATCH,
                f"the measurement reports {measurement.value} but the policy transform of its own "
                f"ratios (time {measurement.time_ratio}, memory {measurement.memory_ratio}) is "
                f"{derived}",
            )
        reasons = reasons + (
            f"ratios re-derived {derived} from the policy breakpoints "
            f"(time b={policy.efficiency.timing_breakpoint}, "
            f"memory b={policy.efficiency.memory_breakpoint})",
        )
    return _Dimension(
        ScoreDimension.EFFICIENCY,
        True,
        Decimal(measurement.value),
        (),
        reasons,
        "gated_by_correctness" if gate_failed else None,
    )


def _check_item_contract(
    policy: FrozenScoringPolicy,
    profile: LanguageProfile | None,
    dimension: ScoreDimension,
    items: tuple[RubricItemEvidence, ...],
) -> None:
    """The scorer owns the weight plan: a manifest must match it, never redefine it."""
    if any(item.hard_acceptance for item in items):
        raise ScoringRefused(
            ScoringRefusalCode.REVIEW_ROUTES_TO_DIMENSION,
            f"{dimension.value} may only score optional quality items; a required acceptance "
            "condition belongs to correctness and must not be deducted twice",
        )
    expected = _expected_item_weights(policy, profile, dimension, items)
    declared = {item.item_id: item.weight_bp for item in items}
    if declared != expected:
        differing = sorted(
            f"{item_id} (declared {declared.get(item_id)}, expected {expected.get(item_id)})"
            for item_id in set(declared) | set(expected)
            if declared.get(item_id) != expected.get(item_id)
        )
        raise ScoringRefused(
            ScoringRefusalCode.WEIGHT_MISMATCH,
            f"{dimension.value} item weights do not match the frozen plan: {differing}",
        )


def _expected_item_weights(
    policy: FrozenScoringPolicy,
    profile: LanguageProfile | None,
    dimension: ScoreDimension,
    items: tuple[RubricItemEvidence, ...],
) -> dict[str, int]:
    if dimension is ScoreDimension.CODE_QUALITY:
        return dict(policy.code_quality.items)
    if dimension is ScoreDimension.IDIOMATIC:
        if profile is None:
            raise ScoringRefused(
                ScoringRefusalCode.POLICY_INVALID,
                "idiomatic strength is applicable but no frozen language profile was supplied",
            )
        block = policy.idiomatic.language_rubric_weight_bp
        scaled = _scale_weights(
            {item.item_id: item.weight_bp for item in profile.idiom_items}, block
        )
        return scaled | dict(policy.idiomatic.residual_judge_items)
    scenario_block = policy.robustness.scenario_weight_bp
    residual = dict(policy.robustness.residual_judge_items)
    scenarios = {item.item_id: item.weight_bp for item in items if item.item_id not in residual}
    if sum(scenarios.values()) != scenario_block:
        raise ScoringRefused(
            ScoringRefusalCode.WEIGHT_MISMATCH,
            f"the frozen quality-only scenarios carry {sum(scenarios.values())} basis "
            f"points rather than the policy's {scenario_block}",
        )
    return scenarios | residual


def _scale_weights(source: dict[str, int], target_bp: int) -> dict[str, int]:
    """Renormalise a profile's weights into ``target_bp``.

    The relative weights of the language profile survive exactly; only the block total changes, and
    it is the scoring policy that fixes that total. The largest-remainder split keeps the integer
    result deterministic and exact.
    """
    if not source:
        raise ScoringRefused(
            ScoringRefusalCode.POLICY_INVALID, "a language profile declared no items to scale"
        )
    return split_integer(target_bp, source)


def _weighted_dimension_value(items: tuple[RubricItemEvidence, ...]) -> Decimal | None:
    applicable = [item for item in items if item.status == "measured"]
    total_weight = sum(item.weight_bp for item in applicable)
    if total_weight == 0:
        return None
    numerator = sum(
        (Decimal(item.weight_bp) * item.normalized() for item in applicable), Decimal(0)
    )
    return HUNDRED * numerator / Decimal(total_weight)


# ----------------------------------------------------------------------------------- item rows


def _rubric_rows(
    policy: FrozenScoringPolicy,
    resolved: _Dimension,
    weight: Fraction,
    integer_weight: int,
) -> list[tuple[ItemExplanation, ScoreItem]]:
    dimension = resolved.dimension
    shares = {
        item.item_id: (item.weight_bp if item.status == "measured" else 0)
        for item in resolved.items
    }
    exact = split_exact(weight, shares)
    integers = split_integer(integer_weight, shares)
    rows: list[tuple[ItemExplanation, ScoreItem]] = []
    for item in sorted(resolved.items, key=lambda entry: entry.item_id):
        measured = item.status == "measured" and resolved.gating_reason is None
        value = HUNDRED * item.normalized() if item.status == "measured" else None
        leaf = exact[item.item_id]
        contribution = dimension_share(leaf, value) if measured and value else Decimal(0)
        rendered = quantize_score(value) if value is not None else None
        rows.append(
            (
                ItemExplanation(
                    dimension=dimension,
                    item_id=item.item_id,
                    group=_group_for(item),
                    primary_owner=dimension,
                    applicable=item.status == "measured",
                    status=_item_status(resolved, item),
                    raw_value=rendered,
                    nominal_weight_bp=item.weight_bp,
                    effective_weight_bp=_weight_text(leaf),
                    effective_weight_bps=integers[item.item_id],
                    contribution=quantize_score(contribution),
                    formula=(
                        f"{integers[item.item_id]}bp * {rendered} / 10000"
                        if measured and rendered
                        else "0 (gated)"
                        if resolved.gating_reason
                        else "0 (not applicable)"
                    ),
                    evidence_refs=item.evidence_refs,
                    policy_digest=policy.content_digest(),
                    reasons=item.reasons,
                ),
                ScoreItem(
                    kind="score_item",
                    schema_version=1,
                    dimension=dimension,
                    item_id=item.item_id,
                    applicable=item.status == "measured",
                    primary_owner=dimension,
                    raw_value=quantize_score(value) if value is not None else None,
                    effective_weight_bps=integers[item.item_id],
                    gating_reason=resolved.gating_reason,
                    contribution=quantize_score(contribution),
                    evidence_ids=[],
                ),
            )
        )
    return rows


def _scalar_rows(
    policy: FrozenScoringPolicy,
    resolved: _Dimension,
    weight: Fraction,
    integer_weight: int,
    contribution: Decimal,
) -> list[tuple[ItemExplanation, ScoreItem]]:
    dimension = resolved.dimension
    measured = (
        resolved.applicable and resolved.gating_reason is None and resolved.raw_value is not None
    )
    value = resolved.raw_value if measured else None
    rendered = quantize_score(value) if value is not None else None
    reasons = resolved.reasons + (("gated_by_correctness",) if resolved.gating_reason else ())
    row = ItemExplanation(
        dimension=dimension,
        item_id=SCALAR_ITEM_IDS[dimension],
        group="dimension",
        primary_owner=dimension,
        applicable=resolved.applicable,
        status=_scalar_status(resolved),
        raw_value=rendered,
        nominal_weight_bp=policy.nominal_weight_bp(dimension),
        effective_weight_bp=_weight_text(weight),
        effective_weight_bps=integer_weight,
        contribution=quantize_score(contribution),
        formula=(
            f"{integer_weight}bp * {rendered} / 10000"
            if measured and rendered
            else "0 (gated by correctness)"
            if resolved.gating_reason
            else "0 (unresolved)"
            if resolved.applicable
            else "0 (not_applicable)"
        ),
        evidence_refs=(),
        policy_digest=policy.content_digest(),
        reasons=reasons,
    )
    return [
        (
            row,
            ScoreItem(
                kind="score_item",
                schema_version=1,
                dimension=dimension,
                item_id=SCALAR_ITEM_IDS[dimension],
                applicable=resolved.applicable,
                primary_owner=dimension,
                raw_value=rendered,
                effective_weight_bps=integer_weight if resolved.applicable else 0,
                gating_reason=resolved.gating_reason,
                contribution=quantize_score(contribution),
                evidence_ids=[],
            ),
        )
    ]


def _group_for(
    item: RubricItemEvidence,
) -> Literal["dimension", "rubric_group", "judge_item", "scenario"]:
    if item.source == "scenario":
        return "scenario"
    if item.source in {"judge_votes", "adjudication"}:
        return "judge_item"
    return "rubric_group"


def _item_status(
    resolved: _Dimension, item: RubricItemEvidence
) -> Literal["measured", "not_applicable", "gated", "missing", "needs_review"]:
    if resolved.gating_reason:
        return "gated"
    return item.status


def _scalar_status(
    resolved: _Dimension,
) -> Literal["measured", "not_applicable", "gated", "missing", "needs_review"]:
    if resolved.gating_reason:
        return "gated"
    if not resolved.applicable:
        return "not_applicable"
    if resolved.raw_value is None:
        return "needs_review"
    return "measured"


def _issue_rows(ledger: OwnershipLedger) -> tuple[IssueExplanation, ...]:
    return tuple(
        IssueExplanation(
            issue_key=issue.issue_key,
            family=issue.family,
            penalty_kind=issue.penalty_kind,
            owner=issue.owner,
            severity=issue.severity,
            confidence=issue.confidence,
            relation=issue.relation,
            penalty_bp=issue.penalty_bp,
            counted=issue.counted,
            blocks_completion=issue.blocks_completion,
            collapsed_reports=issue.collapsed_reports,
            tools=issue.tools,
            reason=issue.reason,
            evidence_refs=issue.evidence_refs,
        )
        for issue in ledger.issues
    )


# ------------------------------------------------------------------------------- presentation


def score_identity(outcome: ScoringOutcome) -> str:
    """The identity of the *score*, independent of which evidence produced it.

    The scorecard digest deliberately covers the evidence manifest, so a second report of one issue
    changes the scorecard's identity while changing nothing about the score. This digest is the
    narrower claim: two evaluations that awarded the same numbers have the same score identity.
    """
    explanation = outcome.explanation
    return canonical_digest(
        {
            "gate": str(explanation.gate),
            "status": str(explanation.status),
            "total_score": explanation.total_score,
            "dimensions": [
                [
                    row.dimension.value,
                    row.applicable,
                    row.raw_value,
                    row.contribution,
                    row.effective_weight_bps,
                ]
                for row in explanation.dimensions
            ],
            "items": [
                [row.dimension.value, row.item_id, row.status, row.raw_value, row.contribution]
                for row in explanation.items
            ],
        }
    )


def _weight_text(value: Fraction) -> str:
    return format(decimal_from_fraction(value), "f")


def _dimension_formula(resolved: _Dimension, weight: Fraction) -> str:
    if not resolved.applicable:
        return "not_applicable: nominal weight redistributed over the applicable dimensions"
    if resolved.gating_reason:
        return f"{_weight_text(weight)}bp * 0 / 10000 (gated_by_correctness)"
    if resolved.raw_value is None:
        return f"{_weight_text(weight)}bp * unresolved: no dimension value may be published"
    measured = [item for item in resolved.items if item.status == "measured"]
    if not measured:
        return f"{_weight_text(weight)}bp * {quantize_score(resolved.raw_value)} / 10000"
    numerator = sum((Decimal(item.weight_bp) * item.normalized() for item in measured), Decimal(0))
    denominator = sum(item.weight_bp for item in measured)
    return f"{_weight_text(weight)}bp * 100 * ({numerator} / {denominator}) / 10000"


def _total_formula(
    board: str,
    terms: list[str],
    total: Decimal,
    publishable: bool,
    blocking: list[BlockingReason],
) -> str:
    if board == "correctness_only":
        return "correctness-only board: 10000bp * 100 / 10000"
    if not publishable:
        first = ", ".join(reason.reference for reason in blocking[:4])
        return f"no composite published; outstanding: {first}"
    return " + ".join(terms) + f" = {quantize_score(total)}"


def _scorecard_id(evidence: ValidatedEvidenceManifest, policy: FrozenScoringPolicy) -> str:
    """A deterministic UUIDv4-shaped identity derived from the score's own content.

    ``scorecard_id`` is a non-semantic field excluded from the canonical digest, so deriving it
    from content rather than from a sequence lets a replay reproduce the archived bytes exactly -
    with no clock, no random source and no database.
    """
    seed = f"{policy.content_digest()}|{evidence.content_digest()}|{evidence.invocation.run_id}"
    raw = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    # Canonical UUIDv4 shape: version nibble 4, variant nibble in {8,9,a,b}, lowercase hex.
    variant = "89ab"[int(raw[16], 16) % 4]
    return f"{raw[0:8]}-{raw[8:12]}-4{raw[13:16]}-{variant}{raw[17:20]}-{raw[20:32]}"


__all__ = [
    "DIMENSION_ORDER",
    "SCALAR_ITEM_IDS",
    "ScoringOutcome",
    "score_evaluation",
    "score_identity",
]
