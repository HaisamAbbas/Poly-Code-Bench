"""Build, test, analysis and performance plans for C: typed argument vectors, never shell strings.

Every plan runs through a Python guest driver rather than a shell:

* ``pcb_c_build.py`` compiles and links one target, normalizes the compiler's diagnostics, and
  optionally runs the binary (plain, or under Valgrind);
* ``pcb_c_run.py`` wraps any command with capture, an in-guest deadline and build-directory
  cleanup;
* the analyzer plans invoke ``clang-tidy`` / ``cppcheck`` directly, with their frozen
  configurations.

Sources live under ``work/`` so candidate files, trusted scaffolding (headers and the test
harness) and hidden test groups are separate, role-checked inputs. The one rule the C
toolchain does not let be implicit is *which flags* ran: every plan records its
``flags_digest`` in the tool identity, so a warning from a different flag set is not
comparable evidence.
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
    WorkloadSpec,
)
from polycodebench_plugins_api.contracts import ToolIdentity

from polycodebench_lang_c.identities import GUEST_ROOT, ImageIdentities, Recipe
from polycodebench_lang_c.recipe import (
    BuildRecipe,
    clang_tidy_arguments,
    cppcheck_arguments,
    resolve_recipe,
)
from polycodebench_lang_c.taskspec import (
    LANE_ANALYZER,
    SanitizerPolicy,
    oracle_from_mapping,
    quality_from_mapping,
)

GUEST = f"{GUEST_ROOT}/guest"
BUILD_DIR = "/workspace/build"
MIB = 1024**2
# Seconds the in-guest runner stops before the supervisor's own deadline.
RUNNER_MARGIN = 3
MAX_PLAN_SECONDS = 110
#: The build driver's own exit vocabulary (see guest/pcb_c_build.py). The split matters: a compile
#: error is something the candidate did, while an *incomplete* run is the harness failing to run at
#: all, and reporting one as the other would either fail a candidate for infrastructure or excuse a
#: real build error as a harness problem.
BUILD_OK = 0
BUILD_FINDINGS = (1, 2)  # warnings promoted to errors, then compile/link error
BUILD_INCOMPLETE = 3  # the driver could not start a compiler or write its output
BUILD_ERRORS = (*BUILD_FINDINGS, BUILD_INCOMPLETE)
#: 126/127 come from the runner: tool not executable / not found.
TOOL_ERRORS = (BUILD_INCOMPLETE, 126, 127)
#: Valgrind's own ``--error-exitcode``; distinct from a crash so a memcheck finding is not an error.
VALGRIND_FINDINGS = 42


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


def _candidate_inputs(paths: tuple[str, ...]) -> tuple[PlanInput, ...]:
    return tuple(PlanInput(path=f"work/{path}", role="candidate") for path in paths)


def _scaffold_inputs(scaffold: Mapping[str, str]) -> tuple[PlanInput, ...]:
    return tuple(
        PlanInput(path=f"work/{path}", role="config", digest=digest)
        for path, digest in sorted(scaffold.items())
    )


def _run(name: str, deadline: int, *command: str, merge: bool = False) -> tuple[str, ...]:
    return (
        "python",
        "-B",
        f"{GUEST}/pcb_c_run.py",
        "--name",
        f"out/{name}",
        "--deadline",
        str(max(1, deadline - RUNNER_MARGIN)),
        "--cleanup",
        BUILD_DIR,
        *(("--merge",) if merge else ()),
        "--",
        *command,
    )


def _captured(name: str, stdout_format: Literal["json", "jsonl", "text"]) -> tuple[PlanOutput, ...]:
    """The three files every plan produces.

    ``.out`` is the *report*: it is required, because a plan whose output did not arrive
    is missing evidence and has to be reported as missing rather than read as a clean scan.
    ``.err`` is supplementary - with ``--merge`` the sanitizers' own text lands in ``.out``
    - so its absence is not a failure.
    """
    return (
        PlanOutput(path=f"out/{name}.out", format=stdout_format, required=True, max_bytes=4 * MIB),
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
    recipe: Recipe = "runtime",
) -> dict[str, object]:
    image = ids.images[recipe]
    return {
        "plan_id": plan_id,
        "image": image.reference,
        "image_digest": image.digest,
        "argv": argv,
        "working_directory": ".",
        "environment": {"LC_ALL": "C", **(environment or {})},
        "inputs": inputs,
        "outputs": outputs,
        "resources": resources(timeout),
        "exit_semantics": semantics,
        "tool": tool,
        "scope": scope,
        "parser_id": parser_id,
    }


def _recipe_for(task: FrozenTask, recipe: Recipe, sanitizer: str | None = None) -> BuildRecipe:
    """The task's frozen warning policy, resolved into one recipe.

    ``werror`` is only reachable when the package recorded a clean baseline, which
    :class:`~polycodebench_lang_c.taskspec.WarningPolicy` already enforces. Re-checking here means a
    plan cannot be built from a hand-written quality block that skipped that rule.
    """
    quality = quality_from_mapping(task.quality)
    return resolve_recipe(
        recipe=recipe,
        standard=quality.c_standard,
        warning_set=quality.warning_policy.warning_set,
        warnings_as_errors=quality.warning_policy.warnings_as_errors,
        sanitizer=sanitizer,
        include_dir="work/include",
    )


def _build_argv(
    recipe: BuildRecipe,
    *,
    name: str,
    binary: str,
    sources: tuple[str, ...],
    extra_includes: tuple[str, ...] = (),
    execute: str | None = None,
    deadline: int = 30,
    archive: bool = False,
    run_args: tuple[str, ...] = (),
) -> tuple[str, ...]:
    argv = [
        "python",
        "-B",
        f"{GUEST}/pcb_c_build.py",
        "--out",
        f"out/{name}.build.json",
        "--build-dir",
        BUILD_DIR,
        "--binary",
        binary,
        "--std",
        recipe.standard,
        "--warning-set",
        recipe.warning_set,
        "--recipe",
        recipe.recipe,
        "--include",
        recipe.include_dir,
    ]
    if recipe.warnings_as_errors:
        argv.append("--warn-error")
    if recipe.sanitizer is not None:
        argv.extend(("--sanitizer", recipe.sanitizer))
    if archive:
        argv.append("--archive")
    if execute is not None:
        argv.extend(("--execute", execute, "--run-deadline", str(deadline)))
        if run_args:
            # One ``--run-arg`` per value: the guest's parser is a repeating-flag walk, so
            # ``("--run-arg", *run_args)`` would hand the second argument to the parser as a bare
            # positional and it would exit 2 before building anything.
            argv.extend(part for argument in run_args for part in ("--run-arg", argument))
    for include in extra_includes:
        argv.extend(("--object-include", include))
    for source in sources:
        argv.extend(("--source", source))
    return tuple(argv)


def build_plan(ids: ImageIdentities, task: FrozenTask, candidate: Candidate) -> BuildPlan:
    """Compile every required output into an archive, with the task's frozen warning policy.

    The product is an archive rather than an executable because C has no library object to
    link: the point of this lane is that every required output *compiles* under the frozen
    flags, and the test lanes are what decide whether it works.

    ``-Werror`` is only present when the package proved the baseline clean under the same flags.
    That is the whole answer to "a blanket -Werror must not silently invalidate an
    otherwise admitted legacy task": the stronger policy is opt-in and its precondition is
    checked twice - once when the task is admitted and again here, so a hand-written
    quality block cannot skip it.
    """
    paths = tuple(p for p in task.required_outputs if p.endswith(".c"))
    if not paths:
        raise ValueError("a C task needs at least one required .c output")
    quality = quality_from_mapping(task.quality)
    recipe = _recipe_for(task, "runtime")
    argv = _run(
        "build",
        100,
        *_build_argv(
            recipe,
            name="build",
            binary=f"{BUILD_DIR}/libtask.a",
            sources=tuple(f"work/{p}" for p in paths),
            archive=True,
        ),
    )
    fields = _base(
        ids,
        plan_id="c.build",
        argv=argv,
        tool=ids.tool("clang", recipe="runtime", flags_digest=recipe.flags_digest()),
        parser_id="c-build",
        inputs=(*_candidate_inputs(paths), *_scaffold_inputs(quality.scaffold_files)),
        outputs=(
            PlanOutput(path="out/build.build.json", format="json"),
            *_captured("build", "text"),
        ),
        scope=paths,
        timeout=100,
        semantics=ExitSemantics(success=(BUILD_OK,), findings=BUILD_FINDINGS, error=TOOL_ERRORS),
        recipe="runtime",
    )
    return BuildPlan(**fields)  # type: ignore[arg-type]


def make_test_plan(ids: ImageIdentities, task: FrozenTask) -> TestPlan:
    """One linked binary per oracle group, built with the release recipe and run by the harness.

    Acceptance groups link the candidate and the group under the *runtime* recipe with no
    instrumentation, so a sanitizer finding can never change whether a task passes.
    """
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    recipe = _recipe_for(task, "runtime")
    groups = []
    for group in oracle.groups:
        timeout = oracle.suite_timeout_seconds
        binary = f"{BUILD_DIR}/{group.group_id}"
        sources = (
            *(f"work/{p}" for p in task.required_outputs if p.endswith(".c")),
            *(f"work/{p}" for p in group.files),
            f"{GUEST_ROOT}/rules/pcb_ctest.c",
        )
        argv = _run(
            group.group_id,
            timeout,
            *_build_argv(
                recipe,
                name=group.group_id,
                binary=binary,
                sources=sources,
                extra_includes=tuple(
                    f"work/{p.rsplit('/', 1)[0]}" for p in group.files if "/" in p
                ),
                execute="plain",
                deadline=oracle.case_timeout_seconds * len(group.cases),
            ),
            merge=True,
        )
        fields = _base(
            ids,
            plan_id=f"c.test.{group.group_id}",
            argv=argv,
            tool=ids.tool("clang", recipe="runtime", flags_digest=recipe.flags_digest()),
            parser_id="c-harness",
            inputs=(
                *_candidate_inputs(tuple(p for p in task.required_outputs if p.endswith(".c"))),
                *_scaffold_inputs(quality.scaffold_files),
                *(PlanInput(path=f"work/{p}", role="overlay") for p in group.files),
            ),
            outputs=(
                PlanOutput(path=f"out/{group.group_id}.build.json", format="json"),
                *_captured(group.group_id, "jsonl"),
            ),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(
                success=(BUILD_OK,),
                # 1 is ambiguous on purpose and is resolved by reading the records, never
                # the status: a failed case and a warnings-as-errors build are both the
                # candidate's fault, and the build document plus the harness stream say
                # which happened.
                findings=(1, *BUILD_FINDINGS),
                error=TOOL_ERRORS,
            ),
            recipe="runtime",
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
    recipe: Recipe,
    argv: tuple[str, ...],
    outputs: tuple[PlanOutput, ...],
    semantics: ExitSemantics,
    output_schema: str,
    flags_digest: str,
    timeout: int = 110,
    extra_inputs: tuple[PlanInput, ...] = (),
    ownership: Mapping[str, ScoreDimension | None] | None = None,
    merge: bool = False,
    plan_id: str | None = None,
) -> AnalysisPlan:
    task = context.task
    quality = quality_from_mapping(task.quality)
    plan_outputs = outputs
    if not any(
        output.required and not output.path.endswith((".run.json", ".build.json"))
        for output in outputs
    ):
        report = next((output for output in outputs if output.path.endswith(".out")), None)
        if report is not None:
            plan_outputs = tuple(
                item.model_copy(update={"required": True}) if item is report else item
                for item in outputs
            )
    fields = _base(
        ids,
        plan_id=plan_id or f"c.analysis.{analyzer}",
        argv=argv,
        tool=ids.tool(tool, recipe=recipe, flags_digest=flags_digest),
        parser_id=f"c-{analyzer.replace('_', '-')}",
        inputs=(
            *_candidate_inputs(context.candidate_paths),
            *_scaffold_inputs(quality.scaffold_files),
            *extra_inputs,
        ),
        outputs=plan_outputs,
        scope=tuple(context.candidate_paths),
        timeout=timeout,
        semantics=semantics,
        recipe=recipe,
    )
    return AnalysisPlan(
        **fields,  # type: ignore[arg-type]
        language_id="c",
        analyzer_id=analyzer,
        candidate_digest=context.candidate_digest,
        required=analyzer in task.required_analyzers,
        output_schema=output_schema,
        evidence_ownership=dict(ownership or {}),
        baseline_reusable=True,
    )


def _static_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    """clang-tidy and cppcheck over the candidate sources, in the evaluator image.

    Both run in the *uninstrumented* evaluator recipe at ``-O0``: they are static analyses, so
    instrumentation would only change their cost and could change which paths they model.
    """
    task = context.task
    quality = quality_from_mapping(task.quality)
    recipe = _recipe_for(task, "evaluator")
    sources = tuple(f"work/{p}" for p in context.candidate_paths if p.endswith((".c", ".h")))
    flags = recipe.compile_flags()
    plan: list[AnalysisPlan] = []
    if "clang_tidy" in task.required_analyzers or quality.lane("clang_tidy") is not None:
        argv = (
            "clang-tidy",
            *sources,
            *clang_tidy_arguments(),
            "--",
            *flags,
        )
        plan.append(
            _analysis(
                ids,
                context,
                analyzer="clang_tidy",
                tool="clang-tidy",
                recipe="evaluator",
                argv=_run("clang-tidy", 110, *argv),
                outputs=_captured("clang-tidy", "text"),
                # clang-tidy exits 0 whether or not it has findings; a non-zero exit is a compile
                # failure or a crash, and the scan is then missing rather than clean.
                semantics=ExitSemantics(success=(0,), error=TOOL_ERRORS),
                output_schema="c-tidy-diagnostics-v1",
                flags_digest=recipe.flags_digest(),
            )
        )
    if "cppcheck" in task.required_analyzers or quality.lane("cppcheck") is not None:
        argv = (
            "cppcheck",
            *cppcheck_arguments(),
            "-Iwork/include",
            *sources,
        )
        plan.append(
            _analysis(
                ids,
                context,
                analyzer="cppcheck",
                tool="cppcheck",
                recipe="evaluator",
                argv=_run("cppcheck", 100, *argv),
                outputs=_captured("cppcheck", "text"),
                # cppcheck exits 0 when it is clean; its default is 0 even with findings unless
                # --error-exitcode is given, so findings are parsed from the text and never from
                # the status.
                semantics=ExitSemantics(success=(0,), error=TOOL_ERRORS),
                output_schema="c-cppcheck-diagnostics-v1",
                flags_digest=recipe.flags_digest(),
                timeout=100,
            )
        )
    return plan


def _lane_groups(
    task: FrozenTask, lane: SanitizerPolicy
) -> list[tuple[str, tuple[str, ...], tuple[PlanInput, ...]]]:
    """One entry per oracle group the lane interprets: its id, its files and their overlay inputs.

    A lane is declared against specific groups, not against the whole suite: memcheck over the
    acceptance group would measure the harness as much as the candidate, and would multiply an
    already-slow lane by the suite timeout.
    """
    oracle = oracle_from_mapping(task.inventory)
    wanted = set(lane.groups)
    entries: list[tuple[str, tuple[str, ...], tuple[PlanInput, ...]]] = []
    for group in oracle.groups:
        if group.group_id not in wanted:
            continue
        files = tuple(dict.fromkeys(group.files))
        entries.append(
            (
                group.group_id,
                files,
                tuple(PlanInput(path=f"work/{f}", role="overlay") for f in files),
            )
        )
    if not entries:
        # A lane that names no existing group would silently produce no evidence. One empty
        # entry keeps the lane visible as a plan that fails, which is a better answer than a
        # lane that vanishes.
        entries.append(("", (), ()))
    return entries


def _dynamic_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    """Sanitizer and Valgrind lanes: one plan per (lane, oracle group), each in its own recipe.

    One plan per group rather than one per lane is forced by C, and the forcing is
    informative: every oracle group file carries its own ``main``, so a lane that named two
    groups could only be run as two programs. Building them separately is also what makes
    the evidence better - each group gets its own sanitizer report, so a defect in the
    acceptance group and a defect in the quality-only group are separate findings instead
    of whichever one aborted first.

    The *analyzer id* is the tool (``asan``, ``ubsan``, ``valgrind``) so it matches the frozen
    ``required_analyzers`` list; the *lane* is the capability being judged, and it is what
    the report document is keyed by. Conflating the two would make it impossible to require
    memcheck without also demanding a sanitizer that cannot judge the task.

    The sanitizer lanes build *and* run in the ``instrumented`` image, so the flags and the
    runtime cannot disagree. Valgrind builds in ``runtime`` and runs under memcheck: Valgrind
    is an observer, not a compiler flag, so instrumenting the binary as well would only add
    cost without adding coverage. Neither image is ever reachable from a performance plan.
    """
    task = context.task
    quality = quality_from_mapping(task.quality)
    timeout = oracle_from_mapping(task.inventory).suite_timeout_seconds
    plan: list[AnalysisPlan] = []
    for lane in quality.active_lanes:
        analyzer = LANE_ANALYZER[lane.lane]
        for group_id, files, overlay in _lane_groups(task, lane):
            if lane.lane in {"address", "undefined"}:
                recipe = _recipe_for(task, "instrumented", sanitizer=lane.lane)
                argv = _run(
                    f"{analyzer}.{group_id}",
                    timeout,
                    *_build_argv(
                        recipe,
                        name=f"{analyzer}.{group_id}",
                        binary=f"{BUILD_DIR}/{analyzer}-{group_id}",
                        sources=(
                            *(f"work/{p}" for p in task.required_outputs if p.endswith(".c")),
                            *(f"work/{f}" for f in files),
                            f"{GUEST_ROOT}/rules/pcb_ctest.c",
                        ),
                        execute="plain",
                        deadline=timeout - 10,
                    ),
                    merge=True,
                )
                plan.append(
                    _analysis(
                        ids,
                        context,
                        analyzer=analyzer,
                        plan_id=f"c.analysis.{analyzer}.{group_id}",
                        tool="clang",
                        recipe="instrumented",
                        argv=argv,
                        outputs=(
                            PlanOutput(path=f"out/{analyzer}.{group_id}.build.json", format="json"),
                            *_captured(f"{analyzer}.{group_id}", "text"),
                        ),
                        semantics=ExitSemantics(
                            success=(BUILD_OK,), findings=(1, *BUILD_FINDINGS), error=TOOL_ERRORS
                        ),
                        output_schema=f"pcb-c-sanitizer-report-v1.{lane.lane}",
                        flags_digest=recipe.flags_digest(),
                        timeout=timeout,
                        extra_inputs=overlay,
                        ownership={"candidate-undefined-behaviour": ScoreDimension.ROBUSTNESS},
                    )
                )
            elif lane.lane == "valgrind":
                recipe = _recipe_for(task, "runtime")
                argv = _run(
                    f"{analyzer}.{group_id}",
                    timeout,
                    *_build_argv(
                        recipe,
                        name=f"{analyzer}.{group_id}",
                        binary=f"{BUILD_DIR}/{analyzer}-{group_id}",
                        sources=(
                            *(f"work/{p}" for p in task.required_outputs if p.endswith(".c")),
                            *(f"work/{f}" for f in files),
                            f"{GUEST_ROOT}/rules/pcb_ctest.c",
                        ),
                        execute="valgrind",
                        deadline=timeout - 10,
                    ),
                    merge=True,
                )
                plan.append(
                    _analysis(
                        ids,
                        context,
                        analyzer=analyzer,
                        plan_id=f"c.analysis.{analyzer}.{group_id}",
                        tool="valgrind",
                        recipe="evaluator",
                        argv=argv,
                        outputs=(
                            PlanOutput(path=f"out/{analyzer}.{group_id}.build.json", format="json"),
                            *_captured(f"{analyzer}.{group_id}", "text"),
                        ),
                        semantics=ExitSemantics(
                            success=(BUILD_OK,),
                            findings=(1, VALGRIND_FINDINGS, *BUILD_FINDINGS),
                            error=TOOL_ERRORS,
                        ),
                        output_schema="pcb-c-sanitizer-report-v1.valgrind",
                        flags_digest=recipe.flags_digest(),
                        timeout=timeout,
                        extra_inputs=overlay,
                    )
                )
    return plan


def analysis_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    return [*_static_plans(ids, context), *_dynamic_plans(ids, context)]


def performance_plan(ids: ImageIdentities, task: FrozenTask) -> PerformancePlan | None:
    """The measured-performance lane, in the release recipe and nowhere else.

    :meth:`ImageIdentities.require_release_recipe` is the enforcement point: if the recorded
    performance image ever declared instrumentation, this function refuses to build a plan rather
    than producing a number nobody could trust (PCB-20-1).
    """
    quality = quality_from_mapping(task.quality)
    declared = quality.performance
    if declared is None:
        return None
    record = ids.require_release_recipe("performance")
    recipe = _recipe_for(task, "performance")
    timeout = declared.hard_timeout_seconds
    iteration = ExecutionPlan(
        **_base(  # type: ignore[arg-type]
            ids,
            plan_id="c.performance.iteration",
            argv=_run(
                "perf",
                timeout,
                *_build_argv(
                    recipe,
                    name="perf",
                    binary=f"{BUILD_DIR}/pcb_workload",
                    sources=(
                        *(f"work/{p}" for p in task.required_outputs if p.endswith(".c")),
                        f"work/{declared.workload_file}",
                    ),
                    # Without this the iteration only *built* the workload and captured an empty
                    # stdout, so the admission smoke had nothing to read and could never pass. The
                    # performance lane measures executed work, so the workload has to actually run.
                    execute="plain",
                    deadline=timeout - RUNNER_MARGIN,
                    run_args=("{seed}", "{scale}"),
                ),
            ),
            tool=ids.tool("clang", recipe="performance", flags_digest=recipe.flags_digest()),
            parser_id="c-perf",
            inputs=(
                *_candidate_inputs(tuple(p for p in task.required_outputs if p.endswith(".c"))),
                *_scaffold_inputs(quality.scaffold_files),
                PlanInput(path=f"work/{declared.workload_file}", role="overlay"),
            ),
            outputs=(
                PlanOutput(path="out/perf.build.json", format="json"),
                *_captured("perf", "text"),
            ),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(
                success=(BUILD_OK,), findings=BUILD_FINDINGS, error=TOOL_ERRORS
            ),
            recipe="performance",
        )
    )
    return PerformancePlan(
        plan_id="c.performance",
        reference_digest=None,
        language_runtime=f"clang {record.cc} -std={recipe.standard}",
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
