"""Golden and property checks for the efficiency transform (Technical Spec 13.4, Prompt 13).

Every expected number here is the hand-evaluable value of the documented formula, not a recorded
implementation output.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from polycodebench_evaluation.efficiency import (
    MEMORY_BREAKPOINT,
    TIME_BREAKPOINT,
    EfficiencyError,
    censored_time_outcome,
    efficiency_score,
    mad,
    median,
    ratio,
    relative_mad,
    scale_growth_diagnostic,
    stdev,
    transform,
    weighted_geometric_mean,
)

D = Decimal


def test_transform_matches_the_documented_piecewise_formula() -> None:
    # f(r, 4) = 100 when r <= 1; 100*(4-r)/3 for 1 < r < 4; 0 at r >= 4
    assert transform(D("0.25"), TIME_BREAKPOINT) == D("100")
    assert transform(D("1"), TIME_BREAKPOINT) == D("100")
    assert transform(D("2.5"), TIME_BREAKPOINT) == D("50")  # 100*1.5/3
    assert transform(D("1.75"), TIME_BREAKPOINT) == D("75")  # 100*2.25/3
    assert transform(D("4"), TIME_BREAKPOINT) == D("0")
    assert transform(D("9"), TIME_BREAKPOINT) == D("0")
    # f(r, 2) for memory
    assert transform(D("1.5"), MEMORY_BREAKPOINT) == D("50")
    assert transform(D("2"), MEMORY_BREAKPOINT) == D("0")


def test_transform_is_monotone_and_bounded() -> None:
    values = [transform(D(str(step)) / 10, TIME_BREAKPOINT) for step in range(1, 61)]
    assert all(0 <= value <= 100 for value in values)
    assert all(a >= b for a, b in zip(values, values[1:], strict=False))


def test_efficiency_weighting_is_the_documented_blend() -> None:
    # time ratio 2 -> 100*(4-2)/3 = 66.666667; memory ratio 1.5 -> 50
    # E = 0.7*66.666666... + 0.3*50 = 61.666666... -> 61.666667
    assert efficiency_score(time_ratio=D("2"), memory_ratio=D("1.5")) == D("61.666667")
    # a fast candidate (ratio 1) and equal memory -> 100
    assert efficiency_score(time_ratio=D("1"), memory_ratio=D("1")) == D("100.000000")
    # ratio 1 time, memory at breakpoint -> 70
    assert efficiency_score(time_ratio=D("1"), memory_ratio=D("2")) == D("70.000000")


def test_efficiency_refuses_to_invent_a_missing_component() -> None:
    with pytest.raises(EfficiencyError):
        efficiency_score(time_ratio=None, memory_ratio=D("1"))
    with pytest.raises(EfficiencyError):
        efficiency_score(time_ratio=D("1"), memory_ratio=None)


def test_weighted_geometric_mean_of_ratios_and_weight_check() -> None:
    # sqrt(1 * 2) with equal weights = 1.414214 (6dp)
    assert weighted_geometric_mean(
        [(D("1"), 5000), (D("2"), 5000)], quantization="six_places"
    ) == D("1.414214")
    # weights must be basis points summing to 10000 and values must be positive
    with pytest.raises(EfficiencyError):
        weighted_geometric_mean([(D("1"), 5000), (D("2"), 4000)])
    with pytest.raises(EfficiencyError):
        weighted_geometric_mean([(D("0"), 10000)])


def test_ratio_applies_the_same_positive_floor_to_both_sides() -> None:
    # a sub-floor candidate time against a zero reference time cannot divide: both sides are
    # floored, so the ratio stays finite (1.0) instead of exploding or becoming NaN
    assert ratio(D("500"), D("0"), D("1000")) == D("1")
    assert ratio(D("3000"), D("1000"), D("1000")) == D("3")
    # the floor is symmetric: a sub-floor reference is floored too, never ignored
    assert ratio(D("3000"), D("10"), D("1000")) == D("3")


def test_median_mad_and_relative_mad() -> None:
    samples = [10, 20, 30, 40, 100]
    assert median(samples) == D("30")
    assert mad(samples) == D("10")  # |[20,10,0,10,70]| median = 10
    assert relative_mad(samples, D("1")) == D(1) / D(3)
    assert stdev([5, 5, 5]) == D("0")


def test_censored_timeout_is_a_lower_bound_not_a_duration() -> None:
    reference_median_ns = D("1000000")  # 1 ms reference
    # A 5 s timeout against a 1 ms reference is a ratio lower bound of 5000 >= 4 -> censored zero.
    outcome, bound = censored_time_outcome(D("5"), reference_median_ns, TIME_BREAKPOINT)
    assert outcome == "zero_by_lower_bound"
    assert bound == D("5000")
    # the censored time component is zero, so only the memory half can earn points:
    # E = 0.7*0 + 0.3*f(1,2)=100 -> 30.000000
    assert efficiency_score(
        time_ratio=None, memory_ratio=D("1"), time_censored="zero_by_lower_bound"
    ) == D("30.000000")
    # A 2 ms timeout against a 1 ms reference does not prove the breakpoint -> insufficient info.
    outcome, _ = censored_time_outcome(D("0.002"), reference_median_ns, TIME_BREAKPOINT)
    assert outcome == "insufficient_information"
    with pytest.raises(EfficiencyError):
        efficiency_score(time_ratio=None, memory_ratio=D("1"))


def test_scale_growth_is_a_diagnostic_not_a_proof() -> None:
    # 4x scale with 8x time is quadratic in these two points, but the module only reports numbers.
    growth = scale_growth_diagnostic([(1000, D("100")), (4000, D("800"))])
    assert growth["ratio_scale"] == D("4")
    assert growth["ratio_time"] == D("8")
    assert growth["time_per_unit"] == D("2")
    assert scale_growth_diagnostic([(1000, D("100"))])["ratio_time"] is None
