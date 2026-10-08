"""Pure observed-risk aggregation with explicit evidence and coverage gates."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from polycodebench_core.benchmark_audit_documents import (
    OBSERVED_RISK_SIGNAL_LAYOUT,
    AuditDocumentRef,
    DecimalMeasurement,
    EntityRef,
    RiskAssessmentPayloadV2,
    RiskComponentAssessment,
    RiskComponentCoverage,
    RiskComponentKey,
    RiskIndexMeasurement,
    RiskPolicyDocumentV2,
    RiskPolicyPayloadV2,
    RiskSignalDefinition,
    RiskSignalKey,
    RiskSignalObservation,
    RiskState,
    audit_document_digest,
)
from polycodebench_core.models import Decimal6

_COMPONENT_WEIGHTS: dict[RiskComponentKey, Decimal] = {
    "M": Decimal("50.000000"),
    "C": Decimal("25.000000"),
    "E": Decimal("15.000000"),
    "L": Decimal("10.000000"),
}
_SIGNAL_LAYOUT = OBSERVED_RISK_SIGNAL_LAYOUT
_SIGNALS_BY_COMPONENT: dict[RiskComponentKey, frozenset[RiskSignalKey]] = {
    "M": frozenset({"duplication", "synthetic_similarity"}),
    "C": frozenset({"corpus_overlap_evidence"}),
    "E": frozenset({"web_exposure"}),
    "L": frozenset({"leakage_history"}),
}
_COMPONENT_ORDER: tuple[RiskComponentKey, ...] = ("M", "C", "E", "L")


def build_observed_risk_v1_policy(
    *,
    calibration_state: Literal["proposed", "validated"] = "proposed",
    calibration_evidence_refs: Sequence[AuditDocumentRef] = (),
) -> RiskPolicyPayloadV2:
    """Build the frozen proposed policy; calibrated variants require evidence refs."""
    return RiskPolicyPayloadV2(
        policy_version="observed-risk-v1",
        formula="50*M+25*C+15*E+10*L",
        component_weights={key: f"{value:.6f}" for key, value in _COMPONENT_WEIGHTS.items()},
        tier_thresholds={"low": "25.000000", "medium": "60.000000"},
        signal_definitions=tuple(
            RiskSignalDefinition(
                signal=signal,
                role=role,
                applicability=applicability,
                definition=definition,
            )
            for signal, role, applicability, definition in _SIGNAL_LAYOUT
        ),
        required_components=_COMPONENT_ORDER,
        missingness_policy="unknown_components_null_bounds_include_unavailable_weight",
        calibration_state=calibration_state,
        calibration_evidence_refs=tuple(calibration_evidence_refs),
        claim_policy="heuristic_only_no_probability_or_cleanliness_claim",
    )


def assess_observed_risk(
    *,
    plan_ref: AuditDocumentRef,
    task_ref: EntityRef,
    policy_document: RiskPolicyDocumentV2,
    signal_observations: Sequence[RiskSignalObservation],
    component_coverage: Sequence[RiskComponentCoverage],
    context_ref: AuditDocumentRef | None = None,
) -> RiskAssessmentPayloadV2:
    """Aggregate reviewed signals, retaining missingness as bounds rather than zeros."""
    if plan_ref.kind != "audit_plan":
        raise ValueError("risk assessment requires the frozen plan and risk policy references")
    policy = policy_document.payload
    policy_ref = AuditDocumentRef(
        document_id=policy_document.id,
        digest=audit_document_digest(policy_document),
        kind=policy_document.kind,
    )
    if task_ref.entity_kind != "task_version":
        raise ValueError("risk assessment must bind a task version")
    if len(signal_observations) != 8 or len({item.signal for item in signal_observations}) != 8:
        raise ValueError("risk assessment requires exactly one observation for all eight signals")
    expected_signals = {signal for signal, _, _, _ in _SIGNAL_LAYOUT}
    if {item.signal for item in signal_observations} != expected_signals:
        raise ValueError("risk assessment signal set does not match observed-risk-v1")
    if len(component_coverage) != 4 or len({item.component for item in component_coverage}) != 4:
        raise ValueError("risk assessment requires one coverage record per score component")
    if {item.component for item in component_coverage} != set(_COMPONENT_ORDER):
        raise ValueError("risk assessment coverage set does not match observed-risk-v1")

    observations = {item.signal: item for item in signal_observations}
    coverage_by_component = {item.component: item for item in component_coverage}
    assessment_components: list[RiskComponentAssessment] = []
    explanation_codes: set[str] = set()

    for component in _COMPONENT_ORDER:
        coverage = coverage_by_component[component]
        group_observations = [observations[key] for key in _SIGNALS_BY_COMPONENT[component]]
        unresolved_signals = [
            item
            for item in group_observations
            if item.state != "observed" or item.evidence_state in {"candidate_only", "disputed"}
        ]
        coverage_complete = (
            coverage.complete_units == coverage.planned_units
            and coverage.failed_units == 0
            and coverage.blocked_units == 0
            and coverage.truncated_units == 0
            and coverage.pending_reviews == 0
        )
        resolved = coverage_complete and not unresolved_signals
        accepted_values = [
            Decimal(item.normalized_value)
            for item in group_observations
            if item.evidence_state == "accepted" and item.normalized_value is not None
        ]
        measured_value = max(accepted_values) if accepted_values else None
        if measured_value is None and resolved:
            measured_value = Decimal("0.000000")
        weight = _COMPONENT_WEIGHTS[component]
        points = _quantize(weight * measured_value) if measured_value is not None else Decimal(0)
        missing_weight = Decimal(0) if resolved else weight

        evidence_refs = _unique_refs(
            ref
            for observation in group_observations
            if observation.evidence_state == "accepted"
            for ref in observation.evidence_refs
        )
        coverage_refs = _unique_refs(coverage.scope_refs)
        reasons: list[str] = []
        if not coverage_complete:
            reasons.append("coverage_incomplete")
        if unresolved_signals:
            reasons.extend(
                "signal_unmeasured" if item.state != "observed" else "signal_review_pending"
                for item in unresolved_signals
            )
        if resolved and measured_value == 0:
            reasons.append("complete_scope_no_accepted_substantive_match")
        elif accepted_values:
            reasons.append("maximum_accepted_substantive_signal_strength")
        if reasons:
            explanation_codes.update(reasons)
        assessment_components.append(
            RiskComponentAssessment(
                component=component,
                availability="measured" if measured_value is not None else "unknown",
                coverage_state=(
                    "complete"
                    if resolved
                    else "partial"
                    if coverage.complete_units > 0
                    else "unavailable"
                ),
                value=None if measured_value is None else _fixed(measured_value),
                points=_fixed(points),
                missing_weight=_fixed(missing_weight),
                evidence_refs=evidence_refs,
                coverage_refs=coverage_refs,
                reason_codes=tuple(dict.fromkeys(reasons)),
            )
        )

    scope_complete = all(
        component.coverage_state == "complete" for component in assessment_components
    )
    has_completed_units = any(item.complete_units > 0 for item in component_coverage)
    has_blocking_outcomes = any(
        item.failed_units or item.blocked_units or item.truncated_units
        for item in component_coverage
    )
    if scope_complete:
        scope_state: Literal["complete", "partial", "blocked", "not_run"] = "complete"
    elif has_completed_units:
        scope_state = "partial"
    elif has_blocking_outcomes:
        scope_state = "blocked"
    else:
        scope_state = "not_run"

    lower = sum((Decimal(item.points) for item in assessment_components), Decimal(0))
    upper = min(
        Decimal(100),
        lower + sum((Decimal(item.missing_weight) for item in assessment_components), Decimal(0)),
    )
    if scope_state == "complete" and policy.calibration_state == "validated":
        index = _fixed(lower)
        index_measurement = RiskIndexMeasurement(value=index, null_reason=None)
        if lower < Decimal("25.000000"):
            state: RiskState = "low_observed"
        elif lower < Decimal("60.000000"):
            state = "medium_observed"
        else:
            state = "high_observed"
        explanation_codes.add("calibrated_complete_scope_tier")
    else:
        if scope_state == "not_run":
            null_reason: Literal["scope_incomplete", "policy_uncalibrated", "not_run"] = "not_run"
        elif scope_state != "complete":
            null_reason = "scope_incomplete"
        else:
            null_reason = "policy_uncalibrated"
        index_measurement = RiskIndexMeasurement(value=None, null_reason=null_reason)
        if lower >= Decimal("60.000000"):
            state = "high_observed"
            explanation_codes.add("high_lower_bound_partial_or_uncalibrated")
        else:
            state = "insufficient_evidence"
            explanation_codes.add("tier_withheld_by_coverage_or_calibration")

    accepted_refs = _unique_refs(
        ref
        for observation in signal_observations
        if observation.evidence_state == "accepted"
        for ref in observation.evidence_refs
    )
    completed_components = sum(
        1 for item in assessment_components if item.coverage_state == "complete"
    )
    coverage_percent = _fixed(Decimal(completed_components) * Decimal(25))
    return RiskAssessmentPayloadV2(
        plan_ref=plan_ref,
        task_ref=task_ref,
        context_ref=context_ref,
        policy_ref=policy_ref,
        scope_state=scope_state,
        calibration_state=policy.calibration_state,
        signal_observations=tuple(observations[key] for key, _, _, _ in _SIGNAL_LAYOUT),
        component_coverage=tuple(coverage_by_component[key] for key in _COMPONENT_ORDER),
        components=tuple(assessment_components),
        accepted_evidence=accepted_refs,
        calculated_lower_bound=_fixed(lower),
        calculated_upper_bound=_fixed(upper),
        observed_index=index_measurement,
        coverage=DecimalMeasurement(value=coverage_percent, null_reason=None),
        state=state,
        explanation_codes=tuple(sorted(explanation_codes)),
    )


def observed_risk_claim(assessment: RiskAssessmentPayloadV2) -> str:
    """Return a constrained claim that cannot describe the heuristic as probability."""
    lower = assessment.calculated_lower_bound
    upper = assessment.calculated_upper_bound
    if assessment.state == "low_observed":
        return (
            f"Low observed-risk index {assessment.observed_index.value} "
            "under the completed declared scope."
        )
    if assessment.state == "medium_observed":
        return (
            f"Medium observed-risk index {assessment.observed_index.value} "
            "under the completed declared scope."
        )
    if assessment.state == "high_observed" and assessment.observed_index.value is not None:
        return (
            f"High observed-risk index {assessment.observed_index.value} "
            "under the completed declared scope."
        )
    if assessment.state == "high_observed":
        return (
            f"High observed-risk lower bound {lower} with an upper bound {upper}; "
            "coverage is partial or calibration is pending."
        )
    return (
        f"Insufficient evidence for a low or medium tier; "
        f"policy missingness bounds are {lower} to {upper}."
    )


def _unique_refs(refs: Iterable[AuditDocumentRef]) -> tuple[AuditDocumentRef, ...]:
    result: dict[tuple[str, str], AuditDocumentRef] = {}
    for ref in refs:
        result[(ref.kind, str(ref.document_id))] = ref
    return tuple(result[key] for key in sorted(result))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _fixed(value: Decimal) -> Decimal6:
    return f"{_quantize(value):.6f}"
