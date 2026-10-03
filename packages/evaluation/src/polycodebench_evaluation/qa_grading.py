"""Repository Q&A grading: entailment fact recall plus separately named diagnostics (17.3).

The aggregation rules are the specification's, implemented once here:

* fact credit = mean of the fixed three 0/1 entailment votes, or an adjudication; a deterministic
  factual contradiction forces zero credit for that fact;
* fact recall is the weighted native-style formula; the deterministic native presence
  aggregation is computed and preserved separately;
* grounding is the fraction of asserted fact citations whose spans support the fact under the
  same entailment rubric;
* unsupported/contradicted claim counts and rates cover additional material claims extracted by
  the frozen procedure; incomplete verification yields ``unknown`` (``None``), never zero;
* empty answers recall zero and leave claim precision undefined; no six code dimensions and no
  invented answer-quality index ever appear.

Entailment judging runs through the frozen rubric/panel pair
(``qa-entailment-rubric-v1``/``qa-entailment-panel-v1``) and the existing judge services; this
module is the only place that turns a ``JudgementResult`` into entailment votes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from polycodebench_core.judge_contracts import (
    InputSpan,
    JudgementResult,
    JudgePacketInput,
)
from polycodebench_core.models import ContractModel
from polycodebench_core.qa_contracts import (
    Citation,
    EntailmentVotes,
    FactCredit,
    QaAnswer,
    QaContractError,
    QaMetrics,
    QaOracle,
    claim_precision,
    contradicted_claims,
    extract_material_claims,
    fact_credits,
    grounding_rate,
    native_presence_aggregation,
    quantize6,
    rate,
    validate_citations,
    weighted_fact_recall,
)

QA_ENTAILMENT_ITEM = "entailment"
SUPPORT_THRESHOLD = Decimal("0.5")


class QaGradeReport(ContractModel):
    """One graded Q&A answer: primary recall, preserved native aggregation, named diagnostics."""

    kind: Literal["qa_grade_report"] = "qa_grade_report"
    oracle_digest: str
    answer_digest: str | None
    metrics: QaMetrics
    credits: tuple[FactCredit, ...]
    invalid_citations: tuple[str, ...]
    unsupported_claims: tuple[str, ...] = ()
    contradicted_claims: tuple[str, ...] = ()
    incomplete_verifications: tuple[str, ...] = ()

    @property
    def fact_recall(self) -> str | None:
        return self.metrics.fact_recall


def entailment_packet_input(
    *,
    language: Literal["python", "rust"],
    fact_statement: str,
    candidate_text: str,
) -> JudgePacketInput:
    """The fixed entailment packet: the stated fact and the candidate text, nothing else.

    No oracle weight, no answer identity and no score expectation is exposed to the judge. The
    judge contract's span origins are the frozen vocabulary: the candidate text is the candidate
    span and the stated fact is the reference evidence span.
    """
    return JudgePacketInput(
        language=language,
        task_statement=f"Does the candidate text express this fact: {fact_statement}",
        constraints=("Judge entailment only: expressible by paraphrase; contradiction scores 0.",),
        item_ids=(QA_ENTAILMENT_ITEM,),
        spans=(
            InputSpan(
                origin="candidate_code",
                path="candidate_text",
                text=candidate_text[:8000],
            ),
            InputSpan(
                origin="analyzer_evidence",
                path="stated_fact",
                text=fact_statement[:8000],
            ),
        ),
        comments=(),
        withheld_values=(),
        packet_role="scored",
    )


def entailment_votes(
    judgement: JudgementResult, *, item_id: str = QA_ENTAILMENT_ITEM
) -> EntailmentVotes:
    """The frozen conversion of a judge result into 0/1 entailment votes.

    A half anchor is not a valid entailment vote: the fixed protocol asks for a binary judgment,
    so a ``0.500000`` vote is refused rather than silently rounded.
    """
    outcome = judgement.item(item_id)
    votes: list[int] = []
    for score in outcome.vote_scores:
        if score == "0.000000":
            votes.append(0)
        elif score == "1.000000":
            votes.append(1)
        else:
            raise QaContractError(f"entailment votes are 0/1; judge returned {score}")
    if not votes:
        if outcome.mean_score is None:
            raise QaContractError("an entailment judgment needs votes or an adjudication")
        return EntailmentVotes(
            schema_version=1,
            kind="entailment_votes",
            votes=(0,),
            adjudicated_credit=outcome.mean_score,
        )
    return EntailmentVotes(
        schema_version=1,
        kind="entailment_votes",
        votes=tuple(votes),
        adjudicated_credit=outcome.mean_score if outcome.source == "adjudication" else None,
    )


@dataclass(frozen=True)
class QaVoteSet:
    """Every entailment judgment one grading needs. Missing entries mean unknown, not zero."""

    fact_votes: Mapping[str, EntailmentVotes] = field(default_factory=dict)
    claim_fact_votes: Mapping[tuple[int, str], EntailmentVotes] = field(default_factory=dict)
    span_fact_votes: Mapping[tuple[int, int, str], EntailmentVotes] = field(default_factory=dict)
    claim_support_votes: Mapping[tuple[int, int], EntailmentVotes] = field(default_factory=dict)


def grade_qa(
    oracle: QaOracle,
    answer: QaAnswer,
    *,
    snapshot: Mapping[str, bytes],
    votes: QaVoteSet,
) -> QaGradeReport:
    """Grade one answer against one pinned oracle. Prose is never scored as generated code."""
    credits = fact_credits(oracle, answer, votes=votes.fact_votes)
    recall = weighted_fact_recall(
        oracle,
        {
            credit.fact_id: None if credit.credit is None else Decimal(credit.credit)
            for credit in credits
        },
    )
    invalid_citations, _total = validate_citations(
        answer, snapshot=snapshot, base_digest=oracle.base_digest
    )
    material_claims = _material_claims(answer)
    expressed = _expressed_claims(oracle, material_claims, answer, votes)
    grounding, grounding_incomplete = _grounding(oracle, material_claims, answer, votes, expressed)
    unsupported, contradicted, claims_incomplete = _claim_diagnostics(
        oracle, material_claims, answer, votes, expressed
    )
    supported_claims = _supported_claim_count(material_claims, answer, votes, expressed)
    additional = _additional_count(material_claims, expressed)
    metrics = QaMetrics(
        schema_version=1,
        kind="qa_metrics",
        fact_recall=None if recall is None else quantize6(recall),
        fact_recall_status="incomplete" if recall is None else "complete",
        native_presence_aggregation=native_presence_aggregation(oracle, answer.answer_text),
        grounding_rate=None if grounding_incomplete or grounding is None else quantize6(grounding),
        unsupported_claims=None if claims_incomplete else len(unsupported),
        # Contradiction is a deterministic check, so its count is known even when other
        # verifications are incomplete.
        contradicted_claims=len(contradicted),
        unsupported_rate=None
        if claims_incomplete
        else _rate_str(rate(len(unsupported), additional)),
        contradicted_rate=_rate_str(rate(len(contradicted), additional)),
        claim_precision=_rate_str(
            claim_precision(supported_claims=supported_claims, material_claims=len(material_claims))
        ),
        invalid_citations=len(invalid_citations),
    )
    incomplete: list[str] = []
    if recall is None:
        incomplete.append("fact judgments missing for at least one fact")
    if grounding_incomplete:
        incomplete.append("span-to-fact judgments missing for an asserted citation")
    if claims_incomplete:
        incomplete.append("claim-support judgments missing for a cited additional claim")
    return QaGradeReport(
        schema_version=1,
        kind="qa_grade_report",
        oracle_digest=oracle.digest,
        answer_digest=answer.digest,
        metrics=metrics,
        credits=credits,
        invalid_citations=invalid_citations,
        unsupported_claims=unsupported,
        contradicted_claims=contradicted,
        incomplete_verifications=tuple(incomplete),
    )


def _rate_str(value: Decimal | None) -> str | None:
    return None if value is None else quantize6(value)


def _material_claims(answer: QaAnswer) -> tuple[str, ...]:
    """The frozen extraction runs over the prose, so no stated claim escapes the diagnostics."""
    return extract_material_claims(answer.answer_text)


def _citations_by_claim(
    answer: QaAnswer, material_claims: Sequence[str]
) -> list[tuple[Citation, ...]]:
    """Citations of each extracted claim: the structured claims that quote it carry the spans."""
    by_text = {" ".join(claim.text.split()): claim.citations for claim in answer.claims}
    associated: list[tuple[Citation, ...]] = []
    for claim in material_claims:
        normalized = " ".join(claim.split())
        citations: tuple[Citation, ...] = ()
        for text, claim_citations in by_text.items():
            if text and text in normalized:
                citations = claim_citations
                break
        associated.append(citations)
    return associated


def _expressed_claims(
    oracle: QaOracle,
    claims: Sequence[str],
    answer: QaAnswer,
    votes: QaVoteSet,
) -> dict[int, str]:
    """Claim index -> fact id for claims that express an oracle fact (entailment >= threshold)."""
    expressed: dict[int, str] = {}
    for index in range(len(claims)):
        for fact in oracle.facts:
            judgment = votes.claim_fact_votes.get((index, fact.fact_id))
            if judgment is not None and judgment.credit() >= SUPPORT_THRESHOLD:
                expressed[index] = fact.fact_id
                break
    return expressed


def _grounding(
    oracle: QaOracle,
    claims: Sequence[str],
    answer: QaAnswer,
    votes: QaVoteSet,
    expressed: Mapping[int, str],
) -> tuple[Decimal | None, bool]:
    asserted = 0
    supported = 0
    incomplete = False
    citations_by_claim = _citations_by_claim(answer, claims)
    for index, fact_id in expressed.items():
        for cite_index, _citation in enumerate(citations_by_claim[index]):
            asserted += 1
            judgment = votes.span_fact_votes.get((index, cite_index, fact_id))
            if judgment is None:
                incomplete = True
                continue
            if judgment.credit() >= SUPPORT_THRESHOLD:
                supported += 1
    if incomplete:
        return None, True
    return grounding_rate(supported=supported, asserted=asserted), False


def _claim_diagnostics(
    oracle: QaOracle,
    claims: Sequence[str],
    answer: QaAnswer,
    votes: QaVoteSet,
    expressed: Mapping[int, str],
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Unsupported/contradicted additional claims; incomplete verification marks the counts."""
    additional = [claim for index, claim in enumerate(claims) if index not in expressed]
    contradicted = contradicted_claims(oracle, tuple(additional))
    unsupported: list[str] = []
    incomplete = False
    citations_by_claim = _citations_by_claim(answer, claims)
    for index, claim in enumerate(claims):
        if index in expressed:
            continue
        citations = citations_by_claim[index]
        if not citations:
            unsupported.append(claim)
            continue
        supported = False
        claim_incomplete = False
        for cite_index, _citation in enumerate(citations):
            judgment = votes.claim_support_votes.get((index, cite_index))
            if judgment is None:
                claim_incomplete = True
                continue
            if judgment.credit() >= SUPPORT_THRESHOLD:
                supported = True
        if claim_incomplete:
            incomplete = True
        elif not supported:
            unsupported.append(claim)
    return tuple(unsupported), tuple(contradicted), incomplete


def _additional_count(claims: Sequence[str], expressed: Mapping[int, str]) -> int:
    return sum(1 for index in range(len(claims)) if index not in expressed)


def _supported_claim_count(
    claims: Sequence[str],
    answer: QaAnswer,
    votes: QaVoteSet,
    expressed: Mapping[int, str],
) -> int:
    count = 0
    citations_by_claim = _citations_by_claim(answer, claims)
    for index in range(len(claims)):
        if index in expressed:
            count += 1
            continue
        for cite_index, _citation in enumerate(citations_by_claim[index]):
            judgment = votes.claim_support_votes.get((index, cite_index))
            if judgment is not None and judgment.credit() >= SUPPORT_THRESHOLD:
                count += 1
                break
    return count
