"""Durable reviewed local releases; public readers see only strict projections."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from polycodebench_core.application_errors import (
    AuthorizationError,
    IdempotencyConflict,
    InvalidState,
    NotFound,
    OptimisticVersionConflict,
    PersistenceConflict,
)
from polycodebench_core.canonical import canonical_json_bytes

REQUIRED_CHECKS = frozenset(
    {
        "membership",
        "evidence_completeness",
        "protocol_compatibility",
        "scorer_replay",
        "coverage_intervals",
        "native_labels",
        "disclosures",
        "rights",
        "judge_calibration",
        "provenance",
    }
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def canonical_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def content_digest(content: dict[str, Any], projection: dict[str, Any]) -> str:
    return digest({"content": content, "projection": projection})


@dataclass(frozen=True)
class ReleasePrincipal:
    subject_id: str
    roles: frozenset[str]
    mfa: bool = True


@dataclass(frozen=True)
class SigningKey:
    key_id: str
    private_key: Ed25519PrivateKey


@dataclass(frozen=True)
class ValidationEvidence:
    """Trusted validator receipt bound to the source snapshot and concrete evidence."""

    check: str
    subject_digest: str
    expected_digest: str
    observed_digest: str
    reference: str


def validate_projection(projection: dict[str, Any]) -> None:
    fields = {"schema_version", "fixture_kind", "scope", "cohort_digest", "metrics", "limitations"}
    if (
        set(projection) != fields
        or type(projection["schema_version"]) is not int
        or projection["schema_version"] != 1
    ):
        raise InvalidState("projection fields are outside the public allowlist")
    if projection["fixture_kind"] != "synthetic_internal" or projection["scope"] != "exploratory":
        raise InvalidState("local synthetic reports must be explicitly exploratory")
    if not isinstance(projection["cohort_digest"], str) or not _DIGEST.fullmatch(
        projection["cohort_digest"]
    ):
        raise InvalidState("projection requires a cohort digest")
    if not isinstance(projection["limitations"], list) or not projection["limitations"]:
        raise InvalidState("projection requires disclosed limitations")
    if any(
        not isinstance(item, str) or not item.strip() or len(item) > 2000
        for item in projection["limitations"]
    ):
        raise InvalidState("invalid limitation")
    metrics = projection["metrics"]
    if not isinstance(metrics, list) or not metrics:
        raise InvalidState("projection is incomplete")
    seen: set[str] = set()
    from decimal import Decimal, InvalidOperation

    for metric in metrics:
        if not isinstance(metric, dict) or set(metric) != {
            "metric_id",
            "value",
            "interval_low",
            "interval_high",
            "coverage",
            "conditional_on_pass",
        }:
            raise InvalidState("metric fields are outside the public allowlist")
        name = metric["metric_id"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", name)
            or name in seen
        ):
            raise InvalidState("invalid or duplicate public metric identity")
        seen.add(name)
        if type(metric["conditional_on_pass"]) is not bool:
            raise InvalidState("conditional metrics require an explicit label")
        if metric["conditional_on_pass"] and "conditional_on_pass" not in name:
            raise InvalidState("conditional metrics require a distinct labeled metric ID")
        for field in ("value", "interval_low", "interval_high", "coverage"):
            value = metric[field]
            if value is None and field != "coverage":
                continue
            if not isinstance(value, str):
                raise InvalidState("public scores must be decimal strings")
            try:
                number = Decimal(value)
                if not number.is_finite() or (field == "coverage" and not 0 <= number <= 1):
                    raise InvalidState("invalid numeric projection")
            except InvalidOperation as exc:
                raise InvalidState("invalid numeric projection") from exc
        low, high = metric["interval_low"], metric["interval_high"]
        if (low is None) != (high is None) or (low is not None and Decimal(low) > Decimal(high)):
            raise InvalidState("invalid interval projection")
        if metric["value"] is None and low is not None:
            raise InvalidState("unavailable metrics cannot carry an uncertainty interval")
    canonical_bytes(projection)


def verify_manifest(manifest: dict[str, Any], public_key: Ed25519PublicKey) -> bool:
    try:
        body = {key: value for key, value in manifest.items() if key != "signature"}
        if body["algorithm"] != "Ed25519":
            return False
        public_key.verify(
            base64.b64decode(manifest["signature"], validate=True), canonical_bytes(body)
        )
        return True
    except (InvalidSignature, ValueError, TypeError, KeyError):
        return False


class ReleaseStore:
    """SQLite is the atomic local publication target; no remote export is available.

    The caller authenticates principals and supplies receipts from trusted validators.
    Workers must never be granted this database or signing key.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pointers(target TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL, release_id TEXT);
                CREATE TABLE IF NOT EXISTS requests(subject TEXT, action TEXT, request_id TEXT,
                    digest TEXT, result TEXT,
                    PRIMARY KEY(subject, action, request_id));
                CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY,
                    subject TEXT, action TEXT,
                    release_id TEXT, occurred_at TEXT, request_id TEXT, digest TEXT);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _authorize(principal: ReleasePrincipal, role: str) -> None:
        if (
            not principal.subject_id
            or not principal.mfa
            or not principal.roles & {role, "administrator"}
        ):
            raise AuthorizationError()

    @staticmethod
    def _get(db: sqlite3.Connection, release_id: str) -> dict[str, Any]:
        row = db.execute("SELECT document FROM releases WHERE id=?", (release_id,)).fetchone()
        if row is None:
            raise NotFound("release does not exist")
        return cast(dict[str, Any], json.loads(row[0]))

    def get(self, release_id: str) -> dict[str, Any]:
        with self._connect() as db:
            return self._get(db, release_id)

    def current(self, target: str = "local:board") -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT generation, release_id FROM pointers WHERE target=?", (target,)
            ).fetchone()
            return dict(row) if row else {"generation": 0, "release_id": None}

    def _mutate(
        self,
        principal: ReleasePrincipal,
        role: str,
        action: str,
        request_id: str,
        payload: dict[str, Any],
        operation: Callable[[sqlite3.Connection], dict[str, Any]],
    ) -> dict[str, Any]:
        self._authorize(principal, role)
        if not request_id.strip():
            raise InvalidState("idempotency key is required")
        request_digest = digest(payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT digest, result FROM requests WHERE subject=? AND action=? AND request_id=?",
                (principal.subject_id, action, request_id),
            ).fetchone()
            if previous:
                if previous[0] != request_digest:
                    raise IdempotencyConflict()
                return cast(dict[str, Any], json.loads(previous[1]))
            result = operation(db)
            db.execute(
                "INSERT OR REPLACE INTO releases VALUES (?, ?)",
                (result["id"], canonical_bytes(result).decode()),
            )
            db.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                (
                    principal.subject_id,
                    action,
                    request_id,
                    request_digest,
                    canonical_bytes(result).decode(),
                ),
            )
            db.execute(
                "INSERT INTO audit(subject,action,release_id,occurred_at,request_id,digest) "
                "VALUES (?,?,?,?,?,?)",
                (
                    principal.subject_id,
                    action,
                    result["id"],
                    datetime.now(UTC).isoformat(),
                    request_id,
                    digest(result),
                ),
            )
            return result

    @staticmethod
    def _editable(document: dict[str, Any], version: int) -> None:
        if document["version"] != version:
            raise OptimisticVersionConflict()
        if document["state"] in {"published", "withdrawn"}:
            raise InvalidState("published history requires a successor correction")

    def draft(
        self,
        principal: ReleasePrincipal,
        content: dict[str, Any],
        projection: dict[str, Any],
        request_id: str,
        predecessor: str | None = None,
        correction_reason: str | None = None,
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            if predecessor:
                parent = self._get(db, predecessor)
                if (
                    parent["state"] not in {"published", "withdrawn"}
                    or not correction_reason
                    or not correction_reason.strip()
                ):
                    raise InvalidState("correction requires published history and a reason")
            elif correction_reason:
                raise InvalidState("correction requires predecessor")
            canonical_bytes(content)
            validate_projection(projection)
            return {
                "id": str(uuid4()),
                "version": 1,
                "state": "draft",
                "content": content,
                "projection": projection,
                "content_digest": content_digest(content, projection),
                "predecessor": predecessor,
                "correction_reason": correction_reason,
                "validation": None,
                "review": None,
                "approval": None,
                "manifest": None,
            }

        return self._mutate(
            principal,
            "curator",
            "draft",
            request_id,
            {
                "content": content,
                "projection": projection,
                "predecessor": predecessor,
                "correction_reason": correction_reason,
            },
            operation,
        )

    def update(
        self,
        principal: ReleasePrincipal,
        release_id: str,
        content: dict[str, Any],
        projection: dict[str, Any],
        expected_version: int,
        request_id: str,
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            doc = self._get(db, release_id)
            self._editable(doc, expected_version)
            validate_projection(projection)
            doc.update(
                content=content,
                projection=projection,
                content_digest=content_digest(content, projection),
                version=doc["version"] + 1,
                state="draft",
                validation=None,
                review=None,
                approval=None,
            )
            return doc

        return self._mutate(
            principal,
            "curator",
            "update",
            request_id,
            locals_payload(release_id, expected_version, content=content, projection=projection),
            operation,
        )

    def validate(
        self,
        principal: ReleasePrincipal,
        release_id: str,
        evidence: tuple[ValidationEvidence, ...],
        expected_version: int,
        request_id: str,
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            doc = self._get(db, release_id)
            self._editable(doc, expected_version)
            validate_projection(doc["projection"])
            if (
                len(evidence) != len(REQUIRED_CHECKS)
                or {item.check for item in evidence} != REQUIRED_CHECKS
            ):
                raise InvalidState("all concrete validation receipts are required")
            for item in evidence:
                if (
                    item.subject_digest != doc["content_digest"]
                    or not item.reference.strip()
                    or not _DIGEST.fullmatch(item.expected_digest)
                    or item.expected_digest != item.observed_digest
                ):
                    raise InvalidState("validation evidence mismatch or stale snapshot")
            report = {
                "subject_digest": doc["content_digest"],
                "receipts": sorted(
                    [asdict(item) for item in evidence], key=lambda item: item["check"]
                ),
            }
            doc.update(
                state="review_required",
                version=doc["version"] + 1,
                validation={"report": report, "digest": digest(report)},
                review=None,
                approval=None,
            )
            return doc

        return self._mutate(
            principal,
            "curator",
            "validate",
            request_id,
            locals_payload(
                release_id, expected_version, evidence=[asdict(item) for item in evidence]
            ),
            operation,
        )

    def review(
        self,
        principal: ReleasePrincipal,
        release_id: str,
        reason: str,
        expected_version: int,
        request_id: str,
    ) -> dict[str, Any]:
        return self._review_action(
            principal, release_id, reason, expected_version, request_id, approve=False
        )

    def approve(
        self,
        principal: ReleasePrincipal,
        release_id: str,
        reason: str,
        expected_version: int,
        request_id: str,
    ) -> dict[str, Any]:
        return self._review_action(
            principal, release_id, reason, expected_version, request_id, approve=True
        )

    def _review_action(
        self,
        principal: ReleasePrincipal,
        release_id: str,
        reason: str,
        expected_version: int,
        request_id: str,
        approve: bool,
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            doc = self._get(db, release_id)
            self._editable(doc, expected_version)
            if (
                doc["state"] != "review_required"
                or not reason.strip()
                or (approve and not doc["review"])
            ):
                raise InvalidState("validated content and recorded review are required")
            if doc["validation"]["report"]["subject_digest"] != doc["content_digest"]:
                raise InvalidState("stale validation")
            receipt = {
                "actor": principal.subject_id,
                "reason": reason,
                "content_digest": doc["content_digest"],
            }
            if approve:
                receipt["approval_digest"] = digest(
                    {
                        "membership": doc["content_digest"],
                        "policy": digest(doc["content"].get("policy", {})),
                        "validation": doc["validation"]["digest"],
                        "projection": digest(doc["projection"]),
                    }
                )
                doc.update(approval=receipt, state="approved")
            else:
                doc["review"] = receipt
            doc["version"] += 1
            return doc

        return self._mutate(
            principal,
            "reviewer",
            "approve" if approve else "review",
            request_id,
            locals_payload(release_id, expected_version, reason=reason),
            operation,
        )

    def publish(
        self,
        principal: ReleasePrincipal,
        release_id: str,
        signer: SigningKey,
        expected_generation: int,
        expected_version: int,
        request_id: str,
        target: str = "local:board",
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            if target != "local:board":
                raise InvalidState("only the explicit local publication target is supported")
            doc = self._get(db, release_id)
            self._editable(doc, expected_version)
            if (
                doc["state"] != "approved"
                or doc["approval"]["content_digest"] != doc["content_digest"]
            ):
                raise InvalidState("publication requires approval of exact content")
            validate_projection(doc["projection"])
            manifest = {
                "schema_version": 1,
                "release_id": release_id,
                "content_digest": doc["content_digest"],
                "projection_digest": digest(doc["projection"]),
                "validation_digest": doc["validation"]["digest"],
                "approval_digest": doc["approval"]["approval_digest"],
                "predecessor": doc["predecessor"],
                "algorithm": "Ed25519",
                "key_id": signer.key_id,
            }
            if not signer.key_id.strip():
                raise InvalidState("signing key identity is required")
            manifest["signature"] = base64.b64encode(
                signer.private_key.sign(canonical_bytes(manifest))
            ).decode("ascii")
            if not verify_manifest(manifest, signer.private_key.public_key()):
                raise InvalidState("signature verification failed")
            self._pointer(db, target, expected_generation, release_id)
            doc.update(state="published", version=doc["version"] + 1, manifest=manifest)
            return doc

        return self._mutate(
            principal,
            "publisher",
            "publish",
            request_id,
            locals_payload(
                release_id,
                expected_version,
                target=target,
                key_id=signer.key_id,
                expected_generation=expected_generation,
            ),
            operation,
        )

    @staticmethod
    def _pointer(
        db: sqlite3.Connection, target: str, generation: int, release_id: str | None
    ) -> None:
        row = db.execute("SELECT generation FROM pointers WHERE target=?", (target,)).fetchone()
        if (row[0] if row else 0) != generation:
            raise PersistenceConflict("publication pointer generation changed")
        db.execute(
            "INSERT INTO pointers VALUES (?,?,?) ON CONFLICT(target) DO UPDATE "
            "SET generation=excluded.generation,release_id=excluded.release_id",
            (target, generation + 1, release_id),
        )

    def withdraw(
        self,
        principal: ReleasePrincipal,
        release_id: str,
        reason: str,
        expected_version: int,
        expected_generation: int,
        request_id: str,
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            doc = self._get(db, release_id)
            if doc["version"] != expected_version:
                raise OptimisticVersionConflict()
            if doc["state"] != "published" or not reason.strip():
                raise InvalidState("withdrawal requires published release and reason")
            row = db.execute(
                "SELECT release_id,generation FROM pointers WHERE target='local:board'"
            ).fetchone()
            if row and row["generation"] != expected_generation:
                raise PersistenceConflict("publication pointer generation changed")
            if row and row["release_id"] == release_id:
                self._pointer(db, "local:board", expected_generation, None)
            doc.update(
                state="withdrawn",
                version=doc["version"] + 1,
                withdrawal={"reason": reason, "actor": principal.subject_id},
            )
            return doc

        return self._mutate(
            principal,
            "publisher",
            "withdraw",
            request_id,
            locals_payload(
                release_id, expected_version, reason=reason, expected_generation=expected_generation
            ),
            operation,
        )

    def public(self, release_id: str) -> dict[str, Any]:
        doc = self.get(release_id)
        if doc["state"] not in {"published", "withdrawn"}:
            raise NotFound("release is not published")
        return {
            "projection": doc["projection"],
            "manifest": doc["manifest"],
            "withdrawal": doc.get("withdrawal"),
        }

    def audit(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY sequence")]


def locals_payload(release_id: str, expected_version: int, **extra: Any) -> dict[str, Any]:
    return {"release_id": release_id, "expected_version": expected_version, **extra}
