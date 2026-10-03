"""Repository Q&A contracts: pinned inputs, citations, claim extraction, contradictions (Prompt 27).

PCB-27-1 and PCB-27-3 at the pure-contract level: citations bind to the base snapshot, editing
tools are refused for Q&A protocols, retrieval context/truncation is logged, and the frozen
claim procedure plus the deterministic contradiction check never silently upgrade an unknown.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from polycodebench_core.qa_contracts import (
    QaContractError,
    QaOracle,
    QaSubmissionInvalid,
    QaTaskInputs,
    claim_precision,
    contradicted_claims,
    extract_material_claims,
    fact_credits,
    grounding_rate,
    native_presence,
    native_presence_aggregation,
    parse_qa_answer,
    rate,
    retrieval_records,
    validate_citations,
    validate_qa_protocol_tools,
    weighted_fact_recall,
)

BASE = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def span(path: str = "pkg/core.py", start: int = 1, end: int = 5, digest: str = BASE) -> dict:
    return {
        "schema_version": 1,
        "kind": "code_span",
        "path": path,
        "start_line": start,
        "end_line": end,
        "base_digest": digest,
    }


def fact(fact_id: str, **overrides: object) -> dict:
    values: dict = {
        "schema_version": 1,
        "kind": "atomic_fact",
        "fact_id": fact_id,
        "weight": 1,
        "statement": "load_config reads the file and validates it",
        "accepted_paraphrases": (),
        "verifying_spans": (span(),),
        "exact_values": (),
    }
    values.update(overrides)
    return values


def oracle(*facts: dict) -> QaOracle:
    return QaOracle(
        schema_version=1,
        kind="qa_oracle",
        oracle_id="oracle-1",
        version=1,
        base_digest=BASE,
        facts=facts,
    )


def envelope(answer: str, claims: list[dict] | None = None) -> str:
    import json

    return json.dumps({"answer": answer, "claims": claims or []})


def citation(path: str = "pkg/core.py", start: int = 1, end: int = 5, digest: str = BASE) -> dict:
    return {"path": path, "start_line": start, "end_line": end, "base_digest": digest}


# ------------------------------------------------------------------------- pinned inputs


def test_qa_protocols_are_read_search_only() -> None:
    validate_qa_protocol_tools(("list_files", "read_file", "search"))
    with pytest.raises(QaContractError, match="editing tools are disabled"):
        validate_qa_protocol_tools(("list_files", "apply_patch"))
    with pytest.raises(QaContractError, match="editing tools are disabled"):
        validate_qa_protocol_tools(("read_file", "run_command"))
    with pytest.raises(QaContractError, match="unknown tools"):
        validate_qa_protocol_tools(("read_file", "browse_web"))


def test_retrieval_context_and_truncation_are_logged() -> None:
    records = retrieval_records(
        (
            {
                "name": "read_file",
                "event_seq": 1,
                "status": "ok",
                "truncated": True,
                "original_bytes": 90_000,
                "returned_bytes": 32_000,
            },
            {"name": "search", "event_seq": 2, "status": "error", "truncated": False},
        )
    )
    assert records[0].truncated is True
    assert records[0].original_bytes == 90_000 and records[0].returned_bytes == 32_000
    assert records[1].status == "error"
    with pytest.raises(QaContractError, match="non-retrieval tool"):
        retrieval_records(({"name": "apply_patch", "event_seq": 3},))


def test_question_inputs_pin_the_snapshot() -> None:
    inputs = QaTaskInputs(
        schema_version=1,
        kind="qa_task_inputs",
        question_id="q-1",
        question="How does load_config handle retries?",
        base_digest=BASE,
        snapshot_paths=("pkg/core.py", "pkg/util.py"),
    )
    assert inputs.base_digest == BASE
    with pytest.raises(ValueError, match="unique"):
        QaTaskInputs(
            schema_version=1,
            kind="qa_task_inputs",
            question_id="q-1",
            question="How does load_config handle retries?",
            base_digest=BASE,
            snapshot_paths=("pkg/core.py", "pkg/core.py"),
        )


# ------------------------------------------------------------------------------- citations


def test_citations_reference_the_base_snapshot_only() -> None:
    answer = parse_qa_answer(
        envelope(
            "load_config validates the config.",
            [{"text": "load_config validates the config", "citations": [citation()]}],
        ),
        base_digest=BASE,
    )
    reasons, total = validate_citations(
        answer, snapshot={"pkg/core.py": b"line\n" * 10}, base_digest=BASE
    )
    assert reasons == () and total == 1

    foreign = parse_qa_answer(
        envelope(
            "load_config validates the config.",
            [
                {
                    "text": "load_config validates the config",
                    "citations": [citation(digest=OTHER_DIGEST)],
                }
            ],
        ),
        base_digest=OTHER_DIGEST,
    )
    reasons, _ = validate_citations(
        foreign, snapshot={"pkg/core.py": b"line\n" * 10}, base_digest=BASE
    )
    assert any("digest does not match" in reason for reason in reasons)

    missing = parse_qa_answer(
        envelope(
            "load_config validates the config.",
            [
                {
                    "text": "load_config validates the config",
                    "citations": [citation(path="pkg/absent.py")],
                }
            ],
        ),
        base_digest=BASE,
    )
    reasons, _ = validate_citations(missing, snapshot={"pkg/core.py": b"x\n"}, base_digest=BASE)
    assert any("not in the base snapshot" in reason for reason in reasons)

    overrun = parse_qa_answer(
        envelope(
            "load_config validates the config.",
            [{"text": "load_config validates the config", "citations": [citation(end=99)]}],
        ),
        base_digest=BASE,
    )
    reasons, _ = validate_citations(
        overrun, snapshot={"pkg/core.py": b"line\n" * 10}, base_digest=BASE
    )
    assert any("exceeds the snapshot file" in reason for reason in reasons)


def test_envelope_parsing_refuses_malformed_and_ambiguous_submissions() -> None:
    with pytest.raises(QaSubmissionInvalid, match="not JSON"):
        parse_qa_answer("not json at all", base_digest=BASE)
    with pytest.raises(QaSubmissionInvalid, match="ambiguous"):
        parse_qa_answer("```json\n{}\n```\nand more\n```json\n{}\n```", base_digest=BASE)
    with pytest.raises(QaSubmissionInvalid, match="exactly answer and claims"):
        parse_qa_answer('{"answer": "hi", "extra": 1}', base_digest=BASE)
    with pytest.raises(QaSubmissionInvalid, match="pinned base snapshot digest"):
        parse_qa_answer(
            envelope(
                "load_config validates the config.",
                [
                    {
                        "text": "load_config validates the config",
                        "citations": [citation(digest=OTHER_DIGEST)],
                    }
                ],
            ),
            base_digest=BASE,
        )
    with pytest.raises(QaSubmissionInvalid, match="quote the answer"):
        parse_qa_answer(
            envelope(
                "load_config validates the config.", [{"text": "invented claim", "citations": []}]
            ),
            base_digest=BASE,
        )


# ------------------------------------------------------------------- claims and contradictions


def test_claim_extraction_is_frozen_bounded_and_deterministic() -> None:
    text = "load_config validates the config. ok. The retry limit is 3 attempts!"
    claims = extract_material_claims(text)
    assert claims == (
        "load_config validates the config",
        "The retry limit is 3 attempts",
    )
    assert extract_material_claims(text) == claims
    long_text = ". ".join(f"Sentence number {index} carries content words" for index in range(60))
    assert len(extract_material_claims(long_text)) == 40


def test_exact_value_contradictions_are_deterministic() -> None:
    oracle_ = oracle(
        fact(
            "fact-limit",
            statement="the retry limit is 3 attempts",
            exact_values=("3",),
        )
    )
    contradicted = contradicted_claims(
        oracle_, ("the retry limit is 9 attempts", "load_config validates the config")
    )
    assert contradicted == ("the retry limit is 9 attempts",)
    assert contradicted_claims(oracle_, ("the retry limit is 3 attempts",)) == ()


# --------------------------------------------------------------------------- recall formula


def test_fact_recall_weighting_repetition_and_absence() -> None:
    oracle_ = oracle(
        fact("fact-a", weight=1),
        fact("fact-b", weight=3, statement="the retry limit is 3 attempts", exact_values=("3",)),
    )
    answer = parse_qa_answer(
        envelope("load_config reads the file and validates it"), base_digest=BASE
    )
    credits = fact_credits(oracle_, answer, votes={})
    by_id = {credit.fact_id: credit for credit in credits}
    # Without judgment on a non-empty answer the credit is unknown, not zero.
    assert by_id["fact-a"].credit is None
    assert weighted_fact_recall(oracle_, {c.fact_id: None for c in credits}) is None

    judged = fact_credits(
        oracle_,
        answer,
        votes={
            "fact-a": _votes(1, 1, 1),
            "fact-b": _votes(0, 0, 0),
        },
    )
    assert weighted_fact_recall(oracle_, {c.fact_id: Decimal(c.credit) for c in judged}) == Decimal(
        "25"
    )


def test_repeated_facts_add_no_credit() -> None:
    oracle_ = oracle(fact("fact-a"))
    once = parse_qa_answer(
        envelope("load_config reads the file and validates it"), base_digest=BASE
    )
    twice = parse_qa_answer(
        envelope(
            "load_config reads the file and validates it. "
            + "load_config reads the file and validates it."
        ),
        base_digest=BASE,
    )
    credits_once = fact_credits(oracle_, once, votes={"fact-a": _votes(1, 1, 1)})
    credits_twice = fact_credits(oracle_, twice, votes={"fact-a": _votes(1, 1, 1)})
    # Repetition changes nothing: one fact, one credit.
    assert credits_once[0].credit == credits_twice[0].credit == "1.000000"


def test_empty_answer_recalls_zero_and_precision_is_undefined() -> None:
    oracle_ = oracle(fact("fact-a"), fact("fact-b"))
    empty = parse_qa_answer(envelope(""), base_digest=BASE)
    credits = fact_credits(oracle_, empty, votes={})
    assert all(credit.credit == "0.000000" for credit in credits)
    assert weighted_fact_recall(
        oracle_, {credit.fact_id: Decimal(credit.credit) for credit in credits}
    ) == Decimal("0")
    assert claim_precision(supported_claims=0, material_claims=0) is None


def test_contradiction_forces_zero_credit() -> None:
    oracle_ = oracle(
        fact("fact-limit", statement="the retry limit is 3 attempts", exact_values=("3",))
    )
    answer = parse_qa_answer(envelope("the retry limit is 9 attempts"), base_digest=BASE)
    credits = fact_credits(oracle_, answer, votes={"fact-limit": _votes(1, 1, 1)})
    assert credits[0].credit == "0.000000"
    assert credits[0].contradicted is True and credits[0].source == "contradiction"


def test_native_presence_aggregation_is_preserved_separately() -> None:
    oracle_ = oracle(
        fact("fact-a", accepted_paraphrases=("the loader parses then validates the config",)),
        fact("fact-b", statement="the retry limit is 3 attempts", exact_values=("3",)),
    )
    answer = parse_qa_answer(
        envelope("The loader parses then validates the config."), base_digest=BASE
    )
    presence = native_presence(oracle_, answer.answer_text)
    assert presence == {"fact-a": True, "fact-b": False}
    assert native_presence_aggregation(oracle_, answer.answer_text) == "50.000000"


def test_diagnostic_rates_keep_unknown_states_distinct() -> None:
    assert rate(None, 4) is None
    assert rate(2, 0) is None
    assert grounding_rate(supported=1, asserted=0) is None
    assert grounding_rate(supported=1, asserted=2) == Decimal("50")


def _votes(*values: int):
    from polycodebench_core.qa_contracts import EntailmentVotes

    return EntailmentVotes(schema_version=1, kind="entailment_votes", votes=tuple(values))


def test_oracle_spans_are_tied_to_the_base_digest() -> None:
    with pytest.raises(ValueError, match="base digest"):
        oracle(fact("fact-a", verifying_spans=(span(digest=OTHER_DIGEST),)))
