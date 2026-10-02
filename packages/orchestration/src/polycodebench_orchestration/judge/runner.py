"""Judge gateway: run one packet through exactly three logical votes.

The runner owns the parts that need the network and the ledger. For every vote index it dispatches
one delivery, validates the response against the packet, and — only if the response was *invalid* —
dispatches at most two replacements carrying the frozen schema-repair instruction. A low score is
never a reason to ask again, and every delivery, valid or not, is retained with its reason.

Judge calls are charged to the evaluation scope through the same ``ModelGateway`` the solve path
uses, so budget accounting, endpoint approval, capability validation and response settlement are
identical to any other model call.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from polycodebench_core.canonical import sha256_bytes
from polycodebench_core.identity import utc_timestamp
from polycodebench_core.judge_contracts import (
    AdjudicationDecision,
    InvalidVoteReason,
    JudgeDelivery,
    JudgementResult,
    JudgePacket,
    JudgePanel,
    JudgeVote,
    VoteRejected,
    judge_record_bytes,
)
from polycodebench_core.judge_prompts import (
    judge_request_prompt,
    repair_reason,
    vote_response_schema,
)
from polycodebench_core.model_contracts import (
    BudgetExhausted,
    CallScope,
    CapabilityUnsupported,
    CostBoundUnavailable,
    Message,
    ModelRequest,
    ProviderCallFailed,
    ResponseSchema,
    TextBlock,
)
from polycodebench_core.model_planning import ModelConfig
from polycodebench_core.models import ProtocolDefinition
from polycodebench_services.judging import aggregate, parse_vote
from pydantic import ValidationError

from polycodebench_orchestration.gateway.service import GatewayResult, ModelGateway

MAX_SEED = 18_446_744_073_709_551_615


def vote_seed(packet_digest: str, vote_index: int) -> int:
    """Deterministic, recorded per-vote seed material. Distinct per vote by construction."""
    material = hashlib.sha256(f"judge-vote/{packet_digest}/{vote_index}".encode()).digest()
    return int.from_bytes(material[:8], "big")


@dataclass(frozen=True)
class VoteArtifacts:
    """Where a delivery's bytes landed. Supplied by the caller's artifact store."""

    raw_artifact_id: str | None = None
    call_delivery_id: str | None = None
    call_intent_id: str | None = None


@dataclass
class PacketRun:
    """What one panel run produced for one packet: every delivery, every vote, the result."""

    packet: JudgePacket
    deliveries: list[JudgeDelivery] = field(default_factory=list)
    votes: list[JudgeVote] = field(default_factory=list)
    request_digests: list[str] = field(default_factory=list)

    def result(
        self,
        panel: JudgePanel,
        *,
        adjudications: tuple[AdjudicationDecision, ...] = (),
        audit_selected: bool = False,
    ) -> JudgementResult:
        return aggregate(
            packet=self.packet,
            panel=panel,
            votes=self.votes,
            deliveries=self.deliveries,
            adjudications=adjudications,
            audit_selected=audit_selected,
        )


class JudgeRunner:
    """Dispatch and validate judge votes for one packet."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        panel: JudgePanel,
        protocol: ProtocolDefinition,
        resolve_artifacts: Callable[[JudgePacket, int, int, GatewayResult], VoteArtifacts],
        persist_delivery: Callable[[JudgePacket, JudgeDelivery, VoteArtifacts], None],
        persist_vote: Callable[[JudgePacket, JudgeVote, bytes, VoteArtifacts], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._gateway = gateway
        self._panel = panel
        self._protocol = protocol
        self._resolve_artifacts = resolve_artifacts
        self._persist_delivery = persist_delivery
        self._persist_vote = persist_vote
        self._sleep = sleep

    @property
    def panel(self) -> JudgePanel:
        return self._panel

    async def run(
        self,
        *,
        packet: JudgePacket,
        config: ModelConfig,
        config_document_id: UUID,
        scope: CallScope,
        dispatch_allowed: Callable[[], bool] | None = None,
    ) -> PacketRun:
        """Run the whole panel for one packet.

        Delivery and vote identities are derived from the packet, the vote index and the delivery
        index, so a replayed run re-uses the gateway's recorded responses instead of re-sampling.
        """
        if self._panel.digest() != packet.panel_digest:
            raise VoteRejected("panel_mismatch", "packet and panel are not the same frozen pair")
        run = PacketRun(packet=packet)
        for vote_index in range(self._panel.votes_required):
            await self._one_vote(
                run=run,
                vote_index=vote_index,
                config=config,
                config_document_id=config_document_id,
                scope=scope,
                dispatch_allowed=dispatch_allowed,
            )
        return run

    async def _one_vote(
        self,
        *,
        run: PacketRun,
        vote_index: int,
        config: ModelConfig,
        config_document_id: UUID,
        scope: CallScope,
        dispatch_allowed: Callable[[], bool] | None,
    ) -> None:
        packet = run.packet
        repair: tuple[str, str] | None = None
        for delivery_index in range(self._panel.max_deliveries_per_vote()):
            seed = self._seed_for(config, packet, vote_index)
            # The packet, schema, evidence and seed are identical across every delivery of one
            # logical vote. The fixed schema-repair instruction is applied to the rendered user
            # message only, after validation fails, so the replacement asks the same question of the
            # same evidence; the ledger can therefore tell a replacement from a different input.
            request = self._request(
                packet=packet,
                vote_index=vote_index,
                seed=seed,
                repair=repair,
                config=config,
            )
            run.request_digests.append(request.digest())
            key = f"judge/{packet.digest()}/{vote_index}/{delivery_index}"
            try:
                result = await self._gateway.call(
                    scope=scope,
                    logical_call_key=key,
                    config=config,
                    config_document_id=config_document_id,
                    protocol=self._protocol,
                    request=request,
                    dispatch_allowed=dispatch_allowed,
                )
            except ProviderCallFailed as failure:
                delivery = self._transport_delivery(
                    packet, vote_index, delivery_index, seed, failure, repair
                )
                run.deliveries.append(delivery)
                self._persist_delivery(packet, delivery, VoteArtifacts())
                return
            except (BudgetExhausted, CostBoundUnavailable, CapabilityUnsupported):
                # A blocked judge call is not a vote and not a model failure: it propagates so the
                # stage records infra_blocked instead of a partial average.
                raise
            artifacts = self._resolve_artifacts(packet, vote_index, delivery_index, result)
            text = result.response.text
            raw_digest = sha256_bytes(text.encode("utf-8"))
            try:
                vote = parse_vote(
                    text=text,
                    packet=packet,
                    panel=self._panel,
                    vote_index=vote_index,
                    seed=seed,
                    raw_response_digest=raw_digest,
                    created_at=utc_timestamp(),
                    raw_response_artifact_id=artifacts.raw_artifact_id,
                    call_delivery_id=artifacts.call_delivery_id,
                )
            except VoteRejected as rejected:
                delivery = JudgeDelivery(
                    packet_id=packet.packet_id,
                    vote_index=vote_index,
                    delivery_index=delivery_index,
                    status="invalid",
                    invalid_reason=_reason(rejected.reason),
                    detail=rejected.detail,
                    judge_revision=self._panel.judge_revision,
                    seed=seed,
                    seed_supported=seed is not None,
                    repair_instruction_id=repair[0] if repair else None,
                    raw_response_digest=raw_digest,
                    raw_response_artifact_id=artifacts.raw_artifact_id,
                    call_delivery_id=artifacts.call_delivery_id,
                    call_intent_id=artifacts.call_intent_id,
                    created_at=utc_timestamp(),
                )
                run.deliveries.append(delivery)
                self._persist_delivery(packet, delivery, artifacts)
                if delivery_index + 1 < self._panel.max_deliveries_per_vote():
                    repair = repair_reason(self._panel.repair, rejected.reason)
                continue
            except (ValidationError, ValueError) as invalid:
                # A response the validator did not anticipate is still a rejected delivery: it is
                # retained with its reason and the vote is not silently dropped.
                delivery = JudgeDelivery(
                    packet_id=packet.packet_id,
                    vote_index=vote_index,
                    delivery_index=delivery_index,
                    status="invalid",
                    invalid_reason=InvalidVoteReason.SCHEMA_VIOLATION,
                    detail=f"contract violation: {str(invalid)[:200]}",
                    judge_revision=self._panel.judge_revision,
                    seed=seed,
                    seed_supported=seed is not None,
                    repair_instruction_id=repair[0] if repair else None,
                    raw_response_digest=raw_digest,
                    raw_response_artifact_id=artifacts.raw_artifact_id,
                    call_delivery_id=artifacts.call_delivery_id,
                    call_intent_id=artifacts.call_intent_id,
                    created_at=utc_timestamp(),
                )
                run.deliveries.append(delivery)
                self._persist_delivery(packet, delivery, artifacts)
                if delivery_index + 1 < self._panel.max_deliveries_per_vote():
                    repair = repair_reason(self._panel.repair, "schema_violation")
                continue
            delivery = JudgeDelivery(
                packet_id=packet.packet_id,
                vote_index=vote_index,
                delivery_index=delivery_index,
                status="valid",
                judge_revision=self._panel.judge_revision,
                seed=seed,
                seed_supported=seed is not None,
                repair_instruction_id=repair[0] if repair else None,
                raw_response_digest=raw_digest,
                raw_response_artifact_id=artifacts.raw_artifact_id,
                call_delivery_id=artifacts.call_delivery_id,
                call_intent_id=artifacts.call_intent_id,
                created_at=utc_timestamp(),
            )
            run.deliveries.append(delivery)
            run.votes.append(vote)
            self._persist_delivery(packet, delivery, artifacts)
            if self._persist_vote is not None:
                self._persist_vote(packet, vote, judge_record_bytes(vote), artifacts)
            return

    def _seed_for(self, config: ModelConfig, packet: JudgePacket, vote_index: int) -> int | None:
        """The recorded per-vote seed, or None when the judge configuration cannot take one."""
        base = vote_seed(packet.digest(), vote_index)
        return self._gateway.provider_seed(config, base)

    def _request(
        self,
        *,
        packet: JudgePacket,
        vote_index: int,
        seed: int | None,
        repair: tuple[str, str] | None,
        config: ModelConfig,
    ) -> ModelRequest:
        system, user = judge_request_prompt(packet, self._panel, repair=repair)
        schema = vote_response_schema(self._panel, packet)
        # The panel declares the judge's output ceiling; the resolved model configuration may allow
        # less, and a request may never ask for more than the configuration declared. A judge that
        # cannot answer inside its own ceiling returns an invalid vote, not a larger request.
        ceiling = min(self._panel.inference.max_output_tokens, config.max_output_tokens)
        return ModelRequest(
            system=system,
            messages=(
                Message(
                    role="user",
                    blocks=(
                        TextBlock(
                            text=f"Vote {vote_index} of {self._panel.votes_required}. {user}"
                        ),
                    ),
                ),
            ),
            tools=(),
            response_schema=ResponseSchema(
                name=self._panel.inference.response_schema_name, json_schema=schema
            ),
            temperature=self._panel.inference.temperature,
            seed=seed,
            max_output_tokens=ceiling,
        )

    def _transport_delivery(
        self,
        packet: JudgePacket,
        vote_index: int,
        delivery_index: int,
        seed: int | None,
        failure: ProviderCallFailed,
        repair: tuple[str, str] | None,
    ) -> JudgeDelivery:
        return JudgeDelivery(
            packet_id=packet.packet_id,
            vote_index=vote_index,
            delivery_index=delivery_index,
            status="transport_failure",
            invalid_reason=None,
            detail=f"{failure.failure.kind.value}:{failure.failure.code}"[:400],
            judge_revision=self._panel.judge_revision,
            seed=seed,
            seed_supported=seed is not None,
            repair_instruction_id=repair[0] if repair else None,
            call_intent_id=failure.intent_id,
            created_at=utc_timestamp(),
        )


def _reason(reason: str) -> InvalidVoteReason:
    try:
        return InvalidVoteReason(reason)
    except ValueError:
        return InvalidVoteReason.SCHEMA_VIOLATION
