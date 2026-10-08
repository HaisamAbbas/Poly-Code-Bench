"""Bearer-token principals and role authorization (Technical Specification 20.2).

Tokens are resolved through a directory configured at application construction, so every admin
route enforces a server-side role check independent of any UI. Administrator, reviewer and
publisher actions additionally require an MFA-backed session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import Request
from polycodebench_publication.releases import ReleasePrincipal
from polycodebench_services.rbac import Permission, Principal, Role, authorize

from polycodebench_api.errors import ApiError

_BEARER_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,4096}$")


@dataclass(frozen=True)
class ApiPrincipal:
    """An authenticated caller: OIDC-style subject, granted roles and MFA policy state."""

    subject_id: str
    roles: frozenset[str]
    mfa: bool = False
    email: str | None = None
    email_verified: bool = False
    expires_at: int | None = None
    tenant_id: UUID | None = None


class TokenDirectory:
    """The API's bearer-token registry; unknown or unknown-format tokens authenticate as nobody.

    Deployments can mount a trusted identity export containing SHA-256 token fingerprints rather
    than token values. The issuer must rotate the file as short-lived account tokens expire.
    """

    def __init__(
        self,
        tokens: Mapping[str, ApiPrincipal],
        *,
        web_auth_signing_key: bytes | None = None,
    ) -> None:
        self._tokens = dict(tokens)
        self._fingerprints: dict[str, ApiPrincipal] = {}
        self._web_auth_signing_key = web_auth_signing_key
        if web_auth_signing_key is not None and len(web_auth_signing_key) < 32:
            raise ValueError("web auth signing key must contain at least 32 bytes")

    @classmethod
    def from_env(cls) -> TokenDirectory:
        """Load trusted claim fingerprints from a file or injected JSON secret."""
        identity_file = os.environ.get("PCB_API_IDENTITY_FILE")
        identity_json = os.environ.get("PCB_API_IDENTITY_JSON")
        web_auth_secret = os.environ.get("PCB_WEB_AUTH_SIGNING_KEY")
        environment = os.environ.get("PCB_ENVIRONMENT", "development")
        if web_auth_secret is not None and len(web_auth_secret.encode("utf-8")) < 32:
            raise RuntimeError("PCB_WEB_AUTH_SIGNING_KEY must contain at least 32 bytes")
        if environment in {"staging", "production"} and not web_auth_secret:
            raise RuntimeError("PCB_WEB_AUTH_SIGNING_KEY is required outside development")
        web_auth_signing_key = web_auth_secret.encode("utf-8") if web_auth_secret else None
        if identity_file is not None and identity_json is not None:
            raise RuntimeError(
                "configure only one of PCB_API_IDENTITY_FILE or PCB_API_IDENTITY_JSON"
            )
        if identity_file is None and identity_json is None:
            if environment in {"staging", "production"}:
                raise RuntimeError(
                    "PCB_API_IDENTITY_JSON or PCB_API_IDENTITY_FILE is required outside development"
                )
            return cls({}, web_auth_signing_key=web_auth_signing_key)
        source = "PCB_API_IDENTITY_JSON" if identity_json is not None else "PCB_API_IDENTITY_FILE"
        try:
            if identity_json is not None:
                if len(identity_json.encode("utf-8")) > 4_000_000:
                    raise RuntimeError("PCB_API_IDENTITY_JSON exceeds the 4 MB configuration limit")
                document = json.loads(identity_json)
            else:
                identity_path = Path(identity_file or "")
                if identity_path.stat().st_size > 4_000_000:
                    raise RuntimeError("PCB_API_IDENTITY_FILE exceeds the 4 MB configuration limit")
                document = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"{source} is unreadable or invalid JSON") from error
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "principals"}
            or document["schema_version"] != 1
            or not isinstance(document["principals"], list)
            or len(document["principals"]) > 10_000
        ):
            raise RuntimeError(f"{source} does not match schema version 1")
        allowed_roles = {role.value for role in Role}
        fingerprints: dict[str, ApiPrincipal] = {}
        for item in document["principals"]:
            if not isinstance(item, dict) or set(item) != {"token_sha256", "principal"}:
                raise RuntimeError(f"{source} contains an invalid principal row")
            fingerprint = item["token_sha256"]
            claims = item["principal"]
            if (
                not isinstance(fingerprint, str)
                or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)
                or fingerprint in fingerprints
                or not isinstance(claims, dict)
                or frozenset(claims)
                not in {
                    frozenset(
                        {"subject_id", "roles", "mfa", "email", "email_verified", "expires_at"}
                    ),
                    frozenset(
                        {
                            "subject_id",
                            "roles",
                            "mfa",
                            "email",
                            "email_verified",
                            "expires_at",
                            "tenant_id",
                        }
                    ),
                }
                or not isinstance(claims["subject_id"], str)
                or not claims["subject_id"].strip()
                or claims["subject_id"].strip() != claims["subject_id"]
                or not isinstance(claims["roles"], list)
                or any(role not in allowed_roles for role in claims["roles"])
                or not isinstance(claims["mfa"], bool)
                or (claims["email"] is not None and not isinstance(claims["email"], str))
                or not isinstance(claims["email_verified"], bool)
                or (claims["email_verified"] and not claims["email"])
                or not isinstance(claims["expires_at"], int)
                or isinstance(claims["expires_at"], bool)
                or claims["expires_at"] <= int(datetime.now(UTC).timestamp())
                or (
                    "tenant_id" in claims
                    and (
                        not isinstance(claims["tenant_id"], str)
                        or not _is_uuid(claims["tenant_id"])
                    )
                )
            ):
                raise RuntimeError(f"{source} contains invalid or expired claims")
            fingerprints[fingerprint] = ApiPrincipal(
                subject_id=claims["subject_id"],
                roles=frozenset(claims["roles"]),
                mfa=claims["mfa"],
                email=claims["email"],
                email_verified=claims["email_verified"],
                expires_at=claims["expires_at"],
                tenant_id=UUID(cast(str, claims["tenant_id"])) if "tenant_id" in claims else None,
            )
        directory = cls({}, web_auth_signing_key=web_auth_signing_key)
        directory._fingerprints = fingerprints
        return directory

    def resolve(self, token: str) -> ApiPrincipal | None:
        principal = self._tokens.get(token)
        if principal is None:
            fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
            principal = self._fingerprints.get(fingerprint)
        if principal is None and self._web_auth_signing_key is not None:
            principal = self._resolve_web_auth_token(token)
        if principal is not None and principal.expires_at is not None:
            if principal.expires_at <= int(datetime.now(UTC).timestamp()):
                return None
        return principal

    def _resolve_web_auth_token(self, token: str) -> ApiPrincipal | None:
        """Verify the web BFF's five-minute, submitter-only OIDC session assertion."""
        key = self._web_auth_signing_key
        if key is None:
            return None
        parts = token.split(".")
        if len(parts) != 3 or any(not part for part in parts):
            return None
        try:
            header = _decode_jwt_part(parts[0])
            claims = _decode_jwt_part(parts[1])
            if header != {"alg": "HS256", "typ": "JWT"}:
                return None
            expected = hmac.new(
                key,
                f"{parts[0]}.{parts[1]}".encode("ascii"),
                hashlib.sha256,
            ).digest()
            signature = _decode_b64url(parts[2])
            if not hmac.compare_digest(signature, expected):
                return None
            now = int(datetime.now(UTC).timestamp())
            if (
                set(claims)
                != {"iss", "aud", "sub", "roles", "email", "email_verified", "iat", "exp", "jti"}
                or claims["iss"] != "polycodebench-web"
                or claims["aud"] != "polycodebench-api"
                or not isinstance(claims["sub"], str)
                or not claims["sub"].strip()
                or len(claims["sub"]) > 512
                or claims["roles"] != ["submitter"]
                or not isinstance(claims["email"], str)
                or claims["email_verified"] is not True
                or len(claims["email"]) > 320
                or not isinstance(claims["iat"], int)
                or isinstance(claims["iat"], bool)
                or not isinstance(claims["exp"], int)
                or isinstance(claims["exp"], bool)
                or claims["iat"] > now + 30
                or claims["exp"] <= now
                or claims["exp"] <= claims["iat"]
                or claims["exp"] - claims["iat"] > 300
                or not isinstance(claims["jti"], str)
                or not claims["jti"]
                or len(claims["jti"]) > 200
            ):
                return None
            return ApiPrincipal(
                subject_id=claims["sub"],
                roles=frozenset({"submitter"}),
                email=claims["email"],
                email_verified=True,
                expires_at=claims["exp"],
            )
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None


def _decode_b64url(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid JWT encoding")
    padded = value + "=" * (-len(value) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise ValueError("invalid JWT encoding")
    return decoded


def _decode_jwt_part(value: str) -> dict[str, object]:
    decoded = json.loads(_decode_b64url(value))
    if not isinstance(decoded, dict):
        raise ValueError("JWT part is not an object")
    return decoded


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def bearer_principal(request: Request, tokens: TokenDirectory) -> ApiPrincipal:
    """Authenticate the bearer token or reject with one generic unauthenticated response."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not _BEARER_TOKEN.fullmatch(token):
        raise ApiError("UNAUTHENTICATED")
    principal = tokens.resolve(token)
    if principal is None or not principal.subject_id.strip():
        raise ApiError("UNAUTHENTICATED")
    return principal


def require_permission(
    request: Request,
    tokens: TokenDirectory,
    permission: Permission,
    *,
    mfa: bool = False,
) -> ApiPrincipal:
    """Authenticate, then enforce one rbac permission and, where policy demands it, MFA."""
    principal = bearer_principal(request, tokens)
    rbac_roles = frozenset(Role(role) for role in principal.roles if role in set(Role))
    authorize(Principal(subject_id=principal.subject_id, roles=rbac_roles), permission)
    if mfa and not principal.mfa:
        raise ApiError("FORBIDDEN")
    return principal


def release_principal(principal: ApiPrincipal) -> ReleasePrincipal:
    """The release store's own principal for one privileged change."""
    return ReleasePrincipal(
        subject_id=principal.subject_id, roles=principal.roles, mfa=principal.mfa
    )


__all__ = [
    "ApiPrincipal",
    "TokenDirectory",
    "bearer_principal",
    "release_principal",
    "require_permission",
]
