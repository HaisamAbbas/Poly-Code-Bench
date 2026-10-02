"""Java diagnostic and orthogonal idiom profiles: loading, normalisation and evaluation.

The rule this module exists to enforce is Architecture 11.2, verbatim: **"Streams, records, and
SOLID terminology are not automatic quality points."** Concretely:

* An item is scored as ``opportunities - unique_violations`` over the task's *frozen* opportunity
  counts. A construct that merely appears in the source cannot raise a score: only an analyzer
  finding or a context-scanner violation at a declared opportunity can lower one. There is no
  "used a stream" credit anywhere in this module.
* That is enforced structurally, not by convention. ``PRESENCE_ONLY_FAMILIES`` names the families a
  rule mapping may *not* create, and :func:`_reject_presence_scoring` fails the profile load if a
  mapping would turn one of them into a scorable issue. A reviewer adding a "modern constructs"
  mapping therefore gets a load-time error instead of quietly shipping a bonus for terminology.
* Where a lint fires on a bare token - ``DM_DEFAULT_ENCODING`` on every ``new FileInputStream``, a
  resource pattern on any ``Closeable`` - it counts only when the context scanner independently
  confirms a violation at the same site. The scanner, not the token, decides.
* Evidence from a required scan that is missing, incomplete or unsupported makes the dependent
  items ``missing``; it is never read as "no violations".
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
    ProfileItem,
    ProfileItemResult,
    ProfileResult,
    RuleMapping,
)

from polycodebench_lang_java.observations import COLUMN_KEYED_FAMILIES, issue_key

PROFILE_VERSION = "java-profile-v1"
CONTEXT_PREFIX = "java.context."
TOKEN_ONLY = "context-required"
LANGUAGE = "java"

#: Check-id families that describe a construct being *present*, not a defect. A rule mapping that
#: scores one of these is scoring terminology, so the profile refuses to load. Each entry is the
#: family a mapping would produce for the idiomatic claim it is not allowed to make.
PRESENCE_ONLY_FAMILIES: frozenset[str] = frozenset(
    {
        # "used a stream / collector / lambda"
        "stream-used",
        "stream-pipeline",
        "collector-used",
        # "declared a record"
        "record-declared",
        "sealed-record",
        # "declared an interface / followed SOLID"
        "interface-declared",
        "solid-conformance",
        "dependency-injection-annotation",
        "lombok-annotation",
        "functional-interface",
    }
)

_DIAGNOSTIC_DESCRIPTIONS = {
    "design_boundaries": "Type and module boundaries that actually hold",
    "resource_handling": "Acquired resources released on every path",
    "concurrency": "Shared state published safely and closed down",
    "modern_apis": "Appropriate modern JDK APIs rather than legacy equivalents",
    "null_safety": "Null contracts modelled rather than assumed",
    "security": "No avoidable injection, exposure or unsafe-deserialization surface",
}
_IDIOM_DESCRIPTIONS = {
    "class_api_boundaries": "Class and API boundary design",
    "library_abstractions": "Use of the standard library's abstractions",
    "value_nullability": "Value and nullability modelling in the API",
    "resource_concurrency": "Resource and concurrency abstraction design",
}
_CONFIDENCE_RANK = {
    Confidence.UNREVIEWED: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.CONFIRMED: 4,
}
_SEVERITY_RANK = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class ItemResult(ProfileItemResult):
    """One item's outcome. Re-exported under the plugin's own name for callers."""


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "languages" / "profiles-v1.yaml").is_file():
            return parent
    raise FileNotFoundError("config/languages/profiles-v1.yaml")


def _reject_presence_scoring(mappings: Sequence[RuleMapping]) -> None:
    """Fail the load if any mapping scores a construct's mere presence."""
    for mapping in mappings:
        family = mapping.equivalence_family
        if family is None:
            continue
        if family in PRESENCE_ONLY_FAMILIES:
            raise ValueError(
                f"rule mapping {mapping.check_id or mapping.check_prefix!r} scores "
                f"{family!r}, which describes a construct being present; Java earns no points "
                "for streams, records or SOLID terminology (Architecture 11.2)"
            )


@lru_cache(maxsize=2)
def load_profile(root: str | None = None) -> JavaProfile:
    base = Path(root) if root else _repo_root()
    weights = yaml.safe_load((base / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    own = yaml.safe_load((base / "config/languages/java-profile-v1.yaml").read_text("utf-8"))
    java = weights["profiles"][LANGUAGE]
    document = {
        "language_id": LANGUAGE,
        "profile_version": own["profile_version"],
        "effective_for_scoring": bool(own["effective_for_scoring"]),
        "diagnostic_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _DIAGNOSTIC_DESCRIPTIONS[name],
            }
            for name, percent in java["diagnostic_percent"].items()
        ],
        "idiom_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _IDIOM_DESCRIPTIONS[name],
            }
            for name, percent in java["idiomatic_percent"].items()
        ],
        "rule_mappings": own["rule_mappings"],
        "applicability_rules": own["applicability_rules"],
        "ownership": own["ownership"],
    }
    profile = LanguageProfile.model_validate_json(json.dumps(document))
    _reject_presence_scoring(profile.rule_mappings)
    return JavaProfile(profile)


class JavaProfile:
    """The Java profile object the supervisor publishes as ``language_profile``."""

    def __init__(self, profile: LanguageProfile) -> None:
        self.profile = profile
        self._rules = {rule.rule_id: rule for rule in profile.applicability_rules}

    def resolve(self, check_id: str) -> RuleMapping | None:
        for mapping in self.profile.rule_mappings:
            if mapping.matches(check_id):
                return mapping
        return None

    def key_for(self, check_id: str, path: str, line: int, column: int | None = None) -> str:
        """Canonical issue key: cross-tool equivalence comes from reviewed mappings only."""
        mapping = self.resolve(check_id)
        family = (mapping.equivalence_family if mapping else None) or check_id.rsplit(".", 1)[-1]
        precise = column if family in COLUMN_KEYED_FAMILIES else None
        return issue_key(family, path, line, precise)

    def owner(self, check_id: str):  # type: ignore[no-untyped-def]
        mapping = self.resolve(check_id)
        return mapping.owner if mapping else None

    def is_token_only(self, check_id: str) -> bool:
        """True for a lint that fires on a bare token rather than on a proven defect."""
        mapping = self.resolve(check_id)
        return mapping is not None and mapping.context_evaluator == TOKEN_ONLY

    # ------------------------------------------------------------------ normalisation

    def normalize(self, observations: Sequence[Observation]) -> list[Observation]:
        """Merge duplicate reports of one canonical issue and apply the context rule.

        Within one issue key:

        * a token-only lint never counts on its own. A scanner verdict at the same site decides
          (violation counts; benign or hint does not); with no scanner evidence the lint is
          demoted to ``needs_review``;
        * precise lints (an unclosed stream, a SpotBugs ``OBL_UNSATISFIED_OBLIGATION``) are
          measured facts and are not overridden by the scanner's heuristic "benign" verdict;
        * several measured reports of one issue collapse into the strongest one.
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
        context = [o for o in group if o.check_id.startswith(CONTEXT_PREFIX)]
        tokens = [o for o in group if self.is_token_only(o.check_id) and o not in context]
        precise = [o for o in group if o not in context and o not in tokens]
        counted = [o for o in (*context, *precise) if o.status == MeasurementStatus.MEASURED]
        if counted:
            best = max(
                counted,
                key=lambda o: (
                    _SEVERITY_RANK[o.severity],
                    _CONFIDENCE_RANK.get(o.confidence or Confidence.UNREVIEWED, 0),
                    o.check_id,
                ),
            )
            others = sorted({o.check_id for o in group} - {best.check_id})
            note = f" (also reported by {', '.join(others)})" if others else ""
            return best.model_copy(update={"explanation": (best.explanation or "") + note})
        if context:
            chosen = sorted(context, key=lambda o: (o.status.value, o.check_id))[0]
            others = sorted({o.check_id for o in group} - {chosen.check_id})
            note = f" (also reported by {', '.join(others)})" if others else ""
            return chosen.model_copy(update={"explanation": (chosen.explanation or "") + note})
        if precise:
            return sorted(precise, key=lambda o: (o.status.value, o.check_id))[0]
        # Only token-only lints: nothing but the token is known about this site.
        first = sorted(tokens, key=lambda o: o.check_id)[0]
        if first.status != MeasurementStatus.MEASURED:
            return first
        return first.model_copy(
            update={
                "status": MeasurementStatus.NEEDS_REVIEW,
                "value": None,
                "severity": None,
                "confidence": None,
                "explanation": (first.explanation or "")
                + " (token-only lint; no contextual evidence, so it is not counted)",
            }
        )

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
            if obs.check_id.startswith("java.") and obs.check_id.endswith(".scan")
        }
        failed_tools: dict[str, str] = {}
        for tool in required_tools:
            scan = scans.get(f"java.{tool}.scan")
            if scan is None or scan.status == MeasurementStatus.MISSING:
                failed_tools[tool] = f"required scan incomplete: {tool}"
            elif scan.status == MeasurementStatus.NOT_APPLICABLE:
                # Unsupported is not clean: a task that requires the scan cannot be scored from it.
                failed_tools[tool] = f"required scan unsupported: {tool}"
            elif scan.status != MeasurementStatus.MEASURED:
                failed_tools[tool] = f"required scan incomplete: {tool}"
        confirmed = {
            obs.issue_key
            for obs in observations
            if obs.check_id.startswith(CONTEXT_PREFIX)
            and obs.status == MeasurementStatus.MEASURED
            and obs.issue_key is not None
        }
        feeders = self._feeders()
        violations: dict[str, dict[str, set[str]]] = {"diagnostic": {}, "idiom": {}}
        for obs in observations:
            if obs.status != MeasurementStatus.MEASURED or obs.issue_key is None:
                continue
            mapping = self.resolve(obs.check_id)
            if mapping is None:
                continue
            if mapping.context_evaluator == TOKEN_ONLY and obs.issue_key not in confirmed:
                # Holds even when the caller skipped normalize(): a token alone proves nothing.
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
