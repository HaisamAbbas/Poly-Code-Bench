"""Shared read-only publication catalog and trusted snapshot synchronization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from polycodebench_core.application_errors import InvalidState, NotFound, PersistenceConflict
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from polycodebench_persistence.models import public_release_document, public_release_pointer

_PUBLIC_KEYS = frozenset(
    {
        "id",
        "slug",
        "version",
        "state",
        "content",
        "projection",
        "content_digest",
        "manifest",
        "withdrawal",
    }
)


class PostgresPublicReleaseCatalog:
    """Read public snapshots as ``pcb_public_reader``; synchronize as ``pcb_publisher``.

    The writer accepts only already verified, sanitized release documents. Signature, digest and
    typed content verification belongs to the publication/operations layer, which is deliberately
    above persistence in the package dependency graph.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _set_role(connection: Any, role: str) -> None:
        if role not in {"pcb_public_reader", "pcb_publisher"}:
            raise ValueError("unsupported public release database role")
        connection.exec_driver_sql(f"SET LOCAL ROLE {role}")

    def get(self, release_id: str) -> dict[str, Any]:
        with self._engine.begin() as connection:
            self._set_role(connection, "pcb_public_reader")
            document = connection.execute(
                select(public_release_document.c.document).where(
                    public_release_document.c.release_id == release_id
                )
            ).scalar_one_or_none()
        if document is None:
            raise NotFound("release does not exist")
        return cast(dict[str, Any], document)

    def public(self, release_id: str) -> dict[str, Any]:
        document = self.get(release_id)
        if document.get("state") not in {"published", "withdrawn"}:
            raise NotFound("release is not published")
        return {
            "projection": document["projection"],
            "manifest": document["manifest"],
            "withdrawal": document.get("withdrawal"),
        }

    def list_public(self) -> list[dict[str, Any]]:
        with self._engine.begin() as connection:
            self._set_role(connection, "pcb_public_reader")
            rows = connection.execute(
                select(public_release_document.c.document).order_by(
                    public_release_document.c.release_id
                )
            ).scalars()
            return [cast(dict[str, Any], row) for row in rows]

    def current(self, target: str = "local:board") -> dict[str, Any]:
        with self._engine.begin() as connection:
            self._set_role(connection, "pcb_public_reader")
            row = connection.execute(
                select(
                    public_release_pointer.c.generation,
                    public_release_pointer.c.release_id,
                ).where(public_release_pointer.c.target == target)
            ).one_or_none()
        return (
            {"generation": int(row.generation), "release_id": row.release_id}
            if row is not None
            else {"generation": 0, "release_id": None}
        )

    def audit(self) -> list[dict[str, Any]]:
        """Return only public publication timestamps, never reviewer/approver audit details."""
        with self._engine.begin() as connection:
            self._set_role(connection, "pcb_public_reader")
            rows = connection.execute(
                select(
                    public_release_document.c.release_id,
                    public_release_document.c.published_at,
                ).order_by(
                    public_release_document.c.published_at, public_release_document.c.release_id
                )
            ).all()
        return [
            {
                "action": "publish",
                "release_id": row.release_id,
                "occurred_at": row.published_at.isoformat(),
            }
            for row in rows
        ]

    def sync_publication(
        self,
        *,
        target: str,
        generation: int,
        current_release_id: str | None,
        documents: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Append verified public documents and advance a target pointer monotonically.

        Replays are idempotent. Existing published content cannot be replaced; the sole permitted
        mutation is a signed-release-preserving published-to-withdrawn notice.
        """
        if not target.strip() or len(target) > 160 or generation < 0:
            raise InvalidState("invalid publication target or generation")
        normalized = [self._validate_snapshot(row) for row in documents]
        by_id = {document["id"]: document for document, _ in normalized}
        if len(by_id) != len(normalized):
            raise InvalidState("duplicate release identity in publication sync")
        if current_release_id is not None and current_release_id not in by_id:
            # Existing DB history may contain it already, but require an explicit snapshot in this
            # batch so the deployment command always verifies the pointer's release identity.
            raise InvalidState("current pointer release must be included in the verified snapshot")

        inserted = 0
        withdrawn = 0
        pointer_advanced = False
        with self._engine.begin() as connection:
            self._set_role(connection, "pcb_publisher")
            for document, published_at in normalized:
                release_id = str(document["id"])
                existing = connection.execute(
                    select(
                        public_release_document.c.document,
                        public_release_document.c.published_at,
                    )
                    .where(public_release_document.c.release_id == release_id)
                    .with_for_update()
                ).one_or_none()
                if not isinstance(published_at, datetime):
                    raise InvalidState("publication timestamp must be a datetime")
                if existing is None:
                    connection.execute(
                        insert(public_release_document).values(
                            release_id=release_id,
                            document=document,
                            content_digest=document["content_digest"],
                            state=document["state"],
                            published_at=published_at,
                        )
                    )
                    inserted += 1
                elif existing.document == document:
                    continue
                elif self._valid_withdrawal(existing.document, document):
                    connection.execute(
                        update(public_release_document)
                        .where(public_release_document.c.release_id == release_id)
                        .values(document=document, state="withdrawn")
                    )
                    withdrawn += 1
                else:
                    raise PersistenceConflict(
                        "published release identity is immutable; publish a successor"
                    )

            if generation == 0:
                if current_release_id is not None:
                    raise InvalidState("generation zero cannot have a current release")
                pointer = None
            else:
                if current_release_id is not None:
                    row = by_id[current_release_id]
                    if row["state"] != "published":
                        raise InvalidState("a withdrawn release cannot remain the current pointer")
                pointer = connection.execute(
                    select(
                        public_release_pointer.c.generation,
                        public_release_pointer.c.release_id,
                    )
                    .where(public_release_pointer.c.target == target)
                    .with_for_update()
                ).one_or_none()
                if pointer is None:
                    connection.execute(
                        insert(public_release_pointer).values(
                            target=target,
                            generation=generation,
                            release_id=current_release_id,
                        )
                    )
                    pointer_advanced = True
                elif generation < pointer.generation:
                    pass  # An older mirror sync cannot move the public pointer backwards.
                elif generation == pointer.generation:
                    if current_release_id != pointer.release_id:
                        raise PersistenceConflict("publication pointer generation was reused")
                else:
                    connection.execute(
                        update(public_release_pointer)
                        .where(public_release_pointer.c.target == target)
                        .values(
                            generation=generation,
                            release_id=current_release_id,
                            updated_at=func.now(),
                        )
                    )
                    pointer_advanced = True

            effective = connection.execute(
                select(
                    public_release_pointer.c.generation,
                    public_release_pointer.c.release_id,
                ).where(public_release_pointer.c.target == target)
            ).one_or_none()
        return {
            "inserted": inserted,
            "withdrawn": withdrawn,
            "pointer_advanced": pointer_advanced,
            "generation": int(effective.generation) if effective is not None else 0,
            "release_id": effective.release_id if effective is not None else None,
        }

    @staticmethod
    def _validate_snapshot(source: Mapping[str, Any]) -> tuple[dict[str, Any], datetime]:
        document = dict(source)
        published_at = document.pop("published_at", None)
        if not isinstance(published_at, datetime):
            raise InvalidState("publication timestamp is required")
        if (
            set(document) - _PUBLIC_KEYS
            or not {"id", "version", "state", "content", "projection", "content_digest", "manifest"}
            <= set(document)
            or document.get("state") not in {"published", "withdrawn"}
            or not isinstance(document.get("id"), str)
            or type(document.get("version")) is not int
            or document["version"] < 1
            or not isinstance(document.get("content"), dict)
            or not isinstance(document.get("projection"), dict)
            or not isinstance(document.get("manifest"), dict)
            or not isinstance(document.get("content_digest"), str)
            or any(key in document for key in ("validation", "review", "approval"))
        ):
            raise InvalidState("snapshot is not an allowlisted public release document")
        withdrawal = document.get("withdrawal")
        if document["state"] == "published" and withdrawal is not None:
            raise InvalidState("published snapshot cannot contain a withdrawal notice")
        if document["state"] == "withdrawn":
            if (
                not isinstance(withdrawal, dict)
                or set(withdrawal) - {"reason", "replacement_id"}
                or not isinstance(withdrawal.get("reason"), str)
                or not withdrawal["reason"].strip()
                or withdrawal.get("replacement_id") is not None
                and not isinstance(withdrawal.get("replacement_id"), str)
            ):
                raise InvalidState("withdrawal notice is outside the public allowlist")
        return document, published_at

    @staticmethod
    def _valid_withdrawal(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
        if old.get("state") != "published" or new.get("state") != "withdrawn":
            return False
        if old.get("id") != new.get("id") or old.get("content_digest") != new.get("content_digest"):
            return False
        if new.get("version") != old.get("version", 0) + 1:
            return False
        stable_old = {
            key: value
            for key, value in old.items()
            if key not in {"state", "version", "withdrawal"}
        }
        stable_new = {
            key: value
            for key, value in new.items()
            if key not in {"state", "version", "withdrawal"}
        }
        notice = new.get("withdrawal")
        return (
            stable_old == stable_new
            and isinstance(notice, dict)
            and bool(str(notice.get("reason", "")).strip())
        )


__all__ = ["PostgresPublicReleaseCatalog"]
