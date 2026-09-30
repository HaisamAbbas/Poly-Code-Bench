"""Anthropic Messages adapter (``POST {base}/v1/messages``).

Wire facts (Claude API reference, fetched 2026-09-30): ``max_tokens`` is required; there is no
``seed`` parameter; ``temperature`` is deprecated for newer models so it is only sent when the
registration declares it; ``output_config.format`` carries JSON-schema output; ``usage``
reports ``input_tokens``, ``output_tokens`` plus cache creation/read counters, and
``output_tokens`` already includes thinking tokens; the request id is the ``request-id``
response header. Assistant turns round-trip through ``provider_payload`` so thinking blocks
and their signatures are replayed verbatim.
"""

from __future__ import annotations

import json
from typing import Any

from polycodebench_core.model_contracts import (
    CapabilityUnsupported,
    FinishReason,
    Message,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ProviderKind,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    Usage,
    UsageCounter,
)
from polycodebench_core.model_planning import ModelConfig

from polycodebench_orchestration.gateway.adapters.base import (
    BaseAdapter,
    MalformedResponse,
    WireRequest,
    loads_object,
    non_negative_int,
)
from polycodebench_orchestration.gateway.secrets import Secret

ANTHROPIC_VERSION = "2023-06-01"
_FINISH = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_CALLS,
    "refusal": FinishReason.CONTENT_FILTER,
}


class AnthropicAdapter(BaseAdapter):
    provider_kind = ProviderKind.ANTHROPIC
    wire_capabilities = ModelCapabilities(
        native_tools=True,
        structured_output=True,
        seed=False,
        temperature=True,
        reasoning_efforts=(),
        reasoning_budget_tokens=True,
        streaming=False,
        usage_counters=frozenset(
            {UsageCounter.INPUT, UsageCounter.OUTPUT, UsageCounter.CACHED_INPUT}
        ),
    )

    def build_request(self, config: ModelConfig, request: ModelRequest) -> WireRequest:
        decision = self.decide_controls(config, request)
        body: dict[str, Any] = {
            "model": config.model,
            "max_tokens": request.max_output_tokens,
            "messages": [self._message(m) for m in request.messages],
        }
        if request.system:
            body["system"] = request.system
        if request.tools:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters_schema,
                }
                for tool in request.tools
            ]
            body["tool_choice"] = {"type": "auto"}
        if request.response_schema is not None and decision.send_schema:
            body["output_config"] = {
                "format": {"type": "json_schema", "schema": request.response_schema.json_schema}
            }
        if request.temperature is not None and decision.send_temperature:
            body["temperature"] = float(request.temperature)
        if request.reasoning is not None and request.reasoning.budget_tokens is not None:
            if request.reasoning.budget_tokens >= request.max_output_tokens:
                raise CapabilityUnsupported("thinking budget must be below max output tokens")
            body["thinking"] = {"type": "enabled", "budget_tokens": request.reasoning.budget_tokens}
        return WireRequest(
            path="/v1/messages",
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "anthropic-version": ANTHROPIC_VERSION,
            },
            body=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            dropped_controls=tuple(decision.dropped),
        )

    def authenticate(self, headers: dict[str, str], secret: Secret | None) -> dict[str, str]:
        if secret is None:
            raise CapabilityUnsupported("Anthropic requests require credentials")
        headers["x-api-key"] = secret.reveal()
        return headers

    def _message(self, message: Message) -> dict[str, Any]:
        if (
            message.provider_payload is not None
            and message.provider_payload_origin is self.provider_kind
        ):
            return message.provider_payload
        content: list[dict[str, Any]] = []
        # tool_result blocks must come first in the user turn that answers a tool call.
        ordered = sorted(message.blocks, key=lambda b: not isinstance(b, ToolResultBlock))
        for block in ordered:
            if isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolResultBlock):
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.call_id,
                        "content": block.content,
                        "is_error": block.is_error,
                    }
                )
            else:
                if not block.arguments_valid:
                    raise CapabilityUnsupported("cannot replay a tool call with invalid arguments")
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.call_id,
                        "name": block.name,
                        "input": json.loads(block.arguments_json or "{}"),
                    }
                )
        return {"role": message.role, "content": content}

    def parse_response(self, body: bytes, headers: dict[str, str]) -> ModelResponse:
        data = loads_object(body)
        content = data.get("content")
        if data.get("type") == "error" or not isinstance(content, list):
            raise MalformedResponse("not a message response")
        blocks: list[TextBlock | ToolCallBlock] = []
        for raw in content:
            if not isinstance(raw, dict):
                raise MalformedResponse("malformed content block")
            kind = raw.get("type")
            if kind == "text" and isinstance(raw.get("text"), str):
                blocks.append(TextBlock(text=raw["text"]))
            elif kind == "tool_use":
                if not isinstance(raw.get("id"), str) or not isinstance(raw.get("name"), str):
                    raise MalformedResponse("malformed tool_use block")
                arguments = raw.get("input", {})
                blocks.append(
                    ToolCallBlock(
                        call_id=raw["id"],
                        name=raw["name"],
                        arguments_json=json.dumps(arguments, separators=(",", ":")),
                        arguments_valid=isinstance(arguments, dict),
                    )
                )
            # thinking / redacted_thinking blocks are preserved only via provider_payload
        stop = data.get("stop_reason")
        model = data.get("model")
        message_id = data.get("id")
        return ModelResponse(
            provider_request_id=headers.get("request-id") or headers.get("x-request-id"),
            provider_response_id=message_id if isinstance(message_id, str) else None,
            finish_reason=_FINISH.get(stop, FinishReason.OTHER)
            if isinstance(stop, str)
            else FinishReason.OTHER,
            raw_finish_reason=stop if isinstance(stop, str) else None,
            blocks=tuple(blocks),
            usage=_usage(data.get("usage")),
            provider_revision=model if isinstance(model, str) else None,
            provider_payload={"role": "assistant", "content": content},
            provider_payload_origin=self.provider_kind,
        )


def _usage(raw: Any) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    base = non_negative_int(raw.get("input_tokens"))
    created = non_negative_int(raw.get("cache_creation_input_tokens"))
    read = non_negative_int(raw.get("cache_read_input_tokens"))
    # input_tokens excludes cached segments; billed input is the sum of the reported parts.
    input_tokens = None if base is None else base + (created or 0) + (read or 0)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=non_negative_int(raw.get("output_tokens")),
        reasoning_tokens=None,
        cached_input_tokens=read,
    )
