"""Judge outcomes as scoring evidence: the one adapter between the judge stack and the scorer.

Prompt 25, PCB-25-2. ``packages/services/judging.py`` produces a ``JudgementResult`` of
``ItemOutcome`` rows; ``packages/scoring/manifest.py`` consumes ``RubricItemEvidence`` rows. This
module is the only place allowed to convert between them, so the mapping is reviewable in one
file and no caller can hand-score a judge item.

Frozen mapping rules:

* ``ready`` becomes a measured item whose score is the vote mean, converted to basis points with
  the scoring policy's half-even rounding. An adjudicated outcome records ``source`` accordingly;
  the original votes remain visible in the judgement result this evidence points at.
* ``needs_review`` is never a silent score: the item is reported as ``needs_review`` with its
  triggers and the scorer blocks completion until a reviewer resolves it.
* ``infra_blocked`` is ``missing`` evidence. It is never read as a zero.

A judge outcome can only ever feed a weighted quality item. A required acceptance condition is
never satisfied by a judgment: see ``polycodebench_evaluation.repo_task_grading``, where the
acceptance gate is computed from mandatory criteria before any judge result is read.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal

from polycodebench_core.judge_contracts import ItemOutcome, JudgementResult
from polycodebench_core.models import ScoreDimension

from polycodebench_scoring.manifest import EvidenceRef, RubricItemEvidence

_BASIS_POINTS = Decimal(10_000)


class JudgeEvidenceMismatch(ValueError):
    """The judgement and the frozen weight plan disagree about which items exist."""


def outcome_score_bp(outcome: ItemOutcome) -> int | None:
    """Vote mean in basis points, half-even, or ``None`` when no mean exists."""
    if outcome.mean_score is None:
        return None
    return int((Decimal(outcome.mean_score) * _BASIS_POINTS).to_integral_value(ROUND_HALF_EVEN))


def rubric_item_from_outcome(
    outcome: ItemOutcome,
    *,
    weight_bp: int,
    evidence_ref: EvidenceRef,
    opportunities: int | None = None,
) -> RubricItemEvidence:
    """One judge item outcome as one scoring evidence row."""
    if not 1 <= weight_bp <= 10_000:
        raise JudgeEvidenceMismatch(
            f"{outcome.item_id}: frozen weight {weight_bp} is outside 1..10000 basis points"
        )
    if outcome.status == "ready":
        score_bp = outcome_score_bp(outcome)
        assert score_bp is not None  # ready means a complete vote set produced a mean
        return RubricItemEvidence(
            item_id=outcome.item_id,
            dimension=outcome.dimension,
            weight_bp=weight_bp,
            status="measured",
            score_bp=score_bp,
            opportunities=outcome.vote_count if opportunities is None else opportunities,
            source=outcome.source,
            evidence_refs=(evidence_ref,),
            reasons=(f"judge mean {outcome.mean_score} over {outcome.vote_count} votes",),
        )
    if outcome.status == "needs_review":
        return RubricItemEvidence(
            item_id=outcome.item_id,
            dimension=outcome.dimension,
            weight_bp=weight_bp,
            status="needs_review",
            score_bp=None,
            opportunities=outcome.vote_count if opportunities is None else opportunities,
            source=outcome.source,
            evidence_refs=(evidence_ref,),
            reasons=tuple(sorted(trigger.value for trigger in outcome.triggers))
            or (outcome.reason or "judge item needs review",),
        )
    return RubricItemEvidence(
        item_id=outcome.item_id,
        dimension=outcome.dimension,
        weight_bp=weight_bp,
        status="missing",
        score_bp=None,
        opportunities=outcome.vote_count if opportunities is None else opportunities,
        source=outcome.source,
        evidence_refs=(evidence_ref,),
        reasons=(outcome.reason or "judge item evidence never arrived",),
    )


def judgement_items(
    judgement: JudgementResult,
    *,
    weights: Mapping[str, tuple[ScoreDimension, int]],
    evidence_ref: EvidenceRef,
) -> tuple[RubricItemEvidence, ...]:
    """Every frozen judge item of one task, in the weight plan's order.

    ``weights`` is the frozen plan (item id -> dimension and basis points). A judgement that
    misses an item still produces a row - marked ``missing`` - so the scorer sees the gap instead
    of silently scoring a subset. A judgement that reports an item outside the plan is refused.
    """
    outcomes = {outcome.item_id: outcome for outcome in judgement.items}
    unknown = sorted(set(outcomes) - set(weights))
    if unknown:
        raise JudgeEvidenceMismatch(f"judgement reports items outside the frozen plan: {unknown}")
    rows: list[RubricItemEvidence] = []
    for item_id, (dimension, weight_bp) in sorted(weights.items()):
        outcome = outcomes.get(item_id)
        if outcome is None:
            rows.append(
                RubricItemEvidence(
                    item_id=item_id,
                    dimension=dimension,
                    weight_bp=weight_bp,
                    status="missing",
                    score_bp=None,
                    source="judge_votes",
                    evidence_refs=(evidence_ref,),
                    reasons=("judgement returned no outcome for this frozen item",),
                )
            )
            continue
        rows.append(
            rubric_item_from_outcome(outcome, weight_bp=weight_bp, evidence_ref=evidence_ref)
        )
    return tuple(rows)


def judged_means(judgement: JudgementResult) -> dict[str, str | None]:
    """Item id -> judged mean, for acceptance checks that read a judgment's own number."""
    return {outcome.item_id: outcome.mean_score for outcome in judgement.items}
