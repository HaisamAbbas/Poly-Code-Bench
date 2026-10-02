"""Efficiency aggregation (Technical Spec 13.4). Pure, deterministic, decimal arithmetic.

Nothing here reads the clock or a file: the same measurements always produce the same numbers, and
every value is a documented transform of *observed* medians. Two rules are load-bearing:

* a timed-out candidate is **censored** - its lower bound may prove a zero score component, and is
  never reported as an exact duration;
* empirical scale growth is recorded as a diagnostic only. No function in this module claims a
  Big-O bound.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from typing import Literal

getcontext().prec = 40

TIME_BREAKPOINT = Decimal("4")
MEMORY_BREAKPOINT = Decimal("2")
TIME_WEIGHT = Decimal("0.70")
MEMORY_WEIGHT = Decimal("0.30")
# Positive measurement floors (Technical Spec 13.4: "the same positive measurement floors"). They
# bound a pathological sub-microsecond/near-zero ratio from becoming an unbounded score.
TIME_FLOOR_NS = Decimal("1000")  # 1 microsecond
MEMORY_FLOOR_KB = Decimal("64")

CensoredOutcome = Literal["zero_by_lower_bound", "insufficient_information"]
Quantization = Literal["none", "six_places"]


class EfficiencyError(ValueError):
    """A measurement set that cannot produce a defensible efficiency value."""


def median(values: Sequence[Decimal | int | float]) -> Decimal:
    if not values:
        raise EfficiencyError("median needs at least one sample")
    ordered = sorted(Decimal(str(value)) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def mad(values: Sequence[Decimal | int | float], center: Decimal | None = None) -> Decimal:
    """Median absolute deviation (Technical Spec 13.3 stability band)."""
    if not values:
        raise EfficiencyError("mad needs at least one sample")
    middle = median(values) if center is None else center
    return median([abs(Decimal(str(value)) - middle) for value in values])


def relative_mad(values: Sequence[Decimal | int | float], floor: Decimal) -> Decimal:
    center = median(values)
    deviation = mad(values, center)
    return deviation / max(center, floor)


def stdev(values: Sequence[Decimal | int | float]) -> Decimal:
    if len(values) < 2:
        return Decimal(0)
    mean = sum(Decimal(str(value)) for value in values) / Decimal(len(values))
    variance = sum((Decimal(str(value)) - mean) ** 2 for value in values) / Decimal(len(values) - 1)
    return variance.sqrt()


def ratio(numerator: Decimal, denominator: Decimal, floor: Decimal) -> Decimal:
    """Ratio with the same positive floor applied to both sides (Technical Spec 13.4)."""
    return max(numerator, floor) / max(denominator, floor)


def transform(r: Decimal, breakpoint: Decimal) -> Decimal:
    """``f(r, b)`` from Technical Spec 13.4, verbatim."""
    if r <= Decimal(1):
        return Decimal(100)
    if r >= breakpoint:
        return Decimal(0)
    return Decimal(100) * (breakpoint - r) / (breakpoint - Decimal(1))


def weighted_geometric_mean(
    pairs: Sequence[tuple[Decimal, int]], *, quantization: Quantization = "none"
) -> Decimal:
    """Weighted geometric mean of ratios whose weights are basis points summing to 10000."""
    if not pairs:
        raise EfficiencyError("a weighted geometric mean needs at least one workload")
    total = sum(weight for _, weight in pairs)
    if total != 10_000:
        raise EfficiencyError(f"workload weights must sum to 10000 basis points, got {total}")
    if any(value <= 0 for value, _ in pairs):
        raise EfficiencyError("geometric means need positive ratios")
    exponent = sum(Decimal(weight) / Decimal(10_000) for _, weight in pairs)
    log_sum = sum((Decimal(weight) / Decimal(10_000)) * value.ln() for value, weight in pairs)
    result = (log_sum / exponent).exp()
    if quantization == "six_places":
        return result.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return result


def censored_time_outcome(
    timeout_seconds: Decimal, reference_median_ns: Decimal, breakpoint: Decimal
) -> tuple[CensoredOutcome, Decimal | None]:
    """What a timed-out candidate may claim (Technical Spec 13.4).

    The timeout gives a *lower bound* on the candidate's duration, so the true ratio is at least
    ``timeout / reference``. When that bound already reaches the breakpoint the time component is
    zero and the result is labelled censored. Otherwise the plan does not contain enough
    information for this transform and the caller must mark performance incomplete - it must not
    invent a duration and must not award partial credit.
    """
    lower_bound = (timeout_seconds * Decimal(1_000_000_000)) / max(
        reference_median_ns, TIME_FLOOR_NS
    )
    if lower_bound >= breakpoint:
        return "zero_by_lower_bound", lower_bound
    return "insufficient_information", lower_bound


def efficiency_score(
    *,
    time_ratio: Decimal | None,
    memory_ratio: Decimal | None,
    time_censored: CensoredOutcome | None = None,
    memory_censored: CensoredOutcome | None = None,
    quantization: Quantization = "six_places",
) -> Decimal:
    """``E = 0.70*f(r_time,4) + 0.30*f(r_memory,2)`` (Technical Spec 13.4).

    A missing component is an error, never a neutral zero or a neutral one hundred: incomplete
    evidence must not read as a perfect or a failing efficiency score.
    """
    if time_ratio is None and time_censored != "zero_by_lower_bound":
        raise EfficiencyError("efficiency needs a measured time ratio or a censored zero")
    if memory_ratio is None and memory_censored != "zero_by_lower_bound":
        raise EfficiencyError("efficiency needs a measured memory ratio or a censored zero")
    time_component = (
        Decimal(0)
        if time_censored == "zero_by_lower_bound"
        else transform(time_ratio, TIME_BREAKPOINT)  # type: ignore[arg-type]
    )
    memory_component = (
        Decimal(0)
        if memory_censored == "zero_by_lower_bound"
        else transform(memory_ratio, MEMORY_BREAKPOINT)  # type: ignore[arg-type]
    )
    score = TIME_WEIGHT * time_component + MEMORY_WEIGHT * memory_component
    return score.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


def scale_growth_diagnostic(
    measurements: Sequence[tuple[int, Decimal]],
) -> dict[str, Decimal | None]:
    """Observed time per input unit between the smallest and largest given scale.

    This is an empirical diagnostic, deliberately *not* a complexity proof: it describes the
    measured points handed to it and nothing else. A caller that wants lane-wide growth passes
    every scale; a per-workload row passes only the scales at or below it.
    """
    if len(measurements) < 2:
        return {"ratio_time": None, "ratio_scale": None, "time_per_unit": None}
    ordered = sorted(measurements)
    (small_scale, small_time), (large_scale, large_time) = ordered[0], ordered[-1]
    scale_ratio = Decimal(large_scale) / Decimal(small_scale)
    return {
        "ratio_time": large_time / small_time,
        "ratio_scale": scale_ratio,
        "time_per_unit": (large_time / Decimal(large_scale)) / (small_time / Decimal(small_scale)),
    }
