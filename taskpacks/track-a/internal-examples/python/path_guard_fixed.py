"""Authored internal reference repair; not an external or model benchmark result."""

from pathlib import Path


def is_within(root: str, candidate: str) -> bool:
    base = Path(root).resolve()
    child = Path(candidate).resolve()
    return child == base or base in child.parents
