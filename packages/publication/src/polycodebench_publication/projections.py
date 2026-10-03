"""Typed public read projections over published release data.

This module is the single source of truth for what a public response may contain. It reads only
from :class:`~polycodebench_publication.releases.ReleaseStore`'s *published* projections and from
the aggregation layer's aggregate results, and it never reaches worker, attempt, evidence or hidden
task tables (Architecture 14.1: "The public API has read-only access to published projections. It
cannot query hidden tasks even if a URL or filter is manipulated.").

Everything here is framework-free so the API adapter, the generated TypeScript client and the web
frontend all agree on one shape. Three invariants hold throughout:

* **Decimal strings.** Scores are canonical decimal strings (Technical Specification 20.1); the
  frontend parses them only for display and plotting. Nothing here returns a float.
* **Explicit missingness.** A metric that was not measured is ``None`` with a ``status`` and a
  ``reason``; it is never ``0`` (Architecture 14.2: "N/A must not render as zero").
* **No hidden identifiers.** Public task and artifact identities are disclosed identities. A probe
  for a private identifier returns a generic not-found, and no response carries a usable private
  download token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import ClassVar, Literal

from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from pydantic import Field, model_validator

from polycodebench_publication.aggregation import (
    AggregateResult,
    MetricDefinition,
    PublicationModel,
)

# -------- public error taxonomy

ErrorCode = Literal[
    "INVALID_CURSOR",
    "INVALID_FILTER",
    "UNAUTHENTICATED",
    "FORBIDDEN",
    "NOT_FOUND",
    "IDEMPOTENCY_CONFLICT",
    "RESULT_CONFLICT",
    "LEASE_LOST",
    "INCOMPATIBLE_COHORT",
    "RELEASE_NOT_READY",
    "VERSION_CONFLICT",
    "SCHEMA_INVALID",
    "CAPABILITY_UNSUPPORTED",
    "TASK_INVALID",
    "RATE_LIMITED",
    "BUDGET_EXHAUSTED",
    "DEPENDENCY_UNAVAILABLE",
]

#: HTTP status for each taxonomy code. Every public route raises one of these, never a bare 500
#: with a stack trace (Technical Specification 20.6).
ERROR_STATUS: dict[str, int] = {
    "INVALID_CURSOR": 400,
    "INVALID_FILTER": 400,
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "RESULT_CONFLICT": 409,
    "LEASE_LOST": 409,
    "INCOMPATIBLE_COHORT": 409,
    "RELEASE_NOT_READY": 409,
    "VERSION_CONFLICT": 412,
    "SCHEMA_INVALID": 422,
    "CAPABILITY_UNSUPPORTED": 422,
    "TASK_INVALID": 422,
    "RATE_LIMITED": 429,
    "BUDGET_EXHAUSTED": 429,
    "DEPENDENCY_UNAVAILABLE": 503,
}


class PublicError(PublicationModel):
    """The error body of a public or administrative response.

    ``message`` is deliberately generic per code. It must never interpolate a hidden task name, a
    private URL, a stack trace or a credential: a route that rejects an unknown public identifier
    raises the same ``not found`` as an identifier that exists but is private.
    """

    kind: Literal["public_error"] = "public_error"
    code: ErrorCode
    message: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=64)
    details: tuple[str, ...] = ()

    @model_validator(mode="after")
    def no_leak(self) -> PublicError:
        banned = ("/", "\\", "@", "sha256:", "http", "..")
        for fragment in (*self.details, self.message):
            lowered = fragment.lower()
            if any(token in lowered for token in banned):
                raise ValueError("public error detail must not carry paths, digests or URLs")
        return self


# -------- metric values


MetricStatus = Literal[
    "measured",
    "insufficient_information",
    "not_applicable",
    "gated_zero",
    "missing",
    "needs_review",
]


class PublicMetric(PublicationModel):
    """One public metric value with its availability made explicit.

    ``value``/``interval_low``/``interval_high`` are decimal strings or ``None``. A metric with no
    measurement keeps the key present and null rather than being omitted, so the frontend cannot
    mistake "not measured" for "not applicable".
    """

    kind: Literal["public_metric"] = "public_metric"
    metric_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    unit: str = Field(min_length=1, max_length=32)
    direction: Literal["higher", "lower"]
    status: MetricStatus
    value: str | None
    interval_low: str | None
    interval_high: str | None
    coverage: str | None
    conditional_on_pass: bool
    reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def consistent(self) -> PublicMetric:
        for field in ("value", "interval_low", "interval_high", "coverage"):
            raw: str | None = getattr(self, field)
            if raw is not None:
                _ = _finite_decimal(raw, field)
        if (self.interval_low is None) != (self.interval_high is None):
            raise ValueError("a public interval is disclosed in full or not at all")
        if self.interval_low is not None and self.interval_high is not None:
            if Decimal(self.interval_low) > Decimal(self.interval_high):
                raise ValueError("interval bounds are inverted")
        if self.status != "measured" and self.value is not None:
            raise ValueError("only a measured metric may carry a value")
        if self.status == "measured" and self.value is None:
            raise ValueError("a measured metric must carry its value")
        if self.value is None and self.interval_low is not None:
            raise ValueError("an unmeasured metric cannot carry an interval")
        return self


class Coverage(PublicationModel):
    """How much of a cohort an entry actually covers.

    ``tasks`` is the number of tasks in the common cohort, not the number an entry found easy.
    """

    kind: Literal["coverage"] = "coverage"
    tasks: int = Field(ge=0)
    samples: int = Field(ge=0)
    independent_clusters: int = Field(ge=0)


class MetricRegistry(PublicationModel):
    """The metric definitions a response ships alongside its values.

    T 19.1: the API returns these definitions with a version so the frontend cannot implement its
    own scoring formula.
    """

    kind: Literal["metric_registry"] = "metric_registry"
    policy_digest: str = Field(min_length=1, max_length=200)
    definitions: tuple[MetricDefinition, ...] = ()


# -------- paginated envelopes


class PageMeta(PublicationModel):
    """Release-scoped pagination metadata.

    ``next_cursor`` is an opaque signed cursor bound to the release, filters and sort. A cursor for
    a different release or filter set is rejected with ``INVALID_CURSOR``.
    """

    kind: Literal["page_meta"] = "page_meta"
    release_id: str = Field(min_length=1, max_length=120)
    release_digest: str = Field(min_length=1, max_length=200)
    total: int = Field(ge=0)
    returned: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    sort: str = Field(min_length=1, max_length=40)
    filters: tuple[str, ...] = ()
    next_cursor: str | None = None


class Page[RowT: PublicationModel](PublicationModel):
    """A generic ``{data, meta}`` envelope over a concrete row type.

    Generic rather than ``tuple[Any, ...]`` so every endpoint's rows stay typed end to end while
    sharing one cursor policy and one metadata shape.
    """

    kind: Literal["page"] = "page"
    data: tuple[RowT, ...] = ()
    meta: PageMeta


# -------- cursor policy


class Cursor(PublicationModel):
    """The decoded contents of an opaque cursor.

    Signed, not encrypted: the client may read its own position but cannot forge one for another
    release or filter set without the server key.
    """

    kind: Literal["cursor"] = "cursor"
    release_id: str
    filters_digest: str
    sort: str
    offset: int = Field(ge=0)

    def sign(self, key: bytes) -> str:
        body = (
            base64.urlsafe_b64encode(
                f"{self.release_id}|{self.filters_digest}|{self.sort}|{self.offset}".encode()
            )
            .decode()
            .rstrip("=")
        )
        sig = (
            base64.urlsafe_b64encode(hmac.new(key, body.encode(), hashlib.sha256).digest())
            .decode()
            .rstrip("=")
        )
        return f"{body}.{sig}"

    @staticmethod
    def verify(token: str, key: bytes) -> Cursor:
        """Decode and authenticate a cursor token.

        Raises ``ValueError`` on every malformed, truncated or forged token; the API layer turns
        that into a single ``INVALID_CURSOR`` response that says nothing about which part was wrong,
        so a cursor probe cannot learn the server's release or filter identity.
        """
        if token.count(".") != 1:
            raise ValueError("malformed cursor")
        body, signature = token.split(".")
        expected = (
            base64.urlsafe_b64encode(hmac.new(key, body.encode(), hashlib.sha256).digest())
            .decode()
            .rstrip("=")
        )
        if not hmac.compare_digest(signature, expected):
            raise ValueError("cursor signature mismatch")
        try:
            padded = body + "=" * (-len(body) % 4)
            parts = base64.urlsafe_b64decode(padded).decode().split("|")
            release_id, filters_digest, sort, offset = parts
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("malformed cursor") from error
        if not offset.isdigit():
            raise ValueError("malformed cursor")
        return Cursor(
            release_id=release_id,
            filters_digest=filters_digest,
            sort=sort,
            offset=int(offset),
        )


def filters_digest(filters: Mapping[str, object]) -> str:
    """Digest of the effective filter set, bound into every cursor.

    Two pages are only comparable when their release, filters and sort all match, which is what
    stops a cursor from being replayed against a narrower filter to walk past its own cohort.

    The items are flattened to pairs of strings before canonicalising: ``canonical_json_bytes``
    refuses tuples, and a filter value may itself be a sequence.
    """
    pairs = [[str(key), str(value)] for key, value in sorted(filters.items())]
    return "sha256:" + sha256_bytes(canonical_json_bytes({"filters": pairs}))


# -------- release and leaderboard


class ReleaseSummary(PublicationModel):
    """``GET /releases``: a published release's public identity and its disclosed limits.

    Carries no manifest, no evidence bundle and no task identity list.
    """

    kind: Literal["release_summary"] = "release_summary"
    release_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    state: Literal["published", "withdrawn"]
    scope: Literal["exploratory", "ranked_eligible"]
    fixture_kind: str = Field(min_length=1, max_length=64)
    cohort_digest: str = Field(min_length=1, max_length=200)
    published_at: str | None = None
    limitations: tuple[str, ...] = ()
    withdrawal_reason: str | None = Field(default=None, max_length=400)
    replacement_release_id: str | None = Field(default=None, max_length=120)
    methodology_url: str = Field(min_length=1, max_length=200)


class LeaderboardEntry(PublicationModel):
    """One row of ``GET /leaderboard``.

    ``rank`` is ``None`` whenever the release is exploratory: an exploratory release may publish an
    unranked partial entry with explicit coverage but no recalculated all-language score
    (T 19.4).
    """

    kind: Literal["leaderboard_entry"] = "leaderboard_entry"
    model_config_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    rank: int | None = Field(default=None, ge=1)
    ranking_label: Literal["exploratory", "ranked_eligible"]
    metrics: tuple[PublicMetric, ...]
    coverage: Coverage
    languages: tuple[str, ...] = ()
    run_mode: str | None = Field(default=None, max_length=120)
    budget_profile_id: str | None = Field(default=None, max_length=120)
    generation_cost_micros: str | None = None
    latency_ms_p50: int | None = Field(default=None, ge=0)
    latency_ms_p95: int | None = Field(default=None, ge=0)
    evidence_url: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def rank_matches_scope(self) -> LeaderboardEntry:
        if self.ranking_label == "exploratory" and self.rank is not None:
            raise ValueError("an exploratory entry carries no rank")
        return self


# -------- profiles


class DimensionBreakdown(PublicationModel):
    """A language or model dimension's score and the coverage behind it."""

    kind: Literal["dimension_breakdown"] = "dimension_breakdown"
    dimension: str = Field(min_length=1, max_length=64)
    metric: PublicMetric
    applicable_tasks: int = Field(ge=0)
    opportunity_count: int = Field(ge=0)


class LanguageEntryProfile(PublicationModel):
    """One model's language-specific metrics, diagnostics, and published coverage."""

    kind: Literal["language_entry_profile"] = "language_entry_profile"
    language_id: str = Field(min_length=1, max_length=64)
    model_config_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    dimensions: tuple[DimensionBreakdown, ...] = ()
    diagnostics: tuple[DimensionBreakdown, ...] = ()
    tool_coverage: tuple[tuple[str, str], ...] = ()
    evidence_url: str = Field(min_length=1, max_length=200)


class ModelProfile(PublicationModel):
    """``GET /models/{id}``: a published configuration-specific profile.

    Carries no credentials, no provider secret reference and no endpoint URL (T 20.4).
    """

    kind: Literal["model_profile"] = "model_profile"
    model_config_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    release_id: str = Field(min_length=1, max_length=120)
    capabilities: tuple[str, ...] = ()
    dimensions: tuple[DimensionBreakdown, ...] = ()
    language_profiles: tuple[LanguageEntryProfile, ...] = ()
    languages: tuple[str, ...] = ()
    run_mode: str | None = Field(default=None, max_length=120)
    budget_profile_id: str | None = Field(default=None, max_length=120)
    generation_cost_micros: str | None = None
    latency_ms_p50: int | None = Field(default=None, ge=0)
    latency_ms_p95: int | None = Field(default=None, ge=0)
    metrics: tuple[PublicMetric, ...] = ()
    coverage: Coverage | None = None
    evidence_url: str = Field(min_length=1, max_length=200)


class LanguageProfile(PublicationModel):
    """``GET /languages/{id}``: dimension and diagnostic profile with opportunity counts.

    ``opportunity_count`` is the *measured* opportunity count. A feature the language cannot
    observe is ``not_applicable`` rather than absent, so a chart cannot imply evidence for an
    untested feature (Prompt 30, PCB-30-3).
    """

    kind: Literal["language_profile"] = "language_profile"
    language_id: str = Field(min_length=1, max_length=64)
    release_id: str = Field(min_length=1, max_length=120)
    dimensions: tuple[DimensionBreakdown, ...] = ()
    diagnostics: tuple[DimensionBreakdown, ...] = ()
    tool_coverage: tuple[tuple[str, str], ...] = ()
    metrics: tuple[PublicMetric, ...] = ()
    coverage: Coverage | None = None
    entries: tuple[LanguageEntryProfile, ...] = ()


# -------- comparison


IncompatibilityCode = Literal[
    "different_cohort",
    "missing_language",
    "protocol_mismatch",
    "insufficient_common_coverage",
    "too_few_entries",
    "too_many_entries",
]


class Incompatibility(PublicationModel):
    """Why two or more entries cannot be compared on a common cohort.

    Typed rather than free text: the frontend must distinguish "these two models were never run on
    the same task set" from "this entry is missing a language", and must never silently renormalise
    a partial entry into a full rank (E2E-28).
    """

    kind: Literal["incompatibility"] = "incompatibility"
    code: IncompatibilityCode
    model_config_id: str | None = Field(default=None, max_length=120)
    detail: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def no_leak(self) -> Incompatibility:
        if any(token in self.detail.lower() for token in ("/", "sha256:", "http")):
            raise ValueError("incompatibility detail must not carry paths, digests or URLs")
        return self


class PairedDelta(PublicationModel):
    """A paired difference between two entries on the common cohort."""

    kind: Literal["paired_delta"] = "paired_delta"
    metric_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    baseline_model_config_id: str = Field(min_length=1, max_length=120)
    candidate_model_config_id: str = Field(min_length=1, max_length=120)
    delta_value: str | None
    interval_low: str | None
    interval_high: str | None
    status: MetricStatus
    reason: str | None = Field(default=None, max_length=200)


class ComparisonResult(PublicationModel):
    """``GET /compare``: either a common-cohort comparison or typed incompatibility.

    Exactly one of ``entries`` and ``incompatibilities`` is populated. A request that cannot be
    compared returns the reasons and no numbers at all, so a partial entry can never be presented
    as a full rank.
    """

    kind: Literal["comparison_result"] = "comparison_result"
    release_id: str = Field(min_length=1, max_length=120)
    cohort_digest: str = Field(min_length=1, max_length=200)
    scope: Literal["exploratory", "ranked_eligible"]
    common_tasks: int = Field(ge=0)
    common_independent_clusters: int = Field(ge=0)
    entries: tuple[LeaderboardEntry, ...] = ()
    deltas: tuple[PairedDelta, ...] = ()
    incompatibilities: tuple[Incompatibility, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def one_side_only(self) -> ComparisonResult:
        if bool(self.entries) == bool(self.incompatibilities):
            raise ValueError("a comparison returns entries or incompatibilities, never both")
        return self


# -------- methodology


class PublicTask(PublicationModel):
    """``GET /tasks/{id}``: a *disclosed* task version.

    A private task is never partially exposed: its identifier resolves to the same not-found an
    unknown identifier gets, and nothing in this model can carry a hidden bundle path, an oracle
    digest or a reference solution.
    """

    kind: Literal["public_task"] = "public_task"
    task_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    disclosed: bool
    language_id: str = Field(min_length=1, max_length=64)
    family: str = Field(min_length=1, max_length=64)
    difficulty: str = Field(min_length=1, max_length=32)
    statement_summary: str = Field(min_length=1, max_length=2000)
    limitations: tuple[str, ...] = ()
    evidence_url: str = Field(min_length=1, max_length=200)


class ContributionRow(PublicationModel):
    """One row of the exact contribution chain (Prompt 15's explanation contract)."""

    kind: Literal["contribution_row"] = "contribution_row"
    item_id: str = Field(min_length=1, max_length=80)
    dimension: str = Field(min_length=1, max_length=64)
    nominal_weight_bp: int = Field(ge=0, le=10_000)
    effective_weight_bp: int = Field(ge=0, le=10_000)
    presentation_weight_bp: int = Field(ge=0, le=10_000)
    arithmetic: str = Field(min_length=1, max_length=200)
    evidence_refs: tuple[str, ...] = ()
    value: str | None = None


class PublicScorecard(PublicationModel):
    """``GET /scorecards/{id}``: the public score breakdown.

    ``formula_version`` and ``policy_digest`` make the score reproducible; ``gating_status``
    distinguishes a score of zero from a score that could not be produced at all.
    """

    kind: Literal["public_scorecard"] = "public_scorecard"
    scorecard_id: str = Field(min_length=1, max_length=120)
    release_id: str = Field(min_length=1, max_length=120)
    model_config_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    formula_version: str = Field(min_length=1, max_length=64)
    policy_digest: str = Field(min_length=1, max_length=200)
    gating_status: Literal["scored", "gated_zero", "needs_review"]
    metrics: tuple[PublicMetric, ...]
    contributions: tuple[ContributionRow, ...] = ()
    evidence_url: str = Field(min_length=1, max_length=200)


class ArtifactRef(PublicationModel):
    """``GET /artifacts/{id}``: a controlled public download.

    Carries a signed, short-lived download path issued by the API, never a storage URL. A private
    artifact resolves to not-found, and a probe cannot obtain a usable token for one.
    """

    kind: Literal["artifact_ref"] = "artifact_ref"
    artifact_id: str = Field(min_length=1, max_length=120)
    content_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=1, max_length=200)
    download_url: str = Field(min_length=1, max_length=400)
    expires_at: str = Field(min_length=1, max_length=40)


class Methodology(PublicationModel):
    """``GET /methodology/{version}``: the frozen methods behind a release.

    Records what is native versus adapted, so a reader can tell an original benchmark's own metric
    from this product's adaptation of it.
    """

    kind: Literal["methodology"] = "methodology"
    version: str = Field(min_length=1, max_length=64)
    methods: tuple[str, ...] = ()
    formulas: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    deviations: tuple[str, ...] = ()
    native_benchmarks: tuple[tuple[str, str], ...] = ()
    correction_history: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


# -------- public manifest


class PublicManifest(PublicationModel):
    """The signed manifest a published release exposes.

    Signature is over canonical manifest bytes excluding the signature fields themselves (T 19.5),
    and the verification key is identified by key id rather than embedded.
    """

    kind: Literal["public_manifest"] = "public_manifest"
    release_id: str = Field(min_length=1, max_length=120)
    cohort_digest: str = Field(min_length=1, max_length=200)
    projection_digest: str = Field(min_length=1, max_length=200)
    signing_algorithm: Literal["ed25519"] = "ed25519"
    signing_key_id: str = Field(min_length=1, max_length=120)
    signature: str = Field(min_length=1, max_length=200)
    generated_at: str | None = None

    canonical_excluded_fields: ClassVar[frozenset[str]] = frozenset({"signature", "generated_at"})


# -------- helpers


def _finite_decimal(raw: str, field: str) -> Decimal:
    try:
        number = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"{field} must be a decimal string") from error
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def metric_value(metric: PublicMetric) -> str | None:
    """The decimal string for display; ``None`` for a metric that was not measured.

    Frontends parse this only to plot. It exists so no consumer reaches for a float field.
    """
    return metric.value


def aggregate_to_metric(aggregate: AggregateResult, definition: MetricDefinition) -> PublicMetric:
    """Map one aggregate result onto its public metric definition.

    The aggregate's own ``status`` and ``reasons`` become the public ``status``/``reason``, so a
    result that could not be produced stays visibly unavailable instead of becoming ``0.000000``.
    """
    status: MetricStatus = (
        "measured" if aggregate.status == "complete" else "insufficient_information"
    )
    return PublicMetric(
        metric_id=definition.metric_id,
        label=definition.label,
        unit=definition.unit,
        direction=definition.direction,
        status=status,
        value=aggregate.value,
        interval_low=None,
        interval_high=None,
        coverage=aggregate.coverage,
        conditional_on_pass=definition.conditional_on_pass,
        reason=aggregate.reasons[0] if aggregate.reasons else None,
    )


def sort_leaderboard(rows: Sequence[LeaderboardEntry], sort: str) -> tuple[LeaderboardEntry, ...]:
    """Stable ordering for a leaderboard page.

    Ties fall back to the model identity, so paging is deterministic and two adjacent pages never
    repeat or skip a row for a given sort and filter set (T 20.1: stable ordering).
    """
    if sort not in {"rank_asc", "label_asc", "coverage_desc"}:
        raise ValueError("unsupported sort")
    if sort == "label_asc":
        return tuple(sorted(rows, key=lambda r: r.model_config_id))
    if sort == "coverage_desc":
        return tuple(
            sorted(rows, key=lambda r: (-r.coverage.independent_clusters, r.model_config_id))
        )
    return tuple(
        sorted(
            rows,
            key=lambda r: (
                0 if r.rank is not None else 1,
                r.rank if r.rank is not None else 0,
                r.model_config_id,
            ),
        )
    )


__all__ = [
    "ArtifactRef",
    "ComparisonResult",
    "ContributionRow",
    "Coverage",
    "Cursor",
    "DimensionBreakdown",
    "ERROR_STATUS",
    "ErrorCode",
    "Incompatibility",
    "IncompatibilityCode",
    "LanguageProfile",
    "LeaderboardEntry",
    "Methodology",
    "MetricRegistry",
    "MetricStatus",
    "Page",
    "PageMeta",
    "PairedDelta",
    "PublicError",
    "PublicManifest",
    "PublicMetric",
    "PublicScorecard",
    "PublicTask",
    "ReleaseSummary",
    "aggregate_to_metric",
    "filters_digest",
    "metric_value",
    "sort_leaderboard",
]
