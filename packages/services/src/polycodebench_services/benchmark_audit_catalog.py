"""Safe catalog loading and no-dispatch resource planning for benchmark audits."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import yaml
from polycodebench_core.benchmark_audit_registry import (
    AuditCatalogBundle,
    AuditResourcePlan,
    AuditResourceRequest,
    BenchmarkResourcePlan,
    BenchmarkScopeConformanceReport,
    BenchmarkScopeEvidence,
    SourceResourcePlan,
)
from pydantic import ValidationError
from yaml.nodes import MappingNode


class BenchmarkAuditCatalogError(ValueError):
    """Raised when versioned audit configuration is malformed or inconsistent."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    untyped_loader = cast(Any, loader)
    for key_node, value_node in node.value:
        key = untyped_loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise BenchmarkAuditCatalogError(
                "catalog mapping keys must be scalar values"
            ) from error
        if duplicate:
            raise BenchmarkAuditCatalogError(f"duplicate YAML key: {key}")
        mapping[key] = untyped_loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _tuple_sequences(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_tuple_sequences(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuple_sequences(item) for key, item in value.items()}
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = yaml.load(stream, Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeError, yaml.YAMLError, BenchmarkAuditCatalogError) as error:
        raise BenchmarkAuditCatalogError(
            f"cannot load audit catalog {path.name}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise BenchmarkAuditCatalogError(f"audit catalog {path.name} must contain a YAML mapping")
    return value


def load_audit_catalog(config_dir: Path) -> AuditCatalogBundle:
    """Load and cross-check all local, versioned catalog documents without network access."""
    try:
        registry = _tuple_sequences(_read_yaml(config_dir / "registry-v1.yaml"))
        source_policies = _tuple_sequences(_read_yaml(config_dir / "source-policy-v1.yaml"))
        capabilities = _tuple_sequences(_read_yaml(config_dir / "capability-matrix-v1.yaml"))
        limits = _tuple_sequences(_read_yaml(config_dir / "planning-limits-v1.yaml"))
        return AuditCatalogBundle.model_validate(
            {
                "registry": registry,
                "source_policies": source_policies,
                "capabilities": capabilities,
                "limits": limits,
            },
            strict=True,
        )
    except ValidationError as error:
        raise BenchmarkAuditCatalogError(f"audit catalog validation failed: {error}") from error


def plan_audit_resources(
    bundle: AuditCatalogBundle, request: AuditResourceRequest
) -> AuditResourcePlan:
    """Return a bounded capacity estimate; this function never submits a source query."""
    benchmark_by_slug = {entry.slug: entry for entry in bundle.registry.benchmarks}
    source_by_slug = {entry.slug: entry for entry in bundle.source_policies.groups}
    unknown_benchmarks = set(request.task_counts) - set(benchmark_by_slug)
    if unknown_benchmarks:
        raise BenchmarkAuditCatalogError(
            f"unknown benchmark slugs: {', '.join(sorted(unknown_benchmarks))}"
        )
    unknown_sources = set(request.source_groups) - set(source_by_slug)
    if unknown_sources:
        raise BenchmarkAuditCatalogError(
            f"unknown source groups: {', '.join(sorted(unknown_sources))}"
        )
    limits = bundle.limits
    total_tasks = sum(request.task_counts.values())
    if total_tasks > limits.max_tasks_per_plan:
        raise BenchmarkAuditCatalogError(
            f"selected tasks exceed the frozen per-plan limit ({limits.max_tasks_per_plan})"
        )
    if len(request.source_groups) > limits.max_source_groups_per_plan:
        raise BenchmarkAuditCatalogError("selected source groups exceed the frozen per-plan limit")
    if len(request.stages) > limits.max_stages_per_task:
        raise BenchmarkAuditCatalogError(
            "selected retrieval stages exceed the frozen per-plan limit"
        )

    query_ceiling = total_tasks * len(request.source_groups) * len(request.stages)
    candidate_capacity_per_task = min(
        limits.max_candidates_per_task,
        limits.max_candidates_per_source_per_task * len(request.source_groups),
    )
    candidate_ceiling = total_tasks * candidate_capacity_per_task
    if query_ceiling > limits.max_query_units_per_plan:
        raise BenchmarkAuditCatalogError("query ceiling exceeds the frozen per-plan limit")

    estimated_storage = (
        total_tasks * request.average_item_bytes if request.average_item_bytes is not None else None
    )
    blockers: list[str] = []
    blockers.append("source/provider pricing is unknown; no work may be dispatched")
    if estimated_storage is not None and estimated_storage > limits.max_storage_bytes_per_plan:
        raise BenchmarkAuditCatalogError("estimated storage exceeds the frozen per-plan limit")

    benchmark_plans: list[BenchmarkResourcePlan] = []
    for benchmark_slug, selected_tasks in request.task_counts.items():
        benchmark = benchmark_by_slug[benchmark_slug]
        benchmark_blockers: list[str] = []
        if benchmark.importer_status in {"not_implemented", "metadata_only"}:
            benchmark_blockers.append("no import-capable audit adapter is registered")
        elif benchmark.importer_status == "blocked":
            benchmark_blockers.append("benchmark import is explicitly blocked by catalog policy")
        if benchmark.rights_state != "approved_for_declared_scope":
            benchmark_blockers.append("rights are not approved for the selected source scope")
        if benchmark.version_state in {"metadata_only", "unresolved"}:
            benchmark_blockers.append("an exact benchmark source revision is not pinned")
        if benchmark.split_state != "pinned":
            benchmark_blockers.append("an exact benchmark split is not frozen")
        if benchmark.access_state in {"gated", "owner_supplied"}:
            benchmark_blockers.append(
                "benchmark access requires an owner grant or supplied artifact"
            )
        if benchmark.audit_status in {"blocked", "retired"}:
            benchmark_blockers.append(f"benchmark catalog state is {benchmark.audit_status}")
        if benchmark_blockers:
            blockers.extend(f"{benchmark_slug}: {reason}" for reason in benchmark_blockers)
        benchmark_plans.append(
            BenchmarkResourcePlan(
                benchmark_slug=benchmark_slug,
                selected_tasks=selected_tasks,
                version_state=benchmark.version_state,
                split_state=benchmark.split_state,
                access_state=benchmark.access_state,
                rights_state=benchmark.rights_state,
                importer_status=benchmark.importer_status,
                state="blocked" if benchmark_blockers else "ready_for_authorized_import",
                blockers=tuple(benchmark_blockers),
            )
        )

    source_plans: list[SourceResourcePlan] = []
    for source_slug in request.source_groups:
        policy = source_by_slug[source_slug]
        source_query_units = total_tasks * len(request.stages)
        source_blockers: list[str] = []
        if policy.authorization_state != "approved_scoped":
            source_blockers.append("no approved source-scope evidence is registered")
        if policy.connector_state not in {"importable", "audit_conformant"}:
            source_blockers.append("no source connector is registered for this group")
        if policy.access_state in {"gated", "owner_supplied"}:
            source_blockers.append("source access requires owner authorization")
        if source_query_units > policy.max_requests_per_plan:
            source_blockers.append("source-specific request cap would be exceeded")
        state: Literal["ready_for_authorized_execution", "blocked"] = (
            "blocked" if source_blockers else "ready_for_authorized_execution"
        )
        if source_blockers:
            blockers.extend(f"{source_slug}: {reason}" for reason in source_blockers)
        source_plans.append(
            SourceResourcePlan(
                source_group=source_slug,
                planned_query_units=source_query_units,
                request_cap=policy.max_requests_per_plan,
                authorization_state=policy.authorization_state,
                state=state,
                blockers=tuple(source_blockers),
            )
        )
    if estimated_storage is None:
        blockers.append("item byte-size estimate was not supplied")
    return AuditResourcePlan(
        plan_version="dry-run-v1",
        policy_version=limits.policy_version,
        benchmark_counts=dict(request.task_counts),
        total_tasks=total_tasks,
        selected_stages=request.stages,
        benchmark_plans=tuple(benchmark_plans),
        source_plans=tuple(source_plans),
        query_ceiling=query_ceiling,
        candidate_ceiling=candidate_ceiling,
        estimated_storage_bytes=estimated_storage,
        estimated_cost_usd=None,
        cost_state="unknown_price",
        model_call_ceiling=0,
        dispatch_allowed=False,
        state="blocked" if blockers else "ready_for_review",
        blockers=tuple(blockers),
    )


_SOURCE_FIXTURE_IMPORTERS = frozenset({"humaneval", "mbpp", "swe-bench-verified", "gsm8k"})


def build_scope_conformance_report(
    bundle: AuditCatalogBundle,
) -> BenchmarkScopeConformanceReport:
    """Materialize every catalog family as explicit version/access/scope/test evidence.

    Repository metadata and synthetic source fixtures never upgrade a family to live
    conformance. The report is deterministic and performs no source or model calls.
    """
    capabilities = {row.benchmark_slug: row for row in bundle.capabilities.benchmarks}
    rows: list[BenchmarkScopeEvidence] = []
    for benchmark in bundle.registry.benchmarks:
        capability = capabilities[benchmark.slug]
        supported_components = set(capability.supported_components)
        supported_modalities = set(capability.supported_modalities)
        unsupported_components = tuple(
            component
            for component in capability.component_scope
            if component not in supported_components
        )
        unsupported_modalities = tuple(
            modality
            for modality in capability.required_modalities
            if modality not in supported_modalities
        )
        blockers: list[str] = []
        if benchmark.version_state != "dataset_version_pinned":
            blockers.append("benchmark_dataset_revision_not_pinned")
        if benchmark.split_state != "pinned":
            blockers.append("benchmark_split_not_pinned")
        if not benchmark.source_pins:
            blockers.append("official_source_revision_not_pinned")
        if benchmark.access_state in {"gated", "owner_supplied"}:
            blockers.append("benchmark_access_requires_authorization")
        if benchmark.rights_state != "approved_for_declared_scope":
            blockers.append("benchmark_rights_not_approved")
        if capability.importer_state != "audit_conformant":
            blockers.append("benchmark_importer_not_live_conformant")
        if unsupported_components:
            blockers.append("benchmark_components_not_supported")
        if unsupported_modalities:
            blockers.append("benchmark_modalities_not_supported")
        if capability.runtime_state != "conformant":
            blockers.append("benchmark_runtime_not_conformant")
        if capability.conformance_state != "live_verified":
            blockers.append("benchmark_live_conformance_not_verified")
        live_state: Literal["pending", "blocked", "live_verified"]
        if benchmark.audit_status == "blocked" or capability.conformance_state == "blocked":
            live_state = "blocked"
        elif capability.runtime_state == "blocked" or (
            benchmark.family in {"agent", "multimodal", "private"} and unsupported_modalities
        ):
            live_state = "blocked"
        elif blockers:
            live_state = "pending"
        else:
            live_state = "live_verified"

        test_references = [
            "tests/test_benchmark_breadth.py",
            "tests/test_benchmark_audit_catalog.py",
        ]
        if benchmark.slug in _SOURCE_FIXTURE_IMPORTERS:
            test_references.append("tests/test_benchmark_importers.py")
        rows.append(
            BenchmarkScopeEvidence(
                benchmark_slug=benchmark.slug,
                name=benchmark.name,
                family=benchmark.family,
                official_url=benchmark.official_url,
                version=benchmark.version,
                version_state=benchmark.version_state,
                source_pins=benchmark.source_pins,
                split_policy=benchmark.split_policy,
                split_state=benchmark.split_state,
                upstream_lineage=benchmark.upstream_lineage,
                access_state=benchmark.access_state,
                rights_state=benchmark.rights_state,
                component_scope=capability.component_scope,
                supported_components=capability.supported_components,
                unsupported_components=unsupported_components,
                source_groups=capability.source_groups,
                required_modalities=capability.required_modalities,
                supported_modalities=capability.supported_modalities,
                unsupported_modalities=unsupported_modalities,
                importer_state=capability.importer_state,
                runtime_state=capability.runtime_state,
                conformance_state=capability.conformance_state,
                fixture_evidence_state=(
                    "synthetic_source_fixture"
                    if benchmark.slug in _SOURCE_FIXTURE_IMPORTERS
                    else "catalog_contract_only"
                ),
                test_references=tuple(test_references),
                live_state=live_state,
                live_blockers=tuple(sorted(set(blockers))),
            )
        )
    return BenchmarkScopeConformanceReport(
        schema_version=1,
        catalog_version=bundle.registry.catalog_version,
        observed_on=bundle.registry.observed_on,
        benchmarks=tuple(rows),
    )
