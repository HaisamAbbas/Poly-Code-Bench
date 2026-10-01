"""Admission decision logic: wrong or surprising variant behaviour must fail the report (offline).

The executable evidence is produced in Docker (see test_python_conformance_docker.py); here the
engine's verdict logic is exercised with synthetic per-variant outcomes so that every way a task
oracle could be wrong - flaky reference, faulty variant that passes, undetected defect, timeout
misclassified as a harness error - is shown to block admission.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from polycodebench_core.models import MeasurementStatus, Observation
from polycodebench_evaluation.suite_admission import (
    PENDING_GATES,
    CandidateEvaluation,
    SuiteAdmission,
    variant_files,
)
from polycodebench_lang_python import PythonLanguagePlugin
from python_plugin_support import draft, frozen, manifest, package_files

plugin = PythonLanguagePlugin()
VIEW = frozen(plugin)
MANIFEST = manifest()
FILES = package_files()
PRECHECK = {"strict-schema": (True, "ok")}


def evaluation(gates: tuple[str, ...], **extra: Any) -> CandidateEvaluation:
    return CandidateEvaluation(
        gates=gates,  # type: ignore[arg-type]
        reasons=extra.get("reasons", ()),
        failed_cases=extra.get("failed", ()),
        outcome_digests=extra.get("digests", tuple("sha256:" + "a" * 64 for _ in gates)),
        durations_ms=(1,),
    )


def _observation(**fields: Any) -> Observation:
    base: dict[str, Any] = {
        "schema_version": 1,
        "kind": "observation",
        "tool_digest": "sha256:" + "1" * 64,
        "candidate_digest": "sha256:" + "2" * 64,
        "severity": None,
        "confidence": None,
        "location": None,
        "baseline_relation": None,
        "issue_key": None,
        "primary_owner": None,
        "raw_artifact_ids": [],
        "explanation": None,
    }
    return Observation.model_validate_json(json.dumps({**base, **fields}))


def observation(check: str, key: str | None) -> Observation:
    return _observation(
        check_id=check, status="measured", value=True, confidence="high", issue_key=key
    )


def scan(tool: str, status: str = "measured") -> Observation:
    return _observation(
        check_id=f"python.{tool}.scan", status=status, value=0 if status == "measured" else None
    )


def good_outcomes() -> list[tuple[dict[str, Any], dict[str, bytes], CandidateEvaluation]]:
    result = []
    for fixture in copy.deepcopy(MANIFEST["fixtures"]):
        files = variant_files(FILES, fixture["solution_path"])
        variant = fixture["variant"]
        if variant == "reference":
            outcome = evaluation(("pass",) * 5)
        elif variant == "faulty":
            outcome = evaluation(("fail",), failed=tuple(fixture["expectation"]["failing_cases"]))
        elif variant == "timeout":
            outcome = evaluation(("fail",), reasons=("candidate_timeout:tests/x.py::t",))
        else:
            outcome = evaluation(("pass",))
        result.append((fixture, files, outcome))
    return result


def good_analyses() -> dict[str, tuple[list[Observation], tuple[str, ...]]]:
    scans = tuple(f"python.{t}.scan=measured" for t in ("bandit", "context", "ruff"))
    families = [
        observation("python.context.x", f"py.{family}.abc")
        for family in ("mutable-default", "bare-except", "manual-counter")
    ]
    return {"reference": ([], scans), "quality-defective": (families, scans)}


def report(outcomes=None, analyses=None, smoke=(True, "ok"), validation=None):  # type: ignore[no-untyped-def]
    engine = SuiteAdmission(plugin, runner=None, image_digests=("sha256:" + "5" * 64,))  # type: ignore[arg-type]
    return engine._report(
        MANIFEST["task"],
        "sha256:" + "6" * 64,
        validation or plugin.validate_task(draft()),
        outcomes or good_outcomes(),
        analyses or good_analyses(),
        list(MANIFEST["output_contract"]["allowed_paths"]),
        VIEW,
        PRECHECK,
        smoke,
    )


def failed(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {c.check_id for c in result.checks if c.status != "pass"}


def test_a_correct_package_passes_executable_admission_but_not_quality_admission() -> None:
    result = report()
    assert failed(result) == set() and result.executable_admission_passed
    assert result.quality_admission == "pending" and result.pending_gates == PENDING_GATES
    assert any("Prompt 13" in gate for gate in result.pending_gates)


def mutate(variant: str, outcome: CandidateEvaluation):  # type: ignore[no-untyped-def]
    return [
        (f, files, outcome if f["variant"] == variant else o) for f, files, o in good_outcomes()
    ]


def test_a_failing_or_flaky_reference_blocks_admission() -> None:
    assert "reference-acceptance" in failed(
        report(mutate("reference", evaluation(("pass",) * 4 + ("fail",))))
    )
    flaky = evaluation(("pass",) * 5, digests=tuple(f"sha256:{i}" * 1 + "0" * 63 for i in "abcde"))
    assert failed(report(mutate("reference", flaky))) == {"five-reference-repetitions"}
    incomplete = evaluation(("incomplete",) * 5)
    assert "reference-acceptance" in failed(report(mutate("reference", incomplete)))
    too_few = evaluation(("pass",) * 3)
    assert "five-reference-repetitions" in failed(report(mutate("reference", too_few)))


def test_a_faulty_variant_must_fail_for_its_declared_reason() -> None:
    assert "known-fault-rejection" in failed(report(mutate("faulty", evaluation(("pass",)))))
    wrong_reason = evaluation(("fail",), failed=("tests/other.py::t",))
    assert "known-fault-rejection" in failed(report(mutate("faulty", wrong_reason)))
    assert "known-fault-rejection" in failed(report(mutate("faulty", evaluation(("incomplete",)))))


def test_an_alternative_valid_solution_must_pass() -> None:
    assert "alternative-solution-acceptance" in failed(
        report(mutate("alternative", evaluation(("fail",))))
    )


def test_timeouts_must_be_candidate_failures_not_harness_errors() -> None:
    harness = evaluation(("incomplete",), reasons=("harness_failure:no report",))
    assert "timeout-variants-rejected" in failed(report(mutate("timeout", harness)))
    escaped = evaluation(("pass",))
    assert "timeout-variants-rejected" in failed(report(mutate("timeout", escaped)))


def test_a_quality_defective_variant_must_pass_tests_and_show_its_defect() -> None:
    assert "quality-defect-detected" in failed(
        report(mutate("quality_defective", evaluation(("fail",))))
    )
    analyses = good_analyses()
    analyses["quality-defective"] = (
        [observation("python.context.x", "py.bare-except.abc")],
        analyses["reference"][1],
    )
    assert "quality-defect-detected" in failed(report(analyses=analyses))
    not_measured = good_analyses()
    miss = observation("python.context.x", "py.mutable-default.abc").model_copy(
        update={"status": MeasurementStatus.NOT_APPLICABLE, "value": None}
    )
    not_measured["quality-defective"] = (
        [miss, *not_measured["quality-defective"][0][1:]],
        not_measured["reference"][1],
    )
    assert "quality-defect-detected" in failed(report(analyses=not_measured))


def test_required_analyzers_must_complete_on_the_reference() -> None:
    analyses = good_analyses()
    analyses["reference"] = (
        [],
        ("python.bandit.scan=measured", "python.context.scan=measured", "python.ruff.scan=missing"),
    )
    assert failed(report(analyses=analyses)) == {"required-analyzer-compatibility"}
    analyses["reference"] = ([], ("python.bandit.scan=measured", "python.context.scan=measured"))
    assert "required-analyzer-compatibility" in failed(report(analyses=analyses))


def test_files_outside_the_output_contract_and_broken_workloads_block_admission() -> None:
    outcomes = good_outcomes()
    fixture, files, outcome = outcomes[0]
    outcomes[0] = (fixture, {**files, "extra.py": b"x = 1\n"}, outcome)
    assert f"output-contract-{fixture['name']}" in failed(report(outcomes))
    assert "performance-workload-smoke" in failed(
        report(smoke=(False, "verifier rejected reference"))
    )


def test_structural_validation_failures_block_admission() -> None:
    bad = dict(package_files())
    del bad["admission/exposure-rights.json"]
    assert "plugin-task-validation" in failed(
        report(validation=plugin.validate_task(draft(files=bad)))
    )


@pytest.mark.parametrize(
    "variant", ["reference", "faulty", "alternative", "timeout", "quality_defective"]
)
def test_every_variant_kind_is_required(variant: str) -> None:
    outcomes = [o for o in good_outcomes() if o[0]["variant"] != variant]
    assert not report(outcomes).executable_admission_passed
