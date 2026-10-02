"""Offline checks for performance plan validation and the block/aggregation rules.

The measurement itself runs in Docker (``tests/test_performance_docker.py``). These tests pin the
logic that decides whether a measurement is *allowed* and how blocks are selected, using the real
Python ``top-words`` plan rather than hand-written stand-ins.
"""

from __future__ import annotations

import copy

import pytest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_evaluation.performance import (
    PerformanceRefused,
    PerformanceRunner,
    check_speed_lane,
    hardware_probe_argv,
    paired_order,
    side_invariants,
    specialize_for_side,
)
from polycodebench_lang_python import PythonLanguagePlugin
from python_plugin_support import frozen

plugin = PythonLanguagePlugin()
VIEW = frozen(plugin)
CANDIDATE = Candidate.model_validate_json(
    '{"schema_version":1,"kind":"candidate","candidate_id":"'
    + new_entity_id()
    + '","run_id":"'
    + new_entity_id()
    + '","task_id":"fixture-top-words","task_version":1,"sample_index":0,'
    '"submission_kind":"source_bundle","payload_digest":"sha256:'
    + "1" * 64
    + '","artifact_ids":[],"frozen_at":null}'
)


def plan() -> object:
    performance = plugin.performance_plan(VIEW)
    assert performance is not None
    return performance


def test_two_sides_differ_only_in_the_source_tree_they_read() -> None:
    base = plan().iteration_plan
    candidate = specialize_for_side(base, "candidate")
    reference = specialize_for_side(base, "reference")
    assert side_invariants(candidate) == side_invariants(reference)
    # the actual difference is the candidate input root, never flags, runtime or workload
    assert candidate.inputs[0].path.startswith("cand/")
    assert reference.inputs[0].path.startswith("ref/")
    assert candidate.image_digest == reference.image_digest
    assert candidate.resources == reference.resources
    assert candidate.tool == reference.tool
    # overlays/config (the trusted workload) are identical for both sides
    assert [i.path for i in candidate.inputs if i.role != "candidate"] == [
        i.path for i in reference.inputs if i.role != "candidate"
    ]


def test_speed_lane_rejects_instrumented_and_profiling_builds() -> None:
    base = plan().iteration_plan
    assert check_speed_lane(base).accepted
    for marker in ("-fsanitize=address", "miri", "--coverage", "pprof"):
        instrumented = base.model_copy(update={"argv": (*base.argv, marker)})
        check = check_speed_lane(instrumented)
        assert not check.accepted
        # the report names where the marker was found, not just that something matched
        assert any(entry.startswith("argv:") for entry in check.forbidden_tokens_found)
    # instrumentation is also commonly switched on by an environment key, not a flag
    by_env = base.model_copy(update={"environment": {**base.environment, "COVERAGE": "1"}})
    check = check_speed_lane(by_env)
    assert not check.accepted
    assert "environment:coverage" in check.forbidden_tokens_found


def test_validation_refuses_a_missing_plan_and_a_bad_weight_split() -> None:
    runner = PerformanceRunner(plugin)
    with pytest.raises(PerformanceRefused):
        runner.validate(None, None)
    broken = copy.deepcopy(plan())
    object.__setattr__(broken, "workloads", (broken.workloads[0],))
    with pytest.raises(PerformanceRefused):
        runner.validate(broken, None)


def test_validation_binds_the_expected_reference_identity_and_hardware_class() -> None:
    runner = PerformanceRunner(plugin)
    current = plan()
    runner.validate(current, "sha256:" + "2" * 64)  # any expected digest is fine when undeclared
    mismatched = PerformanceRunner(plugin, hardware_baseline={"hardware_class": "gpu-a100"})
    with pytest.raises(PerformanceRefused):
        mismatched.validate(current, None)


def test_hardware_class_must_match_the_recorded_worker() -> None:
    strict = PerformanceRunner(
        plugin, hardware_baseline={"hardware_class": "local-development", "cpu_model": "AMD EPYC"}
    )
    assert strict.validate(plan(), None)


def test_pair_order_is_deterministic_and_balanced() -> None:
    # the same (seed, workload seed, iteration) always yields the same first side
    assert paired_order(7, 11, 3) == paired_order(7, 11, 3)
    # across many iterations the order is not systematically one-sided
    candidate_first = sum(paired_order(0, 5, index) for index in range(200))
    assert 40 < candidate_first < 160


def test_hardware_probe_is_a_bounded_guest_command() -> None:
    argv = hardware_probe_argv()
    assert argv[:4] == ("python", "-I", "-B", "-c")
    # it must not read the candidate tree or take the whole workspace
    assert "cand" not in argv[4] and "ref" not in argv[4]
