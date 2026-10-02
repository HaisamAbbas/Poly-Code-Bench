"""Go task package contracts: hidden oracle inventory, quality plan and task validation.

Layout (Technical Spec 11.1, Go profile)::

    manifest.yaml
    visible/task.md  visible/repo/go.mod  visible/repo/go.sum  visible/repo/<pkg>/*.go
    hidden/oracle.json  hidden/quality-plan.yaml  hidden/tests/*_test.go  hidden/reference/<pkg>/*.go
    admission/<variant>/...            (faulty, alternative, quality-defective, timeout solutions)
    admission/exposure-rights.json

The Go contract differs from Rust's in three places that matter here:

* **No typing expectation.** Go is statically typed with no annotations, so there is nothing
  analogous to "does this task expect types". Instead a task declares whether the *race detector*
  applies, which is the only Go-specific precondition for a quality dimension.
* **Race applicability is explicit and three-valued** (``required`` / ``optional`` /
  ``unsupported``). Only ``required`` makes a race plan mandatory. ``unsupported`` records that the
  task has no shared-state concurrency to detect, so an absent scan is *not* a defect and the
  concurrency items stay ``not_applicable``; ``optional`` means the scan runs when the task permits
  it but never gates on it.
* **Test identity is a testing-package test name** (``TestTiesBreakAlphabetically``), which is what
  the JSON event stream reports, so case ids are matched against the names the runner actually
  prints. Subtests (``TestX/sub``) are runtime children of their parent and are not declared
  separately.

Everything else - groups, cases, robustness scenarios, exposure and rights records - uses the same
shapes as the shared contracts so the supervisor and the profile stay language-agnostic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import canonical_digest, parse_json_strict
from polycodebench_core.models import Digest, RelativePath, Slug
from polycodebench_plugins_api import (
    PluginModel,
    TaskDraft,
    ValidationIssue,
    ValidationReport,
)
from polycodebench_plugins_api.testreport import InventoryGroup, inventory_from_document
from pydantic import Field, ValidationError, model_validator

from polycodebench_lang_go.locks import LockError, module_digest
from polycodebench_lang_go.symbols import sanitize

# Item names are the profile's (config/languages/profiles-v1.yaml#profiles.go).
DIAGNOSTIC_ITEMS = (
    "error_handling",
    "goroutines_channels",
    "cancellation_context",
    "simple_interfaces",
    "standard_library",
    "formatting_vet_staticcheck",
)
IDIOM_ITEMS = (
    "simple_interfaces_api",
    "standard_library_composition",
    "error_api",
    "context_concurrency",
)
KNOWN_ANALYZERS = ("gofmt", "vet", "staticcheck", "gosec", "context", "race", "dependency")
RaceApplicability = Literal["required", "optional", "unsupported"]
VARIANTS = ("reference", "faulty", "alternative", "quality_defective", "timeout")


class OracleCase(PluginModel):
    kind: Literal["oracle_case"] = "oracle_case"
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    case_kind: Literal["example", "property", "robustness", "edge"]
    predeclared_skip: bool = False


class OracleGroup(PluginModel):
    """One group of cases, run as one ``go test`` invocation over its test files."""

    kind: Literal["oracle_group"] = "oracle_group"
    group_id: Slug
    required: bool
    classification: Literal["acceptance", "quality_only"]
    files: tuple[RelativePath, ...] = Field(min_length=1)
    cases: tuple[OracleCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> OracleGroup:
        ids = [case.case_id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("oracle group declares the same case twice")
        return self


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


class GoOracle(PluginModel):
    kind: Literal["go_task_oracle"] = "go_task_oracle"
    oracle_version: Slug
    groups: tuple[OracleGroup, ...] = Field(min_length=1)
    hard_conditions: tuple[HardCondition, ...] = ()
    robustness_scenarios: tuple[RobustnessScenario, ...] = ()
    # `testing` has no per-case alarm, so ``case_timeout_seconds`` is a budget the task author
    # declares and the suite deadline enforces; a hung case is attributed from partial output.
    case_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # At least 30s: the in-guest runner stops 3s before the supervisor's deadline and the supervisor
    # recognises a timeout only when the run lasted nearly the whole deadline.
    suite_timeout_seconds: int = Field(default=90, ge=30, le=110)

    @model_validator(mode="after")
    def unique_references(self) -> GoOracle:
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
        return str(canonical_digest(self.inventory_document()))


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
    hard_timeout_seconds: int = Field(default=30, ge=30, le=110)

    @model_validator(mode="after")
    def weights_sum(self) -> PerformanceDecl:
        if sum(item.weight_bp for item in self.workloads) != 10_000:
            raise ValueError("workload weights must sum to 10000 basis points")
        return self


class GoQualityPlan(PluginModel):
    """What quality evidence a task expects.

    ``race`` is the applicability switch for the concurrency dimension. A task with no
    shared-state concurrency declares ``unsupported`` and is then never penalised for a missing
    scan, and its concurrency items have no frozen opportunity so they read ``not_applicable``
    rather than perfect. A task that requires a race run must list ``race`` in
    ``required_analyzers``, which is what makes the plan mandatory rather than advisory.

    ``module_files`` and ``go_module_digest`` are not authored: ``freeze_view`` computes them from
    the package so plans can declare digest-checked inputs and a module-derived tool identity.
    """

    kind: Literal["go_task_quality_plan"] = "go_task_quality_plan"
    race: RaceApplicability = "unsupported"
    # Oracle groups whose tests are instrumented; empty means every required group. A race build
    # is expensive, so a task may name the small groups that exercise its concurrency.
    race_groups: tuple[Slug, ...] = ()
    opportunity_tags: tuple[Slug, ...] = ()
    opportunities: Mapping[str, int] = Field(default_factory=dict)
    required_analyzers: tuple[Slug, ...] = ()
    dependency_inventory: tuple[str, ...] = ()
    performance: PerformanceDecl | None = None
    judge_items: tuple[Slug, ...] = ()
    go_module_digest: Digest | None = None
    module_files: Mapping[str, Digest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_items(self) -> GoQualityPlan:
        allowed = set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS)
        for name, count in self.opportunities.items():
            if name not in allowed or count < 0:
                raise ValueError(f"unknown opportunity item or negative count: {name}")
        if any(a not in KNOWN_ANALYZERS for a in self.required_analyzers):
            raise ValueError("unknown required analyzer")
        if self.race == "required" and "race" not in self.required_analyzers:
            raise ValueError("race=required must also list race in required_analyzers")
        if self.race != "required" and "race" in self.required_analyzers:
            raise ValueError("race can only be a required analyzer when race=required")
        if self.race == "unsupported" and self.opportunities.get("goroutines_channels", 0):
            raise ValueError(
                "goroutines_channels opportunities need a race applicability of required or optional"
            )
        return self

    @property
    def race_runs(self) -> bool:
        """Whether a race plan is produced at all (``unsupported`` tasks never get one)."""
        return self.race != "unsupported"


def parse_oracle(data: bytes) -> GoOracle:
    return GoOracle.model_validate_json(json.dumps(parse_json_strict(data)))


def parse_quality_plan(data: bytes) -> GoQualityPlan:
    return GoQualityPlan.model_validate_json(json.dumps(yaml.safe_load(data)))


def oracle_from_mapping(document: Mapping[str, object]) -> GoOracle:
    return GoOracle.model_validate_json(json.dumps(document))


def quality_from_mapping(document: Mapping[str, object]) -> GoQualityPlan:
    return GoQualityPlan.model_validate_json(json.dumps(document))


# ------------------------------------------------------------------------ test discovery

_TEST = re.compile(r"^func\s+(?P<name>Test[A-Za-z0-9_]*)\s*\((?P<arg>[^)]*)\)")


def discover_cases(path: str, source: bytes) -> list[str]:
    """Case ids of every test function in a file (static, nothing is compiled).

    The id is the test name the ``testing`` package reports (``TestTiesBreakAlphabetically``),
    which is exactly what the JSON event stream carries. Methods are not test cases even when their
    name starts with ``Test``: the framework never runs them.
    """
    text = sanitize(source.decode("utf-8", errors="replace"))
    found: list[str] = []
    for line in text.splitlines():
        match = _TEST.match(line)
        if match is None:
            continue
        argument = match.group("arg")
        # `func Test(t *testing.T)` is a helper, not a test; the argument must be the framework.
        if "testing" not in argument and "testing.B" not in argument:
            continue
        found.append(match.group("name"))
    return found


# ------------------------------------------------------------------------- validation


def _issue(
    code: str, message: str, path: str | None = None, severity: str = "error"
) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, path=path, message=message[:500])  # type: ignore[arg-type]


def validate_go_task(task: TaskDraft, plugin_id: str = "go") -> ValidationReport:
    """Structural and executable-contract validation that needs no sandbox."""
    issues: list[ValidationIssue] = []
    checks = [
        "language",
        "layout",
        "module-identity",
        "oracle-inventory",
        "acceptance-links",
        "quality-plan",
        "dimension-evidence",
        "variant-files",
        "exposure-rights",
    ]
    manifest = task.manifest
    files = task.files
    if task.primary_language != "go":
        issues.append(_issue("wrong-language", "primary_language must be go"))
    required = [
        "visible/task.md",
        "visible/repo/go.mod",
        "visible/repo/go.sum",
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
    if not any(o.endswith(".go") for o in outputs):
        issues.append(_issue("outputs", "a Go task needs at least one required .go output"))
    for output in outputs:
        if f"hidden/reference/{output}" not in files:
            issues.append(_issue("missing-reference", "reference for a required output", output))
    # A Go test file lives beside the package it tests, so the oracle names its destination path
    # under the task root rather than a fixed `tests/` directory.
    if not any(p.startswith("hidden/") and p.endswith("_test.go") for p in files):
        issues.append(_issue("no-hidden-tests", "hidden/ contains no _test.go files"))
    mod = files.get("visible/repo/go.mod")
    if mod is not None:
        try:
            module_digest(mod, files.get("visible/repo/go.sum"))
        except LockError as error:
            issues.append(
                _issue("go-module-invalid", f"go.mod/go.sum do not pin a resolution: {error}")
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
    oracle: GoOracle, files: Mapping[str, bytes], manifest: Mapping[str, object]
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
            actual.update(discover_cases(rel, source))
        for case_id in sorted(declared - actual):
            issues.append(
                _issue("oracle-case-absent", f"{group.group_id}: no such test: {case_id}")
            )
        for case_id in sorted(actual - declared):
            issues.append(
                _issue("oracle-case-undeclared", f"{group.group_id}: test not in inventory: {case_id}")
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
    oracle: GoOracle,
    quality: GoQualityPlan,
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
    if (
        quality.performance is not None
        and f"hidden/{quality.performance.workload_file}" not in files
    ):
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
    if "dependency" in quality.required_analyzers and not quality.dependency_inventory:
        issues.append(
            _issue("dependency-without-inventory", "a dependency audit needs an inventory")
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
    for needed in VARIANTS:
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