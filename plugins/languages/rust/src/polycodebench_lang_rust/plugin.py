"""The Rust language plugin (Technical Spec 18.1) and its analyzer plugins (12.3)."""

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

from polycodebench_lang_rust import plans
from polycodebench_lang_rust.identities import ImageIdentities, load_identities
from polycodebench_lang_rust.locks import lock_digest
from polycodebench_lang_rust.parsers import (
    PARSERS,
    build_finished,
    cargo_messages,
    first_build_error,
)
from polycodebench_lang_rust.profile import PROFILE_VERSION, RustProfile, load_profile
from polycodebench_lang_rust.symbols import index_rust_sources
from polycodebench_lang_rust.taskspec import (
    oracle_from_mapping,
    parse_oracle,
    parse_quality_plan,
    quality_from_mapping,
    validate_rust_task,
)
from polycodebench_lang_rust.testparse import parse_group_report

_DIMENSIONS: dict[str, tuple[ScoreDimension, ...]] = {
    "clippy": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
    "context": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC, ScoreDimension.ROBUSTNESS),
    "miri": (ScoreDimension.ROBUSTNESS,),
    "dependency": (ScoreDimension.SECURITY,),
}
_CRATE_FILES = ("Cargo.toml", "Cargo.lock")


class RustAnalyzer:
    """One analyzer: it declares a plan and parses the recorded output; it never executes."""

    def __init__(self, analyzer_id: str, identities: ImageIdentities, profile: RustProfile) -> None:
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
            languages=("rust",),
            check_ids=checks or (f"rust.{self.analyzer_id}.scan",),
            produces_dimensions=_DIMENSIONS[self.analyzer_id],
        )

    def plan(self, context: AnalysisContext) -> AnalysisPlan:
        for candidate in plans.analysis_plans(self._identities, context):
            if candidate.analyzer_id == self.analyzer_id:
                return candidate
        raise ValueError(f"analyzer {self.analyzer_id!r} does not apply to this task")

    def parse(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return PARSERS[self.analyzer_id](raw, plan, self._profile)


class RustLanguagePlugin:
    api_version = API_VERSION
    language_id = "rust"
    # Layout hints for suite admission: plans put the crate under ``work/`` and take candidate
    # sources, hidden tests and trusted scaffolding as separate, role-checked inputs.
    overlay_prefix = "work/"
    candidate_suffixes = (".rs",)

    def __init__(self, identities: ImageIdentities | None = None) -> None:
        self._identities = identities or load_identities()
        self._profile = load_profile()

    # -------------------------------------------------------------------- task authoring

    def validate_task(self, task: TaskDraft) -> ValidationReport:
        return validate_rust_task(task, self.language_id)

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
        return index_rust_sources(source)

    def profile(self, version: str) -> LanguageProfile:
        if version != PROFILE_VERSION:
            raise ValueError(f"unknown Rust profile version {version!r}")
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
        # Trusted crate scaffolding: everything the candidate does not write itself (Cargo.toml,
        # Cargo.lock and any fixed support module) is a digest-checked `config` input.
        crate_files = {
            path.removeprefix("visible/repo/"): "sha256:" + hashlib.sha256(data).hexdigest()
            for path, data in sorted(draft.files.items())
            if path.startswith("visible/repo/")
            and path.removeprefix("visible/repo/") not in required
        }
        lock = draft.files.get("visible/repo/Cargo.lock")
        view = json.loads(quality.model_dump_json())
        view["crate_files"] = crate_files
        view["cargo_lock_digest"] = str(lock_digest(lock)) if lock is not None else None
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
        """The crate scaffold (``config`` role) keyed by the plan input path that names it."""
        crate = quality_from_mapping(view.quality).crate_files
        return {f"work/{path}": files[f"visible/repo/{path}"] for path in crate}

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
        try:
            messages = cargo_messages(raw, "build")
        except (ValueError, KeyError):
            return "incomplete", "build output could not be parsed"
        finished = build_finished(messages)
        if status == "completed" and finished is True:
            return "pass", "the crate compiles"
        if status == "completed_with_findings" and finished is False:
            return "fail", first_build_error(messages)
        return "incomplete", "build exit status disagrees with Cargo's own report"

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

    def analyzer(self, analyzer_id: str) -> RustAnalyzer:
        return RustAnalyzer(analyzer_id, self._identities, self._profile)

    def parse_analysis(self, raw: ArtifactReader, plan: AnalysisPlan) -> list[Observation]:
        return self.analyzer(plan.analyzer_id).parse(raw, plan)

    def normalize(self, observations: list[Observation]) -> list[Observation]:
        return self._profile.normalize(observations)

    @property
    def language_profile(self) -> RustProfile:
        return self._profile

    def property_engine(self, task: FrozenTask, raw: Mapping[str, bytes]) -> PropertyEngineIdentity:
        """Rust evidence comes from fixed Cargo test cases, not a generated property engine."""
        del task, raw
        return PropertyEngineIdentity(
            engine="cargo-test",
            engine_version=self._identities.runtime.cargo,
            deterministic_policy="fixed-test-cases",
        )

    @property
    def rust_profile(self) -> RustProfile:
        return self._profile

    @property
    def identities(self) -> ImageIdentities:
        return self._identities
