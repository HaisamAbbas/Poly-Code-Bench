"""Public acceptance tests for py-listsort-v1 (visible to the solver).

The solver may run these tests against ``solution.py``. Public test failures
are the only feedback available for repair.
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


class PublicListSortV1Test(unittest.TestCase):
    def test_public_basic(self):
        rows = ["carol clark", "amy adams", "bob brown"]
        result = solution.sort_names(rows)
        self.assertEqual(result, ["amy adams", "bob brown", "carol clark"])
        self.assertIsNot(result, rows)
        self.assertEqual(rows, ["carol clark", "amy adams", "bob brown"])

    def test_public_order_insensitive(self):
        rows = ["Zoe Young", "bob adams", "Alice smith"]
        self.assertEqual(
            solution.sort_names(rows),
            ["bob adams", "Alice smith", "Zoe Young"],
        )

    def test_public_stability(self):
        rows = ["zoe young", "Alice Smith", "alice smith"]
        self.assertEqual(
            solution.sort_names(rows),
            ["Alice Smith", "alice smith", "zoe young"],
        )


if __name__ == "__main__":
    unittest.main()
