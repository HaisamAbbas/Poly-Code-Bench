"""Synthetic internal uncertainty checks, not model benchmark measurements."""

import hashlib
import random
from decimal import Decimal

import pytest
from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_publication.aggregation import (
    BootstrapConfig,
    CohortPolicy,
    CohortTask,
    MetricDefinition,
    Observation,
    StratumWeight,
    _number,
    _percentile,
    bootstrap,
    paired_comparison,
)


def fixture() -> tuple[CohortPolicy, MetricDefinition, tuple[Observation, ...]]:
    cohort = CohortPolicy(
        tasks=tuple(
            CohortTask(
                task_id=f"{language}-{cluster}",
                language=language,
                stratum="main",
                cluster_id=str(cluster),
            )
            for language in ("python", "rust")
            for cluster in range(2)
        ),
        required_languages=("python", "rust"),
        stratum_weights=tuple(
            StratumWeight(language=language, stratum="main", weight_bps=10000)
            for language in ("python", "rust")
        ),
        protocol_digest="internal",
        tool_context_policy_digest="internal",
        budget_tier="internal",
        scorer_digest="internal",
        evaluator_digest="internal",
        judge_panel_digest="internal",
        hardware_class="synthetic",
        seeds=(1, 2, 3),
        applicability_policy_digest="internal",
        compatibility_policy_digest="internal",
    )
    metric = MetricDefinition(metric_id="total_score", label="Internal synthetic score")
    rows = tuple(
        Observation(
            entry_id="internal-a",
            task_id=task.task_id,
            sample_index=index,
            scorecard_digest=f"synthetic-{task.task_id}-{index}",
            metric_id=metric.metric_id,
            status="pass",
            value=str(value),
        )
        for task in cohort.tasks
        for index, value in enumerate((0, 10, 100))
    )
    return cohort, metric, rows


def test_independent_generation_draws_for_each_cluster_occurrence() -> None:
    cohort, metric, rows = fixture()
    config = BootstrapConfig(seed=5, replicates=20)
    result = bootstrap(cohort, metric, rows, "internal-a", config)
    rng = random.Random(config.seed)
    reference = []
    for _ in range(config.replicates):
        draws = rng.choices(["0", "1"], k=2)
        languages: dict[str, list[Decimal]] = {"python": [], "rust": []}
        for cluster in draws:
            for task in sorted(cohort.tasks, key=lambda task: task.task_id):
                if task.cluster_id == cluster:
                    values = rng.choices([Decimal(0), Decimal(10), Decimal(100)], k=3)
                    languages[task.language].append(sum(values) / 3)
        reference.append(sum(sum(values) / len(values) / 2 for values in languages.values()))
    assert all(
        abs(Decimal(actual) - expected) < Decimal("1e-25")
        for actual, expected in zip(result.replicates, reference, strict=True)
    )
    assert result == bootstrap(cohort, metric, tuple(reversed(rows)), "internal-a", config)


def test_replicate_evidence_recomputes_interval_and_digest() -> None:
    cohort, metric, rows = fixture()
    result = bootstrap(cohort, metric, rows, "internal-a", BootstrapConfig(replicates=30))
    ordered = sorted(map(Decimal, result.replicates))
    assert _number(_percentile(ordered, Decimal("0.025"))) == result.lower
    assert _number(_percentile(ordered, Decimal("0.975"))) == result.upper
    assert (
        result.replicate_digest
        == hashlib.sha256(canonical_json_bytes(list(result.replicates))).hexdigest()
    )
    assert result.rng.startswith("python-random-MT19937-")
    assert result.percentile_rule == "linear-index-(n-1)*p-v1"
    assert result.status == "insufficient"


def test_paired_attempts_independent_but_cluster_draws_common() -> None:
    cohort, metric, rows = fixture()
    other = tuple(row.model_copy(update={"entry_id": "internal-b"}) for row in rows)
    result = paired_comparison(
        cohort, metric, (*rows, *other), "internal-a", "internal-b", BootstrapConfig(replicates=40)
    )
    assert result.estimate == "0.000000"
    assert len(set(result.replicates)) > 1
    incomplete = bootstrap(cohort, metric, rows[:-1], "internal-a")
    assert incomplete.status == "unavailable"
    assert incomplete.accepted_replicates == 0
    assert incomplete.lower is incomplete.upper is None


def test_percentile_linear_interpolation_and_invalid_input() -> None:
    assert _percentile([Decimal(0), Decimal(10)], Decimal("0.25")) == Decimal("2.5")
    for values, probability in (([], Decimal("0.5")), ([Decimal(0)], Decimal("1.1"))):
        with pytest.raises(ValueError, match="percentile"):
            _percentile(values, probability)
