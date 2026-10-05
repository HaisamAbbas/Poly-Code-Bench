"""Regression checks for C++ image identity and cross-language registration metadata."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_cpp_images_for_tests", ROOT / "scripts/build_cpp_images.py"
)
assert SPEC is not None and SPEC.loader is not None
build_cpp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_cpp)


def test_cppcheck_version_parser_records_the_two_component_version(monkeypatch) -> None:
    monkeypatch.setattr(
        build_cpp,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="Cppcheck 2.10\n", stderr=""
        ),
    )

    assert build_cpp.tool_version("evaluator", "sha256:" + "a" * 64, "cppcheck") == "2.10"


def test_cpp_image_refresh_keeps_every_registered_language(tmp_path, monkeypatch) -> None:
    languages = ("python", "rust", "javascript", "typescript", "c", "cpp", "go", "java")
    entry_points = {
        "python": "py.plugin:PythonPlugin",
        "rust": "rust.plugin:RustPlugin",
        "javascript": "js.plugin:JavaScriptPlugin",
        "typescript": "js.plugin:TypeScriptPlugin",
        "c": "c.plugin:CPlugin",
        "cpp": "cpp.plugin:CppPlugin",
        "go": "go.plugin:GoPlugin",
        "java": "java.plugin:JavaPlugin",
    }
    monkeypatch.setattr(build_cpp, "ROOT", tmp_path)
    allowlist = tmp_path / "config/plugins/allowlist-v1.yaml"
    monkeypatch.setattr(build_cpp, "ALLOWLIST", allowlist)

    for index, language in enumerate(languages, start=1):
        identity = tmp_path / f"config/images/{language}-v1.json"
        identity.parent.mkdir(parents=True, exist_ok=True)
        digest = "sha256:" + str(index) * 64
        identity.write_text(
            json.dumps(
                {
                    "kind": f"{language}_images",
                    "images": {"runtime": {"digest": digest}},
                }
            ),
            encoding="utf-8",
        )
        distribution = tmp_path / f"plugins/languages/{language}/pyproject.toml"
        distribution.parent.mkdir(parents=True, exist_ok=True)
        distribution.write_text(
            '[project.entry-points."polycodebench.language_plugins"]\n'
            + f'{language} = "{entry_points[language]}"\n',
            encoding="utf-8",
        )

    # C++'s current recipe output replaces its fixture digest just as a real rebuild would.
    cpp_digest = "sha256:" + "f" * 64
    build_cpp.write_allowlist({"evaluator": {"digest": cpp_digest}})
    result = yaml.safe_load(allowlist.read_text(encoding="utf-8"))
    plugins = {item["plugin_id"]: item for item in result["plugins"]}

    assert set(plugins) == set(languages)
    assert plugins["javascript"]["entry_point"] == entry_points["javascript"]
    assert plugins["typescript"]["entry_point"] == entry_points["typescript"]
    assert plugins["cpp"]["image_digests"] == [cpp_digest]
