"""The accountable model gateway: one logical call, bounded deliveries, nothing shopped.

Order of operations for every logical call (Technical Spec 8.3):
guard/lease -> recorded state -> approved endpoint -> capability validation -> cost bound ->
throttle slot -> persist intent and reserve (one transaction) -> lease re-check -> send ->
persist raw bytes -> settle -> only then return the parsed response to the caller.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from polycodebench_core.application_errors import LeaseLost
from polycodebench_core.endpoint_policy import RegisteredEndpoint
from polycodebench_core.model_contracts import (
    CallScope,
    CapabilityUnsupported,
    CostBoundUnavailable,
    EndpointPolicyViolation,
    FailureKind,
    ModelRequest,
    ModelResponse,
    ProviderCallFailed,
    ProviderKind,
    ReservationPlan,
    TransportFailure,
    Usage,
    stable_json_bytes,
)
from polycodebench_core.model_planning import (
    ModelConfig,
    compute_cost_micro_usd,
    cost_bound,
    effective_capabilities,
    map_provider_seed,
    require_cost_bound,
)
from polycodebench_core.models import ProtocolDefinition
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.model_ledger import CallHandle, PostgresModelLedger

from polycodebench_orchestration.gateway.adapters.base import (
    BaseAdapter,
    MalformedResponse,
    WireRequest,
)
from polycodebench_orchestration.gateway.secrets import Secret, SecretResolver
from polycodebench_orchestration.gateway.throttle import ThrottleRegistry
from polycodebench_orchestration.gateway.transport import (
    HttpResponse,
    HttpTransport,
    TransportError,
)

MAX_STORED_ERROR_BODY = 64 * 1024
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._~:/%@!$&()*+,;=-]*$")


class ResponseStore(Protocol):
    """Durable, digest-verified storage for request/response bytes."""

    def put(self, data: bytes, *, kind: str, media_type: str) -> UUID: ...

    def get(self, artifact_id: UUID) -> bytes: ...


@dataclass(frozen=True)
class RetryPolicy:
    """``in_flight_grace`` should comfortably exceed ``request_timeout_seconds``: a delivery
    older than the grace period with no stored bytes is treated as lost (ambiguous)."""

    max_deliveries: int = 3
    in_flight_grace: timedelta = timedelta(minutes=10)
    request_timeout_seconds: float = 120.0
    base_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class GatewayResult:
    response: ModelResponse
    intent_id: UUID
    delivery_index: int
    source: str  # "provider", "stored" (recovered, no request) or "recovered_raw"
    settlement_state: str | None
    dropped_controls: tuple[str, ...]
    prior_ambiguous_deliveries: int


class _Superseded:
    """This response lost the arrival race; the recorded one must be read instead."""


class ModelGateway:
    def __init__(
        self,
        *,
        endpoints: PostgresEndpointRepository,
        ledger: PostgresModelLedger,
        store: ResponseStore,
        transport: HttpTransport,
        secrets: SecretResolver,
        adapters: Mapping[ProviderKind, BaseAdapter],
        throttles: ThrottleRegistry | None = None,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._endpoints = endpoints
        self._ledger = ledger
        self._store = store
        self._transport = transport
        self._secrets = secrets
        self._adapters = adapters
        self._throttles = throttles or ThrottleRegistry()
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    async def call(
        self,
        *,
        scope: CallScope,
        logical_call_key: str,
        config: ModelConfig,
        config_document_id: UUID,
        protocol: ProtocolDefinition,
        request: ModelRequest,
        dispatch_allowed: Callable[[], bool] | None = None,
    ) -> GatewayResult:
        """Run (or recover) one logical model call.

        ``dispatch_allowed`` is the lease/fence check of the owning job (Prompt 07). It is
        consulted before every delivery and again immediately before the bytes are sent, so an
        expired worker cannot spend budget.
        """
        self._require_dispatch(dispatch_allowed)
        digest = request.digest()
        # A recorded response or definitive failure is consumed even if the endpoint has
        # since been revoked: recovery never needs the network and never re-samples.
        recorded = await asyncio.to_thread(
            self._ledger.recorded_call, scope, logical_call_key, digest, config_document_id
        )
        if recorded is not None:
            return self._from_recorded(recorded, ())
        # Nothing below touches the network until the endpoint is approved and validated.
        endpoint = self._endpoints.get_approved(config.endpoint_id)
        adapter = self._adapter_for(config, endpoint.provider_kind)
        adapter.validate(
            config, protocol, requires_structured_output=request.response_schema is not None
        )
        wire: WireRequest = adapter.build_request(config, request)  # rejects unsupported controls
        if not _SAFE_PATH.fullmatch(wire.path):
            raise CapabilityUnsupported("request path contains characters that cannot be sent")
        caps = effective_capabilities(adapter.capabilities(), config.declared_capabilities)
        request_bytes = request.canonical_bytes()
        bound = cost_bound(
            config,
            caps,
            request_bytes=len(request_bytes),
            output_tokens=request.max_output_tokens,
            has_tools=bool(request.tools),
        )
        if config.strict_money_cap and not bound.strict_cap_eligible:
            raise CostBoundUnavailable(bound.reason)
        plan = ReservationPlan(
            money_micro_usd=require_cost_bound(bound),
            input_tokens=bound.input_tokens_bound,
            output_tokens=bound.output_tokens_bound,
            turns=1,
        )
        secret = self._resolve_secret(endpoint)
        # Fails here, before any reservation, if the credential or header is unusable.
        headers = adapter.authenticate(dict(wire.headers), secret)
        request_artifact = self._store.put(
            request_bytes, kind="model_request", media_type="application/json"
        )
        snapshot = {
            "price": config.price.model_dump(mode="json") if config.price else None,
            "cost_bound": bound.model_dump(mode="json"),
            "strict_cap_claim": config.strict_money_cap and bound.strict_cap_eligible,
            "dropped_controls": list(wire.dropped_controls),
            "provider_kind": endpoint.provider_kind.value,
            "model": config.model,
        }

        failure: TransportFailure | None = None
        handle: CallHandle | None = None
        for attempt in range(self._retry.max_deliveries):
            self._require_dispatch(dispatch_allowed)
            # Re-read approval on every delivery so a revocation stops retries.
            endpoint = self._endpoints.get_approved(config.endpoint_id)
            throttle = self._throttles.for_endpoint(endpoint.endpoint_id)
            # The slot is taken before the reservation so queueing never ages an open delivery.
            async with throttle.slot():
                handle = await asyncio.to_thread(
                    self._ledger.begin_call,
                    scope=scope,
                    logical_call_key=logical_call_key,
                    request_digest=digest,
                    model_config_id=config_document_id,
                    request_artifact_id=request_artifact,
                    price_snapshot=snapshot,
                    plan=plan,
                    max_deliveries=self._retry.max_deliveries,
                    in_flight_grace=self._retry.in_flight_grace,
                )
                if handle.action in {"stored", "failed"}:
                    return self._from_recorded(handle, wire.dropped_controls)
                if handle.action == "recover_raw":
                    outcome = await self._recover_raw(adapter, config, handle, wire, bound.kind)
                else:
                    outcome = await self._deliver(
                        adapter,
                        endpoint,
                        config,
                        handle,
                        wire,
                        headers,
                        secret,
                        bound.kind,
                        throttle_note=throttle.note_rate_limited,
                        dispatch_allowed=dispatch_allowed,
                    )
            if isinstance(outcome, GatewayResult):
                return outcome
            if isinstance(outcome, _Superseded):
                settled = await asyncio.to_thread(
                    self._ledger.recorded_call, scope, logical_call_key, digest, config_document_id
                )
                assert settled is not None and settled.action == "stored"
                return self._from_recorded(settled, wire.dropped_controls)
            failure = outcome
            if not failure.retryable or attempt + 1 >= self._retry.max_deliveries:
                break
            await self._sleep(self._backoff(failure, attempt))
        assert failure is not None and handle is not None
        raise ProviderCallFailed(
            failure,
            intent_id=str(handle.intent_id),
            exposure_retained=await self._exposure_retained(handle.intent_id),
        )

    # -------------------------------------------------------------- planning helpers

    def check_compatibility(
        self,
        config: ModelConfig,
        protocol: ProtocolDefinition,
        *,
        requires_structured_output: bool = False,
    ) -> None:
        """Preflight: raises if the approved endpoint cannot run this protocol as configured."""
        endpoint = self._endpoints.get_approved(config.endpoint_id)
        adapter = self._adapter_for(config, endpoint.provider_kind)
        adapter.validate(config, protocol, requires_structured_output=requires_structured_output)

    def provider_seed(self, config: ModelConfig, sample_seed: int) -> int | None:
        """Deterministic provider seed for a sample, or ``None`` when policy or support says no."""
        if config.seed_policy == "omit":
            return None
        endpoint = self._endpoints.get_approved(config.endpoint_id)
        adapter = self._adapter_for(config, endpoint.provider_kind)
        caps = effective_capabilities(adapter.capabilities(), config.declared_capabilities)
        return map_provider_seed(sample_seed, caps)

    # ------------------------------------------------------------------ delivery

    async def _deliver(
        self,
        base: BaseAdapter,
        endpoint: RegisteredEndpoint,
        config: ModelConfig,
        handle: CallHandle,
        wire: WireRequest,
        headers: dict[str, str],
        secret: Secret | None,
        bound_kind: str,
        *,
        throttle_note: Callable[[float | None], float],
        dispatch_allowed: Callable[[], bool] | None,
    ) -> GatewayResult | _Superseded | TransportFailure:
        assert handle.delivery_id is not None
        if dispatch_allowed is not None and not dispatch_allowed():
            # The lease was lost after the reservation but before any byte was sent.
            revoked = TransportFailure(
                kind=FailureKind.NOT_DELIVERED, code="dispatch_revoked", retryable=False
            )
            await asyncio.to_thread(
                self._ledger.settle_failure,
                delivery_id=handle.delivery_id,
                failure=revoked,
                body_artifact_id=None,
            )
            raise LeaseLost()
        answer: HttpResponse | None = None
        failure: TransportFailure | None = None
        try:
            answer = await self._transport.send(
                endpoint,
                wire.path,
                headers,
                wire.body,
                timeout_seconds=self._retry.request_timeout_seconds,
            )
        except (TransportError, EndpointPolicyViolation) as error:
            failure = base.classify_transport_error(error)
        except Exception as error:  # an unknown fault after a possible send stays ambiguous
            failure = base.classify_transport_error(error)

        if failure is None:
            assert answer is not None
            if 200 <= answer.status < 300:
                return await self._accept(base, config, handle, wire, secret, answer, bound_kind)
            failure = base.classify_http(answer.status, answer.headers, answer.body)
            if failure.kind is FailureKind.RATE_LIMITED:
                throttle_note(failure.retry_after_seconds)
        body_artifact = (
            self._store.put(
                _scrub(failure.body, secret)[:MAX_STORED_ERROR_BODY],
                kind="model_error_body",
                media_type="application/octet-stream",
            )
            if failure.body
            else None
        )
        await asyncio.to_thread(
            self._ledger.settle_failure,
            delivery_id=handle.delivery_id,
            failure=failure,
            body_artifact_id=body_artifact,
        )
        return failure

    async def _accept(
        self,
        adapter: BaseAdapter,
        config: ModelConfig,
        handle: CallHandle,
        wire: WireRequest,
        secret: Secret | None,
        answer: HttpResponse,
        bound_kind: str,
    ) -> GatewayResult | _Superseded | TransportFailure:
        assert handle.delivery_id is not None
        # Raw bytes are durable before parsing or notifying the controller.
        raw_bytes = _scrub(answer.body, secret)
        raw_artifact = self._store.put(
            raw_bytes, kind="model_raw_response", media_type="application/json"
        )
        # False means recovery already retired this delivery; settle_response then records the
        # bytes as a late response, and only the first-arriving response is ever consumed.
        await asyncio.to_thread(
            self._ledger.attach_raw_response,
            delivery_id=handle.delivery_id,
            raw_artifact_id=raw_artifact,
            provider_request_id=adapter.provider_request_id(answer.headers),
        )
        return await self._settle_raw(
            adapter,
            config,
            handle,
            wire,
            raw_bytes,
            raw_artifact,
            answer.headers,
            answer.status,
            bound_kind,
        )

    async def _recover_raw(
        self,
        adapter: BaseAdapter,
        config: ModelConfig,
        handle: CallHandle,
        wire: WireRequest,
        bound_kind: str,
    ) -> GatewayResult | _Superseded | TransportFailure:
        """Recovery: bytes were persisted but settlement never ran. No new request is made."""
        assert handle.raw_artifact_id is not None
        headers = {"x-request-id": handle.provider_request_id} if handle.provider_request_id else {}
        result = await self._settle_raw(
            adapter,
            config,
            handle,
            wire,
            self._store.get(handle.raw_artifact_id),
            handle.raw_artifact_id,
            headers,
            200,
            bound_kind,
        )
        if isinstance(result, GatewayResult):
            return GatewayResult(**{**result.__dict__, "source": "recovered_raw"})
        return result

    async def _settle_raw(
        self,
        adapter: BaseAdapter,
        config: ModelConfig,
        handle: CallHandle,
        wire: WireRequest,
        raw_bytes: bytes,
        raw_artifact: UUID,
        headers: dict[str, str],
        status: int,
        bound_kind: str,
    ) -> GatewayResult | _Superseded | TransportFailure:
        assert handle.delivery_id is not None
        try:
            response = adapter.parse_response(raw_bytes, headers)
        except (MalformedResponse, ValueError, TypeError):
            failure = TransportFailure(
                kind=FailureKind.AMBIGUOUS,
                code="malformed_response",
                retryable=True,
                http_status=status,
                provider_request_id=adapter.provider_request_id(headers),
            )
            await asyncio.to_thread(
                self._ledger.settle_failure,
                delivery_id=handle.delivery_id,
                failure=failure,
                body_artifact_id=raw_artifact,
            )
            return failure
        normalized = self._store.put(
            stable_json_bytes(response.model_dump(mode="json")),
            kind="model_response",
            media_type="application/json",
        )
        estimate, basis = _estimate_cost(config, response.usage)
        settlement = await asyncio.to_thread(
            self._ledger.settle_response,
            delivery_id=handle.delivery_id,
            provider_request_id=response.provider_request_id,
            raw_artifact_id=raw_artifact,
            normalized_artifact_id=normalized,
            usage=response.usage,
            usage_reliable=_usage_is_plausible(response),
            estimated_cost_micro_usd=estimate,
            estimate_basis=basis,
        )
        if not settlement.consumed:
            return _Superseded()
        return GatewayResult(
            response=response,
            intent_id=handle.intent_id,
            delivery_index=handle.delivery_index,
            source="provider",
            settlement_state=settlement.state,
            dropped_controls=wire.dropped_controls,
            prior_ambiguous_deliveries=handle.prior_ambiguous_deliveries,
        )

    # ------------------------------------------------------------------ helpers

    def _from_recorded(self, handle: CallHandle, dropped: tuple[str, ...]) -> GatewayResult:
        """Consume recorded state: the stored response, or the recorded definitive failure."""
        if handle.action == "failed":
            state = self._ledger.intent_state(handle.intent_id)
            retained = bool(state["exposure_retained"])
            failure = TransportFailure(
                kind=FailureKind.AMBIGUOUS if retained else FailureKind.REJECTED,
                code=_safe_code(handle.failure_code),
                retryable=False,
                provider_request_id=handle.provider_request_id,
            )
            raise ProviderCallFailed(
                failure, intent_id=str(handle.intent_id), exposure_retained=retained
            )
        assert handle.normalized_artifact_id is not None
        response = ModelResponse.model_validate(
            json.loads(self._store.get(handle.normalized_artifact_id)), strict=False
        )
        return GatewayResult(
            response=response,
            intent_id=handle.intent_id,
            delivery_index=handle.delivery_index,
            source="stored",
            settlement_state=None,
            dropped_controls=dropped,
            prior_ambiguous_deliveries=0,
        )

    async def _exposure_retained(self, intent_id: UUID) -> bool:
        state = await asyncio.to_thread(self._ledger.intent_state, intent_id)
        return bool(state["exposure_retained"])

    def _resolve_secret(self, endpoint: RegisteredEndpoint) -> Secret | None:
        try:
            return self._secrets.resolve(endpoint.secret_ref)
        except ValueError:
            raise EndpointPolicyViolation(
                "provisioned secret is not usable as a credential"
            ) from None

    def _adapter_for(self, config: ModelConfig, endpoint_kind: ProviderKind) -> BaseAdapter:
        if config.provider_kind is not endpoint_kind:
            raise CapabilityUnsupported("model configuration does not match the endpoint provider")
        adapter = self._adapters.get(endpoint_kind)
        if adapter is None:
            raise CapabilityUnsupported("no adapter is registered for this provider")
        return adapter

    @staticmethod
    def _require_dispatch(allowed: Callable[[], bool] | None) -> None:
        if allowed is not None and not allowed():
            raise LeaseLost()

    def _backoff(self, failure: TransportFailure, attempt: int) -> float:
        if failure.retry_after_seconds is not None:
            return failure.retry_after_seconds
        return float(self._retry.base_backoff_seconds * (2**attempt))


def _usage_is_plausible(response: ModelResponse) -> bool:
    """Reported zeros next to real output are not evidence of a free call."""
    usage = response.usage
    if not usage.complete:
        return False
    assert usage.input_tokens is not None and usage.output_tokens is not None
    return usage.input_tokens >= 1 and (usage.output_tokens >= 1 or not response.blocks)


def _estimate_cost(config: ModelConfig, usage: Usage) -> tuple[int | None, str]:
    """A labeled estimate from reported usage and the price snapshot; never zero by default."""
    if config.price is None or not usage.complete:
        return None, "unavailable"
    assert usage.input_tokens is not None and usage.output_tokens is not None
    cost = compute_cost_micro_usd(
        config.price, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
    )
    return cost, f"price_snapshot:{config.price.price_id};list_price_no_cache_discount"


def _scrub(data: bytes | None, secret: Secret | None) -> bytes:
    if data is None:
        return b""
    return secret.scrub(data)[0] if secret is not None else data


def _safe_code(code: str | None) -> str:
    text = (code or "exhausted").lower()
    return text if text.replace("_", "").isalnum() and text[:1].isalpha() else "exhausted"
