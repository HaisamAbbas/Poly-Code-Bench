"""The frozen policy, ownership and profile configurations the scorer depends on.

These tests exist because a policy that silently loads with different weights is the one failure
that would quietly change every published score. They check the real repository files.
"""

from __future__ import annotations

import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
import scoring_support as s
import yaml
from polycodebench_core.models import ScoreDimension
from polycodebench_scoring import (
    CompositeWeights,
    ScoringConfigError,
    effective_weights,
    integer_weights,
    quantize_score,
)
from polycodebench_scoring.arithmetic import ratio_transform, split_integer
from polycodebench_scoring.loader import load_evidence_ownership, load_scoring_policy
from polycodebench_scoring.policy import TOTAL_BP


@pytest.fixture(scope="module")
def policy():  # type: ignore[no-untyped-def]
    return load_scoring_policy(s.POLICY_PATH)


@pytest.fixture(scope="module")
def ownership():  # type: ignore[no-untyped-def]
    return load_evidence_ownership(s.OWNERSHIP_PATH)


def test_the_repository_policy_loads_and_is_not_effective_for_scoring(policy) -> None:  # type: ignore[no-untyped-def]
    # Calibration is still pending, so the policy must not claim it can produce ranked results.
    assert policy.policy_id == "polycodebench-code-pilot-v1"
    assert policy.effective_for_scoring is False
    assert policy.calibration_status == "pending"
    assert policy.score_schema_version == 1
    assert policy.rounding.mode == "half_even"
    assert policy.rounding.places == 6


def test_the_composite_weights_are_the_documented_basis_points(policy) -> None:  # type: ignore[no-untyped-def]
    weights = policy.code_composite.weights
    assert weights.base_bp(ScoreDimension.CORRECTNESS) == 3000
    assert weights.base_bp(ScoreDimension.SECURITY) == 2000
    assert weights.base_bp(ScoreDimension.EFFICIENCY) == 1500
    assert weights.base_bp(ScoreDimension.CODE_QUALITY) == 1500
    assert weights.base_bp(ScoreDimension.IDIOMATIC) == 1000
    assert weights.base_bp(ScoreDimension.ROBUSTNESS) == 1000
    assert weights.quality_points_bp == 7000


def test_the_security_penalty_table_is_the_documented_one(policy) -> None:  # type: ignore[no-untyped-def]
    assert policy.security.penalty("critical") == 50
    assert policy.security.penalty("high") == 25
    assert policy.security.penalty("medium") == 10
    assert policy.security.penalty("low") == 3


def test_the_efficiency_transform_is_the_documented_one(policy) -> None:  # type: ignore[no-untyped-def]
    assert policy.efficiency.timing_breakpoint == 4
    assert policy.efficiency.memory_breakpoint == 2
    assert policy.efficiency.time_weight == 7000
    assert policy.efficiency.memory_weight == 3000
    # f(r, b) from Technical Spec 13.4.
    assert ratio_transform(Decimal("1"), policy.efficiency.timing_breakpoint) == Decimal(100)
    assert ratio_transform(Decimal("2"), policy.efficiency.timing_breakpoint) == Decimal(200) / 3
    assert ratio_transform(Decimal("4"), policy.efficiency.timing_breakpoint) == Decimal(0)
    assert ratio_transform(Decimal("1.5"), policy.efficiency.memory_breakpoint) == Decimal(50)


def test_the_code_quality_groups_and_anchors_are_the_documented_ones(policy) -> None:  # type: ignore[no-untyped-def]
    assert policy.code_quality.items == {
        "naming_readability": 2000,
        "decomposition": 2500,
        "duplication": 1500,
        "unnecessary_complexity": 1500,
        "repository_style_consistency": 1500,
        "minimal_relevant_scope": 1000,
    }
    assert policy.code_quality.anchors == ("0.000000", "0.500000", "1.000000")


def test_the_idiomatic_and_robustness_blocks_span_their_dimensions(policy) -> None:  # type: ignore[no-untyped-def]
    assert (
        policy.idiomatic.language_rubric_weight_bp + policy.idiomatic.residual_judge_weight_bp
        == TOTAL_BP
    )
    assert policy.idiomatic.full_language_profiles_are_diagnostic_only is True
    assert (
        policy.robustness.scenario_weight_bp + policy.robustness.residual_judge_weight_bp
        == TOTAL_BP
    )
    assert policy.robustness.duplicate_test_count_bonus is False


def test_the_ownership_policy_declares_one_owner_per_family(ownership) -> None:  # type: ignore[no-untyped-def]
    families = {rule.family: rule.owner for rule in ownership.rules}
    assert families["canonical-security-issue"] is ScoreDimension.SECURITY
    assert families["measured-runtime-regression"] is ScoreDimension.EFFICIENCY
    assert families["resource-cleanup-failure"] is ScoreDimension.ROBUSTNESS
    assert families["language-api-design"] is ScoreDimension.IDIOMATIC
    assert ownership.relations_penalized == ("introduced", "worsened", "unchanged_in_scope")
    assert ownership.relations_without_penalty == ("resolved", "unchanged_out_of_scope")
    assert ownership.duplicate_issue_key_policy == "collapse_to_canonical_issue"
    # No distinct consequence is pre-approved: a new pair must be justified before it is used.
    assert ownership.distinct_consequences == ()


def test_the_policy_binds_the_ownership_policy_it_expects(ownership) -> None:  # type: ignore[no-untyped-def]
    policy = load_scoring_policy(s.POLICY_PATH)
    assert policy.evidence_ownership_policy_id == ownership.policy_id


def test_the_policy_binds_the_language_profile_source_by_digest(policy) -> None:  # type: ignore[no-untyped-def]
    from polycodebench_core.canonical import sha256_bytes

    profiles_path = s.CONFIG_ROOT / "languages" / "profiles-v1.yaml"
    # A real digest, reproducible with sha256sum. A placeholder would let a profile weight change
    # slip past the policy that supposedly pins it.
    assert policy.idiomatic.profile_source_digest == sha256_bytes(profiles_path.read_bytes())


def test_the_profile_source_digest_check_passes_on_the_repository_files() -> None:
    import scripts.hash_scoring_profile_source as hasher

    assert hasher.main([]) == 0


def test_full_applicability_redistributes_nothing() -> None:
    weights = CompositeWeights(
        correctness=3000,
        security=2000,
        efficiency=1500,
        code_quality=1500,
        idiomatic=1000,
        robustness=1000,
    )
    exact = effective_weights(
        weights,
        (
            ScoreDimension.SECURITY,
            ScoreDimension.EFFICIENCY,
            ScoreDimension.CODE_QUALITY,
            ScoreDimension.IDIOMATIC,
            ScoreDimension.ROBUSTNESS,
        ),
    )
    assert exact[ScoreDimension.SECURITY] == Fraction(2000)
    assert sum(exact.values()) == TOTAL_BP
    assert integer_weights(exact) == {
        ScoreDimension.CORRECTNESS: 3000,
        ScoreDimension.SECURITY: 2000,
        ScoreDimension.EFFICIENCY: 1500,
        ScoreDimension.CODE_QUALITY: 1500,
        ScoreDimension.IDIOMATIC: 1000,
        ScoreDimension.ROBUSTNESS: 1000,
    }


def test_redistribution_keeps_the_relative_base_weights_and_spans_10000() -> None:
    weights = CompositeWeights(
        correctness=3000,
        security=2000,
        efficiency=1500,
        code_quality=1500,
        idiomatic=1000,
        robustness=1000,
    )
    exact = effective_weights(
        weights,
        (
            ScoreDimension.SECURITY,
            ScoreDimension.CODE_QUALITY,
            ScoreDimension.IDIOMATIC,
            ScoreDimension.ROBUSTNESS,
        ),
    )
    # 2000/5500, 1500/5500, 1000/5500, 1000/5500 of the 7000 quality points.
    assert exact[ScoreDimension.SECURITY] == Fraction(7000 * 2000, 5500)
    assert exact[ScoreDimension.CODE_QUALITY] == Fraction(7000 * 1500, 5500)
    assert exact[ScoreDimension.IDIOMATIC] == Fraction(7000 * 1000, 5500)
    assert sum(exact.values()) == TOTAL_BP
    integers = integer_weights(exact)
    assert sum(integers.values()) == TOTAL_BP
    assert integers[ScoreDimension.CORRECTNESS] == 3000
    # 2545 + 1909 + 1273 + 1273 = 7000
    assert integers[ScoreDimension.SECURITY] == 2545
    assert integers[ScoreDimension.CODE_QUALITY] == 1909
    assert integers[ScoreDimension.IDIOMATIC] == 1273
    assert integers[ScoreDimension.ROBUSTNESS] == 1273


def test_a_correctness_only_board_puts_the_whole_composite_on_correctness() -> None:
    weights = CompositeWeights(
        correctness=3000,
        security=2000,
        efficiency=1500,
        code_quality=1500,
        idiomatic=1000,
        robustness=1000,
    )
    exact = effective_weights(weights, ())
    assert exact == {ScoreDimension.CORRECTNESS: Fraction(TOTAL_BP)}
    assert integer_weights(exact)[ScoreDimension.CORRECTNESS] == TOTAL_BP


def test_split_integer_is_a_deterministic_largest_remainder_split() -> None:
    shares = {"a": 1, "b": 1, "c": 1}
    assert sum(split_integer(100, shares).values()) == 100
    assert split_integer(100, shares) == {"a": 34, "b": 33, "c": 33}
    assert split_integer(100, shares) == split_integer(100, dict(reversed(list(shares.items()))))
    assert split_integer(0, shares) == {"a": 0, "b": 0, "c": 0}


def test_quantisation_is_fixed_point_six_places_half_even() -> None:
    assert quantize_score(Decimal("89.75")) == "89.750000"
    assert quantize_score(Decimal("90.77272727272727")) == "90.772727"
    assert quantize_score(Decimal("61.66666666666666")) == "61.666667"
    # Half-even rounds a trailing five to the even digit, not away from zero.
    assert quantize_score(Decimal("0.0000005")) == "0.000000"
    assert quantize_score(Decimal("0.0000015")) == "0.000002"


def test_a_broken_policy_file_is_refused_rather_than_defaulted(tmp_path: Path) -> None:
    document = yaml.safe_load(s.POLICY_PATH.read_text(encoding="utf-8"))
    document["security"]["penalties"]["high"] = "twenty five"
    broken = tmp_path / "pilot.yaml"
    broken.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ScoringConfigError):
        load_scoring_policy(broken)


def test_an_unknown_policy_key_is_refused(tmp_path: Path) -> None:
    document = yaml.safe_load(s.POLICY_PATH.read_text(encoding="utf-8"))
    document["an_unfrozen_knob"] = 1
    broken = tmp_path / "pilot.yaml"
    broken.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ScoringConfigError):
        load_scoring_policy(broken)


def test_weights_that_do_not_span_the_composite_are_refused() -> None:
    with pytest.raises(ValueError):
        CompositeWeights(
            correctness=3000,
            security=2000,
            efficiency=1500,
            code_quality=1500,
            idiomatic=1000,
            robustness=999,
        )
    with pytest.raises(ValueError):
        CompositeWeights(
            correctness=2500,
            security=2500,
            efficiency=1500,
            code_quality=1500,
            idiomatic=1000,
            robustness=1000,
        )


def test_the_json_archive_round_trips_through_strict_parsing(tmp_path: Path) -> None:
    from polycodebench_scoring.loader import archive_document, load_scoring_policy, write_json

    policy = load_scoring_policy(s.POLICY_PATH)
    path = tmp_path / "policy.json"
    write_json(path, archive_document(policy))
    assert load_scoring_policy(path).content_digest() == policy.content_digest()
    # Floats are outside the canonical domain and cannot enter a frozen policy.
    path.write_text(json.dumps({"kind": "frozen_scoring_policy", "weight": 1.5}), encoding="utf-8")
    with pytest.raises(ScoringConfigError):
        load_scoring_policy(path)
