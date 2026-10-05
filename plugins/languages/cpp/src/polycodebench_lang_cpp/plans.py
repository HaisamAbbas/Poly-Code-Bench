"""Build, test, analysis and performance plans: typed argument vectors, never shell strings.

Every plan runs the pinned guest tooling in ``/opt/pcb/guest``, so a plan is a list of argv tokens
rather than a command line someone could get wrong. The C++ shapes that matter:

* the candidate lives under ``work/`` and every input is role-tagged, so candidate sources, the
  hidden tests (``overlay``) and the pinned recipe (``config``) can never be confused;
* the compiler, the language standard and the build profile come from the frozen recipe, and the
  flags come from the pinned toolchain lock, never from the manifest;
* an instrumented plan builds the hidden groups with the sanitized profile and runs them under the
  matching runtime; the performance plan builds with the *release* profile only, so a sanitized
  binary can never be timed as release code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, Literal

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

from polycodebench_lang_cpp.identities import (
    GUEST_ROOT,
    ImageIdentities,
    Recipe,
    tool_identity_name,
)
from polycodebench_lang_cpp.locks import BuildProfile, ToolchainLock
from polycodebench_lang_cpp.taskspec import (
    SANITIZER_REQUIREMENTS,
    CppOracle,
    CppQualityPlan,
    CppRecipe,
    oracle_from_mapping,
    quality_from_mapping,
    recipe_from_mapping,
)

GUEST = f"{GUEST_ROOT}/guest"
RULES = f"{GUEST_ROOT}/rules"
WORK = "/workspace/work"
OUTPUTS = "/workspace/out"
BUILD_DIR = "/workspace/.pcb-build"
MIB = 1024**2
# The in-guest runner stops before the supervisor's own deadline, so the runner — which keeps
# partial output naming the case in flight — always wins the race against the outer kill.
RUNNER_MARGIN = 3
# The local Docker driver caps one exec at 120s, so no plan may declare more than this.
MAX_PLAN_SECONDS = 110
# 126/127 come from the guest runner: the compiler is not executable / not found.
TOOL_ERRORS = (2, 126, 127)
SANITIZER_TOOL_ERRORS = (126, 127)
COMPILE_FAILED = 1
# Exit 2 is a wrapper usage error. The sanitizer wrapper uses exit 3 when a runtime cannot judge;
# its structured report distinguishes that from a candidate defect.
UNSUPPORTED = 3
SOURCE_SUFFIXES = (".cpp", ".cc", ".cxx")
# `ToolIdentity.name` is a slug, so the compiler is recorded as `clang-c++`.
COMPILER_TOOL = tool_identity_name("clang++")


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


def cxxflags(recipe: CppRecipe, profile: BuildProfile, lock: ToolchainLock) -> tuple[str, ...]:
    """The exact compiler flags for one task: the pinned standard, then the pinned profile."""
    return (lock.standard_flag(recipe.standard), *profile.cxxflags)


def sources_of(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(path for path in paths if path.endswith(SOURCE_SUFFIXES))


def _candidate_inputs(paths: Sequence[str]) -> tuple[PlanInput, ...]:
    return tuple(PlanInput(path=f"work/{path}", role="candidate") for path in paths)


def _recipe_inputs(recipe: CppRecipe) -> tuple[PlanInput, ...]:
    return (PlanInput(path="work/pcb_recipe.json", role="config", digest=recipe.digest()),)


def _captured(name: str, stdout_format: Literal["text", "json"]) -> tuple[PlanOutput, ...]:
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
    tool_name: str,
    lock: ToolchainLock,
    parser_id: str,
    inputs: tuple[PlanInput, ...],
    outputs: tuple[PlanOutput, ...],
    scope: tuple[str, ...],
    timeout: int,
    semantics: ExitSemantics,
    environment: Mapping[str, str] | None = None,
    recipe: Recipe = "evaluator",
) -> dict[str, Any]:
    image = ids.images[recipe]
    return {
        "plan_id": plan_id,
        "image": image.reference,
        "image_digest": image.digest,
        "argv": argv,
        "working_directory": ".",
        "environment": dict(environment or {}),
        "inputs": inputs,
        "outputs": outputs,
        "resources": resources(timeout),
        "exit_semantics": semantics,
        "tool": ids.tool(tool_name, recipe=recipe, lock=lock),
        "scope": scope,
        "parser_id": parser_id,
    }


def _compile_arguments(
    recipe: CppRecipe, profile: BuildProfile, lock: ToolchainLock
) -> tuple[str, ...]:
    """The compiler flags clang-tidy needs to parse one candidate translation unit."""
    includes = [f"-I{WORK}/{directory}" for directory in recipe.include_dirs]
    return (*cxxflags(recipe, profile, lock), *includes, *recipe.link_flags)


def _compiler_argv(
    recipe: CppRecipe, profile: BuildProfile, lock: ToolchainLock, sources: Sequence[str]
) -> tuple[str, ...]:
    compiler = lock.compiler(recipe.compiler)
    return (
        "--compiler",
        compiler.binary,
        *[token for flag in cxxflags(recipe, profile, lock) for token in ("--cxxflag", flag)],
        *[
            token
            for directory in recipe.include_dirs
            for token in ("--include", f"{WORK}/{directory}")
        ],
        *[token for path in sources for token in ("--source", f"{WORK}/{path}")],
    )


def _recipe_of(quality: CppQualityPlan) -> CppRecipe:
    if quality.recipe is None:
        raise ValueError("the frozen quality plan carries no pinned recipe")
    return recipe_from_mapping(quality.recipe)


# --------------------------------------------------------------------------- build


def build_plan(
    ids: ImageIdentities, lock: ToolchainLock, task: FrozenTask, candidate: Candidate
) -> BuildPlan:
    del candidate
    sources = sources_of(task.required_outputs)
    if not sources:
        raise ValueError("a C++ task needs at least one required .cpp output")
    quality = quality_from_mapping(task.quality)
    recipe = _recipe_of(quality)
    profile = lock.build_profile(recipe.build_profile)
    timeout = 100
    argv = (
        "python",
        "-B",
        f"{GUEST}/pcb_cpp_build.py",
        "--name",
        f"{OUTPUTS}/build",
        "--deadline",
        str(timeout - RUNNER_MARGIN),
        "--root",
        BUILD_DIR,
        *_compiler_argv(recipe, profile, lock, sources),
        "--archive",
        f"{BUILD_DIR}/libpcb.a",
    )
    return BuildPlan.model_validate(
        _base(
            ids,
            plan_id="cpp.build",
            argv=argv,
            tool_name=COMPILER_TOOL,
            lock=lock,
            parser_id="cpp-build",
            # Headers are candidate outputs too: stage them even though the compiler argv only
            # names translation units, or an otherwise valid implementation cannot build.
            inputs=(*_candidate_inputs(task.required_outputs), *_recipe_inputs(recipe)),
            outputs=_captured("build", "text"),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(COMPILE_FAILED,), error=TOOL_ERRORS),
            recipe="runtime",
        )
    )


# ---------------------------------------------------------------------------- tests


def make_test_plan(ids: ImageIdentities, lock: ToolchainLock, task: FrozenTask) -> TestPlan:
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    recipe = _recipe_of(quality)
    profile = lock.build_profile(recipe.build_profile)
    compiler = lock.compiler(recipe.compiler)
    sources = sources_of(task.required_outputs)
    groups: list[TestGroupPlan] = []
    for group in oracle.groups:
        timeout = oracle.suite_timeout_seconds
        argv = (
            "python",
            "-B",
            f"{GUEST}/pcb_cpp_test.py",
            "--name",
            f"out/{group.group_id}",
            "--deadline",
            str(timeout - RUNNER_MARGIN),
            "--compiler",
            compiler.binary,
            *[token for flag in cxxflags(recipe, profile, lock) for token in ("--cxxflag", flag)],
            *[
                token
                for directory in (*recipe.include_dirs, "rules")
                for token in ("--include", f"{WORK}/{directory}" if directory != "rules" else RULES)
            ],
            *[token for path in sources for token in ("--source", f"{WORK}/{path}")],
            "--test",
            f"{WORK}/{group.files[0]}",
            "--binary",
            f"{OUTPUTS}/{group.group_id}.bin",
        )
        groups.append(
            TestGroupPlan(
                group_id=group.group_id,
                required=group.required,
                # A quality-only group is evidence about robustness rather than about the contract,
                # so it is repeated: a marginal implementation is stable or lucky, and the two
                # are not the same answer.
                repetitions=3 if group.classification == "quality_only" else 1,
                plan=ExecutionPlan.model_validate(
                    _base(
                        ids,
                        plan_id=f"cpp.test.{group.group_id}",
                        argv=argv,
                        tool_name=COMPILER_TOOL,
                        lock=lock,
                        parser_id="cpp-test-protocol",
                        inputs=(
                            *_candidate_inputs(task.required_outputs),
                            *_recipe_inputs(recipe),
                            *(
                                PlanInput(path=f"work/{name}", role="overlay")
                                for name in group.files
                            ),
                        ),
                        outputs=_captured(group.group_id, "text"),
                        scope=task.required_outputs,
                        timeout=timeout,
                        # A failing case or candidate signal is evidence about the solution. The
                        # parser cross-checks abnormal exits against the supervisor record.
                        semantics=ExitSemantics(
                            success=(0,),
                            findings=(COMPILE_FAILED, *range(129, 193)),
                            error=TOOL_ERRORS,
                        ),
                        recipe="runtime",
                    )
                ),
            )
        )
    return TestPlan(groups=tuple(groups), expected_inventory_digest=str(oracle.inventory_digest()))


# ------------------------------------------------------------------------ analyzers


def _analysis(
    ids: ImageIdentities,
    lock: ToolchainLock,
    context: AnalysisContext,
    *,
    analyzer: str,
    tool_name: str,
    argv: tuple[str, ...],
    outputs: tuple[PlanOutput, ...],
    semantics: ExitSemantics,
    output_schema: str,
    timeout: int,
    extra_inputs: tuple[PlanInput, ...] = (),
    ownership: Mapping[str, ScoreDimension | None] | None = None,
    environment: Mapping[str, str] | None = None,
    recipe: Recipe = "evaluator",
) -> AnalysisPlan:
    quality = quality_from_mapping(context.task.quality)
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
    return AnalysisPlan.model_validate(
        {
            **_base(
                ids,
                plan_id=f"cpp.analysis.{analyzer}",
                argv=argv,
                tool_name=tool_name,
                lock=lock,
                parser_id=f"cpp-{analyzer}",
                inputs=(
                    *_candidate_inputs(context.candidate_paths),
                    *_recipe_inputs(_recipe_of(quality)),
                    *extra_inputs,
                ),
                outputs=plan_outputs,
                scope=tuple(context.candidate_paths),
                timeout=timeout,
                semantics=semantics,
                environment=environment,
                recipe=recipe,
            ),
            "language_id": "cpp",
            "analyzer_id": analyzer,
            "candidate_digest": context.candidate_digest,
            "required": analyzer in context.task.required_analyzers,
            "output_schema": output_schema,
            "evidence_ownership": dict(ownership or {}),
            "baseline_reusable": True,
        }
    )


def _lint_tidy(ids: ImageIdentities, lock: ToolchainLock, context: AnalysisContext) -> AnalysisPlan:
    """clang-tidy compiles the candidate itself, so it needs the build's own flags."""
    quality = quality_from_mapping(context.task.quality)
    recipe = _recipe_of(quality)
    profile = lock.build_profile(recipe.build_profile)
    invocation = lock.analyzer("clang_tidy")
    sources = sources_of(context.candidate_paths)
    timeout = 100
    argv = (
        "python",
        "-B",
        f"{GUEST}/pcb_cpp_run.py",
        "--name",
        "out/clang_tidy",
        "--deadline",
        str(timeout - RUNNER_MARGIN),
        "--cleanup",
        BUILD_DIR,
        "--",
        invocation.binary,
        *invocation.flags,
        f"--config-file={RULES}/.clang-tidy",
        *[f"{WORK}/{path}" for path in sources],
        "--",
        *_compile_arguments(recipe, profile, lock),
    )
    return _analysis(
        ids,
        lock,
        context,
        analyzer="clang_tidy",
        tool_name="clang-tidy",
        argv=argv,
        outputs=_captured("clang_tidy", "text"),
        # clang-tidy exits 0 with warnings, so findings never use the exit code: a non-zero exit is
        # a compile failure, and the scan is then missing rather than clean.
        semantics=ExitSemantics(success=(0,), error=(COMPILE_FAILED, *TOOL_ERRORS)),
        output_schema="clang-tidy-text-v1",
        timeout=timeout,
    )


def _lint_cppcheck(
    ids: ImageIdentities, lock: ToolchainLock, context: AnalysisContext
) -> AnalysisPlan:
    quality = quality_from_mapping(context.task.quality)
    recipe = _recipe_of(quality)
    invocation = lock.analyzer("cppcheck")
    suppressions = PurePosixPath(invocation.checks_source).name
    timeout = 100
    argv = (
        "python",
        "-B",
        f"{GUEST}/pcb_cpp_run.py",
        "--name",
        "out/cppcheck",
        "--deadline",
        str(timeout - RUNNER_MARGIN),
        "--",
        invocation.binary,
        *invocation.flags,
        f"--suppressions-list={RULES}/{suppressions}",
        # The XML report is the evidence, so a finding must not change the exit status.
        "--error-exitcode=0",
        "--language=c++",
        f"--std={recipe.standard}",
        *[f"-I{WORK}/{directory}" for directory in recipe.include_dirs],
        *[f"{WORK}/{path}" for path in context.candidate_paths],
    )
    return _analysis(
        ids,
        lock,
        context,
        analyzer="cppcheck",
        tool_name="cppcheck",
        argv=argv,
        outputs=_captured("cppcheck", "text"),
        semantics=ExitSemantics(success=(0,), findings=(COMPILE_FAILED,), error=TOOL_ERRORS),
        output_schema="cppcheck-xml-v2",
        timeout=timeout,
    )


def _scan_context(
    ids: ImageIdentities, lock: ToolchainLock, context: AnalysisContext
) -> AnalysisPlan:
    quality = quality_from_mapping(context.task.quality)
    argv = (
        "python",
        "-B",
        f"{GUEST}/pcb_cpp_scan.py",
        "--root",
        WORK,
        "--output",
        "out/context.json",
        "--opportunities",
        ",".join(quality.opportunity_tags),
        *[f"{WORK}/{path}" for path in context.candidate_paths],
    )
    return _analysis(
        ids,
        lock,
        context,
        analyzer="context",
        tool_name="context-scan",
        argv=argv,
        outputs=(PlanOutput(path="out/context.json", format="json"),),
        semantics=ExitSemantics(success=(0,), findings=(COMPILE_FAILED,), error=TOOL_ERRORS),
        output_schema="pcb-cpp-scan-v1",
        timeout=60,
    )


def _instrumented(
    ids: ImageIdentities,
    lock: ToolchainLock,
    context: AnalysisContext,
    *,
    analyzer: str,
    sanitizers: tuple[str, ...],
    oracle: CppOracle,
) -> AnalysisPlan:
    """One plan: build each hidden group with the sanitized profile and run it under the runtime."""
    quality = quality_from_mapping(context.task.quality)
    recipe = _recipe_of(quality)
    profile = lock.profile_for(sanitizers)
    sources = sources_of(context.candidate_paths)
    detector = lock.detector(sanitizers[0])
    timeout = MAX_PLAN_SECONDS
    argv = (
        "python",
        "-B",
        f"{GUEST}/pcb_cpp_sanitize.py",
        "--name",
        f"out/{analyzer}",
        "--deadline",
        str(timeout - RUNNER_MARGIN),
        "--detector",
        detector,
        *_compiler_argv(recipe, profile, lock, sources),
        *[
            token
            for group in oracle.groups
            for token in ("--group", f"{group.group_id}={WORK}/{group.files[0]}")
        ],
        "--include",
        RULES,
        "--binary-dir",
        f"{OUTPUTS}/{analyzer}",
    )
    return _analysis(
        ids,
        lock,
        context,
        analyzer=analyzer,
        tool_name=COMPILER_TOOL,
        argv=argv,
        outputs=(
            PlanOutput(path=f"out/{analyzer}.report.json", format="json"),
            PlanOutput(
                path=f"out/{analyzer}.out", format="text", required=False, max_bytes=4 * MIB
            ),
            PlanOutput(
                path=f"out/{analyzer}.err", format="text", required=False, max_bytes=4 * MIB
            ),
        ),
        # A measured defect and a runtime that refused to judge both exit non-zero; only the
        # report says which one it was.
        semantics=ExitSemantics(
            success=(0,),
            findings=(COMPILE_FAILED, UNSUPPORTED),
            error=SANITIZER_TOOL_ERRORS,
        ),
        output_schema="pcb-cpp-sanitizer-v1",
        timeout=timeout,
        extra_inputs=tuple(
            PlanInput(path=f"work/{name}", role="overlay")
            for group in oracle.groups
            for name in group.files
        ),
        ownership={
            "candidate-undefined-behaviour": ScoreDimension.ROBUSTNESS,
            "resource-cleanup-failure": ScoreDimension.ROBUSTNESS,
        },
        environment=lock.sanitizer_environment(sanitizers),
        # An instrumented run executes the candidate, so it belongs in the runtime image: that
        # image carries the sanitizer runtimes and none of the analyzers.
        recipe="runtime",
    )


def analysis_plans(
    ids: ImageIdentities, lock: ToolchainLock, context: AnalysisContext
) -> list[AnalysisPlan]:
    task = context.task
    quality = quality_from_mapping(task.quality)
    plans = [
        _lint_tidy(ids, lock, context),
        _lint_cppcheck(ids, lock, context),
        _scan_context(ids, lock, context),
    ]
    if quality.runs_sanitizers:
        oracle = oracle_from_mapping(task.inventory)
        for chosen in quality.instrumentation_lanes:
            for analyzer, needed in SANITIZER_REQUIREMENTS.items():
                if set(chosen) & set(needed):
                    plans.append(
                        _instrumented(
                            ids,
                            lock,
                            context,
                            analyzer=analyzer,
                            sanitizers=tuple(chosen),
                            oracle=oracle,
                        )
                    )
                    break
    return plans


# --------------------------------------------------------------------- performance


def performance_plan(
    ids: ImageIdentities, lock: ToolchainLock, task: FrozenTask
) -> PerformancePlan | None:
    """The release lane.

    A sanitized build is roughly an order of magnitude slower and finds different defects, so a
    timing taken from one is not a release timing. The workload is therefore compiled with the
    pinned release profile in the performance image, and a task whose recipe asks for a sanitized
    profile is refused here rather than quietly measured without instrumentation.
    """
    quality = quality_from_mapping(task.quality)
    declared = quality.performance
    if declared is None:
        return None
    recipe = _recipe_of(quality)
    profile = lock.release_profile()
    compiler = lock.compiler(recipe.compiler)
    sources = sources_of(task.required_outputs)
    if not sources:
        raise ValueError("a C++ performance lane needs at least one required .cpp output")
    timeout = declared.hard_timeout_seconds
    argv = (
        "python",
        "-B",
        f"{GUEST}/pcb_cpp_test.py",
        "--name",
        "out/perf",
        "--deadline",
        str(timeout - RUNNER_MARGIN),
        "--compiler",
        compiler.binary,
        *[
            token
            for flag in (lock.standard_flag(recipe.standard), *profile.cxxflags)
            for token in ("--cxxflag", flag)
        ],
        *[
            token
            for directory in recipe.include_dirs
            for token in ("--include", f"{WORK}/{directory}")
        ],
        *[token for path in sources for token in ("--source", f"{WORK}/{path}")],
        "--test",
        f"{WORK}/{declared.workload_file}",
        "--binary",
        f"{OUTPUTS}/perf.bin",
        "--arg",
        "--scale",
        "--arg",
        "{scale}",
        "--arg",
        "--seed",
        "--arg",
        "{seed}",
    )
    return PerformancePlan(
        plan_id="cpp.performance",
        reference_digest=None,
        language_runtime=f"clang++ {ids.performance.clang}",
        hardware_class="local-development",
        workloads=tuple(
            WorkloadSpec(
                workload_id=workload.workload_id,
                scale=workload.scale,
                weight_bp=workload.weight_bp,
                input_seed=workload.input_seed,
            )
            for workload in declared.workloads
        ),
        warmup_iterations=declared.warmup_iterations,
        measured_iterations=declared.measured_iterations,
        metric_ids=("elapsed_ns", "peak_rss_kb"),
        iteration_plan=ExecutionPlan.model_validate(
            _base(
                ids,
                plan_id="cpp.performance.iteration",
                argv=argv,
                tool_name=COMPILER_TOOL,
                lock=lock,
                parser_id="cpp-perf",
                inputs=(
                    *_candidate_inputs(task.required_outputs),
                    *_recipe_inputs(recipe),
                    PlanInput(path=f"work/{declared.workload_file}", role="overlay"),
                ),
                outputs=_captured("perf", "text"),
                scope=task.required_outputs,
                timeout=timeout,
                semantics=ExitSemantics(success=(0, COMPILE_FAILED), error=TOOL_ERRORS),
                recipe="performance",
            )
        ),
        threads=1,
    )


__all__ = [
    "COMPILE_FAILED",
    "GUEST",
    "MAX_PLAN_SECONDS",
    "RULES",
    "TOOL_ERRORS",
    "UNSUPPORTED",
    "WORK",
    "analysis_plans",
    "build_plan",
    "cxxflags",
    "make_test_plan",
    "performance_plan",
    "resources",
    "sources_of",
]
