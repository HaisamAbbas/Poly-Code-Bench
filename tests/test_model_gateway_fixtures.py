"""Capability/conformance fixtures: provider-shaped responses and their normalized meaning.

EVIDENCE LABEL: FIXTURE. Files in ``tests/fixtures/model_gateway`` are hand-written from each
provider's documented response schema; they prove the adapters' parsing rules, not that any
provider currently emits these bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polycodebench_core.model_contracts import FinishReason
from polycodebench_orchestration.gateway.adapters.anthropic import AnthropicAdapter
from polycodebench_orchestration.gateway.adapters.base import BaseAdapter
from polycodebench_orchestration.gateway.adapters.google import GoogleAdapter
from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
from polycodebench_orchestration.gateway.adapters.openai_compatible import OpenAICompatibleAdapter

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "model_gateway").glob("*.json"))
ADAPTERS: dict[str, BaseAdapter] = {
    "openai_compatible": OpenAICompatibleAdapter(),
    "local": LocalEndpointAdapter(),
    "anthropic": AnthropicAdapter(),
    "google": GoogleAdapter(),
}


def test_every_adapter_has_at_least_one_fixture() -> None:
    covered = {json.loads(path.read_text(encoding="utf-8"))["adapter"] for path in FIXTURES}
    assert covered == set(ADAPTERS)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_adapter_normalizes_fixture_as_documented(path: Path) -> None:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["provenance"].startswith("FIXTURE")
    adapter = ADAPTERS[fixture["adapter"]]
    response = adapter.parse_response(json.dumps(fixture["body"]).encode(), fixture["headers"])
    expected = fixture["expected"]
    assert response.finish_reason is FinishReason(expected["finish_reason"])
    assert [[c.call_id, c.name, c.arguments_valid] for c in response.tool_calls] == expected[
        "tool_calls"
    ]
    usage = response.usage
    assert usage.input_tokens == expected["input_tokens"]
    assert usage.output_tokens == expected["output_tokens"]
    assert usage.reasoning_tokens == expected["reasoning_tokens"]
    assert usage.cached_input_tokens == expected["cached_input_tokens"]
    assert usage.complete is expected["complete_usage"]
    assert response.provider_request_id == expected["provider_request_id"]
    assert response.provider_revision == expected["provider_revision"]
