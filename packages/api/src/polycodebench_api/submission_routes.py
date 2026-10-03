"""Submitter, reviewer and administrator routes for one bounded model evaluation request."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from polycodebench_core.endpoint_policy import EndpointNetworkPolicy, parse_endpoint_url
from polycodebench_core.model_contracts import (
    EndpointNotApproved,
    EndpointPolicyViolation,
    ModelCapabilities,
    ProviderKind,
)
from polycodebench_publication.aggregation import PublicationModel
from polycodebench_services.rbac import Permission, Principal, Role
from polycodebench_services.runs import RunCreateRequest
from pydantic import Field, ValidationError
from starlette.responses import Response

from polycodebench_api.auth import ApiPrincipal, require_permission
from polycodebench_api.context import services_of
from polycodebench_api.envelope import NO_STORE, ResponseMeta, envelope, respond
from polycodebench_api.errors import ApiError
from polycodebench_api.submissions import (
    ModelSubmission,
    ModelSubmissionInput,
    PermissionReviewRecord,
    SubmissionRate,
    SubmissionReviewView,
)

router = APIRouter(prefix="/v1")
MAX_SUBMISSION_ATTEMPTS = 500


class SubmissionRejectInput(PublicationModel):
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)


class SubmissionApprovalInput(PublicationModel):
    endpoint_registration_id: UUID
    run_request: dict[str, object]
    permission_review: PermissionReviewRecord


class EndpointRegistrationInput(PublicationModel):
    provider_kind: ProviderKind
    base_url: str = Field(min_length=9, max_length=512)
    secret_ref: str = Field(min_length=1, max_length=192)
    network_policy: EndpointNetworkPolicy
    declared_capabilities: ModelCapabilities


class EndpointDecisionInput(PublicationModel):
    decision: Literal["approved", "rejected", "revoked"]
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)
    conformance_report: dict[str, object] | None = None


class EndpointRegistrationResult(PublicationModel):
    kind: Literal["endpoint_registration_result"] = "endpoint_registration_result"
    endpoint_registration_id: str
    status: Literal["pending"]


class EndpointDecisionResult(PublicationModel):
    kind: Literal["endpoint_decision_result"] = "endpoint_decision_result"
    endpoint_registration_id: str
    status: Literal["approved", "rejected", "revoked"]


def _meta(payload: object) -> ResponseMeta:
    from polycodebench_core.canonical import canonical_json_bytes, sha256_bytes

    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return ResponseMeta(release_digest="sha256:" + sha256_bytes(canonical_json_bytes(payload)))


def _principal(value: ApiPrincipal) -> Principal:
    roles = frozenset(Role(role) for role in value.roles if role in set(Role))
    return Principal(subject_id=value.subject_id, roles=roles)


def _require_mfa_permission(request: Request, permission: Permission) -> ApiPrincipal:
    return require_permission(request, services_of(request).tokens, permission, mfa=True)


def _with_run_status(request: Request, result: dict[str, object]) -> dict[str, object]:
    run_id = result.get("resulting_run_id")
    if isinstance(run_id, str):
        runs = services_of(request).runs
        summary = runs.get_run_summary(UUID(run_id)) if runs is not None else None
        if summary is not None:
            state = summary.get("status")
            result["run_status"] = state if isinstance(state, str) else "queued"
        else:
            result["run_status"] = "queued"
    else:
        result["run_status"] = None
    return result


@router.post("/model-submissions")
def post_model_submission(
    request: Request,
    submission: ModelSubmissionInput,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    """Create a pending record for the verified principal; never contact or schedule a model."""
    services = services_of(request)
    principal = require_permission(request, services.tokens, Permission.SUBMISSION_CREATE)
    if not principal.email_verified or not principal.email:
        raise ApiError("FORBIDDEN")
    if principal.email.casefold() != submission.contact_email.casefold():
        raise ApiError("FORBIDDEN")
    if idempotency_key is None:
        raise ApiError("SCHEMA_INVALID")
    payload = submission.model_dump(mode="json")
    result = services.submissions.submit(
        subject=principal.subject_id,
        request_id=idempotency_key.strip(),
        payload=payload,
        rate=SubmissionRate(
            limit=services.submission_rate_limit,
            window_seconds=services.submission_rate_window_seconds,
        ),
    )
    return respond(
        request,
        envelope(ModelSubmission.model_validate(result), _meta(result)),
        status_code=201,
        cache=NO_STORE,
    )


@router.get("/model-submissions/{submission_id}")
def get_own_model_submission(request: Request, submission_id: UUID) -> Response:
    services = services_of(request)
    principal = require_permission(request, services.tokens, Permission.SUBMISSION_READ_OWN)
    result = _with_run_status(
        request,
        services.submissions.get_owned(
            subject=principal.subject_id, submission_id=str(submission_id)
        ),
    )
    return respond(
        request,
        envelope(ModelSubmission.model_validate(result), _meta(result)),
        cache=NO_STORE,
    )


@router.get("/admin/model-submissions")
def list_model_submissions_for_review(
    request: Request,
    status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> Response:
    _require_mfa_permission(request, Permission.SUBMISSION_REVIEW)
    rows = services_of(request).submissions.list_for_review(
        statuses=tuple(status or ("pending", "under_review")), limit=limit
    )
    views = tuple(SubmissionReviewView.model_validate(row) for row in rows)
    return respond(
        request,
        envelope(views, _meta([row.model_dump(mode="json") for row in views])),
        cache=NO_STORE,
    )


@router.get("/admin/model-submissions/{submission_id}")
def get_model_submission_for_review(request: Request, submission_id: UUID) -> Response:
    _require_mfa_permission(request, Permission.SUBMISSION_REVIEW)
    result = services_of(request).submissions.get_for_review(submission_id=str(submission_id))
    view = SubmissionReviewView.model_validate(result)
    return respond(request, envelope(view, _meta(view)), cache=NO_STORE)


@router.post("/admin/model-submissions/{submission_id}/reject")
def reject_model_submission(
    request: Request,
    submission_id: UUID,
    decision: SubmissionRejectInput,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    principal = _require_mfa_permission(request, Permission.SUBMISSION_REVIEW)
    if idempotency_key is None:
        raise ApiError("SCHEMA_INVALID")
    result = services_of(request).submissions.reject(
        submission_id=str(submission_id),
        reviewer=principal.subject_id,
        reason=decision.reason,
        expected_version=decision.expected_version,
        request_id=idempotency_key.strip(),
    )
    view = SubmissionReviewView.model_validate(result)
    return respond(request, envelope(view, _meta(view)), cache=NO_STORE)


@router.post("/admin/model-submissions/{submission_id}/approve")
def approve_model_submission(
    request: Request,
    submission_id: UUID,
    approval: SubmissionApprovalInput,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    services = services_of(request)
    principal = _require_mfa_permission(request, Permission.ENDPOINT_APPROVE)
    if idempotency_key is None:
        raise ApiError("SCHEMA_INVALID")
    if services.run_creation is None or services.endpoints is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    try:
        run_request = RunCreateRequest.model_validate_json(
            json.dumps(approval.run_request), strict=True
        )
    except (TypeError, ValueError, ValidationError):
        raise ApiError("SCHEMA_INVALID") from None
    review = services.submissions.get_for_review(submission_id=str(submission_id))
    if run_request.endpoint_registration_id != approval.endpoint_registration_id:
        raise ApiError("SCHEMA_INVALID")
    if run_request.max_attempts > MAX_SUBMISSION_ATTEMPTS:
        raise ApiError("SCHEMA_INVALID")
    permission_review = approval.permission_review
    if (
        permission_review.source_url != review["source_url"]
        or permission_review.source_license != review["source_license"]
    ):
        raise ApiError("SCHEMA_INVALID")
    try:
        endpoint = services.endpoints.get_approved(approval.endpoint_registration_id)
        submitted_endpoint = parse_endpoint_url(str(review["endpoint_url"]), endpoint.policy)
    except EndpointNotApproved:
        raise ApiError("FORBIDDEN") from None
    except EndpointPolicyViolation:
        raise ApiError("SCHEMA_INVALID") from None
    if submitted_endpoint != endpoint.endpoint:
        raise ApiError("SCHEMA_INVALID")
    domain_principal = _principal(principal)
    approval_document = {
        "endpoint_registration_id": str(approval.endpoint_registration_id),
        "permission_review": permission_review.model_dump(mode="json"),
        "run_plan": run_request.model_dump(mode="json"),
    }
    claim = services.submissions.begin_approval(
        submission_id=str(submission_id),
        reviewer=principal.subject_id,
        expected_version=int(review["row_version"]),
        request_id=idempotency_key.strip(),
        approval_document=approval_document,
    )
    if claim["status"] == "approved":
        result = services.submissions.get_for_review(submission_id=str(submission_id))
    else:
        run = services.run_creation.create(
            domain_principal,
            run_request,
            f"model-submission-{submission_id}",
        )
        result = services.submissions.finish_approval(
            submission_id=str(submission_id),
            reviewer=principal.subject_id,
            run_id=str(run.run_id),
            approval_digest=str(claim["approval_digest"]),
            request_id=idempotency_key.strip(),
        )
    view = SubmissionReviewView.model_validate(result)
    return respond(request, envelope(view, _meta(view)), status_code=202, cache=NO_STORE)


@router.post("/admin/model-endpoints")
def register_model_endpoint(
    request: Request,
    registration: EndpointRegistrationInput,
) -> Response:
    services = services_of(request)
    principal = _require_mfa_permission(request, Permission.ENDPOINT_APPROVE)
    if services.endpoints is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    try:
        endpoint_id = services.endpoints.register(
            _principal(principal),
            provider_kind=registration.provider_kind,
            base_url=registration.base_url,
            secret_ref=registration.secret_ref,
            policy=registration.network_policy,
            declared_capabilities=registration.declared_capabilities,
        )
    except EndpointPolicyViolation:
        raise ApiError("SCHEMA_INVALID") from None
    result = EndpointRegistrationResult(endpoint_registration_id=str(endpoint_id), status="pending")
    return respond(
        request,
        envelope(result, _meta(result)),
        status_code=201,
        cache=NO_STORE,
    )


@router.post("/admin/model-endpoints/{endpoint_id}/decision")
def decide_model_endpoint(
    request: Request,
    endpoint_id: UUID,
    decision: EndpointDecisionInput,
) -> Response:
    services = services_of(request)
    principal = _require_mfa_permission(request, Permission.ENDPOINT_APPROVE)
    if services.endpoints is None:
        raise ApiError("DEPENDENCY_UNAVAILABLE")
    try:
        services.endpoints.decide(
            _principal(principal),
            endpoint_id,
            decision=decision.decision,
            reason=decision.reason,
            expected_version=decision.expected_version,
            conformance_report=decision.conformance_report,
        )
    except EndpointPolicyViolation:
        raise ApiError("SCHEMA_INVALID") from None
    result = EndpointDecisionResult(
        endpoint_registration_id=str(endpoint_id), status=decision.decision
    )
    return respond(request, envelope(result, _meta(result)), cache=NO_STORE)


__all__ = ["router"]
