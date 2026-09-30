"""Role assignment persistence; credentials and tokens are never stored."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from polycodebench_core.application_errors import OptimisticVersionConflict
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import audit_event, subject_role


class PostgresIdentityRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def roles_for_subject(self, subject_id: str) -> tuple[str, ...]:
        try:
            with self._engine.connect() as connection:
                rows = (
                    connection.execute(
                        select(subject_role.c.role)
                        .where(
                            subject_role.c.subject_id == subject_id,
                            subject_role.c.revoked_at.is_(None),
                        )
                        .order_by(subject_role.c.role)
                    )
                    .scalars()
                    .all()
                )
                return tuple(rows)
        except DBAPIError as error:
            raise map_database_error(error) from None

    def grant_role(
        self,
        *,
        subject_id: str,
        role: str,
        actor_subject: str,
        request_id: str,
        expected_version: int | None = None,
    ) -> int:
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                existing = (
                    connection.execute(
                        select(subject_role)
                        .where(subject_role.c.subject_id == subject_id, subject_role.c.role == role)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is None:
                    if expected_version not in (None, 0):
                        raise OptimisticVersionConflict()
                    connection.execute(
                        insert(subject_role).values(
                            subject_id=subject_id,
                            role=role,
                            granted_by=actor_subject,
                            row_version=0,
                            granted_at=now,
                            revoked_at=None,
                        )
                    )
                    new_version = 0
                else:
                    version = int(existing["row_version"])
                    if expected_version is not None and version != expected_version:
                        raise OptimisticVersionConflict()
                    if existing["revoked_at"] is None:
                        return version
                    new_version = version + 1
                    changed = connection.execute(
                        update(subject_role)
                        .where(
                            subject_role.c.subject_id == subject_id,
                            subject_role.c.role == role,
                            subject_role.c.row_version == version,
                        )
                        .values(
                            granted_by=actor_subject,
                            granted_at=now,
                            revoked_at=None,
                            row_version=new_version,
                        )
                    ).rowcount
                    if changed != 1:
                        raise OptimisticVersionConflict()
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=actor_subject,
                        action="identity.role.grant",
                        resource_type="subject_role",
                        resource_id=f"{subject_id}:{role}",
                        request_id=request_id,
                        before_digest=None,
                        after_digest=None,
                        details={"role": role, "row_version": new_version},
                    )
                )
                return new_version
        except OptimisticVersionConflict:
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def revoke_role(
        self,
        *,
        subject_id: str,
        role: str,
        actor_subject: str,
        request_id: str,
        expected_version: int,
    ) -> int:
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                changed = connection.execute(
                    update(subject_role)
                    .where(
                        subject_role.c.subject_id == subject_id,
                        subject_role.c.role == role,
                        subject_role.c.row_version == expected_version,
                        subject_role.c.revoked_at.is_(None),
                    )
                    .values(revoked_at=now, row_version=expected_version + 1)
                ).rowcount
                if changed != 1:
                    raise OptimisticVersionConflict()
                connection.execute(
                    insert(audit_event).values(
                        id=uuid4(),
                        actor_subject=actor_subject,
                        action="identity.role.revoke",
                        resource_type="subject_role",
                        resource_id=f"{subject_id}:{role}",
                        request_id=request_id,
                        before_digest=None,
                        after_digest=None,
                        details={"role": role, "row_version": expected_version + 1},
                    )
                )
                return expected_version + 1
        except OptimisticVersionConflict:
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None
