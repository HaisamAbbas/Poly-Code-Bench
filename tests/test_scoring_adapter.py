"""Bridge evaluator evidence to score inputs without inventing missing values."""

from __future__ import annotations

import pytest
import scoring_support as s
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import EvaluationState, ScoreDimension
from polycodebench_evaluation.evidence import EvaluationEvidence, PropertyEvidence, ReviewItem
from polycodebench_orchestration.grading.scoring_adapter import (
    ScoringAdapterRejected,
    evaluation_to_manifest,
)
from polycodebench_scoring import score_evaluation
from polycodebench_scoring.loader import load_evidence_ownership, load_scoring_policy


@pytest.fixture(scope="module")
def policy():  # type: ignore[no-untyped-def]
    return load_scoring_policy(s.POLICY_PATH)


@pytest.fixture(scope="module")
def ownership():  # type: ignore[no-untyped-def]
    return load_evidence_ownership(s.OWNERSHIP_PATH)


@pytest.fixture(scope="module")
def profile():  # type: ignore[no-untyped-def]
    return s.python_profile()


def _evaluation(*, gate: str = "pass", review: bool = False) -> EvaluationEvidence:
    task = s.frozen_task(
        (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
        required_analyzers=("bandit",),
    )
    evidence = EvaluationEvidence(
        evaluation_id=s.RUN_ID,
        task_id=task.task_id,
        task_version=task.task_version,
        task_digest=task.task_digest,
        plugin_id=task.primary_language,
        execution_tier="local_fixture",
        candidate_digest=s.DIGEST_B,
        baseline_digest=None,
        overlay_digest=s.DIGEST_C,
        config_digest=s.DIGEST_C,
        inventory_digest=task.inventory_digest,
        allowed_paths_ok=gate == "pass",
        disallowed_paths=(),
        build_verdict="pass",
        build_detail="fixture build completed",
        gate=gate,  # type: ignore[arg-type]
        gate_reasons=("required behavior failed",) if gate == "fail" else (),
        failed_cases=("case-1",) if gate == "fail" else (),
        group_verdicts=(),
        scenarios=(),
        robustness_score_bp=None,
        cases=(),
        property_evidence=PropertyEvidence(
            engine="fixture",
            engine_version="1",
            deterministic_policy="pinned",
            examples_pinned=1,
            case_timeout_seconds=1,
            suite_timeout_seconds=1,
            seeds=(),
            cases=0,
        ),
        analyzers=(),
        issues=(),
        resolutions=(),
        reviews=(
            ReviewItem(
                issue_key="bandit.scan",
                reason="unsupported_required_scan",
                detail="scan did not run",
            ),
        )
        if review
        else (),
        profile_items=(),
        diagnostic_score_bp=None,
        idiom_score_bp=None,
        profile_complete=False,
        native_metrics={},
        incomplete=("quality_work_gated_off:correctness_gate_not_pass",) if gate == "fail" else (),
        raw_artifacts=(),
        execution_note="test fixture",
    )
    return evidence.model_copy(
        update={
            "report_digest": canonical_digest(
                evidence.model_dump(mode="json", exclude={"report_digest"})
            )
        }
    )


def _manifest(evidence, policy, ownership, profile):  # type: ignore[no-untyped-def]
    task = s.frozen_task(
        (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
        required_analyzers=("bandit",),
    )
    return evaluation_to_manifest(
        task=task,
        evidence=evidence,
        policy=policy,
        ownership=ownership,
        profile=profile,
        profile_source_digest=policy.idiomatic.profile_source_digest,
        invocation=s.invocation(),
        evidence_artifact_id="private-evidence-bundle",
        evidence_artifact_digest=s.DIGEST_C,
    )


def test_passing_run_keeps_unproduced_judge_values_missing(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    evidence = _evaluation()
    manifest = _manifest(evidence, policy, ownership, profile)

    outcome = score_evaluation(
        s.frozen_task(
            (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
            required_analyzers=("bandit",),
        ),
        policy,
        manifest,
        ownership=ownership,
        profile=profile,
    )

    assert outcome.scorecard.status is EvaluationState.NEEDS_REVIEW
    assert outcome.scorecard.total_score is None
    assert any(reason.reference == "policy.calibration" for reason in manifest.blocking)
    missing = [item for item in manifest.items if item.status == "missing"]
    assert missing
    assert all(item.score_bp is None for item in missing)


def test_failed_gate_is_zero_when_optional_quality_work_was_skipped(
    policy, ownership, profile
) -> None:  # type: ignore[no-untyped-def]
    evidence = _evaluation(gate="fail", review=True)
    manifest = _manifest(evidence, policy, ownership, profile)
    task = s.frozen_task(
        (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
        required_analyzers=("bandit",),
    )

    outcome = score_evaluation(task, policy, manifest, ownership=ownership, profile=profile)

    assert outcome.scorecard.status is EvaluationState.READY
    assert outcome.scorecard.total_score == "0.000000"
    assert all(
        item.contribution == "0.000000"
        for item in outcome.scorecard.items
        if item.dimension is not ScoreDimension.CORRECTNESS
    )
    gated_missing = [
        item
        for item in outcome.explanation.items
        if item.dimension is ScoreDimension.CODE_QUALITY and item.status == "gated"
    ]
    assert gated_missing
    assert all(item.raw_value is None and item.effective_weight_bps > 0 for item in gated_missing)


def test_adapter_rejects_a_modified_report_digest(policy, ownership, profile) -> None:  # type: ignore[no-untyped-def]
    evidence = _evaluation().model_copy(update={"report_digest": s.DIGEST_A})

    with pytest.raises(ScoringAdapterRejected, match="report digest"):
        _manifest(evidence, policy, ownership, profile)
