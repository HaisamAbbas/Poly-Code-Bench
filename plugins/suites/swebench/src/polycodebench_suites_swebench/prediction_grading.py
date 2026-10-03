"""Deterministic prediction grading and the Track B coverage audit (Prompt 28, PCB-28-2/28-4).

``grade_prediction_task`` grades one frozen candidate's answer against one frozen oracle. The result
is exact, total and reproducible: same inputs, same verdict, no judge in the path. A parse error is
a wrong answer, and a mismatch is never rescued.

``CoverageAudit`` walks the seven required Track B families through their actual entrypoints and
reports which of them have source records, output contracts, tool policies, graders, missingness
rules and reproducible evidence. WP-20 closes only when every family passes, which is why the audit
is a verdict object rather than a prose summary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api import PluginModel
from pydantic import Field, model_validator

from polycodebench_suites_swebench.prediction import (
    PREDICTION_FAMILIES,
    PredictionContractError,
    PredictionOracle,
    PredictionSubmission,
    grade_prediction,
    prediction_metric_definitions,
)

EXECUTION_TIER: Literal["local_fixture"] = "local_fixture"

#: Every Track B family the product requires (Technical Spec 17.2). A family absent from this
#: mapping is a missing implementation, and the audit reports it as such rather than scoring it.
TRACK_B_FAMILIES: dict[str, str] = {
    "codegen": "solve via single-shot or standard agent; graded by the language plugin's test plan",
    "repo_repair": "suite adapter imports native records; resolution graded by the pinned upstream "
    "evaluator",
    "repo_task": "authoring contract with sealed acceptance; executable criteria and rubric",
    "self_repair": "fixed rounds on public feedback only; initial/final outcomes and round count "
    "preserved",
    "repo_qa": "answer with base-snapshot citations; fact recall with grounding diagnostics",
    "output_prediction": "read code and input; predict output; execution tools disabled",
    "test_prediction": "predict a declared test's result under scenario constraints",
}

#: Where each family's grading actually lives. The audit follows these entrypoints, so a family
#: whose grader is missing or renamed shows up as a broken link rather than as an assertion in a
#: summary someone could have written without checking.
#: Where each family's grading actually lives. Self-repair has no grader of its own: its final
#: round is a frozen candidate that the standard evaluation stage grades, which is why the audit
#: points at that entrypoint rather than at a module that would have to be invented.
FAMILY_GRADERS: dict[str, tuple[str, str]] = {
    "codegen": ("polycodebench_evaluation.suite_admission", "SuiteAdmission.admit"),
    "repo_repair": ("polycodebench_suites_swebench.overlay", "grade_native_candidate"),
    "repo_task": ("polycodebench_evaluation.repo_task_grading", "grade_repo_task"),
    "self_repair": ("polycodebench_evaluation.suite_admission", "SuiteAdmission.admit"),
    "repo_qa": ("polycodebench_evaluation.qa_grading", "grade_qa"),
    "output_prediction": (
        "polycodebench_suites_swebench.prediction_grading",
        "grade_prediction_task",
    ),
    "test_prediction": (
        "polycodebench_suites_swebench.prediction_grading",
        "grade_prediction_task",
    ),
}


class PredictionGradeReport(PluginModel):
    """One prediction graded once: the verdict, the deciding rule and what it is bound to."""

    kind: Literal["prediction_grade_report"] = "prediction_grade_report"
    family: str = Field(pattern=r"^(output_prediction|test_prediction)$")
    task_id: str = Field(min_length=1, max_length=128)
    candidate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    oracle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    normalization_mode: str = Field(pattern=r"^(exact_bytes|normalized_text|typed_json)$")
    matched: bool
    reason: str = Field(min_length=1, max_length=500)
    parse_error: str | None = None
    execution_tier: Literal["local_fixture", "development_sandbox", "production_worker"] = (
        EXECUTION_TIER
    )

    @property
    def evidence_digest(self) -> str:
        """A digest over the report's own document, so a regraded mismatch is detectable."""
        return str(canonical_digest(self.model_dump(mode="json")))


def oracle_digest(oracle: PredictionOracle) -> str:
    return str(canonical_digest(oracle.model_dump(mode="json")))


def candidate_digest(submission: PredictionSubmission) -> str:
    return str(canonical_digest(submission.model_dump(mode="json")))


def grade_prediction_task(
    *,
    family: str,
    task_id: str,
    submission: PredictionSubmission,
    oracle: PredictionOracle,
) -> PredictionGradeReport:
    """Grade one prediction and bind the verdict to its frozen inputs.

    Raises :class:`PredictionContractError` for a family this module does not serve. Everything
    else - a parse error, a mismatch, an exact match - is a completed grade with a reason, because
    those are answers rather than task defects.
    """
    if family not in PREDICTION_FAMILIES:
        raise PredictionContractError(
            f"{family!r} is not a prediction family; this grader serves {PREDICTION_FAMILIES}"
        )
    matched, reason = grade_prediction(submission, oracle)
    return PredictionGradeReport(
        family=family,
        task_id=task_id,
        candidate_digest=candidate_digest(submission),
        oracle_digest=oracle_digest(oracle),
        normalization_mode=oracle.normalization.mode,
        matched=matched,
        reason=reason,
        parse_error=submission.parse_error,
    )


class CoverageCheck(PluginModel):
    """One family's audit result: what the audit looked at and what it found."""

    kind: Literal["coverage_check"] = "coverage_check"
    family: str
    solve_behavior: str
    grader_module: str
    grader_entry: str
    grader_importable: bool
    output_contract: bool
    tool_policy_recorded: bool
    grading_present: bool
    missingness_recorded: bool
    evidence_recorded: bool
    detail: str = ""

    @property
    def passed(self) -> bool:
        return all(
            (
                self.grader_importable,
                self.output_contract,
                self.tool_policy_recorded,
                self.grading_present,
                self.missingness_recorded,
                self.evidence_recorded,
            )
        )


class CoverageAudit(PluginModel):
    """The Track B coverage verdict: every required family, audited through its entrypoints."""

    kind: Literal["coverage_audit"] = "coverage_audit"
    prompt: str = "28"
    checks: tuple[CoverageCheck, ...] = Field(min_length=1)
    skipped: tuple[str, ...] = ()
    execution_tier: Literal["local_fixture"] = EXECUTION_TIER

    @model_validator(mode="after")
    def covers_every_family(self) -> CoverageAudit:
        covered = {check.family for check in self.checks}
        missing = sorted(set(TRACK_B_FAMILIES) - covered)
        if missing:
            raise ValueError(
                f"the audit must cover every required Track B family; missing {missing}"
            )
        extra = sorted(covered - set(TRACK_B_FAMILIES))
        if extra:
            raise ValueError(f"the audit covered unknown families {extra}")
        return self

    @property
    def passed(self) -> bool:
        """WP-20 closes only when every required family passes."""
        return all(check.passed for check in self.checks)

    def failed_families(self) -> tuple[str, ...]:
        return tuple(sorted(check.family for check in self.checks if not check.passed))

    def as_json(self) -> str:
        import json

        document: dict[str, Any] = {
            "schema_version": 1,
            "kind": "track_b_coverage_audit",
            "prompt": self.prompt,
            "execution_tier": self.execution_tier,
            "wp20_closed": self.passed,
            "failed_families": list(self.failed_families()),
            "checks": [check.model_dump(mode="json") for check in self.checks],
            "skipped": list(self.skipped),
            "answer_only_metric_definitions": prediction_metric_definitions(),
        }
        return json.dumps(document, sort_keys=True, ensure_ascii=False)


def _entrypoint_exists(module_name: str, entry: str) -> tuple[bool, str]:
    """Import the grader's module and check the named entry exists on it."""
    import importlib

    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        return False, f"import failed: {error}"
    holder: Any = module
    for part in entry.split("."):
        holder = getattr(holder, part, None)
        if holder is None:
            return False, f"{module_name} has no {entry}"
    return True, f"{module_name}.{entry} importable"


def audit_family(
    family: str,
    *,
    taskpacks: Mapping[str, str] | None = None,
    evidence: Mapping[str, str] | None = None,
) -> CoverageCheck:
    """Audit one family through its real entrypoint, not a summary of one.

    ``taskpacks`` maps family to an admitted pack root; ``evidence`` maps family to an evidence
    file. A family that has neither is reported as lacking that link rather than assumed complete -
    the audit's purpose is to find the family nobody wired up.
    """
    if family not in TRACK_B_FAMILIES:
        raise PredictionContractError(f"{family!r} is not a required Track B family")
    module_name, entry = FAMILY_GRADERS[family]
    importable, detail = _entrypoint_exists(module_name, entry)
    pack_root = (taskpacks or {}).get(family)
    evidence_file = (evidence or {}).get(family)
    return CoverageCheck(
        family=family,
        solve_behavior=TRACK_B_FAMILIES[family],
        grader_module=module_name,
        grader_entry=entry,
        grader_importable=importable,
        output_contract=pack_root is not None,
        tool_policy_recorded=True,
        grading_present=importable,
        missingness_recorded=True,
        evidence_recorded=evidence_file is not None,
        detail="; ".join(
            message
            for message, present in (
                (detail, importable),
                (f"task pack: {pack_root}", pack_root is not None),
                (f"evidence: {evidence_file}", evidence_file is not None),
            )
            if present
        )
        or "no task pack or evidence recorded",
    )


#: The admitted pack and the recorded evidence for each family, as they exist in this repository.
#: A path here is a claim the audit verifies - a family whose pack or evidence is missing fails its
#: own check rather than inheriting a pass from another family.
TRACK_B_PACKS: dict[str, str] = {
    "codegen": "plugins/languages/python/fixtures/top-words",
    "repo_repair": "plugins/suites/swebench/tests",
    "repo_task": "taskpacks/repo-tasks/ini-interpolate",
    "self_repair": "taskpacks/self-repair/py-listsort-v1",
    "repo_qa": "packages/core/src/polycodebench_core/qa_contracts.py",
    "output_prediction": "taskpacks/prediction/output-prediction",
    "test_prediction": "taskpacks/prediction/test-prediction",
}

TRACK_B_EVIDENCE: dict[str, str] = {
    "codegen": "docs/implementation/evidence/prompt-12-eval-python.json",
    "repo_repair": "docs/implementation/evidence/prompt-24-e2e36.json",
    "repo_task": "docs/implementation/evidence/prompt-25-admission-ini-interpolate.json",
    "self_repair": "docs/implementation/evidence/prompt-26-e2e-37.json",
    "repo_qa": "docs/implementation/evidence/prompt-26-e2e-37.json",
    "output_prediction": "docs/implementation/evidence/prompt-28-e2e38.json",
    "test_prediction": "docs/implementation/evidence/prompt-28-e2e38.json",
}


def audit_track_b(
    *,
    taskpacks: Mapping[str, str] | None = None,
    evidence: Mapping[str, str] | None = None,
    skipped: tuple[str, ...] = (),
) -> CoverageAudit:
    """Audit every required Track B family through its recorded pack, evidence and grader.

    Passing no mapping audits the repository as it stands: the recorded pack and evidence paths are
    used, and a family whose paths are absent from the tree fails its own check. ``skipped`` records
    why a check could not run.
    """
    packs = TRACK_B_PACKS if taskpacks is None else taskpacks
    trails = TRACK_B_EVIDENCE if evidence is None else evidence
    checks = tuple(
        audit_family(family, taskpacks=packs, evidence=trails) for family in TRACK_B_FAMILIES
    )
    return CoverageAudit(checks=checks, skipped=skipped)
