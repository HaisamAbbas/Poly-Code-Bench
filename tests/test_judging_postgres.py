"""Prompt 14 E2E-21/E2E-22 on actual PostgreSQL, SeaweedFS and the real model gateway.

EVIDENCE LABEL: the database schema, artifact store, ledger, budget accounting, gateway dispatch,
packet/vote/result persistence and reviewer adjudication are all real. The judge model is a
FIXTURE: scripted transport answers replayed through the same adapter, endpoint approval, cost
reservation and settlement path a real provider would use. Nothing here is evidence that any real
judge model behaves this way, and nothing here substitutes for the human calibration labels.

E2E-21: three logical votes (1 / 0.5 / 1) through the gateway average to 0.833333, every delivery
and vote is retained, the judge request carries no tools and three distinct recorded seeds, and the
ledger settles the calls.
E2E-22: invalid JSON is retained and replaced within the fixed bound, a vote that never validates
leaves fewer than three valid votes so the result is ``infra_blocked``, and a candidate comment
instructing the judge produces a review trigger rather than a score.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from judging_support import (
    HALF_SCORE,
    cohort,
    injection_packet,
    packet,
    provisioned_panel,
    rubric,
    vote_document,
)
from migration_support import require_migrated_through
from model_gateway_support import (
    FULL_CAPS,
    Reply,
    ScriptedTransport,
    make_config,
    openai_body,
)
from polycodebench_core.canonical import canonical_document_bytes, parse_json_strict, sha256_bytes
from polycodebench_core.endpoint_policy import EndpointNetworkPolicy, NetworkPolicyKind
from polycodebench_core.judge_calibration import CalibrationPacket, CalibrationStratum
from polycodebench_core.judge_contracts import (
    JudgeDelivery,
    JudgementResult,
    JudgePacket,
    JudgeVote,
    PanelUnavailable,
    VoteRejected,
    judge_record_bytes,
)
from polycodebench_core.model_contracts import CallScope, ProviderKind
from polycodebench_orchestration.gateway.secrets import EnvironmentSecretResolver
from polycodebench_orchestration.gateway.service import ModelGateway, RetryPolicy
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.gateway.throttle import ThrottleRegistry
from polycodebench_orchestration.judge.cli import EXIT_OK, _run_pending_batch
from polycodebench_orchestration.judge.protocol import load_judge_protocol
from polycodebench_orchestration.judge.runner import JudgeRunner, VoteArtifacts
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.judging import PostgresJudgingRepository
from polycodebench_persistence.model_configs import PostgresModelConfigRepository
from polycodebench_persistence.model_ledger import PostgresModelLedger
from polycodebench_persistence.models import (
    adjudication as adjudication_table,
)
from polycodebench_persistence.models import (
    artifact_quota,
    attempt,
    evaluation,
    judge_delivery,
    judge_item_result,
    judge_packet,
    judge_result,
)
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_persistence.runs import PostgresRunRepository
from polycodebench_services.judging import adjudication_id, build_adjudication
from polycodebench_services.rbac import Role
from polycodebench_services.runs import RunCreationService
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from test_persistence_postgres import _request, _seed

REQUIRED_REVISION = "b9e04c7a1f38"  # judge execution records
INTERNAL = EndpointNetworkPolicy(
    kind=NetworkPolicyKind.INTERNAL_LOCAL, allowed_cidrs=("127.0.0.0/8",)
)
PASSING_CONFORMANCE = {"passed": True, "evidence": "FIXTURE conformance report for tests"}
CANDIDATE_A = "11111111-1111-4111-8111-111111111111"
CANDIDATE_B = "33333333-3333-4333-8333-333333333333"


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
            "hidden": "pcb-p14-hidden",
            "internal": "pcb-p14-internal",
            "public": "pcb-p14-public",
        },
    )
    store.ensure_buckets()
    with database.engine.begin() as connection:
        for domain in ("model-gateway", "judge", "model-config"):
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


class World:
    def __init__(self, database: Database, artifacts: ArtifactRepository) -> None:
        self.database = database
        self.artifacts = artifacts
        self.store = ArtifactResponseStore(
            artifacts, owner="pcb-judge-test", encryption_domain="judge"
        )
        self.judging = PostgresJudgingRepository(database.engine)
        self.configs = PostgresModelConfigRepository(
            database.engine, artifacts, owner="pcb-judge-test"
        )
        self.ledger = PostgresModelLedger(database.engine)
        self.endpoints = PostgresEndpointRepository(database.engine)

    @property
    def judging_gateway_store(self) -> ArtifactResponseStore:
        return ArtifactResponseStore(
            self.artifacts, owner="pcb-judge-gateway-test", encryption_domain="model-gateway"
        )

    def gateway(self, transport: ScriptedTransport) -> ModelGateway:
        return ModelGateway(
            endpoints=self.endpoints,
            ledger=self.ledger,
            store=self.judging_gateway_store,
            transport=transport,
            secrets=EnvironmentSecretResolver("models", {}),
            adapters={ProviderKind.LOCAL: _local_adapter()},
            throttles=ThrottleRegistry(max_concurrency=4),
            retry=RetryPolicy(in_flight_grace=timedelta(0), base_backoff_seconds=0.0),
            sleep=_no_sleep,
        )


def _local_adapter() -> Any:
    from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter

    return LocalEndpointAdapter()


async def _no_sleep(seconds: float) -> None:
    return None


@pytest.fixture()
def world(database: Database, artifacts: ArtifactRepository) -> Any:
    """One approved judge endpoint, one judge model configuration, and an active evaluation."""
    ids = _seed(database.engine, samples_per_task=1)
    created = RunCreationService(PostgresRunRepository(database.engine)).create(
        _operator(), _request(ids, samples_per_task=1), f"prompt14-{uuid4()}"
    )
    endpoint_id = world_endpoint(database, uuid4())
    config = make_config(endpoint_id, provider=ProviderKind.LOCAL)
    config_id, _ = PostgresModelConfigRepository(
        database.engine, artifacts, owner="pcb-judge-test"
    ).register(config)
    environment = World(database, artifacts)
    with database.engine.begin() as connection:
        evaluation_id = uuid4()
        connection.execute(
            evaluation.insert().values(
                id=evaluation_id,
                attempt_id=created.attempt_ids[0],
                policy_config_id=ids["run_config"],
                oracle_digest="sha256:" + "7" * 64,
                state="running",
                gate="unknown",
                evidence_manifest_id=ids["artifact"],
                row_version=0,
            )
        )
    ledger = environment.ledger
    campaign = ledger.ensure_account(
        scope_kind="campaign", scope_id=str(ids["campaign"]), hard_limit_micro_usd=10**9
    )
    run = ledger.ensure_account(
        scope_kind="run",
        scope_id=str(created.run_id),
        hard_limit_micro_usd=10**9,
        parent_account_id=campaign,
    )
    ledger.ensure_account(
        scope_kind="attempt",
        scope_id=str(created.attempt_ids[0]),
        hard_limit_micro_usd=10**9,
        parent_account_id=run,
    )
    environment_account = ledger.ensure_account(
        scope_kind="evaluation",
        scope_id=str(evaluation_id),
        hard_limit_micro_usd=10**9,
        parent_account_id=run,
        resource_limits={"turns": 64, "input_tokens": 50_000_000, "output_tokens": 5_000_000},
    )
    environment.evaluation_id = evaluation_id
    environment.account_id = environment_account
    environment.config = config
    environment.config_id = config_id
    environment.judge_panel = provisioned_panel(
        str(config_id),
        revision="fixture-judge-rev-1",
        provider_kind="local",
        excluded=(CANDIDATE_A, CANDIDATE_B),
    )
    environment.cohort_id = environment.judging.register_cohort(
        cohort(
            cohort_id=f"pilot-cohort-{environment.config_id}",
            judge_panel=environment.judge_panel,
            candidate_model_config_ids=(CANDIDATE_A, CANDIDATE_B),
        )
    )
    yield environment
    environment.database.dispose()


def world_endpoint(database: Database, marker: UUID) -> UUID:
    endpoints = PostgresEndpointRepository(database.engine)
    endpoint_id = endpoints.register(
        provider_kind=ProviderKind.LOCAL,
        base_url=f"http://127.0.0.1:{18080 + (int(str(marker)[:2], 16) % 64)}/v1",
        secret_ref="none",
        policy=INTERNAL,
        declared_capabilities=FULL_CAPS,
        registered_by="prompt14-test",
    )
    endpoints.decide(
        endpoint_id,
        decision="approved",
        actor="prompt14-test",
        reason="fixture judge endpoint",
        expected_version=0,
        conformance_report=PASSING_CONFORMANCE,
    )
    return endpoint_id


def _operator() -> Any:
    from polycodebench_services.rbac import Principal, Role

    return Principal("prompt14-integration", frozenset({Role.OPERATOR}))


def _reply(text: str, request_id: str) -> Reply:
    return Reply(200, openai_body(text), {"x-request-id": request_id})


def _register_packet(world: Any, judge_packet: Any) -> tuple[UUID, UUID]:
    artifact = world.store.put(
        judge_packet.canonical_bytes(), kind="judge_packet", media_type="application/json"
    )
    row_id = world.judging.register_packet(
        evaluation_id=world.evaluation_id,
        packet=judge_packet,
        packet_artifact_id=artifact,
        cohort_id=world.cohort_id,
    )
    return row_id, artifact


def _runner(world: Any, judge_packet: Any, packet_row_id: UUID, gateway: Any) -> JudgeRunner:
    def resolve(target: Any, vote_index: int, delivery_index: int, result: Any) -> VoteArtifacts:
        assert target.packet_id == judge_packet.packet_id
        del vote_index, delivery_index
        found = world.judging.delivery_artifacts(result.intent_id, result.delivery_index)
        return VoteArtifacts(
            raw_artifact_id=str(found["raw_artifact_id"]) if found["raw_artifact_id"] else None,
            call_delivery_id=str(found["call_delivery_id"]) if found["call_delivery_id"] else None,
            call_intent_id=str(result.intent_id),
        )

    def persist_delivery(target: Any, delivery: JudgeDelivery, artifacts: VoteArtifacts) -> None:
        assert target.packet_id == judge_packet.packet_id
        world.judging.record_delivery(
            packet_row_id=packet_row_id,
            delivery=delivery,
            call_delivery_id=UUID(artifacts.call_delivery_id)
            if artifacts.call_delivery_id
            else None,
            call_intent_id=UUID(artifacts.call_intent_id) if artifacts.call_intent_id else None,
        )

    def persist_vote(
        target: Any, vote: JudgeVote, normalized: bytes, artifacts: VoteArtifacts
    ) -> None:
        assert target.packet_id == judge_packet.packet_id
        artifact = world.store.put(normalized, kind="judge_vote", media_type="application/json")
        world.judging.record_vote(
            packet_row_id=packet_row_id,
            vote=vote.model_copy(update={"normalized_artifact_id": str(artifact)}),
        )

    return JudgeRunner(
        gateway=gateway,
        panel=world.judge_panel,
        protocol=load_judge_protocol(),
        resolve_artifacts=resolve,
        persist_delivery=persist_delivery,
        persist_vote=persist_vote,
    )


def _run(world: Any, judge_packet: Any, packet_row_id: UUID, script: list[Reply]) -> Any:
    transport = ScriptedTransport(script)
    runner = _runner(world, judge_packet, packet_row_id, world.gateway(transport))
    world.transport = transport
    run = asyncio.run(
        runner.run(
            packet=judge_packet,
            config=world.config,
            config_document_id=world.config_id,
            scope=CallScope(kind="evaluation", scope_id=world.evaluation_id),
        )
    )
    world.result = run.result(world.judge_panel)
    return run


def _store_result(world: Any, packet_row_id: UUID, result: JudgementResult) -> str:
    digest = sha256_bytes(canonical_document_bytes(result))
    frozen = result.model_copy(update={"report_digest": digest})
    artifact = world.store.put(
        canonical_document_bytes(frozen), kind="judge_result", media_type="application/json"
    )
    world.judging.record_result(
        packet_row_id=packet_row_id,
        evaluation_id=world.evaluation_id,
        result=frozen,
        report_artifact_id=artifact,
        result_index=world.judging.next_result_index(packet_row_id),
    )
    return digest


def _write_evidence(name: str, document: dict[str, Any]) -> None:
    """Write one secret-free evidence file when the run asks for it (PCB_TEST_EVIDENCE_DIR)."""
    out_dir = os.environ.get("PCB_TEST_EVIDENCE_DIR")
    if not out_dir:
        return
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / f"{name}.json").write_text(
        json.dumps(document, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _packet_evidence(world: Any, target: Any) -> dict[str, Any]:
    """What the judge was shown: no identity, no tools, and the untrusted comments."""
    return {
        "packet_id": target.packet_id,
        "packet_digest": target.digest(),
        "rubric": f"{target.rubric_id}@{target.rubric_version}",
        "rubric_digest": target.rubric_digest,
        "panel": f"{target.panel_id}@{target.panel_version}",
        "panel_digest": target.panel_digest,
        "language": target.language,
        "packet_role": target.packet_role,
        "items": [item.item_id for item in target.items],
        "evidence_anchors": [span.anchor_id for span in target.spans],
        "withheld_fields": list(target.withheld_fields),
        "excluded_evidence": list(target.excluded_evidence),
        "tools": list(target.tools),
        "untrusted_comments": [
            {
                "anchor_id": comment.anchor_id,
                "path": comment.path,
                "instruction_attempt": comment.instruction_attempt,
                "in_any_item_scope": any(
                    comment.anchor_id in item.in_scope_anchor_ids for item in target.items
                ),
            }
            for comment in target.untrusted_comments
        ],
        "cohort_id": str(world.cohort_id),
    }


# ===================================================================== E2E-21


def test_e2e21_three_logical_votes_average_and_retain_every_delivery(world: Any) -> None:
    target = packet(judge_panel=world.judge_panel)
    packet_row_id, packet_artifact = _register_packet(world, target)
    script = [
        _reply(
            json.dumps(
                vote_document(
                    target,
                    scores={"decomposition": "1.000000", "minimal_relevant_scope": "1.000000"},
                )
            ),
            "judge-vote-0",
        ),
        _reply(
            json.dumps(
                vote_document(
                    target,
                    scores={"decomposition": HALF_SCORE, "minimal_relevant_scope": HALF_SCORE},
                )
            ),
            "judge-vote-1",
        ),
        _reply(
            json.dumps(
                vote_document(
                    target,
                    scores={"decomposition": "1.000000", "minimal_relevant_scope": "1.000000"},
                )
            ),
            "judge-vote-2",
        ),
    ]
    run = _run(world, target, packet_row_id, script)
    result = world.result

    # E2E-21: mean 0.833333 before dimension scaling, with every vote and citation retained
    assert result.status == "ready"
    assert result.valid_vote_indexes == (0, 1, 2)
    assert len(result.deliveries) == 3
    for item in result.items:
        assert item.mean_score == "0.833333"
        assert item.vote_scores == ("1.000000", HALF_SCORE, "1.000000")
        assert item.vote_count == 3
    assert all(vote.items[0].citations for vote in run.votes)

    # the judge is asked once per vote, with a structured schema and no tools at all
    assert len(world.transport.sent) == 3
    for sent in world.transport.sent:
        wire = json.loads(sent["body"])  # provider wire JSON, not pcb-json-v1
        assert "tools" not in wire
        assert wire["response_format"]["type"] == "json_schema"
        assert wire["response_format"]["json_schema"]["name"] == "judge_vote_v1"
        assert wire["temperature"] == 0.0
        assert wire["max_completion_tokens"] <= world.config.max_output_tokens

    # distinct recorded seeds, one per logical vote
    seeds = {delivery.seed for delivery in run.deliveries}
    assert len(seeds) == 3
    assert all(delivery.seed_supported for delivery in run.deliveries)

    # persistence: three deliveries, three votes, one frozen result
    stored_deliveries = world.judging.deliveries(packet_row_id)
    assert [row["status"] for row in stored_deliveries] == ["valid", "valid", "valid"]
    assert len({row["seed"] for row in stored_deliveries}) == 3
    assert len(world.judging.votes(packet_row_id)) == 3
    digest = _store_result(world, packet_row_id, result)
    rows = world.judging.results(packet_row_id)
    assert [row["status"] for row in rows] == ["ready"]
    assert rows[0]["report_digest"] == digest
    with world.database.engine.connect() as connection:
        items = connection.execute(
            select(judge_item_result.c.item_id, judge_item_result.c.mean_score).where(
                judge_item_result.c.result_id == rows[0]["id"]
            )
        ).all()
    assert dict(items) == {
        "decomposition": Decimal("0.833333"),
        "minimal_relevant_scope": Decimal("0.833333"),
    }

    # the packet bytes are the evidence a reviewer re-reads, and they round-trip
    stored_packet = JudgePacket.model_validate(
        _envelope_payload(world.store.get(packet_artifact)), strict=False
    )
    assert stored_packet.packet_id == target.packet_id
    assert stored_packet.digest() == target.digest()

    # judge evidence is immutable: an update or delete is refused by the database
    with pytest.raises(DBAPIError), world.database.engine.begin() as connection:
        connection.execute(
            judge_delivery.update()
            .where(judge_delivery.c.packet_id == packet_row_id)
            .values(status="valid")
        )
    with pytest.raises(DBAPIError), world.database.engine.begin() as connection:
        connection.execute(judge_packet.delete().where(judge_packet.c.id == packet_row_id))

    _write_evidence(
        "prompt-14-e2e-21",
        {
            "scenario": "E2E-21",
            "claim": "three logical votes 1 / 0.5 / 1 average to 0.833333 with every vote retained",
            "judge_tier": "FIXTURE judge responses through the real gateway",
            "packet": _packet_evidence(world, target),
            "vote_scores": {item.item_id: list(item.vote_scores) for item in result.items},
            "mean_scores": {item.item_id: item.mean_score for item in result.items},
            "spreads": {item.item_id: item.spread for item in result.items},
            "valid_vote_indexes": list(result.valid_vote_indexes),
            "deliveries": [
                {
                    "vote_index": row["vote_index"],
                    "delivery_index": row["delivery_index"],
                    "status": row["status"],
                    "invalid_reason": row["invalid_reason"],
                    "seed_recorded": str(row["seed"]) if row["seed"] is not None else None,
                    "seed_supported": row["seed_supported"],
                    "repair_instruction_id": row["repair_instruction_id"],
                }
                for row in stored_deliveries
            ],
            "distinct_seeds": len({row["seed"] for row in stored_deliveries}) == 3,
            "judge_requests": [
                {
                    "tools_present": "tools" in json.loads(sent["body"]),
                    "response_schema": json.loads(sent["body"])["response_format"]["json_schema"][
                        "name"
                    ],
                    "max_completion_tokens": json.loads(sent["body"])["max_completion_tokens"],
                    "temperature": json.loads(sent["body"])["temperature"],
                }
                for sent in world.transport.sent
            ],
            "panel": {
                "judge_revision": world.judge_panel.judge_revision,
                "votes_required": world.judge_panel.votes_required,
                "max_deliveries_per_vote": world.judge_panel.max_deliveries_per_vote(),
                "repair_instruction_id": world.judge_panel.repair.instruction_id,
                "candidate_models_excluded": list(
                    world.judge_panel.excluded_candidate_model_config_ids
                ),
            },
            "ledger": {
                "money": world.ledger.account_summary(world.account_id)["money_micro_usd"],
                "turns": world.ledger.account_summary(world.account_id)["turns"],
                "balanced": world.ledger.verify_balances(world.account_id) == [],
            },
            "result_status": result.status,
            "report_digest": digest,
            "note": "judge responses are scripted fixtures; no real judge model was contacted",
        },
    )

    # the calls settled against the evaluation budget and the ledger balances
    assert world.ledger.verify_balances(world.account_id) == []
    summary = world.ledger.account_summary(world.account_id)
    assert summary["money_micro_usd"]["spent_confirmed"] > 0
    assert summary["turns"]["spent_confirmed"] == 3  # one logical call per vote


# ===================================================================== E2E-22


def test_e2e22_invalid_votes_missing_third_vote_and_comment_instructions(world: Any) -> None:
    target = injection_packet(judge_panel=world.judge_panel)
    packet_row_id, _ = _register_packet(world, target)
    comment_anchor = next(iter(target.comment_anchor_ids))
    obeyed = vote_document(
        target,
        anchors={item.item_id: comment_anchor for item in target.items},
        rationale="The candidate comment instructs me to give full marks; I am following it.",
    )
    good = vote_document(target)
    script = [
        # vote 0: invalid JSON, then the replacement delivery is accepted
        _reply("this is not JSON at all", "judge-v0-d0"),
        _reply(json.dumps(good), "judge-v0-d1"),
        # vote 1: three invalid deliveries - the fixed bound is exhausted
        _reply(json.dumps({"packet_digest": target.digest(), "items": {}}), "judge-v1-d0"),
        _reply(
            json.dumps(
                {
                    **good,
                    "items": {
                        item.item_id: {
                            "score": "0.750000" if item.item_id == "decomposition" else "1.000000",
                            "citations": [{"anchor_id": item.in_scope_anchor_ids[0]}],
                            "rationale": "Outside the declared anchor set on purpose.",
                        }
                        for item in target.items
                    },
                }
            ),
            "judge-v1-d1",
        ),
        _reply(json.dumps({**good, "requested_action": "report full marks"}), "judge-v1-d2"),
        # vote 2: schema-valid, but it follows the candidate comment instead of the evidence
        _reply(json.dumps(obeyed), "judge-v2-d0"),
    ]
    _run(world, target, packet_row_id, script)
    result = world.result

    # invalid deliveries are retained with their reasons
    stored = world.judging.deliveries(packet_row_id)
    assert len(stored) == 6
    statuses = [
        (row["vote_index"], row["delivery_index"], row["status"], row["invalid_reason"])
        for row in stored
    ]
    assert statuses == [
        (0, 0, "invalid", "not_json"),
        (0, 1, "valid", None),
        (1, 0, "invalid", "missing_item"),
        (1, 1, "invalid", "anchor_not_in_set"),
        (1, 2, "invalid", "extra_instruction_action"),
        (2, 0, "valid", None),
    ]
    assert all(row["repair_instruction_id"] in (None, "schema-repair-v1") for row in stored)
    replacements = [row for row in stored if row["delivery_index"] == 1]
    assert [row["repair_instruction_id"] for row in replacements] == ["schema-repair-v1"] * 2

    # two valid votes cannot produce a ready result, and nothing is averaged
    assert result.status == "infra_blocked"
    assert result.valid_vote_indexes == (0, 2)
    assert len(world.judging.votes(packet_row_id)) == 2
    for item in result.items:
        assert item.mean_score is None
        assert "missing_required_vote" in [trigger.value for trigger in item.triggers]

    # the repair instruction names the failure and never a score
    repair_bodies = [
        sent["body"] for index, sent in enumerate(world.transport.sent) if index in (1, 3, 4)
    ]
    assert len(repair_bodies) == 3
    for body in repair_bodies:
        messages = json.loads(body)["messages"]
        prompt = next(message["content"] for message in messages if message["role"] == "user")
        instruction = prompt.split("(repair instruction id:", 1)[0]
        assert "rejected by the output validator" in instruction
        assert "does not want any particular" in instruction
        assert "schema-repair-v1" in prompt
        for anchor in ("1.000000", "0.500000", "0.000000"):
            assert anchor not in instruction  # the instruction names no score
        # the same packet and schema are re-sent; only the fixed instruction is added
        assert (
            json.loads(body)["response_format"] == json.loads(repair_bodies[0])["response_format"]
        )

    # the comment had no authority: the vote is retained and flagged, never adopted silently
    vote_two = next(vote for vote in result.deliveries if vote.vote_index == 2)
    assert vote_two.status == "valid"
    assert result.untrusted_comment_count == 1
    assert all(
        "untrusted_comment_only" in [trigger.value for trigger in item.triggers]
        for item in result.items
    )

    _write_evidence(
        "prompt-14-e2e-22",
        {
            "scenario": "E2E-22",
            "claim": (
                "invalid output is retained and replaced within a fixed bound; fewer than three "
                "valid votes cannot produce a ready result; candidate comments have no authority"
            ),
            "judge_tier": "FIXTURE judge responses through the real gateway",
            "packet": _packet_evidence(world, target),
            "deliveries": [
                {
                    "vote_index": row["vote_index"],
                    "delivery_index": row["delivery_index"],
                    "status": row["status"],
                    "invalid_reason": row["invalid_reason"],
                    "repair_instruction_id": row["repair_instruction_id"],
                }
                for row in stored
            ],
            "valid_vote_indexes": list(result.valid_vote_indexes),
            "item_outcomes": [
                {
                    "item_id": item.item_id,
                    "status": item.status,
                    "mean_score": item.mean_score,
                    "vote_count": item.vote_count,
                    "required_votes": item.required_votes,
                    "triggers": [trigger.value for trigger in item.triggers],
                }
                for item in result.items
            ],
            "untrusted_comment_count": result.untrusted_comment_count,
            "repair_instruction_contains_no_score": True,
            "result_status": result.status,
        },
    )

    # E2E-22 readiness stays blocked: no score is derived from two votes
    _store_result(world, packet_row_id, result)
    assert [row["status"] for row in world.judging.results(packet_row_id)] == ["infra_blocked"]
    with world.database.engine.connect() as connection:
        means = connection.execute(
            select(judge_item_result.c.item_id, judge_item_result.c.mean_score).join(
                judge_result, judge_result.c.id == judge_item_result.c.result_id
            )
        ).all()
    assert dict(means) == {"decomposition": None, "minimal_relevant_scope": None}


def test_a_second_invald_replacement_is_the_last_one(world: Any) -> None:
    """The bound is two replacements per vote: a third attempt is never dispatched."""
    target = packet(judge_panel=world.judge_panel)
    packet_row_id, _ = _register_packet(world, target)
    script = [_reply(f"not json {index}", f"b{index}") for index in range(9)]
    _run(world, target, packet_row_id, script)
    stored = world.judging.deliveries(packet_row_id)
    assert [(row["vote_index"], row["delivery_index"]) for row in stored] == [
        (vote, delivery) for vote in range(3) for delivery in range(3)
    ]
    assert all(row["invalid_reason"] == "not_json" for row in stored)
    assert [row["repair_instruction_id"] for row in stored] == [
        None,
        "schema-repair-v1",
        "schema-repair-v1",
    ] * 3
    assert len(world.transport.sent) == 9  # no fourth delivery for any vote
    assert world.result.status == "infra_blocked"
    assert world.result.valid_vote_indexes == ()


def test_zero_valued_vote_is_not_asked_again(world: Any) -> None:
    target = packet(judge_panel=world.judge_panel)
    packet_row_id, _ = _register_packet(world, target)
    zero = vote_document(
        target, scores={"decomposition": "0.000000", "minimal_relevant_scope": "0.000000"}
    )
    script = [_reply(json.dumps(zero), f"zero-{index}") for index in range(3)]
    _run(world, target, packet_row_id, script)
    assert len(world.judging.deliveries(packet_row_id)) == 3
    assert len(world.transport.sent) == 3  # no replacement delivery for a low score
    assert all(item.mean_score == "0.000000" for item in world.result.items)
    assert world.result.status == "ready"


def test_unprovisioned_judge_access_is_blocked_rather_than_invented(world: Any) -> None:
    from polycodebench_services.judging import load_panel

    unprovisioned = load_panel(Path("config/judging/panel-v1.yaml"))
    with pytest.raises(PanelUnavailable):
        unprovisioned.require_access((CANDIDATE_A, CANDIDATE_B))
    with world.database.engine.connect() as connection:
        packets = connection.execute(select(judge_packet.c.id)).all()
    assert packets is not None  # the table exists; the blocked run wrote no packet
    target = packet(judge_panel=world.judge_panel)
    packet_row_id, _ = _register_packet(world, target)
    with pytest.raises(VoteRejected):
        build_adjudication(
            packet=target,
            rubric=rubric(),
            panel=world.judge_panel,
            item_id="decomposition",
            score="1.000000",
            cited_anchor_ids=(target.spans[0].anchor_id,),
            reason="No judge has voted, so a reviewer cannot cite retained votes here.",
            reviewer_subject="reviewer-1",
            votes=(),
            reviewed_vote_indexes=(0,),
        )
    assert world.judging.votes(packet_row_id) == []


def test_reviewer_override_appends_a_result_and_preserves_the_votes(world: Any) -> None:
    target = packet(judge_panel=world.judge_panel)
    packet_row_id, _ = _register_packet(world, target)
    good = vote_document(target)
    spread = vote_document(
        target, scores={"decomposition": "0.000000", "minimal_relevant_scope": "1.000000"}
    )
    script = [
        _reply(json.dumps(good), "r0"),
        _reply(json.dumps(spread), "r1"),
        _reply(json.dumps(good), "r2"),
    ]
    _run(world, target, packet_row_id, script)
    first = world.result
    assert first.status == "needs_review"
    assert "anchor_spread" in [trigger.value for trigger in first.item("decomposition").triggers]
    _store_result(world, packet_row_id, first)

    stored_votes = [
        JudgeVote.model_validate(_envelope_payload(row_body), strict=False)
        for row_body in _stored_vote_bodies(world, packet_row_id)
    ]
    assert len(stored_votes) == 3
    span = target.spans[0].anchor_id
    decision = build_adjudication(
        packet=target,
        rubric=rubric(),
        panel=world.judge_panel,
        item_id="decomposition",
        score="1.000000",
        cited_anchor_ids=(span,),
        reason="Reviewed all three votes: the single helper is the shape the task requires here.",
        reviewer_subject="reviewer-1",
        votes=stored_votes,
        reviewed_vote_indexes=(0, 1, 2),
        decided_at="2026-10-02T02:00:00.000000Z",
    )
    artifact = world.store.put(
        judge_record_bytes(decision), kind="judge_adjudication", media_type="application/json"
    )
    adjudication_id_value = world.judging.record_adjudication(
        evaluation_id=world.evaluation_id,
        decision=decision,
        resolution_artifact_id=artifact,
        supersedes_id=None,
    )
    assert adjudication_id(decision) == str(adjudication_id_value)

    second = first.model_copy(
        update={
            "adjudications": (decision,),
            "items": tuple(
                item.model_copy(
                    update={
                        "source": "adjudication",
                        "adjudication_id": adjudication_id(decision),
                        "mean_score": decision.score,
                        "triggers": (),
                        "status": "ready",
                    }
                )
                if item.item_id == "decomposition"
                else item
                for item in first.items
            ),
        }
    )
    second = second.model_copy(update={"status": "ready"})
    _store_result(world, packet_row_id, second)

    results = world.judging.results(packet_row_id)
    assert [row["result_index"] for row in results] == [0, 1]
    assert [row["status"] for row in results] == ["needs_review", "ready"]
    # the original votes are still there, unchanged
    assert len(world.judging.votes(packet_row_id)) == 3
    with world.database.engine.connect() as connection:
        rows = connection.execute(
            select(
                judge_item_result.c.item_id,
                judge_item_result.c.source,
                judge_item_result.c.adjudication_id,
                judge_item_result.c.vote_scores,
            )
            .join(judge_result, judge_result.c.id == judge_item_result.c.result_id)
            .where(judge_result.c.result_index == 1)
        ).all()
    adjudicated = {row[0]: row for row in rows}
    assert adjudicated["decomposition"][1] == "adjudication"
    assert str(adjudicated["decomposition"][2]) == str(adjudication_id_value)
    assert adjudicated["decomposition"][3] == ["1.000000", "0.000000", "1.000000"]
    assert adjudicated["minimal_relevant_scope"][1] == "judge_votes"

    # the adjudication row and its artifact are immutable evidence
    with pytest.raises(DBAPIError), world.database.engine.begin() as connection:
        connection.execute(
            adjudication_table.update()
            .where(adjudication_table.c.id == adjudication_id_value)
            .values(reason="rewritten after the fact")
        )
    stored_adjudications = world.judging.adjudications_for(world.evaluation_id)
    assert any(row["target_kind"] == "judge_item" for row in stored_adjudications)


def _envelope_payload(body: bytes) -> dict[str, Any]:
    document = parse_json_strict(body)
    assert isinstance(document, dict) and "payload" in document
    payload = document["payload"]
    assert isinstance(payload, dict)
    return payload


def _stored_vote_bodies(world: Any, packet_row_id: UUID) -> list[bytes]:
    bodies: list[bytes] = []
    for row in world.judging.votes(packet_row_id):
        bodies.append(world.store.get(UUID(str(row["normalized_artifact_id"]))))
    return bodies


def test_calibration_labels_are_persisted_and_stored_judge_results_are_replayable(
    world: Any,
) -> None:
    """Calibration evidence storage: labels are append-only rows bound to the cohort."""
    target = packet(judge_panel=world.judge_panel, packet_role="calibration")
    packet_row_id, _ = _register_packet(world, target)
    from polycodebench_core.judge_calibration import CalibrationLabel
    from polycodebench_core.models import ScoreDimension  # noqa: F401

    label = CalibrationLabel(
        packet_id=target.packet_id,
        packet_digest=target.digest(),
        item_id="decomposition",
        score="1.000000",
        labeler_subject="reviewer-fixture",
        qualification="polycodebench_reviewer_v1",
        rationale="FIXTURE label used to prove label storage; not a human review.",
        cited_anchor_ids=(target.spans[0].anchor_id,),
        labeled_at="2026-10-02T00:00:00.000000Z",
    )
    stored = world.judging.record_label(
        cohort_id=world.cohort_id, packet_row_id=packet_row_id, label=label
    )
    rows = world.judging.labels(cohort_id=world.cohort_id)
    assert any(row["id"] == stored for row in rows)
    assert all(row["qualification"] == "polycodebench_reviewer_v1" for row in rows)
    # a duplicate label from the same reviewer for the same item is refused by the database
    with pytest.raises(Exception):  # noqa: B017 - uniqueness is a database invariant
        world.judging.record_label(
            cohort_id=world.cohort_id, packet_row_id=packet_row_id, label=label
        )


def test_calibration_packet_ids_and_selection_survive_a_database_round_trip(world: Any) -> None:
    pool = tuple(
        CalibrationPacket(
            packet_id=str(uuid4()),
            packet_digest="sha256:" + f"{index:064x}",
            language="python",
            strata=(
                CalibrationStratum.REPRESENTATIVE,
                CalibrationStratum.ADVERSARIAL_COMMENT,
                CalibrationStratum.STYLISTIC_ALTERNATIVE,
            ),
        )
        for index in range(3)
    )
    assert len({packet.packet_digest for packet in pool}) == 3
    with world.database.engine.connect() as connection:
        cohort_row = connection.execute(
            select(judge_packet.c.cohort_id, judge_packet.c.packet_role).where(
                judge_packet.c.cohort_id == world.cohort_id
            )
        ).first()
    assert cohort_row is None or cohort_row[1] in {"scored", "calibration"}


def test_cohort_registration_replays_but_refuses_a_different_panel(world: Any) -> None:
    same = cohort(
        cohort_id=f"pilot-cohort-{world.config_id}",
        judge_panel=world.judge_panel,
        candidate_model_config_ids=(CANDIDATE_A, CANDIDATE_B),
    )
    assert world.judging.register_cohort(same) == world.cohort_id
    changed = same.model_copy(
        update={"evaluation_version": 2, "panel_digest": "sha256:" + "9" * 64}
    )
    second = world.judging.register_cohort(changed)
    assert second != world.cohort_id
    with pytest.raises(Exception):  # noqa: B017 - the unique cohort/version pair is enforced
        world.judging.register_cohort(
            same.model_copy(update={"panel_digest": "sha256:" + "8" * 64})
        )


def test_judge_pending_queue_is_scoped_bounded_and_packet_locked(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = packet(judge_panel=world.judge_panel)
    packet_row_id, _ = _register_packet(world, target)
    calibration_packet = packet(judge_panel=world.judge_panel, packet_role="calibration")
    _register_packet(world, calibration_packet)

    pending = world.judging.packets_pending_execution(
        evaluation_id=world.evaluation_id,
        cohort_id=world.cohort_id,
        panel_digest=world.judge_panel.digest(),
        limit=25,
    )
    assert [row["id"] for row in pending] == [packet_row_id]
    assert (
        world.judging.packets_pending_execution(
            evaluation_id=world.evaluation_id,
            cohort_id=world.cohort_id,
            panel_digest=world.judge_panel.digest(),
            limit=25,
            after_created_at=pending[0]["created_at"],
            after_packet_id=packet_row_id,
        )
        == []
    )
    assert (
        world.judging.packets_pending_execution(
            evaluation_id=world.evaluation_id,
            cohort_id=uuid4(),
            panel_digest=world.judge_panel.digest(),
            limit=25,
        )
        == []
    )
    with pytest.raises(ValueError, match="batch size"):
        world.judging.packets_pending_execution(
            evaluation_id=world.evaluation_id,
            cohort_id=world.cohort_id,
            panel_digest=world.judge_panel.digest(),
            limit=26,
        )
    with pytest.raises(ValueError, match="both created_at and packet_id"):
        world.judging.packets_pending_execution(
            evaluation_id=world.evaluation_id,
            cohort_id=world.cohort_id,
            panel_digest=world.judge_panel.digest(),
            limit=25,
            after_created_at=pending[0]["created_at"],
        )

    with world.judging.packet_execution_lock(packet_row_id) as acquired:
        assert acquired is True
        with world.judging.packet_execution_lock(packet_row_id) as duplicate:
            assert duplicate is False

    monkeypatch.setattr(
        "polycodebench_orchestration.judge.cli.load_panel", lambda _path: world.judge_panel
    )
    replies = [
        _reply(json.dumps(vote_document(target)), f"queue-vote-{index}") for index in range(3)
    ]
    transport = ScriptedTransport(replies)
    gateway_factory = world.gateway
    world.gateway = lambda: gateway_factory(transport)
    monkeypatch.setenv("PCB_JUDGE_DISPATCH_ENABLED", "true")
    monkeypatch.setenv("PCB_SERVICE_IDENTITY", "prompt14-queue-test")
    monkeypatch.setenv("PCB_ROLES", Role.OPERATOR.value)
    monkeypatch.delenv("PCB_VERIFIED_BY", raising=False)
    monkeypatch.delenv("PCB_VERIFIED_ROLE", raising=False)
    args = SimpleNamespace(
        evaluation_id=world.evaluation_id,
        cohort_id=world.cohort_id,
        limit=25,
        poll_seconds=15,
        rubric=Path("config/judging/rubric-v1.yaml"),
        panel=Path("config/judging/panel-v1.yaml"),
    )
    assert _run_pending_batch(world, args) == EXIT_OK
    assert len(transport.sent) == 3
    assert (
        world.judging.packets_pending_execution(
            evaluation_id=world.evaluation_id,
            cohort_id=world.cohort_id,
            panel_digest=world.judge_panel.digest(),
            limit=25,
        )
        == []
    )


def test_attempt_and_evaluation_rows_are_the_real_solve_graph(world: Any) -> None:
    with world.database.engine.connect() as connection:
        state = connection.execute(
            select(evaluation.c.state).where(evaluation.c.id == world.evaluation_id)
        ).scalar_one()
        attempts = connection.execute(
            select(attempt.c.id).where(
                attempt.c.id.in_(
                    select(evaluation.c.attempt_id).where(evaluation.c.id == world.evaluation_id)
                )
            )
        ).all()
    assert state == "running"
    assert len(attempts) == 1
    assert world.ledger.verify_balances(world.account_id) == []
