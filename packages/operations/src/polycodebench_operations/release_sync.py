"""Verify and mirror signed SQLite publication snapshots into the shared API catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from polycodebench_core.application_errors import InvalidState
from polycodebench_persistence.public_releases import PostgresPublicReleaseCatalog
from polycodebench_publication.keyring import Keyring
from polycodebench_publication.projections_query import ReleaseContent
from polycodebench_publication.releases import (
    ReleaseStore,
    content_digest,
    digest,
    validate_release_kind,
)


def verified_publication_snapshot(
    store: ReleaseStore,
    *,
    keyring: Keyring,
    source_target: str = "local:board",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate signatures and public schemas, then strip all review-only fields."""
    published_at: dict[str, datetime] = {}
    for row in store.audit():
        release_id = row.get("release_id")
        if row.get("action") != "publish" or not isinstance(release_id, str):
            continue
        try:
            published_at[release_id] = datetime.fromisoformat(
                str(row["occurred_at"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError) as error:
            raise InvalidState("publication audit timestamp is invalid") from error

    snapshots: list[dict[str, Any]] = []
    for source in store.list_public():
        release_id = source.get("id")
        if not isinstance(release_id, str):
            raise InvalidState("release has no stable identity")
        timestamp = published_at.get(release_id)
        if timestamp is None:
            raise InvalidState("published release has no publication audit timestamp")
        if timestamp.tzinfo is None:
            raise InvalidState("publication timestamp must include a timezone")
        manifest = source.get("manifest")
        if not isinstance(manifest, dict):
            raise InvalidState("published release has no signed manifest")
        valid, key_state = keyring.verify(manifest)
        if not valid:
            raise InvalidState(f"release signature verification failed: {key_state}")
        if manifest.get("release_id") != release_id:
            raise InvalidState("manifest release identity does not match the document")
        projection = source.get("projection")
        content = source.get("content")
        claimed_digest = source.get("content_digest")
        if not isinstance(projection, dict) or not isinstance(content, dict):
            raise InvalidState("published release content is malformed")
        if claimed_digest != content_digest(content, projection):
            raise InvalidState("published release content digest does not match")
        if manifest.get("content_digest") != claimed_digest:
            raise InvalidState("signed manifest does not bind the release content")
        if manifest.get("projection_digest") != digest(projection):
            raise InvalidState("signed manifest does not bind the public projection")
        try:
            validate_release_kind(content, projection)
            ReleaseContent.model_validate(content)
        except (TypeError, ValueError) as error:
            raise InvalidState("published release does not match the public API schema") from error
        if source.get("state") not in {"published", "withdrawn"}:
            raise InvalidState("source store exposed a non-public release")

        # Do not copy local review/approval/validation receipts, actor identities or other
        # workflow-only data. The API requires only these signed, typed public fields.
        public_document: dict[str, Any] = {
            "id": release_id,
            "version": source["version"],
            "state": source["state"],
            "content": content,
            "projection": projection,
            "content_digest": claimed_digest,
            "manifest": manifest,
        }
        if source.get("state") == "withdrawn":
            notice = source.get("withdrawal")
            if not isinstance(notice, dict) or not str(notice.get("reason", "")).strip():
                raise InvalidState("withdrawn release requires a public reason")
            public_document["withdrawal"] = {
                "reason": str(notice["reason"]),
                "replacement_id": notice.get("replacement_id"),
            }
        snapshots.append({**public_document, "published_at": timestamp})

    pointer = store.current(source_target)
    generation = int(pointer.get("generation", 0))
    release_id = pointer.get("release_id")
    if release_id is not None and not isinstance(release_id, str):
        raise InvalidState("source publication pointer identity is invalid")
    return snapshots, {"generation": generation, "release_id": release_id}


def sync_publication(
    store: ReleaseStore,
    catalog: PostgresPublicReleaseCatalog,
    *,
    keyring: Keyring,
    target: str,
    source_target: str = "local:board",
) -> dict[str, Any]:
    snapshots, pointer = verified_publication_snapshot(
        store, keyring=keyring, source_target=source_target
    )
    return catalog.sync_publication(
        target=target,
        generation=pointer["generation"],
        current_release_id=pointer["release_id"],
        documents=snapshots,
    )


__all__ = ["sync_publication", "verified_publication_snapshot"]
