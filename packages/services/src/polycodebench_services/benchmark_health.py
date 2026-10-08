"""Decimal-only benchmark-health aggregation and scope-aware trend planning."""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Literal, cast
from uuid import UUID

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    BenchmarkHealthDocumentV2,
    BenchmarkHealthMetricsV2,
    BenchmarkHealthPayloadV2,
    BenchmarkHealthScopeV2,
    EntityRef,
    HealthConfidenceMetric,
    HealthDetectorStratum,
    HealthRatioMetric,
    ImmutableArtifactRef,
    MatchRelation,
    RiskState,
    StrictAuditModel,
    audit_document_digest,
    benchmark_health_comparability_key,
    benchmark_health_discontinuities,
    benchmark_health_sample_digest,
    health_percent,
    health_wilson_95,
)
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_core.models import Decimal6, UtcTimestamp
from pydantic import Field, model_validator

AssessmentStatus = Literal["complete", "partial", "unknown", "unscanned", "blocked"]
HealthModality = Literal["text", "code", "image", "audio", "video", "repository", "other"]


class HealthTaskObservation(StrictAuditModel):
    """One selected task's finite, already-reviewed health inputs."""

    task_ref: EntityRef
    family_ref: EntityRef | None
    assessment_status: AssessmentStatus
    risk_state: RiskState | None
    observed_risk_index: Decimal6 | None
    exact_duplicate: bool | None
    semantic_duplicate: bool | None
    pre_cutoff_exposure: bool | None
    provenance_complete: bool | None
    fresh: bool | None
    planned_units: int = Field(ge=0)
    completed_units: int = Field(ge=0)
    failed_units: int = Field(ge=0)
    truncated_units: int = Field(ge=0)
    blocked_units: int = Field(ge=0)
    unknown_units: int = Field(ge=0)

    @model_validator(mode="after")
    def task_observation_reconciles(self) -> HealthTaskObservation:
        if self.task_ref.entity_kind != "task_version":
            raise ValueError("health observations must bind task-version references")
        if self.family_ref is not None and self.family_ref.entity_kind != "task_family":
            raise ValueError("health family references must bind stable task-family identities")
        if (self.assessment_status == "unscanned") != (self.risk_state is None):
            raise ValueError("unscanned status and risk-tier availability must agree")
        if self.risk_state is not None and self.risk_state not in {
            "low_observed",
            "medium_observed",
            "high_observed",
            "insufficient_evidence",
        }:
            raise ValueError("health observations cannot use a non-applicable risk tier")
        if self.assessment_status == "unscanned" and self.observed_risk_index is not None:
            raise ValueError("unscanned tasks cannot carry an observed risk index")
        if self.observed_risk_index is not None and (
            self.assessment_status != "complete"
            or self.risk_state not in {"low_observed", "medium_observed", "high_observed"}
        ):
            raise ValueError("mean risk eligibility requires a complete measured tier")
        if (
            self.completed_units
            + self.failed_units
            + self.truncated_units
            + self.blocked_units
            + self.unknown_units
            != self.planned_units
        ):
            raise ValueError("task coverage units must reconcile to that task's planned units")
        return self


class DetectorLabelObservation(StrictAuditModel):
    """A finite labeled hit/miss outcome for one detector stratum."""

    label_id: UUID
    relation: MatchRelation
    language: str = Field(min_length=1, max_length=64)
    modality: HealthModality
    source_ref: AuditDocumentRef | None
    ground_truth: Literal["positive", "negative", "unknown"]
    detector_result: Literal["positive", "negative", "unknown"]

    @model_validator(mode="after")
    def label_stratum_is_bounded(self) -> DetectorLabelObservation:
        if self.source_ref is not None and self.source_ref.kind != "corpus_snapshot":
            raise ValueError("detector label sources must be pinned corpus snapshots")
        if not self.language.isascii():
            raise ValueError("detector language labels must be bounded ASCII identifiers")
        return self


def _ratio(numerator: int, denominator: int, unknown_count: int) -> HealthRatioMetric:
    if denominator == 0:
        return HealthRatioMetric.model_validate(
            {
                "numerator": 0,
                "denominator": 0,
                "unknown_count": 0,
                "value": None,
                "null_reason": "empty_denominator",
                "lower_bound": False,
            }
        )
    return HealthRatioMetric.model_validate(
        {
            "numerator": numerator,
            "denominator": denominator,
            "unknown_count": unknown_count,
            "value": health_percent(numerator, denominator),
            "null_reason": None,
            "lower_bound": unknown_count > 0,
        }
    )


def _detector_metric(
    numerator: int, denominator: int, unknown_count: int
) -> HealthConfidenceMetric:
    if denominator == 0:
        return HealthConfidenceMetric.model_validate(
            {
                "numerator": 0,
                "denominator": 0,
                "unknown_count": unknown_count,
                "value": None,
                "lower_95": None,
                "upper_95": None,
                "null_reason": "empty_denominator",
                "interval_method": "wilson_95_v1",
            }
        )
    value, lower, upper = health_wilson_95(numerator, denominator)
    return HealthConfidenceMetric.model_validate(
        {
            "numerator": numerator,
            "denominator": denominator,
            "unknown_count": unknown_count,
            "value": value,
            "lower_95": lower,
            "upper_95": upper,
            "null_reason": None,
            "interval_method": "wilson_95_v1",
        }
    )


def _aggregate_detector_labels(
    labels: tuple[DetectorLabelObservation, ...],
) -> tuple[HealthDetectorStratum, ...]:
    if len({label.label_id for label in labels}) != len(labels):
        raise ValueError("detector label rows must have unique identities")
    grouped: dict[tuple[str, str, str, str, str], list[DetectorLabelObservation]] = defaultdict(
        list
    )
    source_refs_by_id: dict[UUID, AuditDocumentRef] = {}
    for label in labels:
        if label.source_ref is None:
            source_id = ""
            source_digest = ""
        else:
            prior_ref = source_refs_by_id.get(label.source_ref.document_id)
            if prior_ref is not None and prior_ref != label.source_ref:
                raise ValueError("one detector source identity cannot carry conflicting digests")
            source_refs_by_id[label.source_ref.document_id] = label.source_ref
            source_id = str(label.source_ref.document_id)
            source_digest = label.source_ref.digest
        key = (label.relation, label.language, label.modality, source_id, source_digest)
        grouped[key].append(label)

    result: list[HealthDetectorStratum] = []
    for relation, language, modality, source_id, source_digest in sorted(grouped):
        rows = grouped[(relation, language, modality, source_id, source_digest)]
        known = [
            row
            for row in rows
            if row.ground_truth != "unknown" and row.detector_result != "unknown"
        ]
        unknown = len(rows) - len(known)
        true_positive = sum(
            row.ground_truth == "positive" and row.detector_result == "positive" for row in known
        )
        false_negative = sum(
            row.ground_truth == "positive" and row.detector_result == "negative" for row in known
        )
        true_negative = sum(
            row.ground_truth == "negative" and row.detector_result == "negative" for row in known
        )
        false_positive = sum(
            row.ground_truth == "negative" and row.detector_result == "positive" for row in known
        )
        positive = true_positive + false_negative
        negative = true_negative + false_positive
        source_ref = rows[0].source_ref
        result.append(
            HealthDetectorStratum.model_validate(
                {
                    "relation": relation,
                    "language": language,
                    "modality": modality,
                    "source_ref": source_ref,
                    "labeled_positive": positive,
                    "labeled_negative": negative,
                    "true_positive": true_positive,
                    "false_positive": false_positive,
                    "true_negative": true_negative,
                    "false_negative": false_negative,
                    "unknown_labels": unknown,
                    "precision": _detector_metric(
                        true_positive, true_positive + false_positive, unknown
                    ).model_dump(mode="json"),
                    "recall": _detector_metric(true_positive, positive, unknown).model_dump(
                        mode="json"
                    ),
                    "false_positive_rate": _detector_metric(
                        false_positive, negative, unknown
                    ).model_dump(mode="json"),
                    "false_negative_rate": _detector_metric(
                        false_negative, positive, unknown
                    ).model_dump(mode="json"),
                }
            )
        )
    return tuple(result)


def aggregate_benchmark_health(
    observations: tuple[HealthTaskObservation, ...],
    *,
    context_ref: AuditDocumentRef | None,
    detector_labels: tuple[DetectorLabelObservation, ...] = (),
) -> BenchmarkHealthMetricsV2:
    """Aggregate explicit task outcomes; never extrapolate a sample or hide unknowns."""
    if not observations or len(observations) > 100_000:
        raise ValueError("health aggregation requires one to 100,000 selected task outcomes")
    task_ids = [item.task_ref.entity_id for item in observations]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("health aggregation cannot repeat a selected task version")
    if context_ref is not None and context_ref.kind != "model_context":
        raise ValueError("model-specific pre-cutoff metrics require a model-context reference")
    if context_ref is None and any(item.pre_cutoff_exposure is not None for item in observations):
        raise ValueError("pre-cutoff observations require an explicit model context")

    statuses = {name: 0 for name in ("complete", "partial", "unknown", "unscanned", "blocked")}
    tiers = {name: 0 for name in ("low", "medium", "high", "insufficient")}
    tier_names: dict[RiskState, str] = {
        "low_observed": "low",
        "medium_observed": "medium",
        "high_observed": "high",
        "insufficient_evidence": "insufficient",
    }
    for item in observations:
        statuses[item.assessment_status] += 1
        if item.risk_state is not None:
            tiers[tier_names[item.risk_state]] += 1

    family_ids: set[UUID] = set()
    family_identity: dict[UUID, EntityRef] = {}
    for item in observations:
        if item.family_ref is None:
            continue
        prior = family_identity.get(item.family_ref.entity_id)
        if prior is not None and prior != item.family_ref:
            raise ValueError("one family identity cannot carry conflicting version digests")
        family_identity[item.family_ref.entity_id] = item.family_ref
        family_ids.add(item.family_ref.entity_id)

    selected = len(observations)
    exact_yes = sum(item.exact_duplicate is True for item in observations)
    exact_unknown = sum(item.exact_duplicate is None for item in observations)
    semantic_yes = sum(item.semantic_duplicate is True for item in observations)
    semantic_unknown = sum(item.semantic_duplicate is None for item in observations)
    union_yes = sum(
        item.exact_duplicate is True or item.semantic_duplicate is True for item in observations
    )
    union_unknown = sum(
        item.exact_duplicate is not True
        and item.semantic_duplicate is not True
        and (item.exact_duplicate is None or item.semantic_duplicate is None)
        for item in observations
    )
    if context_ref is None:
        pre_cutoff = HealthRatioMetric.model_validate(
            {
                "numerator": 0,
                "denominator": selected,
                "unknown_count": 0,
                "value": None,
                "null_reason": "not_applicable",
                "lower_bound": False,
            }
        )
    else:
        pre_cutoff_yes = sum(item.pre_cutoff_exposure is True for item in observations)
        pre_cutoff_unknown = sum(item.pre_cutoff_exposure is None for item in observations)
        pre_cutoff = _ratio(pre_cutoff_yes, selected, pre_cutoff_unknown)

    coverage_completed = sum(item.completed_units for item in observations)
    coverage_planned = sum(item.planned_units for item in observations)
    coverage_failed = sum(item.failed_units for item in observations)
    coverage_truncated = sum(item.truncated_units for item in observations)
    coverage_blocked = sum(item.blocked_units for item in observations)
    coverage_unknown = sum(item.unknown_units for item in observations)
    if coverage_planned == 0:
        coverage_value = None
        coverage_null_reason: str | None = "empty_denominator"
    else:
        coverage_value = health_percent(coverage_completed, coverage_planned)
        coverage_null_reason = None

    provenance_known = [item.provenance_complete for item in observations]
    fresh_known = [item.fresh for item in observations]
    eligible_risk = [
        Decimal(item.observed_risk_index)
        for item in observations
        if item.observed_risk_index is not None
    ]
    if eligible_risk:
        with localcontext() as context:
            context.prec = 28
            mean = (sum(eligible_risk, Decimal(0)) / Decimal(len(eligible_risk))).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_EVEN
            )
        mean_value: str | None = f"{mean:.6f}"
        mean_null_reason: str | None = None
    else:
        mean_value = None
        mean_null_reason = "insufficient_coverage"

    return BenchmarkHealthMetricsV2.model_validate(
        {
            "assessment_states": {
                "selected_tasks": selected,
                **statuses,
            },
            "risk_tiers": {
                "assessed_tasks": selected - statuses["unscanned"],
                **tiers,
            },
            "families": {
                "selected_tasks": selected,
                "independent_families": len(family_ids),
                "tasks_without_family_evidence": sum(
                    item.family_ref is None for item in observations
                ),
            },
            "exact_duplicate_prevalence": _ratio(exact_yes, selected, exact_unknown).model_dump(
                mode="json"
            ),
            "semantic_duplicate_prevalence": _ratio(
                semantic_yes, selected, semantic_unknown
            ).model_dump(mode="json"),
            "duplicate_union_prevalence": _ratio(union_yes, selected, union_unknown).model_dump(
                mode="json"
            ),
            "pre_cutoff_exposure": pre_cutoff.model_dump(mode="json"),
            "mandatory_coverage": {
                "completed_units": coverage_completed,
                "planned_units": coverage_planned,
                "failed_units": coverage_failed,
                "truncated_units": coverage_truncated,
                "blocked_units": coverage_blocked,
                "unknown_units": coverage_unknown,
                "value": coverage_value,
                "null_reason": coverage_null_reason,
            },
            "provenance_completeness": _ratio(
                sum(value is True for value in provenance_known),
                selected,
                sum(value is None for value in provenance_known),
            ).model_dump(mode="json"),
            "freshness": _ratio(
                sum(value is True for value in fresh_known),
                selected,
                sum(value is None for value in fresh_known),
            ).model_dump(mode="json"),
            "mean_observed_risk": {
                "selected_tasks": selected,
                "eligible_tasks": len(eligible_risk),
                "missing_tasks": selected - len(eligible_risk),
                "value": mean_value,
                "null_reason": mean_null_reason,
            },
            "detector_quality": tuple(
                item.model_dump(mode="json") for item in _aggregate_detector_labels(detector_labels)
            ),
        }
    )


def build_benchmark_health_scope(
    *,
    membership_ref: AuditDocumentRef,
    plan_ref: AuditDocumentRef,
    policy_ref: AuditDocumentRef,
    context_ref: AuditDocumentRef | None,
    source_refs: tuple[AuditDocumentRef, ...],
    population_task_refs: tuple[EntityRef, ...],
    selected_task_refs: tuple[EntityRef, ...],
    sampling_method: str,
    sampling_design: dict[str, object],
    scan_methods: tuple[str, ...],
    family_mapping: tuple[tuple[EntityRef, EntityRef | None], ...],
    family_mapping_ref: ImmutableArtifactRef | None,
    source_window_start: UtcTimestamp,
    source_window_end: UtcTimestamp,
    provenance_definition_version: str,
    freshness_definition_version: str,
    freshness_max_scan_age_hours: int,
    freshness_max_source_age_days: int,
) -> BenchmarkHealthScopeV2:
    """Freeze the selected cohort and versioned measurement definitions."""
    selected_by_id = {item.entity_id: item for item in selected_task_refs}
    population_by_id = {item.entity_id: item for item in population_task_refs}
    selected_ids = set(selected_by_id)
    population_ids = set(population_by_id)
    if len(selected_ids) != len(selected_task_refs) or len(population_ids) != len(
        population_task_refs
    ):
        raise ValueError("health scope cannot repeat task-version identities")
    if not selected_ids <= population_ids or any(
        population_by_id[task_id] != task_ref for task_id, task_ref in selected_by_id.items()
    ):
        raise ValueError("health sample must be selected from the frozen benchmark membership")
    family_by_task = {task.entity_id: (task, family) for task, family in family_mapping}
    if (
        len(family_mapping) != len(selected_task_refs)
        or set(family_by_task) != selected_ids
        or any(family_by_task[task.entity_id][0] != task for task in selected_task_refs)
        or any(
            family is not None and family.entity_kind != "task_family"
            for _, family in family_mapping
        )
    ):
        raise ValueError("family mapping must account for every selected task exactly once")
    sampling_mode: Literal["census", "sampled"] = (
        "census" if selected_ids == population_ids else "sampled"
    )
    frozen_sampling_method = "census_v1" if sampling_mode == "census" else sampling_method
    sample_digest = benchmark_health_sample_digest(selected_task_refs)
    family_digest = sha256_bytes(
        canonical_json_bytes(
            [
                {
                    "task_ref": task.model_dump(mode="json"),
                    "family_ref": family.model_dump(mode="json") if family else None,
                }
                for task, family in sorted(family_mapping, key=lambda entry: entry[0].entity_id.hex)
            ]
        )
    )
    values: dict[str, object] = {
        "membership_ref": membership_ref,
        "plan_ref": plan_ref,
        "policy_ref": policy_ref,
        "context_ref": context_ref,
        "source_refs": tuple(sorted(source_refs, key=lambda item: item.document_id.hex)),
        "source_window_start": source_window_start,
        "source_window_end": source_window_end,
        "population_task_count": len(population_task_refs),
        "selected_task_count": len(selected_task_refs),
        "sampling_mode": sampling_mode,
        "sampling_method": frozen_sampling_method,
        "sample_digest": sample_digest,
        "sampling_design_digest": sha256_bytes(canonical_json_bytes(sampling_design)),
        "method_digest": sha256_bytes(canonical_json_bytes(sorted(scan_methods))),
        "family_mapping_digest": family_digest,
        "family_mapping_ref": family_mapping_ref,
        "provenance_definition_version": provenance_definition_version,
        "freshness_definition_version": freshness_definition_version,
        "freshness_max_scan_age_hours": freshness_max_scan_age_hours,
        "freshness_max_source_age_days": freshness_max_source_age_days,
        "metric_definition_version": "benchmark-health-v1",
    }
    draft = BenchmarkHealthScopeV2.model_construct(
        **cast(Any, values),
        comparability_key="sha256:" + "0" * 64,
    )
    values["comparability_key"] = benchmark_health_comparability_key(draft)
    return BenchmarkHealthScopeV2.model_validate(values)


def build_benchmark_health_payload(
    *,
    scope: BenchmarkHealthScopeV2,
    observations: tuple[HealthTaskObservation, ...],
    assessment_refs: tuple[AuditDocumentRef, ...],
    temporal_refs: tuple[AuditDocumentRef, ...],
    coverage_refs: tuple[AuditDocumentRef, ...],
    detector_labels: tuple[DetectorLabelObservation, ...] = (),
    previous_points: tuple[tuple[AuditDocumentRef, BenchmarkHealthDocumentV2], ...] = (),
) -> BenchmarkHealthPayloadV2:
    """Create an immutable scoped point and mark any change from linked prior cohorts."""
    if len({ref.document_id for ref, _ in previous_points}) != len(previous_points):
        raise ValueError("health trend cannot repeat a previous point")
    if any(
        ref.kind != "benchmark_health"
        or ref.document_id != document.id
        or ref.digest != audit_document_digest(document)
        for ref, document in previous_points
    ):
        raise ValueError("health trend references must bind exact prior health documents")
    observation_ids = {item.task_ref.entity_id for item in observations}
    if len(observation_ids) != len(observations) or len(observations) != scope.selected_task_count:
        raise ValueError("health observations must cover each selected task exactly once")
    family_mapping = tuple(
        (item.task_ref, item.family_ref)
        for item in sorted(observations, key=lambda row: row.task_ref.entity_id.hex)
    )
    expected_family_digest = sha256_bytes(
        canonical_json_bytes(
            [
                {
                    "task_ref": task.model_dump(mode="json"),
                    "family_ref": family.model_dump(mode="json") if family else None,
                }
                for task, family in family_mapping
            ]
        )
    )
    if expected_family_digest != scope.family_mapping_digest:
        raise ValueError("health task-family observations differ from the frozen family mapping")
    if (
        any(item.family_ref is not None for item in observations)
        and scope.family_mapping_ref is None
    ):
        raise ValueError("known task families require private mapping evidence")
    previous_refs = tuple(ref for ref, _ in previous_points)
    reasons = tuple(
        sorted(
            {
                reason
                for _, point in previous_points
                for reason in benchmark_health_discontinuities(scope, point.payload.scope)
            }
        )
    )
    trend_state: Literal["initial", "comparable", "discontinuity"] = (
        "initial" if not previous_points else "discontinuity" if reasons else "comparable"
    )
    metrics = aggregate_benchmark_health(
        observations,
        context_ref=scope.context_ref,
        detector_labels=detector_labels,
    )
    return BenchmarkHealthPayloadV2.model_validate(
        {
            "scope": scope,
            "assessment_refs": assessment_refs,
            "temporal_refs": temporal_refs,
            "coverage_refs": coverage_refs,
            "metrics": metrics,
            "trend_refs": previous_refs,
            "trend_state": trend_state,
            "discontinuity_reasons": reasons,
            "interpretation_limits": (
                "descriptive_only",
                "sampled_is_not_census",
                "partial_coverage_is_not_clean",
                "exposure_is_not_training_membership",
                "no_binary_float_aggregation",
            ),
        }
    )
