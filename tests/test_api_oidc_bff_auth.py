from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from uuid import UUID

import pytest
from polycodebench_api.auth import ApiPrincipal, TokenDirectory


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _signed_token(key: bytes, claims: dict[str, object]) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    signature = hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    return {
        "iss": "polycodebench-web",
        "aud": "polycodebench-api",
        "sub": "https://issuer.example|subject-123",
        "roles": ["submitter"],
        "email": "person@example.org",
        "email_verified": True,
        "iat": now,
        "exp": now + 300,
        "jti": "opaque-random-id",
        **overrides,
    }


def test_web_oidc_assertion_resolves_only_as_verified_submitter() -> None:
    key = b"synthetic-test-signing-key-long-enough"
    directory = TokenDirectory({}, web_auth_signing_key=key)

    claims = _claims()
    principal = directory.resolve(_signed_token(key, claims))

    assert principal == ApiPrincipal(
        subject_id="https://issuer.example|subject-123",
        roles=frozenset({"submitter"}),
        email="person@example.org",
        email_verified=True,
        expires_at=claims["exp"],
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "untrusted-web"},
        {"aud": "polycodebench-admin"},
        {"roles": ["administrator"]},
        {"roles": ["submitter", "reviewer"]},
        {"email_verified": False},
        {"email_verified": "true"},
        {"exp": int(time.time()) - 1},
        {"iat": int(time.time()) + 60},
        {"exp": int(time.time()) + 3600},
        {"jti": ""},
    ],
)
def test_web_oidc_assertion_rejects_invalid_or_elevated_claims(
    overrides: dict[str, object],
) -> None:
    key = b"synthetic-test-signing-key-long-enough"
    directory = TokenDirectory({}, web_auth_signing_key=key)

    assert directory.resolve(_signed_token(key, _claims(**overrides))) is None


def test_web_oidc_assertion_rejects_tampering_wrong_key_and_short_keys() -> None:
    key = b"synthetic-test-signing-key-long-enough"
    token = _signed_token(key, _claims())
    directory = TokenDirectory({}, web_auth_signing_key=key)
    other_directory = TokenDirectory({}, web_auth_signing_key=b"another-signing-key-long-enough!!")
    parts = token.split(".")
    attacker_payload = _b64url(json.dumps(_claims(email="attacker@example.org")).encode())
    tampered = f"{parts[0]}.{attacker_payload}.{parts[2]}"

    assert directory.resolve(tampered) is None
    assert other_directory.resolve(token) is None
    with pytest.raises(ValueError, match="at least 32 bytes"):
        TokenDirectory({}, web_auth_signing_key=b"too-short")


def test_production_token_directory_requires_shared_web_auth_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PCB_ENVIRONMENT", "production")
    monkeypatch.delenv("PCB_API_IDENTITY_FILE", raising=False)
    monkeypatch.delenv("PCB_API_IDENTITY_JSON", raising=False)
    monkeypatch.delenv("PCB_WEB_AUTH_SIGNING_KEY", raising=False)

    with pytest.raises(RuntimeError, match="PCB_WEB_AUTH_SIGNING_KEY is required"):
        TokenDirectory.from_env()

    monkeypatch.setenv("PCB_WEB_AUTH_SIGNING_KEY", "synthetic-production-test-signing-key-32-bytes")
    with pytest.raises(RuntimeError, match="PCB_API_IDENTITY_JSON"):
        TokenDirectory.from_env()


def test_production_token_directory_loads_inline_identity_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "synthetic-production-test-signing-key-32-bytes"
    monkeypatch.setenv("PCB_ENVIRONMENT", "staging")
    monkeypatch.setenv("PCB_WEB_AUTH_SIGNING_KEY", key)
    monkeypatch.delenv("PCB_API_IDENTITY_FILE", raising=False)
    monkeypatch.setenv("PCB_API_IDENTITY_JSON", '{"schema_version":1,"principals":[]}')

    directory = TokenDirectory.from_env()
    claims = _claims()

    assert directory.resolve(_signed_token(key.encode(), claims)) == ApiPrincipal(
        subject_id="https://issuer.example|subject-123",
        roles=frozenset({"submitter"}),
        email="person@example.org",
        email_verified=True,
        expires_at=claims["exp"],
    )


def test_identity_directory_sources_are_exclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    identity_file = tmp_path / "identities.json"
    identity_file.write_text('{"schema_version":1,"principals":[]}', encoding="utf-8")
    monkeypatch.setenv("PCB_ENVIRONMENT", "development")
    monkeypatch.setenv("PCB_API_IDENTITY_FILE", str(identity_file))
    monkeypatch.setenv("PCB_API_IDENTITY_JSON", '{"schema_version":1,"principals":[]}')

    with pytest.raises(RuntimeError, match="configure only one"):
        TokenDirectory.from_env()


def test_identity_directory_accepts_and_validates_tenant_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "private-audit-test-bearer-token"
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    tenant_id = "22222222-2222-4222-8222-222222222222"
    now = int(time.time())
    principal = {
        "subject_id": "operator-1",
        "roles": ["operator"],
        "mfa": False,
        "email": None,
        "email_verified": False,
        "expires_at": now + 300,
        "tenant_id": tenant_id,
    }
    monkeypatch.setenv("PCB_ENVIRONMENT", "development")
    monkeypatch.delenv("PCB_API_IDENTITY_FILE", raising=False)
    monkeypatch.setenv(
        "PCB_API_IDENTITY_JSON",
        json.dumps(
            {
                "schema_version": 1,
                "principals": [{"token_sha256": fingerprint, "principal": principal}],
            }
        ),
    )

    directory = TokenDirectory.from_env()
    assert directory.resolve(token) == ApiPrincipal(
        subject_id="operator-1",
        roles=frozenset({"operator"}),
        expires_at=now + 300,
        tenant_id=UUID(tenant_id),
    )

    principal["tenant_id"] = "not-a-uuid"
    monkeypatch.setenv(
        "PCB_API_IDENTITY_JSON",
        json.dumps(
            {
                "schema_version": 1,
                "principals": [{"token_sha256": fingerprint, "principal": principal}],
            }
        ),
    )
    with pytest.raises(RuntimeError, match="invalid or expired claims"):
        TokenDirectory.from_env()
