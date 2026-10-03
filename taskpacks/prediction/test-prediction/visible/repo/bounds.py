"""Bounds helpers whose test behaviour is read, not run."""

UPPER = 100
LOWER = 0


def clamp(value: int, upper: int = UPPER) -> int:
    """Return ``value`` bound into ``[LOWER, upper]``; reject a non-int outright."""
    if not isinstance(value, int):
        raise TypeError(f"clamp expects an int, got {type(value).__name__}")
    if value > upper:
        return upper
    if value < LOWER:
        return LOWER
    return value
