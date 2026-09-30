# ruff: noqa: F811 - pytest fixtures are imported from the PostgreSQL test module
"""Opt-in bounded live adapter smoke checks (Prompt 08).

Each adapter runs only when its environment is configured; otherwise it is reported as
*untested*, never as passed. The call path is the production path: registered endpoint,
live conformance probe, approval, budgeted ``ModelGateway.call`` over the pinned transport,
PostgreSQL ledger and artifact store.

Configuration (per provider ``LOCAL`` | ``OPENAI`` | ``ANTHROPIC`` | ``GOOGLE``):
  PCB_LIVE_<P>_URL          base URL (hosted: https://host[/prefix]; local: http://127.0.0.1:port/v1)
  PCB_LIVE_<P>_MODEL        model name to call
  PCB_LIVE_<P>_SECRET_NAME  hosted only; the key itself must be in PCBSECRET__MODELS__<NAME>
  PCB_LIVE_<P>_PRICE_IN / _PRICE_OUT   hosted only; operator-supplied micro-USD per 1M tokens
  PCB_LIVE_EVIDENCE_DIR     optional; writes a secret-free evidence JSON per provider
EVIDENCE LABEL: LIVE (real provider process/API), bounded to one conformance probe set plus one
single-shot call with a tiny output cap and a hard budget.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from model_gateway_support import make_request, single_shot_protocol
from polycodebench_core.endpoint_policy import EndpointNetworkPolicy, NetworkPolicyKind
from polycodebench_core.model_contracts import (
    Message,
    ModelCapabilities,
    PriceSnapshot,
    ProviderKind,
    TextBlock,
    UsageCounter,
)
from polycodebench_core.model_planning import ModelConfig
from polycodebench_orchestration.gateway.adapters.anthropic import AnthropicAdapter
from polycodebench_orchestration.gateway.adapters.google import GoogleAdapter
from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
from polycodebench_orchestration.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from polycodebench_orchestration.gateway.conformance import run_conformance
from polycodebench_orchestration.gateway.endpoint_check import static_endpoint_check
from polycodebench_orchestration.gateway.secrets import EnvironmentSecretResolver
from polycodebench_orchestration.gateway.service import ModelGateway, RetryPolicy
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.gateway.transport import PinnedHttpTransport
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.model_configs import PostgresModelConfigRepository
from polycodebench_persistence.model_ledger import PostgresModelLedger
from test_model_gateway_postgres import World, artifacts, build_world, database  # noqa: F401

CASES = {
    "LOCAL": (ProviderKind.LOCAL, LocalEndpointAdapter()),
    "OPENAI": (ProviderKind.OPENAI_COMPATIBLE, OpenAICompatibleAdapter()),
    "ANTHROPIC": (ProviderKind.ANTHROPIC, AnthropicAdapter()),
    "GOOGLE": (ProviderKind.GOOGLE, GoogleAdapter()),
}
HARD_BUDGET_MICRO_USD = 50_000  # five cents for the entire smoke; local is attested zero-cost


def _caps(kind: ProviderKind) -> ModelCapabilities:
    """Operator-declared facts for the smoke. Conformance then checks usage and tool claims."""
    counters = frozenset({UsageCounter.INPUT, UsageCounter.OUTPUT})
    if kind is ProviderKind.LOCAL:
        return ModelCapabilities(
            native_tools=True,
            structured_output=False,
            seed=True,
            seed_min=0,
            seed_max=2**31 - 1,
            temperature=True,
            context_limit_tokens=8192,
            max_output_tokens_limit=512,
            usage_counters=counters,
            output_cap_bounds_all_billed_output=True,
            output_limit_parameter="max_tokens",
        )
    return ModelCapabilities(
        native_tools=True,
        structured_output=False,
        seed=kind is not ProviderKind.ANTHROPIC,
        seed_min=0 if kind is not ProviderKind.ANTHROPIC else None,
        seed_max=2**31 - 1 if kind is not ProviderKind.ANTHROPIC else None,
        temperature=kind is not ProviderKind.ANTHROPIC,
        context_limit_tokens=100_000,
        max_output_tokens_limit=512,
        usage_counters=counters,
        # Hosted reasoning models may bill hidden tokens beyond a visible cap; only the operator
        # can attest otherwise, so the smoke declares a non-reasoning configuration explicitly.
        output_cap_bounds_all_billed_output=True,
    )


@pytest.mark.parametrize("name", list(CASES))
def test_live_adapter_smoke(name: str, database, artifacts) -> None:  # type: ignore[no-untyped-def]
    url = os.environ.get(f"PCB_LIVE_{name}_URL")
    model = os.environ.get(f"PCB_LIVE_{name}_MODEL")
    if not url or not model:
        pytest.skip(f"{name}: not configured; adapter is UNTESTED live")
    kind, adapter = CASES[name]
    hosted = kind is not ProviderKind.LOCAL
    secret_name = os.environ.get(f"PCB_LIVE_{name}_SECRET_NAME", "")
    secret_ref = f"secret://models/{secret_name}" if hosted else "none"
    if hosted and not (
        secret_name
        and os.environ.get(f"PCBSECRET__MODELS__{secret_name.upper().replace('-', '_')}")
        and os.environ.get(f"PCB_LIVE_{name}_PRICE_IN")
        and os.environ.get(f"PCB_LIVE_{name}_PRICE_OUT")
    ):
        pytest.skip(f"{name}: credentials or operator prices missing; adapter is UNTESTED live")

    parts = urlsplit(url)
    host = parts.hostname or ""
    policy = (
        EndpointNetworkPolicy(kind=NetworkPolicyKind.PUBLIC_ALLOWLIST, allowed_hosts=(host,))
        if hosted
        else EndpointNetworkPolicy(
            kind=NetworkPolicyKind.INTERNAL_LOCAL, allowed_cidrs=("127.0.0.0/8",)
        )
    )
    world: World = build_world(
        database,
        artifacts,
        campaign_limit=HARD_BUDGET_MICRO_USD,
        attempt_limit=HARD_BUDGET_MICRO_USD,
    )
    endpoints = PostgresEndpointRepository(database.engine)
    endpoint_id = endpoints.register(
        provider_kind=kind,
        base_url=url,
        secret_ref=secret_ref,
        policy=policy,
        declared_capabilities=_caps(kind),
        registered_by="live-smoke-admin",
    )
    secrets = EnvironmentSecretResolver("models")
    endpoint, _ = endpoints.get_for_conformance(endpoint_id)
    static = static_endpoint_check(endpoint, secrets)
    assert static["ok"], static

    price = (
        PriceSnapshot(
            price_id=f"live-{name.lower()}-operator",
            input_micro_usd_per_million_tokens=int(os.environ[f"PCB_LIVE_{name}_PRICE_IN"]),
            output_micro_usd_per_million_tokens=int(os.environ[f"PCB_LIVE_{name}_PRICE_OUT"]),
            basis="contract_price",
            source="operator-supplied for this smoke run",
            effective_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        )
        if hosted
        else PriceSnapshot(
            price_id="live-local-attested-zero",
            input_micro_usd_per_million_tokens=0,
            output_micro_usd_per_million_tokens=0,
            basis="operator_attested_self_hosted",
            source="self-hosted local inference; no per-token money cost (attested)",
            effective_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        )
    )
    config = ModelConfig(
        schema_version=1,
        kind="model_config",
        provider_kind=kind,
        model=model,
        immutable_revision=None,
        endpoint_id=endpoint_id,
        declared_capabilities=_caps(kind),
        price=price,
        temperature="0.000000" if kind is not ProviderKind.ANTHROPIC else None,
        seed_policy="pass_if_supported",
        reasoning=None,
        max_output_tokens=64,
        strict_money_cap=True,
    )
    transport = PinnedHttpTransport()
    conformance = asyncio.run(
        run_conformance(
            adapter, transport, endpoint, config, secrets.resolve(secret_ref), timeout_seconds=300
        )
    )
    endpoints.decide(
        endpoint_id,
        decision="approved" if conformance["passed"] else "rejected",
        actor="live-smoke-admin",
        reason="live smoke conformance",
        expected_version=0,
        conformance_report=conformance,
    )
    assert conformance["passed"], conformance

    config_id, _ = PostgresModelConfigRepository(
        database.engine, artifacts, owner="live-smoke"
    ).register(config)
    gateway = ModelGateway(
        endpoints=endpoints,
        ledger=PostgresModelLedger(database.engine),
        store=ArtifactResponseStore(artifacts, owner="live-smoke"),
        transport=transport,
        secrets=secrets,
        adapters={kind: adapter},
        retry=RetryPolicy(max_deliveries=1, request_timeout_seconds=300),
    )
    request = make_request(
        "Reply with exactly one word: ready",
        temperature=config.temperature,
        messages=(
            Message(role="user", blocks=(TextBlock(text="Reply with exactly one word: ready"),)),
        ),
        max_output_tokens=64,
    )
    result = asyncio.run(
        gateway.call(
            scope=world.scope(),
            logical_call_key="live-smoke-1",
            config=config,
            config_document_id=config_id,
            protocol=single_shot_protocol().model_copy(
                update={"maximum_input_context_tokens": 1024}
            ),
            request=request,
        )
    )
    assert result.response.text.strip(), "live response had no text"
    ledger = PostgresModelLedger(database.engine)
    for account in world.accounts.values():
        assert ledger.verify_balances(account) == []
    summary = ledger.account_summary(world.accounts["campaign"])["money_micro_usd"]
    usage = result.response.usage
    evidence = {
        "label": "LIVE",
        "adapter": kind.value,
        "model": model,
        "endpoint_host": host,
        "date": datetime.now(UTC).isoformat(),
        "conformance": conformance,
        "call": {
            "finish_reason": result.response.finish_reason.value,
            "provider_request_id_present": result.response.provider_request_id is not None,
            "provider_revision": result.response.provider_revision,
            "usage": usage.model_dump(mode="json"),
            "settlement_state": result.settlement_state,
            "response_characters": len(result.response.text),
        },
        "ledger_money_micro_usd": summary,
        "ledger_consistent": True,
        "note": "response text intentionally not recorded",
    }
    out_dir = os.environ.get("PCB_LIVE_EVIDENCE_DIR")
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / f"prompt-08-live-smoke-{name.lower()}.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )
    assert summary["reserved_open"] == 0
    assert summary["spent_confirmed"] + summary["uncertain_committed"] <= HARD_BUDGET_MICRO_USD
