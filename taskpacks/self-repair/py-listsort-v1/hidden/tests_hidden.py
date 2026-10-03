"""Hidden acceptance tests for py-listsort-v1.

These run only during admission and grading; they are never part of the repair
feedback loop.
"""

import sys
import unittest
from pathlib import Path


def _candidate_dir() -> Path:
    """Locate the directory holding the candidate ``solution.py``."""
    bases = (Path.cwd(), Path(__file__).resolve().parent, *Path(__file__).resolve().parents)
    for base in bases:
        for candidate in (base, base / "repo", base / "visible" / "repo"):
            if (candidate / "solution.py").is_file():
                return candidate
    raise RuntimeError("solution.py not found; run these tests from the candidate workspace")


sys.path.insert(0, str(_candidate_dir()))

import solution  # noqa: E402


class HiddenListSortV1Test(unittest.TestCase):
    def test_hidden_mixed_case(self):
        rows = ["bob adams", "Alice smith", "carol BROWN", "dave Allen"]
        self.assertEqual(
            solution.sort_names(rows),
            ["bob adams", "dave Allen", "carol BROWN", "Alice smith"],
        )

    def test_hidden_stability(self):
        rows = ["alice smith", "Alice Smith", "bob adams"]
        self.assertEqual(
            solution.sort_names(rows),
            ["bob adams", "alice smith", "Alice Smith"],
        )


if __name__ == "__main__":
    unittest.main()
