"""Opaque signed cursor pagination, release/filter/sort bound (Technical Specification 20.1).

A cursor is signed with the server key and bound to the release (or listing scope), the effective
filter digest and the sort. A cursor replayed against any of those being different, or a forged
token, is one generic ``INVALID_CURSOR`` response that reveals nothing about the server state.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from polycodebench_publication.projections import Cursor

from polycodebench_api.errors import ApiError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class PageRequest:
    """One page window over a stably ordered row sequence."""

    limit: int
    sort: str
    offset: int


def parse_page_request(
    *,
    limit: int,
    cursor: str | None,
    sort: str,
    allowed_sorts: frozenset[str],
    binding: str,
    filters_digest: str,
    key: bytes,
) -> PageRequest:
    """Validate the pagination parameters and authenticate the cursor against this request."""
    if limit < 1 or limit > MAX_LIMIT:
        raise ApiError("INVALID_FILTER", "limit must be between 1 and 200")
    if sort not in allowed_sorts:
        raise ApiError("INVALID_FILTER", "sort is not valid for this resource")
    offset = 0
    if cursor is not None:
        try:
            decoded = Cursor.verify(cursor, key)
        except ValueError:
            raise ApiError("INVALID_CURSOR") from None
        if (
            decoded.release_id != binding
            or decoded.filters_digest != filters_digest
            or decoded.sort != sort
        ):
            raise ApiError("INVALID_CURSOR")
        offset = decoded.offset
    return PageRequest(limit=limit, sort=sort, offset=offset)


def page_rows[RowT](
    rows: Sequence[RowT],
    request: PageRequest,
    *,
    binding: str,
    filters_digest: str,
    key: bytes,
) -> tuple[tuple[RowT, ...], str | None]:
    """Take this page's window and mint the signed cursor for the next one, if any."""
    window = tuple(rows[request.offset : request.offset + request.limit])
    next_cursor: str | None = None
    end = request.offset + request.limit
    if end < len(rows):
        next_cursor = Cursor(
            release_id=binding,
            filters_digest=filters_digest,
            sort=request.sort,
            offset=end,
        ).sign(key)
    return window, next_cursor


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PageRequest",
    "page_rows",
    "parse_page_request",
]
