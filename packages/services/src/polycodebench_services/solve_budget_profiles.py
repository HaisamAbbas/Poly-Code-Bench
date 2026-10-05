"""Load the frozen execution budgets that run configurations name."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import yaml
from polycodebench_core.models import SAFE_JSON_INTEGER_MAX, Slug
from polycodebench_core.solve_contracts import SolveBudget
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Positive = Annotated[int, Field(strict=True, gt=0, le=SAFE_JSON_INTEGER_MAX)]
NonNegative = Annotated[int, Field(strict=True, ge=0, le=SAFE_JSON_INTEGER_MAX)]


class _BudgetProfile(BaseModel):
    """One complete profile row from ``config/budgets``; extra knobs fail closed."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    model_turns: Positive
    tool_calls: NonNegative
    active_solve_seconds: Positive
    per_command_seconds: Positive | None
    input_tokens: Positive
    output_tokens: Positive
    guest_memory_mib: Positive
    guest_cpu: Positive
    writable_disk_gib: Positive
    process_count: Positive

    def solve_budget(self) -> SolveBudget:
        return SolveBudget(
            schema_version=1,
            kind="solve_budget",
            model_turns=self.model_turns,
            tool_calls=self.tool_calls,
            active_solve_seconds=self.active_solve_seconds,
            per_command_seconds=self.per_command_seconds,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


def load_solve_budget_profiles(path: Path) -> dict[str, SolveBudget]:
    """Parse all configured profiles into immutable protocol-budget contracts.

    Loading these operational ceilings does not authorize provider spending; the gateway's
    independent owner-authorized money limit remains required for live calls.
    """
    document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or document.get("kind") != "budget_profiles"
    ):
        raise ValueError(f"{path.name}: expected version 1 budget_profiles document")
    rows = document.get("profiles")
    if not isinstance(rows, dict) or not rows:
        raise ValueError(f"{path.name}: at least one budget profile is required")

    slug = TypeAdapter(Slug)
    profiles: dict[str, SolveBudget] = {}
    for raw_id, raw_profile in rows.items():
        profile_id = slug.validate_python(raw_id)
        if not isinstance(raw_profile, dict):
            raise ValueError(f"{path.name}: budget profile {profile_id} must be a mapping")
        profile = _BudgetProfile.model_validate(raw_profile)
        profiles[profile_id] = profile.solve_budget()
    return profiles
