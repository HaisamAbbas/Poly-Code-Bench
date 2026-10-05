"""Record real analyzer executions as parser test fixtures (needs the local Docker images).

Runs each analysis plan of the Python plugin against small candidate files inside the sandbox and
stores the plan, the supervisor execution record and the declared outputs under
``tests/fixtures/python_tool_output/``. Parser tests then replay those recordings offline, so the
parsers are checked against what the pinned tools really print, not against hand-written JSON.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "python_tool_output"
BASE = ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"

DEFECTS = """import hashlib
import os
import pickle
import subprocess

TOKEN = "abc123secret"


def run(cmd, cache=[]):
    cache.append(cmd)
    try:
        return subprocess.run(cmd, shell=True, check=False)
    except:
        pass


def load(blob):
    return pickle.loads(blob)


def count(words):
    c = {}
    for w in words:
        c[w] = c.get(w, 0) + 1
    total = sum([len(w) for w in words])
    for i in range(len(words)):
        print(words[i])
    handle = open(os.devnull)
    return c, total, handle


def digest(data):
    return hashlib.md5(data).hexdigest()
"""

CLEAN = """from collections import Counter


def count(words: list[str]) -> dict[str, int]:
    return dict(Counter(words))
"""

TYPED_BAD = """def add(a: int, b: int) -> str:
    return a + b


def untyped(x):
    return x
"""


def _view(plugin: PythonLanguagePlugin, typed: bool):  # type: ignore[no-untyped-def]
    files = {
        p.relative_to(BASE).as_posix(): p.read_bytes()
        for area in ("visible", "hidden", "admission")
        for p in (BASE / area).rglob("*")
        if p.is_file()
    }
    import yaml  # type: ignore[import-untyped,unused-ignore]

    manifest = yaml.safe_load((BASE / "manifest.yaml").read_text(encoding="utf-8"))
    draft = TaskDraft(
        task_id="conformance-top-words", primary_language="python", manifest=manifest, files=files
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    if typed:
        quality = {
            **view.quality,
            "typing_expectation": "required",
            "required_analyzers": ["ruff", "bandit", "context", "mypy"],
            "opportunities": {
                **cast("Mapping[str, int]", view.quality["opportunities"]),
                "type_hints": 1,
            },
        }
        view = view.model_copy(
            update={"quality": quality, "required_analyzers": ("ruff", "bandit", "context", "mypy")}
        )
    return view


async def record(name: str, source: str, *, typed: bool) -> None:
    plugin = PythonLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "record-state",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    view = _view(plugin, typed)
    files = {"solution.py": source.encode("utf-8")}
    context = AnalysisContext(
        task=view, candidate_digest="sha256:" + "2" * 64, candidate_paths=("solution.py",)
    )
    for plan in plugin.analysis_plans(context):
        run = await runner.run(
            plan, materialize_inputs(plan, {"candidate": files}), stage_id=f"rec-{name}"
        )
        target = FIXTURES / name / plan.analyzer_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
        (target / "execution.json").write_text(
            run.record.model_dump_json(indent=1), encoding="utf-8"
        )
        for path, data in run.outputs.items():
            (target / path.replace("/", "__")).write_bytes(data)
        print(name, plan.analyzer_id, run.record.exit_code, sorted(run.outputs))


async def main() -> None:
    await record("defects", DEFECTS, typed=False)
    await record("clean", CLEAN, typed=False)
    await record("typed-bad", TYPED_BAD, typed=True)


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
