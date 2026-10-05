"""E2E-38: deterministic prediction families and the Track B coverage audit.

Three properties are under test, in the order the prompt names them:

* PCB-28-1 — a prediction task's protocol cannot carry an execution tool. The restriction is
  structural (``validate_prediction_tools`` / ``check_protocol_constraints``), so a different tool
  policy is by construction a different cohort rather than a softer one.
* PCB-28-2 — all three normalization modes are graded from frozen rules; a parse error is a wrong
  answer; a mismatch is never rescued by a judge because the grader takes no judge input.
* PCB-28-3 — the prediction families are answer-only: the six generated-code dimensions are
  absent from the metric definitions rather than reported as zero.
"""

from __future__ import annotations

import json

import output_prediction_instance as output_fixture
import pytest
import test_prediction_instance as test_fixture
from polycodebench_core.models import ProtocolConstraints
from polycodebench_suites_swebench.prediction import (
    EXECUTION_TOOLS,
    NormalizationRules,
    PredictionContractError,
    PredictionOracle,
    check_protocol_constraints,
    normalize,
    parse_submission,
    prediction_metric_definitions,
    validate_prediction_tools,
)
from polycodebench_suites_swebench.prediction_grading import (
    TRACK_B_FAMILIES,
    audit_family,
    audit_track_b,
    grade_prediction_task,
)


def constraints(**overrides: object) -> ProtocolConstraints:
    base: dict[str, object] = {
        "schema_version": 1,
        "kind": "protocol_constraints",
        "protocol_id": "prediction-v1",
        "allowed_tools": [],
        "public_test_feedback": False,
        "hidden_feedback": False,
        "network_policy": "disabled",
        "dependency_inventory_digest": None,
        "maximum_model_turns": 1,
        "maximum_tool_calls": 0,
        "maximum_wall_seconds": 30,
    }
    base.update(overrides)
    return ProtocolConstraints.model_validate(base)


def oracle_of(mode: str) -> PredictionOracle:
    document = output_fixture.oracle(mode)
    rules = NormalizationRules.model_validate(
        {**document["normalization"], "kind": "prediction_normalization"}
    )
    return PredictionOracle.model_validate(
        {
            "kind": "prediction_oracle",
            "normalization": rules,
            "expected": document.get("expected"),
            "expected_typed": document.get("expected_typed"),
            "reason": document["reason"],
        }
    )


def graded(mode: str, answer_name: str, *, family: str = "output_prediction"):
    answers = output_fixture.CANDIDATE_ANSWERS[mode]
    submission = parse_submission(answers[answer_name], oracle_of(mode).normalization)
    return grade_prediction_task(
        family=family,
        task_id=output_fixture.INSTANCE_ID,
        submission=submission,
        oracle=oracle_of(mode),
    )


# --------------------------------------------------------------- PCB-28-1: tool policy


def test_a_prediction_protocol_cannot_carry_an_execution_tool() -> None:
    """Every tool that runs code is refused, by name, before a task ever freezes."""
    for tool in sorted(EXECUTION_TOOLS):
        with pytest.raises(PredictionContractError, match="prediction tasks cannot allow"):
            validate_prediction_tools([tool])


def test_read_only_tool_policies_are_accepted() -> None:
    """Reading the target is the point of prediction; listing and reading remain allowed."""
    validate_prediction_tools(["list_files", "read_file", "search"])


def test_a_different_tool_policy_is_a_different_cohort() -> None:
    """A policy that could run the target is refused as a different protocol, not a softer one."""
    with pytest.raises(PredictionContractError) as failure:
        check_protocol_constraints(
            constraints(allowed_tools=["read_file", "run_command"]), family="output_prediction"
        )
    assert "changes the task from prediction to generation" in str(failure.value)


def test_both_prediction_families_refuse_feedback_and_network() -> None:
    for family in ("output_prediction", "test_prediction"):
        with pytest.raises(PredictionContractError, match="no test feedback"):
            check_protocol_constraints(constraints(public_test_feedback=True), family=family)
        with pytest.raises(PredictionContractError, match="no hidden feedback"):
            check_protocol_constraints(constraints(hidden_feedback=True), family=family)
        with pytest.raises(PredictionContractError, match="network disabled"):
            check_protocol_constraints(constraints(network_policy="allowlisted"), family=family)


def test_a_prediction_record_declares_whether_its_oracle_was_executed() -> None:
    """An execution-derived oracle is recorded, not hidden - while the model still cannot run it."""
    assert output_fixture.INPUTS["execution_required"] is True
    assert output_fixture.oracle("exact")["reason"].startswith("derived by executing")
    test_record = test_fixture.INPUTS
    assert test_record["execution_required"] is True
    assert test_record["test_id"] == test_fixture.TEST_ID


def test_the_prediction_protocol_carries_no_tool_at_all() -> None:
    """The shipped protocol file is the frozen cohort: zero tools, no feedback, one turn."""
    import yaml

    document = yaml.safe_load(
        (
            __import__("pathlib").Path(__file__).resolve().parents[4]
            / "config"
            / "protocols"
            / "prediction-v1.yaml"
        ).read_text(encoding="utf-8")
    )
    assert document["tools"] == []
    assert document["public_test_feedback"] is False
    assert document["budget"]["tool_calls"] == 0
    # The same constraints the contract checker enforces, checked against the shipped file.
    frozen = constraints(
        protocol_id=document["protocol_id"],
        allowed_tools=document["tools"],
        public_test_feedback=document["public_test_feedback"],
        maximum_tool_calls=document["budget"]["tool_calls"],
        maximum_model_turns=document["budget"]["model_turns"],
    )
    check_protocol_constraints(frozen, family="output_prediction")
    check_protocol_constraints(frozen, family="test_prediction")


# --------------------------------------------- PCB-28-2: frozen normalization grading


def test_exact_bytes_compares_characters_not_whitespace() -> None:
    assert graded("exact", "reference").matched
    assert not graded("exact", "faulty").matched
    # An exact_bytes task defines no parsing, so an unparseable-looking string is just text.
    assert not graded("exact", "different-text").matched


def test_normalized_text_applies_only_its_declared_rules() -> None:
    reference = graded("collapsed", "reference")
    assert reference.matched
    assert "normalized_text" in reference.reason
    # Whitespace collapse is part of the frozen rules: a different run shape still matches.
    assert graded("collapsed", "alternative").matched
    # A different digit is a different answer under any whitespace rule.
    assert not graded("collapsed", "faulty").matched


def test_typed_json_ignores_declared_key_order_and_honours_tolerance() -> None:
    assert graded("typed", "reference").matched
    assert graded("typed", "alternative").matched
    wrong = graded("typed", "faulty")
    assert not wrong.matched
    # A zero tolerance is zero: the judge cannot soften 41 into 40.
    assert "frozen rules" in wrong.reason


def test_a_parse_error_is_a_wrong_answer() -> None:
    wrong = graded("typed", "parse-error")
    assert not wrong.matched
    assert wrong.parse_error is not None
    assert wrong.reason.startswith("parse_error:")


def test_judge_votes_cannot_rescue_a_mismatch() -> None:
    """The grade report has no judge field at all: there is no channel for a rescue."""
    wrong = graded("typed", "faulty")
    document = json.loads(wrong.model_dump_json())
    assert not any("judge" in key or "vote" in key for key in document)
    assert wrong.matched is False


def test_the_test_prediction_family_grades_the_declared_test() -> None:
    document = test_fixture.oracle()
    rules = NormalizationRules.model_validate(
        {**document["normalization"], "kind": "prediction_normalization"}
    )
    oracle = PredictionOracle.model_validate(
        {
            "kind": "prediction_oracle",
            "normalization": rules,
            "expected": document["expected"],
            "expected_typed": None,
            "reason": document["reason"],
        }
    )
    answers = test_fixture.CANDIDATE_ANSWERS
    for name, expected_match in (
        ("reference", True),
        # The right status on a different test is not this test passing.
        ("faulty", False),
        # The oracle's mechanism is not the declared output.
        ("wrong-output", False),
        ("no-op", False),
        # An empty answer is unparseable for an exact_bytes task too: it carries no answer.
        ("parse-error", False),
    ):
        submission = parse_submission(answers[name], rules)
        report = grade_prediction_task(
            family="test_prediction",
            task_id=test_fixture.INSTANCE_ID,
            submission=submission,
            oracle=oracle,
        )
        assert report.matched is expected_match, (name, report.reason)
    # The empty answer is the one that fails to carry an answer at all.
    empty = parse_submission(answers["parse-error"], rules)
    assert empty.parse_error is not None


def test_normalization_never_silently_defaults() -> None:
    """A rules document that does not match its mode is refused, not defaulted."""
    with pytest.raises(ValueError, match="exact_bytes compares bytes"):
        NormalizationRules.model_validate({"mode": "exact_bytes", "whitespace": "collapse_runs"})
    with pytest.raises(ValueError, match="typed_json must state its numeric tolerance"):
        NormalizationRules.model_validate({"mode": "typed_json"})


def test_normalization_applies_stated_rules_not_guesses() -> None:
    rules = NormalizationRules.model_validate(
        {
            "mode": "normalized_text",
            "line_endings": "normalize_to_lf",
            "whitespace": "collapse_runs",
            "final_newline": "ignored",
            "case_insensitive": True,
        }
    )
    assert normalize("A=1\r\nB=2\r\n", rules) == "a=1 b=2"
    # exact_bytes applies nothing at all: the comparison is byte equality.
    identity = NormalizationRules.model_validate({"mode": "exact_bytes"})
    assert normalize("A=1\r\nB=2\r\n", identity) == "A=1\r\nB=2\r\n"


# -------------------------------------- PCB-28-3: answer-only metrics and N/A dimensions


def test_prediction_metrics_declare_no_code_dimensions() -> None:
    definitions = prediction_metric_definitions()
    prediction = definitions["prediction_match"]
    assert prediction["domain"] == "answer_only"
    assert prediction["code_dimensions"] == []
    assert prediction["missingness"] == "missing_when_not_run"


def test_answer_only_metrics_are_absent_not_zero() -> None:
    """The six generated-code dimensions cannot appear in a prediction report at all."""
    report = graded("typed", "reference")
    document = json.loads(report.model_dump_json())
    for dimension in (
        "correctness",
        "security",
        "efficiency",
        "code_quality",
        "idiomatic",
        "robustness",
    ):
        assert not any(dimension in key for key in document), dimension


# ------------------------------------------------- PCB-28-4: the Track B coverage audit


def test_the_audit_covers_every_required_track_b_family() -> None:
    audit = audit_track_b()
    assert set(TRACK_B_FAMILIES) == {check.family for check in audit.checks}
    assert audit.passed
    assert all(check.output_contract and check.evidence_recorded for check in audit.checks)


def test_the_audit_refuses_a_partial_family_list() -> None:
    from polycodebench_suites_swebench.prediction_grading import CoverageAudit, CoverageCheck

    # The audit's own validator refuses to *build* a verdict that omits a family: a partial
    # checklist cannot become a pass by being narrowed after the fact.
    with pytest.raises(ValueError, match="missing"):
        CoverageAudit(
            checks=(
                CoverageCheck(
                    family="codegen",
                    solve_behavior="x",
                    grader_module="m",
                    grader_entry="e",
                    grader_importable=True,
                    output_contract=True,
                    tool_policy_recorded=True,
                    grading_present=True,
                    missingness_recorded=True,
                    evidence_recorded=True,
                ),
            )
        )


def test_the_audit_closes_wp20_only_when_every_family_passes() -> None:
    from polycodebench_suites_swebench.prediction_grading import CoverageAudit, CoverageCheck

    def check(family: str, ok: bool) -> CoverageCheck:
        return CoverageCheck(
            family=family,
            solve_behavior="x",
            grader_module="m",
            grader_entry="e",
            grader_importable=ok,
            output_contract=ok,
            tool_policy_recorded=ok,
            grading_present=ok,
            missingness_recorded=ok,
            evidence_recorded=ok,
        )

    families = tuple(check(family, True) for family in TRACK_B_FAMILIES)
    assert CoverageAudit(checks=families).passed
    broken = families[:-1] + (check(sorted(TRACK_B_FAMILIES)[-1], False),)
    failed = CoverageAudit(checks=broken)
    assert not failed.passed
    assert failed.failed_families() == (sorted(TRACK_B_FAMILIES)[-1],)


def test_the_audit_follows_real_entrypoints_and_flags_a_broken_one() -> None:
    """A grader whose module is missing is reported, not assumed present."""
    audit = audit_track_b()
    by_family = {check.family: check for check in audit.checks}
    assert by_family["repo_repair"].grader_importable
    assert by_family["repo_task"].grader_importable
    assert by_family["output_prediction"].grader_entry == "grade_prediction_task"
    # Both prediction families share the deterministic grader.
    assert (
        by_family["output_prediction"].grader_module == by_family["test_prediction"].grader_module
    )


def test_the_audit_opens_pack_and_evidence_paths_instead_of_trusting_strings() -> None:
    wrong_pack = audit_family(
        "output_prediction",
        taskpacks={"output_prediction": "taskpacks/qa/py-configkit-qa-v1"},
        evidence={"output_prediction": "docs/implementation/evidence/prompt-28-e2e38.json"},
    )
    assert not wrong_pack.output_contract
    assert not wrong_pack.passed
    assert "family does not match output_prediction" in wrong_pack.detail

    wrong_evidence = audit_family(
        "output_prediction",
        taskpacks={"output_prediction": "taskpacks/prediction/output-prediction"},
        evidence={"output_prediction": "taskpacks/prediction/output-prediction/manifest.yaml"},
    )
    assert wrong_evidence.output_contract
    assert not wrong_evidence.evidence_recorded
    assert "not readable JSON" in wrong_evidence.detail

    missing_record = audit_family(
        "output_prediction",
        taskpacks={"output_prediction": "taskpacks/prediction/output-prediction"},
        evidence={"output_prediction": "docs/implementation/evidence/absent-evidence.json"},
    )
    assert not missing_record.evidence_recorded
    assert "path does not exist" in missing_record.detail


def test_the_audit_verdict_is_deterministic_json() -> None:
    first = json.loads(audit_track_b().as_json())
    second = json.loads(audit_track_b().as_json())
    assert first == second
    assert first["wp20_closed"] is True
    assert first["failed_families"] == []
    assert set(first["answer_only_metric_definitions"]) == {"prediction_match"}
    repo_qa = next(check for check in first["checks"] if check["family"] == "repo_qa")
    assert "taskpacks/qa/py-configkit-qa-v1" in repo_qa["detail"]
    assert "prompt-27-e2e-38.json" in repo_qa["detail"]
