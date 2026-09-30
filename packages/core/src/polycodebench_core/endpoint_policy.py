"""Endpoint registration and network policy rules (pure; DNS is resolved by the caller).

Public provider endpoints are contacted only over HTTPS, only at reviewed hostnames, and
only at globally routable addresses. Local inference endpoints need an explicit internal
registration naming the CIDRs they may resolve to. Every resolved address is checked on
every connection so DNS rebinding and mixed answers cannot reach private space.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, model_validator

from polycodebench_core.model_contracts import (
    EndpointPolicyViolation,
    ModelCapabilities,
    ProviderKind,
    Strict,
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SECRET_REF = re.compile(r"^secret://([a-z][a-z0-9-]{0,62})/([a-z][a-z0-9-]{0,127})$")
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".localdomain")
_FORBIDDEN_URL_CHARS = frozenset("\\\r\n\t ")
MAX_URL_LENGTH = 512
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
_ALWAYS_BLOCKED_V4 = (
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. cloud metadata 169.254.169.254
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
)
_ALWAYS_BLOCKED_V6 = (
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fd00:ec2::/32"),  # AWS IMDS IPv6
    ipaddress.ip_network("fec0::/10"),  # deprecated site-local
)


class NetworkPolicyKind(StrEnum):
    PUBLIC_ALLOWLIST = "public-https-allowlist"
    INTERNAL_LOCAL = "internal-local"


class EndpointNetworkPolicy(Strict):
    kind: NetworkPolicyKind
    allowed_hosts: tuple[str, ...] = ()
    allowed_cidrs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def coherent(self) -> EndpointNetworkPolicy:
        if self.kind is NetworkPolicyKind.PUBLIC_ALLOWLIST:
            if not self.allowed_hosts or self.allowed_cidrs:
                raise ValueError("public policy requires allowed_hosts and no CIDRs")
            for host in self.allowed_hosts:
                if normalize_host(host) != host or _as_ip(host) is not None:
                    raise ValueError("public allowed hosts must be normalized DNS names")
        else:
            if not self.allowed_cidrs or self.allowed_hosts:
                raise ValueError("internal policy requires allowed_cidrs and no public hosts")
            for cidr in self.allowed_cidrs:
                _validate_internal_cidr(cidr)
        return self

    def networks(self) -> tuple[IPNetwork, ...]:
        return tuple(ipaddress.ip_network(cidr) for cidr in self.allowed_cidrs)


class ParsedEndpoint(Strict):
    scheme: str
    host: str
    port: Annotated[int, Field(ge=1, le=65535)]
    base_path: str

    @property
    def url(self) -> str:
        default = (self.scheme == "https" and self.port == 443) or (
            self.scheme == "http" and self.port == 80
        )
        netloc = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{netloc}{'' if default else f':{self.port}'}{self.base_path}"


class RegisteredEndpoint(Strict):
    """An approved endpoint as the gateway sees it: no secret value, only its reference."""

    endpoint_id: UUID
    provider_kind: ProviderKind
    endpoint: ParsedEndpoint
    secret_ref: str
    policy: EndpointNetworkPolicy
    declared_capabilities: ModelCapabilities


def normalize_host(host: str) -> str:
    return host.lower().rstrip(".")


def _as_ip(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def parse_secret_ref(ref: str) -> tuple[str, str]:
    match = _SECRET_REF.fullmatch(ref)
    if match is None:
        raise EndpointPolicyViolation("secret reference must be secret://<namespace>/<name>")
    return match.group(1), match.group(2)


def parse_endpoint_url(url: str, policy: EndpointNetworkPolicy) -> ParsedEndpoint:
    """Validate a registration URL syntactically. No DNS and no network access."""
    if len(url) > MAX_URL_LENGTH or not url.isascii() or _FORBIDDEN_URL_CHARS & set(url):
        raise EndpointPolicyViolation("endpoint URL is malformed")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise EndpointPolicyViolation("endpoint URL is malformed") from None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise EndpointPolicyViolation("endpoint URL must not embed credentials")
    if parts.query or parts.fragment or not parts.hostname:
        raise EndpointPolicyViolation("endpoint URL must not contain a query or fragment")
    path = parts.path.rstrip("/")
    if re.search(r"(?i)%(2e|2f|5c|00)", path) or ".." in path.split("/") or "//" in path:
        raise EndpointPolicyViolation("endpoint path is not canonical")
    host = normalize_host(parts.hostname)
    literal = _as_ip(host)
    if policy.kind is NetworkPolicyKind.PUBLIC_ALLOWLIST:
        if parts.scheme != "https":
            raise EndpointPolicyViolation("public endpoints require HTTPS")
        if literal is not None:
            raise EndpointPolicyViolation("public endpoints must use DNS names, not IP literals")
        if host not in policy.allowed_hosts:
            raise EndpointPolicyViolation("endpoint host is not on the reviewed allowlist")
        if (port or 443) != 443:
            raise EndpointPolicyViolation("public endpoints must use port 443")
        _validate_dns_name(host)
    else:
        if parts.scheme not in {"http", "https"}:
            raise EndpointPolicyViolation("internal endpoints require http or https")
        if literal is None:
            _validate_dns_name(host, allow_internal_suffix=True)
        elif not any(literal in net for net in policy.networks()):
            raise EndpointPolicyViolation("endpoint address is outside the registered CIDRs")
    return ParsedEndpoint(
        scheme=parts.scheme,
        host=str(literal) if literal is not None else host,
        port=port or (443 if parts.scheme == "https" else 80),
        base_path=path,
    )


def check_resolved_addresses(
    addresses: Iterable[str], policy: EndpointNetworkPolicy
) -> tuple[IPAddress, ...]:
    """Every resolved address must be permitted; one bad answer rejects the connection."""
    parsed: list[IPAddress] = []
    for raw in addresses:
        address = _as_ip(raw)
        if address is None:
            raise EndpointPolicyViolation("resolver returned a non-address")
        parsed.append(address)
    if not parsed:
        raise EndpointPolicyViolation("endpoint host did not resolve")
    for address in parsed:
        if policy.kind is NetworkPolicyKind.PUBLIC_ALLOWLIST:
            if not _is_public(address):
                raise EndpointPolicyViolation("endpoint resolved to a non-public address")
        elif _always_blocked(address) or not any(address in net for net in policy.networks()):
            raise EndpointPolicyViolation("endpoint resolved outside the registered CIDRs")
    return tuple(parsed)


def required_policy_kind(provider: ProviderKind) -> NetworkPolicyKind:
    """Local inference is always internal; every other provider is public-only."""
    return (
        NetworkPolicyKind.INTERNAL_LOCAL
        if provider is ProviderKind.LOCAL
        else NetworkPolicyKind.PUBLIC_ALLOWLIST
    )


def _validate_dns_name(host: str, *, allow_internal_suffix: bool = False) -> None:
    labels = host.split(".")
    last = labels[-1]
    if (
        len(host) > 253
        or (len(labels) < 2 and not allow_internal_suffix)
        or any(not _LABEL.fullmatch(label) for label in labels)
        or last.isdigit()
        or last.startswith("0x")
        or (not allow_internal_suffix and (host == "localhost" or host.endswith(_BLOCKED_SUFFIXES)))
    ):
        raise EndpointPolicyViolation("endpoint hostname is not acceptable")


def _unwrap(address: IPAddress) -> IPAddress:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address.ipv4_mapped
        if address in _NAT64:
            return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if address in _SIX_TO_FOUR:
            return ipaddress.IPv4Address((int(address) >> 80) & 0xFFFFFFFF)
        if address.teredo is not None:
            return address.teredo[1]
    return address


def _is_ipv4_compatible(address: IPAddress) -> bool:
    """Deprecated ``::a.b.c.d`` form; ``::`` and ``::1`` are handled by their own rules."""
    return address.version == 6 and 1 < int(address) < 2**32


def _always_blocked(address: IPAddress) -> bool:
    if _is_ipv4_compatible(address):
        return True
    address = _unwrap(address)
    blocked = _ALWAYS_BLOCKED_V4 if address.version == 4 else _ALWAYS_BLOCKED_V6
    return any(address in net for net in blocked)


def _is_public(address: IPAddress) -> bool:
    inner = _unwrap(address)
    return bool(inner.is_global) and not inner.is_multicast and not _always_blocked(inner)


def _validate_internal_cidr(cidr: str) -> None:
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except ValueError:
        raise ValueError("allowed CIDR must be a strict network") from None
    minimum_prefix = 8 if network.version == 4 else 32
    if network.prefixlen < minimum_prefix:
        raise ValueError("allowed CIDR is too broad")
    blocked = _ALWAYS_BLOCKED_V4 if network.version == 4 else _ALWAYS_BLOCKED_V6
    if any(network.overlaps(item) for item in blocked):
        raise ValueError("allowed CIDR overlaps a blocked range")
    if not (network.is_loopback or network.is_private) or network.is_global:
        raise ValueError("internal registration requires loopback or private address space")
