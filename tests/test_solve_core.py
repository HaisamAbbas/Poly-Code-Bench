"""Solve contracts, tool schemas, context policy and extraction (pure logic, no I/O).

Evidence level: unit, with hand-written FIXTURES for extraction (tests/fixtures/solve_extraction).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from model_gateway_support import FULL_CAPS  # noqa: F401 - shared fixtures keep these tests honest
from polycodebench_core.model_contracts import (
    FinishReason,
    Message,
    ModelResponse,
    TextBlock,
    ToolCallBlock,
    Usage,
)
from polycodebench_core.models import ProtocolConstraints, TaskOutputContract
from polycodebench_core.solve_context import CompletedTurn, build_context, summarize_result
from polycodebench_core.solve_contracts import (
    ARGS_MODELS,
    TOOL_NAMES,
    BudgetState,
    ContextBudgetExceeded,
    ContextPolicy,
    PathForbidden,
    SolveBudget,
    SolveError,
    SolveProtocol,
    ToolResult,
    is_protected,
    normalize_workspace_path,
    resolve_effective_protocol,
    tool_specs,
)
from polycodebench_core.solve_extraction import (
    extract_from_response,
    freeze_workspace_files,
    workspace_patch,
)
from polycodebench_services.solve_protocols import load_protocol_directory
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/solve_extraction/cases.json").read_text())
PROTOCOLS = load_protocol_directory(ROOT / "config/protocols")


def contract(name: str) -> TaskOutputContract:
    return TaskOutputContract.model_validate(
        {"schema_version": 1, "kind": "task_output_contract", **CASES["contracts"][name]},
        strict=False,
    )


def constraints(**overrides: Any) -> ProtocolConstraints:
    values: dict[str, Any] = {
        "schema_version": 1,
        "kind": "protocol_constraints",
        "protocol_id": "standard-agent-v1",
        "allowed_tools": list(TOOL_NAMES),
        "public_test_feedback": True,
        "hidden_feedback": False,
        "network_policy": "disabled",
        "dependency_inventory_digest": None,
        "maximum_model_turns": 30,
        "maximum_tool_calls": 100,
        "maximum_wall_seconds": 600,
    }
    values.update(overrides)
    return ProtocolConstraints(**values)


# ------------------------------------------------------------------------- protocols


def test_shipped_protocols_are_valid_and_distinct() -> None:
    single, agent = PROTOCOLS["single-shot-v1"], PROTOCOLS["standard-agent-v1"]
    assert single.mode == "single_shot" and single.tools == [] and single.budget.model_turns == 1
    assert agent.mode == "standard_agent" and agent.tools == list(TOOL_NAMES)
    assert agent.budget.model_turns == 30 and agent.budget.tool_calls == 100
    assert agent.context.retain_recent_turns == 8 and agent.context.summarizer == "none"
    assert (
        single.to_definition().allowed_tools == [] and single.to_definition().mode == "single_shot"
    )


def test_protocol_rejects_incoherent_combinations() -> None:
    agent = PROTOCOLS["standard-agent-v1"]
    single = PROTOCOLS["single-shot-v1"]
    with pytest.raises(ValidationError):
        SolveProtocol.model_validate({**single.model_dump(), "tools": ["read_file"]}, strict=False)
    with pytest.raises(ValidationError):
        SolveProtocol.model_validate(
            {**agent.model_dump(), "tools": ["read_file", "launch_missiles"]}, strict=False
        )
    budget = agent.budget.model_dump() | {"per_command_seconds": None}
    with pytest.raises(ValidationError):
        SolveProtocol.model_validate({**agent.model_dump(), "budget": budget}, strict=False)
    with pytest.raises(ValidationError):  # public feedback needs the tool that provides it
        SolveProtocol.model_validate(
            {**agent.model_dump(), "tools": ["read_file"], "public_test_feedback": True},
            strict=False,
        )


def test_effective_protocol_takes_the_minimum_and_identity_changes_with_the_task() -> None:
    agent = PROTOCOLS["standard-agent-v1"]
    full = resolve_effective_protocol(agent, constraints())
    narrowed = resolve_effective_protocol(
        agent,
        constraints(
            allowed_tools=["list_files", "read_file", "apply_patch"],
            maximum_model_turns=10,
            maximum_tool_calls=40,
            maximum_wall_seconds=300,
            public_test_feedback=False,
        ),
    )
    assert full.tools == list(TOOL_NAMES) and full.budget.model_turns == 30
    assert narrowed.tools == ["list_files", "read_file", "apply_patch"]
    assert (narrowed.budget.model_turns, narrowed.budget.tool_calls) == (10, 40)
    assert narrowed.budget.active_solve_seconds == 300
    assert narrowed.protocol_digest == full.protocol_digest  # same protocol ...
    assert narrowed.digest != full.digest  # ... but a different effective cohort identity
    assert narrowed.definition().allowed_tools == narrowed.tools
    # a task can never widen what the protocol allows
    widened = resolve_effective_protocol(
        PROTOCOLS["single-shot-v1"], constraints(protocol_id="single-shot-v1")
    )
    assert widened.tools == []


def test_tasks_needing_hidden_feedback_or_another_protocol_are_refused() -> None:
    agent = PROTOCOLS["standard-agent-v1"]
    with pytest.raises(SolveError, match="hidden feedback"):
        resolve_effective_protocol(agent, constraints(hidden_feedback=True))
    with pytest.raises(SolveError, match="different protocol"):
        resolve_effective_protocol(agent, constraints(protocol_id="other-v1"))
    with pytest.raises(SolveError, match="no tools"):
        resolve_effective_protocol(agent, constraints(allowed_tools=[]))


# ------------------------------------------------------------------------ tool schemas


def test_tool_specs_are_canonical_strict_and_subset_ordered() -> None:
    specs = tool_specs(["run_command", "read_file"])
    assert [s.name for s in specs] == [
        "read_file",
        "run_command",
    ]  # standard order, not caller order
    for spec in tool_specs(list(TOOL_NAMES)):
        schema = spec.parameters_schema
        assert schema["additionalProperties"] is False and "title" not in schema
    assert set(ARGS_MODELS) == set(TOOL_NAMES)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("read_file", {"path": "a.py", "max_lines": 401}),
        ("read_file", {"path": "a.py", "start_line": 0}),
        ("read_file", {"path": "a.py", "extra": 1}),
        ("read_file", {"path": 7}),
        ("list_files", {"depth": 11}),
        ("list_files", {"depth": "2"}),
        ("search", {"pattern": ""}),
        ("apply_patch", {"diff": ""}),
        ("run_command", {"command": "x" * 8193}),
        ("run_command", {"command": "a\x00b"}),
        ("run_public_tests", {"group_ids": []}),
        ("run_public_tests", {"group_ids": ["Bad Id"]}),
    ],
)
def test_tool_arguments_are_strictly_validated(tool: str, arguments: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ARGS_MODELS[tool].model_validate_json(json.dumps(arguments), strict=True)


@pytest.mark.parametrize(
    "raw",
    ["", "/abs", "../up", "a/../../b", "a\\b", ".pcb_inbox/x", "dir/.pcb_x", "a\x01b", "x" * 600],
)
def test_workspace_paths_reject_escapes(raw: str) -> None:
    with pytest.raises(PathForbidden):
        normalize_workspace_path(raw)


def test_workspace_path_normalisation_and_protection() -> None:
    assert normalize_workspace_path("./a//b/./c") == "a/b/c"
    assert normalize_workspace_path(".", allow_root=True) == "."
    assert is_protected("tests/t.py", ["tests"]) and is_protected("tests", ["tests"])
    assert not is_protected("testsuite/x", ["tests"]) and not is_protected("src/tests", ["tests"])


# --------------------------------------------------------------------------- budgets


def test_budget_state_reports_remaining_and_first_exhausted_dimension() -> None:
    budget = SolveBudget(
        schema_version=1,
        kind="solve_budget",
        model_turns=3,
        tool_calls=2,
        active_solve_seconds=10,
        per_command_seconds=5,
        input_tokens=100,
        output_tokens=50,
    )
    state = BudgetState().plus(
        turns=2, tool_calls=2, input_tokens=40, output_tokens=10, active_ms=2500
    )
    remaining = state.remaining(budget)
    assert (remaining.model_turns, remaining.tool_calls, remaining.active_seconds) == (1, 0, 8)
    assert state.exhausted(budget) is None and state.tool_budget_exhausted(budget)
    assert state.plus(turns=1).exhausted(budget) == "model_turns"
    assert state.plus(output_tokens=40).exhausted(budget) == "output_tokens"
    assert state.plus(active_ms=8000).exhausted(budget) == "active_solve_seconds"


# ---------------------------------------------------------------------------- context


def _turn(index: int, size: int = 50, calls: int = 1) -> CompletedTurn:
    blocks = tuple(
        ToolCallBlock(
            call_id=f"c{index}_{n}",
            name="read_file",
            arguments_json=json.dumps({"path": f"f{index}.py"}),
        )
        for n in range(calls)
    )
    message = Message(role="assistant", blocks=blocks)
    results = tuple(
        ToolResult(
            event_seq=index * 10 + n,
            tool_call_id=f"c{index}_{n}",
            name="read_file",
            status="ok",
            content="x" * size,
            original_bytes=size,
            returned_bytes=size,
        )
        for n in range(calls)
    )
    return CompletedTurn(index, message, blocks, results)


def _build(turns: list[CompletedTurn], ceiling: int = 60000):  # type: ignore[no-untyped-def]
    policy = ContextPolicy(
        schema_version=1,
        kind="context_policy",
        retain_recent_turns=8,
        max_input_context_tokens=ceiling,
        token_counter="utf8_bytes_div4_v1",
        summarizer="none",
    )
    return build_context(
        task_message="# Task\nfix it",
        turns=turns,
        policy=policy,
        system="sys",
        tools=tool_specs(["read_file"]),
        max_output_tokens=500,
        budget_text="Budget remaining {}",
        request_template={"temperature": "0.000000", "seed": None, "reasoning": None},
    )


def test_latest_eight_turns_stay_full_and_older_outputs_become_deterministic_summaries() -> None:
    turns = [_turn(i) for i in range(12)]
    first, second = _build(turns), _build(turns)
    assert first.messages == second.messages  # pure function of committed state
    assert first.retained_full == tuple(range(4, 12)) and first.compacted_turns == (0, 1, 2, 3)
    assert first.dropped_turns == () and first.over_ceiling_events == ()
    rendered = [
        block.content
        for message in first.messages
        for block in message.blocks
        if block.kind == "tool_result"
    ]
    assert (
        json.loads(rendered[0])["compacted"] is True and json.loads(rendered[0])["path"] == "f0.py"
    )
    assert rendered[-1].splitlines()[0].startswith("{") and "x" * 50 in rendered[-1]
    # every assistant tool call still has its result, so providers accept the history
    calls = [b.call_id for m in first.messages for b in m.blocks if b.kind == "tool_call"]
    results = [b.call_id for m in first.messages for b in m.blocks if b.kind == "tool_result"]
    assert calls == results
    assert first.messages[-1].blocks[-1].kind == "text"  # the budget line rides the last user turn


def test_over_ceiling_drops_oldest_summaries_then_summarises_recent_turns() -> None:
    turns = [_turn(i, size=4000) for i in range(12)]
    build = _build(turns, ceiling=9500)
    assert build.dropped_turns and build.estimated_tokens <= 9500
    kinds = {event.kind for event in build.over_ceiling_events}
    assert "truncation" in kinds
    kept = {t for t in range(12)} - set(build.dropped_turns)
    # dropping whole turns keeps call/result pairs valid
    calls = [b.call_id for m in build.messages for b in m.blocks if b.kind == "tool_call"]
    results = [b.call_id for m in build.messages for b in m.blocks if b.kind == "tool_result"]
    assert calls == results and len(calls) == len(kept)
    assert build.retained_full[-1] == 11  # the latest turn is never summarised away


def test_core_context_that_cannot_fit_is_a_declared_failure() -> None:
    with pytest.raises(ContextBudgetExceeded):
        _build([_turn(0, size=40_000)], ceiling=300)


def test_oversized_latest_turn_results_are_clipped_and_recorded_before_failing() -> None:
    built = _build([_turn(0, size=40_000)], ceiling=2000)
    assert built.estimated_tokens <= 2000
    assert any("clip_latest_turn" in e.reason for e in built.over_ceiling_events)


def test_summaries_name_the_tool_paths_status_and_artifact_only() -> None:
    result = ToolResult(
        event_seq=4,
        tool_call_id="c",
        name="run_command",
        status="error",
        error_code="timeout",
        content="SECRET OUTPUT " * 100,
        original_bytes=1400,
        returned_bytes=1400,
        artifact_id="art-1",
    )
    call = ToolCallBlock(
        call_id="c", name="run_command", arguments_json=json.dumps({"command": "make " * 50})
    )
    summary = json.loads(summarize_result(call, result))
    assert summary["tool"] == "run_command" and summary["error_code"] == "timeout"
    assert summary["artifact_id"] == "art-1" and len(summary["command"]) <= 80
    assert "SECRET" not in json.dumps(summary)


# -------------------------------------------------------------------------- extraction


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_extraction_fixtures(case: dict[str, Any]) -> None:
    extraction = extract_from_response(
        case["response"],
        contract=contract(case["contract"]),
        rule=case["rule"],
        required_outputs=case["required"],
    )
    expected = case["expect"]
    assert extraction.validity == expected["validity"]
    assert sorted(extraction.reasons) == sorted(expected["reasons"])
    if expected.get("has_patch"):
        assert "patch" in extraction.payload and extraction.payload["patch_digest"].startswith(
            "sha256:"
        )
    if "findings_kept" in expected:
        assert len(extraction.payload["findings"]) == expected["findings_kept"]
    # deterministic: the same response always yields the same bytes and digest
    again = extract_from_response(
        case["response"],
        contract=contract(case["contract"]),
        rule=case["rule"],
        required_outputs=case["required"],
    )
    assert (
        again.content_bytes() == extraction.content_bytes()
        and again.digest() == extraction.digest()
    )


def test_extraction_is_pure_and_uses_no_model() -> None:
    import inspect

    import polycodebench_core.solve_extraction as module

    source = inspect.getsource(module)
    assert "gateway" not in source.lower() and "async def" not in source
    assert "httpx" not in source and "urllib" not in source


def test_workspace_freezing_is_as_written_with_outside_changes_recorded_only() -> None:
    contract_files = contract("two_files")
    workspace = {"src/main.py": b"print(1)\n", "scratch/notes.txt": b"tmp", "tests/t.py": b"same"}
    extraction = freeze_workspace_files(
        workspace,
        contract=contract_files,
        required_outputs=["src/main.py"],
        protected_baseline={"tests/t.py": b"same"},
    )
    assert extraction.validity == "valid"
    assert [f["path"] for f in extraction.payload["files"]] == ["src/main.py"]
    assert extraction.ignored == (
        "outside_contract:scratch/notes.txt",
        "outside_contract:tests/t.py",
    )
    tampered = freeze_workspace_files(
        {**workspace, "tests/t.py": b"changed"},
        contract=contract_files,
        required_outputs=["src/main.py"],
        protected_baseline={"tests/t.py": b"same"},
    )
    assert tampered.validity == "contract_invalid" and "protected_path_modified" in tampered.reasons
    deleted = freeze_workspace_files(
        {"src/main.py": b"x"},
        contract=contract_files,
        required_outputs=["src/main.py"],
        protected_baseline={"tests/t.py": b"same"},
    )
    assert "protected_path_modified" in deleted.reasons


def test_workspace_patch_diffs_only_the_output_contract() -> None:
    patch_contract = contract("patch")
    base = {"src/a.py": b"one\ntwo\n", "src/old.py": b"bye\n", "README": b"r\n"}
    final = {"src/a.py": b"one\nTWO\n", "src/new.py": b"hello", "README": b"changed\n"}
    extraction = workspace_patch(base, final, contract=patch_contract, protected_baseline={})
    assert extraction.validity == "valid"
    diff = extraction.payload["patch"]
    assert "--- a/src/a.py\n+++ b/src/a.py" in diff and "-two\n+TWO\n" in diff
    assert "--- /dev/null\n+++ b/src/new.py" in diff and "\\ No newline at end of file" in diff
    assert "--- a/src/old.py\n+++ /dev/null" in diff
    assert "README" not in diff and extraction.ignored == ("outside_contract:README",)
    empty = workspace_patch(base, dict(base), contract=patch_contract, protected_baseline={})
    assert empty.validity == "contract_invalid" and empty.reasons == ("empty_patch",)


def test_workspace_patch_splits_on_newlines_only_and_records_empty_files() -> None:
    patch_contract = contract("patch")
    base = {"src/a.py": b"one\x0ctwo\nthree\n", "src/gone.py": b""}
    final = {"src/a.py": b"one\x0ctwo\nTHREE\n", "src/empty.py": b""}
    extraction = workspace_patch(base, final, contract=patch_contract, protected_baseline={})
    assert extraction.validity == "valid"
    diff = extraction.payload["patch"]
    assert "@@ -1,2 +1,2 @@\n one\x0ctwo\n-three\n+THREE\n" in diff
    assert "--- /dev/null\n+++ b/src/empty.py\n" in diff
    assert "--- a/src/gone.py\n+++ /dev/null\n" in diff


def test_patch_extraction_honours_protected_paths_and_never_raises() -> None:
    patch_contract = contract("patch")
    diff = "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    text = json.dumps({"patch": diff})
    hit = extract_from_response(
        text,
        contract=patch_contract,
        rule="json_envelope",
        required_outputs=[],
        protected_paths=["src/x.py"],
    )
    assert hit.reasons == ("patch_touches_protected_path",)
    for hostile in ("\ud800", "[" * 200_000, "{" * 200_000, "\x00"):
        result = extract_from_response(
            hostile, contract=patch_contract, rule="json_envelope", required_outputs=[]
        )
        assert result.validity == "contract_invalid"


def test_model_response_contract_is_unchanged_by_freezing() -> None:
    response = ModelResponse(
        provider_request_id=None,
        finish_reason=FinishReason.STOP,
        raw_finish_reason="stop",
        blocks=(TextBlock(text="hi"),),
        usage=Usage(),
    )
    assert response.text == "hi"
