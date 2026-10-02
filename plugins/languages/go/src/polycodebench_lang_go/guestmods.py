"""Load the guest scripts as modules so the parsers reuse the exact classification logic.

The guest directory ships inside the sandbox images as plain scripts and is not a package, so the
parsers import the same files by path. Nothing here executes candidate code: these modules only
turn recorded bytes into records.
"""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType

GUEST_DIR = Path(__file__).resolve().parent / "guest"


@cache
def load_guest(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"pcb_guest_{name}", GUEST_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"guest module {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
