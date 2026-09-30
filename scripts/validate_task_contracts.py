"""Check pilot scoring, language, method, budget, and evidence contract consistency."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict[str, Any]:
    value = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML object")
    if value.get("schema_version") != 1:
        raise ValueError(f"{path} must declare schema_version: 1")
    return value


def _percent_weights(value: object, label: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} has no weights")
    total = sum((Decimal(str(weight)) for weight in value.values()), Decimal(0))
    if total != Decimal(100):
        raise ValueError(f"{label} weights sum to {total}, expected 100")


def validate() -> None:
    scoring = _yaml("config/scoring/pilot-v1.yaml")
    if scoring.get("status") != "owner_approved_pending_calibration":
        raise ValueError("scoring policy must remain explicitly labeled pending calibration")
    if (
        scoring.get("effective_for_scoring") is not False
        or scoring.get("owner_approval") != "approved"
        or scoring.get("approval_scope") != "freeze_as_pilot_baseline_only"
        or scoring.get("calibration_status") != "pending"
    ):
        raise ValueError("owner approval freezes only the baseline; scoring remains inactive")
    composite = scoring["code_composite"]
    weights = composite["weights"]
    if sum(weights.values()) != 10_000:
        raise ValueError("code composite must total 10,000 basis points")
    expected = {
        "correctness": 3000,
        "security": 2000,
        "efficiency": 1500,
        "code_quality": 1500,
        "idiomatic": 1000,
        "robustness": 1000,
    }
    if weights != expected:
        raise ValueError("code composite differs from the sourced pilot starting weights")

    language = _yaml("config/languages/profiles-v1.yaml")
    profiles = language["profiles"]
    required_languages = {"python", "rust", "javascript", "typescript", "c", "cpp", "go", "java"}
    if set(profiles) != required_languages:
        raise ValueError("language profile register omits or aliases a required language")
    for name, profile in profiles.items():
        _percent_weights(profile["diagnostic_percent"], f"{name} diagnostic profile")
        _percent_weights(profile["idiomatic_percent"], f"{name} idiomatic profile")

    budgets = _yaml("config/budgets/pilot-v1.yaml")
    if budgets.get("status") != "proposed_not_authorized":
        raise ValueError("pilot budgets must not imply live-spend authorization")
    if budgets["money"].get("hard_limit_micro_usd") is not None:
        raise ValueError("owner-authorized live money cap has not been supplied")

    deviations = _yaml("config/methodology/deviations-v1.yaml")
    if set(deviations["families"]) != {"swebench", "livecodebench", "cursorbench", "deepcodebench"}:
        raise ValueError("method deviation register must include every named benchmark family")
    evidence = _yaml("config/evidence/schema-map-v1.yaml")
    for schema_path in evidence["schemas"].values():
        if not (ROOT / schema_path).is_file():
            raise ValueError(f"evidence schema is missing: {schema_path}")

    importer = _yaml("config/task-admission/admission-v1.yaml")
    if (
        importer["freeze_policy"].get("public_scored_requires_execution_tier")
        != "production_worker"
    ):
        raise ValueError("public scored admission must require production-worker evidence")
    if importer["freeze_policy"].get("fixture_execution_tier_allowed_only_in_split") != "fixture":
        raise ValueError("local authored fixtures must remain excluded from scored splits")


if __name__ == "__main__":
    validate()
    print(
        "PASS: admission, scoring, rights/method labels, budgets, language profiles, "
        "evidence schemas"
    )
