"""Java task package contracts: hidden oracle inventory, quality plan and task validation.

Layout (Technical Spec 11.1, Java profile)::

    manifest.yaml
    visible/task.md  visible/repo/pom.xml  visible/repo/deps.lock.json
    visible/repo/src/main/java/...
    hidden/oracle.json  hidden/quality-plan.yaml  hidden/deps.lock.json
    hidden/tests/<Name>Test.java  hidden/reference/src/main/java/...
    admission/<variant>/...            (faulty, alternative, null-unsafe, resource-leak,
                                        concurrent, security-defective, timeout solutions)
    admission/exposure-rights.json

The Java contract differs from Python's and Rust's in four places that matter here:

* **No typing expectation.** Java is statically typed, so there is nothing analogous to Python's
  "does this task expect annotations"; what a task declares instead is which of the *resource*,
  *concurrency*, *null-safety*, *boundary* and *security-surface* opportunities it has, and those
  are the frozen counts every item is scored against.
* **Resource applicability is three-valued** (``required`` / ``optional`` / ``unsupported``), like
  Rust's Miri switch. A task that provably has no acquisition (pure computation over arguments)
  declares ``unsupported`` and is never penalised for a missing resource scan; ``required`` is what
  makes a resource probe mandatory rather than advisory.
* **A frozen dependency resolution is mandatory.** ``hidden/deps.lock.json`` is authored by
  admission tooling from ``mvn -o dependency:list`` and is the Java analogue of ``Cargo.lock``; its
  digest is part of the evaluator identity. A task without one is rejected, because a Java toolchain
  that does not pin what its POM resolved to cannot make two runs comparable.
* **Test identity is a JUnit 5 method**, qualified by its declaring class
  (``demo.TopWordsTest#tiesBreakAlphabetically``), because that is what the console launcher
  actually prints and what surefire's XML records.

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

from polycodebench_lang_java.locks import LockError, lock_digest
from polycodebench_lang_java.symbols import sanitize

# Item names are the profile's (config/languages/profiles-v1.yaml#profiles.java).
DIAGNOSTIC_ITEMS = (
    "design_boundaries",
    "resource_handling",
    "concurrency",
    "modern_apis",
    "null_safety",
    "security",
)
IDIOM_ITEMS = (
    "class_api_boundaries",
    "library_abstractions",
    "value_nullability",
    "resource_concurrency",
)
KNOWN_ANALYZERS = ("spotbugs", "pmd", "checkstyle", "context", "dependency")
#: Resource and concurrency probes are task-declared JUnit groups, not analyzer names. Their
#: evidence is an actual test outcome (Architecture 11.2: "Resource/concurrency tests are
#: necessary").
ResourceApplicability = Literal["required", "optional", "unsupported"]
#: The fixture variants a Java task must be admitted with (PCB-23-3). The Java set is wider than
#: Rust's because Java's failure modes are: a wrong answer, an alternative valid design, a null
#: contract violated, a resource leaked, shared state published unsafely, and a security defect.
VARIANTS = (
    "reference",
    "faulty",
    "alternative",
    "null_unsafe",
    "resource_leak",
    "concurrent_defect",
    "security_defective",
    "timeout",
)
#: Variants that must fail a *test*, as opposed to failing only on quality evidence.
CORRECTNESS_VARIANTS = ("faulty", "timeout")


class OracleCase(PluginModel):
    kind: Literal["oracle_case"] = "oracle_case"
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    case_kind: Literal["example", "property", "robustness", "edge"]
    predeclared_skip: bool = False


class OracleGroup(PluginModel):
    """One group of cases, run as one ``mvn -o test`` invocation over its test classes."""

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

    @property
    def selectors(self) -> tuple[str, ...]:
        """``-Dtest=`` selectors for this group's classes (fully-qualified, JUnit 5)."""
        return tuple(
            path.replace("\\", "/").removeprefix("tests/").removesuffix(".java").replace("/", ".")
            for path in self.files
        )


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


class JavaOracle(PluginModel):
    kind: Literal["java_task_oracle"] = "java_task_oracle"
    oracle_version: Slug
    groups: tuple[OracleGroup, ...] = Field(min_length=1)
    hard_conditions: tuple[HardCondition, ...] = ()
    robustness_scenarios: tuple[RobustnessScenario, ...] = ()
    # JUnit has no per-case alarm, so ``case_timeout_seconds`` is a budget the task author declares
    # and the suite deadline enforces; a hung case is attributed from surefire's own record.
    case_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # At least 30s: the in-guest runner stops 3s before the supervisor's deadline and the supervisor
    # recognises a timeout only when the run lasted nearly the whole deadline. Maven itself needs
    # ~13s of JVM start-up on the pinned image, so the floor is higher than a bare binary's.
    suite_timeout_seconds: int = Field(default=90, ge=30, le=110)

    @model_validator(mode="after")
    def unique_references(self) -> JavaOracle:
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
    """The frozen measurement contract for one task (PCB-23-1).

    ``measurement_mode`` selects one of the two flag sets frozen into the performance image
    (``java-<mode>`` / ``java-steady-state``). It is a declared, admission-frozen field: the
    iteration plan reads it and nothing else, and no code path may substitute a mode based on what
    a candidate did or how fast it looked. ``warmup_iterations`` is likewise declared here, so the
    JIT warmup budget cannot grow or shrink to suit an individual candidate.
    """

    kind: Literal["performance_decl"] = "performance_decl"
    workload_file: RelativePath
    workloads: tuple[WorkloadDecl, ...] = Field(min_length=1)
    #: One of the modes baked into the image. ``unknown`` here is an error, not a default.
    measurement_mode: Literal["cold", "steady_state"] = "steady_state"
    warmup_iterations: int = Field(default=20, ge=0, le=100)
    measured_iterations: int = Field(default=20, ge=1, le=1000)
    hard_timeout_seconds: int = Field(default=60, ge=30, le=110)

    @model_validator(mode="after")
    def weights_sum(self) -> PerformanceDecl:
        if sum(item.weight_bp for item in self.workloads) != 10_000:
            raise ValueError("workload weights must sum to 10000 basis points")
        if self.measurement_mode == "cold" and self.warmup_iterations != 0:
            raise ValueError("cold mode must declare zero warmup iterations")
        if self.measurement_mode == "steady_state" and self.warmup_iterations < 20:
            raise ValueError("steady_state mode requires at least 20 fixed warmup iterations")
        return self


class JavaQualityPlan(PluginModel):
    """What quality evidence a task expects.

    ``resource`` is the applicability switch for the resource dimension, exactly as Rust's ``miri``
    is for undefined behaviour. ``resources`` and ``concurrency`` being *required* is what makes the
    corresponding probes mandatory; a task that provably has no acquisition declares ``unsupported``
    and is never penalised for the absence.

    ``pinned_files`` and ``dependency_lock_digest`` are not authored: ``freeze_view`` computes them
    from the package so plans can declare digest-checked inputs and a lock-derived tool identity.
    """

    kind: Literal["java_task_quality_plan"] = "java_task_quality_plan"
    resources: ResourceApplicability = "unsupported"
    concurrency: ResourceApplicability = "unsupported"
    security_surface: ResourceApplicability = "unsupported"
    # Oracle groups whose tests the threaded concurrency probe drives; empty means every required
    # group. Interpreting a whole suite under stress is slow, so a task may name the small groups
    # that actually exercise shared state.
    concurrency_groups: tuple[Slug, ...] = ()
    resource_groups: tuple[Slug, ...] = ()
    opportunity_tags: tuple[Slug, ...] = ()
    opportunities: Mapping[str, int] = Field(default_factory=dict)
    required_analyzers: tuple[Slug, ...] = ()
    dependency_inventory: tuple[str, ...] = ()
    performance: PerformanceDecl | None = None
    judge_items: tuple[Slug, ...] = ()
    dependency_lock_digest: Digest | None = None
    pinned_files: Mapping[str, Digest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_items(self) -> JavaQualityPlan:
        allowed = set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS)
        for name, count in self.opportunities.items():
            if name not in allowed or count < 0:
                raise ValueError(f"unknown opportunity item or negative count: {name}")
        if any(a not in KNOWN_ANALYZERS for a in self.required_analyzers):
            raise ValueError("unknown required analyzer")
        if self.resources == "required" and "resource_handling" not in self.opportunities:
            raise ValueError("resources=required needs a resource_handling opportunity")
        if self.resources == "required" and not self.resource_groups:
            raise ValueError("resources=required needs a task-specific resource test group")
        if self.concurrency == "required" and "concurrency" not in self.opportunities:
            raise ValueError("concurrency=required needs a concurrency opportunity")
        if self.concurrency == "required" and not self.concurrency_groups:
            raise ValueError("concurrency=required needs a task-specific concurrency test group")
        if self.security_surface == "required" and "security_surface" not in self.opportunity_tags:
            raise ValueError("security_surface=required needs the security_surface tag")
        if self.security_surface == "required" and "context" not in self.required_analyzers:
            raise ValueError("security_surface=required needs the contextual security probe")
        if self.concurrency == "unsupported" and self.concurrency_groups:
            raise ValueError("concurrency=unsupported cannot name concurrency groups")
        return self

    @property
    def resource_runs(self) -> bool:
        """Whether a resource probe is produced at all (``unsupported`` tasks never get one)."""
        return self.resources != "unsupported"

    @property
    def concurrency_runs(self) -> bool:
        return self.concurrency != "unsupported"

    @property
    def security_runs(self) -> bool:
        return self.security_surface != "unsupported"


def parse_oracle(data: bytes) -> JavaOracle:
    return JavaOracle.model_validate_json(json.dumps(parse_json_strict(data)))


def parse_quality_plan(data: bytes) -> JavaQualityPlan:
    return JavaQualityPlan.model_validate_json(json.dumps(yaml.safe_load(data)))


def oracle_from_mapping(document: Mapping[str, object]) -> JavaOracle:
    return JavaOracle.model_validate_json(json.dumps(document))


def quality_from_mapping(document: Mapping[str, object]) -> JavaQualityPlan:
    return JavaQualityPlan.model_validate_json(json.dumps(document))


# ------------------------------------------------------------------------ test discovery

_ANNOTATED_METHOD = re.compile(
    r"(?m)^[ \t]*(?P<annotations>(?:@[\w.]+(?:\s*\([^)]*\))?[ \t]*(?:\r?\n[ \t]*)?)+)"
    r"(?:(?:public|protected|private|static|final|synchronized|default)\s+)*"
    r"(?:void|boolean|int|long|double|float|char|byte|short|[\w.< >\[\]?]+)"
    r"\s+(?P<name>[A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:throws [\w., ]+)?\s*\{"
)
_CLASS = re.compile(r"\b(?:class|interface|enum|record)\s+(?P<name>[A-Za-z_]\w*)")
_TEST_SOURCES = ("org/junit/jupiter/api/Test", "org.junit.jupiter.api.Test")


def discover_cases(path: str, source: bytes) -> list[str]:
    """Case ids of every ``@Test`` method in a test class (static, nothing is compiled).

    The id is ``<fully-qualified-class>#<method>``, the identity surefire's XML records and the
    console launcher prints. A ``@Test`` that is also ``@Disabled`` is still discovered: it must be
    declared in the oracle, as skipped, so the inventory stays exact.
    """
    text = sanitize(source.decode("utf-8", errors="replace"))
    if not any(marker in source.decode("utf-8", errors="replace") for marker in _TEST_SOURCES):
        return []
    # A file may declare a package and more than one top-level class; the package qualifies all of
    # them, and surefire records them by the same fully-qualified name.
    package_match = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
    package = package_match.group(1) if package_match else ""
    stem = path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".java")
    found: list[str] = []
    for cls in _CLASS.finditer(text):
        qualified = f"{package}.{cls.group('name')}" if package else cls.group("name")
        # Method discovery is bounded by this class's braces.
        start = text.find("{", cls.end())
        if start == -1:
            continue
        depth = 0
        end = len(text)
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        body = text[start:end]
        for match in _ANNOTATED_METHOD.finditer(body):
            if re.search(r"(?:^|\s)@Test(?:\s|\(|$)", match.group("annotations")):
                found.append(f"{qualified}#{match.group('name')}")
        if not found:
            # The class had no @Test; keep the stem so the caller reports a real mismatch.
            continue
        if stem and not found[-1].startswith(qualified):
            continue
    if not found:
        return []
    # Preserve file order for reproducible error messages.
    ordered: list[str] = []
    for case in found:
        if case not in ordered:
            ordered.append(case)
    return ordered


# ------------------------------------------------------------------------- validation


def _issue(
    code: str, message: str, path: str | None = None, severity: str = "error"
) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, path=path, message=message[:500])  # type: ignore[arg-type]


def validate_java_task(task: TaskDraft, plugin_id: str = "java") -> ValidationReport:
    """Structural and executable-contract validation that needs no sandbox."""
    issues: list[ValidationIssue] = []
    checks = [
        "language",
        "layout",
        "dependency-identity",
        "oracle-inventory",
        "acceptance-links",
        "quality-plan",
        "dimension-evidence",
        "variant-files",
        "exposure-rights",
    ]
    manifest = task.manifest
    files = task.files
    if task.primary_language != "java":
        issues.append(_issue("wrong-language", "primary_language must be java"))
    required = [
        "visible/task.md",
        "visible/repo/pom.xml",
        "hidden/oracle.json",
        "hidden/quality-plan.yaml",
        "hidden/deps.lock.json",
        "admission/exposure-rights.json",
    ]
    acceptance = manifest.get("acceptance")
    outputs: list[str] = []
    if isinstance(acceptance, Mapping):
        outputs = [str(o) for o in acceptance.get("required_outputs", [])]
    for path in required:
        if path not in files:
            issues.append(_issue("missing-file", "required task file is absent", path))
    if not any(o.endswith(".java") for o in outputs):
        issues.append(_issue("outputs", "a Java task needs at least one required .java output"))
    if not any(o.startswith("src/main/java/") for o in outputs):
        issues.append(
            _issue("outputs-not-in-source-root", "required outputs must live under src/main/java/")
        )
    for output in outputs:
        if f"hidden/reference/{output}" not in files:
            issues.append(_issue("missing-reference", "reference for a required output", output))
    if not any(p.startswith("hidden/tests/") and p.endswith(".java") for p in files):
        issues.append(_issue("no-hidden-tests", "hidden/tests contains no .java files"))
    lock = files.get("hidden/deps.lock.json")
    if lock is not None:
        try:
            lock_digest(lock)
        except LockError as error:
            issues.append(
                _issue("deps-lock-invalid", f"deps.lock.json does not pin a resolution: {error}")
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
    oracle: JavaOracle, files: Mapping[str, bytes], manifest: Mapping[str, object]
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
        if not actual:
            issues.append(
                _issue("oracle-no-cases", f"{group.group_id}: no @Test methods discovered")
            )
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
    oracle: JavaOracle,
    quality: JavaQualityPlan,
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
    known_groups = {group.group_id for group in oracle.groups}
    for name in (*quality.concurrency_groups, *quality.resource_groups):
        if name not in known_groups:
            issues.append(_issue("probe-group-unknown", f"probe names an unknown group: {name}"))
    groups = {group.group_id: group for group in oracle.groups}
    for name in quality.resource_groups:
        if name in groups and groups[name].classification != "quality_only":
            issues.append(
                _issue("resource-group-not-quality", "resource probes must be quality-only", name)
            )
    for name in quality.concurrency_groups:
        if name in groups and groups[name].classification != "quality_only":
            issues.append(
                _issue(
                    "concurrency-group-not-quality",
                    "concurrency stress probes must be quality-only",
                    name,
                )
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
    # `variant` is the shared admission role (faulty / quality_defective); `language_variant`
    # preserves Java's richer authored-fixture subtype without forking the core admission schema.
    variants = {
        str(f.get("language_variant", f.get("variant"))) for f in fixtures if isinstance(f, Mapping)
    }
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
