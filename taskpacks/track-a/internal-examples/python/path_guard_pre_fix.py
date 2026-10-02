"""Authored internal pre-fix example; not an external or model benchmark result."""

from pathlib import Path


def is_within(root: str, candidate: str) -> bool:
    return Path(candidate).as_posix().startswith(Path(root).as_posix())
