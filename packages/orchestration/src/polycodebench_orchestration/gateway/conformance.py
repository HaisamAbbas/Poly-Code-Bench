"""Conformance probes for endpoints that claim provider compatibility.

A nominally OpenAI-compatible or local server must *demonstrate* the capabilities its
registration claims (usage counters, native tool calls, accepted controls). The report is
stored with the registration and required for approval; it is evidence from that exact
endpoint at that time, not a property of a model name.
"""

from __future__ import annotations

import json
from typing import Any

from polycodebench_core.endpoint_policy import RegisteredEndpoint
from polycodebench_core.model_contracts import (
    Message,
    ModelRequest,
    TextBlock,
    ToolSpec,
    UsageCounter,
)
from polycodebench_core.model_planning import ModelConfig, effective_capabilities

from polycodebench_orchestration.gateway.adapters.base import BaseAdapter, _FailureRaised
from polycodebench_orchestration.gateway.secrets import Secret
from polycodebench_orchestration.gateway.transport import HttpTransport

PROBE_OUTPUT_TOKENS = 128
_ECHO_TOOL = ToolSpec(
    name="echo",
    description="Return the supplied value to the caller.",
    parameters_schema={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    },
)


async def run_conformance(
    adapter: BaseAdapter,
    transport: HttpTransport,
    endpoint: RegisteredEndpoint,
    config: ModelConfig,
    secret: Secret | None,
    *,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Probe each claimed capability. ``passed`` is true only if every claim held."""
    caps = effective_capabilities(adapter.capabilities(), config.declared_capabilities)
    checks: list[dict[str, Any]] = []

    async def probe(name: str, request: ModelRequest, verify: Any) -> None:
        try:
            response = await adapter.generate(
                transport, endpoint, config, request, secret, timeout_seconds=timeout_seconds
            )
        except _FailureRaised as raised:
            checks.append({"check": name, "passed": False, "detail": raised.failure.code})
            return
        except Exception as error:
            checks.append({"check": name, "passed": False, "detail": type(error).__name__})
            return
        ok, detail = verify(response)
        checks.append({"check": name, "passed": ok, "detail": detail})

    def text_request(prompt: str) -> ModelRequest:
        return ModelRequest(
            messages=(Message(role="user", blocks=(TextBlock(text=prompt),)),),
            max_output_tokens=min(PROBE_OUTPUT_TOKENS, config.max_output_tokens),
            temperature=config.temperature,
            reasoning=config.reasoning,
        )

    def basic(response: Any) -> tuple[bool, str]:
        return bool(response.blocks) and response.finish_reason is not None, "text returned"

    await probe("basic_completion", text_request("Reply with the single word: ok"), basic)

    def usage(response: Any) -> tuple[bool, str]:
        need = {
            UsageCounter.INPUT: response.usage.input_tokens,
            UsageCounter.OUTPUT: response.usage.output_tokens,
        }
        claimed = [counter for counter in need if counter in caps.usage_counters]
        missing = [counter.value for counter in claimed if need[counter] is None]
        return not missing, f"missing counters: {missing}" if missing else "counters present"

    if caps.usage_counters:
        await probe("usage_counters", text_request("Reply with the single word: ok"), usage)

    if caps.native_tools:
        tool_request = ModelRequest(
            messages=(
                Message(
                    role="user",
                    blocks=(
                        TextBlock(text='Call the echo tool with value "ping". Do not answer.'),
                    ),
                ),
            ),
            tools=(_ECHO_TOOL,),
            max_output_tokens=min(PROBE_OUTPUT_TOKENS, config.max_output_tokens),
            temperature=config.temperature,
            reasoning=config.reasoning,
        )

        def tool(response: Any) -> tuple[bool, str]:
            for call in response.tool_calls:
                if call.name == "echo" and call.arguments_valid:
                    try:
                        parsed = json.loads(call.arguments_json)
                    except ValueError:
                        return False, "arguments are not JSON"
                    return "value" in parsed, "tool call returned with parsed arguments"
            return False, "no valid tool call returned"

        await probe("native_tool_call", tool_request, tool)

    return {
        "adapter": adapter.provider_kind.value,
        "passed": bool(checks) and all(item["passed"] for item in checks),
        "checks": checks,
        "evidence": "live probe of the registered endpoint",
    }
