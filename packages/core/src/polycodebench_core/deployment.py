"""Environment manifests and identity-derived deployment authority (T 22.1, T 10.5).

A process never becomes ``staging`` or ``production`` because a string says so. Its environment,
role and isolation tier are derived from the *verified* principal it runs as (for AWS, the
STS caller identity of its task role) matched against the reviewed manifest for that
environment. ``PCB_ENVIRONMENT``/``PCB_ROLE`` are only claims; a mismatch refuses startup.

This module is pure: it parses already-loaded manifest documents and takes the verified caller
principal as data. Fetching the caller identity (STS) lives in ``polycodebench_operations``.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EnvironmentName = Literal["dev", "integration", "staging", "production"]
IsolationTier = Literal["development", "production"]
ManifestStatus = Literal["template", "deployed"]

PLACEHOLDER = "REQUIRED"
"""Marker for owner-supplied values not yet available. Only template manifests may contain it."""

TIER_FOR_ENVIRONMENT: Mapping[str, IsolationTier] = {
    "dev": "development",
    "integration": "development",
    "staging": "production",
    "production": "production",
}

_ROLE_ARN = re.compile(
    r"^arn:aws[a-z-]*:iam::(?P<account>\d{12}):role/(?:[\w+=,.@-]+/)*(?P<name>[\w+=,.@-]+)$"
)
_ASSUMED_ROLE_ARN = re.compile(
    r"^arn:aws[a-z-]*:sts::(?P<account>\d{12}):assumed-role/(?P<name>[\w+=,.@-]+)/[\w+=,.@-]+$"
)


class DeploymentRefused(RuntimeError):
    """Startup refusal. Messages name the mismatch, never secret values."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IdentitySection(_Strict):
    provider: Literal["none", "aws"]
    account_id: str | None = None
    region: str | None = None
    service_roles: dict[str, str] = Field(default_factory=dict)
    operator_roles: dict[str, str] = Field(default_factory=dict)


class ObjectStoreSection(_Strict):
    endpoint: str
    bucket_hidden: str
    bucket_internal: str
    bucket_public: str


class SecretsSection(_Strict):
    database_dsn_refs: dict[str, str]
    model_namespace: str
    judge_namespace: str
    signing_key_refs: dict[str, str]
    active_signing_key_id: str
    cursor_key_ref: str | None = None


class SandboxSection(_Strict):
    provider: Literal["local_docker", "ec2_vm"]
    approved_vm_image: str | None = None
    launch_templates: dict[str, str] = Field(default_factory=dict)
    launch_template_versions: dict[str, str] = Field(default_factory=dict)
    lane_subnets: dict[str, str] = Field(default_factory=dict)
    guest_ttl_seconds: int = Field(ge=60, le=86_400)
    orphan_alert_grace_seconds: int = Field(default=600, ge=0, le=3_600)


class TelemetrySection(_Strict):
    otel_endpoint: str | None = None
    metrics_port: int = Field(ge=1, le=65_535)
    log_format: Literal["json", "text"]


class CapacitySection(_Strict):
    max_concurrency: int = Field(ge=1, le=1_024)
    queue_classes: list[str] = Field(min_length=1)
    performance_hardware_class: str | None = None


class PolicySection(_Strict):
    allowed_data: list[Literal["synthetic_fixtures", "public_samples", "private_tasks"]]
    fake_transports_allowed: bool
    ranked_release_allowed: bool
    publication_board: str


class NetworkSection(_Strict):
    vpc_cidr: str | None = None


class EnvironmentManifest(_Strict):
    schema_version: Literal[1]
    environment: EnvironmentName
    status: ManifestStatus
    isolation_tier: IsolationTier
    identity: IdentitySection
    object_store: ObjectStoreSection
    secrets: SecretsSection
    sandbox: SandboxSection
    telemetry: TelemetrySection
    capacity: CapacitySection
    policy: PolicySection
    network: NetworkSection = NetworkSection()

    @model_validator(mode="after")
    def _environment_rules(self) -> EnvironmentManifest:
        env = self.environment
        if self.isolation_tier != TIER_FOR_ENVIRONMENT[env]:
            raise ValueError(f"{env} must declare isolation_tier {TIER_FOR_ENVIRONMENT[env]}")
        trusted = env in {"staging", "production"}
        if trusted:
            if self.identity.provider != "aws":
                raise ValueError(f"{env} requires a verifiable cloud identity provider")
            if self.sandbox.provider != "ec2_vm":
                raise ValueError(f"{env} requires the disposable VM sandbox driver")
            if self.policy.fake_transports_allowed:
                raise ValueError(f"{env} must not allow fake model transports")
            if not self.identity.service_roles:
                raise ValueError(f"{env} must list its service role identities")
            if not self.object_store.endpoint.startswith("https://"):
                raise ValueError(f"{env} object store endpoint must use HTTPS")
            if self.telemetry.log_format != "json":
                raise ValueError(f"{env} requires structured JSON logs")
        else:
            if self.sandbox.provider != "local_docker":
                raise ValueError(f"{env} runs development sandboxes only")
            if self.policy.ranked_release_allowed:
                raise ValueError(f"{env} cannot publish ranked releases")
            if "private_tasks" in self.policy.allowed_data:
                raise ValueError(f"{env} may not hold private tasks")
        if env != "production" and self.policy.ranked_release_allowed:
            raise ValueError("only production may publish ranked releases")
        if self.secrets.active_signing_key_id not in self.secrets.signing_key_refs:
            raise ValueError("active signing key must be one of the declared signing keys")
        buckets = [
            self.object_store.bucket_hidden,
            self.object_store.bucket_internal,
            self.object_store.bucket_public,
        ]
        if len(set(buckets)) != 3:
            raise ValueError("hidden, internal and public buckets must be distinct")
        if self.identity.provider == "aws" and self.status == "deployed":
            account = self.identity.account_id or ""
            if not re.fullmatch(r"\d{12}", account):
                raise ValueError("deployed AWS manifests require a 12-digit account_id")
            for role, arn in {
                **self.identity.service_roles,
                **self.identity.operator_roles,
            }.items():
                match = _ROLE_ARN.fullmatch(arn)
                if not match or match["account"] != account:
                    raise ValueError(f"role {role} must be an IAM role ARN in account {account}")
        if self.status == "deployed" and placeholders(self):
            raise ValueError("deployed manifests may not contain REQUIRED placeholders")
        if self.status == "deployed" and self.sandbox.provider == "ec2_vm":
            expected_lanes = {"solve", "grading", "admission", "performance"}
            if set(self.sandbox.launch_templates) != expected_lanes:
                raise ValueError("deployed AWS manifests require a template ID for every lane")
            if set(self.sandbox.launch_template_versions) != expected_lanes:
                raise ValueError(
                    "deployed AWS manifests require a pinned version for every lane template"
                )
            if any(
                not value.isdecimal() or int(value) < 1
                for value in self.sandbox.launch_template_versions.values()
            ):
                raise ValueError("deployed AWS launch-template versions must be positive integers")
        if self.network.vpc_cidr and PLACEHOLDER not in self.network.vpc_cidr:
            ipaddress.ip_network(self.network.vpc_cidr)
        return self


def placeholders(manifest: EnvironmentManifest) -> list[str]:
    """Dotted paths of every value still carrying the REQUIRED placeholder."""

    found: list[str] = []

    def walk(value: object, path: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str) and PLACEHOLDER in value:
            found.append(path)

    walk(manifest.model_dump(mode="json"), "")
    return found


def separation_violations(manifests: Iterable[EnvironmentManifest]) -> list[str]:
    """Resources that two environments share (T 22.1 requires separate everything)."""

    owners: dict[tuple[str, str], str] = {}
    violations: list[str] = []
    networks: list[tuple[str, ipaddress.IPv4Network | ipaddress.IPv6Network]] = []

    def claim(kind: str, value: str | None, env: str) -> None:
        if not value or PLACEHOLDER in value:
            return
        key = (kind, value)
        other = owners.setdefault(key, env)
        if other != env:
            violations.append(f"{kind} {value!r} is shared by {other} and {env}")

    for manifest in manifests:
        env = manifest.environment
        store = manifest.object_store
        for bucket in (store.bucket_hidden, store.bucket_internal, store.bucket_public):
            claim("bucket", bucket, env)
        for ref in manifest.secrets.database_dsn_refs.values():
            claim("database credential", ref, env)
        for ref in manifest.secrets.signing_key_refs.values():
            claim("signing key", ref, env)
        for key_id in manifest.secrets.signing_key_refs:
            claim("signing key id", key_id, env)
        claim("model secret namespace", manifest.secrets.model_namespace, env)
        claim("judge secret namespace", manifest.secrets.judge_namespace, env)
        claim("cursor key", manifest.secrets.cursor_key_ref, env)
        for arn in {**manifest.identity.service_roles, **manifest.identity.operator_roles}.values():
            claim("service identity", arn, env)
        if env in {"staging", "production"}:
            claim("cloud account", manifest.identity.account_id, env)
        cidr = manifest.network.vpc_cidr
        if cidr and PLACEHOLDER not in cidr:
            network = ipaddress.ip_network(cidr)
            for other_env, other in networks:
                if (
                    other_env != env
                    and network.version == other.version
                    and network.overlaps(other)
                ):
                    violations.append(f"VPC {cidr} of {env} overlaps {other} of {other_env}")
            networks.append((env, network))
    return violations


@dataclass(frozen=True)
class CallerPrincipal:
    """A principal reported by a trusted identity service (never by the request)."""

    account_id: str
    arn: str
    verified_by: Literal["aws-sts"]


@dataclass(frozen=True)
class VerifiedDeployment:
    environment: EnvironmentName
    role: str
    isolation_tier: IsolationTier
    principal: str
    verified_by: Literal["aws-sts", "local-development"]
    ranked_release_allowed: bool

    def require(self, environment: EnvironmentName) -> None:
        if self.environment != environment:
            raise DeploymentRefused(
                f"operation requires {environment}; verified deployment is {self.environment}"
            )


def _role_name_from_principal(arn: str) -> tuple[str, str] | None:
    for pattern in (_ASSUMED_ROLE_ARN, _ROLE_ARN):
        match = pattern.fullmatch(arn)
        if match:
            return match["account"], match["name"]
    return None


def resolve_deployment(
    manifest: EnvironmentManifest,
    *,
    claimed_environment: str,
    claimed_role: str,
    caller: CallerPrincipal | None,
) -> VerifiedDeployment:
    """Derive the deployment authority of this process, or refuse.

    ``claimed_*`` come from the process environment and are only compared, never trusted.
    """

    if claimed_environment != manifest.environment:
        raise DeploymentRefused(
            f"claimed environment {claimed_environment!r} does not match the "
            f"{manifest.environment} manifest"
        )
    if manifest.identity.provider == "none":
        if caller is not None:
            raise DeploymentRefused(
                "a cloud principal is present but the manifest is development-only; "
                "refusing to run trusted credentials under a development policy"
            )
        return VerifiedDeployment(
            environment=manifest.environment,
            role=claimed_role,
            isolation_tier="development",
            principal="local-development",
            verified_by="local-development",
            ranked_release_allowed=False,
        )
    if manifest.status != "deployed":
        raise DeploymentRefused(
            f"{manifest.environment} manifest is a template with unresolved deployment inputs"
        )
    if caller is None:
        raise DeploymentRefused(f"{manifest.environment} requires a verified cloud principal")
    expected_arn = manifest.identity.service_roles.get(
        claimed_role
    ) or manifest.identity.operator_roles.get(claimed_role)
    if expected_arn is None:
        raise DeploymentRefused(f"role {claimed_role!r} is not declared for {manifest.environment}")
    observed = _role_name_from_principal(caller.arn)
    expected = _role_name_from_principal(expected_arn)
    if observed is None or expected is None:
        raise DeploymentRefused("caller principal is not an IAM role session")
    if caller.account_id != manifest.identity.account_id or observed[0] != caller.account_id:
        raise DeploymentRefused(f"caller account does not match the {manifest.environment} account")
    if observed != expected:
        raise DeploymentRefused(
            f"caller role {observed[1]!r} is not the {manifest.environment} {claimed_role} identity"
        )
    return VerifiedDeployment(
        environment=manifest.environment,
        role=claimed_role,
        isolation_tier=manifest.isolation_tier,
        principal=caller.arn,
        verified_by=caller.verified_by,
        ranked_release_allowed=manifest.policy.ranked_release_allowed,
    )


def admissible_result_tiers(scope: Literal["ranked", "exploratory"]) -> frozenset[str]:
    """Isolation tiers a release of ``scope`` may contain (T 10.5)."""

    return (
        frozenset({"production"}) if scope == "ranked" else frozenset({"production", "development"})
    )


def refuse_inadmissible_tiers(
    scope: Literal["ranked", "exploratory"], tiers: Iterable[str]
) -> None:
    allowed = admissible_result_tiers(scope)
    bad = sorted({tier for tier in tiers if tier not in allowed})
    if bad:
        raise DeploymentRefused(f"{scope} releases refuse results with isolation tier {bad}")


__all__ = [
    "PLACEHOLDER",
    "TIER_FOR_ENVIRONMENT",
    "CallerPrincipal",
    "DeploymentRefused",
    "EnvironmentManifest",
    "VerifiedDeployment",
    "admissible_result_tiers",
    "placeholders",
    "refuse_inadmissible_tiers",
    "resolve_deployment",
    "separation_violations",
]
