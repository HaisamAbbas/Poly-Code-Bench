"""Transactional artifact intake and immutable verified-reference repository."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidReference,
    InvalidState,
    NotFound,
    PersistenceConflict,
    PersistenceUnavailable,
)
from sqlalchemy import and_, insert, or_, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    artifact,
    artifact_declassification,
    artifact_edge,
    artifact_projection_approval,
    artifact_quota,
    artifact_retention_hold,
    artifact_upload,
    audit_event,
)
from polycodebench_persistence.object_store import ObjectStoreError, S3ArtifactStore

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_VISIBILITY = {"hidden", "internal", "public"}
_DOMAIN = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
_CANONICAL_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}/[0-9a-f]{2}/[0-9a-f]{64}$", re.ASCII)


class ArtifactRepository:
    """Owns upload state, quota reservations, artifact references and retention."""

    def __init__(self, engine: Engine, store: S3ArtifactStore, *, max_upload_bytes: int) -> None:
        if max_upload_bytes < 0:
            raise ValueError("max upload size must be nonnegative")
        self._engine = engine
        self._store = store
        self._max_upload_bytes = max_upload_bytes

    def begin_upload(
        self,
        *,
        owner: str,
        visibility: str,
        encryption_domain: str,
        expected_digest: str,
        expected_size: int,
        media_type: str,
        ttl: timedelta = timedelta(hours=24),
    ) -> UUID:
        if (
            not owner
            or visibility not in _VISIBILITY
            or not _DOMAIN.fullmatch(encryption_domain)
            or not _DIGEST.fullmatch(expected_digest)
            or expected_size < 0
            or expected_size > self._max_upload_bytes
            or not media_type
            or len(media_type) > 255
            or ttl <= timedelta(0)
        ):
            raise InvalidState("artifact upload metadata is invalid")
        upload_id = uuid4()
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                reservation = connection.execute(
                    update(artifact_quota)
                    .where(
                        artifact_quota.c.visibility == visibility,
                        artifact_quota.c.encryption_domain == encryption_domain,
                        artifact_quota.c.used_bytes
                        + artifact_quota.c.reserved_bytes
                        + expected_size
                        <= artifact_quota.c.max_bytes,
                    )
                    .values(
                        reserved_bytes=artifact_quota.c.reserved_bytes + expected_size,
                        row_version=artifact_quota.c.row_version + 1,
                    )
                    .returning(artifact_quota.c.visibility)
                ).first()
                if reservation is None:
                    raise InvalidState("artifact quota is unavailable or exceeded")
                connection.execute(
                    insert(artifact_upload).values(
                        id=upload_id,
                        owner_subject=owner,
                        visibility=visibility,
                        encryption_domain=encryption_domain,
                        expected_digest=expected_digest,
                        expected_size_bytes=expected_size,
                        media_type=media_type,
                        provisional_key=f"provisional/{upload_id}",
                        state="reserved",
                        expires_at=now + ttl,
                        garbage_collect_after=now + ttl + timedelta(days=30),
                    )
                )
            return upload_id
        except (InvalidState, InvalidReference):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def upload(self, *, upload_id: UUID, owner: str, body: bytes) -> None:
        row = self._owned_upload(upload_id, owner)
        if row["state"] == "reserved" and _is_expired(row):
            self._expire_upload(row)
            raise InvalidState("upload reservation has expired")
        if row["state"] == "uploaded":
            # Retry is accepted only for the same exact bytes.
            prior = self._store.get_bytes(
                str(row["visibility"]),
                str(row["provisional_key"]),
                max_bytes=self._max_upload_bytes,
            )
            if prior == body:
                return
            raise PersistenceConflict("upload retry differs from the stored bytes")
        if row["state"] != "reserved":
            raise InvalidState("upload is not writable")
        if len(body) > self._max_upload_bytes:
            self.reject(upload_id=upload_id, owner=owner, failure_code="size_limit")
            raise InvalidState("artifact exceeds configured size limit")
        try:
            try:
                self._store.put_provisional(str(row["visibility"]), str(upload_id), body)
            except ObjectStoreError:
                # The object may have landed before the previous process committed
                # state='uploaded'. Resume only when the existing stage bytes match.
                prior = self._store.get_bytes(
                    str(row["visibility"]),
                    str(row["provisional_key"]),
                    max_bytes=self._max_upload_bytes,
                )
                if prior != body:
                    raise PersistenceConflict(
                        "provisional retry differs from stored bytes"
                    ) from None
            with self._engine.begin() as connection:
                changed = connection.execute(
                    update(artifact_upload)
                    .where(
                        artifact_upload.c.id == upload_id,
                        artifact_upload.c.owner_subject == owner,
                        artifact_upload.c.state == "reserved",
                    )
                    .values(state="uploaded")
                ).rowcount
                if changed != 1:
                    raise InvalidState("upload state changed during write")
        except DBAPIError as error:
            raise map_database_error(error) from None

    def upload_visibility(self, *, upload_id: UUID, owner: str) -> str:
        return str(self._owned_upload(upload_id, owner)["visibility"])

    def finalize(self, *, upload_id: UUID, owner: str) -> UUID:
        return self._finalize(upload_id=upload_id, owner=owner)

    def finalize_publication(
        self, *, upload_id: UUID, owner: str, approval_id: UUID, request_id: str
    ) -> UUID:
        return self._finalize(
            upload_id=upload_id, owner=owner, approval_id=approval_id, request_id=request_id
        )

    def _finalize(
        self,
        *,
        upload_id: UUID,
        owner: str,
        approval_id: UUID | None = None,
        request_id: str | None = None,
    ) -> UUID:
        row = self._owned_upload(upload_id, owner)
        if approval_id is not None and row["visibility"] != "public":
            raise InvalidState("publication upload must use public visibility")
        if approval_id is None and row["visibility"] == "public":
            raise InvalidState("public artifacts require reviewed publication")
        if row["state"] == "verified" and row["artifact_id"] is not None:
            artifact_id = UUID(str(row["artifact_id"]))
            return self._publication_result(approval_id, artifact_id)
        if row["state"] in {"uploaded", "finalizing"} and _is_expired(row):
            self._expire_upload(row)
            raise InvalidState("upload reservation has expired")
        if row["state"] not in {"uploaded", "finalizing"}:
            raise InvalidState("upload is not ready to finalize")
        try:
            with self._engine.begin() as connection:
                locked = (
                    connection.execute(
                        select(artifact_upload)
                        .where(artifact_upload.c.id == upload_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one()
                )
                if locked["owner_subject"] != owner:
                    raise NotFound()
                if locked["state"] == "verified" and locked["artifact_id"]:
                    artifact_id = UUID(str(locked["artifact_id"]))
                    return self._publication_result(approval_id, artifact_id)
                if locked["state"] == "uploaded":
                    connection.execute(
                        update(artifact_upload)
                        .where(artifact_upload.c.id == upload_id)
                        .values(state="finalizing")
                    )
                elif locked["state"] != "finalizing":
                    raise InvalidState("upload is not ready to finalize")
                row = dict(locked)
        except DBAPIError as error:
            raise map_database_error(error) from None

        try:
            data = self._store.get_bytes(
                str(row["visibility"]),
                str(row["provisional_key"]),
                max_bytes=self._max_upload_bytes,
            )
        except ObjectStoreError:
            self.reject(upload_id=upload_id, owner=owner, failure_code="object_unavailable")
            raise InvalidState("provisional artifact could not be verified") from None
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if len(data) != row["expected_size_bytes"] or digest != row["expected_digest"]:
            self.reject(upload_id=upload_id, owner=owner, failure_code="integrity_mismatch")
            self._delete_provisional(str(row["visibility"]), str(row["provisional_key"]))
            raise InvalidState("uploaded bytes do not match the declared size and SHA-256")

        key = self._store.put_verified(
            str(row["visibility"]), str(row["encryption_domain"]), digest, data
        )
        # Conditional object creation may have found a prior object. Never rely on ETag:
        # read that canonical key and independently compare both length and SHA-256.
        stored = self._store.get_bytes(
            str(row["visibility"]), key, max_bytes=self._max_upload_bytes
        )
        if (
            len(stored) != len(data)
            or hashlib.sha256(stored).digest() != hashlib.sha256(data).digest()
        ):
            self.reject(upload_id=upload_id, owner=owner, failure_code="canonical_object_mismatch")
            raise InvalidState("canonical artifact failed independent integrity verification")
        artifact_id = self._commit_verified(
            row, key, digest, approval_id=approval_id, request_id=request_id
        )
        self._delete_provisional(str(row["visibility"]), str(row["provisional_key"]))
        return artifact_id

    def reject(self, *, upload_id: UUID, owner: str, failure_code: str) -> None:
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        select(artifact_upload)
                        .where(artifact_upload.c.id == upload_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None or row["owner_subject"] != owner:
                    raise NotFound()
                if row["state"] in {"verified", "rejected", "expired"}:
                    return
                connection.execute(
                    update(artifact_upload)
                    .where(artifact_upload.c.id == upload_id)
                    .values(state="rejected", failure_code=failure_code[:64])
                )
                connection.execute(
                    update(artifact_quota)
                    .where(
                        artifact_quota.c.visibility == row["visibility"],
                        artifact_quota.c.encryption_domain == row["encryption_domain"],
                    )
                    .values(
                        reserved_bytes=artifact_quota.c.reserved_bytes - row["expected_size_bytes"],
                        row_version=artifact_quota.c.row_version + 1,
                    )
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    def get_verified(self, artifact_id: UUID) -> dict[str, object]:
        with self._engine.connect() as connection:
            record = (
                connection.execute(
                    select(artifact).where(
                        artifact.c.id == artifact_id, artifact.c.status == "verified"
                    )
                )
                .mappings()
                .one_or_none()
            )
        if record is None:
            raise NotFound()
        return dict(record)

    def read_verified(self, artifact_id: UUID) -> tuple[dict[str, object], bytes]:
        record = self.get_verified(artifact_id)
        data = self._store.get_bytes(
            str(record["visibility"]), str(record["storage_key"]), max_bytes=self._max_upload_bytes
        )
        if (
            len(data) != record["size_bytes"]
            or "sha256:" + hashlib.sha256(data).hexdigest() != record["content_digest"]
        ):
            raise InvalidState("stored artifact failed integrity verification")
        return record, data

    def add_manifest_edge(self, *, parent_id: UUID, child_id: UUID, relation: str) -> None:
        if not relation or len(relation) > 64:
            raise InvalidState("manifest relation is invalid")
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(artifact_edge).values(
                        parent_artifact_id=parent_id, child_artifact_id=child_id, relation=relation
                    )
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    def approve_projection(
        self,
        *,
        source_artifact_id: UUID,
        projection_digest: str,
        approved_by: str,
        reason: str,
        request_id: str,
    ) -> UUID:
        if not _DIGEST.fullmatch(projection_digest) or not reason.strip() or not approved_by:
            raise InvalidState("projection approval is invalid")
        approval_id = uuid4()
        try:
            with self._engine.begin() as connection:
                source = connection.execute(
                    select(artifact.c.visibility, artifact.c.status).where(
                        artifact.c.id == source_artifact_id
                    )
                ).one_or_none()
                if source is None or source.status != "verified" or source.visibility == "public":
                    raise InvalidState("projection source must be verified and non-public")
                connection.execute(
                    insert(artifact_projection_approval).values(
                        id=approval_id,
                        source_artifact_id=source_artifact_id,
                        projection_digest=projection_digest,
                        approved_by=approved_by,
                        reason=reason,
                    )
                )
                connection.execute(
                    insert(audit_event).values(
                        actor_subject=approved_by,
                        action="artifact.projection.approve",
                        resource_type="artifact_projection_approval",
                        resource_id=str(approval_id),
                        request_id=request_id,
                        after_digest=projection_digest,
                        details={"source_artifact_id": str(source_artifact_id)},
                    )
                )
            return approval_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def get_projection_approval(self, approval_id: UUID) -> dict[str, object]:
        with self._engine.connect() as connection:
            approval = (
                connection.execute(
                    select(artifact_projection_approval).where(
                        artifact_projection_approval.c.id == approval_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        if approval is None:
            raise NotFound()
        return dict(approval)

    def published_projection(self, source_artifact_id: UUID) -> dict[str, object] | None:
        with self._engine.connect() as connection:
            record = (
                connection.execute(
                    select(artifact_declassification).where(
                        artifact_declassification.c.source_artifact_id == source_artifact_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(record) if record is not None else None

    def _publication_result(self, approval_id: UUID | None, artifact_id: UUID) -> UUID:
        if approval_id is None:
            return artifact_id
        approval = self.get_projection_approval(approval_id)
        published = self.published_projection(UUID(str(approval["source_artifact_id"])))
        if (
            published is None
            or published["public_artifact_id"] != artifact_id
            or published["review_digest"] != approval["projection_digest"]
            or published["approved_by"] != approval["approved_by"]
            or published["reason"] != approval["reason"]
            or not self.is_publicly_released(artifact_id)
        ):
            raise InvalidState("upload is not published under this approval")
        return artifact_id

    def is_publicly_released(self, artifact_id: UUID) -> bool:
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    select(artifact_declassification.c.public_artifact_id)
                    .join(
                        artifact,
                        artifact.c.id == artifact_declassification.c.public_artifact_id,
                    )
                    .join(
                        artifact_projection_approval,
                        and_(
                            artifact_projection_approval.c.source_artifact_id
                            == artifact_declassification.c.source_artifact_id,
                            artifact_projection_approval.c.projection_digest
                            == artifact.c.content_digest,
                            artifact_projection_approval.c.projection_digest
                            == artifact_declassification.c.review_digest,
                            artifact_projection_approval.c.approved_by
                            == artifact_declassification.c.approved_by,
                            artifact_projection_approval.c.reason
                            == artifact_declassification.c.reason,
                            artifact_projection_approval.c.created_at
                            <= artifact_declassification.c.created_at,
                        ),
                    )
                    .where(artifact_declassification.c.public_artifact_id == artifact_id)
                    .limit(1)
                ).first()
                is not None
            )

    def add_hold(self, *, artifact_id: UUID, actor: str, reason: str, request_id: str) -> UUID:
        if not reason.strip():
            raise InvalidState("retention reason is required")
        hold_id = uuid4()
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(artifact_retention_hold).values(
                        id=hold_id, artifact_id=artifact_id, reason=reason, held_by=actor
                    )
                )
                connection.execute(
                    insert(audit_event).values(
                        actor_subject=actor,
                        action="artifact.hold",
                        resource_type="artifact",
                        resource_id=str(artifact_id),
                        request_id=request_id,
                        details={"hold_id": str(hold_id)},
                    )
                )
            return hold_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def release_hold(self, *, hold_id: UUID, actor: str, request_id: str) -> None:
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                hold = (
                    connection.execute(
                        select(artifact_retention_hold)
                        .where(artifact_retention_hold.c.id == hold_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if hold is None or hold["released_at"] is not None:
                    raise NotFound()
                connection.execute(
                    update(artifact_retention_hold)
                    .where(artifact_retention_hold.c.id == hold_id)
                    .values(released_by=actor, released_at=now)
                )
                connection.execute(
                    insert(audit_event).values(
                        actor_subject=actor,
                        action="artifact.hold.release",
                        resource_type="artifact",
                        resource_id=str(hold["artifact_id"]),
                        request_id=request_id,
                        details={"hold_id": str(hold_id)},
                    )
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    def collect_garbage(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(artifact_upload).where(
                        or_(
                            artifact_upload.c.state.in_(["reserved", "uploaded", "finalizing"])
                            & (artifact_upload.c.expires_at < current),
                            artifact_upload.c.state.in_(["verified", "rejected", "expired"])
                            & (artifact_upload.c.garbage_collect_after < current),
                        ),
                    )
                )
                .mappings()
                .all()
            )
        removed = 0
        for row in rows:
            # Verified canonical artifacts are retained. Only the staging key is collected.
            if row["state"] in {"reserved", "uploaded", "finalizing"}:
                self._expire_upload(dict(row))
            elif row["garbage_collect_after"] < current:
                self._delete_provisional(str(row["visibility"]), str(row["provisional_key"]))
                removed += 1
        removed += self._collect_orphan_canonical_objects(current)
        return removed

    def _collect_orphan_canonical_objects(self, now: datetime) -> int:
        referenced: dict[str, set[str]] = {visibility: set() for visibility in _VISIBILITY}
        with self._engine.connect() as connection:
            records = connection.execute(
                select(artifact.c.visibility, artifact.c.storage_key)
            ).all()
            in_flight = connection.execute(
                select(
                    artifact_upload.c.visibility,
                    artifact_upload.c.encryption_domain,
                    artifact_upload.c.expected_digest,
                ).where(
                    artifact_upload.c.state.in_(["uploaded", "finalizing"]),
                    artifact_upload.c.expires_at > now,
                )
            ).all()
        for visibility, key in records:
            referenced[str(visibility)].add(str(key))
        for visibility, domain, digest in in_flight:
            if _DIGEST.fullmatch(str(digest)) and _DOMAIN.fullmatch(str(domain)):
                referenced[str(visibility)].add(f"{domain}/{str(digest)[7:9]}/{str(digest)[7:]}")
        cutoff = now - timedelta(days=30)
        removed = 0
        for visibility in sorted(_VISIBILITY):
            for key, modified_at in self._store.list_objects(visibility):
                if key in referenced[visibility] or not _CANONICAL_KEY.fullmatch(key):
                    continue
                if modified_at.tzinfo is None:
                    modified_at = modified_at.replace(tzinfo=UTC)
                if modified_at >= cutoff:
                    continue
                self._delete_provisional(visibility, key)
                removed += 1
        return removed

    def _expire_upload(self, row: dict[str, object]) -> None:
        with self._engine.begin() as connection:
            locked = (
                connection.execute(
                    select(artifact_upload)
                    .where(artifact_upload.c.id == row["id"])
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if locked is None or locked["state"] not in {"reserved", "uploaded", "finalizing"}:
                return
            connection.execute(
                update(artifact_upload)
                .where(artifact_upload.c.id == row["id"])
                .values(state="expired", failure_code="expired")
            )
            connection.execute(
                update(artifact_quota)
                .where(
                    artifact_quota.c.visibility == row["visibility"],
                    artifact_quota.c.encryption_domain == row["encryption_domain"],
                )
                .values(
                    reserved_bytes=artifact_quota.c.reserved_bytes - row["expected_size_bytes"],
                    row_version=artifact_quota.c.row_version + 1,
                )
            )

    def _owned_upload(self, upload_id: UUID, owner: str) -> dict[str, object]:
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(artifact_upload).where(artifact_upload.c.id == upload_id)
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None or row["owner_subject"] != owner:
                raise NotFound()
            return dict(row)
        except DBAPIError as error:
            raise map_database_error(error) from None

    def _commit_verified(
        self,
        row: dict[str, object],
        key: str,
        digest: str,
        *,
        approval_id: UUID | None = None,
        request_id: str | None = None,
    ) -> UUID:
        artifact_id = uuid4()
        if row["visibility"] == "public" and approval_id is None:
            raise InvalidState("public artifacts require reviewed publication")
        try:
            with self._engine.begin() as connection:
                current_upload = (
                    connection.execute(
                        select(artifact_upload)
                        .where(artifact_upload.c.id == row["id"])
                        .with_for_update()
                    )
                    .mappings()
                    .one()
                )
                if current_upload["state"] == "verified":
                    return self._publication_result(
                        approval_id, UUID(str(current_upload["artifact_id"]))
                    )
                if current_upload["state"] != "finalizing":
                    raise InvalidState("upload is no longer ready to finalize")
                approval = None
                prior_publication = None
                if approval_id is not None:
                    approval = (
                        connection.execute(
                            select(artifact_projection_approval)
                            .where(artifact_projection_approval.c.id == approval_id)
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        approval is None
                        or approval["projection_digest"] != digest
                        or approval["approved_by"] == row["owner_subject"]
                    ):
                        raise InvalidState("publication does not match a separate approval")
                    prior_publication = (
                        connection.execute(
                            select(artifact_declassification)
                            .where(
                                artifact_declassification.c.source_artifact_id
                                == approval["source_artifact_id"]
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if prior_publication is not None and any(
                        prior_publication[field] != expected
                        for field, expected in (
                            ("review_digest", digest),
                            ("approved_by", approval["approved_by"]),
                            ("reason", approval["reason"]),
                        )
                    ):
                        raise PersistenceConflict(
                            "source artifact already has a different public projection"
                        )
                existing = (
                    connection.execute(
                        select(artifact)
                        .where(
                            artifact.c.visibility == row["visibility"],
                            artifact.c.encryption_domain == row["encryption_domain"],
                            artifact.c.content_digest == digest,
                            artifact.c.status == "verified",
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing:
                    existing_bytes = self._store.get_bytes(
                        str(row["visibility"]),
                        str(existing["storage_key"]),
                        max_bytes=self._max_upload_bytes,
                    )
                    if (
                        existing["size_bytes"] != len(existing_bytes)
                        or hashlib.sha256(existing_bytes).hexdigest() != digest[7:]
                    ):
                        raise InvalidState("deduplicated artifact size mismatch")
                    artifact_id = UUID(str(existing["id"]))
                    added = False
                else:
                    connection.execute(
                        insert(artifact).values(
                            id=artifact_id,
                            visibility=row["visibility"],
                            content_digest=digest,
                            size_bytes=row["expected_size_bytes"],
                            media_type=row["media_type"],
                            storage_key=key,
                            encryption_domain=row["encryption_domain"],
                            status="provisional",
                        )
                    )
                    connection.execute(
                        update(artifact)
                        .where(artifact.c.id == artifact_id)
                        .values(status="verified")
                    )
                    added = True
                if (
                    prior_publication is not None
                    and prior_publication["public_artifact_id"] != artifact_id
                ):
                    raise PersistenceConflict(
                        "source artifact already has a different public projection"
                    )
                changed = connection.execute(
                    update(artifact_upload)
                    .where(
                        artifact_upload.c.id == row["id"], artifact_upload.c.state == "finalizing"
                    )
                    .values(state="verified", artifact_id=artifact_id)
                ).rowcount
                if changed != 1:
                    raise InvalidState("upload state changed during finalization")
                connection.execute(
                    update(artifact_quota)
                    .where(
                        artifact_quota.c.visibility == row["visibility"],
                        artifact_quota.c.encryption_domain == row["encryption_domain"],
                    )
                    .values(
                        reserved_bytes=artifact_quota.c.reserved_bytes - row["expected_size_bytes"],
                        used_bytes=artifact_quota.c.used_bytes
                        + (row["expected_size_bytes"] if added else 0),
                        row_version=artifact_quota.c.row_version + 1,
                    )
                )
                connection.execute(
                    insert(audit_event).values(
                        actor_subject=row["owner_subject"],
                        action="artifact.verified",
                        resource_type="artifact",
                        resource_id=str(artifact_id),
                        request_id=str(row["id"]),
                        after_digest=digest,
                        details={"visibility": row["visibility"], "upload_id": str(row["id"])},
                    )
                )
                if approval is not None and prior_publication is None:
                    connection.execute(
                        insert(artifact_declassification).values(
                            source_artifact_id=approval["source_artifact_id"],
                            public_artifact_id=artifact_id,
                            review_digest=digest,
                            approved_by=approval["approved_by"],
                            reason=approval["reason"],
                        )
                    )
                    connection.execute(
                        insert(audit_event).values(
                            actor_subject=row["owner_subject"],
                            action="artifact.declassify",
                            resource_type="artifact",
                            resource_id=str(artifact_id),
                            request_id=request_id or str(row["id"]),
                            after_digest=digest,
                            details={
                                "source_artifact_id": str(approval["source_artifact_id"]),
                                "approval_id": str(approval_id),
                            },
                        )
                    )
            return artifact_id
        except DBAPIError as error:
            mapped = map_database_error(error)
            if isinstance(mapped, PersistenceConflict):
                # A concurrent upload won the unique digest race. Replay finds its row;
                # this call remains retryable because the DB transaction rolled back.
                raise PersistenceConflict(
                    "concurrent artifact registration; retry finalization"
                ) from None
            raise mapped from None

    def _delete_provisional(self, visibility: str, key: str) -> None:
        try:
            self._store.delete(visibility, key)
        except ObjectStoreError:
            # Retried by the next GC pass; a committed verified reference is unaffected.
            return


def _is_expired(row: dict[str, object]) -> bool:
    expires_at = row["expires_at"]
    return isinstance(expires_at, datetime) and expires_at <= datetime.now(UTC)


class PostgresPublicArtifactReader:
    """Read only artifacts admitted by the database's public-release allowlist view."""

    def __init__(self, engine: Engine, store: S3ArtifactStore, *, max_read_bytes: int) -> None:
        if max_read_bytes < 0:
            raise ValueError("maximum public artifact read size must be nonnegative")
        self._engine = engine
        self._store = store
        self._max_read_bytes = max_read_bytes

    def get_verified(self, artifact_id: UUID) -> dict[str, object]:
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        text(
                            "SELECT id, content_digest, size_bytes, media_type, storage_key "
                            "FROM public_artifact_catalog WHERE id = :artifact_id"
                        ),
                        {"artifact_id": artifact_id},
                    )
                    .mappings()
                    .one_or_none()
                )
        except DBAPIError as error:
            raise map_database_error(error) from None
        if row is None:
            raise NotFound()
        return {**dict(row), "visibility": "public", "status": "verified"}

    def read_verified(self, artifact_id: UUID) -> tuple[dict[str, object], bytes]:
        record = self.get_verified(artifact_id)
        try:
            data = self._store.get_bytes(
                "public", str(record["storage_key"]), max_bytes=self._max_read_bytes
            )
        except ObjectStoreError:
            raise PersistenceUnavailable("public artifact storage read failed") from None
        if (
            len(data) != record["size_bytes"]
            or "sha256:" + hashlib.sha256(data).hexdigest() != record["content_digest"]
        ):
            raise InvalidState("stored public artifact failed integrity verification")
        return record, data

    def is_publicly_released(self, artifact_id: UUID) -> bool:
        try:
            with self._engine.connect() as connection:
                return (
                    connection.execute(
                        text("SELECT 1 FROM public_artifact_catalog WHERE id = :artifact_id"),
                        {"artifact_id": artifact_id},
                    ).first()
                    is not None
                )
        except DBAPIError as error:
            raise map_database_error(error) from None
