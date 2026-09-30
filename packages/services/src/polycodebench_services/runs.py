"""Run creation use case with authorization and canonical idempotency input."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from polycodebench_core.application_errors import PersistenceUnavailable
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from pydantic import BaseModel, ConfigDict, Field, field_validator

from polycodebench_services.rbac import Permission, Principal, authorize


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    campaign_id: UUID
    config_document_id: UUID
    task_set_id: UUID
    model_revision_id: UUID
    samples_per_task: int = Field(ge=1, le=10_000)
    master_seed: str

    @field_validator("master_seed")
    @classmethod
    def validate_seed(cls, value: str) -> str:
        if (
            not value.isascii()
            or not value.isdecimal()
            or (len(value) > 1 and value.startswith("0"))
        ):
            raise ValueError("master_seed must be a canonical unsigned 64-bit decimal string")
        if int(value) > 18_446_744_073_709_551_615:
            raise ValueError("master_seed exceeds unsigned 64-bit range")
        return value


class RunCreateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    run_id: UUID
    attempt_ids: tuple[UUID, ...]
    replayed: bool = False


class RunRepository(Protocol):
    def create_idempotently(
        self,
        *,
        subject_id: str,
        route: str,
        idempotency_key: str,
        request_digest: str,
        request: Mapping[str, object],
    ) -> Mapping[str, object]: ...


class RunCreationService:
    route = "POST /v1/admin/runs"

    def __init__(self, repository: RunRepository) -> None:
        self._repository = repository

    def create(
        self,
        principal: Principal,
        request: RunCreateRequest,
        idempotency_key: str,
    ) -> RunCreateResult:
        authorize(principal, Permission.RUN_CREATE)
        if not idempotency_key or len(idempotency_key) > 255 or not idempotency_key.isascii():
            raise ValueError("Idempotency-Key must be non-empty ASCII and at most 255 characters")
        request_document = request.model_dump(mode="json")
        request_digest = sha256_bytes(canonical_json_bytes(request_document))
        stored = self._repository.create_idempotently(
            subject_id=principal.subject_id,
            route=self.route,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            request=request_document,
        )
        attempt_ids = stored.get("attempt_ids")
        if not isinstance(attempt_ids, (list, tuple)) or not all(
            isinstance(item, str) for item in attempt_ids
        ):
            raise PersistenceUnavailable("run repository returned an invalid result")
        return RunCreateResult(
            run_id=UUID(str(stored["run_id"])),
            attempt_ids=tuple(UUID(item) for item in attempt_ids),
            replayed=bool(stored.get("replayed", False)),
        )
