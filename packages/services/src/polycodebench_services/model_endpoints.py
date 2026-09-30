"""Endpoint registration and approval use cases (administrator authority, spec 20.2)."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from polycodebench_core.endpoint_policy import EndpointNetworkPolicy
from polycodebench_core.model_contracts import ModelCapabilities, ProviderKind

from polycodebench_services.rbac import Permission, Principal, authorize


class EndpointRepository(Protocol):
    def register(
        self,
        *,
        provider_kind: ProviderKind,
        base_url: str,
        secret_ref: str,
        policy: EndpointNetworkPolicy,
        declared_capabilities: ModelCapabilities,
        registered_by: str,
    ) -> UUID: ...

    def decide(
        self,
        endpoint_id: UUID,
        *,
        decision: str,
        actor: str,
        reason: str,
        expected_version: int,
        conformance_report: dict[str, Any] | None = None,
    ) -> None: ...


class ModelEndpointService:
    """Only administrators register or decide on endpoints; approval is never implicit."""

    def __init__(self, repository: EndpointRepository) -> None:
        self._repository = repository

    def register(
        self,
        principal: Principal,
        *,
        provider_kind: ProviderKind,
        base_url: str,
        secret_ref: str,
        policy: EndpointNetworkPolicy,
        declared_capabilities: ModelCapabilities,
    ) -> UUID:
        authorize(principal, Permission.ENDPOINT_APPROVE)
        return self._repository.register(
            provider_kind=provider_kind,
            base_url=base_url,
            secret_ref=secret_ref,
            policy=policy,
            declared_capabilities=declared_capabilities,
            registered_by=principal.subject_id,
        )

    def decide(
        self,
        principal: Principal,
        endpoint_id: UUID,
        *,
        decision: str,
        reason: str,
        expected_version: int,
        conformance_report: dict[str, Any] | None = None,
    ) -> None:
        authorize(principal, Permission.ENDPOINT_APPROVE)
        self._repository.decide(
            endpoint_id,
            decision=decision,
            actor=principal.subject_id,
            reason=reason,
            expected_version=expected_version,
            conformance_report=conformance_report,
        )
