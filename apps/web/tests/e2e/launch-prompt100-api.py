"""Launch synthetic public signatures using a process-local development key."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from polycodebench_api.app import create_app
from polycodebench_api.auth import TokenDirectory
from polycodebench_core.audit_attestations import (
    AttestationRevocationSnapshot,
    AttestationTrustStore,
    PublicAuditAttestationProjection,
    PublicAuditHealthSummary,
    SignedPublicAuditAttestation,
    TrustedAttestationKey,
)
from polycodebench_core.benchmark_audit_documents import (
    AuditAttestationDocument,
    parse_audit_document,
)
from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_services.audit_attestations import sign_public_attestation

VALID_ID = UUID("50000000-0000-4000-8000-000000000100")
TAMPERED_ID = UUID("60000000-0000-4000-8000-000000000100")
PRIVATE_FIELD_ID = UUID("70000000-0000-4000-8000-000000000100")
REVOKED_ID = UUID("80000000-0000-4000-8000-000000000100")
SUPERSEDED_ID = UUID("90000000-0000-4000-8000-000000000100")
PRIVATE_SENTINEL = "PROMPT100_PRIVATE_FIXTURE_MARKER"
_DIGEST = "sha256:" + "b" * 64


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _document(
    key_id: UUID,
    report_id: UUID,
    issued_at: str,
    expires_at: str,
    attestation_id: UUID = VALID_ID,
) -> AuditAttestationDocument:
    def ref(kind: str) -> dict[str, str]:
        return {"document_id": str(uuid4()), "digest": _DIGEST, "kind": kind}

    value = {
        "id": str(attestation_id),
        "kind": "audit_attestation",
        "schema_version": 1,
        "payload": {
            "benchmark_ref": ref("benchmark_snapshot"),
            "scan_ref": ref("coverage_manifest"),
            "policy_ref": ref("risk_policy"),
            "coverage_ref": ref("coverage_manifest"),
            "claim_digests": [_DIGEST],
            "model_context": None,
            "issued_at": issued_at,
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
            "created_at": issued_at,
            "timestamp_precision": "second",
            "actor": "synthetic-fixture",
            "trace_id": None,
            "row_version": 0,
        },
    }
    document = parse_audit_document(canonical_json_bytes(value))
    assert isinstance(document, AuditAttestationDocument)
    return document


def _health() -> PublicAuditHealthSummary:
    return PublicAuditHealthSummary(
        kind="public_benchmark_health",
        schema_version=1,
        report_id=uuid4(),
        review_state="published",
        benchmark_label="Synthetic HumanEval",
        benchmark_version="fixture-v1",
        source_window_start="2026-01-01T00:00:00Z",
        source_window_end="2026-02-01T00:00:00Z",
        selected_tasks=12,
        complete_tasks=5,
        partial_tasks=2,
        unknown_tasks=1,
        unscanned_tasks=3,
        blocked_tasks=1,
        assessed_tasks=9,
        low_risk_tasks=4,
        medium_risk_tasks=2,
        high_risk_tasks=1,
        insufficient_risk_tasks=2,
        limitations=("partial_coverage", "synthetic_fixture"),
    )


class _PublicAttestationFixture:
    def __init__(self) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        now_text = _utc_text(now)
        expires_text = _utc_text(now + timedelta(days=30))
        key_id = uuid4()
        private_key = Ed25519PrivateKey.generate()
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        key = TrustedAttestationKey(
            key_id=key_id,
            signature_algorithm="Ed25519",
            public_key_b64=b64encode(public_bytes).decode("ascii"),
            trust_level="development",
            status="active",
            valid_from="2020-01-01T00:00:00Z",
        )
        self.trust_store = AttestationTrustStore(
            schema_version=1,
            checked_at=now_text,
            keys=(key,),
        )
        health = _health()

        def signed_for(attestation_id: UUID) -> SignedPublicAuditAttestation:
            document = _document(
                key_id,
                health.report_id,
                now_text,
                expires_text,
                attestation_id,
            )
            return sign_public_attestation(
                document,
                health,
                key_id=key_id,
                private_key=private_key,
            )

        signed = signed_for(VALID_ID)
        lifecycle = AttestationRevocationSnapshot(status="published", checked_at=now_text)
        valid = PublicAuditAttestationProjection(
            schema_version=1,
            attestation=signed,
            lifecycle=lifecycle,
        ).model_dump(mode="json")
        tampered_base = signed_for(TAMPERED_ID)
        changed_health = tampered_base.claims.health.model_copy(
            update={"benchmark_label": "TAMPERED CLAIM"}
        )
        tampered = tampered_base.model_copy(
            update={"claims": tampered_base.claims.model_copy(update={"health": changed_health})}
        )
        revoked_signed = signed_for(REVOKED_ID)
        revoked = PublicAuditAttestationProjection(
            schema_version=1,
            attestation=revoked_signed,
            lifecycle=AttestationRevocationSnapshot(
                status="revoked",
                checked_at=now_text,
                reason_code="correction",
            ),
        ).model_dump(mode="json")
        superseded = PublicAuditAttestationProjection(
            schema_version=1,
            attestation=signed_for(SUPERSEDED_ID),
            lifecycle=AttestationRevocationSnapshot(
                status="superseded",
                checked_at=now_text,
                successor_id=VALID_ID,
            ),
        ).model_dump(mode="json")
        private_projection = PublicAuditAttestationProjection(
            schema_version=1,
            attestation=signed_for(PRIVATE_FIELD_ID),
            lifecycle=lifecycle,
        ).model_dump(mode="json")
        self.rows: dict[UUID, Mapping[str, object]] = {
            VALID_ID: valid,
            TAMPERED_ID: PublicAuditAttestationProjection(
                schema_version=1,
                attestation=tampered,
                lifecycle=lifecycle,
            ).model_dump(mode="json"),
            PRIVATE_FIELD_ID: {**private_projection, "private_marker": PRIVATE_SENTINEL},
            REVOKED_ID: revoked,
            SUPERSEDED_ID: superseded,
        }

    def get(self, attestation_id: UUID) -> Mapping[str, object] | None:
        return self.rows.get(attestation_id)


def main() -> None:
    fixture = _PublicAttestationFixture()
    app = create_app(
        tokens=TokenDirectory({}),
        public_audit_attestations=fixture,
        attestation_trust_store=fixture.trust_store,
    )
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8140, log_level="warning")


if __name__ == "__main__":
    main()
