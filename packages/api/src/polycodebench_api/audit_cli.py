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
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from polycodebench_core.audit_attestations import (
    AttestationTrustStore,
    SignedPublicAuditAttestation,
)
from polycodebench_core.benchmark_audit_documents import AuditPlanDocument, parse_audit_document
from polycodebench_core.canonical import canonical_json_bytes, parse_json_strict
from polycodebench_services.audit_attestations import verify_public_attestation

_MAX_LOCAL_BYTES = 10 * 1024 * 1024
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


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

    registry = commands.add_parser("registry", help="read the pinned local audit registry")
    registry.add_argument("action", choices=("list",))

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
    return parser


def _command_route(args: argparse.Namespace) -> tuple[str, str] | None:
    command = args.command
    if command == "registry":
        return "GET", "/v1/benchmark-audit/registry"
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
    if args.command == "plan":
        content = _read_local_file(args.payload, "plan")
        document = parse_audit_document(content)
        if not isinstance(document, AuditPlanDocument):
            raise ValueError("plan file must contain a strict audit_plan document")
        return {"kind": document.kind, "document_id": str(document.id)}
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
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("API URL must be an absolute HTTP(S) origin")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("non-local API URLs must use HTTPS")
    if parsed.query or parsed.fragment:
        raise ValueError("API URL cannot contain a query or fragment")
    return value.rstrip("/")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        local_input = _validate_local_input(args)
        if args.command == "verify-attestation":
            print(json.dumps(local_input, sort_keys=True))
            return 0 if local_input["signature_valid"] else 4
        route = _command_route(args)
        if args.dry_run:
            method, path = route if route is not None else (None, None)
            print(
                json.dumps(
                    {
                        "command": args.command,
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
        if args.command in {"plan", "run"} and not args.idempotency_key:
            print("INVALID: --idempotency-key is required for mutations.", file=sys.stderr)
            return 2
        if args.command == "plan":
            body = _read_local_file(args.payload, "plan")
            document = parse_audit_document(body)
            if not isinstance(document, AuditPlanDocument):
                raise ValueError("plan file must contain a strict audit_plan document")
        elif args.command == "run":
            body = canonical_json_bytes(local_input)
        else:
            body = None
        if not args.token:
            print("INVALID: set PCB_API_TOKEN or pass --token.", file=sys.stderr)
            return 2
        base_url = _safe_base_url(args.api_url)
        method, path = route
        headers: dict[str, str] = {}
        if args.command in {"plan", "run"}:
            path += "?dry_run=false"
            headers["Idempotency-Key"] = args.idempotency_key
            headers["If-None-Match"] = "*"
        status, response = _request(method, base_url + path, args.token, body, headers)
        if status in {200, 201}:
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
