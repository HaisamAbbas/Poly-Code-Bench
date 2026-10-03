"""Public read API over published release projections (Technical Specification 20.4).

Every route resolves against the published release document only. A private or unknown identity
returns the same generic not-found, scores stay canonical decimal strings, and nothing here
reaches worker, attempt or hidden task data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_publication.projections import (
    ComparisonResult,
    Incompatibility,
    MetricRegistry,
    ReleaseSummary,
    sort_leaderboard,
)
from polycodebench_publication.projections_query import (
    PublicApiError,
    ReleaseContent,
    compare,
    language_profile,
    leaderboard,
    methodology,
    model_profile,
    public_task,
    release_summary,
    scorecard,
)
from starlette.responses import Response

from polycodebench_api.context import services_of
from polycodebench_api.documents import (
    LATEST,
    LISTING_SCOPE,
    content_of,
    is_exploratory,
    load_public_document,
    public_documents,
    publication_times,
    registry_of,
    release_digest,
)
from polycodebench_api.envelope import (
    IMMUTABLE_CACHE,
    NO_STORE,
    SHORT_CACHE,
    ResponseMeta,
    envelope,
    respond,
)
from polycodebench_api.errors import ApiError
from polycodebench_api.filters import FilterSet, entry_ids_in_slice, parse_filters, task_slice
from polycodebench_api.pagination import DEFAULT_LIMIT, page_rows, parse_page_request
from polycodebench_api.submissions import (
    ModelSubmission,
    ModelSubmissionInput,
    SubmissionRate,
)

router = APIRouter(prefix="/v1")

RELEASE_SORTS = frozenset({"release_id_asc"})
TASK_SORTS = frozenset({"task_id_asc"})
LEADERBOARD_SORTS = frozenset({"rank_asc", "label_asc", "coverage_desc"})


def _cache(pinned: bool) -> str:
    return IMMUTABLE_CACHE if pinned else SHORT_CACHE


def single_meta(
    release_id: str,
    digest: str,
    exploratory: bool,
    *,
    registry: MetricRegistry | None = None,
    filters: FilterSet | None = None,
) -> ResponseMeta:
    return ResponseMeta(
        release_id=release_id,
        release_digest=digest,
        exploratory=exploratory,
        total=1,
        returned=1,
        limit=1,
        filters=filters.labels() if filters else (),
        registry=registry,
    )


def _digest_of(payload: object) -> str:
    return "sha256:" + sha256_bytes(canonical_json_bytes(payload))


# --------------------------------------------------------------------------- releases


@router.get("/releases")
def list_releases(
    request: Request,
    state: Annotated[list[str], Query(default_factory=list)],
    scope: Annotated[list[str], Query(default_factory=list)],
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Response:
    """Published release identities and disclosed limits; no manifests, no private rows."""
    services = services_of(request)
    filters = parse_filters(states=state, scopes=scope)
    page = parse_page_request(
        limit=limit,
        cursor=cursor,
        sort="release_id_asc",
        allowed_sorts=RELEASE_SORTS,
        binding=LISTING_SCOPE,
        filters_digest=filters.digest(),
        key=services.secret_key,
    )
    times = publication_times(services)
    rows: list[ReleaseSummary] = []
    for document in public_documents(services):
        summary = release_summary(document)
        published_at = times.get(summary.release_id)
        if published_at:
            summary = summary.model_copy(update={"published_at": published_at})
        if filters.states and summary.state not in filters.states:
            continue
        if filters.scopes and summary.scope not in filters.scopes:
            continue
        rows.append(summary)
    rows.sort(key=lambda item: item.release_id)
    window, next_cursor = page_rows(
        rows,
        page,
        binding=LISTING_SCOPE,
        filters_digest=filters.digest(),
        key=services.secret_key,
    )
    meta = ResponseMeta(
        release_digest=_digest_of([row.model_dump(mode="json") for row in window]),
        current_release_id=services.releases.current(services.target).get("release_id"),
        total=len(rows),
        returned=len(window),
        limit=page.limit,
        sort=page.sort,
        filters=filters.labels(),
        next_cursor=next_cursor,
    )
    return respond(request, envelope(window, meta), cache=SHORT_CACHE)


@router.get("/releases/{release_id}")
def get_release(request: Request, release_id: str) -> Response:
    services = services_of(request)
    resolved, document, pinned = load_public_document(services, release_id)
    summary = release_summary(document)
    published_at = publication_times(services).get(resolved)
    if published_at:
        summary = summary.model_copy(update={"published_at": published_at})
    meta = single_meta(resolved, release_digest(document), is_exploratory(document))
    return respond(request, envelope(summary, meta), cache=_cache(pinned))


# --------------------------------------------------------------------------- leaderboard


@router.get("/leaderboard")
def get_leaderboard(
    request: Request,
    language: Annotated[list[str], Query(default_factory=list)],
    family: Annotated[list[str], Query(default_factory=list)],
    difficulty: Annotated[list[str], Query(default_factory=list)],
    task_date: Annotated[str | None, Query()] = None,
    release: str = LATEST,
    sort: str = "rank_asc",
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Response:
    services = services_of(request)
    filters = parse_filters(
        languages=language, families=family, difficulties=difficulty, task_date=task_date
    )
    resolved, document, pinned = load_public_document(services, release)
    page = parse_page_request(
        limit=limit,
        cursor=cursor,
        sort=sort,
        allowed_sorts=LEADERBOARD_SORTS,
        binding=resolved,
        filters_digest=filters.digest(),
        key=services.secret_key,
    )
    rows = list(leaderboard(document, languages=frozenset(filters.languages) or None))
    if filters.restricts_tasks:
        content = content_of(document)
        allowed = entry_ids_in_slice(content, task_slice(content, filters))
        rows = [row for row in rows if row.model_config_id in allowed]
    ordered = list(sort_leaderboard(rows, sort))
    window, next_cursor = page_rows(
        ordered,
        page,
        binding=resolved,
        filters_digest=filters.digest(),
        key=services.secret_key,
    )
    meta = ResponseMeta(
        release_id=resolved,
        release_digest=release_digest(document),
        exploratory=is_exploratory(document),
        total=len(ordered),
        returned=len(window),
        limit=page.limit,
        sort=page.sort,
        filters=filters.labels(),
        next_cursor=next_cursor,
        registry=registry_of(document),
    )
    return respond(request, envelope(window, meta), cache=_cache(pinned))


# --------------------------------------------------------------------------- profiles


@router.get("/models/{model_config_id}")
def get_model(request: Request, model_config_id: str, release: str = LATEST) -> Response:
    services = services_of(request)
    resolved, document, pinned = load_public_document(services, release)
    profile = model_profile(document, model_config_id)
    meta = single_meta(
        resolved,
        release_digest(document),
        is_exploratory(document),
        registry=registry_of(document),
    )
    return respond(request, envelope(profile, meta), cache=_cache(pinned))


@router.get("/languages/{language_id}")
def get_language(request: Request, language_id: str, release: str = LATEST) -> Response:
    services = services_of(request)
    resolved, document, pinned = load_public_document(services, release)
    profile = language_profile(document, language_id)
    meta = single_meta(
        resolved,
        release_digest(document),
        is_exploratory(document),
        registry=registry_of(document),
    )
    return respond(request, envelope(profile, meta), cache=_cache(pinned))


# --------------------------------------------------------------------------- comparison


def _filter_gaps(
    content: ReleaseContent, filters: FilterSet, model_ids: Sequence[str]
) -> list[Incompatibility]:
    """Entries that cannot join the *filtered* cohort are typed incompatibilities, never dropped."""
    by_id = {entry.model_config_id: entry for entry in content.entries}
    allowed: frozenset[str] | None = None
    if filters.restricts_tasks:
        allowed = entry_ids_in_slice(content, task_slice(content, filters))
    gaps: list[Incompatibility] = []
    for model_config_id in model_ids:
        entry = by_id.get(model_config_id)
        if entry is None:
            continue  # the query layer reports absence itself
        if filters.languages and not set(filters.languages) <= set(entry.languages):
            gaps.append(
                Incompatibility(
                    code="missing_language",
                    model_config_id=model_config_id,
                    detail="entry does not cover the filtered cohort",
                )
            )
        elif allowed is not None and model_config_id not in allowed:
            gaps.append(
                Incompatibility(
                    code="insufficient_common_coverage",
                    model_config_id=model_config_id,
                    detail="entry has no task in the filtered cohort",
                )
            )
    return gaps


def _comparison(
    document: Mapping[str, Any], filters: FilterSet, model_ids: Sequence[str]
) -> ComparisonResult:
    base = compare(document, tuple(model_ids))
    gaps = _filter_gaps(content_of(document), filters, model_ids)
    if not gaps:
        return base
    merged = (base.incompatibilities + tuple(gaps)) if base.incompatibilities else tuple(gaps)
    return ComparisonResult(
        release_id=base.release_id,
        cohort_digest=base.cohort_digest,
        scope=base.scope,
        common_tasks=0,
        common_independent_clusters=0,
        entries=(),
        deltas=(),
        incompatibilities=merged,
        limitations=base.limitations,
    )


@router.get("/compare")
def get_compare(
    request: Request,
    models: Annotated[list[str], Query(default_factory=list)],
    language: Annotated[list[str], Query(default_factory=list)],
    family: Annotated[list[str], Query(default_factory=list)],
    difficulty: Annotated[list[str], Query(default_factory=list)],
    task_date: Annotated[str | None, Query()] = None,
    release: str = LATEST,
) -> Response:
    services = services_of(request)
    filters = parse_filters(
        languages=language, families=family, difficulties=difficulty, task_date=task_date
    )
    resolved, document, pinned = load_public_document(services, release)
    result = _comparison(document, filters, models)
    meta = single_meta(
        resolved,
        release_digest(document),
        is_exploratory(document),
        registry=registry_of(document),
        filters=filters,
    )
    return respond(request, envelope(result, meta), cache=_cache(pinned))


# --------------------------------------------------------------------------- tasks


@router.get("/tasks")
def list_tasks(
    request: Request,
    language: Annotated[list[str], Query(default_factory=list)],
    family: Annotated[list[str], Query(default_factory=list)],
    difficulty: Annotated[list[str], Query(default_factory=list)],
    task_date: Annotated[str | None, Query()] = None,
    release: str = LATEST,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Response:
    """Disclosed task versions only; private task identities never appear in a listing."""
    services = services_of(request)
    filters = parse_filters(
        languages=language, families=family, difficulties=difficulty, task_date=task_date
    )
    resolved, document, pinned = load_public_document(services, release)
    page = parse_page_request(
        limit=limit,
        cursor=cursor,
        sort="task_id_asc",
        allowed_sorts=TASK_SORTS,
        binding=resolved,
        filters_digest=filters.digest(),
        key=services.secret_key,
    )
    content = content_of(document)
    rows = [
        task
        for task in content.disclosed_tasks
        if (not filters.languages or task.language_id in filters.languages)
        and (not filters.families or task.family in filters.families)
        and (not filters.difficulties or task.difficulty in filters.difficulties)
    ]
    rows.sort(key=lambda task: task.task_id)
    window, next_cursor = page_rows(
        rows,
        page,
        binding=resolved,
        filters_digest=filters.digest(),
        key=services.secret_key,
    )
    meta = ResponseMeta(
        release_id=resolved,
        release_digest=release_digest(document),
        exploratory=is_exploratory(document),
        total=len(rows),
        returned=len(window),
        limit=page.limit,
        sort=page.sort,
        filters=filters.labels(),
        next_cursor=next_cursor,
        registry=registry_of(document),
    )
    return respond(request, envelope(window, meta), cache=_cache(pinned))


@router.get("/tasks/{task_id}")
def get_task(request: Request, task_id: str, release: str = LATEST) -> Response:
    services = services_of(request)
    resolved, document, pinned = load_public_document(services, release)
    task = public_task(document, task_id)
    meta = single_meta(resolved, release_digest(document), is_exploratory(document))
    return respond(request, envelope(task, meta), cache=_cache(pinned))


@router.get("/scorecards/{scorecard_id}")
def get_scorecard(request: Request, scorecard_id: str, release: str = LATEST) -> Response:
    services = services_of(request)
    resolved, document, pinned = load_public_document(services, release)
    card = scorecard(document, scorecard_id)
    meta = single_meta(
        resolved,
        release_digest(document),
        is_exploratory(document),
        registry=registry_of(document),
    )
    return respond(request, envelope(card, meta), cache=_cache(pinned))


# --------------------------------------------------------------------------- methodology


@router.get("/methodology/{version}")
def get_methodology(request: Request, version: str, release: str = LATEST) -> Response:
    """The frozen methods behind a release, resolved by version as release links expect."""
    services = services_of(request)
    pinned = release != LATEST
    candidates: list[tuple[str, dict[str, Any]]] = []
    try:
        resolved, document, _ = load_public_document(services, release)
        candidates.append((resolved, document))
    except ApiError:
        if pinned:
            raise
    for document in public_documents(services):
        identifier = str(document.get("id", ""))
        if identifier and all(identifier != existing for existing, _ in candidates):
            candidates.append((identifier, document))
    for resolved, document in candidates:
        try:
            methods = methodology(document)
        except PublicApiError:
            continue
        if methods.version == version:
            meta = single_meta(resolved, release_digest(document), is_exploratory(document))
            return respond(request, envelope(methods, meta), cache=_cache(pinned))
    raise ApiError("NOT_FOUND")


# --------------------------------------------------------------------------- submissions


@router.post("/model-submissions")
def post_model_submission(
    request: Request,
    submission: ModelSubmissionInput,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response:
    """Public intake: validated metadata plus contact and permission attestation only."""
    services = services_of(request)
    if idempotency_key is None or not idempotency_key.strip():
        raise ApiError("SCHEMA_INVALID", "idempotency key is required")
    payload = submission.model_dump(mode="json")
    result = services.submissions.submit(
        subject=submission.contact_email,
        request_id=idempotency_key.strip(),
        payload=payload,
        rate=SubmissionRate(
            limit=services.submission_rate_limit,
            window_seconds=services.submission_rate_window_seconds,
        ),
    )
    accepted = ModelSubmission.model_validate(result)
    meta = ResponseMeta(release_digest=_digest_of(result))
    return respond(request, envelope(accepted, meta), cache=NO_STORE)


__all__ = ["router", "single_meta"]
