"""Authenticated, bounded model-submission intake and review ledger.

Public input contains model/source metadata and an attestation only. It never accepts a secret,
model configuration, task set, budget or run plan. Endpoint strings are syntax-checked without DNS
resolution; this module never makes an outbound request.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_core.endpoint_policy import (
    EndpointNetworkPolicy,
    NetworkPolicyKind,
    parse_endpoint_url,
)
from polycodebench_core.model_contracts import EndpointPolicyViolation
from polycodebench_publication.aggregation import PublicationModel
from pydantic import Field, model_validator

from polycodebench_api.errors import ApiError

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+-]{0,159}$")
_SUBMISSION_STATUSES = {"pending", "under_review", "rejected", "approved"}


def _validate_public_url(value: str) -> str:
    """Validate a public HTTPS URL using existing endpoint rules, without resolving or fetching."""
    try:
        host = (urlsplit(value).hostname or "").lower().rstrip(".")
        policy = EndpointNetworkPolicy(
            kind=NetworkPolicyKind.PUBLIC_ALLOWLIST,
            allowed_hosts=(host,),
        )
        parse_endpoint_url(value, policy)
    except (EndpointPolicyViolation, ValueError, TypeError):
        raise ValueError("URL must be a canonical public HTTPS hostname URL") from None
    return value


class ModelSubmissionInput(PublicationModel):
    """Publicly acceptable information. Unknown fields (including credentials) are refused."""

    kind: Literal["model_submission_input"] = "model_submission_input"
    model_name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=120)
    organization: str | None = Field(default=None, max_length=160)
    contact_email: str = Field(min_length=3, max_length=320)
    endpoint_url: str = Field(min_length=9, max_length=512)
    source_url: str = Field(min_length=9, max_length=512)
    source_license: str = Field(min_length=1, max_length=160)
    permission_attested: bool

    @model_validator(mode="after")
    def validate_submission(self) -> ModelSubmissionInput:
        if not _EMAIL.fullmatch(self.contact_email):
            raise ValueError("contact must be a valid email address")
        if self.permission_attested is not True:
            raise ValueError("permission attestation is required")
        if not _IDENTIFIER.fullmatch(self.provider) or not _IDENTIFIER.fullmatch(self.model_name):
            raise ValueError("provider and model identifiers contain unsupported characters")
        _validate_public_url(self.endpoint_url)
        _validate_public_url(self.source_url)
        return self


class SubmissionRunProgress(PublicationModel):
    """Counts from the submitter's own run; values are persisted scheduler states."""

    attempt_states: dict[str, int]
    solve_job_states: dict[str, int]


class ModelSubmission(PublicationModel):
    """Submitter-safe status. Review notes and internal plan identities are not returned here."""

    kind: Literal["model_submission"] = "model_submission"
    submission_id: str = Field(min_length=1, max_length=64)
    status: Literal["pending", "rejected", "approved"]
    model_name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=120)
    organization: str | None = Field(default=None, max_length=160)
    contact_email: str = Field(min_length=3, max_length=320)
    endpoint_url: str = Field(min_length=9, max_length=512)
    source_url: str = Field(min_length=9, max_length=512)
    source_license: str = Field(min_length=1, max_length=160)
    permission_attested: bool
    submitted_at: str = Field(min_length=1, max_length=40)
    row_version: int = Field(ge=0)
    rejection_reason: str | None = Field(default=None, max_length=2000)
    resulting_run_id: str | None = Field(default=None, max_length=64)
    run_status: str | None = Field(default=None, max_length=32)
    run_progress: SubmissionRunProgress | None = None


class SubmissionReviewView(PublicationModel):
    """Private reviewer projection, available only after an explicit review permission check."""

    kind: Literal["submission_review_view"] = "submission_review_view"
    submission_id: str
    requester_subject: str
    status: Literal["pending", "under_review", "rejected", "approved"]
    model_name: str
    provider: str
    organization: str | None
    contact_email: str
    endpoint_url: str
    source_url: str
    source_license: str
    permission_attested: bool
    submitted_at: str
    row_version: int
    reviewer_subject: str | None
    rejection_reason: str | None
    permission_review: dict[str, object] | None = None
    approved_plan: dict[str, object] | None = None
    resulting_run_id: str | None = None


class PermissionReviewRecord(PublicationModel):
    """An internal, attributable decision about the submitter's source/permission declaration."""

    kind: Literal["source_permission_review"] = "source_permission_review"
    source_url: str = Field(min_length=9, max_length=512)
    source_license: str = Field(min_length=1, max_length=160)
    evidence_reference: str = Field(min_length=1, max_length=256)
    rights_confirmed: Literal[True]
    decision_reason: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class SubmissionRate:
    """Per-authenticated-subject intake limit."""

    limit: int
    window_seconds: int


def _digest(payload: Mapping[str, object]) -> str:
    return sha256_bytes(canonical_json_bytes(dict(payload)))


class SubmissionStore:
    """Transactional local request/review ledger with owner isolation and append-only audit rows."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS submissions(
                    submission_id TEXT PRIMARY KEY, subject TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, document TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', row_version INTEGER NOT NULL DEFAULT 0,
                    reviewer_subject TEXT, rejection_reason TEXT, approval_document TEXT,
                    approval_digest TEXT, resulting_run_id TEXT);
                CREATE TABLE IF NOT EXISTS requests(subject TEXT, action TEXT, request_id TEXT,
                    digest TEXT, result TEXT,
                    PRIMARY KEY(subject, action, request_id));
                CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY,
                    subject TEXT, action TEXT, request_id TEXT,
                    occurred_at TEXT, digest TEXT);
            """)
            self._ensure_columns(db)

    @staticmethod
    def _ensure_columns(db: sqlite3.Connection) -> None:
        """Upgrade the original pending-only development table without dropping requests."""
        present = {str(row[1]) for row in db.execute("PRAGMA table_info(submissions)")}
        migrations = {
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "row_version": "INTEGER NOT NULL DEFAULT 0",
            "reviewer_subject": "TEXT",
            "rejection_reason": "TEXT",
            "approval_document": "TEXT",
            "approval_digest": "TEXT",
            "resulting_run_id": "TEXT",
        }
        for name, declaration in migrations.items():
            if name not in present:
                db.execute(f"ALTER TABLE submissions ADD COLUMN {name} {declaration}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def submit(
        self,
        *,
        subject: str,
        request_id: str,
        payload: Mapping[str, object],
        rate: SubmissionRate,
    ) -> dict[str, object]:
        """Accept a pending request only. This method has no endpoint or run dependency."""
        _check_request_id(request_id)
        request_digest = _digest(payload)
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT digest, result FROM requests WHERE subject=? AND action=? AND request_id=?",
                (subject, "submit", request_id),
            ).fetchone()
            if previous is not None:
                if previous[0] != request_digest:
                    raise ApiError("IDEMPOTENCY_CONFLICT")
                result = cast(dict[str, object], json.loads(previous[1]))
                current = db.execute(
                    "SELECT status,row_version,rejection_reason,resulting_run_id FROM submissions "
                    "WHERE submission_id=? AND subject=?",
                    (result["submission_id"], subject),
                ).fetchone()
                if current is not None:
                    status = str(current["status"])
                    result["status"] = "pending" if status == "under_review" else status
                    result["row_version"] = int(current["row_version"])
                    result["rejection_reason"] = current["rejection_reason"]
                    result["resulting_run_id"] = current["resulting_run_id"]
                return result
            window_start = datetime.now(UTC).timestamp() - rate.window_seconds
            recent = db.execute(
                "SELECT COUNT(*) FROM submissions WHERE subject=? AND occurred_at>=?",
                (subject, _iso(window_start)),
            ).fetchone()
            if recent is not None and int(recent[0]) >= rate.limit:
                raise ApiError("RATE_LIMITED", retry_after=rate.window_seconds)
            submission_id = str(uuid4())
            result: dict[str, object] = {
                "kind": "model_submission",
                "submission_id": submission_id,
                "status": "pending",
                **{key: value for key, value in payload.items() if key != "kind"},
                "submitted_at": now,
                "row_version": 0,
                "rejection_reason": None,
                "resulting_run_id": None,
                "run_status": None,
            }
            document = json.dumps(result, sort_keys=True)
            db.execute(
                "INSERT INTO submissions(submission_id,subject,occurred_at,document,status,"
                "row_version) VALUES (?,?,?,?, 'pending', 0)",
                (submission_id, subject, now, document),
            )
            db.execute(
                "INSERT INTO requests VALUES (?, 'submit', ?, ?, ?)",
                (subject, request_id, request_digest, document),
            )
            self._audit(db, subject, "model_submission.create", request_id, now, request_digest)
            return result

    def get_owned(self, *, subject: str, submission_id: str) -> dict[str, object]:
        """Return one submitter's own status, with a uniform not-found result for every other ID."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM submissions WHERE submission_id=? AND subject=?",
                (submission_id, subject),
            ).fetchone()
        if row is None:
            raise ApiError("NOT_FOUND")
        document = cast(dict[str, object], json.loads(row["document"]))
        status = str(row["status"])
        document["status"] = "pending" if status == "under_review" else status
        document["row_version"] = int(row["row_version"])
        document["rejection_reason"] = row["rejection_reason"]
        document["resulting_run_id"] = row["resulting_run_id"]
        return document

    def list_for_review(
        self, *, statuses: tuple[str, ...], limit: int = 100
    ) -> list[dict[str, object]]:
        if not statuses or not set(statuses) <= _SUBMISSION_STATUSES or not 1 <= limit <= 200:
            raise ApiError("SCHEMA_INVALID")
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM submissions WHERE status IN ({placeholders}) "
                "ORDER BY occurred_at, submission_id LIMIT ?",
                (*statuses, limit),
            ).fetchall()
        return [self._review_view(row) for row in rows]

    def get_for_review(self, *, submission_id: str) -> dict[str, object]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM submissions WHERE submission_id=?", (submission_id,)
            ).fetchone()
        if row is None:
            raise ApiError("NOT_FOUND")
        return self._review_view(row)

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
        now = datetime.now(UTC).isoformat()
        digest = _digest(
            {"submission_id": submission_id, "status": "rejected", "reason": reason.strip()}
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,row_version FROM submissions WHERE submission_id=?",
                (submission_id,),
            ).fetchone()
            if row is None:
                raise ApiError("NOT_FOUND")
            if int(row["row_version"]) != expected_version:
                raise ApiError("VERSION_CONFLICT")
            if row["status"] not in {"pending", "under_review"}:
                raise ApiError("RESULT_CONFLICT")
            next_version = expected_version + 1
            db.execute(
                "UPDATE submissions SET status='rejected',reviewer_subject=?,rejection_reason=?,"
                "row_version=? WHERE submission_id=? AND row_version=?",
                (reviewer, reason.strip(), next_version, submission_id, expected_version),
            )
            self._audit(db, reviewer, "model_submission.reject", request_id, now, digest)
        return self.get_for_review(submission_id=submission_id)

    def begin_approval(
        self,
        *,
        submission_id: str,
        reviewer: str,
        expected_version: int,
        request_id: str,
        approval_document: Mapping[str, object],
    ) -> dict[str, object]:
        """Reserve a single exact approval plan before asking the idempotent run service."""
        _check_request_id(request_id)
        document = dict(approval_document)
        approval_digest = _digest(document)
        encoded = json.dumps(document, sort_keys=True)
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM submissions WHERE submission_id=?", (submission_id,)
            ).fetchone()
            if row is None:
                raise ApiError("NOT_FOUND")
            if row["status"] == "approved":
                if row["approval_digest"] != approval_digest:
                    raise ApiError("RESULT_CONFLICT")
                return {
                    "status": "approved",
                    "resulting_run_id": row["resulting_run_id"],
                    "approval_digest": approval_digest,
                    "replayed": True,
                }
            if row["status"] == "under_review":
                if row["reviewer_subject"] != reviewer or row["approval_digest"] != approval_digest:
                    raise ApiError("RESULT_CONFLICT")
                return {
                    "status": "under_review",
                    "resulting_run_id": row["resulting_run_id"],
                    "approval_digest": approval_digest,
                    "replayed": True,
                }
            if row["status"] != "pending":
                raise ApiError("RESULT_CONFLICT")
            if int(row["row_version"]) != expected_version:
                raise ApiError("VERSION_CONFLICT")
            db.execute(
                "UPDATE submissions SET status='under_review', reviewer_subject=?,"
                "approval_document=?,approval_digest=?,row_version=row_version+1 "
                "WHERE submission_id=? AND row_version=?",
                (reviewer, encoded, approval_digest, submission_id, expected_version),
            )
            self._audit(
                db, reviewer, "model_submission.approval_started", request_id, now, approval_digest
            )
        return {
            "status": "under_review",
            "resulting_run_id": None,
            "approval_digest": approval_digest,
            "replayed": False,
        }

    def finish_approval(
        self,
        *,
        submission_id: str,
        reviewer: str,
        run_id: str,
        approval_digest: str,
        request_id: str,
    ) -> dict[str, object]:
        """Record the idempotently-created queued run once, and only for the claimed plan."""
        _check_request_id(request_id)
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,row_version,reviewer_subject,approval_digest,resulting_run_id "
                "FROM submissions WHERE submission_id=?",
                (submission_id,),
            ).fetchone()
            if row is None:
                raise ApiError("NOT_FOUND")
            if row["approval_digest"] != approval_digest:
                raise ApiError("RESULT_CONFLICT")
            if row["status"] == "approved":
                if row["resulting_run_id"] != run_id:
                    raise ApiError("RESULT_CONFLICT")
                return self._review_view(row)
            if row["status"] != "under_review" or row["reviewer_subject"] != reviewer:
                raise ApiError("RESULT_CONFLICT")
            db.execute(
                "UPDATE submissions SET status='approved',resulting_run_id=?,"
                "row_version=row_version+1 WHERE submission_id=? AND row_version=?",
                (run_id, submission_id, int(row["row_version"])),
            )
            self._audit(db, reviewer, "model_submission.approved", request_id, now, approval_digest)
        return self.get_for_review(submission_id=submission_id)

    @staticmethod
    def _review_view(row: sqlite3.Row) -> dict[str, object]:
        document = cast(dict[str, object], json.loads(row["document"]))
        approval = json.loads(row["approval_document"]) if row["approval_document"] else None
        return {
            "kind": "submission_review_view",
            "submission_id": str(row["submission_id"]),
            "requester_subject": str(row["subject"]),
            "status": str(row["status"]),
            "model_name": document["model_name"],
            "provider": document["provider"],
            "organization": document.get("organization"),
            "contact_email": document["contact_email"],
            "endpoint_url": document["endpoint_url"],
            "source_url": document["source_url"],
            "source_license": document["source_license"],
            "permission_attested": document["permission_attested"],
            "submitted_at": document["submitted_at"],
            "row_version": int(row["row_version"]),
            "reviewer_subject": row["reviewer_subject"],
            "rejection_reason": row["rejection_reason"],
            "permission_review": approval.get("permission_review") if approval else None,
            "approved_plan": approval.get("run_plan") if approval else None,
            "resulting_run_id": row["resulting_run_id"],
        }

    @staticmethod
    def _audit(
        db: sqlite3.Connection,
        subject: str,
        action: str,
        request_id: str,
        occurred_at: str,
        digest: str,
    ) -> None:
        db.execute(
            "INSERT INTO audit(subject,action,request_id,occurred_at,digest) VALUES (?,?,?,?,?)",
            (subject, action, request_id, occurred_at, digest),
        )


def _check_request_id(request_id: str) -> None:
    if not request_id or len(request_id) > 128 or not request_id.isascii():
        raise ApiError("SCHEMA_INVALID")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


__all__ = [
    "ModelSubmission",
    "ModelSubmissionInput",
    "PermissionReviewRecord",
    "SubmissionRate",
    "SubmissionReviewView",
    "SubmissionStore",
]
