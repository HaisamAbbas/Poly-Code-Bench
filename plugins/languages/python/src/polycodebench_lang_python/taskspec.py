"""Python task package contracts: hidden oracle inventory, quality plan and task validation.

Layout (Technical Spec 11.1, Python profile)::

    manifest.yaml
    visible/task.md  visible/repo/...  visible/tests/...
    hidden/oracle.json  hidden/quality-plan.yaml  hidden/tests/...  hidden/reference/...
    hidden/perf/workload.py            (optional performance workload)
    admission/<variant>/...            (faulty, alternative, quality-defective, timeout solutions)

The oracle lists *every* test case the grader expects; validation fails when the hidden tests
contain a case the inventory does not declare, so a mandatory test can never be silently dropped
or added.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from typing import Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import canonical_digest, parse_json_strict
from polycodebench_core.models import RelativePath, Slug
from polycodebench_plugins_api import (
    PluginModel,
    TaskDraft,
    ValidationIssue,
    ValidationReport,
)
from polycodebench_plugins_api.testreport import InventoryGroup, inventory_from_document
from pydantic import Field, ValidationError, model_validator

DIAGNOSTIC_ITEMS = (
    "readability_idioms",
    "type_hints",
    "stdlib_use",
    "error_handling",
    "lint_style",
    "performance_awareness",
)
IDIOM_ITEMS = (
    "iteration_laziness",
    "stdlib_api_choice",
    "data_protocol_modeling",
    "context_resource_abstraction",
)
KNOWN_ANALYZERS = ("ruff", "mypy", "bandit", "semgrep", "context", "dependency")
VARIANTS = ("reference", "faulty", "alternative", "quality_defective", "timeout")


class OracleCase(PluginModel):
    kind: Literal["oracle_case"] = "oracle_case"
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    case_kind: Literal["example", "property", "robustness", "edge"]
    predeclared_skip: bool = False


class OracleGroup(PluginModel):
    kind: Literal["oracle_group"] = "oracle_group"
    group_id: Slug
    required: bool
    classification: Literal["acceptance", "quality_only"]
    files: tuple[RelativePath, ...] = Field(min_length=1)
    cases: tuple[OracleCase, ...] = Field(min_length=1)


class HardCondition(PluginModel):
    kind: Literal["hard_condition"] = "hard_condition"
    condition_id: Slug
    group_id: Slug


class RobustnessScenario(PluginModel):
    kind: Literal["robustness_scenario"] = "robustness_scenario"
    scenario_id: Slug
    group_id: Slug
    weight_bp: int = Field(ge=1, le=10_000)
    repetitions: int = Field(default=3, ge=1, le=10)
    hard_acceptance: bool = False


class PythonOracle(PluginModel):
    kind: Literal["python_task_oracle"] = "python_task_oracle"
    oracle_version: Slug
    groups: tuple[OracleGroup, ...] = Field(min_length=1)
    hard_conditions: tuple[HardCondition, ...] = ()
    robustness_scenarios: tuple[RobustnessScenario, ...] = ()
    case_timeout_seconds: int = Field(default=5, ge=1, le=60)
    suite_timeout_seconds: int = Field(default=60, ge=1, le=110)
    hypothesis_examples: int = Field(default=50, ge=1, le=1000)

    @model_validator(mode="after")
    def unique_references(self) -> PythonOracle:
        ids = [group.group_id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("oracle group ids must be unique")
        known = set(ids)
        if any(c.group_id not in known for c in self.hard_conditions):
            raise ValueError("hard condition names an unknown group")
        if any(s.group_id not in known for s in self.robustness_scenarios):
            raise ValueError("robustness scenario names an unknown group")
        if (
            self.robustness_scenarios
            and sum(s.weight_bp for s in self.robustness_scenarios) != 10_000
        ):
            raise ValueError("robustness scenario weights must sum to 10000")
        return self

    def inventory_document(self) -> dict[str, object]:
        return {
            "groups": [
                {
                    "group_id": g.group_id,
                    "required": g.required,
                    "cases": [
                        {
                            "case_id": c.case_id,
                            "required": c.required,
                            "predeclared_skip": c.predeclared_skip,
                        }
                        for c in g.cases
                    ],
                }
                for g in self.groups
            ]
        }

    def inventory(self) -> tuple[InventoryGroup, ...]:
        return inventory_from_document(self.inventory_document())

    def inventory_digest(self) -> str:
        return str(canonical_digest(self.model_dump(mode="json")))


class ExposureRights(PluginModel):
    """Exposure and rights record required for every task (Technical Spec 11.4)."""

    kind: Literal["exposure_rights_record"] = "exposure_rights_record"
    authorship: str = Field(min_length=3, max_length=500)
    originality_statement: str = Field(min_length=10, max_length=1000)
    first_public_at: str | None
    public_exposure_review: str = Field(min_length=5, max_length=1000)
    access_history: tuple[Mapping[str, str], ...] = Field(min_length=1)
    provider_transmission_policy: str = Field(min_length=3, max_length=300)
    rights: Mapping[str, str]

    @model_validator(mode="after")
    def rights_are_explicit(self) -> ExposureRights:
        needed = {"license_expression", "status", "owner_confirmation"}
        if not needed <= set(self.rights):
            raise ValueError(
                "rights record needs license_expression, status and owner_confirmation"
            )
        if self.rights["owner_confirmation"] not in {"pending", "confirmed"}:
            raise ValueError("owner_confirmation must be pending or confirmed")
        return self


class WorkloadDecl(PluginModel):
    kind: Literal["workload_decl"] = "workload_decl"
    workload_id: Slug
    scale: int = Field(ge=1, le=10**9)
    weight_bp: int = Field(ge=1, le=10_000)
    input_seed: int = Field(ge=0, le=2**63 - 1)


class PerformanceDecl(PluginModel):
    kind: Literal["performance_decl"] = "performance_decl"
    workload_file: RelativePath
    workloads: tuple[WorkloadDecl, ...] = Field(min_length=1)
    warmup_iterations: int = Field(default=5, ge=0, le=100)
    measured_iterations: int = Field(default=20, ge=1, le=1000)
    hard_timeout_seconds: int = Field(default=30, ge=1, le=110)

    @model_validator(mode="after")
    def weights(self) -> PerformanceDecl:
        if sum(w.weight_bp for w in self.workloads) != 10_000:
            raise ValueError("workload weights must sum to 10000")
        return self


class PythonQualityPlan(PluginModel):
    kind: Literal["python_task_quality_plan"] = "python_task_quality_plan"
    typing_expectation: Literal["required", "none"]
    opportunity_tags: tuple[Slug, ...] = ()
    opportunities: Mapping[str, int] = Field(default_factory=dict)
    required_analyzers: tuple[Slug, ...]
    dependency_inventory: tuple[str, ...] = ()
    performance: PerformanceDecl | None = None
    judge_items: tuple[Slug, ...] = ()

    @model_validator(mode="after")
    def valid_items(self) -> PythonQualityPlan:
        allowed = set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS)
        for name, count in self.opportunities.items():
            if name not in allowed or count < 0:
                raise ValueError(f"unknown opportunity item or negative count: {name}")
        if any(a not in KNOWN_ANALYZERS for a in self.required_analyzers):
            raise ValueError("unknown required analyzer")
        if self.typing_expectation == "required" and "mypy" not in self.required_analyzers:
            raise ValueError("a required typing expectation needs the mypy analyzer")
        if self.typing_expectation == "none" and self.opportunities.get("type_hints", 0):
            raise ValueError("type_hints opportunities contradict typing_expectation none")
        return self


def parse_oracle(data: bytes) -> PythonOracle:
    return PythonOracle.model_validate_json(json.dumps(parse_json_strict(data)))


def parse_quality_plan(data: bytes) -> PythonQualityPlan:
    return PythonQualityPlan.model_validate_json(json.dumps(yaml.safe_load(data)))


def discover_cases(path: str, source: bytes) -> list[str]:
    """Node ids of every test function/method in a test module (static, no import)."""
    tree = ast.parse(source, filename=path)
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test"
        ):
            found.append(f"{path}::{node.name}")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(
                    item, ast.FunctionDef | ast.AsyncFunctionDef
                ) and item.name.startswith("test"):
                    found.append(f"{path}::{node.name}::{item.name}")
    return found


def _issue(
    code: str, message: str, path: str | None = None, severity: str = "error"
) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, path=path, message=message)  # type: ignore[arg-type]


def validate_python_task(task: TaskDraft, plugin_id: str = "python") -> ValidationReport:
    """Structural and executable-contract validation that needs no sandbox."""
    issues: list[ValidationIssue] = []
    checks = [
        "language",
        "layout",
        "python-syntax",
        "oracle-inventory",
        "acceptance-links",
        "quality-plan",
        "dimension-evidence",
        "variant-files",
        "exposure-rights",
    ]
    manifest = task.manifest
    files = task.files
    if task.primary_language != "python":
        issues.append(_issue("wrong-language", "primary_language must be python"))
    required = [
        "visible/task.md",
        "hidden/oracle.json",
        "hidden/quality-plan.yaml",
        "admission/exposure-rights.json",
    ]
    acceptance = manifest.get("acceptance")
    outputs: list[str] = []
    if isinstance(acceptance, Mapping):
        outputs = [str(o) for o in acceptance.get("required_outputs", [])]
    for path in required:
        if path not in files:
            issues.append(_issue("missing-file", "required task file is absent", path))
    for output in outputs:
        if f"hidden/reference/{output}" not in files:
            issues.append(_issue("missing-reference", "reference for a required output", output))
    if not any(p.startswith("hidden/tests/") for p in files):
        issues.append(_issue("no-hidden-tests", "hidden/tests contains no files"))
    for path, data in sorted(files.items()):
        if path.endswith(".py"):
            try:
                ast.parse(data, filename=path)
            except (SyntaxError, ValueError) as error:
                variant = path.startswith("admission/") and "syntax" in path
                issues.append(
                    _issue(
                        "python-syntax",
                        f"{type(error).__name__}: {str(error)[:100]}",
                        path,
                        "warning" if variant else "error",
                    )
                )
    oracle = quality = None
    try:
        ExposureRights.model_validate_json(files["admission/exposure-rights.json"])
    except (KeyError, ValueError, ValidationError) as error:
        issues.append(
            _issue("exposure-rights-invalid", f"exposure-rights.json: {str(error)[:200]}")
        )
    try:
        oracle = parse_oracle(files["hidden/oracle.json"])
    except (KeyError, ValueError, ValidationError) as error:
        issues.append(_issue("oracle-invalid", f"hidden/oracle.json: {str(error)[:200]}"))
    try:
        quality = parse_quality_plan(files["hidden/quality-plan.yaml"])
    except (KeyError, ValueError, ValidationError, yaml.YAMLError) as error:
        issues.append(
            _issue("quality-plan-invalid", f"hidden/quality-plan.yaml: {str(error)[:200]}")
        )
    if oracle is not None:
        issues.extend(_check_oracle(oracle, files, manifest))
    if oracle is not None and quality is not None:
        issues.extend(_check_quality(oracle, quality, manifest, files))
    issues.extend(_check_fixtures(manifest, files))
    return ValidationReport(plugin_id=plugin_id, checks=tuple(checks), issues=tuple(issues))


def _check_oracle(
    oracle: PythonOracle, files: Mapping[str, bytes], manifest: Mapping[str, object]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for group in oracle.groups:
        declared = {case.case_id for case in group.cases}
        actual: set[str] = set()
        for rel in group.files:
            source = files.get(f"hidden/{rel}")
            if source is None:
                issues.append(
                    _issue("oracle-file-missing", "group file is not in the package", rel)
                )
                continue
            try:
                actual.update(discover_cases(rel, source))
            except SyntaxError:
                continue
        for case_id in sorted(declared - actual):
            issues.append(
                _issue("oracle-case-absent", f"{group.group_id}: no such test: {case_id}")
            )
        for case_id in sorted(actual - declared):
            issues.append(
                _issue(
                    "oracle-case-undeclared", f"{group.group_id}: test not in inventory: {case_id}"
                )
            )
    acceptance = manifest.get("acceptance")
    if isinstance(acceptance, Mapping):
        required_groups = {g.group_id for g in oracle.groups if g.required}
        if set(acceptance.get("required_test_group_ids", [])) != required_groups:
            issues.append(
                _issue(
                    "acceptance-mismatch",
                    "required_test_group_ids differ from required oracle groups",
                )
            )
        known_conditions = {c.condition_id for c in oracle.hard_conditions}
        if not set(acceptance.get("hard_condition_ids", [])) <= known_conditions:
            issues.append(_issue("hard-condition-unknown", "hard_condition_ids not in the oracle"))
    return issues


def _check_quality(
    oracle: PythonOracle,
    quality: PythonQualityPlan,
    manifest: Mapping[str, object],
    files: Mapping[str, bytes],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    plan = manifest.get("quality_plan")
    dimensions: set[str] = set()
    if isinstance(plan, Mapping):
        dimensions = {str(d) for d in plan.get("applicable_dimensions", [])}
        if set(plan.get("required_analyzers", [])) != set(quality.required_analyzers):
            issues.append(
                _issue("analyzer-mismatch", "required analyzers differ from the manifest")
            )
    opps = quality.opportunities
    if "security" in dimensions and "security_surface" not in quality.opportunity_tags:
        issues.append(
            _issue("security-without-surface", "security applies but no security_surface tag")
        )
    if "efficiency" in dimensions and quality.performance is None:
        issues.append(_issue("efficiency-without-workload", "efficiency applies but no workload"))
    if quality.performance is not None:
        if f"hidden/{quality.performance.workload_file}" not in files:
            issues.append(_issue("workload-missing", quality.performance.workload_file))
    if "robustness" in dimensions and not oracle.robustness_scenarios:
        issues.append(_issue("robustness-without-scenario", "robustness applies but no scenario"))
    if "idiomatic" in dimensions and not any(opps.get(i, 0) for i in IDIOM_ITEMS):
        issues.append(
            _issue("idiomatic-without-opportunity", "idiomatic applies without opportunity")
        )
    if "code_quality" in dimensions and not any(opps.get(i, 0) for i in DIAGNOSTIC_ITEMS):
        issues.append(
            _issue("quality-without-opportunity", "code_quality applies without opportunity")
        )
    for item in quality.judge_items:
        if item not in set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS) | {
            "naming_readability",
            "decomposition",
            "duplication",
            "unnecessary_complexity",
            "repository_style_consistency",
            "minimal_relevant_scope",
        }:
            issues.append(_issue("judge-item-unknown", item))
    return issues


def _check_fixtures(
    manifest: Mapping[str, object], files: Mapping[str, bytes]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        return [_issue("fixtures-missing", "manifest has no fixtures list")]
    variants = {str(f.get("variant")) for f in fixtures if isinstance(f, Mapping)}
    for needed in ("reference", "faulty", "alternative", "quality_defective", "timeout"):
        if needed not in variants:
            issues.append(_issue("variant-missing", f"no {needed} fixture declared"))
    for fixture in fixtures:
        if not isinstance(fixture, Mapping):
            continue
        base = str(fixture.get("solution_path", ""))
        if not any(p == base or p.startswith(base.rstrip("/") + "/") for p in files):
            issues.append(
                _issue("fixture-solution-missing", "fixture solution is not in the package", base)
            )
    return issues


def oracle_from_mapping(document: Mapping[str, object]) -> PythonOracle:
    return PythonOracle.model_validate_json(json.dumps(document))


def quality_from_mapping(document: Mapping[str, object]) -> PythonQualityPlan:
    return PythonQualityPlan.model_validate_json(json.dumps(document))
