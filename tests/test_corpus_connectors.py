from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from polycodebench_core.corpus_connectors import (
    ConnectorExecutionRecord,
    ConnectorOperation,
    ConnectorRequestObservation,
    OptionalIndexQueryMetadata,
    SketchErrorModel,
    SourceConnectorPlan,
    SourceConnectorPlanRequest,
)
from polycodebench_services.benchmark_audit_catalog import load_audit_catalog
from polycodebench_services.corpus_connectors import (
    build_connector_coverage_report,
    build_source_connector_plan,
    execute_source_connector_plan,
    optional_index_tool_capabilities,
    source_connector_capabilities,
    source_connector_plan_digest,
)
from pydantic import ValidationError

ROOT = Path(__file__).parents[1]
BUNDLE = load_audit_catalog(ROOT / "config" / "benchmark-audit")
POLICIES = {item.slug: item for item in BUNDLE.source_policies.groups}
DIGEST = "sha256:" + "a" * 64


def _request(
    policy_slug: str = "common-crawl",
    *,
    source_uri: str | None = None,
    operation: ConnectorOperation = "fetch",
    private_query: bool = False,
    disclosure: object | None = None,
    max_requests: int = 10,
    max_response_bytes: int = 1024,
    max_total_bytes: int = 10_240,
    minimum_request_interval_ms: int = 1_000,
) -> SourceConnectorPlanRequest:
    policy = POLICIES[policy_slug]
    values: dict[str, object] = {
        "source_group": policy_slug,
        "operation": operation,
        "source_uri": source_uri or f"https://{policy.allowed_hosts[0]}/snapshot/revision-1",
        "source_revision": "snapshot-2026-01",
        "scope_digest": DIGEST,
        "credential_ref": None,
        "max_requests": max_requests,
        "max_response_bytes": max_response_bytes,
        "max_total_bytes": max_total_bytes,
        "request_timeout_ms": 5_000,
        "total_timeout_ms": 60_000,
        "minimum_request_interval_ms": minimum_request_interval_ms,
        "max_retries": 0,
        "private_query": private_query,
        "query_payload_digest": DIGEST if operation == "query" else None,
        "disclosure": disclosure,
    }
    return SourceConnectorPlanRequest.model_validate(values)


def _plan(request: SourceConnectorPlanRequest | None = None) -> SourceConnectorPlan:
    request = request or _request()
    return build_source_connector_plan(POLICIES[request.source_group], request)


def _hypothetical_dispatchable_plan() -> SourceConnectorPlan:
    """Exercise coverage accounting without representing an approved real source plan."""
    values = _plan().model_dump(mode="python")
    values["state"] = "planned"
    values["blocker_codes"] = ()
    return SourceConnectorPlan.model_validate(values)


def _execution(plan: SourceConnectorPlan, **updates: object) -> ConnectorExecutionRecord:
    values: dict[str, object] = {
        "plan_digest": source_connector_plan_digest(plan),
        "state": "complete",
        "requests_attempted": 1,
        "elapsed_ms": 10,
        "request_observations": (
            ConnectorRequestObservation(
                logical_request=0,
                retry_index=0,
                started_after_ms=0,
                elapsed_ms=10,
                response_bytes=100,
            ),
        ),
        "bytes_received": 100,
        "metadata_records": 1,
        "content_records": 1,
        "extracted_text_records": 1,
        "source_date_records": 1,
        "rights_evidence_records": 1,
        "eligible_metadata_records": 1,
        "eligible_content_records": 1,
        "eligible_extracted_text_records": 1,
        "eligible_source_date_records": 1,
        "eligible_rights_evidence_records": 1,
        "extraction_failures": 0,
        "error_codes": (),
    }
    values.update(updates)
    return ConnectorExecutionRecord.model_validate(values)


def test_eight_source_groups_have_specific_contract_only_capabilities() -> None:
    capabilities = source_connector_capabilities()

    assert {item.source_group for item in capabilities} == set(POLICIES)
    assert all(item.state == "contract_only" for item in capabilities)
    assert all(
        set(item.operations) == {"metadata", "fetch", "extract", "query"} for item in capabilities
    )
    by_slug = {item.source_group: item for item in capabilities}
    assert {"url_index_record", "warc_response", "extracted_text"} <= set(
        by_slug["common-crawl"].record_kinds
    )
    assert "pdf_bytes" in by_slug["arxiv"].record_kinds
    assert "post_revision" in by_slug["stack-exchange"].record_kinds
    assert "dump_revision" in by_slug["wikipedia"].record_kinds
    assert all(policy.authorization_state == "not_approved" for policy in POLICIES.values())
    assert all(policy.connector_state == "not_implemented" for policy in POLICIES.values())
    assert all(policy.conformance_state == "not_run" for policy in POLICIES.values())


def test_unapproved_source_plan_is_blocked_and_execution_performs_no_io() -> None:
    plan = _plan()

    assert plan.state == "blocked"
    assert {
        "source_authorization_not_approved",
        "source_connector_not_registered",
        "source_conformance_not_live_verified",
        "source_scope_manifest_unverified",
        "source_revision_pin_unverified",
        "connector_runtime_not_implemented",
    } <= set(plan.blocker_codes)
    execution = execute_source_connector_plan(plan)
    assert execution.state == "blocked"
    assert execution.requests_attempted == 0
    assert execution.bytes_received == 0
    report = build_connector_coverage_report(plan, execution)
    assert {dimension.dimension for dimension in report.dimensions} == {
        "url_metadata",
        "source_content",
        "extracted_text",
        "source_dates",
        "rights_evidence",
    }
    assert all(dimension.state == "blocked" for dimension in report.dimensions)
    assert all(dimension.observed_records == 0 for dimension in report.dimensions)
    assert report.scope_digest == plan.scope_digest
    assert report.plan_digest == source_connector_plan_digest(plan)


@pytest.mark.parametrize(
    "uri",
    [
        "http://index.commoncrawl.org/CC-MAIN-2026-01-index",
        "https://attacker.invalid/",
        "https://user@index.commoncrawl.org/snapshot",
        "https://index.commoncrawl.org:443/snapshot",
        "https://index.commoncrawl.org/snapshot?",
        "https://index.commoncrawl.org/snapshot#",
        "https://index.commoncrawl.org/%2e%2e/private",
        "https://index.commoncrawl.org/a/../private",
        "https://index.commoncrawl.org/a\\..\\private",
        "https://index.commoncrawl.org/snapshot\n",
    ],
)
def test_source_uri_rejects_noncanonical_or_out_of_scope_egress(uri: str) -> None:
    with pytest.raises(ValueError, match="canonical ASCII|exact HTTPS"):
        _plan(_request(source_uri=uri))


def test_source_plan_rejects_unregistered_host_policy_mismatch_and_budget_overruns() -> None:
    with pytest.raises(ValueError, match="different source groups"):
        build_source_connector_plan(POLICIES["github"], _request("common-crawl"))
    with pytest.raises(ValueError, match="request budget"):
        _plan(_request(max_requests=2_001))
    with pytest.raises(ValueError, match="response byte budget"):
        _plan(_request(max_response_bytes=5_242_881, max_total_bytes=10_000))
    with pytest.raises(ValidationError, match="minimum_request_interval_ms"):
        _request(minimum_request_interval_ms=0)
    with pytest.raises(ValidationError, match="total byte cap"):
        _request(max_total_bytes=10_241)

    credential_request = _request().model_copy(
        update={"credential_ref": UUID("d29c5107-7c48-4599-8a4b-bb8478a38948")}
    )
    credential_plan = _plan(credential_request)
    assert credential_plan.state == "blocked"
    assert "credential_authorization_verifier_unavailable" in credential_plan.blocker_codes


def test_private_query_digest_and_disclosure_do_not_substitute_for_authorization() -> None:
    host = POLICIES["github"].allowed_hosts[0]
    disclosure = {
        "tenant_id": UUID("d29c5107-7c48-4599-8a4b-bb8478a38948"),
        "recipient_host": host,
        "query_payload_digest": DIGEST,
        "authorization_evidence_digest": "sha256:" + "b" * 64,
    }
    request = _request(
        "github",
        source_uri=f"https://{host}/owner/repository",
        operation="query",
        private_query=True,
        disclosure=disclosure,
    )

    plan = _plan(request)

    assert plan.state == "blocked"
    assert plan.disclosure is not None
    assert plan.disclosure.recipient_host == host
    assert plan.disclosure.query_payload_digest == DIGEST
    assert plan.exposure_intent is None
    assert "private_remote_query_authorization_verifier_unavailable" in plan.blocker_codes
    assert "exact_remote_disclosure_authorization_missing" not in plan.blocker_codes
    assert execute_source_connector_plan(plan).requests_attempted == 0


def test_private_query_without_exact_disclosure_is_blocked() -> None:
    request = _request(
        "github",
        source_uri="https://github.com/owner/repository",
        operation="query",
        private_query=True,
    )

    plan = _plan(request)

    assert plan.state == "blocked"
    assert plan.exposure_intent is None
    assert "exact_remote_disclosure_authorization_missing" in plan.blocker_codes

    wrong_recipient = _request(
        "github",
        source_uri="https://github.com/owner/repository",
        operation="query",
        private_query=True,
        disclosure={
            "tenant_id": UUID("d29c5107-7c48-4599-8a4b-bb8478a38948"),
            "recipient_host": "api.github.com",
            "query_payload_digest": DIGEST,
            "authorization_evidence_digest": "sha256:" + "b" * 64,
        },
    )
    mismatch_plan = _plan(wrong_recipient)
    assert mismatch_plan.disclosure is None
    assert mismatch_plan.exposure_intent is None
    assert "exact_remote_disclosure_authorization_missing" in mismatch_plan.blocker_codes


def test_coverage_does_not_call_unbounded_results_complete() -> None:
    plan = _hypothetical_dispatchable_plan()
    execution = _execution(plan)

    report = build_connector_coverage_report(plan, execution)

    assert all(dimension.state == "complete" for dimension in report.dimensions)
    assert all(dimension.eligible_records == 1 for dimension in report.dimensions)
    unknown_denominator = _execution(
        plan,
        eligible_metadata_records=None,
        eligible_content_records=None,
        eligible_extracted_text_records=None,
        eligible_source_date_records=None,
        eligible_rights_evidence_records=None,
    )
    unknown_report = build_connector_coverage_report(plan, unknown_denominator)
    assert all(dimension.state == "unknown" for dimension in unknown_report.dimensions)
    assert all(
        dimension.reason_code == "eligible_scope_denominator_not_recorded"
        for dimension in unknown_report.dimensions
    )


def test_coverage_keeps_url_metadata_separate_from_content_and_extraction() -> None:
    plan = _hypothetical_dispatchable_plan()
    execution = _execution(
        plan,
        metadata_records=2,
        eligible_metadata_records=2,
        content_records=1,
        eligible_content_records=2,
        extracted_text_records=0,
        eligible_extracted_text_records=1,
    )

    report = build_connector_coverage_report(plan, execution)
    dimensions = {item.dimension: item for item in report.dimensions}

    assert dimensions["url_metadata"].state == "complete"
    assert dimensions["source_content"].state == "partial"
    assert dimensions["extracted_text"].state == "partial"
    assert dimensions["source_content"].observed_records == 1
    assert dimensions["extracted_text"].observed_records == 0


def test_execution_and_coverage_reject_inconsistent_or_over_budget_evidence() -> None:
    plan = _hypothetical_dispatchable_plan()
    execution = _execution(plan)
    with pytest.raises(ValueError, match="different frozen plan"):
        build_connector_coverage_report(plan, execution.model_copy(update={"plan_digest": DIGEST}))
    with pytest.raises(ValueError, match="byte limit"):
        oversized_response = ConnectorRequestObservation(
            logical_request=0,
            retry_index=0,
            started_after_ms=0,
            elapsed_ms=10,
            response_bytes=plan.max_total_bytes + 1,
        )
        too_many_bytes = ConnectorExecutionRecord.model_validate(
            {
                **execution.model_dump(mode="python"),
                "request_observations": (oversized_response,),
                "bytes_received": oversized_response.response_bytes,
            }
        )
        build_connector_coverage_report(plan, too_many_bytes)
    single_response_over_cap = ConnectorRequestObservation(
        logical_request=0,
        retry_index=0,
        started_after_ms=0,
        elapsed_ms=10,
        response_bytes=plan.max_response_bytes + 1,
    )
    with pytest.raises(ValueError, match="response byte limit"):
        build_connector_coverage_report(
            plan,
            _execution(
                plan,
                request_observations=(single_response_over_cap,),
                bytes_received=single_response_over_cap.response_bytes,
            ),
        )
    with pytest.raises(ValidationError, match="extracted text records"):
        _execution(plan, extracted_text_records=2)
    with pytest.raises(ValidationError, match="truncated execution requires error codes"):
        _execution(plan, state="truncated", error_codes=())

    blocked_plan = _plan()
    with pytest.raises(ValueError, match="blocked source plans"):
        build_connector_coverage_report(blocked_plan, _execution(blocked_plan))


def test_execution_enforces_rate_time_response_retry_and_total_byte_caps() -> None:
    plan = _hypothetical_dispatchable_plan()
    first = ConnectorRequestObservation(
        logical_request=0,
        retry_index=0,
        started_after_ms=0,
        elapsed_ms=10,
        response_bytes=100,
    )
    second = ConnectorRequestObservation(
        logical_request=1,
        retry_index=0,
        started_after_ms=500,
        elapsed_ms=10,
        response_bytes=100,
    )
    too_fast = _execution(
        plan,
        requests_attempted=2,
        elapsed_ms=510,
        request_observations=(first, second),
        bytes_received=200,
    )
    with pytest.raises(ValueError, match="minimum request interval"):
        build_connector_coverage_report(plan, too_fast)

    slow_request = ConnectorRequestObservation(
        logical_request=0,
        retry_index=0,
        started_after_ms=0,
        elapsed_ms=plan.request_timeout_ms + 1,
        response_bytes=100,
    )
    with pytest.raises(ValueError, match="request timeout"):
        build_connector_coverage_report(
            plan,
            _execution(
                plan,
                elapsed_ms=slow_request.elapsed_ms,
                request_observations=(slow_request,),
            ),
        )

    retry = ConnectorRequestObservation(
        logical_request=0,
        retry_index=1,
        started_after_ms=0,
        elapsed_ms=10,
        response_bytes=100,
    )
    with pytest.raises(ValueError, match="retry limit"):
        build_connector_coverage_report(
            plan,
            _execution(plan, request_observations=(retry,)),
        )

    too_much_total_time = _execution(plan, elapsed_ms=plan.total_timeout_ms + 1)
    with pytest.raises(ValueError, match="total timeout"):
        build_connector_coverage_report(plan, too_much_total_time)


def test_optional_tools_are_unconfigured_and_never_claim_closed_model_training_membership() -> None:
    capabilities = optional_index_tool_capabilities()

    assert {item.tool_name for item in capabilities} == {"data-portraits", "infini-gram"}
    assert all(item.state == "not_configured" for item in capabilities)
    assert all(item.query_state == "not_run" for item in capabilities)
    assert all(item.corpus_version is None for item in capabilities)
    assert all(item.training_membership_claim_allowed is False for item in capabilities)


def test_optional_tool_query_contract_pins_metadata_and_keeps_results_candidate_only() -> None:
    sketch_query = OptionalIndexQueryMetadata.model_validate(
        {
            "tool_name": "data-portraits",
            "corpus_version": "identified-corpus-v1",
            "corpus_digest": DIGEST,
            "adapter_version": "adapter-v1",
            "query_digest": DIGEST,
            "query_state": "candidate_only",
            "result_digest": DIGEST,
            "result_count": 1,
            "sketch_digest": DIGEST,
            "method_version": "sketch-method-v1",
            "sketch_error_model": {
                "version": "error-model-v1",
                "model_digest": DIGEST,
                "false_positive_rate": "0.010000",
                "false_negative_rate": "0.000000",
            },
            "index_digest": None,
            "result_positions": (),
            "error_code": None,
        }
    )
    index_query = OptionalIndexQueryMetadata.model_validate(
        {
            "tool_name": "infini-gram",
            "corpus_version": "identified-index-v2",
            "corpus_digest": DIGEST,
            "adapter_version": "adapter-v1",
            "query_digest": DIGEST,
            "query_state": "candidate_only",
            "result_digest": DIGEST,
            "result_count": 2,
            "sketch_digest": None,
            "method_version": None,
            "sketch_error_model": None,
            "index_digest": DIGEST,
            "result_positions": (4, 20),
            "error_code": None,
        }
    )

    assert sketch_query.training_membership_claim_allowed is False
    assert sketch_query.sketch_error_model is not None
    assert sketch_query.sketch_error_model.false_positive_rate == "0.010000"
    assert index_query.result_positions == (4, 20)
    assert index_query.training_membership_claim_allowed is False


def test_optional_tool_query_rejects_missing_error_evidence_and_training_claims() -> None:
    base = {
        "tool_name": "data-portraits",
        "corpus_version": "identified-corpus-v1",
        "corpus_digest": DIGEST,
        "adapter_version": "adapter-v1",
        "query_digest": DIGEST,
        "query_state": "candidate_only",
        "result_digest": DIGEST,
        "result_count": 1,
        "sketch_digest": DIGEST,
        "method_version": "sketch-method-v1",
        "sketch_error_model": {
            "version": "error-model-v1",
            "model_digest": DIGEST,
            "false_positive_rate": "0.010000",
            "false_negative_rate": "0.000000",
        },
        "index_digest": None,
        "result_positions": (),
        "error_code": None,
    }
    with pytest.raises(ValidationError, match="error model"):
        OptionalIndexQueryMetadata.model_validate({**base, "sketch_error_model": None})
    with pytest.raises(ValidationError, match="training_membership_claim_allowed"):
        OptionalIndexQueryMetadata.model_validate(
            {**base, "training_membership_claim_allowed": True}
        )
    with pytest.raises(ValidationError, match="false_positive_rate"):
        SketchErrorModel.model_validate(
            {
                "version": "error-model-v1",
                "model_digest": DIGEST,
                "false_positive_rate": "0.0101",
                "false_negative_rate": "0.000000",
            }
        )
