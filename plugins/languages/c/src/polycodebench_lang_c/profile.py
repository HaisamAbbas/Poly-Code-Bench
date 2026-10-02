"""The C diagnostic and idiom profiles: loading, normalisation and evaluation.

C's specificity against Rust's profile is the *dynamic* lane. A sanitizer finding is a fact about
executed paths, not a guess about intent, so it counts directly - but only when the lane actually ran.
A missing, crashed, unsupported or timed-out lane makes the item ``missing``, never perfect, and a
lane declared ``unsupported`` makes it ``not_applicable`` rather than penalising a task the tool
cannot judge.

Two other rules are inherited from the shared contract and are what keep C honest:

* counts are per *canonical issue*, so two analyzers reporting one unchecked ``malloc`` count once;
* a construct's presence is not a violation. ``free`` is correct somewhere and a leak somewhere else,
  and only a leak is evidence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.models import Confidence, MeasurementStatus, Observation, ScoreDimension
from polycodebench_plugins_api import LanguageProfile, PluginModel, ProfileItem, RuleMapping
from pydantic import Field

from polycodebench_lang_c.observations import COLUMN_KEYED_FAMILIES, issue_key

PROFILE_VERSION = "c-profile-v1"
CPPCHECK_PREFIX = "c.cppcheck."
TIDY_PREFIX = "c.tidy."
#: Analyzer ids whose findings are precise measurements rather than pattern matches.
PRECISE_PREFIXES = ("c.asan.", "c.ubsan.", "c.valgrind.")

_DIAGNOSTIC_DESCRIPTIONS = {
    "memory_safety": "Memory safety on executed paths, and the ownership the code shows",
    "undefined_behavior": "Undefined behaviour the language standard leaves unspecified",
    "performance_cache": "Algorithmic and memory cost a reviewer can point at",
    "const_ownership": "const-correctness and ownership expressed through qualifiers",
    "portability": "Portability across the frozen target rather than one compiler version",
    "error_checks": "Specific, complete error checking rather than hopeful returns",
}
_IDIOM_DESCRIPTIONS = {
    "ownership_api_contracts": "Clear ownership and API contracts in headers and definitions",
    "const_type_portability": "Const, type choices and portability of the frozen interfaces",
    "data_function_interfaces": "Suitable data structures and function interfaces for the task",
}
_SEVERITY_RANK = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_CONFIDENCE_RANK = {
    Confidence.UNREVIEWED: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.CONFIRMED: 4,
}


class ItemResult(PluginModel):
    kind: Literal["profile_item_result"] = "profile_item_result"
    item_id: str
    group: Literal["diagnostic", "idiom"]
    weight_bp: int
    opportunities: int = Field(ge=0)
    unique_violations: int = Field(ge=0)
    status: Literal["measured", "not_applicable", "missing"]
    score_bp: int | None = None
    issue_keys: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class ProfileResult(PluginModel):
    kind: Literal["profile_result"] = "profile_result"
    profile_version: str
    diagnostic: tuple[ItemResult, ...]
    idioms: tuple[ItemResult, ...]
    diagnostic_score_bp: int | None
    idiom_score_bp: int | None
    complete: bool
    coverage_note: str = ""


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "languages" / "profiles-v1.yaml").is_file():
            return parent
    raise FileNotFoundError("config/languages/profiles-v1.yaml")


@lru_cache(maxsize=2)
def load_profile(root: str | None = None) -> "CProfile":
    base = Path(root) if root else _repo_root()
    weights = yaml.safe_load((base / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    own = yaml.safe_load((base / "config/languages/c-profile-v1.yaml").read_text("utf-8"))
    profile_c = weights["profiles"]["c"]
    document = {
        "language_id": "c",
        "profile_version": own["profile_version"],
        "effective_for_scoring": bool(own["effective_for_scoring"]),
        "diagnostic_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _DIAGNOSTIC_DESCRIPTIONS[name],
            }
            for name, percent in profile_c["diagnostic_percent"].items()
        ],
        "idiom_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _IDIOM_DESCRIPTIONS[name],
            }
            for name, percent in profile_c["idiomatic_percent"].items()
        ],
        "rule_mappings": own["rule_mappings"],
        "applicability_rules": own["applicability_rules"],
        "ownership": own["ownership"],
    }
    return CProfile(LanguageProfile.model_validate_json(json.dumps(document)))


class CProfile:
    def __init__(self, profile: LanguageProfile) -> None:
        self.profile = profile
        self._rules = {rule.rule_id: rule for rule in profile.applicability_rules}

    def resolve(self, check_id: str) -> RuleMapping | None:
        for mapping in self.profile.rule_mappings:
            if mapping.matches(check_id):
                return mapping
        return None

    def key_for(self, check_id: str, path: str, line: int, column: int | None = None) -> str:
        """Canonical issue key: cross-tool equivalence comes from reviewed mappings only.

        A sanitizer's family (``bounds-violation``) and cppcheck's (``arrayIndexOutOfBounds``) are
        both mapped to one family in ``c-profile-v1.yaml``, so one out-of-bounds read is one issue
        however many tools saw it.
        """
        mapping = self.resolve(check_id)
        family = (mapping.equivalence_family if mapping else None) or check_id.rsplit(".", 1)[-1]
        precise = column if family in COLUMN_KEYED_FAMILIES else None
        return issue_key(family, path, line, precise)

    def owner(self, check_id: str) -> ScoreDimension | None:  # type: ignore[no-untyped-def]
        mapping = self.resolve(check_id)
        return mapping.owner if mapping else None

    def is_precise(self, check_id: str) -> bool:
        """True for a measured dynamic finding, which no heuristic may downgrade."""
        return check_id.startswith(PRECISE_PREFIXES)

    # ------------------------------------------------------------------ normalisation

    def normalize(self, observations: Sequence[Observation]) -> list[Observation]:
        """Merge duplicate reports of one canonical issue.

        A sanitizer finding is precise and wins outright. Otherwise the strongest measured report
        wins, and a ``needs_review`` report from the same site is dropped rather than kept alongside a
        measured one that already decided the question.
        """
        keyed: dict[str, list[Observation]] = {}
        passthrough: list[Observation] = []
        for obs in observations:
            if obs.issue_key is None:
                passthrough.append(obs)
            else:
                keyed.setdefault(obs.issue_key, []).append(obs)
        merged: list[Observation] = []
        for _key, group in sorted(keyed.items()):
            merged.append(self._merge(group))
        return sorted(passthrough, key=lambda o: o.check_id) + merged

    def _merge(self, group: list[Observation]) -> Observation:
        precise = [o for o in group if self.is_precise(o.check_id)]
        counted = [o for o in (*precise, *group) if o.status == MeasurementStatus.MEASURED]
        if counted:
            best = max(
                counted,
                key=lambda o: (
                    1 if self.is_precise(o.check_id) else 0,
                    _SEVERITY_RANK[o.severity],
                    _CONFIDENCE_RANK.get(o.confidence or Confidence.UNREVIEWED, 0),
                    o.check_id,
                ),
            )
            others = sorted({o.check_id for o in group} - {best.check_id})
            note = f" (also reported by {', '.join(others)})" if others else ""
            return best.model_copy(update={"explanation": (best.explanation or "") + note})
        return sorted(group, key=lambda o: (o.status.value, o.check_id))[0]

    # --------------------------------------------------------------------- evaluation

    def evaluate(
        self,
        *,
        opportunities: Mapping[str, int],
        observations: Sequence[Observation],
        required_tools: Sequence[str],
    ) -> ProfileResult:
        scans: dict[str, list[Observation]] = {}
        for obs in observations:
            if obs.check_id.startswith("c.") and obs.check_id.endswith(".scan"):
                scans.setdefault(obs.check_id, []).append(obs)
        failed_tools: dict[str, str] = {}
        for tool in required_tools:
            reports = scans.get(f"c.{tool}.scan", [])
            if not reports:
                failed_tools[tool] = f"required scan produced no evidence: {tool}"
                continue
            # A C dynamic lane emits one scan per oracle group. Every one of them has to have run: a
            # lane that judged the acceptance group and never reached the quality group has measured
            # less than the task asked for, and averaging that away would be the wrong kind of kind.
            for report in reports:
                if report.status == MeasurementStatus.MISSING:
                    failed_tools[tool] = f"required scan incomplete: {tool}"
                elif report.status == MeasurementStatus.NOT_APPLICABLE:
                    failed_tools[tool] = f"required scan unsupported: {tool}"
                elif report.status != MeasurementStatus.MEASURED:
                    failed_tools[tool] = f"required scan incomplete: {tool}"
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
            coverage_note=(
                "dynamic items score executed paths only; unexercised code is not evidence"
            ),
        )

    def _feeders(self) -> dict[tuple[str, str], set[str]]:
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
        group: Literal["diagnostic", "idiom"],
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