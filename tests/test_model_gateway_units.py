"""Prompt 08 unit and local-socket checks: policy, capabilities, adapters, transport, secrets.

Evidence level: unit and local loopback sockets. Provider bodies are documented-shape FIXTURES;
nothing here contacts a real provider.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from uuid import uuid4

import pytest
from model_gateway_support import (
    FULL_CAPS,
    PRICE,
    SECRET_VALUE,
    agent_protocol,
    make_config,
    make_request,
    openai_body,
    single_shot_protocol,
)
from polycodebench_core.endpoint_policy import (
    EndpointNetworkPolicy,
    NetworkPolicyKind,
    RegisteredEndpoint,
    check_resolved_addresses,
    parse_endpoint_url,
    parse_secret_ref,
    required_policy_kind,
)
from polycodebench_core.model_contracts import (
    CapabilityControl,
    CapabilityException,
    CapabilityUnsupported,
    CostBoundUnavailable,
    EndpointPolicyViolation,
    FailureKind,
    FinishReason,
    Message,
    ModelCapabilities,
    ProviderKind,
    ReasoningRequest,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolSpec,
    UsageCounter,
)
from polycodebench_core.model_planning import (
    check_compatibility,
    cost_bound,
    effective_capabilities,
    ensure_compatible,
    map_provider_seed,
)
from polycodebench_orchestration.gateway.adapters.anthropic import AnthropicAdapter
from polycodebench_orchestration.gateway.adapters.google import GoogleAdapter
from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
from polycodebench_orchestration.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from polycodebench_orchestration.gateway.plan import plan_model_run
from polycodebench_orchestration.gateway.secrets import EnvironmentSecretResolver, Secret
from polycodebench_orchestration.gateway.throttle import ProviderThrottle
from polycodebench_orchestration.gateway.transport import PinnedHttpTransport, TransportError

PUBLIC = EndpointNetworkPolicy(
    kind=NetworkPolicyKind.PUBLIC_ALLOWLIST, allowed_hosts=("api.anthropic.com",)
)
INTERNAL = EndpointNetworkPolicy(
    kind=NetworkPolicyKind.INTERNAL_LOCAL, allowed_cidrs=("127.0.0.0/8",)
)


# ----------------------------------------------------------------------- PCB-08-2 policy


@pytest.mark.parametrize(
    "url",
    [
        "http://api.anthropic.com",  # not TLS
        "https://api.anthropic.com:8443",  # port
        "https://evil.example.com",  # not allowlisted
        "https://user:pw@api.anthropic.com",  # embedded credentials
        "https://api.anthropic.com/%2e%2e/admin",  # encoded traversal
        "https://api.anthropic.com/?x=1",  # query
        "https://127.0.0.1",  # IP literal on a public registration
        "https://api.anthropic.com\\@evil.com",  # backslash confusion
        "https://api.anthropic.com/v1 ",  # whitespace
    ],
)
def test_public_registration_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(EndpointPolicyViolation):
        parse_endpoint_url(url, PUBLIC)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "::1",
        "fd00:ec2::254",  # IMDS IPv6
        "fe80::1",
        "::ffff:10.0.0.1",  # v4-mapped private
        "::ffff:169.254.169.254",
        "2002:0a00:0001::1",  # 6to4 wrapping 10.0.0.1
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
        "::7f00:1",  # deprecated IPv4-compatible form of 127.0.0.1
        "fec0::1",  # deprecated site-local
    ],
)
def test_public_policy_rejects_private_and_mapped_addresses(address: str) -> None:
    with pytest.raises(EndpointPolicyViolation):
        check_resolved_addresses([address], PUBLIC)


def test_one_bad_dns_answer_rejects_the_whole_answer_set() -> None:
    assert check_resolved_addresses(["8.8.8.8"], PUBLIC)
    with pytest.raises(EndpointPolicyViolation):
        check_resolved_addresses(["8.8.8.8", "10.0.0.5"], PUBLIC)
    with pytest.raises(EndpointPolicyViolation):
        check_resolved_addresses([], PUBLIC)


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "8.8.8.0/24", "169.254.0.0/16", "10.0.0.0/7"])
def test_internal_policy_cannot_be_widened_to_public_or_metadata_space(cidr: str) -> None:
    with pytest.raises(ValueError):
        EndpointNetworkPolicy(kind=NetworkPolicyKind.INTERNAL_LOCAL, allowed_cidrs=(cidr,))


def test_internal_policy_confines_resolution_to_registered_cidrs() -> None:
    policy = EndpointNetworkPolicy(
        kind=NetworkPolicyKind.INTERNAL_LOCAL, allowed_cidrs=("10.20.0.0/16",)
    )
    assert parse_endpoint_url("http://10.20.3.4:8000/v1", policy).port == 8000
    with pytest.raises(EndpointPolicyViolation):
        parse_endpoint_url("http://10.21.0.1:8000/v1", policy)
    with pytest.raises(EndpointPolicyViolation):
        check_resolved_addresses(["10.21.0.1"], policy)


def test_local_inference_requires_internal_and_hosted_requires_public() -> None:
    assert required_policy_kind(ProviderKind.LOCAL) is NetworkPolicyKind.INTERNAL_LOCAL
    for kind in (ProviderKind.OPENAI_COMPATIBLE, ProviderKind.ANTHROPIC, ProviderKind.GOOGLE):
        assert required_policy_kind(kind) is NetworkPolicyKind.PUBLIC_ALLOWLIST


@pytest.mark.parametrize("ref", ["sk-live-abc", "secret://A/b", "secret://ns/", "env:KEY", ""])
def test_secret_references_are_references_only(ref: str) -> None:
    with pytest.raises(EndpointPolicyViolation):
        parse_secret_ref(ref)
    assert parse_secret_ref("secret://models/openai-primary") == ("models", "openai-primary")


def test_secret_never_appears_in_repr_or_pickles_and_is_scrubbed() -> None:
    secret = Secret(SECRET_VALUE)
    assert SECRET_VALUE not in repr(secret) and SECRET_VALUE not in str(secret)
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(secret)
    cleaned, hit = secret.scrub(f"echo {SECRET_VALUE} end".encode())
    assert hit and SECRET_VALUE.encode() not in cleaned


def test_environment_resolver_honours_only_its_namespace() -> None:
    resolver = EnvironmentSecretResolver(
        "models", {"PCBSECRET__MODELS__OPENAI_PRIMARY": SECRET_VALUE}
    )
    assert resolver.resolve("secret://models/openai-primary").reveal() == SECRET_VALUE  # type: ignore[union-attr]
    assert resolver.resolve("none") is None
    with pytest.raises(EndpointPolicyViolation):
        resolver.resolve("secret://judges/openai-primary")
    with pytest.raises(EndpointPolicyViolation):
        resolver.resolve("secret://models/missing")


# ---------------------------------------------------------------- PCB-08-1 capabilities


def test_unsupported_controls_are_rejected_not_dropped() -> None:
    no_tools = FULL_CAPS.model_copy(update={"native_tools": False})
    config = make_config(uuid4(), capabilities=no_tools)
    report = check_compatibility(config, agent_protocol(), OpenAICompatibleAdapter().capabilities())
    assert [item.control for item in report.unsupported] == [CapabilityControl.TOOLS]
    with pytest.raises(CapabilityUnsupported):
        ensure_compatible(report, config)
    # A single-shot protocol has no tools, so the same model is compatible with it.
    assert check_compatibility(
        config, single_shot_protocol(), OpenAICompatibleAdapter().capabilities()
    ).ok


def test_named_cohort_exception_is_recorded_not_silent() -> None:
    caps = FULL_CAPS.model_copy(update={"temperature": False})
    config = make_config(
        uuid4(),
        capabilities=caps,
        capability_exceptions=(
            CapabilityException(
                control=CapabilityControl.TEMPERATURE,
                reason="model rejects temperature; recorded cohort exception",
                approved_by="admin-1",
            ),
        ),
    )
    report = check_compatibility(config, single_shot_protocol(), AnthropicAdapter().capabilities())
    assert report.exceptions_applied == (CapabilityControl.TEMPERATURE,)
    ensure_compatible(report, config)
    wire = OpenAICompatibleAdapter().build_request(
        config.model_copy(update={"provider_kind": ProviderKind.OPENAI_COMPATIBLE}),
        make_request(),
    )
    assert "temperature:cohort_exception" in wire.dropped_controls
    assert b"temperature" not in wire.body


def test_seed_policy_semantics() -> None:
    no_seed = FULL_CAPS.model_copy(update={"seed": False, "seed_min": None, "seed_max": None})
    wire_caps = OpenAICompatibleAdapter().capabilities()
    passthrough = make_config(uuid4(), capabilities=no_seed, seed_policy="pass_if_supported")
    row = next(
        r
        for r in check_compatibility(passthrough, single_shot_protocol(), wire_caps).requirements
        if r.control is CapabilityControl.SEED
    )
    assert row.status == "recorded_unsupported"
    strict = make_config(uuid4(), capabilities=no_seed, seed_policy="deterministic_mapping")
    with pytest.raises(CapabilityUnsupported):
        ensure_compatible(check_compatibility(strict, single_shot_protocol(), wire_caps), strict)


def test_anthropic_wire_has_no_seed_and_rejects_deterministic_mapping() -> None:
    config = make_config(
        uuid4(), provider=ProviderKind.ANTHROPIC, seed_policy="deterministic_mapping"
    )
    with pytest.raises(CapabilityUnsupported):
        ensure_compatible(
            check_compatibility(config, single_shot_protocol(), AnthropicAdapter().capabilities()),
            config,
        )
    assert (
        map_provider_seed(7, effective_capabilities(AnthropicAdapter().capabilities(), FULL_CAPS))
        is None
    )


def test_reasoning_context_and_usage_claims_are_validated() -> None:
    wire = OpenAICompatibleAdapter().capabilities()
    bad_effort = make_config(uuid4(), reasoning=ReasoningRequest(effort="extreme"))
    assert not check_compatibility(bad_effort, single_shot_protocol(), wire).ok
    tiny_window = make_config(
        uuid4(), capabilities=FULL_CAPS.model_copy(update={"context_limit_tokens": 4_000})
    )
    assert any(
        r.control is CapabilityControl.CONTEXT
        for r in check_compatibility(tiny_window, single_shot_protocol(), wire).unsupported
    )
    no_usage = make_config(
        uuid4(), capabilities=FULL_CAPS.model_copy(update={"usage_counters": frozenset()})
    )
    assert any(
        r.control is CapabilityControl.USAGE
        for r in check_compatibility(no_usage, single_shot_protocol(), wire).unsupported
    )


def test_seed_mapping_is_deterministic_and_in_range() -> None:
    caps = effective_capabilities(OpenAICompatibleAdapter().capabilities(), FULL_CAPS)
    assert caps.seed_min == 0 and caps.seed_max == 2**31 - 1
    first = map_provider_seed(2**64 - 1, caps)
    assert first == map_provider_seed(2**64 - 1, caps)
    assert first is not None and 0 <= first <= 2**31 - 1
    with pytest.raises(ValueError):
        map_provider_seed(2**64, caps)
    with pytest.raises(ValueError):
        ModelCapabilities(
            native_tools=False, structured_output=False, seed=True, temperature=False
        )  # seed support without a declared range


# ---------------------------------------------------------------- PCB-08-4 cost bounds


def test_cost_bound_is_a_rounded_up_integer_upper_bound() -> None:
    config = make_config(uuid4(), max_output_tokens=1000)
    caps = effective_capabilities(OpenAICompatibleAdapter().capabilities(), FULL_CAPS)
    bound = cost_bound(config, caps, request_bytes=1000)
    assert bound.kind == "provider_bound" and bound.strict_cap_eligible
    input_tokens = 1000 + 256  # one token per byte is the provable bound, plus fixed overhead
    assert bound.input_tokens_bound == input_tokens
    expected = -(-input_tokens * 3_000_000 // 1_000_000) + -(-1000 * 15_000_000 // 1_000_000)
    assert bound.max_cost_micro_usd == expected


def test_no_enforceable_bound_blocks_strict_cap_claims() -> None:
    wire = OpenAICompatibleAdapter().capabilities()
    no_price = make_config(uuid4(), price=None)
    report = check_compatibility(no_price, single_shot_protocol(), wire)
    assert report.cost_bound_kind == "none" and not report.strict_cap_eligible
    with pytest.raises(CostBoundUnavailable):
        ensure_compatible(report, no_price)
    unbounded_reasoning = make_config(
        uuid4(),
        capabilities=FULL_CAPS.model_copy(update={"output_cap_bounds_all_billed_output": False}),
    )
    with pytest.raises(CostBoundUnavailable):
        ensure_compatible(
            check_compatibility(unbounded_reasoning, single_shot_protocol(), wire),
            unbounded_reasoning,
        )


def test_operator_conservative_reserve_is_labeled_and_never_strict() -> None:
    with pytest.raises(ValueError):
        make_config(
            uuid4(),
            price=None,
            cost_policy="operator_conservative",
            conservative_call_reserve_micro_usd=5000,
        )  # strict cap still true
    config = make_config(
        uuid4(),
        price=None,
        cost_policy="operator_conservative",
        conservative_call_reserve_micro_usd=5000,
        strict_money_cap=False,
    )
    bound = cost_bound(
        config,
        effective_capabilities(OpenAICompatibleAdapter().capabilities(), FULL_CAPS),
        request_bytes=10,
    )
    assert bound.kind == "operator_conservative" and bound.max_cost_micro_usd == 5000
    assert not bound.strict_cap_eligible


def test_run_plan_output_is_labeled_estimate_with_blockers() -> None:
    adapter = OpenAICompatibleAdapter()
    plan = plan_model_run(
        config=make_config(uuid4()),
        protocol=agent_protocol(),
        adapter=adapter,
        tasks=10,
        samples_per_task=3,
        max_request_bytes=2000,
    )
    assert plan.label.startswith("ESTIMATE") and plan.compatible
    assert plan.planned_model_calls == 10 * 3 * 30
    assert (
        plan.worst_case_money_micro_usd
        == plan.planned_model_calls * plan.per_call.max_cost_micro_usd
    )  # type: ignore[operator]
    blocked = plan_model_run(
        config=make_config(uuid4(), price=None),
        protocol=agent_protocol(),
        adapter=adapter,
        tasks=1,
        samples_per_task=1,
    )
    assert not blocked.compatible and blocked.worst_case_money_micro_usd is None
    assert any("cost bound" in item for item in blocked.blockers)


# -------------------------------------------------------------------- adapter wire shapes


def _tool() -> ToolSpec:
    return ToolSpec(
        name="read_file",
        description="Read a file",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )


def test_openai_request_and_tool_roundtrip_preserves_arguments_verbatim() -> None:
    adapter = OpenAICompatibleAdapter()
    config = make_config(
        uuid4(),
        provider=ProviderKind.OPENAI_COMPATIBLE,
        reasoning=ReasoningRequest(effort="low"),
    )
    request = make_request(tools=(_tool(),), seed=42, reasoning=ReasoningRequest(effort="low"))
    wire = adapter.build_request(config, request)
    body = json.loads(wire.body)
    assert wire.path == "/chat/completions"
    assert body["max_completion_tokens"] == 1000 and "max_tokens" not in body
    assert body["seed"] == 42 and body["temperature"] == 0.0 and body["reasoning_effort"] == "low"
    assert body["tools"][0]["function"]["name"] == "read_file"
    headers = adapter.authenticate(dict(wire.headers), Secret(SECRET_VALUE))
    assert headers["authorization"] == f"Bearer {SECRET_VALUE}"
    assert SECRET_VALUE.encode() not in wire.body and SECRET_VALUE not in str(wire.headers)

    raw = openai_body(
        None,  # type: ignore[arg-type]
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{not json"},
            },
        ],
        usage={
            "prompt_tokens": 50,
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 25},
            "prompt_tokens_details": {"cached_tokens": 10},
        },
    )
    response = adapter.parse_response(raw, {"x-request-id": "req-9"})
    assert [c.call_id for c in response.tool_calls] == ["call_1", "call_2"]  # order preserved
    assert response.tool_calls[0].arguments_valid and not response.tool_calls[1].arguments_valid
    assert response.tool_calls[1].arguments_json == "{not json"  # kept, not repaired
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.provider_request_id == "req-9"
    assert response.provider_revision == "fixture-model|fp_fixture"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (50, 40)
    assert (response.usage.reasoning_tokens, response.usage.cached_input_tokens) == (25, 10)

    follow_up = make_request(
        reasoning=ReasoningRequest(effort="low"),
        messages=(
            Message(role="user", blocks=(TextBlock(text="go"),)),
            Message(role="assistant", blocks=response.tool_calls[:1]),
            Message(
                role="user",
                blocks=(ToolResultBlock(call_id="call_1", name="read_file", content="x"),),
            ),
        ),
    )
    messages = json.loads(adapter.build_request(config, follow_up).body)["messages"]
    assert messages[2]["tool_calls"][0]["id"] == "call_1"
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": "x"}


def test_openai_missing_usage_is_unknown_not_zero() -> None:
    adapter = OpenAICompatibleAdapter()
    response = adapter.parse_response(openai_body("hi", usage=None), {})
    assert response.usage.input_tokens is None and response.usage.output_tokens is None
    assert not response.usage.complete and response.usage.availability()["input_tokens"] is False
    partial = adapter.parse_response(openai_body("hi", usage={"prompt_tokens": 5}), {})
    assert partial.usage.input_tokens == 5 and partial.usage.output_tokens is None
    junk = adapter.parse_response(
        openai_body("hi", usage={"prompt_tokens": -3, "completion_tokens": True}), {}
    )
    assert junk.usage.input_tokens is None and junk.usage.output_tokens is None


def test_openai_requires_exactly_one_choice() -> None:
    from polycodebench_orchestration.gateway.adapters.base import MalformedResponse

    adapter = OpenAICompatibleAdapter()
    two = json.dumps({"choices": [{"message": {"content": "a"}}, {"message": {"content": "b"}}]})
    with pytest.raises(MalformedResponse):
        adapter.parse_response(two.encode(), {})
    with pytest.raises(MalformedResponse):
        adapter.parse_response(b"<html>gateway error</html>", {})


def test_local_adapter_is_distinct_and_authentication_optional() -> None:
    adapter = LocalEndpointAdapter()
    assert adapter.provider_kind is ProviderKind.LOCAL
    assert adapter.authenticate({"a": "b"}, None) == {"a": "b"}
    config = make_config(uuid4(), provider=ProviderKind.OPENAI_COMPATIBLE)
    with pytest.raises(CapabilityUnsupported):
        adapter.validate(config, single_shot_protocol())  # wrong adapter for the configuration


def test_output_limit_parameter_is_declared_by_the_endpoint_not_inferred() -> None:
    caps = FULL_CAPS.model_copy(update={"output_limit_parameter": "max_tokens"})
    wire = LocalEndpointAdapter().build_request(
        make_config(uuid4(), capabilities=caps), make_request()
    )
    body = json.loads(wire.body)
    assert body["max_tokens"] == 1000 and "max_completion_tokens" not in body


def test_anthropic_request_response_and_usage_semantics() -> None:
    adapter = AnthropicAdapter()
    config = make_config(
        uuid4(),
        provider=ProviderKind.ANTHROPIC,
        capabilities=FULL_CAPS.model_copy(
            update={
                "reasoning_efforts": (),
                "reasoning_budget_tokens": True,
                "seed": False,
                "seed_min": None,
                "seed_max": None,
            }
        ),
        seed_policy="omit",
        temperature=None,
        reasoning=ReasoningRequest(budget_tokens=512),
    )
    request = make_request(
        tools=(_tool(),),
        temperature=None,
        reasoning=ReasoningRequest(budget_tokens=512),
    )
    wire = adapter.build_request(config, request)
    body = json.loads(wire.body)
    assert wire.path == "/v1/messages" and wire.headers["anthropic-version"] == "2023-06-01"
    assert body["max_tokens"] == 1000 and body["system"] == "You are a careful engineer."
    assert body["tools"][0]["input_schema"]["type"] == "object"
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 512}
    assert "seed" not in body
    assert adapter.authenticate({}, Secret(SECRET_VALUE))["x-api-key"] == SECRET_VALUE
    with pytest.raises(CapabilityUnsupported):
        adapter.authenticate({}, None)
    tight = config.model_copy(update={"reasoning": ReasoningRequest(budget_tokens=1000)})
    with pytest.raises(CapabilityUnsupported):  # thinking budget must leave room for the answer
        adapter.build_request(
            tight, make_request(temperature=None, reasoning=ReasoningRequest(budget_tokens=1000))
        )

    raw = json.dumps(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "fixture-model",
            "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "sig-abc"},
                {"type": "text", "text": "reading"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_file",
                    "input": {"path": "a.py"},
                },
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 77,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 100,
            },
        }
    ).encode()
    response = adapter.parse_response(raw, {"request-id": "req_anthropic_1"})
    assert response.provider_request_id == "req_anthropic_1"
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.tool_calls[0].arguments_json == '{"path":"a.py"}'
    # billed input is the sum of uncached, cache-creation and cache-read segments
    assert response.usage.input_tokens == 115 and response.usage.cached_input_tokens == 100
    assert response.usage.output_tokens == 77 and response.usage.reasoning_tokens is None
    # thinking blocks survive only through the verbatim provider payload, and are replayed
    assert response.provider_payload is not None
    replay = make_request(
        temperature=None,
        reasoning=ReasoningRequest(budget_tokens=512),
        messages=(
            Message(role="user", blocks=(TextBlock(text="go"),)),
            Message(
                role="assistant",
                blocks=(TextBlock(text="reading"),),
                provider_payload=response.provider_payload,
                provider_payload_origin=ProviderKind.ANTHROPIC,
            ),
        ),
    )
    sent = json.loads(adapter.build_request(config, replay).body)["messages"][1]
    assert sent["content"][0]["signature"] == "sig-abc"


def test_anthropic_rejects_temperature_when_model_does_not_accept_it() -> None:
    config = make_config(
        uuid4(),
        provider=ProviderKind.ANTHROPIC,
        capabilities=FULL_CAPS.model_copy(
            update={"temperature": False, "seed": False, "seed_min": None, "seed_max": None}
        ),
        seed_policy="omit",
    )
    with pytest.raises(CapabilityUnsupported):
        AnthropicAdapter().build_request(config, make_request())


def test_google_request_response_and_usage_semantics() -> None:
    adapter = GoogleAdapter()
    config = make_config(uuid4(), provider=ProviderKind.GOOGLE, model="gemini-fixture-1")
    wire = adapter.build_request(config, make_request(tools=(_tool(),), seed=99))
    body = json.loads(wire.body)
    assert wire.path == "/v1beta/models/gemini-fixture-1:generateContent"
    assert (
        body["generationConfig"]["maxOutputTokens"] == 1000
        and body["generationConfig"]["seed"] == 99
    )
    assert body["systemInstruction"]["parts"][0]["text"] == "You are a careful engineer."
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "read_file"
    assert adapter.authenticate({}, Secret(SECRET_VALUE))["x-goog-api-key"] == SECRET_VALUE
    with pytest.raises(CapabilityUnsupported):
        adapter.build_request(
            make_config(uuid4(), provider=ProviderKind.GOOGLE, model="../x"), make_request()
        )

    raw = json.dumps(
        {
            "responseId": "resp-1",
            "modelVersion": "gemini-fixture-1-001",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "thinking...", "thought": True},
                            {
                                "functionCall": {
                                    "name": "read_file",
                                    "args": {"path": "a.py", "limit": 0.5},
                                }
                            },
                            {"functionCall": {"name": "read_file", "args": {"path": "b.py"}}},
                        ],
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 100,
                "toolUsePromptTokenCount": 7,
                "candidatesTokenCount": 20,
                "thoughtsTokenCount": 60,
                "cachedContentTokenCount": 40,
                "totalTokenCount": 187,
            },
        }
    ).encode()
    response = adapter.parse_response(raw, {})
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert [c.call_id for c in response.tool_calls] == ["call_1", "call_2"]  # synthesized, ordered
    assert response.text == ""  # thought parts are not answer text
    # thought tokens are billed as output and reported separately from candidates
    assert response.usage.input_tokens == 107 and response.usage.output_tokens == 80
    assert response.usage.reasoning_tokens == 60 and response.usage.cached_input_tokens == 40
    assert response.provider_revision == "gemini-fixture-1-001"


def test_google_does_not_implement_unverified_controls() -> None:
    wire = GoogleAdapter().capabilities()
    assert (
        not wire.structured_output
        and not wire.reasoning_efforts
        and not wire.reasoning_budget_tokens
    )
    config = make_config(
        uuid4(), provider=ProviderKind.GOOGLE, reasoning=ReasoningRequest(effort="low")
    )
    with pytest.raises(CapabilityUnsupported):
        GoogleAdapter().build_request(
            config, make_request(reasoning=ReasoningRequest(effort="low"))
        )


def test_http_failure_classification_keeps_ambiguity() -> None:
    adapter = OpenAICompatibleAdapter()
    rate = adapter.classify_http(429, {"retry-after": "7", "x-request-id": "r1"}, b"slow down")
    assert rate.kind is FailureKind.RATE_LIMITED and rate.retry_after_seconds == 7
    assert adapter.classify_http(400, {}, b"bad").kind is FailureKind.REJECTED
    assert adapter.classify_http(401, {}, b"").code == "auth_failed"
    assert adapter.classify_http(302, {}, b"").code == "unexpected_redirect"
    for status in (500, 502, 503, 504, 529, 408):
        assert adapter.classify_http(status, {}, b"").kind is FailureKind.AMBIGUOUS
    assert adapter.classify_transport_error(TransportError("connect", "connect_failed")).kind is (
        FailureKind.NOT_DELIVERED
    )
    after_send = adapter.classify_transport_error(TransportError("receive", "x", timed_out=True))
    assert after_send.kind is FailureKind.AMBIGUOUS and after_send.code == "timeout_after_send"
    policy = adapter.classify_transport_error(EndpointPolicyViolation("blocked"))
    assert policy.kind is FailureKind.REJECTED and not policy.retryable


# ------------------------------------------------------------------------ throttling


def test_throttle_bounds_concurrency_and_applies_rate_limit_cooldown() -> None:
    async def scenario() -> tuple[int, list[float]]:
        now = [0.0]
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)
            now[0] += seconds

        throttle = ProviderThrottle(max_concurrency=2, clock=lambda: now[0], sleep=fake_sleep)

        async def worker() -> None:
            async with throttle.slot():
                await asyncio.sleep(0.01)

        await asyncio.gather(*(worker() for _ in range(8)))
        throttle.note_rate_limited(5.0)
        async with throttle.slot():
            pass
        return throttle.peak_in_flight, slept

    peak, slept = asyncio.run(scenario())
    assert peak == 2
    assert slept == [5.0]  # the cooldown delayed the next admission by Retry-After


# ---------------------------------------------- pinned transport against local sockets


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        if self.path.endswith("/redirect"):
            self.send_response(302)
            self.send_header("location", "http://169.254.169.254/latest")
            self.send_header("content-length", "0")
            self.end_headers()
        elif self.path.endswith("/drip"):
            self.send_response(200)
            self.send_header("content-length", "100000")
            self.end_headers()
            for _ in range(40):  # one byte at a time: each read succeeds, the whole never ends
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except OSError:
                    return
                threading.Event().wait(0.2)
        elif self.path.endswith("/stall"):
            threading.Event().wait(2.0)
        elif self.path.endswith("/big"):
            payload = b"x" * 5000
            self.send_response(200)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("content-length", str(len(payload)))
            self.send_header("x-request-id", "local-1")
            self.end_headers()
            self.wfile.write(payload)


@pytest.fixture(scope="module")
def local_server() -> tuple[str, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "127.0.0.1", server.server_address[1]
    server.shutdown()


def _endpoint(
    port: int, policy: EndpointNetworkPolicy = INTERNAL, host: str = "127.0.0.1"
) -> RegisteredEndpoint:
    return RegisteredEndpoint(
        endpoint_id=uuid4(),
        provider_kind=ProviderKind.LOCAL,
        endpoint=parse_endpoint_url(f"http://{host}:{port}/v1", policy),
        secret_ref="none",
        policy=policy,
        declared_capabilities=FULL_CAPS,
    )


def test_pinned_transport_roundtrip_redirect_and_size_limits(local_server: tuple[str, int]) -> None:
    _, port = local_server
    endpoint = _endpoint(port)
    transport = PinnedHttpTransport()

    async def run() -> None:
        ok = await transport.send(
            endpoint, "/chat", {"content-type": "application/json"}, b"{}", timeout_seconds=3
        )
        assert (
            ok.status == 200
            and ok.headers["x-request-id"] == "local-1"
            and ok.body == b'{"ok":true}'
        )
        redirect = await transport.send(endpoint, "/redirect", {}, b"{}", timeout_seconds=3)
        assert redirect.status == 302  # returned to the caller, never followed
        small = PinnedHttpTransport(max_response_bytes=1000)
        with pytest.raises(TransportError) as oversize:
            await small.send(endpoint, "/big", {}, b"{}", timeout_seconds=3)
        assert oversize.value.code == "response_too_large"

    asyncio.run(run())


def test_pinned_transport_distinguishes_unsent_from_possibly_sent(
    local_server: tuple[str, int],
) -> None:
    _, port = local_server
    transport = PinnedHttpTransport()

    async def run() -> None:
        with pytest.raises(TransportError) as stalled:
            await transport.send(_endpoint(port), "/stall", {}, b"{}", timeout_seconds=0.3)
        assert stalled.value.request_may_have_been_sent and stalled.value.timed_out
        closed = _endpoint(1)  # nothing listens on port 1
        with pytest.raises(TransportError) as refused:
            await transport.send(closed, "/x", {}, b"{}", timeout_seconds=1)
        assert not refused.value.request_may_have_been_sent

    asyncio.run(run())


def test_pinned_transport_validates_every_resolution_before_connecting(
    local_server: tuple[str, int],
) -> None:
    """DNS rebinding: a public-policy endpoint whose name resolves to loopback is refused."""
    _, port = local_server
    transport = PinnedHttpTransport(resolver=lambda host, port_: ["127.0.0.1"])
    public = RegisteredEndpoint(
        endpoint_id=uuid4(),
        provider_kind=ProviderKind.ANTHROPIC,
        endpoint=parse_endpoint_url("https://api.anthropic.com", PUBLIC),
        secret_ref="secret://models/a",
        policy=PUBLIC,
        declared_capabilities=FULL_CAPS,
    )

    async def run() -> None:
        with pytest.raises(EndpointPolicyViolation):
            await transport.send(public, "/v1/messages", {}, b"{}", timeout_seconds=1)

    with patch("socket.create_connection") as dial:
        asyncio.run(run())
    dial.assert_not_called()  # the resolver answer was rejected before any socket was opened

    internal_only = PinnedHttpTransport(resolver=lambda host, p: ["10.9.9.9"])
    with pytest.raises(EndpointPolicyViolation):
        asyncio.run(
            internal_only.send(
                _endpoint(port, host="localhost"), "/x", {}, b"{}", timeout_seconds=1
            )
        )


def test_price_snapshot_constants_are_fixture_only() -> None:
    assert "not a real price" in PRICE.source
    assert UsageCounter.INPUT in FULL_CAPS.usage_counters
    assert ToolCallBlock  # imported for type coverage of the block union


# ---------------------------------------------------- independent-review regressions (units)


def test_secret_reference_names_cannot_collide_in_the_environment() -> None:
    for ref in ("secret://models/Prod-Key", "secret://models/prod_key"):
        with pytest.raises(EndpointPolicyViolation):
            parse_secret_ref(ref)


def test_secret_value_is_trimmed_and_header_unsafe_values_are_refused() -> None:
    assert Secret(SECRET_VALUE + "\n").reveal() == SECRET_VALUE
    for bad in (SECRET_VALUE + "\nX-Injected: 1", SECRET_VALUE[:10] + " " + SECRET_VALUE[10:]):
        with pytest.raises(ValueError) as error:
            Secret(bad)
        assert SECRET_VALUE[:10] not in str(error.value)  # the message never echoes the value


def test_anthropic_puts_tool_results_before_other_blocks() -> None:
    config = make_config(
        uuid4(),
        provider=ProviderKind.ANTHROPIC,
        capabilities=FULL_CAPS.model_copy(
            update={"seed": False, "seed_min": None, "seed_max": None}
        ),
        seed_policy="omit",
        temperature=None,
    )
    request = make_request(
        temperature=None,
        messages=(
            Message(
                role="user",
                blocks=(
                    TextBlock(text="continue"),
                    ToolResultBlock(call_id="toolu_1", name="read_file", content="data"),
                ),
            ),
        ),
    )
    content = json.loads(AnthropicAdapter().build_request(config, request).body)["messages"][0][
        "content"
    ]
    assert [block["type"] for block in content] == ["tool_result", "text"]


def test_tool_use_reserves_extra_input_allowance_and_smaller_caps_reserve_less() -> None:
    config = make_config(uuid4(), max_output_tokens=1000)
    caps = effective_capabilities(OpenAICompatibleAdapter().capabilities(), FULL_CAPS)
    plain = cost_bound(config, caps, request_bytes=100)
    with_tools = cost_bound(config, caps, request_bytes=100, has_tools=True)
    assert with_tools.input_tokens_bound == plain.input_tokens_bound + 1024
    assert with_tools.max_cost_micro_usd > plain.max_cost_micro_usd  # type: ignore[operator]
    small = cost_bound(config, caps, request_bytes=100, output_tokens=100)
    assert small.output_tokens_bound == 100 and small.max_cost_micro_usd < plain.max_cost_micro_usd  # type: ignore[operator]


def test_request_must_match_the_resolved_config() -> None:
    adapter = LocalEndpointAdapter()
    config = make_config(uuid4())
    for deviation in (
        {"max_output_tokens": 5000},
        {"temperature": "0.500000"},
        {"reasoning": ReasoningRequest(effort="low")},
    ):
        with pytest.raises(CapabilityUnsupported):
            adapter.build_request(config, make_request(**deviation))


def test_transport_enforces_an_overall_deadline_against_slow_drip(
    local_server: tuple[str, int],
) -> None:
    _, port = local_server
    import time

    async def run() -> float:
        started = time.monotonic()
        with pytest.raises(TransportError) as stalled:
            await PinnedHttpTransport().send(
                _endpoint(port), "/drip", {}, b"{}", timeout_seconds=1.0
            )
        assert stalled.value.timed_out and stalled.value.request_may_have_been_sent
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 4.0  # the drip would have lasted 8 s; every individual read succeeded


def test_oversized_response_is_ambiguous_but_not_retried(local_server: tuple[str, int]) -> None:
    _, port = local_server
    adapter = LocalEndpointAdapter()

    async def run() -> TransportError:
        with pytest.raises(TransportError) as oversize:
            await PinnedHttpTransport(max_response_bytes=1000).send(
                _endpoint(port), "/big", {}, b"{}", timeout_seconds=3
            )
        return oversize.value

    error = asyncio.run(run())
    failure = adapter.classify_transport_error(error)
    assert failure.kind is FailureKind.AMBIGUOUS and failure.retryable is False


def test_header_injection_is_rejected_locally_before_any_send(
    local_server: tuple[str, int],
) -> None:
    _, port = local_server

    async def run() -> TransportError:
        with pytest.raises(TransportError) as bad:
            await PinnedHttpTransport().send(
                _endpoint(port), "/chat", {"authorization": "Bearer a\nb"}, b"{}", timeout_seconds=3
            )
        return bad.value

    error = asyncio.run(run())
    assert error.phase == "build" and not error.request_may_have_been_sent
    assert LocalEndpointAdapter().classify_transport_error(error).kind is FailureKind.REJECTED


# ------------------------------------------------------------------- conformance (fixture)


def _conformance_endpoint() -> RegisteredEndpoint:
    return _endpoint(18080)


def test_conformance_passes_only_when_every_claim_holds() -> None:
    from model_gateway_support import ScriptedTransport, ok
    from polycodebench_orchestration.gateway.conformance import run_conformance

    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "echo", "arguments": '{"value": "ping"}'},
    }
    good = ScriptedTransport(
        [ok("ok"), ok("ok"), ok("", tool_calls=[tool_call])],
    )
    config = make_config(uuid4())
    report = asyncio.run(
        run_conformance(LocalEndpointAdapter(), good, _conformance_endpoint(), config, None)
    )
    assert report["passed"] and [c["check"] for c in report["checks"]] == [
        "basic_completion",
        "usage_counters",
        "native_tool_call",
    ]

    no_usage = ScriptedTransport(
        [ok("ok", usage=None), ok("ok", usage=None), ok("", tool_calls=[tool_call], usage=None)]
    )
    failed = asyncio.run(
        run_conformance(LocalEndpointAdapter(), no_usage, _conformance_endpoint(), config, None)
    )
    assert not failed["passed"]
    assert {c["check"]: c["passed"] for c in failed["checks"]}["usage_counters"] is False

    no_tool = ScriptedTransport([ok("ok"), ok("ok"), ok("I will not call a tool")])
    skipped = asyncio.run(
        run_conformance(LocalEndpointAdapter(), no_tool, _conformance_endpoint(), config, None)
    )
    assert not skipped["passed"]
    assert {c["check"]: c["passed"] for c in skipped["checks"]}["native_tool_call"] is False


def test_openai_reasoning_budget_is_opt_in_nested_object() -> None:
    adapter = OpenAICompatibleAdapter()
    budget = ReasoningRequest(budget_tokens=2000)
    declared = FULL_CAPS.model_copy(update={"reasoning_budget_tokens": True})
    config = make_config(
        uuid4(), provider=ProviderKind.OPENAI_COMPATIBLE, capabilities=declared, reasoning=budget
    )
    body = json.loads(adapter.build_request(config, make_request(reasoning=budget)).body)
    assert body["reasoning"] == {"max_tokens": 2000} and "reasoning_effort" not in body
    # Not declared by the operator: refused, never silently dropped.
    undeclared = make_config(
        uuid4(),
        provider=ProviderKind.OPENAI_COMPATIBLE,
        capabilities=FULL_CAPS.model_copy(update={"reasoning_budget_tokens": False}),
        reasoning=budget,
    )
    with pytest.raises(CapabilityUnsupported):
        adapter.build_request(undeclared, make_request(reasoning=budget))
    # Default (no reasoning control): nothing is sent.
    plain = make_config(uuid4(), provider=ProviderKind.OPENAI_COMPATIBLE)
    plain_body = json.loads(adapter.build_request(plain, make_request()).body)
    assert "reasoning" not in plain_body and "reasoning_effort" not in plain_body
