"""Endpoint registration and approval. No secret value is ever stored or read here."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidState,
    NotFound,
    OptimisticVersionConflict,
)
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.endpoint_policy import (
    EndpointNetworkPolicy,
    RegisteredEndpoint,
    parse_endpoint_url,
    parse_secret_ref,
    required_policy_kind,
)
from polycodebench_core.model_contracts import (
    EndpointNotApproved,
    EndpointPolicyViolation,
    ModelCapabilities,
    ProviderKind,
)
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import audit_event, endpoint_registration

# Native hosted providers must carry a secret reference; only LOCAL may be unauthenticated.
NO_SECRET = "none"


class PostgresEndpointRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(
        self,
        *,
        provider_kind: ProviderKind,
        base_url: str,
        secret_ref: str,
        policy: EndpointNetworkPolicy,
        declared_capabilities: ModelCapabilities,
        registered_by: str,
    ) -> UUID:
        """Create a *pending* registration after syntactic policy validation (no network)."""
        if policy.kind is not required_policy_kind(provider_kind):
            raise EndpointPolicyViolation(
                "local inference needs an explicit internal registration; "
                "hosted providers need a public allowlist registration"
            )
        parsed = parse_endpoint_url(base_url, policy)
        if secret_ref == NO_SECRET:
            if provider_kind is not ProviderKind.LOCAL:
                raise EndpointPolicyViolation(
                    "hosted provider endpoints require a secret reference"
                )
        else:
            parse_secret_ref(secret_ref)
        endpoint_id = uuid4()
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(endpoint_registration).values(
                        id=endpoint_id,
                        provider_kind=provider_kind.value,
                        base_url_ref=parsed.url,
                        secret_ref=secret_ref,
                        network_policy_id=policy.kind.value,
                        network_policy=policy.model_dump(mode="json"),
                        declared_capabilities=declared_capabilities.model_dump(mode="json"),
                        approval_status="pending",
                        capabilities_digest=canonical_digest(
                            declared_capabilities.model_dump(mode="json")
                        ),
                        registered_by=registered_by,
                    )
                )
                _audit(connection, registered_by, "endpoint.register", endpoint_id)
        except DBAPIError as error:
            raise map_database_error(error) from None
        return endpoint_id

    def decide(
        self,
        endpoint_id: UUID,
        *,
        decision: str,
        actor: str,
        reason: str,
        expected_version: int,
        conformance_report: dict[str, Any] | None = None,
    ) -> None:
        if decision not in {"approved", "rejected", "revoked"} or not reason.strip():
            raise InvalidState("endpoint decision and reason are required")
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        select(endpoint_registration)
                        .where(endpoint_registration.c.id == endpoint_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise NotFound()
                if row["row_version"] != expected_version:
                    raise OptimisticVersionConflict()
                allowed = {"pending": {"approved", "rejected"}, "approved": {"revoked"}}
                if decision not in allowed.get(row["approval_status"], set()):
                    raise InvalidState("endpoint registration state does not allow that decision")
                if decision == "approved" and row["provider_kind"] in {
                    ProviderKind.OPENAI_COMPATIBLE.value,
                    ProviderKind.LOCAL.value,
                }:
                    # A nominally compatible endpoint must pass conformance first.
                    if not conformance_report or conformance_report.get("passed") is not True:
                        raise InvalidState("a passing conformance report is required for approval")
                values: dict[str, Any] = {
                    "approval_status": decision,
                    "decision_reason": reason,
                    "row_version": row["row_version"] + 1,
                }
                if decision == "approved":
                    values.update(approved_by=actor, approved_at=now)
                if conformance_report is not None:
                    values["conformance_report"] = conformance_report
                connection.execute(
                    update(endpoint_registration)
                    .where(endpoint_registration.c.id == endpoint_id)
                    .values(**values)
                )
                _audit(connection, actor, f"endpoint.{decision}", endpoint_id)
        except DBAPIError as error:
            raise map_database_error(error) from None

    def get_approved(self, endpoint_id: UUID) -> RegisteredEndpoint:
        """Return an approved registration or raise before any network contact is possible."""
        endpoint, status = self._load(endpoint_id)
        if status != "approved":
            raise EndpointNotApproved("endpoint is not approved for use")
        return endpoint

    def get_for_conformance(self, endpoint_id: UUID) -> tuple[RegisteredEndpoint, str]:
        """Administrator conformance probing may reach a *pending* endpoint, never a rejected
        or revoked one. Gateway calls use ``get_approved`` only."""
        endpoint, status = self._load(endpoint_id)
        if status not in {"pending", "approved"}:
            raise EndpointNotApproved("endpoint was rejected or revoked")
        return endpoint, status

    def _load(self, endpoint_id: UUID) -> tuple[RegisteredEndpoint, str]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(endpoint_registration).where(endpoint_registration.c.id == endpoint_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise EndpointNotApproved("endpoint is not approved for use")
        policy = EndpointNetworkPolicy.model_validate(row["network_policy"], strict=False)
        endpoint = RegisteredEndpoint(
            endpoint_id=row["id"],
            provider_kind=ProviderKind(row["provider_kind"]),
            endpoint=parse_endpoint_url(row["base_url_ref"], policy),
            secret_ref=row["secret_ref"],
            policy=policy,
            declared_capabilities=ModelCapabilities.model_validate(
                row["declared_capabilities"], strict=False
            ),
        )
        return endpoint, str(row["approval_status"])


def _audit(connection: Any, actor: str, action: str, endpoint_id: UUID) -> None:
    connection.execute(
        insert(audit_event).values(
            id=uuid4(),
            actor_subject=actor,
            action=action,
            resource_type="endpoint_registration",
            resource_id=str(endpoint_id),
            request_id=f"endpoint-{uuid4()}",
            details={},
        )
    )
