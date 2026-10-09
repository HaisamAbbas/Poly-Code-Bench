"""Private benchmark-audit CLI. Dry-run is deliberately local and performs no HTTP work."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from polycodebench_core.audit_attestations import (
    AttestationTrustStore,
    SignedPublicAuditAttestation,
)
from polycodebench_core.benchmark_audit_documents import AuditPlanDocument, parse_audit_document
from polycodebench_core.benchmark_audit_registry import AuditResourceRequest
from polycodebench_core.canonical import canonical_json_bytes, parse_json_strict
from polycodebench_services.audit_attestations import verify_public_attestation

from polycodebench_api.submission_routes import (
    EndpointDecisionInput,
    EndpointRegistrationInput,
    SubmissionApprovalInput,
    SubmissionRejectInput,
)
from polycodebench_api.submissions import ModelSubmissionInput

_MAX_LOCAL_BYTES = 10 * 1024 * 1024
_MAX_AUDIT_REQUEST_BYTES = 64 * 1024
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_AUDIT_CAPABILITIES = {
    "remote_reads": [
        "audit scope-preview",
        "audit registry list",
        "audit list/show stored documents (tenant and object access checked)",
        "audit status",
        "audit attest report",
    ],
    "remote_preparations": ["audit resource-plan (bounded estimate, no dispatch)"],
    "remote_writes": [
        "audit plan (immutable document, no dispatch)",
        "audit run (planned state, dispatch_authorized=false)",
    ],
    "local_only": ["audit verify-attestation"],
    "blocked_without_reviewed_adapters": [
        "audit import (durable import and membership admission)",
        "audit matches review",
        "audit temporal assess",
        "audit sealed create",
        "audit firewall evaluate",
        "audit replacements plan",
        "audit monitor plan",
        "audit health",
        "source scans and query dispatch",
        "model or guest execution",
        "signing, publication, and alert delivery",
    ],
    "dispatch_authorized": False,
}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self, request: Request, fp: Any, code: int, msg: str, headers: Any, new_url: str
    ) -> None:
        return None


_NO_REDIRECTS = build_opener(_NoRedirectHandler)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb")
    root = parser.add_subparsers(dest="root_command", required=True)
    audit = root.add_parser("audit", help="private benchmark-audit operations")
    commands = audit.add_subparsers(dest="command", required=True)

    commands.add_parser("capabilities", help="show available audit operations and hard blocks")
    registry = commands.add_parser("registry", help="read the pinned local audit registry")
    registry.add_argument("action", choices=("list",))

    commands.add_parser("scope-preview", help="read benchmark scope and readiness metadata")
    resource_plan = commands.add_parser(
        "resource-plan", help="estimate bounded audit resources without dispatch"
    )
    resource_plan.add_argument("--payload", type=Path, required=True)

    audit_resources = (
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
    )
    list_documents = commands.add_parser("list", help="list tenant-scoped audit documents")
    list_documents.add_argument("resource", choices=audit_resources)
    list_documents.add_argument("--limit", type=int, default=50)
    list_documents.add_argument("--cursor")
    show_document = commands.add_parser("show", help="read one tenant-scoped audit document")
    show_document.add_argument("resource", choices=audit_resources)
    show_document.add_argument("document_id", type=UUID)

    imported = commands.add_parser("import", help="inspect a local benchmark package")
    imported.add_argument("--benchmark", required=True)
    imported.add_argument("--revision", required=True)
    imported.add_argument("--file", type=Path, required=True)

    plan = commands.add_parser("plan", help="validate or persist a frozen audit plan")
    plan.add_argument("--payload", type=Path, required=True)

    run = commands.add_parser("run", help="create a blocked-by-default audit run")
    run.add_argument("plan_id", type=UUID)
    run.add_argument("--query-units", type=int, default=0)
    run.add_argument("--storage-bytes", type=int, default=0)

    status = commands.add_parser("status", help="read audit run status")
    status.add_argument("run_id", type=UUID)

    review = commands.add_parser("matches", help="review match evidence")
    review_sub = review.add_subparsers(dest="matches_action", required=True)
    review_action = review_sub.add_parser("review")
    review_action.add_argument("match_id", type=UUID)
    review_action.add_argument("--decision", type=Path, required=True)

    temporal = commands.add_parser("temporal", help="review exposure chronology")
    temporal_sub = temporal.add_subparsers(dest="temporal_action", required=True)
    temporal_assess = temporal_sub.add_parser("assess")
    temporal_assess.add_argument("task_id", type=UUID)
    temporal_assess.add_argument("--model-context", type=UUID, required=True)

    sealed = commands.add_parser("sealed", help="manage encrypted evaluation manifests")
    sealed_sub = sealed.add_subparsers(dest="sealed_action", required=True)
    sealed_create = sealed_sub.add_parser("create")
    sealed_create.add_argument("--manifest", type=Path, required=True)
    sealed_create.add_argument("--policy", type=UUID, required=True)

    firewall = commands.add_parser("firewall", help="evaluate finite-scope task admission")
    firewall_sub = firewall.add_subparsers(dest="firewall_action", required=True)
    firewall_evaluate = firewall_sub.add_parser("evaluate")
    firewall_evaluate.add_argument("task_id", type=UUID)
    firewall_evaluate.add_argument("--policy", type=UUID, required=True)

    replacements = commands.add_parser("replacements", help="plan independently reviewed tasks")
    replacements_sub = replacements.add_subparsers(dest="replacements_action", required=True)
    replacements_plan = replacements_sub.add_parser("plan")
    replacements_plan.add_argument("--snapshot", type=UUID, required=True)
    replacements_plan.add_argument("--policy", type=UUID, required=True)

    monitor = commands.add_parser("monitor", help="plan bounded monitoring")
    monitor_sub = monitor.add_subparsers(dest="monitor_action", required=True)
    monitor_plan = monitor_sub.add_parser("plan")
    monitor_plan.add_argument("--snapshot", type=UUID, required=True)
    monitor_plan.add_argument("--policy", type=UUID, required=True)

    health = commands.add_parser("health", help="plan a health projection from a snapshot")
    health.add_argument("snapshot_id", type=UUID)
    health.add_argument("--context", type=UUID, required=True)
    health.add_argument("--format", choices=("json",), default="json")

    attest = commands.add_parser("attest", help="read a reviewed audit attestation")
    attest_sub = attest.add_subparsers(dest="attest_action", required=True)
    attest_report = attest_sub.add_parser("report")
    attest_report.add_argument("report_id", type=UUID)

    verify = commands.add_parser("verify-attestation", help="verify an attestation file")
    verify.add_argument("file", type=Path)
    verify.add_argument("--trust-store", type=Path, required=True, help="public keys only")

    submissions = root.add_parser("submissions", help="manage model evaluation requests")
    submission_commands = submissions.add_subparsers(dest="submission_action", required=True)
    submit = submission_commands.add_parser("submit", help="submit a model for private review")
    submit.add_argument("--payload", type=Path, required=True)
    submission_list = submission_commands.add_parser("list", help="list requests for review")
    submission_list.add_argument("--status", action="append", default=[])
    submission_list.add_argument("--limit", type=int, default=100)
    submission_show = submission_commands.add_parser("show", help="show one private review record")
    submission_show.add_argument("submission_id", type=UUID)
    submission_approve = submission_commands.add_parser("approve", help="approve a bounded run")
    submission_approve.add_argument("submission_id", type=UUID)
    submission_approve.add_argument("--decision", type=Path, required=True)
    submission_reject = submission_commands.add_parser("reject", help="reject a request")
    submission_reject.add_argument("submission_id", type=UUID)
    submission_reject.add_argument("--decision", type=Path, required=True)

    endpoints = root.add_parser("endpoints", help="register and review model endpoints")
    endpoint_commands = endpoints.add_subparsers(dest="endpoint_action", required=True)
    endpoint_register = endpoint_commands.add_parser("register", help="register a pending endpoint")
    endpoint_register.add_argument("--payload", type=Path, required=True)
    endpoint_list = endpoint_commands.add_parser("list", help="list registered endpoints")
    endpoint_list.add_argument("--status", action="append", default=[])
    endpoint_list.add_argument("--limit", type=int, default=100)
    endpoint_show = endpoint_commands.add_parser("show", help="show a private endpoint record")
    endpoint_show.add_argument("endpoint_id", type=UUID)
    endpoint_decide = endpoint_commands.add_parser(
        "decide", help="approve, reject or revoke an endpoint"
    )
    endpoint_decide.add_argument("endpoint_id", type=UUID)
    endpoint_decide.add_argument("--decision", type=Path, required=True)

    for child in commands.choices.values():
        nested = next(
            (action for action in child._actions if isinstance(action, argparse._SubParsersAction)),
            None,
        )
        leaves = nested.choices.values() if nested is not None else (child,)
        for leaf in leaves:
            leaf.add_argument(
                "--dry-run", action="store_true", help="validate locally; make no HTTP call"
            )
            leaf.add_argument(
                "--api-url", default=os.environ.get("PCB_API_URL", "https://api.invalid")
            )
            leaf.add_argument("--token", default=os.environ.get("PCB_API_TOKEN"))
            leaf.add_argument("--idempotency-key")
    for group in (submissions, endpoints):
        nested = next(
            action for action in group._actions if isinstance(action, argparse._SubParsersAction)
        )
        for leaf in nested.choices.values():
            leaf.add_argument(
                "--dry-run", action="store_true", help="validate locally; make no HTTP call"
            )
            leaf.add_argument(
                "--api-url", default=os.environ.get("PCB_API_URL", "https://api.invalid")
            )
            leaf.add_argument("--token", default=os.environ.get("PCB_API_TOKEN"))
            leaf.add_argument("--idempotency-key")
    return parser


def _command_route(args: argparse.Namespace) -> tuple[str, str] | None:
    if args.root_command == "submissions":
        action = args.submission_action
        if action == "submit":
            return "POST", "/v1/model-submissions"
        if action == "list":
            query: list[tuple[str, str]] = [("status", value) for value in args.status]
            query.append(("limit", str(args.limit)))
            return "GET", "/v1/admin/model-submissions?" + urlencode(query)
        if action == "show":
            return "GET", f"/v1/admin/model-submissions/{args.submission_id}"
        if action in {"approve", "reject"}:
            return "POST", f"/v1/admin/model-submissions/{args.submission_id}/{action}"
        return None
    if args.root_command == "endpoints":
        action = args.endpoint_action
        if action == "register":
            return "POST", "/v1/admin/model-endpoints"
        if action == "list":
            query = [("status", value) for value in args.status]
            query.append(("limit", str(args.limit)))
            return "GET", "/v1/admin/model-endpoints?" + urlencode(query)
        if action == "show":
            return "GET", f"/v1/admin/model-endpoints/{args.endpoint_id}"
        if action == "decide":
            return "POST", f"/v1/admin/model-endpoints/{args.endpoint_id}/decision"
        return None
    command = args.command
    if command == "registry":
        return "GET", "/v1/benchmark-audit/registry"
    if command == "scope-preview":
        return "GET", "/v1/benchmark-audit/scope-preview"
    if command == "resource-plan":
        return "POST", "/v1/benchmark-audit/resource-plan"
    if command == "list":
        query = [("limit", str(args.limit))]
        if args.cursor is not None:
            query.append(("cursor", args.cursor))
        return "GET", f"/v1/benchmark-audit/{args.resource}?{urlencode(query)}"
    if command == "show":
        return "GET", f"/v1/benchmark-audit/{args.resource}/{args.document_id}"
    if command == "plan":
        return "POST", "/v1/benchmark-audit/plans"
    if command == "run":
        return "POST", "/v1/benchmark-audit/runs"
    if command == "status":
        return "GET", f"/v1/benchmark-audit/runs/{args.run_id}"
    if command == "health":
        return None
    if command == "attest":
        return "GET", f"/v1/benchmark-audit/attestations/{args.report_id}"
    return None


def _validate_local_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.root_command == "submissions":
        action = args.submission_action
        if action == "list":
            if not 1 <= args.limit <= 200:
                raise ValueError("submission list limit must be in [1,200]")
            if any(
                status not in {"pending", "under_review", "approved", "rejected"}
                for status in args.status
            ):
                raise ValueError("submission status filter is invalid")
            return {"limit": args.limit, "status_filters": args.status}
        if action == "submit":
            raw = _read_local_file(args.payload, "submission")
            parse_json_strict(raw)
            submission = ModelSubmissionInput.model_validate_json(raw, strict=True)
            return {
                "model_name": submission.model_name,
                "provider": submission.provider,
                "input_bytes": len(raw),
            }
        if action == "show":
            return {"submission_id": str(args.submission_id)}
        if action == "approve":
            raw = _read_local_file(args.decision, "approval")
            parse_json_strict(raw)
            decision = SubmissionApprovalInput.model_validate_json(raw, strict=True)
            return {
                "submission_id": str(args.submission_id),
                "endpoint_registration_id": str(decision.endpoint_registration_id),
                "input_bytes": len(raw),
            }
        if action == "reject":
            raw = _read_local_file(args.decision, "rejection")
            parse_json_strict(raw)
            decision = SubmissionRejectInput.model_validate_json(raw, strict=True)
            return {
                "submission_id": str(args.submission_id),
                "expected_version": decision.expected_version,
                "input_bytes": len(raw),
            }
    if args.root_command == "endpoints":
        action = args.endpoint_action
        if action == "list":
            if not 1 <= args.limit <= 200:
                raise ValueError("endpoint list limit must be in [1,200]")
            if any(
                status not in {"pending", "approved", "rejected", "revoked"}
                for status in args.status
            ):
                raise ValueError("endpoint status filter is invalid")
            return {"limit": args.limit, "status_filters": args.status}
        if action == "register":
            raw = _read_local_file(args.payload, "endpoint registration")
            parse_json_strict(raw)
            registration = EndpointRegistrationInput.model_validate_json(raw, strict=True)
            return {
                "provider_kind": registration.provider_kind.value,
                "base_url": registration.base_url,
                "input_bytes": len(raw),
            }
        if action == "show":
            return {"endpoint_id": str(args.endpoint_id)}
        if action == "decide":
            raw = _read_local_file(args.decision, "endpoint decision")
            parse_json_strict(raw)
            decision = EndpointDecisionInput.model_validate_json(raw, strict=True)
            return {
                "endpoint_id": str(args.endpoint_id),
                "decision": decision.decision,
                "expected_version": decision.expected_version,
                "input_bytes": len(raw),
            }
    if args.command == "plan":
        content = _read_local_file(args.payload, "plan")
        document = parse_audit_document(content)
        if not isinstance(document, AuditPlanDocument):
            raise ValueError("plan file must contain a strict audit_plan document")
        return {"kind": document.kind, "document_id": str(document.id)}
    if args.command == "resource-plan":
        content = _read_local_file(args.payload, "resource plan")
        if len(content) > _MAX_AUDIT_REQUEST_BYTES:
            raise ValueError("resource plan exceeds the 64 KiB API request limit")
        parse_json_strict(content)
        request = AuditResourceRequest.model_validate_json(content, strict=True)
        return {
            "task_counts": request.task_counts,
            "source_groups": request.source_groups,
            "stages": request.stages,
            "input_bytes": len(content),
            "dispatch_allowed": False,
        }
    if args.command == "list":
        if not 1 <= args.limit <= 200:
            raise ValueError("audit list limit must be in [1,200]")
        if args.cursor is not None and len(args.cursor) > 4096:
            raise ValueError("audit list cursor exceeds the 4096 character limit")
        return {
            "resource": args.resource,
            "limit": args.limit,
            "has_cursor": args.cursor is not None,
        }
    if args.command == "show":
        return {"resource": args.resource, "document_id": str(args.document_id)}
    if args.command == "import":
        size = args.file.stat().st_size
        if size > _MAX_LOCAL_BYTES:
            raise ValueError("benchmark file exceeds the 10 MiB local input limit")
        return {"benchmark": args.benchmark, "revision": args.revision, "input_bytes": size}
    if args.command == "run":
        if (
            not 0 <= args.query_units <= _MAX_SAFE_INTEGER
            or not 0 <= args.storage_bytes <= _MAX_SAFE_INTEGER
        ):
            raise ValueError("run reservations must fit the nonnegative safe-integer range")
        return {
            "plan_document_id": str(args.plan_id),
            "reserved_query_units": args.query_units,
            "reserved_storage_bytes": args.storage_bytes,
        }
    if args.command in {"matches", "temporal", "sealed", "firewall", "replacements", "monitor"}:
        result: dict[str, Any] = {
            "command": args.command,
            "validation_scope": "command_arguments_only",
        }
        if args.command == "matches":
            content = _read_local_file(args.decision, "decision")
            json.loads(content)
            result["validation_scope"] = "decision_json_syntax"
            result["decision_bytes"] = len(content)
        elif args.command == "sealed":
            content = _read_local_file(args.manifest, "manifest")
            document = parse_audit_document(content)
            if document.kind != "sealed_manifest":
                raise ValueError("manifest file must contain a strict sealed_manifest document")
            result["validation_scope"] = "sealed_manifest_schema"
            result["manifest_bytes"] = len(content)
        return result
    if args.command == "verify-attestation":
        content = _read_local_file(args.file, "attestation")
        parse_json_strict(content)  # Reject duplicate JSON members before typed JSON validation.
        signed = SignedPublicAuditAttestation.model_validate_json(content, strict=True)
        trust_content = _read_local_file(args.trust_store, "attestation trust store")
        parse_json_strict(trust_content)
        trust_store = AttestationTrustStore.model_validate_json(trust_content, strict=True)
        verification = verify_public_attestation(signed, trust_store)
        return {
            "attestation_id": str(signed.claims.attestation_id),
            "signature_valid": verification.signature_valid,
            "trusted_key": verification.trusted_key,
            "current_endorsement": verification.current_endorsement,
            "revocation_freshness": verification.revocation_freshness,
            "result": verification.result,
            "claim_limitations": signed.claims.claim_limitations,
            "validation_scope": "canonical_signature_and_public_claims",
        }
    if args.command == "health":
        return {
            "snapshot_id": str(args.snapshot_id),
            "model_context_id": str(args.context),
            "format": args.format,
            "projection_available": False,
        }
    return {"command": args.command}


def _read_local_file(path: Path, label: str) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(_MAX_LOCAL_BYTES + 1)
    if len(content) > _MAX_LOCAL_BYTES:
        raise ValueError(f"{label} file exceeds the 10 MiB local input limit")
    return content


def _request_body(args: argparse.Namespace) -> bytes | None:
    if args.root_command == "submissions":
        action = args.submission_action
        if action == "submit":
            raw = _read_local_file(args.payload, "submission")
            value = ModelSubmissionInput.model_validate_json(raw, strict=True)
            return canonical_json_bytes(value.model_dump(mode="json"))
        if action == "approve":
            raw = _read_local_file(args.decision, "approval")
            value = SubmissionApprovalInput.model_validate_json(raw, strict=True)
            return canonical_json_bytes(value.model_dump(mode="json"))
        if action == "reject":
            raw = _read_local_file(args.decision, "rejection")
            value = SubmissionRejectInput.model_validate_json(raw, strict=True)
            return canonical_json_bytes(value.model_dump(mode="json"))
    elif args.root_command == "endpoints":
        action = args.endpoint_action
        if action == "register":
            raw = _read_local_file(args.payload, "endpoint registration")
            value = EndpointRegistrationInput.model_validate_json(raw, strict=True)
            return canonical_json_bytes(value.model_dump(mode="json"))
        if action == "decide":
            raw = _read_local_file(args.decision, "endpoint decision")
            value = EndpointDecisionInput.model_validate_json(raw, strict=True)
            return canonical_json_bytes(value.model_dump(mode="json"))
    elif args.root_command == "audit":
        if args.command == "plan":
            return _read_local_file(args.payload, "plan")
        if args.command == "resource-plan":
            raw = _read_local_file(args.payload, "resource plan")
            if len(raw) > _MAX_AUDIT_REQUEST_BYTES:
                raise ValueError("resource plan exceeds the 64 KiB API request limit")
            value = AuditResourceRequest.model_validate_json(raw, strict=True)
            return canonical_json_bytes(value.model_dump(mode="json"))
        if args.command == "run":
            return canonical_json_bytes(_validate_local_input(args))
    return None


def _is_mutation(args: argparse.Namespace) -> bool:
    if args.root_command == "submissions":
        return args.submission_action in {"submit", "approve", "reject"}
    if args.root_command == "endpoints":
        return args.endpoint_action in {"register", "decide"}
    return args.command in {"plan", "run"}


def _command_name(args: argparse.Namespace) -> str:
    if args.root_command == "audit":
        return f"audit {args.command}"
    if args.root_command == "submissions":
        return f"submissions {args.submission_action}"
    if args.root_command == "endpoints":
        return f"endpoints {args.endpoint_action}"
    return str(args.root_command)


def _request(
    method: str,
    url: str,
    token: str,
    body: bytes | None,
    extra_headers: dict[str, str],
) -> tuple[int, bytes]:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    headers.update(extra_headers)
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with _NO_REDIRECTS.open(request, timeout=15) as response:
            return response.status, response.read(2_000_000)
    except HTTPError as error:
        return error.code, error.read(256_000)
    except (URLError, TimeoutError, OSError):
        raise ConnectionError("API request could not be completed") from None


def _safe_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError("API URL contains an invalid port") from None
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("API URL must be an absolute HTTP(S) origin without credentials or a path")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("non-local API URLs must use HTTPS")
    if parsed.query or parsed.fragment:
        raise ValueError("API URL cannot contain a query or fragment")
    return value.rstrip("/")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.root_command == "audit" and args.command == "capabilities":
            print(json.dumps(_AUDIT_CAPABILITIES, sort_keys=True))
            return 0
        local_input = _validate_local_input(args)
        if args.root_command == "audit" and args.command == "verify-attestation":
            print(json.dumps(local_input, sort_keys=True))
            return 0 if local_input["signature_valid"] else 4
        route = _command_route(args)
        if args.dry_run:
            method, path = route if route is not None else (None, None)
            print(
                json.dumps(
                    {
                        "command": _command_name(args),
                        "method": method,
                        "path": path,
                        "remote_work": "none",
                        "server_capability": "available" if route is not None else "blocked",
                        "validation_scope": "local-input-only",
                        "input": local_input,
                    },
                    sort_keys=True,
                )
            )
            return 0 if route is not None else 5
        if route is None:
            print(
                "BLOCKED: this operation has no approved API adapter in this deployment.",
                file=sys.stderr,
            )
            return 3
        if _is_mutation(args) and not args.idempotency_key:
            print("INVALID: --idempotency-key is required for mutations.", file=sys.stderr)
            return 2
        body = _request_body(args)
        if not args.token:
            print("INVALID: set PCB_API_TOKEN or pass --token.", file=sys.stderr)
            return 2
        base_url = _safe_base_url(args.api_url)
        method, path = route
        headers: dict[str, str] = {}
        if args.root_command == "audit" and args.command in {"plan", "run"}:
            path += "?dry_run=false"
        if _is_mutation(args):
            headers["Idempotency-Key"] = args.idempotency_key
        if args.root_command == "audit" and args.command in {"plan", "run"}:
            headers["If-None-Match"] = "*"
        status, response = _request(method, base_url + path, args.token, body, headers)
        if status in {200, 201, 202}:
            try:
                parsed = json.loads(response)
            except json.JSONDecodeError:
                print("DEPENDENCY_ERROR: API returned invalid JSON.", file=sys.stderr)
                return 4
            print(json.dumps(parsed, sort_keys=True))
            return 0
        request_id: str | None = None
        try:
            error_payload = json.loads(response).get("error", {})
            error = error_payload.get("code", "DEPENDENCY_UNAVAILABLE")
            if not isinstance(error, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error):
                error = "DEPENDENCY_UNAVAILABLE"
            candidate_request_id = error_payload.get("request_id")
            if isinstance(candidate_request_id, str) and re.fullmatch(
                r"[A-Fa-f0-9-]{1,64}", candidate_request_id
            ):
                request_id = candidate_request_id
        except (json.JSONDecodeError, AttributeError):
            error = "DEPENDENCY_UNAVAILABLE"
        suffix = f" (request {request_id})" if request_id else ""
        print(f"{error}: request was not completed{suffix}.", file=sys.stderr)
        if status == 429:
            return 5
        return 3 if status in {401, 403, 404, 409, 412} else 2 if status < 500 else 4
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        return 2
    except ConnectionError as error:
        print(f"DEPENDENCY_ERROR: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
