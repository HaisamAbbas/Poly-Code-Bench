"""PostgreSQL release catalog role, replay, pointer and withdrawal integration checks."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from polycodebench_core.application_errors import PersistenceConflict
from polycodebench_persistence.database import Database
from polycodebench_persistence.models import public_release_document, public_release_pointer
from polycodebench_persistence.public_releases import PostgresPublicReleaseCatalog
from polycodebench_publication.projections_query import ReleaseContent
from polycodebench_publication.releases import content_digest, digest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError


def _database_url() -> str:
    value = os.environ.get("PCB_TEST_DATABASE_URL")
    if not value:
        pytest.skip("PCB_TEST_DATABASE_URL is not set; PostgreSQL catalog tests are opt-in")
    from sqlalchemy.engine import make_url

    if "test" not in (make_url(value).database or "").lower():
        pytest.fail("PCB_TEST_DATABASE_URL must name a dedicated database containing 'test'")
    return value


def test_postgres_public_release_catalog_sync_is_idempotent_and_immutable(monkeypatch):
    database = Database(_database_url())
    catalog = PostgresPublicReleaseCatalog(database.engine)
    release_id = f"catalog-test-{uuid4()}"
    target = f"catalog-test:{uuid4()}"
    published_at = datetime.now(UTC)
    content = ReleaseContent(
        policy_digest=digest({"policy": "catalog-test"}),
        formula_version="catalog-test-v1",
    ).model_dump(mode="json")
    projection = {
        "schema_version": 1,
        "fixture_kind": "synthetic_internal",
        "scope": "exploratory",
        "cohort_digest": digest({"cohort": "catalog-test"}),
        "limitations": ["Synthetic catalog integration test."],
        "metrics": [
            {
                "metric_id": "pass_rate",
                "value": "0.5",
                "interval_low": None,
                "interval_high": None,
                "coverage": "1",
                "conditional_on_pass": False,
            }
        ],
    }
    public = {
        "id": release_id,
        "version": 1,
        "state": "published",
        "content": content,
        "projection": projection,
        "content_digest": content_digest(content, projection),
        "manifest": {"kind": "signed_manifest"},
        "published_at": published_at,
    }
    try:
        first = catalog.sync_publication(
            target=target,
            generation=1,
            current_release_id=release_id,
            documents=[public],
        )
        assert first == {
            "inserted": 1,
            "withdrawn": 0,
            "pointer_advanced": True,
            "generation": 1,
            "release_id": release_id,
        }
        assert catalog.current(target) == {"generation": 1, "release_id": release_id}
        assert catalog.get(release_id)["content_digest"] == public["content_digest"]

        with database.engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE pcb_public_reader")
            assert connection.execute(
                text("SELECT release_id FROM public_release_document WHERE release_id=:id"),
                {"id": release_id},
            ).scalar_one() == release_id
        with pytest.raises(DBAPIError):
            with database.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL ROLE pcb_public_reader")
                connection.execute(text("SELECT id FROM release LIMIT 1"))

        from polycodebench_api.app import create_app

        monkeypatch.setenv("PCB_PUBLIC_RELEASE_BACKEND", "postgres")
        monkeypatch.setenv("PCB_PUBLICATION_TARGET", target)
        app = create_app(
            cursor_key=b"postgres-catalog-api-test-key-000000000000",
            persistence_database=database,
        )
        with TestClient(app) as client:
            response = client.get(f"/v1/releases/{release_id}")
        assert response.status_code == 200
        assert response.json()["data"]["release_id"] == release_id

        replay = catalog.sync_publication(
            target=target,
            generation=1,
            current_release_id=release_id,
            documents=[public],
        )
        assert replay["inserted"] == 0
        assert replay["pointer_advanced"] is False

        changed = {**public, "content": {"kind": "different_release_content"}}
        with pytest.raises(PersistenceConflict):
            catalog.sync_publication(
                target=target,
                generation=1,
                current_release_id=release_id,
                documents=[changed],
            )

        withdrawn = {
            **public,
            "version": 2,
            "state": "withdrawn",
            "withdrawal": {"reason": "test correction", "replacement_id": None},
            "published_at": published_at,
        }
        withdrawal = catalog.sync_publication(
            target=target,
            generation=2,
            current_release_id=None,
            documents=[withdrawn],
        )
        assert withdrawal["withdrawn"] == 1
        assert withdrawal["generation"] == 2
        assert catalog.get(release_id)["state"] == "withdrawn"
        assert catalog.current(target) == {"generation": 2, "release_id": None}
    finally:
        with database.engine.begin() as connection:
            connection.execute(
                delete(public_release_pointer).where(public_release_pointer.c.target == target)
            )
            connection.execute(
                delete(public_release_document).where(
                    public_release_document.c.release_id == release_id
                )
            )
        database.dispose()


def test_scoped_publisher_role_can_write_catalog_but_cannot_become_api_reader():
    publisher_url = os.environ.get("PCB_TEST_PUBLISHER_DATABASE_URL")
    admin_url = os.environ.get("PCB_TEST_ADMIN_DATABASE_URL")
    if not publisher_url or not admin_url:
        pytest.skip("publisher and admin test DSNs are required for scoped-role verification")
    publisher = Database(publisher_url)
    release_id = f"publisher-role-test-{uuid4()}"
    target = f"publisher-role-test:{uuid4()}"
    snapshot = {
        "id": release_id,
        "version": 1,
        "state": "published",
        "content": {"kind": "release_content"},
        "projection": {"scope": "exploratory"},
        "content_digest": "sha256:" + "b" * 64,
        "manifest": {"kind": "signed_manifest"},
        "published_at": datetime.now(UTC),
    }
    try:
        catalog = PostgresPublicReleaseCatalog(publisher.engine)
        assert (
            catalog.sync_publication(
                target=target,
                generation=1,
                current_release_id=release_id,
                documents=[snapshot],
            )["inserted"]
            == 1
        )
        tampered = {
            key: value
            for key, value in {
                **snapshot,
                "state": "withdrawn",
                "withdrawal": {"reason": "attempted overwrite", "replacement_id": None},
            }.items()
            if key != "published_at"
        }
        with pytest.raises(DBAPIError):
            with publisher.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL ROLE pcb_publisher")
                connection.execute(
                    update(public_release_document)
                    .where(public_release_document.c.release_id == release_id)
                    .values(document=tampered, state="withdrawn")
                )
        with pytest.raises(DBAPIError):
            catalog.get(release_id)
    finally:
        publisher.dispose()
        administrator = Database(admin_url)
        with administrator.engine.begin() as connection:
            connection.execute(
                delete(public_release_pointer).where(public_release_pointer.c.target == target)
            )
            connection.execute(
                delete(public_release_document).where(
                    public_release_document.c.release_id == release_id
                )
            )
        administrator.dispose()
