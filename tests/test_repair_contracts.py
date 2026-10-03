"""Self-repair protocol contracts: rounds, feedback isolation, budgets and selection (Prompt 26).

Pure-logic regression cases for the E2E-37 semantics:

* every round retains its candidate, prompt, feedback and cost (PCB-26-1);
* the frozen limits decide when rounds stop - never a discretionary extra round (PCB-26-1);
* the final artifact is the protocol-selected round, never the hidden-score best round, and
  initial/final outcomes stay distinct from cumulative cost (PCB-26-2);
* infrastructure redelivery is not a repair round and never erases spent budget (PCB-26-3);
* hidden results cannot enter feedback or be reported by a round (PCB-26-4).
"""

from __future__ import annotations

import pytest
from polycodebench_core.repair_contracts import (
    HiddenFeedbackRejected,
    PublicCaseResult,
    RepairBudgetExhausted,
    RepairProtocolError,
    RepairSpend,
    begin_round,
    checkpoint_of,
    complete_round,
    feedback_from_public,
    freeze_selection,
    metrics_of,
    record_model_failure,
    redeliver,
    restore_run,
    select_final,
    start_repair_run,
)
from polycodebench_core.solve_contracts import SolveProtocol

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
PUBLIC = ("public-a", "public-b")


def base_protocol() -> SolveProtocol:
    return SolveProtocol(
        schema_version=1,
        kind="solve_protocol",
        protocol_id="single-shot-v1",
        version=1,
        mode="single_shot",
        tools=[],
        public_test_feedback=False,
        single_shot_extraction="json_envelope",
        context={
            "schema_version": 1,
            "kind": "context_policy",
            "retain_recent_turns": 8,
            "max_input_context_tokens": 60000,
            "token_counter": "utf8_bytes_div4_v1",
            "summarizer": "none",
        },
        budget={
            "schema_version": 1,
            "kind": "solve_budget",
            "model_turns": 1,
            "tool_calls": 0,
            "active_solve_seconds": 180,
            "per_command_seconds": None,
            "input_tokens": 32000,
            "output_tokens": 8000,
        },
        freeze_on_budget_exhaustion=False,
        prompt_policy="pcb-solve-v1",
    )


def protocol(**limit_overrides: object):
    limits = {
        "schema_version": 1,
        "kind": "repair_limits",
        "maximum_repair_rounds": 2,
        "maximum_model_calls": 3,
        "maximum_active_seconds": 600,
        "maximum_input_tokens": 100_000,
        "maximum_output_tokens": 20_000,
        "maximum_cumulative_cost_micros": None,
    }
    limits.update(limit_overrides)
    from polycodebench_core.repair_contracts import RepairProtocol

    return RepairProtocol(
        schema_version=1,
        kind="repair_protocol",
        protocol_id="self-repair-v1",
        version=1,
        base=base_protocol(),
        feedback_policy="public_tests_only",
        selection_rule="final_round",
        limits=limits,  # type: ignore[arg-type]
        prompt_policy="pcb-repair-v1",
    )


def result(case_id: str, outcome: str = "fail", reason: str = "") -> PublicCaseResult:
    return PublicCaseResult(
        schema_version=1,
        kind="public_case_result",
        case_id=case_id,
        outcome=outcome,  # type: ignore[arg-type]
        reason=reason,
    )


def run_with_rounds(rounds: int = 2, **limit_overrides: object):
    run = start_repair_run(
        repair_run_id="repair-1",
        attempt_id="attempt-1",
        protocol=protocol(**limit_overrides),
        public_case_ids=PUBLIC,
    )
    for index in range(rounds):
        feedback = (
            feedback_from_public(index, (result("public-a"),), public_case_ids=set(PUBLIC))
            if index
            else None
        )
        ticket = begin_round(
            run,
            prompt_digest=DIGEST_A,
            request_digest=DIGEST_B,
            feedback=feedback,
        )
        run = complete_round(
            run,
            ticket,
            candidate_digest=DIGEST_C,
            public_results=(result("public-a", "pass" if index == rounds - 1 else "fail"),),
            input_tokens=100,
            output_tokens=50,
            cost_micros=10,
            active_ms=1000,
        )
    return run


# ------------------------------------------------------------------------------- PCB-26-1


def test_every_round_retains_candidate_prompt_feedback_and_cost() -> None:
    run = run_with_rounds(2)
    initial, repair = run.rounds
    assert initial.candidate_digest == DIGEST_C and initial.candidate_revision == 1
    assert initial.prompt_digest == DIGEST_A and initial.request_digest == DIGEST_B
    assert initial.feedback_digest is None and initial.input_tokens == 100
    assert repair.candidate_revision == 2
    assert repair.feedback_digest is not None and repair.feedback_digest != initial.prompt_digest
    assert repair.cost_micros == 10
    assert run.spend.model_calls == 2 and run.spend.cost_micros == 20


def test_the_protocol_fixes_when_rounds_stop() -> None:
    run = run_with_rounds(3)
    with pytest.raises(RepairBudgetExhausted, match="maximum_repair_rounds"):
        begin_round(run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)
    # Token and cost ceilings stop the run just as firmly as the round ceiling.
    run = run_with_rounds(1, maximum_repair_rounds=5, maximum_model_calls=6)
    heavy = redeliver(run, input_tokens=100_000, output_tokens=20_000, cost_micros=1, active_ms=1)
    with pytest.raises(RepairBudgetExhausted, match="maximum_input_tokens"):
        begin_round(heavy, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)
    run = run_with_rounds(
        1,
        maximum_repair_rounds=5,
        maximum_model_calls=6,
        maximum_cumulative_cost_micros=5,
    )
    with pytest.raises(RepairBudgetExhausted, match="maximum_cumulative_cost_micros"):
        begin_round(run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)


def test_limits_are_coherent_and_repair_rounds_require_feedback() -> None:
    from polycodebench_core.repair_contracts import RepairLimits

    with pytest.raises(ValueError, match="model-call budget"):
        RepairLimits(
            schema_version=1,
            kind="repair_limits",
            maximum_repair_rounds=5,
            maximum_model_calls=2,
            maximum_active_seconds=60,
            maximum_input_tokens=1,
            maximum_output_tokens=1,
            maximum_cumulative_cost_micros=None,
        )
    run = start_repair_run(
        repair_run_id="repair-1",
        attempt_id="attempt-1",
        protocol=protocol(),
        public_case_ids=PUBLIC,
    )
    early_feedback = feedback_from_public(
        1, (result("public-a"),), public_case_ids=set(PUBLIC)
    )
    with pytest.raises(RepairProtocolError, match="receives no repair feedback"):
        begin_round(
            run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=early_feedback
        )
    ticket = begin_round(run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)
    run = complete_round(
        run,
        ticket,
        candidate_digest=DIGEST_C,
        public_results=(),
        input_tokens=1,
        output_tokens=1,
        cost_micros=1,
        active_ms=1,
    )
    with pytest.raises(RepairProtocolError, match="feedback that drove it"):
        begin_round(run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)
    mismatched = feedback_from_public(9, (result("public-a"),), public_case_ids=set(PUBLIC))
    with pytest.raises(RepairProtocolError, match="addressed to the round"):
        begin_round(run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=mismatched)


def test_model_failure_round_is_recorded_without_a_candidate() -> None:
    run = start_repair_run(
        repair_run_id="repair-1",
        attempt_id="attempt-1",
        protocol=protocol(),
        public_case_ids=PUBLIC,
    )
    ticket = begin_round(run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)
    run = record_model_failure(
        run, ticket, input_tokens=10, output_tokens=5, cost_micros=3, active_ms=100
    )
    assert run.rounds[0].state == "model_failure"
    assert run.rounds[0].candidate_digest is None
    assert run.spend.model_calls == 1 and run.spend.cost_micros == 3


# ------------------------------------------------------------------------------- PCB-26-2


def test_final_selection_is_protocol_rule_not_hidden_best_of() -> None:
    """Round 0 is the hidden-score best round; the protocol still selects the final round."""
    run = run_with_rounds(2)
    done = freeze_selection(run)
    assert done.selected_round_index == 1
    # Initial and final native correctness are evaluation outcomes, kept distinct from cost.
    metrics = metrics_of(
        done, initial_native_correct=False, final_native_correct=True
    )
    assert metrics.initial_native_correct is False
    assert metrics.final_native_correct is True
    assert metrics.repair_round_count == 1
    assert metrics.cumulative_cost_micros == done.spend.cost_micros


def test_first_public_pass_uses_public_results_only() -> None:
    from polycodebench_core.repair_contracts import RepairProtocol

    protocol_first = protocol().model_copy(
        update={"selection_rule": "first_public_pass"}
    )
    protocol_first = RepairProtocol.model_validate(protocol_first.model_dump())
    run = start_repair_run(
        repair_run_id="repair-1",
        attempt_id="attempt-1",
        protocol=protocol_first,
        public_case_ids=PUBLIC,
    )
    # Round 0 passes publicly; round 1 is later and also passes. first_public_pass selects 0.
    for index in range(2):
        feedback = (
            feedback_from_public(index, (result("public-a", "pass"),), public_case_ids=set(PUBLIC))
            if index
            else None
        )
        ticket = begin_round(
            run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=feedback
        )
        run = complete_round(
            run,
            ticket,
            candidate_digest=DIGEST_C,
            public_results=(result("public-a", "pass"),),
            input_tokens=1,
            output_tokens=1,
            cost_micros=1,
            active_ms=1,
        )
    assert select_final(run) == 0
    with pytest.raises(RepairProtocolError, match="nothing to select"):
        select_final(
            start_repair_run(
                repair_run_id="repair-2",
                attempt_id="attempt-2",
                protocol=protocol_first,
                public_case_ids=PUBLIC,
            )
        )


def test_selection_is_one_shot_and_complete_runs_stay_closed() -> None:
    done = freeze_selection(run_with_rounds(1))
    with pytest.raises(RepairProtocolError, match="runs once"):
        select_final(done)
    with pytest.raises(RepairProtocolError, match="cannot open another round"):
        begin_round(done, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)


# ------------------------------------------------------------------------------- PCB-26-3


def test_redelivery_is_not_a_round_and_never_erases_spend() -> None:
    run = run_with_rounds(1)
    spent_before = run.spend
    recovered = redeliver(run, input_tokens=7, output_tokens=3, cost_micros=2, active_ms=50)
    assert len(recovered.rounds) == len(run.rounds)
    assert recovered.rounds[-1].deliveries == run.rounds[-1].deliveries + 1
    assert recovered.spend.redeliveries == 1
    assert recovered.spend.model_calls == spent_before.model_calls
    assert recovered.spend.cost_micros == spent_before.cost_micros + 2
    assert recovered.spend.input_tokens == spent_before.input_tokens + 7
    # Recovery continues the same round frontier: the next round index is unchanged.
    assert recovered.current_round_index == run.current_round_index


def test_checkpoint_binds_the_run_and_drift_is_refused() -> None:
    run = run_with_rounds(1)
    checkpoint = checkpoint_of(run, pending_round_index=1, pending_delivery_ids=("call-1",))
    assert restore_run(checkpoint, expected_run=run) is run
    tampered = run_with_rounds(2)
    with pytest.raises(RepairProtocolError, match="does not belong"):
        restore_run(checkpoint, expected_run=tampered)


def test_spend_is_monotonic_under_redelivery() -> None:
    spend = RepairSpend(schema_version=1, kind="repair_spend", cost_micros=5)
    grown = spend.plus(cost_micros=3, redeliveries=1)
    assert grown.cost_micros == 8 and spend.cost_micros == 5


# ------------------------------------------------------------------------------- PCB-26-4


def test_hidden_results_cannot_become_feedback() -> None:
    with pytest.raises(HiddenFeedbackRejected, match="hidden results never reach repair"):
        feedback_from_public(
            1, (result("hidden-case"),), public_case_ids=set(PUBLIC)
        )
    mixed = feedback_from_public(
        1, (result("public-a"),), public_case_ids=set(PUBLIC)
    )
    from polycodebench_core.repair_contracts import assert_public_feedback

    with pytest.raises(HiddenFeedbackRejected, match="non-public"):
        assert_public_feedback(
            mixed.model_copy(
                update={"results": (result("public-a"), result("hidden-case"))}
            ),
            public_case_ids=set(PUBLIC),
        )


def test_hidden_results_cannot_be_reported_by_a_round() -> None:
    from polycodebench_core.repair_contracts import assert_public_results

    with pytest.raises(HiddenFeedbackRejected, match="non-public"):
        assert_public_results((result("hidden-case"),), public_case_ids=set(PUBLIC))


def test_feedback_rendering_is_deterministic_and_public_only() -> None:
    feedback = feedback_from_public(
        1,
        (result("public-b", "fail", "assertion failed"), result("public-a", "pass")),
        public_case_ids=set(PUBLIC),
    )
    text = feedback.render()
    assert text == (
        "Public test feedback (these are the only results you may use to repair):\n"
        "- public-a: pass\n"
        "- public-b: fail - assertion failed"
    )
    assert feedback.digest == feedback.digest
