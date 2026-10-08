"""Pure firewall reduction over immutable evidence contracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    AuditDocumentRef,
    DerivedBenchmarkManifestPayload,
    EntityRef,
    FirewallDecisionPayloadV2,
    FirewallPolicyDocumentV2,
    FirewallReason,
    FirewallScopeDocument,
    ReplacementPlanPayloadV2,
    ReplacementValidationDocument,
    RiskAssessmentDocumentV2,
    TemporalAssessmentDocumentV2,
    audit_document_digest,
)


def document_ref(document: AuditDocument) -> AuditDocumentRef:
    """Return a typed content reference for an immutable audit document."""
    return AuditDocumentRef(
        document_id=document.id,
        digest=audit_document_digest(document),
        kind=document.kind,
    )


def validate_derived_family_split_consistency(
    *,
    candidate: DerivedBenchmarkManifestPayload,
    related_manifests: Sequence[DerivedBenchmarkManifestPayload],
) -> None:
    """Keep each source family in one split across a benchmark version's manifests."""
    family_splits: dict[str, set[str]] = {}
    for manifest in related_manifests:
        for entry in manifest.entries:
            family_splits.setdefault(entry.source_family_id, set()).add(entry.split)
            if entry.derived_source_family_id is not None:
                family_splits.setdefault(entry.derived_source_family_id, set()).add(entry.split)
    for entry in candidate.entries:
        for family_id in (entry.source_family_id, entry.derived_source_family_id):
            if family_id is None:
                continue
            existing_splits = family_splits.get(family_id, set())
            if existing_splits and existing_splits != {entry.split}:
                raise ValueError("source-family variants cannot cross official splits")


def validate_replacement_draft_budget(
    *,
    plan: ReplacementPlanPayloadV2,
    source_id: str,
    existing_total: int,
    existing_for_source: int,
) -> None:
    """Enforce preregistered plan and per-source draft limits before persistence."""
    if existing_total < 0 or existing_for_source < 0:
        raise ValueError("replacement draft counts cannot be negative")
    if existing_total >= plan.budgets.max_drafts:
        raise ValueError("replacement plan draft cap has been reached")
    quota = next((item for item in plan.source_quotas if item.source_id == source_id), None)
    if quota is None:
        raise ValueError("replacement source has no preregistered quota")
    if existing_for_source >= quota.maximum_drafts:
        raise ValueError("replacement source draft quota has been reached")


def build_firewall_decision(
    *,
    policy: FirewallPolicyDocumentV2,
    scope: FirewallScopeDocument,
    risk: RiskAssessmentDocumentV2,
    validation: ReplacementValidationDocument,
    temporal: TemporalAssessmentDocumentV2 | None,
    reviewer: EntityRef,
) -> FirewallDecisionPayloadV2:
    """Compute a firewall state; no-match coverage alone never grants admission."""
    policy_ref = document_ref(policy)
    scope_ref = document_ref(scope)
    risk_ref = document_ref(risk)
    validation_ref = document_ref(validation)
    policy_payload = policy.payload
    scope_payload = scope.payload
    risk_payload = risk.payload
    validation_payload = validation.payload

    if (
        scope_payload.policy_ref != policy_ref
        or scope_payload.audit_ref != policy_payload.audit_plan_ref
    ):
        raise ValueError("firewall scope does not match its frozen policy and audit plan")
    if (
        scope_payload.task_ref != validation_payload.task_ref
        or scope_payload.task_ref != risk_payload.task_ref
    ):
        raise ValueError("firewall, risk and replacement validation must bind the same task")
    if validation_payload.exposure_scope_ref != scope_ref:
        raise ValueError("replacement validation must bind the firewall scope used for admission")
    if risk_payload.plan_ref != policy_payload.audit_plan_ref:
        raise ValueError("risk assessment does not belong to the firewall's frozen audit plan")
    if reviewer.entity_kind != "reviewer":
        raise ValueError("firewall decision requires an identified human reviewer")

    expected_keys = {unit.scope_key for unit in policy_payload.required_scope}
    actual_keys = {outcome.scope_key for outcome in scope_payload.outcomes}
    reasons: set[FirewallReason] = set()
    if actual_keys != expected_keys or any(
        outcome.state not in {"no_match", "match"} for outcome in scope_payload.outcomes
    ):
        reasons.add("scope_incomplete")

    matched = [outcome for outcome in scope_payload.outcomes if outcome.state == "match"]
    if any(outcome.relation in policy_payload.prohibited_relations for outcome in matched):
        reasons.add("prohibited_overlap")

    if validation_payload.review_state == "rejected":
        reasons.add("validity_rejected")
    elif validation_payload.review_state == "pending":
        reasons.add("validity_pending")
    if validation_payload.rights_state == "denied":
        reasons.add("rights_denied")
    elif validation_payload.rights_state == "pending":
        reasons.add("rights_pending")

    if risk_payload.scope_state != "complete" or risk_payload.calibration_state != "validated":
        reasons.add("risk_scope_incomplete")
    elif risk_payload.state == "high_observed":
        reasons.add(
            "high_risk_rejected"
            if policy_payload.high_risk_action == "reject"
            else "high_risk_review"
        )
    elif risk_payload.state not in {"low_observed", "medium_observed"}:
        reasons.add("risk_scope_incomplete")

    temporal_ref: AuditDocumentRef | None = None
    if temporal is not None:
        temporal_ref = document_ref(temporal)
    if policy_payload.require_temporal_review:
        if temporal is None:
            reasons.add("temporal_unresolved")
        elif temporal.payload.status != "post_declared_cutoff":
            if temporal.payload.status in {
                "pre_cutoff_exposure_detected",
                "interval_overlap",
            }:
                reasons.add("temporal_exposure_detected")
            else:
                reasons.add("temporal_unresolved")

    participant_ids = {
        validation_payload.author_ref.entity_id,
        validation_payload.reviewer_ref.entity_id,
        *(item.entity_id for item in validation_payload.checker_refs),
    }
    if reviewer.entity_id in participant_ids:
        reasons.add("reviewer_not_independent")

    hard_rejections = {
        "prohibited_overlap",
        "validity_rejected",
        "rights_denied",
        "high_risk_rejected",
    }
    result: Literal["admit", "review", "reject"] = (
        "reject" if reasons & hard_rejections else "review" if reasons else "admit"
    )
    exposure_refs = tuple(
        sorted(
            {
                ref
                for outcome in matched
                for ref in outcome.evidence_refs
                if ref.kind == "match_evidence"
            },
            key=lambda ref: str(ref.document_id),
        )
    )
    return FirewallDecisionPayloadV2(
        task_ref=scope_payload.task_ref,
        policy_ref=policy_ref,
        scope_ref=scope_ref,
        risk_assessment_ref=risk_ref,
        validity_ref=validation_ref,
        temporal_ref=temporal_ref,
        exposure_refs=exposure_refs,
        result=result,
        reasons=tuple(sorted(reasons)),
        reviewer=reviewer,
        resolution_reason=None,
    )
