"""Contract tests for opt-in behavioral diagnostics and descriptive reconciliation."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    AuditDocumentRef,
    BehavioralAuditPlanDocumentV2,
    BehavioralMethodRegistryDocument,
    BehavioralObservationDocument,
    BehavioralTaskValidityDocument,
    audit_document_digest,
    parse_audit_document,
)
from polycodebench_services.behavioral_diagnostics import (
    BehavioralApplicability,
    build_behavioral_assessment,
    build_behavioral_method_registry_payload,
    resolve_behavioral_applicability,
)


def _id(number: int, version: int = 4) -> str:
    return f"{number:08x}-1111-{version}111-8111-111111111111"


def _entity(kind: str, number: int) -> dict[str, Any]:
    return {"entity_id": _id(number), "entity_kind": kind, "digest": None}


def _audit_ref(kind: str, number: int) -> dict[str, Any]:
    return {
        "document_id": _id(number),
        "digest": "sha256:" + "a" * 64,
        "kind": kind,
    }


def _metadata(actor: str = "behavioral-test") -> dict[str, Any]:
    return {
        "created_at": "2026-10-08T12:00:00Z",
        "timestamp_precision": "second",
        "actor": actor,
        "trace_id": None,
        "row_version": 0,
    }


def _document(
    kind: str,
    payload: dict[str, Any],
    number: int,
    version: int = 1,
) -> AuditDocument:
    return parse_audit_document(
        json.dumps(
            {
                "id": _id(number),
                "kind": kind,
                "schema_version": version,
                "payload": payload,
                "metadata": _metadata(),
            },
            default=str,
        )
    )


def _scenario(
    *, method_available: bool
) -> tuple[
    BehavioralMethodRegistryDocument,
    BehavioralAuditPlanDocumentV2,
    list[BehavioralTaskValidityDocument],
    list[AuditDocumentRef],
    BehavioralApplicability,
]:
    registry_payload = build_behavioral_method_registry_payload().model_dump(mode="json")
    if method_available:
        registry_payload["methods"][0]["implementation_state"] = "available"
        registry_payload["methods"][0]["implementation_ref"] = {
            "artifact_id": _id(990),
            "digest": "sha256:" + "b" * 64,
            "visibility": "private",
            "media_type": "application/octet-stream",
        }
    registry = _document("behavioral_method_registry", registry_payload, 1)
    assert isinstance(registry, BehavioralMethodRegistryDocument)
    registry_ref = AuditDocumentRef(
        document_id=registry.id,
        digest=audit_document_digest(registry),
        kind=registry.kind,
    )

    pairs: list[dict[str, Any]] = []
    validity_docs: list[BehavioralTaskValidityDocument] = []
    for index, split in enumerate(("calibration", "validation", "held_out_test")):
        base = 100 + index * 10
        original = _entity("task_version", base)
        control = _entity("task_version", base + 1)
        family = _entity("task_family", base + 2)
        validity_payload = {
            "original_task_ref": original,
            "control_task_ref": control,
            "family_ref": family,
            "semantic_relation": "semantically_equivalent",
            "difficulty_gap": "0.000000",
            "permitted_difficulty_gap": "0.050000",
            "evidence_refs": [_audit_ref("match_evidence", base + 3)],
            "author_ref": _entity("reviewer", base + 4),
            "reviewer_ref": _entity("reviewer", base + 5),
            "validity_state": "accepted",
            "validity_limits": [
                "semantic_equivalence_is_reviewed",
                "difficulty_match_is_measured",
            ],
        }
        validity = _document("behavioral_task_validity", validity_payload, base + 6)
        assert isinstance(validity, BehavioralTaskValidityDocument)
        validity_docs.append(validity)
        pairs.append(
            {
                "pair_id": _id(base + 7),
                "original_task_ref": original,
                "control_task_ref": control,
                "family_ref": family,
                "split": split,
                "validity_ref": {
                    "document_id": validity.id,
                    "digest": audit_document_digest(validity),
                    "kind": validity.kind,
                },
            }
        )

    contexts = [
        AuditDocumentRef(
            document_id=UUID(_id(201 + index)),
            digest="sha256:" + ("c" if index == 0 else "d") * 64,
            kind="model_context",
        )
        for index in range(2)
    ]
    model_slots = [
        {
            "model_context_ref": context.model_dump(mode="json"),
            "role": role,
            "recipient": recipient,
            "capabilities": [
                "per_sample_scores",
                "stable_model_revision",
                "reference_models",
            ],
            "stable_revision_verified": True,
        }
        for context, role, recipient in zip(
            contexts, ("target", "reference"), ("target.model", "reference.model"), strict=True
        )
    ]
    plan_payload = {
        "method_registry_ref": registry_ref.model_dump(mode="json"),
        "method_id": "constat",
        "audit_run_ref": _entity("audit_run", 301),
        "sample_pairs": pairs,
        "model_slots": model_slots,
        "prompt_artifact_ref": {
            "artifact_id": _id(302),
            "digest": "sha256:" + "e" * 64,
            "visibility": "private",
            "media_type": "text/plain",
        },
        "tool_policy_artifact_ref": {
            "artifact_id": _id(303),
            "digest": "sha256:" + "f" * 64,
            "visibility": "private",
            "media_type": "application/json",
        },
        "decoding_artifact_ref": {
            "artifact_id": _id(304),
            "digest": "sha256:" + "1" * 64,
            "visibility": "private",
            "media_type": "application/json",
        },
        "grading_artifact_ref": {
            "artifact_id": _id(306),
            "digest": "sha256:" + "a" * 64,
            "visibility": "private",
            "media_type": "application/json",
        },
        "exposure_policy_ref": _audit_ref("audit_plan", 307),
        "seed": "7",
        "budget": {
            "max_model_calls": 12,
            "max_samples": 6,
            "max_input_tokens": 10000,
            "max_output_tokens": 1000,
            "max_cost_micro_usd": 100000,
            "max_training_cost_micro_usd": 0,
        },
        "statistical_test": "constat_reference_corrected_performance",
        "alpha": "0.050000",
        "minimum_effect": "0.100000",
        "target_power": "0.800000",
        "bootstrap_replicates": 2000,
        "multiplicity": "holm",
        "planned_hypotheses": 1,
        "decision_rule": "adjusted_p_below_alpha_and_effect_at_least_minimum",
        "ground_truth_state": "unavailable",
        "training_manifest_ref": None,
        "exposed_family_refs": [],
        "unexposed_family_refs": [],
        "preregistered_at": "2026-10-08T12:00:00Z",
        "interpretation_limits": [
            "self_report_is_not_evidence",
            "performance_gap_is_not_training_inclusion",
            "no_universal_probability",
        ],
    }
    plan = _document("behavioral_audit_plan", plan_payload, 401, version=2)
    assert isinstance(plan, BehavioralAuditPlanDocumentV2)
    capabilities = {
        context: frozenset({"per_sample_scores", "reference_models", "stable_model_revision"})
        for context in contexts
    }
    applicability = resolve_behavioral_applicability(
        registry=registry,
        plan=plan,
        validity_documents=validity_docs,
        verified_capabilities=capabilities,
    )
    return registry, plan, validity_docs, contexts, applicability


def _observations(
    plan: BehavioralAuditPlanDocumentV2,
    contexts: list[AuditDocumentRef],
    *,
    first_failed: bool = False,
) -> list[BehavioralObservationDocument]:
    plan_ref = AuditDocumentRef(
        document_id=plan.id,
        digest=audit_document_digest(plan),
        kind=plan.kind,
    )
    results: list[BehavioralObservationDocument] = []
    counter = 500
    for pair_index, pair in enumerate(plan.payload.sample_pairs):
        for task, sample_role in (
            (pair.original_task_ref, "original"),
            (pair.control_task_ref, "control"),
        ):
            for model_index, context in enumerate(contexts):
                failed = first_failed and not results
                dispatched = not failed
                observation_payload = {
                    "plan_ref": plan_ref.model_dump(mode="json"),
                    "pair_id": str(pair.pair_id),
                    "task_ref": task.model_dump(mode="json"),
                    "sample_role": sample_role,
                    "model_context_ref": context.model_dump(mode="json"),
                    "outcome": "failed" if failed else "completed",
                    "dispatch_state": "authorized_dispatched" if dispatched else "not_dispatched",
                    "call_intent_ref": _entity("call_intent", counter) if dispatched else None,
                    "request_digest": "sha256:" + "2" * 64 if dispatched else None,
                    "access_event_refs": [_audit_ref("seal_access_event", counter + 1)]
                    if dispatched
                    else [],
                    "response_artifact_ref": {
                        "artifact_id": _id(counter + 2),
                        "digest": "sha256:" + "3" * 64,
                        "visibility": "private",
                        "media_type": "application/octet-stream",
                    }
                    if dispatched
                    else None,
                    "score": "0.750000" if dispatched else None,
                    "score_source": "structured_metric" if dispatched else None,
                    "likelihood_state": "unavailable",
                    "likelihood_value": None,
                    "confidence_state": "not_requested",
                    "confidence_value": None,
                    "cost_state": "actual" if dispatched else "not_applicable",
                    "cost_micro_usd": 11 if dispatched else None,
                    "usage_state": "reported" if dispatched else "not_applicable",
                    "input_tokens": 100 if dispatched else None,
                    "output_tokens": 20 if dispatched else None,
                    "missing_reason": "infrastructure_failure" if failed else None,
                }
                result = _document("behavioral_observation", observation_payload, counter + 3)
                assert isinstance(result, BehavioralObservationDocument)
                results.append(result)
                counter += 10 + pair_index + model_index
    return results


def test_constat_is_registered_but_blocks_when_implementation_is_not_pinned() -> None:
    registry, plan, _, _, applicability = _scenario(method_available=False)

    assert registry.payload.methods[0].method_id == "constat"
    assert registry.payload.methods[0].implementation_state == "not_pinned"
    assert applicability.state == "blocked"
    assert "method_implementation_not_pinned" in applicability.reason_codes
    assert applicability.calibration_state == "blocked_no_ground_truth"


def test_supported_method_reports_descriptive_panels_and_usage_without_inclusion_claim() -> None:
    registry, plan, validity, contexts, applicability = _scenario(method_available=True)
    assert applicability.state == "applicable"
    observations = _observations(plan, contexts)

    assessment = build_behavioral_assessment(
        plan=plan,
        registry=registry,
        observations=observations,
        applicability=applicability,
    )

    assert assessment.expected_units == 12
    assert assessment.dispatched_model_calls == 12
    assert assessment.completed_units == 12
    assert assessment.assessment_state == "complete"
    assert assessment.inference_state == "calibration_blocked"
    assert assessment.calibration_state == "blocked_no_ground_truth"
    assert assessment.power_state == "insufficient_families"
    assert assessment.power_limit_codes == ("fewer_than_two_families_in_a_split",)
    assert assessment.inclusion_claim == "not_assessed"
    assert assessment.total_cost_state == "actual"
    assert assessment.total_cost_micro_usd == 132
    assert assessment.total_input_tokens == 1200
    assert assessment.total_output_tokens == 240
    assert all(panel.mean_original_score == "0.750000" for panel in assessment.model_panels)
    assert all(panel.mean_control_score == "0.750000" for panel in assessment.model_panels)


def test_assessment_retains_failed_units_and_rejects_a_missing_planned_outcome() -> None:
    registry, plan, _, contexts, applicability = _scenario(method_available=True)
    observations = _observations(plan, contexts, first_failed=True)

    assessment = build_behavioral_assessment(
        plan=plan,
        registry=registry,
        observations=observations,
        applicability=applicability,
    )
    assert assessment.assessment_state == "partial"
    assert assessment.failed_units == 1
    assert assessment.completed_units == 11
    with pytest.raises(ValueError, match="every planned task/model unit"):
        build_behavioral_assessment(
            plan=plan,
            registry=registry,
            observations=observations[:-1],
            applicability=applicability,
        )


def test_retry_deliveries_count_against_the_frozen_model_call_cap() -> None:
    registry, plan, _, contexts, applicability = _scenario(method_available=True)
    observations = _observations(plan, contexts)
    payload = observations[0].payload.model_dump(mode="json")
    payload["access_event_refs"].append(_audit_ref("seal_access_event", 875))
    payload["cost_micro_usd"] = 22
    payload["input_tokens"] = 200
    payload["output_tokens"] = 40
    observations[0] = BehavioralObservationDocument.model_validate(
        {
            "id": UUID(_id(876)),
            "kind": "behavioral_observation",
            "schema_version": 1,
            "payload": payload,
            "metadata": _metadata(),
        }
    )

    assessment = build_behavioral_assessment(
        plan=plan,
        registry=registry,
        observations=observations,
        applicability=applicability,
    )

    assert assessment.dispatched_model_calls == 13
    assert assessment.total_cost_micro_usd == 143
    assert assessment.total_input_tokens == 1300
    assert assessment.total_output_tokens == 260
    assert assessment.budget_state == "over_cap"


def test_v2_plan_rejects_family_reuse_across_frozen_splits() -> None:
    _, plan, _, _, _ = _scenario(method_available=False)
    payload = plan.payload.model_dump(mode="json")
    payload["sample_pairs"][1]["family_ref"] = deepcopy(payload["sample_pairs"][0]["family_ref"])
    with pytest.raises(ValueError, match="families must be disjoint"):
        BehavioralAuditPlanDocumentV2.model_validate(
            {
                "id": str(plan.id),
                "kind": plan.kind,
                "schema_version": 2,
                "payload": payload,
                "metadata": _metadata(),
            }
        )


def test_observation_cannot_attach_unavailable_likelihood_or_confidence_values() -> None:
    _, plan, _, contexts, _ = _scenario(method_available=False)
    observation = _observations(plan, contexts)[0]
    payload = observation.payload.model_dump(mode="json")
    payload["confidence_state"] = "unavailable"
    payload["confidence_value"] = "0.900000"
    with pytest.raises(ValueError, match="values must be present only when actually available"):
        BehavioralObservationDocument.model_validate(
            {
                "id": str(observation.id),
                "kind": observation.kind,
                "schema_version": 1,
                "payload": payload,
                "metadata": _metadata(),
            }
        )


def test_denied_request_keeps_its_access_event_without_claiming_model_dispatch() -> None:
    _, plan, _, contexts, _ = _scenario(method_available=False)
    observation = _observations(plan, contexts)[0]
    payload = observation.payload.model_dump(mode="json")
    payload.update(
        {
            "outcome": "blocked",
            "dispatch_state": "not_dispatched",
            "call_intent_ref": None,
            "response_artifact_ref": None,
            "score": None,
            "score_source": None,
            "cost_state": "not_applicable",
            "cost_micro_usd": None,
            "usage_state": "not_applicable",
            "input_tokens": None,
            "output_tokens": None,
            "missing_reason": "capability_missing",
        }
    )
    denied = BehavioralObservationDocument.model_validate(
        {
            "id": UUID(_id(880)),
            "kind": "behavioral_observation",
            "schema_version": 1,
            "payload": payload,
            "metadata": _metadata(),
        }
    )
    assert denied.payload.dispatch_state == "not_dispatched"
    assert denied.payload.request_digest is not None
    assert denied.payload.access_event_refs


def test_failed_grading_can_retain_private_gateway_response_bytes() -> None:
    _, plan, _, contexts, _ = _scenario(method_available=True)
    observation = _observations(plan, contexts)[0]
    payload = observation.payload.model_dump(mode="json")
    payload.update(
        {
            "outcome": "failed",
            "score": None,
            "score_source": None,
            "missing_reason": "review_pending",
        }
    )
    failed = BehavioralObservationDocument.model_validate(
        {
            "id": UUID(_id(881)),
            "kind": "behavioral_observation",
            "schema_version": 1,
            "payload": payload,
            "metadata": _metadata(),
        }
    )
    assert failed.payload.response_artifact_ref is not None
    assert failed.payload.score is None
