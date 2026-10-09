"""Content-bound match verification and append-only review lifecycle helpers."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID, uuid4

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    DocumentMetadata,
    ImmutableArtifactRef,
    MatchEvidenceDocumentV2,
    MatchEvidencePayloadV2,
    audit_document_digest,
)
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.match_verification import (
    MatchContentEligibility,
    MatchContentState,
    MatchContentVerification,
    MatchCorrectionRecord,
    MatchRelationRubric,
    adjudicate_match_reviews,
    append_match_review,
    initial_match_review_ledger,
)
from polycodebench_core.models import Digest
from polycodebench_core.retrieval import (
    CandidateSelectionResult,
    RetrievalCandidateHit,
    RetrievalPlan,
)

from polycodebench_services.retrieval import retrieval_plan_digest

MAX_SOURCE_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_COMPONENT_EVIDENCE_BYTES = 16 * 1024 * 1024
_NORMALIZATION_VERSION = "text-nfc-lf-preserve-v1"

__all__ = [
    "adjudicate_match_reviews",
    "append_match_review",
    "build_match_correction_record",
    "build_match_successor",
    "initial_match_review_ledger",
    "match_relation_rubric_digest",
    "verify_match_evidence_content",
]


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _document_ref(document: MatchEvidenceDocumentV2) -> AuditDocumentRef:
    return AuditDocumentRef(
        document_id=document.id,
        digest=audit_document_digest(document),
        kind="match_evidence",
    )


def _normalized_text(value: bytes) -> bytes | None:
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return normalized.encode("utf-8", errors="strict")


def _verification(
    document: MatchEvidenceDocumentV2,
    *,
    state: MatchContentState,
    eligibility: MatchContentEligibility,
    source_digest: Digest | None,
    span_digests: tuple[Digest, ...] = (),
    reason_codes: tuple[str, ...],
) -> MatchContentVerification:
    return MatchContentVerification(
        candidate_ref=_document_ref(document),
        state=state,
        eligibility=eligibility,
        source_digest=source_digest,
        verified_span_digests=span_digests,
        reason_codes=reason_codes,
    )


def verify_match_evidence_content(
    document: MatchEvidenceDocumentV2,
    *,
    plan: RetrievalPlan,
    selection: CandidateSelectionResult,
    candidate_hit: RetrievalCandidateHit,
    source_bytes: bytes,
    component_bytes: Mapping[UUID, bytes],
) -> MatchContentVerification:
    """Verify supplied bytes and offsets; do not assert source authorization or rights."""
    payload = document.payload
    if payload.rubric_digest != match_relation_rubric_digest():
        return _verification(
            document,
            state="unsupported",
            eligibility="human_review_required",
            source_digest=None,
            reason_codes=("relation_rubric_digest_not_registered",),
        )
    plan_digest = retrieval_plan_digest(plan)
    source_pin = next(
        (pin for pin in plan.source_pins if pin.source_group == candidate_hit.source_group), None
    )
    matching_component_digests = {
        span.component_artifact_ref.digest for span in payload.matching_spans
    }
    if (
        payload.retrieval_plan_ref.digest != plan_digest
        or selection.plan_digest != plan_digest
        or selection.result_digest != payload.retrieval_result_digest
        or canonical_digest(candidate_hit.model_dump(mode="json")) != payload.candidate_hit_digest
        or candidate_hit not in selection.selected_candidates
        or candidate_hit.task_ref != payload.task_ref
        or candidate_hit.component_ref not in payload.component_refs
        or candidate_hit.source_revision != payload.source_revision
        or candidate_hit.source_document_digest != payload.source_document_ref.digest
        or candidate_hit.candidate_component_digest not in matching_component_digests
        or source_pin is None
        or source_pin.snapshot_ref != payload.source_snapshot_ref
        or source_pin.source_revision != payload.source_revision
        or source_pin.rights_ref not in payload.rights_refs
    ):
        return _verification(
            document,
            state="scope_violation",
            eligibility="human_review_required",
            source_digest=None,
            reason_codes=("retrieval_candidate_scope_binding_failed",),
        )
    if type(source_bytes) is not bytes or len(source_bytes) > MAX_SOURCE_EVIDENCE_BYTES:
        return _verification(
            document,
            state="scope_violation",
            eligibility="human_review_required",
            source_digest=None,
            reason_codes=("source_artifact_bytes_invalid_or_over_limit",),
        )
    if _digest(source_bytes) != payload.source_document_ref.digest:
        return _verification(
            document,
            state="mismatch",
            eligibility="human_review_required",
            source_digest=_digest(source_bytes),
            reason_codes=("source_artifact_digest_mismatch",),
        )

    referenced_artifacts = {
        span.component_artifact_ref.artifact_id for span in payload.matching_spans
    }
    if set(component_bytes) != referenced_artifacts:
        missing = referenced_artifacts - set(component_bytes)
        return _verification(
            document,
            state="missing_artifact" if missing else "scope_violation",
            eligibility="human_review_required",
            source_digest=_digest(source_bytes),
            reason_codes=(
                "component_artifact_missing" if missing else "unreferenced_component_bytes",
            ),
        )

    verified_spans: list[str] = []
    for span in payload.matching_spans:
        source_end = span.source_end_byte
        if source_end > len(source_bytes):
            return _verification(
                document,
                state="mismatch",
                eligibility="human_review_required",
                source_digest=_digest(source_bytes),
                span_digests=tuple(verified_spans),
                reason_codes=("source_span_out_of_bounds",),
            )
        source_span = source_bytes[span.source_start_byte : source_end]
        if _digest(source_span) != span.source_span_digest:
            return _verification(
                document,
                state="mismatch",
                eligibility="human_review_required",
                source_digest=_digest(source_bytes),
                span_digests=tuple(verified_spans),
                reason_codes=("source_span_digest_mismatch",),
            )
        component = component_bytes[span.component_artifact_ref.artifact_id]
        if (
            type(component) is not bytes
            or len(component) > MAX_COMPONENT_EVIDENCE_BYTES
            or _digest(component) != span.component_artifact_ref.digest
        ):
            return _verification(
                document,
                state="mismatch",
                eligibility="human_review_required",
                source_digest=_digest(source_bytes),
                span_digests=tuple(verified_spans),
                reason_codes=("component_artifact_digest_mismatch_or_over_limit",),
            )
        if payload.relation == "exact_component":
            if span.comparison == "exact_bytes":
                matches = source_span == component
            elif payload.normalizer_version == _NORMALIZATION_VERSION:
                normalized_source = _normalized_text(source_span)
                normalized_component = _normalized_text(component)
                if normalized_source is None or normalized_component is None:
                    return _verification(
                        document,
                        state="unsupported",
                        eligibility="human_review_required",
                        source_digest=_digest(source_bytes),
                        span_digests=tuple(verified_spans),
                        reason_codes=("exact_normalized_match_requires_utf8_text",),
                    )
                matches = normalized_source == normalized_component
            else:
                return _verification(
                    document,
                    state="unsupported",
                    eligibility="human_review_required",
                    source_digest=_digest(source_bytes),
                    span_digests=tuple(verified_spans),
                    reason_codes=("match_normalizer_version_not_supported",),
                )
            if not matches:
                return _verification(
                    document,
                    state="mismatch",
                    eligibility="human_review_required",
                    source_digest=_digest(source_bytes),
                    span_digests=tuple(verified_spans),
                    reason_codes=("declared_exact_component_did_not_match",),
                )
        verified_spans.append(span.source_span_digest)

    self_source = (
        payload.source_lineage == "official_self_import"
        or payload.source_task_ref is not None
        and payload.source_task_ref.entity_id == payload.task_ref.entity_id
        or payload.source_benchmark_ref == payload.target_benchmark_ref
    )
    reasons: tuple[str, ...]
    if self_source:
        eligibility: MatchContentEligibility = "self_source_excluded"
        reasons = ("official_benchmark_self_source_excluded",)
    elif any(span.content_class != "substantive" for span in payload.matching_spans):
        eligibility = "boilerplate_review_required"
        reasons = ("boilerplate_or_substantive_classification_requires_review",)
    else:
        eligibility = "human_review_required"
        reasons_list = ["source_authorization_and_rights_verifier_unavailable"]
        if payload.source_lineage == "unknown":
            reasons_list.append("source_lineage_unknown")
        if payload.source_date_state != "verified":
            reasons_list.append("source_date_not_verified")
        if payload.relation in {"shared_concept", "no_substantive_match"}:
            reasons_list.append("relation_does_not_establish_duplicate_overlap")
        elif payload.relation in {
            "near_exact_component",
            "semantic_duplicate",
            "shared_family",
            "unresolved",
        }:
            reasons_list.append("independent_human_relation_review_required")
        else:
            reasons_list.append("exact_match_auto_accept_policy_not_calibrated")
        reasons = tuple(reasons_list)
    return _verification(
        document,
        state="verified",
        eligibility=eligibility,
        source_digest=_digest(source_bytes),
        span_digests=tuple(verified_spans),
        reason_codes=reasons,
    )


def build_match_successor(
    previous: MatchEvidenceDocumentV2,
    *,
    successor_id: UUID,
    payload: MatchEvidencePayloadV2,
    metadata: DocumentMetadata,
) -> MatchEvidenceDocumentV2:
    """Create a corrected immutable successor and leave the prior document untouched."""
    if payload.task_ref != previous.payload.task_ref:
        raise ValueError("a corrected match successor must retain the same task")
    if successor_id == previous.id:
        raise ValueError("a corrected match successor must have a new document identity")
    if payload.review_state not in {"proposed", "review_required", "disputed"}:
        raise ValueError("corrected evidence requires a fresh review before acceptance")
    previous_time = datetime.fromisoformat(previous.metadata.created_at.replace("Z", "+00:00"))
    successor_time = datetime.fromisoformat(metadata.created_at.replace("Z", "+00:00"))
    if successor_time <= previous_time:
        raise ValueError("corrected match successor must be newer than its predecessor")
    return MatchEvidenceDocumentV2(
        id=successor_id,
        kind="match_evidence",
        schema_version=2,
        payload=payload,
        metadata=metadata,
        supersedes_id=previous.id,
    )


def match_relation_rubric_digest() -> str:
    return canonical_digest(MatchRelationRubric().model_dump(mode="json"))


def build_match_correction_record(
    previous: MatchEvidenceDocumentV2,
    successor: MatchEvidenceDocumentV2,
    *,
    correction_id: UUID | None = None,
    actor_subject: str,
    reason: str,
    supporting_evidence_refs: tuple[AuditDocumentRef, ...],
    correction_artifact_ref: ImmutableArtifactRef,
    created_at: str,
) -> MatchCorrectionRecord:
    if successor.supersedes_id != previous.id:
        raise ValueError("corrected evidence must directly supersede its predecessor")
    if (
        actor_subject == previous.payload.author_subject
        or actor_subject in previous.payload.reviewer_subjects
    ):
        raise ValueError(
            "correction actor must be independent of the original author and reviewers"
        )
    return MatchCorrectionRecord(
        correction_id=correction_id or uuid4(),
        predecessor_ref=_document_ref(previous),
        successor_ref=_document_ref(successor),
        actor_subject=actor_subject,
        reason=reason,
        supporting_evidence_refs=supporting_evidence_refs,
        correction_artifact_ref=correction_artifact_ref,
        created_at=created_at,
    )
