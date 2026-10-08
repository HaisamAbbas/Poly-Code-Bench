"""Versioned benchmark/source metadata and bounded audit planning contracts."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AuditRegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourceRevisionPin(AuditRegistryModel):
    source_url: str = Field(min_length=8, max_length=2048)
    ref: str = Field(min_length=1, max_length=255)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    purpose: str = Field(min_length=1, max_length=128)

    @field_validator("source_url")
    @classmethod
    def source_url_must_be_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source URLs must use HTTPS")
        return value


class BenchmarkCatalogEntry(AuditRegistryModel):
    slug: str = Field(min_length=1, max_length=96)
    name: str = Field(min_length=1, max_length=200)
    family: Literal["code", "knowledge", "math", "reasoning", "agent", "multimodal", "private"]
    owner: str = Field(min_length=1, max_length=200)
    official_url: str = Field(min_length=8, max_length=2048)
    version: str | None = Field(max_length=160)
    version_state: Literal[
        "dataset_version_pinned",
        "repository_snapshot_pinned",
        "metadata_only",
        "unresolved",
    ]
    split_policy: str = Field(min_length=1, max_length=500)
    split_state: Literal["pinned", "partially_documented", "unresolved"]
    component_schema: tuple[str, ...] = Field(min_length=1, max_length=16)
    upstream_lineage: tuple[str, ...] = Field(max_length=16)
    access_state: Literal[
        "public_metadata",
        "public_data_review_required",
        "gated",
        "owner_supplied",
    ]
    rights_state: Literal[
        "needs_item_review",
        "license_review_required",
        "gated_access_required",
        "owner_contract_required",
        "approved_for_declared_scope",
    ]
    license_evidence_url: str | None = Field(max_length=2048)
    modalities: tuple[
        Literal["text", "code", "repository", "agent_environment", "image", "tool_call"], ...
    ] = Field(min_length=1, max_length=8)
    audit_status: Literal[
        "catalogued", "metadata_only", "importable", "audit_conformant", "blocked", "retired"
    ]
    importer_status: Literal[
        "not_implemented", "metadata_only", "importable", "audit_conformant", "blocked"
    ]
    source_pins: tuple[SourceRevisionPin, ...] = Field(max_length=8)
    audit_owner: str = Field(min_length=1, max_length=128)
    native_evaluation_unchanged: Literal[True]
    notes: str = Field(min_length=1, max_length=1200)

    @field_validator("slug")
    @classmethod
    def slug_is_stable_ascii(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("benchmark slugs must be lowercase ASCII words separated by hyphens")
        return value

    @field_validator("official_url", "license_evidence_url")
    @classmethod
    def official_urls_must_be_https(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("catalog URLs must use HTTPS")
        return value


class BenchmarkRegistry(AuditRegistryModel):
    schema_version: Literal[1]
    catalog_version: str = Field(min_length=1, max_length=32)
    observed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    benchmarks: tuple[BenchmarkCatalogEntry, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def slugs_are_unique(self) -> BenchmarkRegistry:
        slugs = [entry.slug for entry in self.benchmarks]
        if len(slugs) != len(set(slugs)):
            raise ValueError("benchmark slugs must be unique")
        for entry in self.benchmarks:
            if entry.version_state == "dataset_version_pinned" and entry.version is None:
                raise ValueError("a pinned dataset version requires a version label")
            if entry.version_state == "repository_snapshot_pinned" and not entry.source_pins:
                raise ValueError("a pinned repository snapshot requires a commit reference")
        return self


class SourceGroupPolicy(AuditRegistryModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    official_url: str = Field(min_length=8, max_length=2048)
    access_state: Literal[
        "public_metadata_only", "public_data_review_required", "gated", "owner_supplied"
    ]
    authorization_state: Literal["not_approved", "approved_scoped", "gated"]
    connector_state: Literal[
        "not_implemented", "metadata_only", "importable", "audit_conformant", "blocked"
    ]
    conformance_state: Literal["not_run", "fixture_only", "live_verified", "blocked"]
    scope: str = Field(min_length=1, max_length=1000)
    allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=32)
    retention: str = Field(min_length=1, max_length=800)
    private_query_policy: Literal["local_only", "explicit_disclosure_required", "never"]
    extraction_policy: str = Field(min_length=1, max_length=500)
    max_requests_per_plan: int = Field(ge=1, le=100_000)
    max_response_bytes: int = Field(ge=1024, le=1_073_741_824)

    @field_validator("slug")
    @classmethod
    def source_slug_is_stable_ascii(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError(
                "source group slugs must be lowercase ASCII words separated by hyphens"
            )
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def allowed_hosts_are_hostnames(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        pattern = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$")
        if any(not pattern.fullmatch(host) for host in value):
            raise ValueError("allowed_hosts must contain DNS hostnames without paths or ports")
        if len(value) != len(set(value)):
            raise ValueError("allowed_hosts must be unique")
        return value

    @field_validator("official_url")
    @classmethod
    def official_url_must_be_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source policy URLs must use HTTPS")
        return value


class SourcePolicyRegistry(AuditRegistryModel):
    schema_version: Literal[1]
    policy_version: str = Field(min_length=1, max_length=32)
    observed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    groups: tuple[SourceGroupPolicy, ...] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def source_slugs_are_unique(self) -> SourcePolicyRegistry:
        slugs = [entry.slug for entry in self.groups]
        if len(slugs) != len(set(slugs)):
            raise ValueError("source group slugs must be unique")
        return self


class BenchmarkCapability(AuditRegistryModel):
    benchmark_slug: str = Field(min_length=1, max_length=96)
    importer_state: Literal[
        "not_implemented", "metadata_only", "importable", "audit_conformant", "blocked"
    ]
    component_scope: tuple[str, ...] = Field(min_length=1, max_length=16)
    supported_components: tuple[str, ...] = Field(max_length=16)
    source_groups: tuple[str, ...] = Field(min_length=1, max_length=8)
    required_modalities: tuple[str, ...] = Field(min_length=1, max_length=8)
    supported_modalities: tuple[str, ...] = Field(max_length=8)
    runtime_state: Literal["not_required_for_metadata", "not_implemented", "blocked", "conformant"]
    conformance_state: Literal["not_run", "fixture_only", "live_verified", "blocked"]
    original_evaluation_owner: str = Field(min_length=1, max_length=128)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=16)


class CapabilityMatrix(AuditRegistryModel):
    schema_version: Literal[1]
    matrix_version: str = Field(min_length=1, max_length=32)
    observed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    benchmarks: tuple[BenchmarkCapability, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def capability_slugs_are_unique(self) -> CapabilityMatrix:
        slugs = [entry.benchmark_slug for entry in self.benchmarks]
        if len(slugs) != len(set(slugs)):
            raise ValueError("each benchmark must have one capability row")
        if any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) for slug in slugs):
            raise ValueError("capability benchmark slugs must be stable lowercase ASCII")
        return self


class ResourceLimits(AuditRegistryModel):
    schema_version: Literal[1]
    policy_version: str = Field(min_length=1, max_length=32)
    max_tasks_per_plan: int = Field(ge=1, le=100_000)
    max_source_groups_per_plan: int = Field(ge=1, le=8)
    max_candidates_per_source_per_task: int = Field(ge=1, le=20)
    max_candidates_per_task: int = Field(ge=1, le=100)
    max_stages_per_task: int = Field(ge=1, le=5)
    max_query_units_per_plan: int = Field(ge=1, le=1_000_000)
    max_storage_bytes_per_plan: int = Field(ge=1, le=1_099_511_627_776)


class AuditCatalogBundle(AuditRegistryModel):
    registry: BenchmarkRegistry
    source_policies: SourcePolicyRegistry
    capabilities: CapabilityMatrix
    limits: ResourceLimits

    @model_validator(mode="after")
    def references_are_registered(self) -> AuditCatalogBundle:
        benchmark_slugs = {entry.slug for entry in self.registry.benchmarks}
        source_slugs = {entry.slug for entry in self.source_policies.groups}
        capability_slugs = {entry.benchmark_slug for entry in self.capabilities.benchmarks}
        if capability_slugs != benchmark_slugs:
            raise ValueError(
                "capability matrix must contain exactly one row per catalogued benchmark"
            )
        benchmark_by_slug = {entry.slug: entry for entry in self.registry.benchmarks}
        for capability in self.capabilities.benchmarks:
            unknown_sources = set(capability.source_groups) - source_slugs
            if unknown_sources:
                raise ValueError(
                    f"unknown source groups in capability row: {sorted(unknown_sources)}"
                )
            if set(capability.source_groups) != source_slugs:
                raise ValueError("capability matrix must declare every registered source group")
            benchmark = benchmark_by_slug[capability.benchmark_slug]
            if set(capability.component_scope) != set(benchmark.component_schema):
                raise ValueError("capability component scope must match the registry schema")
            if set(capability.required_modalities) != set(benchmark.modalities):
                raise ValueError("capability modality scope must match the registry modalities")
            if not set(capability.supported_components) <= set(capability.component_scope):
                raise ValueError("supported components must be a subset of the declared scope")
            if not set(capability.supported_modalities) <= set(capability.required_modalities):
                raise ValueError("supported modalities must be a subset of the declared scope")
            if capability.importer_state == "not_implemented" and capability.supported_components:
                raise ValueError("unimplemented importers cannot declare supported components")
        if self.limits.max_source_groups_per_plan > len(source_slugs):
            raise ValueError("resource policy permits more source groups than are registered")
        return self


class AuditResourceRequest(AuditRegistryModel):
    task_counts: dict[str, int] = Field(min_length=1, max_length=100)
    source_groups: tuple[str, ...] = Field(min_length=1, max_length=8)
    stages: tuple[str, ...] = Field(min_length=1, max_length=5)
    average_item_bytes: int | None = Field(ge=1, le=1_099_511_627_776)

    @field_validator("task_counts")
    @classmethod
    def counts_are_positive(cls, value: dict[str, int]) -> dict[str, int]:
        if any(type(count) is not int or count < 1 for count in value.values()):
            raise ValueError("each selected benchmark task count must be a positive integer")
        return value

    @model_validator(mode="after")
    def selections_are_unique(self) -> AuditResourceRequest:
        if len(set(self.source_groups)) != len(self.source_groups):
            raise ValueError("source groups must not be repeated")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("retrieval stages must not be repeated")
        return self


class SourceResourcePlan(AuditRegistryModel):
    source_group: str
    planned_query_units: int = Field(ge=0)
    request_cap: int = Field(ge=1)
    authorization_state: Literal["not_approved", "approved_scoped", "gated"]
    state: Literal["ready_for_authorized_execution", "blocked"]
    blockers: tuple[str, ...]


class BenchmarkResourcePlan(AuditRegistryModel):
    benchmark_slug: str
    selected_tasks: int = Field(ge=1)
    version_state: Literal[
        "dataset_version_pinned",
        "repository_snapshot_pinned",
        "metadata_only",
        "unresolved",
    ]
    split_state: Literal["pinned", "partially_documented", "unresolved"]
    access_state: Literal[
        "public_metadata",
        "public_data_review_required",
        "gated",
        "owner_supplied",
    ]
    rights_state: Literal[
        "needs_item_review",
        "license_review_required",
        "gated_access_required",
        "owner_contract_required",
        "approved_for_declared_scope",
    ]
    importer_status: Literal[
        "not_implemented", "metadata_only", "importable", "audit_conformant", "blocked"
    ]
    state: Literal["ready_for_authorized_import", "blocked"]
    blockers: tuple[str, ...]


class AuditResourcePlan(AuditRegistryModel):
    plan_version: str
    policy_version: str
    benchmark_counts: dict[str, int]
    total_tasks: int = Field(ge=1)
    selected_stages: tuple[str, ...]
    benchmark_plans: tuple[BenchmarkResourcePlan, ...]
    source_plans: tuple[SourceResourcePlan, ...]
    query_ceiling: int = Field(ge=0)
    candidate_ceiling: int = Field(ge=0)
    estimated_storage_bytes: int | None = Field(ge=0)
    estimated_cost_usd: str | None
    cost_state: Literal["unknown_price"]
    model_call_ceiling: Literal[0]
    dispatch_allowed: Literal[False]
    state: Literal["ready_for_review", "blocked"]
    blockers: tuple[str, ...]
