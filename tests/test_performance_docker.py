"""Real-sandbox performance measurement (Technical Spec 13, E2E-19/E2E-20).

Opt in with ``PCB_TEST_DOCKER=1``. These run in the local Docker driver: candidate and reference
share one reserved guest, every iteration is a fresh process, and the numbers below come from real
timings in the pinned image - never from a recorded or mocked report.
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_evaluation.performance import (
    ExclusiveGuestReservation,
    PerformanceRefused,
    PerformanceRunner,
)
from polycodebench_evaluation.plan_runner import PlanRunner, digest_files
from polycodebench_plugins_api import TaskDraft
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real sandbox tests are opt-in (PCB_TEST_DOCKER=1)",
)

ITERATION_TIMEOUT = 120
CACHE = ROOT / ".cache" / "performance"


def _fixture() -> dict[str, Any]:
    from python_task_tool import ROOT as PY_ROOT
    from python_task_tool import _files, _load

    package = PY_ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"
    manifest = _load(package)
    files = _files(package)
    return {"package": package, "manifest": manifest, "files": files}


def _view(fixture: dict[str, Any]) -> Any:
    from polycodebench_lang_python import PythonLanguagePlugin

    plugin = PythonLanguagePlugin()
    draft = TaskDraft(
        task_id=fixture["manifest"]["task"]["task_id"],
        primary_language="python",
        manifest=fixture["manifest"],
        files=fixture["files"],
    )
    return plugin, plugin.freeze_view(draft, "sha256:" + "d" * 64)


def _runner(tag: str) -> PlanRunner:
    from polycodebench_lang_python import PythonLanguagePlugin

    ids = PythonLanguagePlugin().identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=CACHE / tag,
        operation_timeout_seconds=120,
    )
    return PlanRunner(provider, lane="grading")


def _candidate(task_id: str, files: dict[str, bytes]) -> Candidate:
    return Candidate.model_validate_json(
        '{"schema_version":1,"kind":"candidate","candidate_id":"'
        + new_entity_id()
        + '","run_id":"'
        + new_entity_id()
        + f'","task_id":"{task_id}","task_version":1,"sample_index":0,'
        '"submission_kind":"source_bundle","payload_digest":"'
        + digest_files(files)
        + '","artifact_ids":[],"frozen_at":null}'
    )


async def _build(plan: Any, files: Any) -> int:
    """Compile step measured through the same pinned runtime image the iterations use."""
    from polycodebench_evaluation.plan_runner import materialize_inputs

    run = await _RUNNER.run(
        plan, materialize_inputs(plan, {"candidate": dict(files)}), stage_id="perf-build"
    )
    return run.record.duration_ms


def _measure(
    tag: str,
    *,
    candidate_source: bytes,
    reference_source: bytes,
    seed: int = 4242,
    canary_baseline_ns: Decimal | None = None,
    dedicated: bool = False,
    all_scales: bool = True,
) -> Any:
    fixture = _fixture()
    plugin, view = _view(fixture)
    performance = plugin.performance_plan(view)
    assert performance is not None
    # Keep the loop short enough for CI while still exercising warmups, pairs and repeats.
    # With `all_scales` the declared three scales are measured, so the weighted aggregation is
    # exercised on real numbers; trimming to one scale must move that scale's weight to 10000
    # basis points (the plan validator refuses any other split).
    if all_scales:
        workloads = performance.workloads
    else:
        largest = max(performance.workloads, key=lambda item: item.scale)
        workloads = (largest.model_copy(update={"weight_bp": 10_000}),)
    plan = performance.model_copy(
        update={
            "warmup_iterations": 1,
            "measured_iterations": 3,
            "workloads": workloads,
        }
    )
    candidate_files = {"solution.py": candidate_source}
    reference_files = {"solution.py": reference_source}
    runner = PerformanceRunner(
        plugin,
        execution_tier="development_sandbox",
        plan_seed=seed,
        canary_baseline_ns=canary_baseline_ns,
        dedicated_hardware=dedicated,
        build=_build,
    )
    reservation = ExclusiveGuestReservation(
        runner=_runner(tag),
        plan=plan.iteration_plan,
        files={},
        stage_id=f"perf-{tag}",
    )
    build_plans = {
        "candidate": plugin.build_plan(view, _candidate(view.task_id, candidate_files)),
        "reference": plugin.build_plan(view, _candidate(view.task_id, reference_files)),
    }
    return (
        asyncio.run(
            runner.measure(
                view=view,
                plan=plan,
                candidate_files=candidate_files,
                reference_files=reference_files,
                overlay=_perf_overlay(plan, fixture["files"]),
                config={},
                build_plans=build_plans,
                reservation=reservation,
                reference_digest_expected=digest_files(reference_files),
            )
        ),
        reservation,
    )


def _perf_overlay(plan: Any, files: dict[str, bytes]) -> dict[str, bytes]:
    """Map each trusted hidden overlay onto the plan input path that declares it."""
    staged: dict[str, bytes] = {}
    for item in plan.iteration_plan.inputs:
        if item.role != "overlay":
            continue
        declared = item.path
        # `work/...` declared inputs come from `hidden/...` in the package; bare names
        # come from `hidden/perf/...`
        candidates = [f"hidden/{declared}", f"hidden/{declared.removeprefix('work/')}", declared]
        for source in candidates:
            if source in files:
                staged[declared] = files[source]
                break
        else:
            raise AssertionError(f"no package file backs the overlay input {declared!r}")
    return staged


_RUNNER: PlanRunner


@pytest.fixture(autouse=True)
def _bind_runner() -> None:
    global _RUNNER
    _RUNNER = _runner("build")


def test_e2e_19_paired_measurement_on_one_reserved_worker() -> None:
    fixture = _fixture()
    reference = fixture["files"]["hidden/reference/solution.py"]
    evidence, reservation = _measure(
        "e2e-19", candidate_source=reference, reference_source=reference
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "e2e-19.json").write_text(evidence.model_dump_json(indent=2), encoding="utf-8")

    # same worker: one exclusive reservation covered the whole measurement window, and it was
    # actually released afterwards (the frozen record is the state *as held* during measurement)
    assert evidence.reservation.exclusive
    assert reservation.released
    assert evidence.hardware.cpu_model and evidence.hardware.logical_cpus >= 1
    assert evidence.hardware.allocation_class
    assert evidence.speed_lane.accepted and evidence.speed_lane.forbidden_tokens_found == ()
    # candidate and reference identities are bound to the recorded digests
    assert evidence.reference_digest == evidence.reference_digest_expected

    # a block was selected and *every* attempted block is retained; the first valid block by
    # time order wins, never the faster one
    assert evidence.selected_block is not None
    assert evidence.blocks[0].started_first
    for index, block in enumerate(evidence.blocks):
        assert block.block_index == index
        if index < evidence.selected_block:
            assert block.status != "valid"
    assert evidence.blocks[evidence.selected_block].status == "valid"

    # every iteration of every declared scale is retained with its input identity and phase
    # (counts are per block; invalid blocks are retained separately and never mixed in)
    selected = [
        r for r in evidence.iterations if r.block_index == evidence.selected_block
    ]
    counts = evidence.iteration_counts(block=evidence.selected_block)
    for workload in ("small", "medium", "large"):
        assert counts[f"{workload}:candidate:cold_start"] == 1
        assert counts[f"{workload}:reference:cold_start"] == 1
        assert counts[f"{workload}:candidate:measured"] == 3
        assert counts[f"{workload}:reference:measured"] == 3
    assert len(selected) == 24
    for record in evidence.iterations:
        assert record.input_digest and record.environment_digest
        if record.block_index != evidence.selected_block:
            continue
        assert record.outcome == "measured" and record.verified
        assert record.elapsed_ns is not None and record.elapsed_ns > 0
        assert record.peak_rss_kb is not None

    # compile time is recorded separately and is not an iteration
    assert {build.side for build in evidence.build} == {"candidate", "reference"}
    assert all(build.duration_ms > 0 for build in evidence.build)

    # paired ordering is randomized per iteration and both sides ran the same input
    measured = [
        r
        for r in evidence.iterations
        if r.phase == "measured" and r.block_index == evidence.selected_block
    ]
    by_pair: dict[tuple[str, int], list[Any]] = {}
    for record in measured:
        by_pair.setdefault((record.workload_id, record.iteration), []).append(record)
    assert len(by_pair) == 9  # three scales x three measured iterations
    for pair in by_pair.values():
        assert {r.side for r in pair} == {"candidate", "reference"}
        assert len({r.input_digest for r in pair}) == 1
    # the first side in each pair is not systematically the same one
    leaders = [pair[0].side for pair in by_pair.values()]
    assert len(set(leaders)) == 2

    # aggregation produced a score-ready efficiency value over the weighted scales
    assert evidence.efficiency.status == "complete"
    assert evidence.efficiency.score is not None
    assert Decimal(evidence.efficiency.score) >= 0
    assert {w.workload_id for w in evidence.workloads} == {"small", "medium", "large"}
    assert sum(w.weight_bp for w in evidence.workloads) == 10_000
    for workload in evidence.workloads:
        assert workload.status == "measured"
        assert workload.memory_metric == "guest_process_peak_rss_ru_maxrss_kb"
        assert workload.memory_ratio is not None
        assert workload.candidate.stdev_ns is not None
        assert workload.candidate.mad_ns is not None
        assert workload.candidate.samples == 3
    # a candidate identical to the reference cannot be far from its own timings
    assert Decimal(evidence.efficiency.score) > Decimal("50")
    # shared CI hardware cannot establish a production comparison
    assert evidence.hardware_gate == "blocked_shared_ci"


def test_e2e_19_rejects_an_instrumented_speed_lane_before_any_guest_runs() -> None:
    """An instrumented/profiling build must be refused *before* the lane creates a guest."""
    fixture = _fixture()
    plugin, view = _view(fixture)
    performance = plugin.performance_plan(view)
    assert performance is not None
    runner = PerformanceRunner(plugin)
    instrumented = performance.model_copy(
        update={
            "iteration_plan": performance.iteration_plan.model_copy(
                update={"environment": {**performance.iteration_plan.environment, "COVERAGE": "1"}}
            )
        }
    )
    with pytest.raises(PerformanceRefused) as refused:
        runner.validate(instrumented, None)
    assert "speed_lane_forbidden" in str(refused.value)
    # and a clean plan is still accepted, so the refusal is specific, not blanket refusal
    runner.validate(performance, None)


def test_e2e_20_canary_drift_invalidates_the_block_and_first_valid_block_wins() -> None:
    fixture = _fixture()
    reference = fixture["files"]["hidden/reference/solution.py"]
    # A frozen canary baseline far below anything this host can produce: every block's canary
    # median falls outside the band, so no block is valid and no score is invented.
    starved, _ = _measure(
        "e2e-20-invalid",
        candidate_source=reference,
        reference_source=reference,
        canary_baseline_ns=Decimal("1"),
        all_scales=False,
    )
    assert starved.blocks[0].status == "invalid_canary"
    assert starved.blocks[0].canary.reason in {
        "canary_median_outside_band",
        "canary_noise_above_band",
    }
    assert starved.selected_block is None
    assert starved.efficiency.status == "incomplete"
    assert starved.efficiency.score is None
    assert "no_valid_measurement_block" in starved.efficiency.reasons
    # both attempted blocks are retained, including the invalid one
    assert len(starved.blocks) == 2
    assert all(block.status == "invalid_canary" for block in starved.blocks)
    # and no measurement from an invalidated block is presented as a result
    assert starved.iterations == ()
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "e2e-20-invalid.json").write_text(starved.model_dump_json(indent=2), encoding="utf-8")


def test_e2e_19_wrong_but_fast_output_is_rejected_and_cannot_win() -> None:
    fixture = _fixture()
    reference = fixture["files"]["hidden/reference/solution.py"]
    # A deliberately wrong implementation that returns immediately: it would look "fast" if
    # timing alone decided the winner, but the workload verifier must reject its output.
    wrong_fast = b"def top_words(lines, k):\n    return []\n"
    evidence, _ = _measure(
        "e2e-19-wrong-fast",
        candidate_source=wrong_fast,
        reference_source=reference,
        all_scales=False,
    )
    # Correctness acceptance belongs to the grading stage, not the timing lane. What matters
    # here is the invariant: the wrong output is *never* accepted as a measurement, and the
    # faster-but-wrong candidate can never produce an efficiency score.
    candidate_measured = [
        r for r in evidence.iterations if r.side == "candidate" and r.phase == "measured"
    ]
    assert all(r.outcome == "rejected_output" and not r.verified for r in candidate_measured)
    # the reference is unaffected: its measurements are real and measured
    reference_measured = [
        r
        for r in evidence.iterations
        if r.side == "reference" and r.phase == "measured" and r.outcome == "measured"
    ]
    assert reference_measured
    # no block that contained a rejected candidate iteration may be selected, and the fast
    # wrong candidate therefore wins nothing
    selected = (
        next(block for block in evidence.blocks if block.block_index == evidence.selected_block)
        if evidence.selected_block is not None
        else None
    )
    assert selected is None or selected.status == "valid"
    assert evidence.selected_block is None or not any(
        block.status == "invalid_iterations" and block.block_index < evidence.selected_block
        for block in evidence.blocks
    )
    assert evidence.efficiency.status in {"incomplete", "invalid"}
    assert evidence.efficiency.score is None
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "e2e-19-wrong-fast.json").write_text(
        evidence.model_dump_json(indent=2), encoding="utf-8"
    )
