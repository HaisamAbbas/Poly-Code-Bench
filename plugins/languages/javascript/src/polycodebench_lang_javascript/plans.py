"""JavaScript/TypeScript plans: build, test, analysis and performance work (Prompt 19, PCB-19-2).

Every plan is a typed argument array with an image digest, a resource policy, explicit inputs and
declared outputs. Nothing here executes anything: the supervisor runs plans and the parsers read
the recorded bytes back.

Three rules shape this module:

* **No plan may install anything.** There is no ``npm install`` and no ``npx`` in any argv. The
  dependency closure is baked into the image by ``scripts/fetch_js_components.py``, and each image
  ships an npmrc with ``offline=true`` and a registry that resolves nowhere, so an accidental
  install inside a scored run fails loudly instead of quietly downloading a package the recorded
  identity does not know about.
* **The analyzers a candidate will be judged by are absent from the image it solves in.** Lint and
  advisory plans run in the ``evaluator`` recipe; ``runtime`` and ``performance`` carry no eslint,
  and the JavaScript images carry no tsc either.
* **A plan is only declared when the frozen task asks for it.** TypeScript checking is planned only
  when the task requires typing, the dependency audit only when the task declares a dependency
  inventory, and the performance plan only when the task declares a workload. JavaScript is never
  given a TypeScript plan, so a JavaScript candidate cannot be charged for lacking types.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    ToolIdentity,
    WorkloadSpec,
)

from polycodebench_lang_javascript.identities import GUEST_ROOT, ImageIdentities, Recipe
from polycodebench_lang_javascript.taskspec import oracle_from_mapping, quality_from_mapping

GUEST = f"{GUEST_ROOT}/guest"
RULES = f"{GUEST_ROOT}/rules"
MIB = 1024 * 1024
RUNNER = f"{GUEST}/pcb_js_run.py"
CAPTURE = f"{GUEST}/pcb_js_capture.py"
SCAN = f"{GUEST}/pcb_js_scan.py"
AUDIT = f"{GUEST}/pcb_npm_audit.py"
VITEST_REPORT = f"{GUEST}/pcb_vitest_report.py"
# Every dependency binary is addressed by absolute path. The images put them on PATH, but a plan
# that names a bare `vitest` could be resolved against whatever PATH the guest happens to have, and
# a resolved-differently analyzer is not the tool the recorded identity describes.
MODULES = "/opt/pcb/js/node_modules"
ESLINT = f"{MODULES}/eslint/bin/eslint.js"
TSC = f"{MODULES}/typescript/bin/tsc"
VITEST = f"{MODULES}/vitest/vitest.mjs"
# Exit codes a tool uses to mean "I could not run", never "there is nothing to report". A scanner
# that fails is recorded ``missing``; it is never recorded clean (D-10-06).
TOOL_ERRORS = (2, 126, 127)
# The in-guest runner stops this many seconds before the supervisor's deadline, so a timeout is the
# supervisor's decision to make from a real exit status and not a race between two deadlines.
RUNNER_MARGIN = 3
MAX_PLAN_SECONDS = 110
BASE_ENV = {
    "NODE_ENV": "production",
    "CI": "true",
    "NO_COLOR": "1",
    "FORCE_COLOR": "0",
    "npm_config_offline": "true",
    "npm_config_audit": "false",
    "npm_config_fund": "false",
    "npm_config_update_notifier": "false",
}

_ANALYZER_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "eslint": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "typescript": (ScoreDimension.CODE_QUALITY,),
    "context": (
        ScoreDimension.SECURITY,
        ScoreDimension.CODE_QUALITY,
        ScoreDimension.IDIOMATIC,
        ScoreDimension.ROBUSTNESS,
    ),
    "dependency": (ScoreDimension.SECURITY,),
}


def analyzer_dimensions(analyzer_id: str) -> tuple[ScoreDimension, ...]:
    """The composite dimensions an analyzer can feed, as its capability declaration states."""
    return _ANALYZER_DIMENSIONS[analyzer_id]


def resources(timeout: int, *, memory_mib: int = 2048) -> ResourcePolicy:
    """Bounded resources for one plan.

    The workspace is executable because a Node build and a test runner both write into it (the
    vitest cache, ``node_modules/.vite``), and a read-only root would make every plan fail for a
    reason that has nothing to do with the candidate.
    """
    return ResourcePolicy(
        cpu_millis=2000,
        memory_bytes=memory_mib * MIB,
        pids_limit=512,
        disk_bytes=512 * MIB,
        timeout_seconds=timeout,
        max_output_bytes=MIB,
        executable_workspace=True,
    )


def _candidate_inputs(paths: tuple[str, ...]) -> tuple[PlanInput, ...]:
    return tuple(PlanInput(path=f"work/{p}", role="candidate") for p in paths)


def _config_inputs(files: Mapping[str, str]) -> tuple[PlanInput, ...]:
    """Trusted scaffolding (``package.json``, ``tsconfig.json``, ...) as digest-checked inputs."""
    return tuple(
        PlanInput(path=f"work/{path}", role="config", digest=digest)
        for path, digest in sorted(files.items())
    )


def _overlay_inputs(paths: tuple[str, ...]) -> tuple[PlanInput, ...]:
    return tuple(PlanInput(path=f"work/{path}", role="overlay") for path in paths)


def _captured(name: str, stdout_format: Literal["json", "jsonl", "text"]) -> tuple[PlanOutput, ...]:
    return (
        PlanOutput(path=f"out/{name}.out", format=stdout_format, required=False, max_bytes=4 * MIB),
        PlanOutput(path=f"out/{name}.err", format="text", required=False, max_bytes=4 * MIB),
        PlanOutput(path=f"out/{name}.run.json", format="json"),
    )


def _run(name: str, deadline: int, *command: str, merge: bool = False) -> tuple[str, ...]:
    """Wrap a command in the in-guest bounded runner (``name`` is the output prefix)."""
    return (
        "python",
        "-B",
        RUNNER,
        "--name",
        f"out/{name}",
        "--deadline",
        str(max(1, deadline - RUNNER_MARGIN)),
        *(("--merge",) if merge else ()),
        "--",
        *command,
    )


def _captured_argv(name: str, *command: str) -> tuple[str, ...]:
    """Run a tool through the capture helper, which splits stdout/stderr into declared outputs."""
    return (
        "python",
        "-B",
        CAPTURE,
        "--name",
        f"out/{name}",
        "--stdout",
        f"out/{name}.out",
        "--stderr",
        f"out/{name}.err",
        "--",
        *command,
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
    recipe: Recipe = "evaluator",
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


def _sources(task: FrozenTask) -> tuple[str, ...]:
    """The candidate sources this task is about, by the suffixes both identities accept."""
    wanted = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx")
    sources = tuple(p for p in task.required_outputs if p.endswith(wanted))
    if not sources:
        raise ValueError("a JavaScript/TypeScript task needs at least one required source output")
    return sources


def _is_typescript(paths: tuple[str, ...]) -> bool:
    return any(p.endswith((".ts", ".mts", ".cts", ".tsx")) for p in paths)


def build_plan(ids: ImageIdentities, task: FrozenTask, candidate: Candidate) -> BuildPlan:
    """The candidate's build.

    JavaScript's build is a syntax check: Node parses the file, so a syntax error is a candidate
    failure and not an incomplete run. TypeScript's build is the pinned compiler over the task's own
    ``tsconfig.json``; running it here and not only as an analyzer is what makes a type-defective
    submission a *correctness* failure rather than a quality deduction.
    """
    quality = quality_from_mapping(task.quality)
    sources = _sources(task)
    inputs = (*_candidate_inputs(sources), *_config_inputs(quality.config_files))
    timeout = 110 if _is_typescript(sources) else 60
    argv = (
        _run("build", timeout, "node", TSC, "--project", "work/tsconfig.json", "--pretty", "false")
        if _is_typescript(sources)
        else _captured_argv("build", "node", "--check", *sources)
    )
    fields = _base(
        ids,
        plan_id="javascript.build",
        argv=argv,
        tool=ids.tool("tsc" if _is_typescript(sources) else "node", recipe="runtime"),
        parser_id="javascript-build",
        recipe="runtime",
        inputs=inputs,
        outputs=_captured("build", "text"),
        scope=sources,
        timeout=timeout,
        semantics=ExitSemantics(success=(0,), findings=(), error=TOOL_ERRORS),
    )
    del candidate  # the candidate's bytes are the plan inputs; nothing else about it is needed
    return BuildPlan(**fields)  # type: ignore[arg-type]


def make_test_plan(ids: ImageIdentities, task: FrozenTask) -> TestPlan:
    """One vitest invocation per oracle group, under the in-guest bounded runner.

    ``quality_only`` groups repeat: an unstable or flaky case is a candidate defect, and a single
    sample cannot tell one from the other.

    The vitest JSON report is converted by a declared postprocess step, because the runner's own
    output format is not the shared test-report contract and the host parser must not have to
    understand a third reporter's schema.
    """
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    sources = _sources(task)
    timeout = min(oracle.suite_timeout_seconds, MAX_PLAN_SECONDS)
    groups: list[TestGroupPlan] = []
    for group in oracle.groups:
        raw = f"out/test.{group.group_id}.raw.json"
        argv = _run(
            "test." + group.group_id,
            timeout,
            "node",
            VITEST,
            "run",
            "--reporter=json",
            f"--outputFile={raw}",
            "--root",
            "work",
            "--testTimeout",
            str(oracle.case_timeout_seconds * 1000),
            "--passWithNoTests=false",
            *[f"work/{rel}" for rel in group.files],
            merge=True,
        )
        fields = _base(
            ids,
            plan_id=f"javascript.test.{group.group_id}",
            argv=argv,
            tool=ids.tool(quality.test_runner, recipe="runtime"),
            parser_id="javascript-vitest",
            recipe="runtime",
            inputs=(
                *_candidate_inputs(sources),
                *_config_inputs(quality.config_files),
                *_overlay_inputs(tuple(group.files)),
            ),
            outputs=(
                *_captured(f"test.{group.group_id}", "text"),
                PlanOutput(path=raw, format="json", required=False),
                PlanOutput(path=f"out/test.{group.group_id}.jsonl", format="jsonl"),
            ),
            scope=sources,
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS),
        )
        groups.append(
            TestGroupPlan(
                group_id=group.group_id,
                required=group.required,
                repetitions=3 if group.classification == "quality_only" else 1,
                plan=ExecutionPlan(**fields),  # type: ignore[arg-type]
            )
        )
    del quality  # the quality plan is read above through `quality_from_mapping`; keep no alias
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
    lock_digest: str | None = None,
) -> AnalysisPlan:
    task = context.task
    quality = quality_from_mapping(task.quality)
    sources = _sources(task)
    fields = _base(
        ids,
        plan_id=f"javascript.analysis.{analyzer}",
        argv=argv,
        tool=ids.tool(tool, lock_digest=lock_digest),
        parser_id=f"javascript-{analyzer}",
        inputs=(
            *_candidate_inputs(sources),
            *_config_inputs(quality.config_files),
            *extra_inputs,
        ),
        outputs=outputs,
        scope=sources,
        timeout=timeout,
        semantics=semantics,
        environment=environment,
    )
    return AnalysisPlan(
        **fields,  # type: ignore[arg-type]
        analyzer_id=analyzer,
        language_id=task.primary_language,
        candidate_digest=context.candidate_digest,
        required=analyzer in task.required_analyzers,
        output_schema=output_schema,
        evidence_ownership=dict(ownership or {}),
        baseline_reusable=True,
    )


def analysis_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    task = context.task
    quality = quality_from_mapping(task.quality)
    sources = _sources(task)
    work = tuple(f"work/{path}" for path in sources)
    findings_zero = ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS)
    plans = [
        _analysis(
            ids,
            context,
            analyzer="eslint",
            tool="eslint",
            argv=_captured_argv(
                "eslint",
                "node",
                ESLINT,
                "--config",
                f"{RULES}/eslint.config.mjs",
                "--format",
                "json",
                "--no-error-on-unmatched-pattern",
                *work,
            ),
            outputs=_captured("eslint", "json"),
            semantics=findings_zero,
            output_schema="pcb-eslint-v1",
            timeout=90,
        ),
        _analysis(
            ids,
            context,
            analyzer="context",
            tool="context",
            argv=_run(
                "analysis.context",
                60,
                "python",
                "-B",
                SCAN,
                "--root",
                "/workspace",
                "--output",
                "out/context.json",
                *work,
            ),
            outputs=_captured("analysis.context", "text"),
            semantics=ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS),
            output_schema="pcb-js-scan-v1",
            timeout=90,
        ),
    ]
    if quality.typing_runs and ids.ships("tsc"):
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="typescript",
                tool="tsc",
                argv=_run(
                    "analysis.typescript",
                    110,
                    "node",
                    TSC,
                    "--project",
                    "work/tsconfig.json",
                    "--pretty",
                    "false",
                ),
                outputs=_captured("analysis.typescript", "text"),
                # A type error is a diagnostic finding, not a tool failure: tsc exits 2 for a type
                # error and tsc emits no status of its own, so the declared output is the authority.
                semantics=ExitSemantics(success=(0,), findings=(), error=TOOL_ERRORS),
                output_schema="pcb-tsc-v1",
                timeout=110,
            )
        )
    if "dependency" in quality.required_analyzers:
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="dependency",
                tool="context",
                argv=_run(
                    "analysis.dependency",
                    45,
                    "python",
                    "-B",
                    AUDIT,
                    "--lock",
                    "work/package-lock.json",
                    "--advisories",
                    f"{RULES}/advisories/snapshot.json",
                    "--output",
                    "out/dependency.json",
                ),
                outputs=_captured("analysis.dependency", "text"),
                semantics=ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS),
                output_schema="pcb-npm-audit-v1",
                lock_digest=quality.package_lock_digest,
                timeout=60,
            )
        )
    return plans


def performance_plan(ids: ImageIdentities, task: FrozenTask) -> PerformancePlan | None:
    """A measurement plan only when the frozen task declares a workload.

    A task with no performance policy gets no plan rather than a plan that measures nothing: an
    empty measurement is not evidence, and the efficiency dimension stays not_applicable (D-11-18).
    """
    quality = quality_from_mapping(task.quality)
    if quality.performance is None:
        return None
    sources = _sources(task)
    workload = quality.performance.workload_file
    fields = _base(
        ids,
        plan_id="javascript.performance",
        argv=(
            "node",
            f"work/{workload.removeprefix('hidden/')}",
        ),
        tool=ids.tool("node", recipe="performance"),
        parser_id="javascript-performance",
        recipe="performance",
        inputs=(*_candidate_inputs(sources), PlanInput(path=f"work/{workload}", role="overlay")),
        outputs=(PlanOutput(path="out/performance.json", format="json"),),
        scope=sources,
        timeout=quality.performance.hard_timeout_seconds,
        semantics=ExitSemantics(success=(0,), findings=(), error=TOOL_ERRORS),
    )
    return PerformancePlan(
        plan_id="javascript.performance",
        reference_digest=None,
        language_runtime=ids.performance.node,
        hardware_class="local-development",
        workloads=tuple(
            WorkloadSpec(
                workload_id=item.workload_id,
                scale=item.scale,
                weight_bp=item.weight_bp,
                input_seed=item.input_seed,
            )
            for item in quality.performance.workloads
        ),
        warmup_iterations=quality.performance.warmup_iterations,
        measured_iterations=quality.performance.measured_iterations,
        metric_ids=("elapsed_ns", "peak_rss_kb"),
        iteration_plan=ExecutionPlan(**fields),  # type: ignore[arg-type]
        threads=1,
    )