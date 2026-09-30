"""Endpoint checks. The static check never sends a request to the endpoint."""

from __future__ import annotations

from typing import Any

from polycodebench_core.endpoint_policy import RegisteredEndpoint, check_resolved_addresses
from polycodebench_core.model_contracts import EndpointPolicyViolation

from polycodebench_orchestration.gateway.secrets import SecretResolver
from polycodebench_orchestration.gateway.transport import Resolver, TransportError, system_resolver


def static_endpoint_check(
    endpoint: RegisteredEndpoint,
    secrets: SecretResolver,
    *,
    resolver: Resolver = system_resolver,
) -> dict[str, Any]:
    """Policy, DNS and secret-provisioning checks. Resolving DNS is the only network access."""
    report: dict[str, Any] = {
        "endpoint_id": str(endpoint.endpoint_id),
        "provider_kind": endpoint.provider_kind.value,
        "url": endpoint.endpoint.url,
        "policy": endpoint.policy.kind.value,
        "contacted_endpoint": False,
    }
    try:
        addresses = resolver(endpoint.endpoint.host, endpoint.endpoint.port)
        permitted = check_resolved_addresses(addresses, endpoint.policy)
        report["resolution"] = {"addresses": [str(a) for a in permitted], "permitted": True}
    except TransportError as error:
        report["resolution"] = {"permitted": False, "reason": error.code}
    except EndpointPolicyViolation as error:
        report["resolution"] = {"permitted": False, "reason": str(error)}
    try:
        report["secret_provisioned"] = True
        secrets.resolve(endpoint.secret_ref)
    except EndpointPolicyViolation as error:
        report["secret_provisioned"] = False
        report["secret_reason"] = str(error)
    report["declared_capabilities"] = endpoint.declared_capabilities.model_dump(mode="json")
    report["ok"] = bool(report["resolution"]["permitted"]) and bool(report["secret_provisioned"])
    return report
