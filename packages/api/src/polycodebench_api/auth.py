"""Bearer-token principals and role authorization (Technical Specification 20.2).

Tokens are resolved through a directory configured at application construction, so every admin
route enforces a server-side role check independent of any UI. Administrator, reviewer and
publisher actions additionally require an MFA-backed session.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

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


class TokenDirectory:
    """The API's bearer-token registry; unknown or unknown-format tokens authenticate as nobody.

    Deployments can mount a trusted identity export containing SHA-256 token fingerprints rather
    than token values. The issuer must rotate the file as short-lived account tokens expire.
    """

    def __init__(self, tokens: Mapping[str, ApiPrincipal]) -> None:
        self._tokens = dict(tokens)
        self._fingerprints: dict[str, ApiPrincipal] = {}

    @classmethod
    def from_env(cls) -> TokenDirectory:
        """Load trusted claims from ``PCB_API_IDENTITY_FILE`` without storing bearer values."""
        configured = os.environ.get("PCB_API_IDENTITY_FILE")
        if not configured:
            if os.environ.get("PCB_ENVIRONMENT", "development") in {"staging", "production"}:
                raise RuntimeError("PCB_API_IDENTITY_FILE is required outside development")
            return cls({})
        try:
            identity_path = Path(configured)
            if identity_path.stat().st_size > 4_000_000:
                raise RuntimeError("PCB_API_IDENTITY_FILE exceeds the 4 MB configuration limit")
            document = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("PCB_API_IDENTITY_FILE is unreadable or invalid JSON") from error
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "principals"}
            or document["schema_version"] != 1
            or not isinstance(document["principals"], list)
            or len(document["principals"]) > 10_000
        ):
            raise RuntimeError("PCB_API_IDENTITY_FILE does not match schema version 1")
        allowed_roles = {role.value for role in Role}
        fingerprints: dict[str, ApiPrincipal] = {}
        for item in document["principals"]:
            if not isinstance(item, dict) or set(item) != {"token_sha256", "principal"}:
                raise RuntimeError("PCB_API_IDENTITY_FILE contains an invalid principal row")
            fingerprint = item["token_sha256"]
            claims = item["principal"]
            if (
                not isinstance(fingerprint, str)
                or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)
                or fingerprint in fingerprints
                or not isinstance(claims, dict)
                or set(claims)
                != {"subject_id", "roles", "mfa", "email", "email_verified", "expires_at"}
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
            ):
                raise RuntimeError("PCB_API_IDENTITY_FILE contains invalid or expired claims")
            fingerprints[fingerprint] = ApiPrincipal(
                subject_id=claims["subject_id"],
                roles=frozenset(claims["roles"]),
                mfa=claims["mfa"],
                email=claims["email"],
                email_verified=claims["email_verified"],
                expires_at=claims["expires_at"],
            )
        directory = cls({})
        directory._fingerprints = fingerprints
        return directory

    def resolve(self, token: str) -> ApiPrincipal | None:
        principal = self._tokens.get(token)
        if principal is None:
            fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
            principal = self._fingerprints.get(fingerprint)
        if principal is not None and principal.expires_at is not None:
            if principal.expires_at <= int(datetime.now(UTC).timestamp()):
                return None
        return principal


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
    rbac_roles = frozenset(cast(Role, Role(role)) for role in principal.roles if role in set(Role))
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
