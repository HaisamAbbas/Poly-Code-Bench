"""Go diagnostic and orthogonal idiom profiles: loading, normalisation and evaluation.

Rules this module enforces by construction:

* weights come from ``config/languages/profiles-v1.yaml`` and are cross-checked, never re-typed;
* an item without a frozen task opportunity is ``not_applicable`` - never a perfect score. That is
  what keeps a nonconcurrent task from being charged for not using channels;
* counts are per *canonical issue* (one key however many tools report it), so a duplicate report
  cannot add a penalty, and constructs that merely appear in the code never earn credit;
* a discarded error is a token, not a finding. gosec's G104 fires on the unhandled *result*; it
  counts only when the context scanner independently confirms at the same site that the call
  returns an error and discards it. With no scanner evidence the tool report is demoted to
  ``needs_review`` and never counted;
* lifecycle and cancellation findings carry a concrete composite owner: a replaced context, a
  context that is never cancelled, an unjoined goroutine and a measured data race are
  ``robustness``, not style;
* evidence from a required scan that is missing, incomplete or unsupported makes the dependent
  items ``missing``; it is never read as "no violations". A race run that the toolchain could not
  perform is a third state, distinct from a clean run and from a detected race.
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
    RuleMapping,
)
from pydantic import Field

from polycodebench_lang_go.observations import COLUMN_KEYED_FAMILIES, issue_key

PROFILE_VERSION = "go-profile-v1"
CONTEXT_PREFIX = "go.context."
TOKEN_ONLY = "context-required"

_DIAGNOSTIC_DESCRIPTIONS = {
    "error_handling": "Errors returned, wrapped and checked rather than discarded",
    "goroutines_channels": "Goroutines and channels with an observable lifecycle",
    "cancellation_context": "Cancellation propagated and honoured through context.Context",
    "simple_interfaces": "Small interfaces at the point of use",
    "standard_library": "Standard-library composition instead of hand-rolled equivalents",
    "formatting_vet_staticcheck": "gofmt, go vet and selected staticcheck hygiene",
}
_IDIOM_DESCRIPTIONS = {
    "simple_interfaces_api": "Interface shape in API design",
    "standard_library_composition": "Standard-library composition",
    "error_api": "Error API design",
    "context_concurrency": "Context and concurrency abstraction design",
}
_CONFIDENCE_RANK = {
    Confidence.UNREVIEWED: 0,
    Confidence.LOW: 1,
    Confidence.MEDIUM: 2,
    Confidence.HIGH: 3,
    Confidence.CONFIRMED: 4,
}
_SEVERITY_RANK = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


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


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "languages" / "profiles-v1.yaml").is_file():
            return parent
    raise FileNotFoundError("config/languages/profiles-v1.yaml")


@lru_cache(maxsize=2)
def load_profile(root: str | None = None) -> GoProfile:
    base = Path(root) if root else _repo_root()
    weights = yaml.safe_load((base / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    own = yaml.safe_load((base / "config/languages/go-profile-v1.yaml").read_text("utf-8"))
    go = weights["profiles"]["go"]
    document = {
        "language_id": "go",
        "profile_version": own["profile_version"],
        "effective_for_scoring": bool(own["effective_for_scoring"]),
        "diagnostic_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _DIAGNOSTIC_DESCRIPTIONS[name],
            }
            for name, percent in go["diagnostic_percent"].items()
        ],
        "idiom_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _IDIOM_DESCRIPTIONS[name],
            }
            for name, percent in go["idiomatic_percent"].items()
        ],
        "rule_mappings": own["rule_mappings"],
        "applicability_rules": own["applicability_rules"],
        "ownership": own["ownership"],
    }
    profile = LanguageProfile.model_validate_json(json.dumps(document))
    return GoProfile(profile)


class GoProfile:
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
        """True for a tool report that fires on a bare token the scanner must confirm."""
        mapping = self.resolve(check_id)
        return mapping is not None and mapping.context_evaluator == TOKEN_ONLY

    # ------------------------------------------------------------------ normalisation

    def normalize(self, observations: Sequence[Observation]) -> list[Observation]:
        """Merge duplicate reports of one canonical issue and apply the context rule.

        Within one issue key:

        * a token-only tool report never counts on its own. A scanner verdict at the same site
          decides (violation counts; benign or hint does not); with no scanner evidence the tool
          report is demoted to ``needs_review``;
        * precise reports (a broken error chain, a measured data race) are measured facts and are
          not overridden by the scanner's heuristic "benign" verdict;
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
        # Only token-only tool reports: nothing but the token is known about this site.
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
                + " (token-only report; no contextual evidence, so it is not counted)",
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
            if obs.check_id.startswith("go.") and obs.check_id.endswith(".scan")
        }
        failed_tools: dict[str, str] = {}
        for tool in required_tools:
            scan = scans.get(f"go.{tool}.scan")
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