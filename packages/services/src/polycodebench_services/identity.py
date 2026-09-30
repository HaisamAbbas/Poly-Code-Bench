"""Identity use cases consume an already verified authentication subject."""

from __future__ import annotations

from typing import Protocol

from polycodebench_services.errors import NotFound
from polycodebench_services.rbac import Permission, Principal, Role, authorize


class RoleRepository(Protocol):
    def roles_for_subject(self, subject_id: str) -> tuple[str, ...]: ...

    def grant_role(
        self,
        *,
        subject_id: str,
        role: str,
        actor_subject: str,
        request_id: str,
        expected_version: int | None = None,
    ) -> int: ...

    def revoke_role(
        self,
        *,
        subject_id: str,
        role: str,
        actor_subject: str,
        request_id: str,
        expected_version: int,
    ) -> int: ...


class IdentityService:
    def __init__(self, repository: RoleRepository) -> None:
        self._repository = repository

    def principal_for_verified_subject(self, subject_id: str) -> Principal:
        roles = frozenset(Role(role) for role in self._repository.roles_for_subject(subject_id))
        if not roles:
            raise NotFound("identity has no active PolyCodeBench role")
        return Principal(subject_id=subject_id, roles=roles)

    def grant_role(
        self,
        actor: Principal,
        target_subject: str,
        role: Role,
        request_id: str,
        *,
        expected_version: int | None = None,
    ) -> int:
        authorize(actor, Permission.ROLE_ASSIGN)
        return self._repository.grant_role(
            subject_id=target_subject,
            role=role.value,
            actor_subject=actor.subject_id,
            request_id=request_id,
            expected_version=expected_version,
        )

    def revoke_role(
        self,
        actor: Principal,
        target_subject: str,
        role: Role,
        request_id: str,
        *,
        expected_version: int,
    ) -> int:
        authorize(actor, Permission.ROLE_ASSIGN)
        return self._repository.revoke_role(
            subject_id=target_subject,
            role=role.value,
            actor_subject=actor.subject_id,
            request_id=request_id,
            expected_version=expected_version,
        )
