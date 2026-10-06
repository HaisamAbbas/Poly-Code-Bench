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

    compare("environment", manifest.environment, deployment.get("environment"))
    compare("account_id", manifest.identity.account_id, deployment.get("account_id"))
    deployed_roles = deployment.get("service_role_arns", {})
    for role, arn in sorted(manifest.identity.service_roles.items()):
        compare(f"service role {role}", arn, deployed_roles.get(role))
    for role in sorted(set(deployed_roles) - set(manifest.identity.service_roles)):
        differences.append(f"service role {role}: deployed but not declared in the manifest")
    buckets = deployment.get("bucket_names", {})
    compare("bucket hidden", manifest.object_store.bucket_hidden, buckets.get("hidden"))
    compare("bucket internal", manifest.object_store.bucket_internal, buckets.get("internal"))
    compare("bucket public", manifest.object_store.bucket_public, buckets.get("public"))
    signing = deployment.get("signing_secret_arns", {})
    for key_id in manifest.secrets.signing_key_refs:
        if key_id not in signing:
            differences.append(f"signing key {key_id}: no deployed secret")
    templates = deployment.get("launch_template_ids", {})
    for lane, template in sorted(manifest.sandbox.launch_templates.items()):
        compare(f"launch template {lane}", template, templates.get(lane))
    template_versions = deployment.get("launch_template_versions", {})
    for lane, version in sorted(manifest.sandbox.launch_template_versions.items()):
        deployed_version = template_versions.get(lane)
        compare(
            f"launch template version {lane}",
            version,
            str(deployed_version) if deployed_version is not None else None,
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
