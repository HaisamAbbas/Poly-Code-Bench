"""Immutable source-connector plans and coverage contracts; these contracts do not dispatch I/O."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from polycodebench_core.models import Digest

SourceGroupSlug = Literal[
    "common-crawl",
    "github",
    "hugging-face",
    "arxiv",
    "stack-exchange",
    "wikipedia",
    "benchmark-repositories",
    "other-public-datasets",
]
ConnectorOperation = Literal["metadata", "fetch", "extract", "query"]
ConnectorPlanState = Literal["planned", "blocked"]
ConnectorExecutionState = Literal["complete", "truncated", "failed", "blocked"]
CoverageState = Literal["complete", "partial", "blocked", "unknown"]
CoverageDimensionName = Literal[
    "url_metadata",
    "source_content",
    "extracted_text",
    "source_dates",
    "rights_evidence",
]


class SourceConnectorCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_group: SourceGroupSlug
    record_kinds: tuple[str, ...] = Field(min_length=1, max_length=12)
    operations: tuple[ConnectorOperation, ...] = Field(min_length=1, max_length=4)
    state: Literal["contract_only"] = "contract_only"
    limitations: tuple[str, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_capabilities(self) -> SourceConnectorCapability:
        if len(set(self.record_kinds)) != len(self.record_kinds):
            raise ValueError("source record kinds must be unique")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("source operations must be unique")
        return self


class RemoteQueryDisclosure(BaseModel):
    """A data-only binding; the dispatcher must separately verify persisted authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: UUID
    recipient_host: str = Field(min_length=1, max_length=253)
    query_payload_digest: Digest
    authorization_evidence_digest: Digest


class RemoteExposureIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: UUID
    recipient_host: str = Field(min_length=1, max_length=253)
    query_payload_digest: Digest
    authorization_evidence_digest: Digest
    state: Literal["prepared_not_sent"] = "prepared_not_sent"


class SourceConnectorPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_group: SourceGroupSlug
    operation: ConnectorOperation
    source_uri: str = Field(min_length=8, max_length=2048)
    source_revision: str = Field(min_length=1, max_length=255)
    scope_digest: Digest
    credential_ref: UUID | None
    max_requests: int = Field(ge=1, le=100_000)
    max_response_bytes: int = Field(ge=1, le=1_073_741_824)
    max_total_bytes: int = Field(ge=1, le=9_007_199_254_740_991)
    request_timeout_ms: int = Field(ge=1, le=120_000)
    total_timeout_ms: int = Field(ge=1, le=3_600_000)
    minimum_request_interval_ms: int = Field(ge=1_000, le=60_000)
    max_retries: int = Field(ge=0, le=3)
    private_query: bool
    query_payload_digest: Digest | None
    disclosure: RemoteQueryDisclosure | None

    @model_validator(mode="after")
    def validate_request_shape(self) -> SourceConnectorPlanRequest:
        if self.max_total_bytes > self.max_requests * self.max_response_bytes:
            raise ValueError("total byte cap cannot exceed all per-request byte caps")
        if self.request_timeout_ms > self.total_timeout_ms:
            raise ValueError("per-request timeout cannot exceed total plan time")
        if self.operation == "query" and self.query_payload_digest is None:
            raise ValueError("query plans require a payload digest, never query text")
        if self.operation != "query" and (
            self.query_payload_digest is not None or self.private_query
        ):
            raise ValueError("only query plans may carry a private query payload")
        if self.private_query and self.query_payload_digest is None:
            raise ValueError("private query state requires a payload digest")
        if self.disclosure is not None and (
            not self.private_query
            or self.query_payload_digest != self.disclosure.query_payload_digest
        ):
            raise ValueError("disclosure must bind the exact private query payload")
        return self


class SourceConnectorPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_group: SourceGroupSlug
    operation: ConnectorOperation
    source_uri: str = Field(min_length=8, max_length=2048)
    source_host: str = Field(min_length=1, max_length=253)
    source_revision: str = Field(min_length=1, max_length=255)
    scope_digest: Digest
    source_policy_digest: Digest
    connector_state: Literal["contract_only"] = "contract_only"
    credential_ref: UUID | None
    max_requests: int = Field(ge=1, le=100_000)
    max_response_bytes: int = Field(ge=1, le=1_073_741_824)
    max_total_bytes: int = Field(ge=1, le=9_007_199_254_740_991)
    request_timeout_ms: int = Field(ge=1, le=120_000)
    total_timeout_ms: int = Field(ge=1, le=3_600_000)
    minimum_request_interval_ms: int = Field(ge=1_000, le=60_000)
    max_retries: int = Field(ge=0, le=3)
    redirects_allowed: Literal[False] = False
    private_query: bool
    query_payload_digest: Digest | None
    disclosure: RemoteQueryDisclosure | None
    exposure_intent: RemoteExposureIntent | None
    state: ConnectorPlanState
    blocker_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_plan_shape(self) -> SourceConnectorPlan:
        if self.max_total_bytes > self.max_requests * self.max_response_bytes:
            raise ValueError("total byte cap cannot exceed all per-request byte caps")
        if self.request_timeout_ms > self.total_timeout_ms:
            raise ValueError("per-request timeout cannot exceed total plan time")
        if self.operation == "query" and self.query_payload_digest is None:
            raise ValueError("query plans require a payload digest, never query text")
        if self.operation != "query" and self.query_payload_digest is not None:
            raise ValueError("non-query plans cannot carry a query payload digest")
        if self.private_query and (self.operation != "query" or self.query_payload_digest is None):
            raise ValueError("private query state requires a query operation and payload digest")
        if self.disclosure is not None and (
            not self.private_query
            or self.operation != "query"
            or self.disclosure.query_payload_digest != self.query_payload_digest
            or self.disclosure.recipient_host != self.source_host
        ):
            raise ValueError("plan disclosure must bind the exact private query and recipient")
        if self.private_query and self.state == "planned" and self.exposure_intent is None:
            raise ValueError("planned private queries require a prepared exposure intent")
        if self.state == "blocked" and self.exposure_intent is not None:
            raise ValueError("blocked source plans cannot carry a remote exposure intent")
        if self.exposure_intent is not None and (
            self.query_payload_digest != self.exposure_intent.query_payload_digest
            or self.source_host != self.exposure_intent.recipient_host
            or self.disclosure is None
            or self.disclosure.tenant_id != self.exposure_intent.tenant_id
            or self.disclosure.authorization_evidence_digest
            != self.exposure_intent.authorization_evidence_digest
        ):
            raise ValueError("exposure intent does not bind the exact recipient and query digest")
        if self.state == "planned" and self.blocker_codes:
            raise ValueError("planned source connector operations cannot have blockers")
        if self.state == "blocked" and not self.blocker_codes:
            raise ValueError("blocked source connector plans require explicit blockers")
        return self


class ConnectorRequestObservation(BaseModel):
    """Bounded timing and response-size evidence for one actual HTTP attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_request: int = Field(ge=0, le=2_000)
    retry_index: int = Field(ge=0, le=3)
    started_after_ms: int = Field(ge=0, le=3_600_000)
    elapsed_ms: int = Field(ge=0, le=120_000)
    response_bytes: int = Field(ge=0, le=1_073_741_824)


class ConnectorExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    plan_digest: Digest
    state: ConnectorExecutionState
    requests_attempted: int = Field(ge=0, le=100_000)
    elapsed_ms: int = Field(ge=0, le=3_600_000)
    request_observations: tuple[ConnectorRequestObservation, ...] = Field(max_length=2_000)
    bytes_received: int = Field(ge=0, le=9_007_199_254_740_991)
    metadata_records: int = Field(ge=0, le=9_007_199_254_740_991)
    content_records: int = Field(ge=0, le=9_007_199_254_740_991)
    extracted_text_records: int = Field(ge=0, le=9_007_199_254_740_991)
    source_date_records: int = Field(ge=0, le=9_007_199_254_740_991)
    rights_evidence_records: int = Field(ge=0, le=9_007_199_254_740_991)
    eligible_metadata_records: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    eligible_content_records: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    eligible_extracted_text_records: int | None = Field(
        default=None, ge=0, le=9_007_199_254_740_991
    )
    eligible_source_date_records: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    eligible_rights_evidence_records: int | None = Field(
        default=None, ge=0, le=9_007_199_254_740_991
    )
    extraction_failures: int = Field(ge=0, le=9_007_199_254_740_991)
    error_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def terminal_state_is_explicit(self) -> ConnectorExecutionRecord:
        if self.state in {"failed", "blocked", "truncated"} and not self.error_codes:
            raise ValueError("failed, blocked or truncated execution requires error codes")
        if self.state == "complete" and (self.error_codes or self.extraction_failures):
            raise ValueError(
                "complete connector execution cannot hide errors or extraction failures"
            )
        if self.state in {"complete", "truncated"} and self.requests_attempted == 0:
            raise ValueError("executed connector states require at least one attempted request")
        if self.requests_attempted != len(self.request_observations):
            raise ValueError("request count must match the bounded request observation ledger")
        if self.bytes_received != sum(item.response_bytes for item in self.request_observations):
            raise ValueError("received byte count must match the request observation ledger")
        if any(
            item.started_after_ms + item.elapsed_ms > self.elapsed_ms
            for item in self.request_observations
        ):
            raise ValueError("request observations cannot extend beyond total execution time")
        if self.state == "blocked" and any(
            (
                self.requests_attempted,
                self.elapsed_ms,
                self.bytes_received,
                self.metadata_records,
                self.content_records,
                self.extracted_text_records,
                self.source_date_records,
                self.rights_evidence_records,
                self.extraction_failures,
            )
        ):
            raise ValueError("blocked execution must prove that no request or record was observed")
        if self.state == "blocked" and self.request_observations:
            raise ValueError("blocked execution cannot contain request observations")
        if self.requests_attempted == 0 and any(
            (
                self.bytes_received,
                self.metadata_records,
                self.content_records,
                self.extracted_text_records,
                self.source_date_records,
                self.rights_evidence_records,
            )
        ):
            raise ValueError("connector records require at least one recorded request attempt")
        if self.extracted_text_records > self.content_records:
            raise ValueError("extracted text records cannot exceed acquired content records")
        observed = {
            "metadata": (self.metadata_records, self.eligible_metadata_records),
            "content": (self.content_records, self.eligible_content_records),
            "extracted text": (self.extracted_text_records, self.eligible_extracted_text_records),
            "source date": (self.source_date_records, self.eligible_source_date_records),
            "rights evidence": (
                self.rights_evidence_records,
                self.eligible_rights_evidence_records,
            ),
        }
        if any(eligible is not None and count > eligible for count, eligible in observed.values()):
            raise ValueError("observed connector records cannot exceed eligible records")
        if self.state == "blocked" and any(
            eligible is not None
            for eligible in (
                self.eligible_metadata_records,
                self.eligible_content_records,
                self.eligible_extracted_text_records,
                self.eligible_source_date_records,
                self.eligible_rights_evidence_records,
            )
        ):
            raise ValueError("blocked execution cannot declare source eligibility counts")
        return self


class CoverageDimension(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dimension: CoverageDimensionName
    state: CoverageState
    eligible_records: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    observed_records: int = Field(ge=0, le=9_007_199_254_740_991)
    reason_code: str | None

    @model_validator(mode="after")
    def missingness_is_not_zero(self) -> CoverageDimension:
        if self.state == "complete":
            if self.reason_code is not None:
                raise ValueError("complete coverage cannot carry an unknown or blocker reason")
            if self.eligible_records is None or self.observed_records != self.eligible_records:
                raise ValueError("complete coverage requires an exact, fully observed denominator")
        if self.state != "complete" and not self.reason_code:
            raise ValueError("incomplete coverage requires an explicit reason code")
        if self.eligible_records is not None and self.observed_records > self.eligible_records:
            raise ValueError("observed coverage cannot exceed its declared eligible denominator")
        return self


class ConnectorCoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_group: SourceGroupSlug
    source_revision: str = Field(min_length=1, max_length=255)
    scope_digest: Digest
    plan_digest: Digest
    execution: ConnectorExecutionRecord
    dimensions: tuple[CoverageDimension, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def coverage_dimensions_are_unique(self) -> ConnectorCoverageReport:
        dimensions = [item.dimension for item in self.dimensions]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("connector coverage dimensions must be unique")
        expected_dimensions = {
            "url_metadata",
            "source_content",
            "extracted_text",
            "source_dates",
            "rights_evidence",
        }
        if set(dimensions) != expected_dimensions:
            raise ValueError("connector coverage must report all five evidence dimensions")
        if self.plan_digest != self.execution.plan_digest:
            raise ValueError("coverage plan digest must match its execution record")
        if self.execution.state != "complete" and all(
            dimension.state == "complete" for dimension in self.dimensions
        ):
            raise ValueError("incomplete execution cannot claim complete coverage")
        return self


class OptionalIndexToolCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tool_name: Literal["data-portraits", "infini-gram"]
    state: Literal["not_configured", "metadata_only", "fixture_only", "live_verified"]
    corpus_version: str | None
    query_state: Literal["not_run", "candidate_only", "failed"]
    error_code: str | None
    training_membership_claim_allowed: Literal[False] = False

    @model_validator(mode="after")
    def tool_state_is_consistent(self) -> OptionalIndexToolCapability:
        if self.state == "not_configured":
            if self.corpus_version is not None or self.query_state != "not_run":
                raise ValueError("unconfigured optional tools cannot have a corpus or query result")
            if self.error_code is None:
                raise ValueError("unconfigured optional tools require an explicit error code")
        elif self.corpus_version is None:
            raise ValueError("configured optional tools require an exact corpus version")
        if self.state == "metadata_only" and self.query_state != "not_run":
            raise ValueError("metadata-only tools cannot claim a query result")
        if self.state == "fixture_only" and self.query_state == "not_run":
            raise ValueError("fixture-only tools require a recorded fixture query result")
        if self.state == "live_verified" and (
            not self.corpus_version or self.query_state == "not_run"
        ):
            raise ValueError("live optional tools require a pinned corpus and a recorded query")
        if self.query_state == "failed" and not self.error_code:
            raise ValueError("failed optional tool queries require an error code")
        if self.query_state == "candidate_only" and self.state not in {
            "fixture_only",
            "live_verified",
        }:
            raise ValueError("candidate results require fixture or live query evidence")
        if self.query_state != "failed" and self.state != "not_configured" and self.error_code:
            raise ValueError("successful optional tool states cannot carry an error code")
        return self


class SketchErrorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str = Field(min_length=1, max_length=128)
    model_digest: Digest
    false_positive_rate: str = Field(pattern=r"^(?:0\.[0-9]{6}|1\.000000)$")
    false_negative_rate: str = Field(pattern=r"^(?:0\.[0-9]{6}|1\.000000)$")


class OptionalIndexQueryMetadata(BaseModel):
    """Private, candidate-only metadata from an explicitly identified optional index."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tool_name: Literal["data-portraits", "infini-gram"]
    corpus_version: str = Field(min_length=1, max_length=255)
    corpus_digest: Digest
    adapter_version: str = Field(min_length=1, max_length=128)
    query_digest: Digest
    query_state: Literal["candidate_only", "failed"]
    result_digest: Digest | None
    result_count: int | None = Field(default=None, ge=0, le=9_007_199_254_740_991)
    sketch_digest: Digest | None
    method_version: str | None = Field(default=None, min_length=1, max_length=128)
    sketch_error_model: SketchErrorModel | None
    index_digest: Digest | None
    result_positions: tuple[int, ...] = Field(max_length=10_000)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    training_membership_claim_allowed: Literal[False] = False

    @model_validator(mode="after")
    def optional_tool_result_is_candidate_only(self) -> OptionalIndexQueryMetadata:
        if self.query_state == "failed":
            if not self.error_code:
                raise ValueError("failed optional index queries require an error code")
            if (
                self.result_digest is not None
                or self.result_count is not None
                or self.result_positions
            ):
                raise ValueError("failed optional index queries cannot carry result evidence")
        else:
            if (
                self.error_code is not None
                or self.result_digest is None
                or self.result_count is None
            ):
                raise ValueError("candidate results require a digest, count and no error code")
        if self.tool_name == "data-portraits":
            if (
                self.sketch_digest is None
                or self.method_version is None
                or self.sketch_error_model is None
                or self.index_digest is not None
                or self.result_positions
            ):
                raise ValueError("Data Portraits metadata requires sketch, method and error model")
        elif (
            self.index_digest is None
            or self.sketch_digest is not None
            or self.method_version is not None
            or self.sketch_error_model is not None
        ):
            raise ValueError("infini-gram metadata requires its exact index digest only")
        if self.result_count is not None and len(self.result_positions) > self.result_count:
            raise ValueError("result positions cannot exceed the declared candidate count")
        if (
            any(position < 0 for position in self.result_positions)
            or tuple(sorted(set(self.result_positions))) != self.result_positions
        ):
            raise ValueError("result positions must be unique, nonnegative and ordered")
        return self
