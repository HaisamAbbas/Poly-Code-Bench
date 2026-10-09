"""Private, tenant-scoped benchmark-audit APIs and reviewed public health reads.

This adapter reads immutable audit documents and creates plans/runs. It never scans a URL,
dispatches a source query, calls a model, accesses guest execution, signs a report, or publishes a
document. Those operations remain behind their existing services and explicit authorization.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from polycodebench_core.audit_attestations import (
    AttestationVerification,
    PublicAuditAttestationProjection,
    SignedPublicAuditAttestation,
)
from polycodebench_core.benchmark_audit_documents import (
    AuditDocument,
    AuditPlanDocument,
    AuditRunState,
    MatchEvidenceDocumentV2,
    audit_document_digest,
    audit_document_value,
    parse_audit_document,
)
from polycodebench_core.benchmark_audit_registry import (
    AuditCatalogBundle,
    AuditResourcePlan,
    AuditResourceRequest,
    BenchmarkScopeEvidence,
    ResourceLimits,
    SourceGroupPolicy,
)
from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes
from polycodebench_core.match_verification import (
    MatchAdjudicationSubmission,
    MatchReviewLedger,
    MatchReviewSubmission,
)
from polycodebench_persistence.benchmark_audit import PostgresBenchmarkAuditRepository
from polycodebench_publication.aggregation import PublicationModel
from polycodebench_publication.projections import ERROR_STATUS, Cursor, ErrorCode
from polycodebench_services.audit_attestations import verify_public_attestation
from polycodebench_services.benchmark_audit_catalog import (
    BenchmarkAuditCatalogError,
    build_scope_conformance_report,
    load_audit_catalog,
    plan_audit_resources,
)
from polycodebench_services.rbac import Permission
from pydantic import Field, ValidationError, field_validator, model_validator
from starlette.responses import Response

from polycodebench_api.auth import ApiPrincipal, bearer_principal, require_permission
from polycodebench_api.context import services_of
from polycodebench_api.envelope import NO_STORE, respond
from polycodebench_api.errors import ApiError
from polycodebench_api.pagination import DEFAULT_LIMIT, MAX_LIMIT, parse_page_request

private_router = APIRouter(prefix="/v1/benchmark-audit", tags=["benchmark-audit-private"])
public_router = APIRouter(prefix="/v1/public/benchmark-health", tags=["benchmark-health-public"])
public_reports_router = APIRouter(
    prefix="/v1/public/audit-reports", tags=["benchmark-health-public"]
)
public_attestations_router = APIRouter(
    prefix="/v1/public/audit-attestations", tags=["benchmark-attestations-public"]
)

AuditCollection = Literal[
    "snapshots",
    "corpora",
    "plans",
    "runs",
    "queries",
    "matches",
    "assessments",
    "temporal",
    "sealed",
    "firewall",
    "replacements",
    "monitors",
    "health",
    "attestations",
]

_COLLECTION_KIND: dict[str, str] = {
    "snapshots": "benchmark_snapshot",
    "corpora": "corpus_snapshot",
    "plans": "audit_plan",
    "queries": "query_manifest",
    "matches": "match_evidence",
    "assessments": "risk_assessment",
    "temporal": "temporal_assessment",
    "sealed": "sealed_manifest",
    "firewall": "firewall_decision",
    "replacements": "replacement_plan",
    "monitors": "monitor_policy",
    "health": "benchmark_health",
    "attestations": "audit_attestation",
}
_READ_PERMISSION: dict[str, Permission] = {
    "snapshots": Permission.RUN_PLAN,
    "corpora": Permission.RUN_PLAN,
    "plans": Permission.RUN_PLAN,
    "runs": Permission.RUN_PLAN,
    "queries": Permission.RUN_PLAN,
    "matches": Permission.RESTRICTED_EVIDENCE_READ,
    "assessments": Permission.RESTRICTED_EVIDENCE_READ,
    "temporal": Permission.RESTRICTED_EVIDENCE_READ,
    "sealed": Permission.RESTRICTED_EVIDENCE_READ,
    "firewall": Permission.RESTRICTED_EVIDENCE_READ,
    "replacements": Permission.RESTRICTED_EVIDENCE_READ,
    "monitors": Permission.RUN_PLAN,
    "health": Permission.RUN_PLAN,
    "attestations": Permission.RELEASE_REVIEW,
}
_MFA_READ_PERMISSIONS = frozenset({Permission.RESTRICTED_EVIDENCE_READ, Permission.RELEASE_REVIEW})
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{1,255}$")


class AuditApiMeta(PublicationModel):
    release_digest: str = Field(min_length=71, max_length=71)
    total: int | None = Field(default=None, ge=0)
    returned: int = Field(ge=0)
    limit: int | None = Field(default=None, ge=1, le=MAX_LIMIT)
    next_cursor: str | None = Field(default=None, max_length=4096)
    row_version: int | None = Field(default=None, ge=0)


class AuditApiErrorDetail(PublicationModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=64)
    details: tuple[str, ...] = ()


class AuditApiErrorEnvelope(PublicationModel):
    error: AuditApiErrorDetail


class AuditRunView(PublicationModel):
    """Safe, schema-derived audit-run projection; idempotency keys are never returned."""

    kind: Literal["audit_run"] = "audit_run"
    schema_version: Literal[1] = 1
    id: UUID
    plan_document_id: UUID
    campaign_id: UUID | None
    state: AuditRunState
    current_stage: str | None
    dispatch_authorized: bool
    reserved_query_units: int = Field(ge=0)
    reserved_storage_bytes: int = Field(ge=0)
    row_version: int = Field(ge=0)
    created_at: datetime


class AuditDocumentPage(PublicationModel):
    data: tuple[AuditDocument | AuditRunView, ...]
    meta: AuditApiMeta


class AuditMutationData(PublicationModel):
    kind: Literal["benchmark_audit_mutation"] = "benchmark_audit_mutation"
    schema_version: Literal[1] = 1
    resource_id: UUID
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created: bool
    state: Literal["validated", "planned"]
    dispatch_authorized: Literal[False] = False
    row_version: int | None = None
    dry_run: bool = False


class AuditMutationResult(PublicationModel):
    data: AuditMutationData
    meta: AuditApiMeta


class AuditRunCreateInput(PublicationModel):
    plan_document_id: UUID
    reserved_query_units: int = Field(ge=0, le=9_007_199_254_740_991)
    reserved_storage_bytes: int = Field(ge=0, le=9_007_199_254_740_991)
    campaign_id: UUID | None = None


class MatchReviewMutationData(PublicationModel):
    kind: Literal["match_review_result"] = "match_review_result"
    schema_version: Literal[1] = 1
    candidate_document_id: UUID
    opinion_id: UUID | None
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_seq: int | None = Field(default=None, ge=1, le=1_000_000)
    state: Literal["validated", "accepted", "rejected", "disputed"]
    created: bool
    dry_run: bool


class MatchReviewMutationResult(PublicationModel):
    data: MatchReviewMutationData
    meta: AuditApiMeta


class MatchAdjudicationMutationData(PublicationModel):
    kind: Literal["match_adjudication_result"] = "match_adjudication_result"
    schema_version: Literal[1] = 1
    candidate_document_id: UUID
    adjudication_id: UUID | None
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: Literal["validated", "adjudicated"]
    created: bool
    dry_run: bool


class MatchAdjudicationMutationResult(PublicationModel):
    data: MatchAdjudicationMutationData
    meta: AuditApiMeta


class MatchReviewHistoryData(PublicationModel):
    kind: Literal["match_review_history"] = "match_review_history"
    schema_version: Literal[1] = 1
    candidate_document_id: UUID
    ledger: MatchReviewLedger


class MatchReviewHistoryResult(PublicationModel):
    data: MatchReviewHistoryData
    meta: AuditApiMeta


class PublicHealthView(PublicationModel):
    """Allowlisted, reviewed summary; raw audit documents are never public responses."""

    kind: Literal["public_benchmark_health"]
    schema_version: Literal[1]
    report_id: UUID
    review_state: Literal["published"]
    benchmark_label: str = Field(min_length=1, max_length=160)
    benchmark_version: str | None = Field(default=None, max_length=160)
    source_window_start: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"
    )
    source_window_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
    selected_tasks: int = Field(ge=0)
    complete_tasks: int = Field(ge=0)
    partial_tasks: int = Field(ge=0)
    unknown_tasks: int = Field(ge=0)
    unscanned_tasks: int = Field(ge=0)
    blocked_tasks: int = Field(ge=0)
    assessed_tasks: int = Field(ge=0)
    low_risk_tasks: int = Field(ge=0)
    medium_risk_tasks: int = Field(ge=0)
    high_risk_tasks: int = Field(ge=0)
    insufficient_risk_tasks: int = Field(ge=0)
    limitations: tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")], ...] = Field(
        min_length=1, max_length=16
    )

    @field_validator("benchmark_label", "benchmark_version")
    @classmethod
    def display_text_has_no_private_markers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.lower()
        if any(marker in lowered for marker in ("http", "sha256:", "..", "/", "\\", "@")):
            raise ValueError("public benchmark labels must not contain private markers")
        return value

    @model_validator(mode="after")
    def counts_reconcile(self) -> PublicHealthView:
        if (
            self.complete_tasks
            + self.partial_tasks
            + self.unknown_tasks
            + self.unscanned_tasks
            + self.blocked_tasks
            != self.selected_tasks
            or self.low_risk_tasks
            + self.medium_risk_tasks
            + self.high_risk_tasks
            + self.insufficient_risk_tasks
            != self.assessed_tasks
            or self.assessed_tasks + self.unscanned_tasks != self.selected_tasks
            or datetime.fromisoformat(self.source_window_end.replace("Z", "+00:00"))
            < datetime.fromisoformat(self.source_window_start.replace("Z", "+00:00"))
            or len(set(self.limitations)) != len(self.limitations)
        ):
            raise ValueError("public health counts do not reconcile")
        return self


class AuditDocumentResult(PublicationModel):
    data: AuditDocument | AuditRunView | AuditCatalogBundle | PublicHealthView
    meta: AuditApiMeta


class PublicAuditWorkspaceData(PublicationModel):
    catalog_version: str
    observed_on: str
    benchmarks: tuple[BenchmarkScopeEvidence, ...]
    sources: tuple[SourceGroupPolicy, ...]
    limits: ResourceLimits


class PublicAuditWorkspaceResult(PublicationModel):
    data: PublicAuditWorkspaceData
    meta: AuditApiMeta


class PublicAuditResourcePlanResult(PublicationModel):
    data: AuditResourcePlan
    meta: AuditApiMeta


class PublicAuditAttestationView(PublicationModel):
    attestation: SignedPublicAuditAttestation
    verification: AttestationVerification
    successor_id: UUID | None = None


class PublicAuditAttestationResult(PublicationModel):
    data: PublicAuditAttestationView
    meta: AuditApiMeta


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": AuditApiErrorEnvelope, "description": "Safe correlated API error"}
    for status in set(ERROR_STATUS.values())
}
private_router.responses.update(_ERROR_RESPONSES)
public_router.responses.update(_ERROR_RESPONSES)
public_reports_router.responses.update(_ERROR_RESPONSES)


def _private_services(request: Request) -> tuple[ApiPrincipal, PostgresBenchmarkAuditRepository]:
    services = services_of(request)
    principal = bearer_principal(request, services.tokens)
    if principal.tenant_id is None:
        raise ApiError("FORBIDDEN")
    assert principal.tenant_id is not None
    if services.benchmark_audit is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    return principal, services.benchmark_audit


def _tenant_id(principal: ApiPrincipal) -> UUID:
    if principal.tenant_id is None:
        raise ApiError("FORBIDDEN")
    return principal.tenant_id


def _authorize_object(
    request: Request, principal: ApiPrincipal, document: AuditDocument, action: str
) -> None:
    policy = services_of(request).audit_access
    if policy is None or not policy.allows(principal=principal, document=document, action=action):
        raise ApiError("NOT_FOUND")


def _validated_idempotency_key(value: str | None) -> str:
    if value is None or not _IDEMPOTENCY_KEY.fullmatch(value):
        raise ApiError("SCHEMA_INVALID")
    return value


async def _read_bounded_body(request: Request, limit: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None and (
        not content_length.isascii()
        or not content_length.isdigit()
        or len(content_length) > 16
        or int(content_length) > limit
    ):
        raise ApiError("SCHEMA_INVALID")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise ApiError("SCHEMA_INVALID")
    return bytes(body)


def _reject_duplicate_json_members(raw_body: bytes) -> None:
    """Reject duplicate keys before Pydantic's JSON parser can overwrite them."""

    def unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object member")
            result[key] = value
        return result

    json.loads(raw_body, object_pairs_hook=unique_members)


def _page_binding(principal: ApiPrincipal, resource: str) -> tuple[str, str]:
    tenant_id = principal.tenant_id
    if tenant_id is None:
        raise ApiError("FORBIDDEN")
    binding = f"benchmark-audit:{tenant_id}:{principal.subject_id}:{resource}"
    filters_digest = sha256_bytes(
        canonical_json_bytes({"resource": resource, "owner": principal.subject_id})
    )
    return binding, filters_digest


def _catalog() -> AuditCatalogBundle:
    config_dir = Path(os.environ.get("PCB_AUDIT_CONFIG_DIR", "config/benchmark-audit"))
    try:
        return load_audit_catalog(config_dir)
    except BenchmarkAuditCatalogError:
        raise ApiError("DEPENDENCY_UNAVAILABLE") from None


@private_router.get("/scope-preview", response_model=PublicAuditWorkspaceResult)
def get_audit_scope_preview(request: Request) -> Response:
    """Expose metadata-only readiness to a tenant-scoped run planner."""
    principal = require_permission(request, services_of(request).tokens, Permission.RUN_PLAN)
    _tenant_id(principal)
    catalog = _catalog()
    scope = build_scope_conformance_report(catalog)
    data = PublicAuditWorkspaceData(
        catalog_version=scope.catalog_version,
        observed_on=scope.observed_on,
        benchmarks=scope.benchmarks,
        sources=catalog.source_policies.groups,
        limits=catalog.limits,
    )
    data_json = data.model_dump(mode="json")
    body = PublicAuditWorkspaceResult(
        data=data,
        meta=AuditApiMeta(
            release_digest=_meta_digest(data_json),
            returned=len(scope.benchmarks),
            total=len(scope.benchmarks),
            limit=len(scope.benchmarks),
        ),
    )
    return respond(request, body.model_dump(mode="json"), cache=NO_STORE)


@private_router.post(
    "/resource-plan",
    response_model=PublicAuditResourcePlanResult,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": AuditResourceRequest.model_json_schema()}},
        }
    },
)
async def create_audit_resource_plan(request: Request) -> Response:
    """Calculate a bounded no-dispatch estimate for a tenant-scoped operator."""
    principal = require_permission(request, services_of(request).tokens, Permission.RUN_PLAN)
    _tenant_id(principal)
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json":
        raise ApiError("SCHEMA_INVALID")
    raw_body = await _read_bounded_body(request, 64 * 1024)
    try:
        _reject_duplicate_json_members(raw_body)
        plan_request = AuditResourceRequest.model_validate_json(raw_body, strict=True)
    except (UnicodeError, ValueError, ValidationError):
        raise ApiError("SCHEMA_INVALID") from None
    try:
        plan = plan_audit_resources(_catalog(), plan_request)
    except BenchmarkAuditCatalogError:
        raise ApiError("SCHEMA_INVALID") from None
    data = plan.model_dump(mode="json")
    body = PublicAuditResourcePlanResult(
        data=plan,
        meta=AuditApiMeta(
            release_digest=_meta_digest(data),
            returned=1,
            total=1,
            limit=1,
        ),
    )
    return respond(request, body.model_dump(mode="json"), cache=NO_STORE)


def _doc_data(document: AuditDocument) -> dict[str, Any]:
    return audit_document_value(document)


def _meta_digest(data: object) -> str:
    return sha256_bytes(canonical_json_bytes(data))


@private_router.get("/registry", response_model=AuditDocumentResult)
def get_registry(request: Request) -> Response:
    principal = require_permission(request, services_of(request).tokens, Permission.RUN_PLAN)
    if principal.tenant_id is None:
        raise ApiError("FORBIDDEN")
    catalog = _catalog()
    data = catalog.model_dump(mode="json")
    body = AuditDocumentResult(
        data=catalog,
        meta=AuditApiMeta(release_digest=_meta_digest(data), returned=1, total=1, limit=1),
    )
    return respond(request, body.model_dump(mode="json"), cache=NO_STORE)


@private_router.get("/", include_in_schema=False)
def audit_root() -> dict[str, object]:
    return {"resources": ["registry", *_COLLECTION_KIND.keys(), "runs"]}


@private_router.get("/{resource}", response_model=AuditDocumentPage)
def list_audit_documents(
    request: Request,
    resource: AuditCollection,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> Response:
    principal, repository = _private_services(request)
    tenant_id = _tenant_id(principal)
    permission = _READ_PERMISSION[resource]
    require_permission(
        request,
        services_of(request).tokens,
        permission,
        mfa=permission in _MFA_READ_PERMISSIONS,
    )
    binding, filters_digest = _page_binding(principal, resource)
    page = parse_page_request(
        limit=limit,
        cursor=cursor,
        sort="created_at_asc",
        allowed_sorts=frozenset({"created_at_asc"}),
        binding=binding,
        filters_digest=filters_digest,
        key=services_of(request).secret_key,
    )
    documents, total = (
        repository.list_documents(
            kind=_COLLECTION_KIND[resource],
            created_by=principal.subject_id,
            tenant_id=tenant_id,
            limit=page.limit,
            offset=page.offset,
        )
        if resource != "runs"
        else ((), 0)
    )
    page_data: tuple[AuditDocument | AuditRunView, ...]
    if resource == "runs":
        run_rows, total = repository.list_audit_runs(
            created_by=principal.subject_id,
            tenant_id=tenant_id,
            limit=page.limit,
            offset=page.offset,
        )
        for row in run_rows:
            plan = repository.get_document(UUID(str(row["plan_document_id"])), tenant_id=tenant_id)
            if plan is None:
                raise ApiError("NOT_FOUND")
            _authorize_object(request, principal, plan, "run")
        run_views = tuple(_run_data(row) for row in run_rows)
        page_data = run_views
        data_json = [view.model_dump(mode="json") for view in run_views]
    else:
        for document in documents:
            _authorize_object(request, principal, document, "read")
        page_data = documents
        data_json = [_doc_data(document) for document in documents]
    next_cursor = None
    next_offset = page.offset + len(page_data)
    if next_offset < total:
        next_cursor = Cursor(
            release_id=binding,
            filters_digest=filters_digest,
            sort=page.sort,
            offset=next_offset,
        ).sign(services_of(request).secret_key)
    body = AuditDocumentPage(
        data=page_data,
        meta=AuditApiMeta(
            release_digest=_meta_digest(data_json),
            total=total,
            returned=len(page_data),
            limit=page.limit,
            next_cursor=next_cursor,
        ),
    )
    return respond(request, body.model_dump(mode="json"), cache=NO_STORE)


@private_router.get("/{resource}/{document_id}", response_model=AuditDocumentResult)
def get_audit_document(request: Request, resource: AuditCollection, document_id: UUID) -> Response:
    principal, repository = _private_services(request)
    tenant_id = _tenant_id(principal)
    permission = _READ_PERMISSION[resource]
    require_permission(
        request,
        services_of(request).tokens,
        permission,
        mfa=permission in _MFA_READ_PERMISSIONS,
    )
    if resource == "runs":
        row = repository.get_audit_run(
            audit_run_id=document_id,
            created_by=principal.subject_id,
            tenant_id=tenant_id,
        )
        if row is None:
            raise ApiError("NOT_FOUND")
        plan = repository.get_document(UUID(str(row["plan_document_id"])), tenant_id=tenant_id)
        if plan is None:
            raise ApiError("NOT_FOUND")
        _authorize_object(request, principal, plan, "run")
        run_view = _run_data(row)
        data = run_view.model_dump(mode="json")
        body = AuditDocumentResult(
            data=run_view,
            meta=AuditApiMeta(
                release_digest=_meta_digest(data),
                returned=1,
                total=1,
                limit=1,
                row_version=int(data["row_version"]),
            ),
        )
        return respond(request, body.model_dump(mode="json"), cache=NO_STORE)
    document = repository.get_document(document_id, tenant_id=tenant_id)
    if document is None or document.kind != _COLLECTION_KIND[resource]:
        raise ApiError("NOT_FOUND")
    _authorize_object(request, principal, document, "read")
    data = _doc_data(document)
    digest = audit_document_digest(document)
    body = AuditDocumentResult(
        data=document,
        meta=AuditApiMeta(release_digest=digest, returned=1, total=1, limit=1),
    )
    return respond(request, body.model_dump(mode="json"), cache=NO_STORE)


@private_router.post(
    "/plans",
    response_model=AuditMutationResult,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/AuditPlanDocument"}}
            },
        }
    },
)
async def create_audit_plan(
    request: Request,
    dry_run: bool = True,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    principal, repository = _private_services(request)
    tenant_id = _tenant_id(principal)
    require_permission(request, services_of(request).tokens, Permission.RUN_PLAN)
    if (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
        != "application/json"
    ):
        raise ApiError("SCHEMA_INVALID")
    raw_document = await _read_bounded_body(request, 10 * 1024 * 1024)
    try:
        parsed_document = parse_audit_document(raw_document)
    except (UnicodeError, ValueError):
        raise ApiError("SCHEMA_INVALID") from None
    if not isinstance(parsed_document, AuditPlanDocument):
        raise ApiError("SCHEMA_INVALID")
    document = parsed_document
    if document.metadata.actor != principal.subject_id:
        raise ApiError("FORBIDDEN")
    _authorize_object(request, principal, document, "write")
    digest = audit_document_digest(document)
    if dry_run:
        resource_id = document.id
        created = False
        state: Literal["validated", "planned"] = "validated"
        row_version = None
        dry_run_value = True
    else:
        if if_none_match != "*":
            raise ApiError("SCHEMA_INVALID")
        key = _validated_idempotency_key(idempotency_key)
        result = repository.save_document_idempotent(
            document,
            subject=principal.subject_id,
            route=f"POST /v1/benchmark-audit/{tenant_id}/plans",
            idempotency_key=key,
            request_digest=_meta_digest(document.model_dump(mode="json")),
            tenant_id=tenant_id,
        )
        resource_id = result.document_id
        digest = result.digest
        created = result.created
        state = "planned"
        row_version = None
        dry_run_value = False
    data = {
        "resource_id": str(resource_id),
        "digest": digest,
        "created": created,
        "state": state,
        "dispatch_authorized": False,
        "row_version": row_version,
        "dry_run": dry_run_value,
    }
    mutation = AuditMutationData(
        resource_id=resource_id,
        digest=digest,
        created=created,
        state=state,
        dispatch_authorized=False,
        row_version=row_version,
        dry_run=dry_run_value,
    )
    body = AuditMutationResult(
        data=mutation,
        meta=AuditApiMeta(release_digest=_meta_digest(data), returned=1, total=1, limit=1),
    )
    return respond(
        request,
        body.model_dump(mode="json"),
        cache=NO_STORE,
        status_code=200 if dry_run_value or not created else 201,
    )


def _run_data(row: Mapping[str, Any]) -> AuditRunView:
    return AuditRunView(
        id=row["id"],
        plan_document_id=row["plan_document_id"],
        campaign_id=row["campaign_id"],
        state=row["state"],
        current_stage=row["current_stage"],
        dispatch_authorized=row["dispatch_authorized"],
        reserved_query_units=row["reserved_query_units"],
        reserved_storage_bytes=row["reserved_storage_bytes"],
        row_version=row["row_version"],
        created_at=row["created_at"],
    )


@private_router.post(
    "/runs",
    response_model=AuditMutationResult,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": AuditRunCreateInput.model_json_schema()}},
        }
    },
)
async def create_audit_run(
    request: Request,
    dry_run: bool = True,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    services = services_of(request)
    principal, repository = _private_services(request)
    tenant_id = _tenant_id(principal)
    require_permission(request, services.tokens, Permission.RUN_CREATE)
    if (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
        != "application/json"
    ):
        raise ApiError("SCHEMA_INVALID")
    raw_body = await _read_bounded_body(request, 64 * 1024)
    try:
        _reject_duplicate_json_members(raw_body)
        body = AuditRunCreateInput.model_validate_json(raw_body, strict=True)
    except (ValidationError, ValueError):
        raise ApiError("SCHEMA_INVALID") from None
    plan = repository.get_document(body.plan_document_id, tenant_id=tenant_id)
    if not isinstance(plan, AuditPlanDocument):
        raise ApiError("NOT_FOUND")
    _authorize_object(request, principal, plan, "run")
    limits = plan.payload.limits
    query_limit = _plan_limit(limits, "max_query_units", "max_query_units_per_plan")
    storage_limit = _plan_limit(limits, "max_storage_bytes", "max_storage_bytes_per_plan")
    if (
        query_limit is None
        or storage_limit is None
        or body.reserved_query_units > query_limit
        or body.reserved_storage_bytes > storage_limit
    ):
        raise ApiError("BUDGET_EXHAUSTED")
    reservation_digest = _meta_digest(
        {
            "plan_document_id": str(body.plan_document_id),
            "reserved_query_units": body.reserved_query_units,
            "reserved_storage_bytes": body.reserved_storage_bytes,
            "campaign_id": str(body.campaign_id) if body.campaign_id else None,
        }
    )
    if dry_run:
        resource_id = body.plan_document_id
        created = False
        state: Literal["validated", "planned"] = "validated"
        row_version = None
        dry_run_value = True
    else:
        if if_none_match != "*":
            raise ApiError("SCHEMA_INVALID")
        key = _validated_idempotency_key(idempotency_key)
        result = repository.create_audit_run(
            plan_document_id=body.plan_document_id,
            idempotency_key=key,
            reserved_query_units=body.reserved_query_units,
            reserved_storage_bytes=body.reserved_storage_bytes,
            actor=principal.subject_id,
            tenant_id=tenant_id,
            campaign_id=body.campaign_id,
        )
        resource_id = result.audit_run_id
        created = result.created
        state = "planned"
        row_version = result.row_version
        dry_run_value = False
    data = {
        "resource_id": str(resource_id),
        "digest": reservation_digest,
        "created": created,
        "state": state,
        "dispatch_authorized": False,
        "row_version": row_version,
        "dry_run": dry_run_value,
    }
    mutation = AuditMutationData(
        resource_id=resource_id,
        digest=reservation_digest,
        created=created,
        state=state,
        dispatch_authorized=False,
        row_version=row_version,
        dry_run=dry_run_value,
    )
    response = AuditMutationResult(
        data=mutation,
        meta=AuditApiMeta(release_digest=_meta_digest(data), returned=1, total=1, limit=1),
    )
    return respond(
        request,
        response.model_dump(mode="json"),
        cache=NO_STORE,
        status_code=200 if dry_run_value or not created else 201,
    )


@private_router.post(
    "/matches/{candidate_document_id}/reviews",
    response_model=MatchReviewMutationResult,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": MatchReviewSubmission.model_json_schema()}},
        }
    },
)
async def create_match_review(
    request: Request,
    candidate_document_id: UUID,
    dry_run: bool = True,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    principal, repository = _private_services(request)
    tenant_id = _tenant_id(principal)
    require_permission(
        request,
        services_of(request).tokens,
        Permission.RESTRICTED_EVIDENCE_READ,
        mfa=True,
    )
    require_permission(
        request,
        services_of(request).tokens,
        Permission.EVALUATION_ADJUDICATE,
        mfa=True,
    )
    candidate = repository.get_document(candidate_document_id, tenant_id=tenant_id)
    if not isinstance(candidate, MatchEvidenceDocumentV2):
        raise ApiError("NOT_FOUND")
    _authorize_object(request, principal, candidate, "review")
    if candidate.payload.author_subject == principal.subject_id:
        raise ApiError("FORBIDDEN")
    if (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
        != "application/json"
    ):
        raise ApiError("SCHEMA_INVALID")
    raw_body = await _read_bounded_body(request, 64 * 1024)
    try:
        _reject_duplicate_json_members(raw_body)
        submission = MatchReviewSubmission.model_validate_json(raw_body, strict=True)
    except (ValidationError, ValueError):
        raise ApiError("SCHEMA_INVALID") from None

    request_digest = _meta_digest(submission.model_dump(mode="json"))
    if dry_run:
        opinion_id = None
        digest = request_digest
        review_seq = None
        state: Literal["validated", "accepted", "rejected", "disputed"] = "validated"
        created = False
    else:
        if if_none_match != "*":
            raise ApiError("SCHEMA_INVALID")
        key = _validated_idempotency_key(idempotency_key)
        write = repository.append_match_review(
            candidate_document_id=candidate_document_id,
            submission=submission,
            reviewer_subject=principal.subject_id,
            idempotency_key=key,
            request_digest=request_digest,
            tenant_id=tenant_id,
        )
        opinion_id = write.opinion_id
        digest = write.digest
        review_seq = write.review_seq
        state = write.state
        created = write.created
    data = MatchReviewMutationData(
        candidate_document_id=candidate_document_id,
        opinion_id=opinion_id,
        digest=digest,
        review_seq=review_seq,
        state=state,
        created=created,
        dry_run=dry_run,
    )
    body = MatchReviewMutationResult(
        data=data,
        meta=AuditApiMeta(
            release_digest=_meta_digest(data.model_dump(mode="json")),
            returned=1,
            total=1,
            limit=1,
        ),
    )
    return respond(
        request,
        body.model_dump(mode="json"),
        cache=NO_STORE,
        status_code=200 if dry_run or not created else 201,
    )


@private_router.post(
    "/matches/{candidate_document_id}/adjudications",
    response_model=MatchAdjudicationMutationResult,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": MatchAdjudicationSubmission.model_json_schema()}
            },
        }
    },
)
async def create_match_adjudication(
    request: Request,
    candidate_document_id: UUID,
    dry_run: bool = True,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    principal, repository = _private_services(request)
    tenant_id = _tenant_id(principal)
    require_permission(
        request,
        services_of(request).tokens,
        Permission.RESTRICTED_EVIDENCE_READ,
        mfa=True,
    )
    require_permission(
        request,
        services_of(request).tokens,
        Permission.EVALUATION_ADJUDICATE,
        mfa=True,
    )
    candidate = repository.get_document(candidate_document_id, tenant_id=tenant_id)
    if not isinstance(candidate, MatchEvidenceDocumentV2):
        raise ApiError("NOT_FOUND")
    _authorize_object(request, principal, candidate, "review")
    if candidate.payload.author_subject == principal.subject_id:
        raise ApiError("FORBIDDEN")
    if (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
        != "application/json"
    ):
        raise ApiError("SCHEMA_INVALID")
    raw_body = await _read_bounded_body(request, 64 * 1024)
    try:
        _reject_duplicate_json_members(raw_body)
        submission = MatchAdjudicationSubmission.model_validate_json(raw_body, strict=True)
    except (ValidationError, ValueError):
        raise ApiError("SCHEMA_INVALID") from None

    request_digest = _meta_digest(submission.model_dump(mode="json"))
    if dry_run:
        adjudication_id = None
        digest = request_digest
        state: Literal["validated", "adjudicated"] = "validated"
        created = False
    else:
        if if_none_match != "*":
            raise ApiError("SCHEMA_INVALID")
        key = _validated_idempotency_key(idempotency_key)
        write = repository.append_match_adjudication(
            candidate_document_id=candidate_document_id,
            submission=submission,
            adjudicator_subject=principal.subject_id,
            idempotency_key=key,
            request_digest=request_digest,
            tenant_id=tenant_id,
        )
        adjudication_id = write.adjudication_id
        digest = write.digest
        state = write.state
        created = write.created
    data = MatchAdjudicationMutationData(
        candidate_document_id=candidate_document_id,
        adjudication_id=adjudication_id,
        digest=digest,
        state=state,
        created=created,
        dry_run=dry_run,
    )
    body = MatchAdjudicationMutationResult(
        data=data,
        meta=AuditApiMeta(
            release_digest=_meta_digest(data.model_dump(mode="json")),
            returned=1,
            total=1,
            limit=1,
        ),
    )
    return respond(
        request,
        body.model_dump(mode="json"),
        cache=NO_STORE,
        status_code=200 if dry_run or not created else 201,
    )


@private_router.get(
    "/matches/{candidate_document_id}/reviews",
    response_model=MatchReviewHistoryResult,
)
def get_match_review_history(request: Request, candidate_document_id: UUID) -> Response:
    principal, repository = _private_services(request)
    tenant_id = _tenant_id(principal)
    require_permission(
        request,
        services_of(request).tokens,
        Permission.RESTRICTED_EVIDENCE_READ,
        mfa=True,
    )
    candidate = repository.get_document(candidate_document_id, tenant_id=tenant_id)
    if not isinstance(candidate, MatchEvidenceDocumentV2):
        raise ApiError("NOT_FOUND")
    _authorize_object(request, principal, candidate, "review")
    ledger = repository.list_match_reviews(
        candidate_document_id=candidate_document_id,
        tenant_id=tenant_id,
    )
    data = MatchReviewHistoryData(
        candidate_document_id=candidate_document_id,
        ledger=ledger,
    )
    data_json = data.model_dump(mode="json")
    body = MatchReviewHistoryResult(
        data=data,
        meta=AuditApiMeta(
            release_digest=_meta_digest(data_json),
            total=len(ledger.opinions),
            returned=len(ledger.opinions),
        ),
    )
    return respond(request, body.model_dump(mode="json"), cache=NO_STORE)


def _plan_limit(limits: Mapping[str, int], canonical: str, configured: str) -> int | None:
    values = [limits[key] for key in (canonical, configured) if key in limits]
    if not values or any(type(value) is not int or value < 0 for value in values):
        return None
    if len(values) == 2 and values[0] != values[1]:
        return None
    return values[0]


@public_router.get("/{report_id}", response_model=AuditDocumentResult)
@public_reports_router.get("/{report_id}", response_model=AuditDocumentResult)
def get_public_health_report(request: Request, report_id: UUID) -> Response:
    projection_store = services_of(request).public_benchmark_health
    if projection_store is None:
        raise ApiError("NOT_FOUND")
    candidate = projection_store.get(report_id)
    if candidate is None:
        raise ApiError("NOT_FOUND")
    try:
        view = PublicHealthView.model_validate_json(canonical_json_bytes(candidate), strict=True)
    except (TypeError, ValueError, ValidationError):
        raise ApiError("NOT_FOUND") from None
    if view.report_id != report_id:
        raise ApiError("NOT_FOUND")
    data = view.model_dump(mode="json")
    body = AuditDocumentResult(
        data=view,
        meta=AuditApiMeta(release_digest=_meta_digest(data), returned=1, total=1, limit=1),
    )
    return respond(request, body.model_dump(mode="json"), cache=NO_STORE)


@public_attestations_router.get("/{attestation_id}", response_model=PublicAuditAttestationResult)
def get_public_attestation(request: Request, attestation_id: UUID) -> Response:
    services = services_of(request)
    projection_store = services.public_audit_attestations
    trust_store = services.attestation_trust_store
    if projection_store is None or trust_store is None:
        raise ApiError("NOT_FOUND")
    candidate = projection_store.get(attestation_id)
    if candidate is None:
        raise ApiError("NOT_FOUND")
    try:
        projection = PublicAuditAttestationProjection.model_validate_json(
            canonical_json_bytes(candidate), strict=True
        )
    except (TypeError, ValueError, ValidationError):
        raise ApiError("NOT_FOUND") from None
    signed = projection.attestation
    if signed.claims.attestation_id != attestation_id:
        raise ApiError("NOT_FOUND")
    verification = verify_public_attestation(
        signed,
        trust_store,
        revocation=projection.lifecycle,
    )
    successor_id = (
        projection.lifecycle.successor_id
        if (
            projection.lifecycle is not None
            and projection.lifecycle.status == "superseded"
            and verification.result == "superseded"
        )
        else None
    )
    view = PublicAuditAttestationView(
        attestation=signed,
        verification=verification,
        successor_id=successor_id,
    )
    data = view.model_dump(mode="json")
    result = PublicAuditAttestationResult(
        data=view,
        meta=AuditApiMeta(release_digest=_meta_digest(data), returned=1, total=1, limit=1),
    )
    return respond(request, result.model_dump(mode="json"), cache=NO_STORE)


__all__ = [
    "private_router",
    "public_attestations_router",
    "public_reports_router",
    "public_router",
]
