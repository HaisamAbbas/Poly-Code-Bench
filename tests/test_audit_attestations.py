"""Synthetic-key checks for scoped attestation signing, verification and lifecycle rules."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_api import audit_cli
from polycodebench_api.app import create_app
from polycodebench_api.submissions import SubmissionStore
from polycodebench_core.application_errors import OptimisticVersionConflict
from polycodebench_core.audit_attestations import (
    AttestationLifecycleEvent,
    AttestationRevocationSnapshot,
    AttestationTrustStore,
    PublicAuditAttestationProjection,
    PublicAuditHealthSummary,
    SignedPublicAuditAttestation,
    TrustedAttestationKey,
)
from polycodebench_core.benchmark_audit_documents import (
    AuditAttestationDocument,
    audit_document_digest,
    parse_audit_document,
)
from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_persistence.benchmark_audit import _validate_attestation_lifecycle_append
from polycodebench_publication.releases import ReleaseStore
from polycodebench_services.audit_attestations import (
    lifecycle_event_digest,
    sign_public_attestation,
    transition_attestation,
    verify_public_attestation,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
NOW_TEXT = "2026-10-08T12:00:00Z"
EXPIRY_TEXT = "2026-11-08T12:00:00Z"
_DIGEST = "sha256:" + "a" * 64


def _health(**updates: object) -> PublicAuditHealthSummary:
    value: dict[str, object] = {
        "kind": "public_benchmark_health",
        "schema_version": 1,
        "report_id": uuid4(),
        "review_state": "published",
        "benchmark_label": "HumanEval",
        "benchmark_version": "v1",
        "source_window_start": "2026-01-01T00:00:00Z",
        "source_window_end": "2026-10-01T00:00:00Z",
        "selected_tasks": 4,
        "complete_tasks": 2,
        "partial_tasks": 1,
        "unknown_tasks": 0,
        "unscanned_tasks": 1,
        "blocked_tasks": 0,
        "assessed_tasks": 3,
        "low_risk_tasks": 1,
        "medium_risk_tasks": 1,
        "high_risk_tasks": 0,
        "insufficient_risk_tasks": 1,
        "limitations": ("partial_coverage", "descriptive_only"),
    }
    value.update(updates)
    return PublicAuditHealthSummary.model_validate(value, strict=True)


def _document(key_id, report_id, *, expires_at: str = EXPIRY_TEXT) -> AuditAttestationDocument:
    def ref(kind: str) -> dict[str, str]:
        return {"document_id": str(uuid4()), "digest": _DIGEST, "kind": kind}

    value = {
        "id": str(uuid4()),
        "kind": "audit_attestation",
        "schema_version": 1,
        "payload": {
            "benchmark_ref": ref("benchmark_snapshot"),
            "scan_ref": ref("coverage_manifest"),
            "policy_ref": ref("risk_policy"),
            "coverage_ref": ref("coverage_manifest"),
            "claim_digests": [_DIGEST],
            "model_context": None,
            "issued_at": NOW_TEXT,
            "issued_at_precision": "second",
            "expires_at": expires_at,
            "expires_at_precision": "second",
            "review": {
                "document_id": str(report_id),
                "digest": _DIGEST,
                "kind": "benchmark_health",
            },
            "signature_algorithm": "Ed25519",
            "key_ref": {"entity_id": str(key_id), "entity_kind": "signing_key", "digest": None},
            "revocation_refs": [],
        },
        "metadata": {
            "created_at": NOW_TEXT,
            "timestamp_precision": "second",
            "actor": "synthetic-test",
            "trace_id": None,
            "row_version": 0,
        },
    }
    result = parse_audit_document(canonical_json_bytes(value))
    assert isinstance(result, AuditAttestationDocument)
    return result


def _key(*, trust_level: str = "reviewed", status: str = "active"):
    private_key = Ed25519PrivateKey.generate()
    key_id = uuid4()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trusted = TrustedAttestationKey(
        key_id=key_id,
        signature_algorithm="Ed25519",
        public_key_b64=base64.b64encode(public_bytes).decode("ascii"),
        trust_level=trust_level,
        status=status,
        valid_from="2026-01-01T00:00:00Z",
        revoked_at=NOW_TEXT if status == "revoked" else None,
    )
    store = AttestationTrustStore(schema_version=1, checked_at=NOW_TEXT, keys=(trusted,))
    return private_key, key_id, trusted, store


def _signed(*, trust_level: str = "reviewed", expires_at: str = EXPIRY_TEXT):
    private_key, key_id, trusted, store = _key(trust_level=trust_level)
    health = _health()
    document = _document(key_id, health.report_id, expires_at=expires_at)
    signed = sign_public_attestation(document, health, key_id=key_id, private_key=private_key)
    return document, signed, trusted, store


def _published_snapshot(checked_at: str = NOW_TEXT) -> AttestationRevocationSnapshot:
    return AttestationRevocationSnapshot(status="published", checked_at=checked_at)


def test_signing_binds_private_document_digest_and_verifies_canonical_public_claims() -> None:
    document, signed, _, store = _signed()
    result = verify_public_attestation(
        signed,
        store,
        revocation=_published_snapshot(),
        expected_document=document,
        now=NOW,
    )

    assert signed.claims.attestation_digest == audit_document_digest(document)
    assert result.signature_valid is True
    assert result.current_endorsement is True
    assert result.result == "current_scoped_attestation"


def test_tampering_wrong_key_and_private_digest_mismatch_fail_closed() -> None:
    document, signed, _, store = _signed()
    changed_health = signed.claims.health.model_copy(update={"benchmark_label": "MBPP"})
    tampered = signed.model_copy(
        update={"claims": signed.claims.model_copy(update={"health": changed_health})}
    )
    assert not verify_public_attestation(tampered, store, now=NOW).signature_valid

    _, _, _, other_store = _key()
    wrong_key = verify_public_attestation(signed, other_store, now=NOW)
    assert wrong_key.result == "untrusted_signer"
    assert not wrong_key.signature_valid

    mismatch = verify_public_attestation(
        signed,
        store,
        expected_document=_document(uuid4(), signed.claims.health.report_id),
        now=NOW,
    )
    assert mismatch.result == "scope_digest_mismatch"
    assert not mismatch.signature_valid


def test_expiry_revocation_freshness_and_development_keys_are_distinct() -> None:
    _, signed, _, store = _signed()
    offline = verify_public_attestation(signed, store, now=NOW)
    assert offline.signature_valid and not offline.current_endorsement
    assert offline.result == "signature_valid_revocation_unchecked"

    stale_snapshot = _published_snapshot("2026-10-06T12:00:00Z")
    stale = verify_public_attestation(signed, store, revocation=stale_snapshot, now=NOW)
    assert stale.signature_valid and not stale.current_endorsement
    assert stale.result == "signature_valid_revocation_stale"

    stale_trust_store = AttestationTrustStore(
        schema_version=1,
        checked_at="2026-10-06T12:00:00Z",
        keys=store.keys,
    )
    stale_key_list = verify_public_attestation(
        signed,
        stale_trust_store,
        revocation=_published_snapshot(),
        now=NOW,
    )
    assert stale_key_list.signature_valid and not stale_key_list.current_endorsement
    assert stale_key_list.revocation_freshness == "stale"
    assert stale_key_list.result == "signature_valid_revocation_stale"

    revoked = verify_public_attestation(
        signed,
        store,
        revocation=AttestationRevocationSnapshot(
            status="revoked", checked_at=NOW_TEXT, reason_code="key_compromise"
        ),
        now=NOW,
    )
    assert revoked.result == "revoked" and not revoked.current_endorsement

    _, dev_signed, _, dev_store = _signed(trust_level="development")
    dev = verify_public_attestation(
        dev_signed, dev_store, revocation=_published_snapshot(), now=NOW
    )
    assert dev.signature_valid and not dev.current_endorsement
    assert dev.result == "development_key_not_endorsed"

    _, expired_signed, _, expired_store = _signed()
    later = NOW + timedelta(days=40)
    later_text = later.isoformat(timespec="seconds").replace("+00:00", "Z")
    fresh_expiry_store = expired_store.model_copy(update={"checked_at": later_text})
    expired = verify_public_attestation(
        expired_signed,
        fresh_expiry_store,
        now=later,
    )
    assert expired.signature_valid and expired.result == "expired"
    assert expired.revocation_freshness == "offline"

    revoked_after_expiry = verify_public_attestation(
        expired_signed,
        expired_store,
        revocation=AttestationRevocationSnapshot(
            status="revoked",
            checked_at=later.isoformat(timespec="seconds").replace("+00:00", "Z"),
            reason_code="scope_error",
        ),
        now=later,
    )
    assert revoked_after_expiry.result == "revoked"


def test_signing_rejects_a_public_health_report_unrelated_to_review_ref() -> None:
    private_key, key_id, _, _ = _key()
    document = _document(key_id, uuid4())
    with pytest.raises(ValueError, match="referenced reviewed health report"):
        sign_public_attestation(
            document,
            _health(),
            key_id=key_id,
            private_key=private_key,
        )


def _event(
    history: tuple[AttestationLifecycleEvent, ...],
    *,
    action: str,
    actor_id: str,
    actor_role: str,
    **kwargs: object,
) -> AttestationLifecycleEvent:
    return transition_attestation(
        history,
        attestation_id=ATTESTATION_ID,
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        owner_id="owner-1",
        recorded_at=kwargs.pop("recorded_at", NOW_TEXT),
        expires_at=kwargs.pop("expires_at", EXPIRY_TEXT),
        **kwargs,  # type: ignore[arg-type]
    )


ATTESTATION_ID = uuid4()


def test_lifecycle_requires_role_separation_current_publish_and_valid_event_chain() -> None:
    _, signed, _, store = _signed()
    current = verify_public_attestation(
        signed,
        store,
        revocation=_published_snapshot(),
        now=NOW,
    )
    history: tuple[AttestationLifecycleEvent, ...] = ()
    for args in (
        {"action": "draft", "actor_id": "owner-1", "actor_role": "owner"},
        {
            "action": "request_review",
            "actor_id": "owner-1",
            "actor_role": "owner",
            "recorded_at": "2026-10-08T12:01:00Z",
        },
        {
            "action": "approve",
            "actor_id": "reviewer-1",
            "actor_role": "reviewer",
            "recorded_at": "2026-10-08T12:02:00Z",
            "evidence_digest": _DIGEST,
        },
        {
            "action": "sign",
            "actor_id": "signer-1",
            "actor_role": "signer",
            "recorded_at": "2026-10-08T12:03:00Z",
            "evidence_digest": _DIGEST,
            "signature_verification": current,
        },
        {
            "action": "publish",
            "actor_id": "publisher-1",
            "actor_role": "publisher",
            "recorded_at": "2026-10-08T12:04:00Z",
            "evidence_digest": _DIGEST,
            "signature_verification": current,
        },
    ):
        history += (_event(history, **args),)

    assert [event.to_status for event in history] == [
        "draft",
        "review_required",
        "approved",
        "signed",
        "published",
    ]
    assert history[-1].previous_event_digest == lifecycle_event_digest(history[-2])

    with pytest.raises(ValueError, match="distinct authorized publisher"):
        _event(
            history[:-1],
            action="publish",
            actor_id="signer-1",
            actor_role="publisher",
            recorded_at="2026-10-08T12:04:00Z",
            evidence_digest=_DIGEST,
            signature_verification=current,
        )

    with pytest.raises(ValueError, match="expiry events"):
        _event(
            history,
            action="expire",
            actor_id="monitor-1",
            actor_role="monitor",
            recorded_at="2026-10-08T12:05:00Z",
        )

    corrupted = history[2].model_copy(update={"actor_role": "owner"})
    bad_history = (*history[:2], corrupted, *history[3:])
    with pytest.raises(ValueError, match="actor role"):
        _event(
            bad_history,
            action="revoke",
            actor_id="reviewer-1",
            actor_role="reviewer",
            reason_code="scope_error",
            recorded_at="2026-10-08T12:05:00Z",
        )


def test_cli_verifies_local_json_and_reports_signature_without_current_endorsement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, signed, trusted, _ = _signed()
    attestation_file = tmp_path / "attestation.json"
    trust_file = tmp_path / "trust.json"
    attestation_file.write_bytes(canonical_json_bytes(signed.model_dump(mode="json")))
    trust_file.write_bytes(
        canonical_json_bytes(
            AttestationTrustStore(
                schema_version=1,
                checked_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                keys=(trusted,),
            ).model_dump(mode="json")
        )
    )

    result = audit_cli.main(
        ["audit", "verify-attestation", str(attestation_file), "--trust-store", str(trust_file)]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["signature_valid"] is True
    assert output["current_endorsement"] is False
    assert output["revocation_freshness"] == "offline"

    tampered = json.loads(attestation_file.read_text(encoding="utf-8"))
    tampered["claims"]["health"]["benchmark_label"] = "MBPP"
    attestation_file.write_text(json.dumps(tampered), encoding="utf-8")
    assert (
        audit_cli.main(
            [
                "audit",
                "verify-attestation",
                str(attestation_file),
                "--trust-store",
                str(trust_file),
            ]
        )
        == 4
    )
    assert json.loads(capsys.readouterr().out)["signature_valid"] is False


def test_event_digest_changes_when_any_lifecycle_field_changes() -> None:
    event = _event((), action="draft", actor_id="owner-1", actor_role="owner")
    changed = event.model_copy(update={"actor_id": "owner-2"})
    assert lifecycle_event_digest(event) != lifecycle_event_digest(changed)


def test_durable_lifecycle_append_validation_is_chained_and_idempotent() -> None:
    draft = _event((), action="draft", actor_id="owner-1", actor_role="owner")
    review = _event(
        (draft,),
        action="request_review",
        actor_id="owner-1",
        actor_role="owner",
        recorded_at="2026-10-08T12:01:00Z",
    )
    approved = _event(
        (draft, review),
        action="approve",
        actor_id="reviewer-1",
        actor_role="reviewer",
        recorded_at="2026-10-08T12:02:00Z",
        evidence_digest=_DIGEST,
    )

    assert _validate_attestation_lifecycle_append([], draft, owner_id="owner-1")
    assert _validate_attestation_lifecycle_append([draft], review, owner_id="owner-1")
    assert not _validate_attestation_lifecycle_append([draft, review], review, owner_id="owner-1")
    with pytest.raises(OptimisticVersionConflict):
        _validate_attestation_lifecycle_append([draft], approved, owner_id="owner-1")


class _PublicAttestationProjection:
    def __init__(self, attestation_id, projection: dict[str, object]) -> None:
        self.attestation_id = attestation_id
        self.projection = projection

    def get(self, attestation_id):
        return self.projection if attestation_id == self.attestation_id else None


def _attestation_app(tmp_path: Path, signed: SignedPublicAuditAttestation, trust_store):
    projection = PublicAuditAttestationProjection(
        schema_version=1,
        attestation=signed,
        lifecycle=_published_snapshot(),
    )
    return create_app(
        store=ReleaseStore(tmp_path / "releases.sqlite3"),
        submissions=SubmissionStore(tmp_path / "submissions.sqlite3"),
        cursor_key=b"benchmark-audit-test-cursor-key-0123456789",
        public_audit_attestations=_PublicAttestationProjection(
            signed.claims.attestation_id,
            projection.model_dump(mode="json"),
        ),
        attestation_trust_store=trust_store,
    )


def test_public_api_verifies_allowlisted_attestation_and_fails_closed_on_extra_fields(
    tmp_path: Path,
) -> None:
    _, signed, _, store = _signed(trust_level="development")
    app = _attestation_app(tmp_path, signed, store)

    import asyncio

    import httpx

    async def verify() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                f"/v1/public/audit-attestations/{signed.claims.attestation_id}"
            )
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"].lower()
            data = response.json()["data"]
            assert data["verification"]["signature_valid"] is True
            assert data["verification"]["current_endorsement"] is False
            assert data["verification"]["result"] == "development_key_not_endorsed"
            assert "solution-secret-marker" not in response.text
            health = data["attestation"]["claims"]["health"]
            assert "source_url" not in health
            assert "task_text" not in health

            private = json.loads(
                canonical_json_bytes(
                    PublicAuditAttestationProjection(
                        schema_version=1,
                        attestation=signed,
                        lifecycle=_published_snapshot(),
                    ).model_dump(mode="json")
                )
            )
            private["source_url"] = "https://private.invalid/task"
            app.state.services.public_audit_attestations.projection = private
            rejected = await client.get(
                f"/v1/public/audit-attestations/{signed.claims.attestation_id}"
            )
            assert rejected.status_code == 404
            assert "private.invalid" not in rejected.text

    asyncio.run(verify())


def test_public_api_reports_invalid_signature_without_claim_endorsement(tmp_path: Path) -> None:
    _, signed, _, store = _signed()
    altered_health = signed.claims.health.model_copy(update={"benchmark_label": "MBPP"})
    tampered = signed.model_copy(
        update={"claims": signed.claims.model_copy(update={"health": altered_health})}
    )
    app = _attestation_app(tmp_path, tampered, store)

    import asyncio

    import httpx

    async def verify() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                f"/v1/public/audit-attestations/{signed.claims.attestation_id}"
            )
            assert response.status_code == 200
            assert response.json()["data"]["verification"]["signature_valid"] is False

    asyncio.run(verify())


def test_public_api_without_public_store_and_trust_configuration_returns_not_found(
    tmp_path: Path,
) -> None:
    import asyncio

    import httpx

    app = create_app(
        store=ReleaseStore(tmp_path / "unconfigured-releases.sqlite3"),
        submissions=SubmissionStore(tmp_path / "unconfigured-submissions.sqlite3"),
        cursor_key=b"benchmark-audit-test-cursor-key-0123456789",
    )

    async def verify() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(f"/v1/public/audit-attestations/{uuid4()}")
            assert response.status_code == 404

    asyncio.run(verify())
