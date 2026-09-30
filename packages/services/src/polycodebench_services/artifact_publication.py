"""Narrow, review-gated public artifact projection writer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from polycodebench_core.application_errors import (
    AuthorizationError,
    InvalidState,
    PersistenceConflict,
)

from polycodebench_services.artifacts import ArtifactPrincipal

_DOMAIN = "public-projection"


@dataclass(frozen=True, slots=True)
class ReviewedMetadataProjection:
    """Allowlisted public fields; source content and storage metadata are excluded."""

    display_name: str
    description: str
    version: str

    def canonical_bytes(self) -> bytes:
        values = {
            "description": self.description,
            "display_name": self.display_name,
            "projection_type": "reviewed_metadata",
            "schema_version": 1,
            "version": self.version,
        }
        if (
            any(
                not isinstance(value, str)
                for value in (self.display_name, self.description, self.version)
            )
            or not self.display_name.strip()
            or len(self.display_name) > 160
            or len(self.description) > 2000
            or not self.version.strip()
            or len(self.version) > 80
            or any("\x00" in value for value in (self.display_name, self.description, self.version))
        ):
            raise InvalidState("public projection fields are invalid")
        return json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "ascii"
        )


class ProjectionRepository(Protocol):
    def get_verified(self, artifact_id: UUID) -> dict[str, object]: ...
    def get_projection_approval(self, approval_id: UUID) -> dict[str, object]: ...
    def published_projection(self, source_artifact_id: UUID) -> dict[str, object] | None: ...
    def approve_projection(
        self,
        *,
        source_artifact_id: UUID,
        projection_digest: str,
        approved_by: str,
        reason: str,
        request_id: str,
    ) -> UUID: ...

    def begin_upload(
        self,
        *,
        owner: str,
        visibility: str,
        encryption_domain: str,
        expected_digest: str,
        expected_size: int,
        media_type: str,
    ) -> UUID: ...

    def upload(self, *, upload_id: UUID, owner: str, body: bytes) -> None: ...
    def finalize_publication(
        self,
        *,
        upload_id: UUID,
        owner: str,
        approval_id: UUID,
        request_id: str,
    ) -> UUID: ...


class PublicProjectionService:
    """Writes reviewed projections as new public objects and leaves sources intact."""

    def __init__(self, repository: ProjectionRepository) -> None:
        self._repository = repository

    def approve_metadata(
        self,
        *,
        principal: ArtifactPrincipal,
        source_artifact_id: UUID,
        projection: ReviewedMetadataProjection,
        reason: str,
        request_id: str,
    ) -> UUID:
        if not principal.roles & {"reviewer", "administrator"}:
            raise AuthorizationError()
        source = self._repository.get_verified(source_artifact_id)
        if source["visibility"] == "public":
            raise InvalidState("projection source must be private or internal")
        payload = projection.canonical_bytes()
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        return self._repository.approve_projection(
            source_artifact_id=source_artifact_id,
            projection_digest=digest,
            approved_by=principal.subject_id,
            reason=reason,
            request_id=request_id,
        )

    def publish_reviewed_metadata(
        self,
        *,
        principal: ArtifactPrincipal,
        approval_id: UUID,
        projection: ReviewedMetadataProjection,
        request_id: str,
    ) -> UUID:
        if not principal.roles & {"publisher", "administrator"}:
            raise AuthorizationError()
        approval = self._repository.get_projection_approval(approval_id)
        if approval["approved_by"] == principal.subject_id:
            raise AuthorizationError("a separate reviewer approval is required")
        payload = projection.canonical_bytes()
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if approval["projection_digest"] != digest:
            raise InvalidState("projection bytes differ from the approved content")
        source_artifact_id = UUID(str(approval["source_artifact_id"]))
        existing = self._repository.published_projection(source_artifact_id)
        if existing is not None:
            if (
                existing["review_digest"] == digest
                and existing["approved_by"] == approval["approved_by"]
                and existing["reason"] == approval["reason"]
            ):
                return UUID(str(existing["public_artifact_id"]))
            raise PersistenceConflict("source artifact already has a different public projection")
        upload_id = self._repository.begin_upload(
            owner=principal.subject_id,
            visibility="public",
            encryption_domain=_DOMAIN,
            expected_digest=digest,
            expected_size=len(payload),
            media_type="application/json",
        )
        self._repository.upload(upload_id=upload_id, owner=principal.subject_id, body=payload)
        return self._repository.finalize_publication(
            upload_id=upload_id,
            owner=principal.subject_id,
            approval_id=approval_id,
            request_id=request_id,
        )
