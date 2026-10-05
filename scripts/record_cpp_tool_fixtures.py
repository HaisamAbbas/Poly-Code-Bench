"""Record real C++ plan executions as parser test fixtures (needs the local Docker images).

Runs the C++ plugin's build, test and analysis plans against the authored fixture variants inside
the sandbox and stores each plan, the supervisor execution record and the declared outputs under
``tests/fixtures/cpp_tool_output/<scenario>/<plan>/``. Parser tests replay those recordings
offline, so the parsers are checked against what the pinned clang, clang-tidy, cppcheck, ASan and
UBSan really print, not against a hand-written transcript.

    .venv/Scripts/python.exe scripts/record_cpp_tool_fixtures.py [scenario ...]

Every scenario but ``unsupported`` is an authored variant under
``plugins/languages/cpp/fixtures/top-words``; ``unsupported`` injects an impossible sanitizer flag
into the pinned instrumented plan, so the run that is recorded is a real one whose sanitizer runtime
refused to judge. That matters for the lane this fixture exists to pin down: a hand-written ASan
report would only prove the parser reads what a human wrote.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_lang_cpp import CppLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, AnalysisPlan, ExecutionPlan, TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "cpp_tool_output"
BASE = ROOT / "plugins" / "languages" / "cpp" / "fixtures" / "top-words"
AREAS = ("visible", "hidden", "admission")
UNIT = "src/top_words.cpp"
HEADER = "include/top_words.hpp"
# The C++ suite timeout is the oracle's own; the timeout scenario needs the same bound the
# admission lane uses, so the recorded run is a real supervisor timeout rather than a slow pass.
SUITE_TIMEOUT = 60


def _read(relative: str) -> str:
    return (BASE / relative).read_text(encoding="utf-8")


# Every scenario is an authored variant; `compile-error` is the reference with one statement
# broken, which is the smallest edit that still compiles the header and fails the translation unit.
_COMPILE_ERROR = (
    _read("hidden/reference/src/top_words.cpp")
    .replace("    if (ranked.size() > k) {", "    if (ranked.size() > k) {", 1)
    .replace("ranked.resize(k);", "ranked.resize(k", 1)
)


def _view(plugin: CppLanguagePlugin) -> tuple[Any, dict[str, bytes]]:
    files = {
        path.relative_to(BASE).as_posix(): path.read_bytes()
        for area in AREAS
        for path in sorted((BASE / area).rglob("*"))
        if path.is_file()
    }
    manifest = yaml.safe_load((BASE / "manifest.yaml").read_text(encoding="utf-8"))
    draft = TaskDraft(
        task_id="conformance-cpp-top-words",
        primary_language="cpp",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    # The oracle's suite timeout is the bound every recorded test plan uses; the timeout scenario
    # relies on it being shorter than the supervisor's own ceiling so the guest deadline wins.
    inventory = {**view.inventory, "suite_timeout_seconds": SUITE_TIMEOUT}
    view = view.model_copy(update={"inventory": inventory})
    return view, files


def _sources(
    plugin: CppLanguagePlugin, view: Any, files: dict[str, bytes], candidate: bytes
) -> dict[str, dict[str, bytes]]:
    """Stage the workspace the way a real run would: candidate, overlay, then trusted config."""
    overlay: dict[str, bytes] = {}
    for group in view.inventory["groups"]:
        for relative in group["files"]:
            overlay[f"work/{relative}"] = files[f"hidden/{relative}"]
    config = plugin.trusted_inputs(files, view)
    return {
        "candidate": {
            UNIT: candidate,
            HEADER: files["visible/repo/include/top_words.hpp"],
        },
        "overlay": overlay,
        "config": config,
    }


def _store(name: str, plan: ExecutionPlan, run: Any) -> None:
    target = FIXTURES / name / plan.plan_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
    (target / "execution.json").write_text(run.record.model_dump_json(indent=1), encoding="utf-8")
    for path, data in run.outputs.items():
        (target / path.replace("/", "__")).write_bytes(data)
    print(name, plan.plan_id, run.record.exit_code, run.record.timed_out, sorted(run.outputs))


def _candidate(view: Any) -> Candidate:
    return Candidate.model_validate_json(
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


def _impossible_sanitizer(plan: AnalysisPlan) -> AnalysisPlan:
    """The pinned instrumented plan with a sanitizer flag no runtime can honour.

    ``-fsanitize=vptr`` needs the CFI runtime, which the pinned images do not carry; the plan is
    otherwise the plugin's own, so what gets recorded is a real run of real tooling that refuses.
    """
    argv: list[str] = []
    injected = False
    for token in plan.argv:
        argv.append(token)
        if not injected and token == "-fsanitize=address,undefined":
            argv.append("-fsanitize=vptr")
            injected = True
    assert injected, "the instrumented plan must carry a sanitizer flag to make impossible"
    return plan.model_copy(update={"argv": tuple(argv)})


async def record(name: str, source: str, *, instrumented_argv: bool = False) -> None:
    plugin = CppLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
            ids.performance.reference: ids.performance.digest,
        },
        state_dir=ROOT / ".cache" / "record-cpp-state",
        # The provider's ceiling is 120s, tight enough that a hung plan fails the recording
        # instead of stalling it; the in-guest deadline comes from the oracle's own timeout.
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    view, files = _view(plugin)
    sources = _sources(plugin, view, files, source.encode("utf-8"))
    plans: list[ExecutionPlan] = [plugin.build_plan(view, _candidate(view))]
    plans += [group.plan for group in plugin.test_plan(view).groups]
    context = AnalysisContext(
        task=view,
        candidate_digest="sha256:" + "2" * 64,
        candidate_paths=(UNIT, HEADER),
    )
    for analysis in plugin.analysis_plans(context):
        if instrumented_argv and analysis.analyzer_id in {"asan", "tsan"}:
            analysis = _impossible_sanitizer(analysis)
        plans.append(analysis)
    for plan in plans:
        run = await runner.run(plan, materialize_inputs(plan, sources), stage_id=f"cpp-rec-{name}")
        _store(name, plan, run)


SCENARIOS: dict[str, tuple[str, bool]] = {
    "reference": ("hidden/reference/src/top_words.cpp", False),
    "alternative": ("admission/alternative-sort/src/top_words.cpp", False),
    "compile-error": ("", False),
    "quality-defective": ("admission/quality-defective/src/top_words.cpp", False),
    "ownership-defective": ("admission/ownership-defective/src/top_words.cpp", False),
    "leak-defective": ("admission/leak-defective/src/top_words.cpp", False),
    "race-defective": ("admission/race-defective/src/top_words.cpp", False),
    "timeout": ("admission/timeout-case/src/top_words.cpp", False),
    "unsupported": ("hidden/reference/src/top_words.cpp", True),
}


async def main(names: list[str]) -> int:
    selected = names or list(SCENARIOS)
    unknown = sorted(set(selected) - set(SCENARIOS))
    if unknown:
        print(f"unknown scenarios: {unknown}", file=sys.stderr)
        return 2
    for name in selected:
        relative, instrumented = SCENARIOS[name]
        source = _COMPILE_ERROR if not relative else _read(relative)
        await record(name, source, instrumented_argv=instrumented)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
