"""Strict queue, dependency, worker, and execution outcome contracts."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = str


class JobDependencySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    parent_key: str = Field(min_length=1, max_length=128)
    condition: Literal["success", "gate_pass", "gate_fail", "terminal"] = "success"
    accepted_skip_reasons: tuple[str, ...] = ()

    @field_validator("parent_key")
    @classmethod
    def parent_key_is_ascii(cls, value: str) -> str:
        if not value.isascii() or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError("dependency keys must be safe ASCII identifiers")
        return value

    @field_validator("accepted_skip_reasons")
    @classmethod
    def skip_reasons_are_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) for value in values
        ):
            raise ValueError("accepted skip reasons must be unique stable codes")
        return values

    @model_validator(mode="after")
    def terminal_requires_explicit_skips(self) -> JobDependencySpec:
        if self.condition == "terminal" and not self.accepted_skip_reasons:
            raise ValueError(
                "terminal dependencies must name branch-specific accepted skip reasons"
            )
        if self.condition != "terminal" and self.accepted_skip_reasons:
            raise ValueError("accepted skip reasons are only valid for terminal dependencies")
        return self


class JobDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    key: str = Field(min_length=1, max_length=128)
    stage: str = Field(min_length=1, max_length=48)
    shard_key: str = Field(default="", max_length=255)
    input_digest: str
    input_artifact_id: UUID | None = None
    queue_class: str = Field(min_length=1, max_length=64)
    resource_class: str = Field(default="default", min_length=1, max_length=64)
    priority: int = Field(default=0, ge=-(2**31), le=2**31 - 1)
    required: bool = True
    max_deliveries: int = Field(default=3, ge=1, le=10)
    provider_key: str = Field(default="system", min_length=1, max_length=128)
    dependencies: tuple[JobDependencySpec, ...] = ()

    @field_validator("key")
    @classmethod
    def key_is_safe(cls, value: str) -> str:
        if not value.isascii() or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError("job keys must be safe ASCII identifiers")
        return value

    @field_validator("stage")
    @classmethod
    def stage_is_safe(cls, value: str) -> str:
        if not value.isascii() or not re.fullmatch(r"[a-z][a-z0-9_:-]{0,47}", value):
            raise ValueError("stage names must be safe ASCII identifiers")
        return value

    @field_validator("input_digest")
    @classmethod
    def digest_is_valid(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("job input digest must be lowercase SHA-256")
        return value

    @field_validator("shard_key", "queue_class", "resource_class", "provider_key")
    @classmethod
    def labels_are_bounded_ascii(cls, value: str) -> str:
        if not value.isascii() or "\x00" in value:
            raise ValueError("job labels must be ASCII without NUL")
        return value

    @model_validator(mode="after")
    def dependencies_are_unique_and_not_self(self) -> JobDefinition:
        keys = [dependency.parent_key for dependency in self.dependencies]
        if self.key in keys or len(keys) != len(set(keys)):
            raise ValueError("job dependencies must be unique and cannot be self-referential")
        return self


class CapacitySlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    slot_key: str = Field(min_length=1, max_length=128)
    resource_class: str = Field(min_length=1, max_length=64)
    resource_spec_config_id: UUID


class WorkerRegistrationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    workload_identity: str = Field(min_length=1, max_length=255)
    lane: Literal["solve", "grading", "admission"]
    hardware_class: str = Field(min_length=1, max_length=128)
    driver_identity: str = Field(min_length=1, max_length=255)
    audit_capable: bool = False
    allowed_queue_classes: tuple[str, ...] = Field(min_length=1)
    allowed_resource_classes: tuple[str, ...] = Field(min_length=1)
    resource_spec_config_id: UUID
    slots: tuple[CapacitySlotSpec, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_capabilities_and_slots(self) -> WorkerRegistrationSpec:
        if len(set(self.allowed_queue_classes)) != len(self.allowed_queue_classes):
            raise ValueError("queue classes must be unique")
        if len(set(self.allowed_resource_classes)) != len(self.allowed_resource_classes):
            raise ValueError("resource classes must be unique")
        if len({slot.slot_key for slot in self.slots}) != len(self.slots):
            raise ValueError("worker slot keys must be unique")
        if any(slot.resource_class not in self.allowed_resource_classes for slot in self.slots):
            raise ValueError("slot resource classes must be authorized by the worker registration")
        if any(not value.isascii() for value in (self.workload_identity, self.driver_identity)):
            raise ValueError("worker identities must be ASCII")
        return self


class JobClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    job_id: UUID
    execution_id: UUID
    slot_id: UUID
    worker_id: str
    slot_key: str
    stage: str
    scope_type: Literal[
        "attempt", "evaluation", "release", "curation_round", "discovery_search", "audit_run"
    ]
    scope_id: UUID
    input_artifact_id: UUID | None
    input_digest: str
    resource_class: str
    queue_class: str
    fence: int = Field(ge=1, le=2**63 - 1)
    deliveries: int = Field(ge=1, le=10)
    lease_until_epoch: int
    guest_id: str | None = None


class StageOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    quality_gate: Literal["pass", "fail", "unknown", "not_applicable"] | None = None
    model_failure: bool = False
    failure_class: str | None = None
    event_details: dict[str, str | int | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def model_failure_is_not_an_infrastructure_failure(self) -> StageOutcome:
        if self.model_failure and self.failure_class is not None:
            raise ValueError(
                "candidate/model failure is completed stage output, not delivery failure"
            )
        if self.model_failure and self.quality_gate not in {"fail", "unknown"}:
            raise ValueError("model failure must produce a failed or unknown quality gate")
        if not self.model_failure and self.failure_class is not None:
            raise ValueError("failure_class is reserved for failed infrastructure deliveries")
        return self
