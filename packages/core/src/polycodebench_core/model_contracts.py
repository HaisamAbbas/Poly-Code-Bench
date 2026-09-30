"""Provider-neutral model gateway contracts: requests, responses, usage and failures.

Nothing here performs I/O. Adapters translate these types to provider wire formats and
back; the gateway persists them. Unknown provider facts are represented as ``None`` and
are never coerced to zero.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from polycodebench_core.application_errors import ServiceError
from polycodebench_core.canonical import sha256_bytes

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_SAFE_TOKENS = 2**53 - 1


def stable_json_bytes(value: Any) -> bytes:
    """Deterministic JSON for provider documents, which may legitimately contain floats."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ProviderKind(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    LOCAL = "local"


class UsageCounter(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    REASONING = "reasoning"
    CACHED_INPUT = "cached_input"


class CapabilityControl(StrEnum):
    """Named controls a cohort exception may waive. Each is recorded, never implicit."""

    TOOLS = "tools"
    STRUCTURED_OUTPUT = "structured_output"
    SEED = "seed"
    TEMPERATURE = "temperature"
    REASONING = "reasoning"
    CONTEXT = "context"
    USAGE = "usage"


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    OTHER = "other"


class ModelCapabilities(Strict):
    """What an endpoint/model can do. Adapters declare wire ceilings; operators declare
    model facts. Effective capability is the conjunction; nothing is inferred from names."""

    native_tools: bool
    structured_output: bool
    seed: bool
    seed_min: int | None = None
    seed_max: int | None = None
    temperature: bool
    reasoning_efforts: tuple[str, ...] = ()
    reasoning_budget_tokens: bool = False
    streaming: bool = False
    context_limit_tokens: Annotated[int, Field(ge=1, le=MAX_SAFE_TOKENS)] | None = None
    max_output_tokens_limit: Annotated[int, Field(ge=1, le=MAX_SAFE_TOKENS)] | None = None
    usage_counters: frozenset[UsageCounter] = frozenset()
    # True only when the provider documents that the output cap bounds every billed output
    # token, including any reasoning tokens; without it no strict money bound exists.
    output_cap_bounds_all_billed_output: bool = False
    output_limit_parameter: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"

    @field_serializer("usage_counters")
    def _stable_counters(self, value: frozenset[UsageCounter]) -> list[str]:
        return sorted(item.value for item in value)

    @model_validator(mode="after")
    def seed_range_is_declared(self) -> ModelCapabilities:
        if self.seed:
            if self.seed_min is None or self.seed_max is None or self.seed_min > self.seed_max:
                raise ValueError("seed support requires a declared provider seed range")
        elif self.seed_min is not None or self.seed_max is not None:
            raise ValueError("seed range is only valid when seed is supported")
        if len(set(self.reasoning_efforts)) != len(self.reasoning_efforts):
            raise ValueError("reasoning efforts must be unique")
        return self


class PriceSnapshot(Strict):
    """Operator-supplied price evidence. The gateway never invents prices."""

    price_id: str
    currency: Literal["USD"] = "USD"
    input_micro_usd_per_million_tokens: Annotated[int, Field(ge=0, le=10**12)]
    output_micro_usd_per_million_tokens: Annotated[int, Field(ge=0, le=10**12)]
    basis: Literal["published_list_price", "contract_price", "operator_attested_self_hosted"]
    source: Annotated[str, Field(min_length=1, max_length=512)]
    effective_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    # Tokens are at most this many per UTF-8 request byte (parts per million). The default
    # of one token per byte is a provable upper bound for byte-level tokenizers.
    input_tokens_per_byte_ppm: Annotated[int, Field(ge=1, le=1_000_000)] = 1_000_000

    @field_validator("price_id")
    @classmethod
    def safe_price_id(cls, value: str) -> str:
        if not _SAFE_NAME.fullmatch(value):
            raise ValueError("price_id must be a safe identifier")
        return value


class CapabilityException(Strict):
    """A named, reviewed cohort exception. The waived control is recorded in the run."""

    control: CapabilityControl
    reason: Annotated[str, Field(min_length=8, max_length=512)]
    approved_by: Annotated[str, Field(min_length=1, max_length=255)]


class ReasoningRequest(Strict):
    effort: str | None = None
    budget_tokens: Annotated[int, Field(ge=1, le=MAX_SAFE_TOKENS)] | None = None

    @model_validator(mode="after")
    def one_control(self) -> ReasoningRequest:
        if (self.effort is None) == (self.budget_tokens is None):
            raise ValueError("reasoning requires exactly one of effort or budget_tokens")
        return self


class TextBlock(Strict):
    kind: Literal["text"] = "text"
    text: str


class ToolCallBlock(Strict):
    kind: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments_json: str  # exactly what the model produced, preserved even when invalid
    arguments_valid: bool = True


class ToolResultBlock(Strict):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    content: str
    is_error: bool = False


ContentBlock = Annotated[TextBlock | ToolCallBlock | ToolResultBlock, Field(discriminator="kind")]


class Message(Strict):
    role: Literal["user", "assistant"]
    blocks: tuple[ContentBlock, ...] = Field(min_length=1)
    # Opaque provider-native assistant message (thinking blocks, thought signatures). The
    # producing adapter re-emits it verbatim so multi-turn semantics are preserved.
    provider_payload: dict[str, Any] | None = None
    provider_payload_origin: ProviderKind | None = None

    @model_validator(mode="after")
    def payload_has_origin(self) -> Message:
        if (self.provider_payload is None) != (self.provider_payload_origin is None):
            raise ValueError("provider payload and origin must be supplied together")
        if self.provider_payload is not None and self.role != "assistant":
            raise ValueError("only assistant messages carry provider payloads")
        return self


class ToolSpec(Strict):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")]
    description: Annotated[str, Field(max_length=2048)]
    parameters_schema: dict[str, Any]


class ResponseSchema(Strict):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")]
    json_schema: dict[str, Any]


class ModelRequest(Strict):
    """One canonical request. ``seed`` is the provider seed after deterministic mapping."""

    system: str | None = None
    messages: tuple[Message, ...] = Field(min_length=1)
    tools: tuple[ToolSpec, ...] = ()
    response_schema: ResponseSchema | None = None
    temperature: Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{6}$")] | None = None
    seed: int | None = None
    reasoning: ReasoningRequest | None = None
    max_output_tokens: Annotated[int, Field(ge=1, le=MAX_SAFE_TOKENS)]

    @model_validator(mode="after")
    def unique_tool_names(self) -> ModelRequest:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return stable_json_bytes(self.model_dump(mode="json", exclude_none=False))

    def digest(self) -> str:
        return sha256_bytes(self.canonical_bytes())


class Usage(Strict):
    """Normalized counters. ``output_tokens`` is total billed output including reasoning;
    ``reasoning_tokens`` is the reported subset. ``None`` means the provider did not say."""

    input_tokens: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)] | None = None
    output_tokens: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)] | None = None
    reasoning_tokens: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)] | None = None
    cached_input_tokens: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)] | None = None
    reported_cost_micro_usd: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)] | None = None

    @property
    def complete(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None

    def availability(self) -> dict[str, bool]:
        return {
            "input_tokens": self.input_tokens is not None,
            "output_tokens": self.output_tokens is not None,
            "reasoning_tokens": self.reasoning_tokens is not None,
            "cached_input_tokens": self.cached_input_tokens is not None,
            "reported_cost": self.reported_cost_micro_usd is not None,
        }


class ModelResponse(Strict):
    provider_request_id: str | None
    provider_response_id: str | None = None
    finish_reason: FinishReason
    raw_finish_reason: str | None
    blocks: tuple[TextBlock | ToolCallBlock, ...]
    usage: Usage
    provider_revision: str | None = None
    provider_payload: dict[str, Any] | None = None
    provider_payload_origin: ProviderKind | None = None

    @property
    def tool_calls(self) -> tuple[ToolCallBlock, ...]:
        return tuple(block for block in self.blocks if isinstance(block, ToolCallBlock))

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.blocks if isinstance(block, TextBlock))


class FailureKind(StrEnum):
    # The provider demonstrably never accepted work: safe to treat as unbilled.
    NOT_DELIVERED = "not_delivered"
    # Provider answered with a definitive rejection (4xx); no generation occurred.
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"
    # Request may have been processed and billed; the outcome is unknown.
    AMBIGUOUS = "ambiguous"


class TransportFailure(Strict):
    kind: FailureKind
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    retryable: bool
    retry_after_seconds: float | None = None
    provider_request_id: str | None = None
    http_status: int | None = None
    body: bytes | None = None


class CallScope(Strict):
    kind: Literal["attempt", "evaluation"]
    scope_id: UUID


class ReservationPlan(Strict):
    """Conservative exposure reserved before a delivery is dispatched."""

    money_micro_usd: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)]
    input_tokens: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)]
    output_tokens: Annotated[int, Field(ge=0, le=MAX_SAFE_TOKENS)]
    turns: Annotated[int, Field(ge=0, le=1)]


class ModelGatewayError(ServiceError):
    code = "MODEL_GATEWAY_ERROR"
    status_code = 502


class CapabilityUnsupported(ModelGatewayError):
    code = "CAPABILITY_UNSUPPORTED"
    status_code = 422


class EndpointNotApproved(ModelGatewayError):
    code = "ENDPOINT_NOT_APPROVED"
    status_code = 403


class EndpointPolicyViolation(ModelGatewayError):
    code = "ENDPOINT_POLICY_VIOLATION"
    status_code = 422


class BudgetExhausted(ModelGatewayError):
    code = "BUDGET_EXHAUSTED"
    status_code = 429


class ScopeNotActive(ModelGatewayError):
    code = "SCOPE_NOT_ACTIVE"
    status_code = 409


class BudgetAccountMissing(ModelGatewayError):
    code = "BUDGET_ACCOUNT_MISSING"
    status_code = 422


class CostBoundUnavailable(ModelGatewayError):
    code = "COST_BOUND_UNAVAILABLE"
    status_code = 422


class ProviderCallFailed(ModelGatewayError):
    """A delivery ended without a usable response; the failure is persisted."""

    code = "PROVIDER_CALL_FAILED"

    def __init__(self, failure: TransportFailure, *, intent_id: str, exposure_retained: bool):
        super().__init__(f"{failure.kind.value}:{failure.code}")
        self.failure = failure
        self.intent_id = intent_id
        self.exposure_retained = exposure_retained


class ModelAdapter(Protocol):
    """Provider adapter. Provider-specific message conversion stays inside adapters."""

    provider_kind: ProviderKind

    def capabilities(self) -> ModelCapabilities: ...

    def validate(self, config: Any, protocol: Any) -> None: ...

    def build_request(self, config: Any, request: ModelRequest) -> Any: ...

    def parse_response(self, body: bytes, headers: dict[str, str]) -> ModelResponse: ...

    def classify_transport_error(self, error: Exception) -> TransportFailure: ...
