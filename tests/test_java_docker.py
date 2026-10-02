"""Real-container Java fixture admission (E2E-15/35 implementation evidence only).

Run with ``$env:PCB_TEST_DOCKER='1'; uv run pytest -q tests/test_java_docker.py`` after
``scripts/build_java_images.py``. These authored fixtures are not model benchmark results.

Every case here plans with the real plugin, executes in the pinned Java images with no network, and
parses the recorded bytes back through the production parsers. Nothing is simulated, so a stale
image identity, an analyzer artifact missing from the offline repository, or a guest that cannot
start Maven fails an assertion instead of producing a plausible-looking observation.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from java_plugin_support import TASK_ROOT, draft
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, MeasurementStatus
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_lang_java import JavaLanguagePlugin
from polycodebench_plugins_api import AnalysisContext, TaskDraft
from polycodebench_plugins_api.testreport import reconcile
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real Java image admission is opt-in (PCB_TEST_DOCKER=1)",
)

SOURCE = "src/main/java/demo/TopWords.java"


@dataclass(frozen=True)
class JavaContext:
    """Everything a case needs, built once so the plugin is not reconstructed per test."""

    draft: TaskDraft
    plugin: JavaLanguagePlugin
    view: Any
    inventory: dict[str, Any]
    groups: dict[str, Any]


@pytest.fixture(scope="module")
def java() -> JavaContext:
    package = draft()
    plugin = JavaLanguagePlugin()
    view = plugin.freeze_view(package, "sha256:" + "1" * 64)
    return JavaContext(
        package,
        plugin,
        view,
        {group.group_id: group for group in plugin.inventory(view)},
        {group.group_id: group for group in plugin.test_plan(view).groups},
    )


def _candidate(context: JavaContext) -> Candidate:
    return Candidate.model_validate(
        {
            "kind": "candidate",
            "schema_version": 1,
            "candidate_id": new_entity_id(),
            "run_id": new_entity_id(),
            "task_id": context.view.task_id,
            "task_version": context.view.task_version,
            "sample_index": 0,
            "submission_kind": "source_bundle",
            "payload_digest": "sha256:" + "2" * 64,
            "artifact_ids": [],
            "frozen_at": None,
        }
    )


def _sources(context: JavaContext, variant: str) -> dict[str, dict[str, bytes]]:
    """The input roles the Java plans declare, for one fixture variant.

    The candidate is read from the manifest rather than from a hard-coded path so that a fixture
    cannot silently stop exercising the file the manifest names.
    """
    manifest = yaml.safe_load((TASK_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    entry = next(
        item
        for item in manifest["fixtures"]
        if item.get("language_variant", item["variant"]) == variant
    )
    return {
        "candidate": {SOURCE: context.draft.files[str(entry["solution_path"])]},
        "overlay": {
            "work/" + path.removeprefix("hidden/"): data
            for path, data in context.draft.files.items()
            if path.startswith("hidden/tests/")
        },
        "config": context.plugin.trusted_inputs(context.draft.files, context.view),
    }


def _runner(context: JavaContext, label: str) -> PlanRunner:
    ids = context.plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / f"java-docker-test-{label}",
        # The provider caps this at 120s. A full offline Maven `test` on this task measures about
        # 30s (JVM start plus Surefire), so 120s leaves ample headroom.
        operation_timeout_seconds=120,
    )
    return PlanRunner(provider, lane="admission")


async def _build(context: JavaContext, variant: str, label: str) -> None:
    sources = _sources(context, variant)
    plan = context.plugin.build_plan(context.view, _candidate(context))
    result = await _runner(context, label).run(
        plan, materialize_inputs(plan, sources), stage_id="java-fixture-admission"
    )
    status, reason = context.plugin.parse_build(plan, result.reader())
    # Every admitted variant must compile. A build failure would mean the fixture no longer tests
    # the behaviour its variant is named for.
    assert status == "pass", (variant, status, reason)


async def _group_gate(context: JavaContext, variant: str, group_id: str, label: str) -> Any:
    group = context.groups[group_id]
    sources = _sources(context, variant)
    runner = _runner(context, label)
    records: list[Any] = []
    controls: list[Any] = []
    for repetition in range(group.repetitions):
        result = await runner.run(
            group.plan,
            materialize_inputs(group.plan, sources),
            stage_id="java-fixture-admission",
        )
        got, control = context.plugin.parse_test_group(
            group, context.inventory[group_id], result.reader(), repetition=repetition
        )
        records.extend(got)
        controls.append(control)
    return reconcile((context.inventory[group_id],), records, controls), controls


def test_java_reference_passes_all_groups_and_the_evaluator_analyzers(java: JavaContext) -> None:
    async def run() -> None:
        context = java
        report = context.plugin.validate_task(context.draft)
        assert report.ok, [issue.model_dump() for issue in report.issues]
        assert context.view.image_digest == context.plugin.identities.runtime.digest
        await _build(context, "reference", "reference-build")

        records: list[Any] = []
        controls: list[Any] = []
        runner = _runner(context, "reference-tests")
        sources = _sources(context, "reference")
        for group in context.groups.values():
            for repetition in range(group.repetitions):
                result = await runner.run(
                    group.plan,
                    materialize_inputs(group.plan, sources),
                    stage_id="java-fixture-admission",
                )
                got, control = context.plugin.parse_test_group(
                    group, context.inventory[group.group_id], result.reader(), repetition=repetition
                )
                records.extend(got)
                controls.append(control)
        verdict = reconcile(tuple(context.inventory.values()), records, controls)
        assert verdict.gate == "pass", verdict

        analysis = AnalysisContext(
            task=context.view,
            candidate_digest="sha256:" + "3" * 64,
            candidate_paths=(SOURCE,),
        )
        plans = {plan.analyzer_id: plan for plan in context.plugin.analysis_plans(analysis)}
        assert set(plans) == {"spotbugs", "pmd", "checkstyle", "context", "dependency"}
        for analyzer_id, plan in plans.items():
            result = await runner.run(
                plan,
                materialize_inputs(plan, sources),
                stage_id="java-fixture-admission",
            )
            observations = context.plugin.parse_analysis(result.reader(), plan)
            scan = next(item for item in observations if item.check_id.endswith(".scan"))
            # A scan that is not MEASURED means the image could not run the tool. The shared
            # contract requires that be visible rather than read as "no findings".
            assert scan.status == MeasurementStatus.MEASURED, (analyzer_id, observations)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("variant", "group_id", "expected"),
    (
        # An alternative design must be accepted: the task constrains behaviour, not spelling.
        ("alternative", "behaviour", "pass"),
        ("faulty", "behaviour", "fail"),
        ("null_unsafe", "behaviour", "fail"),
        ("timeout", "behaviour", "fail"),
    ),
)
def test_java_correctness_fixture_variant_has_its_declared_acceptance_outcome(
    java: JavaContext, variant: str, group_id: str, expected: str
) -> None:
    """These variants are wrong about behaviour, so the JUnit gate must reject them.

    The resource and concurrency variants are a different kind of fixture and are checked by
    ``test_java_robustness_fixtures_are_detected_by_their_analyzer`` instead: they behave
    correctly and are wrong about design, so a passing test group is the correct outcome for them.
    Asserting they fail the gate would be asserting something untrue.
    """

    async def run() -> None:
        await _build(java, variant, f"{variant}-build")
        verdict, _controls = await _group_gate(java, variant, group_id, f"{variant}-{group_id}")
        assert verdict.gate == expected, (variant, verdict)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("variant", "check_prefix"),
    (
        ("resource_leak", "java.context.resource-unclosed"),
        ("concurrent_defect", "java.context.published-mutable-state"),
    ),
)
def test_java_robustness_fixtures_are_detected_by_their_analyzer(
    java: JavaContext, variant: str, check_prefix: str
) -> None:
    """A defect that does not change behaviour must still be caught by an analyzer.

    These variants pass their JUnit group, and that is correct: a leaked stream on a path nothing
    exercises, or a shared map nothing reads back, is not a wrong answer. What must catch them is
    the context scanner, so that is what is asserted here. Asserting the gate failed instead would
    be asserting something untrue about what these fixtures do.
    """

    async def run() -> None:
        context = java
        await _build(context, variant, f"{variant}-build")
        sources = _sources(context, variant)
        analysis = AnalysisContext(
            task=context.view,
            candidate_digest="sha256:" + "7" * 64,
            candidate_paths=(SOURCE,),
        )
        plans = list(context.plugin.analysis_plans(analysis))
        plan = next(item for item in plans if item.analyzer_id == "context")
        result = await _runner(context, f"{variant}-context").run(
            plan,
            materialize_inputs(plan, sources),
            stage_id="java-fixture-admission",
        )
        observations = context.plugin.parse_analysis(result.reader(), plan)
        assert any(
            item.check_id == check_prefix and item.status == MeasurementStatus.MEASURED
            for item in observations
        ), (variant, check_prefix, observations)

    asyncio.run(run())


def test_java_a_nonterminating_candidate_is_killed_rather_than_left_running(
    java: JavaContext,
) -> None:
    async def run() -> None:
        verdict, controls = await _group_gate(java, "timeout", "behaviour", "timeout-named")
        assert verdict.gate == "fail"
        assert any(control.status == "candidate_timeout" for control in controls), controls

    asyncio.run(run())


def test_java_security_fixture_is_detected_by_the_context_analyzer(java: JavaContext) -> None:
    async def run() -> None:
        context = java
        await _build(context, "security_defective", "security-build")
        analysis = AnalysisContext(
            task=context.view,
            candidate_digest="sha256:" + "4" * 64,
            candidate_paths=(SOURCE,),
        )
        plans = list(context.plugin.analysis_plans(analysis))
        plan = next(item for item in plans if item.analyzer_id == "context")
        sources = _sources(context, "security_defective")
        result = await _runner(context, "security-context").run(
            plan,
            materialize_inputs(plan, sources),
            stage_id="java-fixture-admission",
        )
        observations = context.plugin.parse_analysis(result.reader(), plan)
        assert any(
            item.check_id == "java.context.command-injection"
            and item.status == MeasurementStatus.MEASURED
            for item in observations
        ), observations

    asyncio.run(run())


def test_java_the_recipe_distinction_and_frozen_modes_are_real(java: JavaContext) -> None:
    """Isolation and the frozen JIT policy must hold in the artifacts, not only in the record.

    A runtime image that could run SpotBugs would let a candidate inspect the tools that judge it,
    and a measurement mode selectable per run would make two candidates incomparable.
    """
    ids = java.plugin.identities
    for tool in ("spotbugs", "pmd", "checkstyle"):
        assert ids.runtime.tools[tool] == "absent", tool
        assert ids.evaluator.tools[tool] != "absent", tool
    modes = ids.build.jvm_measurement
    assert set(modes) == {"cold", "steady-state"}
    assert "-Xint" in modes["cold"]
    assert "-Xint" not in modes["steady-state"]
