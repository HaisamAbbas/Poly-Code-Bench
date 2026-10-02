"""Test-session path setup.

The Rust language plugin is a workspace member that is not installed into every development
environment, so its source directory is put on the import path here, before any test module (and
its import sorting) can depend on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

RUST_SRC = Path(__file__).resolve().parents[1] / "plugins" / "languages" / "rust" / "src"
GO_SRC = Path(__file__).resolve().parents[1] / "plugins" / "languages" / "go" / "src"
if str(GO_SRC) not in sys.path:
    sys.path.insert(0, str(GO_SRC))
if str(RUST_SRC) not in sys.path:
    sys.path.insert(0, str(RUST_SRC))

CPP_SRC = Path(__file__).resolve().parents[1] / "plugins" / "languages" / "cpp" / "src"
if str(CPP_SRC) not in sys.path:
    sys.path.insert(0, str(CPP_SRC))
