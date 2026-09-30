"""Model configuration, capability validation and conservative cost bounds (pure logic)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from polycodebench_core.model_contracts import (
    CapabilityControl,
    CapabilityException,
    CapabilityUnsupported,
    CostBoundUnavailable,
    ModelCapabilities,
    PriceSnapshot,
    ProviderKind,
    ReasoningRequest,
    Strict,
    UsageCounter,
)
from polycodebench_core.models import ContractModel, Decimal6, ProtocolDefinition

# Conservative allowances, not provider-documented figures: chat framing and special tokens, and
# the hidden instructions some providers prepend when tools are supplied. Plan output shows
# both as part of the labeled estimate.
REQUEST_TOKEN_OVERHEAD = 256
TOOL_PROMPT_OVERHEAD_TOKENS = 1024


class ModelConfig(ContractModel):
    """Fully resolved model configuration. Every field that changes behavior is explicit."""

    kind: Literal["model_config"]
    provider_kind: ProviderKind
    model: Annotated[str, Field(min_length=1, max_length=255)]
    immutable_revision: Annotated[str, Field(min_length=1, max_length=255)] | None
    endpoint_id: UUID
    declared_capabilities: ModelCapabilities
    price: PriceSnapshot | None
    temperature: Decimal6 | None
    seed_policy: Literal["pass_if_supported", "omit", "deterministic_mapping"]
    reasoning: ReasoningRequest | None
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)]
    capability_exceptions: tuple[CapabilityException, ...] = ()
    cost_policy: Literal["provider_bound", "operator_conservative"] = "provider_bound"
    conservative_call_reserve_micro_usd: Annotated[int, Field(strict=True, ge=1)] | None = None
    strict_money_cap: bool = True

    @model_validator(mode="after")
    def coherent(self) -> ModelConfig:
        controls = [item.control for item in self.capability_exceptions]
        if len(controls) != len(set(controls)):
            raise ValueError("capability exceptions must name each control at most once")
        if (self.cost_policy == "operator_conservative") != (
            self.conservative_call_reserve_micro_usd is not None
        ):
            raise ValueError("conservative reserve is required exactly for that cost policy")
        if self.cost_policy == "operator_conservative" and self.strict_money_cap:
            raise ValueError("an operator conservative reserve cannot support a strict money cap")
        return self

    def excepted(self, control: CapabilityControl) -> bool:
        return any(item.control == control for item in self.capability_exceptions)


class RequirementStatus(Strict):
    control: CapabilityControl
    requirement: str
    status: Literal["supported", "unsupported", "excepted", "recorded_unsupported"]
    detail: str


class CostBound(Strict):
    kind: Literal["provider_bound", "operator_conservative", "none"]
    max_cost_micro_usd: int | None
    input_tokens_bound: int
    output_tokens_bound: int
    strict_cap_eligible: bool
    reason: str


class CompatibilityReport(Strict):
    requirements: tuple[RequirementStatus, ...]
    exceptions_applied: tuple[CapabilityControl, ...]
    cost_bound_kind: Literal["provider_bound", "operator_conservative", "none"]
    strict_cap_eligible: bool

    @property
    def unsupported(self) -> tuple[RequirementStatus, ...]:
        return tuple(item for item in self.requirements if item.status == "unsupported")

    @property
    def ok(self) -> bool:
        return not self.unsupported


def effective_capabilities(
    wire: ModelCapabilities, declared: ModelCapabilities
) -> ModelCapabilities:
    """Conjunction of adapter wire ceiling and operator-declared model facts."""
    seed = wire.seed and declared.seed
    seed_min = seed_max = None
    if seed:
        assert declared.seed_min is not None and declared.seed_max is not None
        seed_min, seed_max = declared.seed_min, declared.seed_max
        if wire.seed_min is not None and wire.seed_max is not None:
            seed_min, seed_max = max(seed_min, wire.seed_min), min(seed_max, wire.seed_max)
            if seed_min > seed_max:
                seed = False
                seed_min = seed_max = None
    limits = [v for v in (wire.max_output_tokens_limit, declared.max_output_tokens_limit) if v]
    return ModelCapabilities(
        native_tools=wire.native_tools and declared.native_tools,
        structured_output=wire.structured_output and declared.structured_output,
        seed=seed,
        seed_min=seed_min,
        seed_max=seed_max,
        temperature=wire.temperature and declared.temperature,
        reasoning_efforts=tuple(
            e for e in declared.reasoning_efforts if e in wire.reasoning_efforts
        ),
        reasoning_budget_tokens=wire.reasoning_budget_tokens and declared.reasoning_budget_tokens,
        streaming=wire.streaming and declared.streaming,
        context_limit_tokens=declared.context_limit_tokens,
        max_output_tokens_limit=min(limits) if limits else None,
        usage_counters=wire.usage_counters & declared.usage_counters,
        output_cap_bounds_all_billed_output=declared.output_cap_bounds_all_billed_output,
        output_limit_parameter=declared.output_limit_parameter,
    )


def check_compatibility(
    config: ModelConfig,
    protocol: ProtocolDefinition,
    wire: ModelCapabilities,
    *,
    requires_structured_output: bool = False,
) -> CompatibilityReport:
    """Evaluate every requested control. Unsupported controls are rejected unless a named
    cohort exception covers them; nothing is silently dropped."""
    caps = effective_capabilities(wire, config.declared_capabilities)
    rows: list[RequirementStatus] = []
    applied: list[CapabilityControl] = []

    def record(control: CapabilityControl, requirement: str, ok: bool, detail: str) -> None:
        if ok:
            rows.append(
                RequirementStatus(
                    control=control, requirement=requirement, status="supported", detail=detail
                )
            )
        elif config.excepted(control):
            applied.append(control)
            rows.append(
                RequirementStatus(
                    control=control, requirement=requirement, status="excepted", detail=detail
                )
            )
        else:
            rows.append(
                RequirementStatus(
                    control=control, requirement=requirement, status="unsupported", detail=detail
                )
            )

    if protocol.mode == "standard_agent" and protocol.allowed_tools:
        record(
            CapabilityControl.TOOLS,
            "native tool calling for the standard agent cohort",
            caps.native_tools,
            "native tools supported"
            if caps.native_tools
            else "no native tools; single-shot or a separate text-tool protocol is required",
        )
    if requires_structured_output:
        record(
            CapabilityControl.STRUCTURED_OUTPUT,
            "schema-constrained output",
            caps.structured_output,
            "structured output " + ("supported" if caps.structured_output else "not supported"),
        )
    if config.temperature is not None:
        record(
            CapabilityControl.TEMPERATURE,
            f"temperature {config.temperature}",
            caps.temperature,
            "temperature "
            + ("supported" if caps.temperature else "not supported by this model/adapter"),
        )
    if config.seed_policy == "deterministic_mapping":
        record(
            CapabilityControl.SEED,
            "deterministic provider seed",
            caps.seed,
            "seed supported" if caps.seed else "provider seed not supported",
        )
    elif config.seed_policy == "pass_if_supported":
        # Spec 4.2: unsupported seeds are recorded as unsupported, not a rejection.
        rows.append(
            RequirementStatus(
                control=CapabilityControl.SEED,
                requirement="seed passed if supported",
                status="supported" if caps.seed else "recorded_unsupported",
                detail="seed will be sent" if caps.seed else "seed unsupported; recorded, not sent",
            )
        )
    if config.reasoning is not None:
        reasoning = config.reasoning
        ok = (
            reasoning.effort in caps.reasoning_efforts
            if reasoning.effort is not None
            else caps.reasoning_budget_tokens
        )
        record(
            CapabilityControl.REASONING,
            f"reasoning control {reasoning.model_dump()}",
            ok,
            "reasoning control supported" if ok else "reasoning control not supported",
        )
    if caps.context_limit_tokens is None:
        record(
            CapabilityControl.CONTEXT,
            "declared context limit",
            False,
            "context limit is not declared",
        )
    else:
        fits = protocol.maximum_input_context_tokens + config.max_output_tokens <= (
            caps.context_limit_tokens
        )
        record(
            CapabilityControl.CONTEXT,
            "protocol context plus output fits the model window",
            fits,
            f"limit {caps.context_limit_tokens}",
        )
    needed = {UsageCounter.INPUT, UsageCounter.OUTPUT}
    has_usage = needed <= caps.usage_counters
    record(
        CapabilityControl.USAGE,
        "input and output usage counters",
        has_usage,
        "usage reported"
        if has_usage
        else "usage not reported; calls settle as unknown-cost exposure, never zero",
    )
    if caps.max_output_tokens_limit and config.max_output_tokens > caps.max_output_tokens_limit:
        rows.append(
            RequirementStatus(
                control=CapabilityControl.CONTEXT,
                requirement="max output tokens",
                status="unsupported",
                detail=f"exceeds limit {caps.max_output_tokens_limit}",
            )
        )
    bound = cost_bound(config, caps, request_bytes=0)
    return CompatibilityReport(
        requirements=tuple(rows),
        exceptions_applied=tuple(applied),
        cost_bound_kind=bound.kind,
        strict_cap_eligible=bound.strict_cap_eligible,
    )


def ensure_compatible(report: CompatibilityReport, config: ModelConfig) -> None:
    if report.unsupported:
        names = ", ".join(f"{item.control.value} ({item.detail})" for item in report.unsupported)
        raise CapabilityUnsupported(f"unsupported controls without a cohort exception: {names}")
    if config.strict_money_cap and not report.strict_cap_eligible:
        raise CostBoundUnavailable(
            "no enforceable provider cost bound exists; a strict money cap cannot be claimed"
        )


def cost_bound(
    config: ModelConfig,
    caps: ModelCapabilities,
    *,
    request_bytes: int,
    output_tokens: int | None = None,
    has_tools: bool = False,
) -> CostBound:
    """Conservative per-call maximum in micro-USD (integer, rounded up).

    ``output_tokens`` is the cap actually sent on the wire; it may not exceed the resolved
    configuration's cap, so the reservation always covers what the provider can bill.
    """
    output_bound = config.max_output_tokens if output_tokens is None else output_tokens
    if caps.max_output_tokens_limit:
        output_bound = min(output_bound, caps.max_output_tokens_limit)
    price = config.price
    input_bound = _input_bound(request_bytes, price, has_tools)
    if config.cost_policy == "operator_conservative":
        assert config.conservative_call_reserve_micro_usd is not None
        return CostBound(
            kind="operator_conservative",
            max_cost_micro_usd=config.conservative_call_reserve_micro_usd,
            input_tokens_bound=input_bound,
            output_tokens_bound=output_bound,
            strict_cap_eligible=False,
            reason="operator-declared per-call reserve; not a provider-enforced bound",
        )
    if price is None:
        return CostBound(
            kind="none",
            max_cost_micro_usd=None,
            input_tokens_bound=input_bound,
            output_tokens_bound=output_bound,
            strict_cap_eligible=False,
            reason="no price snapshot supplied",
        )
    if not caps.output_cap_bounds_all_billed_output:
        return CostBound(
            kind="none",
            max_cost_micro_usd=None,
            input_tokens_bound=input_bound,
            output_tokens_bound=output_bound,
            strict_cap_eligible=False,
            reason="provider does not document that the output cap bounds all "
            "billed output (reasoning) tokens",
        )
    cost = _ceil_div(input_bound * price.input_micro_usd_per_million_tokens, 1_000_000) + (
        _ceil_div(output_bound * price.output_micro_usd_per_million_tokens, 1_000_000)
    )
    return CostBound(
        kind="provider_bound",
        max_cost_micro_usd=cost,
        input_tokens_bound=input_bound,
        output_tokens_bound=output_bound,
        strict_cap_eligible=True,
        reason=f"price {price.price_id} x token upper bounds (no cache discount)",
    )


def require_cost_bound(bound: CostBound) -> int:
    if bound.max_cost_micro_usd is None:
        raise CostBoundUnavailable(bound.reason)
    return bound.max_cost_micro_usd


def compute_cost_micro_usd(price: PriceSnapshot, *, input_tokens: int, output_tokens: int) -> int:
    """Estimated charge from reported usage; list price with no cache discount (an upper
    estimate). Labeled ``estimated`` wherever it is stored."""
    return _ceil_div(input_tokens * price.input_micro_usd_per_million_tokens, 1_000_000) + (
        _ceil_div(output_tokens * price.output_micro_usd_per_million_tokens, 1_000_000)
    )


def map_provider_seed(sample_seed: int, caps: ModelCapabilities) -> int | None:
    """Deterministic ``modulo-v1`` mapping of the unsigned 64-bit sample seed."""
    if not caps.seed or caps.seed_min is None or caps.seed_max is None:
        return None
    if not 0 <= sample_seed <= 2**64 - 1:
        raise ValueError("sample seed must be an unsigned 64-bit integer")
    return caps.seed_min + sample_seed % (caps.seed_max - caps.seed_min + 1)


def _input_bound(request_bytes: int, price: PriceSnapshot | None, has_tools: bool) -> int:
    ppm = price.input_tokens_per_byte_ppm if price else 1_000_000
    overhead = REQUEST_TOKEN_OVERHEAD + (TOOL_PROMPT_OVERHEAD_TOKENS if has_tools else 0)
    return _ceil_div(request_bytes * ppm, 1_000_000) + overhead


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)
