"""The JavaScript and TypeScript language plugins (Technical Spec 18.1) and analyzer plugins.

One implementation, two identities. ``JavaScriptLanguagePlugin`` and ``TypeScriptLanguagePlugin``
differ in exactly five places - the language id, the profile document, the candidate suffixes, the
suffix that makes a task TypeScript, and whether a pinned compiler is expected - and share every
plan, parser, symbol index and profile rule. That is deliberate: the two languages share a runtime,
so the evidence they produce is comparable, and a fork between them would let the same JavaScript
defect be scored two different ways depending on which identity happened to be selected.

The differences that matter for scoring are data, not code:

* JavaScript's profile has no ``type_safety`` item and no ``type_domain_modeling`` idiom, and the
  TypeScript analyzer is never planned for a JavaScript task. A JavaScript candidate therefore
  cannot be deducted for lacking TypeScript types - the absence is structural, not conditional
  (Architecture 11.1, E2E-35).
* TypeScript's profile carries both, and ``typing_expectation`` on the task decides whether the
  pinned strict compiler runs at all. A TypeScript task that does not require typing gets no
  ``tsc`` plan, so it is never charged for type points it was never offered.
* Compiler strictness comes from the task's own ``tsconfig.json``, so the reference solution and
  every candidate are judged at the same strictness (Technical Spec 18.2).
"""

from __future__ import annotations

import hashlib
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

from polycodebench_lang_javascript import plans
from polycodebench_lang_javascript.identities import ImageIdentities, load_identities
from polycodebench_lang_javascript.locks import lock_digest
from polycodebench_lang_javascript.observations import scan_observation
from polycodebench_lang_javascript.parsers import PARSERS
from polycodebench_lang_javascript.profile import JsProfile, load_profile, profile_version
from polycodebench_lang_javascript.symbols import CANDIDATE_SUFFIXES, index_js_sources
from polycodebench_lang_javascript.taskspec import (
    parse_oracle,
    parse_quality_plan,
    quality_from_mapping,
    validate_js_task,
)
from polycodebench_lang_javascript.testparse import parse_group_report

_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "eslint": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "typescript": (ScoreDimension.CODE_QUALITY,),
    "context": (
        ScoreDimension.SECURITY,
        ScoreDimension.CODE_QUALITY,
        ScoreDimension.IDIOMATIC,
        ScoreDimension.ROBUSTNESS,
    ),
    "dependency": (ScoreDimension.SECURITY,),
}

_LOCK = "visible/repo/package-lock.json"
_SYNTAX_EXIT = 2


class JsAnalyzer:
    """One analyzer: it declares a plan and parses the recorded output; it never executes."""

    def __init__(
        self,
        analyzer_id: str,
        language: str,
        identities: ImageIdentities,
        profile: JsProfile,
    ) -> None:
        self.analyzer_id = analyzer_id
        self.language = language
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
            languages=(self.language,),
            check_ids=checks or (f"{self.language}.{self.analyzer_id}.scan",),
            produces_dimensions=_DIMENSIONS[self.analyzer_id],
        )

    def plan(self, context: AnalysisContext) -> AnalysisPlan:
        for candidate in plans.analysis_plans(self._identities, context):
            if candidate.analyzer_id == self.analyzer_id:
                return candidate
        raise ValueError(f"analyzer {self.analyzer_id!r} does not apply to this task")

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return PARSERS[self.analyzer_id](raw, plan, self._profile)


class _NodeLanguagePlugin:
    """Everything both identities share; the subclasses state only what actually differs."""

    api_version = API_VERSION
    language_id: str = "javascript"
    # Layout hints for suite admission: plans put the candidate sources, the task's own
    # `package.json`/`tsconfig.json` and the hidden tests under ``work/`` as separate, role-checked
    # inputs, so a candidate can never supply or modify the harness scaffolding.
    overlay_prefix = "work/"

    def __init__(self, identities: ImageIdentities | None = None) -> None:
        self._identities = identities or load_identities(self.language_id)
        self._profile = load_profile(self.language_id)

    # -------------------------------------------------------------------- task authoring

    def validate_task(self, task: TaskDraft) -> ValidationReport:
        return validate_js_task(task, self.language_id)

    # ------------------------------------------------------------------------ plans

    def build_plan(self, task: FrozenTask, candidate: Candidate) -> BuildPlan:
        return plans.build_plan(self._identities, task, candidate)

    def test_plan(self, task: FrozenTask) -> TestPlan:
        return plans.make_test_plan(self._identities, task)

    def analysis_plans(self, context: AnalysisContext) -> list[AnalysisPlan]:
        return plans.analysis_plans(self._identities, context)

    def performance_plan(self, task: FrozenTask) -> PerformancePlan | None:
        return plans.performance_plan(self._identities, task)

    # ----------------------------------------------------- evidence parsing (executable)

    def freeze_view(
        self, draft: TaskDraft, task_digest: str, task_version: int = 1
    ) -> FrozenTask:
        """The slice of a validated task that plans are built from."""
        oracle = parse_oracle(draft.files["hidden/oracle.json"])
        quality = parse_quality_plan(draft.files["hidden/quality-plan.yaml"])
        manifest = draft.manifest
        acceptance = manifest["acceptance"]
        quality_section = manifest["quality_plan"]
        runtime = manifest["runtime"]
        assert isinstance(acceptance, dict) and isinstance(quality_section, dict)
        assert isinstance(runtime, dict)
        required = set(acceptance["required_outputs"])
        # Trusted scaffolding: everything the candidate does not write itself (package.json,
        # package-lock.json, tsconfig.json and any fixed support module) is a digest-checked
        # `config` input, so a candidate cannot weaken the linter or the compiler at run time.
        config_files = {
            path.removeprefix("visible/repo/"): _digest(data)
            for path, data in sorted(draft.files.items())
            if path.startswith("visible/repo/")
            and path.removeprefix("visible/repo/") not in required
        }
        lock = draft.files.get(_LOCK)
        view = json.loads(quality.model_dump_json())
        view["config_files"] = config_files
        view["package_lock_digest"] = str(lock_digest(lock)) if lock is not None else None
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
        """The task's scaffolding (``config`` role) keyed by the plan input path that names it."""
        config = quality_from_mapping(view.quality).config_files
        return {f"work/{path}": files[f"visible/repo/{path}"] for path in config}

    def inventory(self, task: FrozenTask) -> tuple[InventoryGroup, ...]:
        return parse_oracle(
            json.dumps(task.inventory).encode("utf-8")
        ).inventory()

    def parse_build(
        self, plan: object, raw: ArtifactReader
    ) -> tuple[Literal["pass", "fail", "incomplete"], str]:
        assert isinstance(plan, BuildPlan)
        status, record = plan_status(plan, raw)
        if status == "timed_out":
            return "incomplete", "build timed out"
        if status in {"tool_error", "output_missing"}:
            return "incomplete", f"build harness error ({status})"
        if record is None:
            return "incomplete", "build harness error (no execution record)"
        output = raw.read("out/build.out").decode("utf-8", errors="replace") if plan.outputs else ""
        if record.exit_code == 0:
            return "pass", "the sources parse" if not self.is_typed else "the sources type-check"
        if record.exit_code == _SYNTAX_EXIT or (self.is_typed and record.exit_code == 2):
            head = next((line.strip() for line in output.splitlines() if line.strip()), "")
            detail = head if self.is_typed else "the sources do not parse as JavaScript"
            return "fail", f"{detail} ({head})" if self.is_typed else detail
        return "incomplete", f"build harness error (exit {record.exit_code})"

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

    def analyzer(self, analyzer_id: str) -> JsAnalyzer:
        return JsAnalyzer(analyzer_id, self.language_id, self._identities, self._profile)

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return self.analyzer(plan.analyzer_id).parse(raw, plan)

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        return self._profile.normalize(observations)

    def property_engine(
        self, task: FrozenTask, raw: Mapping[str, bytes]
    ) -> PropertyEngineIdentity:
        """Vitest is the runner; it has no declared example budget and no seed control.

        That is stated rather than borrowed from another language's policy. A runner with no seed
        knob says so in the evidence, instead of implying a determinism guarantee it does not
        provide.
        """
        del raw
        quality = quality_from_mapping(task.quality)
        return PropertyEngineIdentity(
            engine=quality.test_runner,
            engine_version=self._identities.images["runtime"].tools.get(quality.test_runner),
            deterministic_policy="runner-file-order",
            examples_pinned=None,
        )

    def profile(self, version: str) -> LanguageProfile:
        """The scored profile for a declared version.

        Every registered language plugin answers this, and the supervisor reads it rather than
        probing for a per-language attribute. An unknown version is an error rather than a silent
        return of whatever is loaded, so a task cannot be scored against a profile it did not name.
        """
        if version != profile_version(self.language_id):
            raise ValueError(f"unknown {self.language_id} profile version {version!r}")
        return self._profile.profile

    @property
    def language_profile(self) -> JsProfile:
        return self._profile

    @property
    def identities(self) -> ImageIdentities:
        return self._identities

    @property
    def candidate_suffixes(self) -> tuple[str, ...]:
        return CANDIDATE_SUFFIXES[self.language_id]

    @property
    def is_typed(self) -> bool:
        """Whether this identity compiles with the pinned TypeScript compiler."""
        return self.language_id == "typescript"


class _PropertyEngine:
    """The identity of the property-test engine, as the shared contract models it."""

    def __init__(
        self,
        *,
        engine: str,
        engine_version: str | None,
        deterministic_policy: str,
        examples_pinned: int | None,
    ) -> None:
        self.engine = engine
        self.engine_version = engine_version
        self.deterministic_policy = deterministic_policy
        self.examples_pinned = examples_pinned


class JavaScriptLanguagePlugin(_NodeLanguagePlugin):
    """JavaScript: no TypeScript types are required, offered or charged for.

    The absence is structural. This identity publishes no ``type_safety`` item, never gets a
    ``typescript`` analyzer and never invokes a compiler, so no scoring path can deduct a
    JavaScript candidate for lacking TypeScript types (Architecture 11.1, E2E-35).
    """

    language_id = "javascript"


class TypeScriptLanguagePlugin(_NodeLanguagePlugin):
    """TypeScript: the same Node runtime, with the pinned compiler and the typing items."""

    language_id = "typescript"


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


__all__ = [
    "JavaScriptLanguagePlugin",
    "JsAnalyzer",
    "TypeScriptLanguagePlugin",
]