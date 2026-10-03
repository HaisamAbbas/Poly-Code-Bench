"""Application services shared by the public and administrative route modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from fastapi import Request
from polycodebench_publication.releases import ReleaseStore, SigningKey

from polycodebench_api.auth import ApiPrincipal, TokenDirectory
from polycodebench_api.submissions import SubmissionStore


class RunSummarySource(Protocol):
    """Read-only access to run state counters (``polycodebench_persistence`` implements it)."""

    def get_run_summary(self, run_id: UUID) -> Mapping[str, object] | None: ...


@dataclass(frozen=True)
class ApiServices:
    """Everything a route needs, assembled once at application construction."""

    releases: ReleaseStore
    submissions: SubmissionStore
    tokens: TokenDirectory
    secret_key: bytes
    signing_key: SigningKey
    runs: RunSummarySource | None = None
    target: str = "local:board"
    artifact_ttl_seconds: int = 900
    submission_rate_limit: int = 5
    submission_rate_window_seconds: int = 3600


def services_of(request: Request) -> ApiServices:
    return cast(ApiServices, request.app.state.services)


__all__ = [
    "ApiPrincipal",
    "ApiServices",
    "RunSummarySource",
    "TokenDirectory",
    "services_of",
]
