"""Frozen common cohorts and reproducible hierarchy-aware uncertainty.

Inputs are internal scorecard references. Results from synthetic inputs remain internal;
this module makes no claim that they constitute model benchmark measurements.
"""

from __future__ import annotations

import hashlib
import random
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from polycodebench_core.canonical import canonical_document_digest, canonical_json_bytes
from polycodebench_core.models import ContractModel, EvaluationState, Gate, Scorecard
from pydantic import ConfigDict, Field, field_validator, model_validator

Positive = Annotated[int, Field(gt=0)]


def _decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal metric value") from exc
    if not number.is_finite():
        raise ValueError("metric value must be finite")
    return number


class PublicationModel(ContractModel):
    """Base for contracts that are written to and read back from JSON.

    ``ContractModel`` is strict so a number cannot arrive as a string. The before validator
    converts arrays only for fields declared as tuples; JSON datetime strings are parsed by their
    field validators, and other scalar validation remains strict.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1

    @model_validator(mode="before")
    @classmethod
    def normalize_json_tuple_fields(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value

        normalized = dict(value)
        for name, field in cls.model_fields.items():
            if name in normalized:
                normalized[name] = _json_array_to_tuple(normalized[name], field.annotation)
        return normalized

    def content_digest(self) -> str:
        return canonical_document_digest(self)


def _json_array_to_tuple(value: Any, annotation: Any) -> Any:
    """Convert JSON arrays to tuples only where the declared contract expects tuples."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Annotated:
        return _json_array_to_tuple(value, args[0])

    if origin in (Union, UnionType):
        tuple_branch = next((arg for arg in args if get_origin(arg) is tuple), None)
        if tuple_branch is not None:
            return _json_array_to_tuple(value, tuple_branch)
        return value

    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            return value
        item_types = args[:-1] if args and args[-1] is Ellipsis else args
        if len(item_types) == 1 and (not args or args[-1] is Ellipsis):
            item_types = item_types * len(value)
        return tuple(
            _json_array_to_tuple(item, item_types[index]) if index < len(item_types) else item
            for index, item in enumerate(value)
        )

    return value


class MetricDefinition(PublicationModel):
    kind: Literal["metric_definition"] = "metric_definition"
    metric_id: str
    label: str
    unit: str = "score"
    direction: Literal["higher", "lower"] = "higher"
    domain: tuple[str, str] = ("0", "100")
    applicability_rule: str = "predeclared task metric IDs"
    sample_aggregation: Literal["mean"] = "mean"
    task_aggregation: Literal["weighted_mean", "micro_f1"] = "weighted_mean"
    missingness_policy: Literal["block"] = "block"
    formatter: str = "decimal-6"
    uncertainty_method: str = "cluster-hierarchical-percentile-v1"
    source_score_item_ids: tuple[str, ...] = ()
    conditional_on_pass: bool = False

    @model_validator(mode="after")
    def valid_metric(self) -> MetricDefinition:
        low, high = map(_decimal, self.domain)
        if not low.is_finite() or not high.is_finite() or low > high:
            raise ValueError("invalid metric domain")
        if self.conditional_on_pass and "conditional_on_pass" not in self.metric_id:
            raise ValueError("conditional metrics require an explicitly labeled ID")
        return self


class CohortTask(PublicationModel):
    kind: Literal["cohort_task"] = "cohort_task"
    task_id: str
    task_version: Positive = 1
    language: str
    stratum: str
    cluster_id: str
    weight: Positive = 1
    applicable_metrics: tuple[str, ...] = ("total_score", "pass_rate")
    earliest_public_at: datetime | None = None

    @field_validator("earliest_public_at", mode="before")
    @classmethod
    def parse_exposure_timestamp(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("invalid exposure timestamp") from error

    @model_validator(mode="after")
    def valid_exposure(self) -> CohortTask:
        if self.earliest_public_at is not None and self.earliest_public_at.tzinfo is None:
            raise ValueError("exposure dates require timezone-aware timestamps")
        if len(set(self.applicable_metrics)) != len(self.applicable_metrics):
            raise ValueError("applicable metric IDs must be unique")
        return self

    @model_validator(mode="after")
    def valid_task(self) -> CohortTask:
        if self.earliest_public_at is not None and self.earliest_public_at.tzinfo is None:
            raise ValueError("public exposure dates require a timezone")
        if len(set(self.applicable_metrics)) != len(self.applicable_metrics):
            raise ValueError("duplicate applicable metrics")
        return self


class StratumWeight(PublicationModel):
    kind: Literal["stratum_weight"] = "stratum_weight"
    language: str
    stratum: str
    weight_bps: Annotated[int, Field(gt=0, le=10000)]


class CohortPolicy(PublicationModel):
    kind: Literal["cohort_policy"] = "cohort_policy"
    tasks: tuple[CohortTask, ...]
    required_languages: tuple[str, ...]
    stratum_weights: tuple[StratumWeight, ...]
    planned_samples: Positive = 3
    protocol_digest: str
    tool_context_policy_digest: str
    budget_tier: str
    scorer_digest: str
    evaluator_digest: str
    judge_panel_digest: str
    hardware_class: str
    seeds: tuple[int, ...]
    applicability_policy_digest: str
    comparison_filter: str = "all"
    compatibility_policy_digest: str
    editorial_index_enabled: Literal[False] = False

    @model_validator(mode="after")
    def valid_cohort(self) -> CohortPolicy:
        if not self.tasks or len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("cohort requires unique tasks")
        if not self.required_languages or len(set(self.required_languages)) != len(
            self.required_languages
        ):
            raise ValueError("required languages must be unique and nonempty")
        pairs = [(s.language, s.stratum) for s in self.stratum_weights]
        if any(s.language not in self.required_languages for s in self.stratum_weights):
            raise ValueError("stratum outside required languages")
        if len(self.seeds) != self.planned_samples or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("one unique seed is required per planned sample")
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate stratum weights")
        for language in self.required_languages:
            if sum(s.weight_bps for s in self.stratum_weights if s.language == language) != 10000:
                raise ValueError("frozen stratum weights must sum to 10000 per language")
        if any(
            t.language not in self.required_languages or (t.language, t.stratum) not in pairs
            for t in self.tasks
        ):
            raise ValueError("task outside frozen language/stratum policy")
        return self


class Observation(PublicationModel):
    kind: Literal["aggregate_observation"] = "aggregate_observation"
    entry_id: str
    task_id: str
    task_version: Positive = 1
    sample_index: Annotated[int, Field(ge=0)]
    scorecard_digest: str
    metric_id: str
    status: Literal[
        "pass",
        "failure",
        "infrastructure_missing",
        "pending",
        "evaluating",
        "needs_review",
        "quarantined",
        "cancelled",
        "not_applicable",
    ]
    value: str | None = None
    true_positive: Annotated[int, Field(ge=0)] = 0
    false_positive: Annotated[int, Field(ge=0)] = 0
    false_negative: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def valid_value(self) -> Observation:
        if self.value is not None:
            _decimal(self.value)
        if self.status not in {"pass", "failure"} and self.value is not None:
            raise ValueError("an incomplete or inapplicable observation cannot contain a score")
        return self


class AggregateResult(PublicationModel):
    kind: Literal["aggregate_result"] = "aggregate_result"
    cohort_digest: str
    metric_id: str
    entry_id: str
    value: str | None
    language_values: tuple[tuple[str, str | None], ...]
    task_count: int
    applicable_task_count: int
    observed_task_count: int
    planned_sample_count: int
    observed_sample_count: int
    failure_count: int
    passing_denominator: int | None
    independent_cluster_count: int
    independent_clusters_by_language: tuple[tuple[str, int], ...]
    coverage: str
    status: Literal["complete", "partial", "unavailable"]
    ranking_label: Literal["exploratory", "ranked_eligible"]
    reasons: tuple[str, ...]


def _number(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), "f")


def _rows(
    cohort: CohortPolicy,
    metric: MetricDefinition,
    observations: tuple[Observation, ...],
    entry_id: str,
) -> dict[str, list[Observation]]:
    tasks = {t.task_id: t for t in cohort.tasks}
    result: dict[str, list[Observation]] = {}
    seen: set[tuple[str, int]] = set()
    for row in observations:
        if row.entry_id != entry_id or row.metric_id != metric.metric_id:
            continue
        if row.task_id not in tasks or row.task_version != tasks[row.task_id].task_version:
            raise ValueError("observation does not match immutable cohort membership")
        if row.sample_index >= cohort.planned_samples:
            raise ValueError("sample outside planned sample policy")
        key = (row.task_id, row.sample_index)
        if key in seen:
            raise ValueError("duplicate planned sample")
        seen.add(key)
        if row.value is not None and not (
            Decimal(metric.domain[0]) <= Decimal(row.value) <= Decimal(metric.domain[1])
        ):
            raise ValueError("observation outside metric domain")
        result.setdefault(row.task_id, []).append(row)
    return result


def _statistic(
    cohort: CohortPolicy,
    metric: MetricDefinition,
    rows: dict[str, list[Observation]],
    multiplicities: Counter[str] | None = None,
) -> tuple[Decimal | None, tuple[tuple[str, str | None], ...]]:
    languages: list[tuple[str, str | None]] = []
    total = Decimal(0)
    for language in cohort.required_languages:
        language_value = Decimal(0)
        available = True
        for stratum in (s for s in cohort.stratum_weights if s.language == language):
            tasks = [
                t
                for t in cohort.tasks
                if t.language == language
                and t.stratum == stratum.stratum
                and metric.metric_id in t.applicable_metrics
                and (multiplicities is None or multiplicities[t.cluster_id])
            ]
            if not tasks:
                available = False
                break
            numerator = Decimal(0)
            denominator = Decimal(0)
            tp = fp = fn = Decimal(0)
            for task in tasks:
                samples = rows.get(task.task_id, [])
                multiplicity = multiplicities[task.cluster_id] if multiplicities is not None else 1
                if len(samples) != cohort.planned_samples * multiplicity or any(
                    s.status not in {"pass", "failure"}
                    or (
                        s.value is None
                        and s.status == "pass"
                        and metric.task_aggregation != "micro_f1"
                    )
                    for s in samples
                ):
                    available = False
                    break
                selected = [
                    s for s in samples if not metric.conditional_on_pass or s.status == "pass"
                ]
                if not selected:
                    available = False
                    break
                weight = Decimal(task.weight * multiplicity)
                if metric.task_aggregation == "micro_f1":
                    # Expected one-attempt counts, summed before computing the actual F1 statistic.
                    tp += weight * sum(s.true_positive for s in selected) / len(selected)
                    fp += weight * sum(s.false_positive for s in selected) / len(selected)
                    fn += weight * sum(s.false_negative for s in selected) / len(selected)
                else:
                    numerator += (
                        weight
                        * sum(
                            Decimal(s.value or "0") if s.status == "pass" else Decimal(0)
                            for s in selected
                        )
                        / len(selected)
                    )
                    denominator += weight
            if not available:
                break
            if metric.task_aggregation == "micro_f1":
                value = 100 * 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else Decimal(0)
            else:
                value = numerator / denominator
            language_value += value * stratum.weight_bps / 10000
        languages.append((language, _number(language_value) if available else None))
        if available:
            total += language_value / len(cohort.required_languages)
    return (total if all(v is not None for _, v in languages) else None, tuple(languages))


def aggregate(
    cohort: CohortPolicy,
    metric: MetricDefinition,
    observations: tuple[Observation, ...],
    entry_id: str,
) -> AggregateResult:
    rows = _rows(cohort, metric, observations, entry_id)
    tasks = [t for t in cohort.tasks if metric.metric_id in t.applicable_metrics]
    samples = [s for t in tasks for s in rows.get(t.task_id, [])]
    observed = [s for s in samples if s.status in {"pass", "failure"}]
    value, language_values = _statistic(cohort, metric, rows)
    clusters = tuple(
        (language, len({t.cluster_id for t in tasks if t.language == language}))
        for language in cohort.required_languages
    )
    reasons: list[str] = []
    if value is None:
        reasons.append("incomplete_required_coverage")
    state_reasons = {
        "infrastructure_missing": "infrastructure_missing",
        "pending": "evaluations_pending",
        "evaluating": "evaluations_in_progress",
        "needs_review": "evaluations_need_review",
        "quarantined": "evaluations_quarantined",
        "cancelled": "evaluations_cancelled",
        "not_applicable": "evaluations_not_applicable",
    }
    reasons.extend(
        reason
        for status, reason in state_reasons.items()
        if any(sample.status == status for sample in samples)
    )
    minimum = 30 if metric.metric_id in ("total_score", "pass_rate") else 20
    if any(count < minimum for _, count in clusters):
        reasons.append("insufficient_independent_clusters")
    if cohort.planned_samples < 3:
        reasons.append("insufficient_planned_samples")
    planned = len(tasks) * cohort.planned_samples
    return AggregateResult(
        cohort_digest=cohort.content_digest(),
        metric_id=metric.metric_id,
        entry_id=entry_id,
        value=_number(value) if value is not None else None,
        language_values=language_values,
        task_count=len(cohort.tasks),
        applicable_task_count=len(tasks),
        observed_task_count=sum(
            any(s.status in {"pass", "failure"} for s in rows.get(t.task_id, [])) for t in tasks
        ),
        planned_sample_count=planned,
        observed_sample_count=len(observed),
        failure_count=sum(s.status == "failure" for s in samples),
        passing_denominator=sum(s.status == "pass" for s in samples)
        if metric.conditional_on_pass
        else None,
        independent_cluster_count=len({t.cluster_id for t in tasks}),
        independent_clusters_by_language=clusters,
        coverage=_number(Decimal(len(observed)) / planned) if planned else "0.000000",
        status="complete" if value is not None else "partial" if observed else "unavailable",
        ranking_label="exploratory" if reasons else "ranked_eligible",
        reasons=tuple(reasons),
    )


class BootstrapConfig(PublicationModel):
    kind: Literal["bootstrap_config"] = "bootstrap_config"
    seed: int = 16001
    replicates: Annotated[int, Field(ge=2)] = 2000
    confidence_bps: Annotated[int, Field(gt=0, lt=10000)] = 9500
    max_redraw_factor: Positive = 20


class UncertaintyResult(PublicationModel):
    kind: Literal["uncertainty_result"] = "uncertainty_result"
    metric_id: str
    cohort_digest: str
    entry_ids: tuple[str, ...]
    estimate: str | None
    lower: str | None
    upper: str | None
    seed: int
    requested_replicates: int
    accepted_replicates: int
    redraw_count: int
    method: str = "cluster-hierarchical-percentile-v1"
    percentile_rule: str = "linear-index-(n-1)*p-v1"
    rng: str
    replicate_digest: str
    status: Literal["ok", "unstable", "insufficient", "unavailable"]
    replicates: tuple[str, ...]


def _percentile(values: list[Decimal], p: Decimal) -> Decimal:
    if not values or not Decimal(0) <= p <= Decimal(1):
        raise ValueError("percentile requires nonempty sorted values and probability in [0, 1]")
    index = Decimal(len(values) - 1) * p
    floor = int(index)
    fraction = index - floor
    return values[floor] * (1 - fraction) + values[min(floor + 1, len(values) - 1)] * fraction


def _uncertainty(
    cohort: CohortPolicy,
    metric: MetricDefinition,
    observations: tuple[Observation, ...],
    entries: tuple[str, ...],
    config: BootstrapConfig,
) -> UncertaintyResult:
    original = [aggregate(cohort, metric, observations, e) for e in entries]
    rows = [_rows(cohort, metric, observations, e) for e in entries]
    clusters = sorted(
        {t.cluster_id for t in cohort.tasks if metric.metric_id in t.applicable_metrics}
    )
    values: list[Decimal] = []
    redraws = 0
    rng = random.Random(config.seed)
    estimate = None
    if all(r.value is not None for r in original):
        estimate = Decimal(original[0].value or "0")
        if len(entries) == 2:
            estimate -= Decimal(original[1].value or "0")
        for _ in range(config.replicates * config.max_redraw_factor):
            draws = rng.choices(clusters, k=len(clusters))
            # Each occurrence resamples attempts independently, including conditional metrics.
            selected_tasks = tuple(
                (occurrence, task)
                for occurrence, cluster in enumerate(draws)
                for task in sorted(cohort.tasks, key=lambda task: task.task_id)
                if task.cluster_id == cluster and metric.metric_id in task.applicable_metrics
            )
            sampled_cohort = cohort.model_copy(
                update={
                    "tasks": tuple(
                        task.model_copy(
                            update={"task_id": f"bootstrap-{occurrence}-{task.task_id}"}
                        )
                        for occurrence, task in selected_tasks
                    )
                }
            )
            sampled: list[dict[str, list[Observation]]] = []
            for entry_rows in rows:
                sampled.append(
                    {
                        f"bootstrap-{occurrence}-{task.task_id}": rng.choices(
                            sorted(entry_rows[task.task_id], key=lambda row: row.sample_index),
                            k=cohort.planned_samples,
                        )
                        for occurrence, task in selected_tasks
                    }
                )
            statistics = [_statistic(sampled_cohort, metric, sample)[0] for sample in sampled]
            if any(v is None for v in statistics):
                redraws += 1
                continue
            statistic = statistics[0]
            assert statistic is not None
            if len(entries) == 2:
                other = statistics[1]
                assert other is not None
                statistic -= other
            values.append(statistic)
            if len(values) == config.replicates:
                break
    # Preserve full precision for interval replay. Six digits are a display minimum.
    serialized = tuple(
        format(v, ".6f") if v == v.quantize(Decimal("0.000001")) else format(v, "f") for v in values
    )
    ordered = sorted(values)
    tail = (1 - Decimal(config.confidence_bps) / 10000) / 2
    status: Literal["ok", "unstable", "insufficient", "unavailable"] = "ok"
    if estimate is None:
        status = "unavailable"
    elif len(values) < config.replicates or redraws / (len(values) + redraws) > 0.1:
        status = "unstable"
    elif len(clusters) < 2 or any(r.ranking_label == "exploratory" for r in original):
        status = "insufficient"
    return UncertaintyResult(
        metric_id=metric.metric_id,
        cohort_digest=cohort.content_digest(),
        entry_ids=entries,
        estimate=_number(estimate) if estimate is not None else None,
        lower=_number(_percentile(ordered, tail)) if ordered else None,
        upper=_number(_percentile(ordered, 1 - tail)) if ordered else None,
        seed=config.seed,
        requested_replicates=config.replicates,
        accepted_replicates=len(values),
        redraw_count=redraws,
        rng=f"python-random-MT19937-{sys.version_info.major}.{sys.version_info.minor}",
        replicate_digest=hashlib.sha256(canonical_json_bytes(list(serialized))).hexdigest(),
        status=status,
        replicates=serialized,
    )


def bootstrap(
    cohort: CohortPolicy,
    metric: MetricDefinition,
    observations: tuple[Observation, ...],
    entry_id: str,
    config: BootstrapConfig | None = None,
) -> UncertaintyResult:
    return _uncertainty(cohort, metric, observations, (entry_id,), config or BootstrapConfig())


def paired_comparison(
    cohort: CohortPolicy,
    metric: MetricDefinition,
    observations: tuple[Observation, ...],
    left_entry: str,
    right_entry: str,
    config: BootstrapConfig | None = None,
) -> UncertaintyResult:
    if left_entry == right_entry:
        raise ValueError("paired comparison requires two distinct entries")
    return _uncertainty(
        cohort, metric, observations, (left_entry, right_entry), config or BootstrapConfig()
    )


def filter_post_cutoff(cohort: CohortPolicy, cutoffs: dict[str, datetime | None]) -> CohortPolicy:
    """Common intersection: supported cutoffs only; unknown exposure stays excluded."""
    if not cutoffs or any(c is None for c in cutoffs.values()):
        raise ValueError("post-declared-cutoff comparison unavailable: unknown cutoff")
    if any(c is not None and c.tzinfo is None for c in cutoffs.values()):
        raise ValueError("cutoffs require timezone-aware timestamps")
    cutoff = max(c for c in cutoffs.values() if c is not None)
    tasks = tuple(
        t
        for t in cohort.tasks
        if t.earliest_public_at is not None and t.earliest_public_at > cutoff
    )
    return CohortPolicy.model_validate(
        {
            **cohort.model_dump(),
            "tasks": tasks,
            "comparison_filter": "post-declared-cutoff:" + cutoff.isoformat(),
        }
    )


def observation_from_scorecard(
    scorecard: Scorecard, *, entry_id: str, sample_index: int, metric: MetricDefinition
) -> Observation:
    from polycodebench_core.canonical import canonical_document_digest

    if scorecard.status != EvaluationState.READY:
        status_by_state: dict[
            EvaluationState,
            Literal[
                "pending",
                "evaluating",
                "needs_review",
                "infrastructure_missing",
                "quarantined",
                "cancelled",
            ],
        ] = {
            EvaluationState.PENDING: "pending",
            EvaluationState.EVALUATING: "evaluating",
            EvaluationState.NEEDS_REVIEW: "needs_review",
            EvaluationState.INFRA_BLOCKED: "infrastructure_missing",
            EvaluationState.QUARANTINED: "quarantined",
            EvaluationState.CANCELLED: "cancelled",
        }
        incomplete_status = status_by_state[scorecard.status]
        return Observation(
            entry_id=entry_id,
            task_id=scorecard.task_id,
            task_version=scorecard.task_version,
            sample_index=sample_index,
            scorecard_digest=canonical_document_digest(scorecard),
            metric_id=metric.metric_id,
            status=incomplete_status,
            value=None,
        )
    if scorecard.gate == Gate.UNKNOWN:
        raise ValueError("a ready scorecard cannot carry an unknown gate")
    if scorecard.gate == Gate.NOT_APPLICABLE:
        return Observation(
            entry_id=entry_id,
            task_id=scorecard.task_id,
            task_version=scorecard.task_version,
            sample_index=sample_index,
            scorecard_digest=canonical_document_digest(scorecard),
            metric_id=metric.metric_id,
            status="not_applicable",
            value=None,
        )
    status: Literal["pass", "failure"] = "failure" if scorecard.gate == Gate.FAIL else "pass"
    value = scorecard.total_score
    if metric.metric_id == "pass_rate":
        value = "100" if status == "pass" else "0"
    elif metric.source_score_item_ids:
        items = [i for i in scorecard.items if i.item_id in metric.source_score_item_ids]
        if len(items) != len(metric.source_score_item_ids) or any(not i.applicable for i in items):
            raise ValueError("metric source score items missing or not applicable")
        value = _number(sum((Decimal(i.contribution) for i in items), Decimal(0)))
    elif metric.metric_id != "total_score":
        raise ValueError("metric requires a declared scorecard extraction rule")
    return Observation(
        entry_id=entry_id,
        task_id=scorecard.task_id,
        task_version=scorecard.task_version,
        sample_index=sample_index,
        scorecard_digest=canonical_document_digest(scorecard),
        metric_id=metric.metric_id,
        status=status,
        value=value,
    )
