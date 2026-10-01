"""Typed, strict sandbox lifecycle contracts shared by providers."""

from __future__ import annotations

import re
from typing import Literal
from uuid import uuid4

from polycodebench_core.models import Digest, RelativePath, Slug
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Lane = Literal["solve", "grading", "admission"]
NetworkPolicy = Literal["none"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SandboxSpec(StrictModel):
    schema_version: Literal[1] = 1
    stage_id: Slug
    fence: int = Field(ge=1, le=2**63 - 1)
    lane: Lane
    image: str = Field(min_length=1, max_length=256)
    image_digest: Digest
    cpu_millis: int = Field(ge=100, le=64_000)
    memory_bytes: int = Field(ge=64 * 1024 * 1024, le=256 * 1024**3)
    disk_bytes: int = Field(ge=16 * 1024**2, le=1024**4)
    pids_limit: int = Field(ge=1, le=4096)
    timeout_seconds: int = Field(ge=1, le=86_400)
    ttl_seconds: int = Field(ge=1, le=7 * 24 * 3600)
    network: NetworkPolicy = "none"
    # Compiled languages must execute the binaries they build in the workspace. Interpreted
    # guests keep the default, a workspace that cannot execute anything it receives.
    executable_workspace: bool = False
    isolation_tier: Literal["development"] = "development"

    @model_validator(mode="after")
    def image_is_digest_pinned(self) -> SandboxSpec:
        if "@sha256:" not in self.image or not self.image.endswith(self.image_digest[7:]):
            raise ValueError("sandbox image must match its pinned sha256 digest")
        return self


class SandboxHandle(StrictModel):
    schema_version: Literal[1] = 1
    sandbox_id: str = Field(default_factory=lambda: str(uuid4()))
    stage_id: Slug
    fence: int = Field(ge=1)
    lane: Lane
    driver: Literal["local_docker", "ec2_vm"]
    resource_id: str = Field(min_length=1, max_length=256)
    expires_at_epoch: int = Field(ge=0)
    max_execution_seconds: int = Field(ge=1, le=86_400)
    isolation_tier: Literal["development", "production"]

    @model_validator(mode="after")
    def tier_matches_provider(self) -> SandboxHandle:
        if (self.driver, self.isolation_tier) not in {
            ("local_docker", "development"),
            ("ec2_vm", "production"),
        }:
            raise ValueError(
                "sandbox isolation tier cannot be requested independently of its driver"
            )
        return self

    @field_validator("sandbox_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        from uuid import UUID

        if str(UUID(value)) != value:
            raise ValueError("sandbox_id must be a canonical UUID")
        return value


class InputManifest(StrictModel):
    schema_version: Literal[1] = 1
    files: dict[RelativePath, bytes]
    max_total_bytes: int = Field(ge=0, le=2**31)

    @model_validator(mode="after")
    def bounded(self) -> InputManifest:
        if sum(map(len, self.files.values())) > self.max_total_bytes:
            raise ValueError("input manifest exceeds its declared byte bound")
        return self


class ExecRequest(StrictModel):
    schema_version: Literal[1] = 1
    argv: tuple[str, ...] = Field(min_length=1, max_length=256)
    timeout_seconds: int = Field(ge=1, le=86_400)
    environment: dict[str, str] = Field(default_factory=dict)
    max_output_bytes: int = Field(default=1_048_576, ge=0, le=8 * 1024**2)

    @model_validator(mode="after")
    def safe_environment(self) -> ExecRequest:
        if (
            sum(len(key.encode()) + len(value.encode()) for key, value in self.environment.items())
            > 16_384
        ):
            raise ValueError("sandbox environment exceeds its byte bound")
        for key, value in self.environment.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key) or "\x00" in value:
                raise ValueError("invalid sandbox environment entry")
            if key in {"PATH", "HOME"} or key.startswith(("DOCKER_", "AWS_", "PCB_", "SSH_")):
                raise ValueError("sandbox environment cannot override protected variables")
        if any("\x00" in arg for arg in self.argv):
            raise ValueError("argv cannot contain NUL")
        if sum(len(arg.encode()) for arg in self.argv) > 65_536:
            raise ValueError("candidate argument vector exceeds its byte bound")
        return self


class ExecResult(StrictModel):
    schema_version: Literal[1] = 1
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_ms: int = Field(ge=0)
    isolation_tier: Literal["development", "production"]
    sandbox_id: str


class WorkspaceFile(StrictModel):
    path: RelativePath
    size_bytes: int = Field(ge=0)
    digest: Digest


class WorkspaceManifest(StrictModel):
    schema_version: Literal[1] = 1
    sandbox_id: str
    captured_at_epoch: int = Field(ge=0)
    files: tuple[WorkspaceFile, ...]
    total_bytes: int = Field(ge=0)
    archive_digest: Digest
    archive_bytes: bytes
    isolation_tier: Literal["development", "production"]

    @model_validator(mode="after")
    def archive_matches_manifest(self) -> WorkspaceManifest:
        import hashlib

        if len(self.archive_bytes) > 512 * 1024**2:
            raise ValueError("workspace archive exceeds its byte limit")
        if f"sha256:{hashlib.sha256(self.archive_bytes).hexdigest()}" != self.archive_digest:
            raise ValueError("workspace archive digest does not match its bytes")
        paths = [item.path for item in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("workspace file manifest must be sorted and unique")
        if sum(item.size_bytes for item in self.files) != self.total_bytes:
            raise ValueError("workspace manifest total does not match its files")
        return self


class IsolationAttestation(StrictModel):
    schema_version: Literal[1] = 1
    sandbox_id: str
    driver: Literal["local_docker", "ec2_vm"]
    isolation_tier: Literal["development", "production"]
    policy_digest: Digest
    controls: tuple[str, ...]
    verified_by: Literal["local_driver", "trusted_worker_identity"]

    @model_validator(mode="after")
    def trusted_tier_identity(self) -> IsolationAttestation:
        if self.driver == "local_docker" and (
            self.isolation_tier != "development" or self.verified_by != "local_driver"
        ):
            raise ValueError("local Docker can attest development isolation only")
        if self.driver == "ec2_vm" and (
            self.isolation_tier != "production" or self.verified_by != "trusted_worker_identity"
        ):
            raise ValueError("production VM attestation requires trusted worker identity")
        return self
