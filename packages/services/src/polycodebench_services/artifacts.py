"""Role-scoped artifact operations; identifiers never act as bearer capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from polycodebench_core.application_errors import AuthorizationError, NotFound


@dataclass(frozen=True, slots=True)
class ArtifactPrincipal:
    subject_id: str
    roles: frozenset[str]
    artifact_scope: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    artifact_id: UUID
    body: bytes
    media_type: str
    headers: dict[str, str]


class ArtifactReader(Protocol):
    def get_verified(self, artifact_id: UUID) -> dict[str, object]: ...

    def read_verified(self, artifact_id: UUID) -> tuple[dict[str, object], bytes]: ...

    def is_publicly_released(self, artifact_id: UUID) -> bool: ...


class ArtifactUploader(Protocol):
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

    def upload_visibility(self, *, upload_id: UUID, owner: str) -> str: ...

    def upload(self, *, upload_id: UUID, owner: str, body: bytes) -> None: ...
    def finalize(self, *, upload_id: UUID, owner: str) -> UUID: ...


class ArtifactAccessService:
    """Checks visibility on every read; never accepts storage keys or digests."""

    _HIDDEN_READERS = frozenset({"curator", "reviewer", "evaluator", "administrator"})
    _INTERNAL_READERS = frozenset(
        {"operator", "curator", "reviewer", "evaluator", "publisher", "administrator"}
    )

    def __init__(self, reader: ArtifactReader) -> None:
        self._reader = reader

    def download(self, principal: ArtifactPrincipal, artifact_id: UUID) -> ArtifactDownload:
        try:
            record = self._reader.get_verified(artifact_id)
        except NotFound:
            raise NotFound() from None
        if not self._may_read(principal, artifact_id, str(record["visibility"])):
            # Uniform 404 avoids turning UUID or digest knowledge into an oracle.
            raise NotFound()
        if record["visibility"] == "public" and not self._reader.is_publicly_released(artifact_id):
            raise NotFound()
        _, body = self._reader.read_verified(artifact_id)
        return ArtifactDownload(
            artifact_id=artifact_id,
            body=body,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="artifact-{artifact_id}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "public, max-age=31536000, immutable"
                if record["visibility"] == "public"
                else "private, no-store",
            },
        )

    @classmethod
    def _may_read(cls, principal: ArtifactPrincipal, artifact_id: UUID, visibility: str) -> bool:
        if visibility == "public":
            return bool(
                principal.roles
                & (
                    cls._HIDDEN_READERS
                    | cls._INTERNAL_READERS
                    | {"public_reader", "solve_supervisor"}
                )
            )
        if visibility == "internal":
            if "solve_supervisor" in principal.roles:
                return artifact_id in principal.artifact_scope
            return bool(principal.roles & cls._INTERNAL_READERS)
        if visibility == "hidden":
            return "solve_supervisor" not in principal.roles and bool(
                principal.roles & cls._HIDDEN_READERS
            )
        return False


def require_upload_role(principal: ArtifactPrincipal, visibility: str) -> None:
    if visibility == "hidden" and principal.roles & {"curator", "evaluator", "administrator"}:
        return
    if visibility == "internal" and principal.roles & {
        "operator",
        "curator",
        "evaluator",
        "administrator",
    }:
        return
    if visibility == "public" and principal.roles & {"publisher", "administrator"}:
        raise AuthorizationError("public artifacts require reviewed projection publication")
    raise AuthorizationError()


class ArtifactUploadService:
    """Application write entrypoint; storage repositories remain behind role checks."""

    def __init__(self, uploader: ArtifactUploader) -> None:
        self._uploader = uploader

    def begin_upload(
        self,
        *,
        principal: ArtifactPrincipal,
        visibility: str,
        encryption_domain: str,
        expected_digest: str,
        expected_size: int,
        media_type: str,
    ) -> UUID:
        require_upload_role(principal, visibility)
        return self._uploader.begin_upload(
            owner=principal.subject_id,
            visibility=visibility,
            encryption_domain=encryption_domain,
            expected_digest=expected_digest,
            expected_size=expected_size,
            media_type=media_type,
        )

    def upload(self, *, principal: ArtifactPrincipal, upload_id: UUID, body: bytes) -> None:
        visibility = self._uploader.upload_visibility(
            upload_id=upload_id, owner=principal.subject_id
        )
        require_upload_role(principal, visibility)
        self._uploader.upload(upload_id=upload_id, owner=principal.subject_id, body=body)

    def finalize(self, *, principal: ArtifactPrincipal, upload_id: UUID) -> UUID:
        visibility = self._uploader.upload_visibility(
            upload_id=upload_id, owner=principal.subject_id
        )
        require_upload_role(principal, visibility)
        return self._uploader.finalize(upload_id=upload_id, owner=principal.subject_id)
