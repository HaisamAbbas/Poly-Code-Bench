"""Durable self-repair state: round-boundary checkpoints, redelivery and restart (Prompt 26).

PCB-26-3 at the persistence layer: recovering infrastructure does not grant additional repair
rounds or erase spent budget. Rounds are immutable evidence rows; the run row committed together
with a round is the round-boundary checkpoint; redelivery inserts one delivery and grows spend
under the same round frontier.

Skips when ``PCB_TEST_DATABASE_URL`` (and for artifacts, ``PCB_OBJECT_STORE_ENDPOINT``) are not
configured, like the other PostgreSQL suites.
"""

# ruff: noqa: F811 - pytest fixtures are imported from the PostgreSQL test module

from __future__ import annotations

import pytest
from polycodebench_core.application_errors import PersistenceConflict
from polycodebench_core.repair_contracts import (
    RepairBudgetExhausted,
    RepairProtocol,
    begin_round,
    complete_round,
    feedback_from_public,
    freeze_selection,
    redeliver,
    restore_run,
    start_repair_run,
)
from polycodebench_persistence.models import repair_round
from polycodebench_persistence.repair_state import NewDelivery, PostgresRepairRepository
from sqlalchemy.exc import DBAPIError
from test_model_gateway_postgres import (  # noqa: F401 - fixtures
    artifacts,
    build_world,
    database,
)
from test_repair_contracts import base_protocol, result

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
PUBLIC = ("public-a", "public-b")


def make_protocol(**limit_overrides: object) -> RepairProtocol:
    limits = {
        "schema_version": 1,
        "kind": "repair_limits",
        "maximum_repair_rounds": 1,
        "maximum_model_calls": 2,
        "maximum_active_seconds": 600,
        "maximum_input_tokens": 100_000,
        "maximum_output_tokens": 20_000,
        "maximum_cumulative_cost_micros": None,
    }
    limits.update(limit_overrides)
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


def new_run(attempt_id: str, **limit_overrides: object):
    return start_repair_run(
        repair_run_id=f"repair-{attempt_id[-8:]}",
        attempt_id=attempt_id,
        protocol=make_protocol(**limit_overrides),
        public_case_ids=PUBLIC,
    )


def first_round(run):
    ticket = begin_round(run, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=None)
    return complete_round(
        run,
        ticket,
        candidate_digest=DIGEST_C,
        public_results=(result("public-a", "fail"),),
        input_tokens=100,
        output_tokens=50,
        cost_micros=10,
        active_ms=1000,
    )


def test_round_boundary_checkpoint_roundtrip(database, artifacts) -> None:
    world = build_world(database, artifacts)
    repository = PostgresRepairRepository(database.engine)
    run = new_run(str(world.attempts[0]))
    repository.create_run(run)
    run = first_round(run)
    repository.commit_round(
        run,
        expected_rounds=0,
        delivery=NewDelivery(
            delivery_index=1, input_tokens=100, output_tokens=50, cost_micros=10, active_ms=1000
        ),
    )
    restored = repository.load_run(run.repair_run_id)
    assert restored.digest == run.digest
    assert [entry.round_index for entry in restored.rounds] == [0]
    assert restored.rounds[0].candidate_digest == DIGEST_C
    checkpoint = repository.checkpoint(run.repair_run_id, pending_round_index=1)
    assert checkpoint.committed_rounds == 1
    assert restore_run(checkpoint, expected_run=restored) is restored


def test_stale_controller_cannot_advance_the_round_frontier(database, artifacts) -> None:
    world = build_world(database, artifacts)
    repository = PostgresRepairRepository(database.engine)
    run = new_run(str(world.attempts[1]))
    repository.create_run(run)
    run = first_round(run)
    repository.commit_round(
        run,
        expected_rounds=0,
        delivery=NewDelivery(
            delivery_index=1, input_tokens=100, output_tokens=50, cost_micros=10, active_ms=1000
        ),
    )
    # A second commit of the same frontier is a conflict, never a second round.
    with pytest.raises(PersistenceConflict, match="advanced elsewhere"):
        repository.commit_round(
            run,
            expected_rounds=0,
            delivery=NewDelivery(
                delivery_index=1, input_tokens=100, output_tokens=50, cost_micros=10, active_ms=1000
            ),
        )
    assert repository.load_run(run.repair_run_id).current_round_index == 1


def test_redelivery_adds_a_delivery_and_never_erases_spend(database, artifacts) -> None:
    world = build_world(database, artifacts)
    repository = PostgresRepairRepository(database.engine)
    run = new_run(str(world.attempts[0]))
    repository.create_run(run)
    run = first_round(run)
    repository.commit_round(
        run,
        expected_rounds=0,
        delivery=NewDelivery(
            delivery_index=1, input_tokens=100, output_tokens=50, cost_micros=10, active_ms=1000
        ),
    )
    recovered = redeliver(run, input_tokens=7, output_tokens=3, cost_micros=2, active_ms=50)
    repository.record_redelivery(
        recovered,
        round_index=0,
        delivery=NewDelivery(
            delivery_index=2, input_tokens=7, output_tokens=3, cost_micros=2, active_ms=50
        ),
    )
    restored = repository.load_run(run.repair_run_id)
    assert restored.digest == recovered.digest
    assert restored.current_round_index == run.current_round_index
    assert restored.rounds[0].deliveries == 2
    assert restored.spend.redeliveries == 1
    assert restored.spend.cost_micros == run.spend.cost_micros + 2
    # The same delivery index cannot be double-counted.
    with pytest.raises(PersistenceConflict):
        repository.record_redelivery(
            recovered,
            round_index=0,
            delivery=NewDelivery(
                delivery_index=2, input_tokens=7, output_tokens=3, cost_micros=2, active_ms=50
            ),
        )


def test_restart_recovery_grants_no_extra_repair_rounds(database, artifacts) -> None:
    world = build_world(database, artifacts)
    repository = PostgresRepairRepository(database.engine)
    run = new_run(str(world.attempts[0]))
    repository.create_run(run)
    run = first_round(run)
    repository.commit_round(
        run,
        expected_rounds=0,
        delivery=NewDelivery(
            delivery_index=1, input_tokens=100, output_tokens=50, cost_micros=10, active_ms=1000
        ),
    )
    # A restart reads the same durable state; the frozen limits still decide when rounds stop.
    restarted = repository.load_run(run.repair_run_id)
    feedback = feedback_from_public(1, (result("public-a"),), public_case_ids=set(PUBLIC))
    ticket = begin_round(
        restarted, prompt_digest=DIGEST_A, request_digest=DIGEST_B, feedback=feedback
    )
    restarted = complete_round(
        restarted,
        ticket,
        candidate_digest=DIGEST_C,
        public_results=(result("public-a", "pass"),),
        input_tokens=1,
        output_tokens=1,
        cost_micros=1,
        active_ms=1,
    )
    repository.commit_round(
        restarted,
        expected_rounds=1,
        delivery=NewDelivery(
            delivery_index=1, input_tokens=1, output_tokens=1, cost_micros=1, active_ms=1
        ),
    )
    after_recovery = repository.load_run(run.repair_run_id)
    with pytest.raises(RepairBudgetExhausted, match="maximum_repair_rounds"):
        begin_round(
            after_recovery,
            prompt_digest=DIGEST_A,
            request_digest=DIGEST_B,
            feedback=feedback_from_public(
                2, (result("public-a"),), public_case_ids=set(PUBLIC)
            ),
        )


def test_selection_persists_and_round_rows_stay_immutable(database, artifacts) -> None:
    world = build_world(database, artifacts)
    repository = PostgresRepairRepository(database.engine)
    run = new_run(str(world.attempts[0]))
    repository.create_run(run)
    run = first_round(run)
    repository.commit_round(
        run,
        expected_rounds=0,
        delivery=NewDelivery(
            delivery_index=1, input_tokens=100, output_tokens=50, cost_micros=10, active_ms=1000
        ),
    )
    done = freeze_selection(repository.load_run(run.repair_run_id))
    repository.record_selection(done)
    restored = repository.load_run(run.repair_run_id)
    assert restored.state == "complete" and restored.selected_round_index == 0
    with pytest.raises(DBAPIError):
        with database.engine.begin() as connection:
            connection.execute(
                repair_round.update().where(repair_round.c.round_index == 0).values(state="frozen")
            )
