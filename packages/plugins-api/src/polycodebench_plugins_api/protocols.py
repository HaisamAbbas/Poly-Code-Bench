"""Plugin protocols from Technical Spec 12.3 and 18.1."""

from __future__ import annotations

from typing import Literal, Protocol

from polycodebench_core.models import Candidate, Observation

from polycodebench_plugins_api.contracts import (
    AnalysisContext,
    AnalysisPlan,
    AnalyzerCapabilities,
    ArtifactReader,
    BuildPlan,
    FrozenTask,
    LanguageProfile,
    PerformancePlan,
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
