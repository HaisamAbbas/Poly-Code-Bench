"""Q&A grading: entailment fact recall, preserved native aggregation, named diagnostics.

E2E-38 Q&A cases and adversarial citation/judge fixtures (Prompt 27). Entailment runs through
the real judge services (build_packet / parse_vote / aggregate) with the frozen
``qa-entailment-rubric-v1``/``qa-entailment-panel-v1`` pair and deterministic fixture votes -
the panel is unprovisioned, exactly as for every other judge run in this workspace. The
prediction-family cases stay pending until Prompt 28.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from judging_support import vote_text
from polycodebench_core.canonical import sha256_bytes
from polycodebench_core.judge_contracts import JudgeDelivery, JudgementResult
from polycodebench_core.qa_contracts import (
    EntailmentVotes,
    QaContractError,
    QaOracle,
    QaSubmissionInvalid,
    parse_qa_answer,
)
from polycodebench_evaluation.qa_grading import (
    QA_ENTAILMENT_ITEM,
    QaVoteSet,
    entailment_packet_input,
    entailment_votes,
    grade_qa,
)
from polycodebench_services.judging import (
    aggregate,
    build_packet,
    load_panel,
    load_rubric,
    parse_vote,
)

CONFIG = Path(__file__).resolve().parents[1] / "config"
BASE = "sha256:" + "a" * 64


def rubric():
    return load_rubric(CONFIG / "judging" / "qa-entailment-rubric-v1.yaml")


def panel():
    return load_panel(CONFIG / "judging" / "qa-entailment-panel-v1.yaml")


def span(path: str = "pkg/core.py", start: int = 1, end: int = 5) -> dict:
    return {
        "schema_version": 1,
        "kind": "code_span",
        "path": path,
        "start_line": start,
        "end_line": end,
        "base_digest": BASE,
    }


def oracle() -> QaOracle:
    return QaOracle(
        schema_version=1,
        kind="qa_oracle",
        oracle_id="oracle-1",
        version=1,
        base_digest=BASE,
        facts=(
            {
                "schema_version": 1,
                "kind": "atomic_fact",
                "fact_id": "fact-load",
                "weight": 1,
                "statement": "load_config reads the file and validates it",
                "accepted_paraphrases": ("the loader parses then validates the config",),
                "verifying_spans": (span(),),
                "exact_values": (),
            },
            {
                "schema_version": 1,
                "kind": "atomic_fact",
                "fact_id": "fact-limit",
                "weight": 2,
                "statement": "the retry limit is 3 attempts",
                "accepted_paraphrases": (),
                "verifying_spans": (span(start=10, end=12),),
                "exact_values": ("3",),
            },
        ),
    )


def envelope(answer: str, claims: list | None = None) -> str:
    return json.dumps({"answer": answer, "claims": claims or []})


def judgment_result(fact_statement: str, candidate_text: str, *scores: str) -> JudgementResult:
    """One entailment judgment run through the real judge services with fixture votes."""
    packet = build_packet(
        rubric=rubric(),
        panel=panel(),
        packet_input=entailment_packet_input(
            language="python",
            fact_statement=fact_statement,
            candidate_text=candidate_text,
        ),
    )
    votes = []
    deliveries = []
    for index, score in enumerate(scores):
        votes.append(
            parse_vote(
                text=vote_text(packet, scores={QA_ENTAILMENT_ITEM: score}),
                packet=packet,
                panel=panel(),
                vote_index=index,
                seed=None,
                raw_response_digest=sha256_bytes(f"{index}-{score}".encode()),
            )
        )
        deliveries.append(
            JudgeDelivery.model_validate(
                {
                    "packet_id": packet.packet_id,
                    "vote_index": index,
                    "delivery_index": 0,
                    "status": "valid",
                    "judge_revision": panel().judge_revision,
                    "created_at": "2026-10-03T12:00:00Z",
                }
            )
        )
    return aggregate(packet=packet, panel=panel(), votes=votes, deliveries=deliveries)


def judgment(fact_statement: str, candidate_text: str, *scores: str) -> EntailmentVotes:
    """The frozen conversion of one real judge run into 0/1 entailment votes."""
    return entailment_votes(judgment_result(fact_statement, candidate_text, *scores))


# ------------------------------------------------------------------------------- E2E-38


def test_e2e38_fact_recall_uses_entailment_votes_over_paraphrases() -> None:
    answer = parse_qa_answer(
        envelope(
            "The loader parses then validates the config. The retry limit is 3 attempts.",
            [
                {
                    "text": "The loader parses then validates the config",
                    "citations": [
                        {
                            "path": "pkg/core.py",
                            "start_line": 1,
                            "end_line": 5,
                            "base_digest": BASE,
                        }
                    ],
                },
                {
                    "text": "The retry limit is 3 attempts",
                    "citations": [
                        {
                            "path": "pkg/core.py",
                            "start_line": 10,
                            "end_line": 12,
                            "base_digest": BASE,
                        }
                    ],
                },
            ],
        ),
        base_digest=BASE,
    )
    votes = QaVoteSet(
        fact_votes={
            "fact-load": judgment(
                "load_config reads the file and validates it",
                answer.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            "fact-limit": judgment(
                "the retry limit is 3 attempts",
                answer.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
        },
        claim_fact_votes={
            (0, "fact-load"): judgment(
                "load_config reads the file and validates it",
                "The loader parses then validates the config",
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            (1, "fact-limit"): judgment(
                "the retry limit is 3 attempts",
                "The retry limit is 3 attempts",
                "1.000000",
                "1.000000",
                "1.000000",
            ),
        },
        span_fact_votes={
            (0, 0, "fact-load"): judgment(
                "load_config reads the file and validates it",
                "load_config validates the config",
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            (1, 0, "fact-limit"): judgment(
                "the retry limit is 3 attempts",
                "retry limit 3 attempts",
                "1.000000",
                "1.000000",
                "1.000000",
            ),
        },
    )
    report = grade_qa(oracle(), answer, snapshot={"pkg/core.py": b"x\n" * 20}, votes=votes)
    assert report.metrics.fact_recall == "100.000000"
    assert report.metrics.fact_recall_status == "complete"
    assert report.metrics.grounding_rate == "100.000000"
    assert report.metrics.unsupported_claims == 0
    assert report.metrics.claim_precision == "100.000000"
    assert report.metrics.code_dimensions == "not_applicable"


def test_e2e38_missing_facts_zero_repetition_no_credit_and_empty_recall_zero() -> None:
    one_fact = parse_qa_answer(
        envelope("load_config reads the file and validates it"), base_digest=BASE
    )
    votes = QaVoteSet(
        fact_votes={
            "fact-load": judgment(
                "load_config reads the file and validates it",
                one_fact.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            "fact-limit": judgment(
                "the retry limit is 3 attempts",
                one_fact.answer_text,
                "0.000000",
                "0.000000",
                "0.000000",
            ),
        }
    )
    report = grade_qa(oracle(), one_fact, snapshot={"pkg/core.py": b"x\n" * 20}, votes=votes)
    # Missing fact: zero credit (1 of 3 weighted points).
    assert report.metrics.fact_recall == "33.333333"

    repeated = parse_qa_answer(
        envelope(
            "load_config reads the file and validates it. "
            + "load_config reads the file and validates it."
        ),
        base_digest=BASE,
    )
    repeated_votes = QaVoteSet(
        fact_votes={
            "fact-load": judgment(
                "load_config reads the file and validates it",
                repeated.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            "fact-limit": judgment(
                "the retry limit is 3 attempts",
                repeated.answer_text,
                "0.000000",
                "0.000000",
                "0.000000",
            ),
        }
    )
    repeated_report = grade_qa(
        oracle(), repeated, snapshot={"pkg/core.py": b"x\n" * 20}, votes=repeated_votes
    )
    # Repetition adds no credit.
    assert repeated_report.metrics.fact_recall == "33.333333"

    empty = parse_qa_answer(envelope(""), base_digest=BASE)
    empty_report = grade_qa(oracle(), empty, snapshot={}, votes=QaVoteSet())
    assert empty_report.metrics.fact_recall == "0.000000"
    assert empty_report.metrics.claim_precision is None


def test_e2e38_contradiction_is_not_an_expressed_fact_even_with_generous_votes() -> None:
    answer = parse_qa_answer(envelope("the retry limit is 9 attempts"), base_digest=BASE)
    votes = QaVoteSet(
        fact_votes={
            "fact-load": judgment(
                "load_config reads the file and validates it",
                answer.answer_text,
                "0.000000",
                "0.000000",
                "0.000000",
            ),
            "fact-limit": judgment(
                "the retry limit is 3 attempts",
                answer.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
        }
    )
    report = grade_qa(oracle(), answer, snapshot={"pkg/core.py": b"x\n" * 20}, votes=votes)
    assert report.metrics.fact_recall == "0.000000"
    assert report.credits[1].contradicted is True
    assert report.metrics.contradicted_claims == 1


def test_e2e38_wrong_citations_are_invalid_and_grounding_unsupported_diagnostics_follow() -> None:
    answer = parse_qa_answer(
        envelope(
            "load_config reads the file and validates it. The retry limit is 3 attempts.",
            [
                {
                    "text": "load_config reads the file and validates it",
                    "citations": [
                        {
                            "path": "pkg/core.py",
                            "start_line": 999,
                            "end_line": 999,
                            "base_digest": BASE,
                        }
                    ],
                },
                {
                    "text": "The retry limit is 3 attempts",
                    "citations": [
                        {
                            "path": "pkg/absent.py",
                            "start_line": 1,
                            "end_line": 2,
                            "base_digest": BASE,
                        }
                    ],
                },
            ],
        ),
        base_digest=BASE,
    )
    votes = QaVoteSet(
        fact_votes={
            "fact-load": judgment(
                "load_config reads the file and validates it",
                answer.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            "fact-limit": judgment(
                "the retry limit is 3 attempts",
                answer.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
        },
        claim_fact_votes={
            (0, "fact-load"): judgment(
                "load_config reads the file and validates it",
                "load_config reads the file and validates it",
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            (1, "fact-limit"): judgment(
                "the retry limit is 3 attempts",
                "The retry limit is 3 attempts",
                "1.000000",
                "1.000000",
                "1.000000",
            ),
        },
        span_fact_votes={
            (0, 0, "fact-load"): judgment(
                "load_config reads the file and validates it",
                "wrong span",
                "0.000000",
                "0.000000",
                "0.000000",
            ),
            (1, 0, "fact-limit"): judgment(
                "the retry limit is 3 attempts", "wrong span", "0.000000", "0.000000", "0.000000"
            ),
        },
    )
    report = grade_qa(oracle(), answer, snapshot={"pkg/core.py": b"x\n" * 20}, votes=votes)
    assert len(report.invalid_citations) == 2
    # Facts are still expressed (recall counts them) but the asserted citations do not support
    # them: grounding is zero, and the diagnostics stay separately named from fact recall.
    assert report.metrics.fact_recall == "100.000000"
    assert report.metrics.grounding_rate == "0.000000"


def test_e2e38_incomplete_verification_is_unknown_not_zero() -> None:
    answer = parse_qa_answer(
        envelope(
            "load_config reads the file and validates it. An extra claim with no proof.",
            [
                {
                    "text": "load_config reads the file and validates it",
                    "citations": [
                        {
                            "path": "pkg/core.py",
                            "start_line": 1,
                            "end_line": 5,
                            "base_digest": BASE,
                        }
                    ],
                }
            ],
        ),
        base_digest=BASE,
    )
    # No fact votes and no claim-support votes at all: verification is incomplete.
    report = grade_qa(oracle(), answer, snapshot={"pkg/core.py": b"x\n" * 20}, votes=QaVoteSet())
    assert report.metrics.fact_recall is None
    assert report.metrics.fact_recall_status == "incomplete"
    assert report.metrics.unsupported_claims is None
    assert report.metrics.unsupported_rate is None
    assert report.incomplete_verifications
    assert report.metrics.contradicted_claims == 0


def test_e2e38_native_aggregation_is_preserved_separately_from_entailment() -> None:
    # A semantic paraphrase outside the accepted list: entailment credits it, the preserved
    # native presence aggregation does not see it - and both are recorded.
    answer = parse_qa_answer(
        envelope("The configuration is parsed first and checked afterward."), base_digest=BASE
    )
    votes = QaVoteSet(
        fact_votes={
            "fact-load": judgment(
                "load_config reads the file and validates it",
                answer.answer_text,
                "1.000000",
                "1.000000",
                "1.000000",
            ),
            "fact-limit": judgment(
                "the retry limit is 3 attempts",
                answer.answer_text,
                "0.000000",
                "0.000000",
                "0.000000",
            ),
        }
    )
    report = grade_qa(oracle(), answer, snapshot={}, votes=votes)
    assert report.metrics.fact_recall == "33.333333"
    assert report.metrics.native_presence_aggregation == "0.000000"


# --------------------------------------------------------------- adversarial judge fixtures


def test_adversarial_half_anchor_entailment_votes_are_refused() -> None:
    # A half anchor passes the judge layer (it is a declared anchor) but the frozen entailment
    # conversion refuses it: the fixed protocol asks for a binary 0/1 judgment.
    verdict = judgment_result(
        "load_config reads the file and validates it", "some candidate text", "0.500000"
    )
    assert verdict.item(QA_ENTAILMENT_ITEM).vote_scores == ("0.500000",)
    with pytest.raises(QaContractError, match="entailment votes are 0/1"):
        entailment_votes(verdict)


def test_entailment_packet_is_fixed_and_blinded() -> None:
    packet_input = entailment_packet_input(
        language="python",
        fact_statement="the retry limit is 3 attempts",
        candidate_text="The retry limit is 3 attempts.",
    )
    assert packet_input.item_ids == (QA_ENTAILMENT_ITEM,)
    assert packet_input.packet_role == "scored"
    assert packet_input.withheld_values == ()
    rendered = json.dumps(packet_input.model_dump(mode="json"))
    assert "weight" not in rendered and "oracle" not in rendered.lower()


def test_adjudication_overrides_the_vote_mean() -> None:
    adjudicated = EntailmentVotes(
        schema_version=1,
        kind="entailment_votes",
        votes=(0, 0, 0),
        adjudicated_credit="1.000000",
    )
    assert adjudicated.credit() == Decimal("1")
    unadjudicated = EntailmentVotes(schema_version=1, kind="entailment_votes", votes=(0, 0, 1))
    assert unadjudicated.credit() == Decimal(1) / Decimal(3)


def test_answer_envelope_citations_carry_the_pinned_digest_only() -> None:
    with pytest.raises(QaSubmissionInvalid, match="pinned base snapshot digest"):
        parse_qa_answer(
            envelope(
                "load_config reads the file and validates it",
                [
                    {
                        "text": "load_config reads the file and validates it",
                        "citations": [
                            {
                                "path": "pkg/core.py",
                                "start_line": 1,
                                "end_line": 5,
                                "base_digest": "sha256:" + "f" * 64,
                            }
                        ],
                    }
                ],
            ),
            base_digest=BASE,
        )
