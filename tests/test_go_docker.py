"""Real-sandbox evidence for the Go plugin (opt-in: PCB_TEST_DOCKER=1).

E2E-15/35 (Go subcase): the ``top-words`` fixture is planned by the plugin, executed by the
supervisor in the pinned images and parsed back into evidence. Every check here runs a real Go
toolchain: the acceptance gate, the analyzers, the race detector and the guest runner's own exit
contract. Nothing is simulated, so a stale image identity or a guest that cannot start the module
shows up as a failing assertion rather than as a plausible-looking observation.

These run in the local Docker driver (development isolation), not a production worker.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from go_plugin_support import (
    ROOT,
    TOP_WORDS,
    draft,
    package_files,
    with_quality,
)
from polycodebench_core.models import Candidate, MeasurementStatus
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_lang_go import GoLanguagePlugin
from polycodebench_plugins_api import AnalysisContext
from polycodebench_plugins_api.testreport import reconcile
from polycodebench_runner.provider import LocalDockerSandboxProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real sandbox tests are opt-in (PCB_TEST_DOCKER=1)",
)

plugin = GoLanguagePlugin()
VIEW = plugin.freeze_view(draft(), "sha256:" + "1" * 64)
# A concurrency opportunity is not declared by this task, so the race detector is not applicable.
# `race="required"` on the same task is what switches it on, and the switch is the point of the
# race cases below.
CONCURRENT = with_quality(
    VIEW,
    race="required",
    opportunities={**dict(VIEW.quality["opportunities"]), "goroutines_channels": 1},
    required_analyzers=[*VIEW.required_analyzers, "race"],
)


def _runner(tag: str) -> PlanRunner:
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / f"go-docker-test-{tag}",
        operation_timeout_seconds=120,
    )
    return PlanRunner(provider, lane="admission")


def _variant(relative: str) -> bytes:
    return (TOP_WORDS / relative).read_bytes()


def _sources(candidate: bytes) -> dict[str, dict[str, bytes]]:
    files = package_files()
    sources: dict[str, dict[str, bytes]] = {}
    for path, data in files.items():
        if path.startswith("visible/repo/"):
            sources[path.removeprefix("visible/repo/")] = {"content": data}
        elif path.startswith("hidden/") and path.endswith("_test.go"):
            sources[path.removeprefix("hidden/")] = {"content": data}
    sources["topwords/topwords.go"] = {"content": candidate}
    return sources


def _candidate() -> Candidate:
    return Candidate.model_validate_json(
        f'{{"schema_version": 1, "kind": "candidate", "candidate_id": '
        f'"cand-go-docker", "task_id": "{VIEW.task_id}", "task_version": '
        f'{VIEW.task_version}, "language_id": "go", "model_id": "docker-test", '
        f'"created_at": "2026-01-01T00:00:00Z", "attempt_kind": "generation", '
        f'"files": [{{"path": "topwords/topwords.go", "sha256": '
        f'"{"5" * 64}", "bytes": 1}}]}}'
    )


async def _test_gate(candidate: bytes, *, executable: bool = True) -> tuple[Any, Any]:
    runner = _runner("gate")
    sources = _sources(candidate)
    inventory = {g.group_id: g for g in plugin.inventory(VIEW)}
    records: list[Any] = []
    controls: list[Any] = []
    for group in plugin.test_plan(VIEW).groups:
        plan = group.plan
        if not executable:
            plan = plan.model_copy(
                update={
                    "resources": plan.resources.model_copy(update={"executable_workspace": False})
                }
            )
        run = await runner.run(plan, materialize_inputs(plan, sources), stage_id="go-docker")
        got, control = plugin.parse_test_group(
            group.model_copy(update={"plan": plan}),
            inventory[group.group_id],
            run.reader(),
            repetition=0,
        )
        records += got
        controls.append(control)
    return reconcile(tuple(inventory.values()), records, controls), controls


def test_reference_builds_passes_and_is_clean_under_every_analyzer() -> None:
    reference = _variant("hidden/reference/topwords/topwords.go")

    async def go() -> None:
        runner = _runner("reference")
        sources = _sources(reference)
        build = plugin.build_plan(VIEW, _candidate())
        run = await runner.run(build, materialize_inputs(build, sources), stage_id="go-docker")
        assert plugin.parse_build(build, run.reader())[0] == "pass"

        verdict, _controls = await _test_gate(reference)
        assert verdict.gate == "pass", verdict

        context = AnalysisContext(
            task=VIEW,
            candidate_digest="sha256:" + "2" * 64,
            candidate_paths=("topwords/topwords.go",),
        )
        analyzers = {p.analyzer_id for p in plugin.analysis_plans(context)}
        # The task declares no concurrency opportunity, so no race plan may be produced for it.
        assert "race" not in analyzers, analyzers
        for plan in plugin.analysis_plans(context):
            result = await runner.run(plan, materialize_inputs(plan, sources), stage_id="go-docker")
            observations = plugin.parse_analysis(result.reader(), plan)
            scan = next(o for o in observations if o.check_id.endswith(".scan"))
            assert scan.status == MeasurementStatus.MEASURED, (plan.analyzer_id, scan.explanation)
            assert scan.value == 0, (plan.analyzer_id, observations)

    asyncio.run(go())


def test_a_race_is_detected_in_the_runtime_image_and_never_in_the_measurement_image() -> None:
    """PCB-22-2: a data race is a real measurement, and it cannot reach the performance lane."""
    defective = _variant("admission/race-defective/topwords/topwords.go")

    async def go() -> None:
        ids = plugin.identities
        context = AnalysisContext(
            task=CONCURRENT,
            candidate_digest="sha256:" + "3" * 64,
            candidate_paths=("topwords/topwords.go",),
        )
        race = next(p for p in plugin.analysis_plans(context) if p.analyzer_id == "race")
        assert race.image_digest == ids.runtime.digest
        assert race.image_digest != ids.performance.digest
        assert race.environment.get("CGO_ENABLED") == "1"

        sources = _sources(defective)
        run = await runner_run(race, sources)
        observations = plugin.parse_analysis(run.reader(), race)
        races = [o for o in observations if o.check_id == "go.race.data-race"]
        assert races, observations
        assert races[0].status == MeasurementStatus.MEASURED
        assert races[0].owner == "robustness"

    async def runner_run(plan: Any, sources: dict[str, dict[str, bytes]]) -> Any:
        return await _runner("race").run(
            plan, materialize_inputs(plan, sources), stage_id="go-docker"
        )

    asyncio.run(go())


def test_faulty_and_hung_candidates_fail_for_the_right_reasons() -> None:
    faulty = _variant("admission/faulty-ties/topwords/topwords.go")
    hung = _variant("admission/timeout-case/topwords/topwords.go")

    async def go() -> None:
        verdict, _ = await _test_gate(faulty)
        assert verdict.gate == "fail"
        assert any("TestTiesBreakAlphabetically" in reason for reason in verdict.reasons)

        verdict, controls = await _test_gate(hung)
        assert verdict.gate == "fail"
        timed_out = [c for c in controls if c.status == "candidate_timeout"]
        assert timed_out, [c.status for c in controls]
        # A timeout names the case that was still in flight: it is a candidate failure with
        # evidence, not an opaque harness error.
        assert timed_out[0].in_flight_case

    asyncio.run(go())


def test_a_workspace_that_cannot_execute_never_produces_a_passing_gate() -> None:
    reference = _variant("hidden/reference/topwords/topwords.go")

    async def go() -> None:
        verdict, _ = await _test_gate(reference, executable=False)
        assert verdict.gate != "pass"

    asyncio.run(go())


def test_an_alternative_valid_implementation_passes_the_same_gate() -> None:
    """PCB-22-4: a different correct algorithm is not a different task."""
    alternative = _variant("admission/alternative-heaps/topwords/topwords.go")

    async def go() -> None:
        verdict, _ = await _test_gate(alternative)
        assert verdict.gate == "pass", verdict

    asyncio.run(go())


def test_the_quality_defective_variant_passes_its_tests_and_still_shows_its_defects() -> None:
    """PCB-22-4: an ignored error is not a test failure, and must not be scored as clean."""
    defective = _variant("admission/quality-defective/topwords/topwords.go")

    async def go() -> None:
        verdict, _ = await _test_gate(defective)
        assert verdict.gate == "pass", verdict

        runner = _runner("quality-defective")
        sources = _sources(defective)
        context = AnalysisContext(
            task=VIEW,
            candidate_digest="sha256:" + "4" * 64,
            candidate_paths=("topwords/topwords.go",),
        )
        found: set[str] = set()
        for plan in plugin.analysis_plans(context):
            if plan.analyzer_id not in {"context", "gosec"}:
                continue
            run = await runner.run(plan, materialize_inputs(plan, sources), stage_id="go-docker")
            found |= {
                o.check_id
                for o in plugin.parse_analysis(run.reader(), plan)
                if o.status == MeasurementStatus.MEASURED and not o.check_id.endswith(".scan")
            }
        assert found, "the defective variant produced no measured finding at all"

    asyncio.run(go())
