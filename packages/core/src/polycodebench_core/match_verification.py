"""Immutable review, adjudication and correction contracts for match evidence."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    ImmutableArtifactRef,
    MatchEvidenceDocumentV2,
    MatchOpinionDecision,
    MatchRelation,
    MatchReviewOpinion,
    ShortText,
    audit_document_digest,
)
from polycodebench_core.models import Digest, UtcTimestamp

MatchLedgerState = Literal["proposed", "accepted", "rejected", "disputed", "adjudicated"]
MatchAdjudicationDecision = Literal["accepted", "rejected"]
MATCH_RELATION_RUBRIC_DEFINITIONS = (
    "exact_component: verified substantive component equality beyond boilerplate",
    "near_exact_component: small edit or renaming with differences retained",
    "semantic_duplicate: same task requirements/solution under independent human review",
    "shared_family: lineage or template relationship without automatic exact overlap",
    "shared_concept: common topic or algorithm with zero duplicate contribution",
    "no_substantive_match: reviewed evidence does not establish substantive overlap",
    "unresolved: inadequate or conflicting evidence; never an automatic clean result",
)
MatchContentState = Literal[
    "verified", "mismatch", "missing_artifact", "scope_violation", "unsupported"
]
MatchContentEligibility = Literal[
    "candidate_only", "human_review_required", "boilerplate_review_required", "self_source_excluded"
]


class MatchContentVerification(BaseModel):
    """Digest/span validation result, explicitly weaker than trusted source verification."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    candidate_ref: AuditDocumentRef
    state: MatchContentState
    eligibility: MatchContentEligibility
    source_digest: Digest | None
    verified_span_digests: tuple[Digest, ...] = Field(max_length=64)
    reason_codes: tuple[ShortText, ...] = Field(max_length=16)
    source_trust: Literal["unverified"] = "unverified"
    rights_trust: Literal["unverified"] = "unverified"
    accepted_evidence: Literal[False] = False

    @model_validator(mode="after")
    def content_result_is_explicit(self) -> MatchContentVerification:
        if self.candidate_ref.kind != "match_evidence":
            raise ValueError("content verification must bind match evidence")
        if self.state == "verified":
            if self.source_digest is None or not self.verified_span_digests:
                raise ValueError("verified content requires source and span digests")
        elif not self.reason_codes:
            raise ValueError("incomplete or mismatched content requires explicit reason codes")
        if self.eligibility == "self_source_excluded" and self.state != "verified":
            raise ValueError("self-source exclusion requires integrity-checked source content")
        return self


class MatchAdjudication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    adjudication_id: UUID
    candidate_ref: AuditDocumentRef
    candidate_author_subject: ShortText
    adjudicator_subject: ShortText
    reviewed_opinion_ids: tuple[UUID, ...] = Field(min_length=2, max_length=32)
    decision: MatchAdjudicationDecision
    relation: MatchRelation
    reason: ShortText
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=32)
    decision_artifact_ref: ImmutableArtifactRef
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def adjudicator_is_independent(self) -> MatchAdjudication:
        if self.candidate_ref.kind != "match_evidence":
            raise ValueError("match adjudication must bind match evidence")
        if self.adjudicator_subject == self.candidate_author_subject:
            raise ValueError("a match author cannot adjudicate their own evidence")
        if len(set(self.reviewed_opinion_ids)) != len(self.reviewed_opinion_ids):
            raise ValueError("adjudication must cite unique review opinions")
        if len({(item.kind, item.document_id) for item in self.evidence_refs}) != len(
            self.evidence_refs
        ):
            raise ValueError("adjudication evidence references must be unique")
        return self


class MatchReviewSubmission(BaseModel):
    """Reviewer-supplied decision fields; identity, sequence and time are server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decision: MatchOpinionDecision
    relation: MatchRelation
    reason: ShortText
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=32)
    decision_artifact_ref: ImmutableArtifactRef

    @model_validator(mode="after")
    def evidence_refs_are_unique(self) -> MatchReviewSubmission:
        if len({(item.kind, item.document_id) for item in self.evidence_refs}) != len(
            self.evidence_refs
        ):
            raise ValueError("match review evidence references must be unique")
        if self.decision_artifact_ref.visibility == "public":
            raise ValueError("match review decision artifacts must remain private or restricted")
        return self


class MatchAdjudicationSubmission(BaseModel):
    """Adjudicator-supplied fields; identity, opinion set and time are server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decision: MatchAdjudicationDecision
    relation: MatchRelation
    reason: ShortText
    evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=32)
    decision_artifact_ref: ImmutableArtifactRef

    @model_validator(mode="after")
    def evidence_refs_are_unique_and_private(self) -> MatchAdjudicationSubmission:
        if len({(item.kind, item.document_id) for item in self.evidence_refs}) != len(
            self.evidence_refs
        ):
            raise ValueError("match adjudication evidence references must be unique")
        if self.decision_artifact_ref.visibility == "public":
            raise ValueError("match adjudication artifacts must remain private or restricted")
        return self


class MatchReviewLedger(BaseModel):
    """An immutable projection of append-only opinions; persistence must append events."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    candidate_ref: AuditDocumentRef
    candidate_author_subject: ShortText
    opinions: tuple[MatchReviewOpinion, ...] = Field(max_length=32)
    adjudication: MatchAdjudication | None
    state: MatchLedgerState
    resolved_relation: MatchRelation | None

    @model_validator(mode="after")
    def ledger_state_is_derived(self) -> MatchReviewLedger:
        if self.candidate_ref.kind != "match_evidence":
            raise ValueError("match review ledger must bind match evidence")
        if len({item.opinion_id for item in self.opinions}) != len(self.opinions):
            raise ValueError("match review opinion identities must be unique")
        if len({item.reviewer_subject for item in self.opinions}) != len(self.opinions):
            raise ValueError("each reviewer may submit one opinion per candidate")
        for position, opinion in enumerate(self.opinions, start=1):
            if (
                opinion.candidate_ref != self.candidate_ref
                or opinion.candidate_author_subject != self.candidate_author_subject
                or opinion.review_seq != position
            ):
                raise ValueError("review opinions must append in sequence for this candidate")
        decisions = {item.decision for item in self.opinions}
        has_conflict = "accepted" in decisions and "rejected" in decisions
        if self.adjudication is not None:
            if not has_conflict:
                raise ValueError("adjudication requires conflicting accepted/rejected opinions")
            if (
                self.adjudication.candidate_ref != self.candidate_ref
                or self.adjudication.candidate_author_subject != self.candidate_author_subject
            ):
                raise ValueError("adjudication must bind the same candidate and author")
            cited = set(self.adjudication.reviewed_opinion_ids)
            conflicting = {
                item.opinion_id
                for item in self.opinions
                if item.decision in {"accepted", "rejected"}
            }
            if not conflicting <= cited:
                raise ValueError("adjudication must retain and cite every conflicting opinion")
            if self.adjudication.adjudicator_subject in {
                self.candidate_author_subject,
                *(item.reviewer_subject for item in self.opinions),
            }:
                raise ValueError("adjudicator must be independent of author and reviewers")
            expected_state: MatchLedgerState = "adjudicated"
            expected_relation = self.adjudication.relation
        elif has_conflict or "disputed" in decisions:
            expected_state = "disputed"
            expected_relation = None
        elif not self.opinions:
            expected_state = "proposed"
            expected_relation = None
        else:
            latest = self.opinions[-1]
            expected_state = latest.decision
            expected_relation = latest.relation
        if self.state != expected_state or self.resolved_relation != expected_relation:
            raise ValueError("match review ledger state must derive from its append-only events")
        return self


def _match_document_ref(document: MatchEvidenceDocumentV2) -> AuditDocumentRef:
    return AuditDocumentRef(
        document_id=document.id,
        digest=audit_document_digest(document),
        kind="match_evidence",
    )


def initial_match_review_ledger(document: MatchEvidenceDocumentV2) -> MatchReviewLedger:
    author = document.payload.author_subject
    if author is None:
        raise ValueError("review lifecycle requires a recorded candidate author")
    return MatchReviewLedger(
        candidate_ref=_match_document_ref(document),
        candidate_author_subject=author,
        opinions=(),
        adjudication=None,
        state="proposed",
        resolved_relation=None,
    )


def append_match_review(
    ledger: MatchReviewLedger,
    *,
    opinion_id: UUID,
    reviewer_subject: str,
    decision: MatchOpinionDecision,
    relation: MatchRelation,
    reason: ShortText,
    evidence_refs: tuple[AuditDocumentRef, ...],
    decision_artifact_ref: ImmutableArtifactRef,
    created_at: str,
) -> MatchReviewLedger:
    if ledger.adjudication is not None:
        raise ValueError("cannot append an ordinary review after adjudication")
    opinion = MatchReviewOpinion(
        opinion_id=opinion_id,
        candidate_ref=ledger.candidate_ref,
        candidate_author_subject=ledger.candidate_author_subject,
        reviewer_subject=reviewer_subject,
        review_seq=len(ledger.opinions) + 1,
        decision=decision,
        relation=relation,
        reason=reason,
        evidence_refs=evidence_refs,
        decision_artifact_ref=decision_artifact_ref,
        created_at=created_at,
    )
    opinions = (*ledger.opinions, opinion)
    decisions = {item.decision for item in opinions}
    conflict = "accepted" in decisions and "rejected" in decisions
    if conflict or "disputed" in decisions:
        state: MatchLedgerState = "disputed"
        resolved_relation = None
    else:
        latest = opinions[-1]
        state = latest.decision
        resolved_relation = latest.relation
    return MatchReviewLedger(
        candidate_ref=ledger.candidate_ref,
        candidate_author_subject=ledger.candidate_author_subject,
        opinions=opinions,
        adjudication=None,
        state=state,
        resolved_relation=resolved_relation,
    )


def adjudicate_match_reviews(
    ledger: MatchReviewLedger,
    *,
    adjudication_id: UUID,
    adjudicator_subject: str,
    decision: MatchAdjudicationDecision,
    relation: MatchRelation,
    reason: ShortText,
    evidence_refs: tuple[AuditDocumentRef, ...],
    decision_artifact_ref: ImmutableArtifactRef,
    created_at: str,
) -> MatchReviewLedger:
    if ledger.state != "disputed":
        raise ValueError("match adjudication requires unresolved conflicting opinions")
    conflict_ids = tuple(
        item.opinion_id for item in ledger.opinions if item.decision in {"accepted", "rejected"}
    )
    if len(conflict_ids) < 2:
        raise ValueError("match adjudication requires both accepted and rejected opinions")
    decision_record = MatchAdjudication(
        adjudication_id=adjudication_id,
        candidate_ref=ledger.candidate_ref,
        candidate_author_subject=ledger.candidate_author_subject,
        adjudicator_subject=adjudicator_subject,
        reviewed_opinion_ids=conflict_ids,
        decision=decision,
        relation=relation,
        reason=reason,
        evidence_refs=evidence_refs,
        decision_artifact_ref=decision_artifact_ref,
        created_at=created_at,
    )
    return MatchReviewLedger(
        candidate_ref=ledger.candidate_ref,
        candidate_author_subject=ledger.candidate_author_subject,
        opinions=ledger.opinions,
        adjudication=decision_record,
        state="adjudicated",
        resolved_relation=decision_record.relation,
    )


class MatchJudgeSpan(BaseModel):
    """Source text enters a judge packet as labeled untrusted data, never policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    span_id: ShortText
    source_span_digest: Digest
    text: str = Field(min_length=1, max_length=4_000)
    trust: Literal["untrusted_source_data"] = "untrusted_source_data"
    instruction_attempt: bool


class MatchJudgePacket(BaseModel):
    """A minimum-context, no-tools packet whose model output can only propose a relation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    packet_id: UUID
    candidate_ref: AuditDocumentRef
    task_context_digest: Digest
    component_refs: tuple[Digest, ...] = Field(min_length=1, max_length=16)
    source_spans: tuple[MatchJudgeSpan, ...] = Field(min_length=1, max_length=32)
    rubric_digest: Digest
    policy_digest: Digest
    tools: tuple[str, ...] = ()
    verdict_authority: Literal["proposal_only"] = "proposal_only"

    @model_validator(mode="after")
    def packet_is_closed(self) -> MatchJudgePacket:
        if self.candidate_ref.kind != "match_evidence":
            raise ValueError("match judge packet must bind candidate evidence")
        if self.tools:
            raise ValueError("match evidence judge packets cannot expose tools")
        if len(set(self.component_refs)) != len(self.component_refs):
            raise ValueError("match judge packet components must be unique")
        if len({item.span_id for item in self.source_spans}) != len(self.source_spans):
            raise ValueError("match judge packet span IDs must be unique")
        return self


class MatchCorrectionRecord(BaseModel):
    """A correction points to immutable predecessor/successor documents."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    correction_id: UUID
    predecessor_ref: AuditDocumentRef
    successor_ref: AuditDocumentRef
    actor_subject: ShortText
    reason: ShortText
    supporting_evidence_refs: tuple[AuditDocumentRef, ...] = Field(min_length=1, max_length=32)
    correction_artifact_ref: ImmutableArtifactRef
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def successor_is_distinct(self) -> MatchCorrectionRecord:
        if (
            self.predecessor_ref.kind != "match_evidence"
            or self.successor_ref.kind != "match_evidence"
        ):
            raise ValueError("match corrections must bind match evidence documents")
        if self.predecessor_ref.document_id == self.successor_ref.document_id:
            raise ValueError("a correction must create a successor evidence document")
        if len({(item.kind, item.document_id) for item in self.supporting_evidence_refs}) != len(
            self.supporting_evidence_refs
        ):
            raise ValueError("correction evidence references must be unique")
        return self


class MatchRelationRubric(BaseModel):
    """Frozen relation meanings; rubric changes require a new digest/version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal["match-relation-rubric-v1"] = "match-relation-rubric-v1"
    relation_definitions: tuple[ShortText, ...] = MATCH_RELATION_RUBRIC_DEFINITIONS
    exact_requires_verified_substantive_spans: Literal[True] = True
    semantic_requires_human_review: Literal[True] = True
    concept_only_duplicate_weight: Literal[0] = 0
    unresolved_requires_review: Literal[True] = True
    auto_accept_enabled: Literal[False] = False

    @model_validator(mode="after")
    def definitions_are_versioned(self) -> MatchRelationRubric:
        if self.relation_definitions != MATCH_RELATION_RUBRIC_DEFINITIONS:
            raise ValueError("changing match relation definitions requires a new rubric version")
        return self
