"""Build, test, analysis and performance plans: typed argument vectors, never shell strings.

Every plan runs Cargo through ``pcb_rust_run.py``, which captures stdout/stderr to files, enforces
the deadline from inside the guest (so a hung test leaves partial output naming the case in
flight) and removes the build directory. The crate lives under ``work/`` so candidate files,
trusted crate scaffolding (``config``) and hidden tests (``overlay``) are separate, digest-checked
inputs.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

from polycodebench_core.models import Candidate, ScoreDimension
from polycodebench_plugins_api import (
    AnalysisContext,
    AnalysisPlan,
    BuildPlan,
    ExecutionPlan,
    ExitSemantics,
    FrozenTask,
    PerformancePlan,
    PlanInput,
    PlanOutput,
    ResourcePolicy,
    TestGroupPlan,
    TestPlan,
    WorkloadSpec,
)
from polycodebench_plugins_api.contracts import ToolIdentity

from polycodebench_lang_rust.identities import GUEST_ROOT, ImageIdentities
from polycodebench_lang_rust.taskspec import oracle_from_mapping, quality_from_mapping

GUEST = f"{GUEST_ROOT}/guest"
RULES = f"{GUEST_ROOT}/rules"
MIRI_TOOLCHAIN = "nightly-2026-09-30"
MANIFEST = "work/Cargo.toml"
TARGET_DIR = "/workspace/target"
BASE_ENV = {
    "CARGO_TARGET_DIR": TARGET_DIR,
    "CARGO_INCREMENTAL": "0",
    "RUST_BACKTRACE": "0",
}
MIB = 1024**2
# Seconds the in-guest runner stops before the supervisor's own deadline, so the runner (which can
# keep partial output) always wins the race against the outer kill.
RUNNER_MARGIN = 3
# The local Docker driver caps one exec at 120s (the supervisor adds an 8s grace to the plan
# deadline), so no plan may declare more than this.
MAX_PLAN_SECONDS = 110
# 126/127 come from the runner: tool not executable / not found (missing dependency). Exit 101 is
# cargo's own "the build or a test failed".
TOOL_ERRORS = (2, 126, 127)
CARGO_FAILED = 101


def resources(timeout: int, *, memory_mib: int = 2048) -> ResourcePolicy:
    return ResourcePolicy(
        cpu_millis=2000,
        memory_bytes=memory_mib * MIB,
        pids_limit=512,
        disk_bytes=512 * MIB,
        timeout_seconds=timeout,
        max_output_bytes=MIB,
        executable_workspace=True,
    )


@lru_cache(maxsize=1)
def selected_lints() -> tuple[str, ...]:
    """Clippy lints the profile enables, from ``rules/clippy.toml`` (a list per lint group)."""
    path = Path(__file__).resolve().parent / "rules" / "clippy.toml"
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for group in ("correctness", "suspicious", "style", "pedantic", "nursery", "cargo"):
        for name in document.get(group, []):
            names.append(name if name.startswith("clippy::") else f"clippy::{name}")
    return tuple(dict.fromkeys(names))


def _candidate_inputs(paths: tuple[str, ...]) -> tuple[PlanInput, ...]:
    return tuple(PlanInput(path=f"work/{p}", role="candidate") for p in paths)


def _crate_inputs(crate_files: Mapping[str, str]) -> tuple[PlanInput, ...]:
    return tuple(
        PlanInput(path=f"work/{path}", role="config", digest=digest)
        for path, digest in sorted(crate_files.items())
    )


def _run(name: str, deadline: int, *command: str, merge: bool = False) -> tuple[str, ...]:
    """The runner invocation for one Cargo command (``name`` is the output prefix)."""
    return (
        "python",
        "-B",
        f"{GUEST}/pcb_rust_run.py",
        "--name",
        f"out/{name}",
        "--deadline",
        str(max(1, deadline - RUNNER_MARGIN)),
        "--cleanup",
        TARGET_DIR,
        *(("--merge",) if merge else ()),
        "--",
        *command,
    )


def _captured(name: str, stdout_format: Literal["json", "jsonl", "text"]) -> tuple[PlanOutput, ...]:
    return (
        PlanOutput(path=f"out/{name}.out", format=stdout_format, required=False, max_bytes=4 * MIB),
        PlanOutput(path=f"out/{name}.err", format="text", required=False, max_bytes=4 * MIB),
        PlanOutput(path=f"out/{name}.run.json", format="json"),
    )


def _base(
    ids: ImageIdentities,
    *,
    plan_id: str,
    argv: tuple[str, ...],
    tool: ToolIdentity,
    parser_id: str,
    inputs: tuple[PlanInput, ...],
    outputs: tuple[PlanOutput, ...],
    scope: tuple[str, ...],
    timeout: int,
    semantics: ExitSemantics,
    environment: Mapping[str, str] | None = None,
    recipe: Literal["runtime", "evaluator", "performance"] = "evaluator",
) -> dict[str, object]:
    image = ids.images[recipe]
    return {
        "plan_id": plan_id,
        "image": image.reference,
        "image_digest": image.digest,
        "argv": argv,
        "working_directory": ".",
        "environment": {**BASE_ENV, **(environment or {})},
        "inputs": inputs,
        "outputs": outputs,
        "resources": resources(timeout),
        "exit_semantics": semantics,
        "tool": tool,
        "scope": scope,
        "parser_id": parser_id,
    }


def _cargo(*args: str) -> tuple[str, ...]:
    return ("cargo", *args, "--offline", "--locked", "--manifest-path", MANIFEST)


def build_plan(ids: ImageIdentities, task: FrozenTask, candidate: Candidate) -> BuildPlan:
    paths = tuple(p for p in task.required_outputs if p.endswith(".rs"))
    if not paths:
        raise ValueError("a Rust task needs at least one required .rs output")
    quality = quality_from_mapping(task.quality)
    timeout = 100
    fields = _base(
        ids,
        plan_id="rust.build",
        argv=_run("build", timeout, *_cargo("build"), "--message-format=json"),
        tool=ids.tool("cargo", recipe="runtime"),
        parser_id="rust-build",
        recipe="runtime",
        inputs=(*_candidate_inputs(paths), *_crate_inputs(quality.crate_files)),
        outputs=_captured("build", "jsonl"),
        scope=paths,
        timeout=timeout,
        semantics=ExitSemantics(success=(0,), findings=(CARGO_FAILED,), error=TOOL_ERRORS),
    )
    return BuildPlan(**fields)  # type: ignore[arg-type]


def make_test_plan(ids: ImageIdentities, task: FrozenTask) -> TestPlan:
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    groups = []
    for group in oracle.groups:
        targets = [f.removeprefix("tests/").removesuffix(".rs") for f in group.files]
        selectors = [part for target in targets for part in ("--test", target)]
        timeout = oracle.suite_timeout_seconds
        fields = _base(
            ids,
            plan_id=f"rust.test.{group.group_id}",
            argv=_run(
                group.group_id,
                timeout,
                *_cargo("test"),
                "--no-fail-fast",
                *selectors,
                "--",
                "--test-threads=1",
                merge=True,
            ),
            tool=ids.tool("cargo", recipe="runtime"),
            parser_id="rust-libtest",
            recipe="runtime",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                *_crate_inputs(quality.crate_files),
                *(PlanInput(path=f"work/{p}", role="overlay") for p in group.files),
            ),
            outputs=_captured(group.group_id, "text"),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(CARGO_FAILED,), error=TOOL_ERRORS),
        )
        repetitions = 3 if group.classification == "quality_only" else 1
        groups.append(
            TestGroupPlan(
                group_id=group.group_id,
                required=group.required,
                repetitions=repetitions,
                plan=ExecutionPlan(**fields),  # type: ignore[arg-type]
            )
        )
    return TestPlan(groups=tuple(groups), expected_inventory_digest=str(oracle.inventory_digest()))


def _analysis(
    ids: ImageIdentities,
    context: AnalysisContext,
    *,
    analyzer: str,
    tool: str,
    argv: tuple[str, ...],
    outputs: tuple[PlanOutput, ...],
    semantics: ExitSemantics,
    output_schema: str,
    environment: Mapping[str, str] | None = None,
    timeout: int = 90,
    extra_inputs: tuple[PlanInput, ...] = (),
    ownership: Mapping[str, ScoreDimension | None] | None = None,
    needs_lock: bool = False,
) -> AnalysisPlan:
    task = context.task
    quality = quality_from_mapping(task.quality)
    fields = _base(
        ids,
        plan_id=f"rust.analysis.{analyzer}",
        argv=argv,
        tool=ids.tool(tool, lock_digest=quality.cargo_lock_digest if needs_lock else None),
        parser_id=f"rust-{analyzer}",
        inputs=(
            *_candidate_inputs(context.candidate_paths),
            *_crate_inputs(quality.crate_files),
            *extra_inputs,
        ),
        outputs=outputs,
        scope=tuple(context.candidate_paths),
        timeout=timeout,
        semantics=semantics,
        environment=environment,
    )
    return AnalysisPlan(
        **fields,  # type: ignore[arg-type]
        language_id="rust",
        analyzer_id=analyzer,
        candidate_digest=context.candidate_digest,
        required=analyzer in task.required_analyzers,
        output_schema=output_schema,
        evidence_ownership=dict(ownership or {}),
        baseline_reusable=True,
    )


def _miri_overlay(task: FrozenTask) -> tuple[tuple[str, ...], tuple[PlanInput, ...]]:
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    wanted = set(quality.miri_groups) or {g.group_id for g in oracle.groups if g.required}
    files = tuple(dict.fromkeys(f for g in oracle.groups if g.group_id in wanted for f in g.files))
    selectors = tuple(
        part for f in files for part in ("--test", f.removeprefix("tests/").removesuffix(".rs"))
    )
    return selectors, tuple(PlanInput(path=f"work/{p}", role="overlay") for p in files)


def analysis_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    task = context.task
    quality = quality_from_mapping(task.quality)
    candidate_sources = tuple(f"work/{p}" for p in context.candidate_paths if p.endswith(".rs"))
    findings_zero = ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS)
    clippy_timeout = 110
    flags = [part for lint in selected_lints() for part in ("-W", lint)]
    plans = [
        _analysis(
            ids,
            context,
            analyzer="clippy",
            tool="clippy",
            argv=_run(
                "clippy",
                clippy_timeout,
                *_cargo("clippy"),
                "--message-format=json",
                "--",
                "-A",
                "clippy::all",
                *flags,
            ),
            outputs=_captured("clippy", "jsonl"),
            # Clippy exits 0 with warnings, so findings never use the exit code: a non-zero exit
            # is a compile failure, and the scan is then missing rather than clean.
            semantics=ExitSemantics(success=(0,), error=(CARGO_FAILED, *TOOL_ERRORS)),
            output_schema="cargo-clippy-json-v1",
            timeout=clippy_timeout,
            needs_lock=True,
        ),
        _analysis(
            ids,
            context,
            analyzer="context",
            tool="context-scan",
            argv=(
                "python",
                "-B",
                f"{GUEST}/pcb_rust_scan.py",
                "--root",
                "/workspace",
                "--output",
                "out/context.json",
                "--opportunities",
                ",".join(quality.opportunity_tags),
                *candidate_sources,
            ),
            outputs=(PlanOutput(path="out/context.json", format="json"),),
            semantics=findings_zero,
            output_schema="pcb-rust-scan-v1",
            timeout=60,
        ),
    ]
    if quality.miri_runs:
        selectors, overlay = _miri_overlay(task)
        miri_timeout = 110
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="miri",
                tool="miri",
                argv=_run(
                    "miri",
                    miri_timeout,
                    "cargo",
                    f"+{MIRI_TOOLCHAIN}",
                    "miri",
                    "test",
                    "--offline",
                    "--locked",
                    "--manifest-path",
                    MANIFEST,
                    *selectors,
                    merge=True,
                ),
                outputs=_captured("miri", "text"),
                # Miri exits non-zero for UB, unsupported operations and compile errors alike; the
                # exit status is never read as a verdict (the diagnostic text is).
                semantics=ExitSemantics(
                    success=(0,), findings=(1, CARGO_FAILED), error=TOOL_ERRORS
                ),
                output_schema="miri-text-v1",
                # Use the sysroot baked into the image as-is: the sandbox is read-only and its only
                # writable locations cannot execute binaries, so Miri cannot rebuild one.
                environment={"MIRI_SYSROOT": f"{GUEST_ROOT}/cache/miri"},
                timeout=miri_timeout,
                extra_inputs=overlay,
                ownership={"candidate-undefined-behaviour": ScoreDimension.ROBUSTNESS},
                needs_lock=True,
            )
        )
    if task.dependency_inventory or "dependency" in task.required_analyzers:
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="dependency",
                tool="dependency-check",
                argv=(
                    "python",
                    "-B",
                    f"{GUEST}/pcb_lock_audit.py",
                    "--lock",
                    "work/Cargo.lock",
                    "--advisories",
                    f"{RULES}/advisories/snapshot.json",
                    "--output",
                    "out/dependency.json",
                ),
                outputs=(PlanOutput(path="out/dependency.json", format="json"),),
                semantics=findings_zero,
                output_schema="pcb-lock-audit-v1",
                timeout=30,
                ownership={"canonical-security-issue": ScoreDimension.SECURITY},
                needs_lock=True,
            )
        )
    return plans


def performance_plan(ids: ImageIdentities, task: FrozenTask) -> PerformancePlan | None:
    quality = quality_from_mapping(task.quality)
    declared = quality.performance
    if declared is None:
        return None
    # The workload is a task-supplied Rust program (an overlay at src/bin/pcb_workload.rs) built
    # with the performance image's baked release profile; it prints one JSON line of measurements.
    timeout = declared.hard_timeout_seconds
    iteration = ExecutionPlan(
        **_base(  # type: ignore[arg-type]
            ids,
            plan_id="rust.performance.iteration",
            argv=_run(
                "perf",
                timeout,
                *_cargo("run", "--release"),
                "--bin",
                "pcb_workload",
                "--",
                "--scale",
                "{scale}",
                "--seed",
                "{seed}",
            ),
            tool=ids.tool("cargo", recipe="performance"),
            parser_id="rust-perf",
            recipe="performance",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                *_crate_inputs(quality.crate_files),
                PlanInput(path="work/src/bin/pcb_workload.rs", role="overlay"),
            ),
            outputs=_captured("perf", "text"),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(CARGO_FAILED,), error=TOOL_ERRORS),
        )
    )
    return PerformancePlan(
        plan_id="rust.performance",
        reference_digest=None,
        language_runtime=f"rustc {ids.performance.rustc}",
        hardware_class="local-development",
        workloads=tuple(
            WorkloadSpec(
                workload_id=w.workload_id,
                scale=w.scale,
                weight_bp=w.weight_bp,
                input_seed=w.input_seed,
            )
            for w in declared.workloads
        ),
        warmup_iterations=declared.warmup_iterations,
        measured_iterations=declared.measured_iterations,
        metric_ids=("elapsed_ns", "peak_rss_kb"),
        iteration_plan=iteration,
        threads=1,
    )
