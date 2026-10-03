"""Bearer-token principals and role authorization (Technical Specification 20.2).

Tokens are resolved through a directory configured at application construction, so every admin
route enforces a server-side role check independent of any UI. Administrator, reviewer and
publisher actions additionally require an MFA-backed session.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from fastapi import Request
from polycodebench_publication.releases import ReleasePrincipal
from polycodebench_services.rbac import Permission, Principal, Role, authorize

from polycodebench_api.errors import ApiError


@dataclass(frozen=True)
class ApiPrincipal:
    """An authenticated caller: OIDC-style subject, granted roles and MFA policy state."""

    subject_id: str
    roles: frozenset[str]
    mfa: bool = False


class TokenDirectory:
    """The API's bearer-token registry; unknown or unknown-format tokens authenticate as nobody."""

    def __init__(self, tokens: Mapping[str, ApiPrincipal]) -> None:
        self._tokens = dict(tokens)

    def resolve(self, token: str) -> ApiPrincipal | None:
        return self._tokens.get(token)


def bearer_principal(request: Request, tokens: TokenDirectory) -> ApiPrincipal:
    """Authenticate the bearer token or reject with one generic unauthenticated response."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError("UNAUTHENTICATED")
    principal = tokens.resolve(token.strip())
    if principal is None:
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
    rbac_roles = frozenset(
        cast(Role, Role(role)) for role in principal.roles if role in set(Role)
    )
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
