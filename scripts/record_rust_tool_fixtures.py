"""Record real Rust plan executions as parser test fixtures (needs the local Docker images).

Runs the Rust plugin's build, test and analysis plans against small candidate crates inside the
sandbox and stores each plan, the supervisor execution record and the declared outputs under
``tests/fixtures/rust_tool_output/<scenario>/<plan>/``. Parser tests replay those recordings
offline, so the parsers are checked against what the pinned toolchain really prints.

    .venv/Scripts/python.exe scripts/record_rust_tool_fixtures.py [scenario ...]
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
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, ExecutionPlan, TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "rust_tool_output"
BASE = ROOT / "plugins" / "languages" / "rust" / "fixtures" / "top-words"

# Candidate sources are recorded as authored here; each scenario targets one behaviour of the
# parsers. The functional body is the reference's so tests pass unless a scenario says otherwise.
REFERENCE = (BASE / "hidden/reference/src/lib.rs").read_text(encoding="utf-8")
STACK_HELPER = "".join(
    line + chr(10)
    for line in (
        "fn recurse(n: u64) -> u64 {",
        "    let pad = [n as u8; 1024];",
        "    std::hint::black_box(&pad);",
        "    recurse(n + 1) + pad[0] as u64",
        "}",
        "",
    )
)
FOREIGN_DECL = "".join(
    line + chr(10)
    for line in (
        'extern "C" {',
        "    fn getppid() -> i32;",
        "}",
        "",
    )
)

SCENARIOS: dict[str, str] = {
    "reference": REFERENCE,
    "faulty-ties": (BASE / "admission/faulty-ties/src/lib.rs").read_text(encoding="utf-8"),
    "alternative": (BASE / "admission/alternative-btree/src/lib.rs").read_text(encoding="utf-8"),
    "defects": (BASE / "admission/quality-defective/src/lib.rs").read_text(encoding="utf-8"),
    "timeout": (BASE / "admission/timeout-case/src/lib.rs").read_text(encoding="utf-8"),
    "compile-error": REFERENCE.replace("ranked.truncate(k);", "ranked.truncate(k)"),
    "panics": REFERENCE.replace(
        "    ranked.truncate(k);",
        '    if k == 0 {\n        panic!("k must be positive");\n    }\n    ranked.truncate(k);',
    ),
    "stack-overflow": REFERENCE.replace(
        "pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {",
        STACK_HELPER + "pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {\n"
        "    if text.is_empty() {\n        recurse(0);\n    }",
    ),
    "ub": REFERENCE.replace(
        "pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {",
        "pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {\n"
        "    if text.is_empty() {\n"
        "        let probe = [1u8, 2u8];\n"
        "        // Reads one element past the end of the array: undefined behaviour.\n"
        "        let _ = unsafe { *probe.as_ptr().add(2) };\n"
        "    }",
    ),
    "unsupported": REFERENCE.replace(
        "pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {",
        FOREIGN_DECL + "pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {\n"
        "    // SAFETY: getppid has no preconditions.\n"
        "    let _pid = unsafe { getppid() };",
    ),
}


def _view(plugin: RustLanguagePlugin, *, miri: str, required: list[str]):  # type: ignore[no-untyped-def]
    files = {
        p.relative_to(BASE).as_posix(): p.read_bytes()
        for area in ("visible", "hidden", "admission")
        for p in (BASE / area).rglob("*")
        if p.is_file()
    }
    manifest = yaml.safe_load((BASE / "manifest.yaml").read_text(encoding="utf-8"))
    draft = TaskDraft(
        task_id="conformance-rust-top-words",
        primary_language="rust",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    quality = {**view.quality, "miri": miri, "required_analyzers": required}
    return view.model_copy(
        update={"quality": quality, "required_analyzers": tuple(required)}
    ), files


def _sources(files: dict[str, bytes], view, candidate: bytes) -> dict[str, dict[str, bytes]]:  # type: ignore[no-untyped-def]
    overlay = {
        f"work/{p.removeprefix('hidden/')}": data
        for p, data in files.items()
        if p.startswith("hidden/tests/")
    }
    config = {f"work/{path}": files[f"visible/repo/{path}"] for path in view.quality["crate_files"]}
    return {"candidate": {"src/lib.rs": candidate}, "overlay": overlay, "config": config}


def _store(name: str, plan, run) -> None:  # type: ignore[no-untyped-def]
    target = FIXTURES / name / plan.plan_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
    (target / "execution.json").write_text(run.record.model_dump_json(indent=1), encoding="utf-8")
    for path, data in run.outputs.items():
        (target / path.replace("/", "__")).write_bytes(data)
    print(name, plan.plan_id, run.record.exit_code, run.record.timed_out, sorted(run.outputs))


async def record(name: str, source: str) -> None:
    plugin = RustLanguagePlugin()
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
    view, files = _view(plugin, miri="optional", required=["clippy", "context"])
    sources = _sources(files, view, source.encode("utf-8"))
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
        task=view, candidate_digest="sha256:" + "2" * 64, candidate_paths=("src/lib.rs",)
    )
    plans += plugin.analysis_plans(context)
    for plan in plans:
        run = await runner.run(plan, materialize_inputs(plan, sources), stage_id=f"rec-{name}")
        _store(name, plan, run)


async def main(names: list[str]) -> None:
    for name in names or list(SCENARIOS):
        await record(name, SCENARIOS[name])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
    sys.exit(0)
