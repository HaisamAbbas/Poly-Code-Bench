"""Opt-in local assembly for the independent evaluator worker lane."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import UUID

from polycodebench_core.canonical import (
    canonical_digest,
    canonical_document_digest,
    canonical_envelope,
)
from polycodebench_core.deployment import (
    CallerPrincipal,
    DeploymentRefused,
    EnvironmentManifest,
    VerifiedDeployment,
    resolve_deployment,
)
from polycodebench_core.image_platform import (
    ImageArchitecture,
    architecture_variant,
    host_image_architecture,
)
from polycodebench_core.model_contracts import Strict
from polycodebench_core.models import Digest, Slug
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.models import (
    artifact,
    capacity_slot,
    config_document,
    worker_registration,
)
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_plugins_api import PluginAllowlist, load_allowlist
from polycodebench_runner.provider import (
    AwsWorkerIdentityVerifier,
    Ec2VmSandboxProvider,
    GuestControlChannel,
    LocalDockerSandboxProvider,
    SandboxProvider,
)
from pydantic import field_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine

from polycodebench_orchestration.grading.assignment import DatabaseEvaluationAssignmentLoader
from polycodebench_orchestration.grading.executor import EvaluationStageExecutor
from polycodebench_orchestration.worker import WorkerService


class GradingWorkerResourceSpec(Strict):
    """Slot capacity identity for a worker whose guests use frozen plan resources."""

    schema_version: Literal[1]
    kind: Literal["resource_spec"]
    resource_class: Slug
    lane: Literal["grading"]
    max_parallel_evaluations: Literal[1] = 1
    artifact_domain: Literal["evaluation-evidence"] = "evaluation-evidence"
    plugin_allowlist_digest: Digest
    image_allowlist_digest: Digest | None = None

    @field_validator("plugin_allowlist_digest")
    @classmethod
    def plugin_allowlist_digest_is_sha256(cls, value: str) -> str:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("plugin allowlist digest must be a pinned SHA-256")
        return value

    @field_validator("image_allowlist_digest")
    @classmethod
    def image_allowlist_digest_is_sha256(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("language image allowlist digest must be a pinned SHA-256")
        return value


def load_grading_image_allowlist(
    directory: Path, *, architecture: ImageArchitecture | None = None
) -> dict[str, str]:
    """Read the image identities for every installed language without a language registry.

    ``<language>-v1.json`` holds the linux/amd64 pins. On an arm64 host only languages that ship a
    ``<language>-v1-arm64.json`` sibling are offered: an amd64 digest cannot run there.
    """
    result: dict[str, str] = {}
    chosen = architecture or host_image_architecture()
    canonical = sorted(directory.glob("*-v1.json"))
    sources = [
        variant
        for variant in (architecture_variant(path, chosen) for path in canonical)
        if variant.is_file()
    ]
    if not sources:
        raise ValueError("no versioned language image identities were found")
    for path in sources:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ValueError(f"language image identity cannot be read: {path.name}") from None
        records = document.get("images") if isinstance(document, dict) else None
        if not isinstance(records, dict) or not records:
            raise ValueError(f"language image identity has no image records: {path.name}")
        for record in records.values():
            if not isinstance(record, dict):
                raise ValueError(f"language image record is invalid: {path.name}")
            reference = record.get("reference")
            digest = record.get("digest")
            if not isinstance(reference, str) or not isinstance(digest, str):
                raise ValueError(f"language image is not digest-pinned: {path.name}")
            pinned = re.fullmatch(r"[^@\s]+@(sha256:[0-9a-f]{64})", reference)
            if pinned is None or pinned.group(1) != digest:
                raise ValueError(f"language image is not digest-pinned: {path.name}")
            previous = result.setdefault(reference, digest)
            if previous != digest:
                raise ValueError(f"language image identity conflicts for {reference}")
    return result


def build_local_evaluation_worker(
    *,
    worker_id: UUID,
    scheduler_engine: Engine,
    evaluator_engine: Engine,
    artifact_engine: Engine,
    object_store: S3ArtifactStore,
    plugin_allowlist_path: Path,
    image_identity_directory: Path,
    state_dir: Path,
    resource: GradingWorkerResourceSpec,
) -> WorkerService:
    """Assemble local evaluation with the same image, input and claim guards as production."""
    images: Mapping[str, str] = load_grading_image_allowlist(image_identity_directory)
    sandbox = LocalDockerSandboxProvider(
        allowed_images=dict(images),
        state_dir=state_dir,
        provider_id=f"grading-worker-{worker_id.hex[:12]}",
    )
    return _build_evaluation_worker(
        worker_id=worker_id,
        scheduler_engine=scheduler_engine,
        evaluator_engine=evaluator_engine,
        artifact_engine=artifact_engine,
        object_store=object_store,
        plugin_allowlist_path=plugin_allowlist_path,
        resource=resource,
        images=images,
        sandbox=sandbox,
        execution_tier="development_sandbox",
    )


def build_ec2_evaluation_worker(
    *,
    worker_id: UUID,
    manifest: EnvironmentManifest,
    deployment: VerifiedDeployment,
    scheduler_engine: Engine,
    evaluator_engine: Engine,
    artifact_engine: Engine,
    object_store: S3ArtifactStore,
    plugin_allowlist_path: Path,
    image_identity_directory: Path,
    ec2_client: object,
    sts_client: object,
    control_channel: GuestControlChannel,
) -> WorkerService:
    """Assemble a production evaluator from the deployed eval-supervisor identity."""
    if (
        manifest.status != "deployed"
        or manifest.environment not in {"staging", "production"}
        or manifest.sandbox.provider != "ec2_vm"
        or manifest.isolation_tier != "production"
    ):
        raise ValueError("AWS evaluator requires a deployed production-tier manifest")
    if deployment.verified_by != "aws-sts":
        raise ValueError("AWS evaluator requires an STS-verified deployment identity")
    try:
        resolved = resolve_deployment(
            manifest,
            claimed_environment=deployment.environment,
            claimed_role=deployment.role,
            caller=CallerPrincipal(
                account_id=manifest.identity.account_id or "",
                arn=deployment.principal,
                verified_by="aws-sts",
            ),
        )
    except DeploymentRefused:
        raise ValueError("evaluator deployment identity does not match its manifest") from None
    if resolved.role != "eval-supervisor":
        raise ValueError("AWS evaluator requires the eval-supervisor role")
    images = load_grading_image_allowlist(image_identity_directory)
    sandbox_config = manifest.sandbox
    try:
        lanes = ("solve", "grading", "admission")
        launch_templates = {lane: sandbox_config.launch_templates[lane] for lane in lanes}
        launch_template_versions = {
            lane: int(sandbox_config.launch_template_versions[lane]) for lane in lanes
        }
        subnets = {lane: sandbox_config.lane_subnets[lane] for lane in lanes}
        security_groups = {lane: sandbox_config.lane_security_groups[lane] for lane in lanes}
        supervisor_arn = manifest.identity.service_roles["eval-supervisor"]
        if sandbox_config.approved_vm_image is None or sandbox_config.guest_instance_type is None:
            raise KeyError("guest instance settings")
        if sandbox_config.control_security_group_id is None:
            raise KeyError("control security group")
    except (KeyError, ValueError):
        raise ValueError("AWS evaluator manifest is missing sandbox deployment inputs") from None

    sandbox = Ec2VmSandboxProvider(
        ec2_client=ec2_client,
        control_channel=control_channel,
        worker_identity_verified=AwsWorkerIdentityVerifier(
            sts_client=sts_client,
            expected_supervisor_arn=supervisor_arn,
        ),
        approved_ami_id=sandbox_config.approved_vm_image,
        launch_template_by_lane=launch_templates,
        launch_template_version_by_lane=launch_template_versions,
        environment=manifest.environment,
        supervisor_role="eval-supervisor",
        control_security_group_id=sandbox_config.control_security_group_id,
        subnet_by_lane=subnets,
        security_group_by_lane=security_groups,
        instance_type=sandbox_config.guest_instance_type,
        candidate_image_digests=images,
    )
    resource = load_grading_resource_for_worker(
        scheduler_engine,
        worker_id,
        expected_driver_identity="Ec2VmSandboxProvider",
    )
    return _build_evaluation_worker(
        worker_id=worker_id,
        scheduler_engine=scheduler_engine,
        evaluator_engine=evaluator_engine,
        artifact_engine=artifact_engine,
        object_store=object_store,
        plugin_allowlist_path=plugin_allowlist_path,
        resource=resource,
        images=images,
        sandbox=sandbox,
        execution_tier="production_worker",
    )


def _build_evaluation_worker(
    *,
    worker_id: UUID,
    scheduler_engine: Engine,
    evaluator_engine: Engine,
    artifact_engine: Engine,
    object_store: S3ArtifactStore,
    plugin_allowlist_path: Path,
    resource: GradingWorkerResourceSpec,
    images: Mapping[str, str],
    sandbox: SandboxProvider,
    execution_tier: Literal["development_sandbox", "production_worker"],
) -> WorkerService:
    plugins: PluginAllowlist = load_allowlist(plugin_allowlist_path)
    if canonical_document_digest(plugins) != resource.plugin_allowlist_digest:
        raise ValueError("grading worker plugin allowlist differs from its registered resource")
    image_digest = canonical_digest(dict(images))
    if resource.image_allowlist_digest is None:
        if execution_tier == "production_worker":
            raise ValueError(
                "production evaluator resource does not bind language image identities"
            )
    elif image_digest != resource.image_allowlist_digest:
        raise ValueError("grading worker language images differ from its registered resource")
    artifacts = ArtifactRepository(artifact_engine, object_store, max_upload_bytes=512 * 1024**2)
    jobs = PostgresJobRepository(scheduler_engine)
    assignment_loader = DatabaseEvaluationAssignmentLoader(
        evaluator_engine, artifacts, plugins
    )
    executor = EvaluationStageExecutor(
        load_assignment=assignment_loader,
        plugin_allowlist=plugins,
        execution_tier=execution_tier,
        artifact_owner=f"grading-worker-{worker_id}",
    )
    return WorkerService(
        worker_id=worker_id,
        repository=jobs,
        artifacts=artifacts,
        sandbox=sandbox,
        spec_factory=None,
        executor=executor,
        executor_manages_sandbox=True,
    )


def load_grading_resource_for_worker(
    engine: Engine,
    worker_id: UUID,
    *,
    expected_driver_identity: str | None = None,
) -> GradingWorkerResourceSpec:
    """Resolve an active worker's capacity only from its verified registered config."""
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(
                    worker_registration.c.status.label("worker_status"),
                    worker_registration.c.lane,
                    worker_registration.c.hardware_class,
                    worker_registration.c.driver_identity,
                    capacity_slot.c.resource_class.label("slot_resource_class"),
                    config_document.c.document,
                    config_document.c.digest,
                    artifact.c.status.label("artifact_status"),
                    artifact.c.visibility,
                    artifact.c.encryption_domain,
                    artifact.c.content_digest,
                )
                .select_from(
                    worker_registration.join(
                        capacity_slot, capacity_slot.c.worker_id == worker_registration.c.id
                    )
                    .join(
                        config_document,
                        config_document.c.id == capacity_slot.c.resource_spec_config_id,
                    )
                    .join(
                        artifact,
                        artifact.c.id == config_document.c.canonical_artifact_id,
                    )
                )
                .where(worker_registration.c.id == worker_id)
            )
            .mappings()
            .all()
        )
    if not rows:
        raise ValueError("grading worker has no registered capacity slot")
    if any(
        row["worker_status"] != "active"
        or row["lane"] != "grading"
        or (
            expected_driver_identity is not None
            and (
                row["driver_identity"] != expected_driver_identity
                or row["hardware_class"] != "aws-ec2-disposable-vm-v1"
            )
        )
        or row["artifact_status"] != "verified"
        or row["visibility"] != "internal"
        or row["encryption_domain"] != "worker-config"
        or row["content_digest"] != row["digest"]
        for row in rows
    ):
        raise ValueError("grading worker registration does not bind verified configuration")
    try:
        resources = [
            GradingWorkerResourceSpec.model_validate(row["document"], strict=True)
            for row in rows
        ]
    except ValueError:
        raise ValueError("grading worker resource configuration is invalid") from None
    digests = {str(row["digest"]) for row in rows}
    resource_classes = {str(row["slot_resource_class"]) for row in rows}
    resource_document = resources[0].model_dump(mode="json")
    if (
        len(digests) != 1
        or len(resource_classes) != 1
        or any(resource.resource_class not in resource_classes for resource in resources)
        or any(
            _grading_resource_digest(resource) != row["digest"]
            for row, resource in zip(rows, resources, strict=True)
        )
        or any(resource.model_dump(mode="json") != resource_document for resource in resources)
    ):
        raise ValueError("grading worker capacity slots do not share one frozen resource plan")
    return resources[0]


def _grading_resource_digest(resource: GradingWorkerResourceSpec) -> str:
    """Digest exactly the fields present in a registered resource document.

    Excluding unset fields preserves the canonical digest of schema-v1 resource documents written
    before the optional image allowlist digest was added.
    """
    document = resource.model_dump(mode="json", exclude_unset=True)
    kind = document.pop("kind")
    schema_version = document.pop("schema_version")
    return canonical_digest(canonical_envelope(kind, document, schema_version))


__all__ = [
    "GradingWorkerResourceSpec",
    "build_ec2_evaluation_worker",
    "build_local_evaluation_worker",
    "load_grading_image_allowlist",
    "load_grading_resource_for_worker",
]
