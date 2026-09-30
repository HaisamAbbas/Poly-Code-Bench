"""Prompt 04 storage integration against PostgreSQL and an S3-compatible service."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from time import sleep
from uuid import UUID, uuid4

import pytest
from polycodebench_core.application_errors import (
    AuthorizationError,
    InvalidState,
    NotFound,
    PersistenceConflict,
)
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.models import (
    artifact,
    artifact_declassification,
    artifact_quota,
    artifact_retention_hold,
    artifact_upload,
)
from polycodebench_persistence.object_store import ObjectStoreError, S3ArtifactStore
from polycodebench_services.artifact_publication import (
    PublicProjectionService,
    ReviewedMetadataProjection,
)
from polycodebench_services.artifacts import (
    ArtifactAccessService,
    ArtifactPrincipal,
    ArtifactUploadService,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError


def _test_database_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("PCB_TEST_DATABASE_URL must use a disposable database containing 'test'")
    return value


@pytest.fixture(scope="module")
def database() -> Generator[Database, None, None]:
    instance = Database(_test_database_url())
    yield instance
    instance.dispose()


@pytest.fixture(scope="module")
def store() -> S3ArtifactStore:
    endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.skip("PCB_OBJECT_STORE_ENDPOINT is not configured")
    store = S3ArtifactStore(
        endpoint_url=endpoint,
        buckets={
            "hidden": os.environ.get("PCB_BUCKET_HIDDEN", "pcb-p04-hidden"),
            "internal": os.environ.get("PCB_BUCKET_INTERNAL", "pcb-p04-internal"),
            "public": os.environ.get("PCB_BUCKET_PUBLIC", "pcb-p04-public"),
        },
    )
    store.ensure_buckets()
    return store


def _repository(database: Database, store: S3ArtifactStore, domain: str) -> ArtifactRepository:
    with database.engine.begin() as connection:
        for visibility in ("hidden", "internal", "public"):
            connection.execute(
                insert(artifact_quota)
                .values(
                    visibility=visibility,
                    encryption_domain=domain,
                    max_bytes=4_000_000,
                    used_bytes=0,
                    reserved_bytes=0,
                )
                .on_conflict_do_nothing(index_elements=["visibility", "encryption_domain"])
            )
    return ArtifactRepository(database.engine, store, max_upload_bytes=1_000_000)


def _upload(
    repo: ArtifactRepository, *, owner: str, visibility: str, domain: str, body: bytes
) -> UUID:
    upload_id = repo.begin_upload(
        owner=owner,
        visibility=visibility,
        encryption_domain=domain,
        expected_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=upload_id, owner=owner, body=body)
    repo.upload(upload_id=upload_id, owner=owner, body=body)
    finalized = repo.finalize(upload_id=upload_id, owner=owner)
    assert repo.finalize(upload_id=upload_id, owner=owner) == finalized
    return finalized


def test_integrity_size_digest_retry_and_visibility_domains(
    database: Database, store: S3ArtifactStore
) -> None:
    domain = f"it-{uuid4().hex}"
    repo = _repository(database, store, domain)
    owner = f"artifact-test-{uuid4()}"
    body = b"verified artifact bytes\x00\xff"

    first = _upload(repo, owner=owner, visibility="hidden", domain=domain, body=body)
    second = _upload(repo, owner=owner, visibility="hidden", domain=domain, body=body)
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    public_upload = repo.begin_upload(
        owner=owner,
        visibility="public",
        encryption_domain=domain,
        expected_digest=digest,
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=public_upload, owner=owner, body=body)
    with pytest.raises(InvalidState):
        repo.finalize(upload_id=public_upload, owner=owner)
    approval_id = repo.approve_projection(
        source_artifact_id=first,
        projection_digest=digest,
        approved_by=f"reviewer-{uuid4()}",
        reason="reviewed visibility fixture",
        request_id=f"approve-{uuid4()}",
    )
    public_copy = repo.finalize_publication(
        upload_id=public_upload,
        owner=owner,
        approval_id=approval_id,
        request_id=f"publish-{uuid4()}",
    )
    assert first == second
    assert first != public_copy
    distinct_hidden = _upload(
        repo, owner=owner, visibility="hidden", domain=domain, body=b"another hidden child"
    )
    repo.add_manifest_edge(parent_id=first, child_id=distinct_hidden, relation="contains")
    with pytest.raises(InvalidState):
        repo.add_manifest_edge(parent_id=distinct_hidden, child_id=first, relation="contains")
    with pytest.raises(InvalidState):
        repo.add_manifest_edge(parent_id=first, child_id=public_copy, relation="contains")

    bad_digest = "sha256:" + hashlib.sha256(body).hexdigest()
    changed = repo.begin_upload(
        owner=owner,
        visibility="hidden",
        encryption_domain=domain,
        expected_digest=bad_digest,
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=changed, owner=owner, body=body + b"x")
    with pytest.raises(InvalidState):
        repo.finalize(upload_id=changed, owner=owner)

    wrong_digest = repo.begin_upload(
        owner=owner,
        visibility="hidden",
        encryption_domain=domain,
        expected_digest="sha256:" + "0" * 64,
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=wrong_digest, owner=owner, body=body)
    with pytest.raises(InvalidState):
        repo.finalize(upload_id=wrong_digest, owner=owner)

    truncated = repo.begin_upload(
        owner=owner,
        visibility="hidden",
        encryption_domain=domain,
        expected_digest=bad_digest,
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=truncated, owner=owner, body=body[:-1])
    with pytest.raises(InvalidState):
        repo.finalize(upload_id=truncated, owner=owner)

    with database.engine.connect() as connection:
        rejected = (
            connection.execute(
                select(artifact_upload.c.state).where(
                    artifact_upload.c.id.in_([changed, wrong_digest, truncated])
                )
            )
            .scalars()
            .all()
        )
        assert sorted(rejected) == ["rejected", "rejected", "rejected"]
        assert connection.execute(
            select(artifact.c.id).where(
                artifact.c.content_digest == bad_digest,
                artifact.c.size_bytes == len(body),
                artifact.c.visibility == "hidden",
                artifact.c.encryption_domain == domain,
            )
        ).scalars().all() == [first]

    access = ArtifactAccessService(repo)
    with pytest.raises(AuthorizationError):
        ArtifactUploadService(repo).begin_upload(
            principal=ArtifactPrincipal("solve-identity", frozenset({"solve_supervisor"})),
            visibility="hidden",
            encryption_domain=domain,
            expected_digest="sha256:" + hashlib.sha256(b"x").hexdigest(),
            expected_size=1,
            media_type="application/octet-stream",
        )
    writer = ArtifactUploadService(repo)
    curator = ArtifactPrincipal("curator", frozenset({"curator"}))
    write_body = b"role-checked stage object"
    stage_id = writer.begin_upload(
        principal=curator,
        visibility="hidden",
        encryption_domain=domain,
        expected_digest="sha256:" + hashlib.sha256(write_body).hexdigest(),
        expected_size=len(write_body),
        media_type="application/octet-stream",
    )
    with pytest.raises(AuthorizationError):
        writer.upload(
            principal=ArtifactPrincipal("curator", frozenset({"solve_supervisor"})),
            upload_id=stage_id,
            body=write_body,
        )
    writer.upload(principal=curator, upload_id=stage_id, body=write_body)
    with pytest.raises(AuthorizationError):
        writer.finalize(
            principal=ArtifactPrincipal("curator", frozenset({"solve_supervisor"})),
            upload_id=stage_id,
        )
    writer.finalize(principal=curator, upload_id=stage_id)
    solve = ArtifactPrincipal(
        "solve-identity", frozenset({"solve_supervisor"}), frozenset({public_copy})
    )
    with pytest.raises(NotFound):
        access.download(solve, first)
    with pytest.raises(NotFound):
        access.download(ArtifactPrincipal("public", frozenset({"public_reader"})), first)
    download = access.download(
        ArtifactPrincipal("public", frozenset({"public_reader"})), public_copy
    )
    assert download.body == body
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    with pytest.raises(DBAPIError):
        with database.engine.begin() as connection:
            connection.execute(
                insert(artifact_declassification).values(
                    source_artifact_id=distinct_hidden,
                    public_artifact_id=public_copy,
                    review_digest="sha256:" + hashlib.sha256(body).hexdigest(),
                    approved_by="unrecorded-reviewer",
                    reason="no stored approval",
                )
            )
    with database.engine.connect() as connection:
        connection.exec_driver_sql("SET ROLE pcb_solve_supervisor")
        with pytest.raises(DBAPIError):
            connection.execute(select(artifact.c.id)).all()
        connection.rollback()

    quota_domain = f"quota-{uuid4().hex}"
    with database.engine.begin() as connection:
        connection.execute(
            insert(artifact_quota).values(
                visibility="hidden",
                encryption_domain=quota_domain,
                max_bytes=1,
                used_bytes=0,
                reserved_bytes=0,
            )
        )
    with pytest.raises(InvalidState):
        repo.begin_upload(
            owner=owner,
            visibility="hidden",
            encryption_domain=quota_domain,
            expected_digest="sha256:" + hashlib.sha256(b"xx").hexdigest(),
            expected_size=2,
            media_type="application/octet-stream",
        )


def test_provisional_gc_retains_verified_data_and_releases_quota(
    database: Database, store: S3ArtifactStore
) -> None:
    domain = f"gc-{uuid4().hex}"
    repo = _repository(database, store, domain)
    owner = f"artifact-gc-{uuid4()}"
    resume_body = b"object-store write committed before SQL status update"
    interrupted = repo.begin_upload(
        owner=owner,
        visibility="internal",
        encryption_domain=domain,
        expected_digest="sha256:" + hashlib.sha256(resume_body).hexdigest(),
        expected_size=len(resume_body),
        media_type="application/octet-stream",
    )
    store.put_provisional("internal", str(interrupted), resume_body)
    repo.upload(upload_id=interrupted, owner=owner, body=resume_body)
    resumed_artifact = repo.finalize(upload_id=interrupted, owner=owner)
    assert repo.read_verified(resumed_artifact)[1] == resume_body

    orphan_body = b"canonical write whose database transaction never committed"
    orphan_digest = "sha256:" + hashlib.sha256(orphan_body).hexdigest()
    orphan_key = store.put_verified("internal", domain, orphan_digest, orphan_body)

    body = b"staged then collected"
    pending = repo.begin_upload(
        owner=owner,
        visibility="internal",
        encryption_domain=domain,
        expected_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=pending, owner=owner, body=body)
    verified = _upload(repo, owner=owner, visibility="internal", domain=domain, body=b"retain me")
    after_expiry = datetime.now(UTC) + timedelta(days=2)
    removed_early = repo.collect_garbage(now=after_expiry)
    assert removed_early == 0
    with database.engine.connect() as connection:
        state = connection.execute(
            select(artifact_upload.c.state).where(artifact_upload.c.id == pending)
        ).scalar_one()
        reserved = connection.execute(
            select(artifact_quota.c.reserved_bytes).where(
                artifact_quota.c.visibility == "internal",
                artifact_quota.c.encryption_domain == domain,
            )
        ).scalar_one()
    assert state == "expired"
    assert reserved == 0
    assert store.get_bytes("internal", f"provisional/{pending}", max_bytes=100) == body
    removed_after_retention = repo.collect_garbage(now=datetime.now(UTC) + timedelta(days=31))
    assert removed_after_retention >= 2
    record, stored = repo.read_verified(verified)
    assert record["status"] == "verified"
    assert stored == b"retain me"
    with pytest.raises(ObjectStoreError):
        store.get_bytes("internal", f"provisional/{pending}", max_bytes=100)
    with pytest.raises(ObjectStoreError):
        store.get_bytes("internal", orphan_key, max_bytes=100)


def test_reviewed_projection_is_a_new_allowlisted_public_object(
    database: Database, store: S3ArtifactStore
) -> None:
    domain = f"projection-source-{uuid4().hex}"
    repo = _repository(database, store, domain)
    source_id = _upload(
        repo,
        owner=f"curator-{uuid4()}",
        visibility="hidden",
        domain=domain,
        body=b"private source bytes that must not be copied",
    )
    _repository(database, store, "public-projection")
    before = repo.get_verified(source_id)
    publisher = ArtifactPrincipal("publisher-one", frozenset({"publisher"}))
    reviewer = ArtifactPrincipal("reviewer-one", frozenset({"reviewer"}))
    projection = ReviewedMetadataProjection(
        display_name="Public task family",
        description="Reviewed public summary",
        version="v1",
    )
    service = PublicProjectionService(repo)
    with pytest.raises(AuthorizationError):
        service.approve_metadata(
            principal=publisher,
            source_artifact_id=source_id,
            projection=projection,
            reason="reviewed release export",
            request_id="same-person-rejected",
        )
    approval_id = service.approve_metadata(
        principal=reviewer,
        source_artifact_id=source_id,
        projection=projection,
        reason="reviewed release export",
        request_id=f"approve-{uuid4()}",
    )
    with pytest.raises(AuthorizationError):
        service.publish_reviewed_metadata(
            principal=ArtifactPrincipal(reviewer.subject_id, frozenset({"publisher"})),
            approval_id=approval_id,
            projection=projection,
            request_id=f"self-publish-{uuid4()}",
        )
    with pytest.raises(InvalidState):
        service.publish_reviewed_metadata(
            principal=publisher,
            approval_id=approval_id,
            projection=ReviewedMetadataProjection("Changed", "Unreviewed", "v2"),
            request_id=f"mismatch-{uuid4()}",
        )
    public_id = service.publish_reviewed_metadata(
        principal=publisher,
        approval_id=approval_id,
        projection=projection,
        request_id=f"publish-{uuid4()}",
    )
    replayed_public_id = service.publish_reviewed_metadata(
        principal=publisher,
        approval_id=approval_id,
        projection=projection,
        request_id=f"publish-replay-{uuid4()}",
    )
    after = repo.get_verified(source_id)
    public_record, public_bytes = repo.read_verified(public_id)
    hold_id = repo.add_hold(
        artifact_id=source_id,
        actor=reviewer.subject_id,
        reason="preserve source review evidence",
        request_id=f"hold-{uuid4()}",
    )
    repo.collect_garbage(now=datetime.now(UTC) + timedelta(days=2))
    assert repo.read_verified(source_id)[0]["status"] == "verified"
    assert repo.read_verified(public_id)[0]["status"] == "verified"
    with database.engine.connect() as connection:
        hold = connection.execute(
            select(artifact_retention_hold.c.released_at).where(
                artifact_retention_hold.c.id == hold_id
            )
        ).scalar_one()
    assert hold is None
    assert public_id != source_id
    assert replayed_public_id == public_id
    assert public_record["visibility"] == "public"
    download = ArtifactAccessService(repo).download(
        ArtifactPrincipal("public", frozenset({"public_reader"})), public_id
    )
    assert download.body == public_bytes
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert before["visibility"] == after["visibility"] == "hidden"
    assert before["storage_key"] == after["storage_key"]
    assert b"private source bytes" not in public_bytes
    assert set(json.loads(public_bytes)) == {
        "description",
        "display_name",
        "projection_type",
        "schema_version",
        "version",
    }


def test_concurrent_finalize_releases_a_reservation_once(
    database: Database, store: S3ArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    domain = f"race-{uuid4().hex}"
    repo = _repository(database, store, domain)
    owner = f"race-owner-{uuid4()}"
    body = b"two finalizers share this reservation"
    upload_id = repo.begin_upload(
        owner=owner,
        visibility="internal",
        encryption_domain=domain,
        expected_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        expected_size=len(body),
        media_type="application/octet-stream",
    )
    repo.upload(upload_id=upload_id, owner=owner, body=body)
    other_body = b"another pending reservation"
    repo.begin_upload(
        owner=owner,
        visibility="internal",
        encryption_domain=domain,
        expected_digest="sha256:" + hashlib.sha256(other_body).hexdigest(),
        expected_size=len(other_body),
        media_type="application/octet-stream",
    )
    original_commit = repo._commit_verified
    barrier = Barrier(2)

    def overlapping_commit(*args: object, **kwargs: object) -> UUID:
        barrier.wait(timeout=10)
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(repo, "_commit_verified", overlapping_commit)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(repo.finalize, upload_id=upload_id, owner=owner) for _ in range(2)
        ]
        ids = [future.result(timeout=15) for future in futures]
    assert ids[0] == ids[1]
    with database.engine.connect() as connection:
        quota = (
            connection.execute(
                select(artifact_quota).where(
                    artifact_quota.c.visibility == "internal",
                    artifact_quota.c.encryption_domain == domain,
                )
            )
            .mappings()
            .one()
        )
    assert quota["used_bytes"] == len(body)
    assert quota["reserved_bytes"] == len(other_body)


def test_expired_finalize_retains_provisional_bytes(
    database: Database, store: S3ArtifactStore
) -> None:
    domain = f"expired-{uuid4().hex}"
    repo = _repository(database, store, domain)
    owner = f"expired-owner-{uuid4()}"
    body = b"retain after reservation expiry"
    upload_id = repo.begin_upload(
        owner=owner,
        visibility="hidden",
        encryption_domain=domain,
        expected_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        expected_size=len(body),
        media_type="application/octet-stream",
        ttl=timedelta(seconds=1),
    )
    repo.upload(upload_id=upload_id, owner=owner, body=body)
    sleep(1.1)
    with pytest.raises(InvalidState):
        repo.finalize(upload_id=upload_id, owner=owner)
    assert store.get_bytes("hidden", f"provisional/{upload_id}", max_bytes=100) == body
    with database.engine.connect() as connection:
        state = connection.execute(
            select(artifact_upload.c.state).where(artifact_upload.c.id == upload_id)
        ).scalar_one()
    assert state == "expired"


def test_failed_publication_does_not_register_an_unreviewed_public_artifact(
    database: Database, store: S3ArtifactStore
) -> None:
    domain = f"conflict-source-{uuid4().hex}"
    repo = _repository(database, store, domain)
    _repository(database, store, "public-projection")
    source_id = _upload(
        repo, owner=f"source-{uuid4()}", visibility="hidden", domain=domain, body=b"source"
    )
    service = PublicProjectionService(repo)
    reviewer = ArtifactPrincipal(f"reviewer-{uuid4()}", frozenset({"reviewer"}))
    publisher = ArtifactPrincipal(f"publisher-{uuid4()}", frozenset({"publisher"}))
    first = ReviewedMetadataProjection("First", "Approved version", "v1")
    first_approval = service.approve_metadata(
        principal=reviewer,
        source_artifact_id=source_id,
        projection=first,
        reason="first review",
        request_id=f"review-{uuid4()}",
    )
    service.publish_reviewed_metadata(
        principal=publisher,
        approval_id=first_approval,
        projection=first,
        request_id=f"publish-{uuid4()}",
    )
    changed = ReviewedMetadataProjection("Changed", "Separately reviewed", "v2")
    changed_approval = service.approve_metadata(
        principal=reviewer,
        source_artifact_id=source_id,
        projection=changed,
        reason="second review",
        request_id=f"review-{uuid4()}",
    )
    with pytest.raises(PersistenceConflict):
        service.publish_reviewed_metadata(
            principal=publisher,
            approval_id=changed_approval,
            projection=changed,
            request_id=f"publish-{uuid4()}",
        )
    payload = changed.canonical_bytes()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    upload_id = repo.begin_upload(
        owner=publisher.subject_id,
        visibility="public",
        encryption_domain="public-projection",
        expected_digest=digest,
        expected_size=len(payload),
        media_type="application/json",
    )
    repo.upload(upload_id=upload_id, owner=publisher.subject_id, body=payload)
    with pytest.raises(PersistenceConflict):
        repo.finalize_publication(
            upload_id=upload_id,
            owner=publisher.subject_id,
            approval_id=changed_approval,
            request_id=f"publish-{uuid4()}",
        )
    with database.engine.connect() as connection:
        unreviewed = connection.execute(
            select(artifact.c.id).where(
                artifact.c.visibility == "public",
                artifact.c.encryption_domain == "public-projection",
                artifact.c.content_digest == digest,
            )
        ).all()
    assert unreviewed == []
    repo.reject(upload_id=upload_id, owner=publisher.subject_id, failure_code="conflict")
