from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    DocumentMetadata,
    EntityRef,
    ImmutableArtifactRef,
    MatchEvidenceDocumentV2,
    MatchEvidencePayloadV2,
    MatchSpanV2,
    RiskComponentCoverage,
    RiskPolicyDocumentV2,
    RiskSignalObservation,
    audit_document_digest,
)
from polycodebench_core.benchmark_imports import (
    BenchmarkImportPlan,
    BenchmarkImportResult,
    freeze_sample,
)
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.match_verification import MatchReviewLedger
from polycodebench_core.retrieval import (
    RetrievalCandidateHit,
    RetrievalPlan,
    RetrievalPlanRequest,
)
from polycodebench_services.audit_temporal import build_temporal_assessment_payload
from polycodebench_services.benchmark_audit_catalog import load_audit_catalog
from polycodebench_services.benchmark_health import (
    HealthTaskObservation,
    aggregate_benchmark_health,
)
from polycodebench_services.benchmark_importers import parse_benchmark_snapshot
from polycodebench_services.match_verification import (
    initial_match_review_ledger,
    match_relation_rubric_digest,
    verify_match_evidence_content,
)
from polycodebench_services.retrieval import (
    build_retrieval_plan,
    retrieval_plan_digest,
    select_retrieval_candidates,
)
from polycodebench_services.risk_assessment import (
    assess_observed_risk,
    build_observed_risk_v1_policy,
)

ROOT = Path(__file__).parents[1]
_NOW = "2026-10-09T12:00:00Z"
_DIGEST = "sha256:" + "a" * 64


def _uuid(number: int) -> UUID:
    return UUID(f"30000000-0000-4000-8000-{number:012d}")


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ref(kind: str, number: int, digest: str = _DIGEST) -> AuditDocumentRef:
    return AuditDocumentRef(document_id=_uuid(number), digest=digest, kind=kind)  # type: ignore[arg-type]


def _entity(kind: str, number: int, digest: str = _DIGEST) -> EntityRef:
    return EntityRef(entity_id=_uuid(number), entity_kind=kind, digest=digest)


def _import_fixture() -> BenchmarkImportResult:
    source = b"".join(
        (
            json.dumps(
                {"question": f"Synthetic arithmetic question {index}?", "answer": "Work.\n#### 2"},
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        for index in range(100)
    )
    selected_ids, membership_digest = freeze_sample(
        benchmark_slug="gsm8k",
        revision="3101c7d5072418e28b9008a6636bde82a006892c",
        split="test",
        variant="official",
        seed="7",
        eligible_item_ids=tuple(f"test/{index}" for index in range(100)),
    )
    parser_config = {"format": "jsonl"}
    plan = BenchmarkImportPlan(
        benchmark_slug="gsm8k",
        source_uri="https://github.com/openai/grade-school-math",
        revision="3101c7d5072418e28b9008a6636bde82a006892c",
        split="test",
        variant="official",
        source_member="grade_school_math/data/test.jsonl",
        source_digest=_sha(source),
        source_visibility="public",
        storage_visibility="private",
        rights_state="approved",
        rights_evidence_digest=_DIGEST,
        importer_version="benchmark-import-v1",
        parser_config=tuple(sorted(parser_config.items())),
        parser_config_digest=canonical_digest(parser_config),
        sample_seed="7",
        selected_ids=selected_ids,
        membership_digest=membership_digest,
    )
    # This fixture exercises parsing; it is not source or rights approval.
    return parse_benchmark_snapshot(plan, source)


def _provided_fixture_retrieval_plan(
    task_ref: EntityRef, component_id: UUID, component_digest: str
) -> RetrievalPlan:
    bundle = load_audit_catalog(ROOT / "config" / "benchmark-audit")
    component = EntityRef(
        entity_id=component_id,
        entity_kind="audit_component",
        digest=component_digest,
    )
    request = RetrievalPlanRequest(
        audit_plan_ref=_ref("audit_plan", 100),
        task_ref=task_ref,
        component_refs=(component,),
        source_groups=("github",),
        source_pins=(),
        seed="7",
    )
    blocked = build_retrieval_plan(bundle, request)
    values = blocked.model_dump(mode="python")
    values["source_pins"][0].update(
        {
            "source_revision": "synthetic-fixture-only",
            "snapshot_ref": _ref("corpus_snapshot", 103).model_dump(mode="python"),
            "rights_ref": _ref("corpus_snapshot", 104).model_dump(mode="python"),
            "privacy_scope_digest": _DIGEST,
            "index_pins": (
                {"stage": "exact_normalized", "index_digest": _DIGEST},
                {"stage": "lexical_code", "index_digest": _DIGEST},
            ),
            "state": "provided",
            "blocker_codes": (),
        }
    )
    for unit in values["query_units"]:
        if unit["stage"] in {"exact_normalized", "lexical_code"}:
            unit["state"] = "planned"
            unit["reason_codes"] = ()
    values["state"] = "planned"
    return RetrievalPlan.model_validate(values)


def _match_document(
    *,
    item_id: str,
    question: bytes,
    plan: RetrievalPlan,
    hit: RetrievalCandidateHit,
    result_digest: str,
) -> MatchEvidenceDocumentV2:
    component = plan.component_refs[0]
    source_artifact = ImmutableArtifactRef(
        artifact_id=_uuid(105),
        digest=_sha(question),
        visibility="restricted",
        media_type="text/plain",
    )
    target_benchmark = _ref("benchmark_snapshot", 106)
    source_pin = plan.source_pins[0]
    assert source_pin.snapshot_ref is not None
    assert source_pin.rights_ref is not None
    payload = MatchEvidencePayloadV2(
        task_ref=plan.task_ref,
        target_benchmark_ref=target_benchmark,
        retrieval_plan_ref=_ref("audit_plan", 100, retrieval_plan_digest(plan)),
        retrieval_result_digest=result_digest,
        candidate_hit_digest=canonical_digest(hit.model_dump(mode="json")),
        source_snapshot_ref=source_pin.snapshot_ref,
        source_document_ref=source_artifact,
        source_revision=hit.source_revision,
        source_task_ref=_entity("task_version", 107),
        source_benchmark_ref=_ref("benchmark_snapshot", 108),
        source_lineage="unknown",
        component_refs=(component,),
        matching_spans=(
            MatchSpanV2(
                component_ref=component,
                component_artifact_ref=source_artifact,
                source_start_byte=0,
                source_end_byte=len(question),
                source_span_digest=_sha(question),
                field="question",
                content_class="substantive",
                comparison="exact_bytes",
            ),
        ),
        relation="exact_component",
        answer_relationship="not_applicable",
        source_date_state="unknown",
        source_date_evidence=(),
        rights_refs=(source_pin.rights_ref,),
        normalizer_version="text-nfc-lf-preserve-v1",
        parser_version=None,
        rubric_digest=match_relation_rubric_digest(),
        review_state="proposed",
        author_subject=f"synthetic-author:{item_id}",
        reviewer_subjects=(),
        review_record_ref=None,
        counter_evidence_refs=(),
    )
    return MatchEvidenceDocumentV2(
        id=_uuid(109),
        kind="match_evidence",
        schema_version=2,
        payload=payload,
        metadata=DocumentMetadata(
            created_at=_NOW,
            timestamp_precision="second",
            actor="synthetic-fixture",
            trace_id=None,
            row_version=0,
        ),
    )


def _unknown_signal(signal: str) -> RiskSignalObservation:
    return RiskSignalObservation(
        signal=signal,  # type: ignore[arg-type]
        state="unknown",
        evidence_state="none",
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
        unknown_reason="source_or_review_evidence_unavailable",
    )


def test_local_lifecycle_preserves_pending_and_blocked_evidence_without_green_claims() -> None:
    imported = _import_fixture()
    assert imported.state == "complete"
    item = imported.items[0]
    assert item.state == "imported"
    question_component = next(
        component for component in item.components if component.component_key == "question"
    )

    from polycodebench_services.task_fingerprinting import fingerprint_component

    fingerprint = fingerprint_component(
        component_id=_uuid(110),
        component_digest=question_component.digest,
        content=question_component.content,
        media_type=question_component.media_type,
    )
    assert fingerprint.exact_state.state == "available"
    assert fingerprint.exact_digest == question_component.digest

    assert item.source_record_digest is not None
    task_ref = EntityRef(
        entity_id=uuid5(NAMESPACE_URL, f"synthetic-gsm8k:{item.item_id}"),
        entity_kind="task_version",
        digest=item.source_record_digest,
    )
    plan = _provided_fixture_retrieval_plan(
        task_ref,
        fingerprint.component_id,
        question_component.digest,
    )
    unit = next(unit for unit in plan.query_units if unit.stage == "exact_normalized")
    hit = RetrievalCandidateHit(
        task_ref=plan.task_ref,
        component_ref=plan.component_refs[0],
        query_unit_digest=unit.unit_digest,
        source_group="github",
        source_revision="synthetic-fixture-only",
        source_document_digest=question_component.digest,
        candidate_component_digest=question_component.digest,
        stage="exact_normalized",
        rank=1,
    )
    selection = select_retrieval_candidates(plan, (hit,))
    assert len(selection.selected_candidates) == 1
    assert selection.observed_hits == 1

    evidence = _match_document(
        item_id=item.item_id,
        question=question_component.content,
        plan=plan,
        hit=hit,
        result_digest=selection.result_digest,
    )
    verified = verify_match_evidence_content(
        evidence,
        plan=plan,
        selection=selection,
        candidate_hit=hit,
        source_bytes=question_component.content,
        component_bytes={_uuid(105): question_component.content},
    )
    ledger = initial_match_review_ledger(evidence)
    assert verified.state == "verified"
    assert verified.eligibility == "human_review_required"
    assert verified.accepted_evidence is False
    assert verified.source_trust == verified.rights_trust == "unverified"
    assert isinstance(ledger, MatchReviewLedger)
    assert ledger.state == "proposed" and ledger.opinions == ()

    policy_document = RiskPolicyDocumentV2(
        id=_uuid(111),
        kind="risk_policy",
        schema_version=2,
        payload=build_observed_risk_v1_policy(),
        metadata=DocumentMetadata(
            created_at=_NOW,
            timestamp_precision="second",
            actor="synthetic-fixture",
            trace_id=None,
            row_version=0,
        ),
    )
    candidate_signal = RiskSignalObservation(
        signal="duplication",
        state="observed",
        evidence_state="candidate_only",
        basis="exact_or_semantic_duplicate",
        normalized_value="1.000000",
        raw_value=None,
        raw_interval=None,
        raw_unit=None,
        evidence_refs=(
            AuditDocumentRef(
                document_id=evidence.id,
                digest=audit_document_digest(evidence),
                kind="match_evidence",
            ),
        ),
        author_ref=None,
        review_ref=None,
        reviewer_ref=None,
        configuration_ref=_ref("task_fingerprint", 110),
        observed_at=_NOW,
        source_lineage_refs=(_ref("corpus_snapshot", 103),),
        verification_state="unverified",
        rights_state="unknown",
        unknown_reason=None,
    )
    signals = [
        candidate_signal if name == "duplication" else _unknown_signal(name)
        for name in (
            "publication_age",
            "web_exposure",
            "duplication",
            "corpus_overlap_evidence",
            "popularity",
            "synthetic_similarity",
            "model_familiarity",
            "leakage_history",
        )
    ]
    coverage_ref = _ref("coverage_manifest", 112)
    coverage = tuple(
        RiskComponentCoverage(
            component=key,  # type: ignore[arg-type]
            planned_units=1,
            complete_units=1 if key == "M" else 0,
            failed_units=0,
            blocked_units=0 if key == "M" else 1,
            truncated_units=0,
            pending_reviews=1 if key == "M" else 0,
            scope_refs=(coverage_ref,),
        )
        for key in ("M", "C", "E", "L")
    )
    risk = assess_observed_risk(
        plan_ref=plan.audit_plan_ref,
        task_ref=plan.task_ref,
        policy_document=policy_document,
        signal_observations=signals,
        component_coverage=coverage,
    )
    assert risk.state == "insufficient_evidence"
    assert risk.scope_state == "partial"
    assert risk.observed_index.value is None
    assert risk.accepted_evidence == ()

    temporal = build_temporal_assessment_payload(
        artifact_ref=ImmutableArtifactRef(
            artifact_id=_uuid(113),
            digest=question_component.digest,
            visibility="private",
            media_type="text/plain",
        ),
        chronology=(),
        model_context=None,
    )
    assert temporal.status == "mutable_model_context"
    assert temporal.claim_qualifier_codes == ("insufficient_evidence",)

    health = aggregate_benchmark_health(
        (
            HealthTaskObservation(
                task_ref=plan.task_ref,
                family_ref=None,
                assessment_status="partial",
                risk_state="insufficient_evidence",
                observed_risk_index=None,
                exact_duplicate=None,
                semantic_duplicate=None,
                pre_cutoff_exposure=None,
                provenance_complete=None,
                fresh=None,
                planned_units=3,
                completed_units=2,
                failed_units=0,
                truncated_units=0,
                blocked_units=1,
                unknown_units=0,
            ),
        ),
        context_ref=None,
    )
    assert health.assessment_states.partial == 1
    assert health.risk_tiers.insufficient == 1
    assert health.risk_tiers.low == health.risk_tiers.medium == health.risk_tiers.high == 0
    assert health.mean_observed_risk.value is None
