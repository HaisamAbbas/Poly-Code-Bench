"""C task package contracts: hidden oracle inventory, quality plan and task validation.

Layout (Technical Spec 11.1, C profile)::

    manifest.yaml
    visible/task.md   visible/repo/include/<task>.h   visible/repo/src/<task>.c
    hidden/oracle.json   hidden/quality-plan.yaml
    hidden/tests/<group>.c              hidden/reference/src/<task>.c
    hidden/perf/<workload>.c            admission/<variant>/src/<task>.c
    admission/exposure-rights.json

C differs from the Rust contract in four places that matter here:

* **No package manager.** A C task's dependency surface is its frozen headers and compile flags, so
  the analogue of ``Cargo.lock`` is a digest of the *flags* plus the harness sources. That is what
  ``recipe_digest`` records.
* **Warning policy is three-valued** (``werror`` / ``warn`` / ``error``) and ``werror`` is only
  legal when the frozen baseline compiled clean under the same flags. A blanket ``-Werror``
  must never invalidate an otherwise admitted legacy task (Architecture 11.2, PCB-20-3).
* **Instrumentation applicability is explicit and three-valued** per lane
  (``address`` / ``undefined`` / ``valgrind``): ``required`` / ``optional`` / ``unsupported``. Only
  ``required`` makes a lane mandatory; ``unsupported`` records that the lane cannot judge this task,
  so an absent scan is *not* a defect.
* **Case identity is ``<group>.<case>``** from an explicit ``PCB_CASE`` table, so a case id is
  readable in the test source and the oracle inventory is checked against exactly that.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import canonical_digest, parse_json_strict
from polycodebench_core.models import Digest, RelativePath, Slug
from polycodebench_plugins_api import PluginModel, TaskDraft, ValidationIssue, ValidationReport
from polycodebench_plugins_api.testreport import InventoryGroup, inventory_from_document
from pydantic import Field, ValidationError, model_validator

#: Item names from config/languages/profiles-v1.yaml#profiles.c.
DIAGNOSTIC_ITEMS = (
    "memory_safety",
    "undefined_behavior",
    "performance_cache",
    "const_ownership",
    "portability",
    "error_checks",
)
IDIOM_ITEMS = ("ownership_api_contracts", "const_type_portability", "data_function_interfaces")
KNOWN_ANALYZERS = ("clang_tidy", "cppcheck", "asan", "ubsan", "valgrind")
#: A lane is a *capability*; an analyzer is the *tool* that provides it. Address safety and
#: undefined behaviour are two lanes served by two sanitizers, and the two names are
#: deliberately different: conflating them would make it impossible to require memcheck
#: without also demanding a sanitizer that cannot judge the task.
LANE_ANALYZER = {"address": "asan", "undefined": "ubsan", "valgrind": "valgrind"}
LANE_NAMES = tuple(LANE_ANALYZER)
Applicability = Literal["required", "optional", "unsupported"]
WarningMode = Literal["werror", "warn", "error"]
VARIANTS = ("reference", "faulty", "alternative", "quality_defective", "timeout")
SUPPORTED_STANDARDS = ("c11", "c17", "c23")
WARNING_SET_NAMES = ("none", "minimal", "standard", "strict")


class OracleCase(PluginModel):
    kind: Literal["oracle_case"] = "oracle_case"
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    case_kind: Literal["example", "property", "robustness", "edge"]
    predeclared_skip: bool = False


class OracleGroup(PluginModel):
    """One group of cases, compiled and linked as a single test binary."""

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
        for case in self.cases:
            if not case.case_id.startswith(f"{self.group_id}."):
                raise ValueError(
                    f"case id {case.case_id!r} must be qualified by its group {self.group_id!r}"
                )
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


class COracle(PluginModel):
    kind: Literal["c_task_oracle"] = "c_task_oracle"
    oracle_version: Slug
    groups: tuple[OracleGroup, ...] = Field(min_length=1)
    hard_conditions: tuple[HardCondition, ...] = ()
    robustness_scenarios: tuple[RobustnessScenario, ...] = ()
    # C has no per-case alarm, so this is a declared budget the suite deadline enforces.
    case_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # At least 30s: the in-guest runner stops 3s before the supervisor's deadline.
    suite_timeout_seconds: int = Field(default=90, ge=30, le=110)

    @model_validator(mode="after")
    def unique_references(self) -> COracle:
        ids = [group.group_id for group in self.groups]
        if len(set(ids)) != len(ids):
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


class WarningPolicy(PluginModel):
    """The task's frozen answer to "may a warning fail this build?".

    ``baseline_warning_clean`` is the record that makes ``werror`` honest: it says the *frozen*
    baseline compiled with no warning under exactly this flag set. Without it, ``werror`` would
    silently invalidate an otherwise admitted task whose repository already carried debt.
    """

    kind: Literal["warning_policy"] = "warning_policy"
    mode: WarningMode = "warn"
    warning_set: str = "standard"
    baseline_warning_clean: bool = False
    baseline_warnings: int = Field(default=0, ge=0)
    note: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def werror_needs_a_clean_baseline(self) -> WarningPolicy:
        if self.warning_set not in WARNING_SET_NAMES:
            raise ValueError(f"unknown warning set {self.warning_set!r}")
        if self.mode == "werror" and not self.baseline_warning_clean:
            raise ValueError(
                "mode=werror requires baseline_warning_clean=true; a blanket -Werror cannot be "
                "imposed on a repository whose frozen baseline carries warnings"
            )
        if self.mode != "werror" and not self.baseline_warnings:
            # Not an error: a clean baseline under `warn` is the normal pilot case.
            pass
        return self

    @property
    def warnings_as_errors(self) -> bool:
        return self.mode == "werror"


class SanitizerPolicy(PluginModel):
    """Three-valued applicability for one dynamic lane.

    ``unsupported`` is a real answer for C: a task that uses inline assembly, a custom allocator or
    threads cannot be judged by AddressSanitizer, and pretending otherwise would either crash the
    lane or report a misleading clean result.
    """

    kind: Literal["sanitizer_policy"] = "sanitizer_policy"
    lane: Literal["address", "undefined", "valgrind"]
    applicability: Applicability = "unsupported"
    groups: tuple[Slug, ...] = ()
    reason: str = Field(default="", max_length=400)


class CQualityPlan(PluginModel):
    """What quality evidence a task expects.

    ``recipe_digest`` and ``scaffold_files`` are not authored: ``freeze_view`` computes them
    so plans can declare digest-checked inputs and a flag-addressed tool identity.
    """

    kind: Literal["c_task_quality_plan"] = "c_task_quality_plan"
    c_standard: str = "c17"
    warning_policy: WarningPolicy = Field(default_factory=WarningPolicy)
    sanitizers: tuple[SanitizerPolicy, ...] = ()
    opportunity_tags: tuple[Slug, ...] = ()
    opportunities: Mapping[str, int] = Field(default_factory=dict)
    required_analyzers: tuple[Slug, ...] = ()
    performance: PerformanceDecl | None = None
    judge_items: tuple[Slug, ...] = ()
    recipe_digest: Digest | None = None
    scaffold_files: Mapping[str, Digest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_items(self) -> CQualityPlan:
        if self.c_standard not in SUPPORTED_STANDARDS:
            raise ValueError(f"unsupported C standard {self.c_standard!r}")
        allowed = set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS)
        for name, count in self.opportunities.items():
            if name not in allowed or count < 0:
                raise ValueError(f"unknown opportunity item or negative count: {name}")
        unknown = [a for a in self.required_analyzers if a not in KNOWN_ANALYZERS]
        if unknown:
            raise ValueError(f"unknown required analyzer: {unknown}")
        lanes = [policy.lane for policy in self.sanitizers]
        if len(set(lanes)) != len(lanes):
            raise ValueError("a sanitizer lane is declared once")
        required_lanes = {
            policy.lane for policy in self.sanitizers if policy.applicability == "required"
        }
        required_analyzers = {
            LANE_ANALYZER[lane] for lane in required_lanes if lane in LANE_ANALYZER
        }
        if not required_analyzers <= set(self.required_analyzers):
            raise ValueError(
                "a sanitizer with applicability=required must also be listed in required_analyzers"
            )
        if "address" in required_lanes or "undefined" in required_lanes:
            unsupported = {
                policy.lane
                for policy in self.sanitizers
                if policy.lane in {"address", "undefined"} and policy.applicability == "unsupported"
            }
            if unsupported:
                raise ValueError(
                    f"lane cannot be both required and unsupported: {sorted(unsupported)}"
                )
        for policy in self.sanitizers:
            if policy.applicability != "unsupported" and not policy.groups:
                raise ValueError(f"lane {policy.lane} must name the oracle groups it interprets")
        return self

    def lane(self, name: str) -> SanitizerPolicy | None:
        for policy in self.sanitizers:
            if policy.lane == name:
                return policy
        return None

    @property
    def active_lanes(self) -> tuple[SanitizerPolicy, ...]:
        """Lanes that get a plan at all. ``unsupported`` lanes never do."""
        return tuple(p for p in self.sanitizers if p.applicability != "unsupported")

    @property
    def required_lanes(self) -> frozenset[str]:
        return frozenset(p.lane for p in self.sanitizers if p.applicability == "required")


def parse_oracle(data: bytes) -> COracle:
    return COracle.model_validate_json(json.dumps(parse_json_strict(data)))


def parse_quality_plan(data: bytes) -> CQualityPlan:
    return CQualityPlan.model_validate_json(json.dumps(yaml.safe_load(data)))


def oracle_from_mapping(document: Mapping[str, object]) -> COracle:
    return COracle.model_validate_json(json.dumps(document))


def quality_from_mapping(document: Mapping[str, object]) -> CQualityPlan:
    return CQualityPlan.model_validate_json(json.dumps(document))


# ------------------------------------------------------------------------ test discovery

_PCB_CASE = re.compile(r"PCB_CASE\(\s*\"(?P<id>[A-Za-z_][\w.]{0,200})\"")
_GROUP = re.compile(r"PCB_GROUP\(\s*\"(?P<group>[a-z0-9][\w.-]{0,120})\"")


def discover_cases(path: str, source: bytes) -> list[str]:
    """Case ids declared by ``PCB_CASE`` entries in one test file.

    Ids are qualified by the group the file declares, exactly as the binary prints them.
    Comments and directives are blanked but *literals are not*: the id is the payload, and a
    test table that named its cases in comments would be one an author could silently drift
    out of sync with.

    A file with no ``PCB_GROUP`` yields ids qualified by its own stem, so a package whose group is
    declared elsewhere is still discoverable rather than silently empty.
    """
    from polycodebench_lang_c.symbols import strip_comments

    text = strip_comments(source.decode("utf-8", errors="replace"))
    group_match = _GROUP.search(text)
    group = group_match.group("group") if group_match else _stem(path)
    found: list[str] = []
    for match in _PCB_CASE.finditer(text):
        case = match.group("id")
        found.append(case if case.startswith(f"{group}.") else f"{group}.{case}")
    return found


def _stem(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".c")


def discover_groups(path: str, source: bytes) -> list[str]:
    from polycodebench_lang_c.symbols import strip_comments

    text = strip_comments(source.decode("utf-8", errors="replace"))
    return [match.group("group") for match in _GROUP.finditer(text)]


# ------------------------------------------------------------------------- validation


def _issue(
    code: str, message: str, path: str | None = None, severity: str = "error"
) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, path=path, message=message[:500])  # type: ignore[arg-type]


def validate_c_task(task: TaskDraft, plugin_id: str = "c") -> ValidationReport:
    """Structural and executable-contract validation that needs no sandbox."""
    issues: list[ValidationIssue] = []
    checks = [
        "language",
        "layout",
        "scaffold-identity",
        "oracle-inventory",
        "acceptance-links",
        "quality-plan",
        "warning-policy",
        "dimension-evidence",
        "variant-files",
        "exposure-rights",
    ]
    manifest = task.manifest
    files = task.files
    if task.primary_language != "c":
        issues.append(_issue("wrong-language", "primary_language must be c"))
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
    if not any(o.endswith(".c") for o in outputs):
        issues.append(_issue("outputs", "a C task needs at least one required .c output"))
    for output in outputs:
        if f"hidden/reference/{output}" not in files:
            issues.append(_issue("missing-reference", "reference for a required output", output))
    if not any(p.startswith("hidden/tests/") and p.endswith(".c") for p in files):
        issues.append(_issue("no-hidden-tests", "hidden/tests contains no .c files"))
    if not any(p.startswith("visible/repo/include/") for p in files):
        issues.append(
            _issue("no-public-header", "a C task needs a frozen header under visible/repo/include")
        )
    try:
        ExposureRights.model_validate_json(files["admission/exposure-rights.json"])
    except (KeyError, ValueError, ValidationError) as error:
        issues.append(
            _issue("exposure-rights-invalid", f"exposure-rights.json: {str(error)[:200]}")
        )
    oracle = quality = None
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
    oracle: COracle, files: Mapping[str, bytes], manifest: Mapping[str, object]
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
                _issue("oracle-case-absent", f"{group.group_id}: no such case: {case_id}")
            )
        for case_id in sorted(actual - declared):
            issues.append(
                _issue(
                    "oracle-case-undeclared", f"{group.group_id}: case not in inventory: {case_id}"
                )
            )
    acceptance = manifest.get("acceptance")
    if isinstance(acceptance, Mapping):
        required_groups = {g.group_id for g in oracle.groups if g.required}
        if set(acceptance.get("required_test_group_ids", [])) != required_groups:
            issues.append(
                _issue("acceptance-mismatch", "required_test_group_ids differ from required groups")
            )
        known = {c.condition_id for c in oracle.hard_conditions}
        if not set(acceptance.get("hard_condition_ids", [])) <= known:
            issues.append(_issue("hard-condition-unknown", "hard_condition_ids not in the oracle"))
    return issues


def _check_quality(
    oracle: COracle,
    quality: CQualityPlan,
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
    known_groups = {group.group_id for group in oracle.groups}
    for policy in quality.sanitizers:
        unknown = set(policy.groups) - known_groups
        if unknown:
            issues.append(
                _issue(
                    "lane-group-unknown",
                    f"lane {policy.lane} names unknown groups {sorted(unknown)}",
                )
            )
        if policy.lane in {"address", "undefined"} and policy.applicability != "unsupported":
            item = "memory_safety" if policy.lane == "address" else "undefined_behavior"
            if not opps.get(item, 0):
                issues.append(
                    _issue(
                        "lane-without-opportunity",
                        f"lane {policy.lane} needs a {item} opportunity to feed",
                    )
                )
    if "memory_safety" in dimensions and quality.lane("address") is None:
        issues.append(
            _issue(
                "memory-safety-without-lane",
                "memory_safety applies but no address lane is declared",
            )
        )
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


def scaffold_digest(files: Mapping[str, bytes], outputs: set[str]) -> dict[str, str]:
    """Digest-checked ``config`` inputs: everything the candidate does not write itself."""
    return {
        path.removeprefix("visible/repo/"): "sha256:" + hashlib.sha256(data).hexdigest()
        for path, data in sorted(files.items())
        if path.startswith("visible/repo/") and path.removeprefix("visible/repo/") not in outputs
    }
