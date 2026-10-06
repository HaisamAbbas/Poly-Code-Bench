"""Load, validate and reconcile environment manifests (config/environments/<env>.yaml)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from polycodebench_core.deployment import (
    EnvironmentManifest,
    placeholders,
    separation_violations,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[4]
MANIFEST_DIR = REPO_ROOT / "config" / "environments"
ENVIRONMENTS = ("dev", "integration", "staging", "production")


class ManifestError(ValueError):
    pass


def manifest_path(environment: str, directory: Path = MANIFEST_DIR) -> Path:
    if environment not in ENVIRONMENTS:
        raise ManifestError(f"unknown environment {environment!r}")
    return directory / f"{environment}.yaml"


def load_manifest(path: Path) -> EnvironmentManifest:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ManifestError(f"{path.name}: unreadable manifest ({type(error).__name__})") from error
    try:
        return EnvironmentManifest.model_validate(document)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise ManifestError(f"{path.name}: {problems}") from None


@dataclass(frozen=True)
class ValidationReport:
    environment: str
    status: str
    unresolved_inputs: tuple[str, ...]

    @property
    def deployable(self) -> bool:
        return self.status == "deployed" and not self.unresolved_inputs


def validate_all(directory: Path = MANIFEST_DIR) -> tuple[list[ValidationReport], list[str]]:
    """Validate every manifest and the cross-environment separation rules."""

    manifests = [load_manifest(manifest_path(env, directory)) for env in ENVIRONMENTS]
    reports = [ValidationReport(m.environment, m.status, tuple(placeholders(m))) for m in manifests]
    return reports, separation_violations(manifests)


def reconcile(manifest: EnvironmentManifest, terraform_output: dict[str, Any]) -> list[str]:
    """Differences between an applied stack (``terraform output -json``) and the manifest.

    Accepts either the raw ``terraform output -json`` document (``{"deployment": {"value":
    {...}}}``) or the inner ``deployment`` value.
    """

    deployment = terraform_output
    if "deployment" in deployment:
        deployment = deployment["deployment"]
    if "value" in deployment and isinstance(deployment["value"], dict):
        deployment = deployment["value"]
    differences: list[str] = []

    def compare(label: str, expected: object, observed: object) -> None:
        if expected != observed:
            differences.append(f"{label}: manifest {expected!r} != deployed {observed!r}")

    def compare_secret_reference(
        label: str, expected_ref: str | None, observed_arn: object
    ) -> None:
        if expected_ref is None:
            if observed_arn is not None:
                differences.append(f"{label}: deployed secret has no manifest reference")
            return
        if not expected_ref.startswith("aws-sm:") or not isinstance(observed_arn, str):
            differences.append(f"{label}: manifest reference or deployed secret ARN is invalid")
            return
        arn_parts = observed_arn.split(":")
        if (
            len(arn_parts) != 7
            or arn_parts[0] != "arn"
            or not arn_parts[1].startswith("aws")
            or arn_parts[2] != "secretsmanager"
            or arn_parts[3] != manifest.identity.region
            or arn_parts[4] != manifest.identity.account_id
            or arn_parts[5] != "secret"
        ):
            differences.append(
                f"{label}: deployed secret ARN is outside the manifest AWS account/region"
            )
            return
        expected_name = expected_ref.removeprefix("aws-sm:")
        actual_name = arn_parts[6]
        if actual_name != expected_name and not actual_name.startswith(expected_name + "-"):
            differences.append(
                f"{label}: manifest secret reference does not match Terraform output"
            )

    compare("environment", manifest.environment, deployment.get("environment"))
    compare("account_id", manifest.identity.account_id, deployment.get("account_id"))
    compare("region", manifest.identity.region, deployment.get("region"))
    deployed_roles = deployment.get("service_role_arns", {})
    for role, arn in sorted(manifest.identity.service_roles.items()):
        compare(f"service role {role}", arn, deployed_roles.get(role))
    for role in sorted(set(deployed_roles) - set(manifest.identity.service_roles)):
        differences.append(f"service role {role}: deployed but not declared in the manifest")
    deployed_operator_roles = deployment.get("operator_role_arns", {})
    for role, arn in sorted(manifest.identity.operator_roles.items()):
        compare(f"operator role {role}", arn, deployed_operator_roles.get(role))
    for role in sorted(set(deployed_operator_roles) - set(manifest.identity.operator_roles)):
        differences.append(f"operator role {role}: deployed but not declared in the manifest")
    buckets = deployment.get("bucket_names", {})
    compare("bucket hidden", manifest.object_store.bucket_hidden, buckets.get("hidden"))
    compare("bucket internal", manifest.object_store.bucket_internal, buckets.get("internal"))
    compare("bucket public", manifest.object_store.bucket_public, buckets.get("public"))
    compare(
        "object-store endpoint",
        manifest.object_store.endpoint,
        deployment.get("object_store_endpoint"),
    )
    deployed_database_secrets = deployment.get("database_secret_arns", {})
    for role, reference in sorted(manifest.secrets.database_dsn_refs.items()):
        compare_secret_reference(
            f"database secret {role}", reference, deployed_database_secrets.get(role)
        )
    for role in sorted(set(deployed_database_secrets) - set(manifest.secrets.database_dsn_refs)):
        differences.append(f"database secret {role}: deployed but not declared in the manifest")
    secret_namespaces = deployment.get("secret_namespaces", {})
    compare(
        "model secret namespace",
        manifest.secrets.model_namespace.removeprefix("aws-sm:"),
        secret_namespaces.get("model"),
    )
    compare(
        "judge secret namespace",
        manifest.secrets.judge_namespace.removeprefix("aws-sm:"),
        secret_namespaces.get("judge"),
    )
    signing = deployment.get("signing_secret_arns", {})
    for key_id, reference in sorted(manifest.secrets.signing_key_refs.items()):
        compare_secret_reference(f"signing key {key_id}", reference, signing.get(key_id))
    for key_id in sorted(set(signing) - set(manifest.secrets.signing_key_refs)):
        differences.append(f"signing key {key_id}: deployed but not declared in the manifest")
    compare_secret_reference(
        "cursor signing key", manifest.secrets.cursor_key_ref, deployment.get("cursor_secret_arn")
    )
    network_cidr = deployment.get("vpc_cidr")
    compare("VPC CIDR", manifest.network.vpc_cidr, network_cidr)
    sandbox = manifest.sandbox
    compare(
        "approved guest AMI", sandbox.approved_vm_image, deployment.get("approved_guest_ami_id")
    )
    compare(
        "guest instance type", sandbox.guest_instance_type, deployment.get("guest_instance_type")
    )
    templates = deployment.get("launch_template_ids", {})
    for lane, template in sorted(sandbox.launch_templates.items()):
        compare(f"launch template {lane}", template, templates.get(lane))
    template_versions = deployment.get("launch_template_versions", {})
    for lane, version in sorted(sandbox.launch_template_versions.items()):
        deployed_version = template_versions.get(lane)
        compare(
            f"launch template version {lane}",
            version,
            str(deployed_version) if deployed_version is not None else None,
        )
    subnets = deployment.get("lane_subnet_ids", {})
    for lane, subnet in sorted(sandbox.lane_subnets.items()):
        compare(f"guest subnet {lane}", subnet, subnets.get(lane))
    security_groups = deployment.get("guest_security_group_ids", {})
    for lane, security_group in sorted(sandbox.lane_security_groups.items()):
        compare(f"guest security group {lane}", security_group, security_groups.get(lane))
    compare(
        "control security group",
        sandbox.control_security_group_id,
        deployment.get("control_security_group_id"),
    )
    deployed_control_identities = deployment.get("sandbox_control_identity_secret_arns", {})
    for role, reference in sorted(sandbox.control_identity_secret_refs.items()):
        compare_secret_reference(
            f"sandbox control identity {role}",
            reference,
            deployed_control_identities.get(role),
        )
    undeclared_control_roles = set(deployed_control_identities) - set(
        sandbox.control_identity_secret_refs
    )
    for role in sorted(undeclared_control_roles):
        differences.append(
            f"sandbox control identity {role}: deployed but not declared in the manifest"
        )
    hardware = deployment.get("hardware_class")
    if manifest.capacity.performance_hardware_class is not None:
        compare("hardware class", manifest.capacity.performance_hardware_class, hardware)
    return differences


def load_terraform_output(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ManifestError("terraform output must be a JSON object")
    return value
