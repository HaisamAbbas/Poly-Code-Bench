"""Build, test, analysis and performance plans: typed argument vectors, never shell strings."""

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

from polycodebench_lang_python.identities import GUEST_ROOT, ImageIdentities
from polycodebench_lang_python.taskspec import oracle_from_mapping, quality_from_mapping

GUEST = f"{GUEST_ROOT}/guest"
RULES = f"{GUEST_ROOT}/rules"
BASE_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
SEMGREP_ENV = {
    "SEMGREP_SETTINGS_FILE": "/tmp/pcb-semgrep/settings.yml",
    "SEMGREP_VERSION_CACHE_PATH": "/tmp/pcb-semgrep/version",
    "SEMGREP_LOG_FILE": "/tmp/pcb-semgrep/semgrep.log",
    "SEMGREP_SEND_METRICS": "off",
    "XDG_CACHE_HOME": "/tmp",
}
MIB = 1024**2
# 126/127 come from the capture wrapper: tool not executable / not found (missing dependency).
TOOL_ERRORS = (2, 126, 127)


def resources(timeout: int, *, memory_mib: int = 512) -> ResourcePolicy:
    return ResourcePolicy(
        cpu_millis=1000,
        memory_bytes=memory_mib * MIB,
        pids_limit=128,
        disk_bytes=128 * MIB,
        timeout_seconds=timeout,
        max_output_bytes=MIB,
    )


def _candidate_inputs(paths: tuple[str, ...]) -> tuple[PlanInput, ...]:
    return tuple(PlanInput(path=f"work/{p}", role="candidate") for p in paths)


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
    image_kind: Literal["runtime", "evaluator"] = "evaluator",
) -> dict[str, object]:
    image = ids.images[image_kind]
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


def build_plan(ids: ImageIdentities, task: FrozenTask, candidate: Candidate) -> BuildPlan:
    paths = tuple(p for p in task.required_outputs if p.endswith(".py"))
    if not paths:
        raise ValueError("a Python task needs at least one required .py output")
    fields = _base(
        ids,
        plan_id="python.build",
        argv=(
            "python",
            "-B",
            f"{GUEST}/pcb_syntax_check.py",
            "--root",
            "/workspace",
            "--output",
            "out/build.json",
            *[f"work/{p}" for p in paths],
        ),
        tool=ids.tool("syntax-check", image_kind="runtime"),
        parser_id="python-build",
        image_kind="runtime",
        inputs=_candidate_inputs(paths),
        outputs=(PlanOutput(path="out/build.json", format="json"),),
        scope=paths,
        timeout=30,
        semantics=ExitSemantics(success=(0,), findings=(1,), error=(2, 126, 127)),
    )
    return BuildPlan(**fields)  # type: ignore[arg-type]


def make_test_plan(ids: ImageIdentities, task: FrozenTask) -> TestPlan:
    oracle = oracle_from_mapping(task.inventory)
    groups = []
    for group in oracle.groups:
        files = tuple(group.files)
        fields = _base(
            ids,
            plan_id=f"python.test.{group.group_id}",
            argv=(
                "python",
                "-B",
                "-m",
                "pytest",
                "-p",
                "pcb_pytest_report",
                "-c",
                f"{RULES}/pytest.ini",
                "--rootdir",
                "/workspace",
                "--pcb-report",
                f"out/{group.group_id}.jsonl",
                "--pcb-case-timeout",
                str(oracle.case_timeout_seconds),
                "--pcb-hypothesis-examples",
                str(oracle.hypothesis_examples),
                *files,
            ),
            tool=ids.tool("pytest", image_kind="runtime"),
            parser_id="python-pytest",
            image_kind="runtime",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                *(PlanInput(path=p, role="overlay") for p in files),
            ),
            outputs=(PlanOutput(path=f"out/{group.group_id}.jsonl", format="jsonl"),),
            scope=tuple(task.required_outputs),
            timeout=oracle.suite_timeout_seconds,
            semantics=ExitSemantics(success=(0,), findings=(1,), error=(2, 3, 4, 5, 126, 127)),
            environment={"PYTHONPATH": f"{GUEST}:/workspace/work"},
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
    return TestPlan(groups=tuple(groups), expected_inventory_digest=task.inventory_digest)


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
    timeout: int = 60,
    extra_inputs: tuple[PlanInput, ...] = (),
    ownership: Mapping[str, ScoreDimension | None] | None = None,
) -> AnalysisPlan:
    task = context.task
    fields = _base(
        ids,
        plan_id=f"python.analysis.{analyzer}",
        argv=argv,
        tool=ids.tool(tool),
        parser_id=f"python-{analyzer}",
        inputs=(*_candidate_inputs(context.candidate_paths), *extra_inputs),
        outputs=outputs,
        scope=tuple(context.candidate_paths),
        timeout=timeout,
        semantics=semantics,
        environment=environment,
    )
    return AnalysisPlan(
        **fields,  # type: ignore[arg-type]
        analyzer_id=analyzer,
        candidate_digest=context.candidate_digest,
        required=analyzer in task.required_analyzers,
        output_schema=output_schema,
        evidence_ownership=dict(ownership or {}),
        baseline_reusable=True,
    )


def _capture(name: str, *command: str) -> tuple[str, ...]:
    return (
        "python",
        "-B",
        f"{GUEST}/pcb_capture.py",
        "--stdout",
        f"out/{name}.json",
        "--stderr",
        f"out/{name}.err",
        "--",
        *command,
    )


def _outputs(name: str, fmt: str = "json") -> tuple[PlanOutput, ...]:
    return (
        PlanOutput(path=f"out/{name}.json", format=fmt),  # type: ignore[arg-type]
        PlanOutput(path=f"out/{name}.err", format="text", required=False),
    )


def analysis_plans(ids: ImageIdentities, context: AnalysisContext) -> list[AnalysisPlan]:
    task = context.task
    quality = quality_from_mapping(task.quality)
    work = tuple(f"work/{p}" for p in context.candidate_paths)
    findings = ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS)
    plans = [
        _analysis(
            ids,
            context,
            analyzer="ruff",
            tool="ruff",
            argv=_capture(
                "ruff",
                "ruff",
                "check",
                "--config",
                f"{RULES}/ruff.toml",
                "--output-format",
                "json",
                "--no-cache",
                *work,
            ),
            outputs=_outputs("ruff"),
            semantics=findings,
            output_schema="ruff-json-v1",
        ),
        _analysis(
            ids,
            context,
            analyzer="bandit",
            tool="bandit",
            argv=_capture(
                "bandit", "bandit", "-q", "-f", "json", "-c", f"{RULES}/bandit.yaml", *work
            ),
            outputs=_outputs("bandit"),
            semantics=findings,
            output_schema="bandit-json-v1",
            ownership={"canonical-security-issue": ScoreDimension.SECURITY},
        ),
        _analysis(
            ids,
            context,
            analyzer="semgrep",
            tool="semgrep",
            argv=_capture(
                "semgrep",
                "semgrep",
                "scan",
                "--config",
                f"{RULES}/semgrep-rules.yml",
                "--json",
                "--metrics=off",
                "--disable-version-check",
                "--quiet",
                "--error",
                *work,
            ),
            outputs=_outputs("semgrep"),
            semantics=findings,
            output_schema="semgrep-json-v1",
            environment=SEMGREP_ENV,
            timeout=90,
            ownership={"canonical-security-issue": ScoreDimension.SECURITY},
        ),
        _analysis(
            ids,
            context,
            analyzer="context",
            tool="context-scan",
            argv=(
                "python",
                "-B",
                f"{GUEST}/pcb_context_scan.py",
                "--root",
                "/workspace",
                "--output",
                "out/context.json",
                "--typing-expectation",
                quality.typing_expectation,
                "--opportunities",
                ",".join(quality.opportunity_tags),
                *work,
            ),
            outputs=(PlanOutput(path="out/context.json", format="json"),),
            semantics=ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS),
            output_schema="pcb-context-scan-v1",
        ),
    ]
    if quality.typing_expectation == "required" or "mypy" in task.required_analyzers:
        config = "mypy-strict.ini" if quality.typing_expectation == "required" else "mypy.ini"
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="mypy",
                tool="mypy",
                argv=_capture(
                    "mypy",
                    "mypy",
                    "--config-file",
                    f"{RULES}/{config}",
                    "--output",
                    "json",
                    "--no-error-summary",
                    *work,
                ),
                outputs=_outputs("mypy", "jsonl"),
                semantics=findings,
                output_schema="mypy-json-lines-v1",
                timeout=90,
            )
        )
    if task.dependency_inventory:
        plans.append(
            _analysis(
                ids,
                context,
                analyzer="dependency",
                tool="dependency-check",
                argv=(
                    "python",
                    "-B",
                    f"{GUEST}/pcb_dependency_check.py",
                    "--inventory",
                    "config/dependencies.json",
                    "--advisories",
                    f"{GUEST_ROOT}/advisories/snapshot.json",
                    "--output",
                    "out/dependency.json",
                ),
                outputs=(PlanOutput(path="out/dependency.json", format="json"),),
                semantics=ExitSemantics(success=(0,), findings=(1,), error=TOOL_ERRORS),
                output_schema="pcb-dependency-check-v1",
                extra_inputs=(PlanInput(path="config/dependencies.json", role="config"),),
                ownership={"canonical-security-issue": ScoreDimension.SECURITY},
            )
        )
    return plans


def performance_plan(ids: ImageIdentities, task: FrozenTask) -> PerformancePlan | None:
    quality = quality_from_mapping(task.quality)
    declared = quality.performance
    if declared is None:
        return None
    module = task.required_outputs[0].removesuffix(".py").replace("/", ".")
    iteration = ExecutionPlan(
        **_base(  # type: ignore[arg-type]
            ids,
            plan_id="python.performance.iteration",
            argv=(
                "python",
                "-B",
                f"{GUEST}/pcb_perf_driver.py",
                "--candidate-dir",
                "work",
                "--module",
                module,
                "--workload",
                declared.workload_file,
                "--scale",
                "{scale}",
                "--seed",
                "{seed}",
                "--output",
                "out/perf.json",
            ),
            tool=ids.tool("perf-driver", image_kind="runtime"),
            parser_id="python-perf",
            image_kind="runtime",
            inputs=(
                *_candidate_inputs(task.required_outputs),
                PlanInput(path=declared.workload_file, role="overlay"),
            ),
            outputs=(PlanOutput(path="out/perf.json", format="json"),),
            scope=tuple(task.required_outputs),
            timeout=declared.hard_timeout_seconds,
            semantics=ExitSemantics(success=(0,), findings=(3,), error=(2, 4, 126, 127)),
            environment={"PYTHONHASHSEED": "0"},
        )
    )
    return PerformancePlan(
        plan_id="python.performance",
        reference_digest=None,
        language_runtime=ids.evaluator.python,
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
