"""Goldens for Decimal benchmark-health aggregation and trend scope."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from polycodebench_core.application_errors import InvalidState
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    AuditPlanDocument,
    BenchmarkHealthDocumentV2,
    BenchmarkHealthScopeV2,
    BenchmarkSnapshotDocument,
    EntityRef,
    RiskPolicyDocumentV2,
    benchmark_health_discontinuities,
)
from polycodebench_persistence.benchmark_audit import PostgresBenchmarkAuditRepository
from polycodebench_services.benchmark_health import (
    DetectorLabelObservation,
    HealthTaskObservation,
    aggregate_benchmark_health,
    build_benchmark_health_payload,
    build_benchmark_health_scope,
)
from pydantic import ValidationError


def _uuid(number: int) -> UUID:
    return UUID(f"20000000-0000-4000-8000-{number:012d}")


def _ref(kind: str, number: int) -> AuditDocumentRef:
    return AuditDocumentRef(
        document_id=_uuid(number),
        digest="sha256:" + f"{number:064x}",
        kind=kind,  # type: ignore[arg-type]
    )


def _task(number: int) -> EntityRef:
    return EntityRef(entity_id=_uuid(number), entity_kind="task_version")


def _observation(
    number: int,
    *,
    family_id: int | None = None,
    status: str = "complete",
    tier: str | None = "low_observed",
    score: str | None = "10.000000",
    exact: bool | None = False,
    semantic: bool | None = False,
    pre_cutoff: bool | None = None,
    provenance: bool | None = True,
    fresh: bool | None = True,
    planned: int = 1,
    completed: int = 1,
    failed: int = 0,
    truncated: int = 0,
    unknown: int | None = None,
) -> HealthTaskObservation:
    return HealthTaskObservation.model_validate(
        {
            "task_ref": _task(number),
            "family_ref": (
                EntityRef(entity_id=_uuid(family_id), entity_kind="task_family")
                if family_id is not None
                else None
            ),
            "assessment_status": status,
            "risk_state": tier,
            "observed_risk_index": score,
            "exact_duplicate": exact,
            "semantic_duplicate": semantic,
            "pre_cutoff_exposure": pre_cutoff,
            "provenance_complete": provenance,
            "fresh": fresh,
            "planned_units": planned,
            "completed_units": completed,
            "failed_units": failed,
            "truncated_units": truncated,
            "blocked_units": 0,
            "unknown_units": (
                planned - completed - failed - truncated if unknown is None else unknown
            ),
        }
    )


def test_all_assessed_risk_tiers_reconcile_to_the_full_selected_denominator() -> None:
    observations: list[HealthTaskObservation] = []
    for index in range(10_000):
        tier = (
            "low_observed"
            if index < 8_921
            else "medium_observed"
            if index < 9_652
            else "high_observed"
        )
        score = (
            "10.000000"
            if tier == "low_observed"
            else "30.000000"
            if tier == "medium_observed"
            else "70.000000"
        )
        observations.append(
            _observation(
                index + 1,
                family_id=10_000 + index // 5,
                tier=tier,
                score=score,
            )
        )

    all_assessed = aggregate_benchmark_health(tuple(observations), context_ref=None)
    assert all_assessed.assessment_states.selected_tasks == 10_000
    assert all_assessed.risk_tiers.low == 8_921
    assert all_assessed.risk_tiers.medium == 731
    assert all_assessed.risk_tiers.high == 348
    assert all_assessed.risk_tiers.assessed_tasks == 10_000
    assert all_assessed.families.independent_families == 2_000
    assert all_assessed.pre_cutoff_exposure.null_reason == "not_applicable"

    observations[:200] = [
        _observation(
            index + 1,
            family_id=10_000 + index // 5,
            status="unscanned",
            tier=None,
            score=None,
            exact=None,
            semantic=None,
            pre_cutoff=None,
            provenance=None,
            fresh=None,
            planned=1,
            completed=0,
        )
        for index in range(200)
    ]
    with_unscanned = aggregate_benchmark_health(tuple(observations), context_ref=None)
    assert with_unscanned.assessment_states.unscanned == 200
    assert with_unscanned.assessment_states.selected_tasks == 10_000
    assert with_unscanned.risk_tiers.assessed_tasks == 9_800
    assert with_unscanned.risk_tiers.low == 8_721
    assert with_unscanned.risk_tiers.medium == 731
    assert with_unscanned.risk_tiers.high == 348
    assert with_unscanned.exact_duplicate_prevalence.unknown_count == 200


def test_duplicate_union_deduplicates_exact_semantic_overlap_and_pre_cutoff_unknowns() -> None:
    observations = tuple(
        _observation(
            index + 1,
            exact=index < 3,
            semantic=2 <= index < 19,
            pre_cutoff=True if index < 21 else None if index < 30 else False,
        )
        for index in range(1_000)
    )
    metrics = aggregate_benchmark_health(observations, context_ref=_ref("model_context", 90))
    assert metrics.exact_duplicate_prevalence.numerator == 3
    assert metrics.exact_duplicate_prevalence.value == "0.300000"
    assert metrics.semantic_duplicate_prevalence.numerator == 17
    assert metrics.semantic_duplicate_prevalence.value == "1.700000"
    assert metrics.duplicate_union_prevalence.numerator == 19
    assert metrics.duplicate_union_prevalence.value == "1.900000"
    assert metrics.pre_cutoff_exposure.numerator == 21
    assert metrics.pre_cutoff_exposure.value == "2.100000"
    assert metrics.pre_cutoff_exposure.unknown_count == 9
    assert metrics.pre_cutoff_exposure.lower_bound is True


def test_coverage_excludes_failed_and_truncated_units_and_null_denominators_are_explicit() -> None:
    observations = tuple(
        _observation(
            index + 1,
            planned=1,
            completed=1 if index < 40 else 0,
            failed=1 if 40 <= index < 44 else 0,
            truncated=1 if index >= 44 else 0,
        )
        for index in range(50)
    )
    metrics = aggregate_benchmark_health(observations, context_ref=None)
    assert metrics.mandatory_coverage.completed_units == 40
    assert metrics.mandatory_coverage.failed_units == 4
    assert metrics.mandatory_coverage.truncated_units == 6
    assert metrics.mandatory_coverage.value == "80.000000"

    no_planned_units = aggregate_benchmark_health(
        (_observation(1, planned=0, completed=0),),
        context_ref=None,
    )
    assert no_planned_units.mandatory_coverage.value is None
    assert no_planned_units.mandatory_coverage.null_reason == "empty_denominator"


def test_mean_observed_risk_uses_only_eligible_tasks_and_half_even_rounding() -> None:
    metrics = aggregate_benchmark_health(
        (
            _observation(1, score="1.000000"),
            _observation(2, score="1.000001"),
            _observation(
                3,
                status="partial",
                tier="insufficient_evidence",
                score=None,
                completed=0,
                planned=1,
            ),
        ),
        context_ref=None,
    )
    assert metrics.mean_observed_risk.value == "1.000000"
    assert metrics.mean_observed_risk.eligible_tasks == 2
    assert metrics.mean_observed_risk.missing_tasks == 1


def test_detector_quality_is_stratified_and_reports_unknowns_with_wilson_intervals() -> None:
    outcomes = (
        ("positive", "positive"),
        ("positive", "negative"),
        ("negative", "negative"),
        ("negative", "positive"),
        ("unknown", "unknown"),
    )
    labels = tuple(
        DetectorLabelObservation.model_validate(
            {
                "label_id": _uuid(index + 100),
                "relation": "exact_component",
                "language": "python",
                "modality": "code",
                "source_ref": None,
                "ground_truth": ground_truth,
                "detector_result": detector_result,
            }
        )
        for index, (ground_truth, detector_result) in enumerate(outcomes)
    )
    metrics = aggregate_benchmark_health(
        (_observation(1),), context_ref=None, detector_labels=labels
    )
    (stratum,) = metrics.detector_quality
    assert (stratum.true_positive, stratum.false_positive) == (1, 1)
    assert (stratum.true_negative, stratum.false_negative) == (1, 1)
    assert stratum.unknown_labels == 1
    assert stratum.precision.value == "50.000000"
    assert stratum.precision.value is not None
    assert stratum.precision.lower_95 is not None
    assert stratum.precision.upper_95 is not None
    assert (
        Decimal(stratum.precision.lower_95)
        < Decimal(stratum.precision.value)
        < Decimal(stratum.precision.upper_95)
    )
    assert stratum.precision.unknown_count == 1


def test_detector_strata_reject_conflicting_digests_for_one_source_identity() -> None:
    source_ref = _ref("corpus_snapshot", 400)
    conflicting_ref = source_ref.model_copy(
        update={"digest": "sha256:" + "f" * 64},
    )
    labels = tuple(
        DetectorLabelObservation.model_validate(
            {
                "label_id": _uuid(index + 410),
                "relation": "exact_component",
                "language": "python",
                "modality": "code",
                "source_ref": ref,
                "ground_truth": "positive",
                "detector_result": "positive",
            }
        )
        for index, ref in enumerate((source_ref, conflicting_ref))
    )
    with pytest.raises(ValueError, match="conflicting digests"):
        aggregate_benchmark_health((_observation(1),), context_ref=None, detector_labels=labels)


def test_one_family_with_five_variants_counts_as_one_independent_family() -> None:
    metrics = aggregate_benchmark_health(
        tuple(_observation(index, family_id=500) for index in range(1, 6)),
        context_ref=None,
    )
    assert metrics.families.selected_tasks == 5
    assert metrics.families.independent_families == 1


def test_comparability_key_fixes_source_windows_and_marks_scope_breaks() -> None:
    population = tuple(_task(index) for index in range(1, 6))

    def scope(
        *,
        start: str,
        policy: AuditDocumentRef | None = None,
        selected: tuple[EntityRef, ...] = population,
        method: str = "exact-v1",
    ) -> BenchmarkHealthScopeV2:
        return build_benchmark_health_scope(
            membership_ref=_ref("benchmark_snapshot", 20),
            plan_ref=_ref("audit_plan", 21),
            policy_ref=policy or _ref("risk_policy", 22),
            context_ref=_ref("model_context", 23),
            source_refs=(_ref("corpus_snapshot", 24),),
            population_task_refs=population,
            selected_task_refs=selected,
            sampling_method="uniform_hash_v1",
            sampling_design={"mode": "census" if len(selected) == 5 else "sampled"},
            scan_methods=(method,),
            family_mapping=tuple((task, None) for task in selected),
            family_mapping_ref=None,
            source_window_start=start,
            source_window_end="2026-10-03T00:00:00Z",
            provenance_definition_version="provenance-v1",
            freshness_definition_version="freshness-v1",
            freshness_max_scan_age_hours=168,
            freshness_max_source_age_days=30,
        )

    current = scope(start="2026-10-01T00:00:00Z")
    later_window = scope(start="2026-10-02T00:00:00Z")
    assert current.comparability_key != later_window.comparability_key
    assert benchmark_health_discontinuities(current, later_window) == ("source_window_changed",)

    changed_policy = scope(start="2026-10-01T00:00:00Z", policy=_ref("risk_policy", 25))
    assert benchmark_health_discontinuities(current, changed_policy) == ("risk_policy_changed",)

    sampled = scope(start="2026-10-01T00:00:00Z", selected=population[:3])
    assert sampled.sampling_mode == "sampled"
    assert benchmark_health_discontinuities(current, sampled) == ("sampling_changed",)


def test_duplicate_task_inputs_and_invalid_coverage_fail_closed() -> None:
    observation = _observation(1)
    with pytest.raises(ValueError, match="cannot repeat"):
        aggregate_benchmark_health((observation, observation), context_ref=None)
    with pytest.raises(ValidationError, match="planned units"):
        _observation(2, planned=2, completed=1, unknown=0)


def test_persisted_health_points_bind_exact_membership_and_are_append_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(1)
    membership_ref = _ref("benchmark_snapshot", 20)
    plan_ref = _ref("audit_plan", 21)
    policy_ref = _ref("risk_policy", 22)
    scope = build_benchmark_health_scope(
        membership_ref=membership_ref,
        plan_ref=plan_ref,
        policy_ref=policy_ref,
        context_ref=None,
        source_refs=(),
        population_task_refs=(task,),
        selected_task_refs=(task,),
        sampling_method="census_v1",
        sampling_design={"mode": "census"},
        scan_methods=("exact-v1",),
        family_mapping=((task, None),),
        family_mapping_ref=None,
        source_window_start="2026-10-01T00:00:00Z",
        source_window_end="2026-10-08T00:00:00Z",
        provenance_definition_version="provenance-v1",
        freshness_definition_version="freshness-v1",
        freshness_max_scan_age_hours=168,
        freshness_max_source_age_days=30,
    )
    payload = build_benchmark_health_payload(
        scope=scope,
        observations=(
            _observation(
                1,
                status="unscanned",
                tier=None,
                score=None,
                planned=0,
                completed=0,
            ),
        ),
        assessment_refs=(),
        temporal_refs=(),
        coverage_refs=(),
    )
    health = BenchmarkHealthDocumentV2.model_construct(
        id=_uuid(30),
        payload=payload,
        supersedes_id=None,
    )
    snapshot = BenchmarkSnapshotDocument.model_construct(
        id=membership_ref.document_id,
        payload=SimpleNamespace(membership=(task,)),
    )
    plan = AuditPlanDocument.model_construct(
        id=plan_ref.document_id,
        payload=SimpleNamespace(
            benchmark_ref=membership_ref,
            policy=policy_ref,
            model_context=None,
            task_refs=(task,),
            source_plan=(),
            sample_design={"mode": "census"},
            methods=("exact-v1",),
        ),
    )
    policy = RiskPolicyDocumentV2.model_construct(id=policy_ref.document_id, payload=object())
    documents: dict[UUID, object] = {
        membership_ref.document_id: snapshot,
        plan_ref.document_id: plan,
        policy_ref.document_id: policy,
    }

    def lookup_document(cls: type[object], connection: object, ref: AuditDocumentRef) -> object:
        del cls, connection
        return documents[ref.document_id]

    monkeypatch.setattr(
        PostgresBenchmarkAuditRepository,
        "_document_by_ref",
        classmethod(lookup_document),
    )
    PostgresBenchmarkAuditRepository._validate_benchmark_health_document(object(), health)

    documents[plan_ref.document_id] = AuditPlanDocument.model_construct(
        id=plan_ref.document_id,
        payload=SimpleNamespace(
            benchmark_ref=_ref("benchmark_snapshot", 23),
            policy=policy_ref,
            model_context=None,
            task_refs=(task,),
            source_plan=(),
            sample_design={"mode": "census"},
            methods=("exact-v1",),
        ),
    )
    with pytest.raises(InvalidState, match="frozen plan"):
        PostgresBenchmarkAuditRepository._validate_benchmark_health_document(object(), health)

    successor = BenchmarkHealthDocumentV2.model_construct(
        id=_uuid(31),
        payload=payload,
        supersedes_id=health.id,
    )
    with pytest.raises(InvalidState, match="immutable snapshots"):
        PostgresBenchmarkAuditRepository._validate_benchmark_health_document(object(), successor)
