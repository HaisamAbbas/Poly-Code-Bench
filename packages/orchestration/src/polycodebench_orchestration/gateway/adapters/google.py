"""Google Gemini ``generateContent`` adapter.

``POST {base}/v1beta/models/{model}:generateContent``.

Wire facts verified against the Gemini API reference (fetched 2026-09-30): ``contents``,
``systemInstruction``, ``tools[].functionDeclarations``, ``toolConfig``,
``generationConfig.{temperature,seed,maxOutputTokens}``, header ``x-goog-api-key``, response
``candidates[].finishReason``, ``usageMetadata.{promptTokenCount,candidatesTokenCount,
thoughtsTokenCount,totalTokenCount}``, ``modelVersion`` and ``responseId``. The thinking
guide states ``maxOutputTokens`` includes thought tokens.

Not implemented, therefore rejected rather than guessed: thinking controls and schema-
constrained output. Google's current guides document those for the newer Interactions API and
the REST field names for ``generateContent`` could not be confirmed from the fetched docs.
"""

from __future__ import annotations

import json
import re
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

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FILTERED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


class GoogleAdapter(BaseAdapter):
    provider_kind = ProviderKind.GOOGLE
    wire_capabilities = ModelCapabilities(
        native_tools=True,
        structured_output=False,
        seed=True,
        seed_min=-(2**31),
        seed_max=2**31 - 1,
        temperature=True,
        reasoning_efforts=(),
        reasoning_budget_tokens=False,
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

    def build_request(self, config: ModelConfig, request: ModelRequest) -> WireRequest:
        decision = self.decide_controls(config, request)
        if not _MODEL_NAME.fullmatch(config.model):
            raise CapabilityUnsupported("model name is not a valid path segment")
        generation: dict[str, Any] = {"maxOutputTokens": request.max_output_tokens}
        if request.temperature is not None and decision.send_temperature:
            generation["temperature"] = float(request.temperature)
        if request.seed is not None and decision.send_seed:
            generation["seed"] = request.seed
        body: dict[str, Any] = {
            "contents": [self._content(m) for m in request.messages],
            "generationConfig": generation,
        }
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        if request.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters_schema,
                        }
                        for tool in request.tools
                    ]
                }
            ]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        return WireRequest(
            path=f"/v1beta/models/{config.model}:generateContent",
            headers={"content-type": "application/json", "accept": "application/json"},
            body=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            dropped_controls=tuple(decision.dropped),
        )

    def authenticate(self, headers: dict[str, str], secret: Secret | None) -> dict[str, str]:
        if secret is None:
            raise CapabilityUnsupported("Google requests require credentials")
        headers["x-goog-api-key"] = secret.reveal()
        return headers

    def _content(self, message: Message) -> dict[str, Any]:
        if (
            message.provider_payload is not None
            and message.provider_payload_origin is self.provider_kind
        ):
            return message.provider_payload
        parts: list[dict[str, Any]] = []
        for block in message.blocks:
            if isinstance(block, TextBlock):
                parts.append({"text": block.text})
            elif isinstance(block, ToolResultBlock):
                key = "error" if block.is_error else "output"
                parts.append(
                    {"functionResponse": {"name": block.name, "response": {key: block.content}}}
                )
            else:
                if not block.arguments_valid:
                    raise CapabilityUnsupported("cannot replay a tool call with invalid arguments")
                parts.append(
                    {"functionCall": {"name": block.name, "args": json.loads(block.arguments_json)}}
                )
        return {"role": "user" if message.role == "user" else "model", "parts": parts}

    def provider_request_id(self, headers: dict[str, str]) -> str | None:
        return headers.get("x-goog-request-id") or headers.get("x-request-id")

    def parse_response(self, body: bytes, headers: dict[str, str]) -> ModelResponse:
        data = loads_object(body)
        candidates = data.get("candidates")
        feedback = data.get("promptFeedback")
        blocks: list[TextBlock | ToolCallBlock] = []
        raw_finish: str | None = None
        payload: dict[str, Any] | None = None
        if isinstance(candidates, list) and candidates:
            candidate = candidates[0]
            if not isinstance(candidate, dict):
                raise MalformedResponse("malformed candidate")
            raw_finish = candidate.get("finishReason")
            content = candidate.get("content")
            if isinstance(content, dict):
                payload = content
                for index, part in enumerate(content.get("parts") or []):
                    if not isinstance(part, dict):
                        raise MalformedResponse("malformed part")
                    if isinstance(part.get("functionCall"), dict):
                        call = part["functionCall"]
                        if not isinstance(call.get("name"), str):
                            raise MalformedResponse("function call has no name")
                        args = call.get("args", {})
                        call_id = call.get("id")
                        blocks.append(
                            ToolCallBlock(
                                call_id=call_id if isinstance(call_id, str) else f"call_{index}",
                                name=call["name"],
                                arguments_json=json.dumps(args, separators=(",", ":")),
                                arguments_valid=isinstance(args, dict),
                            )
                        )
                    elif isinstance(part.get("text"), str) and not part.get("thought"):
                        blocks.append(TextBlock(text=part["text"]))
        elif not isinstance(feedback, dict):
            raise MalformedResponse("response has neither candidates nor promptFeedback")
        else:
            raw_finish = "PROMPT_BLOCKED"
        if raw_finish == "STOP" or raw_finish is None:
            finish = FinishReason.STOP
        elif raw_finish == "MAX_TOKENS":
            finish = FinishReason.LENGTH
        elif raw_finish in _FILTERED or raw_finish == "PROMPT_BLOCKED":
            finish = FinishReason.CONTENT_FILTER
        else:
            finish = FinishReason.OTHER
        if any(isinstance(b, ToolCallBlock) for b in blocks) and finish is FinishReason.STOP:
            finish = FinishReason.TOOL_CALLS
        version = data.get("modelVersion")
        response_id = data.get("responseId")
        return ModelResponse(
            provider_request_id=self.provider_request_id(headers)
            or (response_id if isinstance(response_id, str) else None),
            provider_response_id=response_id if isinstance(response_id, str) else None,
            finish_reason=finish,
            raw_finish_reason=raw_finish if isinstance(raw_finish, str) else None,
            blocks=tuple(blocks),
            usage=_usage(data.get("usageMetadata")),
            provider_revision=version if isinstance(version, str) else None,
            provider_payload=payload,
            provider_payload_origin=self.provider_kind if payload is not None else None,
        )


def _usage(raw: Any) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    prompt = non_negative_int(raw.get("promptTokenCount"))
    tool_prompt = non_negative_int(raw.get("toolUsePromptTokenCount"))
    candidates = non_negative_int(raw.get("candidatesTokenCount"))
    thoughts = non_negative_int(raw.get("thoughtsTokenCount"))
    # Thought tokens are billed as output and are reported separately from candidates.
    output = None if candidates is None else candidates + (thoughts or 0)
    return Usage(
        input_tokens=None if prompt is None else prompt + (tool_prompt or 0),
        output_tokens=output,
        reasoning_tokens=thoughts,
        cached_input_tokens=non_negative_int(raw.get("cachedContentTokenCount")),
    )
