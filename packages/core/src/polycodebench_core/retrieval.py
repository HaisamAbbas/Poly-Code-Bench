"""Frozen candidate-retrieval plans, bounded results, coverage and replay contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from polycodebench_core.benchmark_audit_documents import AuditDocumentRef, EntityRef
from polycodebench_core.models import Digest, Seed64

RetrievalStage = Literal["exact_normalized", "lexical_code", "semantic"]
SourceGroupSlug = Literal[
    "common-crawl",
    "github",
    "hugging-face",
    "arxiv",
    "stack-exchange",
    "wikipedia",
    "benchmark-repositories",
    "other-public-datasets",
]
RetrievalFeature = Literal[
    "exact_bytes", "normalized_text", "token_shingles", "code_structure", "semantic"
]
RetrievalPlanState = Literal["planned", "blocked"]
RETRIEVAL_STAGE_ORDER: tuple[RetrievalStage, ...] = (
    "exact_normalized",
    "lexical_code",
    "semantic",
)
RetrievalMethodState = Literal["available", "partial", "blocked", "unsupported"]
RetrievalUnitState = Literal["planned", "blocked", "unsupported"]
RetrievalOutcomeState = Literal[
    "planned", "complete", "no_match", "truncated", "failed", "blocked", "unsupported"
]
RetrievalCoverageState = Literal["complete", "partial", "blocked"]


class RetrievalMethodPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    stage: RetrievalStage
    method_digest: Digest | None
    state: RetrievalMethodState
    supported_features: tuple[RetrievalFeature, ...] = Field(max_length=5)
    unsupported_features: tuple[RetrievalFeature, ...] = Field(max_length=5)
    reason_codes: tuple[str, ...] = Field(max_length=12)

    @model_validator(mode="after")
    def method_state_is_explicit(self) -> RetrievalMethodPin:
        if len(set(self.supported_features)) != len(self.supported_features):
            raise ValueError("supported retrieval features must be unique")
        if len(set(self.unsupported_features)) != len(self.unsupported_features):
            raise ValueError("unsupported retrieval features must be unique")
        if set(self.supported_features) & set(self.unsupported_features):
            raise ValueError("retrieval features cannot be both supported and unsupported")
        if self.state == "available" and (
            self.method_digest is None
            or self.reason_codes
            or self.unsupported_features
            or not self.supported_features
        ):
            raise ValueError("available retrieval methods require a digest and no blockers")
        if self.state == "partial" and (
            self.method_digest is None
            or not self.reason_codes
            or not self.supported_features
            or not self.unsupported_features
        ):
            raise ValueError("partial retrieval methods require supported and blocked features")
        if self.state in {"blocked", "unsupported"} and (
            not self.reason_codes or self.supported_features or not self.unsupported_features
        ):
            raise ValueError(
                "unavailable retrieval methods require reasons and no supported features"
            )
        return self


class RetrievalIndexPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    stage: RetrievalStage
    index_digest: Digest


class RetrievalSourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_group: SourceGroupSlug
    source_revision: str | None = Field(default=None, min_length=1, max_length=255)
    snapshot_ref: AuditDocumentRef | None
    rights_ref: AuditDocumentRef | None
    privacy_scope_digest: Digest | None
    index_pins: tuple[RetrievalIndexPin, ...] = Field(max_length=3)
    state: Literal["provided", "blocked"]
    blocker_codes: tuple[str, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def pin_is_complete_or_blocked(self) -> RetrievalSourcePin:
        index_stages = [item.stage for item in self.index_pins]
        if len(index_stages) != len(set(index_stages)):
            raise ValueError("source index stage pins must be unique")
        if self.state == "provided":
            if (
                self.source_revision is None
                or self.snapshot_ref is None
                or self.snapshot_ref.kind != "corpus_snapshot"
                or self.rights_ref is None
                or self.privacy_scope_digest is None
                or not {"exact_normalized", "lexical_code"} <= set(index_stages)
                or self.blocker_codes
            ):
                raise ValueError(
                    "provided retrieval sources require snapshot, rights and stage indexes"
                )
        elif not self.blocker_codes:
            raise ValueError("blocked retrieval sources require explicit blocker codes")
        return self


class RetrievalPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    audit_plan_ref: AuditDocumentRef
    task_ref: EntityRef
    component_refs: tuple[EntityRef, ...] = Field(min_length=1, max_length=16)
    source_groups: tuple[SourceGroupSlug, ...] = Field(min_length=1, max_length=8)
    source_pins: tuple[RetrievalSourcePin, ...] = Field(max_length=8)
    seed: Seed64

    @model_validator(mode="after")
    def request_scope_is_unique(self) -> RetrievalPlanRequest:
        if self.audit_plan_ref.kind != "audit_plan":
            raise ValueError("retrieval request requires a frozen audit plan reference")
        if len({item.entity_id for item in self.component_refs}) != len(self.component_refs):
            raise ValueError("retrieval request component references must be unique")
        if len(set(self.source_groups)) != len(self.source_groups):
            raise ValueError("retrieval source groups must be unique")
        pin_groups = [item.source_group for item in self.source_pins]
        if len(pin_groups) != len(set(pin_groups)) or not set(pin_groups) <= set(
            self.source_groups
        ):
            raise ValueError("source pins must be unique and belong to the requested source scope")
        return self


class RetrievalQueryUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unit_digest: Digest
    component_ref: EntityRef
    source_group: SourceGroupSlug
    stage: RetrievalStage
    state: RetrievalUnitState
    reason_codes: tuple[str, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def unit_state_has_reason(self) -> RetrievalQueryUnit:
        if self.state == "planned" and self.reason_codes:
            raise ValueError("planned retrieval units cannot carry blocker reasons")
        if self.state != "planned" and not self.reason_codes:
            raise ValueError("blocked or unsupported retrieval units require reasons")
        return self


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    audit_plan_ref: AuditDocumentRef
    task_ref: EntityRef
    component_refs: tuple[EntityRef, ...] = Field(min_length=1, max_length=16)
    source_pins: tuple[RetrievalSourcePin, ...] = Field(min_length=1, max_length=8)
    method_pins: tuple[RetrievalMethodPin, ...] = Field(min_length=3, max_length=3)
    query_units: tuple[RetrievalQueryUnit, ...] = Field(min_length=1, max_length=384)
    seed: Seed64
    max_candidates_per_source: int = Field(ge=1, le=20)
    max_candidates_total: int = Field(ge=1, le=100)
    selection_rule: Literal["stage_rank_source_identity_v1"] = "stage_rank_source_identity_v1"
    state: RetrievalPlanState
    dispatch_allowed: Literal[False] = False

    @model_validator(mode="after")
    def frozen_plan_is_complete(self) -> RetrievalPlan:
        if self.audit_plan_ref.kind != "audit_plan":
            raise ValueError("retrieval plan must bind its immutable audit plan")
        component_ids = [item.entity_id for item in self.component_refs]
        source_groups = [item.source_group for item in self.source_pins]
        stages = [item.stage for item in self.method_pins]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("retrieval plan component references must be unique")
        if len(source_groups) != len(set(source_groups)):
            raise ValueError("retrieval source groups must be unique")
        if tuple(stages) != RETRIEVAL_STAGE_ORDER:
            raise ValueError("retrieval methods must use the fixed stage order")
        expected = {
            (component.entity_id, source.source_group, stage)
            for component in self.component_refs
            for source in self.source_pins
            for stage in RETRIEVAL_STAGE_ORDER
        }
        actual = {
            (unit.component_ref.entity_id, unit.source_group, unit.stage)
            for unit in self.query_units
        }
        if len(actual) != len(self.query_units) or actual != expected:
            raise ValueError(
                "query units must cover every planned component/source/stage exactly once"
            )
        if (self.state == "blocked") != any(unit.state == "blocked" for unit in self.query_units):
            raise ValueError("retrieval plan state must reflect blocked query units")
        return self


class RetrievalCandidateHit(BaseModel):
    """A content-free candidate locator emitted by an approved local index."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_ref: EntityRef
    component_ref: EntityRef
    query_unit_digest: Digest
    source_group: SourceGroupSlug
    source_revision: str = Field(min_length=1, max_length=255)
    source_document_digest: Digest
    candidate_component_digest: Digest
    stage: RetrievalStage
    rank: int = Field(ge=1, le=1_000_000)


class SourceCandidateLimit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_group: SourceGroupSlug
    unique_candidates: int = Field(ge=0, le=9_007_199_254_740_991)
    retained_candidates: int = Field(ge=0, le=20)
    discarded_candidates: int = Field(ge=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def source_truncation_reconciles(self) -> SourceCandidateLimit:
        if self.retained_candidates + self.discarded_candidates != self.unique_candidates:
            raise ValueError("source candidate truncation counts do not reconcile")
        if self.retained_candidates > 20:
            raise ValueError("source retained-candidate cap is 20")
        return self


class CandidateSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan_digest: Digest
    input_candidates_digest: Digest
    observed_hits: int = Field(ge=0, le=9_007_199_254_740_991)
    unique_candidates: int = Field(ge=0, le=9_007_199_254_740_991)
    selected_candidates: tuple[RetrievalCandidateHit, ...] = Field(max_length=100)
    source_limits: tuple[SourceCandidateLimit, ...] = Field(min_length=1, max_length=8)
    total_cap_discarded: int = Field(ge=0, le=9_007_199_254_740_991)
    result_digest: Digest

    @model_validator(mode="after")
    def selection_counts_reconcile(self) -> CandidateSelectionResult:
        if self.unique_candidates > self.observed_hits:
            raise ValueError("unique candidate count cannot exceed observed hits")
        source_unique = sum(item.unique_candidates for item in self.source_limits)
        source_retained = sum(item.retained_candidates for item in self.source_limits)
        source_discarded = sum(item.discarded_candidates for item in self.source_limits)
        source_groups = [item.source_group for item in self.source_limits]
        if len(source_groups) != len(set(source_groups)):
            raise ValueError("candidate source-limit rows must be unique")
        if source_unique != self.unique_candidates:
            raise ValueError(
                "source candidate counts do not reconcile with total unique candidates"
            )
        if source_retained - self.total_cap_discarded != len(self.selected_candidates):
            raise ValueError("global candidate truncation count does not reconcile")
        if self.unique_candidates != source_retained + source_discarded:
            raise ValueError("per-source candidate truncation counts do not reconcile")
        if len(self.selected_candidates) > 100:
            raise ValueError("task retained-candidate cap is 100")
        return self


class RetrievalQueryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unit_digest: Digest
    state: RetrievalOutcomeState
    queries_attempted: int = Field(ge=0, le=9_007_199_254_740_991)
    candidates_observed: int = Field(ge=0, le=9_007_199_254_740_991)
    truncated_candidates: int = Field(ge=0, le=9_007_199_254_740_991)
    candidates_digest: Digest | None
    reason_code: str | None

    @model_validator(mode="after")
    def outcome_matches_observation(self) -> RetrievalQueryOutcome:
        if self.truncated_candidates > self.candidates_observed:
            raise ValueError("truncated candidate count cannot exceed observed candidates")
        if self.state == "planned":
            if any(
                (
                    self.queries_attempted,
                    self.candidates_observed,
                    self.truncated_candidates,
                    self.candidates_digest is not None,
                    self.reason_code is not None,
                )
            ):
                raise ValueError("planned query units cannot claim executed results")
        elif self.state in {"blocked", "unsupported"}:
            if (
                self.queries_attempted
                or self.candidates_observed
                or self.truncated_candidates
                or self.candidates_digest is not None
                or not self.reason_code
            ):
                raise ValueError(
                    "blocked or unsupported query units require a reason and no request evidence"
                )
        elif self.state in {"failed", "truncated"}:
            if not self.reason_code or self.queries_attempted < 1:
                raise ValueError(
                    "failed or truncated units require attempts and an explicit reason"
                )
            if self.state == "truncated" and self.truncated_candidates < 1:
                raise ValueError("truncated units require an explicit truncation count")
            if self.state == "truncated" and (
                self.candidates_observed < 1 or self.candidates_digest is None
            ):
                raise ValueError("truncated units require observed candidates and their digest")
            if (self.candidates_observed > 0) != (self.candidates_digest is not None):
                raise ValueError("failed or truncated observations must bind observed candidates")
        elif self.state == "no_match":
            if (
                self.queries_attempted < 1
                or self.candidates_observed != 0
                or self.truncated_candidates
                or self.reason_code is not None
                or self.candidates_digest is None
            ):
                raise ValueError("no-match requires a completed finite query with zero candidates")
        elif self.state == "complete":
            if (
                self.queries_attempted < 1
                or self.candidates_observed < 1
                or self.truncated_candidates
                or self.reason_code is not None
                or self.candidates_digest is None
            ):
                raise ValueError(
                    "complete query units require candidates and no truncation or errors"
                )
        return self


class RetrievalCoverageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan_digest: Digest
    outcomes: tuple[RetrievalQueryOutcome, ...] = Field(min_length=1, max_length=384)
    state: RetrievalCoverageState

    @model_validator(mode="after")
    def coverage_has_no_hidden_units(self) -> RetrievalCoverageManifest:
        unit_digests = [item.unit_digest for item in self.outcomes]
        if len(unit_digests) != len(set(unit_digests)):
            raise ValueError("coverage outcomes must identify unique query units")
        all_searched = all(item.state in {"complete", "no_match"} for item in self.outcomes)
        any_attempts = any(item.queries_attempted > 0 for item in self.outcomes)
        expected_state = "complete" if all_searched else "partial" if any_attempts else "blocked"
        if self.state != expected_state:
            raise ValueError("coverage state must be derived from every planned query unit")
        return self


class RetrievalCacheIdentity(BaseModel):
    """Content identity for scoped cache lookup; it never asserts prior execution."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: UUID
    permission_scope_digest: Digest
    task_digest: Digest
    component_digest: Digest
    source_group: SourceGroupSlug
    corpus_snapshot_digest: Digest
    index_digest: Digest
    method_digest: Digest
    stage: RetrievalStage
    query_digest: Digest
    selection_seed: Seed64
    max_candidates_per_source: int = Field(ge=1, le=20)
    max_candidates_total: int = Field(ge=1, le=100)
    selection_rule: Literal["stage_rank_source_identity_v1"] = "stage_rank_source_identity_v1"


class RetrievalReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    state: Literal["replayed", "missing_evidence", "mismatch"]
    expected_input_digest: Digest
    expected_result_digest: Digest
    actual_input_digest: Digest | None
    actual_result_digest: Digest | None
    reason_code: str | None

    @model_validator(mode="after")
    def replay_state_is_explicit(self) -> RetrievalReplayResult:
        if self.state == "replayed":
            if (
                self.actual_input_digest != self.expected_input_digest
                or self.actual_result_digest != self.expected_result_digest
                or self.reason_code is not None
            ):
                raise ValueError("successful replay must match stored input and result digests")
        elif not self.reason_code:
            raise ValueError("missing or mismatched replay evidence requires a reason")
        if self.state == "missing_evidence" and (
            self.actual_input_digest is not None or self.actual_result_digest is not None
        ):
            raise ValueError("missing replay evidence cannot claim actual digests")
        return self
