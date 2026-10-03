"""Self-repair as an explicitly versioned candidate protocol (Technical Spec 4.3, 17.2).

Pure data and rules only. The repair controller (orchestration), the round repository
(persistence) and the admitted fixtures all build on these types so that what a repair run
records, what a model may see and what the protocol selects agree by construction.

The invariants this module makes structural rather than conventional:

* **Ordered, immutable rounds.** Every round retains its candidate digest and revision, the exact
  prompt and request digests sent to the model, the feedback it was given and its own cost. Rounds
  are appended, never edited (persisted rounds are insert-only for the same reason candidates are).
* **Public feedback only.** A repair round may be driven only by ``RepairFeedback`` built from the
  frozen public case inventory. Hidden case results cannot be attached to feedback at all: the
  constructors accept only public inventories and :func:`assert_public_feedback` rejects anything
  else before a prompt is built (Technical Spec 17.2: no hidden feedback).
* **The protocol fixes when rounds stop.** ``RepairLimits`` freezes maximum rounds, model calls,
  tokens, time and cumulative cost before any candidate exists. A run over any limit raises
  :class:`RepairBudgetExhausted`; there is no discretionary extra round.
* **The protocol fixes which artifact is final.** :func:`select_final` is a pure function of the
  frozen selection rule and the rounds' own *public* results. It has no parameter through which a
  hidden score could arrive, so the final artifact is never hidden-score best-of (Spec 4.3).
* **Infrastructure recovery is not a repair round.** :func:`redeliver` is the only recovery
  transition: it adds a delivery to the last round and its cost to the accumulated spend. It
  cannot create a round, move the round counter backwards or erase spent budget.

Initial and final native correctness are evaluation outcomes, recorded afterwards in
``RepairMetrics``; selection never reads them.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.models import (
    SAFE_JSON_INTEGER_MAX,
    ContractModel,
    ProtocolDefinition,
    Slug,
)
from polycodebench_core.solve_contracts import SolveError, SolveProtocol

Tokens = Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
DigestStr = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class RepairError(SolveError):
    """A repair-protocol violation. Never a model failure and never an infrastructure result."""

    code = "REPAIR_PROTOCOL_VIOLATION"
    status_code = 422


class HiddenFeedbackRejected(RepairError):
    """Feedback carries hidden evidence. Repair feedback is public-only by protocol."""

    code = "HIDDEN_FEEDBACK_REJECTED"


class RepairBudgetExhausted(RepairError):
    """The frozen repair limits forbid another round. A protocol stop, not a retry hint."""

    code = "REPAIR_BUDGET_EXHAUSTED"

    def __init__(self, dimension: str) -> None:
        super().__init__(f"repair budget exhausted: {dimension}")
        self.dimension = dimension


class RepairProtocolError(RepairError):
    """The run state does not permit the requested transition."""

    code = "REPAIR_STATE_INVALID"


# --------------------------------------------------------------------------- frozen protocol


class RepairLimits(ContractModel):
    """Frozen before any candidate exists. A changed limit is a new protocol version."""

    kind: Literal["repair_limits"] = "repair_limits"
    maximum_repair_rounds: Annotated[int, Field(strict=True, ge=0, le=100)]
    maximum_model_calls: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    maximum_active_seconds: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    maximum_input_tokens: Tokens
    maximum_output_tokens: Tokens
    maximum_cumulative_cost_micros: Annotated[
        int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)
    ] | None

    @model_validator(mode="after")
    def coherent(self) -> RepairLimits:
        if self.maximum_model_calls < 1 + self.maximum_repair_rounds:
            raise ValueError(
                "the model-call budget must cover the initial round and every repair round"
            )
        return self


class RepairProtocol(ContractModel):
    """An explicitly versioned self-repair candidate protocol over one base solve protocol."""

    kind: Literal["repair_protocol"] = "repair_protocol"
    protocol_id: Slug
    version: Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
    base: SolveProtocol
    feedback_policy: Literal["public_tests_only"]
    selection_rule: Literal["final_round", "first_public_pass"]
    limits: RepairLimits
    prompt_policy: Literal["pcb-repair-v1"]

    @model_validator(mode="after")
    def coherent(self) -> RepairProtocol:
        # Repair rounds are single model calls with feedback in the message; a base protocol that
        # gives the model an interactive agent is a different repair shape and gets its own
        # version rather than being silently accepted here.
        if self.base.mode != "single_shot":
            raise ValueError("self-repair-v1 rounds are single-shot calls over the base protocol")
        if not self.base.single_shot_extraction:
            raise ValueError("the base protocol must declare its extraction rule")
        return self

    @property
    def digest(self) -> str:
        return canonical_document_digest(self)

    def to_definition(self) -> ProtocolDefinition:
        """Gateway-facing identity of one repair round's model call."""
        base = self.base.to_definition()
        return base.model_copy(
            update={
                "protocol_id": self.protocol_id,
                "version": self.version,
                "public_test_feedback": True,
                "hidden_feedback": False,
            }
        )


# ---------------------------------------------------------------------------------- feedback


class PublicCaseResult(ContractModel):
    """One public case outcome. Public results are solver-visible evidence by definition."""

    kind: Literal["public_case_result"] = "public_case_result"
    case_id: Slug
    outcome: Literal["pass", "fail", "error", "skipped"]
    reason: str = Field(default="", min_length=0, max_length=400)


class RepairFeedback(ContractModel):
    """The only feedback a repair round may receive: public case results of the previous round."""

    kind: Literal["repair_feedback"] = "repair_feedback"
    for_round: Annotated[int, Field(strict=True, ge=1, le=SAFE_JSON_INTEGER_MAX)]
    source: Literal["public_test_groups"]
    results: tuple[PublicCaseResult, ...] = Field(min_length=0, max_length=200)

    @model_validator(mode="after")
    def results_are_unique(self) -> RepairFeedback:
        identifiers = [result.case_id for result in self.results]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("feedback reports each public case once")
        return self

    @property
    def digest(self) -> str:
        return canonical_document_digest(self)

    def render(self) -> str:
        """Deterministic feedback text for the round prompt (prompt policy pcb-repair-v1)."""
        if not self.results:
            return "Public test feedback: no public cases are declared for this task."
        lines = ["Public test feedback (these are the only results you may use to repair):"]
        for result in sorted(self.results, key=lambda entry: entry.case_id):
            suffix = f" - {result.reason}" if result.reason else ""
            lines.append(f"- {result.case_id}: {result.outcome}{suffix}")
        return "\n".join(lines)


def feedback_from_public(
    for_round: int,
    results: tuple[PublicCaseResult, ...],
    *,
    public_case_ids: frozenset[str] | set[str],
) -> RepairFeedback:
    """Build feedback from public results.

    A case outside the public inventory is refused here, before any prompt exists - this is the
    only constructor the repair controller uses.
    """
    hidden = sorted({result.case_id for result in results} - set(public_case_ids))
    if hidden:
        raise HiddenFeedbackRejected(
            f"feedback references non-public cases: {hidden}; hidden results never reach repair"
        )
    return RepairFeedback(
        schema_version=1,
        kind="repair_feedback",
        for_round=for_round,
        source="public_test_groups",
        results=results,
    )


def assert_public_feedback(feedback: RepairFeedback, *, public_case_ids: set[str]) -> None:
    hidden = sorted({result.case_id for result in feedback.results} - set(public_case_ids))
    if hidden:
        raise HiddenFeedbackRejected(f"feedback references non-public cases: {hidden}")


# ------------------------------------------------------------------------------------- rounds


class RepairRound(ContractModel):
    """One ordered candidate round: frozen candidate, exact request, feedback and cost."""

    kind: Literal["repair_round"] = "repair_round"
    round_index: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    candidate_revision: Annotated[int, Field(strict=True, ge=1, le=SAFE_JSON_INTEGER_MAX)]
    candidate_digest: DigestStr | None = None
    prompt_digest: DigestStr
    request_digest: DigestStr
    feedback_digest: DigestStr | None = None
    deliveries: Annotated[int, Field(strict=True, ge=1, le=100)]
    state: Literal["frozen", "model_failure"]
    public_results: tuple[PublicCaseResult, ...] = Field(max_length=200)
    input_tokens: Tokens
    output_tokens: Tokens
    cost_micros: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]

    @model_validator(mode="after")
    def coherent(self) -> RepairRound:
        if self.round_index == 0 and self.feedback_digest is not None:
            raise ValueError("the initial round receives no repair feedback")
        if self.round_index > 0 and self.feedback_digest is None:
            raise ValueError("a repair round runs on the feedback that drove it")
        if self.candidate_revision != self.round_index + 1:
            raise ValueError("candidate revision is the round index plus one")
        if self.state == "frozen" and self.candidate_digest is None:
            raise ValueError("a frozen round names its candidate")
        if self.state == "model_failure" and self.candidate_digest is not None:
            raise ValueError("a model-failed round froze no candidate")
        identifiers = [result.case_id for result in self.public_results]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("a round reports each public case once")
        return self


class RepairSpend(ContractModel):
    """Cumulative committed consumption. Infrastructure redelivery adds to it, never subtracts."""

    kind: Literal["repair_spend"] = "repair_spend"
    model_calls: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)] = 0
    redeliveries: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)] = 0
    input_tokens: Tokens = 0
    output_tokens: Tokens = 0
    cost_micros: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)] = 0
    active_ms: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)] = 0

    def plus(self, **delta: int) -> RepairSpend:
        data = self.model_dump()
        for key, value in delta.items():
            data[key] += value
        return RepairSpend(**data)


class RepairRun(ContractModel):
    """One self-repair run: ordered immutable rounds under one frozen protocol."""

    kind: Literal["repair_run"] = "repair_run"
    repair_run_id: Slug
    attempt_id: Slug
    protocol: RepairProtocol
    public_case_ids: tuple[Slug, ...]
    rounds: tuple[RepairRound, ...] = ()
    spend: RepairSpend
    state: Literal["open", "complete"] = "open"
    selected_round_index: Annotated[int, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def rounds_are_ordered(self) -> RepairRun:
        indices = [entry.round_index for entry in self.rounds]
        if indices != list(range(len(indices))):
            raise ValueError("rounds are dense and ordered from zero")
        if self.state == "complete" and self.selected_round_index is None:
            raise ValueError("a complete run names its selected round")
        if self.state == "open" and self.selected_round_index is not None:
            raise ValueError("an open run has not selected a final artifact")
        return self

    @property
    def digest(self) -> str:
        return canonical_document_digest(self)

    @property
    def current_round_index(self) -> int:
        return len(self.rounds)


class RoundTicket(ContractModel):
    """Permission to run exactly one round. One ticket, one logical model call."""

    kind: Literal["round_ticket"] = "round_ticket"
    round_index: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    prompt_digest: DigestStr
    request_digest: DigestStr
    feedback_digest: DigestStr | None = None

    @property
    def logical_call_key(self) -> str:
        """Transport redeliveries reuse this key: same logical call, more deliveries."""
        return f"repair-round-{self.round_index}"


class RepairMetrics(ContractModel):
    """What Technical Spec 17.2 requires preserved: initial/final outcomes, cost, round count."""

    kind: Literal["repair_metrics"] = "repair_metrics"
    selected_round_index: int
    repair_round_count: Annotated[int, Field(strict=True, ge=0, le=100)]
    cumulative_input_tokens: Tokens
    cumulative_output_tokens: Tokens
    cumulative_cost_micros: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    model_calls: Annotated[int, Field(strict=True, ge=1, le=1_000)]
    redeliveries: Annotated[int, Field(strict=True, ge=0, le=1_000)]
    initial_native_correct: bool | None = None
    final_native_correct: bool | None = None


class RepairCheckpoint(ContractModel):
    """Round-boundary checkpoint: everything needed to resume without inventing rounds."""

    kind: Literal["repair_checkpoint"] = "repair_checkpoint"
    repair_run_id: Slug
    attempt_id: Slug
    committed_rounds: Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]
    pending_round_index: Annotated[int, Field(strict=True, ge=0)] | None
    pending_delivery_ids: tuple[str, ...] = ()
    spend: RepairSpend
    run_digest: DigestStr

    @model_validator(mode="after")
    def coherent(self) -> RepairCheckpoint:
        if (
            self.pending_round_index is not None
            and self.pending_round_index != self.committed_rounds
        ):
            raise ValueError("only the current frontier round may be pending")
        return self


# ----------------------------------------------------------------------------- transitions


def _run_with(
    run: RepairRun,
    *,
    rounds: tuple[RepairRound, ...] | None = None,
    spend: RepairSpend | None = None,
    state: Literal["open", "complete"] | None = None,
    selected_round_index: int | None = None,
    selecting: bool = False,
) -> RepairRun:
    """Every transition reconstructs the run through the model, so validators always run."""
    return RepairRun(
        schema_version=run.schema_version,
        kind="repair_run",
        repair_run_id=run.repair_run_id,
        attempt_id=run.attempt_id,
        protocol=run.protocol,
        public_case_ids=run.public_case_ids,
        rounds=run.rounds if rounds is None else rounds,
        spend=run.spend if spend is None else spend,
        state=run.state if state is None else state,
        selected_round_index=(
            run.selected_round_index if not selecting else selected_round_index
        ),
    )


def start_repair_run(
    *,
    repair_run_id: str,
    attempt_id: str,
    protocol: RepairProtocol,
    public_case_ids: tuple[str, ...],
) -> RepairRun:
    if len(set(public_case_ids)) != len(public_case_ids):
        raise RepairProtocolError("public case ids must be unique")
    return RepairRun(
        schema_version=1,
        kind="repair_run",
        repair_run_id=repair_run_id,
        attempt_id=attempt_id,
        protocol=protocol,
        public_case_ids=tuple(public_case_ids),
        spend=RepairSpend(schema_version=1, kind="repair_spend"),
    )


def begin_round(
    run: RepairRun,
    *,
    prompt_digest: str,
    request_digest: str,
    feedback: RepairFeedback | None,
) -> RoundTicket:
    """Open the next round if and only if the frozen limits permit it."""
    if run.state != "open":
        raise RepairProtocolError("a complete run cannot open another round")
    limits = run.protocol.limits
    index = run.current_round_index
    if index > limits.maximum_repair_rounds:
        raise RepairBudgetExhausted("maximum_repair_rounds")
    if run.spend.model_calls >= limits.maximum_model_calls:
        raise RepairBudgetExhausted("maximum_model_calls")
    if run.spend.input_tokens >= limits.maximum_input_tokens:
        raise RepairBudgetExhausted("maximum_input_tokens")
    if run.spend.output_tokens >= limits.maximum_output_tokens:
        raise RepairBudgetExhausted("maximum_output_tokens")
    if run.spend.active_ms >= limits.maximum_active_seconds * 1000:
        raise RepairBudgetExhausted("maximum_active_seconds")
    cost_cap = limits.maximum_cumulative_cost_micros
    if cost_cap is not None and run.spend.cost_micros >= cost_cap:
        raise RepairBudgetExhausted("maximum_cumulative_cost_micros")
    if index == 0 and feedback is not None:
        raise RepairProtocolError("the initial round receives no repair feedback")
    if index > 0:
        if feedback is None:
            raise RepairProtocolError("a repair round runs on the feedback that drove it")
        if feedback.for_round != index:
            raise RepairProtocolError("feedback must be addressed to the round being opened")
        assert_public_feedback(feedback, public_case_ids=set(run.public_case_ids))
    return RoundTicket(
        schema_version=1,
        kind="round_ticket",
        round_index=index,
        prompt_digest=prompt_digest,
        request_digest=request_digest,
        feedback_digest=None if feedback is None else feedback.digest,
    )


def complete_round(
    run: RepairRun,
    ticket: RoundTicket,
    *,
    candidate_digest: str,
    public_results: tuple[PublicCaseResult, ...],
    input_tokens: int,
    output_tokens: int,
    cost_micros: int,
    active_ms: int,
    deliveries: int = 1,
) -> RepairRun:
    """Freeze one round's candidate and cost. Rounds are appended, never edited."""
    if ticket.round_index != run.current_round_index:
        raise RepairProtocolError("a round completes only at the run's current frontier")
    assert_public_results(public_results, public_case_ids=set(run.public_case_ids))
    round = RepairRound(
        schema_version=1,
        kind="repair_round",
        round_index=ticket.round_index,
        candidate_revision=ticket.round_index + 1,
        candidate_digest=candidate_digest,
        prompt_digest=ticket.prompt_digest,
        request_digest=ticket.request_digest,
        feedback_digest=ticket.feedback_digest,
        deliveries=deliveries,
        state="frozen",
        public_results=public_results,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micros=cost_micros,
    )
    return _run_with(
        run,
        rounds=(*run.rounds, round),
        spend=run.spend.plus(
            model_calls=1,
            redeliveries=max(0, deliveries - 1),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micros=cost_micros,
            active_ms=active_ms,
        ),
    )


def record_model_failure(
    run: RepairRun,
    ticket: RoundTicket,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_micros: int,
    active_ms: int,
) -> RepairRun:
    """A refused or invalid round is a recorded round with no candidate, not a hidden retry."""
    if ticket.round_index != run.current_round_index:
        raise RepairProtocolError("a failure records only at the run's current frontier")
    round = RepairRound(
        schema_version=1,
        kind="repair_round",
        round_index=ticket.round_index,
        candidate_revision=ticket.round_index + 1,
        candidate_digest=None,
        prompt_digest=ticket.prompt_digest,
        request_digest=ticket.request_digest,
        feedback_digest=ticket.feedback_digest,
        deliveries=1,
        state="model_failure",
        public_results=(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micros=cost_micros,
    )
    return _run_with(
        run,
        rounds=(*run.rounds, round),
        spend=run.spend.plus(
            model_calls=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micros=cost_micros,
            active_ms=active_ms,
        ),
    )


def redeliver(
    run: RepairRun, *, input_tokens: int, output_tokens: int, cost_micros: int, active_ms: int
) -> RepairRun:
    """Infrastructure recovery at a round boundary: more deliveries, same round, spend grows.

    This is the only recovery transition (Technical Spec 7.4/7.5): it cannot open a round, cannot
    skip one and cannot erase what has already been spent.
    """
    if run.state != "open":
        raise RepairProtocolError("a complete run has nothing to redeliver")
    if not run.rounds:
        raise RepairProtocolError("redelivery applies to a round already in flight")
    last = run.rounds[-1]
    if last.state != "frozen":
        raise RepairProtocolError("redelivery applies to a completed round's pending delivery")
    updated = last.model_copy(update={"deliveries": last.deliveries + 1})
    return _run_with(
        run,
        rounds=(*run.rounds[:-1], updated),
        spend=run.spend.plus(
            redeliveries=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micros=cost_micros,
            active_ms=active_ms,
        ),
    )


def assert_public_results(
    results: tuple[PublicCaseResult, ...], *, public_case_ids: set[str]
) -> None:
    hidden = sorted({result.case_id for result in results} - set(public_case_ids))
    if hidden:
        raise HiddenFeedbackRejected(f"round reports non-public cases: {hidden}")


def select_final(run: RepairRun) -> int:
    """The protocol-selected final round. Pure function of the rule and PUBLIC results only.

    There is deliberately no parameter for hidden outcomes: the final artifact is never the
    hidden-score best round (Technical Spec 4.3, PCB-26-2).
    """
    if run.state != "open":
        raise RepairProtocolError("selection runs once, on an open run")
    if not run.rounds:
        raise RepairProtocolError("a run with no frozen round has nothing to select")
    candidates = [entry for entry in run.rounds if entry.state == "frozen"]
    if not candidates:
        raise RepairProtocolError("no round froze a candidate")
    if run.protocol.selection_rule == "first_public_pass":
        for entry in candidates:
            if entry.public_results and all(
                result.outcome == "pass" for result in entry.public_results
            ):
                return entry.round_index
    return candidates[-1].round_index


def freeze_selection(run: RepairRun) -> RepairRun:
    """Complete the run with the protocol-selected artifact. One-shot and irreversible."""
    selected = select_final(run)
    return _run_with(run, state="complete", selecting=True, selected_round_index=selected)


def metrics_of(
    run: RepairRun,
    *,
    initial_native_correct: bool | None = None,
    final_native_correct: bool | None = None,
) -> RepairMetrics:
    """Cumulative cost and round counts straight from the run; outcomes filled by evaluation."""
    selected = run.selected_round_index
    if selected is None:
        raise RepairProtocolError("metrics describe a completed run")
    return RepairMetrics(
        schema_version=1,
        kind="repair_metrics",
        selected_round_index=selected,
        repair_round_count=max(0, len(run.rounds) - 1),
        cumulative_input_tokens=run.spend.input_tokens,
        cumulative_output_tokens=run.spend.output_tokens,
        cumulative_cost_micros=run.spend.cost_micros,
        model_calls=run.spend.model_calls,
        redeliveries=run.spend.redeliveries,
        initial_native_correct=initial_native_correct,
        final_native_correct=final_native_correct,
    )


def checkpoint_of(
    run: RepairRun,
    *,
    pending_round_index: int | None = None,
    pending_delivery_ids: tuple[str, ...] = (),
) -> RepairCheckpoint:
    return RepairCheckpoint(
        schema_version=1,
        kind="repair_checkpoint",
        repair_run_id=run.repair_run_id,
        attempt_id=run.attempt_id,
        committed_rounds=len(run.rounds),
        pending_round_index=pending_round_index,
        pending_delivery_ids=pending_delivery_ids,
        spend=run.spend,
        run_digest=run.digest,
    )


def restore_run(checkpoint: RepairCheckpoint, *, expected_run: RepairRun) -> RepairRun:
    """Recovery re-verifies the checkpoint against the run; drift is refused, not guessed."""
    if checkpoint.run_digest != expected_run.digest:
        raise RepairProtocolError("checkpoint does not belong to this run state")
    return expected_run


__all__ = [
    "HiddenFeedbackRejected",
    "PublicCaseResult",
    "RepairBudgetExhausted",
    "RepairCheckpoint",
    "RepairError",
    "RepairFeedback",
    "RepairLimits",
    "RepairMetrics",
    "RepairProtocol",
    "RepairProtocolError",
    "RepairRound",
    "RepairRun",
    "RepairSpend",
    "RoundTicket",
    "assert_public_feedback",
    "assert_public_results",
    "begin_round",
    "checkpoint_of",
    "complete_round",
    "feedback_from_public",
    "freeze_selection",
    "metrics_of",
    "record_model_failure",
    "redeliver",
    "restore_run",
    "select_final",
    "start_repair_run",
]
