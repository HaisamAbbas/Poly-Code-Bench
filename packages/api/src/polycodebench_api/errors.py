"""Typed error taxonomy for the HTTP adapter.

Every failure leaves this API as ``{"error": {"code", "message", "request_id", "details"}}``
(Technical Specification 20.6). Messages are generic per code and the details are checked by
:class:`~polycodebench_publication.projections.PublicError`'s leak guard, so no response can carry
a hidden task name, a private path, a digest or a URL.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from polycodebench_core.application_errors import (
    AuthorizationError,
    IdempotencyConflict,
    LeaseLost,
    NotFound,
    OptimisticVersionConflict,
    PersistenceConflict,
    PersistenceUnavailable,
    ServiceError,
)
from polycodebench_publication.projections import ERROR_STATUS, ErrorCode, PublicError
from polycodebench_publication.projections_query import PublicApiError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

#: One generic message per taxonomy code. A route never interpolates caller input into it.
GENERIC_MESSAGES: dict[str, str] = {
    "INVALID_CURSOR": "cursor is not valid for this request",
    "INVALID_FILTER": "request parameters are not valid for this release",
    "UNAUTHENTICATED": "authentication is required",
    "FORBIDDEN": "the request is not permitted",
    "NOT_FOUND": "resource is not available",
    "IDEMPOTENCY_CONFLICT": "idempotency key was reused for a different request",
    "RESULT_CONFLICT": "the request conflicts with current state",
    "LEASE_LOST": "worker authority was lost",
    "INCOMPATIBLE_COHORT": "entries cannot be compared",
    "RELEASE_NOT_READY": "release is not ready for this operation",
    "VERSION_CONFLICT": "the row version precondition failed",
    "SCHEMA_INVALID": "request is not valid",
    "CAPABILITY_UNSUPPORTED": "requested capability is not supported",
    "TASK_INVALID": "task request is not valid",
    "RATE_LIMITED": "too many requests",
    "BUDGET_EXHAUSTED": "budget is exhausted",
    "DEPENDENCY_UNAVAILABLE": "dependency is unavailable",
}


class ApiError(Exception):
    """A request rejected for one taxonomy code with optional leak-checked detail hints."""

    def __init__(self, code: ErrorCode, *details: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = tuple(details)
        self.retry_after = retry_after


def request_id(request: Request) -> str:
    """The request correlation id for error bodies; minted per request, never caller-controlled."""
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str) and value:
        return value
    minted = uuid4().hex
    request.state.request_id = minted
    return minted


def error_body(code: ErrorCode, request_id: str, details: tuple[str, ...] = ()) -> dict[str, Any]:
    """The ``{"error": {...}}`` body for one taxonomy code, leak-guarded at construction."""
    try:
        public = PublicError(
            code=code,
            message=GENERIC_MESSAGES[code],
            request_id=request_id,
            details=details,
        )
    except ValueError:
        # A detail fragment that could leak a path, digest or URL falls back to no detail at all.
        public = PublicError(
            code=code,
            message=GENERIC_MESSAGES[code],
            request_id=request_id,
            details=(),
        )
    error = public.model_dump(mode="json", exclude={"kind", "schema_version"})
    return {"error": error}


def error_response(
    request: Request,
    code: ErrorCode,
    details: tuple[str, ...] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=ERROR_STATUS[code],
        content=error_body(code, request_id(request), details),
        headers=dict(headers or {}),
    )


def _service_code(error: ServiceError) -> ErrorCode:
    if isinstance(error, AuthorizationError):
        return "FORBIDDEN"
    if isinstance(error, NotFound):
        return "NOT_FOUND"
    if isinstance(error, IdempotencyConflict):
        return "IDEMPOTENCY_CONFLICT"
    if isinstance(error, OptimisticVersionConflict):
        return "VERSION_CONFLICT"
    if isinstance(error, PersistenceConflict):
        return "RESULT_CONFLICT"
    if isinstance(error, LeaseLost):
        return "LEASE_LOST"
    if isinstance(error, PersistenceUnavailable):
        return "DEPENDENCY_UNAVAILABLE"
    # InvalidState/InvalidReference: valid HTTP whose domain request cannot be honoured.
    return "SCHEMA_INVALID"


def _query_code(code: str) -> ErrorCode:
    return code if code in ERROR_STATUS else "NOT_FOUND"


def install_error_handlers(app: Any) -> None:
    """Map every failure class onto the one error body shape."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, error: ApiError) -> JSONResponse:
        headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
        return error_response(request, error.code, error.details, headers)

    @app.exception_handler(PublicApiError)
    async def _query_error(request: Request, error: PublicApiError) -> JSONResponse:
        return error_response(request, _query_code(error.code))

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, error: ServiceError) -> JSONResponse:
        return error_response(request, _service_code(error))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(request, "SCHEMA_INVALID")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code: ErrorCode = "NOT_FOUND" if error.status_code in {404, 405} else "SCHEMA_INVALID"
        return error_response(request, code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled API failure")
        return error_response(request, "DEPENDENCY_UNAVAILABLE")


__all__ = [
    "ApiError",
    "GENERIC_MESSAGES",
    "error_body",
    "error_response",
    "install_error_handlers",
    "request_id",
]
