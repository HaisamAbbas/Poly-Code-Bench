"""Public model-submission intake (Technical Specification 20.4).

A submission is validated metadata plus contact and permission details. The request schema has no
field that can carry a provider secret value and forbids unknown fields, so a secret supplied
anyway is rejected rather than stored. Accepted submissions are a pending request only: they never
start a run, contact an endpoint or spend a budget.
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
from uuid import uuid4

from polycodebench_core.application_errors import IdempotencyConflict
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_publication.aggregation import PublicationModel
from pydantic import Field, model_validator

from polycodebench_api.errors import ApiError

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
MAX_NOTES = 2000


class ModelSubmissionInput(PublicationModel):
    """What a submitter may send: metadata, contact and an explicit permission attestation."""

    kind: Literal["model_submission_input"] = "model_submission_input"
    model_name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=160)
    organization: str | None = Field(default=None, max_length=160)
    contact_email: str = Field(min_length=3, max_length=320)
    permission_attested: bool
    notes: str | None = Field(default=None, max_length=MAX_NOTES)

    @model_validator(mode="after")
    def attested(self) -> ModelSubmissionInput:
        if not _EMAIL.fullmatch(self.contact_email):
            raise ValueError("contact must be a valid email address")
        if not self.permission_attested:
            raise ValueError("permission attestation is required")
        return self


class ModelSubmission(PublicationModel):
    """The pending request returned to the submitter; it grants nothing."""

    kind: Literal["model_submission"] = "model_submission"
    submission_id: str = Field(min_length=1, max_length=64)
    status: Literal["pending"] = "pending"
    model_name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=160)
    organization: str | None = Field(default=None, max_length=160)
    contact_email: str = Field(min_length=3, max_length=320)
    permission_attested: bool
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    submitted_at: str = Field(min_length=1, max_length=40)


@dataclass(frozen=True)
class SubmissionRate:
    """Per-contact intake limit; rate limiting is named in the error taxonomy."""

    limit: int
    window_seconds: int


def _digest(payload: Mapping[str, object]) -> str:
    return "sha256:" + sha256_bytes(canonical_json_bytes(dict(payload)))


class SubmissionStore:
    """Durable intake ledger with the same idempotency and audit semantics as the release store."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS submissions(
                    submission_id TEXT PRIMARY KEY, subject TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(subject TEXT, action TEXT, request_id TEXT,
                    digest TEXT, result TEXT,
                    PRIMARY KEY(subject, action, request_id));
                CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY,
                    subject TEXT, action TEXT, request_id TEXT,
                    occurred_at TEXT, digest TEXT);
            """)

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
        """Accept one submission idempotently; a replay returns the first accepted request."""
        if not request_id.strip():
            raise ApiError("SCHEMA_INVALID", "idempotency key is required")
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
                    raise IdempotencyConflict()
                return cast(dict[str, object], json.loads(previous[1]))
            window_start = datetime.now(UTC).timestamp() - rate.window_seconds
            recent = db.execute(
                "SELECT COUNT(*) FROM submissions WHERE subject=? AND occurred_at>=?",
                (subject, _iso(window_start)),
            ).fetchone()
            if recent is not None and int(recent[0]) >= rate.limit:
                raise ApiError("RATE_LIMITED", "retry after the rate window")
            submission = ModelSubmission(
                submission_id=str(uuid4()),
                model_name=str(payload["model_name"]),
                provider=str(payload["provider"]),
                organization=_optional_str(payload.get("organization")),
                contact_email=str(payload["contact_email"]),
                permission_attested=bool(payload["permission_attested"]),
                notes=_optional_str(payload.get("notes")),
                submitted_at=now,
            )
            result = submission.model_dump(mode="json")
            db.execute(
                "INSERT INTO submissions VALUES (?, ?, ?, ?)",
                (submission.submission_id, subject, now, json.dumps(result, sort_keys=True)),
            )
            db.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                (subject, "submit", request_id, request_digest, json.dumps(result, sort_keys=True)),
            )
            db.execute(
                "INSERT INTO audit(subject,action,request_id,occurred_at,digest) VALUES (?,?,?,?,?)",
                (subject, "model_submission.create", request_id, now, _digest(result)),
            )
            return cast(dict[str, object], result)


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


__all__ = [
    "MAX_NOTES",
    "ModelSubmission",
    "ModelSubmissionInput",
    "SubmissionRate",
    "SubmissionStore",
]
