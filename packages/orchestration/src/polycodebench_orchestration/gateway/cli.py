"""``pcb-model``: plan, endpoint checks, registration and reconciliation.

Exit codes follow the CLI contract: 0 success, 2 validation/config error, 3 permission error,
4 budget or compatibility block, 5 infrastructure failure. Secret values are never printed and
the static ``check`` never contacts the endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from polycodebench_core.application_errors import AuthorizationError, ServiceError
from polycodebench_core.endpoint_policy import EndpointNetworkPolicy
from polycodebench_core.model_contracts import (
    BudgetExhausted,
    CapabilityUnsupported,
    CostBoundUnavailable,
    ModelCapabilities,
    ProviderKind,
    Usage,
)
from polycodebench_core.model_planning import ModelConfig
from polycodebench_core.models import ProtocolDefinition
from polycodebench_persistence.database import Database
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.model_ledger import PostgresModelLedger
from polycodebench_services.model_endpoints import ModelEndpointService
from polycodebench_services.rbac import Principal, Role

from polycodebench_orchestration.gateway.adapters.anthropic import AnthropicAdapter
from polycodebench_orchestration.gateway.adapters.base import BaseAdapter
from polycodebench_orchestration.gateway.adapters.google import GoogleAdapter
from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
from polycodebench_orchestration.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from polycodebench_orchestration.gateway.conformance import run_conformance
from polycodebench_orchestration.gateway.endpoint_check import static_endpoint_check
from polycodebench_orchestration.gateway.plan import plan_model_run
from polycodebench_orchestration.gateway.secrets import EnvironmentSecretResolver
from polycodebench_orchestration.gateway.transport import PinnedHttpTransport

EXIT_OK, EXIT_VALIDATION, EXIT_PERMISSION, EXIT_BLOCKED, EXIT_INFRA = 0, 2, 3, 4, 5
ADAPTERS: dict[ProviderKind, BaseAdapter] = {
    ProviderKind.OPENAI_COMPATIBLE: OpenAICompatibleAdapter(),
    ProviderKind.ANTHROPIC: AnthropicAdapter(),
    ProviderKind.GOOGLE: GoogleAdapter(),
    ProviderKind.LOCAL: LocalEndpointAdapter(),
}


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pcb-model")
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="compatibility and worst-case cost; no provider calls")
    plan.add_argument("--config", required=True, help="resolved model_config JSON")
    plan.add_argument("--protocol", required=True, help="protocol definition JSON")
    plan.add_argument("--tasks", type=int, required=True)
    plan.add_argument("--samples", type=int, required=True)
    plan.add_argument("--max-request-bytes", type=int)
    plan.add_argument("--max-deliveries", type=int, default=3)

    check = commands.add_parser("check", help="static endpoint check; --live probes the endpoint")
    check.add_argument("endpoint_id", type=UUID)
    check.add_argument("--live", metavar="MODEL_CONFIG_JSON")

    register = commands.add_parser("register", help="register a pending endpoint (administrator)")
    register.add_argument("--provider", required=True, choices=[k.value for k in ProviderKind])
    register.add_argument("--url", required=True)
    register.add_argument("--secret-ref", required=True)
    register.add_argument("--policy", required=True, help="EndpointNetworkPolicy JSON")
    register.add_argument("--capabilities", required=True, help="declared ModelCapabilities JSON")

    decide = commands.add_parser("decide", help="approve, reject or revoke an endpoint")
    decide.add_argument("endpoint_id", type=UUID)
    decide.add_argument("decision", choices=("approved", "rejected", "revoked"))
    decide.add_argument("--reason", required=True)
    decide.add_argument("--expected-version", type=int, required=True)
    decide.add_argument("--conformance", help="conformance report JSON (required for local)")

    summary = commands.add_parser("account", help="budget account balances and ledger check")
    summary.add_argument("account_id", type=UUID)

    reconcile = commands.add_parser("reconcile", help="resolve retained exposure with evidence")
    reconcile.add_argument("delivery_id", type=UUID)
    reconcile.add_argument("--evidence", required=True)
    reconcile.add_argument("--unbilled", action="store_true")
    reconcile.add_argument("--cost-micro-usd", type=int)
    reconcile.add_argument("--input-tokens", type=int)
    reconcile.add_argument("--output-tokens", type=int)
    return parser


def _principal() -> Principal:
    subject = os.environ.get("PCB_SERVICE_IDENTITY", "")
    roles = {Role(item) for item in os.environ.get("PCB_ROLES", "").split(",") if item}
    return Principal(subject, frozenset(roles))


def _run(args: argparse.Namespace) -> int:
    if args.command == "plan":
        config = ModelConfig.model_validate(_load(args.config), strict=False)
        protocol = ProtocolDefinition.model_validate(_load(args.protocol), strict=False)
        result = plan_model_run(
            config=config,
            protocol=protocol,
            adapter=ADAPTERS[config.provider_kind],
            tasks=args.tasks,
            samples_per_task=args.samples,
            max_request_bytes=args.max_request_bytes,
            max_deliveries=args.max_deliveries,
        )
        _emit(result.model_dump(mode="json"))
        return EXIT_OK if result.compatible else EXIT_BLOCKED

    database_url = os.environ.get("PCB_DATABASE_URL")
    if not database_url:
        print("PCB_DATABASE_URL is required", file=sys.stderr)
        return EXIT_VALIDATION
    database = Database(database_url)
    try:
        endpoints = PostgresEndpointRepository(database.engine)
        namespace = os.environ.get("PCB_MODEL_SECRET_NAMESPACE", "models")
        secrets = EnvironmentSecretResolver(namespace)
        if args.command == "check":
            endpoint, status = endpoints.get_for_conformance(args.endpoint_id)
            report = static_endpoint_check(endpoint, secrets)
            report["approval_status"] = status
            if args.live:
                config = ModelConfig.model_validate(_load(args.live), strict=False)
                if config.endpoint_id != endpoint.endpoint_id:
                    print("model config targets a different endpoint", file=sys.stderr)
                    return EXIT_VALIDATION
                if not report["ok"]:
                    _emit(report)
                    return EXIT_BLOCKED
                conformance = asyncio.run(
                    run_conformance(
                        ADAPTERS[endpoint.provider_kind],
                        PinnedHttpTransport(),
                        endpoint,
                        config,
                        secrets.resolve(endpoint.secret_ref),
                    )
                )
                report["contacted_endpoint"] = True
                report["conformance"] = conformance
                report["ok"] = bool(conformance["passed"])
            _emit(report)
            return EXIT_OK if report["ok"] else EXIT_BLOCKED
        if args.command in {"register", "decide"}:
            service = ModelEndpointService(endpoints)
            if args.command == "register":
                endpoint_id = service.register(
                    _principal(),
                    provider_kind=ProviderKind(args.provider),
                    base_url=args.url,
                    secret_ref=args.secret_ref,
                    policy=EndpointNetworkPolicy.model_validate(_load(args.policy), strict=False),
                    declared_capabilities=ModelCapabilities.model_validate(
                        _load(args.capabilities), strict=False
                    ),
                )
                _emit({"endpoint_id": str(endpoint_id), "approval_status": "pending"})
            else:
                service.decide(
                    _principal(),
                    args.endpoint_id,
                    decision=args.decision,
                    reason=args.reason,
                    expected_version=args.expected_version,
                    conformance_report=_load(args.conformance) if args.conformance else None,
                )
                _emit({"endpoint_id": str(args.endpoint_id), "approval_status": args.decision})
            return EXIT_OK
        ledger = PostgresModelLedger(database.engine)
        if args.command == "account":
            _emit(
                {
                    "summary": ledger.account_summary(args.account_id),
                    "ledger_discrepancies": ledger.verify_balances(args.account_id),
                    "unresolved_exposure": ledger.unresolved_exposure(args.account_id),
                }
            )
            return EXIT_OK
        usage = None
        if args.input_tokens is not None or args.output_tokens is not None:
            usage = Usage(input_tokens=args.input_tokens, output_tokens=args.output_tokens)
        settled = ledger.reconcile(
            delivery_id=args.delivery_id,
            actor=_principal().subject_id or "unknown",
            evidence=args.evidence,
            unbilled=args.unbilled,
            cost_micro_usd=args.cost_micro_usd,
            usage=usage,
        )
        _emit(settled.__dict__)
        return EXIT_OK
    finally:
        database.dispose()


def main() -> int:
    args = _parser().parse_args()
    try:
        return _run(args)
    except AuthorizationError:
        print("permission denied", file=sys.stderr)
        return EXIT_PERMISSION
    except (BudgetExhausted, CostBoundUnavailable, CapabilityUnsupported) as error:
        print(f"blocked: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    except ServiceError as error:
        print(f"{error.code}", file=sys.stderr)
        return EXIT_INFRA if error.status_code >= 500 else EXIT_VALIDATION
    except ValueError as error:
        print(f"invalid input: {type(error).__name__}", file=sys.stderr)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
