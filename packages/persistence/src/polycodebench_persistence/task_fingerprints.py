"""Append-only persistence for private component fingerprint features."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidReference,
    InvalidState,
    PersistenceConflict,
)
from polycodebench_core.task_fingerprints import (
    ComponentFingerprint,
    PrivateFingerprintArtifactBindings,
)
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import artifact, audit_component, fingerprint


@dataclass(frozen=True, slots=True)
class FingerprintWrite:
    component_id: UUID
    feature_count: int
    created: bool


class PostgresTaskFingerprintRepository:
    """Store immutable feature rows only when every feature has a verified private artifact."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def persist_component(
        self,
        *,
        component_id: UUID,
        result: ComponentFingerprint,
        artifacts: PrivateFingerprintArtifactBindings,
    ) -> FingerprintWrite:
        if component_id != result.component_id:
            raise InvalidReference("fingerprint result does not target the requested component")
        if result.exact_digest is None or not result.private_features:
            raise InvalidState("blocked or empty fingerprint results cannot be persisted")

        features = {item.feature_kind: item for item in result.private_features}
        bindings = {item.feature_kind: item for item in artifacts.artifacts}
        if set(features) != set(bindings):
            raise InvalidState(
                "private artifact bindings do not match available fingerprint features"
            )
        if any(
            bindings[key].artifact_digest != value.payload_digest for key, value in features.items()
        ):
            raise InvalidState("private fingerprint artifact digest does not match its payload")

        method_version = result.method_version
        try:
            with self._engine.begin() as connection:
                component = (
                    connection.execute(
                        select(audit_component.c.component_digest).where(
                            audit_component.c.id == component_id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if component is None or component["component_digest"] != result.component_digest:
                    raise InvalidReference("fingerprint component is missing or has changed")

                artifact_ids = tuple(binding.artifact_id for binding in bindings.values())
                artifact_rows = (
                    connection.execute(
                        select(
                            artifact.c.id,
                            artifact.c.content_digest,
                            artifact.c.media_type,
                            artifact.c.status,
                            artifact.c.visibility,
                        ).where(artifact.c.id.in_(artifact_ids))
                    )
                    .mappings()
                    .all()
                )
                actual_artifacts = {row["id"]: row for row in artifact_rows}
                for kind, feature in features.items():
                    binding = bindings[kind]
                    artifact_row = actual_artifacts.get(binding.artifact_id)
                    if (
                        artifact_row is None
                        or artifact_row["status"] != "verified"
                        or artifact_row["visibility"] != "hidden"
                        or artifact_row["content_digest"] != feature.payload_digest
                        or artifact_row["media_type"] != "application/json"
                    ):
                        raise InvalidReference(
                            "private fingerprint artifact is missing, unverified, or public"
                        )

                existing_rows = (
                    connection.execute(
                        select(
                            fingerprint.c.feature_kind,
                            fingerprint.c.feature_digest,
                            fingerprint.c.private_artifact_id,
                        ).where(
                            fingerprint.c.component_id == component_id,
                            fingerprint.c.method_version == method_version,
                        )
                    )
                    .mappings()
                    .all()
                )
                expected_rows = {
                    (
                        kind,
                        feature.feature_digest,
                        bindings[kind].artifact_id,
                    )
                    for kind, feature in features.items()
                }
                if existing_rows:
                    actual_rows = {
                        (row["feature_kind"], row["feature_digest"], row["private_artifact_id"])
                        for row in existing_rows
                    }
                    if actual_rows != expected_rows:
                        raise PersistenceConflict(
                            "stored fingerprint features conflict with this immutable retry"
                        )
                    return FingerprintWrite(
                        component_id=component_id,
                        feature_count=len(features),
                        created=False,
                    )

                connection.execute(
                    insert(fingerprint),
                    [
                        {
                            "id": uuid4(),
                            "component_id": component_id,
                            "feature_kind": kind,
                            "method_version": method_version,
                            "feature_digest": feature.feature_digest,
                            "private_artifact_id": bindings[kind].artifact_id,
                        }
                        for kind, feature in features.items()
                    ],
                )
                return FingerprintWrite(
                    component_id=component_id,
                    feature_count=len(features),
                    created=True,
                )
        except (InvalidReference, InvalidState, PersistenceConflict):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None
