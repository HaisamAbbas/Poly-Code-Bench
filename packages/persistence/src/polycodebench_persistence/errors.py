"""Safe mapping from PostgreSQL/SQLAlchemy failures to public error codes."""

from __future__ import annotations

from polycodebench_core.application_errors import (
    AuthorizationError,
    IdempotencyConflict,
    InvalidReference,
    InvalidState,
    OptimisticVersionConflict,
    PersistenceConflict,
    PersistenceUnavailable,
)
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError


def map_database_error(error: DBAPIError) -> Exception:
    """Map SQLSTATE only; never expose SQL text, DSNs, parameters or server detail."""
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    constraint_name = getattr(getattr(original, "diag", None), "constraint_name", None)
    if sqlstate == "23503":
        return InvalidReference()
    if sqlstate == "42501":
        return AuthorizationError()
    if sqlstate == "23514":
        return InvalidState()
    if sqlstate == "23505" and constraint_name == "uq_idempotency_record_subject":
        return IdempotencyConflict()
    if sqlstate in {"23505", "23P01"}:
        return PersistenceConflict()
    if sqlstate == "40001":
        return OptimisticVersionConflict()
    if sqlstate == "40P01":
        return PersistenceConflict(
            "transaction serialization conflict; retry with the same idempotency key"
        )
    if isinstance(error, IntegrityError):
        return PersistenceConflict()
    if isinstance(error, (OperationalError, DBAPIError)):
        return PersistenceUnavailable()
    return PersistenceUnavailable()


def version_conflict() -> OptimisticVersionConflict:
    return OptimisticVersionConflict()
