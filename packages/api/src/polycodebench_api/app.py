"""ASGI application for the public release API."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from polycodebench_publication.releases import ReleaseStore, SigningKey

from polycodebench_api.auth import TokenDirectory
from polycodebench_api.context import ApiServices
from polycodebench_api.errors import install_error_handlers
from polycodebench_api.public_routes import router as public_router
from polycodebench_api.submissions import SubmissionStore


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


def create_app(
    *,
    store: ReleaseStore | None = None,
    cursor_key: bytes | None = None,
) -> FastAPI:
    """Mount the established read routes over one reviewed release store.

    Production deployments must provide a stable cursor signing key. Local development gets an
    ephemeral key, so a server restart intentionally invalidates its cursors.
    """
    release_store = store or ReleaseStore(_store_path())
    key = cursor_key if cursor_key is not None else _cursor_key()
    if len(key) < 32:
        raise ValueError("cursor signing keys must contain at least 32 bytes")
    services = ApiServices(
        releases=release_store,
        submissions=SubmissionStore(release_store.path),
        tokens=TokenDirectory({}),
        secret_key=key,
        signing_key=SigningKey("read-api-process-key", Ed25519PrivateKey.generate()),
    )
    app = FastAPI(
        title="PolyCodeBench Public API",
        version="1.0.0",
        description="Read-only projections from published PolyCodeBench releases.",
    )
    app.state.services = services
    install_error_handlers(app)
    app.include_router(public_router)

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
