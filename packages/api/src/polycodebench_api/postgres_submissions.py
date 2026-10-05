"""PostgreSQL submission/review adapter with transactional rate and idempotency controls."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from polycodebench_core.application_errors import IdempotencyConflict, PersistenceUnavailable
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import audit_event, idempotency_record, model_submission
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_api.errors import ApiError
from polycodebench_api.submissions import SubmissionRate, _check_request_id

IDEMPOTENCY_TTL = timedelta(hours=24)


def _digest(payload: Mapping[str, object]) -> str:
    return sha256_bytes(canonical_json_bytes(dict(payload)))


def _rate_lock_key(subject: str) -> int:
    """Return a stable signed bigint for a transaction-scoped per-subject advisory lock."""
    raw = sha256(f"polycodebench:model-submission-rate:{subject}".encode()).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


class PostgresSubmissionStore:
    """Durable request state. Public submit never depends on endpoint or run services."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def submit(
        self,
        *,
        subject: str,
        request_id: str,
        payload: Mapping[str, object],
        rate: SubmissionRate,
    ) -> dict[str, object]:
        _check_request_id(request_id)
        digest = _digest(payload)
        now = datetime.now(UTC)
        request_route = "POST /v1/model-submissions"
        idempotency_id = uuid4()
        try:
            with self._engine.begin() as connection:
                claimed = connection.execute(
                    pg_insert(idempotency_record)
                    .values(
                        id=idempotency_id,
                        subject=subject,
                        route=request_route,
                        key=request_id,
                        request_digest=digest,
                        state="in_progress",
                        expires_at=now + IDEMPOTENCY_TTL,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            idempotency_record.c.subject,
                            idempotency_record.c.route,
                            idempotency_record.c.key,
                        ]
                    )
                    .returning(idempotency_record.c.id)
                ).scalar_one_or_none()
                if claimed is None:
                    existing = (
                        connection.execute(
                            select(idempotency_record)
                            .where(
                                idempotency_record.c.subject == subject,
                                idempotency_record.c.route == request_route,
                                idempotency_record.c.key == request_id,
                            )
                            .with_for_update()
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing is None:
                        raise PersistenceUnavailable()
                    if existing["expires_at"] <= now:
                        connection.execute(
                            delete(idempotency_record).where(
                                idempotency_record.c.id == existing["id"]
                            )
                        )
                        claimed = connection.execute(
                            pg_insert(idempotency_record)
                            .values(
                                id=idempotency_id,
                                subject=subject,
                                route=request_route,
                                key=request_id,
                                request_digest=digest,
                                state="in_progress",
                                expires_at=now + IDEMPOTENCY_TTL,
                            )
                            .on_conflict_do_nothing()
                            .returning(idempotency_record.c.id)
                        ).scalar_one_or_none()
                    else:
                        if existing["request_digest"] != digest:
                            raise ApiError("IDEMPOTENCY_CONFLICT")
                        if existing["state"] != "completed":
                            raise ApiError("DEPENDENCY_UNAVAILABLE")
                        previous = cast(dict[str, object], existing["response_payload"])
                        row = (
                            connection.execute(
                                select(model_submission).where(
                                    model_submission.c.id == UUID(str(previous["submission_id"])),
                                    model_submission.c.requester_subject == subject,
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                        if row is None:
                            raise PersistenceUnavailable()
                        return _owner_view(row)
                    if claimed is None:
                        raise ApiError("DEPENDENCY_UNAVAILABLE")

                # Serialize this subject's count-and-insert section. A hash collision can only
                # serialize unrelated subjects; it cannot let either subject exceed its limit.
                connection.execute(select(func.pg_advisory_xact_lock(_rate_lock_key(subject))))
                submitted_at = connection.execute(select(func.clock_timestamp())).scalar_one()
                window_start = submitted_at - timedelta(seconds=rate.window_seconds)
                count = connection.execute(
                    select(func.count())
                    .select_from(model_submission)
                    .where(
                        model_submission.c.requester_subject == subject,
                        model_submission.c.created_at >= window_start,
                    )
                ).scalar_one()
                if count >= rate.limit:
                    raise ApiError("RATE_LIMITED", retry_after=rate.window_seconds)

                submission_id = uuid4()
                document = {key: value for key, value in payload.items() if key != "kind"}
                row_document = {
                    "kind": "model_submission",
                    "submission_id": str(submission_id),
                    "status": "pending",
                    **document,
                    "submitted_at": submitted_at.isoformat().replace("+00:00", "Z"),
                    "row_version": 0,
                    "rejection_reason": None,
                    "resulting_run_id": None,
                    "run_status": None,
                }
                connection.execute(
                    insert(model_submission).values(
                        id=submission_id,
                        requester_subject=subject,
                        metadata_artifact_id=None,
                        request_document=document,
                        status="pending",
                        row_version=0,
                        created_at=submitted_at,
                    )
                )
                connection.execute(
                    update(idempotency_record)
                    .where(idempotency_record.c.id == idempotency_id)
                    .values(state="completed", response_code=201, response_payload=row_document)
                )
                _audit(
                    connection,
                    subject,
                    "model_submission.create",
                    str(submission_id),
                    request_id,
                    digest,
                )
                return row_document
        except (IdempotencyConflict, PersistenceUnavailable):
            raise
        except DBAPIError as error:
            raise map_database_error(error) from None

    def get_owned(self, *, subject: str, submission_id: str) -> dict[str, object]:
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(model_submission).where(
                            model_submission.c.id == UUID(submission_id),
                            model_submission.c.requester_subject == subject,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except (ValueError, TypeError):
            row = None
        except DBAPIError as error:
            raise map_database_error(error) from None
        if row is None or row["request_document"] is None:
            raise ApiError("NOT_FOUND")
        return _owner_view(row)

    def list_for_review(
        self, *, statuses: tuple[str, ...], limit: int = 100
    ) -> list[dict[str, object]]:
        allowed = {"pending", "under_review", "rejected", "approved", "withdrawn"}
        if not statuses or not set(statuses) <= allowed or not 1 <= limit <= 200:
            raise ApiError("SCHEMA_INVALID")
        try:
            with self._engine.connect() as connection:
                rows = (
                    connection.execute(
                        select(model_submission)
                        .where(
                            model_submission.c.status.in_(statuses),
                            model_submission.c.request_document.is_not(None),
                        )
                        .order_by(model_submission.c.created_at, model_submission.c.id)
                        .limit(limit)
                    )
                    .mappings()
                    .all()
                )
        except DBAPIError as error:
            raise map_database_error(error) from None
        return [_review_view(row) for row in rows]

    def get_for_review(self, *, submission_id: str) -> dict[str, object]:
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(model_submission).where(model_submission.c.id == UUID(submission_id))
                    )
                    .mappings()
                    .one_or_none()
                )
        except (ValueError, TypeError):
            row = None
        except DBAPIError as error:
            raise map_database_error(error) from None
        if row is None or row["request_document"] is None:
            raise ApiError("NOT_FOUND")
        return _review_view(row)

    def reject(
        self,
        *,
        submission_id: str,
        reviewer: str,
        reason: str,
        expected_version: int,
        request_id: str,
    ) -> dict[str, object]:
        _check_request_id(request_id)
        if not reason.strip() or len(reason) > 2000:
            raise ApiError("SCHEMA_INVALID")
        try:
            with self._engine.begin() as connection:
                row = _locked_submission(connection, submission_id)
                if row is None:
                    raise ApiError("NOT_FOUND")
                if row["request_document"] is None:
                    raise ApiError("NOT_FOUND")
                if row["row_version"] != expected_version:
                    raise ApiError("VERSION_CONFLICT")
                if row["status"] not in {"pending", "under_review"}:
                    raise ApiError("RESULT_CONFLICT")
                connection.execute(
                    update(model_submission)
                    .where(
                        model_submission.c.id == row["id"],
                        model_submission.c.row_version == expected_version,
                    )
                    .values(
                        status="rejected",
                        reviewer_subject=reviewer,
                        rejection_reason=reason.strip(),
                        row_version=expected_version + 1,
                    )
                )
                _audit(
                    connection,
                    reviewer,
                    "model_submission.reject",
                    str(row["id"]),
                    request_id,
                    _digest({"submission_id": submission_id, "reason": reason.strip()}),
                )
                updated = dict(row)
                updated.update(
                    status="rejected",
                    reviewer_subject=reviewer,
                    rejection_reason=reason.strip(),
                    row_version=expected_version + 1,
                )
                return _review_view(updated)
        except DBAPIError as error:
            raise map_database_error(error) from None

    def begin_approval(
        self,
        *,
        submission_id: str,
        reviewer: str,
        expected_version: int,
        request_id: str,
        approval_document: Mapping[str, object],
    ) -> dict[str, object]:
        _check_request_id(request_id)
        document = dict(approval_document)
        digest = _digest(document)
        try:
            with self._engine.begin() as connection:
                row = _locked_submission(connection, submission_id)
                if row is None or row["request_document"] is None:
                    raise ApiError("NOT_FOUND")
                if row["status"] == "approved":
                    if row["approval_digest"] != digest:
                        raise ApiError("RESULT_CONFLICT")
                    return {
                        "status": "approved",
                        "resulting_run_id": str(row["resulting_run_id"]),
                        "approval_digest": digest,
                        "replayed": True,
                    }
                if row["status"] == "under_review":
                    if row["reviewer_subject"] != reviewer or row["approval_digest"] != digest:
                        raise ApiError("RESULT_CONFLICT")
                    return {
                        "status": "under_review",
                        "resulting_run_id": str(row["resulting_run_id"])
                        if row["resulting_run_id"]
                        else None,
                        "approval_digest": digest,
                        "replayed": True,
                    }
                if row["status"] != "pending":
                    raise ApiError("RESULT_CONFLICT")
                if row["row_version"] != expected_version:
                    raise ApiError("VERSION_CONFLICT")
                connection.execute(
                    update(model_submission)
                    .where(
                        model_submission.c.id == row["id"],
                        model_submission.c.row_version == expected_version,
                    )
                    .values(
                        status="under_review",
                        reviewer_subject=reviewer,
                        approval_document=document,
                        approval_digest=digest,
                        row_version=expected_version + 1,
                    )
                )
                _audit(
                    connection,
                    reviewer,
                    "model_submission.approval_started",
                    str(row["id"]),
                    request_id,
                    digest,
                )
                return {
                    "status": "under_review",
                    "resulting_run_id": None,
                    "approval_digest": digest,
                    "replayed": False,
                }
        except DBAPIError as error:
            raise map_database_error(error) from None

    def finish_approval(
        self,
        *,
        submission_id: str,
        reviewer: str,
        run_id: str,
        approval_digest: str,
        request_id: str,
    ) -> dict[str, object]:
        _check_request_id(request_id)
        try:
            with self._engine.begin() as connection:
                row = _locked_submission(connection, submission_id)
                if row is None or row["request_document"] is None:
                    raise ApiError("NOT_FOUND")
                if row["approval_digest"] != approval_digest:
                    raise ApiError("RESULT_CONFLICT")
                if row["status"] == "approved":
                    if str(row["resulting_run_id"]) != run_id:
                        raise ApiError("RESULT_CONFLICT")
                    return _review_view(row)
                if row["status"] != "under_review" or row["reviewer_subject"] != reviewer:
                    raise ApiError("RESULT_CONFLICT")
                parsed_run_id = UUID(run_id)
                connection.execute(
                    update(model_submission)
                    .where(
                        model_submission.c.id == row["id"],
                        model_submission.c.row_version == row["row_version"],
                    )
                    .values(
                        status="approved",
                        resulting_run_id=parsed_run_id,
                        row_version=row["row_version"] + 1,
                    )
                )
                _audit(
                    connection,
                    reviewer,
                    "model_submission.approved",
                    str(row["id"]),
                    request_id,
                    approval_digest,
                )
                updated = dict(row)
                updated.update(
                    status="approved",
                    resulting_run_id=parsed_run_id,
                    row_version=row["row_version"] + 1,
                )
                return _review_view(updated)
        except DBAPIError as error:
            raise map_database_error(error) from None


def _locked_submission(connection: Connection, submission_id: str) -> Mapping[str, object] | None:
    try:
        statement = (
            select(model_submission)
            .where(model_submission.c.id == UUID(submission_id))
            .with_for_update()
        )
    except (ValueError, TypeError):
        return None
    return cast(
        Mapping[str, object] | None,
        connection.execute(statement).mappings().one_or_none(),
    )


def _owner_view(row: Mapping[str, object]) -> dict[str, object]:
    document = row["request_document"]
    if not isinstance(document, dict):
        raise PersistenceUnavailable("submission record is incomplete")
    status = "pending" if row["status"] == "under_review" else str(row["status"])
    return {
        "kind": "model_submission",
        "submission_id": str(row["id"]),
        **document,
        "status": status,
        "submitted_at": _timestamp(row["created_at"]),
        "row_version": int(row["row_version"]),
        "rejection_reason": row["rejection_reason"],
        "resulting_run_id": str(row["resulting_run_id"]) if row["resulting_run_id"] else None,
        "run_status": None,
    }


def _review_view(row: Mapping[str, object]) -> dict[str, object]:
    owner = _owner_view(row)
    approval = row["approval_document"]
    approval = approval if isinstance(approval, dict) else None
    return {
        "kind": "submission_review_view",
        "submission_id": owner["submission_id"],
        "requester_subject": row["requester_subject"],
        "status": row["status"],
        "model_name": owner["model_name"],
        "provider": owner["provider"],
        "organization": owner.get("organization"),
        "contact_email": owner["contact_email"],
        "endpoint_url": owner["endpoint_url"],
        "source_url": owner["source_url"],
        "source_license": owner["source_license"],
        "permission_attested": owner["permission_attested"],
        "submitted_at": owner["submitted_at"],
        "row_version": owner["row_version"],
        "reviewer_subject": row["reviewer_subject"],
        "rejection_reason": row["rejection_reason"],
        "permission_review": approval.get("permission_review") if approval else None,
        "approved_plan": approval.get("run_plan") if approval else None,
        "resulting_run_id": owner["resulting_run_id"],
    }


def _timestamp(value: object) -> str:
    if not isinstance(value, datetime):
        raise PersistenceUnavailable("submission timestamp is unavailable")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _audit(
    connection: Connection,
    subject: str,
    action: str,
    submission_id: str,
    request_id: str,
    digest: str,
) -> None:
    connection.execute(
        insert(audit_event).values(
            id=uuid4(),
            actor_subject=subject,
            action=action,
            resource_type="model_submission",
            resource_id=submission_id,
            before_digest=None,
            after_digest=digest,
            request_id=request_id,
            details={},
        )
    )


__all__ = ["PostgresSubmissionStore"]
