from __future__ import annotations

from uuid import uuid4

import pytest
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    BenchmarkSnapshotDocument,
    BenchmarkSnapshotPayload,
    DerivedBenchmarkEntry,
    DerivedBenchmarkManifestPayload,
    DerivedDistributionCount,
    DocumentMetadata,
    EntityRef,
    FirewallPolicyDocumentV2,
    FirewallPolicyPayloadV2,
    FirewallScopeDocument,
    FirewallScopeOutcome,
    FirewallScopePayload,
    FirewallScopeUnit,
    ImmutableArtifactRef,
    ReplacementBudgets,
    ReplacementPlanPayloadV2,
    ReplacementSourceQuota,
    ReplacementValidationDocument,
    ReplacementValidationPayload,
    RiskAssessmentDocumentV2,
    RiskComponentCoverage,
    RiskPolicyDocumentV2,
    RiskSignalObservation,
    audit_document_digest,
)
from polycodebench_core.benchmark_firewall import (
    build_firewall_decision,
    document_ref,
    validate_derived_family_split_consistency,
    validate_replacement_draft_budget,
)
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_services.benchmark_firewall import validate_replacement_plan_reviewers
from polycodebench_services.risk_assessment import (
    assess_observed_risk,
    build_observed_risk_v1_policy,
)
from pydantic import ValidationError

_NOW = "2026-10-08T12:00:00Z"
_DIGEST = "sha256:" + "a" * 64


def _ref(kind: str) -> AuditDocumentRef:
    return AuditDocumentRef(
        document_id=uuid4(),
        digest=_DIGEST,
        kind=kind,  # type: ignore[arg-type]
    )


def _entity(kind: str) -> EntityRef:
    return EntityRef(entity_id=uuid4(), entity_kind=kind)


def _doc_metadata(actor: str = "test") -> DocumentMetadata:
    return DocumentMetadata(
        created_at=_NOW,
        timestamp_precision="second",
        actor=actor,
        trace_id=None,
        row_version=0,
    )


def _risk_document(
    task_ref: EntityRef,
    plan_ref: AuditDocumentRef,
    *,
    high_risk: bool = False,
) -> RiskAssessmentDocumentV2:
    coverage_ref = _ref("coverage_manifest")
    audit_ref = _ref("audit_plan")
    corpus_ref = _ref("corpus_snapshot")
    policy = build_observed_risk_v1_policy(
        calibration_state="validated",
        calibration_evidence_refs=(_ref("audit_attestation"),),
    )
    policy_document = RiskPolicyDocumentV2(
        id=uuid4(),
        kind="risk_policy",
        schema_version=2,
        payload=policy,
        metadata=_doc_metadata("risk-policy"),
    )
    scored_signals = {
        "web_exposure",
        "duplication",
        "corpus_overlap_evidence",
        "synthetic_similarity",
        "leakage_history",
    }
    signal_names = (
        "publication_age",
        "web_exposure",
        "duplication",
        "corpus_overlap_evidence",
        "popularity",
        "synthetic_similarity",
        "model_familiarity",
        "leakage_history",
    )
    observations: list[RiskSignalObservation] = []
    for name in signal_names:
        context = name in {"publication_age", "popularity"}
        diagnostic = name == "model_familiarity"
        scored = name in scored_signals
        positive = high_risk and name in {"duplication", "corpus_overlap_evidence"}
        observations.append(
            RiskSignalObservation(
                signal=name,  # type: ignore[arg-type]
                state="observed",
                evidence_state="accepted" if positive else "none",
                basis=(
                    "context_only"
                    if context
                    else "behavioral_diagnostic_only"
                    if diagnostic
                    else "exact_or_semantic_duplicate"
                    if name == "duplication" and positive
                    else "verified_corpus_overlap"
                    if name == "corpus_overlap_evidence" and positive
                    else "completed_scope_no_match"
                ),
                normalized_value=(
                    "0.800000"
                    if diagnostic
                    else "1.000000"
                    if positive
                    else "0.000000"
                    if scored
                    else None
                ),
                raw_value="downloads" if name == "popularity" else None,
                raw_interval=(
                    ("147.000000", "147.000000")
                    if name == "publication_age"
                    else ("42.000000", "42.000000")
                    if name == "popularity"
                    else None
                ),
                raw_unit="days"
                if name == "publication_age"
                else "count"
                if name == "popularity"
                else None,
                evidence_refs=(coverage_ref,),
                author_ref=_entity("reviewer") if positive else None,
                review_ref=_ref("match_evidence") if positive else None,
                reviewer_ref=_entity("reviewer") if positive else None,
                configuration_ref=audit_ref,
                observed_at=_NOW,
                source_lineage_refs=(corpus_ref,) if scored else (),
                verification_state="verified" if scored else "not_applicable",
                rights_state="approved" if scored else "not_applicable",
                unknown_reason=None,
            )
        )
    coverage = tuple(
        RiskComponentCoverage(
            component=component,  # type: ignore[arg-type]
            planned_units=1,
            complete_units=1,
            failed_units=0,
            blocked_units=0,
            truncated_units=0,
            pending_reviews=0,
            scope_refs=(coverage_ref,),
        )
        for component in ("M", "C", "E", "L")
    )
    payload = assess_observed_risk(
        plan_ref=plan_ref,
        task_ref=task_ref,
        policy_document=policy_document,
        signal_observations=observations,
        component_coverage=coverage,
    )
    return RiskAssessmentDocumentV2(
        id=uuid4(),
        kind="risk_assessment",
        schema_version=2,
        payload=payload,
        metadata=_doc_metadata("risk-assessor"),
    )


def _validation(
    task_ref: EntityRef,
    *,
    review_state: str = "accepted",
    rights_state: str = "approved",
    exposure_scope_ref: AuditDocumentRef | None = None,
) -> ReplacementValidationDocument:
    pending = review_state == "pending"
    rejected = rights_state == "denied"
    payload = ReplacementValidationPayload(
        plan_ref=_ref("replacement_plan"),
        task_ref=task_ref,
        source_family_id="family-a",
        source_id="source-a",
        draft_index=1,
        family_relation="source_family",
        parent_task_refs=(_entity("task_version"),),
        author_ref=_entity("reviewer"),
        checker_refs=(_entity("reviewer"),),
        reviewer_ref=_entity("reviewer"),
        correctness_state="pending" if pending else "verified",
        rights_state=rights_state,  # type: ignore[arg-type]
        fidelity_state="valid",
        oracle_state="independently_verified",
        difficulty_state="calibrated",
        lineage_state="verified_source_family",
        template_review_state="not_applicable",
        exposure_state="within_policy",
        exposure_scope_ref=exposure_scope_ref or _ref("firewall_scope"),
        test_artifact_ref=ImmutableArtifactRef(
            artifact_id=uuid4(), digest=_DIGEST, visibility="private", media_type="application/json"
        ),
        oracle_artifact_ref=ImmutableArtifactRef(
            artifact_id=uuid4(), digest=_DIGEST, visibility="private", media_type="application/json"
        ),
        difficulty_policy_ref=ImmutableArtifactRef(
            artifact_id=uuid4(), digest=_DIGEST, visibility="private", media_type="application/json"
        ),
        evidence_refs=(_ref("corpus_snapshot"),),
        review_state=("rejected" if rejected else review_state),  # type: ignore[arg-type]
        reason_codes=("rights_denied",) if rejected else ("evidence_pending",) if pending else (),
    )
    return ReplacementValidationDocument(
        id=uuid4(),
        kind="replacement_validation",
        schema_version=1,
        payload=payload,
        metadata=_doc_metadata("replacement-review"),
    )


def _firewall_inputs(
    *,
    scope_state: str = "no_match",
    relation: str | None = None,
    validation_state: str = "accepted",
    rights_state: str = "approved",
) -> tuple[
    FirewallPolicyDocumentV2,
    FirewallScopeDocument,
    RiskAssessmentDocumentV2,
    ReplacementValidationDocument,
    EntityRef,
]:
    task_ref = _entity("task_version")
    source_ref = _ref("corpus_snapshot")
    component_ref = EntityRef(entity_id=uuid4(), entity_kind="audit_component", digest=_DIGEST)
    plan_ref = _ref("audit_plan")
    snapshot_ref = _ref("benchmark_snapshot")
    policy_payload = FirewallPolicyPayloadV2(
        benchmark_ref=snapshot_ref,
        audit_plan_ref=plan_ref,
        policy_version="firewall-v1",
        required_scope=(
            FirewallScopeUnit(
                scope_key="source-a:component-a",
                source_ref=source_ref,
                component_ref=component_ref,
                modality="code",
            ),
        ),
        prohibited_relations=("exact_component", "near_exact_component", "semantic_duplicate"),
        require_temporal_review=False,
        high_risk_action="review",
    )
    policy = FirewallPolicyDocumentV2(
        id=uuid4(),
        kind="firewall_policy",
        schema_version=2,
        payload=policy_payload,
        metadata=_doc_metadata("policy-author"),
    )
    policy_ref = AuditDocumentRef(
        document_id=policy.id,
        digest=audit_document_digest(policy),
        kind=policy.kind,
    )
    outcome = FirewallScopeOutcome(
        scope_key="source-a:component-a",
        state=scope_state,  # type: ignore[arg-type]
        relation=(
            relation  # type: ignore[arg-type]
            if relation is not None
            else "no_substantive_match"
            if scope_state == "no_match"
            else None
        ),
        evidence_refs=(
            _ref("coverage_manifest")
            if scope_state == "no_match"
            else _ref("match_evidence")
            if scope_state == "match"
            else _ref("coverage_manifest"),
        ),
    )
    scope = FirewallScopeDocument(
        id=uuid4(),
        kind="firewall_scope",
        schema_version=1,
        payload=FirewallScopePayload(
            task_ref=task_ref,
            policy_ref=policy_ref,
            audit_ref=plan_ref,
            outcomes=(outcome,),
        ),
        metadata=_doc_metadata("scope-writer"),
    )
    validation = _validation(
        task_ref,
        review_state=validation_state,
        rights_state=rights_state,
        exposure_scope_ref=document_ref(scope),
    )
    risk = _risk_document(task_ref, plan_ref)
    return policy, scope, risk, validation, _entity("reviewer")


def test_complete_zero_hit_scope_does_not_bypass_pending_validity_review() -> None:
    policy, scope, risk, validation, reviewer = _firewall_inputs(validation_state="pending")
    decision = build_firewall_decision(
        policy=policy,
        scope=scope,
        risk=risk,
        validation=validation,
        temporal=None,
        reviewer=reviewer,
    )
    assert decision.result == "review"
    assert decision.reasons == ("validity_pending",)


def test_incomplete_scope_and_denied_rights_fail_closed() -> None:
    policy, scope, risk, validation, reviewer = _firewall_inputs(scope_state="truncated")
    decision = build_firewall_decision(
        policy=policy,
        scope=scope,
        risk=risk,
        validation=validation,
        temporal=None,
        reviewer=reviewer,
    )
    assert decision.result == "review"
    assert "scope_incomplete" in decision.reasons

    policy, scope, risk, validation, reviewer = _firewall_inputs(rights_state="denied")
    decision = build_firewall_decision(
        policy=policy,
        scope=scope,
        risk=risk,
        validation=validation,
        temporal=None,
        reviewer=reviewer,
    )
    assert decision.result == "reject"
    assert "rights_denied" in decision.reasons


def test_confirmed_prohibited_overlap_rejects_even_with_other_gates_passed() -> None:
    policy, scope, risk, validation, reviewer = _firewall_inputs(
        scope_state="match", relation="exact_component"
    )
    decision = build_firewall_decision(
        policy=policy,
        scope=scope,
        risk=risk,
        validation=validation,
        temporal=None,
        reviewer=reviewer,
    )
    assert decision.result == "reject"
    assert decision.reasons == ("prohibited_overlap",)


def test_firewall_decision_requires_the_validated_exposure_scope() -> None:
    policy, scope, risk, validation, reviewer = _firewall_inputs()
    mismatched_validation = ReplacementValidationDocument(
        id=uuid4(),
        kind="replacement_validation",
        schema_version=1,
        payload=validation.payload.model_copy(
            update={"exposure_scope_ref": _ref("firewall_scope")}
        ),
        metadata=_doc_metadata("replacement-review"),
    )
    with pytest.raises(ValueError, match="bind the firewall scope"):
        build_firewall_decision(
            policy=policy,
            scope=scope,
            risk=risk,
            validation=mismatched_validation,
            temporal=None,
            reviewer=reviewer,
        )


def test_high_observed_risk_stays_in_review_under_the_frozen_policy() -> None:
    policy, scope, _, validation, reviewer = _firewall_inputs()
    high_risk = _risk_document(
        scope.payload.task_ref, policy.payload.audit_plan_ref, high_risk=True
    )
    decision = build_firewall_decision(
        policy=policy,
        scope=scope,
        risk=high_risk,
        validation=validation,
        temporal=None,
        reviewer=reviewer,
    )
    assert decision.result == "review"
    assert "high_risk_review" in decision.reasons


def test_required_temporal_review_cannot_be_omitted() -> None:
    policy, scope, risk, validation, reviewer = _firewall_inputs()
    temporal_policy = FirewallPolicyDocumentV2(
        id=uuid4(),
        kind="firewall_policy",
        schema_version=2,
        payload=policy.payload.model_copy(update={"require_temporal_review": True}),
        metadata=_doc_metadata("temporal-policy"),
    )
    temporal_scope = FirewallScopeDocument(
        id=uuid4(),
        kind="firewall_scope",
        schema_version=1,
        payload=scope.payload.model_copy(update={"policy_ref": document_ref(temporal_policy)}),
        metadata=_doc_metadata("temporal-scope"),
    )
    temporal_validation = ReplacementValidationDocument(
        id=uuid4(),
        kind="replacement_validation",
        schema_version=1,
        payload=validation.payload.model_copy(
            update={"exposure_scope_ref": document_ref(temporal_scope)}
        ),
        metadata=_doc_metadata("temporal-validation"),
    )
    decision = build_firewall_decision(
        policy=temporal_policy,
        scope=temporal_scope,
        risk=risk,
        validation=temporal_validation,
        temporal=None,
        reviewer=reviewer,
    )
    assert decision.result == "review"
    assert "temporal_unresolved" in decision.reasons


def test_independent_prospective_lineage_requires_template_audit() -> None:
    task_ref = _entity("task_version")
    base = _validation(task_ref).payload.model_dump(mode="python")
    base.update(
        family_relation="independent_prospective",
        lineage_state="verified_independent",
        template_review_state="pending",
        review_state="pending",
        reason_codes=("evidence_pending",),
    )
    with pytest.raises(ValidationError, match="template/lineage review"):
        ReplacementValidationPayload.model_validate(base)


def test_author_checker_and_final_reviewer_must_be_independent() -> None:
    author, checker, reviewer = _entity("reviewer"), _entity("reviewer"), _entity("reviewer")
    validate_replacement_plan_reviewers(
        plan_authors=(author,),
        plan_checkers=(checker,),
        author=author,
        checker=checker,
        reviewer=reviewer,
    )
    with pytest.raises(ValueError, match="must be independent"):
        validate_replacement_plan_reviewers(
            plan_authors=(author,),
            plan_checkers=(checker,),
            author=author,
            checker=checker,
            reviewer=author,
        )


def test_replacement_draft_budgets_enforce_total_and_source_quotas() -> None:
    plan = ReplacementPlanPayloadV2(
        source_metadata_ref=_ref("replacement_source_metadata"),
        mode="source_family_transformation",
        seed_ancestry_refs=(_ref("benchmark_snapshot"),),
        competency_brief=ImmutableArtifactRef(
            artifact_id=uuid4(), digest=_DIGEST, visibility="private", media_type="text/plain"
        ),
        authors=(_entity("reviewer"),),
        checkers=(_entity("reviewer"),),
        generator_config_ref=ImmutableArtifactRef(
            artifact_id=uuid4(), digest=_DIGEST, visibility="private", media_type="application/json"
        ),
        checker_config_ref=ImmutableArtifactRef(
            artifact_id=uuid4(),
            digest="sha256:" + "b" * 64,
            visibility="private",
            media_type="application/json",
        ),
        difficulty_policy_ref=ImmutableArtifactRef(
            artifact_id=uuid4(), digest=_DIGEST, visibility="private", media_type="application/json"
        ),
        exposure_policy_ref=_ref("audit_plan"),
        source_quotas=(ReplacementSourceQuota(source_id="source-a", maximum_drafts=2),),
        budgets=ReplacementBudgets(
            max_drafts=2, max_rounds=1, max_cost_micro_usd=100, max_wall_seconds=60
        ),
        preregistered_at=_NOW,
    )
    validate_replacement_draft_budget(
        plan=plan, source_id="source-a", existing_total=1, existing_for_source=1
    )
    with pytest.raises(ValueError, match="source draft quota"):
        validate_replacement_draft_budget(
            plan=plan, source_id="source-a", existing_total=1, existing_for_source=2
        )
    with pytest.raises(ValueError, match="plan draft cap"):
        validate_replacement_draft_budget(
            plan=plan, source_id="source-a", existing_total=2, existing_for_source=1
        )
    with pytest.raises(ValueError, match="no preregistered quota"):
        validate_replacement_draft_budget(
            plan=plan, source_id="source-b", existing_total=0, existing_for_source=0
        )


def test_derived_manifest_has_a_separate_membership_digest_and_metric_label() -> None:
    derived_item = _entity("derived_benchmark_item")
    derived_digest = sha256_bytes(canonical_json_bytes([str(derived_item.entity_id)]))
    entry = DerivedBenchmarkEntry(
        original_item_ref=_entity("benchmark_item"),
        original_task_ref=None,
        derived_item_ref=derived_item,
        replacement_task_ref=None,
        disposition="retained",
        source_family_id="family-a",
        derived_source_family_id="family-a",
        split="test",
        original_competency="algorithms",
        derived_competency="algorithms",
        original_difficulty="medium",
        derived_difficulty="medium",
        validation_ref=None,
        oracle_mapping_ref=None,
        reason=None,
    )
    manifest = DerivedBenchmarkManifestPayload(
        official_snapshot_ref=_ref("benchmark_snapshot"),
        official_membership_digest=_DIGEST,
        derived_version="derived-v1",
        entries=(entry,),
        derived_membership_digest=derived_digest,
        change_summary_ref=ImmutableArtifactRef(
            artifact_id=uuid4(), digest=_DIGEST, visibility="private", media_type="application/json"
        ),
        sampling_policy_ref=ImmutableArtifactRef(
            artifact_id=uuid4(),
            digest="sha256:" + "b" * 64,
            visibility="private",
            media_type="application/json",
        ),
        distribution=(
            DerivedDistributionCount(
                dimension="source_family", label="family-a", official_items=1, derived_items=1
            ),
            DerivedDistributionCount(
                dimension="split", label="test", official_items=1, derived_items=1
            ),
            DerivedDistributionCount(
                dimension="competency", label="algorithms", official_items=1, derived_items=1
            ),
            DerivedDistributionCount(
                dimension="difficulty", label="medium", official_items=1, derived_items=1
            ),
        ),
        official_metric_label="official-score",
        derived_metric_label="derived-score",
        comparability="separate_labels_no_automatic_comparison",
        created_at=_NOW,
    )
    assert manifest.derived_membership_digest == derived_digest
    with pytest.raises(ValidationError, match="distribution counts"):
        DerivedBenchmarkManifestPayload.model_validate(
            manifest.model_dump(mode="python")
            | {
                "distribution": (
                    manifest.distribution[0].model_copy(update={"derived_items": 0}),
                    *manifest.distribution[1:],
                )
            }
        )
    with pytest.raises(ValueError, match="cannot cross official splits"):
        validate_derived_family_split_consistency(
            candidate=manifest.model_copy(
                update={"entries": (entry.model_copy(update={"split": "train"}),)}
            ),
            related_manifests=(manifest,),
        )
    with pytest.raises(ValidationError, match="distinct labels"):
        DerivedBenchmarkManifestPayload.model_validate(
            manifest.model_dump(mode="python") | {"derived_metric_label": "official-score"}
        )


def test_registered_official_snapshot_remains_a_distinct_immutable_source() -> None:
    snapshot = BenchmarkSnapshotDocument(
        id=uuid4(),
        kind="benchmark_snapshot",
        schema_version=1,
        payload=BenchmarkSnapshotPayload(
            registry_ref=_entity("benchmark_registry"),
            version="v1",
            split="test",
            membership=(_entity("benchmark_item"),),
            components=(),
            upstream_rights=(),
            importer_refs=(),
        ),
        metadata=_doc_metadata(),
    )
    assert snapshot.payload.version == "v1"
