"""Real-sandbox evidence for the Rust plugin (opt-in: PCB_TEST_DOCKER=1).

E2E-04 (Rust subcase): reference, faulty and timeout variants of the fixture are planned by the
plugin, executed by the supervisor in the pinned images and parsed back into evidence. E2E-16
(Rust): a hung test, a missing executable permission and the analyzers' scans are classified from
real output. These run in the local Docker driver (development isolation), not a production
worker.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, MeasurementStatus
from polycodebench_evaluation.plan_runner import PlanRunner, materialize_inputs
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_plugins_api import AnalysisContext
from polycodebench_plugins_api.testreport import reconcile
from polycodebench_runner.provider import LocalDockerSandboxProvider
from rust_plugin_support import ROOT, TOP_WORDS, draft, package_files

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real sandbox tests are opt-in (PCB_TEST_DOCKER=1)",
)

plugin = RustLanguagePlugin()
VIEW = plugin.freeze_view(draft(), "sha256:" + "1" * 64)


def _runner(tag: str) -> PlanRunner:
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / f"rust-docker-test-{tag}",
        operation_timeout_seconds=120,
    )
    return PlanRunner(provider, lane="admission")


def _sources(candidate: bytes) -> dict[str, dict[str, bytes]]:
    files = package_files()
    return {
        "candidate": {"src/lib.rs": candidate},
        "overlay": {
            f"work/{p.removeprefix('hidden/')}": data
            for p, data in files.items()
            if p.startswith("hidden/tests/")
        },
        "config": {
            f"work/{path}": files[f"visible/repo/{path}"] for path in VIEW.quality["crate_files"]
        },
    }


def _candidate() -> Candidate:
    return Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": VIEW.task_id,
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": "sha256:" + "0" * 64,
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
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
            resources = plan.resources.model_copy(update={"executable_workspace": False})
            plan = plan.model_copy(update={"resources": resources})
        run = await runner.run(plan, materialize_inputs(plan, sources), stage_id="rust-docker")
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
    reference = (TOP_WORDS / "hidden/reference/src/lib.rs").read_bytes()

    async def go() -> None:
        runner = _runner("reference")
        sources = _sources(reference)
        build = plugin.build_plan(VIEW, _candidate())
        run = await runner.run(build, materialize_inputs(build, sources), stage_id="rust-docker")
        assert plugin.parse_build(build, run.reader())[0] == "pass"
        verdict, _controls = await _test_gate(reference)
        assert verdict.gate == "pass", verdict
        context = AnalysisContext(
            task=VIEW, candidate_digest="sha256:" + "2" * 64, candidate_paths=("src/lib.rs",)
        )
        for plan in plugin.analysis_plans(context):
            result = await runner.run(
                plan, materialize_inputs(plan, sources), stage_id="rust-docker"
            )
            observations = plugin.parse_analysis(result.reader(), plan)
            scan = next(o for o in observations if o.check_id.endswith(".scan"))
            assert scan.status == MeasurementStatus.MEASURED, (plan.analyzer_id, scan.explanation)
            assert scan.value == 0, (plan.analyzer_id, observations)

    asyncio.run(go())


def test_faulty_and_hung_candidates_fail_for_the_right_reasons() -> None:
    faulty = (TOP_WORDS / "admission/faulty-ties/src/lib.rs").read_bytes()
    hung = (TOP_WORDS / "admission/timeout-case/src/lib.rs").read_bytes()

    async def go() -> None:
        verdict, _ = await _test_gate(faulty)
        assert verdict.gate == "fail"
        assert any("ties_break_alphabetically" in r for r in verdict.reasons)
        verdict, controls = await _test_gate(hung)
        assert verdict.gate == "fail"
        timed_out = next(c for c in controls if c.status == "candidate_timeout")
        assert timed_out.in_flight_case == "behaviour::uppercase_runs_are_ordinary_words"

    asyncio.run(go())


def test_a_workspace_that_cannot_execute_never_produces_a_passing_gate() -> None:
    reference = (TOP_WORDS / "hidden/reference/src/lib.rs").read_bytes()

    async def go() -> None:
        verdict, _ = await _test_gate(reference, executable=False)
        assert verdict.gate != "pass"

    asyncio.run(go())
