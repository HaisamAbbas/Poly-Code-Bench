"""Build, test, analysis and performance plans: typed argument vectors, never shell strings.

Every plan runs its tool through ``pcb_go_run.py``, which captures stdout/stderr to files, enforces
the deadline from inside the guest (so a hung test leaves partial output naming the case in
flight) and removes the build cache. The module lives under ``work/`` so candidate files, trusted
module scaffolding (``config``) and hidden tests (``overlay``) are separate, digest-checked inputs.

Two invariants this module owns:

* **Offline and pinned.** ``GOTOOLCHAIN=local`` stops the toolchain from fetching a different Go
  release for a task's ``go.mod``, and ``GOPROXY=off`` stops any module fetch. A dependency the
  module graph does not pin therefore fails loudly instead of resolving from the network during a
  scored run.
* **Instrumentation never enters measurement.** The race detector needs ``CGO_ENABLED=1`` and
  recompiles everything with instrumentation, so a race build is a different artifact with
  different timings. :func:`assert_release` refuses any instrumented flag in a performance plan.

The sandbox runs every command from ``/workspace``, and Go resolves a module pattern such as
The sandbox runs every command from ``/workspace``, while Go resolves a module pattern such as
``./...`` against the directory that holds ``go.mod``. Module-scoped tools therefore start in
``work/``: the guest runner takes that directory as data (``--cwd``) and the two tools that are
not driven through it (``gofmt`` and the context scanner) are given absolute paths. No plan ever
needs a shell ``cd``, so the argument vectors stay free of interpolation.
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

from polycodebench_lang_go.identities import GUEST_ROOT, ImageIdentities
from polycodebench_lang_go.taskspec import oracle_from_mapping, quality_from_mapping

GUEST = f"{GUEST_ROOT}/guest"
RULES = f"{GUEST_ROOT}/rules"
MODULE_DIR = "work"
# The sandbox container is `--read-only` and runs as an unprivileged uid that owns only
# /workspace and /tmp, so the Go build cache cannot live in the image. A cache path the guest
# cannot create makes `go build` exit 2 before compiling anything - which the exit contract
# correctly reads as a tool error, not as a candidate failure - and it takes `vet`,
# `staticcheck` and `gosec` down with it, because those all load packages through the build
# cache. The caches therefore live under the workspace tmpfs, which is writable, sized from the
# plan's disk budget and discarded with the container.
# Absolute under the workspace: Go rejects a relative GOPATH/GOMODCACHE outright, and the runner
# executes from /workspace, so this prefix is both writable and unambiguous.
WORKSPACE_ROOT = "/workspace"
GO_CACHE = f"{WORKSPACE_ROOT}/cache/go-build"
GO_MOD_CACHE = f"{WORKSPACE_ROOT}/cache/go-mod"
GO_PATH = f"{WORKSPACE_ROOT}/cache/go-path"
GO_TMP = f"{WORKSPACE_ROOT}/cache/go-tmp"
WORKLOAD_DIR = "work/cmd/pcb_workload"
MIB = 1024**2
# Seconds the in-guest runner stops before the supervisor's own deadline, so the runner (which can
# keep partial output) always wins the race against the outer kill.
RUNNER_MARGIN = 3
# The local Docker driver caps one exec at 120s (the supervisor adds an 8s grace to the plan
# deadline), so no plan may declare more than this.
MAX_PLAN_SECONDS = 110
# 126/127 come from the runner: tool not executable / not found (missing dependency). Exit 2 is a
# usage error and 3 the Go tools' own "the analysis could not be performed".
TOOL_ERRORS = (2, 3, 126, 127)
# Go exits 1 for a finding, a failing test and a build failure alike; the parsers read the tools'
# own reports rather than trusting this code.
GO_FINDINGS = 1
# Any flag that turns a build into an instrumented one. A measured plan may contain none.
INSTRUMENTED_FLAGS = ("-race", "-msan", "-asan", "-cover", "-covermode", "-coverprofile")
BASE_ENV = {
    "GOTOOLCHAIN": "local",
    "GOPROXY": "off",
    "GOSUMDB": "off",
    "GOFLAGS": "-mod=mod",
    "GOCACHE": GO_CACHE,
    "GOMODCACHE": GO_MOD_CACHE,
    "GOPATH": GO_PATH,
    # The compile work directory defaults to /tmp, which the sandbox caps at 16 MiB; a build that
    # fills it dies with ENOSPC, which is indistinguishable from a candidate build error. It
    # follows the caches into the workspace tmpfs, which is sized from the plan's disk budget.
    "GOTMPDIR": GO_TMP,
    # Go writes the toolchain-switch record into GOENV's default location; `off` keeps the image
    # immutable and stops a scored run from depending on a writable HOME.
    "GOENV": "off",
    "CGO_ENABLED": "0",
    # staticcheck keeps its own fact cache under `$XDG_CACHE_HOME` (falling back to `$HOME/.cache`).
    # `HOME` itself is a protected sandbox variable - a candidate must not control it - so the
    # cache location is pinned here instead. Without this it resolved to `/.cache` and failed with
    # "read-only file system" before analysing anything, which the parser then correctly reported
    # as missing evidence.
    "XDG_CACHE_HOME": f"{WORKSPACE_ROOT}/cache/home/.cache",
}


def assert_release(argv: tuple[str, ...], *, where: str) -> None:
    """Refuse an instrumented build in a measurement plan (PCB-22-2).

    A ``-race`` build runs the same algorithm with shadow memory and atomic instrumentation: it is
    several times slower and its memory figures are an artefact of the detector. Such a result must
    never reach the performance lane, so the check lives where the plan is built rather than in a
    downstream stage that might forget to call it.
    """
    offending = sorted(
        {flag for flag in INSTRUMENTED_FLAGS if any(f.startswith(flag) for f in argv)}
    )
    if offending:
        raise ValueError(f"{where} must not use instrumented build flags: {', '.join(offending)}")


def resources(timeout: int, *, memory_mib: int = 2048) -> ResourcePolicy:
    """The one place a plan's budget is turned into a resource policy.

    The ceiling is enforced here rather than trusted to every call site: a plan that declared more
    than the driver's own exec cap would be killed by the sandbox at a time of the driver's choosing
    and surface as an infrastructure failure, which is exactly the class of result that must not
    be mistaken for a candidate timeout.
    """
    if timeout > MAX_PLAN_SECONDS:
        raise ValueError(f"a Go plan may not declare more than {MAX_PLAN_SECONDS}s, got {timeout}s")
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
    return tuple(PlanInput(path=f"{MODULE_DIR}/{p}", role="candidate") for p in paths)


def _module_inputs(module_files: Mapping[str, str]) -> tuple[PlanInput, ...]:
    return tuple(
        PlanInput(path=f"{MODULE_DIR}/{path}", role="config", digest=digest)
        for path, digest in sorted(module_files.items())
    )


def _run(
    name: str, deadline: int, *command: str, merge: bool = False, cwd: str | None = None
) -> tuple[str, ...]:
    """The runner invocation for one Go command (``name`` is the output prefix)."""
    return (
        "python",
        "-B",
        f"{GUEST}/pcb_go_run.py",
        "--name",
        f"out/{name}",
        "--deadline",
        str(max(1, deadline - RUNNER_MARGIN)),
        "--cleanup",
        GO_CACHE,
        *(("--cwd", cwd) if cwd else ()),
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
    recipe: Literal["runtime", "evaluator", "performance"] = "runtime",
) -> dict[str, object]:
    image = ids.images[recipe]
    return {
        "plan_id": plan_id,
        "image": image.reference,
        "image_digest": image.digest,
        "argv": argv,
        "working_directory": ".",
        "environment": {"LC_ALL": "C", **BASE_ENV, **(environment or {})},
        "inputs": inputs,
        "outputs": outputs,
        "resources": resources(timeout),
        "exit_semantics": semantics,
        "tool": tool,
        "scope": scope,
        "parser_id": parser_id,
    }


def _absolute(paths: tuple[str, ...]) -> tuple[str, ...]:
    """The same paths as absolute guest paths, for a tool that runs from ``/workspace``."""
    return tuple(f"/workspace/{path}" for path in paths)


def _go(*args: str) -> tuple[str, ...]:
    """One ``go`` invocation.

    Go resolves a module pattern such as ``./...`` against the directory that holds ``go.mod``,
    and the sandbox runs every command from ``/workspace``. The guest runner therefore starts the
    child in ``work/``; keeping that here means one place decides where a module-scoped tool runs,
    and no plan ever needs a shell ``cd``.
    """
    return ("go", *args)


def build_plan(ids: ImageIdentities, task: FrozenTask, candidate: Candidate) -> BuildPlan:
    paths = tuple(p for p in task.required_outputs if p.endswith(".go"))
    if not paths:
        raise ValueError("a Go task needs at least one required .go output")
    quality = quality_from_mapping(task.quality)
    timeout = 100
    fields = _base(
        ids,
        plan_id="go.build",
        argv=_run("build", timeout, *_go("build", "./..."), cwd=MODULE_DIR),
        tool=ids.tool("go-build", recipe="runtime"),
        parser_id="go-build",
        recipe="runtime",
        inputs=(*_candidate_inputs(paths), *_module_inputs(quality.module_files)),
        outputs=_captured("build", "text"),
        scope=paths,
        timeout=timeout,
        semantics=ExitSemantics(success=(0,), findings=(GO_FINDINGS,), error=TOOL_ERRORS),
    )
    return BuildPlan(**fields)  # type: ignore[arg-type]


def make_test_plan(ids: ImageIdentities, task: FrozenTask) -> TestPlan:
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    groups = []
    for group in oracle.groups:
        # `-run` selects the group's declared cases; a group with no selector runs the whole
        # module, which is what an acceptance group with one file means.
        selector = "|".join(sorted(case.case_id.split("/", 1)[0] for case in group.cases))
        timeout = oracle.suite_timeout_seconds
        argv = _run(
            group.group_id,
            timeout,
            *_go("test", "-json", "-count=1"),
            *(("-run", f"^({selector})$") if selector else ()),
            "./...",
            merge=True,
            cwd=MODULE_DIR,
        )
        fields = _base(
            ids,
            plan_id=f"go.test.{group.group_id}",
            argv=argv,
            tool=ids.tool("go-test", recipe="runtime"),
            parser_id="go-test-json",
            recipe="runtime",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                *_module_inputs(quality.module_files),
                *(PlanInput(path=f"{MODULE_DIR}/{p}", role="overlay") for p in group.files),
            ),
            outputs=_captured(group.group_id, "jsonl"),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(GO_FINDINGS,), error=TOOL_ERRORS),
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
    recipe: Literal["runtime", "evaluator", "performance"] = "evaluator",
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
                item.model_copy(
                    update={
                        "required": True,
                        "empty_is_clean": analyzer == "gofmt",
                    }
                )
                if item is report
                else item
                for item in outputs
            )
    fields = _base(
        ids,
        plan_id=f"go.analysis.{analyzer}",
        argv=argv,
        tool=ids.tool(
            tool,
            recipe=recipe,
            lock_digest=quality.go_module_digest if needs_lock else None,
        ),
        parser_id=f"go-{analyzer}",
        inputs=(
            *_candidate_inputs(context.candidate_paths),
            *_module_inputs(quality.module_files),
            *extra_inputs,
        ),
        outputs=plan_outputs,
        scope=tuple(context.candidate_paths),
        timeout=timeout,
        semantics=semantics,
        environment=environment,
        recipe=recipe,
    )
    return AnalysisPlan(
        **fields,  # type: ignore[arg-type]
        language_id="go",
        analyzer_id=analyzer,
        candidate_digest=context.candidate_digest,
        required=analyzer in task.required_analyzers,
        output_schema=output_schema,
        evidence_ownership=dict(ownership or {}),
        baseline_reusable=True,
    )


def _race_overlay(task: FrozenTask) -> tuple[tuple[PlanInput, ...], ...]:
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    wanted = set(quality.race_groups) or {g.group_id for g in oracle.groups if g.required}
    files = tuple(dict.fromkeys(f for g in oracle.groups if g.group_id in wanted for f in g.files))
    return (tuple(PlanInput(path=f"{MODULE_DIR}/{p}", role="overlay") for p in files),)


def analysis_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    task = context.task
    quality = quality_from_mapping(task.quality)
    candidate_sources = tuple(
        f"{MODULE_DIR}/{p}" for p in context.candidate_paths if p.endswith(".go")
    )
    findings_zero = ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS)
    plans = [
        _analysis(
            ids,
            context,
            analyzer="gofmt",
            tool="gofmt",
            # `gofmt -l` lists the files it would rewrite and exits 0; `-e` keeps reporting on the
            # rest of the file when one line does not parse.
            argv=_run("gofmt", 60, "gofmt", "-l", "-e", *_absolute(candidate_sources)),
            outputs=_captured("gofmt", "text"),
            # Findings never use the exit status, so only the runner's own errors are declared.
            semantics=ExitSemantics(success=(0,), error=TOOL_ERRORS),
            output_schema="gofmt-list-v1",
            timeout=60,
            recipe="runtime",
        ),
        _analysis(
            ids,
            context,
            analyzer="vet",
            tool="go-vet",
            argv=_run("vet", 110, *_go("vet", "./..."), cwd=MODULE_DIR),
            outputs=_captured("vet", "json"),
            semantics=findings_zero,
            output_schema="go-vet-json-v1",
            timeout=110,
            recipe="runtime",
            needs_lock=True,
        ),
        _analysis(
            ids,
            context,
            analyzer="staticcheck",
            tool="staticcheck",
            argv=_run("staticcheck", 110, "staticcheck", "-f", "json", "./...", cwd=MODULE_DIR),
            outputs=_captured("staticcheck", "json"),
            semantics=findings_zero,
            output_schema="staticcheck-json-v1",
            timeout=110,
            needs_lock=True,
        ),
        _analysis(
            ids,
            context,
            analyzer="gosec",
            tool="gosec",
            argv=_run(
                "gosec",
                110,
                # gosec's `-out` writes a file *only when it has a finding to report*: a clean run
                # leaves the path non-existent, which is indistinguishable from a crash that
                # printed nothing. Its JSON goes to stdout, so capturing that stream is what makes
                # "clean" an observable fact rather than an absence. `-quiet` is deliberately not
                # used: it suppresses the per-issue lines that make a partial run legible.
                "gosec",
                "-fmt=json",
                "-confidence=low",
                "-severity=low",
                "./...",
                cwd=MODULE_DIR,
            ),
            outputs=_captured("gosec", "json"),
            semantics=findings_zero,
            output_schema="gosec-json-v1",
            timeout=110,
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
                f"{GUEST}/pcb_go_scan.py",
                "--root",
                "/workspace",
                "--output",
                "out/context.json",
                "--opportunities",
                ",".join(quality.opportunity_tags),
                *_absolute(candidate_sources),
            ),
            outputs=(PlanOutput(path="out/context.json", format="json"),),
            semantics=findings_zero,
            output_schema="pcb-go-scan-v1",
            timeout=60,
        ),
    ]
    if quality.race_runs:
        (overlay,) = _race_overlay(task)
        race_timeout = 110
        # The mirror of the measurement guard: a race plan is only worth running where the detector
        # can actually observe a race. Without this, a mis-pointed recipe would report "clean".
        ids.require_instrumented("runtime")
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="race",
                tool="go-test-race",
                argv=_run(
                    "race",
                    race_timeout,
                    *_go("test", "-race", "-count=1", "./..."),
                    merge=True,
                    cwd=MODULE_DIR,
                ),
                outputs=_captured("race", "jsonl"),
                # The detector reports a race through its own output, never through the exit status.
                semantics=ExitSemantics(success=(0,), findings=(GO_FINDINGS,), error=TOOL_ERRORS),
                output_schema="go-race-text-v1",
                # The race detector needs cgo, so this is the one plan built with CGO_ENABLED=1 and
                # it runs in the runtime image: instrumentation is a property of the run, not of
                # the analyzer set, and it must never be the release measurement lane.
                environment={"CGO_ENABLED": "1"},
                timeout=race_timeout,
                extra_inputs=overlay,
                ownership={"measured-data-race": ScoreDimension.ROBUSTNESS},
                needs_lock=True,
                recipe="runtime",
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
                    f"{GUEST}/pcb_go_mod_audit.py",
                    "--module",
                    f"{MODULE_DIR}/go.mod",
                    "--sum",
                    f"{MODULE_DIR}/go.sum",
                    "--advisories",
                    f"{RULES}/advisories/snapshot.json",
                    "--output",
                    "out/dependency.json",
                ),
                outputs=(PlanOutput(path="out/dependency.json", format="json"),),
                semantics=findings_zero,
                output_schema="pcb-go-mod-audit-v1",
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
    # The workload is a task-supplied Go program (an overlay at cmd/pcb_workload) built with the
    # performance image's release profile; it prints one JSON line of measurements.
    timeout = declared.hard_timeout_seconds
    argv = _run(
        "perf",
        timeout,
        *_go("run", "./cmd/pcb_workload", "--scale", "{scale}", "--seed", "{seed}"),
        cwd=MODULE_DIR,
    )
    assert_release(argv, where="the Go performance plan")
    # Flag identity is checked above; recipe identity is checked here. A measurement plan must
    # target a recipe that declares no instrumentation, so an instrumented result cannot enter the
    # performance lane even if a later recipe change or a hand-edited manifest pointed one there.
    ids.require_release_recipe("performance")
    iteration = ExecutionPlan(
        **_base(  # type: ignore[arg-type]
            ids,
            plan_id="go.performance.iteration",
            argv=argv,
            tool=ids.tool("go-build", recipe="performance"),
            parser_id="go-perf",
            recipe="performance",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                *_module_inputs(quality.module_files),
                PlanInput(path=f"{WORKLOAD_DIR}/main.go", role="overlay"),
            ),
            outputs=_captured("perf", "text"),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(GO_FINDINGS,), error=TOOL_ERRORS),
        )
    )
    return PerformancePlan(
        plan_id="go.performance",
        reference_digest=None,
        language_runtime=f"go {ids.performance.go}",
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
