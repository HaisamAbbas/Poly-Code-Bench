"""Test-session path setup.

Two kinds of import need the repository tree on ``sys.path`` before any test module (and its
import sorting) can depend on them:

* Language plugins that are workspace members rather than installed packages in every
  development environment.
* First-class repository tooling under ``scripts``, which some tests import to exercise the real
  command rather than a copy of it. CI runs a bare ``pytest``, which - unlike ``python -m pytest``
  - does not put the working directory on the import path, so without this the same test passes
  locally and fails in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PLUGIN_SRC_DIRS = (
    ROOT / "plugins" / "languages" / "rust" / "src",
    ROOT / "plugins" / "languages" / "go" / "src",
    ROOT / "plugins" / "languages" / "cpp" / "src",
)

# The repository root comes first so ``scripts`` resolves as a namespace package.
for _import_root in (ROOT, *PLUGIN_SRC_DIRS):
    _path = str(_import_root)
    if _path not in sys.path:
        sys.path.insert(0, _path)
