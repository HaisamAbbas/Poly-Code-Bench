"""Java task-contract tests with authored synthetic fixtures."""

from __future__ import annotations

import pytest
import yaml
from java_plugin_support import TASK_ROOT, draft, identities
from polycodebench_lang_java import JavaLanguagePlugin
from polycodebench_lang_java.taskspec import (
    JavaQualityPlan,
    OracleGroup,
    PerformanceDecl,
    discover_cases,
    parse_oracle,
    parse_quality_plan,
)


def test_authored_java_task_has_all_required_fixture_variants() -> None:
    report = JavaLanguagePlugin(identities()).validate_task(draft())
    assert report.ok, [item.model_dump() for item in report.issues]
    document = yaml.safe_load((TASK_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    variants = {item["language_variant"] for item in document["fixtures"]}
    assert variants >= {
        "reference",
        "faulty",
        "alternative",
        "null_unsafe",
        "resource_leak",
        "concurrent_defect",
        "security_defective",
        "timeout",
    }


def test_junit_discovery_handles_plain_test_and_other_annotations() -> None:
    source = b'''package demo;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;
class ExampleTest {
    @Test
    @DisplayName("plain annotation")
    void firstCase() {}

    @Test()
    void secondCase() {}
}
'''
    assert discover_cases("tests/demo/ExampleTest.java", source) == [
        "demo.ExampleTest#firstCase",
        "demo.ExampleTest#secondCase",
    ]


def test_surefire_selectors_are_package_qualified_java_names() -> None:
    group = OracleGroup.model_validate(
        {
            "group_id": "behaviour",
            "required": True,
            "classification": "acceptance",
            "files": ("tests/demo/TopWordsTest.java",),
            "cases": (
                {"case_id": "demo.TopWordsTest#works", "required": True, "case_kind": "example"},
            ),
        }
    )
    assert group.selectors == ("demo.TopWordsTest",)


def test_perf_mode_and_warmup_are_fixed_before_candidates() -> None:
    with pytest.raises(ValueError, match="cold mode must declare zero warmup"):
        PerformanceDecl.model_validate(
            {
                "workload_file": "tests/Workload.java",
                "workloads": (
                    {"workload_id": "one", "scale": 1, "weight_bp": 10000, "input_seed": 1},
                ),
                "measurement_mode": "cold",
                "warmup_iterations": 1,
            }
        )
    with pytest.raises(ValueError, match="at least 20 fixed warmup"):
        PerformanceDecl.model_validate(
            {
                "workload_file": "tests/Workload.java",
                "workloads": (
                    {"workload_id": "one", "scale": 1, "weight_bp": 10000, "input_seed": 1},
                ),
                "measurement_mode": "steady_state",
                "warmup_iterations": 19,
            }
        )


def test_resource_concurrency_and_security_cannot_be_advertised_without_evidence() -> None:
    with pytest.raises(ValueError, match="resource test group"):
        JavaQualityPlan.model_validate(
            {"resources": "required", "opportunities": {"resource_handling": 1}}
        )
    with pytest.raises(ValueError, match="concurrency test group"):
        JavaQualityPlan.model_validate(
            {"concurrency": "required", "opportunities": {"concurrency": 1}}
        )
    with pytest.raises(ValueError, match="contextual security probe"):
        JavaQualityPlan.model_validate(
            {
                "security_surface": "required",
                "opportunity_tags": ("security_surface",),
                "required_analyzers": ("spotbugs",),
            }
        )
    with pytest.raises(ValueError, match="unknown required analyzer"):
            JavaQualityPlan.model_validate({"required_analyzers": ("concurrency",)})


def test_junit_inventory_and_quality_contract_load_without_coercion() -> None:
    task = draft()
    oracle = parse_oracle(task.files["hidden/oracle.json"])
    quality = parse_quality_plan(task.files["hidden/quality-plan.yaml"])
    assert [group.group_id for group in oracle.groups] == [
        "behaviour",
        "resource-probe",
        "concurrency-probe",
    ]
    assert quality.resources == "required"
    assert quality.concurrency == "required"
    assert quality.security_surface == "required"
    assert set(quality.required_analyzers) == {
        "spotbugs",
        "pmd",
        "checkstyle",
        "context",
        "dependency",
    }
