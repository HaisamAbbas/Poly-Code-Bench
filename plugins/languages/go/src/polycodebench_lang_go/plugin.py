"""The Go language plugin (Technical Spec 18.1) and its analyzer plugins (12.3)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from polycodebench_core.models import Candidate, Observation, ScoreDimension
from polycodebench_plugins_api import (
    API_VERSION,
    AnalysisContext,
    AnalysisPlan,
    AnalyzerCapabilities,
    ArtifactReader,
    BuildPlan,
    FrozenTask,
    LanguageProfile,
    PerformancePlan,
    PropertyEngineIdentity,
    SymbolIndex,
    TaskDraft,
    TestGroupPlan,
    TestPlan,
    ValidationReport,
    plan_status,
)
from polycodebench_plugins_api.testreport import GroupControl, InventoryGroup, TestCaseRecord

from polycodebench_lang_go import plans
from polycodebench_lang_go.identities import ImageIdentities, load_identities
from polycodebench_lang_go.locks import module_digest
from polycodebench_lang_go.parsers import PARSERS, build_error, go_build_messages
from polycodebench_lang_go.profile import PROFILE_VERSION, GoProfile, load_profile
from polycodebench_lang_go.symbols import index_go_sources
from polycodebench_lang_go.taskspec import (
    oracle_from_mapping,
    parse_oracle,
    parse_quality_plan,
    quality_from_mapping,
    validate_go_task,
)
from polycodebench_lang_go.testparse import parse_group_report

_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "gofmt": (ScoreDimension.CODE_QUALITY,),
    "vet": (ScoreDimension.CODE_QUALITY, ScoreDimension.ROBUSTNESS),
    "staticcheck": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "gosec": (ScoreDimension.CODE_QUALITY, ScoreDimension.SECURITY),
    "context": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC, ScoreDimension.ROBUSTNESS),
    "race": (ScoreDimension.ROBUSTNESS,),
    "dependency": (ScoreDimension.SECURITY,),
}


class GoAnalyzer:
    """One analyzer: it declares a plan and parses the recorded output; it never executes."""

    def __init__(self, analyzer_id: str, identities: ImageIdentities, profile: GoProfile) -> None:
        self.analyzer_id = analyzer_id
        self._identities = identities
        self._profile = profile

    def capabilities(self) -> AnalyzerCapabilities:
        checks = tuple(
            sorted(
                {
                    m.check_id or (m.check_prefix or "").rstrip(".") + ".all"
                    for m in self._profile.profile.rule_mappings
                    if f".{self.analyzer_id}." in (m.check_id or m.check_prefix or "")
                }
            )
        )
        return AnalyzerCapabilities(
            analyzer_id=self.analyzer_id,
            languages=("go",),
            check_ids=checks or (f"go.{self.analyzer_id}.scan",),
            produces_dimensions=_DIMENSIONS[self.analyzer_id],
        )

    def plan(self, context: AnalysisContext) -> AnalysisPlan:
        for candidate in plans.analysis_plans(self._identities, context):
            if candidate.analyzer_id == self.analyzer_id:
                return candidate
        raise ValueError(f"analyzer {self.analyzer_id!r} does not apply to this task")

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return PARSERS[self.analyzer_id](raw, plan, self._profile)


class GoLanguagePlugin:
    api_version = API_VERSION
    language_id = "go"
    # Layout hints for suite admission: plans put the module under ``work/`` and take candidate
    # sources, hidden tests and trusted module scaffolding as separate, role-checked inputs.
    overlay_prefix = "work/"
    candidate_suffixes = (".go",)
    # A Go `_test.go` file must live in the package directory it exercises: it is compiled as part
    # of that package and may use unexported identifiers. The hidden suite therefore sits beside
    # the candidate file rather than under a separate ``hidden/tests/`` tree, and only the
    # test files are overlays - the oracle and the quality plan are not staged into the workspace.
    overlay_roots = ("hidden/topwords/", "hidden/perf/")
    overlay_suffixes = ("_test.go",)

    def __init__(self, identities: ImageIdentities | None = None) -> None:
        self._identities = identities or load_identities()
        self._profile = load_profile()

    # -------------------------------------------------------------------- task authoring

    def validate_task(self, task: TaskDraft) -> ValidationReport:
        return validate_go_task(task, self.language_id)

    # ------------------------------------------------------------------------ plans

    def build_plan(self, task: FrozenTask, candidate: Candidate) -> BuildPlan:
        return plans.build_plan(self._identities, task, candidate)

    def test_plan(self, task: FrozenTask) -> TestPlan:
        return plans.make_test_plan(self._identities, task)

    def analysis_plans(self, context: AnalysisContext) -> list[AnalysisPlan]:
        return plans.analysis_plans(self._identities, context)

    def performance_plan(self, task: FrozenTask) -> PerformancePlan | None:
        return plans.performance_plan(self._identities, task)

    # ------------------------------------------------------------------ source facts

    def symbols(self, source: ArtifactReader) -> SymbolIndex:
        return index_go_sources(source)

    def profile(self, version: str) -> LanguageProfile:
        if version != PROFILE_VERSION:
            raise ValueError(f"unknown Go profile version {version!r}")
        return self._profile.profile

    # ----------------------------------------------------- evidence parsing (executable)

    def freeze_view(self, draft: TaskDraft, task_digest: str, task_version: int = 1) -> FrozenTask:
        """The slice of a validated task that plans are built from."""
        import hashlib

        oracle = parse_oracle(draft.files["hidden/oracle.json"])
        quality = parse_quality_plan(draft.files["hidden/quality-plan.yaml"])
        manifest = draft.manifest
        acceptance = manifest["acceptance"]
        quality_section = manifest["quality_plan"]
        runtime = manifest["runtime"]
        assert isinstance(acceptance, dict) and isinstance(quality_section, dict)
        assert isinstance(runtime, dict)
        required = set(acceptance["required_outputs"])
        # Trusted module scaffolding: everything the candidate does not write itself (go.mod, go.sum
        # and any fixed support file) is a digest-checked `config` input.
        module_files = {
            path.removeprefix("visible/repo/"): "sha256:" + hashlib.sha256(data).hexdigest()
            for path, data in sorted(draft.files.items())
            if path.startswith("visible/repo/")
            and path.removeprefix("visible/repo/") not in required
        }
        mod = draft.files.get("visible/repo/go.mod")
        sums = draft.files.get("visible/repo/go.sum")
        view = json.loads(quality.model_dump_json())
        view["module_files"] = module_files
        view["go_module_digest"] = str(module_digest(mod, sums)) if mod is not None else None
        return FrozenTask.model_validate_json(
            json.dumps(
                {
                    "kind": "frozen_task",
                    "schema_version": 1,
                    "task_id": draft.task_id,
                    "task_version": task_version,
                    "task_digest": task_digest,
                    "primary_language": draft.primary_language,
                    "image_digest": runtime["image_digest"],
                    "required_outputs": acceptance["required_outputs"],
                    "protected_paths": acceptance["protected_paths"],
                    "required_test_group_ids": acceptance["required_test_group_ids"],
                    "required_analyzers": list(quality.required_analyzers),
                    "applicable_dimensions": quality_section["applicable_dimensions"],
                    "quality": view,
                    "inventory": json.loads(oracle.model_dump_json()),
                    "inventory_digest": oracle.inventory_digest(),
                    "dependency_inventory": list(quality.dependency_inventory),
                }
            )
        )

    def trusted_inputs(self, files: Mapping[str, bytes], view: FrozenTask) -> dict[str, bytes]:
        """The module scaffold (``config`` role) keyed by the plan input path that names it."""
        module = quality_from_mapping(view.quality).module_files
        return {f"work/{path}": files[f"visible/repo/{path}"] for path in module}

    def inventory(self, task: FrozenTask) -> tuple[InventoryGroup, ...]:
        return oracle_from_mapping(task.inventory).inventory()

    def parse_build(
        self, plan: object, raw: ArtifactReader
    ) -> tuple[Literal["pass", "fail", "incomplete"], str]:
        assert isinstance(plan, BuildPlan)
        status, _record = plan_status(plan, raw)
        if status == "timed_out":
            return "incomplete", "build timed out"
        if status in {"tool_error", "output_missing"}:
            return "incomplete", f"build harness error ({status})"
        text = go_build_messages(raw, "build")
        if status == "completed":
            return "pass", "the module compiles"
        if status == "completed_with_findings":
            return "fail", build_error(text)
        return "incomplete", "build exit status disagrees with the compiler's own output"

    def parse_test_group(
        self,
        group: TestGroupPlan,
        inventory: InventoryGroup,
        raw: ArtifactReader,
        *,
        repetition: int,
    ) -> tuple[list[TestCaseRecord], GroupControl]:
        candidate = {
            path.removeprefix("work/")
            for path in (item.path for item in group.plan.inputs if item.role == "candidate")
        }
        return parse_group_report(
            group, inventory, raw, repetition=repetition, candidate_paths=candidate
        )

    # --------------------------------------------------------------------- extras

    def analyzer(self, analyzer_id: str) -> GoAnalyzer:
        return GoAnalyzer(analyzer_id, self._identities, self._profile)

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return self.analyzer(plan.analyzer_id).parse(raw, plan)

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        return self._profile.normalize(observations)

    @property
    def language_profile(self) -> GoProfile:
        return self._profile

    def property_engine(self, task: FrozenTask, raw: Mapping[str, bytes]) -> PropertyEngineIdentity:
        """Go tasks use fixed `go test` cases; fuzz targets are not run as scored properties."""
        del task, raw
        return PropertyEngineIdentity(
            engine="go-test",
            engine_version=self._identities.runtime.go,
            deterministic_policy="fixed-test-cases",
        )

    @property
    def go_profile(self) -> GoProfile:
        return self._profile

    @property
    def identities(self) -> ImageIdentities:
        return self._identities
