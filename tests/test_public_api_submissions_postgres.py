"""Prompt 32 concurrent intake limits against the durable PostgreSQL adapter."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from migration_support import require_migrated_through
from polycodebench_api.errors import ApiError
from polycodebench_api.postgres_submissions import PostgresSubmissionStore
from polycodebench_api.submissions import SubmissionRate
from polycodebench_persistence.database import Database
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url

REQUIRED_REVISION = "c41e1d8ab0f6"


def _test_database_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not configured")
    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("submission integration requires a disposable PostgreSQL test database")
    return value


@pytest.fixture(scope="module")
def engine() -> Generator[Engine, None, None]:
    database = Database(_test_database_url())
    with database.engine.connect() as connection:
        require_migrated_through(connection, REQUIRED_REVISION)
        if (
            connection.execute(text("SELECT to_regclass('public.model_submission')")).scalar_one()
            is None
        ):
            pytest.fail("PostgreSQL model-submission schema is not installed")
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
