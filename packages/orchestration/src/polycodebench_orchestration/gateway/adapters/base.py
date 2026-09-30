"""Shared adapter behaviour: capability gating, failure classification, wire helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from polycodebench_core.endpoint_policy import RegisteredEndpoint
from polycodebench_core.model_contracts import (
    CapabilityControl,
    CapabilityUnsupported,
    EndpointPolicyViolation,
    FailureKind,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ProviderKind,
    TransportFailure,
)
from polycodebench_core.model_planning import (
    CompatibilityReport,
    ModelConfig,
    check_compatibility,
    effective_capabilities,
    ensure_compatible,
)
from polycodebench_core.models import ProtocolDefinition

from polycodebench_orchestration.gateway.secrets import Secret
from polycodebench_orchestration.gateway.transport import HttpTransport, TransportError

MAX_ERROR_BODY_BYTES = 1024 * 1024  # clipped after scrubbing, in the gateway
MAX_RETRY_AFTER_SECONDS = 120.0


@dataclass(frozen=True)
class WireRequest:
    path: str
    headers: dict[str, str]  # never contains credentials
    body: bytes
    dropped_controls: tuple[str, ...] = ()


@dataclass
class ControlDecision:
    """Which requested controls reach the wire; nothing disappears without a record."""

    send_temperature: bool = True
    send_seed: bool = True
    send_schema: bool = True
    dropped: list[str] = field(default_factory=list)


class BaseAdapter:
    provider_kind: ProviderKind
    wire_capabilities: ModelCapabilities

    def capabilities(self) -> ModelCapabilities:
        return self.wire_capabilities

    # ---------------------------------------------------------------- validation

    def validate(
        self,
        config: ModelConfig,
        protocol: ProtocolDefinition,
        *,
        requires_structured_output: bool = False,
    ) -> CompatibilityReport:
        if config.provider_kind is not self.provider_kind:
            raise CapabilityUnsupported("model configuration targets a different adapter")
        report = check_compatibility(
            config,
            protocol,
            self.capabilities(),
            requires_structured_output=requires_structured_output,
        )
        ensure_compatible(report, config)
        return report

    def decide_controls(self, config: ModelConfig, request: ModelRequest) -> ControlDecision:
        """Reject or record every control the endpoint cannot honour (no silent downgrade)."""
        caps = effective_capabilities(self.capabilities(), config.declared_capabilities)
        decision = ControlDecision()
        if request.tools and not caps.native_tools:
            raise CapabilityUnsupported("endpoint has no native tool calling")
        if request.reasoning is not None:
            reasoning = request.reasoning
            supported = (
                reasoning.effort in caps.reasoning_efforts
                if reasoning.effort is not None
                else caps.reasoning_budget_tokens
            )
            if not supported:
                raise CapabilityUnsupported("reasoning control is not supported")
        if request.max_output_tokens > config.max_output_tokens:
            raise CapabilityUnsupported("request output cap exceeds the resolved model config")
        if request.temperature != config.temperature:
            raise CapabilityUnsupported("request temperature differs from the resolved config")
        if request.reasoning != config.reasoning:
            raise CapabilityUnsupported("request reasoning differs from the resolved config")
        if request.temperature is not None and not caps.temperature:
            if not config.excepted(CapabilityControl.TEMPERATURE):
                raise CapabilityUnsupported("temperature is not supported by this endpoint")
            decision.send_temperature = False
            decision.dropped.append("temperature:cohort_exception")
        if request.seed is not None and config.seed_policy == "omit":
            decision.send_seed = False
            decision.dropped.append("seed:omitted_by_policy")
        elif request.seed is not None:
            if not caps.seed:
                if config.seed_policy == "deterministic_mapping" and not config.excepted(
                    CapabilityControl.SEED
                ):
                    raise CapabilityUnsupported("deterministic seed is not supported")
                decision.send_seed = False
                decision.dropped.append("seed:unsupported_recorded")
            elif not (
                caps.seed_min is not None
                and caps.seed_max is not None
                and caps.seed_min <= request.seed <= caps.seed_max
            ):
                raise CapabilityUnsupported("seed is outside the provider's declared range")
        if request.response_schema is not None and not caps.structured_output:
            if not config.excepted(CapabilityControl.STRUCTURED_OUTPUT):
                raise CapabilityUnsupported("structured output is not supported")
            decision.send_schema = False
            decision.dropped.append("structured_output:cohort_exception")
        if (
            caps.max_output_tokens_limit
            and request.max_output_tokens > caps.max_output_tokens_limit
        ):
            raise CapabilityUnsupported("max output tokens exceeds the declared limit")
        return decision

    # -------------------------------------------------------------- abstract wire

    def build_request(self, config: ModelConfig, request: ModelRequest) -> WireRequest:
        raise NotImplementedError

    def authenticate(self, headers: dict[str, str], secret: Secret | None) -> dict[str, str]:
        raise NotImplementedError

    def parse_response(self, body: bytes, headers: dict[str, str]) -> ModelResponse:
        raise NotImplementedError

    def provider_request_id(self, headers: dict[str, str]) -> str | None:
        return headers.get("x-request-id") or headers.get("request-id")

    # ------------------------------------------------------------ classification

    def classify_http(self, status: int, headers: dict[str, str], body: bytes) -> TransportFailure:
        """Failure classification for a non-2xx answer.

        4xx responses are definitive rejections. 5xx/408 are treated as ambiguous because a
        gateway timeout can follow processing; exposure is retained until reconciled.
        """
        request_id = self.provider_request_id(headers)
        clipped = body[:MAX_ERROR_BODY_BYTES]
        if status == 429:
            return TransportFailure(
                kind=FailureKind.RATE_LIMITED,
                code="rate_limited",
                retryable=True,
                retry_after_seconds=_retry_after(headers),
                provider_request_id=request_id,
                http_status=status,
                body=clipped,
            )
        if status == 408 or status >= 500:
            return TransportFailure(
                kind=FailureKind.AMBIGUOUS,
                code=f"http_{status}",
                retryable=True,
                provider_request_id=request_id,
                http_status=status,
                body=clipped,
                retry_after_seconds=_retry_after(headers),
            )
        code = (
            "auth_failed"
            if status in {401, 403}
            else ("unexpected_redirect" if 300 <= status < 400 else f"http_{status}")
        )
        return TransportFailure(
            kind=FailureKind.REJECTED,
            code=code,
            retryable=False,
            provider_request_id=request_id,
            http_status=status,
            body=clipped,
        )

    def classify_transport_error(self, error: Exception) -> TransportFailure:
        if isinstance(error, EndpointPolicyViolation):
            return TransportFailure(
                kind=FailureKind.REJECTED, code="endpoint_policy_violation", retryable=False
            )
        if isinstance(error, TransportError):
            if error.phase == "build":
                # Rejected locally before any byte was sent; retrying the same bytes cannot help.
                return TransportFailure(kind=FailureKind.REJECTED, code=error.code, retryable=False)
            if not error.request_may_have_been_sent:
                return TransportFailure(
                    kind=FailureKind.NOT_DELIVERED, code=error.code, retryable=True
                )
            code = "timeout_after_send" if error.timed_out else error.code
            return TransportFailure(
                kind=FailureKind.AMBIGUOUS, code=code, retryable=error.retryable
            )
        return TransportFailure(kind=FailureKind.AMBIGUOUS, code="unexpected_error", retryable=True)

    # ------------------------------------------------------------------ generate

    async def generate(
        self,
        transport: HttpTransport,
        endpoint: RegisteredEndpoint,
        config: ModelConfig,
        request: ModelRequest,
        secret: Secret | None,
        *,
        timeout_seconds: float = 120.0,
    ) -> ModelResponse:
        """Convenience single exchange (smoke checks). The gateway uses the split steps so the
        raw bytes are persisted before parsing."""
        wire = self.build_request(config, request)
        headers = self.authenticate(dict(wire.headers), secret)
        try:
            answer = await transport.send(
                endpoint, wire.path, headers, wire.body, timeout_seconds=timeout_seconds
            )
        except (TransportError, EndpointPolicyViolation) as error:
            raise _FailureRaised(self.classify_transport_error(error)) from None
        if not 200 <= answer.status < 300:
            raise _FailureRaised(self.classify_http(answer.status, answer.headers, answer.body))
        return self.parse_response(answer.body, answer.headers)


class _FailureRaised(Exception):
    def __init__(self, failure: TransportFailure) -> None:
        super().__init__(failure.code)
        self.failure = failure


class MalformedResponse(Exception):
    """A 2xx body that does not follow the provider contract (outcome is ambiguous)."""


def loads_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise MalformedResponse("response is not JSON") from None
    if not isinstance(value, dict):
        raise MalformedResponse("response is not a JSON object")
    return value


def non_negative_int(value: Any) -> int | None:
    """Usage counters: only genuine non-negative integers count; anything else is unknown."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


def _retry_after(headers: dict[str, str]) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None  # HTTP-date form is not honoured; the throttle applies its default backoff
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)
