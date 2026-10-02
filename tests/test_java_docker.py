"""Real-container Java fixture admission (E2E-15/35 implementation evidence only).

Run with ``$env:PCB_TEST_DOCKER='1'; uv run pytest -q tests/test_java_docker.py`` after
``scripts/build_java_images.py``. These authored fixtures are not model benchmark results.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "languages" / "java" / "src"))

from java_plugin_support import TASK_ROOT, draft  # noqa: E402
from polycodebench_core.identity import new_entity_id  # noqa: E402
from polycodebench_core.models import Candidate, MeasurementStatus  # noqa: E402
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs  # noqa: E402
from polycodebench_lang_java import JavaLanguagePlugin  # noqa: E402
from polycodebench_plugins_api import AnalysisContext  # noqa: E402
from polycodebench_plugins_api.testreport import reconcile  # noqa: E402
from polycodebench_runner.provider import LocalDockerSandboxProvider  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real Java image admission is opt-in (PCB_TEST_DOCKER=1)",
)

SOURCE = "src/main/java/demo/TopWords.java"
PACKAGE = draft()
plugin = JavaLanguagePlugin()
VIEW = plugin.freeze_view(PACKAGE, "sha256:" + "1" * 64)
INVENTORY = {group.group_id: group for group in plugin.inventory(VIEW)}
GROUPS = {group.group_id: group for group in plugin.test_plan(VIEW).groups}


def _candidate() -> Candidate:
    return Candidate.model_validate(
        {
            "kind": "candidate",
            "schema_version": 1,
            "candidate_id": new_entity_id(),
            "run_id": new_entity_id(),
            "task_id": VIEW.task_id,
            "task_version": VIEW.task_version,
            "sample_index": 0,
            "submission_kind": "source_bundle",
            "payload_digest": "sha256:" + "2" * 64,
            "artifact_ids": [],
            "frozen_at": None,
        }
    )


def _sources(variant: str) -> dict[str, dict[str, bytes]]:
    fixture = yaml.safe_load((TASK_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    entry = next(item for item in fixture["fixtures"] if item["variant"] == variant)
    candidate_path = str(entry["solution_path"])
    candidate = PACKAGE.files[candidate_path]
    overlay = {
        "work/" + path.removeprefix("hidden/"): data
        for path, data in PACKAGE.files.items()
        if path.startswith("hidden/tests/")
    }
    return {
        "candidate": {SOURCE: candidate},
        "overlay": overlay,
        "config": plugin.trusted_inputs(PACKAGE.files, VIEW),
    }


def _runner(label: str) -> PlanRunner:
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / f"java-docker-test-{label}",
        operation_timeout_seconds=180,
    )
    return PlanRunner(provider, lane="admission")


async def _build(variant: str, label: str) -> None:
    sources = _sources(variant)
    plan = plugin.build_plan(VIEW, _candidate())
    result = await _runner(label).run(
        plan, materialize_inputs(plan, sources), stage_id="java-fixture-admission"
    )
    status, reason = plugin.parse_build(plan, result.reader())
    assert status == "pass", (variant, status, reason)


async def _group_gate(variant: str, group_id: str, label: str) -> Any:
    group = GROUPS[group_id]
    sources = _sources(variant)
    runner = _runner(label)
    records: list[Any] = []
    controls: list[Any] = []
    for repetition in range(group.repetitions):
        result = await runner.run(
            group.plan,
            materialize_inputs(group.plan, sources),
            stage_id="java-fixture-admission",
        )
        got, control = plugin.parse_test_group(
            group, INVENTORY[group_id], result.reader(), repetition=repetition
        )
        records.extend(got)
        controls.append(control)
    return reconcile((INVENTORY[group_id],), records, controls)


def test_java_reference_passes_all_groups_and_the_evaluator_analyzers() -> None:
    async def run() -> None:
        report = plugin.validate_task(PACKAGE)
        assert report.ok, [issue.model_dump() for issue in report.issues]
        assert VIEW.image_digest == plugin.identities.runtime.digest
        await _build("reference", "reference-build")

        groups = tuple(INVENTORY.values())
        records: list[Any] = []
        controls: list[Any] = []
        runner = _runner("reference-tests")
        sources = _sources("reference")
        for group in GROUPS.values():
            for repetition in range(group.repetitions):
                result = await runner.run(
                    group.plan,
                    materialize_inputs(group.plan, sources),
                    stage_id="java-fixture-admission",
                )
                got, control = plugin.parse_test_group(
                    group, INVENTORY[group.group_id], result.reader(), repetition=repetition
                )
                records.extend(got)
                controls.append(control)
        verdict = reconcile(groups, records, controls)
        assert verdict.gate == "pass", verdict

        context = AnalysisContext(
            task=VIEW,
            candidate_digest="sha256:" + "3" * 64,
            candidate_paths=(SOURCE,),
        )
        plans = plugin.analysis_plans(context)
        assert {plan.analyzer_id for plan in plans} == {
            "spotbugs",
            "pmd",
            "checkstyle",
            "context",
            "dependency",
        }
        for plan in plans:
            result = await runner.run(
                plan,
                materialize_inputs(plan, sources),
                stage_id="java-fixture-admission",
            )
            observations = plugin.parse_analysis(result.reader(), plan)
            scan = next(item for item in observations if item.check_id.endswith(".scan"))
            assert scan.status == MeasurementStatus.MEASURED, (plan.analyzer_id, observations)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("variant", "group_id"),
    (
        ("alternative", "behaviour"),
        ("faulty", "behaviour"),
        ("null_unsafe", "behaviour"),
        ("resource_leak", "resource-probe"),
        ("concurrent_defect", "concurrency-probe"),
        ("timeout", "behaviour"),
    ),
)
def test_java_fixture_variant_has_its_declared_acceptance_outcome(
    variant: str, group_id: str
) -> None:
    async def run() -> None:
        await _build(variant, f"{variant}-build")
        verdict = await _group_gate(variant, group_id, f"{variant}-{group_id}")
        if variant == "alternative":
            assert verdict.gate == "pass", verdict
        else:
            assert verdict.gate == "fail", verdict

    asyncio.run(run())


def test_java_security_fixture_is_detected_by_the_context_analyzer() -> None:
    async def run() -> None:
        await _build("security_defective", "security-build")
        context = AnalysisContext(
            task=VIEW,
            candidate_digest="sha256:" + "4" * 64,
            candidate_paths=(SOURCE,),
        )
        plan = next(
            item for item in plugin.analysis_plans(context) if item.analyzer_id == "context"
        )
        result = await _runner("security-context").run(
            plan,
            materialize_inputs(plan, _sources("security_defective")),
            stage_id="java-fixture-admission",
        )
        observations = plugin.parse_analysis(result.reader(), plan)
        assert any(
            item.check_id == "java.context.command-injection"
            and item.status == MeasurementStatus.MEASURED
            for item in observations
        ), observations

    asyncio.run(run())
