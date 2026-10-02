"""The Python language plugin (Technical Spec 18.1) and its analyzer plugins (12.3)."""

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

from polycodebench_lang_python import plans
from polycodebench_lang_python.identities import ImageIdentities, load_identities
from polycodebench_lang_python.parsers import PARSERS
from polycodebench_lang_python.profile import PROFILE_VERSION, PythonProfile, load_profile
from polycodebench_lang_python.symbols import index_python_sources
from polycodebench_lang_python.taskspec import (
    parse_oracle,
    parse_quality_plan,
    validate_python_task,
)
from polycodebench_lang_python.testparse import parse_group_report

_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "ruff": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "mypy": (ScoreDimension.CODE_QUALITY,),
    "bandit": (ScoreDimension.SECURITY,),
    "semgrep": (ScoreDimension.SECURITY,),
    "context": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC, ScoreDimension.ROBUSTNESS),
    "dependency": (ScoreDimension.SECURITY,),
}


class PythonAnalyzer:
    """One analyzer: it declares a plan and parses the recorded output; it never executes."""

    def __init__(
        self, analyzer_id: str, identities: ImageIdentities, profile: PythonProfile
    ) -> None:
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
            languages=("python",),
            check_ids=checks or (f"python.{self.analyzer_id}.scan",),
            produces_dimensions=_DIMENSIONS[self.analyzer_id],
        )

    def plan(self, context: AnalysisContext) -> AnalysisPlan:
        for candidate in plans.analysis_plans(self._identities, context):
            if candidate.analyzer_id == self.analyzer_id:
                return candidate
        raise ValueError(f"analyzer {self.analyzer_id!r} does not apply to this task")

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return PARSERS[self.analyzer_id](raw, plan, self._profile)


class PythonLanguagePlugin:
    api_version = API_VERSION
    language_id = "python"

    def __init__(self, identities: ImageIdentities | None = None) -> None:
        self._identities = identities or load_identities()
        self._profile = load_profile()

    # -------------------------------------------------------------------- task authoring

    def validate_task(self, task: TaskDraft) -> ValidationReport:
        return validate_python_task(task, self.language_id)

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
        return index_python_sources(source)

    def profile(self, version: str) -> LanguageProfile:
        if version != PROFILE_VERSION:
            raise ValueError(f"unknown Python profile version {version!r}")
        return self._profile.profile

    # ----------------------------------------------------- evidence parsing (executable)

    def freeze_view(self, draft: TaskDraft, task_digest: str, task_version: int = 1) -> FrozenTask:
        """The slice of a validated task that plans are built from."""
        oracle = parse_oracle(draft.files["hidden/oracle.json"])
        quality = parse_quality_plan(draft.files["hidden/quality-plan.yaml"])
        manifest = draft.manifest
        acceptance = manifest["acceptance"]
        quality_section = manifest["quality_plan"]
        runtime = manifest["runtime"]
        assert isinstance(acceptance, dict) and isinstance(quality_section, dict)
        assert isinstance(runtime, dict)
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
                    "quality": json.loads(quality.model_dump_json()),
                    "inventory": json.loads(oracle.model_dump_json()),
                    "inventory_digest": oracle.inventory_digest(),
                    "dependency_inventory": list(quality.dependency_inventory),
                }
            )
        )

    def inventory(self, task: FrozenTask) -> tuple[InventoryGroup, ...]:
        from polycodebench_lang_python.taskspec import oracle_from_mapping

        return oracle_from_mapping(task.inventory).inventory()

    def parse_build(
        self, plan: object, raw: ArtifactReader
    ) -> tuple[Literal["pass", "fail", "incomplete"], str]:
        from polycodebench_plugins_api import BuildPlan

        assert isinstance(plan, BuildPlan)
        status, record = plan_status(plan, raw)
        if status == "timed_out":
            return "incomplete", "build timed out"
        if status in {"tool_error", "output_missing"}:
            return "incomplete", f"build harness error ({status})"
        document = json.loads(raw.read("out/build.json"))
        broken = [f for f in document["files"] if not f["ok"]]
        if status == "completed" and not broken:
            return "pass", "all files compile"
        if status == "completed_with_findings" and broken:
            first = broken[0]
            return "fail", f"{first['path']}: {first.get('error')} line {first.get('line')}"
        return "incomplete", "build exit status disagrees with its report"

    def parse_test_group(
        self,
        group: TestGroupPlan,
        inventory: InventoryGroup,
        raw: ArtifactReader,
        *,
        repetition: int,
    ) -> tuple[list[TestCaseRecord], GroupControl]:
        return parse_group_report(group, inventory, raw, repetition=repetition)

    # --------------------------------------------------------------------- extras

    def analyzer(self, analyzer_id: str) -> PythonAnalyzer:
        return PythonAnalyzer(analyzer_id, self._identities, self._profile)

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return self.analyzer(plan.analyzer_id).parse(raw, plan)

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        return self._profile.normalize(observations)

    @property
    def language_profile(self) -> PythonProfile:
        return self._profile

    def property_engine(
        self, task: FrozenTask, raw: Mapping[str, bytes]
    ) -> PropertyEngineIdentity:
        """Report the Hypothesis/pytest versions and deterministic example policy from the run."""
        from polycodebench_lang_python.taskspec import oracle_from_mapping

        oracle = oracle_from_mapping(task.inventory)
        version: str | None = None
        examples = oracle.hypothesis_examples
        pytest_version: str | None = None
        for data in raw.values():
            for line in data.splitlines():
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(record, dict) or record.get("type") != "session_start":
                    continue
                pytest_version = record.get("pytest")
                hypothesis = record.get("hypothesis")
                if isinstance(hypothesis, dict):
                    version = hypothesis.get("version")
                    examples = int(hypothesis.get("max_examples", examples))
                break
            if pytest_version is not None:
                break
        return PropertyEngineIdentity(
            engine="hypothesis" if version else "pytest",
            engine_version=version or pytest_version or self._identities.runtime.tools.get("pytest"),
            deterministic_policy="derandomized-fixed-example-budget",
            examples_pinned=examples,
        )

    @property
    def python_profile(self) -> PythonProfile:
        return self._profile

    @property
    def identities(self) -> ImageIdentities:
        return self._identities
