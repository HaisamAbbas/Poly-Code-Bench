"""Typed plans, reports and profiles exchanged between language/analyzer plugins and the supervisor.

Plugins only *describe* work: every plan is a typed argument array with image, resource, input,
output and exit-code declarations. The supervisor executes plans in an isolated guest; parsers
receive recorded bytes and cannot spawn host code (Technical Spec 12.3, 18.1).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, Protocol

from polycodebench_core.models import Digest, RelativePath, ScoreDimension, Slug
from pydantic import BaseModel, ConfigDict, Field, model_validator

API_VERSION = 1
EXECUTION_RECORD_PATH = "_execution.json"
"""Supervisor-written record that parsers may read next to the plan's declared outputs."""

_SHELL_LAUNCHERS = frozenset({"sh", "bash", "dash", "zsh", "cmd", "cmd.exe", "powershell"})
_ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


class PluginModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1


# --------------------------------------------------------------------------- identities


class ToolIdentity(PluginModel):
    """Everything that makes two executions of a tool comparable (Technical Spec 18.2)."""

    kind: Literal["tool_identity"] = "tool_identity"
    name: Slug
    version: str = Field(min_length=1, max_length=64)
    image_digest: Digest
    lock_digest: Digest | None
    rule_bundle_digest: Digest | None
    advisory_snapshot_digest: Digest | None
    parser_version: str = Field(min_length=1, max_length=64)


class ResourcePolicy(PluginModel):
    kind: Literal["resource_policy"] = "resource_policy"
    cpu_millis: int = Field(ge=100, le=64_000)
    memory_bytes: int = Field(ge=64 * 1024**2, le=64 * 1024**3)
    pids_limit: int = Field(ge=1, le=4096)
    disk_bytes: int = Field(ge=16 * 1024**2, le=512 * 1024**2)
    timeout_seconds: int = Field(ge=1, le=3600)
    max_output_bytes: int = Field(ge=1024, le=8 * 1024**2)
    network: Literal["none"] = "none"
    # True for compiled languages whose plans run the binaries they build in the workspace.
    executable_workspace: bool = False


class ExitSemantics(PluginModel):
    """What each exit status of the tool means; anything undeclared is an error, never clean."""

    kind: Literal["exit_semantics"] = "exit_semantics"
    success: tuple[int, ...]
    findings: tuple[int, ...] = ()
    error: tuple[int, ...] = ()

    @model_validator(mode="after")
    def disjoint(self) -> ExitSemantics:
        groups = (set(self.success), set(self.findings), set(self.error))
        if not self.success or sum(len(g) for g in groups) != len(set().union(*groups)):
            raise ValueError("exit semantics need a success code and disjoint code groups")
        return self

    def classify(
        self, exit_code: int | None, timed_out: bool
    ) -> Literal["success", "findings", "error", "timeout", "unexpected"]:
        if timed_out or exit_code is None:
            return "timeout"
        if exit_code in self.success:
            return "success"
        if exit_code in self.findings:
            return "findings"
        if exit_code in self.error:
            return "error"
        return "unexpected"


class PlanInput(PluginModel):
    """An explicit artifact input. ``overlay`` and ``config`` come from trusted storage only."""

    kind: Literal["plan_input"] = "plan_input"
    path: RelativePath
    role: Literal["candidate", "overlay", "baseline", "config"]
    digest: Digest | None = None


class PlanOutput(PluginModel):
    kind: Literal["plan_output"] = "plan_output"
    path: RelativePath
    format: Literal["junit_xml", "xml", "json", "jsonl", "sarif", "text"]
    required: bool = True
    max_bytes: int = Field(default=4 * 1024**2, ge=1, le=64 * 1024**2)


class ExecutionPlan(PluginModel):
    """Common shape of build, test, analysis and performance plans."""

    kind: Literal["execution_plan"] = "execution_plan"
    plan_id: Slug
    image: str = Field(min_length=1, max_length=256)
    image_digest: Digest
    argv: tuple[str, ...] = Field(min_length=1, max_length=128)
    working_directory: RelativePath | Literal["."] = "."
    environment: Mapping[str, str] = Field(default_factory=dict)
    inputs: tuple[PlanInput, ...] = ()
    outputs: tuple[PlanOutput, ...] = ()
    resources: ResourcePolicy
    exit_semantics: ExitSemantics
    tool: ToolIdentity
    scope: tuple[RelativePath, ...] = ()
    parser_id: Slug

    @model_validator(mode="after")
    def typed_argument_array(self) -> ExecutionPlan:
        if self.argv[0].rsplit("/", 1)[-1].lower() in _SHELL_LAUNCHERS:
            raise ValueError("plans must use typed argument arrays, not shell launchers")
        if any("\x00" in part for part in self.argv):
            raise ValueError("argument vectors cannot contain NUL")
        if "@sha256:" not in self.image or not self.image.endswith(self.image_digest[7:]):
            raise ValueError("plan image must be pinned by the declared sha256 digest")
        if self.tool.image_digest != self.image_digest:
            raise ValueError("tool identity must name the image the plan runs in")
        for key, value in self.environment.items():
            if not _ENV_KEY.match(key) or "\x00" in value:
                raise ValueError("invalid plan environment entry")
        if len({item.path for item in self.outputs}) != len(self.outputs):
            raise ValueError("plan outputs must be unique")
        return self


class BuildPlan(ExecutionPlan):
    kind: Literal["build_plan"] = "build_plan"  # type: ignore[assignment]
    noop_reason: str | None = None


class TestGroupPlan(PluginModel):
    """One acceptance/quality test group; its plan writes a structured test report."""

    __test__ = False  # not a pytest class
    kind: Literal["test_group_plan"] = "test_group_plan"
    group_id: Slug
    required: bool
    repetitions: int = Field(ge=1, le=20)
    plan: ExecutionPlan


class TestPlan(PluginModel):
    __test__ = False
    kind: Literal["test_plan"] = "test_plan"
    groups: tuple[TestGroupPlan, ...] = Field(min_length=1)
    expected_inventory_digest: Digest

    @model_validator(mode="after")
    def unique_groups(self) -> TestPlan:
        ids = [group.group_id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("test group identifiers must be unique")
        return self


class AnalysisPlan(ExecutionPlan):
    kind: Literal["analysis_plan"] = "analysis_plan"  # type: ignore[assignment]
    analyzer_id: Slug
    # The language identity this plan belongs to, so a parser can name its check ids without
    # consulting the plugin that produced the plan. Check ids are language-namespaced
    # (`<language>.<analyzer>.<name>`); deriving the prefix from the plan rather than from a
    # plugin attribute is what keeps one analyzer implementation usable by two identities.
    language_id: Slug
    candidate_digest: Digest
    required: bool
    output_schema: Slug
    evidence_ownership: Mapping[str, ScoreDimension | None] = Field(default_factory=dict)
    baseline_reusable: bool = True


class WorkloadSpec(PluginModel):
    kind: Literal["workload_spec"] = "workload_spec"
    workload_id: Slug
    scale: int = Field(ge=1, le=10**9)
    weight_bp: int = Field(ge=1, le=10_000)
    input_seed: int = Field(ge=0, le=2**63 - 1)


class PerformancePlan(PluginModel):
    """Workload contract only; paired measurement is the performance stage (Prompt 13)."""

    kind: Literal["performance_plan"] = "performance_plan"
    plan_id: Slug
    reference_digest: Digest | None
    language_runtime: str = Field(min_length=1, max_length=64)
    hardware_class: Slug
    workloads: tuple[WorkloadSpec, ...] = Field(min_length=1)
    warmup_iterations: int = Field(ge=0, le=100)
    measured_iterations: int = Field(ge=1, le=1000)
    metric_ids: tuple[Slug, ...] = Field(min_length=1)
    iteration_plan: ExecutionPlan
    threads: int = Field(default=1, ge=1, le=256)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> PerformancePlan:
        if sum(item.weight_bp for item in self.workloads) != 10_000:
            raise ValueError("workload weights must sum to 10000 basis points")
        return self


# ------------------------------------------------------------------------- task material


class TaskDraft(PluginModel):
    """A task package as authored: manifest document plus every file (hidden ones included)."""

    kind: Literal["task_draft"] = "task_draft"
    task_id: Slug
    primary_language: Slug
    manifest: Mapping[str, object]
    files: Mapping[str, bytes]


class FrozenTask(PluginModel):
    """The slice of a frozen task a language plugin may plan against."""

    kind: Literal["frozen_task"] = "frozen_task"
    task_id: Slug
    task_version: int = Field(gt=0)
    task_digest: Digest
    primary_language: Slug
    image_digest: Digest
    required_outputs: tuple[RelativePath, ...]
    protected_paths: tuple[RelativePath, ...] = ()
    required_test_group_ids: tuple[Slug, ...]
    required_analyzers: tuple[Slug, ...] = ()
    applicable_dimensions: tuple[ScoreDimension, ...] = ()
    quality: Mapping[str, object] = Field(default_factory=dict)
    inventory: Mapping[str, object] = Field(default_factory=dict)
    inventory_digest: Digest
    dependency_inventory: tuple[str, ...] = ()


class AnalysisContext(PluginModel):
    kind: Literal["analysis_context"] = "analysis_context"
    task: FrozenTask
    candidate_digest: Digest
    candidate_paths: tuple[RelativePath, ...]
    base_paths: tuple[RelativePath, ...] = ()
    purpose: Literal["candidate", "baseline", "reference"] = "candidate"


class ValidationIssue(PluginModel):
    kind: Literal["validation_issue"] = "validation_issue"
    code: Slug
    severity: Literal["error", "warning"]
    path: str | None = None
    message: str = Field(max_length=500)


class ValidationReport(PluginModel):
    kind: Literal["validation_report"] = "validation_report"
    plugin_id: Slug
    checks: tuple[Slug, ...]
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


class Symbol(PluginModel):
    kind: Literal["symbol"] = "symbol"
    symbol_kind: Literal["module", "class", "function", "method", "variable"]
    qualified_name: str = Field(min_length=1, max_length=512)
    path: RelativePath
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    public: bool
    annotated: bool | None = None


class SymbolIndex(PluginModel):
    kind: Literal["symbol_index"] = "symbol_index"
    language_id: Slug
    symbols: tuple[Symbol, ...] = ()
    unparsed_paths: tuple[RelativePath, ...] = ()


class AnalyzerCapabilities(PluginModel):
    kind: Literal["analyzer_capabilities"] = "analyzer_capabilities"
    analyzer_id: Slug
    languages: tuple[Slug, ...]
    check_ids: tuple[Slug, ...]
    produces_dimensions: tuple[ScoreDimension, ...]
    supports_baseline: bool = True
    requires_network: Literal[False] = False


# ---------------------------------------------------------------------------- profiles


class ProfileItem(PluginModel):
    kind: Literal["profile_item"] = "profile_item"
    item_id: Slug
    weight_bp: int = Field(ge=1, le=10_000)
    description: str = Field(max_length=300)


class RuleMapping(PluginModel):
    """Maps a tool rule/detector (exact id or prefix) to profile items and one composite owner."""

    kind: Literal["rule_mapping"] = "rule_mapping"
    check_id: Slug | None = None
    check_prefix: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    items: tuple[Slug, ...] = ()
    idiom_item: Slug | None = None
    owner: ScoreDimension | None
    applicability: Slug
    context_evaluator: Slug | None = None
    equivalence_family: Slug | None = None

    @model_validator(mode="after")
    def exactly_one_selector(self) -> RuleMapping:
        if (self.check_id is None) == (self.check_prefix is None):
            raise ValueError("a rule mapping needs exactly one of check_id and check_prefix")
        if not self.items and self.owner is None and self.idiom_item is None:
            raise ValueError("a rule mapping must feed an item, an idiom item or a composite owner")
        return self

    def matches(self, check_id: str) -> bool:
        if self.check_id is not None:
            return str(self.check_id) == check_id
        prefix = self.check_prefix
        return prefix is not None and check_id.startswith(str(prefix))


class ApplicabilityRule(PluginModel):
    kind: Literal["applicability_rule"] = "applicability_rule"
    rule_id: Slug
    description: str = Field(max_length=300)
    opportunity_detector: Slug
    requires_task_opportunity: Slug | None = None


class LanguageProfile(PluginModel):
    kind: Literal["language_profile"] = "language_profile"
    language_id: Slug
    profile_version: Slug
    effective_for_scoring: bool
    diagnostic_items: tuple[ProfileItem, ...] = Field(min_length=1)
    idiom_items: tuple[ProfileItem, ...] = Field(min_length=1)
    rule_mappings: tuple[RuleMapping, ...]
    applicability_rules: tuple[ApplicabilityRule, ...]
    ownership: Mapping[str, ScoreDimension | None]
    duplicate_composite_penalty: Literal[False] = False
    syntax_count_bonus: Literal[False] = False

    @model_validator(mode="after")
    def weights_sum_and_references(self) -> LanguageProfile:
        for group in (self.diagnostic_items, self.idiom_items):
            if sum(item.weight_bp for item in group) != 10_000:
                raise ValueError("profile item weights must sum to 10000 basis points")
        diagnostic = {item.item_id for item in self.diagnostic_items}
        idioms = {item.item_id for item in self.idiom_items}
        rules = {rule.rule_id for rule in self.applicability_rules}
        for mapping in self.rule_mappings:
            if not set(mapping.items) <= diagnostic:
                raise ValueError("a rule mapping names an unknown diagnostic item")
            if mapping.idiom_item is not None and mapping.idiom_item not in idioms:
                raise ValueError("a rule mapping names an unknown idiom item")
            if mapping.applicability not in rules:
                raise ValueError("a rule mapping names an unknown applicability rule")
        return self


class ProfileItemResult(PluginModel):
    """One diagnostic or idiom item's outcome once applicability and ownership are applied.

    ``status`` carries the whole point of the diagnostic view (Technical Spec 18.3). An item with
    no frozen task opportunity is ``not_applicable`` and is dropped from the weighted average -
    never scored as perfect. An item fed by a required scan that did not complete is ``missing``,
    which makes the profile incomplete instead of producing a number nobody can defend.
    """

    kind: Literal["profile_item_result"] = "profile_item_result"
    item_id: str
    group: Literal["diagnostic", "idiom"]
    weight_bp: int
    opportunities: int = Field(ge=0)
    unique_violations: int = Field(ge=0)
    status: Literal["measured", "not_applicable", "missing"]
    score_bp: int | None = None
    issue_keys: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class ProfileResult(PluginModel):
    """A language profile's evaluation of one candidate's frozen evidence."""

    kind: Literal["profile_result"] = "profile_result"
    profile_version: str
    diagnostic: tuple[ProfileItemResult, ...]
    idioms: tuple[ProfileItemResult, ...]
    diagnostic_score_bp: int | None
    idiom_score_bp: int | None
    complete: bool



class PropertyEngineIdentity(PluginModel):
    """Which property-test engine produced this run's evidence, and under what determinism policy.

    The supervisor must not switch on the task's language to decide this. The language plugin
    states it, so a new language never means editing an engine ``if`` in the evaluator, and a
    language whose runner has no seeded randomness says ``unknown`` rather than borrowing another
    language's policy.
    """

    kind: Literal["property_engine_identity"] = "property_engine_identity"
    engine: str = Field(min_length=1, max_length=64)
    engine_version: str | None = None
    deterministic_policy: str = Field(min_length=1, max_length=64)
    examples_pinned: int | None = Field(default=None, ge=1)


# --------------------------------------------------------------------------- protocols


class ArtifactReader(Protocol):
    """Read-only view over recorded artifacts (plan outputs, candidate files, execution record)."""

    def list(self) -> tuple[str, ...]: ...

    def read(self, path: str) -> bytes: ...


class DictArtifactReader:
    """In-memory reader used by the supervisor and by tests."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        self._files = dict(files)

    def list(self) -> tuple[str, ...]:
        return tuple(sorted(self._files))

    def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError:
            raise FileNotFoundError(path) from None
