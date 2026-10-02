"""Load the guest modules on the host, so one classification serves the guest and its tests.

``pcb_sanitizer_report.py`` runs inside the image to classify an instrumented run, and the host
parser imports the same module to read the report back. There is therefore exactly one definition
of what an unsupported sanitizer runtime looks like, and it cannot drift between the two.
"""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType

GUEST_DIR = Path(__file__).resolve().parent / "guest"


@cache
def load_guest(name: str) -> ModuleType:
    path = GUEST_DIR / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"no guest module {name!r} at {path}")
    spec = importlib.util.spec_from_file_location(f"pcb_guest_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"guest module {name!r} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


__all__ = ["GUEST_DIR", "load_guest"]
