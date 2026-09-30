"""Response storage backed by the verified artifact repository."""

from __future__ import annotations

import hashlib
from uuid import UUID

from polycodebench_persistence.artifacts import ArtifactRepository


class ArtifactResponseStore:
    """Stores request/response bytes as ``internal`` artifacts in a dedicated encryption domain.

    Model traffic can quote task material, so it is never public; publication of any derived
    projection goes through the reviewed declassification path.
    """

    def __init__(
        self, artifacts: ArtifactRepository, *, owner: str, encryption_domain: str = "model-gateway"
    ) -> None:
        self._artifacts = artifacts
        self._owner = owner
        self._domain = encryption_domain

    def put(self, data: bytes, *, kind: str, media_type: str) -> UUID:
        upload_id = self._artifacts.begin_upload(
            owner=self._owner,
            visibility="internal",
            encryption_domain=self._domain,
            expected_digest="sha256:" + hashlib.sha256(data).hexdigest(),
            expected_size=len(data),
            media_type=media_type,
        )
        self._artifacts.upload(upload_id=upload_id, owner=self._owner, body=data)
        return self._artifacts.finalize(upload_id=upload_id, owner=self._owner)

    def get(self, artifact_id: UUID) -> bytes:
        return self._artifacts.read_verified(artifact_id)[1]
