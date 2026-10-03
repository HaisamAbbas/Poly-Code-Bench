"""Repository-task admission and the CursorBench-inspired methodology boundary (Prompt 25).

PCB-25-4: realistic multi-file fixtures admitted through the actual harness, and documentation
that makes no claim of private CursorBench task access or exact reproduction - and no false
statement that the CursorBench approach ignores quality or efficiency.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polycodebench_services.repo_tasks import REPRODUCTION_CLAIM_MARKERS
from repo_task_support import ROOT, admission

PACKS = ("ini-interpolate", "history-group")
METHOD_DOCS = (
    ROOT / "docs" / "methodology" / "cursorbench.md",
    ROOT / "docs" / "implementation" / "repo-task-method.md",
)


@pytest.mark.parametrize("name", PACKS)
def test_realistic_multi_file_fixture_is_admitted_through_the_harness(name: str) -> None:
    result = admission(name)
    assert result.admitted, result.checks
    assert result.execution_tier == "local_fixture"
    assert result.reference_stable and result.alternative_passes and result.alternative_differs
    assert result.faulty_rejected
    assert result.quality_defective_functional_pass and result.quality_defective_detected


@pytest.mark.parametrize("name", PACKS)
def test_admission_evidences_the_full_variant_matrix(name: str) -> None:
    result = admission(name)
    by_variant = {entry.variant: entry for entry in result.variants}
    reference = by_variant["reference"]
    assert reference.repetitions == 5 and reference.identical_outcomes
    assert reference.failed_cases == ()
    faulty = by_variant["faulty"]
    assert set(faulty.expected_failing_cases) <= set(faulty.failed_cases)
    quality = by_variant["quality_defective"]
    assert quality.functional_pass, "the weakness must survive every functional case"
    assert set(quality.expected_issue_families) <= set(quality.observed_issue_families)
    alternative = by_variant["alternative"]
    assert alternative.workspace_digest != reference.workspace_digest


@pytest.mark.parametrize("name", PACKS)
def test_methodology_label_is_inspired_with_independent_curation(name: str) -> None:
    result = admission(name)
    assert result.methodology_label == "inspired"
    assert result.inspiration["source_family"] == "cursorbench"
    statement = result.inspiration["statement"].lower()
    assert "independent" in statement
    for marker in REPRODUCTION_CLAIM_MARKERS:
        assert marker not in statement


@pytest.mark.parametrize("path", METHOD_DOCS)
def test_methodology_boundary_documentation_stays_honest(path: Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    assert path.is_file()
    # Independently curated, correctly labelled inspiration.
    assert "independently curated" in text or "independently" in text
    assert "inspired" in text
    # No claim of private access or exact reproduction of the private benchmark.
    assert "no private" in text or "not cursorbench" in text or "never" in text
    for phrase in ("exact reproduction of", "reproduces cursorbench", "cursorbench tasks are"):
        assert phrase not in text
    # No false statement that the inspired approach ignores quality or efficiency: the source
    # describes correctness alongside code quality and efficiency, and the docs must say so
    # explicitly rather than merely avoid the words.
    assert "quality" in text and "efficiency" in text
    assert (
        "makes no claim that the approach ignores quality or efficiency" in text
        or "no claim that the approach ignores quality" in text
    )
