"""Unranked ``live_exploratory`` release documents built from persisted scorecards.

The input rows are plain values read by an operator tool from the scorecard tables; this module has
no database handle. Aggregation reuses :func:`~polycodebench_publication.aggregation.aggregate`
over a cohort derived from the runs' own attempts, so missing or unscored attempts stay visible as
reduced coverage instead of being dropped. Attempts that ended in a model failure (the model's own
invalid or unparseable output) are terminal and count as zero, never as missing coverage
(PolyCodeBench-Architecture-v1.md sections 5.4 and 10.3); their count is disclosed. Task
identities never leave the internal cohort digest:
the public content carries entries, metric definitions and methodology only.

Nothing built here is ranked. Checks that cannot be evidenced for unranked live data are recorded
as explicit ``not_applicable_exploratory`` receipts with a reason, never as verified.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from polycodebench_core.application_errors import InvalidState

from polycodebench_publication.aggregation import (
    CohortPolicy,
    CohortTask,
    MetricDefinition,
    Observation,
    StratumWeight,
    aggregate,
)
from polycodebench_publication.projections import Coverage, Methodology, aggregate_to_metric
from polycodebench_publication.projections_query import ReleaseContent, ReleaseEntry
from polycodebench_publication.releases import (
    EXPLORATORY_NOT_APPLICABLE_CHECKS,
    LIVE_EXPLORATORY,
    LIVE_EXPLORATORY_DISCLOSURE,
    NOT_APPLICABLE_EXPLORATORY,
    REQUIRED_CHECKS,
    ValidationEvidence,
    content_digest,
    digest,
    validate_release_kind,
)

METHODOLOGY_VERSION = "live-exploratory-v1"
#: Attempt failure class of a candidate-attributable failure; scored as zero, not missing.
MODEL_FAILURE_CLASS = "model_failure"
_SCORED_EVALUATION_STATES = frozenset({"ready", "failed"})
_NOT_APPLICABLE_REASONS = {
    "judge_calibration": "no calibrated judge panel backs unranked live results",
    "coverage_intervals": "no frozen ranked cohort; uncertainty intervals are not published",
    "native_labels": "no native benchmark labels are published",
    "rights": "no task statement, source or candidate content is disclosed",
    "scorer_replay": "archived outcome replay is not run by the live builder",
}

METRICS = (
    MetricDefinition(
        metric_id="total_score",
        label="Mean composite score",
        unit="score",
        domain=("0", "100"),
        uncertainty_method="not_computed_live_exploratory",
    ),
    MetricDefinition(
        metric_id="pass_rate",
        label="Correctness gate pass rate",
        unit="percent",
        domain=("0", "100"),
        uncertainty_method="not_computed_live_exploratory",
    ),
)


@dataclass(frozen=True)
class LiveScorecardRow:
    """One attempt of a selected run, with its persisted scorecard when one exists."""

    run_id: str
    model_provider: str
    model_name: str
    model_revision: str | None
    task_id: str
    task_version: int
    language: str
    stratum: str
    cluster_id: str
    sample_index: int
    attempt_state: str
    evaluation_id: str | None = None
    evaluation_state: str | None = None
    scorecard_id: str | None = None
    gate: str | None = None
    composite: Decimal | None = None
    policy_digest: str | None = None
    scorer_digest: str | None = None
    evidence_digest: str | None = None
    evidence_verified: bool = False
    attempt_failure_class: str | None = None
    #: The scoring policy the run declared; it binds model-failure zeros that have no scorecard.
    run_policy_digest: str | None = None


def model_failure_zero(row: LiveScorecardRow) -> bool:
    """A terminal model failure without a scorecard: scored as zero (Architecture v1 10.3)."""
    return (
        row.scorecard_id is None
        and row.attempt_state == "failed"
        and row.attempt_failure_class == MODEL_FAILURE_CLASS
    )


def _policies(rows: Sequence[LiveScorecardRow]) -> list[str]:
    """Scoring policies binding the published values: scorecards' and model-failure runs'."""
    return sorted(
        {row.policy_digest for row in _scored(rows) if row.policy_digest}
        | {
            row.run_policy_digest
            for row in rows
            if model_failure_zero(row) and row.run_policy_digest
        }
    )


def model_config_id(row: LiveScorecardRow) -> str:
    """Stable, public-safe entry identity: model slug plus the run prefix."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{row.model_provider}-{row.model_name}".lower()).strip("-")
    return f"live-{slug[:40].rstrip('-') or 'model'}-{row.run_id.replace('-', '')[:8]}"


def _label(row: LiveScorecardRow) -> str:
    revision = f" @ {row.model_revision}" if row.model_revision else ""
    return f"{row.model_provider}/{row.model_name}{revision} (live exploratory)"[:200]


def _unfrozen(name: str) -> str:
    return digest({"live_exploratory_unfrozen": name})


def _scored(rows: Iterable[LiveScorecardRow]) -> list[LiveScorecardRow]:
    return [row for row in rows if row.scorecard_id is not None]


def _membership(rows: Sequence[LiveScorecardRow]) -> list[list[Any]]:
    return sorted(
        [
            row.run_id,
            row.task_id,
            row.task_version,
            row.sample_index,
            row.evaluation_id,
            row.scorecard_id,
            row.gate,
            None if row.composite is None else format(row.composite, "f"),
            MODEL_FAILURE_CLASS if model_failure_zero(row) else None,
        ]
        for row in rows
    )


def _cohort(rows: Sequence[LiveScorecardRow]) -> CohortPolicy:
    tasks: dict[str, LiveScorecardRow] = {}
    for row in rows:
        known = tasks.setdefault(row.task_id, row)
        if (known.task_version, known.language, known.stratum, known.cluster_id) != (
            row.task_version,
            row.language,
            row.stratum,
            row.cluster_id,
        ):
            raise InvalidState("selected runs disagree on a task version or its cohort labels")
    languages = sorted({row.language for row in tasks.values()})
    weights: list[StratumWeight] = []
    for language in languages:
        strata = sorted({row.stratum for row in tasks.values() if row.language == language})
        share, remainder = divmod(10_000, len(strata))
        weights.extend(
            StratumWeight(language=language, stratum=stratum, weight_bps=share + (i < remainder))
            for i, stratum in enumerate(strata)
        )
    planned = max(row.sample_index for row in rows) + 1
    scorers = sorted({row.scorer_digest for row in rows if row.scorer_digest})
    return CohortPolicy(
        tasks=tuple(
            CohortTask(
                task_id=task_id,
                task_version=row.task_version,
                language=row.language,
                stratum=row.stratum,
                cluster_id=row.cluster_id,
            )
            for task_id, row in sorted(tasks.items())
        ),
        required_languages=tuple(languages),
        stratum_weights=tuple(weights),
        planned_samples=planned,
        protocol_digest=digest({"live_exploratory_runs": sorted({r.run_id for r in rows})}),
        tool_context_policy_digest=_unfrozen("tool_context_policy"),
        budget_tier="unspecified-live-exploratory",
        scorer_digest=scorers[0] if len(scorers) == 1 else _unfrozen("scorer"),
        evaluator_digest=_unfrozen("evaluator"),
        judge_panel_digest=_unfrozen("judge_panel"),
        hardware_class="local-unverified",
        seeds=tuple(range(planned)),
        applicability_policy_digest=_unfrozen("applicability_policy"),
        compatibility_policy_digest=_unfrozen("compatibility_policy"),
    )


def live_cohort_digest(rows: Sequence[LiveScorecardRow]) -> str:
    """Binds the exact attempt/scorecard membership and the derived cohort into the projection."""
    return digest(
        {
            "kind": "live_exploratory_cohort",
            "cohort_policy": _cohort(rows).content_digest(),
            "membership": _membership(rows),
        }
    )


def _status(
    row: LiveScorecardRow,
) -> Literal[
    "pass",
    "failure",
    "infrastructure_missing",
    "pending",
    "evaluating",
    "needs_review",
    "cancelled",
    "not_applicable",
]:
    if model_failure_zero(row):
        return "failure"
    if row.scorecard_id is None:
        if row.attempt_state == "failed":
            return "infrastructure_missing"
        if row.attempt_state in {"cancelled", "skipped"}:
            return "cancelled"
        return "evaluating" if row.evaluation_id is not None else "pending"
    if row.gate == "not_applicable":
        return "not_applicable"
    if row.gate == "fail":
        return "failure"
    if row.gate == "pass" and row.composite is not None:
        return "pass"
    return "needs_review"


def _observations(
    rows: Sequence[LiveScorecardRow], entry_ids: dict[str, str]
) -> tuple[Observation, ...]:
    observations: list[Observation] = []
    for row in rows:
        status = _status(row)
        for metric in METRICS:
            value: str | None = None
            if model_failure_zero(row):
                value = "0"
            elif status in {"pass", "failure"}:
                if metric.metric_id == "pass_rate":
                    value = "100" if status == "pass" else "0"
                elif row.composite is not None:
                    value = format(row.composite * 100, "f")
            observations.append(
                Observation(
                    entry_id=entry_ids[row.run_id],
                    task_id=row.task_id,
                    task_version=row.task_version,
                    sample_index=row.sample_index,
                    scorecard_digest=digest({"live_scorecard": _membership([row])[0]}),
                    metric_id=metric.metric_id,
                    status=status,
                    value=value,
                )
            )
    return tuple(observations)


def live_release_documents(
    rows: Sequence[LiveScorecardRow],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build typed public content and the allowlisted projection for an unranked live release."""
    if not rows:
        raise InvalidState("live exploratory releases need at least one attempt")
    keys = [(row.run_id, row.task_id, row.sample_index) for row in rows]
    if len(set(keys)) != len(keys):
        raise InvalidState("an attempt has more than one scored evaluation; resolve it first")
    scored = _scored(rows)
    model_failures = [row for row in rows if model_failure_zero(row)]
    if not scored and not model_failures:
        raise InvalidState("selected runs have no persisted scorecards")
    policies = _policies(rows)
    if len(policies) != 1:
        raise InvalidState("live exploratory releases require one frozen scoring policy")

    cohort = _cohort(rows)
    entry_ids = {row.run_id: model_config_id(row) for row in rows}
    if len(set(entry_ids.values())) != len(entry_ids):
        raise InvalidState("selected runs do not have distinct public entry identities")
    observations = _observations(rows, entry_ids)
    by_run = {row.run_id: row for row in rows}
    entries: list[ReleaseEntry] = []
    projection_metrics: list[dict[str, Any]] = []
    for run_id, entry_id in sorted(entry_ids.items(), key=lambda item: item[1]):
        results = [aggregate(cohort, metric, observations, entry_id) for metric in METRICS]
        entries.append(
            ReleaseEntry(
                model_config_id=entry_id,
                label=_label(by_run[run_id]),
                rank=None,
                languages=tuple(sorted({r.language for r in rows if r.run_id == run_id})),
                metrics=tuple(
                    aggregate_to_metric(result, metric)
                    for result, metric in zip(results, METRICS, strict=True)
                ),
                coverage=Coverage(
                    tasks=results[0].task_count,
                    samples=results[0].observed_sample_count,
                    independent_clusters=results[0].independent_cluster_count,
                ),
                evidence_url=f"/v1/methodology/{METHODOLOGY_VERSION}",
            )
        )
        projection_metrics.extend(
            {
                "metric_id": f"{entry_id}.{result.metric_id}",
                "value": result.value,
                "interval_low": None,
                "interval_high": None,
                "coverage": result.coverage,
                "conditional_on_pass": False,
            }
            for result in results
        )

    limitations = [
        LIVE_EXPLORATORY_DISCLOSURE,
        f"Built from {len(scored)} persisted scorecard(s) over {len(rows)} attempt(s) in "
        f"{len(entry_ids)} run(s); unscored attempts reduce coverage and are never imputed.",
        f"{len(model_failures)} attempt(s) ended in a model failure (invalid or unparseable "
        "model output) and are scored as zero, not treated as missing coverage.",
        "Uncertainty intervals, judge calibration, scorer replay, native labels and rights "
        "review were not performed; they are recorded as not applicable to this exploratory "
        "release.",
        "Task identities and content are not disclosed; entries cannot be compared as a rank.",
    ]
    content = ReleaseContent(
        policy_digest=policies[0],
        formula_version=METHODOLOGY_VERSION,
        entries=tuple(entries),
        methodology=Methodology(
            version=METHODOLOGY_VERSION,
            methods=(
                "Each entry is one completed run; every attempt of the run is in the cohort.",
                "Scores are the persisted local-scorer composites; gate failures count as zero.",
                "A model failure (invalid or unparseable model output) is a terminal attempt "
                "outcome that counts as zero; infrastructure failures stay missing coverage.",
            ),
            formulas=(
                "total_score = equal-language, stratum-weighted mean of task means of "
                "100 * composite.",
                "pass_rate = the same weighting over 100 for a passing gate and 0 otherwise.",
            ),
            deviations=("Unranked live exploratory release; no ranked-release gates apply.",),
            limitations=tuple(limitations),
        ),
        metric_definitions=METRICS,
    ).model_dump(mode="json")
    projection = {
        "schema_version": 1,
        "fixture_kind": LIVE_EXPLORATORY,
        "scope": "exploratory",
        "cohort_digest": live_cohort_digest(rows),
        "metrics": projection_metrics,
        "limitations": limitations,
    }
    validate_release_kind(content, projection)
    return content, projection


def live_release_evidence(
    content: dict[str, Any],
    projection: dict[str, Any],
    observed_rows: Sequence[LiveScorecardRow],
) -> tuple[ValidationEvidence, ...]:
    """Receipts comparing the built documents with an independent re-read of the database.

    A receipt whose observation differs keeps its mismatched digests, so the release store refuses
    validation rather than accepting a stale or incomplete snapshot.
    """
    validate_release_kind(content, projection)
    subject = content_digest(content, projection)
    scored = _scored(observed_rows)
    complete = [
        row.scorecard_id
        for row in scored
        if row.evidence_verified
        and row.evidence_digest
        and row.evaluation_state in _SCORED_EVALUATION_STATES
    ]
    expected_entries = sorted(
        [entry["model_config_id"], entry["label"]] for entry in content["entries"]
    )
    observed_entries = sorted(
        [list(pair) for pair in {(model_config_id(r), _label(r)) for r in observed_rows}]
    )
    disclosures = [
        item for item in projection["limitations"] if item == LIVE_EXPLORATORY_DISCLOSURE
    ]
    comparisons = {
        "membership": (projection["cohort_digest"], live_cohort_digest(observed_rows)),
        "evidence_completeness": (
            digest(sorted(row.scorecard_id for row in scored)),
            digest(sorted(complete)),
        ),
        "protocol_compatibility": (
            digest({"policies": [content["policy_digest"]]}),
            digest({"policies": _policies(observed_rows)}),
        ),
        "provenance": (digest(expected_entries), digest(observed_entries)),
        "disclosures": (digest([LIVE_EXPLORATORY_DISCLOSURE]), digest(disclosures)),
    }
    receipts = [
        ValidationEvidence(
            check=check,
            subject_digest=subject,
            expected_digest=expected,
            observed_digest=observed,
            reference=f"postgres:live-exploratory-builder/{check}",
        )
        for check, (expected, observed) in comparisons.items()
    ]
    for check in sorted(EXPLORATORY_NOT_APPLICABLE_CHECKS):
        outcome = digest(
            {"check": check, "outcome": NOT_APPLICABLE_EXPLORATORY, "subject": subject}
        )
        receipts.append(
            ValidationEvidence(
                check=check,
                subject_digest=subject,
                expected_digest=outcome,
                observed_digest=outcome,
                reference=f"{NOT_APPLICABLE_EXPLORATORY}: {_NOT_APPLICABLE_REASONS[check]}",
                outcome=NOT_APPLICABLE_EXPLORATORY,
            )
        )
    if {item.check for item in receipts} != REQUIRED_CHECKS:
        raise InvalidState("live exploratory receipts do not cover the required checks")
    return tuple(sorted(receipts, key=lambda item: item.check))


__all__ = [
    "METHODOLOGY_VERSION",
    "METRICS",
    "MODEL_FAILURE_CLASS",
    "LiveScorecardRow",
    "live_cohort_digest",
    "live_release_documents",
    "live_release_evidence",
    "model_config_id",
    "model_failure_zero",
]
