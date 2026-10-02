"""Checks for version parsing and immutable input verification in the Java image builder."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_java_images import parsed_version  # noqa: E402
from fetch_java_components import ANALYZER_PLUGINS, BUILD_PLUGINS, seed_goals  # noqa: E402


def test_java_runtime_version_string_is_normalized_without_a_capture_group() -> None:
    assert parsed_version('openjdk version "21.0.11" 2026-07-21') == "21-0-11"


def test_tool_identity_version_preserves_plugin_version() -> None:
    assert parsed_version("Apache Maven 3.9.9") == "3-9-9"
    assert parsed_version("maven-compiler-plugin 3.13.0") == "3-13-0"


def test_version_parser_does_not_choose_a_build_date_over_a_tool_version() -> None:
    assert parsed_version('openjdk version "21.0.11" 2026-07-21') == "21-0-11"


def test_java_component_seeds_keep_analyzers_out_of_the_runtime_repository() -> None:
    base_goals = seed_goals(BUILD_PLUGINS)
    evaluator_goals = seed_goals((*BUILD_PLUGINS, *ANALYZER_PLUGINS))
    assert not {"spotbugs:check", "pmd:pmd", "checkstyle:checkstyle"} & set(base_goals)
    assert {"spotbugs:check", "pmd:pmd", "checkstyle:checkstyle"} <= set(evaluator_goals)
    assert base_goals[-2:] == evaluator_goals[-2:] == ["dependency:help", "-q"]
