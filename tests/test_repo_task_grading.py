"""Repository-task grading matrix and shared grading/judge cases (Prompt 25, PCB-25-2/25-3).

The required matrix: one valid alternative, one functionally failing patch and one
conventions/quality weakness that survives functional tests. Plus the shared grading/judge
cases: gate precedence over judgments, baseline-aware quality evidence, unchanged-file scoping,
patch and workspace artifact equivalence, contract drift and the judge-outcome evidence seam.

Judge evidence is deterministic fixture votes through the real judge services; every score here
that comes from a judgment is labelled fixture evidence in the reports.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from polycodebench_core.judge_contracts import ItemOutcome
from polycodebench_core.models import ScoreDimension
from polycodebench_evaluation.evaluator import baseline_from_package
from polycodebench_evaluation.repo_task_grading import (
    CandidateRejected,
    combine_gate,
    judge_is_wanted,
)
from polycodebench_scoring.judge_evidence import (
    JudgeEvidenceMismatch,
    judgement_items,
    rubric_item_from_outcome,
)
from polycodebench_scoring.loader import load_scoring_policy
from polycodebench_scoring.manifest import EvidenceRef, ScoringInvocation
from polycodebench_services.repo_tasks import AcceptanceContractDrift
from repo_task_support import (
    CONFIG,
    any_qualities,
    grade_patch,
    graded,
    judged,
    pack,
    reference_patch,
    rubric,
    score,
    unjudged,
)

REF = EvidenceRef(ref_type="judge_vote", ref_id="test:judgement")


def outcome(
    item_id: str = "duplication",
    *,
    status: str = "ready",
    mean: str | None = "1.000000",
) -> ItemOutcome:
    return ItemOutcome(
        item_id=item_id,
        dimension=ScoreDimension.CODE_QUALITY,
        status=status,  # type: ignore[arg-type]
        mean_score=mean,
        vote_scores=("1.000000",) * 3 if mean else (),
        vote_count=3 if mean else 0,
        required_votes=3,
        spread=mean,
        reason="fixture",
    )


# ------------------------------------------------------------------- the required test matrix


def test_valid_alternative_passes_the_mandatory_gate() -> None:
    """Multiple valid implementations can succeed: the alternative is not reference-shaped."""
    alternative = graded("ini-interpolate", "alternative")
    assert alternative.gate.status == "pass"
    assert alternative.failed_mandatory_criteria == ()
    assert all(
        result.status == "pass"
        for result in alternative.criteria
        if result.evidence_method == "executable"
    )


def test_functionally_failing_patch_is_zero_even_with_perfect_judgments() -> None:
    """The DoD case: a failed mandatory test cannot be overridden by any judgment."""
    failing, judgement = judged(
        "ini-interpolate",
        "faulty",
        {item.item_id: "1.000000" for item in rubric().items},
    )
    assert failing.gate.status == "fail"
    assert "escape-literals" in failing.failed_mandatory_criteria
    # The judgment was perfect on every item - and cannot matter.
    assert all(item.mean_score == "1.000000" for item in judgement.items)
    outcome_doc = score(failing, "ini-interpolate")
    scorecard = outcome_doc.scorecard
    assert scorecard.gate.value == "fail"
    assert scorecard.total_score == "0.000000"
    assert all(item.contribution == "0.000000" for item in scorecard.items)
    assert judge_is_wanted(failing) is False


def test_conventions_weakness_survives_functional_tests_but_is_penalized() -> None:
    """Quality defect passes every hidden case and is penalized only in quality."""
    weak = graded("ini-interpolate", "quality-defective")
    assert weak.gate.status == "pass"
    assert all(case.outcome == "pass" for case in weak.case_outcomes)
    introduced = [row for row in any_qualities(weak) if row["penalized"]]
    assert introduced, "the intended convention defects must be counted"
    assert all(row["in_new_code"] for row in introduced)

    weak_score = score(weak, "ini-interpolate").scorecard
    reference_score = score(graded("ini-interpolate", "reference"), "ini-interpolate").scorecard
    assert weak_score.gate.value == "pass"
    assert weak_score.total_score is not None
    assert reference_score.total_score is not None
    assert Decimal(weak_score.total_score) < Decimal(reference_score.total_score)


def test_legacy_debt_is_context_and_unchanged_files_are_never_new_code() -> None:
    weak = graded("ini-interpolate", "quality-defective")
    rows = any_qualities(weak)
    legacy = [row for row in rows if row["path"] == "cfgkit/legacy_report.py"]
    assert legacy, "the baseline's legacy debt must still be visible as context"
    assert all(row["relation"] == "unchanged_out_of_scope" for row in legacy)
    assert all(row["penalized"] is False for row in legacy)
    assert all(row["in_new_code"] is False for row in legacy)
    assert "cfgkit/legacy_report.py" not in weak.changed_files


# --------------------------------------------------------------------- shared grading cases


def test_patch_and_workspace_artifacts_grade_identically() -> None:
    workspace = graded("ini-interpolate", "reference")
    patched = grade_patch("ini-interpolate", reference_patch("ini-interpolate"))
    assert patched.gate.status == workspace.gate.status == "pass"
    assert patched.case_outcomes == workspace.case_outcomes
    assert patched.changed_files == workspace.changed_files


def test_patch_that_touches_protected_or_unknown_paths_is_rejected() -> None:
    protected = reference_patch("ini-interpolate").replace(
        "cfgkit/loader.py", "cfgkit/errors.py"
    )
    with pytest.raises(CandidateRejected, match="protected"):
        grade_patch("ini-interpolate", protected)
    outside = reference_patch("ini-interpolate").replace("cfgkit/loader.py", "setup.py")
    with pytest.raises(CandidateRejected, match="outside the allowed change set"):
        grade_patch("ini-interpolate", outside)
    tampered = reference_patch("ini-interpolate").replace(
        "-    return validate(parse(text))",
        "-    return validate(parse(text))  # tampered",
    )
    with pytest.raises(CandidateRejected, match="does not apply"):
        grade_patch("ini-interpolate", tampered)


def test_contract_drift_refuses_grading_mid_flight() -> None:
    imported = pack("ini-interpolate")
    drifted = imported.binding.model_copy(update={"contract_digest": "sha256:" + "0" * 64})
    baseline = baseline_from_package({**imported.visible_files, **imported.hidden_files})
    from polycodebench_evaluation.repo_task_grading import grade_repo_task

    with pytest.raises(AcceptanceContractDrift):
        grade_repo_task(
            authoring=imported.authoring,
            binding=drifted,
            rubric=rubric(),
            package=imported.imported.manifest,
            package_digest=imported.imported.package_digest,
            baseline_files=baseline,
            hidden_files=imported.hidden_files,
            submission_kind="source_bundle",
            payload={},
        )


def test_gate_functions_read_no_judgment_and_never_rescind_a_failure() -> None:
    failing = combine_gate("fail", ("criterion-x",), (), ())
    assert failing.status == "fail" and failing.failing_conditions == ("criterion-x",)
    # A judgment cannot convert a failure into a pass: only its own failures can add conditions.
    still_failing = combine_gate("fail", ("criterion-x",), ("judge-criterion",), ())
    assert still_failing.status == "fail"
    assert set(still_failing.failing_conditions) == {"criterion-x", "judge-criterion"}
    passed = combine_gate("pass", (), (), ())
    assert passed.status == "pass"
    incomplete = combine_gate("pass", (), (), ("judge-criterion",))
    assert incomplete.status == "unknown"


def test_judge_backed_gate_criterion_can_fail_but_not_pass_by_override() -> None:
    """A required-gate judgment criterion may fail a candidate at its frozen minimum."""
    gate, _ = judged(
        "history-group",
        "quality-defective",
        {
            item.item_id: (
                "0.000000" if item.item_id == "minimal_relevant_scope" else "1.000000"
            )
            for item in rubric().items
            if item.dimension.value == "code_quality"
        },
    )
    assert gate.gate.status == "fail"
    assert "request-honored" in gate.failed_mandatory_criteria
    executable = [
        result
        for result in gate.criteria
        if result.evidence_method == "executable" and result.required_gate
    ]
    assert all(result.status == "pass" for result in executable)


def test_judge_evidence_seam_maps_outcomes_without_inventing_scores() -> None:
    ready = rubric_item_from_outcome(outcome(), weight_bp=1500, evidence_ref=REF)
    assert ready.status == "measured" and ready.score_bp == 10_000
    assert ready.source == "judge_votes"

    review = rubric_item_from_outcome(
        outcome(status="needs_review", mean="0.500000"), weight_bp=1500, evidence_ref=REF
    )
    assert review.status == "needs_review" and review.score_bp is None

    blocked = rubric_item_from_outcome(
        outcome(status="infra_blocked", mean=None), weight_bp=1500, evidence_ref=REF
    )
    assert blocked.status == "missing" and blocked.score_bp is None

    weights = {"duplication": (ScoreDimension.CODE_QUALITY, 1500)}
    with pytest.raises(JudgeEvidenceMismatch, match="outside the frozen plan"):
        judgement_items(
            SimpleNamespace(items=(outcome(item_id="unknown_item"),)),  # type: ignore[arg-type]
            weights=weights,
            evidence_ref=REF,
        )

    missing_row = judgement_items(
        SimpleNamespace(items=()),  # type: ignore[arg-type]
        weights=weights,
        evidence_ref=REF,
    )
    assert missing_row[0].status == "missing" and missing_row[0].score_bp is None


def test_suppressed_judgement_yields_missing_rows_and_a_zero_gate() -> None:
    suppressed = unjudged("ini-interpolate", "faulty")
    assert suppressed.judge_disposition == "suppressed_after_mandatory_failure"
    manifest = suppressed.to_manifest(
        policy=load_scoring_policy(CONFIG / "scoring" / "pilot-v1.yaml"),
        invocation=ScoringInvocation(
            run_id="3f1b0c2e-5d4a-4b6c-8e9f-0a1b2c3d4e5f",
            candidate_id="7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
            recorded_at="2026-10-03T12:00:00Z",
            scorer_digest="sha256:" + "d" * 64,
        ),
        language_id="python",
    )
    # This failing candidate carries no judgment at all: rows are explicit missing evidence,
    # never an invented zero, and the gate is still the failed mandatory gate.
    assert manifest.gate.status == "fail"
    assert any(item.status == "missing" for item in manifest.items)
