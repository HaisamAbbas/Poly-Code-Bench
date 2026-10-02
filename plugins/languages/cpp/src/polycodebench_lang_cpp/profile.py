"""C++ diagnostic and idiomatic profiles: loading, normalisation and evaluation.

The C++ profile answers three questions the C and Rust profiles cannot answer for it, because the
language's failure modes are different:

* a **raw pointer is not a finding**. ``new``, ``delete``, a ``T*`` parameter and ``std::move``
  are tokens. A mapping whose ``context_evaluator`` is ``context-required`` counts only when the
  context scanner independently confirms a violation at the same site; otherwise it is demoted to
  ``needs_review``. A non-owning raw pointer parameter is never a violation.
* **one defect, one penalty**. Two tools reporting the same copy, the same ownership fault or the
  same undefined behaviour collapse to one canonical issue key, so a defect that ``clang-tidy``,
  ``cppcheck``, the context scanner and AddressSanitizer all see is still one deduction with one
  composite owner.
* **no opportunity, no score**. An item whose frozen task opportunity count is zero is
  ``not_applicable``; it is never quietly normalised into a perfect score.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.models import Confidence, MeasurementStatus, Observation, ScoreDimension
from polycodebench_plugins_api import (
    LanguageProfile,
    PluginModel,
    ProfileItem,
    RuleMapping,
)
from pydantic import Field

from polycodebench_lang_cpp.observations import COLUMN_KEYED_FAMILIES, issue_key

PROFILE_VERSION = "cpp-profile-v1"
CONTEXT_PREFIX = "cpp.context."
TOKEN_ONLY = "context-required"

_DIAGNOSTIC_DESCRIPTIONS = {
    "raii_ownership": "Resources are acquired and released by RAII types, not by manual new/delete",
    "moves_copies": "Values are moved rather than copied, and every copy is deliberate",
    "stl_use": "Standard containers and algorithms replace hand-rolled equivalents",
    "exception_safety": "Throwing paths leave objects valid and destructible",
    "modern_features": "Modern C++ expresses intent that older idioms only approximate",
    "abstraction_performance": "Abstraction choices do not impose a measurable cost",
    "undefined_behavior_memory_safety": "No undefined behaviour and no memory error is reachable",
}
_IDIOM_DESCRIPTIONS = {
    "ownership_raii_design": "Ownership is expressed in the types the API hands back",
    "stl_container_choice": "The container is chosen for its operations, not by habit",
    "value_move_api_semantics": "APIs move values in and out where copy elision cannot",
    "modern_constructs": "The language's own constructs replace boilerplate",
}
_CONFIDENCE_RANK = {
    Confidence.CONFIRMED: 3,
    Confidence.HIGH: 2,
    Confidence.MEDIUM: 1,
    Confidence.LOW: 0,
    Confidence.UNREVIEWED: 0,
}
_SEVERITY_RANK = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
Group = Literal["diagnostic", "idiom"]


class ItemResult(PluginModel):
    kind: Literal["profile_item_result"] = "profile_item_result"
    schema_version: Literal[1] = 1
    item_id: str
    group: Group
    weight_bp: int
    opportunities: int = Field(ge=0)
    unique_violations: int = Field(ge=0)
    status: Literal["measured", "not_applicable", "missing"]
    score_bp: int | None = None
    issue_keys: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class ProfileResult(PluginModel):
    kind: Literal["profile_result"] = "profile_result"
    schema_version: Literal[1] = 1
    profile_version: str
    diagnostic: tuple[ItemResult, ...]
    idioms: tuple[ItemResult, ...]
    diagnostic_score_bp: int | None
    idiom_score_bp: int | None
    complete: bool


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "config" / "languages" / "profiles-v1.yaml").is_file():
            return parent
    raise FileNotFoundError("config/languages/profiles-v1.yaml")


def _items(percent: Mapping[str, Any], descriptions: Mapping[str, str]) -> tuple[ProfileItem, ...]:
    result: list[ProfileItem] = []
    for name, share in percent.items():
        if name not in descriptions:
            raise ValueError(f"C++ profile item {name!r} has no description")
        result.append(
            ProfileItem(
                kind="profile_item",
                item_id=str(name),
                weight_bp=int(round(float(share) * 100)),
                description=descriptions[str(name)],
            )
        )
    total = sum(item.weight_bp for item in result)
    if total != 10_000:
        raise ValueError(f"C++ weights sum to {total} basis points, not 10000")
    return tuple(result)


@lru_cache(maxsize=2)
def load_profile(root: str | None = None) -> CppProfile:
    base = Path(root) if root else _repo_root()
    weights = yaml.safe_load((base / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    section: dict[str, Any] = weights["profiles"]["cpp"]
    document: dict[str, Any] = yaml.safe_load(
        (base / "config/languages/cpp-profile-v1.yaml").read_text("utf-8")
    )
    if document["profile_version"] != PROFILE_VERSION:
        raise ValueError(f"C++ profile version is {document['profile_version']!r}")
    # Strict models reject the YAML document's lists, so the profile is validated the way it is
    # published: as JSON.
    profile = LanguageProfile.model_validate_json(
        json.dumps(
            {
                "language_id": "cpp",
                "profile_version": PROFILE_VERSION,
                "effective_for_scoring": bool(document["effective_for_scoring"]),
                "diagnostic_items": [
                    {
                        "item_id": name,
                        "weight_bp": int(round(float(percent) * 100)),
                        "description": _DIAGNOSTIC_DESCRIPTIONS[str(name)],
                    }
                    for name, percent in section["diagnostic_percent"].items()
                ],
                "idiom_items": [
                    {
                        "item_id": name,
                        "weight_bp": int(round(float(percent) * 100)),
                        "description": _IDIOM_DESCRIPTIONS[str(name)],
                    }
                    for name, percent in section["idiomatic_percent"].items()
                ],
                "rule_mappings": document["rule_mappings"],
                "applicability_rules": document["applicability_rules"],
                "ownership": document["ownership"],
            }
        )
    )
    return CppProfile(profile)


class CppProfile:
    """The published profile plus the C++ rules that decide what counts."""

    def __init__(self, profile: LanguageProfile) -> None:
        self.profile = profile
        self._rules = {rule.rule_id: rule for rule in profile.applicability_rules}

    # -------------------------------------------------------------------- resolution

    def resolve(self, check_id: str) -> RuleMapping | None:
        for mapping in self.profile.rule_mappings:
            if mapping.matches(check_id):
                return mapping
        return None

    def key_for(self, check_id: str, path: str, line: int, column: int | None = None) -> str | None:
        mapping = self.resolve(check_id)
        if mapping is None:
            return None
        family = mapping.equivalence_family or str(check_id).rsplit(".", 1)[-1]
        return issue_key(family, path, line, column if family in COLUMN_KEYED_FAMILIES else None)

    def owner(self, check_id: str) -> ScoreDimension | None:
        mapping = self.resolve(check_id)
        return None if mapping is None else mapping.owner

    def is_token_only(self, check_id: str) -> bool:
        mapping = self.resolve(check_id)
        return mapping is not None and mapping.context_evaluator == TOKEN_ONLY

    # ----------------------------------------------------------------- normalisation

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        """Collapse duplicate reports of one defect; demote unconfirmed token lints."""
        grouped: dict[str, list[Observation]] = {}
        result: list[Observation] = []
        for observation in observations:
            key = observation.issue_key
            if key is None:
                result.append(observation)
            else:
                grouped.setdefault(key, []).append(observation)
        for key in sorted(grouped):
            group = grouped[key]
            context = [o for o in group if (o.check_id or "").startswith(CONTEXT_PREFIX)]
            tokens = [o for o in group if self.is_token_only(o.check_id or "") and o not in context]
            precise = [o for o in group if o not in context and o not in tokens]
            counted = [o for o in (*context, *precise) if o.status == MeasurementStatus.MEASURED]
            if counted:
                best = max(counted, key=self._strength)
                others = sorted({o.check_id or "" for o in counted} - {best.check_id})
                note = f" (also reported by {', '.join(others)})" if others else ""
                result.append(
                    best.model_copy(update={"explanation": (best.explanation or "") + note})
                )
                continue
            if context:
                chosen = sorted(context, key=lambda o: (o.status.value, o.check_id or ""))[0]
                result.append(chosen)
                continue
            if precise:
                result.append(sorted(precise, key=lambda o: (o.status.value, o.check_id or ""))[0])
                continue
            # Only token-only lints: nothing but the token is known about this site.
            first = sorted(tokens, key=lambda o: o.check_id or "")[0]
            if first.status != MeasurementStatus.MEASURED:
                result.append(first)
                continue
            result.append(
                first.model_copy(
                    update={
                        "status": MeasurementStatus.NEEDS_REVIEW,
                        "value": None,
                        "severity": None,
                        "confidence": None,
                        "explanation": (first.explanation or "")
                        + " (token-only lint; no contextual evidence, so it is not counted)",
                    }
                )
            )
        return result

    def _strength(self, observation: Observation) -> tuple[int, int, str]:
        return (
            _SEVERITY_RANK.get(observation.severity, 0),
            _CONFIDENCE_RANK.get(observation.confidence or Confidence.UNREVIEWED, 0),
            observation.check_id or "",
        )

    # -------------------------------------------------------------------- evaluation

    def evaluate(
        self,
        *,
        opportunities: Mapping[str, int],
        observations: Sequence[Observation],
        required_tools: Sequence[str],
    ) -> ProfileResult:
        scans = {
            o.check_id: o
            for o in observations
            if (o.check_id or "").startswith("cpp.") and (o.check_id or "").endswith(".scan")
        }
        failed_tools: dict[str, str] = {}
        for tool in required_tools:
            scan = scans.get(f"cpp.{tool}.scan")
            if scan is None or scan.status == MeasurementStatus.MISSING:
                failed_tools[tool] = f"required scan incomplete: {tool}"
            elif scan.status == MeasurementStatus.NOT_APPLICABLE:
                # Unsupported is not clean: a task that requires this scan cannot be scored from it.
                failed_tools[tool] = f"required scan unsupported: {tool}"
            elif scan.status != MeasurementStatus.MEASURED:
                failed_tools[tool] = f"required scan incomplete: {tool}"
        confirmed = {
            o.issue_key
            for o in observations
            if (o.check_id or "").startswith(CONTEXT_PREFIX)
            and o.status == MeasurementStatus.MEASURED
            and o.issue_key is not None
        }
        feeders = self._feeders()
        violations: dict[str, dict[str, set[str]]] = {"diagnostic": {}, "idiom": {}}
        for observation in observations:
            if observation.status != MeasurementStatus.MEASURED or observation.issue_key is None:
                continue
            mapping = self.resolve(observation.check_id or "")
            if mapping is None:
                continue
            if mapping.context_evaluator == TOKEN_ONLY and observation.issue_key not in confirmed:
                # Holds even when the caller skipped normalize(): a token alone proves nothing.
                continue
            rule = self._rules[str(mapping.applicability)]
            if (
                rule.requires_task_opportunity is not None
                and opportunities.get(rule.requires_task_opportunity, 0) <= 0
            ):
                continue
            for item in mapping.items:
                violations["diagnostic"].setdefault(item, set()).add(observation.issue_key)
            if mapping.idiom_item is not None:
                violations["idiom"].setdefault(mapping.idiom_item, set()).add(observation.issue_key)
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
        """Tools whose evidence can feed each ``(group, item)``, read off the check namespace."""
        result: dict[tuple[str, str], set[str]] = {}
        for mapping in self.profile.rule_mappings:
            reference = mapping.check_id or mapping.check_prefix or ""
            parts = reference.split(".")
            tool = parts[1] if len(parts) > 2 else ""
            for item in mapping.items:
                result.setdefault(("diagnostic", item), set()).add(tool)
            if mapping.idiom_item:
                result.setdefault(("idiom", mapping.idiom_item), set()).add(tool)
        return result

    def _item(
        self,
        item: ProfileItem,
        group: Group,
        opportunities: Mapping[str, int],
        violations: Mapping[str, Mapping[str, set[str]]],
        feeders: Mapping[tuple[str, str], set[str]],
        failed_tools: Mapping[str, str],
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
        broken = sorted(feeders.get((group, item.item_id), set()) & set(failed_tools))
        if broken:
            return ItemResult(
                item_id=item.item_id,
                group=group,
                weight_bp=item.weight_bp,
                opportunities=count,
                unique_violations=0,
                status="missing",
                reasons=tuple(failed_tools[tool] for tool in broken),
            )
        keys = sorted(violations[group].get(item.item_id, set()))
        counted = min(len(keys), count)
        return ItemResult(
            item_id=item.item_id,
            group=group,
            weight_bp=item.weight_bp,
            opportunities=count,
            unique_violations=len(keys),
            status="measured",
            score_bp=(10_000 * (count - counted) + count // 2) // count,
            issue_keys=tuple(keys),
        )


def _weighted(items: Sequence[ItemResult]) -> int | None:
    applicable = [item for item in items if item.status != "not_applicable"]
    if not applicable or any(item.status == "missing" for item in applicable):
        return None
    total = sum(item.weight_bp for item in applicable)
    return (sum(item.weight_bp * (item.score_bp or 0) for item in applicable) + total // 2) // total


def profile_document(profile: LanguageProfile) -> str:
    return json.dumps(profile.model_dump(mode="json"), sort_keys=True)


__all__ = [
    "CONTEXT_PREFIX",
    "CppProfile",
    "ItemResult",
    "PROFILE_VERSION",
    "ProfileResult",
    "TOKEN_ONLY",
    "load_profile",
    "profile_document",
]
