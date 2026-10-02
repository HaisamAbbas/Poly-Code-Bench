"""E2E-23: the Technical Spec 14.6 golden calculations, exactly.

Every expected number in this file is the hand-evaluable value of the documented formula. None of
them was copied from a scorer run, and no formula was changed to make an implementation output
pass.
"""

from __future__ import annotations

import pytest
import scoring_support as s
from polycodebench_core.models import EvaluationState, Gate, ScoreDimension
from polycodebench_scoring import (
    ScoringRefusalCode,
    ScoringRefused,
    efficiency_value,
    score_evaluation,
)
from polycodebench_scoring.loader import load_evidence_ownership, load_scoring_policy


@pytest.fixture(scope="module")
def policy():  # type: ignore[no-untyped-def]
    return load_scoring_policy(s.POLICY_PATH)


@pytest.fixture(scope="module")
def ownership():  # type: ignore[no-untyped-def]
    return load_evidence_ownership(s.OWNERSHIP_PATH)


@pytest.fixture(scope="module")
def profile():  # type: ignore[no-untyped-def]
    return s.python_profile()


def _score(policy, ownership, profile, manifest, applicable=s.ALL_QUALITY):  # type: ignore[no-untyped-def]
    return score_evaluation(
        s.frozen_task(applicable),
        policy,
        manifest,
        ownership=ownership,
        profile=profile,
    )


def _dimension(outcome, dimension: ScoreDimension):  # type: ignore[no-untyped-def]
    return next(row for row in outcome.explanation.dimensions if row.dimension is dimension)


def test_full_quality_example_is_89_750000(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    # 30 + 70 * (.20*90 + .15*80 + .15*85 + .10*90 + .10*80) / 100
    #   = 30 + 70 * 59.75 / 100 = 89.75
    manifest = s.manifest(
        issues=(s.security_issue("medium-confirmed", severity="medium"),),
        efficiency_value=s.efficiency("80.000000"),
        code_quality_bp=8500,
        idiomatic_bp=9000,
        robustness_bp=8000,
    )
    outcome = _score(policy, ownership, profile, manifest)
    assert _dimension(outcome, ScoreDimension.SECURITY).raw_value == "90.000000"
    assert _dimension(outcome, ScoreDimension.EFFICIENCY).raw_value == "80.000000"
    assert _dimension(outcome, ScoreDimension.CODE_QUALITY).raw_value == "85.000000"
    assert _dimension(outcome, ScoreDimension.IDIOMATIC).raw_value == "90.000000"
    assert _dimension(outcome, ScoreDimension.ROBUSTNESS).raw_value == "80.000000"
    assert outcome.scorecard.total_score == s.GOLDEN["full_quality"] == "89.750000"
    assert outcome.scorecard.status is EvaluationState.READY
    assert outcome.scorecard.gate is Gate.PASS


def test_same_example_with_efficiency_predeclared_na_is_90_772727(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    # Numerator .20*90 + .15*85 + .10*90 + .10*80 = 47.75, denominator .55,
    # so 30 + 70*(47.75/55) = 90.772727 after rounding.
    applicable = tuple(d for d in s.ALL_QUALITY if d is not ScoreDimension.EFFICIENCY)
    manifest = s.manifest(
        applicable=applicable,
        include_efficiency=False,
        issues=(s.security_issue("medium-confirmed", severity="medium"),),
        code_quality_bp=8500,
        idiomatic_bp=9000,
        robustness_bp=8000,
    )
    outcome = _score(policy, ownership, profile, manifest, applicable)
    assert outcome.scorecard.total_score == s.GOLDEN["efficiency_not_applicable"] == "90.772727"
    efficiency_row = _dimension(outcome, ScoreDimension.EFFICIENCY)
    assert efficiency_row.applicable is False
    assert efficiency_row.nominal_weight_bp == 1500
    assert efficiency_row.effective_weight_bps == 0
    # The 1500bp are redistributed over the remaining four dimensions in proportion to their
    # base weights, so the composite still spans 10000 basis points.
    assert sum(row.effective_weight_bps for row in outcome.explanation.dimensions) == 10_000


def test_duplicate_confirmed_high_security_finding_is_75_once(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    # Two tools, one canonical defect: max(0, 100 - 25) = 75, not 100 - 25 - 25.
    duplicate = s.security_issue("shared-subprocess", tools=("bandit", "semgrep"))
    manifest = s.manifest(issues=(duplicate,))
    outcome = _score(policy, ownership, profile, manifest)
    security_row = next(
        row for row in outcome.explanation.dimensions if row.dimension is ScoreDimension.SECURITY
    )
    assert security_row.raw_value == s.GOLDEN["duplicate_high_security"] == "75.000000"
    assert len(outcome.explanation.issues) == 1
    assert outcome.explanation.issues[0].collapsed_reports == 1
    assert outcome.explanation.issues[0].tools == ("bandit", "semgrep")


def test_two_separate_records_of_one_issue_still_count_once(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    first = s.security_issue("shared-subprocess", tools=("bandit",))
    second = s.security_issue("shared-subprocess", tools=("semgrep",), digest=s.DIGEST_C)
    outcome = _score(policy, ownership, profile, s.manifest(issues=(first, second)))
    security_row = next(
        row for row in outcome.explanation.dimensions if row.dimension is ScoreDimension.SECURITY
    )
    assert security_row.raw_value == "75.000000"
    assert outcome.explanation.collapsed_duplicate_issues == 1


def test_time_ratio_two_and_memory_ratio_one_and_a_half_is_61_666667(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    # f(2, 4) = 100*(4-2)/3 = 66.666666..., f(1.5, 2) = 100*(2-1.5)/1 = 50.
    # E = 0.70*66.666666... + 0.30*50 = 61.666666... -> 61.666667
    manifest = s.manifest(
        efficiency_value=s.efficiency("61.666667", time_ratio="2", memory_ratio="1.5")
    )
    outcome = _score(policy, ownership, profile, manifest)
    efficiency_row = next(
        row for row in outcome.explanation.dimensions if row.dimension is ScoreDimension.EFFICIENCY
    )
    assert efficiency_row.raw_value == s.GOLDEN["time2_memory15"] == "61.666667"
    derived = efficiency_value(
        s.efficiency("61.666667", time_ratio="2", memory_ratio="1.5"), policy.efficiency
    )
    assert str(derived) == "61.666667"


def test_failed_correctness_is_zero_whatever_the_quality_values(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    manifest = s.manifest(
        gate_status="fail",
        efficiency_value=s.efficiency("100.000000"),
        code_quality_bp=10000,
        idiomatic_bp=10000,
        robustness_bp=10000,
    )
    outcome = _score(policy, ownership, profile, manifest)
    assert outcome.scorecard.total_score == s.GOLDEN["failed_gate"] == "0.000000"
    assert outcome.scorecard.gate is Gate.FAIL
    assert outcome.scorecard.status is EvaluationState.READY
    # Raw observations may exist, but no applicable quality item may contribute.
    assert all(item.contribution == "0.000000" for item in outcome.scorecard.items)
    assert all(
        row.status == "gated"
        for row in outcome.explanation.items
        if row.dimension is not ScoreDimension.CORRECTNESS and row.applicable
    )
    assert "gated_by_correctness" in {
        reason for row in outcome.explanation.items for reason in row.reasons
    }


def test_all_quality_at_100_is_100(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    manifest = s.manifest(
        efficiency_value=s.efficiency("100.000000"),
        code_quality_bp=10000,
        idiomatic_bp=10000,
        robustness_bp=10000,
    )
    outcome = _score(policy, ownership, profile, manifest)
    assert outcome.scorecard.total_score == s.GOLDEN["all_quality_100"] == "100.000000"


def test_required_analyzer_missing_on_passing_code_publishes_nothing(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    manifest = s.manifest(required_evidence=())
    outcome = _score(policy, ownership, profile, manifest)
    # Not 100 and not zero: the attempt is incomplete, so there is no composite at all.
    assert outcome.scorecard.total_score is None
    assert outcome.scorecard.status is EvaluationState.NEEDS_REVIEW
    assert outcome.scorecard.gate is Gate.PASS
    assert [reason.reference for reason in outcome.explanation.blocking] == ["analyzer.bandit"]
    assert "no composite published" in outcome.explanation.total_formula


def test_unknown_gate_is_incomplete_not_zero(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    manifest = s.manifest(gate_status="unknown")
    outcome = _score(policy, ownership, profile, manifest)
    assert outcome.scorecard.total_score is None
    assert outcome.scorecard.gate is Gate.UNKNOWN
    assert outcome.scorecard.status is EvaluationState.INFRA_BLOCKED


def test_efficiency_value_must_match_its_own_ratios(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    manifest = s.manifest(
        efficiency_value=s.efficiency("95.000000", time_ratio="2", memory_ratio="1.5")
    )
    with pytest.raises(ScoringRefused) as raised:
        _score(policy, ownership, profile, manifest)
    assert raised.value.code is ScoringRefusalCode.EFFICIENCY_SCORE_MISMATCH
