"""Python language-plugin conformance run (Technical Spec 18.5, Architecture 7; E2E-15, E2E-16).

Executes the public ``top-words`` fixture package and fault-injected plans in the local Docker
sandbox and records one case per conformance category: valid solution, incorrect solution, known
anti-pattern, analyzer failure, missing dependency, timeout and profile applicability. The report
is development-tier evidence; production isolation is a separate (deferred) gate.

    uv run python scripts/python_conformance.py --report REPORT.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from polycodebench_core.models import MeasurementStatus
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_evaluation.suite_admission import SuiteAdmission, variant_files
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, AnalysisPlan, TaskDraft
from polycodebench_plugins_api.admission import (
    ConformanceCaseResult,
    ConformanceReport,
    make_conformance_report,
)
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"
CAPTURE = "/opt/pcb/guest/pcb_capture.py"
DEFECTS = """import subprocess


def run(cmd, cache=[]):
    cache.append(cmd)
    try:
        return subprocess.run(cmd, shell=True, check=False)
    except:
        pass
"""


def _files() -> dict[str, bytes]:
    return {
        p.relative_to(FIXTURE).as_posix(): p.read_bytes()
        for area in ("visible", "hidden", "admission")
        for p in sorted((FIXTURE / area).rglob("*"))
        if p.is_file()
    }


def _case(
    category: str, name: str, passed: bool, expected: str, observed: str
) -> ConformanceCaseResult:
    return ConformanceCaseResult(
        category=category,  # type: ignore[arg-type]
        name=name,
        passed=bool(passed),
        expected=expected[:300],
        observed=observed[:600],
    )


def _faulty_plan(
    plan: AnalysisPlan, tool: str, command: tuple[str, ...], timeout: int | None = None
) -> AnalysisPlan:
    argv = (
        "python",
        "-B",
        CAPTURE,
        "--stdout",
        f"out/{tool}.json",
        "--stderr",
        f"out/{tool}.err",
        "--",
        *command,
    )
    update: dict[str, Any] = {"argv": argv}
    if timeout is not None:
        update["resources"] = plan.resources.model_copy(update={"timeout_seconds": timeout})
    return plan.model_copy(update=update)


async def run_conformance() -> ConformanceReport:
    plugin = PythonLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "python-conformance-state",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    engine = SuiteAdmission(
        plugin, runner, image_digests=(ids.runtime.digest, ids.evaluator.digest)
    )
    files = _files()
    manifest = yaml.safe_load((FIXTURE / "manifest.yaml").read_text(encoding="utf-8"))
    draft = TaskDraft(
        task_id="conformance-top-words", primary_language="python", manifest=manifest, files=files
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    overlay = {
        p.removeprefix("hidden/"): d
        for p, d in files.items()
        if p.startswith(("hidden/tests/", "hidden/perf/"))
    }
    cases: list[ConformanceCaseResult] = []

    async def evaluate(name: str, solution: str, reps: int = 1):  # type: ignore[no-untyped-def]
        return await engine.evaluate(
            view=view,
            candidate_files=variant_files(files, solution),
            overlay=overlay,
            label=name,
            repetitions=reps,
        )

    # 1. valid solution ---------------------------------------------------------------
    reference, alternative = await asyncio.gather(
        evaluate("conf-ref", "hidden/reference/solution.py"),
        evaluate("conf-alt", "admission/alternative-sorted/solution.py"),
    )
    cases.append(
        _case(
            "valid_solution",
            "reference-and-alternative-pass",
            reference.gate == "pass" and alternative.gate == "pass",
            "both valid solutions pass every required group",
            f"reference={reference.gate} alternative={alternative.gate}",
        )
    )

    # 2. incorrect solution -----------------------------------------------------------
    faulty = await evaluate("conf-faulty", "admission/faulty-ties/solution.py")
    wanted = "tests/test_acceptance.py::test_ties_break_alphabetically"
    cases.append(
        _case(
            "incorrect_solution",
            "wrong-tie-break-fails-its-case",
            faulty.gate == "fail" and wanted in faulty.failed_cases,
            f"gate fail naming {wanted}",
            f"gate={faulty.gate} failed={faulty.failed_cases}",
        )
    )
    syntax = await engine.evaluate(
        view=view,
        candidate_files={"solution.py": b"def top_words(:\n    pass\n"},
        overlay=overlay,
        label="conf-syntax",
        repetitions=1,
    )
    cases.append(
        _case(
            "incorrect_solution",
            "syntax-error-is-a-candidate-failure",
            syntax.gate == "fail" and syntax.reasons[0].startswith("build:"),
            "build failure, gate fail (not incomplete)",
            f"gate={syntax.gate} {syntax.reasons}",
        )
    )

    # 3. known anti-pattern ----------------------------------------------------------
    defective_files = variant_files(files, "admission/quality-defective/solution.py")
    defective = await evaluate("conf-defective", "admission/quality-defective/solution.py")
    observed, scans = await engine.analyze(
        view=view, candidate_files=defective_files, label="conf-defect"
    )
    families = {
        o.issue_key.split(".")[1]
        for o in observed
        if o.status == MeasurementStatus.MEASURED and o.issue_key
    }
    result = plugin.python_profile.evaluate(
        opportunities=dict(view.quality["opportunities"]),  # type: ignore[arg-type]
        observations=observed,
        required_tools=("ruff", "context"),
    )
    unique = {i.item_id: i.unique_violations for i in result.diagnostic}
    cases.append(
        _case(
            "anti_pattern",
            "defects-seen-despite-passing-tests",
            defective.gate == "pass"
            and {"mutable-default", "bare-except", "manual-counter"} <= families
            and unique["error_handling"] >= 2
            and unique["readability_idioms"] >= 1,
            "functional gate passes; analyzers and profile record the defects",
            f"gate={defective.gate} families={sorted(families)} violations={unique}",
        )
    )

    # 4. analyzer failure (E2E-16) ------------------------------------------------------
    candidate = {"solution.py": DEFECTS.encode()}
    context = AnalysisContext(
        task=view, candidate_digest="sha256:" + "4" * 64, candidate_paths=("solution.py",)
    )
    plans = {p.analyzer_id: p for p in plugin.analysis_plans(context)}

    async def analyse(plan: AnalysisPlan):  # type: ignore[no-untyped-def]
        run = await runner.run(
            plan,
            materialize_inputs(plan, {"candidate": candidate}),
            stage_id=f"conf-{plan.analyzer_id}",
        )
        return run, plugin.parse_analysis(run.reader(), plan)

    findings_exit = {tool: await analyse(plans[tool]) for tool in ("ruff", "bandit")}
    parsed_ok = all(
        run.record.exit_code == 1
        and next(o for o in obs if o.check_id == f"python.{tool}.scan").status
        == MeasurementStatus.MEASURED
        and sum(o.status == MeasurementStatus.MEASURED for o in obs) > 1
        for tool, (run, obs) in findings_exit.items()
    )
    cases.append(
        _case(
            "analyzer_failure",
            "findings-exit-is-parsed-as-findings",
            parsed_ok,
            "exit 1 with a valid report yields findings, not a failed scan",
            str({t: (r.record.exit_code, len(o)) for t, (r, o) in findings_exit.items()}),
        )
    )
    injected = {
        "bandit-crash-exit-1": (
            "bandit",
            plans["bandit"],
            ("python", "-c", "import sys; sys.stderr.write('Traceback'); sys.exit(1)"),
            None,
        ),
        "ruff-bad-config": (
            "ruff",
            plans["ruff"],
            (
                "ruff",
                "check",
                "--config",
                "/nonexistent.toml",
                "--output-format",
                "json",
                "work/solution.py",
            ),
            None,
        ),
        "semgrep-missing-tool": ("semgrep", plans["semgrep"], ("no-such-semgrep", "scan"), None),
        "context-hang-timeout": (
            "context",
            plans["context"],
            ("python", "-c", "while True: pass"),
            3,
        ),
    }
    for name, (tool, plan, command, timeout) in injected.items():
        broken = _faulty_plan(plan, tool, command, timeout)
        run, obs = await analyse(broken)
        scan_obs = next(o for o in obs if o.check_id == f"python.{tool}.scan")
        incomplete = (
            scan_obs.status == MeasurementStatus.MISSING
            and scan_obs.value is None
            and len(obs) == 1
        )
        cases.append(
            _case(
                "analyzer_failure",
                name,
                incomplete,
                "required scan is MISSING (never clean) and yields no findings",
                f"exit={run.record.exit_code} timed_out={run.record.timed_out} "
                f"status={scan_obs.status.value}",
            )
        )
    broken_result = plugin.python_profile.evaluate(
        opportunities={"error_handling": 2, "lint_style": 1},
        observations=[
            *findings_exit["ruff"][1],
            *(await analyse(_faulty_plan(plans["context"], "context", ("no-such-tool",))))[1],
        ],
        required_tools=("ruff", "context"),
    )
    states = {i.item_id: i.status for i in broken_result.diagnostic if i.status != "not_applicable"}
    cases.append(
        _case(
            "analyzer_failure",
            "dependent-profile-items-are-missing-not-perfect",
            states == {"error_handling": "missing", "lint_style": "measured"}
            and broken_result.diagnostic_score_bp is None,
            "items fed by the failed scan are missing; no aggregate score is invented",
            str(states),
        )
    )

    # 5. missing dependency ----------------------------------------------------------
    missing_import = {
        "solution.py": b"import numpy_not_installed\n\n\ndef top_words(lines, k):\n    return []\n"
    }
    dep = await engine.evaluate(
        view=view,
        candidate_files=missing_import,
        overlay=overlay,
        label="conf-missing-dep",
        repetitions=1,
    )
    cases.append(
        _case(
            "missing_dependency",
            "candidate-import-error-is-a-candidate-failure",
            dep.gate == "fail" and any("candidate_collection_error" in r for r in dep.reasons),
            "unavailable third-party module fails the candidate at import (no online install)",
            f"gate={dep.gate} reasons={dep.reasons}",
        )
    )
    advisory = plugin.analysis_plans(
        AnalysisContext(
            task=view.model_copy(update={"dependency_inventory": ("requests",)}),
            candidate_digest="sha256:" + "4" * 64,
            candidate_paths=("solution.py",),
        )
    )[-1]
    config = {"config/dependencies.json": json.dumps({"requests": "2.0.0"}).encode()}
    run = await runner.run(
        advisory,
        materialize_inputs(advisory, {"candidate": candidate, "config": config}),
        stage_id="conf-advisory",
    )
    obs = plugin.parse_analysis(run.reader(), advisory)
    cases.append(
        _case(
            "missing_dependency",
            "absent-advisory-snapshot-is-never-clean",
            advisory.analyzer_id == "dependency"
            and obs[0].status == MeasurementStatus.MISSING
            and len(obs) == 1,
            "dependency check without its pinned advisory snapshot is a missing scan",
            f"exit={run.record.exit_code} status={obs[0].status.value}",
        )
    )

    # 6. timeout ---------------------------------------------------------------------
    soft, hard = await asyncio.gather(
        evaluate("conf-soft", "admission/timeout-case/solution.py"),
        evaluate("conf-hard", "admission/timeout-hard/solution.py"),
    )
    cases.append(
        _case(
            "timeout",
            "case-and-suite-timeouts-are-candidate-failures",
            soft.gate == "fail"
            and hard.gate == "fail"
            and any("case_timeout" in r for r in soft.reasons)
            and any(r.startswith("candidate_timeout:") for r in hard.reasons),
            "per-case alarm and hard supervisor deadline both end as candidate failures",
            f"soft={soft.reasons[:2]} hard={hard.reasons[:1]}",
        )
    )

    # 7. profile applicability --------------------------------------------------------
    typed_source = (
        "def add(a: int, b: int) -> str:\n    return a + b\n\n\ndef bare(x):\n    return x\n"
    )
    typed = {"solution.py": typed_source.encode()}
    typed_view = view.model_copy(
        update={
            "quality": {
                **view.quality,
                "typing_expectation": "required",
                "required_analyzers": ["ruff", "bandit", "context", "mypy"],
            },
            "required_analyzers": ("ruff", "bandit", "context", "mypy"),
        }
    )
    typed_obs, _ = await engine.analyze(view=typed_view, candidate_files=typed, label="conf-typed")
    untyped_obs, _ = await engine.analyze(view=view, candidate_files=typed, label="conf-untyped")
    with_types = plugin.python_profile.evaluate(
        opportunities={"type_hints": 2}, observations=typed_obs, required_tools=("context", "mypy")
    )
    without = plugin.python_profile.evaluate(
        opportunities={"lint_style": 1}, observations=untyped_obs, required_tools=("context",)
    )
    hints = next(i for i in with_types.diagnostic if i.item_id == "type_hints")
    na = next(i for i in without.diagnostic if i.item_id == "type_hints")
    cases.append(
        _case(
            "profile_applicability",
            "type-expectation-is-contextual",
            hints.status == "measured"
            and hints.unique_violations >= 2
            and na.status == "not_applicable"
            and not any(o.check_id.startswith("python.mypy.") for o in untyped_obs),
            "type findings count only when the task contract expects types; otherwise N/A",
            f"required: {hints.unique_violations} violations; none: {na.status}",
        )
    )
    return make_conformance_report(
        plugin_id="python",
        plugin_version="0.1.0",
        execution_tier="development_sandbox",
        image_digests=(ids.runtime.digest, ids.evaluator.digest),
        cases=tuple(cases),
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python_conformance")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(run_conformance())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    for case in report.cases:
        print(
            ("PASS" if case.passed else "FAIL"), case.category, case.name, "|", case.observed[:110]
        )
    print(f"conformance passed={report.passed} digest={report.report_digest}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
