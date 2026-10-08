"""Cryptographic commitments and fail-closed timestamp receipt verification."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from polycodebench_core.audit_temporal import evaluate_temporal_eligibility
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    ChronologyInterval,
    EntityRef,
    HidingCommitmentPayload,
    ImmutableArtifactRef,
    LocalTimestampReceipt,
    ModelContextDocument,
    TemporalAssessmentPayloadV2,
    TimestampEvidence,
    TrustedTimestampProof,
    audit_document_digest,
)
from polycodebench_core.canonical import canonical_json_bytes, parse_json_strict, sha256_bytes
from polycodebench_core.models import Digest

_COMMITMENT_DOMAIN = b"polycodebench:hiding-commitment:sha256-salted:v1\x00"


def build_temporal_assessment_payload(
    *,
    artifact_ref: ImmutableArtifactRef,
    chronology: tuple[ChronologyInterval, ...],
    model_context: ModelContextDocument | None,
    exposure_paths: tuple[AuditDocumentRef, ...] = (),
    hiding_commitment: HidingCommitmentPayload | None = None,
) -> TemporalAssessmentPayloadV2:
    """Freeze a model-context document and evaluator result into one assessment."""
    context_payload = model_context.payload if model_context is not None else None
    context_ref = (
        AuditDocumentRef(
            document_id=model_context.id,
            digest=audit_document_digest(model_context),
            kind="model_context",
        )
        if model_context is not None
        else None
    )
    result = evaluate_temporal_eligibility(chronology, context_payload)
    return TemporalAssessmentPayloadV2(
        artifact_ref=artifact_ref,
        model_context_ref=context_ref,
        model_context_snapshot=context_payload,
        chronology=chronology,
        exposure_paths=exposure_paths,
        hiding_commitment=hiding_commitment,
        status=result.status,
        explanation_code=result.explanation_code,
        claim_qualifier_codes=result.claim_qualifier_codes,
    )


@dataclass(frozen=True)
class CreatedHidingCommitment:
    """Public commitment plus a private nonce that must remain separately stored."""

    commitment: Digest
    nonce: bytes


def commitment_for_bytes(canonical_manifest: bytes, nonce: bytes) -> Digest:
    """Compute the hiding commitment over canonical manifest bytes and a 256-bit nonce."""
    if len(nonce) != 32:
        raise ValueError("hiding commitment nonce must contain 256 bits")
    parsed = parse_json_strict(canonical_manifest)
    if not isinstance(parsed, dict) or canonical_json_bytes(parsed) != canonical_manifest:
        raise ValueError("hiding commitment input must be a canonical JSON manifest object")
    return "sha256:" + hashlib.sha256(_COMMITMENT_DOMAIN + nonce + canonical_manifest).hexdigest()


def create_hiding_commitment(canonical_manifest: bytes) -> CreatedHidingCommitment:
    """Create a salted commitment; callers must persist the nonce as a private artifact."""
    nonce = secrets.token_bytes(32)
    return CreatedHidingCommitment(commitment_for_bytes(canonical_manifest, nonce), nonce)


def bind_commitment_artifacts(
    *,
    commitment: Digest,
    nonce_ref: ImmutableArtifactRef,
    local_receipt: LocalTimestampReceipt | None = None,
    trusted_proof: TrustedTimestampProof | None = None,
) -> HidingCommitmentPayload:
    """Bind a commitment to private nonce storage and optional commitment receipts."""
    if local_receipt is not None and not verify_local_timestamp_receipt(local_receipt, commitment):
        raise TimestampVerificationInvalid("local receipt signature or commitment is invalid")
    return HidingCommitmentPayload(
        scheme="sha256-salted-v1",
        commitment=commitment,
        nonce_ref=nonce_ref,
        local_receipt=local_receipt,
        trusted_proof=trusted_proof,
    )


def _local_receipt_signing_bytes(
    *,
    commitment: Digest,
    received_at: TimestampEvidence,
    key_ref: EntityRef,
    public_key_b64: str,
) -> bytes:
    return canonical_json_bytes(
        {
            "adapter_version": "local-ed25519-v1",
            "algorithm": "Ed25519",
            "commitment": commitment,
            "key_ref": key_ref.model_dump(mode="json"),
            "public_key_b64": public_key_b64,
            "received_at": received_at.model_dump(mode="json"),
            "schema_version": 1,
            "trust_label": "local_non_independent",
        }
    )


def create_local_timestamp_receipt(
    commitment: Digest,
    signing_key: Ed25519PrivateKey,
    key_ref: EntityRef,
) -> LocalTimestampReceipt:
    """Sign a local receipt for development; its trust label is never upgraded."""
    if key_ref.entity_kind != "signing_key":
        raise ValueError("local timestamp key reference must identify a signing key")
    now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    received_at = TimestampEvidence(
        value=now,
        precision="microsecond",
        uncertainty="Local system clock; not an independent timestamp authority.",
        source_ref=None,
    )
    public_key_b64 = base64.b64encode(
        signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    signing_bytes = _local_receipt_signing_bytes(
        commitment=commitment,
        received_at=received_at,
        key_ref=key_ref,
        public_key_b64=public_key_b64,
    )
    signature_b64 = base64.b64encode(signing_key.sign(signing_bytes)).decode("ascii")
    return LocalTimestampReceipt(
        schema_version=1,
        adapter_version="local-ed25519-v1",
        commitment=commitment,
        received_at=received_at,
        algorithm="Ed25519",
        key_ref=key_ref,
        public_key_b64=public_key_b64,
        trust_label="local_non_independent",
        signature_b64=signature_b64,
    )


def verify_local_timestamp_receipt(
    receipt: LocalTimestampReceipt,
    expected_commitment: Digest,
) -> bool:
    """Verify the Ed25519 signature and commitment binding of a local receipt."""
    if receipt.commitment != expected_commitment or receipt.trust_label != "local_non_independent":
        return False
    try:
        public_key_bytes = base64.b64decode(receipt.public_key_b64, validate=True)
        signature = base64.b64decode(receipt.signature_b64, validate=True)
        if len(public_key_bytes) != 32 or len(signature) != 64:
            return False
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(
            signature,
            _local_receipt_signing_bytes(
                commitment=receipt.commitment,
                received_at=receipt.received_at,
                key_ref=receipt.key_ref,
                public_key_b64=receipt.public_key_b64,
            ),
        )
    except (ValueError, InvalidSignature):
        return False
    return True


@dataclass(frozen=True)
class VerifiedTimestampEvidence:
    commitment: Digest
    receipt_time: TimestampEvidence
    certificate_refs: tuple[AuditDocumentRef, ...]
    verifier_ref: AuditDocumentRef


class TimestampAuthorityAdapter(Protocol):
    """Versioned adapter that cryptographically verifies one authority's token."""

    provider: str
    adapter_version: str
    protocol: str

    def verify_token(
        self,
        token: bytes,
        expected_commitment: Digest,
    ) -> VerifiedTimestampEvidence: ...


class TimestampVerificationUnavailable(RuntimeError):
    """Raised when no approved adapter exists for the receipt provider/version."""


class TimestampVerificationInvalid(ValueError):
    """Raised when receipt metadata or its cryptographic proof fails validation."""


class ApprovedTimestampAdapterRegistry:
    """Explicit allowlist of timestamp verifier implementations; empty means fail closed."""

    def __init__(
        self,
        adapters: Mapping[tuple[str, str, str], TimestampAuthorityAdapter],
    ) -> None:
        self._adapters = dict(adapters)
        for identity, adapter in self._adapters.items():
            if identity != (adapter.provider, adapter.adapter_version, adapter.protocol):
                raise ValueError("timestamp adapter registry key does not match adapter identity")

    def verify(
        self,
        proof: TrustedTimestampProof,
        expected_commitment: Digest,
        token_reader: Callable[[ImmutableArtifactRef], bytes],
    ) -> TrustedTimestampProof:
        if proof.commitment != expected_commitment:
            raise TimestampVerificationInvalid("timestamp token is bound to another commitment")
        identity = (proof.provider, proof.adapter_version, proof.protocol)
        adapter = self._adapters.get(identity)
        if adapter is None:
            raise TimestampVerificationUnavailable(
                "no approved verifier is configured for this provider, version and protocol"
            )
        if identity != (adapter.provider, adapter.adapter_version, adapter.protocol):
            raise TimestampVerificationInvalid("approved timestamp adapter identity changed")
        if proof.verification_state == "invalid":
            raise TimestampVerificationInvalid("invalid timestamp proof cannot be retried as valid")
        token = token_reader(proof.token_ref)
        if sha256_bytes(token) != proof.token_ref.digest:
            raise TimestampVerificationInvalid(
                "timestamp token bytes do not match their artifact digest"
            )
        evidence = adapter.verify_token(token, expected_commitment)
        if evidence.commitment != expected_commitment:
            raise TimestampVerificationInvalid("verified receipt does not bind the expected bytes")
        if not evidence.certificate_refs:
            raise TimestampVerificationInvalid(
                "verified receipt did not retain certificate evidence"
            )
        return TrustedTimestampProof(
            provider=proof.provider,
            adapter_version=proof.adapter_version,
            protocol=proof.protocol,
            commitment=expected_commitment,
            token_ref=proof.token_ref,
            receipt_time=evidence.receipt_time,
            certificate_refs=evidence.certificate_refs,
            verifier_ref=evidence.verifier_ref,
            verification_state="verified",
        )
