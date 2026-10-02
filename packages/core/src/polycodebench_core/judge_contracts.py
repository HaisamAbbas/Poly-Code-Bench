"""Judge execution contracts: rubric, panel, packet, vote, delivery, outcome, adjudication.

Technical Spec 15.1-15.3 and Architecture 8.8. These are immutable, versioned types plus the
validation rules that decide whether a judge response is admissible at all. Nothing here
performs I/O; the judge gateway (``pcb-judge``), the reviewer workflow and the scorer all read
these types, so what a judge is shown, what it may answer and what is retained are decided in
one place.

Two rules shape the whole module:

* Identity, provider, measured rank, cost and the expected scalar score are *absent from the
  packet type*, so they cannot reach a judge. ``assert_no_identity_leak`` additionally proves it
  for the concrete strings of a run rather than trusting the type.
* Every delivery is retained. A rejected delivery is evidence about the judge, not noise, and a
  low score is never a reason to ask again.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from polycodebench_core.application_errors import ServiceError
from polycodebench_core.canonical import (
    canonical_document_bytes,
    canonical_envelope,
    canonical_json_bytes,
    sha256_bytes,
)
from polycodebench_core.models import (
    ContractModel,
    Decimal6,
    Digest,
    EntityId,
    RelativePath,
    ScoreDimension,
    Slug,
    UtcTimestamp,
)


class JudgeModel(BaseModel):
    """Strict, frozen, extra-free base for the parts of a judge document.

    Top-level documents are ``ContractModel``s (they carry ``schema_version``); the nested parts are
    not documents in their own right and must not pretend to be.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


ANCHOR_QUANTUM = Decimal("0.000001")
MAX_ITEMS = 24
MAX_SPANS = 80
MAX_SPAN_CHARS = 12_000
MAX_COMMENT_CHARS = 2_000
MAX_RATIONALE_CHARS = 1_500
MAX_NOTE_CHARS = 400
MAX_COMMENTS = 24
MAX_CITATIONS = 16
MAX_FACTS = 8
MAX_UNCERTAINTY_FLAGS = 4
MAX_CLAIM_SUBJECT_CHARS = 120
MAX_CALIBRATION_PACKETS_PER_LANGUAGE = 30
PILOT_JUDGE_VOTES = 3
RESIDUAL_DIMENSIONS = (
    ScoreDimension.CODE_QUALITY,
    ScoreDimension.IDIOMATIC,
    ScoreDimension.ROBUSTNESS,
)
# Fields a judge must never see. They are withheld structurally and asserted at runtime.
BLINDED_FIELDS = (
    "candidate_identity",
    "provider",
    "rank",
    "cost",
    "expected_score",
)
# Substrings that make a packet suspicious if they appear in a field name. Checked by
# ``assert_no_identity_leak`` so a future field cannot smuggle identity into a packet.
_FORBIDDEN_NAME_TOKENS = (
    "candidate_id",
    "model_config",
    "model_id",
    "provider",
    "rank",
    "cost",
    "price",
    "expected_score",
    "token_count",
    "latency",
)


class JudgeError(ServiceError):
    code = "JUDGE_ERROR"
    status_code = 500


class PanelUnavailable(JudgeError):
    """No approved judge model configuration, or the panel identity is not usable.

    Absent judge access stays a blocker. It never becomes an invented review or a synthetic
    vote standing in for a judge that was never called.
    """

    code = "JUDGE_PANEL_UNAVAILABLE"
    status_code = 409


class PanelChangeRequiresNewCohort(JudgeError):
    """The panel changed inside an existing cohort; the cohort needs a new version."""

    code = "JUDGE_PANEL_CHANGE_REQUIRES_NEW_COHORT"
    status_code = 409


class VoteRejected(JudgeError):
    """One judge delivery failed schema or packet-reference validation."""

    code = "JUDGE_VOTE_REJECTED"
    status_code = 422

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail[:200]}")
        self.reason = reason
        self.detail = detail


class IdentityLeak(JudgeError):
    """A packet would disclose withheld candidate identity, rank or cost."""

    code = "JUDGE_IDENTITY_LEAK"
    status_code = 422


class CalibrationBlocked(JudgeError):
    """Calibration cannot be reported as measured without real human labels."""

    code = "JUDGE_CALIBRATION_BLOCKED"
    status_code = 409


ItemId = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9_]{2,63}$", min_length=3, max_length=64),
]
AnchorId = Annotated[
    str,
    Field(pattern=r"^(?:sp|cmt)-[0-9a-f]{16}$"),
]


def quantize_anchor(value: Decimal | str) -> str:
    """Six fixed places, half-up. The anchor vocabulary is exactly representable."""
    amount = value if isinstance(value, Decimal) else Decimal(value)
    return str(amount.quantize(ANCHOR_QUANTUM, rounding=ROUND_HALF_UP))


def anchor_levels(values: tuple[str, ...]) -> int:
    """How many anchor steps separate the smallest and largest score of an item."""
    amounts = sorted(Decimal(value) for value in values)
    return len({amount for amount in amounts})


def vote_seed_material(packet_digest: str, vote_index: int) -> bytes:
    """Deterministic per-vote seed material; recorded so a replay uses the same seed."""
    return f"judge-vote/{packet_digest}/{vote_index}".encode()


def judge_record_bytes(document: BaseModel) -> bytes:
    """Canonical bytes of a stored judge *record*, including its recording timestamp.

    ``canonical_document_bytes`` deliberately drops non-semantic fields so a content digest ignores
    recording detail. A stored vote or adjudication is not a content digest: it is the record a
    reviewer re-reads, so it keeps ``created_at``/``decided_at`` under the same envelope and digest
    profile.

    Any strict contract document qualifies: a ``JudgePacket`` is a stored record too, even though it
    is not a ``JudgeModel``.
    """
    kind = getattr(document, "kind", None)
    schema_version = getattr(document, "schema_version", 1)
    if not isinstance(kind, str):
        raise IdentityLeak("judge record is missing its document kind")
    return canonical_json_bytes(
        canonical_envelope(kind, document.model_dump(mode="json"), schema_version)
    )


def derived_judge_id(*parts: str) -> str:
    """A deterministic UUIDv4-shaped identifier derived from judge material.

    Packet, adjudication and calibration-set identities have to be reproducible from their content
    so a replay produces the same names. This lives with the judge contracts on purpose: judge
    identity must not depend on another package's id helper moving or changing.
    """
    raw = bytearray(hashlib.sha256(canonical_json_bytes(list(parts))).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40  # version 4
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    text = bytes(raw).hex()
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


class RubricAnchor(JudgeModel):
    """One declared score with the meaning that makes it checkable."""

    kind: Literal["rubric_anchor"] = "rubric_anchor"
    value: Decimal6
    meaning: Annotated[str, Field(min_length=8, max_length=512)]

    @field_validator("value")
    @classmethod
    def declared_anchor_only(cls, value: str) -> str:
        if value not in {"0.000000", "0.500000", "1.000000"}:
            raise ValueError("pilot rubric anchors are 0, 0.5 and 1")
        return value


class RubricItem(JudgeModel):
    """A residual quality item: no reliable executable or static check owns it."""

    kind: Literal["rubric_item"] = "rubric_item"
    item_id: ItemId
    dimension: ScoreDimension
    question: Annotated[str, Field(min_length=8, max_length=512)]
    residual_reason: Annotated[str, Field(min_length=16, max_length=512)]
    anchors: tuple[RubricAnchor, ...] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def residual_item_is_declarable(self) -> RubricItem:
        if self.dimension not in RESIDUAL_DIMENSIONS:
            raise ValueError("judges only score residual quality, idiom and robustness items")
        values = {anchor.value for anchor in self.anchors}
        if len(values) != len(self.anchors):
            raise ValueError("rubric anchor values must be unique within an item")
        if values != {"0.000000", "0.500000", "1.000000"}:
            raise ValueError("pilot items declare the full 0/0.5/1 anchor set")
        if [anchor.value for anchor in self.anchors] != sorted(values):
            raise ValueError("rubric anchors must be declared in ascending order")
        return self


class JudgeRubric(ContractModel):
    """The frozen rubric. Its digest is part of every packet and every result."""

    schema_version: Literal[1] = 1
    kind: Literal["judge_rubric"] = "judge_rubric"
    rubric_id: Slug
    version: Annotated[int, Field(ge=1)]
    status: Literal["frozen_pilot", "frozen"] = "frozen_pilot"
    languages: tuple[str, ...] = Field(min_length=1, max_length=8)
    items: tuple[RubricItem, ...] = Field(min_length=1, max_length=MAX_ITEMS)
    source: Annotated[str, Field(min_length=8, max_length=512)]
    prompt_template_version: Annotated[str, Field(min_length=3, max_length=64)]

    @field_validator("languages")
    @classmethod
    def known_languages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(value not in {"python", "rust"} for value in values):
            raise ValueError("pilot judge languages are python and rust")
        if len(set(values)) != len(values):
            raise ValueError("rubric languages must be unique")
        return values

    @model_validator(mode="after")
    def item_ids_are_unique(self) -> JudgeRubric:
        identifiers = [item.item_id for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("rubric item ids must be unique")
        return self

    def digest(self) -> str:
        return sha256_bytes(canonical_document_bytes(self))

    def item(self, item_id: str) -> RubricItem:
        for entry in self.items:
            if entry.item_id == item_id:
                return entry
        raise VoteRejected("unknown_item", f"rubric has no item {item_id}")

    def anchors_for(self, item_id: str) -> tuple[str, ...]:
        return tuple(anchor.value for anchor in self.item(item_id).anchors)


class PanelInference(JudgeModel):
    """Fixed inference controls. Changing one is a new panel version.

    Durations are integer milliseconds because the canonical profile has no floating-point
    numbers: a panel whose digest cannot be computed is a panel nobody can prove.
    """

    kind: Literal["panel_inference"] = "panel_inference"
    temperature: Literal["0.000000"] = "0.000000"
    max_output_tokens: Annotated[int, Field(ge=64, le=8192)]
    response_schema_name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")]
    request_timeout_ms: Annotated[int, Field(ge=5_000, le=600_000)] = 120_000


class PanelRepair(JudgeModel):
    """Bounded schema-repair recovery for one logical vote (Technical Spec 15.2)."""

    kind: Literal["panel_repair"] = "panel_repair"
    replacement_deliveries_per_vote: Annotated[int, Field(ge=0, le=2)]
    instruction_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")]

    @model_validator(mode="after")
    def repair_is_bounded(self) -> PanelRepair:
        if self.replacement_deliveries_per_vote > 2:
            raise ValueError("at most two replacement deliveries per vote are permitted")
        return self


class PanelDisagreement(JudgeModel):
    """Operational review defaults. Only a new policy version may change them."""

    kind: Literal["panel_disagreement"] = "panel_disagreement"
    anchor_spread_levels: Annotated[int, Field(ge=2, le=2)] = 2
    conflicting_facts: bool = True
    unsupported_citations: bool = True
    untrusted_comment_only: bool = True
    audit_sample_percent: Annotated[int, Field(ge=0, le=100)] = 10
    audit_seed: Annotated[str, Field(min_length=1, max_length=64)] = "judge-audit-v1"


class JudgePanel(ContractModel):
    """One fixed panel: judge identity, three votes, no tools, blinded fields."""

    schema_version: Literal[1] = 1
    kind: Literal["judge_panel"] = "judge_panel"
    panel_id: Slug
    version: Annotated[int, Field(ge=1)]
    status: Literal["frozen_pilot", "frozen"] = "frozen_pilot"
    effective_for_scoring: bool = False
    calibration_status: Literal["pending", "measured", "blocked"] = "pending"
    judge_model_config_id: EntityId | None = None
    judge_revision: Annotated[str, Field(min_length=1, max_length=128)] = "unprovisioned"
    provider_kind: Annotated[str, Field(min_length=3, max_length=32)] = "unprovisioned"
    inference: PanelInference
    votes_required: Annotated[int, Field(ge=PILOT_JUDGE_VOTES, le=PILOT_JUDGE_VOTES)]
    distinct_seeds: bool = True
    repair: PanelRepair
    disagreement: PanelDisagreement = PanelDisagreement()
    blinded_fields: tuple[str, ...] = BLINDED_FIELDS
    tools: tuple[str, ...] = ()
    excluded_candidate_model_config_ids: tuple[EntityId, ...] = ()
    rubric_id: Slug
    rubric_version: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def panel_is_fixed_and_tool_free(self) -> JudgePanel:
        if self.tools:
            raise ValueError("a judge has no tools")
        if sorted(self.blinded_fields) != sorted(BLINDED_FIELDS):
            raise ValueError("the panel must blind exactly the declared identity fields")
        if len(set(self.excluded_candidate_model_config_ids)) != len(
            self.excluded_candidate_model_config_ids
        ):
            raise ValueError("excluded candidate model configs must be unique")
        if self.effective_for_scoring and not self.excluded_candidate_model_config_ids:
            # The pilot candidate identities only exist once real endpoints are provisioned;
            # a scoring-active panel may not claim distinctness it cannot name.
            raise ValueError("a scoring-active panel must name the pilot candidates it excludes")
        return self

    def digest(self) -> str:
        return sha256_bytes(canonical_document_bytes(self))

    @property
    def provisioned(self) -> bool:
        return self.judge_model_config_id is not None and self.judge_revision != "unprovisioned"

    def require_access(self, cohort_candidates: tuple[str, ...]) -> EntityId:
        """Fail closed unless an approved judge configuration exists and is not a candidate."""
        if not self.provisioned or self.judge_model_config_id is None:
            raise PanelUnavailable(
                f"panel {self.panel_id} has no approved judge model configuration"
            )
        if self.judge_model_config_id in self.excluded_candidate_model_config_ids:
            raise PanelUnavailable("the judge must not be a pilot candidate model")
        if self.judge_model_config_id in cohort_candidates:
            raise PanelUnavailable("the judge must differ from every model in the cohort")
        return self.judge_model_config_id

    def max_deliveries_per_vote(self) -> int:
        return 1 + self.repair.replacement_deliveries_per_vote


class PacketSpan(JudgeModel):
    """One approved evidence span. A citation may only name an identifier defined here."""

    kind: Literal["packet_span"] = "packet_span"
    anchor_id: AnchorId
    origin: Literal["task_constraint", "candidate_code", "analyzer_evidence"]
    path: RelativePath | None = None
    start_line: Annotated[int, Field(ge=1)] | None = None
    end_line: Annotated[int, Field(ge=1)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=MAX_SPAN_CHARS)]
    content_digest: Digest

    @model_validator(mode="after")
    def lines_are_consistent(self) -> PacketSpan:
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("a span carries both start_line and end_line or neither")
        if self.start_line is not None and self.end_line is not None:
            if self.end_line < self.start_line:
                raise ValueError("a span ends at or after it starts")
        if self.origin == "task_constraint" and self.path is not None:
            raise ValueError("a task constraint span has no source path")
        return self


class UntrustedComment(JudgeModel):
    """A candidate comment carrying text that tries to address the judge.

    It is data. It is never in any item's evidence scope, so a vote that justifies an item only
    with comments is recorded as unsupported rather than accepted.
    """

    kind: Literal["untrusted_comment"] = "untrusted_comment"
    anchor_id: AnchorId
    span_anchor_id: AnchorId
    path: RelativePath
    text: Annotated[str, Field(min_length=1, max_length=MAX_COMMENT_CHARS)]
    trust: Literal["untrusted_data"] = "untrusted_data"
    instruction_attempt: bool


class PacketItem(JudgeModel):
    """One judged item as presented: question, anchors, and the evidence it may cite."""

    kind: Literal["packet_item"] = "packet_item"
    item_id: ItemId
    dimension: ScoreDimension
    question: Annotated[str, Field(min_length=8, max_length=512)]
    anchors: tuple[RubricAnchor, ...] = Field(min_length=2, max_length=3)
    in_scope_anchor_ids: tuple[AnchorId, ...] = Field(min_length=1, max_length=MAX_SPANS)

    @model_validator(mode="after")
    def scope_is_declared(self) -> PacketItem:
        if len(set(self.in_scope_anchor_ids)) != len(self.in_scope_anchor_ids):
            raise ValueError("an item's evidence scope must not repeat an anchor")
        return self


class JudgePacket(ContractModel):
    """The anonymized, bounded packet a judge sees (Technical Spec 15.1).

    There is no field for candidate identity, provider, rank, cost or expected score, and the
    packet asserts its own tools list is empty. Candidate comments are carried as untrusted data
    with deterministic instruction-attempt detection.
    """

    schema_version: Literal[1] = 1
    kind: Literal["judge_packet"] = "judge_packet"
    packet_id: EntityId
    rubric_id: Slug
    rubric_version: Annotated[int, Field(ge=1)]
    rubric_digest: Digest
    panel_id: Slug
    panel_version: Annotated[int, Field(ge=1)]
    panel_digest: Digest
    language: Literal["python", "rust"]
    packet_role: Literal["scored", "calibration"] = "scored"
    task_statement: Annotated[str, Field(min_length=8, max_length=4000)]
    constraints: tuple[Annotated[str, Field(min_length=4, max_length=1000)], ...] = ()
    items: tuple[PacketItem, ...] = Field(min_length=1, max_length=MAX_ITEMS)
    spans: tuple[PacketSpan, ...] = Field(min_length=1, max_length=MAX_SPANS)
    untrusted_comments: tuple[UntrustedComment, ...] = Field(max_length=MAX_COMMENTS)
    blinded_fields: tuple[str, ...] = BLINDED_FIELDS
    tools: tuple[str, ...] = ()
    withheld_fields: tuple[str, ...] = BLINDED_FIELDS
    excluded_evidence: tuple[str, ...] = (
        "expected_score",
        "correctness_gate",
        "measured_rank",
        "hidden_test_results",
    )

    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"packet_id"})

    @model_validator(mode="after")
    def packet_is_closed(self) -> JudgePacket:
        if self.tools:
            raise ValueError("a judge packet never exposes executable tools")
        if sorted(self.blinded_fields) != sorted(BLINDED_FIELDS):
            raise ValueError("every packet declares the blinded identity fields")
        span_ids = [span.anchor_id for span in self.spans]
        if len(set(span_ids)) != len(span_ids):
            raise ValueError("packet span anchors must be unique")
        known = set(span_ids)
        item_ids = [item.item_id for item in self.items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("packet item ids must be unique")
        for item in self.items:
            unknown = [anchor for anchor in item.in_scope_anchor_ids if anchor not in known]
            if unknown:
                raise ValueError("an item's evidence scope must exist in the packet")
            if any(anchor.startswith("cmt-") for anchor in item.in_scope_anchor_ids):
                raise ValueError("untrusted candidate comments are never in an item's scope")
        comment_ids = [comment.anchor_id for comment in self.untrusted_comments]
        if len(set(comment_ids)) != len(comment_ids):
            raise ValueError("comment anchors must be unique")
        if any(anchor in known for anchor in comment_ids):
            raise ValueError("a comment anchor cannot also be a span anchor")
        for comment in self.untrusted_comments:
            if comment.span_anchor_id not in known:
                raise ValueError("a comment must name the span that carries it")
        return self

    def digest(self) -> str:
        """Content digest: the packet minus its own self-referential identity."""
        return sha256_bytes(canonical_document_bytes(self))

    def canonical_bytes(self) -> bytes:
        """Full canonical document bytes: the packet exactly as stored and re-read.

        ``digest()`` excludes ``packet_id`` so the identity can be derived from the content, but the
        stored record must round-trip, so it keeps every field. Reading a packet back therefore
        means parsing the bytes and recomputing ``digest()``, not hashing the file.
        """
        return judge_record_bytes(self)

    def item(self, item_id: str) -> PacketItem:
        for entry in self.items:
            if entry.item_id == item_id:
                return entry
        raise VoteRejected("unknown_item", f"packet has no item {item_id}")

    def span(self, anchor_id: str) -> PacketSpan | None:
        for entry in self.spans:
            if entry.anchor_id == anchor_id:
                return entry
        return None

    @property
    def comment_anchor_ids(self) -> frozenset[str]:
        return frozenset(comment.anchor_id for comment in self.untrusted_comments)

    @property
    def instruction_attempt_count(self) -> int:
        return sum(1 for comment in self.untrusted_comments if comment.instruction_attempt)


def _field_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        names: set[str] = set()
        for key, child in value.items():
            names.add(key)
            names |= _field_names(child)
        return names
    if isinstance(value, (list, tuple)):
        nested: set[str] = set()
        for child in value:
            nested |= _field_names(child)
        return nested
    return set()


def assert_no_identity_leak(packet: JudgePacket, withheld_values: tuple[str, ...]) -> None:
    """Prove the rendered packet omits concrete identity, rank and cost facts.

    Two narrow checks. The packet's own field names must not include an identity, rank or cost
    field, and the concrete identity strings the caller withheld must not appear anywhere in the
    packet text. Span *content* is deliberately not scanned for field-like words: candidate code
    legitimately contains its own JSON, and a substring rule that fires on it would be a rule no
    reviewer would honour.
    """
    structure = packet.model_dump(
        mode="json", exclude={"blinded_fields", "withheld_fields", "excluded_evidence"}
    )
    names = _field_names(structure)
    for token in _FORBIDDEN_NAME_TOKENS:
        if token in names:
            raise IdentityLeak(f"packet declares the withheld field {token}")
    blob = packet.canonical_bytes().decode("utf-8")
    for value in withheld_values:
        text = value.strip()
        if len(text) >= 3 and text in blob:
            raise IdentityLeak("packet text discloses a withheld candidate value")


class VoteCitation(JudgeModel):
    kind: Literal["vote_citation"] = "vote_citation"
    anchor_id: AnchorId
    note: Annotated[str, Field(max_length=MAX_NOTE_CHARS)] = ""


class JudgeFact(JudgeModel):
    """One factual claim a vote makes about the candidate, used for contradiction checks."""

    kind: Literal["judge_fact"] = "judge_fact"
    subject: Annotated[str, Field(min_length=3, max_length=MAX_CLAIM_SUBJECT_CHARS)]
    stance: Literal["supports", "opposes"]
    citation_anchor_ids: tuple[AnchorId, ...] = Field(max_length=MAX_CITATIONS)


class ItemVote(JudgeModel):
    kind: Literal["item_vote"] = "item_vote"
    item_id: ItemId
    score: Decimal6
    citations: tuple[VoteCitation, ...] = Field(min_length=1, max_length=MAX_CITATIONS)
    rationale: Annotated[str, Field(min_length=8, max_length=MAX_RATIONALE_CHARS)]
    uncertainty: tuple[
        Literal[
            "ambiguous_evidence",
            "incomplete_evidence",
            "alternative_valid_interpretation",
            "stylistic_difference",
        ],
        ...,
    ] = ()
    facts: tuple[JudgeFact, ...] = Field(max_length=MAX_FACTS)

    @property
    def citation_anchor_ids(self) -> tuple[str, ...]:
        return tuple(citation.anchor_id for citation in self.citations)


class JudgeVote(ContractModel):
    """One accepted logical vote. Every field the review workflow needs is retained here."""

    schema_version: Literal[1] = 1
    kind: Literal["judge_vote"] = "judge_vote"
    packet_id: EntityId
    packet_digest: Digest
    rubric_digest: Digest
    panel_digest: Digest
    judge_revision: Annotated[str, Field(min_length=1, max_length=128)]
    vote_index: Annotated[int, Field(ge=0, le=15)]
    seed: Annotated[int, Field(ge=0, le=18_446_744_073_709_551_615)] | None = None
    items: tuple[ItemVote, ...] = Field(min_length=1, max_length=MAX_ITEMS)
    raw_response_digest: Digest
    raw_response_artifact_id: EntityId | None = None
    normalized_artifact_id: EntityId | None = None
    call_delivery_id: EntityId | None = None
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def items_are_unique(self) -> JudgeVote:
        identifiers = [item.item_id for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("a vote scores each item once")
        return self

    def item(self, item_id: str) -> ItemVote:
        for entry in self.items:
            if entry.item_id == item_id:
                return entry
        raise VoteRejected("unknown_item", f"vote has no item {item_id}")


class InvalidVoteReason(StrEnum):
    NOT_JSON = "not_json"
    SCHEMA_VIOLATION = "schema_violation"
    ANCHOR_NOT_IN_SET = "anchor_not_in_set"
    MISSING_ITEM = "missing_item"
    UNKNOWN_ITEM = "unknown_item"
    CITATION_NOT_IN_PACKET = "citation_not_in_packet"
    EXTRA_INSTRUCTION_ACTION = "extra_instruction_action"
    PANEL_MISMATCH = "panel_mismatch"
    PACKET_MISMATCH = "packet_mismatch"
    EMPTY_RESPONSE = "empty_response"


class JudgeDelivery(JudgeModel):
    """One dispatched judge delivery. Retained whatever its outcome (Technical Spec 15.2)."""

    kind: Literal["judge_delivery"] = "judge_delivery"
    packet_id: EntityId
    vote_index: Annotated[int, Field(ge=0, le=15)]
    delivery_index: Annotated[int, Field(ge=0, le=7)]
    status: Literal["valid", "invalid", "transport_failure"]
    invalid_reason: InvalidVoteReason | None = None
    detail: Annotated[str, Field(max_length=400)] = ""
    judge_revision: Annotated[str, Field(min_length=1, max_length=128)] = "unprovisioned"
    seed: Annotated[int, Field(ge=0, le=18_446_744_073_709_551_615)] | None = None
    seed_supported: bool = False
    repair_instruction_id: str | None = None
    raw_response_digest: Digest | None = None
    raw_response_artifact_id: EntityId | None = None
    call_delivery_id: EntityId | None = None
    call_intent_id: EntityId | None = None
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def invalid_deliveries_are_explained(self) -> JudgeDelivery:
        if self.status == "invalid" and self.invalid_reason is None:
            raise ValueError("an invalid delivery records why it was rejected")
        if self.status != "invalid" and self.invalid_reason is not None:
            raise ValueError("only an invalid delivery carries an invalid reason")
        return self


class ReviewTrigger(StrEnum):
    ANCHOR_SPREAD = "anchor_spread"
    CONFLICTING_FACTS = "conflicting_facts"
    UNSUPPORTED_CITATION = "unsupported_citation"
    UNTRUSTED_COMMENT_ONLY = "untrusted_comment_only"
    MISSING_REQUIRED_VOTE = "missing_required_vote"
    INVALID_VOTE_RECOVERY_EXHAUSTED = "invalid_vote_recovery_exhausted"
    AUDIT_SAMPLE = "audit_sample"


class ItemOutcome(JudgeModel):
    """The per-item result a scorer consumes: a mean, a status and its triggers.

    ``mean_score`` is None unless every required vote is valid, so a two-vote average can never
    reach a scorecard through this type.
    """

    kind: Literal["item_outcome"] = "item_outcome"
    item_id: ItemId
    dimension: ScoreDimension
    status: Literal["ready", "needs_review", "infra_blocked"]
    mean_score: Decimal6 | None
    vote_scores: tuple[Decimal6, ...]
    vote_count: Annotated[int, Field(ge=0, le=15)]
    required_votes: Annotated[int, Field(ge=1, le=PILOT_JUDGE_VOTES)]
    spread: Decimal6 | None
    triggers: tuple[ReviewTrigger, ...] = ()
    source: Literal["judge_votes", "adjudication"] = "judge_votes"
    adjudication_id: EntityId | None = None
    reason: Annotated[str, Field(max_length=400)] = ""

    @model_validator(mode="after")
    def mean_requires_every_vote(self) -> ItemOutcome:
        if self.mean_score is not None and self.vote_count != self.required_votes:
            raise ValueError(
                "a mean score requires exactly the required votes, no more and no fewer"
            )
        if self.mean_score is not None and not (
            Decimal(0) <= Decimal(self.mean_score) <= Decimal(1)
        ):
            raise ValueError("an item mean must stay inside the anchor range")
        if self.source == "adjudication" and self.adjudication_id is None:
            raise ValueError("an adjudicated item names its adjudication")
        return self


class JudgementResult(ContractModel):
    """The frozen per-packet judge result: every delivery, every vote, every item outcome.

    ``report_digest`` is this document's own digest, so it takes no part in the canonical form the
    digest is computed over: a stored result reproduces its registered digest whether or not the
    field was already set when the digest was taken.
    """

    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"report_digest"})

    kind: Literal["judgement_result"] = "judgement_result"
    schema_version: Literal[1] = 1
    packet_id: EntityId
    packet_digest: Digest
    rubric_digest: Digest
    panel_id: Slug
    panel_version: Annotated[int, Field(ge=1)]
    panel_digest: Digest
    evaluation_id: EntityId | None = None
    cohort_digest: Digest | None = None
    status: Literal["ready", "needs_review", "infra_blocked"]
    votes_required: Annotated[int, Field(ge=1, le=PILOT_JUDGE_VOTES)]
    items: tuple[ItemOutcome, ...] = Field(min_length=1, max_length=MAX_ITEMS)
    deliveries: tuple[JudgeDelivery, ...] = Field(max_length=64)
    valid_vote_indexes: tuple[int, ...] = ()
    untrusted_comment_count: Annotated[int, Field(ge=0)] = 0
    report_digest: Digest | None = None

    @model_validator(mode="after")
    def result_is_consistent(self) -> JudgementResult:
        item_ids = [item.item_id for item in self.items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("a result reports each item once")
        if len(set(self.valid_vote_indexes)) != len(self.valid_vote_indexes):
            raise ValueError("valid vote indexes must be unique")
        if self.status == "ready":
            if any(item.status != "ready" for item in self.items):
                raise ValueError("a ready result has no outstanding item")
            if len(self.valid_vote_indexes) < self.votes_required:
                raise ValueError("a ready result needs every required vote")
        return self

    def item(self, item_id: str) -> ItemOutcome:
        for entry in self.items:
            if entry.item_id == item_id:
                return entry
        raise VoteRejected("unknown_item", f"result has no item {item_id}")

    @property
    def infra_blocked(self) -> bool:
        return self.status == "infra_blocked"


class AdjudicationDecision(JudgeModel):
    """A reviewer's recorded override of one item score.

    It cites the evidence it used, names every vote it read and never deletes a vote.
    ``supersedes_id`` names the decision this one replaces, so the referenced row is the superseded
    one; both stay readable.
    """

    kind: Literal["adjudication_decision"] = "adjudication_decision"
    packet_id: EntityId
    packet_digest: Digest
    item_id: ItemId
    rubric_id: Slug
    rubric_version: Annotated[int, Field(ge=1)]
    rubric_digest: Digest
    panel_digest: Digest
    score: Decimal6
    cited_anchor_ids: tuple[AnchorId, ...] = Field(min_length=1, max_length=MAX_CITATIONS)
    reason: Annotated[str, Field(min_length=16, max_length=2000)]
    reviewed_vote_indexes: tuple[Annotated[int, Field(ge=0, le=15)], ...] = Field(min_length=1)
    reviewer_subject: Annotated[str, Field(min_length=1, max_length=255)]
    supersedes_id: EntityId | None = None
    decided_at: UtcTimestamp

    @model_validator(mode="after")
    def decision_is_traceable(self) -> AdjudicationDecision:
        if len(set(self.cited_anchor_ids)) != len(self.cited_anchor_ids):
            raise ValueError("an adjudication cites each anchor once")
        if len(set(self.reviewed_vote_indexes)) != len(self.reviewed_vote_indexes):
            raise ValueError("an adjudication names each reviewed vote once")
        return self


class InputSpan(JudgeModel):
    """One span of evidence offered to the packet builder, before anchor assignment."""

    kind: Literal["input_span"] = "input_span"
    origin: Literal["candidate_code", "analyzer_evidence"]
    path: RelativePath
    start_line: Annotated[int, Field(ge=1)] | None = None
    end_line: Annotated[int, Field(ge=1)] | None = None
    text: Annotated[str, Field(min_length=1, max_length=MAX_SPAN_CHARS)]


class InputComment(JudgeModel):
    """A candidate comment offered as untrusted data."""

    kind: Literal["input_comment"] = "input_comment"
    path: RelativePath
    text: Annotated[str, Field(min_length=1, max_length=MAX_COMMENT_CHARS)]


class JudgePacketInput(ContractModel):
    """Everything a packet may be built from, and nothing about who produced the code.

    The evaluation stage produces this document. It has no field for candidate identity, cost or
    rank, so those cannot reach the packet even by accident.
    """

    kind: Literal["judge_packet_input"] = "judge_packet_input"
    schema_version: Literal[1] = 1
    language: Literal["python", "rust"]
    task_statement: Annotated[str, Field(min_length=8, max_length=4000)]
    constraints: tuple[Annotated[str, Field(min_length=4, max_length=1000)], ...] = Field(
        max_length=24
    )
    item_ids: tuple[ItemId, ...] = Field(min_length=1, max_length=MAX_ITEMS)
    spans: tuple[InputSpan, ...] = Field(min_length=1, max_length=MAX_SPANS)
    comments: tuple[InputComment, ...] = Field(max_length=MAX_COMMENTS)
    packet_role: Literal["scored", "calibration"] = "scored"
    withheld_values: tuple[Annotated[str, Field(min_length=3, max_length=255)], ...] = Field(
        max_length=16
    )

    @model_validator(mode="after")
    def input_is_closed(self) -> JudgePacketInput:
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("a packet input names each rubric item once")
        return self


class ReviewQueueEntry(JudgeModel):
    """What a reviewer is shown for one packet: the anonymized packet, every vote, the triggers.

    It carries no candidate identity, provider, rank or cost, so model identity stays hidden where
    feasible without a second code path.
    """

    kind: Literal["review_queue_entry"] = "review_queue_entry"
    packet: JudgePacket
    result_status: Literal["ready", "needs_review", "infra_blocked"]
    triggers_by_item: dict[str, tuple[str, ...]]
    votes: tuple[JudgeVote, ...]
    deliveries: tuple[JudgeDelivery, ...]
    adjudications: tuple[AdjudicationDecision, ...] = ()
    identity_withheld: Literal[True] = True

    @property
    def needs_decision(self) -> tuple[str, ...]:
        return tuple(
            item_id for item_id, triggers in sorted(self.triggers_by_item.items()) if triggers
        )


def adjudication_identity(decision: AdjudicationDecision) -> str:
    """The content-derived identity of one reviewer decision.

    Using it as the stored primary key makes a replayed decision the same row rather than a second
    copy, and makes the item result's ``adjudication_id`` verifiable from the decision document
    alone.
    """
    return derived_judge_id(
        "judge-adjudication",
        decision.packet_id,
        decision.item_id,
        decision.decided_at,
        decision.reviewer_subject,
        decision.score,
        decision.reason,
    )


class JudgeCohort(ContractModel):
    """A comparison cohort judged by exactly one panel.

    If the panel changes — for example because the judge becomes a candidate — the cohort needs a
    new ``evaluation_version``; the old packets keep their original panel digest.
    """

    schema_version: Literal[1] = 1
    kind: Literal["judge_cohort"] = "judge_cohort"
    cohort_id: Slug
    evaluation_version: Annotated[int, Field(ge=1)]
    panel_id: Slug
    panel_digest: Digest
    rubric_digest: Digest
    candidate_model_config_ids: tuple[EntityId, ...] = Field(min_length=1)
    prompt_digest: Digest | None = None

    @model_validator(mode="after")
    def one_panel_per_cohort(self) -> JudgeCohort:
        if len(set(self.candidate_model_config_ids)) != len(self.candidate_model_config_ids):
            raise ValueError("cohort candidates must be unique")
        return self

    def digest(self) -> str:
        return sha256_bytes(canonical_document_bytes(self))

    def require_same_panel(self, panel_digest: str) -> None:
        if panel_digest != self.panel_digest:
            raise PanelChangeRequiresNewCohort(
                f"cohort {self.cohort_id} is frozen on panel digest {self.panel_digest}"
            )

    def require_distinct_judge(self, panel: JudgePanel) -> EntityId:
        config_id = panel.require_access(self.candidate_model_config_ids)
        if config_id not in self.candidate_model_config_ids:
            return config_id
        raise PanelUnavailable("the judge must not be a model in this cohort")
