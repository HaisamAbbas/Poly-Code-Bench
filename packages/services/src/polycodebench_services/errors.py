"""Stable application errors; database exception details remain private."""

from polycodebench_core.application_errors import (
    AuthorizationError,
    IdempotencyConflict,
    InvalidReference,
    InvalidState,
    NotFound,
    OptimisticVersionConflict,
    PersistenceConflict,
    PersistenceUnavailable,
    ServiceError,
)

__all__ = [
    "AuthorizationError",
    "IdempotencyConflict",
    "InvalidReference",
    "InvalidState",
    "NotFound",
    "OptimisticVersionConflict",
    "PersistenceConflict",
    "PersistenceUnavailable",
    "ServiceError",
]
