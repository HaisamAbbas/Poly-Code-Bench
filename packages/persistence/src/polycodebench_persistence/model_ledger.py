"""Call intents, deliveries, usage and the hierarchical budget ledger.

Every balance change is a row in ``accounting_entry`` moving an amount between buckets
(``none`` is outside the ledger). Cached balances on ``budget_account``/``budget_resource``
are updated in the same transaction and can be re-derived with ``verify_balances``.

Lock order, everywhere: advisory key lock -> call_intent row -> call_delivery row -> account
rows root-first (campaign, optional audit-run ancestor, run, attempt). One order means no
deadlocks between a caller starting a retry and the worker settling the previous delivery.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from polycodebench_core.application_errors import (
    InvalidState,
    NotFound,
    PersistenceConflict,
)
from polycodebench_core.model_contracts import (
    BudgetAccountMissing,
    BudgetExhausted,
    CallScope,
    FailureKind,
    ReservationPlan,
    ScopeNotActive,
    TransportFailure,
    Usage,
)
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError

from polycodebench_persistence.errors import map_database_error
from polycodebench_persistence.models import (
    accounting_entry,
    attempt,
    audit_run,
    budget_account,
    budget_reservation,
    budget_resource,
    call_delivery,
    call_intent,
    campaign,
    evaluation,
    run,
    usage_record,
)

Bucket = Literal["none", "reserved_open", "uncertain_committed", "spent_confirmed"]
RESOURCES = ("money", "turns", "input_tokens", "output_tokens")
LIVE_BUCKETS = ("reserved_open", "uncertain_committed", "spent_confirmed")
DEFAULT_MAX_DELIVERIES = 3
LATE_CODE = "late_response_not_consumed"
_BUCKET_COLUMN = {
    "reserved_open": "reserved_open",
    "uncertain_committed": "uncertain_committed",
    "spent_confirmed": "spent_confirmed",
}


@dataclass(frozen=True)
class CallHandle:
    """Result of ``begin_call``: dispatch a delivery, or consume what is already recorded."""

    action: Literal["dispatch", "stored", "failed", "recover_raw"]
    intent_id: UUID
    delivery_id: UUID | None
    delivery_index: int
    raw_artifact_id: UUID | None = None
    normalized_artifact_id: UUID | None = None
    provider_request_id: str | None = None
    failure_code: str | None = None
    prior_ambiguous_deliveries: int = 0


@dataclass(frozen=True)
class SettlementResult:
    state: Literal["settled", "uncertain"]
    charged_micro_usd: int | None
    retained_micro_usd: int
    revision: int
    # False when an earlier-arriving response already won: this one is evidence only.
    consumed: bool = True


class PostgresModelLedger:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ accounts

    def ensure_account(
        self,
        *,
        scope_kind: Literal["campaign", "run", "attempt", "evaluation", "audit_run"],
        scope_id: str,
        hard_limit_micro_usd: int,
        parent_account_id: UUID | None = None,
        resource_limits: Mapping[str, int] | None = None,
    ) -> UUID:
        """Idempotently open an account; differing limits for an existing scope conflict."""
        limits = dict(resource_limits or {})
        if not set(limits) <= {"turns", "input_tokens", "output_tokens"} or any(
            value < 0 for value in limits.values()
        ):
            raise InvalidState("resource limits are invalid")
        try:
            with self._engine.begin() as connection:
                existing = (
                    connection.execute(
                        select(budget_account).where(
                            budget_account.c.scope_kind == scope_kind,
                            budget_account.c.scope_id == scope_id,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    current = {
                        row["resource"]: row["hard_limit"]
                        for row in connection.execute(
                            select(budget_resource).where(
                                budget_resource.c.account_id == existing["id"]
                            )
                        ).mappings()
                    }
                    if (
                        existing["hard_limit_micro_usd"] != hard_limit_micro_usd
                        or existing["parent_account_id"] != parent_account_id
                        or current != limits
                    ):
                        raise PersistenceConflict("account already exists with different limits")
                    return UUID(str(existing["id"]))
                account_id = uuid4()
                connection.execute(
                    insert(budget_account).values(
                        id=account_id,
                        scope_kind=scope_kind,
                        scope_id=scope_id,
                        parent_account_id=parent_account_id,
                        hard_limit_micro_usd=hard_limit_micro_usd,
                    )
                )
                for resource, limit in limits.items():
                    connection.execute(
                        insert(budget_resource).values(
                            account_id=account_id, resource=resource, hard_limit=limit
                        )
                    )
                return account_id
        except DBAPIError as error:
            raise map_database_error(error) from None

    def _chain(self, connection: Connection, scope: CallScope) -> list[Any]:
        """Accounts applicable to a scope, locked root-first."""
        leaf = (
            connection.execute(
                select(budget_account.c.id, budget_account.c.parent_account_id).where(
                    budget_account.c.scope_kind == scope.kind,
                    budget_account.c.scope_id == str(scope.scope_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        if leaf is None:
            raise BudgetAccountMissing("no budget account is open for this scope")
        ids = [leaf["id"]]
        parent = leaf["parent_account_id"]
        while parent is not None:
            ids.append(parent)
            parent = connection.execute(
                select(budget_account.c.parent_account_id).where(budget_account.c.id == parent)
            ).scalar_one()
        accounts: list[Any] = []
        for account_id in reversed(ids):  # campaign -> run -> attempt: stable lock order
            accounts.append(
                connection.execute(
                    select(budget_account)
                    .where(budget_account.c.id == account_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
        return accounts

    # --------------------------------------------------------------- call intents

    def begin_call(
        self,
        *,
        scope: CallScope,
        logical_call_key: str,
        request_digest: str,
        model_config_id: UUID,
        request_artifact_id: UUID | None,
        price_snapshot: dict[str, Any],
        plan: ReservationPlan,
        max_deliveries: int = DEFAULT_MAX_DELIVERIES,
        in_flight_grace: timedelta = timedelta(minutes=10),
    ) -> CallHandle:
        """Persist the intent and reserve exposure atomically, or return recorded state.

        A recorded response or a definitive failure is returned without a new delivery, so a
        restarted controller can never obtain a second sample for the same logical call.
        """
        if not logical_call_key or len(logical_call_key) > 255:
            raise InvalidState("logical call key is invalid")
        try:
            self._retire_stale_in_flight(scope, logical_call_key, in_flight_grace)
            with self._engine.begin() as connection:
                diagnostic_audit_run_id = self._diagnostic_audit_context(connection, scope)
                intent = self._lock_intent(connection, scope, logical_call_key)
                if intent is None:
                    self._chain(connection, scope)  # no budget account: refuse before any insert
                    intent_id = uuid4()
                    connection.execute(
                        insert(call_intent).values(
                            id=intent_id,
                            **{f"{scope.kind}_id": scope.scope_id},
                            diagnostic_audit_run_id=diagnostic_audit_run_id,
                            logical_call_key=logical_call_key,
                            request_digest=request_digest,
                            model_config_id=model_config_id,
                            price_snapshot=price_snapshot,
                            request_artifact_id=request_artifact_id,
                            state="reserved",
                        )
                    )
                    return self._new_delivery(connection, scope, intent_id, 0, plan, 0)
                if intent["diagnostic_audit_run_id"] != diagnostic_audit_run_id:
                    raise PersistenceConflict(
                        "model call replay changed its diagnostic audit metadata"
                    )
                self._require_same_call(intent, request_digest, model_config_id)
                deliveries = self._deliveries(connection, intent["id"])
                recorded = self._recorded(intent, deliveries)
                if recorded is not None:
                    return recorded
                persisted = next(
                    (
                        d
                        for d in deliveries
                        if d["status"] == "dispatching" and d["raw_response_artifact_id"]
                    ),
                    None,
                )
                if persisted is not None:
                    # The provider answered and the bytes are durable; settle them, never re-ask.
                    return CallHandle(
                        "recover_raw",
                        intent["id"],
                        persisted["id"],
                        persisted["delivery_index"],
                        raw_artifact_id=persisted["raw_response_artifact_id"],
                        provider_request_id=persisted["provider_request_id"],
                    )
                if any(d["status"] == "dispatching" for d in deliveries):
                    raise InvalidState("a delivery for this call is still in flight")
                last = deliveries[-1] if deliveries else None
                if len(deliveries) >= max_deliveries:
                    return self._failed_handle(intent, last, "exhausted")
                ambiguous = sum(1 for d in deliveries if d["status"] == "ambiguous")
                return self._new_delivery(
                    connection, scope, intent["id"], len(deliveries), plan, ambiguous
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    def recorded_call(
        self,
        scope: CallScope,
        logical_call_key: str,
        request_digest: str,
        model_config_id: UUID,
    ) -> CallHandle | None:
        """Read-only: the stored response or definitive failure for a logical call, if any."""
        with self._engine.connect() as connection:
            column = self._scope_column(scope)
            intent = (
                connection.execute(
                    select(call_intent).where(
                        column == scope.scope_id, call_intent.c.logical_call_key == logical_call_key
                    )
                )
                .mappings()
                .one_or_none()
            )
            if intent is None:
                return None
            self._require_same_call(intent, request_digest, model_config_id)
            return self._recorded(intent, self._deliveries(connection, intent["id"]))

    @staticmethod
    def _require_same_call(intent: Any, request_digest: str, model_config_id: UUID) -> None:
        """A logical key names one request to one model configuration, nothing else."""
        if intent["request_digest"] != request_digest:
            raise PersistenceConflict("logical call key was reused for a different request")
        if intent["model_config_id"] != model_config_id:
            raise PersistenceConflict("logical call key was reused with a different model config")

    @staticmethod
    def _deliveries(connection: Connection, intent_id: UUID) -> list[Any]:
        return list(
            connection.execute(
                select(call_delivery)
                .where(call_delivery.c.intent_id == intent_id)
                .order_by(call_delivery.c.delivery_index)
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _failed_handle(intent: Any, last: Any, default_code: str) -> CallHandle:
        return CallHandle(
            "failed",
            intent["id"],
            last["id"] if last else None,
            last["delivery_index"] if last else 0,
            raw_artifact_id=last["raw_response_artifact_id"] if last else None,
            provider_request_id=last["provider_request_id"] if last else None,
            failure_code=(last["failure_code"] if last else None) or default_code,
        )

    def _recorded(self, intent: Any, deliveries: list[Any]) -> CallHandle | None:
        responded = next((d for d in deliveries if d["status"] == "responded"), None)
        if responded is not None:
            return CallHandle(
                "stored",
                intent["id"],
                responded["id"],
                responded["delivery_index"],
                raw_artifact_id=responded["raw_response_artifact_id"],
                normalized_artifact_id=responded["normalized_response_artifact_id"],
                provider_request_id=responded["provider_request_id"],
            )
        if intent["state"] == "failed":
            return self._failed_handle(intent, deliveries[-1] if deliveries else None, "failed")
        return None

    def _lock_intent(self, connection: Connection, scope: CallScope, key: str) -> Any:
        """Serialize callers of one logical key, then lock the intent row."""
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"pcb.call:{scope.kind}:{scope.scope_id}:{key}"},
        )
        column = self._scope_column(scope)
        return (
            connection.execute(
                select(call_intent)
                .where(column == scope.scope_id, call_intent.c.logical_call_key == key)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )

    @staticmethod
    def _scope_column(scope: CallScope) -> Any:
        return {
            "attempt": call_intent.c.attempt_id,
            "evaluation": call_intent.c.evaluation_id,
            "audit_run": call_intent.c.audit_run_id,
        }[scope.kind]

    def _new_delivery(
        self,
        connection: Connection,
        scope: CallScope,
        intent_id: UUID,
        delivery_index: int,
        plan: ReservationPlan,
        prior_ambiguous: int,
    ) -> CallHandle:
        self._require_active_scope(connection, scope)
        accounts = self._chain(connection, scope)
        delivery_id = uuid4()
        connection.execute(
            insert(call_delivery).values(
                id=delivery_id,
                intent_id=intent_id,
                delivery_index=delivery_index,
                dispatched_at=datetime.now(UTC),
                status="dispatching",
            )
        )
        for account in accounts:
            limits = {
                row["resource"]: row
                for row in connection.execute(
                    select(budget_resource)
                    .where(budget_resource.c.account_id == account["id"])
                    .with_for_update()
                ).mappings()
            }
            # A logical call is one model turn however many deliveries it needs: a retry only
            # reserves a turn when no earlier delivery of this call still holds one.
            holds_turn = self._intent_turn_exposure(connection, account["id"], intent_id) > 0
            amounts = {
                "money": plan.money_micro_usd,
                "input_tokens": plan.input_tokens,
                "output_tokens": plan.output_tokens,
                "turns": 0 if holds_turn else plan.turns,
            }
            committed = (
                account["spent_confirmed"]
                + account["reserved_open"]
                + account["uncertain_committed"]
            )
            if committed + amounts["money"] > account["hard_limit_micro_usd"]:
                raise BudgetExhausted("monetary budget cannot cover this call's exposure")
            for resource, row in limits.items():
                used = row["spent_confirmed"] + row["reserved_open"] + row["uncertain_committed"]
                if used + amounts[resource] > row["hard_limit"]:
                    raise BudgetExhausted(f"{resource} budget cannot cover this call")
            connection.execute(
                insert(budget_reservation).values(
                    account_id=account["id"],
                    call_intent_id=intent_id,
                    delivery_index=delivery_index,
                    amount_micro_usd=amounts["money"],
                    state="open",
                )
            )
            for resource in RESOURCES:
                if resource != "money" and resource not in limits:
                    continue
                self._move(
                    connection,
                    account["id"],
                    resource,
                    "none",
                    "reserved_open",
                    amounts[resource],
                    intent_id,
                    delivery_id,
                    "reservation",
                    f"reserve delivery {delivery_index}",
                )
        connection.execute(
            update(call_intent).where(call_intent.c.id == intent_id).values(state="dispatching")
        )
        return CallHandle(
            "dispatch",
            intent_id,
            delivery_id,
            delivery_index,
            prior_ambiguous_deliveries=prior_ambiguous,
        )

    @staticmethod
    def _require_active_scope(connection: Connection, scope: CallScope) -> None:
        """Spec 8.3 step 1: a cancelled or finished scope cannot spend. The share lock orders
        this check against a concurrent cancellation."""
        if scope.kind == "audit_run":
            if not PostgresModelLedger._audit_run_dispatchable(connection, scope.scope_id):
                raise ScopeNotActive("audit run is not authorized for model-call dispatch")
            return
        if scope.kind == "attempt":
            row = (
                connection.execute(
                    select(run.c.purpose, run.c.audit_run_id)
                    .select_from(attempt.join(run, attempt.c.run_id == run.c.id))
                    .where(attempt.c.id == scope.scope_id)
                    .with_for_update(read=True, of=run)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ScopeNotActive("the attempt is not active")
            if row["purpose"] == "audit_diagnostic":
                if not PostgresModelLedger._audit_run_dispatchable(
                    connection, row["audit_run_id"]
                ):
                    raise ScopeNotActive(
                        "diagnostic calls require an authorized scanning audit run"
                    )
            state = connection.execute(
                select(attempt.c.state)
                .where(attempt.c.id == scope.scope_id)
                .with_for_update(read=True)
            ).scalar_one_or_none()
            if state not in {"queued", "running"}:
                raise ScopeNotActive("the attempt is not active")
            return
        table = evaluation
        state = connection.execute(
            select(table.c.state).where(table.c.id == scope.scope_id).with_for_update(read=True)
        ).scalar_one_or_none()
        if state not in {"queued", "running"}:
            raise ScopeNotActive("the attempt or evaluation is not active")

    @staticmethod
    def _audit_run_dispatchable(connection: Connection, audit_run_id: UUID) -> bool:
        row = (
            connection.execute(
                select(
                    audit_run.c.state,
                    audit_run.c.dispatch_authorized,
                    audit_run.c.campaign_id,
                )
                .where(audit_run.c.id == audit_run_id)
                .with_for_update(read=True)
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["state"] != "scanning" or not row["dispatch_authorized"]:
            return False
        if row["campaign_id"] is None:
            return True
        status = connection.execute(
            select(campaign.c.status)
            .where(campaign.c.id == row["campaign_id"])
            .with_for_update(read=True)
        ).scalar_one_or_none()
        return status not in {"cancelled", "cancelling", "failed"}

    @staticmethod
    def _diagnostic_audit_context(connection: Connection, scope: CallScope) -> UUID | None:
        if scope.kind != "attempt":
            return None
        row = (
            connection.execute(
                select(run.c.purpose, run.c.audit_run_id)
                .select_from(attempt.join(run, attempt.c.run_id == run.c.id))
                .where(attempt.c.id == scope.scope_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ScopeNotActive("the attempt is not active")
        if row["purpose"] == "audit_diagnostic":
            audit_run_id = row["audit_run_id"]
            if audit_run_id is None:
                raise ScopeNotActive("diagnostic attempt is missing its audit reference")
            return cast(UUID, audit_run_id)
        return None

    def _retire_stale_in_flight(self, scope: CallScope, key: str, grace: timedelta) -> None:
        """A delivery still 'dispatching' after a crash is ambiguous: it may have been sent."""
        with self._engine.begin() as connection:
            intent = self._lock_intent(connection, scope, key)
            if intent is None:
                return
            stale = (
                connection.execute(
                    select(call_delivery.c.id).where(
                        call_delivery.c.intent_id == intent["id"],
                        call_delivery.c.status == "dispatching",
                        call_delivery.c.raw_response_artifact_id.is_(None),
                        call_delivery.c.dispatched_at < datetime.now(UTC) - grace,
                    )
                )
                .scalars()
                .all()
            )
            for delivery_id in stale:
                self._record_ambiguous(
                    connection, scope, intent["id"], delivery_id, "controller_lost_delivery", None
                )
            if stale:
                self._refresh_intent_state(connection, intent["id"])

    # ----------------------------------------------------------------- outcomes

    def attach_raw_response(
        self, *, delivery_id: UUID, raw_artifact_id: UUID, provider_request_id: str | None
    ) -> bool:
        """Bind durable raw bytes to the in-flight delivery before anything parses them.

        Returns False when the delivery was already retired as ambiguous; the caller then
        settles through ``settle_response``, which records the bytes as a late response.
        """
        try:
            with self._engine.begin() as connection:
                changed = connection.execute(
                    update(call_delivery)
                    .where(
                        call_delivery.c.id == delivery_id,
                        call_delivery.c.status == "dispatching",
                        call_delivery.c.raw_response_artifact_id.is_(None),
                    )
                    .values(
                        raw_response_artifact_id=raw_artifact_id,
                        provider_request_id=provider_request_id,
                    )
                ).rowcount
                return changed == 1
        except DBAPIError as error:
            raise map_database_error(error) from None

    def settle_response(
        self,
        *,
        delivery_id: UUID,
        provider_request_id: str | None,
        raw_artifact_id: UUID,
        normalized_artifact_id: UUID,
        usage: Usage,
        usage_reliable: bool,
        estimated_cost_micro_usd: int | None,
        estimate_basis: str,
    ) -> SettlementResult:
        """Record a persisted response and settle. Missing or implausible usage never
        becomes zero; the first response to arrive is the only one the controller consumes."""
        try:
            with self._engine.begin() as connection:
                intent, delivery = self._lock_call(connection, delivery_id)
                scope = self._scope_of(intent)
                if delivery["status"] == "responded":
                    raise InvalidState("delivery already has a recorded response")
                known = (
                    usage.complete
                    and usage_reliable
                    and (
                        estimated_cost_micro_usd is not None
                        or usage.reported_cost_micro_usd is not None
                    )
                )
                cost = (
                    usage.reported_cost_micro_usd
                    if usage.reported_cost_micro_usd is not None
                    else estimated_cost_micro_usd
                )
                availability: dict[str, Any] = {
                    **usage.availability(),
                    "estimate_basis": estimate_basis,
                    "usage_trusted": usage_reliable,
                }
                others_responded = any(
                    d["status"] == "responded" and d["id"] != delivery_id
                    for d in self._deliveries(connection, intent["id"])
                )
                if others_responded:
                    return self._record_late(
                        connection,
                        scope,
                        intent,
                        delivery,
                        raw_artifact_id,
                        provider_request_id,
                        usage,
                        known,
                        cost,
                        availability,
                        estimated_cost_micro_usd,
                    )
                source: Bucket = (
                    "reserved_open"
                    if delivery["status"] == "dispatching"
                    else "uncertain_committed"
                )
                values: dict[str, Any] = {
                    "status": "responded",
                    "provider_request_id": delivery["provider_request_id"] or provider_request_id,
                    "raw_response_artifact_id": delivery["raw_response_artifact_id"]
                    or raw_artifact_id,
                    "normalized_response_artifact_id": normalized_artifact_id,
                }
                if delivery["responded_at"] is None:
                    values["responded_at"] = datetime.now(UTC)
                connection.execute(
                    update(call_delivery).where(call_delivery.c.id == delivery_id).values(**values)
                )
                revision = self._next_revision(connection, delivery_id)
                connection.execute(
                    insert(usage_record).values(
                        delivery_id=delivery_id,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        reasoning_tokens=usage.reasoning_tokens,
                        usage_available=availability,
                        source="provider_reported" if usage.complete else "unavailable",
                        actual_cost_micro_usd=usage.reported_cost_micro_usd,
                        estimated_cost_micro_usd=estimated_cost_micro_usd if known else None,
                        settlement_revision=revision,
                    )
                )
                charged, retained, _ = self._settle_exposure(
                    connection,
                    scope,
                    intent["id"],
                    delivery_id,
                    cost if known else None,
                    usage if known else None,
                    reason="settle provider response",
                    source_bucket=source,
                )
                self._consume_earlier_turns(connection, scope, intent["id"], delivery_id)
                self._ensure_turn_spent(connection, scope, intent["id"], delivery_id)
                self._refresh_intent_state(connection, intent["id"])
                self._mark_reservations(
                    connection,
                    intent["id"],
                    delivery["delivery_index"],
                    "settled" if known else "uncertain",
                )
                return SettlementResult(
                    "settled" if known else "uncertain", charged, retained, revision
                )
        except DBAPIError as error:
            raise map_database_error(error) from None

    def settle_failure(
        self,
        *,
        delivery_id: UUID,
        failure: TransportFailure,
        body_artifact_id: UUID | None,
    ) -> Literal["released", "retained"]:
        """Definitive failures release exposure; ambiguous ones retain it (never auto-release)."""
        try:
            with self._engine.begin() as connection:
                intent, delivery = self._lock_call(connection, delivery_id)
                scope = self._scope_of(intent)
                if delivery["status"] != "dispatching":
                    raise InvalidState("delivery is not in flight")
                if failure.kind is FailureKind.AMBIGUOUS:
                    self._record_ambiguous(
                        connection,
                        scope,
                        intent["id"],
                        delivery_id,
                        failure.code,
                        body_artifact_id,
                        failure.provider_request_id,
                    )
                    self._refresh_intent_state(connection, intent["id"])
                    return "retained"
                connection.execute(
                    update(call_delivery)
                    .where(call_delivery.c.id == delivery_id)
                    .values(
                        status="failed",
                        responded_at=datetime.now(UTC),
                        failure_code=failure.code,
                        provider_request_id=failure.provider_request_id,
                        raw_response_artifact_id=body_artifact_id,
                    )
                )
                self._release_exposure(
                    connection, scope, intent["id"], delivery_id, "definitive failure"
                )
                self._mark_reservations(
                    connection, intent["id"], delivery["delivery_index"], "released"
                )
                self._refresh_intent_state(connection, intent["id"], terminal=not failure.retryable)
                return "released"
        except DBAPIError as error:
            raise map_database_error(error) from None

    def reconcile(
        self,
        *,
        delivery_id: UUID,
        actor: str,
        evidence: str,
        unbilled: bool = False,
        cost_micro_usd: int | None = None,
        usage: Usage | None = None,
    ) -> SettlementResult:
        """Append-only late resolution of retained exposure with cited evidence.

        Each resource can be resolved once: a second attempt to book a cost for exposure that
        is already resolved is refused rather than charged again.
        """
        if not actor or not evidence.strip():
            raise InvalidState("reconciliation requires an actor and evidence")
        billed = cost_micro_usd is not None or usage is not None
        if unbilled == billed:
            raise InvalidState("state exactly one of unbilled, or billed cost/usage evidence")
        try:
            with self._engine.begin() as connection:
                intent, _ = self._lock_call(connection, delivery_id)
                scope = self._scope_of(intent)
                if not self._has_uncertain(connection, delivery_id):
                    raise InvalidState("delivery has no retained uncertain exposure")
                revision = self._next_revision(connection, delivery_id)
                connection.execute(
                    insert(usage_record).values(
                        delivery_id=delivery_id,
                        input_tokens=usage.input_tokens if usage else None,
                        output_tokens=usage.output_tokens if usage else None,
                        reasoning_tokens=usage.reasoning_tokens if usage else None,
                        usage_available={
                            **(usage.availability() if usage else {}),
                            "evidence": evidence[:500],
                            "actor": actor,
                        },
                        source="reconciliation",
                        actual_cost_micro_usd=0 if unbilled else cost_micro_usd,
                        estimated_cost_micro_usd=None,
                        settlement_revision=revision,
                    )
                )
                if unbilled:
                    retained = self._release_exposure(
                        connection,
                        scope,
                        intent["id"],
                        delivery_id,
                        f"reconciled unbilled: {evidence}",
                    )
                    charged: int | None = 0
                else:
                    charged, retained, skipped = self._settle_exposure(
                        connection,
                        scope,
                        intent["id"],
                        delivery_id,
                        cost_micro_usd,
                        usage,
                        reason=f"reconciled: {evidence}",
                        source_bucket="uncertain_committed",
                    )
                    if cost_micro_usd is not None and "money" in skipped:
                        raise InvalidState("this delivery's cost was already resolved")
                    self._ensure_turn_spent(connection, scope, intent["id"], delivery_id)
                outcome = self._refresh_intent_state(connection, intent["id"])
                return SettlementResult(outcome, charged, retained, revision)
        except DBAPIError as error:
            raise map_database_error(error) from None

    # ------------------------------------------------------------------ queries

    def verify_balances(self, account_id: UUID) -> list[str]:
        """Recompute every bucket from the immutable ledger; empty list means consistent."""
        problems: list[str] = []
        with self._engine.connect() as connection:
            account = (
                connection.execute(select(budget_account).where(budget_account.c.id == account_id))
                .mappings()
                .one_or_none()
            )
            if account is None:
                raise NotFound()
            resources = {
                row["resource"]: row
                for row in connection.execute(
                    select(budget_resource).where(budget_resource.c.account_id == account_id)
                ).mappings()
            }
            for resource in RESOURCES:
                derived = self._derived(connection, account_id, resource)
                cached = account if resource == "money" else resources.get(resource)
                if cached is None:
                    continue
                for bucket in _BUCKET_COLUMN:
                    if cached[bucket] != derived[bucket]:
                        problems.append(
                            f"{resource}.{bucket}: cached {cached[bucket]} "
                            f"!= ledger {derived[bucket]}"
                        )
        return problems

    def account_summary(self, account_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            account = (
                connection.execute(select(budget_account).where(budget_account.c.id == account_id))
                .mappings()
                .one_or_none()
            )
            if account is None:
                raise NotFound()
            summary: dict[str, Any] = {
                "money_micro_usd": {
                    "limit": account["hard_limit_micro_usd"],
                    "spent_confirmed": account["spent_confirmed"],
                    "reserved_open": account["reserved_open"],
                    "uncertain_committed": account["uncertain_committed"],
                }
            }
            for row in connection.execute(
                select(budget_resource).where(budget_resource.c.account_id == account_id)
            ).mappings():
                summary[row["resource"]] = {
                    "limit": row["hard_limit"],
                    "spent_confirmed": row["spent_confirmed"],
                    "reserved_open": row["reserved_open"],
                    "uncertain_committed": row["uncertain_committed"],
                }
            return summary

    def unresolved_exposure(self, account_id: UUID) -> list[dict[str, Any]]:
        """Deliveries whose money exposure is still retained as uncertain."""
        with self._engine.connect() as connection:
            deliveries = (
                connection.execute(
                    select(accounting_entry.c.delivery_id)
                    .where(
                        accounting_entry.c.account_id == account_id,
                        accounting_entry.c.delivery_id.is_not(None),
                    )
                    .distinct()
                )
                .scalars()
                .all()
            )
            result = []
            for delivery_id in deliveries:
                amount = self._delivery_bucket(
                    connection, account_id, "money", delivery_id, "uncertain_committed"
                )
                if amount > 0:
                    result.append({"delivery_id": delivery_id, "uncertain_micro_usd": amount})
            return result

    def intent_state(self, intent_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            intent = (
                connection.execute(select(call_intent).where(call_intent.c.id == intent_id))
                .mappings()
                .one_or_none()
            )
            if intent is None:
                raise NotFound()
            deliveries = self._deliveries(connection, intent_id)
            retained = any(self._has_uncertain(connection, row["id"]) for row in deliveries)
            return {
                "intent": dict(intent),
                "deliveries": [dict(row) for row in deliveries],
                "exposure_retained": retained,
            }

    # ---------------------------------------------------------------- internals

    def _lock_call(self, connection: Connection, delivery_id: UUID) -> tuple[Any, Any]:
        """Lock the intent, then the delivery (the one lock order used everywhere)."""
        intent_id = connection.execute(
            select(call_delivery.c.intent_id).where(call_delivery.c.id == delivery_id)
        ).scalar_one_or_none()
        if intent_id is None:
            raise NotFound()
        intent = (
            connection.execute(
                select(call_intent).where(call_intent.c.id == intent_id).with_for_update()
            )
            .mappings()
            .one()
        )
        delivery = (
            connection.execute(
                select(call_delivery).where(call_delivery.c.id == delivery_id).with_for_update()
            )
            .mappings()
            .one()
        )
        return intent, delivery

    @staticmethod
    def _scope_of(intent: Any) -> CallScope:
        """The authoritative scope is the intent's own; callers cannot redirect a charge."""
        if intent["attempt_id"] is not None:
            return CallScope(kind="attempt", scope_id=intent["attempt_id"])
        if intent["evaluation_id"] is not None:
            return CallScope(kind="evaluation", scope_id=intent["evaluation_id"])
        return CallScope(kind="audit_run", scope_id=intent["audit_run_id"])

    @staticmethod
    def _next_revision(connection: Connection, delivery_id: UUID) -> int:
        return int(
            connection.execute(
                select(func.coalesce(func.max(usage_record.c.settlement_revision), 0)).where(
                    usage_record.c.delivery_id == delivery_id
                )
            ).scalar_one()
            + 1
        )

    def _record_ambiguous(
        self,
        connection: Connection,
        scope: CallScope,
        intent_id: UUID,
        delivery_id: UUID,
        code: str,
        body_artifact_id: UUID | None,
        provider_request_id: str | None = None,
    ) -> None:
        connection.execute(
            update(call_delivery)
            .where(call_delivery.c.id == delivery_id)
            .values(
                status="ambiguous",
                responded_at=datetime.now(UTC),
                failure_code=code,
                provider_request_id=provider_request_id,
                raw_response_artifact_id=body_artifact_id,
            )
        )
        self._retain_reserved(
            connection, scope, intent_id, delivery_id, f"ambiguous outcome: {code}"
        )
        index = connection.execute(
            select(call_delivery.c.delivery_index).where(call_delivery.c.id == delivery_id)
        ).scalar_one()
        self._mark_reservations(connection, intent_id, index, "uncertain")

    def _retain_reserved(
        self,
        connection: Connection,
        scope: CallScope,
        intent_id: UUID,
        delivery_id: UUID,
        reason: str,
    ) -> None:
        for account in self._chain(connection, scope):
            for resource in RESOURCES:
                amount = self._delivery_bucket(
                    connection, account["id"], resource, delivery_id, "reserved_open"
                )
                self._move(
                    connection,
                    account["id"],
                    resource,
                    "reserved_open",
                    "uncertain_committed",
                    amount,
                    intent_id,
                    delivery_id,
                    "retain",
                    reason,
                )

    def _record_late(
        self,
        connection: Connection,
        scope: CallScope,
        intent: Any,
        delivery: Any,
        raw_artifact_id: UUID,
        provider_request_id: str | None,
        usage: Usage,
        known: bool,
        cost: int | None,
        availability: dict[str, Any],
        estimated_cost: int | None,
    ) -> SettlementResult:
        """A response that lost the arrival race is billed evidence, never a second result."""
        delivery_id = delivery["id"]
        if delivery["status"] == "dispatching":
            connection.execute(
                update(call_delivery)
                .where(call_delivery.c.id == delivery_id)
                .values(
                    status="ambiguous",
                    responded_at=datetime.now(UTC),
                    failure_code=LATE_CODE,
                    provider_request_id=provider_request_id,
                    raw_response_artifact_id=raw_artifact_id,
                )
            )
            self._retain_reserved(connection, scope, intent["id"], delivery_id, LATE_CODE)
            self._mark_reservations(
                connection, intent["id"], delivery["delivery_index"], "uncertain"
            )
        else:
            connection.execute(
                update(call_delivery)
                .where(call_delivery.c.id == delivery_id)
                .values(
                    failure_code=LATE_CODE,
                    provider_request_id=delivery["provider_request_id"] or provider_request_id,
                    raw_response_artifact_id=delivery["raw_response_artifact_id"]
                    or raw_artifact_id,
                )
            )
        revision = self._next_revision(connection, delivery_id)
        connection.execute(
            insert(usage_record).values(
                delivery_id=delivery_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                usage_available={**availability, "late_response": True},
                source="provider_reported" if usage.complete else "unavailable",
                actual_cost_micro_usd=usage.reported_cost_micro_usd,
                estimated_cost_micro_usd=estimated_cost if known else None,
                settlement_revision=revision,
            )
        )
        charged: int | None = None
        retained = 0
        if known:
            charged, retained, _ = self._settle_exposure(
                connection,
                scope,
                intent["id"],
                delivery_id,
                cost,
                usage,
                reason="late response for ambiguous delivery",
                source_bucket="uncertain_committed",
            )
        else:
            retained = self._delivery_total(connection, scope, delivery_id)
        self._refresh_intent_state(connection, intent["id"])
        return SettlementResult(
            "settled" if known else "uncertain", charged, retained, revision, consumed=False
        )

    def _settle_exposure(
        self,
        connection: Connection,
        scope: CallScope,
        intent_id: UUID,
        delivery_id: UUID,
        cost_micro_usd: int | None,
        usage: Usage | None,
        *,
        reason: str,
        source_bucket: Bucket,
    ) -> tuple[int | None, int, set[str]]:
        """Charge known amounts out of ``source_bucket``; unknown amounts stay retained.

        A resource is settled from what the delivery still *holds*. Exposure that was already
        resolved (held is zero after a reservation existed) is skipped, never charged again;
        the third return value names the skipped resources.
        """
        charged_money: int | None = None
        retained_money = 0
        skipped: set[str] = set()
        known: dict[str, int | None] = {
            "money": cost_micro_usd,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "turns": 1,
        }
        for account in self._chain(connection, scope):
            for resource in RESOURCES:
                held = self._delivery_bucket(
                    connection, account["id"], resource, delivery_id, source_bucket
                )
                actual = known[resource]
                if resource == "turns" and held == 0:
                    continue  # this delivery holds no turn; the call's turn is tracked per intent
                if held == 0:
                    if actual is None or actual == 0:
                        continue
                    if self._reserved_before(connection, account["id"], resource, delivery_id):
                        skipped.add(resource)  # already resolved; never book it twice
                        continue
                if actual is None:
                    if source_bucket == "reserved_open" and held > 0:
                        self._move(
                            connection,
                            account["id"],
                            resource,
                            "reserved_open",
                            "uncertain_committed",
                            held,
                            intent_id,
                            delivery_id,
                            "retain",
                            f"{reason}: {resource} unknown, exposure retained",
                        )
                    if resource == "money":
                        retained_money = held
                    continue
                spend = min(actual, held)
                self._move(
                    connection,
                    account["id"],
                    resource,
                    source_bucket,
                    "spent_confirmed",
                    spend,
                    intent_id,
                    delivery_id,
                    "charge",
                    reason,
                )
                self._move(
                    connection,
                    account["id"],
                    resource,
                    source_bucket,
                    "none",
                    held - spend,
                    intent_id,
                    delivery_id,
                    "release",
                    f"{reason}: unused reservation",
                )
                if actual > spend:  # provider billed beyond the bound: record the overrun
                    self._move(
                        connection,
                        account["id"],
                        resource,
                        "none",
                        "spent_confirmed",
                        actual - spend,
                        intent_id,
                        delivery_id,
                        "adjustment",
                        f"{reason}: exceeded reservation",
                    )
                if resource == "money":
                    charged_money = actual
        return charged_money, retained_money, skipped

    def _release_exposure(
        self,
        connection: Connection,
        scope: CallScope,
        intent_id: UUID,
        delivery_id: UUID,
        reason: str,
    ) -> int:
        released_money = 0
        for account in self._chain(connection, scope):
            for resource in RESOURCES:
                for bucket in ("reserved_open", "uncertain_committed"):
                    held = self._delivery_bucket(
                        connection, account["id"], resource, delivery_id, bucket
                    )
                    if held > 0:
                        self._move(
                            connection,
                            account["id"],
                            resource,
                            bucket,
                            "none",
                            held,
                            intent_id,
                            delivery_id,
                            "release",
                            reason,
                        )
                        if resource == "money":
                            released_money += held
        return released_money

    def _consume_earlier_turns(
        self, connection: Connection, scope: CallScope, intent_id: UUID, responded_id: UUID
    ) -> None:
        """A response means the turn happened; earlier ambiguous deliveries no longer hold it."""
        earlier = (
            connection.execute(
                select(call_delivery.c.id).where(
                    call_delivery.c.intent_id == intent_id, call_delivery.c.id != responded_id
                )
            )
            .scalars()
            .all()
        )
        for account in self._chain(connection, scope):
            for delivery_id in earlier:
                held = self._delivery_bucket(
                    connection, account["id"], "turns", delivery_id, "uncertain_committed"
                )
                self._move(
                    connection,
                    account["id"],
                    "turns",
                    "uncertain_committed",
                    "spent_confirmed",
                    held,
                    intent_id,
                    delivery_id,
                    "charge",
                    "turn consumed by a later response",
                )

    def _ensure_turn_spent(
        self, connection: Connection, scope: CallScope, intent_id: UUID, delivery_id: UUID
    ) -> None:
        """Guarantee a response is counted as exactly one turn, even if the delivery that
        reserved the turn was released or reconciled as unbilled first."""
        for account in self._chain(connection, scope):
            has_limit = connection.execute(
                select(budget_resource.c.id).where(
                    budget_resource.c.account_id == account["id"],
                    budget_resource.c.resource == "turns",
                )
            ).scalar_one_or_none()
            if has_limit is not None and (
                self._intent_turn_exposure(connection, account["id"], intent_id) <= 0
            ):
                self._move(
                    connection,
                    account["id"],
                    "turns",
                    "none",
                    "spent_confirmed",
                    1,
                    intent_id,
                    delivery_id,
                    "adjustment",
                    "turn consumed by a response",
                )

    def _intent_turn_exposure(
        self, connection: Connection, account_id: UUID, intent_id: UUID
    ) -> int:
        """Net turns this logical call currently holds (reserved, uncertain or spent)."""
        total = 0
        for bucket in LIVE_BUCKETS:
            inflow = connection.execute(
                select(func.coalesce(func.sum(accounting_entry.c.amount_micro_usd), 0)).where(
                    accounting_entry.c.account_id == account_id,
                    accounting_entry.c.call_intent_id == intent_id,
                    accounting_entry.c.resource == "turns",
                    accounting_entry.c.to_bucket == bucket,
                )
            ).scalar_one()
            outflow = connection.execute(
                select(func.coalesce(func.sum(accounting_entry.c.amount_micro_usd), 0)).where(
                    accounting_entry.c.account_id == account_id,
                    accounting_entry.c.call_intent_id == intent_id,
                    accounting_entry.c.resource == "turns",
                    accounting_entry.c.from_bucket == bucket,
                )
            ).scalar_one()
            total += int(inflow - outflow)
        return total

    def _refresh_intent_state(
        self, connection: Connection, intent_id: UUID, *, terminal: bool = False
    ) -> Literal["settled", "uncertain"]:
        """Derive the intent state from all of its deliveries, never from the last one alone."""
        rows = self._deliveries(connection, intent_id)
        uncertain = any(self._has_uncertain(connection, row["id"]) for row in rows)
        responded = any(row["status"] == "responded" for row in rows)
        if responded:
            state = "uncertain" if uncertain else "settled"
        elif terminal:
            state = "failed"
        else:
            state = "uncertain" if uncertain else "reserved"
        connection.execute(
            update(call_intent).where(call_intent.c.id == intent_id).values(state=state)
        )
        return "uncertain" if uncertain else "settled"

    def _has_uncertain(self, connection: Connection, delivery_id: UUID) -> bool:
        accounts = (
            connection.execute(
                select(accounting_entry.c.account_id)
                .where(accounting_entry.c.delivery_id == delivery_id)
                .distinct()
            )
            .scalars()
            .all()
        )
        return any(
            self._delivery_bucket(connection, account, resource, delivery_id, "uncertain_committed")
            > 0
            for account in accounts
            for resource in RESOURCES
        )

    def _delivery_total(self, connection: Connection, scope: CallScope, delivery_id: UUID) -> int:
        leaf = self._chain(connection, scope)[-1]
        return self._delivery_bucket(
            connection, leaf["id"], "money", delivery_id, "uncertain_committed"
        )

    def _reserved_before(
        self, connection: Connection, account_id: UUID, resource: str, delivery_id: UUID
    ) -> bool:
        return (
            connection.execute(
                select(func.count())
                .select_from(accounting_entry)
                .where(
                    accounting_entry.c.account_id == account_id,
                    accounting_entry.c.delivery_id == delivery_id,
                    accounting_entry.c.resource == resource,
                    accounting_entry.c.to_bucket == "reserved_open",
                )
            ).scalar_one()
            > 0
        )

    def _mark_reservations(
        self, connection: Connection, intent_id: UUID, delivery_index: int, state: str
    ) -> None:
        connection.execute(
            update(budget_reservation)
            .where(
                budget_reservation.c.call_intent_id == intent_id,
                budget_reservation.c.delivery_index == delivery_index,
            )
            .values(state=state)
        )

    def _delivery_bucket(
        self,
        connection: Connection,
        account_id: UUID,
        resource: str,
        delivery_id: UUID,
        bucket: str,
    ) -> int:
        inflow = connection.execute(
            select(func.coalesce(func.sum(accounting_entry.c.amount_micro_usd), 0)).where(
                accounting_entry.c.account_id == account_id,
                accounting_entry.c.delivery_id == delivery_id,
                accounting_entry.c.resource == resource,
                accounting_entry.c.to_bucket == bucket,
            )
        ).scalar_one()
        outflow = connection.execute(
            select(func.coalesce(func.sum(accounting_entry.c.amount_micro_usd), 0)).where(
                accounting_entry.c.account_id == account_id,
                accounting_entry.c.delivery_id == delivery_id,
                accounting_entry.c.resource == resource,
                accounting_entry.c.from_bucket == bucket,
            )
        ).scalar_one()
        return int(inflow - outflow)

    def _derived(self, connection: Connection, account_id: UUID, resource: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for bucket in _BUCKET_COLUMN:
            inflow = connection.execute(
                select(func.coalesce(func.sum(accounting_entry.c.amount_micro_usd), 0)).where(
                    accounting_entry.c.account_id == account_id,
                    accounting_entry.c.resource == resource,
                    accounting_entry.c.to_bucket == bucket,
                )
            ).scalar_one()
            outflow = connection.execute(
                select(func.coalesce(func.sum(accounting_entry.c.amount_micro_usd), 0)).where(
                    accounting_entry.c.account_id == account_id,
                    accounting_entry.c.resource == resource,
                    accounting_entry.c.from_bucket == bucket,
                )
            ).scalar_one()
            result[bucket] = int(inflow - outflow)
        return result

    def _move(
        self,
        connection: Connection,
        account_id: UUID,
        resource: str,
        source: Bucket,
        target: Bucket,
        amount: int,
        intent_id: UUID,
        delivery_id: UUID,
        kind: str,
        reason: str,
    ) -> None:
        if amount <= 0:
            return
        connection.execute(
            insert(accounting_entry).values(
                account_id=account_id,
                call_intent_id=intent_id,
                delivery_id=delivery_id,
                entry_kind=kind,
                resource=resource,
                from_bucket=source,
                to_bucket=target,
                amount_micro_usd=amount,
                reason=reason[:500],
            )
        )
        if resource == "money":
            table, key = budget_account, budget_account.c.id
        else:
            table = budget_resource
            key = None
        values: dict[str, Any] = {"row_version": table.c.row_version + 1}
        if source != "none":
            values[_BUCKET_COLUMN[source]] = table.c[_BUCKET_COLUMN[source]] - amount
        if target != "none":
            values[_BUCKET_COLUMN[target]] = table.c[_BUCKET_COLUMN[target]] + amount
        statement = update(table).values(**values)
        if key is not None:
            statement = statement.where(key == account_id)
        else:
            statement = statement.where(
                table.c.account_id == account_id, table.c.resource == resource
            )
        connection.execute(statement)
