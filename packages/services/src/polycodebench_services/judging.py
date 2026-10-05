"""Judge use cases: build a packet, accept or reject a vote, aggregate, review, adjudicate.

Everything here is a pure function of frozen inputs (a rubric, a panel, a packet, retained
deliveries and recorded votes), so the same decisions can be replayed offline and consumed by the
scorer. Network access belongs to ``pcb-judge``; this module performs no I/O.

The decisions it makes are the ones the specification fixes:

* a packet carries no identity, rank, cost or expected score, and no tools;
* exactly three logical votes are required, and a missing one makes the result ``infra_blocked``
  rather than a two-vote average;
* a rejected delivery is replaced at most twice and is always retained;
* a low score is never a reason to re-ask;
* review triggers are computed from the retained votes, and an adjudication supersedes an item
  score without deleting a vote.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_core.identity import utc_timestamp
from polycodebench_core.judge_calibration import CalibrationPolicy
from polycodebench_core.judge_contracts import (
    AdjudicationDecision,
    AnchorId,
    InputComment,
    ItemOutcome,
    ItemVote,
    JudgeDelivery,
    JudgeFact,
    JudgementResult,
    JudgePacket,
    JudgePacketInput,
    JudgePanel,
    JudgeRubric,
    JudgeVote,
    PacketItem,
    PacketSpan,
    ReviewQueueEntry,
    ReviewTrigger,
    UntrustedComment,
    VoteCitation,
    VoteRejected,
    adjudication_identity,
    assert_no_identity_leak,
    derived_judge_id,
    quantize_anchor,
)
from polycodebench_core.judge_prompts import (
    detects_instruction_attempt,
    extract_vote_document,
    validate_vote_document,
)
from polycodebench_core.models import ScoreDimension


class JudgeInputsInvalid(ValueError):
    """The offered evidence cannot form a bounded, judge-safe packet."""


#: Distance between the two adjacent pilot anchors (``0``, ``0.5``, ``1``).
ANCHOR_STEP = Decimal("0.5")


def load_rubric(path: Path) -> JudgeRubric:
    return JudgeRubric.model_validate(_document(path), strict=False)


def load_panel(path: Path) -> JudgePanel:
    return JudgePanel.model_validate(_document(path), strict=False)


def load_calibration_policy(path: Path) -> CalibrationPolicy:
    return CalibrationPolicy.model_validate(_document(path), strict=False)


def _document(path: Path) -> dict[str, object]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise JudgeInputsInvalid(f"{path.name}: expected a mapping")
    return document


def _text_digest(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def _anchor_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{sha256_bytes('|'.join(parts).encode('utf-8'))[7:23]}"


def packet_id_for(rubric: JudgeRubric, panel: JudgePanel, packet_input: JudgePacketInput) -> str:
    """Stable packet identity: the same frozen evidence yields the same packet.

    The identity covers the item selection *and* the span material, so a packet id can never be
    reused for different evidence. The digest then binds id, rubric, panel and anchors together.
    """
    material = canonical_json_bytes(
        {
            "rubric": rubric.digest(),
            "panel": panel.digest(),
            "language": packet_input.language,
            "packet_role": packet_input.packet_role,
            "task_statement": packet_input.task_statement,
            "constraints": list(packet_input.constraints),
            "item_ids": list(packet_input.item_ids),
            "spans": [
                {
                    "origin": span.origin,
                    "path": span.path,
                    "start_line": span.start_line,
                    "end_line": span.end_line,
                    "digest": _text_digest(span.text),
                }
                for span in packet_input.spans
            ],
            "comments": [
                {"path": comment.path, "digest": _text_digest(comment.text)}
                for comment in packet_input.comments
            ],
        }
    )
    return derived_judge_id("judge-packet", sha256_bytes(material))


def build_packet(
    *,
    rubric: JudgeRubric,
    panel: JudgePanel,
    packet_input: JudgePacketInput,
) -> JudgePacket:
    """Assemble the anonymized packet and prove it withholds the declared identity values.

    Anchor identifiers are derived from the span material, so replaying the same evidence
    reproduces the same packet digest. Candidate comments become ``cmt-`` anchors: they are
    carried as untrusted data, they are never inside an item's evidence scope, and a vote whose
    only justification is a comment is recorded as unsupported rather than accepted.
    """
    if rubric.rubric_id != panel.rubric_id or rubric.version != panel.rubric_version:
        raise JudgeInputsInvalid("the panel and rubric must be the same frozen pair")
    if packet_input.language not in rubric.languages:
        raise JudgeInputsInvalid(f"{rubric.rubric_id} does not cover {packet_input.language}")

    spans: list[PacketSpan] = []
    for index, offered in enumerate(packet_input.spans):
        spans.append(
            PacketSpan(
                anchor_id=_anchor_id(
                    "sp",
                    offered.origin,
                    offered.path,
                    str(offered.start_line or ""),
                    str(offered.end_line or ""),
                    _text_digest(offered.text),
                    str(index),
                ),
                origin=offered.origin,
                path=offered.path,
                start_line=offered.start_line,
                end_line=offered.end_line,
                text=offered.text,
                content_digest=_text_digest(offered.text),
            )
        )
    if not spans:
        raise JudgeInputsInvalid("a packet needs at least one evidence span")
    scope: tuple[AnchorId, ...] = tuple(span.anchor_id for span in spans)

    items: list[PacketItem] = []
    for item_id in packet_input.item_ids:
        item = rubric.item(item_id)
        items.append(
            PacketItem(
                item_id=item.item_id,
                dimension=item.dimension,
                question=item.question,
                anchors=item.anchors,
                in_scope_anchor_ids=scope,
            )
        )

    packet = JudgePacket(
        packet_id=packet_id_for(rubric, panel, packet_input),
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.version,
        rubric_digest=rubric.digest(),
        panel_id=panel.panel_id,
        panel_version=panel.version,
        panel_digest=panel.digest(),
        language=packet_input.language,
        packet_role=packet_input.packet_role,
        task_statement=packet_input.task_statement,
        constraints=packet_input.constraints,
        items=tuple(items),
        spans=tuple(spans),
        untrusted_comments=_untrusted_comments(packet_input.comments, spans),
    )
    assert_no_identity_leak(packet, packet_input.withheld_values)
    return packet


def _untrusted_comments(
    comments: Sequence[InputComment], spans: Sequence[PacketSpan]
) -> tuple[UntrustedComment, ...]:
    """Attach each comment to the code span that carries it; never to an item's scope."""
    prepared: list[UntrustedComment] = []
    for index, comment in enumerate(comments):
        carrier = _carrying_span(comment.path, spans)
        if carrier is None:
            raise JudgeInputsInvalid(f"comment on {comment.path} has no carrying evidence span")
        prepared.append(
            UntrustedComment(
                anchor_id=_anchor_id("cmt", comment.path, str(index), _text_digest(comment.text)),
                span_anchor_id=carrier,
                path=comment.path,
                text=comment.text,
                instruction_attempt=detects_instruction_attempt(comment.text),
            )
        )
    return tuple(prepared)


def _carrying_span(path: str, spans: Sequence[PacketSpan]) -> str | None:
    for span in spans:
        if span.path == path:
            return span.anchor_id
    return None


def parse_vote(
    *,
    text: str,
    packet: JudgePacket,
    panel: JudgePanel,
    vote_index: int,
    seed: int | None,
    raw_response_digest: str,
    created_at: str | None = None,
    raw_response_artifact_id: str | None = None,
    call_delivery_id: str | None = None,
) -> JudgeVote:
    """Turn one judge response into an accepted vote, or raise ``VoteRejected``.

    A rejection carries a named reason so the retained delivery says what was wrong: malformed
    JSON, an anchor outside the declared set, a citation to no packet anchor, a missing item or an
    extra instruction action.
    """
    document = extract_vote_document(text)
    validate_vote_document(document, packet=packet, panel=panel)
    entries = document["items"]
    assert isinstance(entries, dict)
    return JudgeVote(
        packet_id=packet.packet_id,
        packet_digest=packet.digest(),
        rubric_digest=packet.rubric_digest,
        panel_digest=packet.panel_digest,
        judge_revision=panel.judge_revision,
        vote_index=vote_index,
        seed=seed,
        items=tuple(
            _item_vote(packet, item_id, entry) for item_id, entry in sorted(entries.items())
        ),
        raw_response_digest=raw_response_digest,
        raw_response_artifact_id=raw_response_artifact_id,
        call_delivery_id=call_delivery_id,
        created_at=created_at or utc_timestamp(),
    )


def _item_vote(packet: JudgePacket, item_id: str, entry: object) -> ItemVote:
    assert isinstance(entry, dict)
    return ItemVote(
        item_id=packet.item(item_id).item_id,
        score=entry["score"],
        citations=tuple(
            VoteCitation(anchor_id=citation["anchor_id"], note=citation.get("note", ""))
            for citation in entry["citations"]
        ),
        rationale=entry["rationale"],
        uncertainty=tuple(entry.get("uncertainty", ())),
        facts=tuple(
            JudgeFact(
                subject=fact["subject"],
                stance=fact["stance"],
                citation_anchor_ids=tuple(fact.get("citation_anchor_ids", ())),
            )
            for fact in entry.get("facts", ())
        ),
    )


# ------------------------------------------------------------------ aggregation


def mean_score(scores: Sequence[str]) -> str:
    """Exact mean of the valid anchor scores, rounded half-up to six places.

    ``1, 0.5, 1`` averages to ``0.833333``: the value Technical Spec E2E-21 names.
    """
    if not scores:
        raise JudgeInputsInvalid("a mean requires at least one score")
    total = sum((Decimal(score) for score in scores), Decimal(0))
    return quantize_anchor(total / Decimal(len(scores)))


def anchor_spread(scores: Sequence[str]) -> str:
    if not scores:
        return "0.000000"
    amounts = [Decimal(score) for score in scores]
    return quantize_anchor(max(amounts) - min(amounts))


def spread_levels(scores: Sequence[str]) -> int:
    """How many declared anchor steps separate the highest and lowest score.

    The pilot anchors are ``{0, 0.5, 1}``, so one step is ``0.5``. A panel triggers review at two
    steps — the full ``1`` max-min spread named in Technical Spec 15.3 and the "two anchor levels"
    of Architecture 8.8. A ``1 / 0.5 / 1`` panel is one step apart and stays ready, which is exactly
    the E2E-21 case.
    """
    if not scores:
        return 0
    amounts = [Decimal(score) for score in scores]
    return int((max(amounts) - min(amounts)) / ANCHOR_STEP)


def conflicting_subjects(votes: Sequence[ItemVote]) -> tuple[str, ...]:
    """Subjects one vote asserts and another denies; deterministic and reproducible."""
    stances: dict[str, set[str]] = {}
    for vote in votes:
        for fact in vote.facts:
            stances.setdefault(fact.subject.strip().lower(), set()).add(fact.stance)
    return tuple(sorted(subject for subject, seen in stances.items() if len(seen) > 1))


def comment_anchors(comments: Sequence[UntrustedComment]) -> frozenset[str]:
    return frozenset(comment.anchor_id for comment in comments)


def citation_triggers(
    *,
    votes: Sequence[ItemVote],
    item: PacketItem,
    panel: JudgePanel,
    untrusted: frozenset[str],
) -> tuple[ReviewTrigger, ...]:
    """Citations that exist but do not support the item are review evidence, not a vote.

    A vote citing only candidate comments is recorded as ``untrusted_comment_only``; any other
    out-of-scope citation is ``unsupported_citation``. Both go to review with the votes intact.
    """
    if not panel.disagreement.unsupported_citations:
        return ()
    triggers: set[ReviewTrigger] = set()
    for vote in votes:
        cited = vote.citation_anchor_ids
        if all(anchor in item.in_scope_anchor_ids for anchor in cited):
            continue
        if cited and all(anchor in untrusted for anchor in cited):
            if panel.disagreement.untrusted_comment_only:
                triggers.add(ReviewTrigger.UNTRUSTED_COMMENT_ONLY)
        else:
            triggers.add(ReviewTrigger.UNSUPPORTED_CITATION)
    return tuple(sorted(triggers, key=lambda trigger: trigger.value))


def audit_selected(packet: JudgePacket, panel: JudgePanel) -> bool:
    """Whether this packet belongs to the seeded audit sample (Technical Spec 15.3).

    The decision depends only on the packet digest and the panel's frozen audit seed, so the sample
    cannot be tuned by adding or removing packets, and a re-run audits the same ones.
    """
    percent = panel.disagreement.audit_sample_percent
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    material = f"{panel.disagreement.audit_seed}/{packet.digest()}".encode()
    draw = int(sha256_bytes(material)[7:15], 16) % 100
    return draw < percent


def aggregate(
    *,
    packet: JudgePacket,
    panel: JudgePanel,
    votes: Sequence[JudgeVote],
    deliveries: Sequence[JudgeDelivery],
    adjudications: Sequence[AdjudicationDecision] = (),
    audit_selected: bool = False,
) -> JudgementResult:
    """Average the valid votes, decide review triggers, and apply recorded adjudications.

    ``mean_score`` stays ``None`` until every required vote is valid, so a partial panel cannot
    reach a scorecard through this type. An adjudicated item reports the reviewer's score with the
    original votes still attached and visible.
    """
    required = panel.votes_required
    untrusted = comment_anchors(packet.untrusted_comments)
    rejected_seen = any(
        delivery.status in {"invalid", "transport_failure"} for delivery in deliveries
    )
    outcomes: list[ItemOutcome] = []
    # One vote per index: a caller that supplies the same index twice cannot inflate the panel.
    ordered_votes: list[JudgeVote] = []
    seen_indexes: set[int] = set()
    for vote in sorted(votes, key=lambda entry: entry.vote_index):
        if vote.vote_index in seen_indexes:
            continue
        seen_indexes.add(vote.vote_index)
        ordered_votes.append(vote)
    for item in packet.items:
        item_votes = [vote.item(item.item_id) for vote in ordered_votes if _has(vote, item.item_id)]
        scores = [vote.score for vote in item_votes]
        # Triggers are computed from whatever votes exist, so a reviewer sees why an item looks
        # suspicious even while the panel is incomplete.
        triggers: set[ReviewTrigger] = set()
        if (
            len(item_votes) >= 2
            and spread_levels(scores) >= panel.disagreement.anchor_spread_levels
        ):
            triggers.add(ReviewTrigger.ANCHOR_SPREAD)
        if panel.disagreement.conflicting_facts and conflicting_subjects(item_votes):
            triggers.add(ReviewTrigger.CONFLICTING_FACTS)
        triggers.update(
            citation_triggers(votes=item_votes, item=item, panel=panel, untrusted=untrusted)
        )
        if len(item_votes) != required:
            triggers.add(ReviewTrigger.MISSING_REQUIRED_VOTE)
            if rejected_seen:
                triggers.add(ReviewTrigger.INVALID_VOTE_RECOVERY_EXHAUSTED)
            outcomes.append(
                ItemOutcome(
                    item_id=item.item_id,
                    dimension=item.dimension,
                    status="infra_blocked",
                    mean_score=None,
                    vote_scores=tuple(scores),
                    vote_count=len(item_votes),
                    required_votes=required,
                    spread=None,
                    triggers=_ordered(triggers),
                    reason="fewer valid votes than the panel requires",
                )
            )
            continue
        decision = latest_decision(packet, adjudications, item.item_id)
        if decision is not None:
            outcomes.append(
                ItemOutcome(
                    item_id=item.item_id,
                    dimension=item.dimension,
                    status="ready",
                    mean_score=decision.score,
                    vote_scores=tuple(scores),
                    vote_count=len(item_votes),
                    required_votes=required,
                    spread=anchor_spread(scores),
                    triggers=(),
                    source="adjudication",
                    adjudication_id=adjudication_id(decision),
                    reason=f"adjudicated by {decision.reviewer_subject}",
                )
            )
            continue
        outcomes.append(
            ItemOutcome(
                item_id=item.item_id,
                dimension=item.dimension,
                status="needs_review" if triggers else "ready",
                mean_score=mean_score(scores),
                vote_scores=tuple(scores),
                vote_count=len(item_votes),
                required_votes=required,
                spread=anchor_spread(scores),
                triggers=_ordered(triggers),
            )
        )
    if audit_selected:
        outcomes = [
            item.model_copy(
                update={
                    "status": "needs_review",
                    "triggers": _ordered({*item.triggers, ReviewTrigger.AUDIT_SAMPLE}),
                }
            )
            for item in outcomes
        ]
    return JudgementResult(
        packet_id=packet.packet_id,
        packet_digest=packet.digest(),
        rubric_digest=packet.rubric_digest,
        panel_id=packet.panel_id,
        panel_version=packet.panel_version,
        panel_digest=packet.panel_digest,
        status=_packet_status(outcomes),
        votes_required=required,
        items=tuple(outcomes),
        deliveries=tuple(
            sorted(deliveries, key=lambda item: (item.vote_index, item.delivery_index))
        ),
        valid_vote_indexes=tuple(sorted(seen_indexes)),
        untrusted_comment_count=packet.instruction_attempt_count,
    )


def _has(vote: JudgeVote, item_id: str) -> bool:
    return any(entry.item_id == item_id for entry in vote.items)


def _ordered(triggers: set[ReviewTrigger]) -> tuple[ReviewTrigger, ...]:
    return tuple(sorted(triggers, key=lambda trigger: trigger.value))


def _packet_status(
    outcomes: Sequence[ItemOutcome],
) -> Literal["ready", "needs_review", "infra_blocked"]:
    statuses = {outcome.status for outcome in outcomes}
    if "infra_blocked" in statuses:
        return "infra_blocked"
    if "needs_review" in statuses:
        return "needs_review"
    return "ready"


def adjudication_id(decision: AdjudicationDecision) -> str:
    """The reviewer's decision identity; the stored row uses it as its primary key."""
    return adjudication_identity(decision)


def decisions_for_packet(
    packet: JudgePacket, adjudications: Sequence[AdjudicationDecision]
) -> tuple[AdjudicationDecision, ...]:
    """Only decisions that belong to *this* packet, under this packet's rubric and panel.

    Scoping by packet identity is what stops a reviewer's decision on one candidate from rewriting
    another candidate's item score, and the digest comparison refuses a decision recorded under a
    different rubric or panel.
    """
    return tuple(
        decision
        for decision in adjudications
        if decision.packet_id == packet.packet_id
        and decision.packet_digest == packet.digest()
        and decision.rubric_digest == packet.rubric_digest
        and decision.panel_digest == packet.panel_digest
    )


def latest_decision(
    packet: JudgePacket,
    adjudications: Sequence[AdjudicationDecision],
    item_id: str,
) -> AdjudicationDecision | None:
    """The effective decision for one item: the newest one that no decision supersedes.

    A decision's ``supersedes_id`` names the decision it replaces, so the *referenced* identity is
    the superseded one. Anything a newer decision supersedes is excluded.
    """
    scoped = decisions_for_packet(packet, adjudications)
    superseded = {
        decision.supersedes_id for decision in scoped if decision.supersedes_id is not None
    }
    candidates = [
        decision
        for decision in scoped
        if decision.item_id == item_id and adjudication_id(decision) not in superseded
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda decision: (decision.decided_at, decision.reviewer_subject))


# ------------------------------------------------------------------ review


def review_entry(
    *,
    packet: JudgePacket,
    result: JudgementResult,
    votes: Sequence[JudgeVote],
    adjudications: Sequence[AdjudicationDecision] = (),
) -> ReviewQueueEntry:
    """The reviewer's view of one packet: anonymized evidence, every vote, every trigger."""
    return ReviewQueueEntry(
        packet=packet,
        result_status=result.status,
        triggers_by_item={
            item.item_id: tuple(trigger.value for trigger in item.triggers) for item in result.items
        },
        votes=tuple(sorted(votes, key=lambda vote: vote.vote_index)),
        deliveries=result.deliveries,
        adjudications=tuple(adjudications),
    )


def build_adjudication(
    *,
    packet: JudgePacket,
    rubric: JudgeRubric,
    panel: JudgePanel,
    item_id: str,
    score: str,
    cited_anchor_ids: Sequence[str],
    reason: str,
    reviewer_subject: str,
    votes: Sequence[JudgeVote],
    reviewed_vote_indexes: Sequence[int],
    decided_at: str | None = None,
    supersedes_id: str | None = None,
) -> AdjudicationDecision:
    """Validate a reviewer's override against the packet and the retained votes.

    A reviewer must score a declared anchor of that item, cite packet anchors, name the votes they
    read and give a substantive reason. Nothing else can override an item score, and no override
    deletes a vote.
    """
    item = packet.item(item_id)
    if rubric.digest() != packet.rubric_digest or panel.digest() != packet.panel_digest:
        raise VoteRejected(
            "panel_mismatch",
            "a reviewer decision must be recorded under the packet's own rubric and panel",
        )
    if rubric.item(item_id).dimension != item.dimension:
        raise VoteRejected("unknown_item", "the rubric and packet disagree about this item")
    if score not in {anchor.value for anchor in item.anchors}:
        raise VoteRejected("anchor_not_in_set", f"{item_id} cannot be adjudicated to {score}")
    known = {span.anchor_id for span in packet.spans} | comment_anchors(packet.untrusted_comments)
    unknown = [anchor for anchor in cited_anchor_ids if anchor not in known]
    if unknown:
        raise VoteRejected("citation_not_in_packet", f"unknown anchors {unknown}")
    retained = {
        vote.vote_index for vote in votes if any(entry.item_id == item_id for entry in vote.items)
    }
    missing = sorted({*reviewed_vote_indexes} - retained)
    if not reviewed_vote_indexes or missing:
        raise VoteRejected(
            "missing_item",
            f"every reviewed vote must be a retained vote for {item_id}; missing {missing}",
        )
    if len(reason.strip()) < 16:
        raise VoteRejected("schema_violation", "an adjudication needs a substantive reason")
    return AdjudicationDecision(
        packet_id=packet.packet_id,
        packet_digest=packet.digest(),
        item_id=item_id,
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.version,
        rubric_digest=rubric.digest(),
        panel_digest=packet.panel_digest,
        score=score,
        cited_anchor_ids=tuple(cited_anchor_ids),
        reason=reason.strip(),
        reviewed_vote_indexes=tuple(sorted(reviewed_vote_indexes)),
        reviewer_subject=reviewer_subject,
        supersedes_id=supersedes_id,
        decided_at=decided_at or utc_timestamp(),
    )


def dimension_of(rubric: JudgeRubric, item_id: str) -> ScoreDimension:
    return rubric.item(item_id).dimension
