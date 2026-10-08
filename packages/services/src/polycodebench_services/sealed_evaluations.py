"""Local-first sealing, exact disclosure barriers and synthetic canary evidence.

The module owns no key store, object store, source connector or logging backend. Those are
explicit injected boundaries. The only bundled key provider is labeled for local development;
production callers must inject the repository's independently approved KMS adapter.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import (
    aes_key_unwrap_with_padding,
    aes_key_wrap_with_padding,
)
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    CanaryCollisionCheck,
    CanaryObservationDocument,
    CanaryObservationPayload,
    CanaryPolicyPayloadV2,
    DocumentMetadata,
    EntityRef,
    HidingCommitmentPayload,
    ImmutableArtifactRef,
    SealAccessEventDocument,
    SealAccessEventPayload,
    SealedManifestDocumentV2,
    SealedManifestPayloadV2,
    SealState,
    TimestampEvidence,
    audit_document_digest,
    validate_sealed_manifest_transition,
)
from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_core.models import Digest

_NONCE_BYTES = 12
_DATA_KEY_BYTES = 32
_MAX_CANARY_COLLISION_ITEMS = 100_000
_MAX_CANARY_COLLISION_BYTES = 1_073_741_824
_DOMAIN = b"polycodebench:sealed-artifact:aes-256-gcm:v1\x00"
_SAFE_KEY_REFERENCE = re.compile(r"^[A-Za-z0-9._:/-]{1,512}$", re.ASCII)
_EXPOSURE_RANK: dict[str, int] = {
    "sealed": 0,
    "none": 0,
    "authorized_disclosure": 1,
    "public_exposed": 2,
    "compromised": 3,
    "retired": 4,
}


class SealedArtifactError(ValueError):
    """Safe, content-free errors for sealed-artifact operations."""


class SealedAccessDenied(SealedArtifactError):
    """Raised when scoped authorization does not allow a requested operation."""

    def __init__(self, message: str, *, successor_manifest: SealedManifestDocumentV2) -> None:
        super().__init__(message)
        self.successor_manifest = successor_manifest


@dataclass(frozen=True, slots=True)
class WrappedDataKey:
    ciphertext: bytes = field(repr=False)
    key_version: str
    wrapping_algorithm: str
    recovery_ref: str | None


class DataKeyProvider(Protocol):
    """Envelope-key adapter contract; production approval is deployment-owned."""

    provider_name: str
    key_version: str
    wrapping_algorithm: str
    recovery_ref: str | None
    assurance: Literal["approved_kms", "local_development"]

    def wrap_key(self, data_key: bytes) -> WrappedDataKey: ...

    def unwrap_key(
        self, wrapped_key: bytes, *, key_version: str, recovery_ref: str | None
    ) -> bytes: ...


class LocalDevelopmentAESKWPKeyProvider:
    """Explicit local adapter using AES-KWP with a caller-supplied development key.

    The key is never loaded from an implicit environment variable and is never returned by
    this class. This adapter is not an approved production KMS and cannot be promoted by
    changing its label; production deployments need an independently reviewed provider.
    """

    provider_name = "local-development-aes-kwp"
    wrapping_algorithm = "AES-KWP"
    assurance: Literal["approved_kms", "local_development"] = "local_development"
    recovery_ref: str | None

    def __init__(self, key: bytes, *, key_version: str) -> None:
        if len(key) not in {16, 24, 32}:
            raise SealedArtifactError("local AES-KWP key has an invalid size")
        if not _SAFE_KEY_REFERENCE.fullmatch(key_version):
            raise SealedArtifactError("key version reference is invalid")
        self.__key = bytes(key)
        self.key_version = key_version
        self.recovery_ref = f"local-development:{key_version}"

    def wrap_key(self, data_key: bytes) -> WrappedDataKey:
        if len(data_key) != _DATA_KEY_BYTES:
            raise SealedArtifactError("data key has an invalid size")
        try:
            wrapped = aes_key_wrap_with_padding(self.__key, data_key)
        except (ValueError, TypeError):
            raise SealedArtifactError("data key wrapping failed") from None
        return WrappedDataKey(
            ciphertext=wrapped,
            key_version=self.key_version,
            wrapping_algorithm=self.wrapping_algorithm,
            recovery_ref=self.recovery_ref,
        )

    def unwrap_key(
        self, wrapped_key: bytes, *, key_version: str, recovery_ref: str | None
    ) -> bytes:
        if key_version != self.key_version or recovery_ref != self.recovery_ref:
            raise SealedArtifactError("wrapped key version or recovery reference is unavailable")
        try:
            value = aes_key_unwrap_with_padding(self.__key, wrapped_key)
        except (ValueError, TypeError):
            raise SealedArtifactError("wrapped data key cannot be verified") from None
        if len(value) != _DATA_KEY_BYTES:
            raise SealedArtifactError("unwrapped data key has an invalid size")
        return value


@dataclass(frozen=True, slots=True)
class SealedCiphertext:
    tenant_id: UUID
    artifact_id: UUID
    media_type: str
    ciphertext: bytes = field(repr=False)
    wrapped_key: bytes = field(repr=False)
    key_provider: str
    key_version: str
    key_wrapping_algorithm: str
    recovery_ref: str | None


@dataclass(frozen=True, slots=True)
class SealedAccessRequest:
    tenant_id: UUID
    artifact_id: UUID
    actor: EntityRef
    operation: Literal[
        "local_screening",
        "candidate_delivery",
        "remote_query",
        "remote_delivery",
        "public_publication",
        "key_rotation",
    ]
    purpose: str
    recipient: str | None
    authorization_ref: AuditDocumentRef

    def __post_init__(self) -> None:
        if not _SAFE_KEY_REFERENCE.fullmatch(self.purpose):
            raise SealedArtifactError("sealed access purpose must be a bounded identifier")
        if self.recipient is not None and not _SAFE_KEY_REFERENCE.fullmatch(self.recipient):
            raise SealedArtifactError("sealed access recipient must be a bounded identifier")
        if self.operation == "local_screening" and self.recipient is None:
            raise SealedArtifactError("local screening requires an exact worker recipient")
        if self.operation not in {"local_screening", "key_rotation"} and self.recipient is None:
            raise SealedArtifactError("disclosure requires an exact recipient")


class SealedAccessAuthorizer(Protocol):
    """Deployment adapter that checks tenant, role, artifact, purpose and recipient scope."""

    def may_access(self, request: SealedAccessRequest, policy: AuditDocumentRef) -> bool: ...

    def may_disclose(
        self,
        request: SealedAccessRequest,
        policy: AuditDocumentRef,
        payload_digest: Digest,
    ) -> bool: ...

    def may_rotate_keys(self, request: SealedAccessRequest, policy: AuditDocumentRef) -> bool: ...


class AccessEventAppender(Protocol):
    """Atomically append an event and its manifest successor; disclosure fails closed."""

    def append_access_event(
        self,
        event: SealAccessEventDocument,
        successor: SealedManifestDocumentV2,
    ) -> None: ...


class PrivateArtifactWriter(Protocol):
    """Storage adapter that returns a verified private artifact reference."""

    def put_private(
        self, body: bytes, *, artifact_id: UUID, media_type: str
    ) -> ImmutableArtifactRef: ...


@dataclass(frozen=True, slots=True)
class PrivateCanaryMarker:
    value: str = field(repr=False)
    artifact_ref: ImmutableArtifactRef
    wrapped_key_ref: ImmutableArtifactRef
    envelope: SealedCiphertext = field(repr=False)
    collision_check: CanaryCollisionCheck


@dataclass(frozen=True, slots=True)
class CanarySourceCandidate:
    body: bytes = field(repr=False)
    match_evidence_ref: AuditDocumentRef
    source_dates: tuple[TimestampEvidence, ...]
    prior_publication: Literal["not_previously_published", "previously_published", "unknown"]
    source_review_ref: AuditDocumentRef | None


@dataclass(frozen=True, slots=True)
class CanaryObservationBatch:
    documents: tuple[CanaryObservationDocument, ...]
    matched_candidate_count: int


@dataclass(frozen=True, slots=True)
class DisclosureResult[ResultT]:
    result: ResultT
    manifest: SealedManifestDocumentV2
    access_event_ref: AuditDocumentRef
    access_event: SealAccessEventDocument


@dataclass(frozen=True, slots=True)
class LocalScreeningResult:
    result: bool
    manifest: SealedManifestDocumentV2
    access_event_ref: AuditDocumentRef


@dataclass(frozen=True, slots=True)
class KeyRotationResult:
    envelope: SealedCiphertext
    manifest: SealedManifestDocumentV2
    access_event_ref: AuditDocumentRef


def seal_private_artifact(
    plaintext: bytes,
    *,
    tenant_id: UUID,
    artifact_id: UUID,
    media_type: str,
    key_provider: DataKeyProvider,
    allow_local_development_key: bool = False,
) -> SealedCiphertext:
    """Encrypt one artifact under a fresh AES-256 DEK and bind tenant/artifact AAD."""
    _validate_provider(key_provider, allow_local_development_key=allow_local_development_key)
    if not isinstance(plaintext, bytes):
        raise SealedArtifactError("sealed artifact input must be bytes")
    if not media_type or len(media_type) > 255 or not media_type.isascii():
        raise SealedArtifactError("sealed artifact media type is invalid")
    data_key = secrets.token_bytes(_DATA_KEY_BYTES)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    aad = _associated_data(tenant_id, artifact_id, media_type)
    try:
        ciphertext = nonce + AESGCM(data_key).encrypt(nonce, plaintext, aad)
        wrapped = key_provider.wrap_key(data_key)
    except SealedArtifactError:
        raise
    except Exception:
        raise SealedArtifactError("sealed artifact encryption failed") from None
    if (
        wrapped.key_version != key_provider.key_version
        or wrapped.wrapping_algorithm != key_provider.wrapping_algorithm
        or wrapped.recovery_ref != key_provider.recovery_ref
        or not wrapped.ciphertext
    ):
        raise SealedArtifactError("key provider returned inconsistent envelope metadata")
    return SealedCiphertext(
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        media_type=media_type,
        ciphertext=ciphertext,
        wrapped_key=wrapped.ciphertext,
        key_provider=key_provider.provider_name,
        key_version=wrapped.key_version,
        key_wrapping_algorithm=wrapped.wrapping_algorithm,
        recovery_ref=wrapped.recovery_ref,
    )


def bind_sealed_artifact_refs(
    envelope: SealedCiphertext,
    *,
    ciphertext_ref: ImmutableArtifactRef,
    wrapped_key_ref: ImmutableArtifactRef,
) -> None:
    """Validate storage receipts before their refs are admitted to a sealed manifest."""
    if ciphertext_ref.artifact_id != envelope.artifact_id:
        raise SealedArtifactError("ciphertext artifact reference has the wrong identity")
    if ciphertext_ref.visibility == "public":
        raise SealedArtifactError("sealed ciphertext cannot use public storage")
    if ciphertext_ref.media_type != envelope.media_type:
        raise SealedArtifactError("sealed ciphertext media type does not match its envelope")
    if ciphertext_ref.digest != _digest(envelope.ciphertext):
        raise SealedArtifactError("stored ciphertext digest does not match encrypted bytes")
    if (
        wrapped_key_ref.visibility != "private"
        or wrapped_key_ref.media_type != "application/vnd.polycodebench.wrapped-key"
    ):
        raise SealedArtifactError("wrapped data keys must use private storage")
    if wrapped_key_ref.digest != _digest(envelope.wrapped_key):
        raise SealedArtifactError("stored wrapped-key digest does not match key bytes")


def build_sealed_manifest(
    envelope: SealedCiphertext,
    *,
    ciphertext_ref: ImmutableArtifactRef,
    wrapped_key_ref: ImmutableArtifactRef,
    hiding_commitment: HidingCommitmentPayload,
    access_policy: AuditDocumentRef,
    retention_policy: AuditDocumentRef,
    actor: str,
) -> SealedManifestDocumentV2:
    """Bind verified private storage refs, the salted commitment and key-recovery metadata."""
    bind_sealed_artifact_refs(
        envelope,
        ciphertext_ref=ciphertext_ref,
        wrapped_key_ref=wrapped_key_ref,
    )
    if hiding_commitment.nonce_ref.visibility != "private":
        raise SealedArtifactError("hiding commitment nonce must remain private")
    payload = SealedManifestPayloadV2(
        tenant_id=envelope.tenant_id,
        encrypted_artifact_refs=(ciphertext_ref,),
        wrapped_key_ref=wrapped_key_ref,
        hiding_commitment=hiding_commitment,
        encryption_algorithm="AES-256-GCM",
        key_wrapping_algorithm=envelope.key_wrapping_algorithm,
        key_provider=envelope.key_provider,
        key_version=envelope.key_version,
        recovery_ref=envelope.recovery_ref,
        nonce_length_bytes=12,
        access_policy=access_policy,
        retention_policy=retention_policy,
        access_event_refs=(),
        disclosure_state="sealed",
    )
    return SealedManifestDocumentV2(
        id=uuid4(),
        kind="sealed_manifest",
        schema_version=2,
        payload=payload,
        metadata=_document_metadata(actor),
    )


def _decrypt_private_artifact(
    envelope: SealedCiphertext,
    *,
    tenant_id: UUID,
    artifact_id: UUID,
    key_provider: DataKeyProvider,
    manifest: SealedManifestDocumentV2,
    allow_local_development_key: bool = False,
) -> bytes:
    """Authenticate manifest/storage bindings before decrypting into scoped worker memory."""
    _validate_provider(key_provider, allow_local_development_key=allow_local_development_key)
    payload = manifest.payload
    ciphertext_ref = next(
        (ref for ref in payload.encrypted_artifact_refs if ref.artifact_id == artifact_id), None
    )
    if (
        tenant_id != payload.tenant_id
        or envelope.tenant_id != tenant_id
        or envelope.artifact_id != artifact_id
        or payload.key_provider != key_provider.provider_name
        or payload.key_version != key_provider.key_version
        or payload.key_wrapping_algorithm != key_provider.wrapping_algorithm
        or payload.recovery_ref != key_provider.recovery_ref
        or envelope.key_provider != payload.key_provider
        or envelope.key_version != payload.key_version
        or envelope.key_wrapping_algorithm != payload.key_wrapping_algorithm
        or envelope.recovery_ref != payload.recovery_ref
        or ciphertext_ref is None
        or ciphertext_ref.media_type != envelope.media_type
        or ciphertext_ref.digest != _digest(envelope.ciphertext)
        or payload.wrapped_key_ref.digest != _digest(envelope.wrapped_key)
    ):
        raise SealedArtifactError("sealed artifact scope or manifest binding is invalid")
    if len(envelope.ciphertext) <= _NONCE_BYTES:
        raise SealedArtifactError("sealed artifact envelope is truncated")
    nonce, ciphertext = envelope.ciphertext[:_NONCE_BYTES], envelope.ciphertext[_NONCE_BYTES:]
    try:
        data_key = key_provider.unwrap_key(
            envelope.wrapped_key,
            key_version=payload.key_version,
            recovery_ref=payload.recovery_ref,
        )
        if len(data_key) != _DATA_KEY_BYTES:
            raise SealedArtifactError("unwrapped data key has an invalid size")
        return AESGCM(data_key).decrypt(
            nonce,
            ciphertext,
            _associated_data(tenant_id, artifact_id, envelope.media_type),
        )
    except InvalidTag:
        raise SealedArtifactError("sealed artifact authentication failed") from None
    except SealedArtifactError:
        raise
    except Exception:
        raise SealedArtifactError("sealed artifact decryption failed") from None


def rewrap_data_key(
    envelope: SealedCiphertext,
    *,
    old_provider: DataKeyProvider,
    new_provider: DataKeyProvider,
    allow_local_development_key: bool = False,
) -> SealedCiphertext:
    """Rotate a wrapped DEK while preserving ciphertext bytes and task identity."""
    _validate_provider(old_provider, allow_local_development_key=allow_local_development_key)
    _validate_provider(new_provider, allow_local_development_key=allow_local_development_key)
    if (
        envelope.key_provider != old_provider.provider_name
        or envelope.key_version != old_provider.key_version
        or envelope.key_wrapping_algorithm != old_provider.wrapping_algorithm
        or envelope.recovery_ref != old_provider.recovery_ref
    ):
        raise SealedArtifactError("old key provider does not match sealed envelope metadata")
    if (
        new_provider.provider_name == old_provider.provider_name
        and new_provider.key_version == old_provider.key_version
    ):
        raise SealedArtifactError("key rotation requires a distinct provider version")
    try:
        data_key = old_provider.unwrap_key(
            envelope.wrapped_key,
            key_version=envelope.key_version,
            recovery_ref=envelope.recovery_ref,
        )
        if len(data_key) != _DATA_KEY_BYTES:
            raise SealedArtifactError("unwrapped data key has an invalid size")
        wrapped = new_provider.wrap_key(data_key)
    except SealedArtifactError:
        raise
    except Exception:
        raise SealedArtifactError("data key rotation failed") from None
    if (
        wrapped.key_version != new_provider.key_version
        or wrapped.wrapping_algorithm != new_provider.wrapping_algorithm
        or wrapped.recovery_ref != new_provider.recovery_ref
    ):
        raise SealedArtifactError("new key provider returned inconsistent envelope metadata")
    return SealedCiphertext(
        tenant_id=envelope.tenant_id,
        artifact_id=envelope.artifact_id,
        media_type=envelope.media_type,
        ciphertext=envelope.ciphertext,
        wrapped_key=wrapped.ciphertext,
        key_provider=new_provider.provider_name,
        key_version=wrapped.key_version,
        key_wrapping_algorithm=wrapped.wrapping_algorithm,
        recovery_ref=wrapped.recovery_ref,
    )


def rotate_sealed_artifact_key(
    envelope: SealedCiphertext,
    *,
    manifest: SealedManifestDocumentV2,
    actor: EntityRef,
    authorization_ref: AuditDocumentRef,
    purpose: str,
    old_provider: DataKeyProvider,
    new_provider: DataKeyProvider,
    authorizer: SealedAccessAuthorizer,
    event_appender: AccessEventAppender,
    artifact_writer: PrivateArtifactWriter,
    allow_local_development_key: bool = False,
) -> KeyRotationResult:
    """Rotate one artifact's DEK wrapper and atomically append the new exposure head."""
    _ensure_active(manifest)
    payload = manifest.payload
    ciphertext_ref = payload.encrypted_artifact_refs[0]
    request = SealedAccessRequest(
        tenant_id=payload.tenant_id,
        artifact_id=envelope.artifact_id,
        actor=actor,
        operation="key_rotation",
        purpose=purpose,
        recipient=None,
        authorization_ref=authorization_ref,
    )
    if (
        ciphertext_ref.artifact_id != envelope.artifact_id
        or envelope.tenant_id != payload.tenant_id
        or ciphertext_ref.digest != _digest(envelope.ciphertext)
        or payload.wrapped_key_ref.digest != _digest(envelope.wrapped_key)
        or payload.encryption_algorithm != "AES-256-GCM"
        or payload.key_provider != old_provider.provider_name
        or payload.key_version != old_provider.key_version
        or payload.key_wrapping_algorithm != old_provider.wrapping_algorithm
        or payload.recovery_ref != old_provider.recovery_ref
    ):
        raise SealedArtifactError("key rotation input does not match the sealed manifest")
    if not _authorization_succeeded(
        lambda: authorizer.may_rotate_keys(request, payload.access_policy)
    ):
        _, successor, _ = _append_event(
            event_appender,
            manifest=manifest,
            request=request,
            outcome="denied",
            payload_digest=None,
            exposure="none",
        )
        raise SealedAccessDenied(
            "sealed key rotation is not authorized", successor_manifest=successor
        )
    rotated = rewrap_data_key(
        envelope,
        old_provider=old_provider,
        new_provider=new_provider,
        allow_local_development_key=allow_local_development_key,
    )
    wrapped_key_ref = _store_private_artifact(
        artifact_writer,
        rotated.wrapped_key,
        media_type="application/vnd.polycodebench.wrapped-key",
    )
    event_ref, successor, _ = _append_event(
        event_appender,
        manifest=manifest,
        request=request,
        outcome="authorized",
        payload_digest=None,
        exposure="none",
        rotated_envelope=rotated,
        rotated_wrapped_key_ref=wrapped_key_ref,
    )
    return KeyRotationResult(rotated, successor, event_ref)


def validate_sealed_manifest_successor(
    previous: SealedManifestDocumentV2,
    successor: SealedManifestDocumentV2,
    appended_event: SealAccessEventDocument,
) -> None:
    """Reject reseal/key rotation successors that alter identity or erase exposure history."""
    try:
        validate_sealed_manifest_transition(previous, successor, appended_event)
    except ValueError as error:
        raise SealedArtifactError(str(error)) from None


def create_canary_marker(
    *,
    tenant_id: UUID,
    local_corpus: Sequence[bytes],
    collision_scope_ref: AuditDocumentRef,
    artifact_writer: PrivateArtifactWriter,
    key_provider: DataKeyProvider,
    allow_local_development_key: bool = False,
    max_attempts: int = 8,
) -> PrivateCanaryMarker:
    """Generate, encrypt and privately store a random marker after local collision checks."""
    if not local_corpus:
        raise SealedArtifactError("canary collision check requires a non-empty local corpus")
    if len(local_corpus) > _MAX_CANARY_COLLISION_ITEMS or any(
        not isinstance(item, bytes) for item in local_corpus
    ):
        raise SealedArtifactError("local canary collision corpus exceeds its item limit")
    if sum(map(len, local_corpus)) > _MAX_CANARY_COLLISION_BYTES:
        raise SealedArtifactError("local canary collision corpus exceeds its byte limit")
    if collision_scope_ref.kind != "corpus_snapshot":
        raise SealedArtifactError("canary collision scope must be a pinned corpus snapshot")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 32:
        raise SealedArtifactError("canary generation attempt limit is invalid")
    for _ in range(max_attempts):
        marker = secrets.token_urlsafe(32)
        marker_bytes = marker.encode("ascii")
        if any(marker_bytes in item for item in local_corpus):
            continue
        envelope = seal_private_artifact(
            marker_bytes,
            tenant_id=tenant_id,
            artifact_id=uuid4(),
            media_type="application/vnd.polycodebench.sealed-artifact",
            key_provider=key_provider,
            allow_local_development_key=allow_local_development_key,
        )
        marker_ref = _store_private_artifact(
            artifact_writer,
            envelope.ciphertext,
            artifact_id=envelope.artifact_id,
            media_type=envelope.media_type,
        )
        wrapped_key_ref = _store_private_artifact(
            artifact_writer,
            envelope.wrapped_key,
            media_type="application/vnd.polycodebench.wrapped-key",
        )
        check = CanaryCollisionCheck(
            scope_ref=collision_scope_ref,
            checked_items=len(local_corpus),
            method_version="exact-bytes-v1",
            result="no_collision",
        )
        return PrivateCanaryMarker(marker, marker_ref, wrapped_key_ref, envelope, check)
    raise SealedArtifactError("unable to generate a collision-free canary marker")


def build_canary_policy(
    markers: Sequence[PrivateCanaryMarker],
    *,
    detection: AuditDocumentRef,
    access: AuditDocumentRef,
    exposure_rules: tuple[str, ...],
) -> CanaryPolicyPayloadV2:
    """Create the strict v2 policy with private marker refs and explicit interpretation limits."""
    if not markers:
        raise SealedArtifactError("canary policy requires at least one private marker")
    for marker in markers:
        _validate_private_marker(marker)
    return CanaryPolicyPayloadV2(
        marker_refs=tuple(marker.artifact_ref for marker in markers),
        marker_entropy_bits=256,
        detection_method="exact_bytes",
        collision_checks=tuple(marker.collision_check for marker in markers),
        detection=detection,
        access=access,
        exposure_rules=exposure_rules,
        local_first=True,
        interpretation_limits=(
            "absence_is_not_clean",
            "observed_disclosure_is_not_training_proof",
        ),
    )


def observe_canary_locally(
    marker: PrivateCanaryMarker,
    *,
    coverage_ref: AuditDocumentRef,
    candidates: Sequence[CanarySourceCandidate],
    external_manifest: SealedManifestDocumentV2 | None = None,
    external_access_event: SealAccessEventDocument | None = None,
    external_query_payload: bytes | None = None,
) -> CanaryObservationBatch:
    """Record exact marker observations without serializing the raw marker or source body."""
    if coverage_ref.kind != "coverage_manifest":
        raise SealedArtifactError("canary observation requires a frozen coverage manifest")
    _validate_private_marker(marker)
    external_values = (external_manifest, external_access_event, external_query_payload)
    external_query = all(value is not None for value in external_values)
    if any(value is not None for value in external_values) != external_query:
        raise SealedArtifactError(
            "external canary queries require manifest, payload and access event"
        )
    access_event_ref: AuditDocumentRef | None = None
    if external_query:
        assert external_manifest is not None
        assert external_access_event is not None
        assert external_query_payload is not None
        event = external_access_event.payload
        manifest_payload = external_manifest.payload
        if (
            manifest_payload.tenant_id != marker.envelope.tenant_id
            or marker.artifact_ref not in manifest_payload.encrypted_artifact_refs
            or marker.wrapped_key_ref != manifest_payload.wrapped_key_ref
            or event.manifest_ref.document_id != external_manifest.id
            or event.manifest_ref.digest != audit_document_digest(external_manifest)
            or event.tenant_id != manifest_payload.tenant_id
            or event.operation != "remote_query"
            or event.outcome != "authorized"
            or event.exposure != "authorized_disclosure"
            or event.authorization_ref is None
            or event.recipient is None
            or event.payload_digest != _digest(external_query_payload)
            or marker.value.encode("ascii") not in external_query_payload
        ):
            raise SealedArtifactError("external canary query is not bound to its disclosure event")
        access_event_ref = AuditDocumentRef(
            document_id=external_access_event.id,
            digest=audit_document_digest(external_access_event),
            kind="seal_access_event",
        )
    marker_bytes = marker.value.encode("ascii")
    hits = [candidate for candidate in candidates if marker_bytes in candidate.body]
    metadata = _document_metadata("canary-observer")
    if not hits:
        document = CanaryObservationDocument(
            id=uuid4(),
            kind="canary_observation",
            schema_version=1,
            payload=CanaryObservationPayload(
                marker_ref=marker.artifact_ref,
                coverage_ref=coverage_ref,
                observation="not_observed_in_scope",
                exact_match=False,
                source_ref=None,
                source_date_evidence=(),
                prior_publication="unknown",
                source_review_ref=None,
                external_query=external_query,
                access_event_ref=access_event_ref,
                interpretation_limits=(
                    "absence_is_not_clean",
                    "observed_disclosure_is_not_training_proof",
                ),
            ),
            metadata=metadata,
        )
        return CanaryObservationBatch((document,), 0)

    documents: list[CanaryObservationDocument] = []
    for candidate in hits:
        fully_reviewed = bool(candidate.source_dates and candidate.source_review_ref)
        if not fully_reviewed or candidate.prior_publication == "unknown":
            observation: Literal[
                "observed_verified",
                "observed_previously_published",
                "not_observed_in_scope",
                "unverified_observation",
            ] = "unverified_observation"
        elif candidate.prior_publication == "previously_published":
            observation = "observed_previously_published"
        else:
            observation = "observed_verified"
        documents.append(
            CanaryObservationDocument(
                id=uuid4(),
                kind="canary_observation",
                schema_version=1,
                payload=CanaryObservationPayload(
                    marker_ref=marker.artifact_ref,
                    coverage_ref=coverage_ref,
                    observation=observation,
                    exact_match=True,
                    source_ref=candidate.match_evidence_ref,
                    source_date_evidence=candidate.source_dates,
                    prior_publication=candidate.prior_publication,
                    source_review_ref=candidate.source_review_ref,
                    external_query=external_query,
                    access_event_ref=access_event_ref,
                    interpretation_limits=(
                        "absence_is_not_clean",
                        "observed_disclosure_is_not_training_proof",
                    ),
                ),
                metadata=metadata,
            )
        )
    return CanaryObservationBatch(tuple(documents), len(hits))


def open_for_local_screening(
    envelope: SealedCiphertext,
    *,
    tenant_id: UUID,
    manifest: SealedManifestDocumentV2,
    actor: EntityRef,
    recipient: str,
    authorization_ref: AuditDocumentRef,
    purpose: str,
    key_provider: DataKeyProvider,
    authorizer: SealedAccessAuthorizer,
    event_appender: AccessEventAppender,
    screen: Callable[[bytes], bool],
    allow_local_development_key: bool = False,
) -> LocalScreeningResult:
    """Log exact worker exposure before passing plaintext to a boolean-only screening callback."""
    _ensure_active(manifest)
    _validate_envelope_scope(envelope, tenant_id=tenant_id, manifest=manifest)
    request = SealedAccessRequest(
        tenant_id=tenant_id,
        artifact_id=envelope.artifact_id,
        actor=actor,
        operation="local_screening",
        purpose=purpose,
        recipient=recipient,
        authorization_ref=authorization_ref,
    )
    if not _authorization_succeeded(
        lambda: authorizer.may_access(request, manifest.payload.access_policy)
    ):
        _, successor, _ = _append_event(
            event_appender,
            manifest=manifest,
            request=request,
            outcome="denied",
            payload_digest=None,
            exposure="none",
        )
        raise SealedAccessDenied(
            "sealed artifact access is not authorized", successor_manifest=successor
        )
    plaintext = _decrypt_private_artifact(
        envelope,
        tenant_id=tenant_id,
        artifact_id=envelope.artifact_id,
        key_provider=key_provider,
        manifest=manifest,
        allow_local_development_key=allow_local_development_key,
    )
    event_ref, successor, _ = _append_event(
        event_appender,
        manifest=manifest,
        request=request,
        outcome="authorized",
        payload_digest=_digest(plaintext),
        exposure="authorized_disclosure",
    )
    try:
        result = screen(plaintext)
    except Exception:
        raise SealedArtifactError(
            "local screening failed after access was durably recorded"
        ) from None
    if type(result) is not bool:
        raise SealedArtifactError("local screening must return only a boolean result")
    return LocalScreeningResult(result, successor, event_ref)


def deliver_to_recipient(
    envelope: SealedCiphertext,
    *,
    tenant_id: UUID,
    manifest: SealedManifestDocumentV2,
    actor: EntityRef,
    authorization_ref: AuditDocumentRef,
    recipient: str,
    purpose: str,
    key_provider: DataKeyProvider,
    authorizer: SealedAccessAuthorizer,
    event_appender: AccessEventAppender,
    deliver: Callable[[bytes], object],
    public: bool = False,
    allow_local_development_key: bool = False,
) -> DisclosureResult[object]:
    """Authorize exact recipient+payload, append exposure, then and only then disclose bytes."""
    _ensure_active(manifest)
    _validate_envelope_scope(envelope, tenant_id=tenant_id, manifest=manifest)
    operation: Literal["candidate_delivery", "remote_delivery", "public_publication"] = (
        "public_publication" if public else "candidate_delivery"
    )
    request = SealedAccessRequest(
        tenant_id=tenant_id,
        artifact_id=envelope.artifact_id,
        actor=actor,
        operation=operation,
        purpose=purpose,
        recipient=recipient,
        authorization_ref=authorization_ref,
    )
    if not _authorization_succeeded(
        lambda: authorizer.may_access(request, manifest.payload.access_policy)
    ):
        _, successor, _ = _append_event(
            event_appender,
            manifest=manifest,
            request=request,
            outcome="denied",
            payload_digest=None,
            exposure="none",
        )
        raise SealedAccessDenied(
            "sealed artifact access is not authorized", successor_manifest=successor
        )
    plaintext = _decrypt_private_artifact(
        envelope,
        tenant_id=tenant_id,
        artifact_id=envelope.artifact_id,
        key_provider=key_provider,
        manifest=manifest,
        allow_local_development_key=allow_local_development_key,
    )
    payload_digest = _digest(plaintext)
    if not _authorization_succeeded(
        lambda: authorizer.may_disclose(request, manifest.payload.access_policy, payload_digest)
    ):
        _, successor, _ = _append_event(
            event_appender,
            manifest=manifest,
            request=request,
            outcome="denied",
            payload_digest=payload_digest,
            exposure="none",
        )
        raise SealedAccessDenied(
            "sealed artifact disclosure is not authorized", successor_manifest=successor
        )
    event_ref, successor, event = _append_event(
        event_appender,
        manifest=manifest,
        request=request,
        outcome="authorized",
        payload_digest=payload_digest,
        exposure="public_exposed" if public else "authorized_disclosure",
    )
    try:
        result = deliver(plaintext)
    except Exception:
        raise SealedArtifactError(
            "recipient delivery failed after disclosure was durably recorded"
        ) from None
    return DisclosureResult(result, successor, event_ref, event)


def dispatch_remote_query(
    payload: bytes,
    *,
    tenant_id: UUID,
    artifact_id: UUID,
    manifest: SealedManifestDocumentV2,
    actor: EntityRef,
    authorization_ref: AuditDocumentRef,
    recipient: str,
    purpose: str,
    authorizer: SealedAccessAuthorizer,
    event_appender: AccessEventAppender,
    dispatch: Callable[[bytes], object],
    operation: Literal["remote_query", "remote_delivery"] = "remote_query",
) -> DisclosureResult[object]:
    """Record a canary/private query disclosure before any external connector sees its bytes."""
    _ensure_active(manifest)
    if not isinstance(payload, bytes):
        raise SealedArtifactError("sealed query payload must be bytes")
    request = SealedAccessRequest(
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        actor=actor,
        operation=operation,
        purpose=purpose,
        recipient=recipient,
        authorization_ref=authorization_ref,
    )
    policy = manifest.payload.access_policy
    if tenant_id != manifest.payload.tenant_id or not any(
        ref.artifact_id == artifact_id for ref in manifest.payload.encrypted_artifact_refs
    ):
        raise SealedArtifactError("sealed query scope does not match manifest")
    if not _authorization_succeeded(lambda: authorizer.may_access(request, policy)):
        _, successor, _ = _append_event(
            event_appender,
            manifest=manifest,
            request=request,
            outcome="denied",
            payload_digest=None,
            exposure="none",
        )
        raise SealedAccessDenied(
            "sealed query disclosure is not authorized", successor_manifest=successor
        )
    payload_digest = _digest(payload)
    if not _authorization_succeeded(
        lambda: authorizer.may_disclose(request, policy, payload_digest)
    ):
        _, successor, _ = _append_event(
            event_appender,
            manifest=manifest,
            request=request,
            outcome="denied",
            payload_digest=payload_digest,
            exposure="none",
        )
        raise SealedAccessDenied(
            "sealed query disclosure is not authorized", successor_manifest=successor
        )
    event_ref, successor, event = _append_event(
        event_appender,
        manifest=manifest,
        request=request,
        outcome="authorized",
        payload_digest=payload_digest,
        exposure="authorized_disclosure",
    )
    try:
        result = dispatch(payload)
    except Exception:
        raise SealedArtifactError(
            "remote query failed after disclosure was durably recorded"
        ) from None
    return DisclosureResult(result, successor, event_ref, event)


def _append_event(
    appender: AccessEventAppender,
    *,
    manifest: SealedManifestDocumentV2,
    request: SealedAccessRequest,
    outcome: Literal["authorized", "denied", "completed", "failed"],
    payload_digest: Digest | None,
    exposure: Literal["none", "authorized_disclosure", "public_exposed", "compromised"],
    rotated_envelope: SealedCiphertext | None = None,
    rotated_wrapped_key_ref: ImmutableArtifactRef | None = None,
) -> tuple[AuditDocumentRef, SealedManifestDocumentV2, SealAccessEventDocument]:
    _ensure_active(manifest)
    if request.tenant_id != manifest.payload.tenant_id:
        raise SealedArtifactError("sealed access tenant does not match manifest")
    if request.purpose == "" or len(request.purpose) > 512:
        raise SealedArtifactError("sealed access purpose is invalid")
    if request.operation not in {"local_screening", "key_rotation"} and request.recipient is None:
        raise SealedArtifactError("disclosure event requires an exact recipient")
    if (rotated_envelope is None) != (rotated_wrapped_key_ref is None):
        raise SealedArtifactError(
            "key rotation successor requires both wrapped key and storage ref"
        )
    if rotated_envelope is not None and request.operation != "key_rotation":
        raise SealedArtifactError("wrapped-key replacement is only valid for key rotation")
    manifest_ref = AuditDocumentRef(
        document_id=manifest.id,
        digest=audit_document_digest(manifest),
        kind="sealed_manifest",
    )
    event = SealAccessEventDocument(
        id=uuid4(),
        kind="seal_access_event",
        schema_version=1,
        payload=SealAccessEventPayload(
            manifest_ref=manifest_ref,
            tenant_id=request.tenant_id,
            actor=request.actor,
            operation=request.operation,
            purpose=request.purpose,
            recipient=request.recipient,
            payload_digest=payload_digest,
            authorization_ref=request.authorization_ref,
            event_time=_timestamp_now(),
            outcome=outcome,
            exposure=exposure,
        ),
        metadata=_document_metadata(str(request.actor.entity_id)),
    )
    event_ref = AuditDocumentRef(
        document_id=event.id,
        digest=audit_document_digest(event),
        kind="seal_access_event",
    )
    state: SealState = manifest.payload.disclosure_state
    event_state: SealState = "sealed" if exposure == "none" else exposure
    if _EXPOSURE_RANK[event_state] > _EXPOSURE_RANK[state]:
        state = event_state
    if state == "retired":
        raise SealedArtifactError("retired sealed manifests cannot be accessed")
    successor_values = {
        **manifest.payload.model_dump(mode="python"),
        "access_event_refs": (*manifest.payload.access_event_refs, event_ref),
        "disclosure_state": state,
    }
    if rotated_envelope is not None and rotated_wrapped_key_ref is not None:
        successor_values.update(
            {
                "wrapped_key_ref": rotated_wrapped_key_ref,
                "key_provider": rotated_envelope.key_provider,
                "key_version": rotated_envelope.key_version,
                "key_wrapping_algorithm": rotated_envelope.key_wrapping_algorithm,
                "recovery_ref": rotated_envelope.recovery_ref,
            }
        )
    successor_payload = SealedManifestPayloadV2.model_validate(successor_values, strict=True)
    successor = SealedManifestDocumentV2(
        id=uuid4(),
        kind="sealed_manifest",
        schema_version=2,
        payload=successor_payload,
        metadata=_document_metadata(str(request.actor.entity_id)),
        supersedes_id=manifest.id,
    )
    validate_sealed_manifest_successor(manifest, successor, event)
    try:
        appender.append_access_event(event, successor)
    except Exception:
        raise SealedArtifactError("sealed access history could not be committed") from None
    return event_ref, successor, event


def _validate_provider(provider: DataKeyProvider, *, allow_local_development_key: bool) -> None:
    for value in (
        provider.provider_name,
        provider.key_version,
        provider.wrapping_algorithm,
    ):
        if not isinstance(value, str) or not _SAFE_KEY_REFERENCE.fullmatch(value):
            raise SealedArtifactError("key provider metadata is invalid")
    if provider.recovery_ref is not None and not _SAFE_KEY_REFERENCE.fullmatch(
        provider.recovery_ref
    ):
        raise SealedArtifactError("key recovery reference is invalid")
    if provider.assurance == "local_development" and not allow_local_development_key:
        raise SealedArtifactError("local development key provider was not explicitly enabled")
    if provider.assurance not in {"approved_kms", "local_development"}:
        raise SealedArtifactError("key provider approval state is unavailable")


def _ensure_active(manifest: SealedManifestDocumentV2) -> None:
    if manifest.payload.disclosure_state == "retired":
        raise SealedArtifactError("retired sealed manifests cannot be accessed or superseded")


def _validate_envelope_scope(
    envelope: SealedCiphertext,
    *,
    tenant_id: UUID,
    manifest: SealedManifestDocumentV2,
) -> None:
    """Reject tenant/artifact substitution before consulting a policy adapter."""
    payload = manifest.payload
    ciphertext_ref = next(
        (ref for ref in payload.encrypted_artifact_refs if ref.artifact_id == envelope.artifact_id),
        None,
    )
    if (
        tenant_id != payload.tenant_id
        or envelope.tenant_id != tenant_id
        or ciphertext_ref is None
        or ciphertext_ref.media_type != envelope.media_type
        or ciphertext_ref.digest != _digest(envelope.ciphertext)
        or payload.wrapped_key_ref.digest != _digest(envelope.wrapped_key)
        or payload.key_provider != envelope.key_provider
        or payload.key_version != envelope.key_version
        or payload.key_wrapping_algorithm != envelope.key_wrapping_algorithm
        or payload.recovery_ref != envelope.recovery_ref
    ):
        raise SealedArtifactError("sealed artifact scope or manifest binding is invalid")


def _validate_private_marker(marker: PrivateCanaryMarker) -> None:
    value = marker.value
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{43}", value, re.ASCII)
        or marker.artifact_ref.visibility != "private"
        or marker.artifact_ref.media_type != "application/vnd.polycodebench.sealed-artifact"
        or marker.envelope.media_type != marker.artifact_ref.media_type
        or marker.artifact_ref.artifact_id != marker.envelope.artifact_id
        or marker.artifact_ref.digest != _digest(marker.envelope.ciphertext)
        or marker.wrapped_key_ref.visibility != "private"
        or marker.wrapped_key_ref.media_type != "application/vnd.polycodebench.wrapped-key"
        or marker.wrapped_key_ref.digest != _digest(marker.envelope.wrapped_key)
        or marker.collision_check.result != "no_collision"
        or marker.collision_check.checked_items < 1
    ):
        raise SealedArtifactError("private canary marker contract is invalid")
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except Exception:
        raise SealedArtifactError("private canary marker entropy is invalid") from None
    if len(decoded) != 32:
        raise SealedArtifactError("private canary marker must contain 256 random bits")


def _store_private_artifact(
    writer: PrivateArtifactWriter,
    body: bytes,
    *,
    media_type: str,
    artifact_id: UUID | None = None,
) -> ImmutableArtifactRef:
    """Store bytes privately and accept only a receipt bound to the submitted bytes."""
    expected_id = artifact_id or uuid4()
    try:
        reference = writer.put_private(body, artifact_id=expected_id, media_type=media_type)
    except Exception:
        raise SealedArtifactError("private artifact storage failed") from None
    if (
        reference.artifact_id != expected_id
        or reference.digest != _digest(body)
        or reference.visibility != "private"
        or reference.media_type != media_type
    ):
        raise SealedArtifactError("private artifact storage returned an unverified reference")
    return reference


def _authorization_succeeded(check: Callable[[], bool]) -> bool:
    try:
        result = check()
    except Exception:
        return False
    return result is True


def _associated_data(tenant_id: UUID, artifact_id: UUID, media_type: str) -> bytes:
    return _DOMAIN + canonical_json_bytes(
        {
            "artifact_id": str(artifact_id),
            "media_type": media_type,
            "tenant_id": str(tenant_id),
            "version": 1,
        }
    )


def _digest(value: bytes) -> Digest:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _timestamp_now() -> TimestampEvidence:
    created_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return TimestampEvidence(
        value=created_at,
        precision="microsecond",
        uncertainty=None,
        source_ref=None,
    )


def _document_metadata(actor: str) -> DocumentMetadata:
    created_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return DocumentMetadata(
        created_at=created_at,
        timestamp_precision="microsecond",
        actor=actor,
        trace_id=None,
        row_version=0,
    )
