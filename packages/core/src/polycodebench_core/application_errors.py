"""Stable cross-service error codes without transport or database details."""

from __future__ import annotations


class ServiceError(Exception):
    code = "SERVICE_ERROR"
    status_code = 500

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class AuthorizationError(ServiceError):
    code = "FORBIDDEN"
    status_code = 403


class IdempotencyConflict(ServiceError):
    code = "IDEMPOTENCY_CONFLICT"
    status_code = 409


class PersistenceConflict(ServiceError):
    code = "RESULT_CONFLICT"
    status_code = 409


class OptimisticVersionConflict(ServiceError):
    code = "VERSION_CONFLICT"
    status_code = 412


class PersistenceUnavailable(ServiceError):
    code = "DEPENDENCY_UNAVAILABLE"
    status_code = 503


class InvalidReference(ServiceError):
    code = "INVALID_REFERENCE"
    status_code = 422


class InvalidState(ServiceError):
    code = "INVALID_STATE"
    status_code = 422


class NotFound(ServiceError):
    code = "NOT_FOUND"
    status_code = 404
