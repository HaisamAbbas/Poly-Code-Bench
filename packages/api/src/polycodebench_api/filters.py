"""Validated filter sets for public listings and comparisons.

The effective filter set is digested into every cursor, so a cursor can never be replayed against a
different release or filter set (Technical Specification 20.1). Task-date filters are rejected:
the published projections expose no task dates, so honouring one silently would invent a cohort
slice the release never published.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from polycodebench_publication.projections import filters_digest
from polycodebench_publication.projections_query import ReleaseContent

from polycodebench_api.errors import ApiError

_TOKEN = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")

RELEASE_STATES = frozenset({"published", "withdrawn"})
RELEASE_SCOPES = frozenset({"exploratory", "ranked_eligible"})


def _tokens(values: Sequence[str], label: str) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise ApiError("INVALID_FILTER", f"{label} filter is not valid for this release")
        cleaned.append(value)
    return tuple(sorted(set(cleaned)))


@dataclass(frozen=True)
class FilterSet:
    """One request's effective filter set; canonicalised before it is bound into a cursor."""

    languages: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    difficulties: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "languages": list(self.languages),
            "families": list(self.families),
            "difficulties": list(self.difficulties),
            "states": list(self.states),
            "scopes": list(self.scopes),
        }

    def digest(self) -> str:
        return filters_digest(self.to_mapping())

    def labels(self) -> tuple[str, ...]:
        parts = (
            [f"language={value}" for value in self.languages]
            + [f"family={value}" for value in self.families]
            + [f"difficulty={value}" for value in self.difficulties]
            + [f"state={value}" for value in self.states]
            + [f"scope={value}" for value in self.scopes]
        )
        return tuple(parts)

    @property
    def restricts_tasks(self) -> bool:
        return bool(self.families or self.difficulties)


def parse_filters(
    *,
    languages: Sequence[str] = (),
    families: Sequence[str] = (),
    difficulties: Sequence[str] = (),
    task_date: str | None = None,
    states: Sequence[str] = (),
    scopes: Sequence[str] = (),
) -> FilterSet:
    """Validate one request's query filters against what a release can actually express."""
    if task_date is not None:
        raise ApiError("INVALID_FILTER", "task dates are not exposed by this release")
    parsed_states = _tokens(states, "release state")
    for value in parsed_states:
        if value not in RELEASE_STATES:
            raise ApiError("INVALID_FILTER", "release state filter is not valid for this release")
    parsed_scopes = _tokens(scopes, "release scope")
    for value in parsed_scopes:
        if value not in RELEASE_SCOPES:
            raise ApiError("INVALID_FILTER", "release scope filter is not valid for this release")
    return FilterSet(
        languages=_tokens(languages, "language"),
        families=_tokens(families, "family"),
        difficulties=_tokens(difficulties, "difficulty"),
        states=parsed_states,
        scopes=parsed_scopes,
    )


def task_slice(content: ReleaseContent, filters: FilterSet) -> tuple[str, ...]:
    """The disclosed task ids inside a family/difficulty slice.

    A slice naming no disclosed task is a parameter set that is not valid for this release rather
    than an empty board that could read as "scored zero".
    """
    selected = tuple(
        task.task_id
        for task in content.disclosed_tasks
        if (not filters.families or task.family in filters.families)
        and (not filters.difficulties or task.difficulty in filters.difficulties)
    )
    if not selected:
        raise ApiError("INVALID_FILTER", "the filtered cohort is not published in this release")
    return selected


def entry_ids_in_slice(content: ReleaseContent, slice_ids: Sequence[str]) -> frozenset[str]:
    """Entries with at least one published scorecard inside the slice.

    Presence-based selection exactly like the language filter: an entry with no task in the slice
    stays out of the board and its scores are never renormalised.
    """
    wanted = frozenset(slice_ids)
    return frozenset(
        card.model_config_id for card in content.scorecards if card.task_id in wanted
    )


__all__ = [
    "FilterSet",
    "entry_ids_in_slice",
    "parse_filters",
    "task_slice",
]
