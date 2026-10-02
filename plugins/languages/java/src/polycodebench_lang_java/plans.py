"""Build, test, analysis and performance plans: typed argument vectors, never shell strings.

Every plan runs Maven through ``pcb_java_run.py``, which captures stdout/stderr to files, enforces
the deadline from inside the guest (so a hung test leaves partial output naming the case in flight)
and removes the build directory. The project lives under ``work/`` so candidate sources, trusted
project scaffolding (``config`` - the POM and the frozen dependency lock) and hidden tests
(``overlay``) are separate, digest-checked inputs.

Maven is always invoked with ``--offline`` and ``--batch-mode``. That is not a performance
optimisation, it is the contract: a scored Java run must not be able to reach Maven Central, and a
task whose POM needs an artifact the image does not carry must fail loudly rather than silently
resolve a different version than the one admission froze.

The frozen JIT policy lives in :func:`jvm_environment`. It reads exactly one thing - the task's
admission-frozen ``measurement_mode`` - and returns the flag set baked into the performance image
for that mode. Nothing in this module consults a candidate, a timing, or anything observed at run
time, which is what PCB-23-1's DoD requires.
"""

from __future__ import annotations

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

from polycodebench_lang_java.identities import GUEST_ROOT, ImageIdentities
from polycodebench_lang_java.taskspec import oracle_from_mapping, quality_from_mapping

GUEST = f"{GUEST_ROOT}/guest"
RULES = f"{GUEST_ROOT}/rules"
POM = "work/pom.xml"
LOCK = "work/deps.lock.json"
TARGET_DIR = "/workspace/target"
#: Maven's own repository inside the pinned image. Every scored run reads it read-only.
M2 = f"{GUEST_ROOT}/m2/repository"
MIB = 1024**2
# Seconds the in-guest runner stops before the supervisor's own deadline, so the runner (which can
# keep partial output) always wins the race against the outer kill.
RUNNER_MARGIN = 3
# The local Docker driver caps one exec at 120s (the supervisor adds an 8s grace to the plan
# deadline), so no plan may declare more than this.
MAX_PLAN_SECONDS = 110
# Maven's own failure code. 1 is also "the tool reported a finding"; the analyzer plans therefore
# declare 1 as a *success-with-findings* code only where findings are expected, and the parsers
# never read an exit code as a verdict.
MAVEN_FAILED = 1
# 126/127 come from the runner: tool not executable / not found (a missing plugin is a broken image,
# not a candidate defect, and must never be read as "no findings").
TOOL_ERRORS = (2, 126, 127)
#: Analyzers whose findings arrive as a report file while Maven itself exits 0. SpotBugs' ``check``
#: goal and PMD's ``check`` goal both fail the build on violations, so this plugin uses the
#: report-generating goals (``spotbugs:check`` writes its XML then reports; ``pmd:pmd`` and
#: ``checkstyle:checkstyle`` never fail) and reads the XML instead of the status.
ANALYZER_SUCCESS = (0,)
ANALYZER_ERRORS = (MAVEN_FAILED, *TOOL_ERRORS)


def resources(timeout: int, *, memory_mib: int = 3072) -> ResourcePolicy:
    # A JVM reserves virtual address space generously and runs the compiler, the test JVM and (for
    # SpotBugs) a second JVM inside one plan, so Java needs more headroom than a native toolchain.
    return ResourcePolicy(
        cpu_millis=2000,
        memory_bytes=memory_mib * MIB,
        pids_limit=512,
        disk_bytes=512 * MIB,
        timeout_seconds=timeout,
        max_output_bytes=2 * MIB,
        executable_workspace=True,
    )


@lru_cache(maxsize=1)
def selected_rules() -> dict[str, str]:
    """The analyzer rule files the evaluator image ships, and the analyzer each belongs to.

    The rules live in this repository, not in the image, because they are part of the reviewed
    profile: their digest is ``rule_bundle_digest`` in the tool identity.
    """
    root = Path(__file__).resolve().parent / "rules"
    mapping = {
        "spotbugs": "spotbugs-exclude.xml",
        "pmd": "pmd.xml",
        "checkstyle": "checkstyle.xml",
    }
    found: dict[str, str] = {}
    for analyzer, name in mapping.items():
        candidate = root / name
        if candidate.is_file():
            found[analyzer] = f"{RULES}/{name}"
    return found


def _candidate_inputs(paths: tuple[str, ...]) -> tuple[PlanInput, ...]:
    return tuple(PlanInput(path=f"work/{p}", role="candidate") for p in paths)


def _pinned_inputs(pinned: Mapping[str, str]) -> tuple[PlanInput, ...]:
    """The POM and the frozen dependency lock, as digest-checked ``config`` inputs."""
    return tuple(
        PlanInput(path=f"work/{path}", role="config", digest=digest)
        for path, digest in sorted(pinned.items())
    )


def _run(name: str, deadline: int, *command: str, merge: bool = False) -> tuple[str, ...]:
    """The runner invocation for one Maven command (``name`` is the output prefix)."""
    return (
        "python",
        "-B",
        f"{GUEST}/pcb_java_run.py",
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
    memory_mib: int = 3072,
) -> dict[str, object]:
    image = ids.images[recipe]
    return {
        "plan_id": plan_id,
        "image": image.reference,
        "image_digest": image.digest,
        "argv": argv,
        "working_directory": ".",
        # Offline is not optional: it is what makes the frozen resolution the only resolution.
        "environment": {
            "MAVEN_OPTS": "-Dmaven.repo.local=" + M2,
            "MAVEN_CONFIG": f"{GUEST_ROOT}/maven",
            "HOME": f"{GUEST_ROOT}/home",
            "LC_ALL": "C",
            "TZ": "UTC",
            **(environment or {}),
        },
        "inputs": inputs,
        "outputs": outputs,
        "resources": resources(timeout, memory_mib=memory_mib),
        "exit_semantics": semantics,
        "tool": tool,
        "scope": scope,
        "parser_id": parser_id,
    }


def _mvn(*goals: str) -> tuple[str, ...]:
    """A Maven invocation that cannot reach a network.

    ``-o`` (``--offline``) fails loudly rather than resolving; ``-B`` disables interactive
    progress and colour so the captured stream is machine-readable; ``-Dstyle.color=never`` and
    ``-Dmaven.wagon.http.retryHandler.count=0`` remove the two remaining sources of cosmetic and
    retry-driven variation. ``-ntp`` suppresses transfer progress for the same reason.
    """
    return (
        "mvn",
        "--offline",
        "--batch-mode",
        "--no-transfer-progress",
        "-Dmaven.wagon.http.retryHandler.count=0",
        "-Dstyle.color=never",
        "-f",
        POM,
        *goals,
    )


# --------------------------------------------------------------- frozen JIT measurement policy


def jvm_environment(ids: ImageIdentities, mode: str) -> dict[str, str]:
    """The environment for one measured iteration, from the task's frozen mode.

    ``mode`` is the task's admission-frozen ``measurement_mode``. The flag set comes from the
    image's recorded ``build.jvm_measurement`` block - the same two sets for every candidate, chosen
    before any candidate existed. This function has no parameter for a candidate, a result or a
    timing, which is the structural reason a Java run cannot warm up or switch JIT policy to suit
    an individual candidate.
    """
    return {"PCB_JVM_MEASUREMENT": mode}


def jvm_argv(ids: ImageIdentities, mode: str, *command: str) -> tuple[str, ...]:
    """``java`` with the image's frozen flags for ``mode``."""
    return ("java", *ids.jvm_flags(mode), *command)


# ------------------------------------------------------------------------------- build/test


def build_plan(ids: ImageIdentities, task: FrozenTask, candidate: Candidate) -> BuildPlan:
    paths = tuple(p for p in task.required_outputs if p.endswith(".java"))
    if not paths:
        raise ValueError("a Java task needs at least one required .java output")
    quality = quality_from_mapping(task.quality)
    timeout = 100
    fields = _base(
        ids,
        plan_id="java.build",
        argv=_run("build", timeout, *_mvn("test-compile", "-DskipTests")),
        tool=ids.tool("compiler", recipe="runtime"),
        parser_id="java-build",
        recipe="runtime",
        inputs=(*_candidate_inputs(paths), *_pinned_inputs(quality.pinned_files)),
        outputs=_captured("build", "text"),
        scope=paths,
        timeout=timeout,
        # Maven exits 1 for a compile error and 0 for success; a findings exit is never used here.
        semantics=ExitSemantics(success=(0,), findings=(MAVEN_FAILED,), error=TOOL_ERRORS),
    )
    return BuildPlan(**fields)  # type: ignore[arg-type]


def make_test_plan(ids: ImageIdentities, task: FrozenTask) -> TestPlan:
    oracle = oracle_from_mapping(task.inventory)
    quality = quality_from_mapping(task.quality)
    groups = []
    for group in oracle.groups:
        selectors = ",".join(group.selectors)
        timeout = min(oracle.suite_timeout_seconds, MAX_PLAN_SECONDS)
        fields = _base(
            ids,
            plan_id=f"java.test.{group.group_id}",
            argv=_run(
                group.group_id,
                timeout,
                *_mvn(
                    "test",
                    f"-Dtest={selectors}",
                    "-Dsurefire.failIfNoSpecifiedTests=false",
                    "-Dsurefire.useFile=false",
                    # Surefire forks one JVM per module; a single fork keeps memory predictable and
                    # keeps the order of side effects deterministic for a stress group.
                    "-DforkCount=1",
                    "-DreuseForks=true",
                ),
                merge=True,
            ),
            tool=ids.tool("surefire", recipe="runtime"),
            parser_id="java-junit5",
            recipe="runtime",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                *_pinned_inputs(quality.pinned_files),
                *(PlanInput(path=f"work/{p}", role="overlay") for p in group.files),
            ),
            outputs=_captured(group.group_id, "text"),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(MAVEN_FAILED,), error=TOOL_ERRORS),
        )
        # A quality-only group is a stress or repeatability group: run it three times, because one
        # passing run of a threaded test proves nothing about stability.
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


# ---------------------------------------------------------------------------------- analysis


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
    timeout: int = 100,
    extra_inputs: tuple[PlanInput, ...] = (),
    ownership: Mapping[str, ScoreDimension | None] | None = None,
    needs_lock: bool = False,
) -> AnalysisPlan:
    task = context.task
    quality = quality_from_mapping(task.quality)
    fields = _base(
        ids,
        plan_id=f"java.analysis.{analyzer}",
        argv=argv,
        tool=ids.tool(tool, lock_digest=quality.dependency_lock_digest if needs_lock else None),
        parser_id=f"java-{analyzer}",
        inputs=(
            *_candidate_inputs(context.candidate_paths),
            *_pinned_inputs(quality.pinned_files),
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
        language_id="java",
        analyzer_id=analyzer,
        candidate_digest=context.candidate_digest,
        required=analyzer in task.required_analyzers,
        output_schema=output_schema,
        evidence_ownership=dict(ownership or {}),
        # SpotBugs and PMD report what the *candidate* contains; the baseline's report covers a
        # different candidate digest, so only genuinely task-independent probes may reuse a
        # baseline. None of the three source analyzers qualify.
        baseline_reusable=False,
    )


def _report(path: str, fmt: Literal["xml", "text"] = "xml") -> tuple[PlanOutput, ...]:
    return (PlanOutput(path=path, format=fmt),)


def analysis_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    task = context.task
    quality = quality_from_mapping(task.quality)
    candidate_sources = tuple(f"work/{p}" for p in context.candidate_paths if p.endswith(".java"))
    rules = selected_rules()
    plans: list[AnalysisPlan] = []

    # SpotBugs analyses bytecode, so it needs the project compiled in the same invocation. It exits
    # non-zero when it finds something: that is a *findings* exit, not an error, and the parser
    # reads the XML rather than the status.
    if "spotbugs" in rules:
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="spotbugs",
                tool="spotbugs",
                argv=_run(
                    "spotbugs",
                    MAX_PLAN_SECONDS,
                    *_mvn(
                        "compile",
                        "spotbugs:check",
                        f"-Dspotbugs.excludeFilterFile={rules['spotbugs']}",
                        # Low threshold + Max effort is the setting that reports real defects; the
                        # profile's rule mappings decide what actually counts.
                        "-Dspotbugs.threshold=Low",
                        "-Dspotbugs.effort=Max",
                        "-Dspotbugs.xmlOutput=true",
                        "-Dspotbugs.xmlOutputPath=target/spotbugs.xml",
                        "-Dspotbugs.failOnError=false",
                    ),
                ),
                outputs=_captured("spotbugs", "text") + _report("work/target/spotbugs.xml"),
                semantics=ExitSemantics(success=(0,), findings=(MAVEN_FAILED,), error=TOOL_ERRORS),
                output_schema="spotbugs-xml-v1",
                timeout=MAX_PLAN_SECONDS,
                needs_lock=True,
            )
        )
    # PMD analyses source, so it needs no compilation and is the cheapest of the three.
    if "pmd" in rules:
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="pmd",
                tool="pmd",
                argv=_run(
                    "pmd",
                    60,
                    *_mvn(
                        "pmd:pmd",
                        f"-Dpmd.rulesets={rules['pmd']}",
                        "-Dpmd.format=xml",
                        # PMD 6.55 rejects a targetJdk above 17; the ruleset is written for 17 and
                        # the compiler release is what actually gates the language level.
                        "-Dpmd.targetJdk=17",
                        "-Dpmd.linkXRef=false",
                    ),
                ),
                outputs=_captured("pmd", "text") + _report("work/target/pmd.xml"),
                semantics=ExitSemantics(success=(0,), findings=(), error=TOOL_ERRORS),
                output_schema="pmd-xml-v1",
                timeout=60,
            )
        )
    # Checkstyle is likewise source-only and structural.
    if "checkstyle" in rules:
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="checkstyle",
                tool="checkstyle",
                argv=_run(
                    "checkstyle",
                    60,
                    *_mvn(
                        "checkstyle:checkstyle",
                        f"-Dcheckstyle.config.location={rules['checkstyle']}",
                        "-Dcheckstyle.output.file=target/checkstyle-result.xml",
                        "-Dcheckstyle.output.format=xml",
                        "-Dcheckstyle.consoleOutput=false",
                        "-Dcheckstyle.failOnViolation=false",
                    ),
                ),
                outputs=_captured("checkstyle", "text")
                + _report("work/target/checkstyle-result.xml"),
                semantics=ExitSemantics(success=(0,), findings=(), error=TOOL_ERRORS),
                output_schema="checkstyle-xml-v1",
                timeout=60,
            )
        )

    plans.append(
        _analysis(
            ids,
            context,
            analyzer="context",
            tool="context-scan",
            argv=(
                "python",
                "-B",
                f"{GUEST}/pcb_java_scan.py",
                "--root",
                "/workspace",
                "--output",
                "out/context.json",
                "--opportunities",
                ",".join(quality.opportunity_tags),
                *candidate_sources,
            ),
            outputs=(PlanOutput(path="out/context.json", format="json"),),
            semantics=ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS),
            output_schema="pcb-java-scan-v1",
            timeout=60,
        )
    )

    if task.dependency_inventory or "dependency" in task.required_analyzers:
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="dependency",
                tool="dependency",
                # Two steps, because they answer different questions and only one of them needs a
                # network-free Maven run. `dependency:list` says what the POM resolved to *here*;
                # the guest audit then checks that resolution, and the task's frozen lock, against
                # the advisory snapshot shipped in the image. Running only Maven would mean the
                # advisories are never applied, and a scan that checked nothing would look clean.
                argv=_run(
                    "dependency-list",
                    60,
                    *_mvn(
                        "dependency:list",
                        "-DoutputFile=target/deps.txt",
                        "-DincludeScope=runtime",
                    ),
                )
                + _run(
                    "dependency-audit",
                    60,
                    "python",
                    "-B",
                    f"{GUEST}/pcb_dependency_audit.py",
                    "--report",
                    f"{TARGET_DIR}/deps.txt",
                    "--lock",
                    LOCK,
                    "--advisories",
                    f"{RULES}/advisories/snapshot.json",
                    "--output",
                    "out/dependency.json",
                ),
                outputs=_captured("dependency-list", "text")
                + _captured("dependency-audit", "text")
                + _report("work/target/deps.txt", "text")
                + (PlanOutput(path="out/dependency.json", format="json"),),
                # The audit exits 1 when it found an advisory and 0 when it did not, which is the
                # shared findings convention every analyzer plan in this repository follows. A
                # failure to *run* is distinct from a failure to find: only the runner's own codes
                # are read as a tool error.
                semantics=ExitSemantics(
                    success=(0,), findings=(1,), error=TOOL_ERRORS
                ),
                output_schema="pcb-dependency-audit-v1",
                timeout=60,
                extra_inputs=(PlanInput(path=LOCK, role="config"),),
                ownership={"canonical-security-issue": ScoreDimension.SECURITY},
                needs_lock=True,
            )
        )
    return plans


# ----------------------------------------------------------------------------- performance


def performance_plan(ids: ImageIdentities, task: FrozenTask) -> PerformancePlan | None:
    quality = quality_from_mapping(task.quality)
    declared = quality.performance
    if declared is None:
        return None
    # The frozen mode decides the JVM flags and nothing else does. Reading it here, once, from the
    # admission-frozen quality plan is what makes the mode identical for every candidate.
    mode = declared.measurement_mode
    ids.jvm_flags(mode)  # fail fast on an unknown mode rather than measuring in the other one
    timeout = declared.hard_timeout_seconds
    workload_class = declared.workload_file.removeprefix("hidden/").removesuffix(".java")
    workload_class = workload_class.removeprefix("tests/").replace("/", ".")
    iteration = ExecutionPlan(
        **_base(  # type: ignore[arg-type]
            ids,
            plan_id="java.performance.iteration",
            # A measured iteration is `java` with the image's frozen flags, not `mvn`: a Maven
            # start-up would dominate the timing and would make the number depend on the plugin
            # resolver rather than on the candidate's code.
            argv=_run(
                "perf",
                timeout,
                *jvm_argv(
                    ids,
                    mode,
                    "-cp",
                    f"{TARGET_DIR}/classes",
                    workload_class,
                    "--scale",
                    "{scale}",
                    "--seed",
                    "{seed}",
                ),
            ),
            tool=ids.tool("java", recipe="performance"),
            parser_id="java-perf",
            recipe="performance",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                *_pinned_inputs(quality.pinned_files),
                PlanInput(path=f"work/{declared.workload_file}", role="overlay"),
            ),
            outputs=_captured("perf", "text"),
            scope=tuple(task.required_outputs),
            timeout=timeout,
            semantics=ExitSemantics(success=(0,), findings=(MAVEN_FAILED,), error=TOOL_ERRORS),
            environment=jvm_environment(ids, mode),
        )
    )
    return PerformancePlan(
        plan_id="java.performance",
        reference_digest=None,
        language_runtime=f"temurin {ids.performance.java} (mode={mode})",
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
