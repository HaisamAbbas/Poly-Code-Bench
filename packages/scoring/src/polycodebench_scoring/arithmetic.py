"""Exact decimal arithmetic for the composite (Technical Spec 14.3, [T4]).

Three responsibilities, all pure:

* **effective weights** - when a quality dimension is predeclared N/A its basis points are
  redistributed among the applicable ones in proportion to their base weights, so the composite
  always spans 10000 basis points;
* **integer presentation** - the ``ScoreItem`` contract stores integer basis points, which are the
  rounded presentation of the exact weights actually used in the sum;
* **efficiency** - the documented piecewise transform, recomputed here from ratios so a wrong
  efficiency value cannot enter a composite.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from fractions import Fraction

from polycodebench_core.models import ScoreDimension

from polycodebench_scoring.manifest import EfficiencyMeasurement
from polycodebench_scoring.policy import (
    COMPOSITE_DIMENSIONS,
    DIMENSION_ORDER,
    TOTAL_BP,
    CompositeWeights,
    EfficiencyPolicy,
)

# 40 significant digits is far beyond the six published places and makes the intermediate
# divisions exact enough that rounding happens once, at presentation.
getcontext().prec = 40

SIX_PLACES = Decimal("0.000001")
ONE = Decimal(1)
HUNDRED = Decimal(100)


def quantize_score(value: Decimal) -> str:
    """Publish one number: fixed point, six places, half-even."""
    return str(value.quantize(SIX_PLACES, rounding=ROUND_HALF_EVEN))


def quantize_to_unit(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)


def decimal_from_fraction(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


def effective_weights(
    weights: CompositeWeights,
    applicable_quality: tuple[ScoreDimension, ...],
) -> dict[ScoreDimension, Fraction]:
    """Base points redistributed over the frozen applicable set (Technical Spec 14.3).

    Correctness keeps its own weight. Each applicable quality dimension receives
    ``base_d / sum(base over applicable)`` of the quality points, so the quality block always sums
    to the policy's quality total and the composite always spans 10000 basis points.
    """
    quality_total = sum(weights.base_bp(dimension) for dimension in applicable_quality)
    result: dict[ScoreDimension, Fraction] = {
        ScoreDimension.CORRECTNESS: Fraction(weights.correctness)
    }
    if quality_total == 0:
        # Every quality dimension is predeclared N/A. Nothing can be redistributed onto, so this is
        # a correctness-only board and correctness carries the whole composite.
        result[ScoreDimension.CORRECTNESS] = Fraction(TOTAL_BP)
        return result
    points = Fraction(weights.quality_points_bp)
    for dimension in applicable_quality:
        result[dimension] = points * Fraction(weights.base_bp(dimension), quality_total)
    return result


def integer_weights(exact: Mapping[ScoreDimension, Fraction]) -> dict[ScoreDimension, int]:
    """Largest-remainder integer allocation of the exact weights, summing to exactly 10000.

    Ties break on the frozen dimension order, so the integer view is as deterministic as the exact
    one. It is presentation only: the composite is computed from the exact fractions.
    """
    floors = {dimension: value.numerator // value.denominator for dimension, value in exact.items()}
    remainder = TOTAL_BP - sum(floors.values())
    if remainder < 0:
        raise ValueError("exact weights cannot round below the composite span")
    order = sorted(
        exact,
        key=lambda dimension: (-_remainder(exact[dimension]), DIMENSION_ORDER[dimension]),
    )
    for dimension in order[:remainder]:
        floors[dimension] += 1
    return floors


def split_exact(total: Fraction, shares: Mapping[str, int]) -> dict[str, Fraction]:
    """Split an exact weight across weighted leaves in proportion to their weights."""
    denominator = sum(shares.values())
    if denominator == 0:
        return {name: Fraction(0) for name in shares}
    return {name: total * Fraction(weight, denominator) for name, weight in shares.items()}


def split_integer(total: int, shares: Mapping[str, int]) -> dict[str, int]:
    """Integer view of :func:`split_exact` summing to exactly ``total``."""
    if sum(shares.values()) == 0:
        return {name: 0 for name in shares}
    exact = split_exact(Fraction(total), shares)
    floors = {name: value.numerator // value.denominator for name, value in exact.items()}
    remainder = total - sum(floors.values())
    order = sorted(exact, key=lambda name: (-_remainder(exact[name]), name))
    for name in order[:remainder]:
        floors[name] += 1
    return floors


def _remainder(value: Fraction) -> Fraction:
    """The fractional part of an exact weight, for the largest-remainder tie-break."""
    return value - (value.numerator // value.denominator)


def ratio_transform(ratio: Decimal, breakpoint: int) -> Decimal:
    """``f(r, b) = 100 if r <= 1; 100*(b-r)/(b-1) if 1 < r < b; 0 if r >= b``."""
    b = Decimal(breakpoint)
    if ratio <= ONE:
        return HUNDRED
    if ratio >= b:
        return Decimal(0)
    return HUNDRED * (b - ratio) / (b - ONE)


def efficiency_value(measurement: EfficiencyMeasurement, policy: EfficiencyPolicy) -> Decimal:
    """``E = 0.70*f(r_time,4) + 0.30*f(r_memory,2)`` with the policy's own breakpoints.

    A censored component contributes a proven zero; an absent component is refused rather than
    replaced by a neutral value.
    """
    time_component: Decimal | None = None
    memory_component: Decimal | None = None
    if measurement.time_censored == "zero_by_lower_bound":
        time_component = Decimal(0)
    elif measurement.time_ratio is not None:
        time_component = ratio_transform(Decimal(measurement.time_ratio), policy.timing_breakpoint)
    if measurement.memory_censored == "zero_by_lower_bound":
        memory_component = Decimal(0)
    elif measurement.memory_ratio is not None:
        memory_component = ratio_transform(
            Decimal(measurement.memory_ratio), policy.memory_breakpoint
        )
    if time_component is None or memory_component is None:
        raise ScoringRefusedInput(
            "efficiency evidence is incomplete: "
            f"time={measurement.time_ratio!r}/{measurement.time_censored!r}, "
            f"memory={measurement.memory_ratio!r}/{measurement.memory_censored!r}"
        )
    score = (
        Decimal(policy.time_weight) * time_component
        + Decimal(policy.memory_weight) * memory_component
    ) / Decimal(TOTAL_BP)
    return score.quantize(SIX_PLACES, rounding=ROUND_HALF_EVEN)


class ScoringRefusedInput(ValueError):
    """Internal marker replaced by the public refusal type at the scorer boundary."""


def dimension_share(weight: Fraction, value: Decimal) -> Decimal:
    """Contribution of one dimension: ``effective basis points / 10000 * value``."""
    return Decimal(weight.numerator) * value / (Decimal(weight.denominator) * Decimal(TOTAL_BP))


def nominal_share(weight_bp: int, value: Decimal) -> Decimal:
    """Contribution of one leaf inside a dimension, before the dimension's weight is applied."""
    return Decimal(weight_bp) * value / Decimal(TOTAL_BP)


__all__ = [
    "COMPOSITE_DIMENSIONS",
    "HUNDRED",
    "ONE",
    "SIX_PLACES",
    "ScoringRefusedInput",
    "decimal_from_fraction",
    "dimension_share",
    "efficiency_value",
    "effective_weights",
    "integer_weights",
    "nominal_share",
    "quantize_score",
    "quantize_to_unit",
    "ratio_transform",
    "split_exact",
    "split_integer",
]
