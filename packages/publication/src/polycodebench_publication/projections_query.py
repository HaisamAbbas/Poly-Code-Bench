"""Typed release content and the read-only query layer over published releases.

A published release carries two things:

* ``projection`` — the strictly allowlisted public aggregate, validated at draft time by
  :func:`~polycodebench_publication.releases.validate_projection`. This is what a release may
  claim about a cohort as a whole.
* ``content`` — the release's own typed rows (model entries, disclosed tasks, scorecards, artifacts,
  methodology). This module defines that contract; the release draft canonicalises it.

The query layer reads both *only* from a release whose state is ``published`` or ``withdrawn``. It
has no database handle, no task repository and no evidence store, so a URL cannot be manipulated
into reading hidden task names, hidden bundles or private evidence (Architecture 14.1).

Nothing here reaches worker tables, and nothing here invents a row: if a release did not declare an
entry, the API reports its absence rather than deriving one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field, model_validator

from polycodebench_publication.aggregation import MetricDefinition, PublicationModel
from polycodebench_publication.projections import (
    ArtifactRef,
    ComparisonResult,
    Coverage,
    DimensionBreakdown,
    Incompatibility,
    LanguageEntryProfile,
    LanguageProfile,
    LeaderboardEntry,
    Methodology,
    ModelProfile,
    PairedDelta,
    PublicMetric,
    PublicScorecard,
    PublicTask,
    ReleaseSummary,
)

Scope = Literal["exploratory", "ranked_eligible"]


class ReleaseLanguageProfile(PublicationModel):
    """Language-specific source rows captured in the immutable release content."""

    kind: Literal["release_language_profile"] = "release_language_profile"
    language_id: str = Field(min_length=1, max_length=64)
    dimensions: tuple[DimensionBreakdown, ...] = ()
    diagnostics: tuple[DimensionBreakdown, ...] = ()
    tool_coverage: tuple[tuple[str, str], ...] = ()


class ReleaseEntry(PublicationModel):
    """One model configuration's published row within a release.

    ``model_config_id`` is the entry identity within the cohort; it is deliberately not part of the
    common cohort digest (T 19.2), which is what makes two entries comparable in the first place.
    """

    kind: Literal["release_entry"] = "release_entry"
    model_config_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    rank: int | None = Field(default=None, ge=1)
    capabilities: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    metrics: tuple[PublicMetric, ...]
    coverage: Coverage
    generation_cost_micros: str | None = None
    latency_ms_p50: int | None = Field(default=None, ge=0)
    latency_ms_p95: int | None = Field(default=None, ge=0)
    dimensions: tuple[DimensionBreakdown, ...] = ()
    language_profiles: tuple[ReleaseLanguageProfile, ...] = ()
    run_mode: str | None = Field(default=None, max_length=120)
    budget_profile_id: str | None = Field(default=None, max_length=120)
    evidence_url: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def cost_is_decimal(self) -> ReleaseEntry:
        if self.generation_cost_micros is not None:
            _non_negative_decimal(self.generation_cost_micros, "generation_cost_micros")
        return self

    @model_validator(mode="after")
    def unique_language_profiles(self) -> ReleaseEntry:
        _require_unique(self.language_profiles, lambda row: row.language_id, "language profile")
        if any(profile.language_id not in self.languages for profile in self.language_profiles):
            raise ValueError("language profile must belong to a declared language")
        return self


class ReleaseContent(PublicationModel):
    """The typed rows a release publishes beside its aggregate projection.

    A reviewer approves exact bytes of this document, so a release cannot be approved with one set
    of rows and published with another.
    """

    kind: Literal["release_content"] = "release_content"
    policy_digest: str = Field(min_length=1, max_length=200)
    formula_version: str = Field(min_length=1, max_length=64)
    entries: tuple[ReleaseEntry, ...] = ()
    disclosed_tasks: tuple[PublicTask, ...] = ()
    scorecards: tuple[PublicScorecard, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    methodology: Methodology | None = None
    metric_definitions: tuple[MetricDefinition, ...] = ()

    @model_validator(mode="after")
    def unique_rows(self) -> ReleaseContent:
        _require_unique(self.entries, lambda e: e.model_config_id, "entry")
        _require_unique(self.disclosed_tasks, lambda t: t.task_id, "task")
        _require_unique(self.scorecards, lambda c: c.scorecard_id, "scorecard")
        _require_unique(self.artifacts, lambda a: a.artifact_id, "artifact")
        return self


def _require_unique(rows: tuple[Any, ...], key: Callable[[Any], str], label: str) -> None:
    values = [key(row) for row in rows]
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate public {label} identity")


def _non_negative_decimal(raw: str, field: str) -> Decimal:
    try:
        number = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"{field} must be a decimal string") from error
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field} must be a finite non-negative decimal string")
    return number


# --------------------------------------------------------------------------- query errors


class PublicApiError(Exception):
    """A query rejected for a typed reason.

    Carries only a taxonomy code and a generic message. It never carries the identity of a resource
    the caller was not allowed to see: a private task and an unknown task raise the same
    ``not_found``.
    """

    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- query layer


def _published(document: Mapping[str, Any]) -> Mapping[str, Any]:
    if document.get("state") not in {"published", "withdrawn"}:
        raise PublicApiError("RELEASE_NOT_READY", "release is not available")
    return document


def _content(document: Mapping[str, Any]) -> ReleaseContent:
    raw = document.get("content")
    if not isinstance(raw, Mapping):
        raise PublicApiError("RELEASE_NOT_READY", "release is not available")
    try:
        return ReleaseContent.model_validate(raw)
    except ValueError as error:
        raise PublicApiError("RELEASE_NOT_READY", "release is not available") from error


def _projection(document: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = document.get("projection")
    return raw if isinstance(raw, Mapping) else {}


def _scope(document: Mapping[str, Any]) -> Scope:
    return (
        "exploratory" if _projection(document).get("scope") == "exploratory" else "ranked_eligible"
    )


def _cohort(document: Mapping[str, Any]) -> str:
    return str(_projection(document).get("cohort_digest", ""))


def _withdrawal(document: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = document.get("withdrawal")
    return raw if isinstance(raw, Mapping) else {}


def _limitations(document: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in _projection(document).get("limitations", ()))


def release_summary(document: Mapping[str, Any]) -> ReleaseSummary:
    """``GET /releases/{id}``: public identity and disclosed limits, never a manifest."""
    doc = _published(document)
    content = _content(doc)
    withdrawal = _withdrawal(doc)
    return ReleaseSummary(
        release_id=str(doc.get("id", "")),
        version=int(doc.get("version", 1)),
        state="withdrawn" if doc.get("state") == "withdrawn" else "published",
        scope=_scope(doc),
        fixture_kind=str(_projection(doc).get("fixture_kind", "unknown")),
        cohort_digest=_cohort(doc),
        limitations=_limitations(doc),
        withdrawal_reason=str(withdrawal.get("reason", ""))[:400] or None,
        replacement_release_id=str(withdrawal.get("replacement_id", ""))[:120] or None,
        methodology_url=(
            f"/v1/methodology/{content.methodology.version}"
            if content.methodology is not None
            else "/v1/methodology/v1"
        ),
    )


def _entry_to_leaderboard(entry: ReleaseEntry, scope: Scope) -> LeaderboardEntry:
    return LeaderboardEntry(
        model_config_id=entry.model_config_id,
        label=entry.label,
        rank=None if scope == "exploratory" else entry.rank,
        ranking_label=scope,
        metrics=entry.metrics,
        coverage=entry.coverage,
        languages=entry.languages,
        run_mode=entry.run_mode,
        budget_profile_id=entry.budget_profile_id,
        generation_cost_micros=entry.generation_cost_micros,
        latency_ms_p50=entry.latency_ms_p50,
        latency_ms_p95=entry.latency_ms_p95,
        evidence_url=entry.evidence_url,
    )


def leaderboard(
    document: Mapping[str, Any],
    *,
    languages: frozenset[str] | None = None,
) -> tuple[LeaderboardEntry, ...]:
    """``GET /leaderboard``: published entries for one release.

    Language filtering happens here rather than in a query engine, because the only readable source
    is the approved release document. An entry missing a required language stays out of a board that
    requires it and is never renormalised into a full rank (E2E-28).
    """
    doc = _published(document)
    scope = _scope(doc)
    rows = [
        _entry_to_leaderboard(entry, scope)
        for entry in _content(doc).entries
        if not languages or bool(languages & frozenset(entry.languages))
    ]
    return tuple(rows)


def model_profile(document: Mapping[str, Any], model_config_id: str) -> ModelProfile:
    """``GET /models/{id}``: one entry's published profile.

    An identifier not in this release raises the same not-found a private identifier gets.
    """
    doc = _published(document)
    content = _content(doc)
    for entry in content.entries:
        if entry.model_config_id == model_config_id:
            return ModelProfile(
                model_config_id=entry.model_config_id,
                label=entry.label,
                release_id=str(doc.get("id", "")),
                capabilities=entry.capabilities,
                dimensions=entry.dimensions,
                language_profiles=tuple(
                    LanguageEntryProfile(
                        language_id=profile.language_id,
                        model_config_id=entry.model_config_id,
                        label=entry.label,
                        dimensions=profile.dimensions,
                        diagnostics=profile.diagnostics,
                        tool_coverage=profile.tool_coverage,
                        evidence_url=entry.evidence_url,
                    )
                    for profile in entry.language_profiles
                ),
                languages=entry.languages,
                run_mode=entry.run_mode,
                budget_profile_id=entry.budget_profile_id,
                generation_cost_micros=entry.generation_cost_micros,
                latency_ms_p50=entry.latency_ms_p50,
                latency_ms_p95=entry.latency_ms_p95,
                metrics=entry.metrics,
                coverage=entry.coverage,
                evidence_url=entry.evidence_url,
            )
    raise PublicApiError("NOT_FOUND", "resource is not available")


def language_profile(document: Mapping[str, Any], language_id: str) -> LanguageProfile:
    """``GET /languages/{id}``: dimension breakdown for one language.

    Built from the entries that actually declared the language, aggregating each declared
    dimension's measured opportunity counts. A language no entry ran is not-found rather than an
    empty board, which could read as "scored zero".
    """
    doc = _published(document)
    matching = [e for e in _content(doc).entries if language_id in e.languages]
    if not matching:
        raise PublicApiError("NOT_FOUND", "resource is not available")

    profiles = tuple(
        LanguageEntryProfile(
            language_id=language_id,
            model_config_id=entry.model_config_id,
            label=entry.label,
            dimensions=next(
                (
                    profile.dimensions
                    for profile in entry.language_profiles
                    if profile.language_id == language_id
                ),
                (),
            ),
            diagnostics=next(
                (
                    profile.diagnostics
                    for profile in entry.language_profiles
                    if profile.language_id == language_id
                ),
                (),
            ),
            tool_coverage=next(
                (
                    profile.tool_coverage
                    for profile in entry.language_profiles
                    if profile.language_id == language_id
                ),
                (),
            ),
            evidence_url=entry.evidence_url,
        )
        for entry in matching
    )
    return LanguageProfile(
        language_id=language_id,
        release_id=str(doc.get("id", "")),
        # Do not combine scores from separate model configurations or copy global dimensions
        # into a language with no language-specific observations.
        dimensions=(),
        diagnostics=(),
        metrics=(),
        # Coverage stays attached to each model profile; combining unlike configurations would
        # turn the language page into a synthetic cohort that the release never defined.
        coverage=None,
        entries=profiles,
    )


def compare(document: Mapping[str, Any], model_config_ids: tuple[str, ...]) -> ComparisonResult:
    """``GET /compare``: common-cohort comparison for 2–4 entries, or typed incompatibility.

    When any requested entry is absent or does not cover the same languages this returns
    incompatibilities and *no numbers at all*, so a partial entry can never be presented as a full
    rank (E2E-28).
    """
    if not 2 <= len(model_config_ids) <= 4:
        raise PublicApiError("INCOMPATIBLE_COHORT", "comparison requires two to four entries")

    doc = _published(document)
    content = _content(doc)
    scope = _scope(doc)
    cohort_digest = _cohort(doc)
    by_id = {entry.model_config_id: entry for entry in content.entries}

    reasons: list[Incompatibility] = [
        Incompatibility(
            code="insufficient_common_coverage",
            model_config_id=missing,
            detail="entry is not published in this release",
        )
        for missing in model_config_ids
        if missing not in by_id
    ]
    selected = [by_id[i] for i in model_config_ids if i in by_id]

    if not reasons and len(selected) >= 2:
        reasons.extend(_language_gaps(selected))
        common_tasks = min(e.coverage.tasks for e in selected)
        common_clusters = min(e.coverage.independent_clusters for e in selected)
        if common_tasks == 0 or common_clusters == 0:
            reasons.append(
                Incompatibility(
                    code="insufficient_common_coverage",
                    detail="entries share no common task set",
                )
            )

    if reasons:
        return ComparisonResult(
            release_id=str(doc.get("id", "")),
            cohort_digest=cohort_digest,
            scope=scope,
            common_tasks=0,
            common_independent_clusters=0,
            entries=(),
            deltas=(),
            incompatibilities=tuple(reasons),
            limitations=_limitations(doc),
        )
    return ComparisonResult(
        release_id=str(doc.get("id", "")),
        cohort_digest=cohort_digest,
        scope=scope,
        common_tasks=min(e.coverage.tasks for e in selected),
        common_independent_clusters=min(e.coverage.independent_clusters for e in selected),
        entries=tuple(_entry_to_leaderboard(e, scope) for e in selected),
        deltas=_deltas(tuple(selected)),
        incompatibilities=(),
        limitations=_limitations(doc),
    )


def _language_gaps(selected: list[ReleaseEntry]) -> list[Incompatibility]:
    gaps: list[Incompatibility] = []
    shared = frozenset(selected[0].languages)
    for entry in selected[1:]:
        if shared - frozenset(entry.languages):
            gaps.append(
                Incompatibility(
                    code="missing_language",
                    model_config_id=entry.model_config_id,
                    detail="entry does not cover every language of the comparison",
                )
            )
            shared &= frozenset(entry.languages)
    return gaps


def _deltas(entries: tuple[ReleaseEntry, ...]) -> tuple[PairedDelta, ...]:
    """Paired differences against the first selected entry, on the common cohort."""
    if len(entries) < 2:
        return ()
    baseline = entries[0]
    baseline_metrics = {metric.metric_id: metric for metric in baseline.metrics}
    deltas: list[PairedDelta] = []
    for candidate in entries[1:]:
        for metric in candidate.metrics:
            reference = baseline_metrics.get(metric.metric_id)
            if reference is None or reference.value is None or metric.value is None:
                deltas.append(
                    PairedDelta(
                        metric_id=metric.metric_id,
                        label=metric.label,
                        baseline_model_config_id=baseline.model_config_id,
                        candidate_model_config_id=candidate.model_config_id,
                        delta_value=None,
                        interval_low=None,
                        interval_high=None,
                        status="insufficient_information",
                        reason="at least one entry did not measure this metric",
                    )
                )
                continue
            difference = Decimal(metric.value) - Decimal(reference.value)
            deltas.append(
                PairedDelta(
                    metric_id=metric.metric_id,
                    label=metric.label,
                    baseline_model_config_id=baseline.model_config_id,
                    candidate_model_config_id=candidate.model_config_id,
                    delta_value=format(difference.quantize(Decimal("0.000001")), "f"),
                    interval_low=None,
                    interval_high=None,
                    status="measured",
                    reason=None,
                )
            )
    return tuple(deltas)


def public_task(document: Mapping[str, Any], task_id: str) -> PublicTask:
    """``GET /tasks/{id}``: a disclosed task, or a generic not-found.

    The message is identical whether the identifier is unknown or names a private task that exists,
    so probing cannot distinguish the two.
    """
    doc = _published(document)
    for task in _content(doc).disclosed_tasks:
        if task.task_id == task_id:
            return task
    raise PublicApiError("NOT_FOUND", "resource is not available")


def scorecard(document: Mapping[str, Any], scorecard_id: str) -> PublicScorecard:
    """``GET /scorecards/{id}``: one published score breakdown."""
    doc = _published(document)
    for card in _content(doc).scorecards:
        if card.scorecard_id == scorecard_id:
            return card
    raise PublicApiError("NOT_FOUND", "resource is not available")


def artifact(document: Mapping[str, Any], artifact_id: str) -> ArtifactRef:
    """``GET /artifacts/{id}``: a controlled public download.

    Only artifacts a release actually published are resolvable. A private or hidden artifact
    resolves to not-found, so no probe can obtain a usable download token for one.
    """
    doc = _published(document)
    for item in _content(doc).artifacts:
        if item.artifact_id == artifact_id:
            return item
    raise PublicApiError("NOT_FOUND", "resource is not available")


def methodology(document: Mapping[str, Any]) -> Methodology:
    """``GET /methodology/{version}``: the release's frozen methods."""
    doc = _published(document)
    content = _content(doc)
    if content.methodology is None:
        raise PublicApiError("NOT_FOUND", "resource is not available")
    return content.methodology


__all__ = [
    "PublicApiError",
    "ReleaseContent",
    "ReleaseEntry",
    "artifact",
    "compare",
    "language_profile",
    "leaderboard",
    "methodology",
    "model_profile",
    "public_task",
    "release_summary",
    "scorecard",
]
