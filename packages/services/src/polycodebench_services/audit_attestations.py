"""Audited benchmark attestation signing, verification and lifecycle rules."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from polycodebench_core.audit_attestations import (
    AttestationLifecycleEvent,
    AttestationRevocationSnapshot,
    AttestationTrustStore,
    AttestationVerification,
    LifecycleStatus,
    PublicAuditAttestationClaims,
    PublicAuditHealthSummary,
    SignedPublicAuditAttestation,
    attestation_lifecycle_event_digest,
    attestation_signature_bytes,
)
from polycodebench_core.benchmark_audit_documents import (
    AuditAttestationDocument,
    audit_document_digest,
)
from polycodebench_core.models import Digest


def sign_public_attestation(
    document: AuditAttestationDocument,
    health: PublicAuditHealthSummary,
    *,
    key_id: UUID,
    private_key: Ed25519PrivateKey,
) -> SignedPublicAuditAttestation:
    """Sign a public allowlist and the digest of its complete private attestation document.

    The private key is supplied by an explicit caller (normally an approved key adapter). This
    function neither loads nor stores key material and is never called from a public route.
    """
    payload = document.payload
    if payload.signature_algorithm != "Ed25519":
        raise ValueError("audit attestation must declare Ed25519")
    if payload.key_ref.entity_kind != "signing_key" or payload.key_ref.entity_id != key_id:
        raise ValueError("attestation signing key does not match the declared key reference")
    if payload.review.kind != "benchmark_health" or payload.review.document_id != health.report_id:
        raise ValueError("public health claims must match the referenced reviewed health report")
    if health.review_state != "published":
        raise ValueError("only a published, reviewed health projection can be signed")

    claims = PublicAuditAttestationClaims(
        schema_version=1,
        attestation_id=document.id,
        attestation_digest=audit_document_digest(document),
        health=health,
        model_context_bound=payload.model_context is not None,
        issued_at=payload.issued_at,
        expires_at=payload.expires_at,
    )
    signing_bytes = attestation_signature_bytes(claims, key_id=key_id)
    signature = private_key.sign(signing_bytes)
    return SignedPublicAuditAttestation(
        schema_version=1,
        signature_algorithm="Ed25519",
        key_id=key_id,
        claims=claims,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )


def verify_public_attestation(
    signed: SignedPublicAuditAttestation,
    trust_store: AttestationTrustStore,
    *,
    revocation: AttestationRevocationSnapshot | None = None,
    expected_document: AuditAttestationDocument | None = None,
    now: datetime | None = None,
    revocation_max_age: timedelta = timedelta(hours=24),
) -> AttestationVerification:
    """Verify exact canonical bytes and report current endorsement separately from signature."""
    current = _aware_utc(now or datetime.now(UTC))
    if revocation_max_age <= timedelta(0):
        raise ValueError("revocation freshness window must be positive")
    trust_freshness = _freshness(
        _parse_utc(trust_store.checked_at) if trust_store.checked_at is not None else None,
        current,
        revocation_max_age,
    )
    trusted_key = next((key for key in trust_store.keys if key.key_id == signed.key_id), None)
    if trusted_key is None:
        return _verification(
            signature_valid=False,
            trusted_key=False,
            key_status="unknown",
            lifecycle_status="unknown",
            revocation_freshness="offline",
            current_endorsement=False,
            result="untrusted_signer",
        )

    if expected_document is not None and (
        expected_document.id != signed.claims.attestation_id
        or audit_document_digest(expected_document) != signed.claims.attestation_digest
    ):
        return _verification(
            signature_valid=False,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="unknown",
            revocation_freshness="offline",
            current_endorsement=False,
            result="scope_digest_mismatch",
        )

    issued = _parse_utc(signed.claims.issued_at)
    valid_from = _parse_utc(trusted_key.valid_from)
    valid_until = _parse_utc(trusted_key.valid_until) if trusted_key.valid_until else None
    if issued < valid_from or (valid_until is not None and issued > valid_until):
        return _verification(
            signature_valid=False,
            trusted_key=False,
            key_status=trusted_key.status,
            lifecycle_status="unknown",
            revocation_freshness="offline",
            current_endorsement=False,
            result="untrusted_signer",
        )

    try:
        raw_key = base64.b64decode(trusted_key.public_key_b64, validate=True)
        signature = base64.b64decode(signed.signature_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            signature,
            attestation_signature_bytes(
                signed.claims,
                key_id=signed.key_id,
                signature_algorithm=signed.signature_algorithm,
            ),
        )
    except (InvalidSignature, ValueError):
        return _verification(
            signature_valid=False,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="unknown",
            revocation_freshness="offline",
            current_endorsement=False,
            result="invalid_signature",
        )

    if trusted_key.status == "revoked":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status="revoked",
            lifecycle_status="revoked",
            revocation_freshness=trust_freshness,
            current_endorsement=False,
            result="revoked",
        )

    if revocation is None or revocation.checked_at is None:
        lifecycle_freshness = "offline"
        status = "unknown"
    else:
        checked_at = _parse_utc(revocation.checked_at)
        lifecycle_freshness = _freshness(checked_at, current, revocation_max_age)
        status = revocation.status
    freshness = _combined_freshness(trust_freshness, lifecycle_freshness)

    if lifecycle_freshness == "current" and status == "revoked":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="revoked",
            revocation_freshness=freshness,
            current_endorsement=False,
            result="revoked",
        )
    if lifecycle_freshness == "current" and status == "superseded":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="superseded",
            revocation_freshness=freshness,
            current_endorsement=False,
            result="superseded",
        )
    if lifecycle_freshness == "current" and status == "expired":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="expired",
            revocation_freshness=freshness,
            current_endorsement=False,
            result="expired",
        )
    if lifecycle_freshness == "current" and status == "unpublished":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="unpublished",
            revocation_freshness=freshness,
            current_endorsement=False,
            result="not_published",
        )
    if _parse_utc(signed.claims.expires_at) <= current:
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="expired",
            revocation_freshness=freshness,
            current_endorsement=False,
            result="expired",
        )
    if trusted_key.trust_level != "reviewed":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status=status,
            revocation_freshness=freshness,
            current_endorsement=False,
            result="development_key_not_endorsed",
        )
    if freshness == "offline":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="unknown",
            revocation_freshness="offline",
            current_endorsement=False,
            result="signature_valid_revocation_unchecked",
        )
    if freshness == "stale":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status=status,
            revocation_freshness="stale",
            current_endorsement=False,
            result="signature_valid_revocation_stale",
        )
    if lifecycle_freshness != "current" or status != "published":
        return _verification(
            signature_valid=True,
            trusted_key=True,
            key_status=trusted_key.status,
            lifecycle_status="unpublished",
            revocation_freshness=freshness,
            current_endorsement=False,
            result="not_published",
        )
    return _verification(
        signature_valid=True,
        trusted_key=True,
        key_status=trusted_key.status,
        lifecycle_status="published",
        revocation_freshness="current",
        current_endorsement=True,
        result="current_scoped_attestation",
    )


def transition_attestation(
    history: Sequence[AttestationLifecycleEvent],
    *,
    attestation_id: UUID,
    action: str,
    actor_id: str,
    actor_role: str,
    owner_id: str,
    recorded_at: str,
    expires_at: str,
    reason_code: str | None = None,
    evidence_digest: Digest | None = None,
    successor_id: UUID | None = None,
    signature_verification: AttestationVerification | None = None,
) -> AttestationLifecycleEvent:
    """Create one legal append-only event with reviewer, signer and publisher separation."""
    previous = history[-1] if history else None
    _validate_event_history(history, attestation_id, owner_id=owner_id)
    from_status: LifecycleStatus | None
    to_status: LifecycleStatus
    if previous is None:
        if action != "draft" or actor_role != "owner" or actor_id != owner_id:
            raise ValueError("attestation history must start with its owner-created draft")
        from_status = None
        to_status = "draft"
        sequence = 0
        previous_digest = None
    else:
        if previous.attestation_id != attestation_id:
            raise ValueError("attestation lifecycle event belongs to another attestation")
        if action not in _ALLOWED_TRANSITIONS.get(previous.to_status, {}):
            raise ValueError("attestation lifecycle transition is not allowed")
        from_status = previous.to_status
        to_status = _ALLOWED_TRANSITIONS[from_status][action]
        sequence = previous.sequence + 1
        previous_digest = attestation_lifecycle_event_digest(previous)

    _require_separation(
        action=action,
        history=history,
        actor_id=actor_id,
        actor_role=actor_role,
        owner_id=owner_id,
        recorded_at=recorded_at,
        expires_at=expires_at,
        reason_code=reason_code,
        evidence_digest=evidence_digest,
        successor_id=successor_id,
        signature_verification=signature_verification,
    )
    return AttestationLifecycleEvent(
        schema_version=1,
        attestation_id=attestation_id,
        sequence=sequence,
        previous_event_digest=previous_digest,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        actor_role=actor_role,  # type: ignore[arg-type]
        recorded_at=recorded_at,
        reason_code=reason_code,
        evidence_digest=evidence_digest,
        successor_id=successor_id,
    )


def lifecycle_event_digest(event: AttestationLifecycleEvent) -> Digest:
    return attestation_lifecycle_event_digest(event)


_ALLOWED_TRANSITIONS: dict[str, dict[str, LifecycleStatus]] = {
    "draft": {"request_review": "review_required"},
    "review_required": {"approve": "approved"},
    "approved": {"sign": "signed"},
    "signed": {"publish": "published", "revoke": "revoked"},
    "published": {"expire": "expired", "revoke": "revoked", "supersede": "superseded"},
    "expired": {"supersede": "superseded"},
    "revoked": {"supersede": "superseded"},
    "superseded": {},
}


def _require_separation(
    *,
    action: str,
    history: Sequence[AttestationLifecycleEvent],
    actor_id: str,
    actor_role: str,
    owner_id: str,
    recorded_at: str,
    expires_at: str,
    reason_code: str | None,
    evidence_digest: Digest | None,
    successor_id: UUID | None,
    signature_verification: AttestationVerification | None,
) -> None:
    recorded = _parse_utc(recorded_at)
    expires = _parse_utc(expires_at)
    prior = _events_by_role(history)
    if action == "request_review" and (actor_role != "owner" or actor_id != owner_id):
        raise ValueError("only the attestation owner can request review")
    if action == "approve":
        if actor_role != "reviewer" or actor_id in {owner_id, prior.get("owner")}:
            raise ValueError("attestation approval requires a distinct human reviewer")
        if evidence_digest is None:
            raise ValueError("attestation approval requires a review evidence digest")
    if action == "sign":
        if actor_role != "signer" or actor_id in {owner_id, prior.get("reviewer")}:
            raise ValueError("attestation signing requires a distinct authorized signer")
        if evidence_digest is None or signature_verification is None:
            raise ValueError("attestation signing requires a verifiable signature artifact")
        if not signature_verification.signature_valid or not signature_verification.trusted_key:
            raise ValueError("attestation signature is not trusted and valid")
    if action == "publish":
        if actor_role != "publisher" or actor_id in {
            owner_id,
            prior.get("reviewer"),
            prior.get("signer"),
        }:
            raise ValueError("attestation publication requires a distinct authorized publisher")
        if evidence_digest is None or signature_verification is None:
            raise ValueError("attestation publication requires verified signed evidence")
        if (
            not signature_verification.current_endorsement
            or signature_verification.result != "current_scoped_attestation"
        ):
            raise ValueError("attestation cannot publish without current key and revocation checks")
        if expires <= recorded:
            raise ValueError("expired attestations cannot be published or silently renewed")
    if action == "expire":
        if actor_role != "monitor" or recorded < expires:
            raise ValueError("attestation expiry events are allowed only after expiry")
    if action == "revoke":
        if actor_role not in {"reviewer", "publisher"} or actor_id == owner_id or not reason_code:
            raise ValueError("attestation revocation requires an authorized reviewer and reason")
    if action == "supersede":
        if (
            actor_role != "reviewer"
            or actor_id == owner_id
            or not successor_id
            or not evidence_digest
        ):
            raise ValueError("attestation correction requires a reviewer and successor evidence")


def _events_by_role(history: Sequence[AttestationLifecycleEvent]) -> dict[str, str]:
    result: dict[str, str] = {}
    for event in history:
        if event.actor_role in {"owner", "reviewer", "signer", "publisher"}:
            result[event.actor_role] = event.actor_id
    return result


def _validate_event_history(
    history: Sequence[AttestationLifecycleEvent], attestation_id: UUID, *, owner_id: str
) -> None:
    previous: AttestationLifecycleEvent | None = None
    actors: dict[str, str] = {}
    for index, event in enumerate(history):
        if event.attestation_id != attestation_id or event.sequence != index:
            raise ValueError("attestation lifecycle history identity or sequence is invalid")
        expected_digest = (
            attestation_lifecycle_event_digest(previous) if previous is not None else None
        )
        if event.previous_event_digest != expected_digest:
            raise ValueError("attestation lifecycle history digest chain is invalid")
        if previous is None:
            if event.from_status is not None or event.to_status != "draft":
                raise ValueError("attestation lifecycle history must begin with a draft")
            if event.actor_role != "owner" or event.actor_id != owner_id:
                raise ValueError("attestation draft must be created by its owner")
        else:
            valid_targets = _ALLOWED_TRANSITIONS.get(previous.to_status, {}).values()
            if (
                event.from_status != previous.to_status
                or event.to_status not in valid_targets
                or _parse_utc(event.recorded_at) < _parse_utc(previous.recorded_at)
            ):
                raise ValueError("attestation lifecycle history contains an invalid transition")

        expected_roles: dict[LifecycleStatus, set[str]] = {
            "draft": {"owner"},
            "review_required": {"owner"},
            "approved": {"reviewer"},
            "signed": {"signer"},
            "published": {"publisher"},
            "expired": {"monitor"},
            "revoked": {"reviewer", "publisher"},
            "superseded": {"reviewer"},
        }
        if event.actor_role not in expected_roles[event.to_status]:
            raise ValueError("attestation lifecycle event actor role is invalid for its state")
        if event.to_status in {"draft", "review_required"} and event.actor_id != owner_id:
            raise ValueError("attestation owner action was performed by another actor")
        if event.to_status in {"approved", "revoked", "superseded"} and event.actor_id == owner_id:
            raise ValueError("attestation owner cannot act as an independent reviewer")
        if event.to_status == "signed" and event.actor_id in {
            owner_id,
            actors.get("reviewer"),
        }:
            raise ValueError("attestation signer must be distinct from owner and reviewer")
        if event.to_status == "published" and event.actor_id in {
            owner_id,
            actors.get("reviewer"),
            actors.get("signer"),
        }:
            raise ValueError(
                "attestation publisher must be distinct from owner, reviewer and signer"
            )
        if event.actor_role in {"owner", "reviewer", "signer", "publisher"}:
            actors[event.actor_role] = event.actor_id
        previous = event


def _verification(**values: object) -> AttestationVerification:
    return AttestationVerification.model_validate(values, strict=True)


def _parse_utc(value: str) -> datetime:
    return _aware_utc(datetime.fromisoformat(value[:-1] + "+00:00"))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("attestation verification time must be timezone-aware UTC")
    return value.astimezone(UTC)


def _freshness(
    checked_at: datetime | None,
    current: datetime,
    max_age: timedelta,
) -> str:
    if checked_at is None:
        return "offline"
    return "current" if checked_at <= current and current - checked_at <= max_age else "stale"


def _combined_freshness(left: str, right: str) -> str:
    if "stale" in {left, right}:
        return "stale"
    if "offline" in {left, right}:
        return "offline"
    return "current"


__all__ = [
    "lifecycle_event_digest",
    "sign_public_attestation",
    "transition_attestation",
    "verify_public_attestation",
]
