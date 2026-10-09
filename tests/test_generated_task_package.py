"""Unit tests for scripts/generated_task_package.py (no Docker, no sealing)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from polycodebench_lang_python.taskspec import (
    ExposureRights,
    parse_oracle,
    parse_quality_plan,
    validate_python_task,
)
from polycodebench_plugins_api import TaskDraft

from scripts import generated_task_package as gen

HIDDEN = """from solution import pair


def test_first():
    assert pair(1) == (1, 1)


async def test_second():
    assert pair(2) == (2, 2)


def helper():
    return 3
"""
EXTRA = """from solution import pair


def test_third():
    assert pair(0) == (0, 0)
"""
REFERENCE = "def pair(x):\n    return (x, x)\n"


def draft_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "task_id": "gen-unit-pair",
        "screening_reference": "screening-report-unit",
        "curated_at": "2026-10-08T00:00:00Z",
        "statement": "# Pair\n\nReturn a tuple holding the argument twice.\n",
        "starter_solution": "def pair(x):\n    raise NotImplementedError\n",
        "public_tests": {
            "test_public.py": "from solution import pair\n\n\ndef test_p():\n"
            "    assert pair(5) == (5, 5)\n"
        },
        "hidden_tests": {"test_hidden.py": HIDDEN, "test_more.py": EXTRA},
        "reference_solution": REFERENCE,
        "fixtures": [
            {
                "variant": "alternative",
                "name": "alt",
                "solution": "def pair(x):\n    return tuple([x] * 2)\n",
                "expectation": {},
            },
            {
                "variant": "faulty",
                "name": "faulty-one",
                "solution": "def pair(x):\n    return (x, 0)\n",
                "expectation": {"failing_cases": ["tests/test_hidden.py::test_first"]},
            },
            {
                "variant": "quality_defective",
                "name": "defect",
                "solution": "def pair(x):\n"
                "    try:\n        return (x, x)\n    except:\n        raise\n",
                "expectation": {"expected_issue_families": ["bare-except"]},
            },
            {
                "variant": "timeout",
                "name": "slow",
                "solution": "def pair(x):\n    while True:\n        pass\n",
                "expectation": {"expected_failure": "candidate_timeout"},
            },
        ],
        "provenance": {
            "authorship": "unit test author",
            "originality_statement": "A trivial fixture written for unit tests only.",
            "public_exposure_review": "not applicable to a unit fixture",
            "access_history": [{"party": "unit test", "access": "authored", "date": "2026-10-08"}],
        },
    }
    document.update(overrides)
    return document


def test_oracle_is_generated_from_hidden_test_functions() -> None:
    draft = gen.load_draft(draft_document())
    oracle = parse_oracle(json.dumps(gen.build_oracle(draft)).encode())
    assert [g.group_id for g in oracle.groups] == ["acceptance"]
    group = oracle.groups[0]
    assert group.required and group.classification == "acceptance"
    assert group.files == ("tests/test_hidden.py", "tests/test_more.py")
    assert [c.case_id for c in group.cases] == [
        "tests/test_hidden.py::test_first",
        "tests/test_hidden.py::test_second",
        "tests/test_more.py::test_third",
    ]
    assert all(c.required and c.case_kind == "example" for c in group.cases)


def test_test_classes_and_non_underscore_tests_are_rejected() -> None:
    with pytest.raises(gen.DraftError, match="test classes"):
        gen.hidden_cases("tests/t.py", "class TestX:\n    def test_a(self):\n        pass\n")
    with pytest.raises(gen.DraftError, match="not test_"):
        gen.hidden_cases("tests/t.py", "def testsomething():\n    pass\n")


@pytest.mark.parametrize("variant", ["faulty", "alternative", "quality_defective", "timeout"])
def test_schema_rejects_a_missing_variant(variant: str) -> None:
    document = draft_document()
    kept = [f for f in document["fixtures"] if f["variant"] != variant]
    # Keep four fixtures so only the missing variant (not the minimum length) can be the cause.
    filler = next(f for f in kept if f["variant"] != variant)
    document["fixtures"] = [*kept, {**filler, "name": "filler"}]
    with pytest.raises(gen.DraftError, match="no fixture for variant"):
        gen.load_draft(document)


def test_schema_rejects_an_unknown_failing_case() -> None:
    document = draft_document()
    document["fixtures"][1]["expectation"] = {"failing_cases": ["tests/test_hidden.py::test_nope"]}
    with pytest.raises(gen.DraftError, match="not in the oracle"):
        gen.package_files(gen.load_draft(document))


def test_generate_refuses_to_overwrite(tmp_path: Path) -> None:
    draft = gen.load_draft(draft_document())
    root = gen.generate(draft, tmp_path)
    marker = root / "visible" / "task.md"
    before = marker.read_bytes()
    with pytest.raises(FileExistsError):
        gen.generate(draft, tmp_path)
    assert marker.read_bytes() == before
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "keep.txt").write_text("x")
    with pytest.raises(FileExistsError):
        gen.generate(gen.load_draft(draft_document(task_id="other")), tmp_path)
    assert (tmp_path / "other" / "keep.txt").read_text() == "x"


def test_visible_tree_holds_no_hidden_file(tmp_path: Path) -> None:
    root = gen.generate(gen.load_draft(draft_document()), tmp_path)
    visible = sorted(p.relative_to(root).as_posix() for p in (root / "visible").rglob("*"))
    assert [p for p in visible if (root / p).is_file()] == [
        "visible/repo/solution.py",
        "visible/task.md",
        "visible/tests/test_public.py",
    ]
    visible_bytes = b"\n".join((root / p).read_bytes() for p in visible if (root / p).is_file())
    for area in ("hidden", "admission"):
        for path in (root / area).rglob("*"):
            if path.is_file():
                assert path.read_bytes() not in visible_bytes
                assert path.relative_to(root).as_posix().encode() not in visible_bytes


def test_generated_package_passes_structural_validation(tmp_path: Path) -> None:
    root = gen.generate(gen.load_draft(draft_document()), tmp_path)
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    files = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and p.name != "manifest.yaml"
    }
    report = validate_python_task(
        TaskDraft(
            task_id="gen-unit-pair", primary_language="python", manifest=manifest, files=files
        )
    )
    assert report.ok, report.issues
    plan = parse_quality_plan(files["hidden/quality-plan.yaml"])
    assert plan.required_analyzers == () and plan.typing_expectation == "none"
    assert plan.performance is None
    rights = ExposureRights.model_validate_json(files["admission/exposure-rights.json"])
    assert rights.rights["status"] == "authored_fixture"
    assert "screening report screening-report-unit" in rights.rights["attribution"]
    assert manifest["rights"]["redistribution_status"] == "authored_fixture"
    assert [f["variant"] for f in manifest["fixtures"]] == [
        "reference",
        "alternative",
        "faulty",
        "quality_defective",
        "timeout",
    ]
