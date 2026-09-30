"""Plan-time compatibility and cost output. Nothing here contacts a provider."""

from __future__ import annotations

from typing import Annotated

from polycodebench_core.model_contracts import Strict
from polycodebench_core.model_planning import (
    CompatibilityReport,
    CostBound,
    ModelConfig,
    cost_bound,
    effective_capabilities,
)
from polycodebench_core.models import ProtocolDefinition
from pydantic import Field

from polycodebench_orchestration.gateway.adapters.base import BaseAdapter

ESTIMATE_LABEL = (
    "ESTIMATE: worst-case exposure from the declared price snapshot and provable token upper "
    "bounds; expected spend is lower. It is not a forecast and not an invoice."
)


class RunCostPlan(Strict):
    label: str
    compatible: bool
    compatibility: CompatibilityReport
    per_call: CostBound
    planned_model_calls: Annotated[int, Field(ge=0)]
    max_deliveries_per_call: int
    worst_case_money_micro_usd: int | None
    worst_case_money_with_retries_micro_usd: int | None
    worst_case_input_tokens: int
    worst_case_output_tokens: int
    price_snapshot_id: str | None
    strict_cap_eligible: bool
    blockers: tuple[str, ...]


def plan_model_run(
    *,
    config: ModelConfig,
    protocol: ProtocolDefinition,
    adapter: BaseAdapter,
    tasks: int,
    samples_per_task: int,
    max_request_bytes: int | None = None,
    max_deliveries: int = 3,
    requires_structured_output: bool = False,
) -> RunCostPlan:
    """Resolve compatibility, cost bound and worst-case exposure for a run plan."""
    from polycodebench_core.model_planning import check_compatibility

    report = check_compatibility(
        config,
        protocol,
        adapter.capabilities(),
        requires_structured_output=requires_structured_output,
    )
    caps = effective_capabilities(adapter.capabilities(), config.declared_capabilities)
    request_bytes = (
        max_request_bytes
        if max_request_bytes is not None
        else protocol.maximum_input_context_tokens
    )
    bound = cost_bound(
        config,
        caps,
        request_bytes=request_bytes,
        has_tools=bool(protocol.allowed_tools),
    )
    turns = max(1, protocol.maximum_turns)
    calls = tasks * samples_per_task * turns
    blockers = [f"{item.control.value}: {item.detail}" for item in report.unsupported]
    if config.strict_money_cap and not bound.strict_cap_eligible:
        blockers.append(f"strict money cap unavailable: {bound.reason}")
    if bound.max_cost_micro_usd is None:
        blockers.append(f"no cost bound: {bound.reason}")
    worst = None if bound.max_cost_micro_usd is None else calls * bound.max_cost_micro_usd
    return RunCostPlan(
        label=ESTIMATE_LABEL,
        compatible=not blockers,
        compatibility=report,
        per_call=bound,
        planned_model_calls=calls,
        max_deliveries_per_call=max_deliveries,
        worst_case_money_micro_usd=worst,
        worst_case_money_with_retries_micro_usd=None if worst is None else worst * max_deliveries,
        worst_case_input_tokens=calls * bound.input_tokens_bound,
        worst_case_output_tokens=calls * bound.output_tokens_bound,
        price_snapshot_id=config.price.price_id if config.price else None,
        strict_cap_eligible=bound.strict_cap_eligible,
        blockers=tuple(blockers),
    )
