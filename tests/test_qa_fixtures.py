"""Admitted cross-file Q&A fixtures and the variant matrix (Prompt 27, PCB-27-4).

Runs the authored answer variants of `taskpacks/qa/py-configkit-qa-v1` through the real
contracts: envelope parsing, citation validity against the pinned snapshot, the preserved native
presence aggregation, the deterministic contradiction check, and fact recall under fixture
entailment votes (the judge panel is unprovisioned; the judge-service path itself is covered in
tests/test_qa_grading.py). The prediction-family variants stay pending until Prompt 28.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from polycodebench_core.qa_contracts import (
    EntailmentVotes,
    QaOracle,
    contradicted_claims,
    extract_material_claims,
    fact_credits,
    native_presence,
    parse_qa_answer,
    validate_citations,
    weighted_fact_recall,
)
from polycodebench_evaluation.qa_grading import QaVoteSet, grade_qa
from polycodebench_services.task_packages import TaskPackageImporter

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "taskpacks" / "qa" / "py-configkit-qa-v1"
BASE_DIGEST = "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


def manifest():
    loaded, _ = TaskPackageImporter().load(PACK)
    return loaded


def oracle() -> QaOracle:
    return QaOracle.model_validate_json(
        json.dumps(json.loads((PACK / "hidden" / "oracle.json").read_text(encoding="utf-8")))
    )


def snapshot() -> dict[str, bytes]:
    repo = PACK / "visible" / "repo"
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file()
    }


def answer_of(variant: str):
    return parse_qa_answer(
        (PACK / "admission" / variant / "answer.json").read_text(encoding="utf-8"),
        base_digest=BASE_DIGEST,
    )


def fixture_votes(oracle_: QaOracle, answer_text: str):
    """Fixture entailment votes: the frozen presence check stands in for the unprovisioned judge.

    The judge-service path with real packet/vote/aggregate flow is covered separately; this
    matrix exercises the grading semantics over the admitted fixture content.
    """
    presence = native_presence(oracle_, answer_text)
    return {
        fact.fact_id: EntailmentVotes(
            schema_version=1,
            kind="entailment_votes",
            votes=(1, 1, 1) if presence[fact.fact_id] else (0, 0, 0),
        )
        for fact in oracle_.facts
    }


def test_pack_imports_as_deepcodebench_inspired() -> None:
    loaded = manifest()
    assert loaded.task.family == "repo_qa"
    assert loaded.task.methodology_label == "inspired"
    assert loaded.task.stratum_id == "deepcodebench-inspired"


def test_oracle_verifying_spans_match_the_snapshot() -> None:
    oracle_ = oracle()
    assert oracle_.base_digest == BASE_DIGEST
    files = snapshot()
    assert len(files) >= 4
    for fact in oracle_.facts:
        for span in fact.verifying_spans:
            data = files[span.path]
            lines = data.decode("utf-8").splitlines()
            assert span.start_line >= 1 and span.end_line <= len(lines), fact.fact_id


def test_reference_and_paraphrased_answers_reach_full_recall() -> None:
    oracle_ = oracle()
    files = snapshot()
    for variant in ("reference", "alternative"):
        answer = answer_of(variant)
        reasons, _ = validate_citations(answer, snapshot=files, base_digest=BASE_DIGEST)
        assert reasons == (), (variant, reasons)
        report = grade_qa(
            oracle_,
            answer,
            snapshot=files,
            votes=QaVoteSet(fact_votes=fixture_votes(oracle_, answer.answer_text)),
        )
        assert report.metrics.fact_recall == "100.000000", variant
        assert report.metrics.unsupported_claims == 0, variant


def test_wrong_citations_are_flagged_and_grounding_zero() -> None:
    oracle_ = oracle()
    files = snapshot()
    answer = answer_of("wrong-citation")
    reasons, total = validate_citations(answer, snapshot=files, base_digest=BASE_DIGEST)
    assert total >= 1
    assert reasons, "every wrong citation must be flagged"
    report = grade_qa(
        oracle_,
        answer,
        snapshot=files,
        votes=QaVoteSet(fact_votes=fixture_votes(oracle_, answer.answer_text)),
    )
    assert report.invalid_citations == reasons
    # The facts are expressed, but the asserted citations support nothing.
    assert report.metrics.grounding_rate in (None, "0.000000")


def test_contradiction_answer_earns_no_credit_and_is_counted() -> None:
    oracle_ = oracle()
    answer = answer_of("contradiction")
    credits = fact_credits(oracle_, answer, votes=fixture_votes(oracle_, answer.answer_text))
    by_id = {credit.fact_id: credit for credit in credits}
    assert any(credit.contradicted for credit in credits)
    contradicted = contradicted_claims(oracle_, extract_material_claims(answer.answer_text))
    assert contradicted
    for credit in credits:
        if credit.contradicted:
            assert credit.credit == "0.000000"
    report = grade_qa(
        oracle_,
        answer,
        snapshot=snapshot(),
        votes=QaVoteSet(fact_votes=fixture_votes(oracle_, answer.answer_text)),
    )
    assert report.metrics.contradicted_claims >= 1
    assert report.metrics.fact_recall != "100.000000"
    assert by_id


def test_empty_answer_recalls_zero_with_undefined_precision() -> None:
    oracle_ = oracle()
    answer = answer_of("empty")
    report = grade_qa(oracle_, answer, snapshot={}, votes=QaVoteSet())
    assert report.metrics.fact_recall == "0.000000"
    assert report.metrics.claim_precision is None
    assert report.metrics.grounding_rate is None


def test_repetition_adds_no_credit() -> None:
    oracle_ = oracle()
    reference = grade_qa(
        oracle_,
        answer_of("reference"),
        snapshot=snapshot(),
        votes=QaVoteSet(fact_votes=fixture_votes(oracle_, answer_of("reference").answer_text)),
    )
    repeated = grade_qa(
        oracle_,
        answer_of("repeated"),
        snapshot=snapshot(),
        votes=QaVoteSet(fact_votes=fixture_votes(oracle_, answer_of("repeated").answer_text)),
    )
    assert repeated.metrics.fact_recall == reference.metrics.fact_recall == "100.000000"
    recalls = weighted_fact_recall(
        oracle_,
        {
            credit.fact_id: Decimal(credit.credit)
            for credit in fact_credits(
                oracle_,
                answer_of("repeated"),
                votes=fixture_votes(oracle_, answer_of("repeated").answer_text),
            )
            if credit.credit is not None
        },
    )
    assert recalls == Decimal("100")


def test_six_code_dimensions_stay_not_applicable() -> None:
    report = grade_qa(
        oracle(),
        answer_of("reference"),
        snapshot=snapshot(),
        votes=QaVoteSet(fact_votes=fixture_votes(oracle(), answer_of("reference").answer_text)),
    )
    assert report.metrics.code_dimensions == "not_applicable"
    fields = set(report.metrics.model_dump())
    for forbidden in ("security", "runtime", "idiom", "robustness", "efficiency"):
        assert not any(forbidden in field for field in fields)
