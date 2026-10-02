"""Record real C plan executions as parser test fixtures (needs the local Docker images).

Runs the C plugin's build, test, analysis and performance plans against the authored fixture variants
inside the sandbox and stores each plan, the supervisor execution record and the declared outputs
under ``tests/fixtures/c_tool_output/<scenario>/<plan>/``. Parser tests replay those recordings
offline, so the parsers are checked against what the pinned clang, clang-tidy, cppcheck, ASan, UBSan
and Valgrind really print.

    .venv/Scripts/python.exe scripts/record_c_tool_fixtures.py [scenario ...]

Every scenario is a real authored variant under ``plugins/languages/c/fixtures/top-words``; nothing
here is a hand-written transcript. That matters for the lanes this fixture exists to pin down: a
hand-written ASan report would only prove the parser reads what a human wrote, not what the toolchain
emits.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import yaml
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_lang_c import CLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, ExecutionPlan, FrozenTask, TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "c_tool_output"
BASE = ROOT / "plugins" / "languages" / "c" / "fixtures" / "top-words"
AREAS = ("visible", "hidden", "admission")


def _read(relative: str) -> str:
    return (BASE / relative).read_text(encoding="utf-8")


def _view(
    plugin: CLanguagePlugin, *, suite_timeout: int | None = None
) -> tuple[FrozenTask, dict[str, bytes]]:
    files = {
        path.relative_to(BASE).as_posix(): path.read_bytes()
        for area in AREAS
        for path in (BASE / area).rglob("*")
        if path.is_file()
    }
    manifest = yaml.safe_load((BASE / "manifest.yaml").read_text(encoding="utf-8"))
    draft = TaskDraft(
        task_id="conformance-c-top-words",
        primary_language="c",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    if suite_timeout is not None:
        inventory = {**view.inventory, "suite_timeout_seconds": suite_timeout}
        view = view.model_copy(update={"inventory": inventory})
    return view, files


def _sources(view: FrozenTask, files: dict[str, bytes], candidate: bytes) -> dict[str, dict[str, bytes]]:
    """Stage the workspace the way a real run would: candidate, overlay, then trusted config."""
    inventory = view.inventory
    groups = inventory["groups"] if isinstance(inventory, dict) else []
    overlay: dict[str, bytes] = {}
    for group in groups:
        for relative in group["files"]:
            overlay[f"work/{relative}"] = files[f"hidden/{relative}"]
    declared = view.quality["performance"]
    if declared:
        overlay[f"work/{declared['workload_file']}"] = files[f"hidden/{declared['workload_file']}"]
    scaffold = view.quality["scaffold_files"]
    config = {f"work/{path}": files[f"visible/repo/{path}"] for path in scaffold}
    return {"candidate": {"src/topwords.c": candidate}, "overlay": overlay, "config": config}


def _store(name: str, plan: ExecutionPlan, run: object) -> None:
    target = FIXTURES / name / plan.plan_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
    (target / "execution.json").write_text(run.record.model_dump_json(indent=1), encoding="utf-8")
    for path, data in run.outputs.items():
        (target / path.replace("/", "__")).write_bytes(data)
    print(name, plan.plan_id, run.record.exit_code, run.record.timed_out, sorted(run.outputs))


async def record(name: str, source: str, *, suite_timeout: int | None = None) -> None:
    plugin = CLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
            ids.instrumented.reference: ids.instrumented.digest,
            ids.performance.reference: ids.performance.digest,
        },
        state_dir=ROOT / ".cache" / "record-c-state",
        # The provider's ceiling is 120s, which is enough for the Valgrind lane and tight enough that
        # a hung plan fails the recording instead of stalling it. The in-guest deadline is separate
        # and comes from the oracle's own suite timeout.
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    view, files = _view(plugin, suite_timeout=suite_timeout)
    sources = _sources(view, files, source.encode("utf-8"))
    candidate = Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": view.task_id,
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": "sha256:" + "0" * 64,
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )
    plans: list[ExecutionPlan] = [plugin.build_plan(view, candidate)]
    plans += [group.plan for group in plugin.test_plan(view).groups]
    context = AnalysisContext(
        task=view, candidate_digest="sha256:" + "2" * 64, candidate_paths=("src/topwords.c",)
    )
    plans += plugin.analysis_plans(context)
    performance = plugin.performance_plan(view)
    if performance is not None:
        plans.append(performance.iteration_plan)
    for plan in plans:
        run = await runner.run(plan, materialize_inputs(plan, sources), stage_id=f"c-rec-{name}")
        _store(name, plan, run)


SCENARIOS: dict[str, tuple[str, int | None]] = {
    "reference": ("hidden/reference/src/topwords.c", None),
    "faulty-ties": ("admission/faulty-ties/src/topwords.c", None),
    "alternative": ("admission/alternative-runs/src/topwords.c", None),
    "quality-defective": ("admission/quality-defective/src/topwords.c", None),
    "buffer-overflow": ("admission/buffer-overflow/src/topwords.c", None),
    "undefined-shift": ("admission/undefined-shift/src/topwords.c", None),
    "resource-leak": ("admission/resource-leak/src/topwords.c", None),
    "compile-error": ("admission/compile-error/src/topwords.c", None),
    "timeout": ("admission/timeout-case/src/topwords.c", 30),
}


async def main(names: list[str]) -> int:
    selected = names or list(SCENARIOS)
    unknown = sorted(set(selected) - set(SCENARIOS))
    if unknown:
        print(f"unknown scenarios: {unknown}", file=sys.stderr)
        return 2
    for name in selected:
        relative, suite_timeout = SCENARIOS[name]
        await record(name, _read(relative), suite_timeout=suite_timeout)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))