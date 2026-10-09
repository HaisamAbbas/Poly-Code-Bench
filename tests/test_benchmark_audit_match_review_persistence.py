"""Transactional boundaries for the append-only benchmark match-review ledger."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from polycodebench_core.application_errors import (
    IdempotencyConflict,
    InvalidState,
    PersistenceConflict,
)
from polycodebench_core.benchmark_audit_documents import (
    MatchReviewOpinion,
    audit_document_digest,
)
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_core.match_verification import (
    MatchAdjudicationSubmission,
    MatchReviewSubmission,
    adjudicate_match_reviews,
    append_match_review,
    initial_match_review_ledger,
)
from polycodebench_persistence.benchmark_audit import (
    MatchAdjudicationWrite,
    MatchReviewWrite,
    PostgresBenchmarkAuditRepository,
)
from sqlalchemy.engine import Engine
from test_benchmark_audit_api import TENANT_A, _match_evidence, _match_review_submission


class _FakeResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def mappings(self) -> _FakeResult:
        return self

    def one_or_none(self) -> object:
        return self.value

    def all(self) -> Sequence[object]:
        assert isinstance(self.value, list)
        return self.value


class _FakeConnection:
    def __init__(self, reads: list[object]) -> None:
        self.reads = list(reads)
        self.writes: list[object] = []

    def execute(self, statement: Any) -> object:
        if statement.is_select:
            return _FakeResult(self.reads.pop(0))
        self.writes.append(statement)
        return type("WriteResult", (), {"rowcount": 1})()

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class _FakeTransaction:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> _FakeConnection:
        return self.connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None


class _FakeEngine:
    def __init__(self, reads: list[object]) -> None:
        self.connection = _FakeConnection(reads)
        self.transaction = _FakeTransaction(self.connection)

    def begin(self) -> _FakeTransaction:
        return self.transaction

    def connect(self) -> _FakeConnection:
        return self.connection


def _document_row(document: Any, tenant_id: UUID) -> dict[str, object]:
    return {
        "id": document.id,
        "kind": document.kind,
        "schema_version": document.schema_version,
        "semantic_digest": audit_document_digest(document),
        "payload": document.payload.model_dump(mode="json"),
        "supersedes_id": document.supersedes_id,
        "created_by": document.metadata.actor,
        "tenant_id": tenant_id,
        "document_created_at": document.metadata.created_at,
        "timestamp_precision": document.metadata.timestamp_precision,
        "trace_id": document.metadata.trace_id,
        "document_row_version": document.metadata.row_version,
    }


def _opinion(candidate: Any, reviewer: str = "reviewer-1") -> MatchReviewOpinion:
    submission = _match_review_submission()
    ledger = append_match_review(
        initial_match_review_ledger(candidate),
        opinion_id=uuid4(),
        reviewer_subject=reviewer,
        decision=submission.decision,
        relation=submission.relation,
        reason=submission.reason,
        evidence_refs=submission.evidence_refs,
        decision_artifact_ref=submission.decision_artifact_ref,
        created_at="2026-10-10T10:00:00Z",
    )
    return ledger.opinions[0]


def _review_row(
    candidate: Any, opinion: MatchReviewOpinion, candidate_id: UUID | None = None
) -> dict[str, object]:
    submission = MatchReviewSubmission(
        decision=opinion.decision,
        relation=opinion.relation,
        reason=opinion.reason,
        evidence_refs=opinion.evidence_refs,
        decision_artifact_ref=opinion.decision_artifact_ref,
    )
    return {
        "id": opinion.opinion_id,
        "candidate_id": candidate_id or uuid4(),
        "review_seq": opinion.review_seq,
        "reviewer_subject": opinion.reviewer_subject,
        "decision": opinion.decision,
        "review_document_id": candidate.id,
        "opinion": opinion.model_dump(mode="json"),
        "idempotency_key": "review-key-001",
        "request_digest": sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json"))),
    }


def _conflicting_reviews(candidate: Any) -> tuple[Any, UUID, list[dict[str, object]]]:
    candidate_id = uuid4()
    accepted = _match_review_submission()
    rejected = accepted.model_copy(
        update={"decision": "rejected", "relation": "no_substantive_match"}
    )
    ledger = initial_match_review_ledger(candidate)
    opinions: list[MatchReviewOpinion] = []
    for reviewer, submission in (("reviewer-1", accepted), ("reviewer-2", rejected)):
        ledger = append_match_review(
            ledger,
            opinion_id=uuid4(),
            reviewer_subject=reviewer,
            decision=submission.decision,
            relation=submission.relation,
            reason=submission.reason,
            evidence_refs=submission.evidence_refs,
            decision_artifact_ref=submission.decision_artifact_ref,
            created_at="2026-10-10T10:00:00Z",
        )
        opinions.append(ledger.opinions[-1])
    return (
        ledger,
        candidate_id,
        [_review_row(candidate, opinion, candidate_id) for opinion in opinions],
    )


def _adjudication_submission() -> MatchAdjudicationSubmission:
    review = _match_review_submission()
    return MatchAdjudicationSubmission(
        decision="accepted",
        relation="semantic_duplicate",
        reason="Independent adjudication resolves the conflicting evidence reviews.",
        evidence_refs=review.evidence_refs,
        decision_artifact_ref=review.decision_artifact_ref,
    )


def _adjudication_row(
    candidate_id: UUID, ledger: Any, submission: MatchAdjudicationSubmission | None = None
) -> dict[str, object]:
    submission = submission or _adjudication_submission()
    resolved = adjudicate_match_reviews(
        ledger,
        adjudication_id=uuid4(),
        adjudicator_subject="adjudicator-1",
        decision=submission.decision,
        relation=submission.relation,
        reason=submission.reason,
        evidence_refs=submission.evidence_refs,
        decision_artifact_ref=submission.decision_artifact_ref,
        created_at="2026-10-10T10:05:00Z",
    )
    assert resolved.adjudication is not None
    adjudication = resolved.adjudication
    return {
        "id": adjudication.adjudication_id,
        "candidate_id": candidate_id,
        "adjudicator_subject": adjudication.adjudicator_subject,
        "decision": adjudication.decision,
        "relation": adjudication.relation,
        "adjudication": adjudication.model_dump(mode="json"),
        "idempotency_key": "adjudication-key-001",
        "request_digest": sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json"))),
    }


def _repository(
    monkeypatch: pytest.MonkeyPatch, engine: _FakeEngine
) -> PostgresBenchmarkAuditRepository:
    monkeypatch.setattr(
        PostgresBenchmarkAuditRepository,
        "_validate_references",
        staticmethod(lambda connection, payload, *, tenant_id: None),
    )
    return PostgresBenchmarkAuditRepository(cast(Engine, engine))


def test_writer_appends_immutable_opinion_and_updates_candidate_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": uuid4(), "state": "proposed"},
            None,
            [],
            None,
        ]
    )
    repository = _repository(monkeypatch, engine)
    submission = _match_review_submission()
    request_digest = sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json")))

    result = repository.append_match_review(
        candidate_document_id=candidate.id,
        submission=submission,
        reviewer_subject="reviewer-1",
        idempotency_key="review-persist-001",
        request_digest=request_digest,
        tenant_id=TENANT_A,
    )

    assert isinstance(result, MatchReviewWrite)
    assert result.created is True
    assert result.review_seq == 1
    assert result.state == "accepted"
    assert len(engine.connection.writes) == 2  # immutable ledger insert, projection update
    assert engine.transaction.committed
    assert not engine.transaction.rolled_back


def test_writer_rejects_self_review_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _match_evidence(author="reviewer-1")
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": uuid4(), "state": "proposed"},
            None,
            [],
            None,
        ]
    )
    repository = _repository(monkeypatch, engine)
    submission = _match_review_submission()
    request_digest = sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json")))

    with pytest.raises(InvalidState, match="review contract"):
        repository.append_match_review(
            candidate_document_id=candidate.id,
            submission=submission,
            reviewer_subject="reviewer-1",
            idempotency_key="review-persist-002",
            request_digest=request_digest,
            tenant_id=TENANT_A,
        )

    assert not engine.connection.writes
    assert engine.transaction.rolled_back


def test_writer_replays_the_same_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _match_evidence()
    opinion = _opinion(candidate)
    submission = MatchReviewSubmission(
        decision=opinion.decision,
        relation=opinion.relation,
        reason=opinion.reason,
        evidence_refs=opinion.evidence_refs,
        decision_artifact_ref=opinion.decision_artifact_ref,
    )
    replay = _review_row(candidate, opinion)
    request_digest = str(replay["request_digest"])
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": replay["candidate_id"], "state": "accepted"},
            replay,
        ]
    )
    repository = _repository(monkeypatch, engine)

    result = repository.append_match_review(
        candidate_document_id=candidate.id,
        submission=submission,
        reviewer_subject="reviewer-1",
        idempotency_key="review-key-001",
        request_digest=request_digest,
        tenant_id=TENANT_A,
    )

    assert result == MatchReviewWrite(
        opinion_id=opinion.opinion_id,
        digest=sha256_bytes(canonical_json_bytes(opinion.model_dump(mode="json"))),
        created=False,
        review_seq=1,
        state="accepted",
    )
    assert not engine.connection.writes
    assert engine.transaction.committed


def test_writer_rejects_idempotency_key_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _match_evidence()
    submission = _match_review_submission()
    request_digest = sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json")))
    replay = {
        "id": uuid4(),
        "reviewer_subject": "reviewer-1",
        "request_digest": "sha256:" + "f" * 64,
    }
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": uuid4(), "state": "accepted"},
            replay,
        ]
    )
    repository = _repository(monkeypatch, engine)

    with pytest.raises(IdempotencyConflict):
        repository.append_match_review(
            candidate_document_id=candidate.id,
            submission=submission,
            reviewer_subject="reviewer-1",
            idempotency_key="review-key-001",
            request_digest=request_digest,
            tenant_id=TENANT_A,
        )

    assert not engine.connection.writes
    assert engine.transaction.rolled_back


def test_history_rebuilds_and_verifies_the_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _match_evidence()
    opinion = _opinion(candidate)
    row = _review_row(candidate, opinion)
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": row["candidate_id"], "state": "accepted"},
            [row],
            None,
        ]
    )
    repository = _repository(monkeypatch, engine)

    ledger = repository.list_match_reviews(candidate_document_id=candidate.id, tenant_id=TENANT_A)

    assert ledger.opinions == (opinion,)
    assert ledger.state == "accepted"


def test_history_fails_closed_on_legacy_rows_without_opinions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    legacy_row = {
        "id": uuid4(),
        "review_seq": 1,
        "reviewer_subject": "reviewer-1",
        "decision": "accepted",
        "review_document_id": candidate.id,
        "opinion": None,
    }
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": uuid4(), "state": "accepted"},
            [legacy_row],
            None,
        ]
    )
    repository = _repository(monkeypatch, engine)

    with pytest.raises(PersistenceConflict, match="no complete opinion"):
        repository.list_match_reviews(candidate_document_id=candidate.id, tenant_id=TENANT_A)


def test_adjudication_appends_an_independent_immutable_event_and_updates_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    _, candidate_id, reviews = _conflicting_reviews(candidate)
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": candidate_id, "state": "disputed"},
            None,
            reviews,
        ]
    )
    repository = _repository(monkeypatch, engine)
    submission = _adjudication_submission()
    request_digest = sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json")))

    result = repository.append_match_adjudication(
        candidate_document_id=candidate.id,
        submission=submission,
        adjudicator_subject="adjudicator-1",
        idempotency_key="adjudication-persist-001",
        request_digest=request_digest,
        tenant_id=TENANT_A,
    )

    assert isinstance(result, MatchAdjudicationWrite)
    assert result.created is True
    assert result.state == "adjudicated"
    assert len(engine.connection.writes) == 2
    assert engine.transaction.committed
    assert not engine.transaction.rolled_back


def test_adjudication_rejects_a_prior_reviewer_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    _, candidate_id, reviews = _conflicting_reviews(candidate)
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": candidate_id, "state": "disputed"},
            None,
            reviews,
        ]
    )
    repository = _repository(monkeypatch, engine)
    submission = _adjudication_submission()
    request_digest = sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json")))

    with pytest.raises(InvalidState, match="adjudication violates"):
        repository.append_match_adjudication(
            candidate_document_id=candidate.id,
            submission=submission,
            adjudicator_subject="reviewer-1",
            idempotency_key="adjudication-persist-002",
            request_digest=request_digest,
            tenant_id=TENANT_A,
        )

    assert not engine.connection.writes
    assert engine.transaction.rolled_back


def test_history_rebuilds_the_adjudication_from_immutable_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    ledger, candidate_id, reviews = _conflicting_reviews(candidate)
    adjudication = _adjudication_row(candidate_id, ledger)
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": candidate_id, "state": "accepted"},
            reviews,
            adjudication,
        ]
    )
    repository = _repository(monkeypatch, engine)

    history = repository.list_match_reviews(candidate_document_id=candidate.id, tenant_id=TENANT_A)

    assert history.state == "adjudicated"
    assert history.adjudication is not None
    assert history.adjudication.adjudicator_subject == "adjudicator-1"
    assert len(history.adjudication.reviewed_opinion_ids) == 2


def test_adjudication_replay_verifies_the_ledger_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    ledger, candidate_id, reviews = _conflicting_reviews(candidate)
    submission = _adjudication_submission()
    row = _adjudication_row(candidate_id, ledger, submission)
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": candidate_id, "state": "accepted"},
            row,
            reviews,
        ]
    )
    repository = _repository(monkeypatch, engine)

    result = repository.append_match_adjudication(
        candidate_document_id=candidate.id,
        submission=submission,
        adjudicator_subject="adjudicator-1",
        idempotency_key="adjudication-key-001",
        request_digest=str(row["request_digest"]),
        tenant_id=TENANT_A,
    )

    assert result.created is False
    assert result.adjudication_id == row["id"]
    assert not engine.connection.writes
    assert engine.transaction.committed


def test_adjudication_idempotency_rejects_a_changed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    ledger, candidate_id, reviews = _conflicting_reviews(candidate)
    submission = _adjudication_submission()
    row = _adjudication_row(candidate_id, ledger, submission)
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": candidate_id, "state": "accepted"},
            row,
            reviews,
        ]
    )
    repository = _repository(monkeypatch, engine)

    with pytest.raises(IdempotencyConflict):
        repository.append_match_adjudication(
            candidate_document_id=candidate.id,
            submission=submission,
            adjudicator_subject="adjudicator-1",
            idempotency_key="adjudication-key-001",
            request_digest="sha256:" + "f" * 64,
            tenant_id=TENANT_A,
        )

    assert not engine.connection.writes
    assert engine.transaction.rolled_back


def test_writer_rejects_new_opinions_after_adjudication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _match_evidence()
    ledger, candidate_id, reviews = _conflicting_reviews(candidate)
    adjudication_submission = _adjudication_submission()
    adjudication = _adjudication_row(candidate_id, ledger, adjudication_submission)
    engine = _FakeEngine(
        [
            _document_row(candidate, TENANT_A),
            {"id": candidate_id, "state": "accepted"},
            None,
            reviews,
            adjudication,
        ]
    )
    repository = _repository(monkeypatch, engine)
    submission = _match_review_submission()
    request_digest = sha256_bytes(canonical_json_bytes(submission.model_dump(mode="json")))

    with pytest.raises(InvalidState, match="already been adjudicated"):
        repository.append_match_review(
            candidate_document_id=candidate.id,
            submission=submission,
            reviewer_subject="reviewer-3",
            idempotency_key="review-after-adjudication",
            request_digest=request_digest,
            tenant_id=TENANT_A,
        )

    assert not engine.connection.writes
    assert engine.transaction.rolled_back
