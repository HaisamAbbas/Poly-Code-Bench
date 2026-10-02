"""Conformance harness for the C language plugin (Prompt 20, PCB-20-4; E2E-15, E2E-35).

Runs the C plugin's real plans against every authored fixture variant inside the pinned sandbox and
checks that each one produces the verdict it was authored to produce. Nothing here is a hand-written
transcript: every case is an executed plan, and a case fails if the evidence says something other
than what the manifest declared.

    .venv/Scripts/python.exe scripts/c_conformance.py [--scenario NAME ...] [--json OUT]

Exit status is 0 only when every case passed. A skipped case (a lane the task declares
``optional``) is reported as skipped and never counted as a pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, MeasurementStatus
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_lang_c import CLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, DictArtifactReader, ExecutionPlan, FrozenTask
from polycodebench_plugins_api.contracts import EXECUTION_RECORD_PATH
from polycodebench_plugins_api.results import record_bytes
from polycodebench_plugins_api.testreport import reconcile
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "plugins" / "languages" / "c" / "fixtures" / "top-words"
AREAS = ("visible", "hidden", "admission")
OUTCOME = Literal["pass", "fail", "skip"]

#: The variants, keyed by the manifest's ``solution_path``. Each entry states what the *evidence* must
#: say - not what the exit status must be, because the exit status alone is exactly the thing C makes
#: unreliable: a wrong answer, a crash and a sanitizer abort all exit non-zero.
SCENARIOS: dict[str, str] = {
    "reference": "hidden/reference/src/topwords.c",
    "faulty-ties": "admission/faulty-ties/src/topwords.c",
    "alternative": "admission/alternative-runs/src/topwords.c",
    "quality-defective": "admission/quality-defective/src/topwords.c",
    "timeout": "admission/timeout-case/src/topwords.c",
    "buffer-overflow": "admission/buffer-overflow/src/topwords.c",
    "undefined-shift": "admission/undefined-shift/src/topwords.c",
    "resource-leak": "admission/resource-leak/src/topwords.c",
    "compile-error": "admission/compile-error/src/topwords.c",
}


@dataclass
class Case:
    case_id: str
    outcome: OUTCOME
    detail: str


@dataclass
class Scenario:
    name: str
    source: str
    cases: list[Case] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(case.outcome == "fail" for case in self.cases):
            return "FAILED"
        return "passed" if any(case.outcome == "pass" for case in self.cases) else "no-evidence"


def _manifest() -> dict[str, Any]:
    loaded = yaml.safe_load((BASE / "manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _package_files() -> dict[str, bytes]:
    return {
        path.relative_to(BASE).as_posix(): path.read_bytes()
        for area in AREAS
        for path in sorted((BASE / area).rglob("*"))
        if path.is_file()
    }


def _view(plugin: CLanguagePlugin, files: dict[str, bytes], *, suite_timeout: int | None) -> FrozenTask:
    manifest = _manifest()
    from polycodebench_plugins_api import TaskDraft  # noqa: PLC0415  (import kept local and explicit)

    draft = TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="c",
        manifest=manifest,
        files=files,
    )
    view = plugin.freeze_view(draft, "sha256:" + "1" * 64)
    if suite_timeout is not None:
        view = view.model_copy(
            update={"inventory": {**view.inventory, "suite_timeout_seconds": suite_timeout}}
        )
    return view


def _sources(view: FrozenTask, files: dict[str, bytes], candidate: bytes) -> dict[str, dict[str, bytes]]:
    groups = view.inventory["groups"]
    overlay: dict[str, bytes] = {}
    for group in groups:
        for relative in group["files"]:
            overlay[f"work/{relative}"] = files[f"hidden/{relative}"]
    declared = view.quality["performance"]
    if declared:
        overlay[f"work/{declared['workload_file']}"] = files[f"hidden/{declared['workload_file']}"]
    config = {f"work/{path}": files[f"visible/repo/{path}"] for path in view.quality["scaffold_files"]}
    return {"candidate": {"src/topwords.c": candidate}, "overlay": overlay, "config": config}


def _candidate(view: FrozenTask) -> Candidate:
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


def _reader(run: Any) -> DictArtifactReader:
    """The plan's outputs *plus* the supervisor's execution record.

    Parsers classify a run from ``_execution.json`` first: without it every parser reports ``tool_error``
    by design, because a tool's own claim about how it went is not evidence. The record travels beside
    the outputs rather than inside them, so a caller has to ask for it deliberately - which is why this
    helper exists instead of the reader being built silently.
    """
    return DictArtifactReader(
        {**run.outputs, EXECUTION_RECORD_PATH: record_bytes(run.record)}
    )


# --------------------------------------------------------------------------- checks


def _gate(case: Case, expectation: str, gate: str, detail: str) -> None:
    """Acceptance gate: ``pass``, ``fail`` or ``incomplete`` must equal the authored expectation.

    ``incomplete`` is deliberately *not* accepted as a pass for a scenario that expects one. A harness
    that could not run the tests has proved nothing, and a conformance run that reported that as a
    pass would be a harness that agrees with itself.
    """
    if expectation == gate:
        case.outcome = "pass"
    else:
        case.outcome = "fail"
        case.detail = f"acceptance gate {gate!r}, expected {expectation!r}: {detail[:160]}"


def _expect_lane(
    case: Case,
    plan: ExecutionPlan,
    observations: list[Any],
    *,
    expect_findings: bool,
    families: Sequence[str] = (),
    expect_missing: bool = False,
) -> None:
    """A dynamic lane must agree with what the variant was authored to demonstrate.

    ``expect_findings`` and ``expect_missing`` are different requirements and a lane that reported
    nothing satisfies neither by accident: an absent lane is reported ``MISSING`` and an empty one
    ``MEASURED`` with a count of zero.
    """
    scan = next(
        (o for o in observations if o.check_id == f"c.{plan.analyzer_id}.scan"), None
    )
    if scan is None:
        case.outcome = "fail"
        case.detail = f"{plan.plan_id} produced no scan observation"
        return
    if expect_missing:
        case.outcome = "pass" if scan.status == MeasurementStatus.MISSING else "fail"
        if case.outcome == "fail":
            case.detail = f"{plan.plan_id} was {scan.status.value}, expected missing"
        return
    if scan.status != MeasurementStatus.MEASURED:
        case.outcome = "fail"
        case.detail = f"{plan.plan_id} was {scan.status.value}: {scan.explanation[:140]}"
        return
    found = [o for o in observations if o.issue_key is not None and o.status == MeasurementStatus.MEASURED]
    if expect_findings and not found:
        case.outcome = "fail"
        case.detail = f"{plan.plan_id} found nothing; the variant was authored to trip this lane"
        return
    if not expect_findings and found:
        case.outcome = "fail"
        case.detail = f"{plan.plan_id} reported {len(found)} finding(s) on a clean variant"
        return
    if families:
        families = {f.lower() for f in families}
        matched = [o for o in found if families & {part.lower() for part in o.check_id.split(".")}]
        if not matched:
            case.outcome = "fail"
            case.detail = (
                f"{plan.plan_id} found {sorted({o.check_id for o in found})}, "
                f"expected one of {sorted(families)}"
            )
            return
    case.outcome = "pass"
    case.detail = f"{plan.plan_id}: {scan.explanation[:140]}"


def _expect_clean_static(case: Case, plan: ExecutionPlan, observations: list[Any]) -> None:
    """A static analyzer over a correct variant must report zero findings.

    This is the check that keeps the frozen check selection honest: a rule that fires on the
    reference implementation is a rule that will charge a correct solution for writing correct C.
    """
    findings = [o for o in observations if o.issue_key is not None]
    if findings:
        case.outcome = "fail"
        case.detail = f"{plan.plan_id} reported {sorted({o.check_id for o in findings})[:4]}"
    else:
        case.outcome = "pass"


async def run_scenario(name: str, runner: PlanRunner, plugin: CLanguagePlugin) -> Scenario:
    files = _package_files()
    # The timeout variant is given the shortest suite the contract allows, so the harness does not sit
    # for the full default budget waiting for a loop that was authored not to finish.
    view = _view(plugin, files, suite_timeout=30 if name == "timeout" else None)
    sources = _sources(view, files, files[SCENARIOS[name]])
    scenario = Scenario(name=name, source=SCENARIOS[name])

    build_plan = plugin.build_plan(view, _candidate(view))
    build_run = await runner.run(build_plan, materialize_inputs(build_plan, sources), stage_id=f"c-{name}")
    build_case = Case("c.build", "pass", "")
    _gate(
        build_case,
        "fail" if name == "compile-error" else "pass",
        *plugin.parse_build(build_plan, _reader(build_run))[:2],
    )
    scenario.cases.append(build_case)

    test_plan = plugin.test_plan(view)
    inventory = list(plugin.inventory(view))
    by_group = {group.group_id: group for group in inventory}
    records: list[Any] = []
    controls: list[Any] = []
    for group_plan in test_plan.groups:
        group_run = await runner.run(
            group_plan.plan, materialize_inputs(group_plan.plan, sources), stage_id=f"c-{name}"
        )
        for repeat in range(group_plan.repetitions):
            group_records, control = plugin.parse_test_group(
                group_plan,
                by_group[group_plan.group_id],
                _reader(group_run),
                repetition=repeat,
            )
            records.extend(group_records)
            controls.append(control)
    # One reconcile over the whole inventory, not per group: the gate is defined over the *required*
    # groups, and reconciling an optional group on its own reports `incomplete` by construction. That
    # is the shared contract's rule and the harness has to ask the question the contract answers.
    verdict = reconcile(inventory, records, controls)
    expectation = "fail" if name in {"faulty-ties", "timeout", "compile-error"} else "pass"
    gate_case = Case("c.acceptance-gate", "pass", "")
    _gate(gate_case, expectation, verdict.gate, "; ".join(verdict.reasons) or "every required case passed")
    scenario.cases.append(gate_case)
    for group_verdict in verdict.groups:
        case = Case(f"c.test.{group_verdict.group_id}", "pass", "")
        failed = group_verdict.failed_cases
        if group_verdict.verdict == "incomplete":
            case.outcome = "fail"
            case.detail = f"{group_verdict.group_id}: incomplete: {'; '.join(group_verdict.reasons)[:150]}"
        else:
            case.detail = f"{group_verdict.group_id}: {group_verdict.verdict}, {failed} failing case(s)"
        scenario.cases.append(case)

    context = AnalysisContext(
        task=view, candidate_digest="sha256:" + "2" * 64, candidate_paths=("src/topwords.c",)
    )
    expected = {
        "reference": {"asan": False, "ubsan": False, "valgrind": False},
        # A correct alternative must be as clean as the reference; a leak would make it a
        # resource-defect fixture, which `resource-leak` already is (D-20-03).
        "alternative": {"asan": False, "ubsan": False, "valgrind": False},
        "quality-defective": {"asan": False, "ubsan": False, "valgrind": False},
        "faulty-ties": {"asan": False, "ubsan": False, "valgrind": True},
        "buffer-overflow": {"asan": True, "ubsan": False, "valgrind": True},
        "undefined-shift": {"asan": False, "ubsan": True, "valgrind": False},
        "resource-leak": {"asan": True, "ubsan": False, "valgrind": True},
        "timeout": {"asan": None, "ubsan": None, "valgrind": None},
        "compile-error": {"asan": None, "ubsan": None, "valgrind": None},
    }[name]
    families = {
        "buffer-overflow": {"asan": ["bounds-violation"]},
        "undefined-shift": {"ubsan": ["shift-out-of-range"]},
        "resource-leak": {"valgrind": ["resource-leak"]},
    }.get(name, {})

    for plan in plugin.analysis_plans(context):
        run = await runner.run(plan, materialize_inputs(plan, sources), stage_id=f"c-{name}")
        observations = plugin.parse_analysis(_reader(run), plan)
        case = Case(plan.plan_id, "pass", "")
        if name == "compile-error":
            # A lane that cannot compile the candidate has no evidence about the candidate. Every
            # lane must say so, and none may report a clean scan.
            _expect_lane(case, plan, observations, expect_findings=False, expect_missing=True)
        elif plan.analyzer_id in {"clang_tidy", "cppcheck"}:
            if name in {"reference", "alternative", "timeout"}:
                _expect_clean_static(case, plan, observations)
            else:
                case.outcome = "pass"
                case.detail = f"{plan.plan_id} ran and reported observations"
        elif plan.analyzer_id in expected and expected[plan.analyzer_id] is None:
            _expect_lane(case, plan, observations, expect_findings=False, expect_missing=True)
        else:
            _expect_lane(
                case,
                plan,
                observations,
                expect_findings=bool(expected[plan.analyzer_id]),
                families=families.get(plan.analyzer_id, ()),
            )
        scenario.cases.append(case)

    performance = plugin.performance_plan(view)
    if performance is not None:
        run = await runner.run(
            performance.iteration_plan,
            materialize_inputs(performance.iteration_plan, sources),
            stage_id=f"c-{name}",
        )
        case = Case(performance.iteration_plan.plan_id, "pass", "")
        record = run.record
        # Two things have to hold, and they are different claims. The *image* assertion is that
        # measurement only ever happens in the release recipe - the DoD for PCB-20-1. The *exit*
        # assertion is that a candidate which does not compile cannot produce a timing, which is the
        # ordinary build contract rather than anything to do with the lane.
        release = performance.iteration_plan.image_digest == plugin.identities.performance.digest
        measured_ok = record.exit_code == 0 and not record.timed_out
        if not release:
            case.outcome = "fail"
            case.detail = "the performance plan did not target the release recipe"
        elif measured_ok == (name != "compile-error"):
            case.detail = (
                f"built and measured in {performance.iteration_plan.image[:40]}"
                if measured_ok
                else "did not build, as a non-compiling candidate must not"
            )
        else:
            case.outcome = "fail"
            case.detail = (
                f"performance iteration exit={record.exit_code} timed_out={record.timed_out}"
            )
        scenario.cases.append(case)
    return scenario


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="c_conformance")
    parser.add_argument("--scenario", action="append", dest="scenarios", default=[])
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    names = args.scenarios or list(SCENARIOS)
    unknown = sorted(set(names) - set(SCENARIOS))
    if unknown:
        print(f"unknown scenarios: {unknown}", file=sys.stderr)
        return 2

    plugin = CLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
            ids.instrumented.reference: ids.instrumented.digest,
            ids.performance.reference: ids.performance.digest,
        },
        state_dir=ROOT / ".cache" / "c-conformance-state",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    scenarios: list[Scenario] = []
    for name in names:
        scenario = await run_scenario(name, runner, plugin)
        scenarios.append(scenario)
        for case in scenario.cases:
            print(f"{case.outcome.upper():4} {name}/{case.case_id}  {case.detail[:110]}")
        print(f"--- {name}: {scenario.verdict}")
    failed = [s for s in scenarios if s.verdict == "FAILED"]
    document = {
        "schema_version": 1,
        "kind": "c_conformance_report",
        "scenarios": [
            {
                "name": scenario.name,
                "source": scenario.source,
                "verdict": scenario.verdict,
                "cases": [
                    {"case_id": c.case_id, "outcome": c.outcome, "detail": c.detail} for c in scenario.cases
                ],
            }
            for scenario in scenarios
        ],
        "passed": not failed,
    }
    document["report_digest"] = str(
        canonical_digest({k: v for k, v in document.items() if k != "report_digest"})
    )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"wrote {args.json}")
    print(f"conformance passed={document['passed']}; report {document['report_digest']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))