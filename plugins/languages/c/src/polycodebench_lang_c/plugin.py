"""The C language plugin (Technical Spec 18.1) and its analyzer plugins (12.3)."""

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

from polycodebench_lang_c import plans
from polycodebench_lang_c.identities import ImageIdentities, load_identities
from polycodebench_lang_c.parsers import PARSERS, build_document, first_build_error, first_warning
from polycodebench_lang_c.profile import PROFILE_VERSION, CProfile, load_profile
from polycodebench_lang_c.recipe import analyzer_check_ids, recipe_document
from polycodebench_lang_c.symbols import index_c_sources
from polycodebench_lang_c.taskspec import (
    oracle_from_mapping,
    parse_oracle,
    parse_quality_plan,
    quality_from_mapping,
    scaffold_digest,
    validate_c_task,
)
from polycodebench_lang_c.testparse import parse_group_report

#: What each analyzer can speak to. A C lane that finds nothing still has to be able to say so.
_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "clang_tidy": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "cppcheck": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "asan": (ScoreDimension.ROBUSTNESS, ScoreDimension.SECURITY),
    "ubsan": (ScoreDimension.ROBUSTNESS,),
    "valgrind": (ScoreDimension.ROBUSTNESS,),
}
_STATIC_ANALYZERS = ("clang_tidy", "cppcheck")
_DYNAMIC_ANALYZERS = ("asan", "ubsan", "valgrind")
#: Recipes each analyzer is allowed to run in. A lane that cannot claim the recipe it needs is
#: at plan time rather than reported as a clean scan.
_RECIPE_OF = {
    "clang_tidy": "evaluator",
    "cppcheck": "evaluator",
    "asan": "instrumented",
    "ubsan": "instrumented",
    "valgrind": "evaluator",
}


class CAnalyzer:
    """One analyzer: it declares a plan and parses the recorded output; it never executes."""

    def __init__(self, analyzer_id: str, identities: ImageIdentities, profile: CProfile) -> None:
        self.analyzer_id = analyzer_id
        self._identities = identities
        self._profile = profile

    def capabilities(self) -> AnalyzerCapabilities:
        if self.analyzer_id in _STATIC_ANALYZERS:
            checks = analyzer_check_ids(prefix="c.tidy")
        elif self.analyzer_id == "cppcheck":
            checks = ("c.cppcheck.uninitvar", "c.cppcheck.nullPointer", "c.cppcheck.memleak")
        else:
            checks = (
                f"c.{self.analyzer_id}.bounds-violation",
                f"c.{self.analyzer_id}.use-after-free",
                f"c.{self.analyzer_id}.undefined-behaviour",
                f"c.{self.analyzer_id}.resource-leak",
            )
        return AnalyzerCapabilities(
            analyzer_id=self.analyzer_id,
            languages=("c",),
            check_ids=checks,
            produces_dimensions=_DIMENSIONS[self.analyzer_id],
        )

    def plan(self, context: AnalysisContext) -> AnalysisPlan:
        for candidate in plans.analysis_plans(self._identities, context):
            if candidate.analyzer_id == self.analyzer_id:
                if (
                    candidate.image_digest
                    != self._identities.record(_RECIPE_OF[self.analyzer_id]).digest
                ):
                    raise ValueError(
                        f"analyzer {self.analyzer_id!r} would run in a recipe that "
                        "cannot support it"
                    )
                return candidate
        raise ValueError(f"analyzer {self.analyzer_id!r} does not apply to this task")

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return PARSERS[self.analyzer_id](raw, plan, self._profile)


class CLanguagePlugin:
    api_version = API_VERSION
    language_id = "c"
    # Layout hints for suite admission: plans put sources under ``work/`` and take candidate files,
    # hidden tests and trusted scaffolding (headers, harness) as separate, role-checked inputs.
    overlay_prefix = "work/"
    candidate_suffixes = (".c", ".h")

    def __init__(self, identities: ImageIdentities | None = None) -> None:
        self._identities = identities or load_identities()
        self._profile = load_profile()

    # -------------------------------------------------------------------- task authoring

    def validate_task(self, task: TaskDraft) -> ValidationReport:
        return validate_c_task(task, self.language_id)

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
        return index_c_sources(source)

    def profile(self, version: str) -> LanguageProfile:
        if version != PROFILE_VERSION:
            raise ValueError(f"unknown C profile version {version!r}")
        return self._profile.profile

    def recipe_document(self) -> dict[str, object]:
        """The frozen build-recipe table this plugin will compile with."""
        return recipe_document()

    # ----------------------------------------------------- evidence parsing (executable)

    def freeze_view(self, draft: TaskDraft, task_digest: str, task_version: int = 1) -> FrozenTask:
        """The slice of a validated task that plans are built from.

        C has no lockfile, so the analogue of one is computed: the digest of the frozen recipe table
        plus the task's scaffold digests. Together those decide what the compiler will accept.
        """
        oracle = parse_oracle(draft.files["hidden/oracle.json"])
        quality = parse_quality_plan(draft.files["hidden/quality-plan.yaml"])
        manifest = draft.manifest
        acceptance = manifest["acceptance"]
        quality_section = manifest["quality_plan"]
        runtime = manifest["runtime"]
        assert isinstance(acceptance, dict) and isinstance(quality_section, dict)
        assert isinstance(runtime, dict)
        required = set(acceptance["required_outputs"])
        scaffold = scaffold_digest(draft.files, required)
        view = json.loads(quality.model_dump_json())
        view["scaffold_files"] = scaffold
        view["recipe_digest"] = self.recipe_digest()
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
                    "dependency_inventory": [],
                }
            )
        )

    def recipe_digest(self) -> str:
        from polycodebench_core.canonical import canonical_digest

        return str(canonical_digest(self.recipe_document()))

    def trusted_inputs(self, files: Mapping[str, bytes], view: FrozenTask) -> dict[str, bytes]:
        """The frozen scaffold (headers, harness-adjacent config) keyed by plan input path."""
        scaffold = quality_from_mapping(view.quality).scaffold_files
        return {f"work/{path}": files[f"visible/repo/{path}"] for path in scaffold}

    def inventory(self, task: FrozenTask) -> tuple[InventoryGroup, ...]:
        return oracle_from_mapping(task.inventory).inventory()

    def parse_build(
        self, plan: object, raw: ArtifactReader
    ) -> tuple[Literal["pass", "fail", "incomplete"], str]:
        assert isinstance(plan, BuildPlan)
        status, _record = plan_status(plan, raw)
        if status == "timed_out":
            return "incomplete", "the build timed out"
        if status in {"tool_error", "output_missing"}:
            return "incomplete", f"build harness error ({status})"
        try:
            document = build_document(raw, "build")
        except ValueError as error:
            return "incomplete", f"build output could not be parsed: {str(error)[:160]}"
        if not document["linked"]:
            if document.get("error_count"):
                return "fail", first_build_error(document)
            if document.get("blocked_by_warnings"):
                # The frozen policy says a warning is a build failure. That is reported as a build
                # failure with the warning named, never as an unexplained "incomplete".
                return (
                    "fail",
                    f"warnings are errors under this task's policy: {first_warning(document)}",
                )
            return "fail", "the translation units did not link"
        return "pass", "every required output compiles under the frozen recipe"

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

    def analyzer(self, analyzer_id: str) -> CAnalyzer:
        return CAnalyzer(analyzer_id, self._identities, self._profile)

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return self.analyzer(plan.analyzer_id).parse(raw, plan)

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        return self._profile.normalize(observations)

    @property
    def language_profile(self) -> CProfile:
        return self._profile

    def property_engine(self, task: FrozenTask, raw: Mapping[str, bytes]) -> PropertyEngineIdentity:
        """C tasks use fixed harness cases, with no generated property-test engine."""
        del task, raw
        return PropertyEngineIdentity(
            engine="none",
            engine_version=None,
            deterministic_policy="fixed-harness-cases",
        )

    @property
    def c_profile(self) -> CProfile:
        return self._profile

    @property
    def identities(self) -> ImageIdentities:
        return self._identities
