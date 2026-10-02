"""Python diagnostic and orthogonal idiom profiles: loading, normalisation and evaluation.

Rules this module enforces by construction:

* weights come from ``config/languages/profiles-v1.yaml`` and are cross-checked, never re-typed;
* an item without a frozen task opportunity is ``not_applicable`` - never a perfect score;
* counts are per *canonical issue* (one key however many tools report it), so a duplicate report
  cannot add a penalty, and constructs that merely appear in the code never earn credit;
* evidence from a required scan that is missing or incomplete makes the dependent items
  ``missing``; it is never read as "no violations".
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.models import Confidence, MeasurementStatus, Observation
from polycodebench_plugins_api import (
    LanguageProfile,
    PluginModel,
    ProfileItem,
    ProfileItemResult as ItemResult,
    ProfileResult,
    RuleMapping,
)
from pydantic import Field

from polycodebench_lang_python.observations import issue_key

PROFILE_VERSION = "python-profile-v1"
COLUMN_KEYED_FAMILIES = frozenset({"mutable-default"})

_DIAGNOSTIC_DESCRIPTIONS = {
    "readability_idioms": "Readability and Pythonic idiom use",
    "type_hints": "Type hints where the task contract expects them",
    "stdlib_use": "Use of the standard library where it fits",
    "error_handling": "Specific, handled, resource-safe error handling",
    "lint_style": "Lint and style hygiene",
    "performance_awareness": "Awareness of avoidable algorithmic or memory cost",
}
_IDIOM_DESCRIPTIONS = {
    "iteration_laziness": "Suitable iteration and laziness",
    "stdlib_api_choice": "Standard library and API choice",
    "data_protocol_modeling": "Clear data and protocol modeling",
    "context_resource_abstraction": "Context and resource abstraction design",
}
_CONFIDENCE_RANK = {
    Confidence.UNREVIEWED: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.CONFIRMED: 4,
}
_SEVERITY_RANK = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "languages" / "profiles-v1.yaml").is_file():
            return parent
    raise FileNotFoundError("config/languages/profiles-v1.yaml")


@lru_cache(maxsize=2)
def load_profile(root: str | None = None) -> PythonProfile:
    base = Path(root) if root else _repo_root()
    weights = yaml.safe_load((base / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    own = yaml.safe_load((base / "config/languages/python-profile-v1.yaml").read_text("utf-8"))
    python = weights["profiles"]["python"]
    document = {
        "language_id": "python",
        "profile_version": own["profile_version"],
        "effective_for_scoring": bool(own["effective_for_scoring"]),
        "diagnostic_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _DIAGNOSTIC_DESCRIPTIONS[name],
            }
            for name, percent in python["diagnostic_percent"].items()
        ],
        "idiom_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _IDIOM_DESCRIPTIONS[name],
            }
            for name, percent in python["idiomatic_percent"].items()
        ],
        "rule_mappings": own["rule_mappings"],
        "applicability_rules": own["applicability_rules"],
        "ownership": own["ownership"],
    }
    profile = LanguageProfile.model_validate_json(json.dumps(document))
    return PythonProfile(profile)


class PythonProfile:
    def __init__(self, profile: LanguageProfile) -> None:
        self.profile = profile
        self._rules = {rule.rule_id: rule for rule in profile.applicability_rules}

    def resolve(self, check_id: str) -> RuleMapping | None:
        for mapping in self.profile.rule_mappings:
            if mapping.matches(check_id):
                return mapping
        return None

    def key_for(self, check_id: str, path: str, line: int, column: int | None) -> str:
        """Canonical issue key: cross-tool equivalence comes from reviewed mappings only."""
        mapping = self.resolve(check_id)
        family = (mapping.equivalence_family if mapping else None) or check_id.rsplit(".", 1)[-1]
        precise = column if family in COLUMN_KEYED_FAMILIES else None
        return issue_key(family, path, line, precise)

    def owner(self, check_id: str):  # type: ignore[no-untyped-def]
        mapping = self.resolve(check_id)
        return mapping.owner if mapping else None

    # ------------------------------------------------------------------ normalisation

    def normalize(self, observations: Sequence[Observation]) -> list[Observation]:
        """Merge duplicate reports of one canonical issue; a context verdict is authoritative."""
        keyed: dict[str, list[Observation]] = {}
        passthrough: list[Observation] = []
        for obs in observations:
            if obs.issue_key is None:
                passthrough.append(obs)
            else:
                keyed.setdefault(obs.issue_key, []).append(obs)
        merged: list[Observation] = []
        for _key, group in sorted(keyed.items()):
            context = [o for o in group if o.check_id.startswith("python.context.")]
            others = [o for o in group if not o.check_id.startswith("python.context.")]
            if context:
                chosen = sorted(context, key=lambda o: o.check_id)[0]
                others_text = ", ".join(sorted({o.check_id for o in others}))
                note = f" (also reported by {others_text})" if others_text else ""
                merged.append(
                    chosen.model_copy(update={"explanation": (chosen.explanation or "") + note})
                )
                continue
            measured = [o for o in group if o.status == MeasurementStatus.MEASURED]
            if not measured:
                merged.append(sorted(group, key=lambda o: o.check_id)[0])
                continue
            best = max(
                measured,
                key=lambda o: (
                    _SEVERITY_RANK[o.severity],
                    _CONFIDENCE_RANK.get(o.confidence or Confidence.UNREVIEWED, 0),
                    o.check_id,
                ),
            )
            names = sorted({o.check_id for o in measured} - {best.check_id})
            note = f" (also reported by {', '.join(names)})" if names else ""
            merged.append(best.model_copy(update={"explanation": (best.explanation or "") + note}))
        return sorted(passthrough, key=lambda o: o.check_id) + merged

    # --------------------------------------------------------------------- evaluation

    def evaluate(
        self,
        *,
        opportunities: Mapping[str, int],
        observations: Sequence[Observation],
        required_tools: Sequence[str],
    ) -> ProfileResult:
        scans = {
            obs.check_id: obs
            for obs in observations
            if obs.check_id.startswith("python.") and obs.check_id.endswith(".scan")
        }
        failed_tools = {
            tool
            for tool in required_tools
            if (obs := scans.get(f"python.{tool}.scan")) is None
            or obs.status != MeasurementStatus.MEASURED
        }
        feeders = self._feeders()
        violations: dict[str, dict[str, set[str]]] = {"diagnostic": {}, "idiom": {}}
        for obs in observations:
            if obs.status != MeasurementStatus.MEASURED or obs.issue_key is None:
                continue
            mapping = self.resolve(obs.check_id)
            if mapping is None:
                continue
            rule = self._rules[mapping.applicability]
            if (
                rule.requires_task_opportunity is not None
                and opportunities.get(rule.requires_task_opportunity, 0) <= 0
            ):
                continue
            for item in mapping.items:
                violations["diagnostic"].setdefault(item, set()).add(obs.issue_key)
            if mapping.idiom_item is not None:
                violations["idiom"].setdefault(mapping.idiom_item, set()).add(obs.issue_key)
        diagnostic = tuple(
            self._item(item, "diagnostic", opportunities, violations, feeders, failed_tools)
            for item in self.profile.diagnostic_items
        )
        idioms = tuple(
            self._item(item, "idiom", opportunities, violations, feeders, failed_tools)
            for item in self.profile.idiom_items
        )
        return ProfileResult(
            profile_version=self.profile.profile_version,
            diagnostic=diagnostic,
            idioms=idioms,
            diagnostic_score_bp=_weighted(diagnostic),
            idiom_score_bp=_weighted(idioms),
            complete=not any(i.status == "missing" for i in (*diagnostic, *idioms)),
        )

    def _feeders(self) -> dict[tuple[str, str], set[str]]:
        """Tools whose evidence can feed each (group, item)."""
        result: dict[tuple[str, str], set[str]] = {}
        for mapping in self.profile.rule_mappings:
            ref = mapping.check_id or mapping.check_prefix or ""
            parts = ref.split(".")
            tool = parts[1] if len(parts) > 2 else ""
            for item in mapping.items:
                result.setdefault(("diagnostic", item), set()).add(tool)
            if mapping.idiom_item:
                result.setdefault(("idiom", mapping.idiom_item), set()).add(tool)
        return result

    def _item(
        self,
        item: ProfileItem,
        group: Literal["diagnostic", "idiom"],
        opportunities: Mapping[str, int],
        violations: Mapping[str, Mapping[str, set[str]]],
        feeders: Mapping[tuple[str, str], set[str]],
        failed_tools: set[str],
    ) -> ItemResult:
        count = int(opportunities.get(item.item_id, 0))
        if count <= 0:
            return ItemResult(
                item_id=item.item_id,
                group=group,
                weight_bp=item.weight_bp,
                opportunities=0,
                unique_violations=0,
                status="not_applicable",
                reasons=("no frozen task opportunity",),
            )
        broken = sorted(feeders.get((group, item.item_id), set()) & failed_tools)
        if broken:
            return ItemResult(
                item_id=item.item_id,
                group=group,
                weight_bp=item.weight_bp,
                opportunities=count,
                unique_violations=0,
                status="missing",
                reasons=tuple(f"required scan incomplete: {tool}" for tool in broken),
            )
        keys = sorted(violations[group].get(item.item_id, set()))
        counted = min(len(keys), count)
        score = (10_000 * (count - counted) + count // 2) // count
        return ItemResult(
            item_id=item.item_id,
            group=group,
            weight_bp=item.weight_bp,
            opportunities=count,
            unique_violations=len(keys),
            status="measured",
            score_bp=score,
            issue_keys=tuple(keys),
        )


def _weighted(items: Sequence[ItemResult]) -> int | None:
    applicable = [i for i in items if i.status != "not_applicable"]
    if not applicable or any(i.status == "missing" for i in applicable):
        return None
    total = sum(i.weight_bp for i in applicable)
    return (sum(i.weight_bp * (i.score_bp or 0) for i in applicable) + total // 2) // total


def profile_document(profile: LanguageProfile) -> str:
    return json.dumps(profile.model_dump(mode="json"), sort_keys=True)
