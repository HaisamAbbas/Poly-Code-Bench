"""Deterministic fixtures for model gateway tests.

EVIDENCE LABEL: everything here is FIXTURE evidence. ``ScriptedTransport`` replays scripted
HTTP outcomes and faults; provider bodies are hand-written shapes that follow each provider's
documented response schema. None of it proves a live provider behaves this way.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from polycodebench_core.endpoint_policy import RegisteredEndpoint
from polycodebench_core.model_contracts import (
    Message,
    ModelCapabilities,
    ModelRequest,
    PriceSnapshot,
    ProviderKind,
    TextBlock,
    UsageCounter,
)
from polycodebench_core.model_planning import ModelConfig
from polycodebench_core.models import ProtocolDefinition
from polycodebench_orchestration.gateway.transport import HttpResponse, TransportError

SECRET_VALUE = "sk-test-0123456789-not-a-real-key"


@dataclass
class Reply:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Fail:
    error: TransportError


class ScriptedTransport:
    """Pops one scripted outcome per ``send``; records every request that reached it."""

    def __init__(
        self,
        script: list[Reply | Fail] | None = None,
        *,
        default: Callable[[], Reply] | None = None,
        gate: asyncio.Event | None = None,
        on_send: Callable[[], None] | None = None,
    ) -> None:
        self.on_send = on_send
        self.script = list(script or [])
        self.default = default
        self.gate = gate
        self.sent: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak = 0

    async def send(
        self,
        endpoint: RegisteredEndpoint,
        path: str,
        headers: dict[str, str],
        body: bytes,
        *,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.sent.append({"path": path, "headers": dict(headers), "body": body})
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        if self.on_send is not None:
            self.on_send()
        try:
            if self.gate is not None:
                await self.gate.wait()
            step = self.script.pop(0) if self.script else (self.default() if self.default else None)
            if step is None:
                raise AssertionError("scripted transport has no outcome left")
            if isinstance(step, Fail):
                raise step.error
            return HttpResponse(status=step.status, headers=dict(step.headers), body=step.body)
        finally:
            self.in_flight -= 1


def openai_body(
    text: str = "hello",
    *,
    usage: dict[str, Any] | None = {"prompt_tokens": 120, "completion_tokens": 30},  # noqa: B006
    tool_calls: list[dict[str, Any]] | None = None,
    finish: str = "stop",
) -> bytes:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish = "tool_calls"
    document: dict[str, Any] = {
        "id": "chatcmpl-fixture-1",
        "object": "chat.completion",
        "model": "fixture-model",
        "system_fingerprint": "fp_fixture",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage is not None:
        document["usage"] = usage
    return json.dumps(document).encode()


def ok(text: str = "hello", request_id: str = "req-fixture-1", **kwargs: Any) -> Reply:
    return Reply(200, openai_body(text, **kwargs), {"x-request-id": request_id})


FULL_CAPS = ModelCapabilities(
    native_tools=True,
    structured_output=True,
    seed=True,
    seed_min=0,
    seed_max=2**31 - 1,
    temperature=True,
    reasoning_efforts=("low", "high"),
    context_limit_tokens=128_000,
    max_output_tokens_limit=16_384,
    usage_counters=frozenset({UsageCounter.INPUT, UsageCounter.OUTPUT, UsageCounter.REASONING}),
    output_cap_bounds_all_billed_output=True,
)
PRICE = PriceSnapshot(
    price_id="fixture-price-v1",
    input_micro_usd_per_million_tokens=3_000_000,
    output_micro_usd_per_million_tokens=15_000_000,
    basis="contract_price",
    source="test fixture; not a real price",
    effective_date="2026-09-30",
)


def make_config(
    endpoint_id: UUID,
    *,
    provider: ProviderKind = ProviderKind.LOCAL,
    capabilities: ModelCapabilities = FULL_CAPS,
    price: PriceSnapshot | None = PRICE,
    max_output_tokens: int = 1000,
    **overrides: Any,
) -> ModelConfig:
    values: dict[str, Any] = {
        "schema_version": 1,
        "kind": "model_config",
        "provider_kind": provider,
        "model": "fixture-model",
        "immutable_revision": "fixture-rev-1",
        "endpoint_id": endpoint_id,
        "declared_capabilities": capabilities,
        "price": price,
        "temperature": "0.000000",
        "seed_policy": "pass_if_supported",
        "reasoning": None,
        "max_output_tokens": max_output_tokens,
        "strict_money_cap": True,
    }
    values.update(overrides)
    return ModelConfig(**values)


def single_shot_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        schema_version=1,
        kind="protocol",
        protocol_id="single-shot-v1",
        version=1,
        mode="single_shot",
        allowed_tools=[],
        public_test_feedback=False,
        hidden_feedback=False,
        network_policy="disabled",
        maximum_turns=1,
        maximum_tool_calls=0,
        maximum_wall_seconds=180,
        maximum_input_context_tokens=32_000,
    )


def agent_protocol() -> ProtocolDefinition:
    return ProtocolDefinition(
        schema_version=1,
        kind="protocol",
        protocol_id="standard-agent-v1",
        version=1,
        mode="standard_agent",
        allowed_tools=["list_files", "read_file"],
        public_test_feedback=True,
        hidden_feedback=False,
        network_policy="disabled",
        maximum_turns=30,
        maximum_tool_calls=100,
        maximum_wall_seconds=600,
        maximum_input_context_tokens=64_000,
    )


def make_request(prompt: str = "Write fizzbuzz.", **overrides: Any) -> ModelRequest:
    values: dict[str, Any] = {
        "system": "You are a careful engineer.",
        "messages": (Message(role="user", blocks=(TextBlock(text=prompt),)),),
        "temperature": "0.000000",
        "max_output_tokens": 1000,
    }
    values.update(overrides)
    return ModelRequest(**values)
