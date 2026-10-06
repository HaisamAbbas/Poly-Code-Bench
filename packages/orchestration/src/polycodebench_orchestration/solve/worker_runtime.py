"""Production-shaped assembly for a bounded local solve worker.

The runtime intentionally receives separate database engines for scheduling, solve state,
gateway accounting, and artifact finalization. Local Docker remains development-tier only; a
production worker must be assembled with the production guest provider by its deployment target.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import UUID

from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.jobs import JobClaim
from polycodebench_core.model_contracts import ProviderKind
from polycodebench_core.model_contracts import Strict as ContractStrict
from polycodebench_core.models import Digest, Slug, TaskRuntime
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.endpoints import PostgresEndpointRepository
from polycodebench_persistence.jobs import PostgresJobRepository
from polycodebench_persistence.model_ledger import PostgresModelLedger
from polycodebench_persistence.models import (
    artifact,
    attempt,
    capacity_slot,
    config_document,
    task_version,
)
from polycodebench_persistence.object_store import S3ArtifactStore
from polycodebench_persistence.solve_state import PostgresSolveRepository
from polycodebench_runner.contracts import SandboxSpec
from polycodebench_runner.provider import LocalDockerSandboxProvider
from polycodebench_services.solve_budget_profiles import load_solve_budget_profiles
from polycodebench_services.solve_protocols import load_protocol_directory
from pydantic import Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine

from polycodebench_orchestration.gateway.adapters.anthropic import AnthropicAdapter
from polycodebench_orchestration.gateway.adapters.base import BaseAdapter
from polycodebench_orchestration.gateway.adapters.google import GoogleAdapter
from polycodebench_orchestration.gateway.adapters.local import LocalEndpointAdapter
from polycodebench_orchestration.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from polycodebench_orchestration.gateway.secrets import EnvironmentSecretResolver
from polycodebench_orchestration.gateway.service import ModelGateway
from polycodebench_orchestration.gateway.store import ArtifactResponseStore
from polycodebench_orchestration.gateway.throttle import ThrottleRegistry
from polycodebench_orchestration.gateway.transport import PinnedHttpTransport
from polycodebench_orchestration.solve.executor import SolveStageExecutor
from polycodebench_orchestration.solve.loader import DatabaseAssignmentLoader
from polycodebench_orchestration.worker import WorkerService

ADAPTERS: dict[ProviderKind, BaseAdapter] = {
    ProviderKind.OPENAI_COMPATIBLE: OpenAICompatibleAdapter(),
    ProviderKind.ANTHROPIC: AnthropicAdapter(),
    ProviderKind.GOOGLE: GoogleAdapter(),
    ProviderKind.LOCAL: LocalEndpointAdapter(),
}


class SolveWorkerResourceSpec(ContractStrict):
    """Immutable resource limits pinned to a worker capacity slot."""

    schema_version: Literal[1]
    kind: Literal["resource_spec"]
    resource_class: Slug
    lane: Literal["solve"]
    image: str = Field(min_length=1, max_length=256)
    image_digest: Digest
    cpu_millis: int = Field(ge=100, le=64_000)
    memory_bytes: int = Field(ge=64 * 1024**2, le=256 * 1024**3)
    disk_bytes: int = Field(ge=16 * 1024**2, le=512 * 1024**2)
    pids_limit: int = Field(ge=1, le=4096)
    timeout_seconds: int = Field(ge=1, le=86_400)
    ttl_seconds: int = Field(ge=1, le=7 * 24 * 3600)
    executable_workspace: bool = False

    @field_validator("image")
    @classmethod
    def image_is_pinned(cls, value: str) -> str:
        if re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("worker resource image must be digest-pinned")
        return value

    @model_validator(mode="after")
    def digest_matches_reference(self) -> SolveWorkerResourceSpec:
        if not self.image.endswith(self.image_digest.removeprefix("sha256:")):
            raise ValueError("worker image reference and digest differ")
        return self


class DatabaseSandboxSpecFactory:
    """Resolve task image identity and slot limits before a guest can be created."""

    def __init__(
        self,
        *,
        scheduler_engine: Engine,
        solve_engine: Engine,
        allowed_images: Mapping[str, str],
    ) -> None:
        if not allowed_images:
            raise ValueError("the local worker needs an immutable image allowlist")
        self._scheduler_engine = scheduler_engine
        self._solve_engine = solve_engine
        self._allowed_images = dict(allowed_images)

    def __call__(self, claim: JobClaim) -> SandboxSpec:
        if claim.stage != "solve" or claim.scope_type != "attempt":
            raise ValueError("solve worker received a non-solve claim")
        with self._scheduler_engine.connect() as connection:
            slot = (
                connection.execute(
                    select(
                        capacity_slot.c.resource_class,
                        config_document.c.kind,
                        config_document.c.document,
                        config_document.c.digest,
                        artifact.c.status,
                        artifact.c.visibility,
                        artifact.c.encryption_domain,
                        artifact.c.content_digest,
                    )
                    .select_from(
                        capacity_slot.join(
                            config_document,
                            capacity_slot.c.resource_spec_config_id == config_document.c.id,
                        ).join(
                            artifact,
                            config_document.c.canonical_artifact_id == artifact.c.id,
                        )
                    )
                    .where(
                        capacity_slot.c.id == claim.slot_id,
                        capacity_slot.c.worker_id == UUID(claim.worker_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if (
            slot is None
            or slot["kind"] != "resource_spec"
            or slot["status"] != "verified"
            or slot["visibility"] != "internal"
            or slot["encryption_domain"] != "worker-config"
        ):
            raise ValueError("worker slot has no verified resource specification")
        try:
            resource = SolveWorkerResourceSpec.model_validate(slot["document"], strict=True)
        except ValidationError:
            raise ValueError("worker resource specification is invalid") from None
        if (
            slot["digest"] != canonical_document_digest(resource)
            or slot["content_digest"] != slot["digest"]
        ):
            raise ValueError("worker resource config digest does not match its verified artifact")
        if (
            resource.resource_class != claim.resource_class
            or slot["resource_class"] != claim.resource_class
        ):
            raise ValueError("worker slot resource class does not match the solve job")

        with self._solve_engine.connect() as connection:
            runtime_document = connection.execute(
                select(task_version.c.document["runtime"])
                .select_from(
                    attempt.join(task_version, task_version.c.id == attempt.c.task_version_id)
                )
                .where(attempt.c.id == claim.scope_id)
            ).scalar_one_or_none()
        if not isinstance(runtime_document, Mapping):
            raise ValueError("attempt has no frozen task runtime")
        try:
            runtime = TaskRuntime.model_validate(runtime_document, strict=False)
        except ValidationError:
            raise ValueError("frozen task runtime is invalid") from None
        if runtime.resource_class != claim.resource_class:
            raise ValueError("task and job resource classes differ")
        if resource.image_digest != runtime.image_digest:
            raise ValueError("worker image digest does not match the frozen task runtime")
        if self._allowed_images.get(resource.image) != runtime.image_digest:
            raise ValueError("task image is not approved by the local worker allowlist")

        return SandboxSpec(
            stage_id=claim.stage,
            fence=claim.fence,
            lane="solve",
            image=resource.image,
            image_digest=resource.image_digest,
            cpu_millis=resource.cpu_millis,
            memory_bytes=resource.memory_bytes,
            disk_bytes=resource.disk_bytes,
            pids_limit=resource.pids_limit,
            timeout_seconds=resource.timeout_seconds,
            ttl_seconds=resource.ttl_seconds,
            executable_workspace=resource.executable_workspace,
        )


def load_image_allowlist(path: Path) -> dict[str, str]:
    """Read an explicit local image allowlist without permitting floating image tags."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("worker image allowlist cannot be read") from None
    if not isinstance(document, dict) or not document:
        raise ValueError("worker image allowlist must be a non-empty JSON object")
    result: dict[str, str] = {}
    for image, digest in document.items():
        reference = re.fullmatch(r"[^@\s]+@(sha256:[0-9a-f]{64})", image)
        if (
            not isinstance(image, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or reference is None
            or reference.group(1) != digest
        ):
            raise ValueError("worker image allowlist contains an invalid pinned image")
        result[image] = digest
    return result


def build_local_solve_worker(
    *,
    worker_id: UUID,
    scheduler_engine: Engine,
    solve_engine: Engine,
    gateway_engine: Engine,
    artifact_engine: Engine,
    object_store: S3ArtifactStore,
    image_allowlist: Mapping[str, str],
    state_dir: Path,
    protocol_directory: Path,
    budget_profile_file: Path,
    secret_namespace: str = "models",
) -> WorkerService:
    """Compose the existing guarded solve path without enabling or registering a worker."""
    artifacts = ArtifactRepository(artifact_engine, object_store, max_upload_bytes=512 * 1024**2)
    jobs = PostgresJobRepository(scheduler_engine)
    solve_repository = PostgresSolveRepository(solve_engine)
    endpoints = PostgresEndpointRepository(gateway_engine)
    gateway = ModelGateway(
        endpoints=endpoints,
        ledger=PostgresModelLedger(gateway_engine),
        store=ArtifactResponseStore(
            artifacts, owner=f"solve-worker-{worker_id}", encryption_domain="solve-session"
        ),
        transport=PinnedHttpTransport(),
        secrets=EnvironmentSecretResolver(secret_namespace),
        adapters=ADAPTERS,
        throttles=ThrottleRegistry(),
    )
    protocols = load_protocol_directory(protocol_directory)
    budget_profiles = load_solve_budget_profiles(budget_profile_file)
    assignment_loader = DatabaseAssignmentLoader(
        solve_engine, artifacts, protocols, budget_profiles
    )
    sandbox = LocalDockerSandboxProvider(
        allowed_images=dict(image_allowlist),
        state_dir=state_dir,
        provider_id=f"solve-worker-{worker_id.hex[:12]}",
    )
    executor = SolveStageExecutor(
        load_assignment=assignment_loader,
        gateway=gateway,
        jobs=jobs,
        solve_repository=solve_repository,
        store_factory=lambda _artifacts: ArtifactResponseStore(
            artifacts, owner=f"solve-worker-{worker_id}", encryption_domain="solve-session"
        ),
    )
    return WorkerService(
        worker_id=worker_id,
        repository=jobs,
        artifacts=artifacts,
        sandbox=sandbox,
        spec_factory=DatabaseSandboxSpecFactory(
            scheduler_engine=scheduler_engine,
            solve_engine=solve_engine,
            allowed_images=image_allowlist,
        ),
        executor=executor,
    )
