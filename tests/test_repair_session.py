"""E2E-37: self-repair with public feedback, hidden failing case and protocol selection.

The executable cases the scenario names, over the admitted ``py-listsort-v1`` fixture, the real
model gateway with scripted fixture replies, and real PostgreSQL persistence:

* **hidden-failure feedback isolation** - only public case results reach the model; hidden
  evaluation runs after selection and its outcomes cannot cause another model call (two runs with
  identical public evidence and different hidden outcomes make identical calls);
* **exhausted round budget** - the frozen ``RepairLimits`` stop the loop with no extra call;
* **restart during a repair round** - a crash before the round-boundary commit resumes at the same
  frontier, consumes the persisted response once and grants no additional repair round;
* **protocol-selected final artifact** - final selection is the protocol rule over public results,
  never the hidden-score best round, with initial/final outcomes and cumulative cost distinct.
"""

# ruff: noqa: F811 - pytest fixtures are imported from the PostgreSQL test module

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from model_gateway_support import ScriptedTransport, ok
from polycodebench_core.model_planning import ModelConfig
from polycodebench_core.models import TaskOutputContract
from polycodebench_core.repair_contracts import (
    PublicCaseResult,
    RepairProtocol,
    metrics_of,
)
from polycodebench_orchestration.repair.session import (
    RepairAssignment,
    RepairSession,
)
from polycodebench_persistence.repair_state import PostgresRepairRepository
from polycodebench_services.task_packages import TaskPackageImporter
from solve_support import envelope_files
from test_model_gateway_postgres import (  # noqa: F401 - fixtures
    artifacts,
    build_world,
    database,
)
from test_repair_contracts import base_protocol

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "taskpacks" / "self-repair" / "py-listsort-v1"

# The hidden case ids are forbidden request material; hidden verdicts are never feedback.
HIDDEN_MARKERS = ("test_hidden_mixed_case", "test_hidden_stability", "tests_hidden")

Files = dict[str, bytes]


def pack_contract() -> TaskOutputContract:
    manifest, _ = TaskPackageImporter().load(PACK)
    return manifest.output_contract


def task_statement() -> str:
    return (PACK / "visible" / "task.md").read_text(encoding="utf-8")


def run_cases(files: Files, tests_dir: Path, prefix: str) -> tuple[PublicCaseResult, ...]:
    """Run one unittest file against a candidate workspace and report per-case results."""
    with tempfile.TemporaryDirectory() as temp:
        workdir = Path(temp)
        for path, data in files.items():
            target = workdir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        proc = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(tests_dir),
                "-p",
                "test*.py",
                "-v",
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    results: list[PublicCaseResult] = []
    for line in proc.stderr.splitlines():
        if " ... ok" not in line and " ... FAIL" not in line and " ... ERROR" not in line:
            continue
        name = line.split(" ... ")[0].rsplit(".", 1)[-1].rstrip(")")
        outcome = "pass" if line.endswith("ok") else "fail"
        results.append(
            PublicCaseResult(
                schema_version=1,
                kind="public_case_result",
                case_id=f"{prefix}-{name}",
                outcome=outcome,
                reason="" if outcome == "pass" else "case failed",
            )
        )
    return tuple(results)


def evaluate_public(files: Files) -> tuple[PublicCaseResult, ...]:
    return run_cases(files, PACK / "visible" / "tests_public", "public")


def evaluate_hidden(files: Files) -> tuple[PublicCaseResult, ...]:
    return run_cases(files, PACK / "hidden", "hidden")


def solution(path: Path) -> str:
    return (path / "solution.py").read_text(encoding="utf-8")


def make_protocol(**limit_overrides: Any) -> RepairProtocol:
    limits: dict[str, Any] = {
        "schema_version": 1,
        "kind": "repair_limits",
        "maximum_repair_rounds": 1,
        "maximum_model_calls": 2,
        "maximum_active_seconds": 600,
        "maximum_input_tokens": 500_000,
        "maximum_output_tokens": 50_000,
        "maximum_cumulative_cost_micros": None,
    }
    limits.update(limit_overrides)
    return RepairProtocol(
        schema_version=1,
        kind="repair_protocol",
        protocol_id="self-repair-v1",
        version=1,
        base=base_protocol(),
        feedback_policy="public_tests_only",
        selection_rule="final_round",
        limits=limits,  # type: ignore[arg-type]
        prompt_policy="pcb-repair-v1",
    )


def assignment(
    world: Any, index: int, repair_run_id: str, config: ModelConfig | None = None
) -> RepairAssignment:
    attempt = str(world.attempts[index])
    return RepairAssignment(
        attempt_id=attempt,
        repair_run_id=f"{repair_run_id}-{attempt[-8:]}",
        scope=world.scope(index),
        config=config or world.config,
        config_document_id=world.config_id,
        sample_seed=7,
        contract=pack_contract(),
        required_outputs=("solution.py",),
        task_statement=task_statement(),
        forbidden_markers=HIDDEN_MARKERS,
        public_case_ids=(
            "public-test_public_basic",
            "public-test_public_order_insensitive",
            "public-test_public_stability",
        ),
        evaluate_public=evaluate_public,
    )


INITIAL = solution(PACK / "visible" / "repo")
FIXED = solution(PACK / "admission" / "reference")
ALTERNATIVE = solution(PACK / "admission" / "alternative")
FAULTY = solution(PACK / "admission" / "faulty")


def scripted(*contents: str) -> ScriptedTransport:
    return ScriptedTransport(script=[ok(envelope_files(content)) for content in contents])


def request_text(entry: dict[str, Any]) -> str:
    return entry["body"].decode("utf-8", errors="replace")


def test_e2e37_only_public_feedback_reaches_the_model(database, artifacts) -> None:  # noqa: F811
    world = build_world(database, artifacts)
    transport = scripted(INITIAL, FIXED)
    session = RepairSession(
        repository=PostgresRepairRepository(database.engine),
        gateway=world.gateway(transport),
        assignment=assignment(world, 0, "repair-e2e37-feedback"),
        protocol=make_protocol(),
    )
    outcome = asyncio.run(session.run())
    assert outcome.run.state == "complete"
    assert outcome.selected_round_index == 1
    assert outcome.repair_round_count == 1
    assert len(transport.sent) == 2
    initial_text = request_text(transport.sent[0])
    repair_text = request_text(transport.sent[1])
    # The repair prompt carries the visible failure and no hidden material at all.
    assert "test_public_order_insensitive" in repair_text
    assert "fail" in repair_text
    for marker in HIDDEN_MARKERS:
        assert marker not in repair_text
        assert marker not in initial_text
    # Hidden evaluation happens afterwards, on the frozen artifacts, and starts from a failure
    # that never appeared in the feedback.
    hidden_initial = evaluate_hidden(outcome.artifacts[0].files)
    hidden_final = evaluate_hidden(outcome.artifacts[1].files)
    assert {result.case_id: result.outcome for result in hidden_initial} == {
        "hidden-test_hidden_mixed_case": "fail",
        "hidden-test_hidden_stability": "fail",
    }
    assert all(result.outcome == "pass" for result in hidden_final)
    metrics = metrics_of(
        outcome.run, initial_native_correct=False, final_native_correct=True
    )
    assert metrics.initial_native_correct is False
    assert metrics.final_native_correct is True
    assert metrics.cumulative_cost_micros == outcome.run.spend.cost_micros
    assert metrics.repair_round_count == 1


def test_e2e37_hidden_outcomes_cannot_cause_another_model_call(database, artifacts) -> None:  # noqa: F811
    """Identical public evidence, different hidden outcomes: identical rounds and requests."""
    runs = []
    for index, suffix in ((0, "hidden-fails"), (1, "hidden-passes")):
        world = build_world(database, artifacts)
        transport = scripted(INITIAL, FIXED)
        session = RepairSession(
            repository=PostgresRepairRepository(database.engine),
            gateway=world.gateway(transport),
            assignment=assignment(world, index, f"repair-e2e37-{suffix}"),
            protocol=make_protocol(),
        )
        runs.append((asyncio.run(session.run()), transport))
    (first, first_transport), (second, second_transport) = runs
    assert len(first_transport.sent) == len(second_transport.sent) == 2
    assert [entry.prompt_digest for entry in first.run.rounds] == [
        entry.prompt_digest for entry in second.run.rounds
    ]
    assert first.selected_round_index == second.selected_round_index == 1


def test_e2e37_exhausted_round_budget_stops_without_extra_calls(database, artifacts) -> None:  # noqa: F811
    world = build_world(database, artifacts)
    transport = scripted(INITIAL, FIXED, ALTERNATIVE, FAULTY)
    session = RepairSession(
        repository=PostgresRepairRepository(database.engine),
        gateway=world.gateway(transport),
        assignment=assignment(world, 0, "repair-e2e37-budget"),
        protocol=make_protocol(maximum_repair_rounds=1, maximum_model_calls=2),
    )
    outcome = asyncio.run(session.run())
    # The two scripted replies after the budget is spent are never consumed: any extra send
    # would raise AssertionError in ScriptedTransport.
    assert len(transport.sent) == 2
    assert outcome.repair_round_count == 1
    assert outcome.run.spend.model_calls == 2
    assert outcome.selected_round_index == 1


def test_e2e37_restart_during_a_repair_round_grants_no_extra_round(database, artifacts) -> None:  # noqa: F811
    world = build_world(database, artifacts)
    transport = scripted(INITIAL, FIXED)
    gateway = world.gateway(transport)
    repository = PostgresRepairRepository(database.engine)
    session = RepairSession(
        repository=repository,
        gateway=gateway,
        assignment=assignment(world, 0, "repair-e2e37-restart"),
        protocol=make_protocol(),
    )

    original = repository.commit_round
    calls = {"count": 0}

    def crashing(*args: Any, **kwargs: Any) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("controller died before the round-boundary commit")
        original(*args, **kwargs)

    repository.commit_round = crashing  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="round-boundary commit"):
        asyncio.run(session.run())
    repository.commit_round = original  # type: ignore[method-assign]

    # The initial round's response was persisted before the crash. A restart resumes at the same
    # frontier and the ledger consumes that stored response once: still one provider call.
    assert len(transport.sent) == 1
    resumed = RepairSession(
        repository=repository,
        gateway=gateway,
        assignment=assignment(world, 0, "repair-e2e37-restart"),
        protocol=make_protocol(),
    )
    outcome = asyncio.run(resumed.run(resume=True))
    assert outcome.run.state == "complete"
    assert [entry.round_index for entry in outcome.run.rounds] == [0, 1]
    assert outcome.selected_round_index == 1
    assert len(transport.sent) == 2
    # Recovery did not erase spend nor open an unplanned round: the initial round's recorded
    # consumption stands. Tokens are the conservative charge; cost stays as *reported* (the
    # fixture reports none, and no price is ever invented).
    assert outcome.run.spend.model_calls == 2
    assert outcome.run.spend.input_tokens > 0
    assert outcome.run.spend.output_tokens > 0
    metrics = metrics_of(outcome.run, initial_native_correct=False, final_native_correct=True)
    assert metrics.cumulative_cost_micros == outcome.run.spend.cost_micros
