"""ASGI application for the public release API."""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from polycodebench_persistence.artifacts import PostgresPublicArtifactReader
from polycodebench_persistence.benchmark_audit import PostgresBenchmarkAuditRepository
from polycodebench_persistence.database import Database
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_persistence.public_releases import PostgresPublicReleaseCatalog
from polycodebench_persistence.runs import PostgresRunRepository
from polycodebench_publication.releases import ReleaseStore, SigningKey
from polycodebench_services.artifacts import ArtifactAccessService
from polycodebench_services.model_endpoints import ModelEndpointService
from polycodebench_services.runs import RunCreationService
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from polycodebench_api.auth import TokenDirectory
from polycodebench_api.benchmark_audit_access import OwnerAuditAccessPolicy
from polycodebench_api.benchmark_audit_routes import private_router as benchmark_audit_router
from polycodebench_api.benchmark_audit_routes import public_reports_router
from polycodebench_api.benchmark_audit_routes import public_router as public_health_router
from polycodebench_api.context import (
    ApiServices,
    AuditAccessPolicy,
    PublicBenchmarkHealthProjection,
    PublicReleaseCatalog,
    RunSummarySource,
    SubmissionRepository,
)
from polycodebench_api.errors import ApiError, install_error_handlers
from polycodebench_api.postgres_submissions import PostgresSubmissionStore
from polycodebench_api.public_routes import router as public_router
from polycodebench_api.submission_routes import router as submission_router
from polycodebench_api.submissions import SubmissionStore

LOGGER = logging.getLogger(__name__)


def _store_path() -> Path:
    configured = os.environ.get("PCB_RELEASE_STORE_PATH", ".cache/public-releases.sqlite3")
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _cursor_key() -> bytes:
    configured = os.environ.get("PCB_CURSOR_SIGNING_KEY")
    if configured:
        try:
            value = bytes.fromhex(configured)
        except ValueError as error:
            raise RuntimeError(
                "PCB_CURSOR_SIGNING_KEY must be 64 hexadecimal characters"
            ) from error
        if len(value) != 32:
            raise RuntimeError("PCB_CURSOR_SIGNING_KEY must be 64 hexadecimal characters")
        return value
    if os.environ.get("PCB_ENVIRONMENT", "development") in {"staging", "production"}:
        raise RuntimeError("PCB_CURSOR_SIGNING_KEY is required outside development")
    return secrets.token_bytes(32)


def _public_artifact_access(database: Database | None) -> ArtifactAccessService | None:
    endpoint = os.environ.get("PCB_OBJECT_STORE_ENDPOINT")
    if database is None or not endpoint:
        return None
    buckets = {
        "hidden": os.environ.get("PCB_BUCKET_HIDDEN", "pcb-hidden"),
        "internal": os.environ.get("PCB_BUCKET_INTERNAL", "pcb-internal"),
        "public": os.environ.get("PCB_BUCKET_PUBLIC", "pcb-public"),
    }
    max_bytes = int(os.environ.get("PCB_ARTIFACT_MAX_BYTES", "10485760"))
    reader = PostgresPublicArtifactReader(
        database.engine,
        S3ArtifactStore.from_environment(endpoint_url=endpoint, buckets=buckets),
        max_read_bytes=max_bytes,
    )
    return ArtifactAccessService(reader, max_download_bytes=max_bytes)


def create_app(
    *,
    store: PublicReleaseCatalog | None = None,
    cursor_key: bytes | None = None,
    tokens: TokenDirectory | None = None,
    run_creation: RunCreationService | None = None,
    endpoints: ModelEndpointService | None = None,
    runs: RunSummarySource | None = None,
    submissions: SubmissionRepository | None = None,
    persistence_database: Database | None = None,
    artifact_access: ArtifactAccessService | None = None,
    benchmark_audit: PostgresBenchmarkAuditRepository | None = None,
    audit_access: AuditAccessPolicy | None = None,
    public_benchmark_health: PublicBenchmarkHealthProjection | None = None,
) -> FastAPI:
    """Mount the established read routes over one reviewed release store.

    Production deployments must provide a stable cursor signing key. Local development gets an
    ephemeral key, so a server restart intentionally invalidates its cursors.
    """
    environment = os.environ.get("PCB_ENVIRONMENT", "development")
    release_backend = os.environ.get("PCB_PUBLIC_RELEASE_BACKEND", "sqlite")
    if release_backend not in {"sqlite", "postgres"}:
        raise RuntimeError("PCB_PUBLIC_RELEASE_BACKEND must be sqlite or postgres")
    if environment in {"staging", "production"} and release_backend != "postgres":
        raise RuntimeError("staging and production require the shared PostgreSQL release catalog")
    key = cursor_key if cursor_key is not None else _cursor_key()
    if len(key) < 32:
        raise ValueError("cursor signing keys must contain at least 32 bytes")
    owns_database = False
    if persistence_database is None and os.environ.get("PCB_DATABASE_URL"):
        persistence_database = Database(os.environ["PCB_DATABASE_URL"])
        owns_database = True
    elif persistence_database is None and os.environ.get("PCB_ENVIRONMENT", "development") in {
        "staging",
        "production",
    }:
        raise RuntimeError("PCB_DATABASE_URL is required outside development")
    if store is not None:
        release_store: PublicReleaseCatalog = store
    elif release_backend == "postgres":
        if persistence_database is None:
            raise RuntimeError("PCB_DATABASE_URL is required for the PostgreSQL release catalog")
        release_store = PostgresPublicReleaseCatalog(persistence_database.engine)
    else:
        release_store = ReleaseStore(_store_path())
    release_store_path = getattr(release_store, "path", None)
    postgres_runs = (
        PostgresRunRepository(
            persistence_database.engine,
            database_role="pcb_submission_approver",
        )
        if persistence_database
        else None
    )
    submission_store = submissions
    if submission_store is None:
        submission_store = (
            PostgresSubmissionStore(persistence_database.engine)
            if persistence_database
            else SubmissionStore(release_store_path)
            if isinstance(release_store_path, str)
            else None
        )
    if submission_store is None:
        raise RuntimeError("a durable submission repository is required for this API configuration")
    endpoint_service = endpoints
    if endpoint_service is None and persistence_database:
        endpoint_service = ModelEndpointService(
            PostgresEndpointRepository(
                persistence_database.engine,
                database_role="pcb_endpoint_administrator",
            )
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owns_database and persistence_database is not None:
                persistence_database.dispose()

    services = ApiServices(
        releases=release_store,
        submissions=submission_store,
        tokens=tokens if tokens is not None else TokenDirectory.from_env(),
        secret_key=key,
        signing_key=SigningKey("read-api-process-key", Ed25519PrivateKey.generate()),
        runs=runs if runs is not None else postgres_runs,
        run_creation=(
            run_creation
            if run_creation is not None
            else (RunCreationService(postgres_runs) if postgres_runs is not None else None)
        ),
        endpoints=endpoint_service,
        artifact_access=(
            artifact_access
            if artifact_access is not None
            else _public_artifact_access(persistence_database)
        ),
        benchmark_audit=(
            benchmark_audit
            if benchmark_audit is not None
            else (
                PostgresBenchmarkAuditRepository(persistence_database.engine)
                if persistence_database is not None
                else None
            )
        ),
        audit_access=audit_access if audit_access is not None else OwnerAuditAccessPolicy(),
        public_benchmark_health=public_benchmark_health,
        target=os.environ.get("PCB_PUBLICATION_TARGET", "local:board"),
    )
    app = FastAPI(
        title="PolyCodeBench API",
        version="1.0.0",
        description="Published projections and authenticated, review-gated submission requests.",
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.persistence_database = persistence_database
    install_error_handlers(app)
    app.include_router(public_router)
    app.include_router(submission_router)
    app.include_router(benchmark_audit_router)
    app.include_router(public_health_router)
    app.include_router(public_reports_router)

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readiness() -> dict[str, str]:
        """Return ready only when the configured database and release catalog can be read."""
        try:
            if persistence_database is not None:
                with persistence_database.engine.connect() as connection:
                    connection.execute(text("SELECT 1")).scalar_one()
            release_store.list_public()
        except (SQLAlchemyError, sqlite3.Error, OSError, RuntimeError) as error:
            # Dependency exception text can contain connection details. Keep this probe's
            # response and log bounded and free of DSNs, hostnames, and credentials.
            LOGGER.warning("API readiness dependency failed (%s)", type(error).__name__)
            raise ApiError("DEPENDENCY_UNAVAILABLE") from None
        return {"status": "ready"}

    return app


app = create_app()
