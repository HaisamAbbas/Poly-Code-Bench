"""Security and state-transition tests for Prompt93 sealed evaluation controls."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Literal
from uuid import UUID, uuid4

import pytest
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    AuditKind,
    CanaryCollisionCheck,
    EntityRef,
    HidingCommitmentPayload,
    ImmutableArtifactRef,
    SealAccessEventDocument,
    SealedManifestDocumentV2,
    SealedManifestPayloadV2,
    TimestampEvidence,
    audit_document_bytes,
)
from polycodebench_services.sealed_evaluations import (
    CanarySourceCandidate,
    LocalDevelopmentAESKWPKeyProvider,
    PrivateCanaryMarker,
    SealedAccessDenied,
    SealedAccessRequest,
    SealedArtifactError,
    SealedCiphertext,
    _decrypt_private_artifact,
    build_canary_policy,
    build_sealed_manifest,
    create_canary_marker,
    deliver_to_recipient,
    dispatch_remote_query,
    observe_canary_locally,
    open_for_local_screening,
    rewrap_data_key,
    rotate_sealed_artifact_key,
    seal_private_artifact,
    validate_sealed_manifest_successor,
)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _doc_ref(kind: AuditKind) -> AuditDocumentRef:
    return AuditDocumentRef(
        document_id=uuid4(),
        digest="sha256:" + "a" * 64,
        kind=kind,
    )


def _artifact_ref(
    body: bytes,
    *,
    artifact_id: UUID | None = None,
    visibility: Literal["private", "restricted", "public"] = "private",
) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_id=artifact_id or uuid4(),
        digest=_digest(body),
        visibility=visibility,
        media_type="application/octet-stream",
    )


def _private_marker(value: str) -> PrivateCanaryMarker:
    tenant_id = UUID("11111111-1111-4111-8111-111111111111")
    provider = LocalDevelopmentAESKWPKeyProvider(b"m" * 32, key_version="marker-test-v1")
    envelope = seal_private_artifact(
        value.encode("ascii"),
        tenant_id=tenant_id,
        artifact_id=uuid4(),
        media_type="application/vnd.polycodebench.sealed-artifact",
        key_provider=provider,
        allow_local_development_key=True,
    )
    marker_ref = ImmutableArtifactRef(
        artifact_id=envelope.artifact_id,
        digest=_digest(envelope.ciphertext),
        visibility="private",
        media_type="application/vnd.polycodebench.sealed-artifact",
    )
    wrapped_key_ref = ImmutableArtifactRef(
        artifact_id=uuid4(),
        digest=_digest(envelope.wrapped_key),
        visibility="private",
        media_type="application/vnd.polycodebench.wrapped-key",
    )
    collision_check = CanaryCollisionCheck(
        scope_ref=_doc_ref("corpus_snapshot"),
        checked_items=1,
        method_version="exact-bytes-v1",
        result="no_collision",
    )
    return PrivateCanaryMarker(value, marker_ref, wrapped_key_ref, envelope, collision_check)


class _PrivateWriter:
    def __init__(self) -> None:
        self.objects: dict[UUID, bytes] = {}

    def put_private(
        self, body: bytes, *, artifact_id: UUID, media_type: str
    ) -> ImmutableArtifactRef:
        self.objects[artifact_id] = body
        return ImmutableArtifactRef(
            artifact_id=artifact_id,
            digest=_digest(body),
            visibility="private",
            media_type=media_type,
        )


class _Appender:
    def __init__(self, initial: SealedManifestDocumentV2, *, fail: bool = False) -> None:
        self.events: list[SealAccessEventDocument] = []
        self.manifests: list[SealedManifestDocumentV2] = []
        self.current = initial
        self.fail = fail

    def append_access_event(
        self,
        event: SealAccessEventDocument,
        successor: SealedManifestDocumentV2,
    ) -> None:
        if self.fail:
            raise RuntimeError("simulated durable audit write failure")
        validate_sealed_manifest_successor(self.current, successor, event)
        self.events.append(event)
        self.manifests.append(successor)
        self.current = successor


class _Authorizer:
    def __init__(self, *, access: bool = True, disclose: bool = True, rotate: bool = True) -> None:
        self.access = access
        self.disclose = disclose
        self.rotate = rotate
        self.access_requests: list[SealedAccessRequest] = []
        self.disclosure_requests: list[tuple[SealedAccessRequest, str]] = []

    def may_access(self, request: SealedAccessRequest, policy: AuditDocumentRef) -> bool:
        self.access_requests.append(request)
        return self.access

    def may_disclose(
        self, request: SealedAccessRequest, policy: AuditDocumentRef, payload_digest: str
    ) -> bool:
        self.disclosure_requests.append((request, payload_digest))
        return self.disclose

    def may_rotate_keys(self, request: SealedAccessRequest, policy: AuditDocumentRef) -> bool:
        return self.rotate


def _sealed_fixture() -> tuple[
    SealedManifestDocumentV2,
    SealedCiphertext,
    LocalDevelopmentAESKWPKeyProvider,
    _PrivateWriter,
    EntityRef,
]:
    tenant_id = UUID("11111111-1111-4111-8111-111111111111")
    artifact_id = uuid4()
    provider = LocalDevelopmentAESKWPKeyProvider(b"a" * 32, key_version="local-v1")
    plaintext = b"private benchmark component"
    envelope = seal_private_artifact(
        plaintext,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        media_type="application/octet-stream",
        key_provider=provider,
        allow_local_development_key=True,
    )
    writer = _PrivateWriter()
    ciphertext_ref = _artifact_ref(envelope.ciphertext, artifact_id=artifact_id)
    wrapped_key_ref = writer.put_private(
        envelope.wrapped_key,
        artifact_id=uuid4(),
        media_type="application/vnd.polycodebench.wrapped-key",
    )
    nonce_ref = writer.put_private(
        b"n" * 32, artifact_id=uuid4(), media_type="application/octet-stream"
    )
    hiding_commitment = HidingCommitmentPayload(
        scheme="sha256-salted-v1",
        commitment="sha256:" + "b" * 64,
        nonce_ref=nonce_ref,
        local_receipt=None,
        trusted_proof=None,
    )
    manifest = build_sealed_manifest(
        envelope,
        ciphertext_ref=ciphertext_ref,
        wrapped_key_ref=wrapped_key_ref,
        hiding_commitment=hiding_commitment,
        access_policy=_doc_ref("monitor_policy"),
        retention_policy=_doc_ref("monitor_policy"),
        actor="test-sealer",
    )
    actor = EntityRef(
        entity_id=UUID("22222222-2222-4222-8222-222222222222"),
        entity_kind="principal",
        digest=None,
    )
    return manifest, envelope, provider, writer, actor


def test_aes_gcm_binds_tenant_artifact_and_manifest_storage_digests() -> None:
    manifest, envelope, provider, _, _ = _sealed_fixture()
    assert (
        _decrypt_private_artifact(
            envelope,
            tenant_id=manifest.payload.tenant_id,
            artifact_id=envelope.artifact_id,
            key_provider=provider,
            manifest=manifest,
            allow_local_development_key=True,
        )
        == b"private benchmark component"
    )
    with pytest.raises(SealedArtifactError, match="scope or manifest"):
        _decrypt_private_artifact(
            envelope,
            tenant_id=UUID("33333333-3333-4333-8333-333333333333"),
            artifact_id=envelope.artifact_id,
            key_provider=provider,
            manifest=manifest,
            allow_local_development_key=True,
        )
    tampered = replace(
        envelope,
        ciphertext=envelope.ciphertext[:-1] + bytes([envelope.ciphertext[-1] ^ 1]),
    )
    with pytest.raises(SealedArtifactError, match="scope or manifest"):
        _decrypt_private_artifact(
            tampered,
            tenant_id=manifest.payload.tenant_id,
            artifact_id=envelope.artifact_id,
            key_provider=provider,
            manifest=manifest,
            allow_local_development_key=True,
        )
    wrong_media_ref = manifest.payload.encrypted_artifact_refs[0].model_copy(
        update={"media_type": "application/vnd.polycodebench.unrelated"}
    )
    with pytest.raises(SealedArtifactError, match="media type"):
        build_sealed_manifest(
            envelope,
            ciphertext_ref=wrong_media_ref,
            wrapped_key_ref=manifest.payload.wrapped_key_ref,
            hiding_commitment=manifest.payload.hiding_commitment,
            access_policy=manifest.payload.access_policy,
            retention_policy=manifest.payload.retention_policy,
            actor="test-sealer",
        )
    changed_ref = manifest.payload.encrypted_artifact_refs[0].model_copy(
        update={"digest": _digest(tampered.ciphertext)}
    )
    changed_manifest = build_sealed_manifest(
        tampered,
        ciphertext_ref=changed_ref,
        wrapped_key_ref=manifest.payload.wrapped_key_ref,
        hiding_commitment=manifest.payload.hiding_commitment,
        access_policy=manifest.payload.access_policy,
        retention_policy=manifest.payload.retention_policy,
        actor="test-sealer",
    )
    with pytest.raises(SealedArtifactError, match="authentication failed"):
        _decrypt_private_artifact(
            tampered,
            tenant_id=manifest.payload.tenant_id,
            artifact_id=envelope.artifact_id,
            key_provider=provider,
            manifest=changed_manifest,
            allow_local_development_key=True,
        )


def test_wrong_tenant_is_rejected_before_authorization_and_labels_are_not_free_form() -> None:
    manifest, envelope, provider, _, actor = _sealed_fixture()
    authorizer = _Authorizer()
    appender = _Appender(manifest)
    with pytest.raises(SealedArtifactError, match="scope or manifest"):
        open_for_local_screening(
            envelope,
            tenant_id=UUID("33333333-3333-4333-8333-333333333333"),
            manifest=manifest,
            actor=actor,
            recipient="worker:screen-1",
            authorization_ref=_doc_ref("audit_plan"),
            purpose="screening",
            key_provider=provider,
            authorizer=authorizer,
            event_appender=appender,
            screen=lambda _: True,
            allow_local_development_key=True,
        )
    assert authorizer.access_requests == []
    assert appender.events == []
    with pytest.raises(SealedArtifactError, match="purpose must be a bounded identifier"):
        SealedAccessRequest(
            tenant_id=manifest.payload.tenant_id,
            artifact_id=envelope.artifact_id,
            actor=actor,
            operation="local_screening",
            purpose="untrusted free-form content",
            recipient="worker:screen-1",
            authorization_ref=_doc_ref("audit_plan"),
        )
    other_tenant = UUID("33333333-3333-4333-8333-333333333333")
    other_tenant_envelope = replace(envelope, tenant_id=other_tenant)
    other_tenant_manifest = build_sealed_manifest(
        other_tenant_envelope,
        ciphertext_ref=manifest.payload.encrypted_artifact_refs[0],
        wrapped_key_ref=manifest.payload.wrapped_key_ref,
        hiding_commitment=manifest.payload.hiding_commitment,
        access_policy=manifest.payload.access_policy,
        retention_policy=manifest.payload.retention_policy,
        actor="test-sealer",
    )
    with pytest.raises(SealedArtifactError, match="authentication failed"):
        _decrypt_private_artifact(
            other_tenant_envelope,
            tenant_id=other_tenant,
            artifact_id=envelope.artifact_id,
            key_provider=provider,
            manifest=other_tenant_manifest,
            allow_local_development_key=True,
        )


def test_local_screening_authorizes_and_logs_before_returning_payload() -> None:
    manifest, envelope, provider, _, actor = _sealed_fixture()
    appender = _Appender(manifest)
    authorization_ref = _doc_ref("audit_plan")
    result = open_for_local_screening(
        envelope,
        tenant_id=manifest.payload.tenant_id,
        manifest=manifest,
        actor=actor,
        recipient="worker:screen-1",
        authorization_ref=authorization_ref,
        purpose="local-fingerprint-screening",
        key_provider=provider,
        authorizer=_Authorizer(),
        event_appender=appender,
        screen=lambda payload: payload == b"private benchmark component",
        allow_local_development_key=True,
    )
    assert result.result is True
    event = appender.events[0].payload
    assert event.operation == "local_screening"
    assert event.outcome == "authorized"
    assert event.authorization_ref == authorization_ref
    assert event.recipient == "worker:screen-1"
    assert event.payload_digest == _digest(b"private benchmark component")
    assert event.exposure == "authorized_disclosure"
    assert result.manifest.payload.disclosure_state == "authorized_disclosure"
    assert result.manifest.payload.access_event_refs == (result.access_event_ref,)


def test_denied_decrypt_never_returns_payload_and_durable_write_failure_blocks_access() -> None:
    manifest, envelope, provider, _, actor = _sealed_fixture()
    appender = _Appender(manifest)
    with pytest.raises(SealedAccessDenied) as denied:
        open_for_local_screening(
            envelope,
            tenant_id=manifest.payload.tenant_id,
            manifest=manifest,
            actor=actor,
            recipient="worker:screen-1",
            authorization_ref=_doc_ref("audit_plan"),
            purpose="screening",
            key_provider=provider,
            authorizer=_Authorizer(access=False),
            event_appender=appender,
            screen=lambda _: True,
            allow_local_development_key=True,
        )
    assert denied.value.successor_manifest.payload.disclosure_state == "sealed"
    assert appender.events[0].payload.outcome == "denied"
    assert appender.events[0].payload.payload_digest is None
    with pytest.raises(SealedArtifactError, match="access history could not be committed"):
        open_for_local_screening(
            envelope,
            tenant_id=manifest.payload.tenant_id,
            manifest=manifest,
            actor=actor,
            recipient="worker:screen-1",
            authorization_ref=_doc_ref("audit_plan"),
            purpose="screening",
            key_provider=provider,
            authorizer=_Authorizer(),
            event_appender=_Appender(manifest, fail=True),
            screen=lambda _: True,
            allow_local_development_key=True,
        )


def test_local_screening_result_cannot_carry_private_payload() -> None:
    manifest, envelope, provider, _, actor = _sealed_fixture()
    appender = _Appender(manifest)
    with pytest.raises(SealedArtifactError, match="only a boolean result"):
        open_for_local_screening(
            envelope,
            tenant_id=manifest.payload.tenant_id,
            manifest=manifest,
            actor=actor,
            recipient="worker:screen-1",
            authorization_ref=_doc_ref("audit_plan"),
            purpose="local-screening",
            key_provider=provider,
            authorizer=_Authorizer(),
            event_appender=appender,
            screen=lambda payload: (True, payload),  # type: ignore[return-value]
            allow_local_development_key=True,
        )
    assert appender.events[0].payload.exposure == "authorized_disclosure"
    assert appender.current.payload.disclosure_state == "authorized_disclosure"


def test_recipient_delivery_logs_exact_disclosure_before_callback_and_cannot_reseal_clean() -> None:
    manifest, envelope, provider, _, actor = _sealed_fixture()
    appender = _Appender(manifest)
    delivery_order: list[str] = []

    def deliver(payload: bytes) -> str:
        delivery_order.append("delivered")
        assert appender.events[-1].payload.exposure == "authorized_disclosure"
        assert appender.events[-1].payload.payload_digest == _digest(payload)
        return "accepted"

    result = deliver_to_recipient(
        envelope,
        tenant_id=manifest.payload.tenant_id,
        manifest=manifest,
        actor=actor,
        authorization_ref=_doc_ref("audit_plan"),
        recipient="candidate:model-7",
        purpose="candidate-evaluation",
        key_provider=provider,
        authorizer=_Authorizer(),
        event_appender=appender,
        deliver=deliver,
        allow_local_development_key=True,
    )
    assert result.result == "accepted"
    assert delivery_order == ["delivered"]
    assert result.manifest.payload.disclosure_state == "authorized_disclosure"
    assert result.manifest.payload.hiding_commitment == manifest.payload.hiding_commitment
    reset_payload = SealedManifestPayloadV2.model_validate(
        {
            **manifest.payload.model_dump(mode="python"),
            "access_event_refs": (),
            "disclosure_state": "sealed",
        },
        strict=True,
    )
    reset = SealedManifestDocumentV2(
        id=uuid4(),
        kind="sealed_manifest",
        schema_version=2,
        payload=reset_payload,
        metadata=manifest.metadata,
        supersedes_id=manifest.id,
    )
    with pytest.raises(SealedArtifactError, match="append exactly its access event"):
        validate_sealed_manifest_successor(manifest, reset, appender.events[0])


def test_disclosure_denial_and_event_write_failure_prevent_remote_delivery() -> None:
    manifest, _, _, _, actor = _sealed_fixture()
    payload = b"private query"
    called = False

    def dispatch(_: bytes) -> None:
        nonlocal called
        called = True

    denied_appender = _Appender(manifest)
    with pytest.raises(SealedAccessDenied):
        dispatch_remote_query(
            payload,
            tenant_id=manifest.payload.tenant_id,
            artifact_id=manifest.payload.encrypted_artifact_refs[0].artifact_id,
            manifest=manifest,
            actor=actor,
            authorization_ref=_doc_ref("audit_plan"),
            recipient="authorized-source:fixture",
            purpose="exact-canary-query",
            authorizer=_Authorizer(disclose=False),
            event_appender=denied_appender,
            dispatch=dispatch,
        )
    assert not called
    assert denied_appender.events[0].payload.exposure == "none"
    assert denied_appender.events[0].payload.payload_digest == _digest(payload)
    with pytest.raises(SealedArtifactError, match="access history could not be committed"):
        dispatch_remote_query(
            payload,
            tenant_id=manifest.payload.tenant_id,
            artifact_id=manifest.payload.encrypted_artifact_refs[0].artifact_id,
            manifest=manifest,
            actor=actor,
            authorization_ref=_doc_ref("audit_plan"),
            recipient="authorized-source:fixture",
            purpose="exact-canary-query",
            authorizer=_Authorizer(),
            event_appender=_Appender(manifest, fail=True),
            dispatch=dispatch,
        )
    assert not called


def test_key_rotation_rewraps_without_changing_ciphertext_commitment_or_exposure() -> None:
    manifest, envelope, old_provider, writer, actor = _sealed_fixture()
    new_provider = LocalDevelopmentAESKWPKeyProvider(b"z" * 32, key_version="local-v2")
    result = rotate_sealed_artifact_key(
        envelope,
        manifest=manifest,
        actor=actor,
        authorization_ref=_doc_ref("audit_plan"),
        purpose="scheduled-key-rotation",
        old_provider=old_provider,
        new_provider=new_provider,
        authorizer=_Authorizer(),
        event_appender=_Appender(manifest),
        artifact_writer=writer,
        allow_local_development_key=True,
    )
    assert result.envelope.ciphertext == envelope.ciphertext
    assert result.envelope.wrapped_key != envelope.wrapped_key
    assert result.manifest.payload.key_version == "local-v2"
    assert result.manifest.payload.hiding_commitment == manifest.payload.hiding_commitment
    assert result.manifest.payload.disclosure_state == "sealed"
    assert (
        _decrypt_private_artifact(
            result.envelope,
            tenant_id=manifest.payload.tenant_id,
            artifact_id=envelope.artifact_id,
            key_provider=new_provider,
            manifest=result.manifest,
            allow_local_development_key=True,
        )
        == b"private benchmark component"
    )


def test_unapproved_or_tampered_wrapping_and_cross_artifact_nonce_context_fail_closed() -> None:
    tenant = UUID("11111111-1111-4111-8111-111111111111")
    provider = LocalDevelopmentAESKWPKeyProvider(b"a" * 32, key_version="local-v1")
    with pytest.raises(SealedArtifactError, match="not explicitly enabled"):
        seal_private_artifact(
            b"secret",
            tenant_id=tenant,
            artifact_id=uuid4(),
            media_type="text/plain",
            key_provider=provider,
        )
    manifest, envelope, _, _, _ = _sealed_fixture()
    corrupted_wrap = replace(envelope, wrapped_key=envelope.wrapped_key[:-1] + b"x")
    with pytest.raises(SealedArtifactError):
        _decrypt_private_artifact(
            corrupted_wrap,
            tenant_id=manifest.payload.tenant_id,
            artifact_id=envelope.artifact_id,
            key_provider=LocalDevelopmentAESKWPKeyProvider(b"a" * 32, key_version="local-v1"),
            manifest=manifest,
            allow_local_development_key=True,
        )


def test_canary_collision_check_private_storage_observations_and_no_hit_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = (b"existing source content", b"another local item")
    candidates = iter(("marker-collides", "Z" * 43))
    monkeypatch.setattr(
        "polycodebench_services.sealed_evaluations.secrets.token_urlsafe",
        lambda _: next(candidates),
    )
    writer = _PrivateWriter()
    collision_scope = _doc_ref("corpus_snapshot")
    marker = create_canary_marker(
        tenant_id=UUID("11111111-1111-4111-8111-111111111111"),
        local_corpus=(b"marker-collides", *corpus),
        collision_scope_ref=collision_scope,
        artifact_writer=writer,
        key_provider=LocalDevelopmentAESKWPKeyProvider(b"m" * 32, key_version="marker-v1"),
        allow_local_development_key=True,
    )
    assert marker.value == "Z" * 43
    assert marker.artifact_ref.visibility == "private"
    assert marker.collision_check.checked_items == 3
    assert marker.value.encode() not in repr(marker).encode()
    policy = build_canary_policy(
        (marker,),
        detection=_doc_ref("monitor_policy"),
        access=_doc_ref("monitor_policy"),
        exposure_rules=("authorize_every_external_query",),
    )
    assert policy.marker_entropy_bits == 256
    assert "absence_is_not_clean" in policy.interpretation_limits

    coverage = _doc_ref("coverage_manifest")
    no_hit = observe_canary_locally(marker, coverage_ref=coverage, candidates=())
    assert no_hit.documents[0].payload.observation == "not_observed_in_scope"
    assert "absence_is_not_clean" in no_hit.documents[0].payload.interpretation_limits
    assert marker.value.encode() not in audit_document_bytes(no_hit.documents[0])


def test_canary_marker_contains_256_random_bits_and_policy_hides_raw_value() -> None:
    import base64

    writer = _PrivateWriter()
    marker = create_canary_marker(
        tenant_id=UUID("11111111-1111-4111-8111-111111111111"),
        local_corpus=(b"private task corpus item",),
        collision_scope_ref=_doc_ref("corpus_snapshot"),
        artifact_writer=writer,
        key_provider=LocalDevelopmentAESKWPKeyProvider(b"m" * 32, key_version="marker-v1"),
        allow_local_development_key=True,
    )
    assert len(base64.urlsafe_b64decode(marker.value + "=")) == 32
    assert writer.objects[marker.artifact_ref.artifact_id] == marker.envelope.ciphertext
    assert writer.objects[marker.wrapped_key_ref.artifact_id] == marker.envelope.wrapped_key
    policy = build_canary_policy(
        (marker,),
        detection=_doc_ref("monitor_policy"),
        access=_doc_ref("monitor_policy"),
        exposure_rules=("authorize_every_external_query",),
    )
    assert marker.value.encode("ascii") not in str(policy.model_dump(mode="json")).encode()


def test_verified_canary_hit_requires_source_date_and_prior_publication_review() -> None:
    marker_value = "P" * 43
    marker = _private_marker(marker_value)
    review_ref = _doc_ref("match_evidence")
    candidate = CanarySourceCandidate(
        body=f"source contains {marker_value} exactly".encode(),
        match_evidence_ref=_doc_ref("match_evidence"),
        source_dates=(
            TimestampEvidence(
                value="2026-08-01",
                precision="day",
                uncertainty=None,
                source_ref=None,
            ),
        ),
        prior_publication="not_previously_published",
        source_review_ref=review_ref,
    )
    observed = observe_canary_locally(
        marker,
        coverage_ref=_doc_ref("coverage_manifest"),
        candidates=(candidate,),
    )
    assert observed.matched_candidate_count == 1
    assert observed.documents[0].payload.observation == "observed_verified"
    assert (
        "observed_disclosure_is_not_training_proof"
        in observed.documents[0].payload.interpretation_limits
    )
    serialized = audit_document_bytes(observed.documents[0])
    assert marker_value.encode() not in serialized
    assert b"source contains" not in serialized

    missing_review = replace(candidate, source_review_ref=None)
    unresolved = observe_canary_locally(
        marker,
        coverage_ref=_doc_ref("coverage_manifest"),
        candidates=(missing_review,),
    )
    assert unresolved.documents[0].payload.observation == "unverified_observation"


def test_external_canary_query_requires_and_persists_disclosure_before_dispatch() -> None:
    task_manifest, _, _, _, actor = _sealed_fixture()
    marker = _private_marker("Q" * 43)
    manifest = build_sealed_manifest(
        marker.envelope,
        ciphertext_ref=marker.artifact_ref,
        wrapped_key_ref=marker.wrapped_key_ref,
        hiding_commitment=task_manifest.payload.hiding_commitment,
        access_policy=task_manifest.payload.access_policy,
        retention_policy=task_manifest.payload.retention_policy,
        actor="test-sealer",
    )
    payload = b"exact query: " + marker.value.encode("ascii")
    appender = _Appender(manifest)
    result = dispatch_remote_query(
        payload,
        tenant_id=manifest.payload.tenant_id,
        artifact_id=manifest.payload.encrypted_artifact_refs[0].artifact_id,
        manifest=manifest,
        actor=actor,
        authorization_ref=_doc_ref("audit_plan"),
        recipient="source:approved-fixture",
        purpose="external-canary-query",
        authorizer=_Authorizer(),
        event_appender=appender,
        dispatch=lambda request: (
            request == payload and appender.events[-1].payload.exposure == "authorized_disclosure"
        ),
    )
    assert result.result is True
    event = appender.events[0]
    assert event.payload.operation == "remote_query"
    assert event.payload.payload_digest == _digest(payload)
    assert event.payload.recipient == "source:approved-fixture"
    assert payload not in audit_document_bytes(event)
    observed = observe_canary_locally(
        marker,
        coverage_ref=_doc_ref("coverage_manifest"),
        candidates=(),
        external_manifest=manifest,
        external_access_event=result.access_event,
        external_query_payload=payload,
    )
    observation = observed.documents[0].payload
    assert observation.external_query is True
    assert observation.access_event_ref == result.access_event_ref
    assert observation.observation == "not_observed_in_scope"
    assert b"Q" * 43 not in audit_document_bytes(observed.documents[0])

    with pytest.raises(SealedArtifactError, match="external canary queries require"):
        observe_canary_locally(
            marker,
            coverage_ref=_doc_ref("coverage_manifest"),
            candidates=(),
            external_query_payload=payload,
        )


def test_rotation_primitive_preserves_ciphertext_and_distinguishes_key_versions() -> None:
    _, envelope, old_provider, _, _ = _sealed_fixture()
    new_provider = LocalDevelopmentAESKWPKeyProvider(b"z" * 32, key_version="local-v2")
    rotated = rewrap_data_key(
        envelope,
        old_provider=old_provider,
        new_provider=new_provider,
        allow_local_development_key=True,
    )
    assert rotated.ciphertext == envelope.ciphertext
    assert rotated.key_version != envelope.key_version
