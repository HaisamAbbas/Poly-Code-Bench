"""Load a guest script by path so parsers and tests share one implementation.

A guest script is plain stdlib source copied verbatim into the pinned images, executed there
image's own interpreter. Importing the same file here means the classification logic that parses a
recorded capture is literally the logic that would have run in the guest, not a second copy that can
drift.
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType

GUEST_DIR = Path(__file__).resolve().parent / "guest"


@lru_cache(maxsize=8)
def load_guest(name: str) -> ModuleType:
    path = GUEST_DIR / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"no guest script named {name!r} in {GUEST_DIR}")
    spec = importlib.util.spec_from_file_location(f"pcb_c_guest_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib contract
        raise ImportError(f"cannot load guest script {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
