"""Create a clearly synthetic, locally signed release for public UI development and tests.

This helper is never called by the HTTP service. Its values exercise rendering states only and
must not be cited as benchmark measurements.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_publication.aggregation import MetricDefinition
from polycodebench_publication.projections import (
    ContributionRow,
    Coverage,
    DimensionBreakdown,
    Methodology,
    PublicMetric,
    PublicScorecard,
)
from polycodebench_publication.projections_query import (
    ReleaseContent,
    ReleaseEntry,
    ReleaseLanguageProfile,
)
from polycodebench_publication.releases import (
    REQUIRED_CHECKS,
    ReleasePrincipal,
    ReleaseStore,
    SigningKey,
    ValidationEvidence,
    digest,
    validate_projection,
)


def _metric(
    metric_id: str,
    label: str,
    value: str | None,
    *,
    status: str = "measured",
    unit: str = "score",
    low: str | None = None,
    high: str | None = None,
    reason: str | None = None,
) -> PublicMetric:
    return PublicMetric(
        metric_id=metric_id,
        label=label,
        unit=unit,
        direction="higher",
        status=status,
        value=value,
        interval_low=low,
        interval_high=high,
        coverage="1.000000" if status == "measured" else None,
        conditional_on_pass=False,
        reason=reason,
    )


def _dimension(
    name: str,
    value: str | None,
    *,
    status: str = "measured",
    reason: str | None = None,
    applicable_tasks: int = 1,
    opportunity_count: int = 1,
) -> DimensionBreakdown:
    metric_id = "dimension_" + name
    return DimensionBreakdown(
        dimension=name,
        metric=_metric(
            metric_id,
            name.replace("_", " ").title(),
            value,
            status=status,
            reason=reason,
        ),
        applicable_tasks=applicable_tasks,
        opportunity_count=opportunity_count,
    )


def _scorecard_metrics() -> tuple[PublicMetric, ...]:
    return (
        _metric(
            "code_score",
            "Code score",
            "89.750000",
            low="81.000000",
            high="96.000000",
            reason="Synthetic UI fixture interval; not a benchmark estimate.",
        ),
        _metric("pass_rate", "Pass rate", "0.670000", unit="ratio"),
        _metric(
            "gated_repair_score",
            "Repair score",
            None,
            status="gated_zero",
            reason="Synthetic example: correctness gate failed.",
        ),
        _metric(
            "not_applicable_example",
            "Concurrency diagnostic",
            None,
            status="not_applicable",
            reason="The synthetic task declared no concurrent workload.",
        ),
        _metric(
            "missing_example",
            "Dependency scan",
            None,
            status="missing",
            reason="No scan result was included in this UI fixture.",
        ),
        _metric(
            "pending_review_example",
            "Maintainability review",
            None,
            status="needs_review",
            reason="Synthetic example: reviewer decision is pending.",
        ),
    )


def _definitions() -> tuple[MetricDefinition, ...]:
    dimensions = (
        "correctness",
        "security",
        "efficiency",
        "code_quality",
        "idiomatic",
        "robustness",
    )
    return (
        MetricDefinition(
            metric_id="code_score",
            label="Code score",
            unit="score",
            domain=("0", "100"),
            uncertainty_method="synthetic-ui-fixture-only",
        ),
        MetricDefinition(metric_id="pass_rate", label="Pass rate", unit="ratio", domain=("0", "1")),
        MetricDefinition(
            metric_id="gated_repair_score",
            label="Repair score",
            unit="score",
            domain=("0", "100"),
        ),
        *(
            MetricDefinition(
                metric_id=f"dimension_{dimension}",
                label=dimension.replace("_", " ").title(),
                unit="score",
                domain=("0", "100"),
                uncertainty_method="synthetic-ui-fixture-only",
            )
            for dimension in dimensions
        ),
        MetricDefinition(
            metric_id="answer_accuracy",
            label="Answer accuracy",
            unit="score",
            domain=("0", "100"),
            uncertainty_method="synthetic-ui-fixture-only",
        ),
    )


def _entries() -> tuple[ReleaseEntry, ...]:
    metrics = _scorecard_metrics()
    dimensions = (
        _dimension("correctness", "89.750000"),
        _dimension(
            "security",
            None,
            status="gated_zero",
            reason="Synthetic example: required correctness gate failed.",
        ),
        _dimension(
            "efficiency",
            None,
            status="not_applicable",
            reason="No performance workload was declared.",
            applicable_tasks=0,
            opportunity_count=0,
        ),
        _dimension("code_quality", "76.000000"),
        _dimension(
            "idiomatic",
            None,
            status="needs_review",
            reason="Synthetic example: reviewer decision is pending.",
        ),
        _dimension(
            "robustness",
            None,
            status="missing",
            reason="No robustness observation was included.",
        ),
    )
    alpha_languages = (
        ReleaseLanguageProfile(
            language_id="python",
            dimensions=(
                _dimension("correctness", "92.000000"),
                _dimension("code_quality", "76.000000"),
            ),
            diagnostics=(
                _dimension("ruff", "88.000000"),
                _dimension(
                    "dependency_audit",
                    None,
                    status="missing",
                    reason="No audit result was included.",
                ),
            ),
            tool_coverage=(("ruff", "1 measured opportunity"), ("dependency audit", "missing")),
        ),
        ReleaseLanguageProfile(
            language_id="rust",
            dimensions=(_dimension("correctness", "82.000000"),),
            diagnostics=(_dimension("clippy", "73.000000"),),
            tool_coverage=(("clippy", "1 measured opportunity"),),
        ),
        ReleaseLanguageProfile(
            language_id="javascript",
            dimensions=(_dimension("correctness", "74.000000"),),
            diagnostics=(_dimension("eslint", "80.000000"),),
            tool_coverage=(("eslint", "1 measured opportunity"),),
        ),
    )
    alpha_id, beta_id, answer_id = "synthetic-code-a", "synthetic-code-b", "synthetic-answer-only"
    alpha_scorecard = "synthetic-scorecard-a"
    beta_scorecard = "synthetic-scorecard-b"
    answer_scorecard = "synthetic-scorecard-answer"
    coverage = Coverage(tasks=1, samples=3, independent_clusters=1)
    return (
        ReleaseEntry(
            model_config_id=alpha_id,
            label="Fixture Code System A",
            capabilities=("code_generation",),
            languages=("python", "rust", "javascript"),
            metrics=metrics,
            coverage=coverage,
            generation_cost_micros="123456",
            latency_ms_p50=820,
            latency_ms_p95=1430,
            dimensions=dimensions,
            language_profiles=alpha_languages,
            run_mode="single_shot",
            budget_profile_id="synthetic-small-budget",
            evidence_url=f"/v1/scorecards/{alpha_scorecard}",
        ),
        ReleaseEntry(
            model_config_id=beta_id,
            label="Fixture Code System B",
            capabilities=("code_generation",),
            languages=("python",),
            metrics=(
                _metric(
                    "code_score",
                    "Code score",
                    "75.000000",
                    low="68.000000",
                    high="83.000000",
                    reason="Synthetic UI fixture interval; not a benchmark estimate.",
                ),
                _metric("pass_rate", "Pass rate", "0.500000", unit="ratio"),
            ),
            coverage=Coverage(tasks=1, samples=2, independent_clusters=1),
            generation_cost_micros="234567",
            latency_ms_p50=1110,
            latency_ms_p95=1950,
            dimensions=(_dimension("correctness", "75.000000"),),
            language_profiles=(
                ReleaseLanguageProfile(
                    language_id="python",
                    dimensions=(_dimension("correctness", "75.000000"),),
                    diagnostics=(_dimension("ruff", "65.000000"),),
                    tool_coverage=(("ruff", "1 measured opportunity"),),
                ),
            ),
            run_mode="standard_agent",
            budget_profile_id="synthetic-medium-budget",
            evidence_url=f"/v1/scorecards/{beta_scorecard}",
        ),
        ReleaseEntry(
            model_config_id=answer_id,
            label="Fixture Answer Only System",
            capabilities=("repository_question_answering",),
            languages=("python",),
            metrics=(_metric("answer_accuracy", "Answer accuracy", "84.000000"),),
            coverage=coverage,
            generation_cost_micros=None,
            latency_ms_p50=None,
            latency_ms_p95=None,
            dimensions=(),
            language_profiles=(),
            run_mode="single_shot",
            budget_profile_id="synthetic-small-budget",
            evidence_url=f"/v1/scorecards/{answer_scorecard}",
        ),
    )


def _scorecards(release_id: str, policy_digest: str) -> tuple[PublicScorecard, ...]:
    rows: list[PublicScorecard] = []
    for model_id, card_id, metrics in (
        ("synthetic-code-a", "synthetic-scorecard-a", _scorecard_metrics()),
        (
            "synthetic-code-b",
            "synthetic-scorecard-b",
            (
                _metric("code_score", "Code score", "75.000000"),
                _metric("pass_rate", "Pass rate", "0.500000", unit="ratio"),
            ),
        ),
        (
            "synthetic-answer-only",
            "synthetic-scorecard-answer",
            (_metric("answer_accuracy", "Answer accuracy", "84.000000"),),
        ),
    ):
        rows.append(
            PublicScorecard(
                scorecard_id=card_id,
                release_id=release_id,
                model_config_id=model_id,
                task_id="synthetic-task-example",
                formula_version="synthetic-ui-fixture-v1",
                policy_digest=policy_digest,
                gating_status="scored",
                metrics=metrics,
                contributions=(
                    ContributionRow(
                        item_id="synthetic-example-item",
                        dimension="correctness",
                        nominal_weight_bp=10_000,
                        effective_weight_bp=10_000,
                        presentation_weight_bp=10_000,
                        arithmetic="Synthetic display fixture; no benchmark arithmetic claimed.",
                        evidence_refs=(),
                        value="89.750000" if model_id == "synthetic-code-a" else "75.000000",
                    ),
                )
                if model_id != "synthetic-answer-only"
                else (),
                evidence_url=f"/v1/scorecards/{card_id}",
            )
        )
    return tuple(rows)


def create_synthetic_release(store: ReleaseStore) -> str:
    """Publish a generated fixture through the real local release lifecycle."""
    principal = ReleasePrincipal(subject_id="local-ui-fixture", roles=frozenset({"administrator"}))
    policy_digest = digest({"policy": "synthetic-ui-fixture-v1"})
    draft_id = str(uuid4())
    content = ReleaseContent(
        policy_digest=policy_digest,
        formula_version="synthetic-ui-fixture-v1",
        entries=_entries(),
        disclosed_tasks=(),
        scorecards=_scorecards(draft_id, policy_digest),
        artifacts=(),
        methodology=Methodology(
            version="synthetic-ui-fixture-v1",
            methods=("Values are authored only to exercise public page states.",),
            limitations=("Synthetic development data; not live benchmark results.",),
        ),
        metric_definitions=_definitions(),
    )
    projection: dict[str, Any] = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest({"fixture": draft_id, "kind": "synthetic-ui"}),
        "limitations": [
            "Synthetic development data for UI testing only; no benchmark measurements "
            "or model evaluations.",
            "Confidence interval values are authored display fixtures and are not "
            "statistical estimates.",
        ],
        "metrics": [
            {
                "metric_id": "code_score",
                "value": "89.750000",
                "coverage": "1.000000",
                "interval_low": "81.000000",
                "interval_high": "96.000000",
                "conditional_on_pass": False,
            }
        ],
    }
    validate_projection(projection)
    content_doc = content.model_dump(mode="json")
    draft = store.draft(principal, content_doc, projection, f"draft-{draft_id}")
    release_id = str(draft["id"])
    version = int(draft["version"])
    content = content.model_copy(
        update={
            "scorecards": tuple(
                card.model_copy(update={"release_id": release_id}) for card in content.scorecards
            )
        }
    )
    draft = store.update(
        principal,
        release_id,
        content.model_dump(mode="json"),
        projection,
        version,
        f"attach-release-id-{draft_id}",
    )
    version = int(draft["version"])
    evidence = tuple(
        ValidationEvidence(
            check=check,
            subject_digest=str(draft["content_digest"]),
            expected_digest=str(draft["content_digest"]),
            observed_digest=str(draft["content_digest"]),
            reference="synthetic-ui-fixture-generator",
        )
        for check in sorted(REQUIRED_CHECKS)
    )
    validated = store.validate(principal, release_id, evidence, version, f"validate-{draft_id}")
    reviewed = store.review(
        principal,
        release_id,
        "Synthetic UI fixture; not benchmark data.",
        int(validated["version"]),
        f"review-{draft_id}",
    )
    approved = store.approve(
        principal,
        release_id,
        str(reviewed["content_digest"]),
        int(reviewed["version"]),
        f"approve-{draft_id}",
    )
    pointer = store.current()
    store.publish(
        principal,
        release_id,
        SigningKey("synthetic-ui-fixture-key", Ed25519PrivateKey.generate()),
        int(pointer["generation"]),
        int(approved["version"]),
        f"publish-{draft_id}",
    )
    return release_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.count < 1 or arguments.count > 5:
        parser.error("--count must be between 1 and 5")
    arguments.store.parent.mkdir(parents=True, exist_ok=True)
    store = ReleaseStore(arguments.store)
    release_ids = tuple(create_synthetic_release(store) for _ in range(arguments.count))
    print(f"Created synthetic development releases {', '.join(release_ids)} in {arguments.store}.")
    print("It is UI test data, not a benchmark result.")


if __name__ == "__main__":
    main()
