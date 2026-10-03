"""Resolving published release documents and their public identity metadata.

Public routes read only releases the store marks public. An unknown, draft or private identity
resolves to the same generic not-found, so probing cannot distinguish them (Architecture 14.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polycodebench_core.application_errors import NotFound
from polycodebench_publication.projections import MetricRegistry
from polycodebench_publication.projections_query import ReleaseContent

from polycodebench_api.context import ApiServices
from polycodebench_api.errors import ApiError

LATEST = "latest"
#: Cursor identity of the multi-release listing, which is not scoped to one release.
LISTING_SCOPE = "releases"


def load_public_document(services: ApiServices, release: str) -> tuple[str, dict[str, Any], bool]:
    """Return ``(release_id, document, pinned)`` for an explicit release or the latest pointer.

    ``pinned`` is true when the caller named an immutable release explicitly; unpinned responses
    depend on the mutable latest pointer and get a short cache lifetime.
    """
    pinned = release != LATEST
    release_id = release
    if not pinned:
        pointer = services.releases.current(services.target)
        resolved = pointer.get("release_id")
        release_id = resolved if isinstance(resolved, str) else ""
        if not release_id:
            raise ApiError("NOT_FOUND")
    try:
        # Visibility gate: drafts and foreign states are not public at all.
        services.releases.public(release_id)
        document = services.releases.get(release_id)
    except NotFound:
        raise ApiError("NOT_FOUND") from None
    return release_id, document, pinned


def release_digest(document: dict[str, Any]) -> str:
    """The release's content digest: the identity cached responses and cursors bind to."""
    return str(document.get("content_digest", "")) or "unknown"


def is_exploratory(document: dict[str, Any]) -> bool:
    projection = document.get("projection")
    scope = projection.get("scope") if isinstance(projection, dict) else None
    return scope == "exploratory"


def registry_of(document: dict[str, Any]) -> MetricRegistry:
    """The release's own metric definitions, so no consumer implements its own scoring formula."""
    content = content_of(document)
    return MetricRegistry(
        policy_digest=content.policy_digest, definitions=content.metric_definitions
    )


def content_of(document: Mapping[str, Any]) -> ReleaseContent:
    """The release's typed published rows; malformed content is never served."""
    try:
        return ReleaseContent.model_validate(document["content"])
    except (ValueError, KeyError, TypeError) as error:
        raise ApiError("RELEASE_NOT_READY") from error


def public_documents(services: ApiServices) -> list[dict[str, Any]]:
    """All public release documents in stable order."""
    return services.releases.list_public()


def publication_times(services: ApiServices) -> dict[str, str]:
    """Publication timestamps from the store's own audit ledger, keyed by release id."""
    times: dict[str, str] = {}
    for row in services.releases.audit():
        if row.get("action") == "publish" and isinstance(row.get("release_id"), str):
            times[str(row["release_id"])] = str(row.get("occurred_at", ""))
    return times


__all__ = [
    "LATEST",
    "LISTING_SCOPE",
    "content_of",
    "is_exploratory",
    "load_public_document",
    "public_documents",
    "publication_times",
    "registry_of",
    "release_digest",
]
