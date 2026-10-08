"""Policy-bounded source plans and explicit coverage; this module never performs network I/O."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from polycodebench_core.benchmark_audit_registry import SourceGroupPolicy
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.corpus_connectors import (
    ConnectorCoverageReport,
    ConnectorExecutionRecord,
    CoverageDimension,
    CoverageDimensionName,
    CoverageState,
    OptionalIndexToolCapability,
    RemoteExposureIntent,
    SourceConnectorCapability,
    SourceConnectorPlan,
    SourceConnectorPlanRequest,
)

_SOURCE_CAPABILITIES: dict[str, SourceConnectorCapability] = {
    "common-crawl": SourceConnectorCapability(
        source_group="common-crawl",
        record_kinds=("url_index_record", "warc_response", "extracted_text"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "URL-index metadata is not full-text corpus coverage.",
            "WARC acquisition, extraction and source conformance are not implemented.",
        ),
    ),
    "github": SourceConnectorCapability(
        source_group="github",
        record_kinds=("repository_metadata", "commit_metadata", "source_archive", "code_text"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "Repository ownership, revision and item rights require explicit approval.",
            "No GitHub API, archive or git connector is configured.",
        ),
    ),
    "hugging-face": SourceConnectorCapability(
        source_group="hugging-face",
        record_kinds=("dataset_card", "dataset_revision", "parquet_metadata", "static_record"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "Dataset config, split, gated access and item rights require approval.",
            "Arbitrary dataset loaders and scripts are never called.",
        ),
    ),
    "arxiv": SourceConnectorCapability(
        source_group="arxiv",
        record_kinds=("paper_metadata", "pdf_bytes", "source_archive", "extracted_text"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "Paper publication date does not establish the date of embedded tasks.",
            "PDF/OCR acquisition and extraction are not implemented.",
        ),
    ),
    "stack-exchange": SourceConnectorCapability(
        source_group="stack-exchange",
        record_kinds=("post_metadata", "post_revision", "post_body"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "Post revisions, quotes, deletion and attribution remain separate evidence.",
            "No Stack Exchange API or dump connector is configured.",
        ),
    ),
    "wikipedia": SourceConnectorCapability(
        source_group="wikipedia",
        record_kinds=("dump_revision", "page_revision", "article_text"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "Dump and page-revision scope is not a current-site absence claim.",
            "Markup extraction, dump acquisition and translation lineage are not implemented.",
        ),
    ),
    "benchmark-repositories": SourceConnectorCapability(
        source_group="benchmark-repositories",
        record_kinds=("repository_commit", "split_manifest", "task_component"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "Exact repository, commit, split and component rights are required.",
            "Official harnesses, loaders and source scripts are not run.",
        ),
    ),
    "other-public-datasets": SourceConnectorCapability(
        source_group="other-public-datasets",
        record_kinds=("owner_catalog_entry", "dataset_revision", "static_record"),
        operations=("metadata", "fetch", "extract", "query"),
        limitations=(
            "Each dataset needs an owner-approved catalog record and rights manifest.",
            "There is no generic web crawler or arbitrary dataset-code runner.",
        ),
    ),
}

_OPTIONAL_TOOL_CAPABILITIES = (
    OptionalIndexToolCapability(
        tool_name="data-portraits",
        state="not_configured",
        corpus_version=None,
        query_state="not_run",
        error_code="tool_not_configured",
    ),
    OptionalIndexToolCapability(
        tool_name="infini-gram",
        state="not_configured",
        corpus_version=None,
        query_state="not_run",
        error_code="tool_not_configured",
    ),
)


def source_connector_capabilities() -> tuple[SourceConnectorCapability, ...]:
    """Return the eight versioned source contracts without claiming a live connector."""
    return tuple(_SOURCE_CAPABILITIES[key] for key in sorted(_SOURCE_CAPABILITIES))


def optional_index_tool_capabilities() -> tuple[OptionalIndexToolCapability, ...]:
    return _OPTIONAL_TOOL_CAPABILITIES


def _validate_source_uri(policy: SourceGroupPolicy, source_uri: str) -> str:
    if (
        not source_uri.isascii()
        or any(character.isspace() or ord(character) < 0x20 for character in source_uri)
        or "\\" in source_uri
        or "?" in source_uri
        or "#" in source_uri
    ):
        raise ValueError("source URI must be canonical ASCII HTTPS without query or fragment")
    try:
        parsed = urlsplit(source_uri)
        decoded_path = unquote(parsed.path, errors="strict")
    except (UnicodeError, ValueError):
        raise ValueError("source URI is malformed") from None
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or host not in policy.allowed_hosts
        or parsed.netloc != host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or decoded_path != parsed.path
        or "\\" in parsed.path
        or "\x00" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise ValueError("source URI is outside the exact HTTPS host/path allowlist")
    return host


def build_source_connector_plan(
    policy: SourceGroupPolicy,
    request: SourceConnectorPlanRequest,
) -> SourceConnectorPlan:
    """Freeze an egress plan and its caps; do not fetch, query or resolve a host."""
    if policy.slug != request.source_group:
        raise ValueError("source plan and policy identify different source groups")
    capability = _SOURCE_CAPABILITIES[request.source_group]
    if request.operation not in capability.operations:
        raise ValueError("source operation is not in the bounded capability contract")
    source_host = _validate_source_uri(policy, request.source_uri)
    if request.max_requests > policy.max_requests_per_plan:
        raise ValueError("request budget exceeds the frozen source policy")
    if request.max_response_bytes > policy.max_response_bytes:
        raise ValueError("response byte budget exceeds the frozen source policy")
    if request.max_total_bytes > request.max_requests * policy.max_response_bytes:
        raise ValueError("total byte budget exceeds the source request ceiling")

    blockers: list[str] = []
    if policy.authorization_state != "approved_scoped":
        blockers.append("source_authorization_not_approved")
    if policy.connector_state not in {"importable", "audit_conformant"}:
        blockers.append("source_connector_not_registered")
    if policy.conformance_state != "live_verified":
        blockers.append("source_conformance_not_live_verified")
    # The request's digest is not backed by a verified immutable finite-scope manifest.
    blockers.append("source_scope_manifest_unverified")
    # A free-form revision label is not proof of an approved immutable source pin.
    blockers.append("source_revision_pin_unverified")
    if policy.access_state == "gated" and request.credential_ref is None:
        blockers.append("approved_credential_reference_missing")
    if request.credential_ref is not None:
        # An opaque UUID is only a lookup hint; this module has no credential authorization store.
        blockers.append("credential_authorization_verifier_unavailable")

    # Source connectors in this checkout expose contracts only. Even a locally edited policy
    # cannot turn the contract into a live network dispatcher.
    blockers.append("connector_runtime_not_implemented")

    # A digest supplied by a caller proves neither consent nor a persisted exposure event.
    # Without a trusted authorization verifier and append-only event store, private remote
    # queries remain blocked even when the data-only disclosure binding is well formed.
    exposure: RemoteExposureIntent | None = None
    disclosure = request.disclosure
    if request.private_query:
        if policy.private_query_policy != "explicit_disclosure_required":
            blockers.append("source_policy_denies_private_remote_queries")
        if (
            disclosure is None
            or disclosure.recipient_host != source_host
            or disclosure.query_payload_digest != request.query_payload_digest
        ):
            blockers.append("exact_remote_disclosure_authorization_missing")
        blockers.append("private_remote_query_authorization_verifier_unavailable")

    blockers_tuple = tuple(sorted(set(blockers)))
    plan_disclosure = (
        disclosure
        if disclosure is not None
        and disclosure.recipient_host == source_host
        and disclosure.query_payload_digest == request.query_payload_digest
        else None
    )
    return SourceConnectorPlan(
        source_group=request.source_group,
        operation=request.operation,
        source_uri=request.source_uri,
        source_host=source_host,
        source_revision=request.source_revision,
        scope_digest=request.scope_digest,
        source_policy_digest=canonical_digest(policy.model_dump(mode="json")),
        credential_ref=request.credential_ref,
        max_requests=request.max_requests,
        max_response_bytes=request.max_response_bytes,
        max_total_bytes=request.max_total_bytes,
        request_timeout_ms=request.request_timeout_ms,
        total_timeout_ms=request.total_timeout_ms,
        minimum_request_interval_ms=request.minimum_request_interval_ms,
        max_retries=request.max_retries,
        private_query=request.private_query,
        query_payload_digest=request.query_payload_digest,
        disclosure=plan_disclosure,
        exposure_intent=exposure,
        state="blocked" if blockers_tuple else "planned",
        blocker_codes=blockers_tuple,
    )


def source_connector_plan_digest(plan: SourceConnectorPlan) -> str:
    return canonical_digest(plan.model_dump(mode="json"))


def execute_source_connector_plan(plan: SourceConnectorPlan) -> ConnectorExecutionRecord:
    """Return a truthful blocked record until an approved, live connector is registered."""
    blockers = plan.blocker_codes or ("connector_runtime_not_implemented",)
    return ConnectorExecutionRecord(
        plan_digest=source_connector_plan_digest(plan),
        state="blocked",
        requests_attempted=0,
        elapsed_ms=0,
        request_observations=(),
        bytes_received=0,
        metadata_records=0,
        content_records=0,
        extracted_text_records=0,
        source_date_records=0,
        rights_evidence_records=0,
        extraction_failures=0,
        error_codes=blockers,
    )


def build_connector_coverage_report(
    plan: SourceConnectorPlan,
    execution: ConnectorExecutionRecord,
) -> ConnectorCoverageReport:
    """Keep URL/metadata coverage distinct from acquired and extracted content coverage."""
    if execution.plan_digest != source_connector_plan_digest(plan):
        raise ValueError("connector execution record belongs to a different frozen plan")
    if plan.state == "blocked" and execution.state != "blocked":
        raise ValueError("blocked source plans cannot have a dispatched execution record")
    if execution.requests_attempted > plan.max_requests:
        raise ValueError("connector execution exceeded the frozen request limit")
    if execution.bytes_received > plan.max_total_bytes:
        raise ValueError("connector execution exceeded the frozen byte limit")
    if execution.elapsed_ms > plan.total_timeout_ms:
        raise ValueError("connector execution exceeded the frozen total timeout")
    attempts_by_logical_request: dict[int, list[int]] = {}
    previous_observation = None
    for observation in execution.request_observations:
        if observation.logical_request >= plan.max_requests:
            raise ValueError("logical request identity exceeds the frozen request limit")
        if observation.retry_index > plan.max_retries:
            raise ValueError("connector execution exceeded the frozen retry limit")
        if observation.elapsed_ms > plan.request_timeout_ms:
            raise ValueError("connector attempt exceeded the frozen request timeout")
        if observation.response_bytes > plan.max_response_bytes:
            raise ValueError("connector attempt exceeded the frozen response byte limit")
        if previous_observation is not None and observation.started_after_ms < (
            previous_observation.started_after_ms
            + previous_observation.elapsed_ms
            + plan.minimum_request_interval_ms
        ):
            raise ValueError("connector execution exceeded the frozen minimum request interval")
        attempts_by_logical_request.setdefault(observation.logical_request, []).append(
            observation.retry_index
        )
        previous_observation = observation
    if any(
        indices != list(range(len(indices))) for indices in attempts_by_logical_request.values()
    ):
        raise ValueError("request retry observations must be contiguous from the initial attempt")
    dimension_counts: tuple[tuple[CoverageDimensionName, int, int | None], ...] = (
        (
            "url_metadata",
            execution.metadata_records,
            execution.eligible_metadata_records,
        ),
        ("source_content", execution.content_records, execution.eligible_content_records),
        (
            "extracted_text",
            execution.extracted_text_records,
            execution.eligible_extracted_text_records,
        ),
        ("source_dates", execution.source_date_records, execution.eligible_source_date_records),
        (
            "rights_evidence",
            execution.rights_evidence_records,
            execution.eligible_rights_evidence_records,
        ),
    )
    dimensions = tuple(
        _coverage_dimension(execution, dimension, observed, eligible)
        for dimension, observed, eligible in dimension_counts
    )
    return ConnectorCoverageReport(
        source_group=plan.source_group,
        source_revision=plan.source_revision,
        scope_digest=plan.scope_digest,
        plan_digest=source_connector_plan_digest(plan),
        execution=execution,
        dimensions=dimensions,
    )


def _coverage_dimension(
    execution: ConnectorExecutionRecord,
    dimension: CoverageDimensionName,
    observed: int,
    eligible: int | None,
) -> CoverageDimension:
    state: CoverageState
    reason: str | None
    if execution.state == "blocked":
        state = "blocked"
        reason = "source_execution_blocked"
    elif execution.state == "complete" and eligible is not None:
        state = "complete" if observed == eligible else "partial"
        reason = None if state == "complete" else "eligible_records_not_observed"
    elif execution.state in {"truncated", "failed"} and (observed > 0 or (eligible or 0) > 0):
        state = "partial"
        reason = (
            "source_result_truncated"
            if execution.state == "truncated"
            else "source_execution_failed"
        )
    else:
        state = "unknown"
        reason = (
            "eligible_scope_denominator_not_recorded"
            if execution.state == "complete"
            else "no_coverage_evidence_observed"
        )
    return CoverageDimension(
        dimension=dimension,
        state=state,
        eligible_records=eligible,
        observed_records=observed,
        reason_code=reason,
    )
