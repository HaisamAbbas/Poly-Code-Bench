"""Repository Q&A: answer evaluation with repository evidence (Technical Spec 17.3).

Pure data and rules only. The grading module (evaluation), the judge packet builder and the
admitted Q&A fixtures all build on these types so that what the oracle freezes, what the metrics
compute and what the reports claim agree by construction.

The measures are named exactly as the specification names them:

* **Fact recall is the native-style measure.** ``100 * sum(weight * credit) / sum(weight)`` over
  atomic facts, credit from the fixed three-vote entailment protocol (or an adjudication). Missed
  facts are zero, repeated facts add nothing, an empty answer recalls zero, and an answer that
  states a factual contradiction never gets credit for that fact.
* **The native aggregation is preserved separately.** ``native_presence_aggregation`` records the
  deterministic presence check (statement or accepted paraphrase substring) with the same formula,
  so a difference between aggregations stays visible instead of being silently replaced.
* **Citation validity, grounding and unsupported/contradicted claims are separately named
  PolyCodeBench adaptations**, never DeepCodeBench metrics. Incomplete extraction or verification
  makes those diagnostics ``unknown`` (``None``) - it is never recorded as zero.
* **No six code dimensions and no invented answer-quality index.** Q&A output is prose with
  citations; ``QaMetrics.code_dimensions`` is always ``not_applicable``.

Citations are validated against the pinned base snapshot only: path present, span inside the
file, and the recorded ``base_digest`` equal to the pinned digest (PCB-27-1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.models import ContractModel, Slug
from polycodebench_core.solve_contracts import MUTATING_TOOLS, TOOL_NAMES, SolveError

Decimal6 = Annotated[str, Field(pattern=r"^-?\d+\.\d{6}$")]
DigestStr = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

#: Q&A solving may retrieve and nothing else (Technical Spec 17.2: editing off).
QA_TOOL_NAMES = ("list_files", "read_file", "search")

CLAIM_EXTRACTION_POLICY = "claim-extraction-v1"
MAX_CLAIMS = 40
MAX_CITATIONS_PER_CLAIM = 5
MAX_ANSWER_CHARS = 8_000
MAX_PARAPHRASES = 10
_RETRIEVAL_TOOLS = set(QA_TOOL_NAMES)

_NUMBERS = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE = re.compile(r"[.!?]\s+|\n+")


class QaContractError(SolveError):
    """A Q&A submission or oracle violates its frozen contract."""

    code = "QA_CONTRACT_VIOLATION"
    status_code = 422


class QaSubmissionInvalid(QaContractError):
    """The answer envelope is missing, malformed or ambiguous. Never repaired by a model."""

    code = "QA_SUBMISSION_INVALID"


# --------------------------------------------------------------------------- pinned inputs


class QaTaskInputs(ContractModel):
    """The pinned inputs of one Q&A task: question and repository snapshot identity."""

    kind: Literal["qa_task_inputs"] = "qa_task_inputs"
    question_id: Slug
    question: str = Field(min_length=8, max_length=4_000)
    base_digest: DigestStr
    snapshot_paths: tuple[str, ...] = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def paths_are_unique(self) -> QaTaskInputs:
        if len(set(self.snapshot_paths)) != len(self.snapshot_paths):
            raise ValueError("snapshot paths must be unique")
        return self


class RetrievalRecord(ContractModel):
    """One retrieval the solver made: context and truncation are logged (PCB-27-1)."""

    kind: Literal["retrieval_record"] = "retrieval_record"
    event_seq: int
    tool_name: str
    status: Literal["ok", "error"]
    truncated: bool
    original_bytes: int
    returned_bytes: int
    artifact_id: str | None = None


def validate_qa_protocol_tools(allowed_tools: Sequence[str]) -> None:
    """Q&A solving is read/search-only: any mutating tool is a protocol violation."""
    unknown = sorted(set(allowed_tools) - set(TOOL_NAMES))
    if unknown:
        raise QaContractError(f"unknown tools in a Q&A protocol: {unknown}")
    editing = sorted(set(allowed_tools) & set(MUTATING_TOOLS))
    if editing:
        raise QaContractError(f"editing tools are disabled for Q&A protocols: {editing}")
    outside = sorted(set(allowed_tools) - _RETRIEVAL_TOOLS)
    if outside:
        raise QaContractError(f"Q&A protocols allow retrieval tools only: {outside}")


def retrieval_records(
    results: Sequence[Mapping[str, Any]],
) -> tuple[RetrievalRecord, ...]:
    """The frozen retrieval log view of tool results: context and truncation kept as recorded."""
    records: list[RetrievalRecord] = []
    for entry in results:
        name = str(entry.get("name", ""))
        if name not in _RETRIEVAL_TOOLS:
            raise QaContractError(f"non-retrieval tool in a Q&A log: {name}")
        records.append(
            RetrievalRecord(
                schema_version=1,
                event_seq=int(entry.get("event_seq", 0)),
                tool_name=name,
                status=str(entry.get("status", "ok")),  # type: ignore[arg-type]
                truncated=bool(entry.get("truncated", False)),
                original_bytes=int(entry.get("original_bytes", 0)),
                returned_bytes=int(entry.get("returned_bytes", 0)),
                artifact_id=entry.get("artifact_id"),
            )
        )
    return tuple(records)


# ---------------------------------------------------------------------------------- oracle


class CodeSpan(ContractModel):
    """A verifying code span, tied to the pinned base snapshot."""

    kind: Literal["code_span"] = "code_span"
    path: str = Field(min_length=1, max_length=512)
    start_line: Annotated[int, Field(strict=True, ge=1)]
    end_line: Annotated[int, Field(strict=True, ge=1)]
    base_digest: DigestStr

    @model_validator(mode="after")
    def ordered(self) -> CodeSpan:
        if self.end_line < self.start_line:
            raise ValueError("a code span ends before it starts")
        return self


class AtomicFact(ContractModel):
    """One ground-truth fact with its weight, accepted paraphrases and verifying spans."""

    kind: Literal["atomic_fact"] = "atomic_fact"
    fact_id: Slug
    weight: Annotated[int, Field(strict=True, ge=1, le=1_000)] = 1
    statement: str = Field(min_length=8, max_length=400)
    accepted_paraphrases: tuple[str, ...] = Field(max_length=MAX_PARAPHRASES)
    verifying_spans: tuple[CodeSpan, ...] = Field(min_length=1, max_length=8)
    exact_values: tuple[str, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def coherent(self) -> AtomicFact:
        if len(set(self.accepted_paraphrases)) != len(self.accepted_paraphrases):
            raise ValueError("accepted paraphrases must be unique")
        if len(set(self.exact_values)) != len(self.exact_values):
            raise ValueError("exact values must be unique")
        return self


class QaOracle(ContractModel):
    """The hidden oracle: versioned atomic facts over one pinned repository snapshot."""

    kind: Literal["qa_oracle"] = "qa_oracle"
    oracle_id: Slug
    version: Annotated[int, Field(strict=True, ge=1)]
    base_digest: DigestStr
    facts: tuple[AtomicFact, ...] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def coherent(self) -> QaOracle:
        identifiers = [fact.fact_id for fact in self.facts]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("fact ids must be unique")
        for fact in self.facts:
            for span in fact.verifying_spans:
                if span.base_digest != self.base_digest:
                    raise ValueError("verifying spans are tied to the oracle's base digest")
        return self

    @property
    def digest(self) -> str:
        return canonical_document_digest(self)

    def total_weight(self) -> int:
        return sum(fact.weight for fact in self.facts)


# ---------------------------------------------------------------------------------- answer


class Citation(ContractModel):
    """A file span the answer cites. Valid only against the pinned base snapshot."""

    kind: Literal["citation"] = "citation"
    path: str = Field(min_length=1, max_length=512)
    start_line: Annotated[int, Field(strict=True, ge=1)]
    end_line: Annotated[int, Field(strict=True, ge=1)]
    base_digest: DigestStr

    @model_validator(mode="after")
    def ordered(self) -> Citation:
        if self.end_line < self.start_line:
            raise ValueError("a citation ends before it starts")
        return self


class AnswerClaim(ContractModel):
    """One material claim of the answer with the citations asserted for it."""

    kind: Literal["answer_claim"] = "answer_claim"
    text: str = Field(min_length=4, max_length=400)
    citations: tuple[Citation, ...] = Field(max_length=MAX_CITATIONS_PER_CLAIM)


class QaAnswer(ContractModel):
    """The structured Q&A submission: prose answer plus claim-level citations."""

    kind: Literal["qa_answer"] = "qa_answer"
    answer_text: str = Field(min_length=0, max_length=MAX_ANSWER_CHARS)
    claims: tuple[AnswerClaim, ...] = Field(max_length=MAX_CLAIMS)

    @model_validator(mode="after")
    def claims_unique(self) -> QaAnswer:
        texts = [claim.text for claim in self.claims]
        if len(set(texts)) != len(texts):
            raise ValueError("the answer asserts each material claim once")
        return self

    @property
    def digest(self) -> str:
        return canonical_document_digest(self)


_ENVELOPE_KEYS = {"answer", "claims"}
_CLAIM_KEYS = {"text", "citations"}
_CITATION_KEYS = {"path", "start_line", "end_line", "base_digest"}
_FENCE_FULL = re.compile(r"```[ \t]*[A-Za-z0-9_+.#-]*[ \t]*\n((?:(?!```).)*)\n?```", re.S)


def parse_qa_answer(text: str, *, base_digest: str) -> QaAnswer:
    """Parse the frozen answer envelope. Invalid or ambiguous submissions are refused, never
    repaired and never chosen between (Technical Spec 9.4)."""
    import json

    stripped = text.strip()
    fenced = _FENCE_FULL.fullmatch(stripped)
    if fenced is not None:
        bodies = [fenced.group(1)]
    else:
        if "```" in stripped:
            raise QaSubmissionInvalid("the answer envelope is ambiguous")
        bodies = [stripped]
    for body in bodies:
        try:
            document = json.loads(body)
        except ValueError as error:
            raise QaSubmissionInvalid(f"the answer envelope is not JSON: {error}") from error
        if not isinstance(document, dict) or set(document) != _ENVELOPE_KEYS:
            raise QaSubmissionInvalid("the answer envelope must have exactly answer and claims")
        claims: list[AnswerClaim] = []
        raw_claims = document["claims"]
        if not isinstance(raw_claims, list):
            raise QaSubmissionInvalid("claims must be a list")
        for entry in raw_claims:
            if not isinstance(entry, dict) or set(entry) != _CLAIM_KEYS:
                raise QaSubmissionInvalid("each claim has exactly text and citations")
            citations: list[Citation] = []
            raw_citations = entry["citations"]
            if not isinstance(raw_citations, list):
                raise QaSubmissionInvalid("citations must be a list")
            for raw_citation in raw_citations:
                if not isinstance(raw_citation, dict) or set(raw_citation) != _CITATION_KEYS:
                    raise QaSubmissionInvalid("each citation has path, span and base digest")
                if raw_citation["base_digest"] != base_digest:
                    raise QaSubmissionInvalid(
                        "citations must reference the pinned base snapshot digest"
                    )
                citations.append(
                    Citation(
                        schema_version=1,
                        path=str(raw_citation["path"]),
                        start_line=int(raw_citation["start_line"]),
                        end_line=int(raw_citation["end_line"]),
                        base_digest=str(raw_citation["base_digest"]),
                    )
                )
            claims.append(
                AnswerClaim(schema_version=1, text=str(entry["text"]), citations=tuple(citations))
            )
        if not isinstance(document["answer"], str):
            raise QaSubmissionInvalid("answer must be text")
        answer_text = document["answer"]
        haystack = " ".join(answer_text.split())
        for claim in claims:
            if " ".join(claim.text.split()) not in haystack:
                raise QaSubmissionInvalid("each claim must quote the answer text")
        return QaAnswer(schema_version=1, answer_text=answer_text, claims=tuple(claims))
    raise QaSubmissionInvalid("the answer envelope is unreadable")


# ------------------------------------------------------------------------- entailment credit


class EntailmentVotes(ContractModel):
    """The fixed three-vote entailment judgement of one text against one fact."""

    kind: Literal["entailment_votes"] = "entailment_votes"
    votes: tuple[Annotated[int, Field(strict=True, ge=0, le=1)], ...] = Field(
        min_length=1, max_length=3
    )
    adjudicated_credit: Decimal6 | None = None

    @model_validator(mode="after")
    def coherent(self) -> EntailmentVotes:
        if self.adjudicated_credit is not None and not (
            Decimal("0") <= Decimal(self.adjudicated_credit) <= Decimal("1")
        ):
            raise ValueError("an adjudicated entailment credit lies in [0, 1]")
        return self

    def credit(self) -> Decimal:
        """Mean of the 0/1 votes, or the adjudicated credit (Technical Spec 17.3)."""
        if self.adjudicated_credit is not None:
            return Decimal(self.adjudicated_credit)
        total = sum(self.votes)
        return Decimal(total) / Decimal(len(self.votes))


def quantize6(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN))


# ------------------------------------------------------------------- deterministic checks


def extract_material_claims(text: str) -> tuple[str, ...]:
    """Frozen claim extraction ``claim-extraction-v1``: deterministic, bounded, ordered."""
    claims: list[str] = []
    for sentence in _SENTENCE.split(text):
        cleaned = " ".join(sentence.split()).strip(".!?,;:")
        words = cleaned.split()
        if len(cleaned) < 4:
            continue
        if len(words) < 4 and not _NUMBERS.search(cleaned):
            continue
        claims.append(cleaned)
        if len(claims) >= MAX_CLAIMS:
            break
    return tuple(claims)


def native_presence(oracle: QaOracle, answer_text: str) -> dict[str, bool]:
    """The preserved native-style presence check: statement or accepted paraphrase substring."""
    haystack = " ".join(answer_text.lower().split())
    presence: dict[str, bool] = {}
    for fact in oracle.facts:
        needles = (fact.statement, *fact.accepted_paraphrases)
        presence[fact.fact_id] = any(
            " ".join(needle.lower().split()) in haystack for needle in needles
        )
    return presence


def native_presence_aggregation(oracle: QaOracle, answer_text: str) -> Decimal6:
    """Fact recall under the preserved native aggregation (presence), same formula, separate."""
    presence = native_presence(oracle, answer_text)
    weighted = sum(fact.weight * (1 if presence[fact.fact_id] else 0) for fact in oracle.facts)
    return quantize6(Decimal(100) * Decimal(weighted) / Decimal(oracle.total_weight()))


def contradicted_claims(oracle: QaOracle, claims: Sequence[str]) -> tuple[str, ...]:
    """Deterministic exact-value contradictions (``contradiction-v1``).

    A claim that shares at least two content words with a fact, where the fact has exact numeric
    values and the claim asserts different numbers, contradicts that fact.
    """
    return tuple(
        claim for claim in claims if any(_claim_contradicts(claim, fact) for fact in oracle.facts)
    )


def _claim_contradicts(claim: str, fact: AtomicFact) -> bool:
    fact_numbers = set(_NUMBERS.findall(" ".join(fact.exact_values)))
    if not fact_numbers:
        return False
    claim_numbers = set(_NUMBERS.findall(claim))
    if not claim_numbers or claim_numbers & fact_numbers:
        return False
    return len(set(_content_words(claim)) & _content_words(fact.statement)) >= 2


def _content_words(text: str) -> set[str]:
    return {
        word.strip(".,;:()[]{}\"'").lower()
        for word in text.split()
        if len(word.strip(".,;:()[]{}\"'")) >= 3
    }


def validate_citations(
    answer: QaAnswer,
    *,
    snapshot: Mapping[str, bytes],
    base_digest: str,
) -> tuple[tuple[str, ...], int]:
    """Citation validity against the pinned snapshot. Returns (invalid reasons, total citations)."""
    reasons: list[str] = []
    total = 0
    for claim in answer.claims:
        for citation in claim.citations:
            total += 1
            if citation.base_digest != base_digest:
                reasons.append(f"{citation.path}: digest does not match the pinned snapshot")
                continue
            data = snapshot.get(citation.path)
            if data is None:
                reasons.append(f"{citation.path}: not in the base snapshot")
                continue
            line_count = len(data.decode("utf-8", errors="replace").splitlines())
            if citation.end_line > line_count:
                reasons.append(
                    f"{citation.path}: span {citation.start_line}-{citation.end_line} "
                    + f"exceeds the snapshot file ({line_count} lines)"
                )
    return tuple(reasons), total


# --------------------------------------------------------------------------------- metrics


class FactCredit(ContractModel):
    """One fact's entailment credit and how it was decided."""

    kind: Literal["fact_credit"] = "fact_credit"
    fact_id: Slug
    weight: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    credit: Decimal6 | None
    contradicted: bool
    mentions: Annotated[int, Field(strict=True, ge=0, le=1_000)]
    source: Literal["entailment_votes", "adjudication", "missing", "contradiction"]


class QaMetrics(ContractModel):
    """Primary fact recall plus the separately named PolyCodeBench diagnostics."""

    kind: Literal["qa_metrics"] = "qa_metrics"
    fact_recall: Decimal6 | None
    fact_recall_status: Literal["complete", "incomplete"]
    native_presence_aggregation: Decimal6
    grounding_rate: Decimal6 | None
    unsupported_claims: Annotated[int, Field(strict=True, ge=0, le=MAX_CLAIMS)] | None
    contradicted_claims: Annotated[int, Field(strict=True, ge=0, le=MAX_CLAIMS)] | None
    unsupported_rate: Decimal6 | None
    contradicted_rate: Decimal6 | None
    claim_precision: Decimal6 | None
    invalid_citations: Annotated[int, Field(strict=True, ge=0, le=1_000)]
    code_dimensions: Literal["not_applicable"] = "not_applicable"


def weighted_fact_recall(oracle: QaOracle, credits: Mapping[str, Decimal | None]) -> Decimal | None:
    """100 * sum(w*c) / sum(w). Any unjudged fact makes the recall unknown, never partial."""
    total = Decimal(0)
    for fact in oracle.facts:
        credit = credits.get(fact.fact_id)
        if credit is None:
            return None
        total += Decimal(fact.weight) * credit
    return Decimal(100) * total / Decimal(oracle.total_weight())


def fact_credits(
    oracle: QaOracle,
    answer: QaAnswer,
    *,
    votes: Mapping[str, EntailmentVotes],
) -> tuple[FactCredit, ...]:
    """Per-fact credit: entailment mean or adjudication; contradiction forces zero."""
    claim_texts = _claim_texts(answer)
    empty = not answer.answer_text.strip() and not answer.claims
    credits: list[FactCredit] = []
    for fact in oracle.facts:
        judgment = votes.get(fact.fact_id)
        mentions = sum(
            1
            for text in (fact.statement, *fact.accepted_paraphrases)
            if " ".join(text.lower().split()) in _answer_haystack(answer)
        )
        source: Literal["entailment_votes", "adjudication", "missing", "contradiction"]
        credit: Decimal | None
        if empty:
            # An empty answer expresses nothing: the facts are missing, not unjudged
            # (Technical Spec 17.3: an empty answer recalls zero).
            source = "missing"
            credit = Decimal(0)
        elif judgment is not None and judgment.adjudicated_credit is not None:
            source = "adjudication"
            credit = judgment.credit()
        elif judgment is not None:
            source = "entailment_votes"
            credit = judgment.credit()
        else:
            source = "missing"
            credit = None
        contradicted = any(_claim_contradicts(claim, fact) for claim in claim_texts)
        if contradicted:
            source = "contradiction"
            credit = Decimal(0)
        credits.append(
            FactCredit(
                schema_version=1,
                fact_id=fact.fact_id,
                weight=fact.weight,
                credit=None if credit is None else quantize6(credit),
                contradicted=contradicted,
                mentions=mentions,
                source=source,
            )
        )
    return tuple(credits)


def _claim_texts(answer: QaAnswer) -> tuple[str, ...]:
    if answer.claims:
        return tuple(claim.text for claim in answer.claims)
    return extract_material_claims(answer.answer_text)


def _answer_haystack(answer: QaAnswer) -> str:
    return " ".join(answer.answer_text.lower().split())


def claim_precision(*, supported_claims: int, material_claims: int) -> Decimal | None:
    """Supported claims over material claims; undefined when the answer asserts nothing."""
    if material_claims == 0:
        return None
    return Decimal(100) * Decimal(supported_claims) / Decimal(material_claims)


def grounding_rate(*, supported: int, asserted: int) -> Decimal | None:
    """Fraction of asserted fact citations whose spans support the fact; unknown when incomplete."""
    if asserted == 0:
        return None
    return Decimal(100) * Decimal(supported) / Decimal(asserted)


def rate(count: int | None, total: int) -> Decimal | None:
    """Claim-level rate; unknown (None) when verification is incomplete, never a silent zero."""
    if count is None or total == 0:
        return None
    return Decimal(100) * Decimal(count) / Decimal(total)


__all__ = [
    "AnswerClaim",
    "AtomicFact",
    "Citation",
    "CodeSpan",
    "EntailmentVotes",
    "FactCredit",
    "MAX_ANSWER_CHARS",
    "MAX_CLAIMS",
    "QaAnswer",
    "QaContractError",
    "QaMetrics",
    "QaOracle",
    "QaSubmissionInvalid",
    "QaTaskInputs",
    "QA_TOOL_NAMES",
    "RetrievalRecord",
    "claim_precision",
    "contradicted_claims",
    "extract_material_claims",
    "fact_credits",
    "grounding_rate",
    "native_presence",
    "native_presence_aggregation",
    "parse_qa_answer",
    "quantize6",
    "rate",
    "retrieval_records",
    "validate_citations",
    "validate_qa_protocol_tools",
    "weighted_fact_recall",
]
