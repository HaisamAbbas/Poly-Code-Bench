"""Suite-mode fixtures in the task package manifest (Prompt 10 extension of Prompt 05)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from polycodebench_services.task_fixture_runner import validate_authored_fixtures
from polycodebench_services.task_packages import TaskPackageImporter, TaskPackageManifest
from python_plugin_support import TOP_WORDS, manifest


def build(document: dict[str, Any]) -> TaskPackageManifest:
    return TaskPackageManifest.model_validate_json(json.dumps(document)).validate_paths()


def test_the_public_fixture_is_a_valid_suite_package() -> None:
    parsed = build(manifest())
    assert all(case.suite_mode for case in parsed.fixtures)
    assert {case.variant for case in parsed.fixtures} == {
        "reference",
        "faulty",
        "alternative",
        "quality_defective",
        "timeout",
    }


def test_suite_fixtures_must_declare_their_expected_outcome() -> None:
    document = copy.deepcopy(manifest())
    del document["fixtures"][1]["expectation"]
    with pytest.raises(ValueError, match="expected outcome"):
        build(document)


def test_packages_use_one_fixture_mode_and_unique_names() -> None:
    mixed = copy.deepcopy(manifest())
    mixed["fixtures"][0]["input_path"] = "visible/task.md"
    mixed["fixtures"][0]["expected_output_path"] = "hidden/oracle.json"
    with pytest.raises(ValueError, match="stdio or suite"):
        build(mixed)
    duplicate = copy.deepcopy(manifest())
    duplicate["fixtures"][1]["name"] = duplicate["fixtures"][0]["name"]
    with pytest.raises(ValueError, match="unique"):
        build(duplicate)


def test_stdio_fixtures_stay_exact_pairs_and_cannot_use_suite_only_variants() -> None:
    half = copy.deepcopy(manifest())
    half["fixtures"][0]["input_path"] = "visible/task.md"
    with pytest.raises(ValueError, match="both an input and an expected output"):
        build(half)
    stdio = copy.deepcopy(manifest())
    for case in stdio["fixtures"]:
        case["input_path"] = "visible/task.md"
        case["expected_output_path"] = "hidden/oracle.json"
    with pytest.raises(ValueError, match="suite-mode only"):
        build(stdio)


def test_core_variants_remain_mandatory() -> None:
    document = copy.deepcopy(manifest())
    document["fixtures"] = [f for f in document["fixtures"] if f["variant"] != "alternative"]
    with pytest.raises(ValueError, match="reference, faulty, and alternative"):
        build(document)


def test_the_stdio_runner_refuses_suite_packages() -> None:
    package = TaskPackageImporter().import_package(TOP_WORDS)
    with pytest.raises(
        ValueError, match="approved immutable image|language-plugin admission runner"
    ):
        validate_authored_fixtures(package)
