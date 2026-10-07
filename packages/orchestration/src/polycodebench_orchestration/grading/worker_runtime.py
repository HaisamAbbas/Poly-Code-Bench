"""Opt-in local assembly for the independent evaluator worker lane."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import UUID

from polycodebench_core.canonical import canonical_document_digest
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
from polycodebench_runner.provider import LocalDockerSandboxProvider
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

    @field_validator("plugin_allowlist_digest")
    @classmethod
    def plugin_allowlist_digest_is_sha256(cls, value: str) -> str:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("plugin allowlist digest must be a pinned SHA-256")
        return value


def load_grading_image_allowlist(directory: Path) -> dict[str, str]:
    """Read the image identities for every installed language without a language registry."""
    result: dict[str, str] = {}
    sources = sorted(directory.glob("*-v1.json"))
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
    plugins: PluginAllowlist = load_allowlist(plugin_allowlist_path)
    if canonical_document_digest(plugins) != resource.plugin_allowlist_digest:
        raise ValueError("grading worker plugin allowlist differs from its registered resource")
    images: Mapping[str, str] = load_grading_image_allowlist(image_identity_directory)
    artifacts = ArtifactRepository(artifact_engine, object_store, max_upload_bytes=512 * 1024**2)
    jobs = PostgresJobRepository(scheduler_engine)
    assignment_loader = DatabaseEvaluationAssignmentLoader(
        evaluator_engine, artifacts, plugins
    )
    executor = EvaluationStageExecutor(
        load_assignment=assignment_loader,
        plugin_allowlist=plugins,
        execution_tier="development_sandbox",
        artifact_owner=f"grading-worker-{worker_id}",
    )
    sandbox = LocalDockerSandboxProvider(
        allowed_images=dict(images),
        state_dir=state_dir,
        provider_id=f"grading-worker-{worker_id.hex[:12]}",
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


def load_grading_resource_for_worker(engine: Engine, worker_id: UUID) -> GradingWorkerResourceSpec:
    """Resolve an active worker's capacity only from its verified registered config."""
    with engine.connect() as connection:
        rows = (
            connection.execute(
                select(
                    worker_registration.c.status.label("worker_status"),
                    worker_registration.c.lane,
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
        or canonical_document_digest(resources[0]) != next(iter(digests))
        or any(resource.model_dump(mode="json") != resource_document for resource in resources)
    ):
        raise ValueError("grading worker capacity slots do not share one frozen resource plan")
    return resources[0]


__all__ = [
    "GradingWorkerResourceSpec",
    "build_local_evaluation_worker",
    "load_grading_image_allowlist",
    "load_grading_resource_for_worker",
]
