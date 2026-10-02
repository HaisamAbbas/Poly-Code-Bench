"""Plugin protocols from Technical Spec 12.3 and 18.1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from polycodebench_core.models import Candidate, Observation, ScoreDimension

from polycodebench_plugins_api.contracts import (
    AnalysisContext,
    AnalysisPlan,
    AnalyzerCapabilities,
    ArtifactReader,
    BuildPlan,
    FrozenTask,
    LanguageProfile,
    PerformancePlan,
    ProfileResult,
    PropertyEngineIdentity,
    RuleMapping,
    SymbolIndex,
    TaskDraft,
    TestGroupPlan,
    TestPlan,
    ValidationReport,
)
from polycodebench_plugins_api.testreport import GroupControl, InventoryGroup, TestCaseRecord


class LanguagePlugin(Protocol):
    api_version: int
    language_id: str

    def validate_task(self, task: TaskDraft) -> ValidationReport: ...

    def build_plan(self, task: FrozenTask, candidate: Candidate) -> BuildPlan: ...

    def test_plan(self, task: FrozenTask) -> TestPlan: ...

    def analysis_plans(self, context: AnalysisContext) -> list[AnalysisPlan]: ...

    def performance_plan(self, task: FrozenTask) -> PerformancePlan | None: ...

    def symbols(self, source: ArtifactReader) -> SymbolIndex: ...

    def profile(self, version: str) -> LanguageProfile: ...


class LanguageProfileEvaluator(Protocol):
    """The profile object every language plugin publishes as ``language_profile``.

    The supervisor asks a plugin three questions: which rule mapping covers a check id, which
    composite dimension owns the resulting issue, and what the frozen opportunities plus the
    recorded observations score. Naming this shape is what keeps the evaluator language-neutral:
    it never asks *which* language produced the evidence, only what that language says about it.
    """

    def resolve(self, check_id: str) -> RuleMapping | None: ...

    def owner(self, check_id: str) -> ScoreDimension | None: ...

    def evaluate(
        self,
        *,
        opportunities: Mapping[str, int],
        observations: Sequence[Observation],
        required_tools: Sequence[str],
    ) -> ProfileResult: ...


class AnalyzerPlugin(Protocol):
    def capabilities(self) -> AnalyzerCapabilities: ...

    def plan(self, context: AnalysisContext) -> AnalysisPlan: ...

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]: ...


class ExecutableLanguagePlugin(LanguagePlugin, Protocol):
    """A language plugin that can also read back the evidence of its own plans.

    The supervisor runs plans; these methods turn recorded bytes into evidence. They never
    execute anything.
    """

    def freeze_view(
        self, draft: TaskDraft, task_digest: str, task_version: int = 1
    ) -> FrozenTask: ...

    def inventory(self, task: FrozenTask) -> tuple[InventoryGroup, ...]: ...

    def parse_build(
        self, plan: object, raw: ArtifactReader
    ) -> tuple[Literal["pass", "fail", "incomplete"], str]: ...

    def parse_test_group(
        self,
        group: TestGroupPlan,
        inventory: InventoryGroup,
        raw: ArtifactReader,
        *,
        repetition: int,
    ) -> tuple[list[TestCaseRecord], GroupControl]: ...

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]: ...

    def normalize(self, observations: list[Observation]) -> list[Observation]: ...

    @property
    def language_profile(self) -> LanguageProfileEvaluator:
        """The diagnostic/idiom profile this identity publishes: one attribute, any language."""
        ...

    def property_engine(
        self, task: FrozenTask, raw: Mapping[str, bytes]
    ) -> PropertyEngineIdentity:
        """Identity of the property-test engine that produced ``raw`` for ``task``.

        Stated by the plugin rather than derived by the supervisor from ``primary_language``, so
        a new language never requires editing an engine switch in shared evaluation code.
        """
        ...
