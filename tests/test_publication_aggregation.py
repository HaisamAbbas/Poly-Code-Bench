"""Synthetic internal behavior fixtures; these are not model benchmark results."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from polycodebench_core.models import (
    EvaluationState,
    Gate,
    Scorecard,
    ScoreDimension,
    ScoreItem,
)
from polycodebench_publication.aggregation import (
    BootstrapConfig,
    CohortPolicy,
    CohortTask,
    MetricDefinition,
    Observation,
    StratumWeight,
    aggregate,
    bootstrap,
    filter_post_cutoff,
    observation_from_scorecard,
    paired_comparison,
)


def cohort(
    *, metric_id: str = "total_score", clusters: int = 4, separate_languages: bool = False
) -> CohortPolicy:
    return CohortPolicy(
        tasks=tuple(
            CohortTask(
                task_id=f"{lang}-{i}",
                language=lang,
                stratum="ordinary",
                cluster_id=f"{lang}-{i}" if separate_languages else f"family-{i}",
                applicable_metrics=(metric_id,),
                earliest_public_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            for lang in ("python", "rust")
            for i in range(clusters)
        ),
        required_languages=("python", "rust"),
        stratum_weights=tuple(
            StratumWeight(language=lang, stratum="ordinary", weight_bps=10000)
            for lang in ("python", "rust")
        ),
        protocol_digest="protocol",
        tool_context_policy_digest="tools",
        budget_tier="internal",
        scorer_digest="scorer",
        evaluator_digest="evaluator",
        judge_panel_digest="judges",
        hardware_class="synthetic",
        seeds=(1, 2, 3),
        applicability_policy_digest="applicability",
        compatibility_policy_digest="compatibility",
    )


def rows(
    policy: CohortPolicy,
    *,
    metric_id: str = "total_score",
    entry_id: str = "internal-a",
    value: str = "80",
) -> tuple[Observation, ...]:
    return tuple(
        Observation(
            entry_id=entry_id,
            task_id=t.task_id,
            sample_index=i,
            scorecard_digest=f"immutable-{t.task_id}-{i}",
            metric_id=metric_id,
            status="pass",
            value=value,
        )
        for t in policy.tasks
        for i in range(policy.planned_samples)
    )


def scorecard(
    state: EvaluationState,
    *,
    gate: Gate = Gate.PASS,
    task_id: str = "python-0",
    index: int = 0,
) -> Scorecard:
    ready = state == EvaluationState.READY
    contribution = "0.000000" if gate == Gate.FAIL else "80.000000"
    items = (
        [
            ScoreItem(
                kind="score_item",
                schema_version=1,
                dimension=ScoreDimension.CORRECTNESS,
                item_id="correctness",
                applicable=True,
                primary_owner=ScoreDimension.CORRECTNESS,
                raw_value=contribution,
                effective_weight_bps=10000,
                gating_reason=None,
                contribution=contribution,
                evidence_ids=[],
            )
        ]
        if ready
        else []
    )
    return Scorecard(
        kind="scorecard",
        schema_version=1,
        scorecard_id=f"00000000-0000-4000-8000-{index + 1:012d}",
        task_id=task_id,
        task_version=1,
        run_id="00000000-0000-4000-8000-000000000101",
        candidate_id="00000000-0000-4000-8000-000000000102",
        evidence_manifest_digest="sha256:" + "a" * 64,
        scoring_policy_digest="sha256:" + "b" * 64,
        scorer_digest="sha256:" + "c" * 64,
        gate=gate,
        status=state,
        total_score=("0.000000" if gate == Gate.FAIL else "80.000000") if ready else None,
        items=items,
        created_at="2026-10-05T00:00:00Z",
    )


def test_failure_denominators_and_partial_coverage_never_renormalize() -> None:
    policy = cohort()
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    observations = rows(policy)
    first = observations[0].model_copy(update={"status": "failure", "value": "100"})
    result = aggregate(policy, metric, (first, *observations[1:]), "internal-a")
    assert result.value == "76.666667"
    assert result.failure_count == 1
    assert result.planned_sample_count == 24
    partial = aggregate(policy, metric, observations[:12], "internal-a")
    assert partial.value is None
    assert partial.language_values == (("python", "80.000000"), ("rust", None))
    assert partial.coverage == "0.500000"
    infrastructure = observations[0].model_copy(
        update={"status": "infrastructure_missing", "value": None}
    )
    assert (
        aggregate(policy, metric, (infrastructure, *observations[1:]), "internal-a").value is None
    )


@pytest.mark.parametrize(
    ("state", "expected_status", "expected_reason"),
    [
        (EvaluationState.PENDING, "pending", "evaluations_pending"),
        (EvaluationState.EVALUATING, "evaluating", "evaluations_in_progress"),
        (EvaluationState.NEEDS_REVIEW, "needs_review", "evaluations_need_review"),
        (EvaluationState.INFRA_BLOCKED, "infrastructure_missing", "infrastructure_missing"),
        (EvaluationState.QUARANTINED, "quarantined", "evaluations_quarantined"),
        (EvaluationState.CANCELLED, "cancelled", "evaluations_cancelled"),
    ],
)
def test_nonready_scorecard_states_remain_distinct_and_block_coverage(
    state: EvaluationState, expected_status: str, expected_reason: str
) -> None:
    policy = cohort()
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    task_id = policy.tasks[0].task_id
    converted = tuple(
        observation_from_scorecard(
            scorecard(state, task_id=task_id, index=index),
            entry_id="internal-a",
            sample_index=index,
            metric=metric,
        )
        for index in range(policy.planned_samples)
    )

    assert {row.status for row in converted} == {expected_status}
    assert all(row.value is None for row in converted)
    complete_other_tasks = tuple(row for row in rows(policy) if row.task_id != task_id)
    result = aggregate(policy, metric, (*complete_other_tasks, *converted), "internal-a")
    assert result.value is None
    assert result.observed_sample_count == len(complete_other_tasks)
    assert result.observed_task_count == len(policy.tasks) - 1
    assert expected_reason in result.reasons
    assert result.status == "partial"


def test_ready_not_applicable_gate_is_not_reported_as_a_pass() -> None:
    policy = cohort()
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    task_id = policy.tasks[0].task_id
    converted = tuple(
        observation_from_scorecard(
            scorecard(
                EvaluationState.READY,
                gate=Gate.NOT_APPLICABLE,
                task_id=task_id,
                index=index,
            ),
            entry_id="internal-a",
            sample_index=index,
            metric=metric,
        )
        for index in range(policy.planned_samples)
    )
    assert {row.status for row in converted} == {"not_applicable"}
    assert all(row.value is None for row in converted)

    complete_other_tasks = tuple(row for row in rows(policy) if row.task_id != task_id)
    result = aggregate(policy, metric, (*complete_other_tasks, *converted), "internal-a")
    assert result.value is None
    assert "evaluations_not_applicable" in result.reasons
    assert result.observed_sample_count == len(complete_other_tasks)


def test_scorecard_gate_controls_pass_rate_observation() -> None:
    metric = MetricDefinition(metric_id="pass_rate", label="Pass rate", unit="percent")
    passing = observation_from_scorecard(
        scorecard(EvaluationState.READY, gate=Gate.PASS),
        entry_id="internal-a",
        sample_index=0,
        metric=metric,
    )
    failing = observation_from_scorecard(
        scorecard(EvaluationState.READY, gate=Gate.FAIL, index=1),
        entry_id="internal-a",
        sample_index=1,
        metric=metric,
    )
    assert (passing.status, passing.value) == ("pass", "100")
    assert (failing.status, failing.value) == ("failure", "0")


def test_conditional_metric_is_labeled_and_carries_passing_denominator() -> None:
    with pytest.raises(ValueError, match="explicitly labeled"):
        MetricDefinition(metric_id="quality", label="Quality", conditional_on_pass=True)
    metric_id = "quality_conditional_on_pass"
    policy = cohort(metric_id=metric_id)
    metric = MetricDefinition(
        metric_id=metric_id, label="Quality conditional on pass", conditional_on_pass=True
    )
    observations = rows(policy, metric_id=metric_id)
    failure = observations[0].model_copy(update={"status": "failure", "value": "0"})
    result = aggregate(policy, metric, (failure, *observations[1:]), "internal-a")
    assert result.value == "80.000000"
    assert result.passing_denominator == 23


def test_fixed_seed_replay_clustered_variants_and_paired_difference() -> None:
    policy = cohort(clusters=8)
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    observations = tuple(
        s.model_copy(update={"value": str(int(s.task_id.split("-")[-1]) * 10)})
        for s in rows(policy)
    )
    config = BootstrapConfig(seed=42, replicates=80)
    first = bootstrap(policy, metric, observations, "internal-a", config)
    assert first == bootstrap(policy, metric, observations, "internal-a", config)
    assert first.accepted_replicates == 80
    assert first.lower != first.upper
    assert first.status == "insufficient"
    second = tuple(
        s.model_copy(update={"entry_id": "internal-b", "value": str(int(s.value or "0") + 5)})
        for s in observations
    )
    paired = paired_comparison(
        policy, metric, (*observations, *second), "internal-a", "internal-b", config
    )
    # Every cross-language variant uses the same sampled family multiplicity.
    assert set(paired.replicates) == {"-5.000000"}
    assert paired.lower == paired.upper == "-5.000000"
    assert first.replicate_digest != paired.replicate_digest


def test_uncertainty_recomputes_micro_f1_in_every_replicate() -> None:
    policy = cohort(metric_id="micro_f1", clusters=3)
    metric = MetricDefinition(metric_id="micro_f1", label="Micro F1", task_aggregation="micro_f1")
    observations = tuple(
        s.model_copy(
            update={
                "true_positive": 1 if s.task_id.endswith("0") else 0,
                "false_positive": 0 if s.task_id.endswith("0") else 9,
                "false_negative": 0,
                "value": None,
            }
        )
        for s in rows(policy, metric_id="micro_f1")
    )
    result = aggregate(policy, metric, observations, "internal-a")
    assert result.value == "10.000000"
    uncertainty = bootstrap(
        policy, metric, observations, "internal-a", BootstrapConfig(seed=1, replicates=40)
    )
    displayed = {
        format(Decimal(v).quantize(Decimal("0.000001")), "f") for v in uncertainty.replicates
    }
    assert "30.769231" in displayed
    assert "10.000000" in displayed
    assert "33.333333" not in displayed


def test_unknown_cutoff_and_common_eligible_filter() -> None:
    policy = cohort()
    with pytest.raises(ValueError, match="unknown cutoff"):
        filter_post_cutoff(policy, {"internal-a": None})
    policy = policy.model_copy(
        update={
            "tasks": (
                policy.tasks[0].model_copy(
                    update={"earliest_public_at": datetime(2025, 1, 1, tzinfo=UTC)}
                ),
                policy.tasks[1].model_copy(update={"earliest_public_at": None}),
                *policy.tasks[2:],
            )
        }
    )
    filtered = filter_post_cutoff(
        policy,
        {
            "internal-a": datetime(2024, 1, 1, tzinfo=UTC),
            "internal-b": datetime(2025, 6, 1, tzinfo=UTC),
        },
    )
    assert len(filtered.tasks) == 6
    assert filtered.content_digest() != policy.content_digest()
    assert filtered.required_languages == policy.required_languages
    assert filtered.stratum_weights == policy.stratum_weights


def test_sparse_strata_redraws_are_labeled_unstable() -> None:
    policy = cohort(clusters=1, separate_languages=True)
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    result = bootstrap(policy, metric, rows(policy), "internal-a", BootstrapConfig(replicates=50))
    assert result.redraw_count > 0
    assert result.status == "unstable"


def test_observations_require_fixed_membership_and_unique_planned_slots() -> None:
    policy = cohort()
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    observations = rows(policy)
    with pytest.raises(ValueError, match="duplicate"):
        aggregate(policy, metric, (*observations, observations[0]), "internal-a")
    with pytest.raises(ValueError, match="immutable cohort"):
        aggregate(
            policy, metric, (observations[0].model_copy(update={"task_version": 2}),), "internal-a"
        )
    assert not policy.editorial_index_enabled


def test_equal_language_weights_and_frozen_task_weights() -> None:
    policy = cohort(clusters=3)
    policy = policy.model_copy(
        update={
            "tasks": tuple(
                t for t in policy.tasks if t.language == "python" or t.task_id == "rust-0"
            )
        }
    )
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    observations = tuple(
        s.model_copy(update={"value": "100" if s.task_id.startswith("python") else "0"})
        for s in rows(policy)
    )
    assert aggregate(policy, metric, observations, "internal-a").value == "50.000000"
    # Removing a planned task for one entry cannot shrink that entry's denominator.
    assert aggregate(policy, metric, observations[3:], "internal-a").value is None


def test_generation_attempt_variation_is_resampled() -> None:
    policy = cohort(clusters=1)
    metric = MetricDefinition(metric_id="total_score", label="All attempts")
    observations = tuple(
        s.model_copy(update={"value": ("0", "50", "100")[s.sample_index]}) for s in rows(policy)
    )
    uncertainty = bootstrap(
        policy, metric, observations, "internal-a", BootstrapConfig(seed=16001, replicates=50)
    )
    assert uncertainty.estimate == "50.000000"
    assert uncertainty.lower != uncertainty.upper
    assert uncertainty.status == "insufficient"
