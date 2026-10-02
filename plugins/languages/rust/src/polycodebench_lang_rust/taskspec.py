"""Rust task package contracts: hidden oracle inventory, quality plan and task validation.

Layout (Technical Spec 11.1, Rust profile)::

    manifest.yaml
    visible/task.md  visible/repo/Cargo.toml  visible/repo/Cargo.lock  visible/repo/src/...
    hidden/oracle.json  hidden/quality-plan.yaml  hidden/tests/<name>.rs  hidden/reference/src/...
    admission/<variant>/...            (faulty, alternative, quality-defective, timeout solutions)
    admission/exposure-rights.json

The Rust contract differs from Python's in three places that matter here:

* **No typing expectation.** Rust's type system is not gradual, so there is nothing analogous to
  "does this task expect annotations". Instead a task declares whether *Miri* applies, which is the
  only Rust-specific precondition for a quality dimension.
* **Miri applicability is explicit and three-valued** (``required`` / ``optional`` /
  ``unsupported``). Only ``required`` makes a Miri plan mandatory. ``unsupported`` records that the
  task cannot be interpreted (foreign calls, syscalls), so an absent scan is *not* a defect, and
  ``optional`` means the scan runs when the task permits it but never gates on it.
* **Test identity is a libtest path qualified by its test binary** (``hidden_core::nested::deep``
  for ``tests/hidden_core.rs``), so case ids are matched against the names the test binary
  actually prints.

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

from polycodebench_lang_rust.locks import LockError, lock_digest
from polycodebench_lang_rust.symbols import sanitize

# Item names are the profile's (config/languages/profiles-v1.yaml#profiles.rust).
DIAGNOSTIC_ITEMS = (
    "ownership_borrowing",
    "unsafe_soundness",
    "result_option",
    "iterators_traits",
    "concurrency",
    "clippy",
)
IDIOM_ITEMS = (
    "ownership_borrowing_api",
    "iterator_trait_composition",
    "result_option_modeling",
    "concurrency_abstraction",
)
KNOWN_ANALYZERS = ("clippy", "context", "miri", "dependency")
MiriApplicability = Literal["required", "optional", "unsupported"]
VARIANTS = ("reference", "faulty", "alternative", "quality_defective", "timeout")


class OracleCase(PluginModel):
    kind: Literal["oracle_case"] = "oracle_case"
    case_id: str = Field(min_length=1, max_length=512)
    required: bool
    case_kind: Literal["example", "property", "robustness", "edge"]
    predeclared_skip: bool = False


class OracleGroup(PluginModel):
    """One group of cases, run as one ``cargo test`` invocation over its test files."""

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


class RustOracle(PluginModel):
    kind: Literal["rust_task_oracle"] = "rust_task_oracle"
    oracle_version: Slug
    groups: tuple[OracleGroup, ...] = Field(min_length=1)
    hard_conditions: tuple[HardCondition, ...] = ()
    robustness_scenarios: tuple[RobustnessScenario, ...] = ()
    # libtest has no per-case alarm, so ``case_timeout_seconds`` is a budget the task author
    # declares and the suite deadline enforces; a hung case is attributed from partial output.
    case_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # At least 30s: the in-guest runner stops 3s before the supervisor's deadline and the supervisor
    # recognises a timeout only when the run lasted nearly the whole deadline.
    suite_timeout_seconds: int = Field(default=90, ge=30, le=110)

    @model_validator(mode="after")
    def unique_references(self) -> RustOracle:
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


class RustQualityPlan(PluginModel):
    """What quality evidence a task expects.

    ``miri`` is the applicability switch for the Miri dimension. A task that cannot be
    interpreted declares ``unsupported`` and is then never penalised for a missing scan; a task
    that requires Miri must list ``miri`` in ``required_analyzers``, which is what makes the plan
    mandatory rather than advisory.

    ``crate_files`` and ``cargo_lock_digest`` are not authored: ``freeze_view`` computes them from
    the package so plans can declare digest-checked inputs and a lock-derived tool identity.
    """

    kind: Literal["rust_task_quality_plan"] = "rust_task_quality_plan"
    miri: MiriApplicability = "unsupported"
    # Oracle groups whose tests Miri interprets; empty means every required group. Interpreting a
    # whole suite is slow, so a task may name the small groups that exercise its unsafe code.
    miri_groups: tuple[Slug, ...] = ()
    opportunity_tags: tuple[Slug, ...] = ()
    opportunities: Mapping[str, int] = Field(default_factory=dict)
    required_analyzers: tuple[Slug, ...] = ()
    dependency_inventory: tuple[str, ...] = ()
    performance: PerformanceDecl | None = None
    judge_items: tuple[Slug, ...] = ()
    cargo_lock_digest: Digest | None = None
    crate_files: Mapping[str, Digest] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_items(self) -> RustQualityPlan:
        allowed = set(DIAGNOSTIC_ITEMS) | set(IDIOM_ITEMS)
        for name, count in self.opportunities.items():
            if name not in allowed or count < 0:
                raise ValueError(f"unknown opportunity item or negative count: {name}")
        if any(a not in KNOWN_ANALYZERS for a in self.required_analyzers):
            raise ValueError("unknown required analyzer")
        if self.miri == "required" and "miri" not in self.required_analyzers:
            raise ValueError("miri=required must also list miri in required_analyzers")
        if self.miri != "required" and "miri" in self.required_analyzers:
            raise ValueError("miri can only be a required analyzer when miri=required")
        if self.miri == "unsupported" and self.opportunities.get("unsafe_soundness", 0):
            raise ValueError(
                "unsafe_soundness opportunities need a miri applicability of required or optional"
            )
        return self

    @property
    def miri_runs(self) -> bool:
        """Whether a Miri plan is produced at all (``unsupported`` tasks never get one)."""
        return self.miri != "unsupported"


def parse_oracle(data: bytes) -> RustOracle:
    return RustOracle.model_validate_json(json.dumps(parse_json_strict(data)))


def parse_quality_plan(data: bytes) -> RustQualityPlan:
    return RustQualityPlan.model_validate_json(json.dumps(yaml.safe_load(data)))


def oracle_from_mapping(document: Mapping[str, object]) -> RustOracle:
    return RustOracle.model_validate_json(json.dumps(document))


def quality_from_mapping(document: Mapping[str, object]) -> RustQualityPlan:
    return RustQualityPlan.model_validate_json(json.dumps(document))


# ------------------------------------------------------------------------ test discovery

_TOKEN = re.compile(
    r"#\[(?P<attr>[^\]]*)\]|\bmod\s+(?P<mod>[A-Za-z_]\w*)\s*\{|\bfn\s+(?P<fn>[A-Za-z_]\w*)"
    r"|(?P<open>\{)|(?P<close>\})"
)


def discover_cases(path: str, source: bytes) -> list[str]:
    """Case ids of every ``#[test]`` function in a test file (static, nothing is compiled).

    The id is ``<file stem>::<module path>::<name>``, the path libtest prints for an integration
    test binary. Items under ``#[cfg(test)] mod`` count; a ``#[test]`` that is also ``#[ignore]``
    is still discovered (it must be declared, as skipped, in the oracle).
    """
    text = sanitize(source.decode("utf-8", errors="replace"))
    stem = path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".rs")
    found: list[str] = []
    modules: list[tuple[str, int]] = []
    depth = 0
    pending_test = False
    for token in _TOKEN.finditer(text):
        if token.group("attr") is not None:
            attr = token.group("attr").strip()
            if attr == "test" or attr.startswith("test ") or attr.endswith("::test"):
                pending_test = True
        elif token.group("mod") is not None:
            depth += 1
            modules.append((token.group("mod"), depth))
            pending_test = False
        elif token.group("fn") is not None:
            if pending_test:
                prefix = "::".join([stem, *(name for name, _ in modules)])
                found.append(f"{prefix}::{token.group('fn')}")
            pending_test = False
        elif token.group("open") is not None:
            depth += 1
        elif token.group("close") is not None:
            if modules and modules[-1][1] == depth:
                modules.pop()
            depth -= 1
    return found


# ------------------------------------------------------------------------- validation


def _issue(
    code: str, message: str, path: str | None = None, severity: str = "error"
) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, path=path, message=message[:500])  # type: ignore[arg-type]


def validate_rust_task(task: TaskDraft, plugin_id: str = "rust") -> ValidationReport:
    """Structural and executable-contract validation that needs no sandbox."""
    issues: list[ValidationIssue] = []
    checks = [
        "language",
        "layout",
        "crate-identity",
        "oracle-inventory",
        "acceptance-links",
        "quality-plan",
        "dimension-evidence",
        "variant-files",
        "exposure-rights",
    ]
    manifest = task.manifest
    files = task.files
    if task.primary_language != "rust":
        issues.append(_issue("wrong-language", "primary_language must be rust"))
    required = [
        "visible/task.md",
        "visible/repo/Cargo.toml",
        "visible/repo/Cargo.lock",
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
    if not any(o.endswith(".rs") for o in outputs):
        issues.append(_issue("outputs", "a Rust task needs at least one required .rs output"))
    for output in outputs:
        if f"hidden/reference/{output}" not in files:
            issues.append(_issue("missing-reference", "reference for a required output", output))
    if not any(p.startswith("hidden/tests/") and p.endswith(".rs") for p in files):
        issues.append(_issue("no-hidden-tests", "hidden/tests contains no .rs files"))
    lock = files.get("visible/repo/Cargo.lock")
    if lock is not None:
        try:
            lock_digest(lock)
        except LockError as error:
            issues.append(
                _issue("cargo-lock-invalid", f"Cargo.lock does not pin a resolution: {error}")
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
    oracle: RustOracle, files: Mapping[str, bytes], manifest: Mapping[str, object]
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
    oracle: RustOracle,
    quality: RustQualityPlan,
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
