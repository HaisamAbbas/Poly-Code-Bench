"""Immutable model configuration documents and their model revision rows."""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from polycodebench_core.application_errors import InvalidState
from polycodebench_core.canonical import (
    canonical_digest,
    canonical_document_bytes,
    canonical_document_digest,
)
from polycodebench_core.model_planning import ModelConfig
from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import config_document, model_revision


class PostgresModelConfigRepository:
    """Stores the fully resolved ``model_config`` once per digest (spec 4.2: resolve then hash)."""

    def __init__(self, engine: Engine, artifacts: ArtifactRepository, *, owner: str) -> None:
        self._engine = engine
        self._artifacts = artifacts
        self._owner = owner

    def register(self, config: ModelConfig, *, domain: str = "model-config") -> tuple[UUID, UUID]:
        """Return ``(config_document_id, model_revision_id)``; identical input is idempotent."""
        digest = canonical_document_digest(config)
        body = canonical_document_bytes(config)
        try:
            with self._engine.connect() as connection:
                existing = _document_id(connection, "model_config", digest)
            if existing is None:
                upload = self._artifacts.begin_upload(
                    owner=self._owner,
                    visibility="internal",
                    encryption_domain=domain,
                    expected_digest="sha256:" + hashlib.sha256(body).hexdigest(),
                    expected_size=len(body),
                    media_type="application/json",
                )
                self._artifacts.upload(upload_id=upload, owner=self._owner, body=body)
                artifact_id = self._artifacts.finalize(upload_id=upload, owner=self._owner)
            with self._engine.begin() as connection:
                capabilities = config.declared_capabilities.model_dump(mode="json")
                capabilities_digest = canonical_digest(capabilities)
                capabilities_id = _document_id(
                    connection, "model_capabilities", capabilities_digest
                )
                config_id = existing
                if config_id is None:
                    if capabilities_id is None:
                        capabilities_id = uuid4()
                        connection.execute(
                            insert(config_document).values(
                                id=capabilities_id,
                                kind="model_capabilities",
                                version_label=capabilities_digest[7:19],
                                digest=capabilities_digest,
                                canonical_artifact_id=artifact_id,
                                schema_version=1,
                                document=capabilities,
                            )
                        )
                    config_id = uuid4()
                    connection.execute(
                        insert(config_document).values(
                            id=config_id,
                            kind="model_config",
                            version_label=digest[7:19],
                            digest=digest,
                            canonical_artifact_id=artifact_id,
                            schema_version=1,
                            document=config.model_dump(mode="json"),
                        )
                    )
                revision_id = self._revision(connection, config, capabilities_id)
            return config_id, revision_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def load(self, config_document_id: UUID) -> ModelConfig:
        """Load one registered, fully resolved model configuration.

        The judge panel names its judge by configuration id, so the panel document stays a small
        frozen file and the resolved configuration comes from the one place that stores it.
        """
        with self._engine.connect() as connection:
            document = connection.execute(
                select(config_document.c.document).where(
                    config_document.c.id == config_document_id,
                    config_document.c.kind == "model_config",
                )
            ).scalar_one_or_none()
        if document is None:
            raise InvalidState("no such registered model_config document")
        return ModelConfig.model_validate(document, strict=False)

    @staticmethod
    def _revision(
        connection: Connection, config: ModelConfig, capabilities_id: UUID | None
    ) -> UUID:
        query = select(model_revision.c.id).where(
            model_revision.c.provider == config.provider_kind.value,
            model_revision.c.name == config.model,
            model_revision.c.immutable_revision.is_(None)
            if config.immutable_revision is None
            else model_revision.c.immutable_revision == config.immutable_revision,
        )
        found = connection.execute(query).scalar_one_or_none()
        if found is not None:
            return UUID(str(found))
        if capabilities_id is None:
            capabilities_id = connection.execute(
                select(config_document.c.id)
                .where(config_document.c.kind == "model_capabilities")
                .limit(1)
            ).scalar_one()
        revision_id = uuid4()
        connection.execute(
            insert(model_revision).values(
                id=revision_id,
                provider=config.provider_kind.value,
                name=config.model,
                immutable_revision=config.immutable_revision,
                endpoint_registration_id=config.endpoint_id,
                capabilities_config_id=capabilities_id,
            )
        )
        return revision_id


def _document_id(connection: Connection, kind: str, digest: str) -> UUID | None:
    return connection.execute(
        select(config_document.c.id).where(
            config_document.c.kind == kind, config_document.c.digest == digest
        )
    ).scalar_one_or_none()
