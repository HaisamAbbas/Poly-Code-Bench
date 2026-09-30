# ruff: noqa: F811 - pytest fixtures are imported from the PostgreSQL test module
"""Regressions for defects found by the independent Prompt 08 review.

The double-charge, lost-turn (two variants), lock-order and wrong-scope defects were reproduced
against the first implementation by the reviewer and again before fixing; the remaining tests
pin defects confirmed by reading the code paths. Evidence level: actual PostgreSQL 17.6 and
SeaweedFS 4.48 with a FIXTURE provider transport, plus loopback sockets.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from datetime import timedelta
from uuid import uuid4

import pytest
from model_gateway_support import (
    Reply,
    ScriptedTransport,
    make_config,
    make_request,
    ok,
    openai_body,
    single_shot_protocol,
)
from polycodebench_core.application_errors import (
    InvalidState,
    LeaseLost,
    PersistenceConflict,
    PersistenceUnavailable,
)
from polycodebench_core.model_contracts import (
    BudgetExhausted,
    CapabilityUnsupported,
    EndpointNotApproved,
    EndpointPolicyViolation,
    FailureKind,
    ProviderCallFailed,
    ReservationPlan,
    TransportFailure,
    Usage,
)
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.gateway.transport import TransportError
from polycodebench_persistence.model_configs import PostgresModelConfigRepository
from polycodebench_persistence.model_ledger import PostgresModelLedger
from sqlalchemy import select
from test_model_gateway_postgres import (  # noqa: F401 - fixtures used by name
    TIMEOUT,
    World,
    _assert_ledger_consistent,
    _counts,
    artifacts,
    build_world,
    database,
)

TURNS = {"turns": 5, "input_tokens": 10**9, "output_tokens": 10**9}


def _plan(world: World) -> ReservationPlan:
    return ReservationPlan(
        money_micro_usd=world.per_call_bound(), input_tokens=50, output_tokens=50, turns=1
    )


def _begin(world: World, key: str, *, index: int = 0, grace: timedelta = timedelta(hours=1)):  # type: ignore[no-untyped-def]
    return world.ledger.begin_call(
        scope=world.scope(index),
        logical_call_key=key,
        request_digest=make_request().digest(),
        model_config_id=world.config_id,
        request_artifact_id=None,
        price_snapshot={},
        plan=_plan(world),
        in_flight_grace=grace,
    )


def _ambiguous(code: str = "timeout_after_send") -> TransportFailure:
    return TransportFailure(kind=FailureKind.AMBIGUOUS, code=code, retryable=True)


def _evidence(world: World, n: int = 2) -> list:  # type: ignore[type-arg]
    store = ArtifactResponseStore(world.artifacts, owner="regression")
    return [
        store.put(f"regression-{uuid4()}".encode(), kind="t", media_type="text/plain")
        for _ in range(n)
    ]


# ---- 1. the reservation must cover what is actually sent on the wire -----------------------


def test_request_cannot_exceed_the_resolved_config_output_cap(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts)
    transport = ScriptedTransport([ok()])
    oversized = make_request().model_copy(update={"max_output_tokens": 16_000})
    with pytest.raises(CapabilityUnsupported):
        asyncio.run(world.call(world.gateway(transport), "cap", request=oversized))
    assert transport.sent == [] and _counts(world) == (0, 0, 0)
    # deviating sampling controls are refused for the same reason: the cohort config governs
    with pytest.raises(CapabilityUnsupported):
        asyncio.run(
            world.call(
                world.gateway(transport), "temp", request=make_request(temperature="0.700000")
            )
        )
    assert transport.sent == []


def test_a_smaller_request_cap_reserves_proportionally_less(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts)
    small = make_request(max_output_tokens=100)
    assert world.per_call_bound(make_request()) > 0
    gate = asyncio.Event()

    async def scenario() -> int:
        transport = ScriptedTransport([ok()], gate=gate)
        task = asyncio.create_task(world.call(world.gateway(transport), "small", request=small))
        while not transport.sent:
            await asyncio.sleep(0.02)
        reserved = _counts(world)[1]
        gate.set()
        await task
        return reserved

    reserved = asyncio.run(scenario())
    assert reserved < world.per_call_bound(make_request())  # sized from the request actually sent


def test_seed_is_not_sent_when_the_policy_omits_it() -> None:
    from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter

    config = make_config(uuid4(), seed_policy="omit")
    wire = LocalEndpointAdapter().build_request(config, make_request(seed=7))
    assert b'"seed"' not in wire.body and "seed:omitted_by_policy" in wire.dropped_controls


# ---- 2. a resolved cost can never be booked twice --------------------------------------------


def test_reconcile_cannot_book_the_same_cost_twice(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts, resource_limits=TURNS)
    handle = _begin(world, "twice")
    world.ledger.settle_failure(
        delivery_id=handle.delivery_id, failure=_ambiguous(), body_artifact_id=None
    )
    bound = world.per_call_bound()
    assert _counts(world) == (0, 0, bound)
    world.ledger.reconcile(
        delivery_id=handle.delivery_id, actor="ops", evidence="invoice A", cost_micro_usd=100
    )
    assert _counts(world) == (100, 0, 0)
    with pytest.raises(InvalidState):
        world.ledger.reconcile(
            delivery_id=handle.delivery_id,
            actor="ops",
            evidence="invoice A again",
            cost_micro_usd=100,
        )
    assert _counts(world) == (100, 0, 0)  # refused, and the failed attempt left no entries
    # the still-uncertain token legs can be resolved separately, without touching money
    world.ledger.reconcile(
        delivery_id=handle.delivery_id,
        actor="ops",
        evidence="usage export",
        usage=Usage(input_tokens=11, output_tokens=22),
    )
    assert _counts(world) == (100, 0, 0)
    assert world.summary("attempt0")["input_tokens"]["spent_confirmed"] == 11
    _assert_ledger_consistent(world)


# ---- 3. the turn is counted once however the retries unfold ----------------------------------


def test_turn_is_reserved_again_after_a_definitive_retryable_failure(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts, resource_limits=TURNS)
    transport = ScriptedTransport([Reply(429, b"{}", {"retry-after": "0"}), ok()])
    asyncio.run(world.call(world.gateway(transport), "turn-429"))
    turns = world.summary("attempt0")["turns"]
    assert (turns["spent_confirmed"], turns["reserved_open"], turns["uncertain_committed"]) == (
        1,
        0,
        0,
    )
    _assert_ledger_consistent(world)


def test_turn_survives_an_unbilled_reconciliation_of_the_earlier_delivery(
    database, artifacts
) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts, resource_limits=TURNS)
    first = _begin(world, "turn-unbilled")
    world.ledger.settle_failure(
        delivery_id=first.delivery_id, failure=_ambiguous(), body_artifact_id=None
    )
    second = _begin(world, "turn-unbilled")
    assert second.action == "dispatch"
    world.ledger.reconcile(
        delivery_id=first.delivery_id, actor="ops", evidence="invoice: no charge", unbilled=True
    )
    raw, normalized = _evidence(world)
    result = world.ledger.settle_response(
        delivery_id=second.delivery_id,
        provider_request_id="r",
        raw_artifact_id=raw,
        normalized_artifact_id=normalized,
        usage=Usage(input_tokens=10, output_tokens=10),
        usage_reliable=True,
        estimated_cost_micro_usd=10,
        estimate_basis="fixture",
    )
    assert result.consumed
    assert world.summary("attempt0")["turns"]["spent_confirmed"] == 1
    _assert_ledger_consistent(world)


# ---- 4. charges follow the delivery's own scope, never the caller's --------------------------


def test_settlement_and_reconciliation_take_no_caller_supplied_scope(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    for method in (
        PostgresModelLedger.settle_response,
        PostgresModelLedger.settle_failure,
        PostgresModelLedger.reconcile,
    ):
        assert "scope" not in inspect.signature(method).parameters
    world = build_world(database, artifacts)
    handle = _begin(world, "scoped", index=0)
    world.ledger.settle_failure(
        delivery_id=handle.delivery_id, failure=_ambiguous(), body_artifact_id=None
    )
    world.ledger.reconcile(
        delivery_id=handle.delivery_id, actor="ops", evidence="invoice", cost_micro_usd=50
    )
    assert world.summary("attempt0")["money_micro_usd"]["spent_confirmed"] == 50
    assert world.summary("attempt1")["money_micro_usd"]["spent_confirmed"] == 0
    _assert_ledger_consistent(world)


# ---- 5. zero reported usage next to real output is not a free call ---------------------------


def test_zero_usage_with_output_is_not_settled_as_free(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    zero = Reply(
        200, openai_body("a real answer", usage={"prompt_tokens": 0, "completion_tokens": 0})
    )
    result = asyncio.run(world.call(world.gateway(ScriptedTransport([zero])), "zero"))
    assert result.settlement_state == "uncertain"
    assert _counts(world) == (0, 0, bound)  # exposure retained, not released as free
    _assert_ledger_consistent(world)


# ---- 6. one lock order: a retry starting must not deadlock with a worker settling ------------


def test_retry_start_and_worker_settlement_never_deadlock(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    problems: list[BaseException] = []
    world = build_world(database, artifacts)
    for round_number in range(30):
        key = f"race-{round_number}"
        handle = _begin(world, key, index=round_number % 2)
        barrier = threading.Barrier(2)

        def settle(delivery_id=handle.delivery_id, barrier=barrier) -> None:  # type: ignore[no-untyped-def]
            barrier.wait(timeout=10)
            try:
                world.ledger.settle_failure(
                    delivery_id=delivery_id, failure=_ambiguous(), body_artifact_id=None
                )
            except InvalidState:
                pass  # the retry path retired the lost delivery first; both orders are valid
            except BaseException as error:
                problems.append(error)

        def retry(index=round_number % 2, key=key, barrier=barrier) -> None:  # type: ignore[no-untyped-def]
            barrier.wait(timeout=10)
            try:
                _begin(world, key, index=index, grace=timedelta(0))
            except (InvalidState, BudgetExhausted):
                pass
            except BaseException as error:
                problems.append(error)

        threads = [threading.Thread(target=settle), threading.Thread(target=retry)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    assert not [p for p in problems if isinstance(p, PersistenceUnavailable | PersistenceConflict)]
    assert problems == []
    for account in world.accounts.values():
        assert world.ledger.verify_balances(account) == []


# ---- 7. the first response to arrive is the only one consumed ---------------------------------


def test_first_arriving_response_wins_even_from_an_ambiguous_delivery(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts, resource_limits=TURNS)
    ledger = world.ledger
    first = _begin(world, "arrival")
    ledger.settle_failure(
        delivery_id=first.delivery_id, failure=_ambiguous(), body_artifact_id=None
    )
    second = _begin(world, "arrival")
    raw_a, norm_a, raw_b, norm_b = _evidence(world, 4)
    usage = Usage(input_tokens=10, output_tokens=10)
    won = ledger.settle_response(
        delivery_id=first.delivery_id,
        provider_request_id="a",
        raw_artifact_id=raw_a,
        normalized_artifact_id=norm_a,
        usage=usage,
        usage_reliable=True,
        estimated_cost_micro_usd=100,
        estimate_basis="fixture",
    )
    lost = ledger.settle_response(
        delivery_id=second.delivery_id,
        provider_request_id="b",
        raw_artifact_id=raw_b,
        normalized_artifact_id=norm_b,
        usage=usage,
        usage_reliable=True,
        estimated_cost_micro_usd=100,
        estimate_basis="fixture",
    )
    assert won.consumed and not lost.consumed
    recorded = ledger.recorded_call(
        world.scope(), "arrival", make_request().digest(), world.config_id
    )
    assert recorded is not None and recorded.action == "stored" and recorded.delivery_index == 0
    state = ledger.intent_state(first.intent_id)
    assert [d["status"] for d in state["deliveries"]] == ["responded", "ambiguous"]
    assert state["deliveries"][1]["failure_code"] == "late_response_not_consumed"
    assert _counts(world) == (200, 0, 0)  # both were billed; only one result exists
    assert world.summary("attempt0")["turns"]["spent_confirmed"] == 1
    _assert_ledger_consistent(world)


def test_gateway_returns_the_recorded_response_when_its_own_delivery_was_superseded(
    database, artifacts
) -> None:
    """A stalled worker's answer arrives after recovery already stored another response."""
    world = build_world(database, artifacts)
    ledger = world.ledger
    injected: dict[str, object] = {}

    def recovery_wins_the_race() -> None:
        # While the original delivery is 'on the wire', another process retires it and stores
        # its own response for the same logical call.
        second = _begin(world, "superseded", grace=timedelta(0))
        raw, normalized = _evidence(world)
        normalized_doc = ArtifactResponseStore(world.artifacts, owner="race").put(
            b'{"provider_request_id":"winner","provider_response_id":null,'
            b'"finish_reason":"stop","raw_finish_reason":"stop",'
            b'"blocks":[{"kind":"text","text":"the recorded answer"}],'
            b'"usage":{"input_tokens":5,"output_tokens":5,"reasoning_tokens":null,'
            b'"cached_input_tokens":null,"reported_cost_micro_usd":null},'
            b'"provider_revision":null,"provider_payload":null,"provider_payload_origin":null}',
            kind="t",
            media_type="application/json",
        )
        ledger.settle_response(
            delivery_id=second.delivery_id,
            provider_request_id="winner",
            raw_artifact_id=raw,
            normalized_artifact_id=normalized_doc,
            usage=Usage(input_tokens=5, output_tokens=5),
            usage_reliable=True,
            estimated_cost_micro_usd=40,
            estimate_basis="fixture",
        )
        injected["done"] = normalized

    transport = ScriptedTransport([ok("the stalled answer")], on_send=recovery_wins_the_race)
    result = asyncio.run(world.call(world.gateway(transport), "superseded"))
    assert injected["done"] and result.response.text == "the recorded answer"
    assert result.source == "stored"  # never the stalled worker's own reply
    state = ledger.intent_state(result.intent_id)
    assert [d["status"] for d in state["deliveries"]] == ["ambiguous", "responded"]
    assert len(transport.sent) == 1
    _assert_ledger_consistent(world)


# ---- 7b. fence and approval are re-checked where they matter ----------------------------------


def test_lease_loss_between_reservation_and_send_releases_the_reservation(
    database, artifacts
) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts)
    answers = iter([True, True, False])  # start, loop top, then just before the send
    transport = ScriptedTransport([ok()])
    with pytest.raises(LeaseLost):
        asyncio.run(
            world.call(
                world.gateway(transport), "fence", dispatch_allowed=lambda: next(answers, False)
            )
        )
    assert transport.sent == [] and _counts(world) == (0, 0, 0)
    intent = world.ledger.intent_state(_intent(database, world, "fence"))
    assert intent["deliveries"][0]["failure_code"] == "dispatch_revoked"


def test_endpoint_revoked_while_retrying_stops_further_deliveries(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    from polycodebench_persistence.endpoints import PostgresEndpointRepository

    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    transport = ScriptedTransport([TIMEOUT, ok()])
    gateway = world.gateway(transport)

    async def revoke(_: float) -> None:
        PostgresEndpointRepository(database.engine).decide(
            world.endpoint_id,
            decision="revoked",
            actor="admin",
            reason="rotated",
            expected_version=1,
        )

    gateway._sleep = revoke  # type: ignore[attr-defined]
    with pytest.raises(EndpointNotApproved):
        asyncio.run(world.call(gateway, "revoked-mid-call"))
    assert len(transport.sent) == 1 and _counts(world) == (0, 0, bound)


def _intent(database, world: World, key: str):  # type: ignore[no-untyped-def]
    from polycodebench_persistence.models import call_intent

    with database.engine.connect() as connection:
        return connection.execute(
            select(call_intent.c.id).where(
                call_intent.c.attempt_id == world.attempts[0], call_intent.c.logical_call_key == key
            )
        ).scalar_one()


# ---- 8. retained exposure is visible even when the last delivery failed definitively ---------


def test_recorded_failure_reports_exposure_retained_by_an_earlier_delivery(
    database, artifacts
) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    transport = ScriptedTransport([Reply(500, b"boom"), Reply(400, b'{"error":"bad"}')])
    gateway = world.gateway(transport)
    with pytest.raises(ProviderCallFailed) as first:
        asyncio.run(world.call(gateway, "mixed"))
    assert first.value.exposure_retained and first.value.failure.code == "http_400"
    with pytest.raises(ProviderCallFailed) as again:
        asyncio.run(world.call(world.gateway(ScriptedTransport([])), "mixed"))
    assert again.value.exposure_retained and again.value.failure.kind is FailureKind.AMBIGUOUS
    assert _counts(world) == (0, 0, bound)


# ---- 9. unusable credentials and requests are refused before money is reserved ---------------


def test_unusable_secret_is_refused_before_any_reservation(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    from model_gateway_support import SECRET_VALUE
    from polycodebench_orchestration.gateway.secrets import EnvironmentSecretResolver

    world = build_world(database, artifacts, secret_ref="secret://models/local-key")
    bad = EnvironmentSecretResolver(
        "models", {"PCBSECRET__MODELS__LOCAL_KEY": SECRET_VALUE[:10] + "\nX-Injected: 1"}
    )
    transport = ScriptedTransport([ok()])
    with pytest.raises(EndpointPolicyViolation):
        asyncio.run(world.call(world.gateway(transport, secrets=bad), "bad-secret"))
    assert transport.sent == [] and _counts(world) == (0, 0, 0)


def test_local_request_errors_are_rejections_not_ambiguous_spend() -> None:
    from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter

    failure = LocalEndpointAdapter().classify_transport_error(
        TransportError("build", "invalid_request", retryable=False)
    )
    assert failure.kind is FailureKind.REJECTED and not failure.retryable


# ---- 11. a logical key names one request to one model configuration --------------------------


def test_reusing_a_key_with_a_different_model_config_conflicts(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts)
    other = make_config(world.endpoint_id, model="another-model")
    other_id, _ = PostgresModelConfigRepository(
        database.engine, artifacts, owner="regression"
    ).register(other)
    gateway = world.gateway(ScriptedTransport([ok("from the first model")]))
    asyncio.run(world.call(gateway, "model-bound"))
    with pytest.raises(PersistenceConflict):
        asyncio.run(
            gateway.call(
                scope=world.scope(),
                logical_call_key="model-bound",
                config=other,
                config_document_id=other_id,
                protocol=single_shot_protocol(),
                request=make_request(),
            )
        )


def test_two_first_callers_of_one_key_serialize_instead_of_failing(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    world = build_world(database, artifacts)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def start() -> None:
        barrier.wait(timeout=10)
        try:
            _begin(world, "same-key")
            outcomes.append("dispatch")
        except InvalidState:
            outcomes.append("in_flight")  # the second caller is told the call is in flight
        except BaseException as error:  # a unique-violation conflict here would be the defect
            outcomes.append(type(error).__name__)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["dispatch", "in_flight"]


# ---- 12. smaller items ------------------------------------------------------------------------
