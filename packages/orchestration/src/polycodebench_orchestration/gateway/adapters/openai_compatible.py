"""OpenAI-compatible Chat Completions adapter (``POST {base}/chat/completions``).

Wire facts (OpenAPI schema, openai/openai-openapi): ``max_tokens`` is deprecated in favour of
``max_completion_tokens``; ``seed``, ``temperature``, ``reasoning_effort``, ``tools`` and
``response_format`` are request fields; ``tool_calls[].function.arguments`` is a JSON *string*;
usage carries ``prompt_tokens``, ``completion_tokens`` (which includes reasoning tokens) with
``completion_tokens_details.reasoning_tokens`` and ``prompt_tokens_details.cached_tokens``.
Which of these a given endpoint honours is declared per registration, never inferred.
"""

from __future__ import annotations

import json
from typing import Any

from polycodebench_core.model_contracts import (
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
from polycodebench_core.model_planning import ModelConfig, effective_capabilities

from polycodebench_orchestration.gateway.adapters.base import (
    BaseAdapter,
    MalformedResponse,
    WireRequest,
    loads_object,
    non_negative_int,
)
from polycodebench_orchestration.gateway.secrets import Secret

_FINISH = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
    "content_filter": FinishReason.CONTENT_FILTER,
}


class OpenAICompatibleAdapter(BaseAdapter):
    provider_kind = ProviderKind.OPENAI_COMPATIBLE
    wire_capabilities = ModelCapabilities(
        native_tools=True,
        structured_output=True,
        seed=True,
        seed_min=-(2**63),
        seed_max=2**63 - 1,
        temperature=True,
        reasoning_efforts=("minimal", "low", "medium", "high"),
        reasoning_budget_tokens=True,
        streaming=False,
        usage_counters=frozenset(
            {
                UsageCounter.INPUT,
                UsageCounter.OUTPUT,
                UsageCounter.REASONING,
                UsageCounter.CACHED_INPUT,
            }
        ),
    )
    requires_authentication = True

    def build_request(self, config: ModelConfig, request: ModelRequest) -> WireRequest:
        decision = self.decide_controls(config, request)
        caps = effective_capabilities(self.capabilities(), config.declared_capabilities)
        body: dict[str, Any] = {
            "model": config.model,
            "messages": self._messages(request),
            caps.output_limit_parameter: request.max_output_tokens,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters_schema,
                    },
                }
                for tool in request.tools
            ]
            body["tool_choice"] = "auto"
        if request.response_schema is not None and decision.send_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema.name,
                    "schema": request.response_schema.json_schema,
                    "strict": True,
                },
            }
        if request.temperature is not None and decision.send_temperature:
            body["temperature"] = float(request.temperature)
        if request.seed is not None and decision.send_seed:
            body["seed"] = request.seed
        if request.reasoning is not None and request.reasoning.effort is not None:
            body["reasoning_effort"] = request.reasoning.effort
        elif request.reasoning is not None and request.reasoning.budget_tokens is not None:
            # Opt-in only (the model config must declare reasoning_budget_tokens): OpenRouter's
            # documented nested object, which caps reasoning tokens.
            body["reasoning"] = {"max_tokens": request.reasoning.budget_tokens}
        return WireRequest(
            path="/chat/completions",
            headers={"content-type": "application/json", "accept": "application/json"},
            body=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            dropped_controls=tuple(decision.dropped),
        )

    def authenticate(self, headers: dict[str, str], secret: Secret | None) -> dict[str, str]:
        if secret is not None:
            headers["authorization"] = f"Bearer {secret.reveal()}"
        return headers

    def _messages(self, request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if request.system:
            out.append({"role": "system", "content": request.system})
        for message in request.messages:
            if (
                message.provider_payload is not None
                and message.provider_payload_origin is self.provider_kind
            ):
                out.append(message.provider_payload)
            else:
                out.extend(self._convert(message))
        return out

    @staticmethod
    def _convert(message: Message) -> list[dict[str, Any]]:
        texts = [b.text for b in message.blocks if isinstance(b, TextBlock)]
        calls = [b for b in message.blocks if isinstance(b, ToolCallBlock)]
        results = [b for b in message.blocks if isinstance(b, ToolResultBlock)]
        out: list[dict[str, Any]] = [
            {"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in results
        ]
        if message.role == "user":
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
            return out
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(texts) if texts else None,
        }
        if calls:
            entry["tool_calls"] = [
                {
                    "id": c.call_id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments_json},
                }
                for c in calls
            ]
        return [entry]

    def parse_response(self, body: bytes, headers: dict[str, str]) -> ModelResponse:
        data = loads_object(body)
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise MalformedResponse("expected exactly one choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise MalformedResponse("choice has no message")
        blocks: list[TextBlock | ToolCallBlock] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            blocks.append(TextBlock(text=content))
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal:
            blocks.append(TextBlock(text=refusal))
        for raw in message.get("tool_calls") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                raise MalformedResponse("malformed tool call")
            function = raw["function"]
            arguments = function.get("arguments", "")
            if not isinstance(arguments, str) or not isinstance(function.get("name"), str):
                raise MalformedResponse("malformed tool call")
            call_id = raw.get("id")
            if not isinstance(call_id, str) or not call_id:
                raise MalformedResponse("tool call has no id")
            blocks.append(
                ToolCallBlock(
                    call_id=call_id,
                    name=function["name"],
                    arguments_json=arguments,
                    arguments_valid=_is_json_object(arguments),
                )
            )
        raw_finish = choice.get("finish_reason")
        finish = (
            _FINISH.get(raw_finish, FinishReason.OTHER)
            if isinstance(raw_finish, str)
            else (FinishReason.OTHER)
        )
        if refusal:
            finish = FinishReason.CONTENT_FILTER
        elif any(isinstance(b, ToolCallBlock) for b in blocks) and finish is FinishReason.STOP:
            finish = FinishReason.TOOL_CALLS
        model = data.get("model")
        fingerprint = data.get("system_fingerprint")
        revision = "|".join(x for x in (model, fingerprint) if isinstance(x, str)) or None
        response_id = data.get("id")
        return ModelResponse(
            provider_request_id=self.provider_request_id(headers),
            provider_response_id=response_id if isinstance(response_id, str) else None,
            finish_reason=finish,
            raw_finish_reason=raw_finish if isinstance(raw_finish, str) else None,
            blocks=tuple(blocks),
            usage=_usage(data.get("usage")),
            provider_revision=revision,
            provider_payload=message,
            provider_payload_origin=self.provider_kind,
        )


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text or "{}"), dict)
    except ValueError:
        return False


def _usage(raw: Any) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    details = raw.get("completion_tokens_details")
    prompt_details = raw.get("prompt_tokens_details")
    return Usage(
        input_tokens=non_negative_int(raw.get("prompt_tokens")),
        output_tokens=non_negative_int(raw.get("completion_tokens")),
        reasoning_tokens=non_negative_int(
            details.get("reasoning_tokens") if isinstance(details, dict) else None
        ),
        cached_input_tokens=non_negative_int(
            prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None
        ),
    )
