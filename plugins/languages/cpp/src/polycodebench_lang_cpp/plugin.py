"""The C++ language plugin (Technical Spec 18.1) and its analyzer plugins (12.3).

C++ is a separate language identity, not a dialect of C. It has its own weights, its own
applicability rules, its own analyzers and its own images; nothing here falls back to the C or
the Rust profile. The class only *declares* work and *reads back* evidence — it never executes a
compiler, a sanitizer or an analyzer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

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
)
from polycodebench_plugins_api.testreport import GroupControl, InventoryGroup, TestCaseRecord

from polycodebench_lang_cpp import parsers, plans
from polycodebench_lang_cpp.identities import ImageIdentities, load_identities
from polycodebench_lang_cpp.locks import ToolchainLock, load_lock
from polycodebench_lang_cpp.profile import PROFILE_VERSION, CppProfile, load_profile
from polycodebench_lang_cpp.symbols import index_cpp_sources
from polycodebench_lang_cpp.taskspec import (
    canonical_bytes,
    oracle_from_mapping,
    parse_oracle,
    parse_quality_plan,
    parse_recipe,
    quality_from_mapping,
    validate_cpp_task,
)
from polycodebench_lang_cpp.testparse import first_build_error, parse_group_report

_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "clang_tidy": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "cppcheck": (ScoreDimension.CODE_QUALITY, ScoreDimension.ROBUSTNESS),
    "context": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC, ScoreDimension.ROBUSTNESS),
    "asan": (ScoreDimension.ROBUSTNESS,),
    "tsan": (ScoreDimension.ROBUSTNESS,),
}
_RECIPE_FILE = "hidden/recipe.json"


class CppAnalyzer:
    """One analyzer: it declares a plan and parses the recorded output; it never executes."""

    def __init__(
        self,
        analyzer_id: str,
        identities: ImageIdentities,
        lock: ToolchainLock,
        profile: CppProfile,
    ) -> None:
        self.analyzer_id = analyzer_id
        self._identities = identities
        self._lock = lock
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
            languages=("cpp",),
            check_ids=checks or (f"cpp.{self.analyzer_id}.scan",),
            produces_dimensions=_DIMENSIONS[self.analyzer_id],
        )

    def plan(self, context: AnalysisContext) -> AnalysisPlan:
        for candidate in plans.analysis_plans(self._identities, self._lock, context):
            if candidate.analyzer_id == self.analyzer_id:
                return candidate
        raise ValueError(f"analyzer {self.analyzer_id!r} does not apply to this task")

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return parsers.PARSERS[self.analyzer_id](raw, plan, self._profile)


class CppLanguagePlugin:
    api_version = API_VERSION
    language_id = "cpp"
    # Layout hints for suite admission: plans put the candidate under ``work/`` and take candidate
    # sources, the hidden tests and the pinned recipe as separate, role-checked inputs.
    overlay_prefix = "work/"
    candidate_suffixes = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")

    def __init__(
        self,
        identities: ImageIdentities | None = None,
        lock: ToolchainLock | None = None,
        profile: CppProfile | None = None,
    ) -> None:
        self._identities = identities or load_identities()
        self._lock = lock or load_lock()
        self._profile = profile or load_profile()

    # ---------------------------------------------------------------- task authoring

    def validate_task(self, task: TaskDraft) -> ValidationReport:
        return validate_cpp_task(task, self.language_id)

    # ------------------------------------------------------------------------ plans

    def build_plan(self, task: FrozenTask, candidate: Candidate) -> BuildPlan:
        return plans.build_plan(self._identities, self._lock, task, candidate)

    def test_plan(self, task: FrozenTask) -> TestPlan:
        return plans.make_test_plan(self._identities, self._lock, task)

    def analysis_plans(self, context: AnalysisContext) -> list[AnalysisPlan]:
        return plans.analysis_plans(self._identities, self._lock, context)

    def performance_plan(self, task: FrozenTask) -> PerformancePlan | None:
        return plans.performance_plan(self._identities, self._lock, task)

    # -------------------------------------------------------------------- facts

    def symbols(self, source: ArtifactReader) -> SymbolIndex:
        return index_cpp_sources(source)

    def profile(self, version: str) -> LanguageProfile:
        if version != PROFILE_VERSION:
            raise ValueError(f"unknown C++ profile version {version!r}")
        return self._profile.profile

    # ------------------------------------------------------- evidence parsing

    def freeze_view(self, draft: TaskDraft, task_digest: str, task_version: int = 1) -> FrozenTask:
        """The slice of a validated task that plans are built from."""
        oracle = parse_oracle(draft.files["hidden/oracle.json"])
        quality = parse_quality_plan(draft.files["hidden/quality-plan.yaml"])
        recipe = parse_recipe(draft.files[_RECIPE_FILE])
        manifest = draft.manifest
        acceptance = manifest["acceptance"]
        quality_section = manifest["quality_plan"]
        runtime = manifest["runtime"]
        assert isinstance(acceptance, dict) and isinstance(quality_section, dict)
        assert isinstance(runtime, dict)
        view = json.loads(quality.model_dump_json())
        # The recipe travels inside the frozen task, and its digest travels with it, so a plan can
        # only ever build with the compiler, standard and profile the task was admitted with.
        view["recipe"] = json.loads(recipe.model_dump_json())
        view["recipe_digest"] = recipe.digest()
        document: dict[str, Any] = {
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
        # Strict models reject the manifest's lists, so the frozen task is published the way every
        # other plan and report is: as JSON.
        return FrozenTask.model_validate_json(json.dumps(document))

    def trusted_inputs(self, files: Mapping[str, bytes], view: FrozenTask) -> dict[str, bytes]:
        """The pinned recipe, serialized exactly as the plan's declared input digest hashes it.

        A plan declares a ``config`` input by the canonical digest of the *parsed* recipe, not by
        the digest of the file's bytes, so an author may reformat ``hidden/recipe.json`` without
        changing what the recipe is. Handing over the canonical bytes keeps that digest meaningful:
        the guest receives precisely the document the plan was built from.
        """
        recipe = quality_from_mapping(view.quality).recipe
        if recipe is None:
            raise ValueError("the frozen task carries no pinned recipe")
        return {"work/pcb_recipe.json": canonical_bytes(recipe)}

    def inventory(self, task: FrozenTask) -> tuple[InventoryGroup, ...]:
        return oracle_from_mapping(task.inventory).inventory()

    def parse_build(
        self, plan: object, raw: ArtifactReader
    ) -> tuple[Literal["pass", "fail", "incomplete"], str]:
        assert isinstance(plan, BuildPlan)
        status, diagnostics = parsers.build_status(raw, plan)
        if status == "pass":
            return "pass", "the candidate compiles under the pinned recipe"
        if status == "fail":
            return "fail", first_build_error(diagnostics)
        return "incomplete", "the build harness produced no usable compiler evidence"

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
            group, inventory, raw, repetition=repetition, candidate_paths=tuple(candidate)
        )

    # ------------------------------------------------------------------------ extras

    def analyzer(self, analyzer_id: str) -> CppAnalyzer:
        return CppAnalyzer(analyzer_id, self._identities, self._lock, self._profile)

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return self.analyzer(plan.analyzer_id).parse(raw, plan)

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        return self._profile.normalize(observations)

    @property
    def language_profile(self) -> CppProfile:
        """Publish the language-neutral resolver/scorer under the shared protocol name."""
        return self._profile

    def property_engine(
        self, task: FrozenTask, raw: Mapping[str, bytes]
    ) -> PropertyEngineIdentity:
        """C++ tasks use deterministic PCB_CHECK examples, not a generated property engine."""
        del task, raw
        return PropertyEngineIdentity(
            engine="pcb-cpp-check",
            engine_version="1",
            deterministic_policy="fixed-inputs",
        )

    @property
    def cpp_profile(self) -> CppProfile:
        return self._profile

    @property
    def toolchain(self) -> ToolchainLock:
        return self._lock

    @property
    def identities(self) -> ImageIdentities:
        return self._identities


def recipe_digest_of(view: FrozenTask) -> str:
    """The frozen task's recipe digest, recomputed from its content rather than trusted."""
    recipe = quality_from_mapping(view.quality).recipe
    if recipe is None:
        raise ValueError("the frozen task carries no pinned recipe")
    data = json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


__all__ = ["CppAnalyzer", "CppLanguagePlugin", "recipe_digest_of"]
