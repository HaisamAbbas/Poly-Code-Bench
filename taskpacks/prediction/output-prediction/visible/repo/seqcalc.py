"""Arithmetic helpers whose behaviour is read, not run."""

CAP = 40


def flow(start: int) -> list[int]:
    """Double ``start`` until it reaches ``CAP``, capping the final step."""
    values = [start]
    while values[-1] < CAP:
        nxt = values[-1] * 2
        values.append(min(nxt, CAP))
    return values


def report(values: list[int]) -> str:
    """Render one run as ``step=value`` fields, pipe-separated."""
    return " | ".join(f"{index + 1}={value}" for index, value in enumerate(values))


def run(start: int) -> str:
    return report(flow(start))
