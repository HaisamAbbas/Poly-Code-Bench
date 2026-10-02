"""JS/TS task package contracts: hidden oracle inventory, quality plan and task validation.

Layout (Technical Spec 11.1, JavaScript/TypeScript profile)::

    manifest.yaml
    visible/task.md  visible/repo/package.json  visible/repo/package-lock.json  visible/repo/src/...
    hidden/oracle.json  hidden/quality-plan.yaml  hidden/tests/<group>.test.js|.ts
    hidden/reference/src/<entry>.js|.ts
    admission/<variant>/...            (faulty, alternative, quality-defective, timeout solutions)
    admission/exposure-rights.json

The contract differs from Rust's and Python's in four places that matter here:

* **Test identity is the Vitest ``fullName``** (``describe`` titles joined by `` > `` plus the test
  title), so case ids are matched against the names the runner actually reports. They are recovered
  statically from ``describe``/``it``/``test`` nesting; nothing is executed to find them.
* **The runner is the task's choice.** A task declares ``vitest`` or ``jest`` in its quality plan,
  and ``jest`` is refused unless the pinned evaluator image actually ships it - a task may not name
  a runner its own image cannot run.
* **Typing expectation is two-valued and language-conditional.** ``required`` is meaningful only
  for TypeScript, and it makes the ``typescript`` analyzer mandatory; ``none`` forbids declaring
  ``type_safety`` opportunities at all. A JavaScript candidate cannot be deducted for lacking
  TypeScript types, so a JavaScript task declaring ``type_safety`` is rejected outright.
* **The lock is an authored input that must actually pin.** ``package-lock.json`` is validated, and
  ``package_lock_digest`` plus ``config_files`` are computed by ``freeze_view``, never authored.
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

from polycodebench_lang_javascript.identities import ABSENT_TOOL, load_identities
from polycodebench_lang_javascript.locks import LockError, lock_digest
from polycodebench_lang_javascript.symbols import sanitize

# The union of both identities' profile items (config/languages/profiles-v1.yaml#profiles), so one
# validator reads every quality plan; a plugin instance only ever exposes its own language's items.
DIAGNOSTIC_ITEMS = (
    "async_correctness",
    "modern_immutability",
    "security",
    "async_error_handling",
    "lint",
    "type_safety",
)
IDIOM_ITEMS = (
    "async_composition",
    "data_module_api",
    "language_constructs",
    "restrained_mutation",
    "type_domain_modeling",
)
ITEMS: dict[str, tuple[str, ...]] = {
    "javascript": ("async_correctness", "modern_immutability", "security", "async_error_handling", "lint"),
    "typescript": (
        "async_correctness",
        "type_safety",
        "modern_immutability",
        "security",
        "async_error_handling",
        "lint",
    ),
}
IDIOMS: dict[str, tuple[str, ...]] = {
    "javascript": ("async_composition", "data_module_api", "language_constructs", "restrained_mutation"),
    "typescript": ("type_domain_modeling", "async_composition", "data_module_api", "language_constructs"),
}
KNOWN_ANALYZERS = ("eslint", "typescript", "context", "dependency")
# The runner a task selects; both are run by the pinned evaluator image, and a task that names one
# the image does not ship is rejected rather than silently run under the other.
TEST_RUNNERS = ("vitest", "jest")
TestRunner = Literal["vitest", "jest"]
SOURCE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "javascript": (".js", ".mjs", ".cjs"),
    "typescript": (".ts", ".mts", ".cts", ".tsx"),
}
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
VARIANTS = ("reference", "faulty", "alternative", "quality_defective", "timeout")


class OracleCase(PluginModel):
    kind: Literal["oracle_case"] = "oracle_case"
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    case_kind: Literal["example", "property", "robustness", "edge"]
    predeclared_skip: bool = False


class OracleGroup(PluginModel):
    """One group of cases, run as one vitest (or jest) invocation over its test files."""

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


class JsOracle(PluginModel):
    kind: Literal["js_task_oracle"] = "js_task_oracle"
    oracle_version: Slug
    groups: tuple[OracleGroup, ...] = Field(min_length=1)
    hard_conditions: tuple[HardCondition, ...] = ()
    robustness_scenarios: tuple[RobustnessScenario, ...] = ()
    # Vitest takes a per-test timeout, so ``case_timeout_seconds`` is enforced by the runner
    # itself; the suite deadline is the outer bound the supervisor enforces.
    case_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # At least 30s: the in-guest runner stops 3s before the supervisor's deadline and the supervisor
    # recognises a timeout only when the run lasted nearly the whole deadline.
    suite_timeout_seconds: int = Field(default=90, ge=30, le=110)

    @model_validator(mode="after")
    def unique_references(self) -> JsOracle:
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


class JsQualityPlan(PluginModel):
    """What quality evidence a task expects.

    ``typing_expectation`` replaces Rust's ``miri`` switch: it is the only JS/TS-specific
    precondition for a quality dimension. ``required`` means the task is written in TypeScript and
    the compiler's verdict is evidence, so ``typescript`` must be a required analyzer; ``none``
    means the task asks for no typing evidence, and then no ``type_safety`` opportunity may exist.

    ``test_runner`` is the task's choice of runner; ``jest`` is only accepted when the pinned
    evaluator image ships it.

    ``package_lock_digest`` and ``config_files`` are not authored: ``freeze_view`` computes them
    from the package so plans declare digest-checked inputs and a lock-derived tool identity.
    """

    kind: Literal["js_task_quality_plan"] = "js_task_quality_plan"
    typing_expectation: Literal["required", "none"] = "none"
    test_runner: TestRunner = "vitest"
    opportunity_tags: tuple[Slug, ...] = ()
    opportunities: Mapping[str, int] = Field(default_factory=dict)
    required_analyzers: tuple[Slug, ...] = ()
    dependency_inventory: tuple[str, ...] = ()
    performance: PerformanceDecl | None = None
    judge_items: tuple[Slug, ...] = ()
    package_lock_digest: Digest | None = None
    config_files: Mapping[str, Digest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_items(self) -> JsQualityPlan:
        allowed = set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS)
        for name, count in self.opportunities.items():
            if name not in allowed or count < 0:
                raise ValueError(f"unknown opportunity item or negative count: {name}")
        if any(a not in KNOWN_ANALYZERS for a in self.required_analyzers):
            raise ValueError("unknown required analyzer")
        if self.typing_expectation == "required" and "typescript" not in self.required_analyzers:
            raise ValueError("a required typing expectation needs the typescript analyzer")
        if self.typing_expectation != "required" and "typescript" in self.required_analyzers:
            raise ValueError("the typescript analyzer only applies when typing is required")
        if self.typing_expectation == "none" and self.opportunities.get("type_safety", 0):
            raise ValueError("type_safety opportunities contradict typing_expectation none")
        if self.typing_expectation == "none" and self.opportunities.get("type_domain_modeling", 0):
            raise ValueError("type_domain_modeling opportunities need typing_expectation required")
        return self

    @property
    def typing_runs(self) -> bool:
        """Whether a ``tsc --noEmit`` plan is produced at all (``none`` tasks never get one)."""
        return self.typing_expectation == "required"


def parse_oracle(data: bytes) -> JsOracle:
    return JsOracle.model_validate_json(json.dumps(parse_json_strict(data)))


def parse_quality_plan(data: bytes) -> JsQualityPlan:
    return JsQualityPlan.model_validate_json(json.dumps(yaml.safe_load(data)))


def oracle_from_mapping(document: Mapping[str, object]) -> JsOracle:
    return JsOracle.model_validate_json(json.dumps(document))


def quality_from_mapping(document: Mapping[str, object]) -> JsQualityPlan:
    return JsQualityPlan.model_validate_json(json.dumps(document))


# ------------------------------------------------------------------------ test discovery


_DESCRIBES = frozenset({"describe", "suite"})
_EVENT = re.compile(
    r"(?P<comment>//[^\n]*|/\*.*?\*/)"
    r"|(?P<string>'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`)"
    # A call site is a bare callee, so `foo.it(` and `mytest(` never read as a test declaration.
    r"|(?P<call>(?<![\w$.])(?P<callee>describe|suite|it|test)"
    r"(?:\s*\.\s*(?:only|skip|todo|concurrent|failing)\s*)*\()"
    r"|(?P<open>\{)|(?P<close>\})",
    re.DOTALL,
)


def _title(literal: str) -> str:
    """The string a test title literal denotes, with its escapes resolved."""
    quote = literal[0]
    body = literal[1:-1]
    if quote == "`":
        return re.sub(r"\\([`\\$])", r"\1", body)
    return re.sub(r"\\(.)", r"\1", body)


def discover_cases(path: str, source: bytes) -> list[str]:
    """Case ids of every ``it``/``test`` in a test file, as Vitest's ``fullName`` (nothing is run).

    One left-to-right scan over the raw source sees comments, string literals, ``describe``/``it``
    call sites and braces in source order. Because a comment or a string is consumed whole, a
    brace or a fake ``it(`` inside one cannot shift the nesting or invent a case. A call site's
    title is the next string literal after it, and a test's id is every enclosing ``describe``
    title followed by the test title, joined by one space - which is what ``fullName`` reports.

    ``it.each``/``describe.each`` tables are not expanded: their titles are computed at run time,
    so their case ids can only be declared in the oracle, not recovered statically.
    """
    text = source.decode("utf-8", errors="replace")
    found: list[str] = []
    titles: dict[int, str] = {}
    depth = 0
    pending: str | None = None
    for event in _EVENT.finditer(text):
        if event.group("comment") is not None:
            continue
        if event.group("string") is not None:
            if pending is not None:
                if pending in _DESCRIBES:
                    titles[depth] = _title(event.group("string"))
                else:
                    prefix = " ".join(titles[level] for level in sorted(titles) if level < depth)
                    found.append(f"{prefix} {event.group('string') and _title(event.group('string'))}".strip())
            pending = None
        elif event.group("call") is not None:
            pending = str(event.group("callee"))
        elif event.group("open") is not None:
            depth += 1
        else:
            depth -= 1
    return found


def _issue(
    code: str, message: str, path: str | None = None, severity: str = "error"
) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, path=path, message=message[:500])  # type: ignore[arg-type]


def _runner_provided(language: str, runner: str) -> bool:
    """Whether the pinned evaluator image ships the runner the task selected.

    A recorded identity that cannot be read is treated as not shipping it: an unpinned runner is
    not a runner a scored run may depend on.
    """
    try:
        return load_identities(language).ships(runner, recipe="evaluator")
    except (OSError, ValueError):
        return False


def validate_js_task(task: TaskDraft, plugin_id: str = "javascript") -> ValidationReport:
    """Structural and executable-contract validation that needs no sandbox."""
    issues: list[ValidationIssue] = []
    checks = [
        "language",
        "layout",
        "package-identity",
        "oracle-inventory",
        "acceptance-links",
        "quality-plan",
        "dimension-evidence",
        "variant-files",
        "exposure-rights",
    ]
    manifest = task.manifest
    files = task.files
    suffixes = SOURCE_SUFFIXES[plugin_id]
    if task.primary_language != plugin_id:
        issues.append(_issue("wrong-language", f"primary_language must be {plugin_id}"))
    required = [
        "visible/task.md",
        "visible/repo/package.json",
        "visible/repo/package-lock.json",
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
    if not any(o.endswith(suffixes) for o in outputs):
        issues.append(
            _issue("outputs", f"a {plugin_id} task needs at least one required source output")
        )
    for output in outputs:
        if f"hidden/reference/{output}" not in files:
            issues.append(_issue("missing-reference", "reference for a required output", output))
    if not any(
        p.startswith("hidden/tests/") and p.endswith(suffixes) for p in files
    ):
        issues.append(_issue("no-hidden-tests", f"hidden/tests contains no {plugin_id} test file"))
    lock = files.get("visible/repo/package-lock.json")
    if lock is not None:
        try:
            lock_digest(lock)
        except LockError as error:
            issues.append(
                _issue("package-lock-invalid", f"package-lock.json does not pin: {error}")
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
    if quality is not None:
        issues.extend(_check_runner(plugin_id, quality))
    if oracle is not None:
        issues.extend(_check_oracle(oracle, files, manifest))
    if oracle is not None and quality is not None:
        issues.extend(_check_quality(plugin_id, oracle, quality, manifest, files))
    issues.extend(_check_fixtures(manifest, files))
    return ValidationReport(plugin_id=plugin_id, checks=tuple(checks), issues=tuple(issues))


def _check_runner(plugin_id: str, quality: JsQualityPlan) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if quality.test_runner not in TEST_RUNNERS:
        issues.append(_issue("unknown-test-runner", f"unknown test runner {quality.test_runner}"))
        return issues
    if quality.test_runner == "vitest":
        return issues
    if not _runner_provided(plugin_id, "jest"):
        issues.append(
            _issue(
                "test-runner-missing",
                f"the pinned {plugin_id} evaluator image does not ship jest",
            )
        )
    return issues


def _check_oracle(
    oracle: JsOracle, files: Mapping[str, bytes], manifest: Mapping[str, object]
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
    plugin_id: str,
    oracle: JsOracle,
    quality: JsQualityPlan,
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
    if plugin_id == "javascript" and any(opps.get(i, 0) for i in ("type_safety", "type_domain_modeling")):
        issues.append(
            _issue(
                "typing-on-javascript",
                "a JavaScript task cannot carry TypeScript typing opportunities",
            )
        )
    for item in quality.judge_items:
        if item not in set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS) | JUDGE_ITEMS:
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


__all__ = [
    "DIAGNOSTIC_ITEMS",
    "IDIOMS",
    "IDIOM_ITEMS",
    "ITEMS",
    "KNOWN_ANALYZERS",
    "SOURCE_SUFFIXES",
    "TEST_RUNNERS",
    "VARIANTS",
    "ExposureRights",
    "JsOracle",
    "JsQualityPlan",
    "OracleCase",
    "OracleGroup",
    "PerformanceDecl",
    "discover_cases",
    "oracle_from_mapping",
    "parse_oracle",
    "parse_quality_plan",
    "quality_from_mapping",
    "validate_js_task",
]
