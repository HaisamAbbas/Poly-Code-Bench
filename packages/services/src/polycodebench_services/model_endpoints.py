"""Endpoint registration and approval use cases (administrator authority, spec 20.2)."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from polycodebench_core.application_errors import NotFound
from polycodebench_core.endpoint_policy import EndpointNetworkPolicy, RegisteredEndpoint
from polycodebench_core.model_contracts import ModelCapabilities, ProviderKind

from polycodebench_services.rbac import Permission, Principal, authorize


class EndpointRepository(Protocol):
    def register(
        self,
        *,
        registration_id: UUID | None = None,
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
        request_id: str | None = None,
    ) -> None: ...

    def get_approved(self, endpoint_id: UUID) -> RegisteredEndpoint: ...

    def get_registration(self, endpoint_id: UUID) -> dict[str, Any] | None: ...

    def list_registrations(
        self, *, statuses: tuple[str, ...], limit: int
    ) -> tuple[dict[str, Any], ...]: ...


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
        registration_id: UUID | None = None,
    ) -> UUID:
        authorize(principal, Permission.ENDPOINT_APPROVE)
        values: dict[str, Any] = dict(
            provider_kind=provider_kind,
            base_url=base_url,
            secret_ref=secret_ref,
            policy=policy,
            declared_capabilities=declared_capabilities,
            registered_by=principal.subject_id,
        )
        if registration_id is not None:
            values["registration_id"] = registration_id
        return self._repository.register(**values)

    def decide(
        self,
        principal: Principal,
        endpoint_id: UUID,
        *,
        decision: str,
        reason: str,
        expected_version: int,
        conformance_report: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        authorize(principal, Permission.ENDPOINT_APPROVE)
        self._repository.decide(
            endpoint_id,
            decision=decision,
            actor=principal.subject_id,
            reason=reason,
            expected_version=expected_version,
            conformance_report=conformance_report,
            request_id=request_id,
        )

    def get_approved(self, endpoint_id: UUID) -> RegisteredEndpoint:
        """Resolve an approved endpoint without performing network I/O."""
        return self._repository.get_approved(endpoint_id)

    def get_registration(self, principal: Principal, endpoint_id: UUID) -> dict[str, Any]:
        authorize(principal, Permission.ENDPOINT_APPROVE)
        result = self._repository.get_registration(endpoint_id)
        if result is None:
            raise NotFound()
        return result

    def list_registrations(
        self, principal: Principal, *, statuses: tuple[str, ...], limit: int
    ) -> tuple[dict[str, Any], ...]:
        authorize(principal, Permission.ENDPOINT_APPROVE)
        return self._repository.list_registrations(statuses=statuses, limit=limit)
