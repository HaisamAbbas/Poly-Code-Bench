"""Opt-in behavioral diagnostic planning and descriptive reconciliation.

This module never dispatches a model call. Remote work must use the standard
model gateway and its audit-run authorization and exposure recording.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    BehavioralAssessmentPayload,
    BehavioralAuditPlanDocumentV2,
    BehavioralMethodDefinition,
    BehavioralMethodRegistryDocument,
    BehavioralMethodRegistryPayload,
    BehavioralModelPerformance,
    BehavioralObservationDocument,
    BehavioralTaskValidityDocument,
    audit_document_digest,
)

_CONSTAT_SOURCE = "https://www.sri.inf.ethz.ch/publications/dekoninck2024constat"
BehavioralCapability = Literal[
    "per_sample_scores",
    "reference_models",
    "stable_model_revision",
    "token_likelihoods",
    "owned_training_manifest",
]
StatisticalTest = Literal[
    "constat_reference_corrected_performance",
    "paired_family_bootstrap",
    "registered_custom",
]
PowerLimitCode = Literal[
    "method_unsupported",
    "capability_missing",
    "task_validity_not_accepted",
    "fewer_than_two_families_in_a_split",
    "power_analysis_not_available",
]

_INTERPRETATION_LIMITS: tuple[
    Literal["performance_gap_is_not_training_inclusion", "no_universal_probability"], ...
] = (
    "performance_gap_is_not_training_inclusion",
    "no_universal_probability",
)
_ASSESSMENT_LIMITS: tuple[
    Literal[
        "self_report_is_not_evidence",
        "performance_gap_is_not_training_inclusion",
        "no_universal_probability",
    ],
    ...,
] = (
    "self_report_is_not_evidence",
    "performance_gap_is_not_training_inclusion",
    "no_universal_probability",
)
_SCORE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class BehavioralApplicability:
    state: Literal["applicable", "blocked"]
    reason_codes: tuple[str, ...]
    calibration_state: Literal["blocked_no_ground_truth", "pending_validation"]


def build_behavioral_method_registry_payload() -> BehavioralMethodRegistryPayload:
    """Register ConStat's method boundary without pretending its adapter is installed."""
    required_capabilities: tuple[BehavioralCapability, ...] = (
        "per_sample_scores",
        "reference_models",
        "stable_model_revision",
    )
    statistical_tests: tuple[StatisticalTest, ...] = ("constat_reference_corrected_performance",)
    return BehavioralMethodRegistryPayload(
        registry_version="behavioral-methods-v1",
        methods=(
            BehavioralMethodDefinition(
                method_id="constat",
                method_version="NeurIPS-2024",
                source_url=_CONSTAT_SOURCE,
                method_family="performance_generalization",
                required_capabilities=required_capabilities,
                statistical_tests=statistical_tests,
                implementation_state="not_pinned",
                implementation_ref=None,
                assumptions=(
                    "Original and reference benchmark tasks measure comparable capabilities.",
                    "Reference model performance corrects for benchmark difficulty.",
                    "Target and reference model revisions and per-sample scores are reproducible.",
                ),
                limitations=(
                    "A corrected performance gap is not evidence of training-set inclusion.",
                    "A score cannot be interpreted as a universal contamination probability.",
                    "No adapter is executable until an exact reviewed implementation is pinned.",
                ),
            ),
        ),
        interpretation_limits=_INTERPRETATION_LIMITS,
    )


def resolve_behavioral_applicability(
    *,
    registry: BehavioralMethodRegistryDocument,
    plan: BehavioralAuditPlanDocumentV2,
    validity_documents: Sequence[BehavioralTaskValidityDocument],
    verified_capabilities: Mapping[AuditDocumentRef, frozenset[str]],
) -> BehavioralApplicability:
    """Check pinned method, independent task validity and adapter-verified capabilities."""
    reasons: list[str] = []
    registry_ref = AuditDocumentRef(
        document_id=registry.id,
        digest=audit_document_digest(registry),
        kind=registry.kind,
    )
    if plan.payload.method_registry_ref != registry_ref:
        reasons.append("method_registry_reference_mismatch")
    method = next(
        (item for item in registry.payload.methods if item.method_id == plan.payload.method_id),
        None,
    )
    if method is None:
        reasons.append("method_not_registered")
    else:
        if method.implementation_state != "available" or method.implementation_ref is None:
            reasons.append("method_implementation_not_pinned")
        if plan.payload.statistical_test not in method.statistical_tests:
            reasons.append("statistical_test_not_registered")
        if "reference_models" in method.required_capabilities and not any(
            slot.role == "reference" for slot in plan.payload.model_slots
        ):
            reasons.append("reference_cohort_missing")
        if (
            "owned_training_manifest" in method.required_capabilities
            and plan.payload.ground_truth_state != "owned_controlled"
        ):
            reasons.append("owned_training_manifest_missing")
        for slot in plan.payload.model_slots:
            available = verified_capabilities.get(slot.model_context_ref, frozenset())
            for capability in method.required_capabilities:
                if capability == "reference_models":
                    continue
                if capability not in available or (
                    capability == "stable_model_revision" and not slot.stable_revision_verified
                ):
                    reasons.append("required_model_capability_missing")
                    break

    validity_by_ref = {
        AuditDocumentRef(
            document_id=document.id,
            digest=audit_document_digest(document),
            kind=document.kind,
        ): document
        for document in validity_documents
    }
    for pair in plan.payload.sample_pairs:
        validity = validity_by_ref.get(pair.validity_ref)
        if validity is None:
            reasons.append("task_validity_evidence_missing")
            continue
        payload = validity.payload
        if (
            payload.validity_state != "accepted"
            or payload.original_task_ref != pair.original_task_ref
            or payload.control_task_ref != pair.control_task_ref
            or payload.family_ref != pair.family_ref
        ):
            reasons.append("task_validity_not_accepted_for_pair")

    return BehavioralApplicability(
        state="blocked" if reasons else "applicable",
        reason_codes=tuple(dict.fromkeys(reasons)),
        calibration_state=(
            "pending_validation"
            if plan.payload.ground_truth_state == "owned_controlled"
            else "blocked_no_ground_truth"
        ),
    )


def build_behavioral_assessment(
    *,
    plan: BehavioralAuditPlanDocumentV2,
    registry: BehavioralMethodRegistryDocument,
    observations: Sequence[BehavioralObservationDocument],
    applicability: BehavioralApplicability,
) -> BehavioralAssessmentPayload:
    """Reconcile every planned unit and report descriptive outcomes without inference."""
    plan_ref = AuditDocumentRef(
        document_id=plan.id,
        digest=audit_document_digest(plan),
        kind=plan.kind,
    )
    registry_ref = AuditDocumentRef(
        document_id=registry.id,
        digest=audit_document_digest(registry),
        kind=registry.kind,
    )
    if plan.payload.method_registry_ref != registry_ref:
        raise ValueError("behavioral assessment registry does not match the frozen plan")
    method = next(
        (item for item in registry.payload.methods if item.method_id == plan.payload.method_id),
        None,
    )
    if method is None:
        raise ValueError("behavioral assessment method is absent from the pinned registry")
    if applicability.state == "applicable" and (
        method.implementation_state != "available" or method.implementation_ref is None
    ):
        raise ValueError("an unpinned behavioral method cannot be assessed as applicable")

    pair_by_id = {pair.pair_id: pair for pair in plan.payload.sample_pairs}
    slot_by_context = {slot.model_context_ref: slot for slot in plan.payload.model_slots}
    expected = {
        (pair.pair_id, task.entity_id, role, slot.model_context_ref)
        for pair in plan.payload.sample_pairs
        for task, role in (
            (pair.original_task_ref, "original"),
            (pair.control_task_ref, "control"),
        )
        for slot in plan.payload.model_slots
    }
    seen: set[tuple[object, object, str, AuditDocumentRef]] = set()
    score_buckets: dict[tuple[AuditDocumentRef, str], list[Decimal]] = defaultdict(list)
    counts: dict[tuple[AuditDocumentRef, str], list[int]] = defaultdict(lambda: [0, 0])
    observation_refs: list[AuditDocumentRef] = []
    outcome_counts = {"completed": 0, "failed": 0, "blocked": 0, "not_run": 0}
    dispatched_model_calls = 0
    total_cost = total_input = total_output = 0
    any_dispatched = False
    cost_unknown = input_unknown = output_unknown = False
    estimated_cost = False

    for observation in observations:
        payload = observation.payload
        if payload.plan_ref != plan_ref:
            raise ValueError("behavioral observation references a different frozen plan")
        key = (
            payload.pair_id,
            payload.task_ref.entity_id,
            payload.sample_role,
            payload.model_context_ref,
        )
        if key not in expected or key in seen:
            raise ValueError("behavioral observations must uniquely match a planned unit")
        seen.add(key)
        pair = pair_by_id[payload.pair_id]
        task_ref = (
            pair.original_task_ref if payload.sample_role == "original" else pair.control_task_ref
        )
        if payload.task_ref != task_ref or payload.model_context_ref not in slot_by_context:
            raise ValueError("behavioral observation task/model differs from its frozen assignment")
        if payload.outcome == "completed" and payload.score is None:
            raise ValueError("completed behavioral observations require a measured score")
        outcome_counts[payload.outcome] += 1
        bucket = counts[(payload.model_context_ref, payload.sample_role)]
        bucket[0] += 1
        if payload.outcome == "completed":
            bucket[1] += 1
            assert payload.score is not None
            score_buckets[(payload.model_context_ref, payload.sample_role)].append(
                Decimal(payload.score)
            )
        if payload.dispatch_state != "not_dispatched":
            any_dispatched = True
            dispatched_model_calls += len(payload.access_event_refs)
            if payload.cost_state == "unavailable":
                cost_unknown = True
            elif payload.cost_micro_usd is not None:
                total_cost += payload.cost_micro_usd
                estimated_cost |= payload.cost_state == "estimated"
            if payload.input_tokens is None:
                input_unknown = True
            else:
                total_input += payload.input_tokens
            if payload.output_tokens is None:
                output_unknown = True
            else:
                total_output += payload.output_tokens
        observation_refs.append(
            AuditDocumentRef(
                document_id=observation.id,
                digest=audit_document_digest(observation),
                kind=observation.kind,
            )
        )
    if seen != expected:
        raise ValueError("every planned task/model unit needs an explicit outcome")

    panels: list[BehavioralModelPerformance] = []
    for slot in plan.payload.model_slots:
        original = counts[(slot.model_context_ref, "original")]
        control = counts[(slot.model_context_ref, "control")]
        original_scores = score_buckets[(slot.model_context_ref, "original")]
        control_scores = score_buckets[(slot.model_context_ref, "control")]
        panels.append(
            BehavioralModelPerformance(
                model_context_ref=slot.model_context_ref,
                role=slot.role,
                planned_original=original[0],
                completed_original=original[1],
                mean_original_score=_mean(original_scores),
                planned_control=control[0],
                completed_control=control[1],
                mean_control_score=_mean(control_scores),
            )
        )

    expected_units = len(expected)
    completed = outcome_counts["completed"]
    total_cost_value = None if cost_unknown else total_cost
    total_input_value = None if input_unknown else total_input
    total_output_value = None if output_unknown else total_output
    total_cost_state: Literal["actual", "estimated", "unavailable", "not_applicable"] = (
        "unavailable"
        if cost_unknown
        else "estimated"
        if estimated_cost
        else "actual"
        if any_dispatched
        else "not_applicable"
    )
    over_budget = (
        len(plan.payload.sample_pairs) * 2 > plan.payload.budget.max_samples
        or dispatched_model_calls > plan.payload.budget.max_model_calls
        or (
            total_cost_value is not None
            and total_cost_value > plan.payload.budget.max_cost_micro_usd
        )
        or (
            total_input_value is not None
            and total_input_value > plan.payload.budget.max_input_tokens
        )
        or (
            total_output_value is not None
            and total_output_value > plan.payload.budget.max_output_tokens
        )
    )
    budget_unknown = (cost_unknown or input_unknown or output_unknown) and not over_budget
    if completed == expected_units:
        state: Literal["complete", "partial", "blocked"] = "complete"
    elif completed == 0 and outcome_counts["failed"] == 0 and outcome_counts["not_run"] == 0:
        state = "blocked"
    else:
        state = "partial"
    if over_budget and state == "complete":
        state = "partial"
    families_by_split = {
        split: {
            pair.family_ref.entity_id for pair in plan.payload.sample_pairs if pair.split == split
        }
        for split in ("calibration", "validation", "held_out_test")
    }
    power_state: Literal["unsupported", "insufficient_families", "not_estimated"]
    power_limit_codes: tuple[PowerLimitCode, ...]
    if applicability.state == "blocked":
        power_state = "unsupported"
        power_limit_codes = (
            "capability_missing"
            if "required_model_capability_missing" in applicability.reason_codes
            else "task_validity_not_accepted"
            if any("task_validity" in reason for reason in applicability.reason_codes)
            else "method_unsupported",
        )
    elif any(len(families) < 2 for families in families_by_split.values()):
        power_state = "insufficient_families"
        power_limit_codes = ("fewer_than_two_families_in_a_split",)
    else:
        power_state = "not_estimated"
        power_limit_codes = ("power_analysis_not_available",)

    return BehavioralAssessmentPayload(
        plan_ref=plan_ref,
        method_registry_ref=registry_ref,
        method_id=plan.payload.method_id,
        observation_refs=tuple(observation_refs),
        expected_units=expected_units,
        dispatched_model_calls=dispatched_model_calls,
        completed_units=completed,
        failed_units=outcome_counts["failed"],
        blocked_units=outcome_counts["blocked"],
        not_run_units=outcome_counts["not_run"],
        model_panels=tuple(panels),
        total_cost_state=total_cost_state,
        total_cost_micro_usd=total_cost_value,
        training_cost_state=(
            "unavailable"
            if plan.payload.ground_truth_state == "owned_controlled"
            else "not_applicable"
        ),
        training_cost_micro_usd=None,
        training_budget_state=(
            "unknown" if plan.payload.ground_truth_state == "owned_controlled" else "not_applicable"
        ),
        total_input_tokens=total_input_value,
        total_output_tokens=total_output_value,
        budget_state="over_cap" if over_budget else "unknown" if budget_unknown else "within_cap",
        assessment_state=state,
        inference_state=(
            "unsupported"
            if applicability.state == "blocked"
            else "calibration_blocked"
            if plan.payload.ground_truth_state == "unavailable"
            else "descriptive_only"
        ),
        power_state=power_state,
        power_limit_codes=power_limit_codes,
        calibration_state=(
            "blocked_no_ground_truth"
            if plan.payload.ground_truth_state == "unavailable"
            else "pending_validation"
        ),
        inclusion_claim="not_assessed",
        interpretation_limits=_ASSESSMENT_LIMITS,
    )


def _mean(values: Sequence[Decimal]) -> str | None:
    if not values:
        return None
    return str(
        (sum(values, Decimal(0)) / Decimal(len(values))).quantize(
            _SCORE_QUANTUM, rounding=ROUND_HALF_UP
        )
    )
