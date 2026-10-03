"""The ``{data, meta}`` envelope, ETag identity and cache policy (Technical Specification 20.1).

Every public response carries the release digest in ``meta`` plus an ``ETag`` over the canonical
response body. Responses resolved from an explicit release are immutable and cache long-term; the
mutable ``latest`` pointer surface gets a short cache lifetime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_publication.aggregation import PublicationModel
from polycodebench_publication.projections import MetricRegistry
from pydantic import Field
from starlette.responses import Response

#: An explicit release is immutable history: cache indefinitely (Technical Specification 20.1).
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"

#: The mutable ``latest`` pointer can move: cache only briefly.
SHORT_CACHE = "public, max-age=60"

#: Token-bearing or caller-specific responses are never cached.
NO_STORE = "private, no-store"


class ResponseMeta(PublicationModel):
    """The ``meta`` of a public envelope: release identity, paging state and metric definitions."""

    kind: Literal["response_meta"] = "response_meta"
    release_id: str | None = Field(default=None, max_length=120)
    current_release_id: str | None = Field(default=None, max_length=120)
    release_digest: str = Field(min_length=1, max_length=200)
    exploratory: bool | None = None
    total: int | None = Field(default=None, ge=0)
    returned: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1, le=200)
    sort: str | None = Field(default=None, max_length=40)
    filters: tuple[str, ...] = ()
    next_cursor: str | None = None
    registry: MetricRegistry | None = None


def envelope(
    payload: PublicationModel | Sequence[PublicationModel], meta: PublicationModel
) -> dict[str, Any]:
    """Build one ``{data, meta}`` body from typed rows and their metadata."""
    if isinstance(payload, PublicationModel):
        data: Any = payload.model_dump(mode="json")
    else:
        data = [row.model_dump(mode="json") for row in payload]
    meta_body = meta.model_dump(mode="json", exclude={"kind", "schema_version"})
    return {"data": data, "meta": meta_body}


def body_etag(body: Mapping[str, Any]) -> str:
    """Opaque body identity: two identical bodies share one ETag across releases and filters."""
    return '"' + sha256_bytes(canonical_json_bytes(dict(body))) + '"'


def respond(
    request: Request,
    body: Mapping[str, Any],
    *,
    cache: str,
    status_code: int = 200,
) -> Response:
    """Return the envelope with its ETag and cache policy, honouring ``If-None-Match``."""
    etag = body_etag(body)
    headers = {"ETag": etag, "Cache-Control": cache}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=dict(body), headers=headers, status_code=status_code)


__all__ = [
    "IMMUTABLE_CACHE",
    "NO_STORE",
    "SHORT_CACHE",
    "ResponseMeta",
    "body_etag",
    "envelope",
    "respond",
]
