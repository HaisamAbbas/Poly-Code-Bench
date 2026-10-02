"""Checks for version parsing and immutable input verification in the Java image builder."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_java_images  # noqa: E402
from build_java_images import parsed_version  # noqa: E402
from fetch_java_components import ANALYZER_PLUGINS, BUILD_PLUGINS, pom, seed_goals  # noqa: E402


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
    analyzer_goals = {
        "com.github.spotbugs:spotbugs-maven-plugin:4.8.6.0:check",
        "org.apache.maven.plugins:maven-pmd-plugin:3.21.2:pmd",
        "org.apache.maven.plugins:maven-checkstyle-plugin:3.3.1:checkstyle",
    }
    assert not analyzer_goals & set(base_goals)
    assert analyzer_goals <= set(evaluator_goals)
    dependency_goal = "org.apache.maven.plugins:maven-dependency-plugin:3.6.1:help"
    assert base_goals[-2:] == evaluator_goals[-2:] == [dependency_goal, "-q"]


def test_java_pmd_seed_uses_the_parser_target_supported_by_its_pinned_version() -> None:
    runtime_pom = pom(BUILD_PLUGINS)
    evaluator_pom = pom((*BUILD_PLUGINS, *ANALYZER_PLUGINS))
    assert "<targetJdk>17</targetJdk>" not in runtime_pom
    assert "<targetJdk>17</targetJdk>" in evaluator_pom


def test_java_allowlist_rebuild_preserves_python_and_rust_image_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "config" / "images"
    image_dir.mkdir(parents=True)
    for language, digest in (
        ("python", "sha256:" + "1" * 64),
        ("rust", "sha256:" + "2" * 64),
    ):
        (image_dir / f"{language}-v1.json").write_text(
            json.dumps(
                {
                    "kind": f"{language}_images",
                    "images": {"runtime": {"digest": digest}},
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(build_java_images, "ROOT", tmp_path)
    assert build_java_images.known_digests() == {
        "python": {"sha256:" + "1" * 64},
        "rust": {"sha256:" + "2" * 64},
    }
