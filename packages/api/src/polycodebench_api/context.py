"""Application services shared by the public and administrative route modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from fastapi import Request
from polycodebench_publication.releases import ReleaseStore, SigningKey
from polycodebench_services.model_endpoints import ModelEndpointService
from polycodebench_services.runs import RunCreationService

from polycodebench_api.auth import ApiPrincipal, TokenDirectory
from polycodebench_api.submissions import SubmissionRate


class RunSummarySource(Protocol):
    """Read-only access to run state counters (``polycodebench_persistence`` implements it)."""

    def get_run_summary(self, run_id: UUID) -> Mapping[str, object] | None: ...


class SubmissionRepository(Protocol):
    """Storage boundary shared by local fixtures and durable PostgreSQL deployments."""

    def submit(
        self,
        *,
        subject: str,
        request_id: str,
        payload: Mapping[str, object],
        rate: SubmissionRate,
    ) -> dict[str, object]: ...

    def get_owned(self, *, subject: str, submission_id: str) -> dict[str, object]: ...

    def list_for_review(
        self, *, statuses: tuple[str, ...], limit: int = 100
    ) -> list[dict[str, object]]: ...

    def get_for_review(self, *, submission_id: str) -> dict[str, object]: ...

    def reject(
        self,
        *,
        submission_id: str,
        reviewer: str,
        reason: str,
        expected_version: int,
        request_id: str,
    ) -> dict[str, object]: ...

    def begin_approval(
        self,
        *,
        submission_id: str,
        reviewer: str,
        expected_version: int,
        request_id: str,
        approval_document: Mapping[str, object],
    ) -> dict[str, object]: ...

    def finish_approval(
        self,
        *,
        submission_id: str,
        reviewer: str,
        run_id: str,
        approval_digest: str,
        request_id: str,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class ApiServices:
    """Everything a route needs, assembled once at application construction."""

    releases: ReleaseStore
    submissions: SubmissionRepository
    tokens: TokenDirectory
    secret_key: bytes
    signing_key: SigningKey
    runs: RunSummarySource | None = None
    run_creation: RunCreationService | None = None
    endpoints: ModelEndpointService | None = None
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
    "SubmissionRepository",
    "TokenDirectory",
    "services_of",
]
