from __future__ import annotations

import json
from uuid import uuid4

import pytest
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    DocumentMetadata,
    EntityRef,
    RiskAssessmentPayloadV2,
    RiskComponentCoverage,
    RiskPolicyDocumentV2,
    RiskPolicyPayloadV2,
    RiskSignalObservation,
    audit_document_digest,
    parse_audit_document,
)
from polycodebench_services.risk_assessment import (
    assess_observed_risk,
    build_observed_risk_v1_policy,
    observed_risk_claim,
)
from pydantic import ValidationError

_DIGEST = "sha256:" + "a" * 64
_NOW = "2026-10-08T12:00:00Z"


def _ref(kind: str) -> AuditDocumentRef:
    return AuditDocumentRef(document_id=uuid4(), digest=_DIGEST, kind=kind)  # type: ignore[arg-type]


def _entity(kind: str) -> EntityRef:
    return EntityRef(entity_id=uuid4(), entity_kind=kind, digest=_DIGEST)


def _policy_document(policy: RiskPolicyPayloadV2) -> RiskPolicyDocumentV2:
    return RiskPolicyDocumentV2(
        id=uuid4(),
        kind="risk_policy",
        schema_version=2,
        payload=policy,
        metadata=DocumentMetadata(
            created_at=_NOW,
            timestamp_precision="second",
            actor="test-policy-builder",
            trace_id=None,
            row_version=0,
        ),
    )


def _signal(
    key: str,
    *,
    basis: str = "completed_scope_no_match",
    evidence_state: str = "none",
    value: str | None = "0.000000",
    state: str = "observed",
) -> RiskSignalObservation:
    is_scored = key in {
        "duplication",
        "synthetic_similarity",
        "corpus_overlap_evidence",
        "web_exposure",
        "leakage_history",
    }
    context = key in {"publication_age", "popularity"}
    diagnostic = key == "model_familiarity"
    if state != "observed":
        return RiskSignalObservation(
            signal=key,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            evidence_state=evidence_state,  # type: ignore[arg-type]
            basis=None,
            normalized_value=None,
            raw_value=None,
            raw_interval=None,
            raw_unit=None,
            evidence_refs=(),
            author_ref=None,
            review_ref=None,
            reviewer_ref=None,
            configuration_ref=None,
            observed_at=None,
            source_lineage_refs=(),
            verification_state="unverified",
            rights_state="unknown",
            unknown_reason="not_measured_in_this_audit_scope",
        )
    accepted = evidence_state == "accepted"
    refs = (_ref("match_evidence"),) if accepted else (_ref("coverage_manifest"),)
    raw_interval = None
    raw_unit = None
    if context:
        basis = "context_only"
        value = None
        raw_value = None if key == "publication_age" else "downloads"
        raw_interval = (
            ("147.000000", "147.000000") if key == "publication_age" else ("42.000000", "42.000000")
        )
        raw_unit = "days" if key == "publication_age" else "count"
    elif diagnostic:
        basis = "behavioral_diagnostic_only"
        value = "0.800000"
        raw_value = None
    else:
        raw_value = None
    return RiskSignalObservation(
        signal=key,  # type: ignore[arg-type]
        state="observed",
        evidence_state=evidence_state,  # type: ignore[arg-type]
        basis=basis,  # type: ignore[arg-type]
        normalized_value=value,
        raw_value=raw_value,
        raw_interval=raw_interval,
        raw_unit=raw_unit,  # type: ignore[arg-type]
        evidence_refs=refs,
        author_ref=_entity("reviewer") if accepted else None,
        review_ref=_ref("match_evidence") if accepted else None,
        reviewer_ref=_entity("reviewer") if accepted else None,
        configuration_ref=_ref("task_fingerprint") if not context else _ref("audit_plan"),
        observed_at=_NOW,
        source_lineage_refs=(_ref("corpus_snapshot"),) if is_scored else (),
        verification_state="verified" if is_scored or accepted else "not_applicable",
        rights_state="approved" if is_scored or accepted else "not_applicable",
        unknown_reason=None,
    )


def _no_match_signals() -> list[RiskSignalObservation]:
    return [
        _signal("publication_age"),
        _signal("web_exposure"),
        _signal("duplication"),
        _signal("corpus_overlap_evidence"),
        _signal("popularity"),
        _signal("synthetic_similarity"),
        _signal("model_familiarity"),
        _signal("leakage_history"),
    ]


def _coverage(component: str, *, complete: bool = True) -> RiskComponentCoverage:
    return RiskComponentCoverage(
        component=component,  # type: ignore[arg-type]
        planned_units=1,
        complete_units=1 if complete else 0,
        failed_units=0,
        blocked_units=0 if complete else 1,
        truncated_units=0,
        pending_reviews=0,
        scope_refs=(_ref("coverage_manifest"),),
    )


def _pending_coverage(component: str, *, pending: int = 1) -> RiskComponentCoverage:
    return RiskComponentCoverage(
        component=component,  # type: ignore[arg-type]
        planned_units=1,
        complete_units=1,
        failed_units=0,
        blocked_units=0,
        truncated_units=0,
        pending_reviews=pending,
        scope_refs=(_ref("coverage_manifest"),),
    )


def _assess(
    signals: list[RiskSignalObservation],
    *,
    complete: bool = True,
    calibrated: bool = True,
    coverage: list[RiskComponentCoverage] | None = None,
) -> RiskAssessmentPayloadV2:
    calibration_refs = (_ref("audit_attestation"),) if calibrated else ()
    policy = build_observed_risk_v1_policy(
        calibration_state="validated" if calibrated else "proposed",
        calibration_evidence_refs=calibration_refs,
    )
    policy_document = _policy_document(policy)
    result = assess_observed_risk(
        plan_ref=_ref("audit_plan"),
        task_ref=_entity("task_version"),
        policy_document=policy_document,
        signal_observations=signals,
        component_coverage=coverage
        or [_coverage(component, complete=complete) for component in ("M", "C", "E", "L")],
    )
    assert result.policy_ref.document_id == policy_document.id
    assert result.policy_ref.digest == audit_document_digest(policy_document)
    return result


def _set_scored(
    signals: list[RiskSignalObservation], key: str, basis: str, value: str
) -> list[RiskSignalObservation]:
    index = next(i for i, item in enumerate(signals) if item.signal == key)
    signals[index] = _signal(key, basis=basis, evidence_state="accepted", value=value)
    return signals


def test_versioned_policy_lists_all_eight_signals_and_preserves_context_roles() -> None:
    policy = build_observed_risk_v1_policy()
    assert policy.formula == "50*M+25*C+15*E+10*L"
    assert len(policy.signal_definitions) == 8
    assert {item.signal for item in policy.signal_definitions} == {
        "publication_age",
        "web_exposure",
        "duplication",
        "corpus_overlap_evidence",
        "popularity",
        "synthetic_similarity",
        "model_familiarity",
        "leakage_history",
    }
    assert {item.signal for item in policy.signal_definitions if item.role == "context"} == {
        "publication_age",
        "popularity",
    }
    assert {
        item.signal for item in policy.signal_definitions if item.applicability == "required"
    } == {
        "web_exposure",
        "duplication",
        "corpus_overlap_evidence",
        "synthetic_similarity",
        "leakage_history",
    }
    assert (
        next(item for item in policy.signal_definitions if item.signal == "model_familiarity").role
        == "diagnostic"
    )
    with pytest.raises(ValidationError, match="frozen M/C/E/L weights"):
        RiskPolicyPayloadV2.model_validate(
            policy.model_dump(mode="python") | {"component_weights": {"M": "20.000000"}}
        )


def test_signal_records_reject_self_review_unapproved_rights_and_reversed_age_ranges() -> None:
    accepted = _signal(
        "duplication",
        basis="exact_or_semantic_duplicate",
        evidence_state="accepted",
        value="1.000000",
    )
    with pytest.raises(ValidationError, match="cannot independently review"):
        RiskSignalObservation.model_validate(
            accepted.model_dump(mode="python") | {"reviewer_ref": accepted.author_ref}
        )
    with pytest.raises(ValidationError, match="verified, rights-approved"):
        RiskSignalObservation.model_validate(
            accepted.model_dump(mode="python") | {"rights_state": "unknown"}
        )
    age = _signal("publication_age")
    with pytest.raises(ValidationError, match="ordered raw-value interval"):
        RiskSignalObservation.model_validate(
            age.model_dump(mode="python") | {"raw_interval": ("148.000000", "147.000000")}
        )


def test_all_required_decimal_score_goldens_and_thresholds() -> None:
    all_signals = _no_match_signals()
    for signal, basis, value in (
        ("duplication", "exact_or_semantic_duplicate", "1.000000"),
        ("corpus_overlap_evidence", "verified_corpus_overlap", "1.000000"),
        ("web_exposure", "verified_public_substantive", "1.000000"),
        ("leakage_history", "corroborated_task_model_report", "1.000000"),
    ):
        _set_scored(all_signals, signal, basis, value)
    full = _assess(all_signals)
    assert full.calculated_lower_bound == "100.000000"
    assert full.calculated_upper_bound == "100.000000"
    assert full.observed_index.value == "100.000000"
    assert full.state == "high_observed"

    signals = _no_match_signals()
    _set_scored(signals, "duplication", "exact_or_semantic_duplicate", "1.000000")
    _set_scored(signals, "web_exposure", "verified_public_substantive", "1.000000")
    assert _assess(signals).observed_index.value == "65.000000"

    signals = _no_match_signals()
    _set_scored(signals, "duplication", "question_only_overlap", "0.600000")
    _set_scored(signals, "web_exposure", "verified_public_substantive", "1.000000")
    assert _assess(signals).observed_index.value == "45.000000"

    signals = _no_match_signals()
    assert _assess(signals).observed_index.value == "0.000000"

    signals = _no_match_signals()
    _set_scored(signals, "corpus_overlap_evidence", "verified_corpus_overlap", "1.000000")
    assert _assess(signals).state == "medium_observed"
    signals = _no_match_signals()
    _set_scored(signals, "duplication", "exact_or_semantic_duplicate", "1.000000")
    _set_scored(signals, "leakage_history", "corroborated_task_model_report", "1.000000")
    assert _assess(signals).observed_index.value == "60.000000"
    assert _assess(signals).state == "high_observed"


def test_partial_unknown_component_has_exact_bounds_and_high_partial_claim() -> None:
    signals = _no_match_signals()
    _set_scored(signals, "duplication", "exact_or_semantic_duplicate", "1.000000")
    _set_scored(signals, "web_exposure", "verified_public_substantive", "1.000000")
    signals[3] = _signal(
        "corpus_overlap_evidence", state="unknown", evidence_state="none", value=None
    )
    coverage = [_coverage("M"), _coverage("C", complete=False), _coverage("E"), _coverage("L")]
    result = _assess(signals, coverage=coverage)
    assert result.observed_index.value is None
    assert result.calculated_lower_bound == "65.000000"
    assert result.calculated_upper_bound == "90.000000"
    assert result.state == "high_observed"
    claim = observed_risk_claim(result).lower()
    assert "lower bound" in claim and "upper bound" in claim and "partial" in claim
    assert "probability" not in claim and "clean" not in claim


def test_only_measured_m_component_is_bounded_and_never_labelled_low() -> None:
    signals = _no_match_signals()
    _set_scored(signals, "duplication", "concept_or_boilerplate", "0.000000")
    for index in (1, 3, 7):
        key = signals[index].signal
        signals[index] = _signal(key, state="unknown", evidence_state="none", value=None)
    coverage = [
        _coverage("M"),
        _coverage("C", complete=False),
        _coverage("E", complete=False),
        _coverage("L", complete=False),
    ]
    result = _assess(signals, coverage=coverage)
    assert result.calculated_lower_bound == "0.000000"
    assert result.calculated_upper_bound == "50.000000"
    assert result.state == "insufficient_evidence"
    assert result.observed_index.value is None


def test_correlated_context_and_behavioral_signals_do_not_inflate_components() -> None:
    signals = _no_match_signals()
    _set_scored(signals, "duplication", "shared_family", "0.300000")
    result = _assess(signals)
    assert next(item for item in result.components if item.component == "M").points == "15.000000"
    assert result.observed_index.value == "15.000000"
    assert next(
        item for item in result.signal_observations if item.signal == "popularity"
    ).raw_value
    assert next(
        item for item in result.signal_observations if item.signal == "publication_age"
    ).raw_interval == ("147.000000", "147.000000")


def test_incomplete_query_and_unvalidated_policy_never_produce_low_tier() -> None:
    signals = _no_match_signals()
    result = _assess(signals, complete=False, calibrated=False)
    assert result.state == "insufficient_evidence"
    assert result.observed_index.value is None
    assert "insufficient evidence" in observed_risk_claim(result).lower()


def test_candidate_or_pending_match_is_not_accepted_score_evidence() -> None:
    signals = _no_match_signals()
    signals[2] = _signal(
        "duplication",
        basis="exact_or_semantic_duplicate",
        evidence_state="candidate_only",
        value="1.000000",
    )
    coverage = [_pending_coverage("M"), _coverage("C"), _coverage("E"), _coverage("L")]
    result = _assess(signals, coverage=coverage)
    assert result.calculated_lower_bound == "0.000000"
    assert result.calculated_upper_bound == "50.000000"
    assert result.state == "insufficient_evidence"
    assert result.accepted_evidence == ()


def test_no_match_with_failed_scope_stays_unknown_instead_of_becoming_zero() -> None:
    result = _assess(_no_match_signals(), complete=False)
    assert result.calculated_lower_bound == "0.000000"
    assert result.calculated_upper_bound == "100.000000"
    assert result.observed_index.value is None
    assert result.state == "insufficient_evidence"


def test_partial_component_adds_its_full_weight_to_the_conservative_upper_bound() -> None:
    signals = _no_match_signals()
    _set_scored(signals, "duplication", "exact_or_semantic_duplicate", "1.000000")
    signals[5] = _signal("synthetic_similarity", state="unknown", evidence_state="none", value=None)
    result = _assess(signals)
    assert result.calculated_lower_bound == "50.000000"
    assert result.calculated_upper_bound == "100.000000"
    assert result.state == "insufficient_evidence"


def test_maximum_component_strength_caps_correlated_duplicate_signals() -> None:
    signals = _no_match_signals()
    _set_scored(signals, "duplication", "exact_or_semantic_duplicate", "1.000000")
    _set_scored(signals, "synthetic_similarity", "exact_or_semantic_duplicate", "1.000000")
    result = _assess(signals)
    assert next(item for item in result.components if item.component == "M").points == "50.000000"
    assert result.observed_index.value == "50.000000"


def test_risk_documents_round_trip_as_v2_without_changing_v1_reader() -> None:
    policy = build_observed_risk_v1_policy()
    document = _policy_document(policy)
    parsed = parse_audit_document(json.dumps(document.model_dump(mode="json")))
    assert parsed.schema_version == 2
    assert parsed.kind == "risk_policy"
    assessment = _assess(_no_match_signals())
    assessment_document = {
        "id": str(uuid4()),
        "kind": "risk_assessment",
        "schema_version": 2,
        "payload": assessment.model_dump(mode="json"),
        "metadata": document.model_dump(mode="json")["metadata"],
    }
    parsed_assessment = parse_audit_document(json.dumps(assessment_document))
    assert parsed_assessment.kind == "risk_assessment"
    assert parsed_assessment.schema_version == 2
    with pytest.raises(ValueError, match="unknown audit document kind/schema version"):
        parse_audit_document('{"kind":"risk_policy","schema_version":3}')
