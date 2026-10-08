"""Canonical public claims and detached signatures for benchmark audit attestations."""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_core.models import Digest

ATTESTATION_SIGNATURE_DOMAIN = "polycodebench.benchmark-audit-attestation.public.v1"
LifecycleStatus = Literal[
    "draft",
    "review_required",
    "approved",
    "signed",
    "published",
    "expired",
    "revoked",
    "superseded",
]


class PublicAuditHealthSummary(BaseModel):
    """Strict public aggregate fields allowed inside a signed attestation claim."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["public_benchmark_health"]
    schema_version: Literal[1]
    report_id: UUID
    review_state: Literal["published"]
    benchmark_label: str = Field(min_length=1, max_length=160)
    benchmark_version: str | None = Field(default=None, max_length=160)
    source_window_start: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"
    )
    source_window_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
    selected_tasks: int = Field(ge=0)
    complete_tasks: int = Field(ge=0)
    partial_tasks: int = Field(ge=0)
    unknown_tasks: int = Field(ge=0)
    unscanned_tasks: int = Field(ge=0)
    blocked_tasks: int = Field(ge=0)
    assessed_tasks: int = Field(ge=0)
    low_risk_tasks: int = Field(ge=0)
    medium_risk_tasks: int = Field(ge=0)
    high_risk_tasks: int = Field(ge=0)
    insufficient_risk_tasks: int = Field(ge=0)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("benchmark_label", "benchmark_version")
    @classmethod
    def public_text_has_no_private_markers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.lower()
        if any(marker in lowered for marker in ("http", "sha256:", "..", "/", "\\", "@")):
            raise ValueError("public benchmark labels must not contain private markers")
        return value

    @model_validator(mode="after")
    def counts_reconcile(self) -> PublicAuditHealthSummary:
        states = (
            self.complete_tasks
            + self.partial_tasks
            + self.unknown_tasks
            + self.unscanned_tasks
            + self.blocked_tasks
        )
        risks = (
            self.low_risk_tasks
            + self.medium_risk_tasks
            + self.high_risk_tasks
            + self.insufficient_risk_tasks
        )
        start = datetime.fromisoformat(self.source_window_start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(self.source_window_end.replace("Z", "+00:00"))
        if (
            states != self.selected_tasks
            or risks != self.assessed_tasks
            or self.assessed_tasks + self.unscanned_tasks != self.selected_tasks
            or end < start
            or len(set(self.limitations)) != len(self.limitations)
            or any(not _LIMITATION_CODE.fullmatch(code) for code in self.limitations)
        ):
            raise ValueError("public health attestation counts or scope do not reconcile")
        return self


_LIMITATION_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class PublicAuditAttestationClaims(BaseModel):
    """Allowlisted signed claims; no task-level evidence or private document refs."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    attestation_id: UUID
    attestation_digest: Digest
    health: PublicAuditHealthSummary
    model_context_bound: bool
    issued_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
    expires_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
    claim_limitations: tuple[
        Literal[
            "scoped_finite_evidence_only",
            "no_unseen_data_guarantee",
            "signature_is_not_certification",
            "model_eligibility_not_inferred",
            "timestamp_not_independently_proven",
        ],
        ...,
    ] = (
        "scoped_finite_evidence_only",
        "no_unseen_data_guarantee",
        "signature_is_not_certification",
        "model_eligibility_not_inferred",
        "timestamp_not_independently_proven",
    )

    @model_validator(mode="after")
    def date_range_is_ordered(self) -> PublicAuditAttestationClaims:
        issued = _utc(self.issued_at)
        expires = _utc(self.expires_at)
        if expires <= issued:
            raise ValueError("attestation expiry must be later than issue time")
        if len(set(self.claim_limitations)) != len(self.claim_limitations):
            raise ValueError("attestation claim limitations must be unique")
        if not set(type(self).model_fields["claim_limitations"].default).issubset(
            self.claim_limitations
        ):
            raise ValueError("required attestation claim limitations are missing")
        return self


class SignedPublicAuditAttestation(BaseModel):
    """Public verification package; contains only signed claims and signature bytes."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    signature_algorithm: Literal["Ed25519"]
    key_id: UUID
    claims: PublicAuditAttestationClaims
    signature_b64: str = Field(min_length=88, max_length=88, pattern=r"^[A-Za-z0-9+/]{86}==$")

    @field_validator("signature_b64")
    @classmethod
    def signature_is_canonical_ed25519(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise ValueError("attestation signature must be canonical base64") from error
        if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("attestation signature must encode 64 Ed25519 bytes")
        return value


class TrustedAttestationKey(BaseModel):
    """Public-only trust record. This model has no signing-key/private-key field."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    key_id: UUID
    signature_algorithm: Literal["Ed25519"]
    public_key_b64: str = Field(min_length=44, max_length=44, pattern=r"^[A-Za-z0-9+/]{43}=$")
    trust_level: Literal["development", "reviewed"]
    status: Literal["active", "revoked"]
    valid_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
    valid_until: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$",
    )
    revoked_at: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$",
    )

    @field_validator("public_key_b64")
    @classmethod
    def public_key_is_canonical_ed25519(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise ValueError("trusted public key must be canonical base64") from error
        if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("trusted Ed25519 public key must encode 32 bytes")
        return value

    @model_validator(mode="after")
    def key_validity_is_coherent(self) -> TrustedAttestationKey:
        start = _utc(self.valid_from)
        end = _utc(self.valid_until) if self.valid_until else None
        revoked = _utc(self.revoked_at) if self.revoked_at else None
        if end is not None and end <= start:
            raise ValueError("trusted signing key validity window is empty")
        if (self.status == "revoked") != (revoked is not None):
            raise ValueError(
                "revoked keys require a revocation timestamp and active keys forbid it"
            )
        return self


class AttestationTrustStore(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    checked_at: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$",
    )
    keys: tuple[TrustedAttestationKey, ...] = Field(max_length=256)

    @model_validator(mode="after")
    def key_ids_are_unique(self) -> AttestationTrustStore:
        if self.checked_at is not None:
            _utc(self.checked_at)
        if len({key.key_id for key in self.keys}) != len(self.keys):
            raise ValueError("attestation trust store contains duplicate key IDs")
        return self


class AttestationRevocationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: Literal["published", "revoked", "superseded", "expired", "unpublished"]
    checked_at: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$",
    )
    successor_id: UUID | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    @model_validator(mode="after")
    def snapshot_has_consistent_evidence(self) -> AttestationRevocationSnapshot:
        if self.status == "superseded" and self.successor_id is None:
            raise ValueError("superseded attestations require a successor reference")
        if self.status != "superseded" and self.successor_id is not None:
            raise ValueError("only superseded attestations may carry a successor reference")
        if self.status == "revoked" and self.reason_code is None:
            raise ValueError("revoked attestations require a reason code")
        return self


class PublicAuditAttestationProjection(BaseModel):
    """Reviewed public store record; raw audit documents are never accepted here."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    attestation: SignedPublicAuditAttestation
    lifecycle: AttestationRevocationSnapshot | None = None

    @model_validator(mode="after")
    def public_id_matches_claim(self) -> PublicAuditAttestationProjection:
        if self.attestation.claims.attestation_id.version != 4:
            raise ValueError("public attestation row identity must be UUIDv4")
        if (
            self.lifecycle is not None
            and self.lifecycle.successor_id == self.attestation.claims.attestation_id
        ):
            raise ValueError("an attestation cannot supersede itself")
        return self


class AttestationVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    signature_valid: bool
    trusted_key: bool
    key_status: Literal["active", "revoked", "unknown"]
    lifecycle_status: Literal[
        "published", "revoked", "superseded", "expired", "unpublished", "unknown"
    ]
    revocation_freshness: Literal["current", "stale", "offline"]
    current_endorsement: bool
    result: Literal[
        "current_scoped_attestation",
        "development_key_not_endorsed",
        "signature_valid_revocation_stale",
        "signature_valid_revocation_unchecked",
        "expired",
        "revoked",
        "superseded",
        "not_published",
        "untrusted_signer",
        "invalid_signature",
        "scope_digest_mismatch",
    ]


class AttestationLifecycleEvent(BaseModel):
    """Append-only status event data emitted by the lifecycle transition service."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    attestation_id: UUID
    sequence: int = Field(ge=0, le=9_007_199_254_740_991)
    previous_event_digest: Digest | None = None
    from_status: LifecycleStatus | None
    to_status: LifecycleStatus
    actor_id: str = Field(min_length=1, max_length=255, pattern=r"^[\x21-\x7E]+$")
    actor_role: Literal["owner", "reviewer", "signer", "publisher", "monitor"]
    recorded_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    evidence_digest: Digest | None = None
    successor_id: UUID | None = None

    @model_validator(mode="after")
    def event_evidence_matches_action(self) -> AttestationLifecycleEvent:
        if self.to_status == "revoked" and self.reason_code is None:
            raise ValueError("revocation events require a reason code")
        if self.to_status == "superseded" and self.successor_id is None:
            raise ValueError("supersession events require a successor attestation")
        if self.to_status == "superseded" and self.successor_id == self.attestation_id:
            raise ValueError("an attestation cannot supersede itself")
        if self.to_status in {"approved", "signed", "published", "superseded"} and (
            self.evidence_digest is None
        ):
            raise ValueError(
                "approval, signature, publication and correction events require evidence"
            )
        if self.to_status != "superseded" and self.successor_id is not None:
            raise ValueError("only supersession events may carry a successor attestation")
        return self


def attestation_signature_bytes(
    claims: PublicAuditAttestationClaims,
    *,
    key_id: UUID,
    signature_algorithm: str = "Ed25519",
) -> bytes:
    """Domain-separated canonical bytes signed by a reviewed attestation key."""
    if signature_algorithm != "Ed25519":
        raise ValueError("unsupported benchmark attestation signature algorithm")
    return canonical_json_bytes(
        {
            "algorithm": signature_algorithm,
            "claims": claims.model_dump(mode="json"),
            "domain": ATTESTATION_SIGNATURE_DOMAIN,
            "key_id": str(key_id),
        }
    )


def attestation_lifecycle_event_digest(event: AttestationLifecycleEvent) -> Digest:
    """Hash canonical lifecycle event bytes for immutable history chaining."""
    return "sha256:" + sha256(canonical_json_bytes(event.model_dump(mode="json"))).hexdigest()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("attestation timestamps must be UTC")
    return parsed.astimezone(UTC)
