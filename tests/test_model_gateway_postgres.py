"""Prompt 08 E2E-10/11/12 on actual PostgreSQL and SeaweedFS.

EVIDENCE LABEL: database, ledger, artifact store and gateway code are real. The provider side
is a deterministic FIXTURE transport (``ScriptedTransport``) that injects timeouts, resets,
429/5xx, malformed and missing-usage answers. No real provider is contacted, so none of this
satisfies the live-provider gates (E2E-31, Prompt 17).
"""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from migration_support import require_migrated_through
from model_gateway_support import (
    FULL_CAPS,
    SECRET_VALUE,
    Fail,
    Reply,
    ScriptedTransport,
    make_config,
    make_request,
    ok,
    single_shot_protocol,
)
from polycodebench_core.application_errors import (
    IdempotencyConflict,
    InvalidState,
    LeaseLost,
    PersistenceConflict,
)
from polycodebench_core.endpoint_policy import EndpointNetworkPolicy, NetworkPolicyKind
from polycodebench_core.model_contracts import (
    BudgetAccountMissing,
    BudgetExhausted,
    CallScope,
    CostBoundUnavailable,
    EndpointNotApproved,
    EndpointPolicyViolation,
    ProviderCallFailed,
    ProviderKind,
    ReservationPlan,
    Usage,
)
from polycodebench_core.model_planning import ModelConfig, cost_bound, effective_capabilities
from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
from polycodebench_orchestration.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from polycodebench_orchestration.gateway.secrets import EnvironmentSecretResolver
from polycodebench_orchestration.gateway.service import (
    GatewayResult,
    ModelGateway,
    RetryPolicy,
)
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.gateway.throttle import ThrottleRegistry
from polycodebench_orchestration.gateway.transport import TransportError
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.model_configs import PostgresModelConfigRepository
from polycodebench_persistence.model_ledger import PostgresModelLedger
from polycodebench_persistence.models import (
    accounting_entry,
    artifact_quota,
    budget_reservation,
    call_delivery,
    call_intent,
    endpoint_registration,
    usage_record,
)
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_persistence.runs import PostgresRunRepository
from polycodebench_services.rbac import Principal, Role
from polycodebench_services.runs import RunCreationService
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from test_persistence_postgres import _request, _seed

REQUIRED_REVISION = "9d3a71c05e24"  # model gateway accounting
INTERNAL = EndpointNetworkPolicy(
    kind=NetworkPolicyKind.INTERNAL_LOCAL, allowed_cidrs=("127.0.0.0/8",)
)
PASSING_CONFORMANCE = {"passed": True, "evidence": "FIXTURE conformance report for tests"}


@pytest.fixture(scope="module")
def database() -> Database:
    url = os.environ.get("PCB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(url).database or "").lower():
        pytest.fail("PCB_TEST_DATABASE_URL must use a dedicated database containing 'test'")
    instance = Database(url)
    with instance.engine.connect() as connection:
        require_migrated_through(connection, REQUIRED_REVISION)
    yield instance
    instance.dispose()


@pytest.fixture(scope="module")
def artifacts(database: Database) -> ArtifactRepository:
    endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.skip("PCB_OBJECT_STORE_ENDPOINT is not configured")
    store = S3ArtifactStore(
        endpoint_url=endpoint,
        buckets={
            "hidden": "pcb-p08-hidden",
            "internal": "pcb-p08-internal",
            "public": "pcb-p08-public",
        },
    )
    store.ensure_buckets()
    with database.engine.begin() as connection:
        for domain in ("model-gateway", "model-config"):
            connection.execute(
                pg_insert(artifact_quota)
                .values(
                    visibility="internal",
                    encryption_domain=domain,
                    max_bytes=500_000_000,
                    used_bytes=0,
                    reserved_bytes=0,
                )
                .on_conflict_do_nothing(index_elements=["visibility", "encryption_domain"])
            )
    return ArtifactRepository(database.engine, store, max_upload_bytes=5_000_000)


class Crash(Exception):
    """Injected controller death."""


class CrashAfterRaw(ModelGateway):
    async def _settle_raw(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise Crash("controller died after the response bytes were persisted")


class CrashAfterSettle(ModelGateway):
    async def _settle_raw(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        await super()._settle_raw(*args, **kwargs)
        raise Crash("controller died after settlement, before notifying the agent")


@dataclass
class World:
    database: Database
    artifacts: ArtifactRepository
    endpoint_id: UUID
    campaign_id: UUID
    run_id: UUID
    attempts: list[UUID]
    accounts: dict[str, UUID]
    config: ModelConfig
    config_id: UUID

    def scope(self, index: int = 0) -> CallScope:
        return CallScope(kind="attempt", scope_id=self.attempts[index])

    @property
    def ledger(self) -> PostgresModelLedger:
        return PostgresModelLedger(self.database.engine)

    def gateway(
        self,
        transport: ScriptedTransport,
        cls: type[ModelGateway] = ModelGateway,
        *,
        retry: RetryPolicy | None = None,
        secrets: EnvironmentSecretResolver | None = None,
    ) -> ModelGateway:
        return cls(
            endpoints=PostgresEndpointRepository(self.database.engine),
            ledger=self.ledger,
            store=ArtifactResponseStore(self.artifacts, owner="model-gateway-test"),
            transport=transport,
            secrets=secrets or EnvironmentSecretResolver("models", {}),
            adapters={
                ProviderKind.LOCAL: LocalEndpointAdapter(),
                ProviderKind.OPENAI_COMPATIBLE: OpenAICompatibleAdapter(),
            },
            throttles=ThrottleRegistry(max_concurrency=16),
            retry=retry or RetryPolicy(in_flight_grace=timedelta(0), base_backoff_seconds=0.0),
            sleep=_no_sleep,
        )

    async def call(
        self, gateway: ModelGateway, key: str, *, index: int = 0, request=None, **kwargs
    ) -> GatewayResult:  # type: ignore[no-untyped-def]
        return await gateway.call(
            scope=self.scope(index),
            logical_call_key=key,
            config=kwargs.pop("config", self.config),
            config_document_id=self.config_id,
            protocol=single_shot_protocol(),
            request=request or make_request(),
            **kwargs,
        )

    def per_call_bound(self, request=None) -> int:  # type: ignore[no-untyped-def]
        caps = effective_capabilities(LocalEndpointAdapter().capabilities(), FULL_CAPS)
        size = len((request or make_request()).canonical_bytes())
        bound = cost_bound(self.config, caps, request_bytes=size)
        assert bound.max_cost_micro_usd is not None
        return bound.max_cost_micro_usd

    def summary(self, name: str) -> dict:  # type: ignore[type-arg]
        return self.ledger.account_summary(self.accounts[name])


async def _no_sleep(seconds: float) -> None:
    return None


def build_world(
    database: Database,
    artifacts: ArtifactRepository,
    *,
    campaign_limit: int = 10**9,
    attempt_limit: int = 10**9,
    resource_limits: dict[str, int] | None = None,
    secret_ref: str = "none",
) -> World:
    ids = _seed(database.engine, samples_per_task=2)
    created = RunCreationService(PostgresRunRepository(database.engine)).create(
        Principal("prompt08-integration", frozenset({Role.OPERATOR})),
        _request(ids, samples_per_task=2),
        f"prompt08-{uuid4()}",
    )
    endpoints = PostgresEndpointRepository(database.engine)
    endpoint_id = endpoints.register(
        provider_kind=ProviderKind.LOCAL,
        base_url="http://127.0.0.1:18080/v1",
        secret_ref=secret_ref,
        policy=INTERNAL,
        declared_capabilities=FULL_CAPS,
        registered_by="admin-1",
    )
    endpoints.decide(
        endpoint_id,
        decision="approved",
        actor="admin-1",
        reason="fixture endpoint",
        expected_version=0,
        conformance_report=PASSING_CONFORMANCE,
    )
    config = make_config(endpoint_id)
    config_id, _ = PostgresModelConfigRepository(
        database.engine, artifacts, owner="model-gateway-test"
    ).register(config)
    ledger = PostgresModelLedger(database.engine)
    campaign = ledger.ensure_account(
        scope_kind="campaign", scope_id=str(ids["campaign"]), hard_limit_micro_usd=campaign_limit
    )
    run = ledger.ensure_account(
        scope_kind="run",
        scope_id=str(created.run_id),
        hard_limit_micro_usd=campaign_limit,
        parent_account_id=campaign,
    )
    accounts = {"campaign": campaign, "run": run}
    for index, attempt_id in enumerate(created.attempt_ids):
        accounts[f"attempt{index}"] = ledger.ensure_account(
            scope_kind="attempt",
            scope_id=str(attempt_id),
            hard_limit_micro_usd=attempt_limit,
            parent_account_id=run,
            resource_limits=resource_limits,
        )
    return World(
        database,
        artifacts,
        endpoint_id,
        UUID(str(ids["campaign"])),
        created.run_id,
        list(created.attempt_ids),
        accounts,
        config,
        config_id,
    )


def _counts(world: World, name: str = "campaign") -> tuple[int, int, int]:
    s = world.summary(name)["money_micro_usd"]
    return s["spent_confirmed"], s["reserved_open"], s["uncertain_committed"]


def _assert_ledger_consistent(world: World) -> None:
    for account in world.accounts.values():
        assert world.ledger.verify_balances(account) == []


def _all_text_columns(database: Database, sql: str) -> str:
    with database.engine.connect() as connection:
        return json.dumps([list(map(str, row)) for row in connection.execute(text(sql))])


# ===================================================================== PCB-08-2 endpoints


def test_endpoint_registration_approval_and_immutability(
    database: Database, artifacts: ArtifactRepository
) -> None:
    repo = PostgresEndpointRepository(database.engine)
    public = EndpointNetworkPolicy(
        kind=NetworkPolicyKind.PUBLIC_ALLOWLIST, allowed_hosts=("api.anthropic.com",)
    )
    # local inference must be an explicit internal registration; hosted must be public
    with pytest.raises(EndpointPolicyViolation):
        repo.register(
            provider_kind=ProviderKind.LOCAL,
            base_url="https://api.anthropic.com",
            secret_ref="none",
            policy=public,
            declared_capabilities=FULL_CAPS,
            registered_by="a",
        )
    with pytest.raises(EndpointPolicyViolation):
        repo.register(
            provider_kind=ProviderKind.ANTHROPIC,
            base_url="http://127.0.0.1:1",
            secret_ref="secret://models/a",
            policy=INTERNAL,
            declared_capabilities=FULL_CAPS,
            registered_by="a",
        )
    with pytest.raises(EndpointPolicyViolation):  # hosted providers cannot be unauthenticated
        repo.register(
            provider_kind=ProviderKind.ANTHROPIC,
            base_url="https://api.anthropic.com",
            secret_ref="none",
            policy=public,
            declared_capabilities=FULL_CAPS,
            registered_by="a",
        )
    with pytest.raises(EndpointPolicyViolation):  # a literal key is not a reference
        repo.register(
            provider_kind=ProviderKind.ANTHROPIC,
            base_url="https://api.anthropic.com",
            secret_ref=SECRET_VALUE,
            policy=public,
            declared_capabilities=FULL_CAPS,
            registered_by="a",
        )
    endpoint_id = repo.register(
        provider_kind=ProviderKind.ANTHROPIC,
        base_url="https://api.anthropic.com",
        secret_ref="secret://models/anthropic-primary",
        policy=public,
        declared_capabilities=FULL_CAPS,
        registered_by="a",
    )
    with pytest.raises(EndpointNotApproved):  # pending endpoints are not usable
        repo.get_approved(endpoint_id)
    repo.decide(
        endpoint_id,
        decision="approved",
        actor="admin",
        reason="reviewed",
        expected_version=0,
        request_id="endpoint-approval-0001",
    )
    repo.decide(
        endpoint_id,
        decision="approved",
        actor="admin",
        reason="reviewed",
        expected_version=0,
        request_id="endpoint-approval-0001",
    )
    with pytest.raises(IdempotencyConflict):
        repo.decide(
            endpoint_id,
            decision="approved",
            actor="admin",
            reason="different decision text",
            expected_version=0,
            request_id="endpoint-approval-0001",
        )
    assert repo.get_approved(endpoint_id).secret_ref == "secret://models/anthropic-primary"
    # stale version and identity edits are refused by the database
    with pytest.raises(Exception):  # noqa: B017 - version conflict from optimistic locking
        repo.decide(endpoint_id, decision="revoked", actor="admin", reason="x", expected_version=0)
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(endpoint_registration)
            .where(endpoint_registration.c.id == endpoint_id)
            .values(base_url_ref="https://api.anthropic.com/evil", row_version=2)
        )
    repo.decide(
        endpoint_id, decision="revoked", actor="admin", reason="rotated", expected_version=1
    )
    with pytest.raises(EndpointNotApproved):
        repo.get_approved(endpoint_id)
    with pytest.raises(InvalidState):  # revoked endpoints cannot be reopened
        repo.decide(
            endpoint_id, decision="approved", actor="admin", reason="oops", expected_version=2
        )


def test_compatible_and_local_endpoints_need_passing_conformance(
    database: Database, artifacts: ArtifactRepository
) -> None:
    repo = PostgresEndpointRepository(database.engine)
    endpoint_id = repo.register(
        provider_kind=ProviderKind.LOCAL,
        base_url="http://127.0.0.1:18081/v1",
        secret_ref="none",
        policy=INTERNAL,
        declared_capabilities=FULL_CAPS,
        registered_by="a",
    )
    for report in (None, {"passed": False}):
        with pytest.raises(InvalidState):
            repo.decide(
                endpoint_id,
                decision="approved",
                actor="admin",
                reason="r",
                expected_version=0,
                conformance_report=report,
            )
    repo.decide(
        endpoint_id,
        decision="approved",
        actor="admin",
        reason="r",
        expected_version=0,
        conformance_report=PASSING_CONFORMANCE,
    )


def test_unapproved_or_revoked_endpoint_is_never_contacted(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport([ok()])
    gateway = world.gateway(transport)
    PostgresEndpointRepository(database.engine).decide(
        world.endpoint_id, decision="revoked", actor="admin", reason="rotated", expected_version=1
    )
    with pytest.raises(EndpointNotApproved):
        asyncio.run(world.call(gateway, "k1"))
    assert transport.sent == []  # no bytes left the gateway
    assert world.summary("campaign")["money_micro_usd"]["reserved_open"] == 0


def test_secret_value_reaches_only_the_wire_and_is_scrubbed_from_storage(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts, secret_ref="secret://models/local-key")
    hostile = Reply(
        200,
        json.dumps(
            {
                "id": "x",
                "model": "m",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": f"key is {SECRET_VALUE}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            }
        ).encode(),
        {"x-request-id": "r"},
    )
    transport = ScriptedTransport([hostile])
    gateway = world.gateway(
        transport,
        secrets=EnvironmentSecretResolver("models", {"PCBSECRET__MODELS__LOCAL_KEY": SECRET_VALUE}),
    )
    result = asyncio.run(world.call(gateway, "k-secret"))
    assert transport.sent[0]["headers"]["authorization"] == f"Bearer {SECRET_VALUE}"
    state = world.ledger.intent_state(result.intent_id)
    blob = json.dumps(state, default=str)
    blob += _all_text_columns(database, "SELECT * FROM endpoint_registration")
    blob += _all_text_columns(database, "SELECT * FROM config_document WHERE kind LIKE 'model%'")
    blob += _all_text_columns(database, "SELECT * FROM audit_event")
    blob += _all_text_columns(database, "SELECT * FROM call_intent")
    assert SECRET_VALUE not in blob
    delivery = state["deliveries"][0]
    raw = world.artifacts.read_verified(delivery["raw_response_artifact_id"])[1]
    assert SECRET_VALUE.encode() not in raw and b"[REDACTED]" in raw
    # the normalized document is parsed from the scrubbed bytes, so it cannot keep the secret
    normalized = world.artifacts.read_verified(delivery["normalized_response_artifact_id"])[1]
    assert SECRET_VALUE.encode() not in normalized


def test_missing_secret_provisioning_blocks_before_any_persistence(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts, secret_ref="secret://models/absent")
    transport = ScriptedTransport([ok()])
    with pytest.raises(EndpointPolicyViolation):
        asyncio.run(world.call(world.gateway(transport), "k"))
    assert transport.sent == []
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                select(func.count())
                .select_from(call_intent)
                .where(call_intent.c.attempt_id == world.attempts[0])
            ).scalar_one()
            == 0
        )


# ====================================================== E2E-10 atomic reservation competition


def test_e2e10_concurrent_calls_compete_for_the_last_allowance(
    database: Database, artifacts: ArtifactRepository
) -> None:
    probe = build_world(database, artifacts)
    bound = probe.per_call_bound()
    allowed = 3
    world = build_world(database, artifacts, campaign_limit=bound * allowed + bound // 2)
    contenders = 9
    gate = asyncio.Event()  # hold dispatched calls in flight so reservations stay open

    async def scenario() -> tuple[list[object], ScriptedTransport]:
        transport = ScriptedTransport(default=lambda: ok(), gate=gate)
        gateway = world.gateway(transport)
        tasks = [
            asyncio.create_task(world.call(gateway, f"race-{i}", index=i % 2))
            for i in range(contenders)
        ]
        # wait until every contender has either reached the provider or been refused
        for _ in range(400):
            done_rejected = sum(1 for t in tasks if t.done())
            if len(transport.sent) + done_rejected >= contenders:
                break
            await asyncio.sleep(0.05)
        assert len(transport.sent) == allowed
        opened = _counts(world)
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert opened == (0, allowed * bound, 0)  # exposure was reserved before dispatch
        return results, transport

    results, transport = asyncio.run(scenario())
    successes = [r for r in results if isinstance(r, GatewayResult)]
    refused = [r for r in results if isinstance(r, BudgetExhausted)]
    assert len(successes) == allowed and len(refused) == contenders - allowed
    assert len(transport.sent) == allowed  # a rejected call never dispatches
    spent, reserved, uncertain = _counts(world)
    assert reserved == 0 and uncertain == 0 and 0 < spent < allowed * bound  # settled below bound
    assert (
        sum(1 for r in results if isinstance(r, Exception) and not isinstance(r, BudgetExhausted))
        == 0
    )
    _assert_ledger_consistent(world)
    with database.engine.connect() as connection:  # refused calls left no intent behind
        intents = connection.execute(
            select(func.count())
            .select_from(call_intent)
            .where(call_intent.c.attempt_id.in_(world.attempts))
        ).scalar_one()
    assert intents == allowed


def test_e2e10_token_and_turn_limits_are_separate_from_money(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(
        database,
        artifacts,
        resource_limits={"turns": 2, "input_tokens": 10**9, "output_tokens": 10**9},
    )
    transport = ScriptedTransport(default=lambda: ok())
    gateway = world.gateway(transport)

    async def run() -> list[object]:
        out: list[object] = []
        for i in range(3):
            try:
                out.append(await world.call(gateway, f"turn-{i}"))
            except BudgetExhausted as error:
                out.append(error)
        return out

    results = asyncio.run(run())
    assert [type(r).__name__ for r in results] == [
        "GatewayResult",
        "GatewayResult",
        "BudgetExhausted",
    ]
    assert len(transport.sent) == 2
    turns = world.summary("attempt0")["turns"]
    assert turns["spent_confirmed"] == 2 and turns["reserved_open"] == 0
    # money was nowhere near exhausted: the turn limit alone blocked the third call
    assert world.summary("attempt0")["money_micro_usd"]["spent_confirmed"] > 0
    _assert_ledger_consistent(world)

    tight = build_world(
        database,
        artifacts,
        resource_limits={"turns": 100, "input_tokens": 10, "output_tokens": 10**9},
    )
    blocked = ScriptedTransport(default=lambda: ok())
    with pytest.raises(BudgetExhausted):
        asyncio.run(tight.call(tight.gateway(blocked), "tok"))
    assert blocked.sent == []


def test_e2e10_parallel_reservations_across_siblings_never_deadlock_or_oversubscribe(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    limited = build_world(database, artifacts, campaign_limit=bound * 5)
    ledger = limited.ledger
    plan = ReservationPlan(money_micro_usd=bound, input_tokens=10, output_tokens=10, turns=1)
    barrier = Barrier(16)

    def reserve(i: int) -> str:
        barrier.wait(timeout=10)
        try:
            ledger.begin_call(
                scope=limited.scope(i % 2),
                logical_call_key=f"thread-{i}",
                request_digest="sha256:" + "0" * 64,
                model_config_id=limited.config_id,
                request_artifact_id=None,
                price_snapshot={},
                plan=plan,
            )
            return "reserved"
        except BudgetExhausted:
            return "refused"

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(reserve, range(16)))
    assert outcomes.count("reserved") == 5 and outcomes.count("refused") == 11
    assert _counts(limited) == (0, 5 * bound, 0)
    _assert_ledger_consistent(limited)


def test_missing_budget_account_refuses_the_call(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    orphan = CallScope(kind="attempt", scope_id=uuid4())
    transport = ScriptedTransport([ok()])
    gateway = world.gateway(transport)
    with pytest.raises(BudgetAccountMissing):
        asyncio.run(
            gateway.call(
                scope=orphan,
                logical_call_key="k",
                config=world.config,
                config_document_id=world.config_id,
                protocol=single_shot_protocol(),
                request=make_request(),
            )
        )
    assert transport.sent == []


def test_lease_loss_stops_dispatch_before_any_spend(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport([ok()])
    with pytest.raises(LeaseLost):
        asyncio.run(world.call(world.gateway(transport), "k", dispatch_allowed=lambda: False))
    assert transport.sent == [] and _counts(world) == (0, 0, 0)


def test_strict_cap_is_blocked_without_an_enforceable_bound(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    unpriced = make_config(world.endpoint_id, price=None)
    transport = ScriptedTransport([ok()])
    with pytest.raises(CostBoundUnavailable):
        asyncio.run(world.call(world.gateway(transport), "k", config=unpriced))
    assert transport.sent == [] and _counts(world) == (0, 0, 0)


# ===================================================== E2E-11 persisted response, controller dies


def test_e2e11_restart_after_settlement_consumes_the_same_response(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport([ok("the answer", request_id="req-abc")])
    with pytest.raises(Crash):
        asyncio.run(world.call(world.gateway(transport, CrashAfterSettle), "solve-1"))
    assert len(transport.sent) == 1
    spent_before = _counts(world)

    # "restart": new gateway, new transport that would fail the test if it were used
    restarted = ScriptedTransport([])
    result = asyncio.run(world.call(world.gateway(restarted), "solve-1"))
    assert restarted.sent == [] and result.source == "stored"
    assert result.response.text == "the answer"
    assert result.response.provider_request_id == "req-abc"
    assert _counts(world) == spent_before  # no second charge, no extra reservation
    state = world.ledger.intent_state(result.intent_id)
    assert [d["status"] for d in state["deliveries"]] == ["responded"]
    _assert_ledger_consistent(world)


def test_e2e11_restart_after_raw_bytes_persisted_settles_without_a_new_request(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport([ok("persisted but unsettled", request_id="req-raw")])
    with pytest.raises(Crash):
        asyncio.run(world.call(world.gateway(transport, CrashAfterRaw), "solve-2"))
    before = world.ledger.intent_state(_intent_id(database, world, "solve-2"))
    assert before["deliveries"][0]["status"] == "dispatching"
    assert before["deliveries"][0]["raw_response_artifact_id"] is not None

    restarted = ScriptedTransport([])
    result = asyncio.run(world.call(world.gateway(restarted), "solve-2"))
    assert restarted.sent == [] and result.source == "recovered_raw"
    assert result.response.text == "persisted but unsettled"
    assert result.response.provider_request_id == "req-raw"
    spent, reserved, uncertain = _counts(world)
    assert spent > 0 and reserved == 0 and uncertain == 0
    # and the next restart simply reads the stored response
    again = asyncio.run(world.call(world.gateway(ScriptedTransport([])), "solve-2"))
    assert again.source == "stored" and again.response.text == result.response.text
    _assert_ledger_consistent(world)


def test_e2e11_no_response_shopping_after_failures(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport(
        [Reply(400, b'{"error":"bad request"}', {"x-request-id": "r400"})]
    )
    gateway = world.gateway(transport)
    with pytest.raises(ProviderCallFailed) as first:
        asyncio.run(world.call(gateway, "k-400"))
    assert not first.value.exposure_retained and _counts(world) == (0, 0, 0)  # rejected, unbilled
    assert len(transport.sent) == 1
    # asking again for the same logical call returns the recorded failure; no new sample
    with pytest.raises(ProviderCallFailed) as second:
        asyncio.run(world.call(world.gateway(ScriptedTransport([ok()])), "k-400"))
    assert second.value.failure.code == "http_400"
    # the same key with different request bytes is a conflict, not a fresh attempt
    with pytest.raises(PersistenceConflict):
        asyncio.run(world.call(gateway, "k-400", request=make_request("A different prompt.")))
    # a stored success cannot be replaced either
    good = ScriptedTransport([ok("first"), ok("second")])
    gw = world.gateway(good)
    first_ok = asyncio.run(world.call(gw, "k-ok"))
    again = asyncio.run(world.call(gw, "k-ok"))
    assert len(good.sent) == 1 and again.response.text == first_ok.response.text == "first"


def test_e2e11_malformed_success_body_is_persisted_and_ambiguous_not_dropped(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport([Reply(200, b"<html>upstream proxy</html>"), ok("recovered")])
    result = asyncio.run(world.call(world.gateway(transport), "k-malformed"))
    state = world.ledger.intent_state(result.intent_id)
    first = state["deliveries"][0]
    assert first["status"] == "ambiguous" and first["failure_code"] == "malformed_response"
    assert world.artifacts.read_verified(first["raw_response_artifact_id"])[1].startswith(b"<html>")
    assert result.prior_ambiguous_deliveries == 1


# ====================================== E2E-12 ambiguous timeout, unavailable usage, retries


TIMEOUT = Fail(TransportError("receive", "receive_failed", timed_out=True))


def test_e2e12_ambiguous_timeout_retains_exposure_and_retry_reserves_more(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    gate = asyncio.Event()
    transport = ScriptedTransport([TIMEOUT, ok("after retry")], gate=gate)
    gateway = world.gateway(transport)

    async def scenario() -> GatewayResult:
        task = asyncio.create_task(world.call(gateway, "amb-1"))
        for _ in range(200):  # first delivery times out; the retry is now in flight
            if len(transport.sent) == 2:
                break
            gate.set()
            await asyncio.sleep(0.02)
            gate.clear()
        snapshot = _counts(world)
        gate.set()
        result = await task
        assert snapshot == (0, bound, bound)  # prior exposure retained + new reservation
        return result

    result = asyncio.run(scenario())
    assert result.prior_ambiguous_deliveries == 1 and result.delivery_index == 1
    spent, reserved, uncertain = _counts(world)
    assert reserved == 0
    assert uncertain == bound  # the first delivery may have billed: never auto-released
    assert spent > 0
    state = world.ledger.intent_state(result.intent_id)
    assert [d["status"] for d in state["deliveries"]] == ["ambiguous", "responded"]
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(usage_record.c.delivery_id).where(
                    usage_record.c.delivery_id.in_([d["id"] for d in state["deliveries"]])
                )
            )
            .scalars()
            .all()
        )
    assert rows == [
        state["deliveries"][1]["id"]
    ]  # no usage (and no zero cost) for the ambiguous one
    assert [
        item["uncertain_micro_usd"]
        for item in world.ledger.unresolved_exposure(world.accounts["campaign"])
    ] == [bound]
    _assert_ledger_consistent(world)


def test_a_turn_is_consumed_once_even_when_a_retry_was_needed(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(
        database,
        artifacts,
        resource_limits={"turns": 5, "input_tokens": 10**9, "output_tokens": 10**9},
    )
    result = asyncio.run(world.call(world.gateway(ScriptedTransport([TIMEOUT, ok()])), "turn-once"))
    assert result.delivery_index == 1
    turns = world.summary("attempt0")["turns"]
    # the retry did not reserve a second turn, and the ambiguous delivery's turn was consumed
    # by the later response instead of staying as uncertain exposure
    assert (turns["spent_confirmed"], turns["reserved_open"], turns["uncertain_committed"]) == (
        1,
        0,
        0,
    )
    tokens = world.summary("attempt0")["output_tokens"]
    assert tokens["uncertain_committed"] > 0  # token exposure of the lost delivery is retained
    _assert_ledger_consistent(world)


def test_e2e12_reconciliation_is_append_only_and_requires_evidence(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport([TIMEOUT, ok("fine")])
    result = asyncio.run(world.call(world.gateway(transport), "amb-2"))
    state = world.ledger.intent_state(result.intent_id)
    ambiguous_id = state["deliveries"][0]["id"]
    with database.engine.connect() as connection:
        entries_before = connection.execute(
            select(func.count()).select_from(accounting_entry)
        ).scalar_one()
    with pytest.raises(InvalidState):
        world.ledger.reconcile(delivery_id=ambiguous_id, actor="ops", evidence=" ", unbilled=True)
    with pytest.raises(InvalidState):  # must assert exactly one outcome
        world.ledger.reconcile(delivery_id=ambiguous_id, actor="ops", evidence="invoice 1")
    settled = world.ledger.reconcile(
        delivery_id=ambiguous_id,
        actor="ops",
        evidence="provider invoice INV-FIXTURE-1 shows no charge for request",
        unbilled=True,
    )
    assert settled.state == "settled"
    spent, reserved, uncertain = _counts(world)
    assert uncertain == 0 and reserved == 0
    with database.engine.connect() as connection:
        assert (
            connection.execute(select(func.count()).select_from(accounting_entry)).scalar_one()
            > entries_before
        )
        revisions = connection.execute(
            select(usage_record.c.settlement_revision, usage_record.c.source).where(
                usage_record.c.delivery_id == ambiguous_id
            )
        ).all()
    assert revisions == [(1, "reconciliation")]
    with pytest.raises(InvalidState):  # nothing left to reconcile
        world.ledger.reconcile(
            delivery_id=ambiguous_id,
            actor="ops",
            evidence="again",
            unbilled=True,
        )
    # the ledger is immutable
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(update(accounting_entry).values(reason="tamper"))
    _assert_ledger_consistent(world)


def test_e2e12_billed_reconciliation_charges_late_and_records_overrun(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    transport = ScriptedTransport([TIMEOUT], default=lambda: TIMEOUT)
    with pytest.raises(ProviderCallFailed) as failed:
        asyncio.run(
            world.call(world.gateway(transport, retry=RetryPolicy(max_deliveries=1)), "amb-3")
        )
    assert failed.value.exposure_retained
    assert _counts(world) == (0, 0, bound)
    # asking again surfaces the same retained failure; it does not spend or dispatch
    same_policy = RetryPolicy(max_deliveries=1, in_flight_grace=timedelta(0))
    with pytest.raises(ProviderCallFailed):
        asyncio.run(
            world.call(world.gateway(ScriptedTransport([ok()]), retry=same_policy), "amb-3")
        )
    delivery = world.ledger.intent_state(UUID(failed.value.intent_id))["deliveries"][0]["id"]
    billed = bound + 1_000  # provider billed beyond our bound: record the overrun, not hide it
    world.ledger.reconcile(
        delivery_id=delivery,
        actor="ops",
        evidence="invoice INV-FIXTURE-2",
        cost_micro_usd=billed,
    )
    assert _counts(world) == (billed, 0, 0)
    _assert_ledger_consistent(world)


def test_e2e12_missing_usage_is_unknown_and_retains_exposure_not_zero(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    transport = ScriptedTransport([ok("no usage reported", usage=None)])
    result = asyncio.run(world.call(world.gateway(transport), "nousage-1"))
    assert result.settlement_state == "uncertain" and result.response.text == "no usage reported"
    assert _counts(world) == (0, 0, bound)  # exposure retained, not free
    delivery = world.ledger.intent_state(result.intent_id)["deliveries"][0]
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(usage_record).where(usage_record.c.delivery_id == delivery["id"])
            )
            .mappings()
            .one()
        )
    assert row["input_tokens"] is None and row["output_tokens"] is None
    assert row["actual_cost_micro_usd"] is None and row["estimated_cost_micro_usd"] is None
    assert row["source"] == "unavailable" and row["usage_available"]["input_tokens"] is False
    # later provider data resolves it through an append-only revision
    world.ledger.reconcile(
        delivery_id=delivery["id"],
        actor="ops",
        evidence="dashboard export FIXTURE-7",
        cost_micro_usd=321,
        usage=Usage(input_tokens=11, output_tokens=22),
    )
    assert _counts(world) == (321, 0, 0)
    _assert_ledger_consistent(world)


def test_e2e12_partial_usage_keeps_unknown_fields_null(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    transport = ScriptedTransport([ok("partial", usage={"prompt_tokens": 40})])
    result = asyncio.run(world.call(world.gateway(transport), "partial-1"))
    assert result.settlement_state == "uncertain"
    delivery = world.ledger.intent_state(result.intent_id)["deliveries"][0]
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(usage_record).where(usage_record.c.delivery_id == delivery["id"])
            )
            .mappings()
            .one()
        )
    assert row["input_tokens"] == 40 and row["output_tokens"] is None


def test_retry_classes_not_delivered_releases_rate_limit_backs_off_ambiguous_retains(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    refused = Fail(TransportError("connect", "connect_failed"))
    limited = Reply(429, b'{"error":"slow"}', {"retry-after": "2"})
    transport = ScriptedTransport([refused, limited, Reply(503, b"busy"), ok("finally")])
    gateway = world.gateway(
        transport, retry=RetryPolicy(max_deliveries=4, in_flight_grace=timedelta(0))
    )
    result = asyncio.run(world.call(gateway, "classes"))
    assert result.delivery_index == 3 and len(transport.sent) == 4
    state = world.ledger.intent_state(result.intent_id)
    assert [d["status"] for d in state["deliveries"]] == [
        "failed",
        "failed",
        "ambiguous",
        "responded",
    ]
    bound = world.per_call_bound()
    spent, reserved, uncertain = _counts(world)
    assert uncertain == bound  # only the 503 outcome is ambiguous; connect failures and 429 are not
    assert reserved == 0
    _assert_ledger_consistent(world)


def test_delivery_cap_bounds_retries_and_keeps_all_exposure(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    transport = ScriptedTransport(default=lambda: Reply(500, b"boom"))
    gateway = world.gateway(
        transport, retry=RetryPolicy(max_deliveries=3, in_flight_grace=timedelta(0))
    )
    with pytest.raises(ProviderCallFailed) as failed:
        asyncio.run(world.call(gateway, "cap"))
    assert len(transport.sent) == 3 and failed.value.exposure_retained
    assert _counts(world) == (0, 0, 3 * bound)  # three ambiguous deliveries, three exposures
    with pytest.raises(ProviderCallFailed):  # the cap is durable: a restart cannot retry further
        asyncio.run(
            world.call(
                world.gateway(ScriptedTransport([ok()]), retry=RetryPolicy(max_deliveries=3)), "cap"
            )
        )
    _assert_ledger_consistent(world)


def test_crash_between_dispatch_and_response_is_treated_as_ambiguous(
    database: Database, artifacts: ArtifactRepository
) -> None:
    """Controller dies after sending but before any bytes were stored."""
    world = build_world(database, artifacts)
    bound = world.per_call_bound()
    ledger = world.ledger
    plan = ReservationPlan(money_micro_usd=bound, input_tokens=5, output_tokens=5, turns=1)
    request = make_request()
    handle = ledger.begin_call(
        scope=world.scope(),
        logical_call_key="lost-1",
        request_digest=request.digest(),
        model_config_id=world.config_id,
        request_artifact_id=None,
        price_snapshot={},
        plan=plan,
    )
    assert handle.action == "dispatch" and _counts(world) == (0, bound, 0)
    # a second caller inside the grace window must not dispatch concurrently
    with pytest.raises(InvalidState):
        ledger.begin_call(
            scope=world.scope(),
            logical_call_key="lost-1",
            request_digest=request.digest(),
            model_config_id=world.config_id,
            request_artifact_id=None,
            price_snapshot={},
            plan=plan,
            in_flight_grace=timedelta(hours=1),
        )
    transport = ScriptedTransport([ok("second try")])
    result = asyncio.run(world.call(world.gateway(transport), "lost-1"))
    assert result.prior_ambiguous_deliveries == 1
    assert _counts(world)[2] == bound  # the lost delivery's exposure is retained
    _assert_ledger_consistent(world)


def test_late_response_for_an_ambiguous_delivery_is_evidence_not_a_second_result(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    ledger = world.ledger
    bound = world.per_call_bound()
    plan = ReservationPlan(money_micro_usd=bound, input_tokens=5, output_tokens=5, turns=1)
    digest = make_request().digest()
    first = ledger.begin_call(
        scope=world.scope(),
        logical_call_key="late-1",
        request_digest=digest,
        model_config_id=world.config_id,
        request_artifact_id=None,
        price_snapshot={},
        plan=plan,
    )
    from polycodebench_core.model_contracts import FailureKind, TransportFailure

    ledger.settle_failure(
        delivery_id=first.delivery_id,  # type: ignore[arg-type]
        failure=TransportFailure(
            kind=FailureKind.AMBIGUOUS, code="timeout_after_send", retryable=True
        ),
        body_artifact_id=None,
    )
    second = ledger.begin_call(
        scope=world.scope(),
        logical_call_key="late-1",
        request_digest=digest,
        model_config_id=world.config_id,
        request_artifact_id=None,
        price_snapshot={},
        plan=plan,
    )
    store = ArtifactResponseStore(artifacts, owner="late-test")
    raw1, raw2, raw3 = (
        store.put(f"x{i}".encode(), kind="t", media_type="text/plain") for i in range(3)
    )
    settled = ledger.settle_response(
        delivery_id=second.delivery_id,  # type: ignore[arg-type]
        provider_request_id="second",
        raw_artifact_id=raw1,
        normalized_artifact_id=raw2,
        usage=Usage(input_tokens=10, output_tokens=10),
        usage_reliable=True,
        estimated_cost_micro_usd=100,
        estimate_basis="fixture",
    )
    assert settled.state == "settled"
    late = ledger.settle_response(
        delivery_id=first.delivery_id,  # type: ignore[arg-type]
        provider_request_id="first-late",
        raw_artifact_id=raw3,
        normalized_artifact_id=raw3,
        usage=Usage(input_tokens=10, output_tokens=10),
        usage_reliable=True,
        estimated_cost_micro_usd=100,
        estimate_basis="fixture",
    )
    assert late.charged_micro_usd == 100 and late.retained_micro_usd == 0
    state = ledger.intent_state(first.intent_id)
    statuses = {d["delivery_index"]: (d["status"], d["failure_code"]) for d in state["deliveries"]}
    assert (
        statuses[0] == ("ambiguous", "late_response_not_consumed") and statuses[1][0] == "responded"
    )
    assert (
        ledger.recorded_call(world.scope(), "late-1", digest, world.config_id).delivery_index == 1
    )  # type: ignore[union-attr]
    assert _counts(world) == (
        200,
        0,
        0,
    )  # both billed, only the first responded delivery is consumed
    _assert_ledger_consistent(world)


def test_request_and_reservation_rows_are_bound_to_one_intent_per_key(
    database: Database, artifacts: ArtifactRepository
) -> None:
    world = build_world(database, artifacts)
    asyncio.run(world.call(world.gateway(ScriptedTransport([ok()])), "one"))
    with database.engine.connect() as connection:
        intents = connection.execute(
            select(call_intent.c.id, call_intent.c.state, call_intent.c.request_artifact_id).where(
                call_intent.c.attempt_id == world.attempts[0]
            )
        ).all()
        assert (
            len(intents) == 1 and intents[0].state == "settled" and intents[0].request_artifact_id
        )
        reservations = connection.execute(
            select(func.count())
            .select_from(budget_reservation)
            .where(budget_reservation.c.call_intent_id == intents[0].id)
        ).scalar_one()
        deliveries = connection.execute(
            select(func.count())
            .select_from(call_delivery)
            .where(call_delivery.c.intent_id == intents[0].id)
        ).scalar_one()
    assert reservations == 3 and deliveries == 1  # campaign, run and attempt accounts


def _intent_id(database: Database, world: World, key: str) -> UUID:
    with database.engine.connect() as connection:
        return connection.execute(
            select(call_intent.c.id).where(
                call_intent.c.attempt_id == world.attempts[0],
                call_intent.c.logical_call_key == key,
            )
        ).scalar_one()


def test_e2e09_usage_survives_cancellation_and_cancelled_scope_cannot_spend(
    database: Database, artifacts: ArtifactRepository
) -> None:
    """Model-usage half of E2E-09 (fixture transport): cancellation keeps incurred usage and
    the gateway itself refuses new calls for a cancelled attempt, independent of caller guards."""
    from polycodebench_core.model_contracts import ScopeNotActive
    from polycodebench_persistence.jobs import PostgresJobRepository

    world = build_world(database, artifacts)
    first = asyncio.run(world.call(world.gateway(ScriptedTransport([ok("before cancel")])), "c-1"))
    spent_before = _counts(world)
    assert spent_before[0] > 0
    PostgresJobRepository(database.engine).cancel_scope(
        "attempt", world.attempts[0], actor="operator-1", reason="operator cancellation"
    )
    transport = ScriptedTransport([ok("must never be requested")])
    with pytest.raises(ScopeNotActive):
        asyncio.run(world.call(world.gateway(transport), "c-2"))  # guard not consulted
    assert transport.sent == [] and _counts(world) == spent_before
    # the incurred call, its usage record and the stored response remain available
    again = asyncio.run(world.call(world.gateway(ScriptedTransport([])), "c-1"))
    assert again.source == "stored" and again.response.text == first.response.text
    delivery = world.ledger.intent_state(first.intent_id)["deliveries"][0]
    with database.engine.connect() as connection:
        assert (
            connection.execute(
                select(usage_record.c.input_tokens).where(
                    usage_record.c.delivery_id == delivery["id"]
                )
            ).scalar_one()
            == 120
        )
    _assert_ledger_consistent(world)
