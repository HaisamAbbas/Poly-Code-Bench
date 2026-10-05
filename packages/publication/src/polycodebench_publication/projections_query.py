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
from urllib.parse import quote

from pydantic import Field, model_validator

from polycodebench_publication.aggregation import MetricDefinition, PublicationModel
from polycodebench_publication.projections import (
    ArtifactRef,
    ComparisonFilters,
    ComparisonResult,
    ComparisonScorecardRef,
    ComparisonTaskRef,
    Coverage,
    DimensionBreakdown,
    Incompatibility,
    LanguageEntryProfile,
    LanguageProfile,
    LeaderboardEntry,
    Methodology,
    ModelProfile,
    PairedDelta,
    PairedTaskDelta,
    PublicMetric,
    PublicScorecard,
    PublicTask,
    PublicTaskContent,
    ReleaseSummary,
    TaskSummary,
)

Scope = Literal["exploratory", "ranked_eligible"]


class ReleaseLanguageProfile(PublicationModel):
    """Language-specific source rows captured in the immutable release content.

    ``evidence_url`` is optional only for older signed release documents. Query methods suppress
    such profiles rather than attaching the entry's release-wide scorecard to language values.
    """

    kind: Literal["release_language_profile"] = "release_language_profile"
    language_id: str = Field(min_length=1, max_length=64)
    evidence_url: str | None = Field(default=None, min_length=1, max_length=200)
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
    task_contents: tuple[PublicTaskContent, ...] = ()
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
        _require_unique(self.task_contents, lambda row: row.task_id, "task content")
        _require_unique(
            self.scorecards,
            lambda row: f"{row.model_config_id}|{row.task_id}|{row.task_version}",
            "model task scorecard",
        )

        tasks = {row.task_id: row for row in self.disclosed_tasks}
        if any(not task.disclosed for task in self.disclosed_tasks):
            raise ValueError("release disclosed task rows cannot include a private task")
        entries = {row.model_config_id for row in self.entries}
        cards = {row.scorecard_id: row for row in self.scorecards}
        cards_by_evidence_url: dict[str, list[PublicScorecard]] = {}
        for scorecard_row in self.scorecards:
            cards_by_evidence_url.setdefault(scorecard_row.evidence_url, []).append(scorecard_row)
        for entry in self.entries:
            for profile in entry.language_profiles:
                if profile.evidence_url is None:
                    continue
                matching_cards = cards_by_evidence_url.get(profile.evidence_url, [])
                if len(matching_cards) != 1:
                    raise ValueError(
                        "language profile evidence must resolve to one public scorecard"
                    )
                language_card = matching_cards[0]
                task = tasks.get(language_card.task_id)
                if (
                    language_card.model_config_id != entry.model_config_id
                    or task is None
                    or task.language_id != profile.language_id
                ):
                    raise ValueError(
                        "language profile evidence must belong to the same model and language"
                    )
        for task_content in self.task_contents:
            task = tasks.get(task_content.task_id)
            if task is None or task.version != task_content.task_version:
                raise ValueError("public task content must match a disclosed task version")
            payload_size = len(task_content.statement.encode("utf-8"))
            payload_size += sum(
                len(row.source_text.encode("utf-8")) for row in task_content.source_versions
            )
            payload_size += sum(
                len(row.diff_text.encode("utf-8")) for row in task_content.submitted_patches
            )
            payload_size += sum(
                len(row.message.encode("utf-8")) for row in task_content.tool_findings
            )
            if payload_size > 512_000:
                raise ValueError("one public task content payload cannot exceed 512000 UTF-8 bytes")
            source_ids = {row.source_id for row in task_content.source_versions}
            for patch in task_content.submitted_patches:
                card = cards.get(patch.scorecard_id)
                if (
                    patch.model_config_id not in entries
                    or card is None
                    or card.task_id != task.task_id
                    or card.task_version != task.version
                    or card.model_config_id != patch.model_config_id
                ):
                    raise ValueError("public patches must be tied to a public task scorecard")
            for finding in task_content.tool_findings:
                card = cards.get(finding.scorecard_id)
                if (
                    finding.model_config_id not in entries
                    or finding.source_id not in source_ids
                    or card is None
                    or card.task_id != task.task_id
                    or card.task_version != task.version
                    or card.model_config_id != finding.model_config_id
                ):
                    raise ValueError(
                        "public findings must refer to released sources and scorecards"
                    )
        for card in self.scorecards:
            if card.model_config_id not in entries:
                raise ValueError("public scorecards must belong to a released configuration")
            if card.task_id not in tasks or tasks[card.task_id].version != card.task_version:
                raise ValueError("public scorecards must belong to a disclosed task version")
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
    release_query_id = quote(str(doc.get("id", "")), safe="")
    methodology_version = content.methodology.version if content.methodology is not None else "v1"
    methodology_path = quote(methodology_version, safe="")
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
        methodology_url=f"/v1/methodology/{methodology_path}?release={release_query_id}",
        methodology_version=methodology_version,
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


def _language_entry_profile(
    entry: ReleaseEntry, profile: ReleaseLanguageProfile
) -> LanguageEntryProfile:
    if profile.evidence_url is None:
        raise ValueError("a public language profile requires language-matched evidence")
    return LanguageEntryProfile(
        language_id=profile.language_id,
        model_config_id=entry.model_config_id,
        label=entry.label,
        dimensions=profile.dimensions,
        diagnostics=profile.diagnostics,
        tool_coverage=profile.tool_coverage,
        evidence_url=profile.evidence_url,
    )


def leaderboard(
    document: Mapping[str, Any],
    *,
    languages: frozenset[str] | None = None,
) -> tuple[LeaderboardEntry, ...]:
    """``GET /leaderboard``: published entries for one release.

    Language filtering happens here rather than in a query engine, because the only readable source
    is the approved release document. It filters configurations by declared language support; the
    returned metrics and coverage remain release-wide. Use ``/languages/{id}`` for language-scoped
    dimensions and diagnostics. An entry missing a required language stays out of the filtered board
    and is never renormalised into a full rank (E2E-28).
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
                    _language_entry_profile(entry, profile)
                    for profile in entry.language_profiles
                    if profile.evidence_url is not None
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

    Built only from language-specific profiles declared in the release. A model's release-wide
    metrics are never copied into this view. A language with no published profile is not-found
    rather than an empty board, which could read as "scored zero".
    """
    doc = _published(document)
    matching = [
        (entry, profile)
        for entry in _content(doc).entries
        for profile in entry.language_profiles
        if profile.language_id == language_id and profile.evidence_url is not None
    ]
    if not matching:
        raise PublicApiError("NOT_FOUND", "resource is not available")

    profiles = tuple(_language_entry_profile(entry, profile) for entry, profile in matching)
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


def compare(
    document: Mapping[str, Any],
    model_config_ids: tuple[str, ...],
    *,
    languages: frozenset[str] = frozenset(),
    families: frozenset[str] = frozenset(),
    difficulties: frozenset[str] = frozenset(),
) -> ComparisonResult:
    """``GET /compare``: common-cohort comparison for 2–4 entries, or typed incompatibility.

    Compatibility and paired rows are resolved only from this immutable release's declared
    entries, disclosed task versions, and scorecards. Coverage counts are never used as a proxy for
    a task intersection. Any incompatible request returns reasons and no numeric rows.
    Entry metrics and ``deltas`` are published release aggregates; the filters apply to the exact
    common task references and ``paired_task_deltas`` only.
    """
    if not 2 <= len(model_config_ids) <= 4:
        raise PublicApiError("INCOMPATIBLE_COHORT", "comparison requires two to four entries")
    if len(set(model_config_ids)) != len(model_config_ids):
        raise PublicApiError("INCOMPATIBLE_COHORT", "comparison requires distinct configurations")

    doc = _published(document)
    content = _content(doc)
    scope = _scope(doc)
    cohort_digest = _cohort(doc)
    applied_filters = ComparisonFilters(
        languages=tuple(sorted(languages)),
        families=tuple(sorted(families)),
        difficulties=tuple(sorted(difficulties)),
    )
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
        if (
            any(entry.run_mode is None for entry in selected)
            or len({entry.run_mode for entry in selected}) != 1
        ):
            reasons.append(
                Incompatibility(
                    code="protocol_mismatch",
                    detail="run protocol is missing or differs across selected configurations",
                )
            )
        if (
            any(entry.budget_profile_id is None for entry in selected)
            or len({entry.budget_profile_id for entry in selected}) != 1
        ):
            reasons.append(
                Incompatibility(
                    code="budget_mismatch",
                    detail="budget profile is missing or differs across selected configurations",
                )
            )
        if languages:
            if any(not languages <= set(entry.languages) for entry in selected):
                reasons.append(
                    Incompatibility(
                        code="missing_language",
                        detail="a selected configuration does not cover the requested language",
                    )
                )
        elif not set.intersection(*(set(entry.languages) for entry in selected)):
            reasons.append(
                Incompatibility(
                    code="missing_language",
                    detail="selected configurations have no common declared language",
                )
            )

    tasks = [
        task
        for task in content.disclosed_tasks
        if (not languages or task.language_id in languages)
        and (not families or task.family in families)
        and (not difficulties or task.difficulty in difficulties)
    ]
    cards_by_model_and_task = {
        (card.model_config_id, card.task_id): card
        for card in content.scorecards
        if card.model_config_id in model_config_ids
    }
    task_rows = tuple(
        ComparisonTaskRef(
            task_id=task.task_id,
            task_version=task.version,
            language_id=task.language_id,
            family=task.family,
            difficulty=task.difficulty,
            scorecards=tuple(
                ComparisonScorecardRef(
                    model_config_id=model_id,
                    scorecard_id=cards_by_model_and_task[(model_id, task.task_id)].scorecard_id,
                    evidence_url=cards_by_model_and_task[(model_id, task.task_id)].evidence_url,
                )
                for model_id in model_config_ids
                if (model_id, task.task_id) in cards_by_model_and_task
                and cards_by_model_and_task[(model_id, task.task_id)].task_version == task.version
            ),
        )
        for task in tasks
        if all(
            (model_id, task.task_id) in cards_by_model_and_task
            and cards_by_model_and_task[(model_id, task.task_id)].task_version == task.version
            for model_id in model_config_ids
        )
    )
    if not reasons and not task_rows:
        reasons.append(
            Incompatibility(
                code="insufficient_common_coverage",
                detail="selected configurations share no disclosed task scorecards",
            )
        )
    per_scorecard_metrics = [
        {
            metric.metric_id
            for metric in cards_by_model_and_task[(model_id, task_ref.task_id)].metrics
        }
        for task_ref in task_rows
        for model_id in model_config_ids
    ]
    common_metric_ids = set.intersection(*per_scorecard_metrics) if per_scorecard_metrics else set()
    if not reasons and not common_metric_ids:
        reasons.append(
            Incompatibility(
                code="incompatible_metric",
                detail="selected task scorecards publish no common metric",
            )
        )

    if reasons:
        return ComparisonResult(
            release_id=str(doc.get("id", "")),
            cohort_digest=cohort_digest,
            scope=scope,
            applied_filters=applied_filters,
            common_tasks=0,
            common_independent_clusters=0,
            entries=(),
            deltas=(),
            common_task_refs=(),
            paired_task_deltas=(),
            incompatibilities=tuple(reasons),
            limitations=_limitations(doc),
        )
    task_deltas = _paired_task_deltas(
        task_rows, model_config_ids, cards_by_model_and_task, common_metric_ids
    )
    return ComparisonResult(
        release_id=str(doc.get("id", "")),
        cohort_digest=cohort_digest,
        scope=scope,
        applied_filters=applied_filters,
        common_tasks=len(task_rows),
        common_independent_clusters=None,
        entries=tuple(_entry_to_leaderboard(e, scope) for e in selected),
        deltas=_deltas(tuple(selected)),
        common_task_refs=task_rows,
        paired_task_deltas=task_deltas,
        incompatibilities=(),
        limitations=_limitations(doc),
    )


def _deltas(entries: tuple[ReleaseEntry, ...]) -> tuple[PairedDelta, ...]:
    """Differences between release aggregate metrics against the first selected entry."""
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
                        interval_method="unavailable",
                        status="insufficient_information",
                        reason="at least one entry did not measure this metric",
                    )
                )
                continue
            difference = Decimal(metric.value) - Decimal(reference.value)
            interval_low = None
            interval_high = None
            interval_method = "unavailable"
            if all(
                value is not None
                for value in (
                    reference.interval_low,
                    reference.interval_high,
                    metric.interval_low,
                    metric.interval_high,
                )
            ):
                interval_low = format(
                    Decimal(metric.interval_low) - Decimal(reference.interval_high), ".6f"
                )
                interval_high = format(
                    Decimal(metric.interval_high) - Decimal(reference.interval_low), ".6f"
                )
                interval_method = "reported_interval_difference_bounds"
            deltas.append(
                PairedDelta(
                    metric_id=metric.metric_id,
                    label=metric.label,
                    baseline_model_config_id=baseline.model_config_id,
                    candidate_model_config_id=candidate.model_config_id,
                    delta_value=format(difference.quantize(Decimal("0.000001")), "f"),
                    interval_low=interval_low,
                    interval_high=interval_high,
                    interval_method=interval_method,
                    status="measured",
                    reason=None,
                )
            )
    return tuple(deltas)


def _paired_task_deltas(
    task_refs: tuple[ComparisonTaskRef, ...],
    model_config_ids: tuple[str, ...],
    cards: Mapping[tuple[str, str], PublicScorecard],
    metric_ids: set[str],
) -> tuple[PairedTaskDelta, ...]:
    if len(model_config_ids) < 2:
        return ()
    baseline_id = model_config_ids[0]
    deltas: list[PairedTaskDelta] = []
    for task_ref in task_refs:
        baseline = cards[(baseline_id, task_ref.task_id)]
        baseline_metrics = {row.metric_id: row for row in baseline.metrics}
        for candidate_id in model_config_ids[1:]:
            candidate = cards[(candidate_id, task_ref.task_id)]
            candidate_metrics = {row.metric_id: row for row in candidate.metrics}
            for metric_id in sorted(metric_ids):
                left = baseline_metrics[metric_id]
                right = candidate_metrics[metric_id]
                measured = (
                    left.status == "measured"
                    and right.status == "measured"
                    and left.value is not None
                    and right.value is not None
                )
                delta_value = (
                    format(Decimal(right.value) - Decimal(left.value), ".6f") if measured else None
                )
                interval_low = None
                interval_high = None
                interval_method = "unavailable"
                if measured and all(
                    value is not None
                    for value in (
                        left.interval_low,
                        left.interval_high,
                        right.interval_low,
                        right.interval_high,
                    )
                ):
                    interval_low = format(
                        Decimal(right.interval_low) - Decimal(left.interval_high), ".6f"
                    )
                    interval_high = format(
                        Decimal(right.interval_high) - Decimal(left.interval_low), ".6f"
                    )
                    interval_method = "reported_interval_difference_bounds"
                deltas.append(
                    PairedTaskDelta(
                        task_id=task_ref.task_id,
                        task_version=task_ref.task_version,
                        metric_id=metric_id,
                        label=right.label,
                        baseline_model_config_id=baseline_id,
                        candidate_model_config_id=candidate_id,
                        baseline_scorecard_id=baseline.scorecard_id,
                        candidate_scorecard_id=candidate.scorecard_id,
                        baseline_value=left.value,
                        candidate_value=right.value,
                        delta_value=delta_value,
                        interval_low=interval_low,
                        interval_high=interval_high,
                        interval_method=interval_method,
                        status="measured" if measured else "insufficient_information",
                        reason=None
                        if measured
                        else "at least one task scorecard did not measure this metric",
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
        if task.task_id == task_id and task.disclosed:
            return task
    raise PublicApiError("NOT_FOUND", "resource is not available")


def task_summary(document: Mapping[str, Any], task_id: str) -> TaskSummary:
    """Return bounded task metadata without copying source, diff, or finding payloads."""
    task = public_task(document, task_id)
    content = _content(document)
    detail = next((row for row in content.task_contents if row.task_id == task_id), None)
    return _task_summary(task, detail)


def task_summaries(document: Mapping[str, Any]) -> tuple[TaskSummary, ...]:
    """Project one bounded summary per disclosed task after parsing release content once."""
    doc = _published(document)
    content = _content(doc)
    details = {row.task_id: row for row in content.task_contents}
    return tuple(
        _task_summary(task, details.get(task.task_id))
        for task in content.disclosed_tasks
        if task.disclosed
    )


def _task_summary(task: PublicTask, detail: PublicTaskContent | None) -> TaskSummary:
    return TaskSummary(
        task_id=task.task_id,
        version=task.version,
        language_id=task.language_id,
        family=task.family,
        difficulty=task.difficulty,
        statement_summary=task.statement_summary,
        evidence_url=task.evidence_url,
        source_version_count=len(detail.source_versions) if detail else 0,
        patch_count=len(detail.submitted_patches) if detail else 0,
        finding_count=len(detail.tool_findings) if detail else 0,
    )


def task_content(document: Mapping[str, Any], task_id: str) -> PublicTaskContent:
    """Load curated detail for an already disclosed task; private IDs stay generic not-found."""
    task = public_task(document, task_id)
    content = _content(document)
    detail = next((row for row in content.task_contents if row.task_id == task_id), None)
    if detail is not None and detail.task_version == task.version:
        return detail
    return PublicTaskContent(
        task_id=task.task_id,
        task_version=task.version,
        statement=task.statement_summary,
        source_versions=(),
        submitted_patches=(),
        tool_findings=(),
    )


def scorecard(document: Mapping[str, Any], scorecard_id: str) -> PublicScorecard:
    """``GET /scorecards/{id}``: one published score breakdown."""
    doc = _published(document)
    content = _content(doc)
    for card in content.scorecards:
        if card.scorecard_id == scorecard_id:
            task_detail = next(
                (
                    row
                    for row in content.task_contents
                    if row.task_id == card.task_id and row.task_version == card.task_version
                ),
                None,
            )
            public_ids = (
                {source.source_id for source in task_detail.source_versions}
                | {patch.patch_id for patch in task_detail.submitted_patches}
                | {finding.finding_id for finding in task_detail.tool_findings}
                if task_detail is not None
                else set()
            )
            redacted = 0
            contributions = []
            for row in card.contributions:
                visible = tuple(ref for ref in row.evidence_refs if ref in public_ids)
                redacted += len(row.evidence_refs) - len(visible)
                contributions.append(row.model_copy(update={"evidence_refs": visible}))
            return card.model_copy(
                update={"contributions": tuple(contributions), "redacted_evidence_count": redacted}
            )
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
    "task_content",
    "release_summary",
    "scorecard",
    "task_summary",
]
