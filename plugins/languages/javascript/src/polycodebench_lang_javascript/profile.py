"""JavaScript/TypeScript diagnostic and orthogonal idiom profiles (Prompt 19, PCB-19-3).

One class serves both identities. The language is the only difference: it selects the profile
document, the check-id prefix and the scan namespace, so the applicability, normalisation and
evaluation rules below cannot drift apart between the two.

Rules this module enforces by construction:

* weights come from ``config/languages/profiles-v1.yaml`` and are cross-checked, never re-typed;
* an item without a frozen task opportunity is ``not_applicable`` - never a perfect score. That is
  what makes an unused async or concurrency opportunity N/A rather than a deduction (E2E-35);
* counts are per *canonical issue* (one key however many tools report it), so a duplicate report
  cannot add a penalty, and constructs that merely appear in the code never earn credit - modern
  syntax is not a bonus;
* some ESLint rules fire on a bare construct rather than on a defect. ``var`` is the canonical
  example: the token is only wrong because of function scoping, so the real defect (redeclaration,
  shadowing, a leak past its block) is what the context scanner reports. A mapping whose
  ``context_evaluator`` is ``context-required`` counts only when the scanner independently confirms
  a violation at the same site; with no scanner evidence the lint is demoted to ``needs_review``,
  and a site the scanner calls benign is never penalised;
* evidence from a required scan that is missing, incomplete or unsupported makes the dependent
  items ``missing``; it is never read as "no violations" (D-10-06);
* the composite owner of a canonical issue comes from the mapping, so one defect can never lower
  both security and idioms (Technical Spec 14.5).
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

from polycodebench_lang_javascript.observations import COLUMN_KEYED_FAMILIES, IssueKeys, slug

PROFILE_VERSIONS = {"javascript": "javascript-profile-v1", "typescript": "typescript-profile-v1"}
PROFILE_FILES = {language: f"config/languages/{language}-profile-v1.yaml" for language in PROFILE_VERSIONS}
TOKEN_ONLY = "context-required"
CONTEXT_SUFFIX = ".context."

_DIAGNOSTIC_DESCRIPTIONS = {
    "async_correctness": "Promises awaited, handled and composed correctly",
    "modern_immutability": "Const-first, shared state not mutated behind the reader",
    "security": "Untrusted input never reaches a sink unsafely",
    "async_error_handling": "Async failures are handled where they happen",
    "lint": "Lint and style hygiene",
    "type_safety": "Static types that hold under the task's strictness",
}
_IDIOM_DESCRIPTIONS = {
    "async_composition": "Promise composition that reads as the control flow",
    "data_module_api": "Module boundaries that expose data cleanly",
    "language_constructs": "Language constructs used where they earn their place",
    "restrained_mutation": "Mutation where nothing else will do",
    "type_domain_modeling": "Domain types that carry the model's invariants",
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


def profile_version(language: str) -> str:
    """The published profile version of one identity."""
    try:
        return PROFILE_VERSIONS[language]
    except KeyError:
        raise ValueError(f"unknown language {language!r}") from None


def _descriptions(items: Sequence[ProfileItem], known: Mapping[str, str]) -> list[ProfileItem]:
    """Attach the human description each item documents, failing loudly on a new one."""
    missing = [item.item_id for item in items if item.item_id not in known]
    if missing:
        raise ValueError(f"profile items without a description: {', '.join(missing)}")
    return [
        item.model_copy(update={"description": known[str(item.item_id)]})
        for item in items
    ]


@lru_cache(maxsize=4)
def load_profile(language: str, root: str | None = None) -> "JsProfile":
    """Load one identity's profile: weights from the shared registry, rules from its own file."""
    base = Path(root) if root else _repo_root()
    weights = yaml.safe_load((base / "config/languages/profiles-v1.yaml").read_text("utf-8"))
    own = yaml.safe_load((base / PROFILE_FILES[language]).read_text("utf-8"))
    declared = weights["profiles"][language]
    document = {
        "language_id": language,
        "profile_version": own["profile_version"],
        "effective_for_scoring": bool(own["effective_for_scoring"]),
        "diagnostic_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _DIAGNOSTIC_DESCRIPTIONS[name],
            }
            for name, percent in declared["diagnostic_percent"].items()
        ],
        "idiom_items": [
            {
                "item_id": name,
                "weight_bp": int(round(float(percent) * 100)),
                "description": _IDIOM_DESCRIPTIONS[name],
            }
            for name, percent in declared["idiomatic_percent"].items()
        ],
        "rule_mappings": own["rule_mappings"],
        "applicability_rules": own["applicability_rules"],
        "ownership": own["ownership"],
    }
    profile = LanguageProfile.model_validate_json(json.dumps(document))
    return JsProfile(language, profile)


class JsProfile:
    """The profile object a JavaScript or TypeScript plugin publishes."""

    def __init__(self, language: str, profile: LanguageProfile) -> None:
        self.language = language
        self.profile = profile
        self._rules = {rule.rule_id: rule for rule in profile.applicability_rules}
        self._keys = IssueKeys(prefix="js." if language == "javascript" else "ts.")
        self.context_prefix = f"{language}{CONTEXT_SUFFIX}"

    @property
    def profile_version(self) -> str:
        return str(self.profile.profile_version)

    def resolve(self, check_id: str) -> RuleMapping | None:
        """First matching mapping; specific entries come before prefixes in the YAML."""
        for mapping in self.profile.rule_mappings:
            if mapping.matches(check_id):
                return mapping
        return None

    def key_for(self, check_id: str, path: str, line: int, column: int | None = None) -> str:
        """Canonical issue key: cross-tool equivalence comes from reviewed mappings only."""
        mapping = self.resolve(check_id)
        family = (mapping.equivalence_family if mapping else None) or check_id.rsplit(".", 1)[-1]
        precise = column if family in COLUMN_KEYED_FAMILIES else None
        return self._keys.key(family, path, line, precise)

    def owner(self, check_id: str):  # type: ignore[no-untyped-def]
        mapping = self.resolve(check_id)
        return mapping.owner if mapping else None

    def is_token_only(self, check_id: str) -> bool:
        """True for a lint that fires on a bare construct rather than on a defect."""
        mapping = self.resolve(check_id)
        return mapping is not None and mapping.context_evaluator == TOKEN_ONLY

    # ------------------------------------------------------------------ normalisation

    def normalize(self, observations: Sequence[Observation]) -> list[Observation]:
        """Merge duplicate reports of one canonical issue and apply the context rule.

        Within one issue key:
        * a token-only lint never counts on its own. A scanner verdict at the same site decides
          (violation counts; benign or hint does not); with no scanner evidence the lint is
          demoted to ``needs_review``;
        * precise lints are measured facts and are not overridden by the scanner's heuristic
          "benign" verdict;
        * several measured reports of one issue collapse into the strongest one, and the surviving
          observation names every other tool that reported it.
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
        context = [o for o in group if o.check_id.startswith(self.context_prefix)]
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
            return best.model_copy(update={"explanation": _named(best, group)})
        if context:
            chosen = sorted(context, key=lambda o: (o.status.value, o.check_id))[0]
            return chosen.model_copy(update={"explanation": _named(chosen, group)})
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
        scan_prefix = f"{self.language}."
        scans = {
            obs.check_id: obs
            for obs in observations
            if obs.check_id.startswith(scan_prefix) and obs.check_id.endswith(".scan")
        }
        failed_tools: dict[str, str] = {}
        for tool in required_tools:
            scan = scans.get(f"{self.language}.{tool}.scan")
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
            if obs.check_id.startswith(self.context_prefix)
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
            profile_version=str(self.profile.profile_version),
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
    ) -> ProfileItemResult:
        count = int(opportunities.get(item.item_id, 0))
        if count <= 0:
            return ProfileItemResult(
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
            return ProfileItemResult(
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
        return ProfileItemResult(
            item_id=item.item_id,
            group=group,
            weight_bp=item.weight_bp,
            opportunities=count,
            unique_violations=len(keys),
            status="measured",
            score_bp=score,
            issue_keys=tuple(keys),
        )


def _named(best: Observation, group: Sequence[Observation]) -> str:
    """Keep every tool's name on the surviving report of one canonical issue."""
    others = sorted({o.check_id for o in group} - {best.check_id})
    note = f" (also reported by {', '.join(others)})" if others else ""
    return (best.explanation or "") + note


def _weighted(items: Sequence[ProfileItemResult]) -> int | None:
    applicable = [i for i in items if i.status != "not_applicable"]
    if not applicable or any(i.status == "missing" for i in applicable):
        return None
    total = sum(i.weight_bp for i in applicable)
    return (sum(i.weight_bp * (i.score_bp or 0) for i in applicable) + total // 2) // total


def profile_document(profile: LanguageProfile) -> str:
    return json.dumps(profile.model_dump(mode="json"), sort_keys=True)


__all__ = [
    "CONTEXT_SUFFIX",
    "PROFILE_VERSIONS",
    "TOKEN_ONLY",
    "JsProfile",
    "load_profile",
    "profile_document",
    "profile_version",
    "slug",
]