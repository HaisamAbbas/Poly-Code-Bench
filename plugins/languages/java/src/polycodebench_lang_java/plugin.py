"""The Java language plugin (Technical Spec 18.1) and its analyzer plugins (12.3)."""

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
    DictArtifactReader,
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

from polycodebench_lang_java import plans
from polycodebench_lang_java.identities import ImageIdentities, load_identities
from polycodebench_lang_java.locks import lock_digest
from polycodebench_lang_java.parsers import (
    PARSERS,
    first_build_error,
    maven_build_errors,
)
from polycodebench_lang_java.profile import PROFILE_VERSION, JavaProfile, load_profile
from polycodebench_lang_java.symbols import index_java_sources
from polycodebench_lang_java.taskspec import (
    oracle_from_mapping,
    parse_oracle,
    parse_quality_plan,
    quality_from_mapping,
    validate_java_task,
)
from polycodebench_lang_java.testparse import parse_group_report

_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "spotbugs": (ScoreDimension.ROBUSTNESS, ScoreDimension.CODE_QUALITY),
    "pmd": (ScoreDimension.CODE_QUALITY,),
    "checkstyle": (ScoreDimension.CODE_QUALITY,),
    "context": (ScoreDimension.CODE_QUALITY, ScoreDimension.ROBUSTNESS),
    "dependency": (ScoreDimension.SECURITY,),
}
_POM = "pom.xml"
_LOCK = "deps.lock.json"


class JavaAnalyzer:
    """One analyzer: it declares a plan and parses the recorded output; it never executes."""

    def __init__(self, analyzer_id: str, identities: ImageIdentities, profile: JavaProfile) -> None:
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
            languages=("java",),
            check_ids=checks or (f"java.{self.analyzer_id}.scan",),
            produces_dimensions=_DIMENSIONS[self.analyzer_id],
        )

    def plan(self, context: AnalysisContext) -> AnalysisPlan:
        for candidate in plans.analysis_plans(self._identities, context):
            if candidate.analyzer_id == self.analyzer_id:
                return candidate
        raise ValueError(f"analyzer {self.analyzer_id!r} does not apply to this task")

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return PARSERS[self.analyzer_id](raw, plan, self._profile)


class JavaLanguagePlugin:
    api_version = API_VERSION
    language_id = "java"
    # Layout hints for suite admission: plans put the project under ``work/`` and take candidate
    # sources, hidden tests and trusted scaffolding as separate, role-checked inputs.
    overlay_prefix = "work/"
    candidate_suffixes = (".java",)

    def __init__(self, identities: ImageIdentities | None = None) -> None:
        self._identities = identities or load_identities()
        self._profile = load_profile()

    # -------------------------------------------------------------------- task authoring

    def validate_task(self, task: TaskDraft) -> ValidationReport:
        return validate_java_task(task, self.language_id)

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
        return index_java_sources(source)

    def profile(self, version: str) -> LanguageProfile:
        if version != PROFILE_VERSION:
            raise ValueError(f"unknown Java profile version {version!r}")
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
        # Trusted project scaffolding: everything the candidate does not write itself (the POM and
        # the frozen dependency lock) is a digest-checked `config` input. The lock is authoritative:
        # the visible copy is only what the task author showed the model, and the hidden copy is
        # what admission resolved from `mvn -o dependency:list`.
        pinned_files = {
            _POM: "sha256:" + hashlib.sha256(draft.files["visible/repo/pom.xml"]).hexdigest(),
            _LOCK: "sha256:" + hashlib.sha256(draft.files["hidden/deps.lock.json"]).hexdigest(),
        }
        view = json.loads(quality.model_dump_json())
        view["pinned_files"] = pinned_files
        view["dependency_lock_digest"] = str(lock_digest(draft.files["hidden/deps.lock.json"]))
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
        """The project scaffold (``config`` role) keyed by the plan input path that names it."""
        quality = quality_from_mapping(view.quality)
        return (
            {
                f"work/{_POM}": files["visible/repo/pom.xml"],
                f"work/{_LOCK}": files["hidden/deps.lock.json"],
            }
            if set(quality.pinned_files) == {_POM, _LOCK}
            else {}
        )

    def inventory(self, task: FrozenTask) -> tuple[InventoryGroup, ...]:
        return oracle_from_mapping(task.inventory).inventory()

    def parse_build(
        self, plan: object, raw: ArtifactReader
    ) -> tuple[Literal["pass", "fail", "incomplete"], str]:
        assert isinstance(plan, BuildPlan)
        status, record = plan_status(plan, raw)
        if status == "timed_out":
            return "incomplete", "build timed out"
        if status in {"tool_error", "output_missing"}:
            return "incomplete", f"build harness error ({status})"
        present = set(raw.list())
        text = (
            raw.read("out/build.out").decode("utf-8", errors="replace")
            if "out/build.out" in present
            else ""
        )
        err_text = (
            raw.read("out/build.err").decode("utf-8", errors="replace")
            if "out/build.err" in present
            else ""
        )
        combined = text + "\n" + err_text
        errors = maven_build_errors(combined)
        if status == "completed" and not errors:
            return "pass", "the project compiles"
        if status == "completed_with_findings" and errors:
            return "fail", first_build_error(combined)
        if status == "completed" and errors:
            # Maven exited 0 yet reported a compile error: the status and the log disagree, so the
            # build is not trusted in either direction.
            return "incomplete", "build exit status disagrees with Maven's own diagnostics"
        del record
        return "incomplete", "build output could not be parsed"

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

    def analyzer(self, analyzer_id: str) -> JavaAnalyzer:
        return JavaAnalyzer(analyzer_id, self._identities, self._profile)

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return self.analyzer(plan.analyzer_id).parse(raw, plan)

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        return self._profile.normalize(observations)

    @property
    def language_profile(self) -> JavaProfile:
        """The profile the supervisor scores against, under the name every language publishes.

        The supervisor must not probe for ``python_profile``/``rust_profile``/``java_profile``; one
        attribute that any language provides is what keeps adding a language out of shared code.
        """
        return self._profile

    def property_engine(self, task: FrozenTask, raw: Mapping[str, bytes]) -> PropertyEngineIdentity:
        """JUnit 5 is this language's property engine, and surefire pins its determinism.

        Stated by the plugin rather than derived by the supervisor from ``primary_language``. The
        engine version is read from the recorded surefire report when present, so evidence names
        the engine that actually ran instead of a compile-time constant.
        """
        reader = DictArtifactReader(dict(raw))
        version: str | None = None
        for path in reader.list():
            if path.startswith("work/target/surefire-reports/TEST-") and path.endswith(".xml"):
                from xml.etree import ElementTree

                try:
                    document = ElementTree.fromstring(
                        reader.read(path).decode("utf-8", errors="replace")
                    )
                except ElementTree.ParseError:
                    continue
                properties = document.find("properties")
                if properties is not None:
                    for entry in properties.findall("property"):
                        if entry.get("name") == "surefire.version":
                            version = entry.get("value")
                            break
                break
        return PropertyEngineIdentity(
            engine="junit5-surefire",
            engine_version=version or self._identities.build.maven,
            # Surefire runs each test method once from a fixed source seed; there is no property
            # generator, so the policy says so rather than borrowing Hypothesis' vocabulary.
            deterministic_policy="junit5-source-seeded",
            examples_pinned=None,
        )

    @property
    def java_profile(self) -> JavaProfile:
        """Alias kept for the Rust-style accessor; ``language_profile`` is the contract."""
        return self._profile

    @property
    def identities(self) -> ImageIdentities:
        return self._identities
