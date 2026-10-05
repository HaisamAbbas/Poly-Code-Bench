"""C++ task-package contracts: oracle, quality plan, pinned recipe, exposure rights, validation.

Layout of a C++ task package::

    manifest.yaml
    visible/task.md
    visible/repo/include/<header>.hpp      # a required output the candidate replaces
    visible/repo/src/<unit>.cpp            # a required output the candidate replaces
    hidden/oracle.json                     # test inventory, hard conditions, robustness scenarios
    hidden/quality-plan.yaml               # opportunities, analyzers, instrumentation, performance
    hidden/recipe.json                     # pinned compiler / standard / build profile
    hidden/tests/<group>.cpp               # hidden acceptance and quality-only groups
    hidden/reference/<...>                 # the reference solution
    hidden/perf/workload.cpp               # optional performance workload
    admission/<variant>/<...>              # candidate variants used by admission
    admission/exposure-rights.json

Two rules make C++ different from every other language here. First, the recipe is a *hidden*,
digest-pinned input: a candidate may not see which standard or build profile will compile it,
because a candidate that adapts to the recipe is not being measured on the recipe. Second,
instrumentation is declared once per task and validated for compatibility before anything runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import Slug
from polycodebench_plugins_api import TaskDraft, ValidationIssue, ValidationReport
from polycodebench_plugins_api.contracts import PluginModel
from polycodebench_plugins_api.testreport import InventoryGroup, inventory_from_document
from pydantic import Field, model_validator

from polycodebench_lang_cpp.locks import LockError, Sanitizer, ToolchainLock, load_lock

DIAGNOSTIC_ITEMS = (
    "raii_ownership",
    "moves_copies",
    "stl_use",
    "exception_safety",
    "modern_features",
    "abstraction_performance",
    "undefined_behavior_memory_safety",
)
IDIOM_ITEMS = (
    "ownership_raii_design",
    "stl_container_choice",
    "value_move_api_semantics",
    "modern_constructs",
)
KNOWN_ANALYZERS = ("clang_tidy", "cppcheck", "context", "asan", "tsan")
# Which sanitizers each instrumented analyzer needs. `asan` reports the address *and* the
# undefined-behaviour detector because they share one build profile and one runtime.
SANITIZER_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "asan": ("address", "undefined"),
    "tsan": ("thread",),
}
Instrumentation = Literal["supported", "unsupported"]
VARIANTS = ("reference", "faulty", "alternative", "quality_defective", "timeout")
CANDIDATE_SUFFIXES = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")
CHECKS = (
    "language",
    "package-files",
    "recipe-identity",
    "oracle-inventory",
    "acceptance-links",
    "quality-plan",
    "dimension-evidence",
    "variant-files",
    "exposure-rights",
)
JUDGE_ITEMS = frozenset(
    {
        "naming_readability",
        "decomposition",
        "duplication",
        "unnecessary_complexity",
        "repository_style_consistency",
        "minimal_relevant_scope",
    }
)
_CASE_MACROS = frozenset({"PCB_CHECK", "PCB_CHECKF", "PCB_SKIP"})


# --------------------------------------------------------------------------- oracle


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
    files: tuple[str, ...] = Field(min_length=1)
    cases: tuple[OracleCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> OracleGroup:
        ids = [case.case_id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError(f"group {self.group_id} repeats a case id")
        return self


class HardCondition(PluginModel):
    kind: Literal["hard_condition"] = "hard_condition"
    condition_id: Slug
    group_id: Slug


class RobustnessScenario(PluginModel):
    kind: Literal["robustness_scenario"] = "robustness_scenario"
    scenario_id: Slug
    group_id: Slug
    weight_bp: int = Field(ge=1, le=10000)
    repetitions: int = Field(default=3, ge=1, le=10)
    hard_acceptance: bool = False


class CppOracle(PluginModel):
    kind: Literal["cpp_task_oracle"] = "cpp_task_oracle"
    oracle_version: str
    groups: tuple[OracleGroup, ...] = Field(min_length=1)
    hard_conditions: tuple[HardCondition, ...] = ()
    robustness_scenarios: tuple[RobustnessScenario, ...] = ()
    case_timeout_seconds: int = Field(default=10, ge=1, le=120)
    suite_timeout_seconds: int = Field(default=90, ge=30, le=110)

    @model_validator(mode="after")
    def consistent(self) -> CppOracle:
        groups = [group.group_id for group in self.groups]
        if len(set(groups)) != len(groups):
            raise ValueError("oracle repeats a group id")
        known = set(groups)
        if any(condition.group_id not in known for condition in self.hard_conditions):
            raise ValueError("a hard condition names an unknown group")
        if any(scenario.group_id not in known for scenario in self.robustness_scenarios):
            raise ValueError("a robustness scenario names an unknown group")
        total = sum(scenario.weight_bp for scenario in self.robustness_scenarios)
        if self.robustness_scenarios and total != 10000:
            raise ValueError(f"robustness scenario weights sum to {total}, not 10000")
        return self

    def inventory_document(self) -> dict[str, object]:
        return {
            "groups": [
                {
                    "group_id": group.group_id,
                    "required": group.required,
                    "cases": [
                        {
                            "case_id": case.case_id,
                            "required": case.required,
                            "predeclared_skip": case.predeclared_skip,
                        }
                        for case in group.cases
                    ],
                }
                for group in self.groups
            ],
        }

    def inventory(self) -> tuple[InventoryGroup, ...]:
        return inventory_from_document(self.inventory_document())

    def inventory_digest(self) -> str:
        return str(canonical_digest(self.inventory_document()))


# --------------------------------------------------------------------- pinned recipe


class CppRecipe(PluginModel):
    """The pinned, hidden build recipe for one task."""

    kind: Literal["cpp_task_recipe"] = "cpp_task_recipe"
    schema_version: Literal[1] = 1
    recipe_version: str
    compiler: str
    standard: str
    build_profile: str
    include_dirs: tuple[str, ...] = ()
    link_flags: tuple[str, ...] = ()

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    def check(self, lock: ToolchainLock) -> None:
        """Refuse a recipe the pinned toolchain does not contain."""
        lock.compiler(self.compiler)
        lock.standard_flag(self.standard)
        profile = lock.build_profile(self.build_profile)
        if profile.release:
            raise LockError(
                f"build profile {self.build_profile!r} is the release measurement profile "
                "and may not be used to build a candidate"
            )
        if profile.sanitized:
            raise LockError(
                f"build profile {self.build_profile!r} is sanitized; a task declares its "
                "instrumentation in the quality plan instead"
            )


def canonical_bytes(recipe: Mapping[str, object]) -> bytes:
    """The exact bytes a plan's declared input digest is taken over."""
    return json.dumps(dict(recipe), sort_keys=True, separators=(",", ":")).encode()


# --------------------------------------------------------------------- quality plan


class WorkloadDecl(PluginModel):
    kind: Literal["workload_decl"] = "workload_decl"
    workload_id: Slug
    scale: int = Field(ge=1, le=10**9)
    weight_bp: int = Field(ge=1, le=10000)
    input_seed: int = Field(ge=0, le=2**63 - 1)


class PerformanceDecl(PluginModel):
    kind: Literal["performance_decl"] = "performance_decl"
    workload_file: str
    workloads: tuple[WorkloadDecl, ...] = Field(min_length=1)
    warmup_iterations: int = Field(default=5, ge=0, le=100)
    measured_iterations: int = Field(default=20, ge=1, le=1000)
    hard_timeout_seconds: int = Field(default=30, ge=30, le=110)

    @model_validator(mode="after")
    def weights_sum(self) -> PerformanceDecl:
        total = sum(workload.weight_bp for workload in self.workloads)
        if total != 10000:
            raise ValueError(f"performance workload weights sum to {total}, not 10000")
        return self


class CppQualityPlan(PluginModel):
    kind: Literal["cpp_task_quality_plan"] = "cpp_task_quality_plan"
    schema_version: Literal[1] = 1
    instrumentation: Instrumentation = "supported"
    sanitizers: tuple[Sanitizer, ...] = ()
    sanitizer_lanes: tuple[tuple[Sanitizer, ...], ...] = ()
    opportunity_tags: tuple[str, ...] = ()
    opportunities: dict[str, int] = Field(default_factory=dict)
    required_analyzers: tuple[str, ...] = ()
    dependency_inventory: tuple[str, ...] = ()
    performance: PerformanceDecl | None = None
    judge_items: tuple[str, ...] = ()
    recipe: dict[str, object] | None = None
    recipe_digest: str | None = None

    @model_validator(mode="after")
    def consistent(self) -> CppQualityPlan:
        allowed = set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS)
        for name, count in self.opportunities.items():
            if name not in allowed or count < 0:
                raise ValueError(f"unknown opportunity item or negative count: {name}")
        unknown = [a for a in self.required_analyzers if a not in KNOWN_ANALYZERS]
        if unknown:
            raise ValueError(f"unknown required analyzer(s): {unknown}")
        # Incompatible tools run as separate instrumented lanes. AddressSanitizer and
        # ThreadSanitizer cannot share one process, but a task may require evidence from both.
        if self.sanitizers and self.sanitizer_lanes:
            raise ValueError("declare sanitizers or sanitizer_lanes, not both")
        lanes = self.instrumentation_lanes
        lock = load_lock()
        for lane in lanes:
            if not lane:
                raise ValueError("a sanitizer lane must declare at least one sanitizer")
            lock.check_sanitizers(tuple(lane))
        lane_analyzers = [
            next(
                (
                    analyzer
                    for analyzer, needed in SANITIZER_REQUIREMENTS.items()
                    if set(needed) & set(lane)
                ),
                None,
            )
            for lane in lanes
        ]
        if len(lane_analyzers) != len(set(lane_analyzers)):
            raise ValueError("each sanitizer analyzer may be declared in only one lane")
        for analyzer, needed in SANITIZER_REQUIREMENTS.items():
            if analyzer in self.required_analyzers and not any(
                set(needed) & set(lane) for lane in lanes
            ):
                raise ValueError(
                    f"{analyzer} is a required analyzer but the task declares no "
                    f"{' or '.join(needed)} sanitizer"
                )
        if self.instrumentation == "unsupported":
            if any(a in self.required_analyzers for a in SANITIZER_REQUIREMENTS):
                raise ValueError(
                    "an instrumented analyzer cannot be required while instrumentation is "
                    "unsupported"
                )
            if self.opportunities.get("undefined_behavior_memory_safety", 0):
                raise ValueError(
                    "undefined_behavior_memory_safety opportunities need an instrumentation "
                    "applicability of supported"
                )
        if self.instrumentation == "supported" and not lanes:
            if self.opportunities.get("undefined_behavior_memory_safety", 0):
                raise ValueError(
                    "undefined_behavior_memory_safety opportunities need at least one sanitizer"
                )
        return self

    @property
    def runs_sanitizers(self) -> bool:
        return self.instrumentation != "unsupported" and bool(self.instrumentation_lanes)

    @property
    def instrumentation_lanes(self) -> tuple[tuple[Sanitizer, ...], ...]:
        if self.sanitizer_lanes:
            return self.sanitizer_lanes
        return (self.sanitizers,) if self.sanitizers else ()


# -------------------------------------------------------------------- exposure rights


class AccessRecord(PluginModel):
    kind: Literal["access_record"] = "access_record"
    party: str = Field(min_length=1, max_length=200)
    access: Literal["authored", "reviewed", "public"]
    date: str


class RightsRecord(PluginModel):
    kind: Literal["rights_record"] = "rights_record"
    license_expression: str
    status: Literal["authored_fixture", "public_domain", "licensed", "restricted"]
    owner_confirmation: Literal["pending", "confirmed"]


class ExposureRights(PluginModel):
    kind: Literal["exposure_rights_record"] = "exposure_rights_record"
    schema_version: Literal[1] = 1
    authorship: str
    originality_statement: str
    first_public_at: str | None = None
    public_exposure_review: str
    access_history: tuple[AccessRecord, ...] = Field(min_length=1)
    provider_transmission_policy: str
    rights: RightsRecord

    @model_validator(mode="after")
    def declared_rights(self) -> ExposureRights:
        if not self.rights.license_expression or not self.rights.status:
            raise ValueError("rights must declare a license expression and a status")
        if self.rights.owner_confirmation not in {"pending", "confirmed"}:
            raise ValueError("rights owner confirmation must be pending or confirmed")
        return self


# --------------------------------------------------------------------------- parsing


def parse_oracle(data: bytes) -> CppOracle:
    return CppOracle.model_validate_json(json.dumps(json.loads(data.decode("utf-8"))))


def parse_quality_plan(data: bytes) -> CppQualityPlan:
    return CppQualityPlan.model_validate_json(json.dumps(yaml.safe_load(data)))


def parse_recipe(data: bytes) -> CppRecipe:
    return CppRecipe.model_validate_json(json.dumps(json.loads(data.decode("utf-8"))))


def oracle_from_mapping(document: Mapping[str, object]) -> CppOracle:
    return CppOracle.model_validate_json(json.dumps(dict(document)))


def quality_from_mapping(document: Mapping[str, object]) -> CppQualityPlan:
    return CppQualityPlan.model_validate_json(json.dumps(dict(document)))


def recipe_from_mapping(document: Mapping[str, object]) -> CppRecipe:
    return CppRecipe.model_validate_json(json.dumps(dict(document)))


def discover_cases(path: str, source: bytes) -> list[str]:
    """Case ids declared by one hidden test file.

    The pinned harness prints one line per ``PCB_CHECK``/``PCB_SKIP``; the same macro call is the
    static declaration, so the inventory cannot drift from the file that runs it.
    """
    text = source.decode("utf-8", errors="replace")
    found: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        if text[index] in {'"', "'"}:
            quote = text[index]
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                elif text[index] == quote:
                    index += 1
                    break
                else:
                    index += 1
            continue
        if text[index].isalpha() or text[index] == "_":
            start = index
            index += 1
            while index < len(text) and (text[index].isalnum() or text[index] == "_"):
                index += 1
            if text[start:index] not in _CASE_MACROS:
                continue
            cursor = index
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor >= len(text) or text[cursor] != "(":
                continue
            cursor += 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor >= len(text) or text[cursor] != '"':
                continue
            cursor += 1
            literal: list[str] = []
            while cursor < len(text) and text[cursor] != '"':
                if text[cursor] == "\\" and cursor + 1 < len(text):
                    cursor += 1
                literal.append(text[cursor])
                cursor += 1
            if cursor < len(text):
                case_id = "".join(literal)
                if case_id and case_id not in found:
                    found.append(case_id)
            continue
        index += 1
    if not found:
        raise ValueError(f"{path} declares no PCB_CHECK/PCB_SKIP cases")
    return found


# ------------------------------------------------------------------------ validation


def _issue(
    code: str, message: str, *, severity: Literal["error", "warning"] = "error", path: str = ""
) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, path=path or None, message=message[:500])


def _section(manifest: Mapping[str, object], key: str) -> Mapping[str, Any] | None:
    value = manifest.get(key)
    return value if isinstance(value, dict) else None


def _str_list(section: Mapping[str, Any], key: str) -> list[str]:
    value = section.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _check_oracle(
    draft: TaskDraft,
    oracle: CppOracle,
    acceptance: Mapping[str, Any],
    issues: list[ValidationIssue],
) -> None:
    for group in oracle.groups:
        for name in group.files:
            path = f"hidden/{name}"
            if path not in draft.files:
                issues.append(
                    _issue("oracle-file-missing", f"{path} is not in the package", path=path)
                )
                continue
            try:
                discovered = discover_cases(name, draft.files[path])
            except ValueError as error:
                issues.append(_issue("oracle-case-discovery", str(error), path=path))
                continue
            declared = [case.case_id for case in group.cases]
            missing = [case for case in declared if case not in discovered]
            if missing:
                issues.append(
                    _issue(
                        "oracle-case-missing",
                        f"group {group.group_id} declares cases absent from {name}: {missing}",
                        path=path,
                    )
                )
            undeclared = [case for case in discovered if case not in declared]
            if undeclared:
                issues.append(
                    _issue(
                        "oracle-case-undeclared",
                        f"{name} runs cases the oracle does not declare: {undeclared}",
                        path=path,
                    )
                )
    required = sorted(group.group_id for group in oracle.groups if group.required)
    if required != sorted(_str_list(acceptance, "required_test_group_ids")):
        issues.append(
            _issue(
                "acceptance-group-mismatch",
                f"required groups {required} do not match the manifest's declared required groups",
                path="hidden/oracle.json",
            )
        )
    declared_conditions = {condition.condition_id for condition in oracle.hard_conditions}
    for condition in _str_list(acceptance, "hard_condition_ids"):
        if condition not in declared_conditions:
            issues.append(
                _issue(
                    "hard-condition-missing",
                    f"the manifest names hard condition {condition!r}, the oracle does not",
                    path="manifest.yaml",
                )
            )


def _robustness_scenarios(draft: TaskDraft) -> tuple[RobustnessScenario, ...]:
    if "hidden/oracle.json" not in draft.files:
        return ()
    try:
        return parse_oracle(draft.files["hidden/oracle.json"]).robustness_scenarios
    except (ValueError, KeyError):
        return ()


def _check_quality(
    draft: TaskDraft,
    plan: CppQualityPlan,
    manifest_quality: Mapping[str, Any],
    issues: list[ValidationIssue],
) -> None:
    if sorted(_str_list(manifest_quality, "required_analyzers")) != sorted(plan.required_analyzers):
        issues.append(
            _issue(
                "analyzer-mismatch",
                "the manifest's required_analyzers do not match the quality plan's",
                path="manifest.yaml",
            )
        )
    dimensions = set(_str_list(manifest_quality, "applicable_dimensions"))
    if "security" in dimensions and "security-surface" not in plan.opportunity_tags:
        issues.append(
            _issue(
                "dimension-evidence",
                "the security dimension needs a security-surface opportunity tag",
                path="hidden/quality-plan.yaml",
            )
        )
    if "efficiency" in dimensions and plan.performance is None:
        issues.append(
            _issue(
                "dimension-evidence",
                "the efficiency dimension needs a declared performance workload",
                path="hidden/quality-plan.yaml",
            )
        )
    if "robustness" in dimensions and not plan.runs_sanitizers and not _robustness_scenarios(draft):
        issues.append(
            _issue(
                "dimension-evidence",
                "the robustness dimension needs instrumented runs or robustness scenarios",
                path="hidden/quality-plan.yaml",
            )
        )
    if "idiomatic" in dimensions and not any(item in plan.opportunities for item in IDIOM_ITEMS):
        issues.append(
            _issue(
                "dimension-evidence",
                "the idiomatic dimension needs at least one idiomatic opportunity",
                path="hidden/quality-plan.yaml",
            )
        )
    if "code_quality" in dimensions and not any(
        item in plan.opportunities for item in DIAGNOSTIC_ITEMS
    ):
        issues.append(
            _issue(
                "dimension-evidence",
                "the code_quality dimension needs at least one diagnostic opportunity",
                path="hidden/quality-plan.yaml",
            )
        )
    for item in plan.judge_items:
        if item not in JUDGE_ITEMS:
            issues.append(
                _issue(
                    "judge-item-unknown",
                    f"unknown judge item {item!r}",
                    path="hidden/quality-plan.yaml",
                )
            )
    if plan.performance is not None:
        workload = f"hidden/{plan.performance.workload_file}"
        if workload not in draft.files:
            issues.append(
                _issue(
                    "performance-workload-missing",
                    f"performance workload {plan.performance.workload_file!r} is not in "
                    "the package",
                    path="hidden/quality-plan.yaml",
                )
            )


def _check_fixtures(
    draft: TaskDraft, manifest: Mapping[str, Any], issues: list[ValidationIssue]
) -> None:
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        issues.append(
            _issue("fixtures-missing", "the manifest declares no fixtures", path="manifest.yaml")
        )
        return
    entries = [item for item in fixtures if isinstance(item, dict)]
    variants = {str(item.get("variant")) for item in entries}
    missing = sorted(set(VARIANTS) - variants)
    if missing:
        issues.append(
            _issue(
                "fixture-variant-missing",
                f"the package has no fixture for variant(s) {missing}",
                path="manifest.yaml",
            )
        )
    for item in entries:
        path = str(item.get("solution_path", ""))
        if path not in draft.files:
            issues.append(
                _issue(
                    "fixture-file-missing",
                    f"fixture {item.get('name')!r} points at missing file {path!r}",
                    path=path,
                )
            )


def validate_cpp_task(draft: TaskDraft, plugin_id: str = "cpp") -> ValidationReport:
    """Author-time admission for a C++ task package."""
    issues: list[ValidationIssue] = []
    manifest = draft.manifest
    task = _section(manifest, "task")
    acceptance = _section(manifest, "acceptance")
    quality_section = _section(manifest, "quality_plan")
    runtime = _section(manifest, "runtime")
    if manifest.get("kind") != "task_package":
        issues.append(
            _issue("manifest-kind", "the manifest is not a task_package", path="manifest.yaml")
        )
    if task is None or str(task.get("primary_language", "")) != plugin_id:
        issues.append(
            _issue("language", f"primary_language must be {plugin_id!r}", path="manifest.yaml")
        )
    if acceptance is None or quality_section is None or runtime is None:
        issues.append(
            _issue(
                "package-file-missing",
                "the manifest needs acceptance, quality_plan and runtime sections",
                path="manifest.yaml",
            )
        )
        return ValidationReport(plugin_id=plugin_id, checks=CHECKS, issues=tuple(issues))

    required_outputs = _str_list(acceptance, "required_outputs")
    if not any(path.endswith((".cpp", ".cc", ".cxx")) for path in required_outputs):
        issues.append(
            _issue(
                "language",
                "a C++ task needs at least one required .cpp output",
                path="manifest.yaml",
            )
        )

    for required in (
        "visible/task.md",
        "hidden/oracle.json",
        "hidden/quality-plan.yaml",
        "hidden/recipe.json",
        "admission/exposure-rights.json",
    ):
        if required not in draft.files:
            issues.append(
                _issue("package-file-missing", f"{required} is not in the package", path=required)
            )
    for path in required_outputs:
        if f"visible/repo/{path}" not in draft.files:
            issues.append(
                _issue(
                    "stub-missing",
                    f"required output {path!r} has no stub under visible/repo",
                    path=f"visible/repo/{path}",
                )
            )
        public_header_is_starter = path.endswith((".h", ".hh", ".hpp", ".hxx")) and (
            f"visible/repo/{path}" in draft.files
        )
        if f"hidden/reference/{path}" not in draft.files and not public_header_is_starter:
            issues.append(
                _issue(
                    "reference-missing",
                    "neither the reference solution nor a public starter header supplies required "
                    f"output {path!r}",
                    path=f"hidden/reference/{path}",
                )
            )

    oracle: CppOracle | None = None
    if "hidden/oracle.json" in draft.files:
        try:
            oracle = parse_oracle(draft.files["hidden/oracle.json"])
        except (ValueError, KeyError) as error:
            issues.append(
                _issue("oracle-invalid", f"oracle is invalid: {error}", path="hidden/oracle.json")
            )
    if "hidden/recipe.json" in draft.files:
        try:
            parse_recipe(draft.files["hidden/recipe.json"]).check(load_lock())
        except (ValueError, KeyError) as error:
            issues.append(
                _issue("recipe-invalid", f"recipe is invalid: {error}", path="hidden/recipe.json")
            )
    plan: CppQualityPlan | None = None
    if "hidden/quality-plan.yaml" in draft.files:
        try:
            plan = parse_quality_plan(draft.files["hidden/quality-plan.yaml"])
        except (ValueError, KeyError) as error:
            issues.append(
                _issue(
                    "quality-plan-invalid",
                    f"quality plan is invalid: {error}",
                    path="hidden/quality-plan.yaml",
                )
            )
    if "admission/exposure-rights.json" in draft.files:
        try:
            ExposureRights.model_validate_json(draft.files["admission/exposure-rights.json"])
        except ValueError as error:
            issues.append(
                _issue(
                    "exposure-rights-invalid",
                    f"exposure rights are invalid: {error}",
                    path="admission/exposure-rights.json",
                )
            )

    if oracle is not None:
        _check_oracle(draft, oracle, acceptance, issues)
    if plan is not None:
        _check_quality(draft, plan, quality_section, issues)
    _check_fixtures(draft, manifest, issues)
    return ValidationReport(plugin_id=plugin_id, checks=CHECKS, issues=tuple(issues))


__all__ = [
    "CANDIDATE_SUFFIXES",
    "CHECKS",
    "CppOracle",
    "CppQualityPlan",
    "CppRecipe",
    "DIAGNOSTIC_ITEMS",
    "ExposureRights",
    "IDIOM_ITEMS",
    "KNOWN_ANALYZERS",
    "PerformanceDecl",
    "SANITIZER_REQUIREMENTS",
    "VARIANTS",
    "WorkloadDecl",
    "discover_cases",
    "oracle_from_mapping",
    "canonical_bytes",
    "parse_oracle",
    "parse_quality_plan",
    "parse_recipe",
    "quality_from_mapping",
    "recipe_from_mapping",
    "validate_cpp_task",
]
