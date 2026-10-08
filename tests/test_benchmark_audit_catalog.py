from __future__ import annotations

from pathlib import Path

import pytest
from polycodebench_core.benchmark_audit_registry import AuditCatalogBundle, AuditResourceRequest
from polycodebench_services.benchmark_audit_catalog import (
    BenchmarkAuditCatalogError,
    _read_yaml,
    build_scope_conformance_report,
    load_audit_catalog,
    plan_audit_resources,
)

ROOT = Path(__file__).parents[1]
CONFIG_DIR = ROOT / "config" / "benchmark-audit"


def _bundle():
    return load_audit_catalog(CONFIG_DIR)


def _request(
    *,
    task_counts: dict[str, int] | None = None,
    source_groups: tuple[str, ...] | None = None,
    stages: tuple[str, ...] = ("exact", "lexical", "structural", "semantic", "verification"),
    average_item_bytes: int | None = 1024,
) -> AuditResourceRequest:
    bundle = _bundle()
    return AuditResourceRequest(
        task_counts=task_counts or {"humaneval": 100, "mbpp": 100, "swe-bench-verified": 100},
        source_groups=source_groups or tuple(group.slug for group in bundle.source_policies.groups),
        stages=stages,
        average_item_bytes=average_item_bytes,
    )


def test_catalog_has_every_spec_family_and_all_eight_source_policies() -> None:
    bundle = _bundle()

    assert len(bundle.registry.benchmarks) == 25
    assert len(bundle.source_policies.groups) == 8
    assert {row.benchmark_slug for row in bundle.capabilities.benchmarks} == {
        row.slug for row in bundle.registry.benchmarks
    }
    assert all(row.native_evaluation_unchanged for row in bundle.registry.benchmarks)
    pilot_slugs = {"humaneval", "mbpp", "swe-bench-verified"}
    assert {
        row.slug for row in bundle.registry.benchmarks if row.importer_status == "blocked"
    } == pilot_slugs | {"gpqa", "gsm8k", "hellaswag", "gaia"}
    benchmark_by_slug = {row.slug: row for row in bundle.registry.benchmarks}
    for capability in bundle.capabilities.benchmarks:
        benchmark = benchmark_by_slug[capability.benchmark_slug]
        source_scope = {row.slug for row in bundle.source_policies.groups}
        assert set(capability.source_groups) == source_scope
        assert capability.component_scope == benchmark.component_schema
        assert capability.required_modalities == benchmark.modalities
        if capability.benchmark_slug == "humaneval":
            assert capability.supported_components == (
                "prompt",
                "entry_point",
                "canonical_solution",
                "tests",
            )
            assert capability.conformance_state == "fixture_only"
        elif capability.benchmark_slug == "mbpp":
            assert capability.supported_components == (
                "prompt",
                "canonical_solution",
                "tests",
                "test_imports",
                "challenge_tests",
            )
            assert capability.conformance_state == "fixture_only"
        elif capability.benchmark_slug == "swe-bench-verified":
            assert capability.supported_components == (
                "repository",
                "base_commit",
                "issue",
                "gold_patch",
                "test_patch",
                "source_metadata",
            )
            assert capability.conformance_state == "fixture_only"
        elif capability.benchmark_slug == "gsm8k":
            assert capability.supported_components == capability.component_scope
            assert capability.conformance_state == "fixture_only"
        elif capability.benchmark_slug in {"hellaswag", "gaia"}:
            assert capability.supported_components == ()
            assert capability.conformance_state == "blocked"
        else:
            assert capability.supported_components == ()
            assert capability.conformance_state == "not_run"
        assert capability.supported_modalities == ()
    assert all(row.connector_state == "not_implemented" for row in bundle.source_policies.groups)
    assert all(row.conformance_state == "not_run" for row in bundle.source_policies.groups)


def test_scope_report_maps_every_family_without_promoting_fixture_or_metadata() -> None:
    bundle = _bundle()
    report = build_scope_conformance_report(bundle)
    rows = {row.benchmark_slug: row for row in report.benchmarks}

    assert len(rows) == 25
    assert set(rows) == {entry.slug for entry in bundle.registry.benchmarks}
    assert all(row.official_url.startswith("https://") for row in rows.values())
    assert all(row.component_scope for row in rows.values())
    assert all(row.source_groups for row in rows.values())
    assert all(row.test_references for row in rows.values())
    assert all(row.live_state != "live_verified" for row in rows.values())

    assert rows["gsm8k"].fixture_evidence_state == "synthetic_source_fixture"
    assert rows["gsm8k"].supported_components == rows["gsm8k"].component_scope
    assert rows["gsm8k"].live_state == "pending"
    assert "benchmark_rights_not_approved" in rows["gsm8k"].live_blockers
    assert rows["mmlu-pro"].version_state == "dataset_version_pinned"
    assert rows["arc"].version_state == "dataset_version_pinned"
    assert rows["mgsm"].upstream_lineage == ("gsm8k",)

    for slug in ("gpqa", "hellaswag", "gaia"):
        assert rows[slug].live_state == "blocked"
    for slug in ("terminal-bench", "gaia", "bfcl", "mmmu", "custom-private"):
        assert rows[slug].unsupported_modalities
        assert rows[slug].live_state == "blocked"
    assert rows["gaia"].access_state == "gated"
    assert rows["hellaswag"].importer_state == "blocked"


def test_catalog_rejects_registry_claims_without_live_conformance() -> None:
    bundle = _bundle()
    registry_rows = tuple(
        row.model_copy(
            update={"audit_status": "audit_conformant", "importer_status": "audit_conformant"}
        )
        if row.slug == "gsm8k"
        else row
        for row in bundle.registry.benchmarks
    )
    capability_rows = tuple(
        row.model_copy(update={"importer_state": "audit_conformant"})
        if row.benchmark_slug == "gsm8k"
        else row
        for row in bundle.capabilities.benchmarks
    )

    with pytest.raises(ValueError, match="requires live scope evidence"):
        AuditCatalogBundle(
            registry=bundle.registry.model_copy(update={"benchmarks": registry_rows}),
            source_policies=bundle.source_policies,
            capabilities=bundle.capabilities.model_copy(update={"benchmarks": capability_rows}),
            limits=bundle.limits,
        )


def test_catalog_rejects_cyclic_benchmark_lineage() -> None:
    bundle = _bundle()
    registry_rows = tuple(
        row.model_copy(update={"upstream_lineage": ("mmlu-pro",)}) if row.slug == "mmlu" else row
        for row in bundle.registry.benchmarks
    )

    with pytest.raises(ValueError, match="lineage contains a cycle"):
        AuditCatalogBundle(
            registry=bundle.registry.model_copy(update={"benchmarks": registry_rows}),
            source_policies=bundle.source_policies,
            capabilities=bundle.capabilities,
            limits=bundle.limits,
        )


def test_pilot_plan_returns_capacity_ceilings_and_never_dispatches() -> None:
    bundle = _bundle()
    request = _request()

    plan = plan_audit_resources(bundle, request)

    assert plan.total_tasks == 300
    assert plan.query_ceiling == 300 * 8 * 5
    assert plan.candidate_ceiling == 300 * 100
    assert plan.estimated_storage_bytes == 300 * 1024
    assert plan.estimated_cost_usd is None
    assert plan.cost_state == "unknown_price"
    assert plan.model_call_ceiling == 0
    assert plan.dispatch_allowed is False
    assert plan.state == "blocked"
    assert any("pricing is unknown" in blocker for blocker in plan.blockers)
    assert all(source.state == "blocked" for source in plan.source_plans)
    assert all("connector" in source.blockers[-1] for source in plan.source_plans)
    assert all(benchmark.state == "blocked" for benchmark in plan.benchmark_plans)
    assert all(source.planned_query_units == 1500 for source in plan.source_plans)


def test_missing_storage_size_stays_unknown_instead_of_becoming_zero() -> None:
    plan = plan_audit_resources(_bundle(), _request(average_item_bytes=None))

    assert plan.estimated_storage_bytes is None
    assert plan.state == "blocked"
    assert any("byte-size estimate" in blocker for blocker in plan.blockers)


def test_gated_benchmark_is_explicitly_blocked() -> None:
    plan = plan_audit_resources(
        _bundle(),
        _request(
            task_counts={"gpqa": 10},
            source_groups=("benchmark-repositories",),
            stages=("exact",),
        ),
    )

    gpqa = plan.benchmark_plans[0]
    assert gpqa.access_state == "gated"
    assert gpqa.state == "blocked"
    assert any("owner grant" in blocker for blocker in gpqa.blockers)
    assert any("explicitly blocked" in blocker for blocker in gpqa.blockers)


@pytest.mark.parametrize(
    ("task_counts", "message"),
    [
        ({"missing-benchmark": 1}, "unknown benchmark"),
        ({"humaneval": 301}, "frozen per-plan limit"),
    ],
)
def test_plan_rejects_unknown_benchmarks_and_oversized_scopes(
    task_counts: dict[str, int], message: str
) -> None:
    with pytest.raises(BenchmarkAuditCatalogError, match=message):
        plan_audit_resources(_bundle(), _request(task_counts=task_counts))


def test_plan_rejects_storage_estimates_over_the_frozen_limit() -> None:
    with pytest.raises(BenchmarkAuditCatalogError, match="storage exceeds"):
        plan_audit_resources(
            _bundle(), _request(task_counts={"humaneval": 300}, average_item_bytes=2_000_000)
        )


def test_source_specific_budget_overrun_blocks_only_in_the_dry_run() -> None:
    bundle = _bundle()
    first_policy = bundle.source_policies.groups[0].model_copy(
        update={"max_requests_per_plan": 5}
    )
    source_policies = bundle.source_policies.model_copy(
        update={"groups": (first_policy, *bundle.source_policies.groups[1:])}
    )
    bundle = bundle.model_copy(update={"source_policies": source_policies})
    request = _request(
        task_counts={"humaneval": 10},
        source_groups=(first_policy.slug,),
        stages=("exact",),
    )

    plan = plan_audit_resources(bundle, request)

    assert plan.dispatch_allowed is False
    assert plan.source_plans[0].planned_query_units == 10
    assert "source-specific request cap would be exceeded" in plan.source_plans[0].blockers


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    config = tmp_path / "duplicate.yaml"
    config.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")

    with pytest.raises(BenchmarkAuditCatalogError, match="duplicate YAML key"):
        _read_yaml(config)


def test_request_rejects_duplicate_source_groups() -> None:
    with pytest.raises(ValueError, match="source groups must not be repeated"):
        _request(source_groups=("github", "github"))
