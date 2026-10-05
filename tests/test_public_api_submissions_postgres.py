"""Prompt 32 concurrent intake limits against the durable PostgreSQL adapter."""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from migration_support import require_migrated_through
from polycodebench_api.app import create_app
from polycodebench_api.auth import ApiPrincipal, TokenDirectory
from polycodebench_api.errors import ApiError
from polycodebench_api.postgres_submissions import PostgresSubmissionStore
from polycodebench_api.submissions import SubmissionRate
from polycodebench_persistence.database import Database
from polycodebench_publication.releases import ReleaseStore
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

REQUIRED_REVISION = "c41e1d8ab0f6"


def _test_database_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("submission integration requires a disposable PostgreSQL test database")
    return value


def _migration_check_url(test_url: str) -> str:
    """Allow schema revision checks with a separate migration identity.

    The submission tests deliberately connect through the submitter role, which should not
    need SELECT access to Alembic's bookkeeping table. CI uses a database owner for both
    connections; local role-scoped runs may supply a privileged URL for the same test DB.
    """
    value = os.environ.get("PCB_TEST_MIGRATION_DATABASE_URL") or test_url
    if make_url(value).database != make_url(test_url).database:
        pytest.fail("PCB_TEST_MIGRATION_DATABASE_URL must target PCB_TEST_DATABASE_URL's database")
    return value


@pytest.fixture(scope="module")
def engine() -> Generator[Engine, None, None]:
    test_url = _test_database_url()
    database = Database(test_url)
    with database.engine.connect() as connection:
        if (
            connection.execute(text("SELECT to_regclass('public.model_submission')")).scalar_one()
            is None
        ):
            pytest.fail("PostgreSQL model-submission schema is not installed")
    migration_engine = create_engine(
        _migration_check_url(test_url), pool_pre_ping=True, hide_parameters=True
    )
    try:
        with migration_engine.connect() as connection:
            require_migrated_through(connection, REQUIRED_REVISION)
    finally:
        migration_engine.dispose()
    yield database.engine
    database.dispose()


def test_concurrent_requests_cannot_exceed_one_subject_rate_limit(engine: Engine) -> None:
    store = PostgresSubmissionStore(engine)
    subject = f"prompt32-concurrency-{uuid4()}"
    request_count = 8
    start = Barrier(request_count)
    payload = {
        "model_name": "Concurrent test fixture",
        "provider": "fixture",
        "organization": None,
        "contact_email": "fixture@example.org",
        "endpoint_url": "https://models.example.org/v1",
        "source_url": "https://models.example.org/source",
        "source_license": "test-only",
        "permission_attested": True,
    }

    def submit(index: int) -> str:
        start.wait(timeout=10)
        try:
            store.submit(
                subject=subject,
                request_id=f"parallel-{index}",
                payload=payload,
                rate=SubmissionRate(limit=2, window_seconds=3600),
            )
            return "created"
        except ApiError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        outcomes = list(executor.map(submit, range(request_count)))

    counts = Counter(outcomes)
    assert counts["created"] == 2
    assert counts["RATE_LIMITED"] == request_count - 2


def test_submitter_database_role_is_scoped_and_supports_idempotent_retry() -> None:
    """Exercise submit, audit and owner reads under the actual submitter RLS policy."""
    role_engine = create_engine(_test_database_url(), pool_pre_ping=True, hide_parameters=True)

    store = PostgresSubmissionStore(role_engine)
    subject = f"prompt32-owner-scope-{uuid4()}"
    payload = {
        "model_name": "Submitter role regression fixture",
        "provider": "fixture",
        "organization": None,
        "contact_email": "fixture@example.org",
        "endpoint_url": "https://models.example.org/v1",
        "source_url": "https://models.example.org/source",
        "source_license": "test-only",
        "permission_attested": True,
    }
    try:
        created = store.submit(
            subject=subject,
            request_id="owner-scope-request-0001",
            payload=payload,
            rate=SubmissionRate(limit=2, window_seconds=3600),
        )
        replayed = store.submit(
            subject=subject,
            request_id="owner-scope-request-0001",
            payload=payload,
            rate=SubmissionRate(limit=2, window_seconds=3600),
        )
        assert replayed["submission_id"] == created["submission_id"]
        audit_engine = create_engine(
            _migration_check_url(_test_database_url()), hide_parameters=True
        )
        try:
            with audit_engine.connect() as connection:
                audit_count = connection.execute(
                    text(
                        "SELECT count(*) FROM audit_event "
                        "WHERE action = 'model_submission.create' "
                        "AND resource_type = 'model_submission' "
                        "AND resource_id = :submission_id "
                        "AND request_id = 'owner-scope-request-0001'"
                    ),
                    {"submission_id": str(created["submission_id"])},
                ).scalar_one()
            assert audit_count == 1
        finally:
            audit_engine.dispose()
        assert (
            store.get_owned(subject=subject, submission_id=str(created["submission_id"]))[
                "submission_id"
            ]
            == created["submission_id"]
        )
        with pytest.raises(ApiError) as forbidden:
            store.get_owned(
                subject=f"{subject}-other",
                submission_id=str(created["submission_id"]),
            )
        assert forbidden.value.code == "NOT_FOUND"
    finally:
        role_engine.dispose()


def test_authenticated_submission_routes_use_postgres_roles(tmp_path: Path, engine: Engine) -> None:
    """Exercise submit, reviewer reject and owner status through the PostgreSQL app adapter."""
    del engine  # The fixture verifies the dedicated database revision before app startup.
    database = Database(_test_database_url())
    release_store = ReleaseStore(Path(str(tmp_path)) / "submission-routes.sqlite3")
    tokens = TokenDirectory(
        {
            "route-submitter-token-000001": ApiPrincipal(
                "route-owner",
                frozenset({"submitter"}),
                email="route-owner@example.org",
                email_verified=True,
            ),
            "route-other-token-0000001": ApiPrincipal(
                "route-other",
                frozenset({"submitter"}),
                email="route-other@example.org",
                email_verified=True,
            ),
            "route-reviewer-token-00001": ApiPrincipal(
                "route-reviewer",
                frozenset({"reviewer"}),
                mfa=True,
            ),
        }
    )
    app = create_app(
        store=release_store,
        cursor_key=b"prompt32-postgres-route-test-key-000",
        tokens=tokens,
        persistence_database=database,
    )

    async def verify() -> str:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/v1/model-submissions",
                headers={
                    "Authorization": "Bearer route-submitter-token-000001",
                    "Idempotency-Key": "route-submit-32-1",
                },
                json={
                    "kind": "model_submission_input",
                    "model_name": "PostgreSQL route fixture",
                    "provider": "Fixture Provider",
                    "contact_email": "route-owner@example.org",
                    "endpoint_url": "https://models.example.org/v1",
                    "source_url": "https://models.example.org/model-card",
                    "source_license": "test-only permission",
                    "permission_attested": True,
                },
            )
            assert created.status_code == 201
            submission_id = created.json()["data"]["submission_id"]
            assert created.json()["data"]["status"] == "pending"
            assert "secret_ref" not in created.text

            review_headers = {"Authorization": "Bearer route-reviewer-token-00001"}
            queue = await client.get("/v1/admin/model-submissions", headers=review_headers)
            assert queue.status_code == 200
            assert any(row["submission_id"] == submission_id for row in queue.json()["data"])

            rejected = await client.post(
                f"/v1/admin/model-submissions/{submission_id}/reject",
                headers={**review_headers, "Idempotency-Key": "route-reject-32-1"},
                json={
                    "reason": "Rejected by the PostgreSQL route integration fixture.",
                    "expected_version": 0,
                },
            )
            assert rejected.status_code == 200
            assert rejected.json()["data"]["status"] == "rejected"

            own_status = await client.get(
                f"/v1/model-submissions/{submission_id}",
                headers={"Authorization": "Bearer route-submitter-token-000001"},
            )
            assert own_status.status_code == 200
            assert own_status.json()["data"]["status"] == "rejected"
            foreign_status = await client.get(
                f"/v1/model-submissions/{submission_id}",
                headers={"Authorization": "Bearer route-other-token-0000001"},
            )
            assert foreign_status.status_code == 404
            return submission_id

    try:
        submission_id = asyncio.run(verify())
        audit_engine = create_engine(
            _migration_check_url(_test_database_url()), hide_parameters=True
        )
        try:
            with audit_engine.connect() as connection:
                actions = (
                    connection.execute(
                        text(
                            "SELECT action FROM audit_event "
                            "WHERE resource_type = 'model_submission' AND resource_id = :id "
                            "ORDER BY action"
                        ),
                        {"id": submission_id},
                    )
                    .scalars()
                    .all()
                )
            assert actions == ["model_submission.create", "model_submission.reject"]
        finally:
            audit_engine.dispose()
    finally:
        database.dispose()
