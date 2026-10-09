"""Endpoint registration and approval. No secret value is ever stored or read here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    IdempotencyConflict,
    InvalidState,
    NotFound,
    OptimisticVersionConflict,
    PersistenceUnavailable,
)
from polycodebench_core.canonical import canonical_digest, canonical_json_bytes, sha256_bytes
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
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import audit_event, endpoint_registration, idempotency_record

# Native hosted providers must carry a secret reference; only LOCAL may be unauthenticated.
NO_SECRET = "none"
IDEMPOTENCY_TTL = timedelta(hours=24)


class PostgresEndpointRepository:
    def __init__(
        self,
        engine: Engine,
        *,
        database_role: Literal["pcb_endpoint_administrator"] | None = None,
    ) -> None:
        if database_role not in {None, "pcb_endpoint_administrator"}:
            raise ValueError("unsupported database role for endpoint repository")
        self._engine = engine
        self._database_role = database_role

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
        endpoint_id = registration_id or uuid4()
        capabilities = declared_capabilities.model_dump(mode="json")
        policy_document = policy.model_dump(mode="json")
        expected = {
            "provider_kind": provider_kind.value,
            "base_url_ref": parsed.url,
            "secret_ref": secret_ref,
            "network_policy_id": policy.kind.value,
            "network_policy": policy_document,
            "declared_capabilities": capabilities,
            "capabilities_digest": canonical_digest(capabilities),
            "registered_by": registered_by,
        }
        try:
            with self._engine.begin() as connection:
                self._set_database_role(connection)
                created = connection.execute(
                    postgres_insert(endpoint_registration)
                    .values(
                        id=endpoint_id,
                        **expected,
                        approval_status="pending",
                    )
                    .on_conflict_do_nothing(index_elements=[endpoint_registration.c.id])
                )
                if created.rowcount == 0:
                    existing = (
                        connection.execute(
                            select(endpoint_registration).where(
                                endpoint_registration.c.id == endpoint_id
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing is None or any(
                        existing[name] != value for name, value in expected.items()
                    ):
                        raise IdempotencyConflict("endpoint registration key was reused")
                else:
                    _audit(connection, registered_by, "endpoint.register", endpoint_id)
        except DBAPIError as error:
            raise map_database_error(error) from None
        return endpoint_id

    def list_registrations(
        self, *, statuses: tuple[str, ...], limit: int
    ) -> tuple[dict[str, Any], ...]:
        if not 1 <= limit <= 200 or not statuses:
            raise ValueError("endpoint query needs valid statuses and a bounded limit")
        with self._engine.connect() as connection:
            self._set_database_role(connection)
            rows = (
                connection.execute(
                    select(endpoint_registration)
                    .where(endpoint_registration.c.approval_status.in_(statuses))
                    .order_by(endpoint_registration.c.created_at.desc(), endpoint_registration.c.id)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return tuple(_review_row(row) for row in rows)

    def get_registration(self, endpoint_id: UUID) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            self._set_database_role(connection)
            row = (
                connection.execute(
                    select(endpoint_registration).where(endpoint_registration.c.id == endpoint_id)
                )
                .mappings()
                .one_or_none()
            )
        return _review_row(row) if row is not None else None

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
    ) -> None:
        if decision not in {"approved", "rejected", "revoked"} or not reason.strip():
            raise InvalidState("endpoint decision and reason are required")
        now = datetime.now(UTC)
        route = f"POST /v1/admin/model-endpoints/{endpoint_id}/decision"
        response_payload = {"endpoint_id": str(endpoint_id), "status": decision}
        request_digest = sha256_bytes(
            canonical_json_bytes(
                {
                    "endpoint_id": str(endpoint_id),
                    "decision": decision,
                    "reason": reason,
                    "expected_version": expected_version,
                    "conformance_report": conformance_report,
                }
            )
        )
        try:
            with self._engine.begin() as connection:
                self._set_database_role(connection)
                idempotency_id: UUID | None = None
                if request_id is not None:
                    if not request_id or not request_id.isascii() or len(request_id) > 128:
                        raise InvalidState("endpoint decision idempotency key is invalid")
                    idempotency_id = uuid4()
                    claim = connection.execute(
                        postgres_insert(idempotency_record)
                        .values(
                            id=idempotency_id,
                            subject=actor,
                            route=route,
                            key=request_id,
                            request_digest=request_digest,
                            state="in_progress",
                            expires_at=now + IDEMPOTENCY_TTL,
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                idempotency_record.c.subject,
                                idempotency_record.c.route,
                                idempotency_record.c.key,
                            ]
                        )
                        .returning(idempotency_record.c.id)
                    ).scalar_one_or_none()
                    if claim is None:
                        existing = (
                            connection.execute(
                                select(idempotency_record)
                                .where(
                                    idempotency_record.c.subject == actor,
                                    idempotency_record.c.route == route,
                                    idempotency_record.c.key == request_id,
                                )
                                .with_for_update()
                            )
                            .mappings()
                            .one_or_none()
                        )
                        if existing is None:
                            raise PersistenceUnavailable()
                        if existing["expires_at"] <= now:
                            connection.execute(
                                delete(idempotency_record).where(
                                    idempotency_record.c.id == existing["id"]
                                )
                            )
                            claim = connection.execute(
                                postgres_insert(idempotency_record)
                                .values(
                                    id=idempotency_id,
                                    subject=actor,
                                    route=route,
                                    key=request_id,
                                    request_digest=request_digest,
                                    state="in_progress",
                                    expires_at=now + IDEMPOTENCY_TTL,
                                )
                                .on_conflict_do_nothing()
                                .returning(idempotency_record.c.id)
                            ).scalar_one_or_none()
                            if claim is None:
                                raise PersistenceUnavailable()
                        else:
                            if existing["request_digest"] != request_digest:
                                raise IdempotencyConflict()
                            if existing["state"] != "completed":
                                raise PersistenceUnavailable()
                            if existing["response_payload"] != response_payload:
                                raise PersistenceUnavailable()
                            return
                    if claim is None:
                        raise PersistenceUnavailable()
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
                _audit(
                    connection,
                    actor,
                    f"endpoint.{decision}",
                    endpoint_id,
                    request_id=request_id,
                )
                if idempotency_id is not None:
                    connection.execute(
                        update(idempotency_record)
                        .where(idempotency_record.c.id == idempotency_id)
                        .values(
                            state="completed",
                            response_code=200,
                            response_payload=response_payload,
                        )
                    )
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
            self._set_database_role(connection)
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

    def _set_database_role(self, connection: Connection) -> None:
        if self._database_role is not None:
            # The value is constrained to this module's fixed compile-time role allowlist.
            connection.exec_driver_sql(f"SET LOCAL ROLE {self._database_role}")


def _audit(
    connection: Any,
    actor: str,
    action: str,
    endpoint_id: UUID,
    *,
    request_id: str | None = None,
) -> None:
    connection.execute(
        insert(audit_event).values(
            id=uuid4(),
            actor_subject=actor,
            action=action,
            resource_type="endpoint_registration",
            resource_id=str(endpoint_id),
            request_id=request_id or f"endpoint-{uuid4()}",
            details={},
        )
    )


def _review_row(row: Any) -> dict[str, Any]:
    return {
        "endpoint_registration_id": str(row["id"]),
        "provider_kind": row["provider_kind"],
        "base_url": row["base_url_ref"],
        "secret_configured": row["secret_ref"] != NO_SECRET,
        "network_policy_id": row["network_policy_id"],
        "approval_status": row["approval_status"],
        "capabilities_digest": row["capabilities_digest"],
        "registered_by": row["registered_by"],
        "network_policy": row["network_policy"],
        "declared_capabilities": row["declared_capabilities"],
        "conformance_report": row["conformance_report"],
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "decision_reason": row["decision_reason"],
        "row_version": row["row_version"],
        "created_at": row["created_at"],
    }
