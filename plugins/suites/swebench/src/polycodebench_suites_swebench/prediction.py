"""Deterministic prediction contracts (Prompt 28, PCB-28-1).

Two families measure what a model says *about* code rather than what it writes:

``output_prediction``
    Read supplied code and input; predict the program's output. Command, test and patch tools are
    disabled, because executing the target changes the protocol into code generation and would need
    a separate label (Technical Spec 17.2).

``test_prediction``
    Predict a declared test's result or output under stated scenario constraints. The oracle is
    frozen; a wrong prediction is wrong no matter how it is argued.

Both are *answer-only* families: a submission is text or a typed value, never source files. The
contract below is what makes that structural rather than a convention - the tool policy, the
submission kind and the grading rule are one document, so a cohort that allows an execution tool is
a different cohort by construction (PCB-28-1's DoD), and a judge has no channel through which it
could overturn a mismatch (PCB-28-2's DoD).

Normalization is explicit because "looks about right" is not a grade: each task states its
line-ending, whitespace and final-newline rules, or its numeric tolerance, ordering and type
constraints (Technical Spec 17.4). A submission the normalization cannot parse is a wrong answer,
not an error to be retried.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from polycodebench_core.models import ProtocolConstraints
from polycodebench_plugins_api import PluginModel
from pydantic import Field, model_validator

#: The families this module serves. A task outside them is another family's problem, and its
#: quality plan is that family's business rather than an answer-only restriction.
PREDICTION_FAMILIES = ("output_prediction", "test_prediction")

#: Tools that execute code. Any of them in a prediction task's tool policy turns the task into a
#: different protocol; see :func:`validate_prediction_tools`.
EXECUTION_TOOLS = frozenset({"run_command", "run_tests", "run_program", "patch_file", "write_file"})

NormalizationMode = Literal["exact_bytes", "normalized_text", "typed_json"]


class PredictionContractError(ValueError):
    """A prediction task's record, protocol or oracle is internally inconsistent."""


class NormalizationRules(PluginModel):
    """The frozen normalization a task grades under.

    ``normalized_text`` must state its rules (Technical Spec 17.4): which characters are folded,
    whether line endings are rewritten and how a final newline is treated. ``typed_json`` must state
    numeric tolerance, ordering and the types the value must have. ``exact_bytes`` states nothing
    because it compares bytes; an empty rules document is the point, not an omission.
    """

    kind: Literal["prediction_normalization"] = "prediction_normalization"
    mode: NormalizationMode
    #: Text-mode rules, each stated, not implied. Empty is valid only for exact_bytes.
    line_endings: Literal["preserve", "normalize_to_lf"] = "preserve"
    whitespace: Literal["significant", "collapse_runs", "strip_edges"] = "significant"
    final_newline: Literal["required", "tolerated", "forbidden", "ignored"] = "ignored"
    #: Case folding is a deliberate relaxation and is off unless the task states it.
    case_insensitive: bool = False
    #: Typed-JSON rules: numeric tolerance in absolute terms, and whether key order is compared.
    numeric_tolerance: str | None = None
    ignore_key_order: bool = False
    required_types: tuple[str, ...] = ()

    @model_validator(mode="after")
    def rules_match_mode(self) -> NormalizationRules:
        if self.mode == "exact_bytes":
            stated = (
                self.line_endings != "preserve"
                or self.whitespace != "significant"
                or self.final_newline != "ignored"
                or self.case_insensitive
                or self.numeric_tolerance is not None
                or self.ignore_key_order
                or bool(self.required_types)
            )
            if stated:
                raise ValueError("exact_bytes compares bytes and declares no normalization rules")
            return self
        if self.mode == "normalized_text":
            if self.line_endings not in {"preserve", "normalize_to_lf"}:
                raise ValueError("normalized_text must state its line-ending rule")
            if self.whitespace not in {"significant", "collapse_runs", "strip_edges"}:
                raise ValueError("normalized_text must state its whitespace rule")
            if self.final_newline not in {"required", "tolerated", "forbidden", "ignored"}:
                raise ValueError("normalized_text must state its final-newline rule")
            if self.numeric_tolerance is not None or self.required_types:
                raise ValueError("numeric tolerance and required types belong to typed_json")
            return self
        # typed_json
        if self.numeric_tolerance is None:
            raise ValueError("typed_json must state its numeric tolerance")
        if self.ignore_key_order is False and self.required_types and len(self.required_types) > 1:
            raise ValueError(
                "typed_json comparing key order across a multi-typed value is a contradiction: "
                + "either compare order and compare one type, or ignore order"
            )
        return self


class PredictionInputs(PluginModel):
    """What the model may read: the target source and its input, and nothing executed.

    ``target_paths`` are given as sources to read. ``execution_required`` is a property of the
    *oracle*, not of the model's tools: a task whose expected output was derived by running the
    target states so, and that fact is recorded on the methodology record rather than hidden. The
    model still cannot execute anything, because the protocol carries no execution tool.
    """

    kind: Literal["prediction_inputs"] = "prediction_inputs"
    target_paths: tuple[str, ...] = Field(min_length=1)
    stdin: str | None = None
    scenario: str | None = None
    #: The declared test a test_prediction task reasons about.
    test_id: str | None = None
    execution_required: bool = False

    @model_validator(mode="after")
    def test_prediction_declares_its_test(self) -> PredictionInputs:
        if self.test_id is None and self.execution_required and not self.scenario:
            raise ValueError(
                "an execution-derived oracle must state its scenario or the test it came from"
            )
        return self


class PredictionOracle(PluginModel):
    """The frozen answer: what a correct submission normalizes to.

    ``reason`` records why this is the expected value - usually the run that produced it. It is
    provenance for the oracle, never a hint shipped to the model: the oracle lives in the hidden
    bundle, and the visible bundle carries only the inputs.
    """

    kind: Literal["prediction_oracle"] = "prediction_oracle"
    normalization: NormalizationRules
    expected: str | None = None
    expected_typed: Any = None
    reason: str = ""

    @model_validator(mode="after")
    def one_answer_shape(self) -> PredictionOracle:
        if self.normalization.mode == "typed_json":
            if self.expected_typed is None:
                raise ValueError("a typed_json oracle carries its expected value as JSON")
            if self.expected is not None:
                raise ValueError("a typed_json oracle does not also carry a text answer")
            return self
        if not self.expected:
            raise ValueError("exact_bytes and normalized_text oracles carry the expected text")
        if self.expected_typed is not None:
            raise ValueError("a text-mode oracle does not also carry a typed value")
        return self


class PredictionSubmission(PluginModel):
    """One candidate's answer, parsed once, before any comparison.

    ``parse_error`` is set when the text cannot be read under the declared mode. A parse error is a
    wrong answer (Technical Spec 17.4), not an exception for a grader to retry or a judge to assess:
    the submission is compared to the oracle exactly as parsed, and an unparseable submission cannot
    match.
    """

    kind: Literal["prediction_submission"] = "prediction_submission"
    submission_kind: Literal["text", "typed_value"] = "text"
    text: str | None = None
    typed: Any = None
    parse_error: str | None = None

    @model_validator(mode="after")
    def parse_error_means_no_value(self) -> PredictionSubmission:
        carries = self.typed is not None or (self.text is not None and bool(self.text.strip()))
        if self.parse_error is not None and carries:
            raise ValueError("a submission with a parse error carries no comparable value")
        if self.parse_error is None and not carries:
            raise ValueError("a submission carries its answer or its parse error")
        if (
            self.submission_kind == "typed_value"
            and self.typed is None
            and self.parse_error is None
        ):
            raise ValueError("a typed_value submission carries a parsed value")
        return self

    @property
    def is_parse_error(self) -> bool:
        return self.parse_error is not None


def validate_prediction_tools(allowed_tools: Sequence[str]) -> None:
    """Refuse any tool policy that could execute the target.

    This is the PCB-28-1 check, called from the admission path with the task's frozen protocol. A
    policy that names an execution tool is not a stricter or looser cohort of the same protocol - it
    is a different protocol, and the task must be recorded as such rather than graded as if the
    model had answered without running anything.
    """
    offending = sorted(EXECUTION_TOOLS.intersection(allowed_tools))
    if offending:
        raise PredictionContractError(
            f"prediction tasks cannot allow execution tools; found {offending}. Running the target "
            + "changes the task from prediction to generation and requires a separate protocol and "
            + "label (Technical Spec 17.2)"
        )


def check_protocol_constraints(constraints: ProtocolConstraints, *, family: str) -> None:
    """Cross-check a frozen protocol against the answer-only rules for prediction families.

    Raises :class:`PredictionContractError` when a prediction task's protocol could let the model
    execute the target or receive hidden feedback. Anything else - read-only tools, zero turns for
    single-shot prediction, a wall-clock cap - is the task's own business.
    """
    if family not in PREDICTION_FAMILIES:
        return
    validate_prediction_tools(constraints.allowed_tools)
    if constraints.public_test_feedback:
        raise PredictionContractError(
            f"{family} tasks measure prediction without execution and receive no test feedback"
        )
    if constraints.hidden_feedback:
        raise PredictionContractError(f"{family} tasks receive no hidden feedback")
    if constraints.network_policy != "disabled":
        raise PredictionContractError(f"{family} tasks run with the network disabled")


def parse_submission(text: str | None, normalization: NormalizationRules) -> PredictionSubmission:
    """Parse a raw answer under the task's normalization, once, before any comparison.

    Returns a :class:`PredictionSubmission` whose ``parse_error`` is set when the answer cannot be
    read. The grader compares what this returns; it never re-parses and never substitutes.
    """
    if text is None or not text.strip():
        return PredictionSubmission(parse_error="the submission carries no answer")
    if normalization.mode == "typed_json":
        import json

        try:
            return PredictionSubmission(submission_kind="typed_value", typed=json.loads(text))
        except ValueError as error:
            return PredictionSubmission(parse_error=f"the answer is not valid JSON: {error}")
    return PredictionSubmission(text=text)


def normalize(text: str, rules: NormalizationRules) -> str:
    """Apply the declared text normalization. The rules are read, never guessed."""
    if rules.line_endings == "normalize_to_lf":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    if rules.case_insensitive:
        text = text.lower()
    if rules.whitespace == "collapse_runs":
        text = " ".join(text.split())
    elif rules.whitespace == "strip_edges":
        text = text.strip()
    if rules.final_newline == "required" and not text.endswith("\n"):
        text += "\n"
    return text


def values_match(submitted: Any, expected: Any, rules: NormalizationRules) -> bool:
    """Compare one parsed submission against the oracle under the frozen rules.

    The comparison is total: every input has an answer, and the answer is ``False`` whenever the
    submission could not parse, carries a different shape, or differs under normalization. There is
    no partial credit and no judge involvement, because a deterministic mismatch is not a judgment
    call.
    """
    if rules.mode == "typed_json":
        return _json_matches(submitted, expected, rules)
    if not isinstance(submitted, str):
        return False
    if rules.mode == "exact_bytes":
        return bool(submitted == expected)
    return bool(normalize(submitted, rules) == normalize(expected or "", rules))


def _json_matches(submitted: Any, expected: Any, rules: NormalizationRules) -> bool:
    if rules.ignore_key_order and isinstance(submitted, dict) and isinstance(expected, dict):
        return submitted == expected
    return bool(submitted == expected)


def grade_prediction(
    submission: PredictionSubmission,
    oracle: PredictionOracle,
) -> tuple[bool, str]:
    """Grade one submission against the oracle. Returns ``(matched, reason)``.

    The reason names the deciding rule so the evidence trail shows *why* a submission matched or
    not - which normalization applied, whether a parse error decided it - without ever consulting a
    judge.
    """
    rules = oracle.normalization
    if submission.is_parse_error:
        assert submission.parse_error is not None
        return False, f"parse_error: {submission.parse_error}"
    submitted = submission.typed if rules.mode == "typed_json" else submission.text
    expected = oracle.expected_typed if rules.mode == "typed_json" else oracle.expected
    if values_match(submitted, expected, rules):
        return True, f"matched under {rules.mode}"
    if rules.mode == "typed_json":
        return False, "typed value differs from the oracle under the frozen rules"
    return False, f"text differs from the oracle under {rules.mode}"


def prediction_metric_definitions() -> dict[str, dict[str, Any]]:
    """The answer-only metric definitions a report or API schema publishes.

    The six generated-code dimensions are absent by construction rather than reported as zero:
    there is no generated code to measure, so they are not applicable, and a metric that does not
    exist cannot be averaged or hidden behind a gate.
    """
    return {
        "prediction_match": {
            "id": "prediction_match",
            "label": "Prediction match",
            "unit": "boolean",
            "direction": "higher_is_better",
            "domain": "answer_only",
            "aggregation": "mean_over_samples",
            "missingness": "missing_when_not_run",
            "formatter": "pass_fail",
            "applicable_families": list(PREDICTION_FAMILIES),
            "code_dimensions": [],
        }
    }
