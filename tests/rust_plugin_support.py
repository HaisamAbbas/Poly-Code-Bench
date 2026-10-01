"""Shared helpers for the Rust language plugin tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "languages" / "rust" / "src"))

from polycodebench_lang_rust import RustLanguagePlugin  # noqa: E402
from polycodebench_plugins_api import (  # noqa: E402
    AnalysisPlan,
    BuildPlan,
    DictArtifactReader,
    ExecutionPlan,
    FrozenTask,
    TaskDraft,
)

TOP_WORDS = ROOT / "plugins" / "languages" / "rust" / "fixtures" / "top-words"
RECORDINGS = ROOT / "tests" / "fixtures" / "rust_tool_output"
DIGEST = "sha256:" + "1" * 64


def package_files(root: Path = TOP_WORDS) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for area in ("visible", "hidden", "admission")
        for path in sorted((root / area).rglob("*"))
        if path.is_file() and "target" not in path.parts
    }


def manifest(root: Path = TOP_WORDS) -> dict[str, Any]:
    loaded = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def draft(root: Path = TOP_WORDS, files: dict[str, bytes] | None = None) -> TaskDraft:
    data = manifest(root)
    return TaskDraft(
        task_id=data["task"]["task_id"],
        primary_language="rust",
        manifest=data,
        files=files if files is not None else package_files(root),
    )


def frozen(plugin: RustLanguagePlugin, **overrides: Any) -> FrozenTask:
    view = plugin.freeze_view(draft(), DIGEST)
    return view.model_copy(update=overrides) if overrides else view


def with_quality(view: FrozenTask, **changes: Any) -> FrozenTask:
    """A frozen task whose quality plan has ``changes`` applied (and analyzers kept in step)."""
    quality = {**view.quality, **changes}
    update: dict[str, Any] = {"quality": quality}
    if "required_analyzers" in changes:
        update["required_analyzers"] = tuple(changes["required_analyzers"])
    return view.model_copy(update=update)


class Recording:
    """A real recorded plan execution: plan, supervisor record and declared outputs."""

    def __init__(self, scenario: str, plan_id: str) -> None:
        self.directory = RECORDINGS / scenario / plan_id
        text = (self.directory / "plan.json").read_text("utf-8")
        kind = json.loads(text)["kind"]
        model = {"analysis_plan": AnalysisPlan, "build_plan": BuildPlan}.get(kind, ExecutionPlan)
        self.plan: Any = model.model_validate_json(text)
        self.record: dict[str, Any] = json.loads(
            (self.directory / "execution.json").read_text("utf-8")
        )
        self.files: dict[str, bytes] = {
            path.name.replace("__", "/"): path.read_bytes()
            for path in self.directory.iterdir()
            if path.name not in {"plan.json", "execution.json"}
        }

    def reader(
        self,
        *,
        record: dict[str, Any] | None = None,
        files: dict[str, bytes] | None = None,
        drop_record: bool = False,
        drop: tuple[str, ...] = (),
    ) -> DictArtifactReader:
        merged = dict(self.files if files is None else files)
        for name in drop:
            merged.pop(name, None)
        if not drop_record:
            merged["_execution.json"] = json.dumps(record or self.record).encode("utf-8")
        return DictArtifactReader(merged)
