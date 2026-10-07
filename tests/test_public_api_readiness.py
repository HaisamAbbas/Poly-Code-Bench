"""Liveness stays independent; readiness requires the configured public dependencies."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from polycodebench_api.app import create_app
from polycodebench_api.auth import TokenDirectory
from polycodebench_api.submissions import SubmissionStore
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError


class Catalog:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.reads = 0

    def list_public(self) -> list[dict[str, object]]:
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return []


def _app(tmp_path: Path, catalog: Catalog, *, database: object | None = None) -> FastAPI:
    return create_app(
        store=catalog,  # type: ignore[arg-type]
        submissions=SubmissionStore(tmp_path / "submissions.sqlite3"),
        tokens=TokenDirectory({}),
        cursor_key=b"api-readiness-test-key-000000000000",
        persistence_database=database,  # type: ignore[arg-type]
    )


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


def test_readyz_reads_the_catalog_and_healthz_remains_liveness_only(tmp_path: Path) -> None:
    catalog = Catalog()
    response = asyncio.run(_get(_app(tmp_path, catalog), "/readyz"))

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert catalog.reads == 1
    assert asyncio.run(_get(_app(tmp_path, catalog), "/healthz")).status_code == 200


def test_readyz_returns_a_bounded_failure_without_dependency_details(tmp_path: Path) -> None:
    catalog = Catalog(failure=RuntimeError("postgresql://private-host/password=secret"))
    app = _app(tmp_path, catalog)

    response = asyncio.run(_get(app, "/readyz"))
    liveness = asyncio.run(_get(app, "/healthz"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "private-host" not in response.text
    assert "secret" not in response.text
    assert liveness.status_code == 200


def test_readyz_checks_the_configured_postgres_connection(tmp_path: Path) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    def fail_connection(*_: object, **__: object) -> None:
        raise OperationalError("private connection details", {}, RuntimeError("unavailable"))

    event.listen(engine, "before_cursor_execute", fail_connection)
    catalog = Catalog()
    app = _app(tmp_path, catalog, database=SimpleNamespace(engine=engine))
    try:
        response = asyncio.run(_get(app, "/readyz"))
    finally:
        engine.dispose()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert catalog.reads == 0
