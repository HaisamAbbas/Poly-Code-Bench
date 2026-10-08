"""Interval semantics, model context and timestamp commitment controls."""

from __future__ import annotations

from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_core.audit_temporal import evaluate_temporal_eligibility
from polycodebench_core.benchmark_audit_documents import (
    AuditDocumentRef,
    ChronologyInterval,
    DocumentMetadata,
    EntityRef,
    HidingCommitmentPayload,
    ImmutableArtifactRef,
    ModelContextDocument,
    ModelContextPayload,
    TemporalAssessmentDocumentV2,
    TemporalAssessmentPayloadV2,
    TimestampEvidence,
    TrustedTimestampProof,
    audit_document_digest,
    parse_audit_document,
)
from polycodebench_core.canonical import (
    canonical_envelope_bytes,
    canonical_json_bytes,
    sha256_bytes,
)
from polycodebench_core.models import Digest
from polycodebench_services.audit_temporal import (
    ApprovedTimestampAdapterRegistry,
    TimestampVerificationUnavailable,
    VerifiedTimestampEvidence,
    bind_commitment_artifacts,
    build_temporal_assessment_payload,
    commitment_for_bytes,
    create_hiding_commitment,
    create_local_timestamp_receipt,
    verify_local_timestamp_receipt,
)

_DIGEST: Digest = "sha256:" + "a" * 64
_DOC_REF = AuditDocumentRef(
    document_id=UUID("33333333-3333-4333-8333-333333333333"),
    digest=_DIGEST,
    kind="audit_attestation",
)


def _timestamp(value: str, precision: str = "second") -> TimestampEvidence:
    return TimestampEvidence(
        value=value,
        precision=precision,  # type: ignore[arg-type]
        uncertainty=None,
        source_ref=_DOC_REF,
    )


def _interval(
    *,
    event: str,
    earliest: str | None,
    latest: str | None,
    basis: str = "verified_upstream_record",
    derivation: str = "direct_source_record",
    verification_state: str = "verified",
    precision: str = "second",
) -> ChronologyInterval:
    return ChronologyInterval(
        event=event,  # type: ignore[arg-type]
        earliest=_timestamp(earliest, precision) if earliest else None,
        latest=_timestamp(latest, precision) if latest else None,
        basis=basis,  # type: ignore[arg-type]
        derivation=derivation,  # type: ignore[arg-type]
        verification_state=verification_state,  # type: ignore[arg-type]
        evidence_ref=_DOC_REF if verification_state != "unknown" else None,
        unknown_reason="No source timestamp supplied." if verification_state == "unknown" else None,
    )


def _context(
    *,
    cutoff: tuple[str, str] | None = ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
    cutoff_confidence: str = "verified",
    cutoff_precision: str = "second",
    pin_confidence: str = "verified_revision",
    revision: str | None = "model-revision-17",
    alias: str | None = None,
    updates: tuple[ChronologyInterval, ...] = (),
) -> ModelContextPayload:
    cutoff_interval = (
        _interval(
            event="model_training_cutoff",
            earliest=cutoff[0],
            latest=cutoff[1],
            basis=(
                "provider_declared"
                if cutoff_confidence == "provider_declared"
                else "verified_upstream_record"
            ),
            derivation=(
                "provider_declaration"
                if cutoff_confidence == "provider_declared"
                else "direct_source_record"
            ),
            verification_state=(
                "unverified" if cutoff_confidence == "provider_declared" else "verified"
            ),
            precision=cutoff_precision,
        )
        if cutoff
        else None
    )
    return ModelContextPayload(
        provider="example-provider",
        model_alias=alias,
        model_revision=revision,
        weights_digest=_DIGEST if pin_confidence == "verified_weight_digest" else None,
        pin_confidence=pin_confidence,  # type: ignore[arg-type]
        pin_evidence_refs=(_DOC_REF,) if pin_confidence != "mutable_alias" else (),
        training_cutoff=cutoff_interval,
        cutoff_confidence=cutoff_confidence,  # type: ignore[arg-type]
        update_history=updates,
        retrieval_mode="unknown",
        retrieval_policy_ref=None,
        tool_policy_ref=None,
        prior_delivery_refs=(),
        recorded_at="2026-10-08T12:00:00Z",
    )


def test_earlier_upstream_issue_wins_over_later_benchmark_publication() -> None:
    result = evaluate_temporal_eligibility(
        (
            _interval(
                event="upstream_public_exposure",
                earliest="2019-02-01T00:00:00Z",
                latest="2019-02-01T00:00:00Z",
            ),
            _interval(
                event="benchmark_publication",
                earliest="2024-05-01T00:00:00Z",
                latest="2024-05-01T00:00:00Z",
            ),
        ),
        _context(),
    )

    assert result.status == "pre_cutoff_exposure_detected"
    assert "not_training_proof" in result.claim_qualifier_codes
    assert result.explanation_code == "exposure_precedes_cutoff"


def test_day_precision_interval_overlapping_cutoff_is_not_post_cutoff() -> None:
    source = ChronologyInterval(
        event="upstream_public_exposure",
        earliest=_timestamp("2020-01-01", "day"),
        latest=_timestamp("2020-01-01", "day"),
        basis="verified_upstream_record",
        derivation="direct_source_record",
        verification_state="verified",
        evidence_ref=_DOC_REF,
        unknown_reason=None,
    )
    result = evaluate_temporal_eligibility((source,), _context())
    assert result.status == "interval_overlap"
    assert result.explanation_code == "source_cutoff_intervals_overlap"


def test_nanosecond_precision_distinguishes_adjacent_instants() -> None:
    context = _context(
        cutoff=(
            "2020-01-01T00:00:00.000000000Z",
            "2020-01-01T00:00:00.000000000Z",
        ),
        cutoff_precision="nanosecond",
    )
    source = _interval(
        event="upstream_public_exposure",
        earliest="2020-01-01T00:00:00.000000001Z",
        latest="2020-01-01T00:00:00.000000001Z",
        precision="nanosecond",
    )
    assert evaluate_temporal_eligibility((source,), context).status == "post_declared_cutoff"


def test_archive_capture_after_cutoff_is_not_mistaken_for_first_exposure() -> None:
    source = _interval(
        event="upstream_public_exposure",
        earliest=None,
        latest="2021-01-01T00:00:00Z",
        basis="archive_capture",
        derivation="archive_capture_time",
    )
    result = evaluate_temporal_eligibility((source,), _context())
    assert result.status == "unknown_source_time"
    assert result.explanation_code == "source_interval_incomplete"


def test_local_timestamp_receipt_cannot_be_recorded_as_public_exposure() -> None:
    with pytest.raises(ValueError, match="does not prove public exposure"):
        ChronologyInterval(
            event="upstream_public_exposure",
            earliest=None,
            latest=_timestamp("2021-01-01T00:00:00Z"),
            basis="local_signed_receipt",
            derivation="commitment_receipt_time",
            verification_state="unverified",
            evidence_ref=_DOC_REF,
            unknown_reason=None,
        )


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (_context(cutoff=None, cutoff_confidence="unknown"), "unknown_cutoff"),
        (
            _context(
                cutoff=None,
                cutoff_confidence="unknown",
                pin_confidence="mutable_alias",
                revision=None,
                alias="latest",
            ),
            "mutable_model_context",
        ),
    ],
)
def test_unknown_cutoff_and_mutable_alias_cannot_receive_post_badge(
    context: ModelContextPayload, expected: str
) -> None:
    source = _interval(
        event="upstream_public_exposure",
        earliest="2024-01-01T00:00:00Z",
        latest="2024-01-01T00:00:00Z",
    )
    assert evaluate_temporal_eligibility((source,), context).status == expected


def test_provider_declared_post_cutoff_claim_is_explicitly_qualified() -> None:
    context = _context(
        cutoff=("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
        cutoff_confidence="provider_declared",
        pin_confidence="provider_declared",
    )
    source = _interval(
        event="upstream_public_exposure",
        earliest="2021-01-01T00:00:00Z",
        latest="2021-01-01T00:00:00Z",
    )
    result = evaluate_temporal_eligibility((source,), context)
    assert result.status == "post_declared_cutoff"
    assert set(result.claim_qualifier_codes) == {
        "no_originality_claim",
        "not_training_proof",
        "provider_declared_cutoff",
        "provider_declared_pin",
    }


def test_temporal_assessment_v2_round_trips_with_qualified_status() -> None:
    context = _context()
    context_ref = AuditDocumentRef(
        document_id=UUID("55555555-5555-4555-8555-555555555555"),
        digest=sha256_bytes(
            canonical_envelope_bytes("model_context", context.model_dump(mode="json"))
        ),
        kind="model_context",
    )
    payload = TemporalAssessmentPayloadV2(
        artifact_ref=ImmutableArtifactRef(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            digest=_DIGEST,
            visibility="private",
            media_type="application/json",
        ),
        model_context_ref=context_ref,
        model_context_snapshot=context,
        chronology=(
            _interval(
                event="upstream_public_exposure",
                earliest="2021-01-01T00:00:00Z",
                latest="2021-01-01T00:00:00Z",
            ),
        ),
        exposure_paths=(),
        hiding_commitment=None,
        status="post_declared_cutoff",
        explanation_code="exposure_follows_cutoff",
        claim_qualifier_codes=("no_originality_claim", "not_training_proof"),
    )
    document = TemporalAssessmentDocumentV2(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        kind="temporal_assessment",
        schema_version=2,
        payload=payload,
        metadata=DocumentMetadata(
            created_at="2026-10-08T12:00:00Z",
            timestamp_precision="second",
            actor="temporal-test",
            trace_id=None,
            row_version=0,
        ),
    )
    parsed = parse_audit_document(canonical_json_bytes(document.model_dump(mode="json")))
    assert isinstance(parsed, TemporalAssessmentDocumentV2)
    assert parsed.payload.claim_qualifier_codes == (
        "no_originality_claim",
        "not_training_proof",
    )


def test_service_builder_binds_context_document_to_interval_evaluator() -> None:
    context_document = ModelContextDocument(
        id=UUID("55555555-5555-4555-8555-555555555555"),
        kind="model_context",
        schema_version=1,
        payload=_context(),
        metadata=DocumentMetadata(
            created_at="2026-10-08T12:00:00Z",
            timestamp_precision="second",
            actor="temporal-test",
            trace_id=None,
            row_version=0,
        ),
    )
    payload = build_temporal_assessment_payload(
        artifact_ref=ImmutableArtifactRef(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            digest=_DIGEST,
            visibility="private",
            media_type="application/json",
        ),
        chronology=(
            _interval(
                event="upstream_public_exposure",
                earliest="2019-01-01T00:00:00Z",
                latest="2019-01-01T00:00:00Z",
            ),
        ),
        model_context=context_document,
    )
    assert payload.status == "pre_cutoff_exposure_detected"
    assert payload.model_context_ref is not None
    assert payload.model_context_ref.digest == audit_document_digest(context_document)
    assert payload.model_context_snapshot == context_document.payload


def test_temporal_assessment_refuses_unqualified_pre_cutoff_claim() -> None:
    context = _context()
    context_ref = AuditDocumentRef(
        document_id=UUID("55555555-5555-4555-8555-555555555555"),
        digest=sha256_bytes(
            canonical_envelope_bytes("model_context", context.model_dump(mode="json"))
        ),
        kind="model_context",
    )
    with pytest.raises(ValueError, match="cannot claim model training"):
        TemporalAssessmentPayloadV2(
            artifact_ref=ImmutableArtifactRef(
                artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
                digest=_DIGEST,
                visibility="private",
                media_type="application/json",
            ),
            model_context_ref=context_ref,
            model_context_snapshot=context,
            chronology=(
                _interval(
                    event="upstream_public_exposure",
                    earliest="2019-01-01T00:00:00Z",
                    latest="2019-01-01T00:00:00Z",
                ),
            ),
            exposure_paths=(),
            hiding_commitment=None,
            status="pre_cutoff_exposure_detected",
            explanation_code="exposure_precedes_cutoff",
            claim_qualifier_codes=("no_originality_claim",),
        )


def test_temporal_assessment_cannot_override_interval_evaluator_result() -> None:
    context = _context()
    context_ref = AuditDocumentRef(
        document_id=UUID("55555555-5555-4555-8555-555555555555"),
        digest=sha256_bytes(
            canonical_envelope_bytes("model_context", context.model_dump(mode="json"))
        ),
        kind="model_context",
    )
    with pytest.raises(ValueError, match="must match the interval evaluator"):
        TemporalAssessmentPayloadV2(
            artifact_ref=ImmutableArtifactRef(
                artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
                digest=_DIGEST,
                visibility="private",
                media_type="application/json",
            ),
            model_context_ref=context_ref,
            model_context_snapshot=context,
            chronology=(
                _interval(
                    event="upstream_public_exposure",
                    earliest="2021-01-01T00:00:00Z",
                    latest="2021-01-01T00:00:00Z",
                ),
            ),
            exposure_paths=(),
            hiding_commitment=None,
            status="interval_overlap",
            explanation_code="exposure_follows_cutoff",
            claim_qualifier_codes=("no_originality_claim", "not_training_proof"),
        )


def test_unverified_earlier_claim_blocks_a_post_cutoff_badge() -> None:
    source = _interval(
        event="upstream_public_exposure",
        earliest="2021-01-01T00:00:00Z",
        latest="2021-01-01T00:00:00Z",
    )
    owner_claim = _interval(
        event="upstream_public_exposure",
        earliest="2018-01-01T00:00:00Z",
        latest="2018-01-01T00:00:00Z",
        basis="owner_claim",
        derivation="owner_assertion",
        verification_state="unverified",
    )
    result = evaluate_temporal_eligibility((source, owner_claim), _context())
    assert result.status == "unknown_source_time"
    assert result.explanation_code == "unverified_source_could_precede_cutoff"


def test_model_update_after_cutoff_invalidates_the_pinned_cutoff_claim() -> None:
    update = _interval(
        event="model_update",
        earliest="2021-01-01T00:00:00Z",
        latest="2021-01-01T00:00:00Z",
    )
    source = _interval(
        event="upstream_public_exposure",
        earliest="2022-01-01T00:00:00Z",
        latest="2022-01-01T00:00:00Z",
    )
    result = evaluate_temporal_eligibility((source,), _context(updates=(update,)))
    assert result.status == "mutable_model_context"
    assert result.explanation_code == "model_update_not_proven_before_cutoff"


def test_commitment_uses_high_entropy_nonce_and_binds_exact_bytes() -> None:
    canonical_manifest = b'{"artifact":"private task"}'
    created = create_hiding_commitment(canonical_manifest)
    assert len(created.nonce) == 32
    assert commitment_for_bytes(canonical_manifest, created.nonce) == created.commitment
    changed_manifest = b'{"artifact":"changed"}'
    assert commitment_for_bytes(changed_manifest, created.nonce) != created.commitment
    assert commitment_for_bytes(canonical_manifest, b"x" * 32) != created.commitment
    with pytest.raises(ValueError, match="256 bits"):
        commitment_for_bytes(canonical_manifest, b"short")
    with pytest.raises(ValueError, match="canonical JSON"):
        commitment_for_bytes(canonical_manifest + b" ", created.nonce)


def test_commitment_contract_keeps_nonce_private_and_receipts_bound() -> None:
    nonce_ref = ImmutableArtifactRef(
        artifact_id=UUID("66666666-6666-4666-8666-666666666666"),
        digest=_DIGEST,
        visibility="private",
        media_type="application/octet-stream",
    )
    payload = HidingCommitmentPayload(
        scheme="sha256-salted-v1",
        commitment=_DIGEST,
        nonce_ref=nonce_ref,
        local_receipt=None,
        trusted_proof=None,
    )
    assert payload.nonce_ref.visibility == "private"
    bind_commitment_artifacts(commitment=_DIGEST, nonce_ref=nonce_ref)
    with pytest.raises(ValueError, match="remain private"):
        HidingCommitmentPayload(
            scheme="sha256-salted-v1",
            commitment=_DIGEST,
            nonce_ref=nonce_ref.model_copy(update={"visibility": "public"}),
            local_receipt=None,
            trusted_proof=None,
        )
    local_receipt = create_local_timestamp_receipt(
        _DIGEST,
        Ed25519PrivateKey.generate(),
        EntityRef(
            entity_id=UUID("22222222-2222-4222-8222-222222222222"),
            entity_kind="signing_key",
        ),
    )
    with pytest.raises(ValueError, match="bound to a different commitment"):
        HidingCommitmentPayload(
            scheme="sha256-salted-v1",
            commitment="sha256:" + "b" * 64,
            nonce_ref=nonce_ref,
            local_receipt=local_receipt,
            trusted_proof=None,
        )
    with pytest.raises(ValueError, match="signature or commitment is invalid"):
        bind_commitment_artifacts(
            commitment=_DIGEST,
            nonce_ref=nonce_ref,
            local_receipt=local_receipt.model_copy(update={"signature_b64": "A" * 88}),
        )


def test_local_ed25519_receipt_verifies_commitment_but_stays_non_independent() -> None:
    key = Ed25519PrivateKey.generate()
    key_ref = EntityRef(
        entity_id=UUID("22222222-2222-4222-8222-222222222222"),
        entity_kind="signing_key",
    )
    receipt = create_local_timestamp_receipt(_DIGEST, key, key_ref)
    assert receipt.adapter_version == "local-ed25519-v1"
    assert receipt.trust_label == "local_non_independent"
    assert verify_local_timestamp_receipt(receipt, _DIGEST)
    assert not verify_local_timestamp_receipt(receipt, "sha256:" + "b" * 64)
    assert not verify_local_timestamp_receipt(
        receipt.model_copy(update={"signature_b64": "A" * 88}), _DIGEST
    )
    changed_time = receipt.model_copy(
        update={
            "received_at": _timestamp("2026-10-08T12:00:00Z"),
        }
    )
    assert not verify_local_timestamp_receipt(changed_time, _DIGEST)


def test_trusted_timestamp_registry_fails_closed_without_approved_adapter() -> None:
    proof = TrustedTimestampProof(
        provider="example-tsa",
        adapter_version="rfc3161-v1",
        protocol="rfc3161",
        commitment=_DIGEST,
        token_ref=ImmutableArtifactRef(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            digest=sha256_bytes(b"encoded signed token"),
            visibility="private",
            media_type="application/timestamp-reply",
        ),
        receipt_time=None,
        certificate_refs=(),
        verifier_ref=None,
        verification_state="pending",
    )
    with pytest.raises(TimestampVerificationUnavailable):
        ApprovedTimestampAdapterRegistry({}).verify(proof, _DIGEST, lambda _: b"token")


def test_approved_timestamp_adapter_result_must_bind_the_commitment() -> None:
    class TestAdapter:
        provider = "example-tsa"
        adapter_version = "rfc3161-v1"
        protocol = "rfc3161"

        def verify_token(
            self, token: bytes, expected_commitment: Digest
        ) -> VerifiedTimestampEvidence:
            assert token == b"encoded signed token"
            return VerifiedTimestampEvidence(
                commitment=expected_commitment,
                receipt_time=_timestamp("2026-10-08T12:00:00Z"),
                certificate_refs=(_DOC_REF,),
                verifier_ref=_DOC_REF,
            )

    proof = TrustedTimestampProof(
        provider="example-tsa",
        adapter_version="rfc3161-v1",
        protocol="rfc3161",
        commitment=_DIGEST,
        token_ref=ImmutableArtifactRef(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            digest=sha256_bytes(b"encoded signed token"),
            visibility="private",
            media_type="application/timestamp-reply",
        ),
        receipt_time=None,
        certificate_refs=(),
        verifier_ref=None,
        verification_state="pending",
    )
    registry = ApprovedTimestampAdapterRegistry(
        {("example-tsa", "rfc3161-v1", "rfc3161"): TestAdapter()}
    )
    verified = registry.verify(proof, _DIGEST, lambda _: b"encoded signed token")
    assert verified.verification_state == "verified"
    assert verified.commitment == _DIGEST
    with pytest.raises(ValueError, match="artifact digest"):
        registry.verify(proof, _DIGEST, lambda _: b"different token bytes")
