# ruff: noqa: F811 - pytest fixtures are imported from the PostgreSQL test module
"""Prompt 09: single-shot and standard-agent solve sessions (E2E-13, E2E-14 and their variants).

EVIDENCE LABEL: real PostgreSQL, real artifact store, real model gateway/ledger and (for agent
tests) a real local Docker sandbox. The MODEL is a deterministic FIXTURE (scripted provider
responses); nothing here is a live model run, and local Docker is development isolation only.
Agent tests need ``PCB_TEST_DOCKER=1``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest
from model_gateway_support import ScriptedTransport
from polycodebench_core.application_errors import PersistenceConflict
from polycodebench_core.model_contracts import ToolCallBlock
from polycodebench_core.solve_contracts import (
    CheckpointMismatch,
    SolveError,
    SolveInterrupted,
    ToolErrorCode,
)
from polycodebench_orchestration.solve.inspection import inspect_attempt
from polycodebench_orchestration.solve.session import AgentSession, SolveConfigurationError
from polycodebench_persistence.models import artifact_quota, call_delivery, call_intent, candidate
from polycodebench_persistence.solve_state import NewCheckpoint, NewEvent
from polycodebench_runner.guest_tools import (
    GuestInfrastructureError,
    GuestToolbox,
    WorkspaceLimitExceeded,
)
from polycodebench_runner.provider import LocalDockerSandboxProvider
from solve_support import (
    CREATE_SOLUTION,
    HIDDEN_MARKER,
    SOLUTION,
    VISIBLE,
    Harness,
    docker_provider,
    envelope_files,
    final,
    reply,
    request_bodies,
    sandbox_spec,
    tool_call,
)
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from test_model_gateway_postgres import artifacts, build_world, database  # noqa: F401

needs_docker = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1", reason="agent tests need PCB_TEST_DOCKER=1"
)
AGENT = "standard-agent-v1"
SINGLE = "single-shot-v1"


def _seed_quota(database):  # type: ignore[no-untyped-def]
    with database.engine.begin() as connection:
        connection.execute(
            pg_insert(artifact_quota)
            .values(
                visibility="internal",
                encryption_domain="solve-session",
                max_bytes=2_000_000_000,
                used_bytes=0,
                reserved_bytes=0,
            )
            .on_conflict_do_nothing(index_elements=["visibility", "encryption_domain"])
        )


@pytest.fixture
def harness(database, artifacts):  # type: ignore[no-untyped-def]
    _seed_quota(database)
    return Harness(build_world(database, artifacts), database.engine, artifacts)


def with_sandbox(
    body: Callable[[LocalDockerSandboxProvider, Any], Awaitable[Any]],
) -> Any:
    async def runner() -> Any:
        sandbox = docker_provider()
        handle = await sandbox.create(sandbox_spec())
        try:
            return await body(sandbox, handle)
        finally:
            await sandbox.destroy(handle)

    return asyncio.run(runner())


def kinds(h: Harness, attempt_index: int) -> list[str]:
    return [kind for _, kind, _ in h.events(h.world.attempts[attempt_index])]


# ============================================================================= E2E-13


@needs_docker
def test_e2e13_same_task_single_shot_and_agent_differ_exactly_as_configured(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    single_transport = ScriptedTransport([reply_text(envelope_files())])
    single = asyncio.run(h.single_shot(h.assignment(0, SINGLE), single_transport))

    agent_transport = ScriptedTransport(
        [
            reply(
                tool_call("c1", "list_files", {"path": ".", "depth": 2}),
                tool_call("c2", "read_file", {"path": "task.md"}),
                request_id="t0",
            ),
            reply(
                tool_call("c3", "apply_patch", {"diff": CREATE_SOLUTION}),
                tool_call("c4", "run_public_tests", {"group_ids": ["basic"]}),
                request_id="t1",
            ),
            final("Implemented solution.py and the public test passes."),
        ]
    )

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(h.assignment(1, AGENT), agent_transport, sandbox, handle)

    agent = with_sandbox(body)

    # --- both protocols produced the same valid candidate content, through different paths
    assert single.outcome.status == agent.outcome.status == "candidate_frozen"
    assert single.outcome.validity == agent.outcome.validity == "valid"
    for result in (single, agent):
        stored = json.loads(h.store().get(UUID(result.outcome.candidate_artifact_id or "")))
        entry = stored["payload"]["files"][0]
        assert entry["path"] == "solution.py" and entry["size"] == len(SOLUTION)
        assert base64.b64decode(entry["content_b64"]).decode() == SOLUTION

    # --- tool availability: none for single-shot, the effective standard set for the agent
    single_bodies, agent_bodies = request_bodies(single_transport), request_bodies(agent_transport)
    assert len(single_bodies) == 1 and "tools" not in single_bodies[0]
    assert len(agent_bodies) == 3
    expected_tools = [
        "list_files",
        "read_file",
        "search",
        "apply_patch",
        "run_command",
        "run_public_tests",
    ]
    for body_ in agent_bodies:
        assert [t["function"]["name"] for t in body_["tools"]] == expected_tools
    # provider order is preserved in the replayed assistant message, results follow in order
    second = agent_bodies[1]["messages"]
    assistant = next(m for m in second if m["role"] == "assistant")
    assert [c["id"] for c in assistant["tool_calls"]] == ["c1", "c2"]
    tool_messages = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
    assert "task.md" in tool_messages[0]["content"] and "Double it" in tool_messages[1]["content"]
    assert (
        "exit 0" in h.results(h.world.attempts[1])[-1]["result"]["content"]
    )  # public tests passed

    # --- transcript, budgets and cohort identity differ exactly as configured
    single_events, agent_events = h.events(h.world.attempts[0]), h.events(h.world.attempts[1])
    started_single = next(p for _, k, p in single_events if k == "session_started")
    started_agent = next(p for _, k, p in agent_events if k == "session_started")
    assert started_single["mode"] == "single_shot" and started_single["tools"] == []
    assert started_agent["mode"] == "standard_agent" and started_agent["tools"] == expected_tools
    assert started_single["budget"]["model_turns"] == 1
    assert (
        started_agent["budget"]["model_turns"] == 30
        and started_agent["budget"]["tool_calls"] == 100
    )
    assert started_single["protocol_digest"] != started_agent["protocol_digest"]
    assert started_single["effective_protocol_digest"] != started_agent["effective_protocol_digest"]
    assert kinds(h, 0).count("model_turn") == 1 and kinds(h, 0).count("tool_result") == 0
    assert kinds(h, 1).count("model_turn") == 3 and kinds(h, 1).count("tool_result") == 4
    candidate_rows = {i: h.repo.candidate_for(h.world.attempts[i]) for i in (0, 1)}
    assert candidate_rows[0].payload["protocol_digest"] == started_single["protocol_digest"]  # type: ignore[union-attr]
    assert candidate_rows[1].payload["protocol_digest"] == started_agent["protocol_digest"]  # type: ignore[union-attr]

    # --- nothing hidden reached a model in either protocol
    for transport in (single_transport, agent_transport):
        assert all(HIDDEN_MARKER not in item["body"] for item in transport.sent)

    # --- run inspection reports the same facts
    report = inspect_attempt(h.repo, h.store(), h.world.attempts[1])
    assert report["protocol"]["mode"] == "standard_agent" and len(report["turns"]) == 3
    assert [c["name"] for c in report["tool_calls"]] == [
        "list_files",
        "read_file",
        "apply_patch",
        "run_public_tests",
    ]
    assert (
        report["terminal"]["status"] == "candidate_frozen"
        and report["candidate"]["validity"] == "valid"
    )
    assert report["budget_consumed"]["turns"] == 3 and report["budget_consumed"]["tool_calls"] == 4
    assert report["recoveries"] == [] and report["checkpoint"]["pending_call_ids"] == []


def reply_text(text: str) -> Any:
    from solve_support import ok

    return ok(text)


def test_single_shot_extraction_failures_are_model_failures_with_evidence_kept(harness) -> None:  # type: ignore[no-untyped-def]
    """An ambiguous answer is invalid; no model picks a better block; the original is kept."""
    h = harness
    fenced = h.assignment(0, SINGLE)
    fenced = type(fenced)(
        **{**fenced.__dict__, "effective": _with_rule(fenced, "single_fenced_block")}
    )
    ambiguous = "Try one:\n```python\nprint(1)\n```\nTry two:\n```python\nprint(2)\n```"
    transport = ScriptedTransport([reply_text(ambiguous)])
    result = asyncio.run(h.single_shot(fenced, transport))
    assert result.outcome.status == "model_failure"
    assert "ambiguous_multiple_blocks" in result.outcome.reason
    row = h.repo.candidate_for(h.world.attempts[0])
    assert row is not None and row.payload["validity"] == "contract_invalid"
    assert len(transport.sent) == 1  # no retry, no second opinion
    intent = h.world.ledger.intent_state(_intent_id(h, 0, "turn-0"))
    raw = h.artifacts.read_verified(intent["deliveries"][0]["raw_response_artifact_id"])[1]
    assert b"Try two" in raw  # the whole original response is preserved as internal evidence


def _with_rule(assignment: Any, rule: str) -> Any:
    from polycodebench_core.solve_contracts import EffectiveProtocol

    protocol = assignment.effective.protocol.model_copy(update={"single_shot_extraction": rule})
    return EffectiveProtocol(
        schema_version=1,
        kind="effective_protocol",
        protocol_digest=assignment.effective.protocol_digest,
        protocol=protocol,
        tools=[],
        budget=assignment.effective.budget,
    )


def _intent_id(h: Harness, index: int, key: str) -> UUID:
    with h.engine.connect() as connection:
        return connection.execute(  # type: ignore[no-any-return]
            select(call_intent.c.id).where(
                call_intent.c.attempt_id == h.world.attempts[index],
                call_intent.c.logical_call_key == key,
            )
        ).scalar_one()


def test_terminal_sessions_are_idempotent_and_candidates_are_immutable(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    assignment = h.assignment(0, SINGLE)
    first = asyncio.run(
        h.single_shot(assignment, ScriptedTransport([reply_text(envelope_files())]))
    )
    count = len(h.events(h.world.attempts[0]))
    idle = ScriptedTransport([])  # any request would fail the test
    again = asyncio.run(h.single_shot(assignment, idle))
    assert idle.sent == [] and len(h.events(h.world.attempts[0])) == count
    assert (
        again.outcome == first.outcome
        and again.candidate_payload_digest == first.candidate_payload_digest
    )
    row = h.repo.candidate_for(h.world.attempts[0])
    assert row is not None
    with pytest.raises(PersistenceConflict):
        h.repo.freeze_candidate(
            h.world.attempts[0],
            payload_digest="sha256:" + "9" * 64,
            submission_kind="files",
            payload={},
            canonical_artifact_id=row.canonical_artifact_id,
        )
    same = h.repo.freeze_candidate(
        h.world.attempts[0],
        payload_digest=row.payload_digest,
        submission_kind="files",
        payload={},
        canonical_artifact_id=row.canonical_artifact_id,
    )
    assert same.candidate_id == row.candidate_id  # replay of identical bytes is a no-op
    with pytest.raises(DBAPIError), h.engine.begin() as connection:
        connection.execute(update(candidate).values(payload_digest="sha256:" + "0" * 64))


def test_hidden_material_never_reaches_the_model_even_if_it_leaks_into_the_task(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    leaky = dict(VISIBLE)
    leaky["task.md"] = b"# Task\nThe grader checks " + HIDDEN_MARKER + b" in the output.\n"
    transport = ScriptedTransport([reply_text(envelope_files())])
    with pytest.raises(SolveError, match="hidden task material"):
        asyncio.run(
            h.single_shot(
                h.assignment(0, SINGLE, visible=leaky),
                transport,
                forbidden_markers=(HIDDEN_MARKER,),
            )
        )
    assert transport.sent == []  # blocked before any request left the controller
    assert h.repo.candidate_for(h.world.attempts[0]) is None


def test_single_shot_response_persisted_before_commit_is_consumed_once(harness) -> None:  # type: ignore[no-untyped-def]
    from polycodebench_orchestration.solve.session import SingleShotSession

    h = harness

    class Crash(Exception):
        pass

    class CrashBeforeCommit(SingleShotSession):
        def _commit(self, events, *, workspace, pending):  # type: ignore[no-untyped-def]
            if any(kind == "model_turn" for kind, _ in events):
                raise Crash("controller died after the gateway stored the response")
            return super()._commit(events, workspace=workspace, pending=pending)

    assignment = h.assignment(0, SINGLE)
    transport_a = ScriptedTransport([reply_text(envelope_files())])
    with pytest.raises(Crash):
        asyncio.run(CrashBeforeCommit(**h.common(assignment, transport_a)).run())
    assert len(transport_a.sent) == 1 and "model_turn" not in kinds(h, 0)

    transport_b = ScriptedTransport([])  # a second request would raise
    result = asyncio.run(h.single_shot(assignment, transport_b))
    assert transport_b.sent == [] and result.outcome.status == "candidate_frozen"
    turn = next(p for _, k, p in h.events(h.world.attempts[0]) if k == "model_turn")
    assert turn["source"] == "stored"
    with h.engine.connect() as connection:
        deliveries = connection.execute(
            select(func.count())
            .select_from(call_delivery)
            .where(call_delivery.c.intent_id == _intent_id(h, 0, "turn-0"))
        ).scalar_one()
    assert deliveries == 1  # exactly one provider delivery for the logical turn


# ============================================================================= E2E-14


@needs_docker
def test_e2e14_guest_interrupted_mid_command_recovers_the_checkpoint(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    attempt = h.world.attempts[0]
    slow = "sleep 6; echo finished > slow.txt; echo finished"
    script = reply(
        tool_call("p1", "apply_patch", {"diff": CREATE_SOLUTION}),
        tool_call("p2", "run_command", {"command": slow, "timeout_seconds": 30}),
        tool_call("p3", "read_file", {"path": "solution.py"}),
        request_id="turn0",
    )
    transport_a = ScriptedTransport([script])
    assignment = h.assignment(0, AGENT)

    async def first_delivery(sandbox: Any, handle: Any) -> None:
        task = asyncio.create_task(h.agent(assignment, transport_a, sandbox, handle))
        for _ in range(300):  # wait until the slow command has been marked as started
            started = [p["tool_call_id"] for _, k, p in h.events(attempt) if k == "tool_started"]
            if "p2" in started:
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("the command never started")
        await sandbox.terminate(handle, "test_guest_died")  # the guest dies mid-command
        with pytest.raises(SolveInterrupted):
            await task

    with_sandbox(first_delivery)

    # --- what the crash left behind: a committed prefix, a pending command, no terminal state
    after_crash = h.events(attempt)
    crash_kinds = [k for _, k, _ in after_crash]
    assert crash_kinds == [
        "session_started",
        "model_turn",
        "tool_started",
        "tool_result",  # the patch was committed before the crash
        "tool_started",  # the slow command was issued ...
    ]  # ... but never committed a result
    assert h.repo.candidate_for(attempt) is None and "model_failure" not in crash_kinds
    checkpoint = h.repo.latest_checkpoint(attempt)
    assert checkpoint is not None and checkpoint.pending_call_ids == ["p2", "p3"]
    assert [s for s, _, _ in after_crash] == list(range(len(after_crash)))

    # --- a fresh process and a fresh guest resume from the checkpoint
    transport_b = ScriptedTransport([final("Done; solution.py is in place.")])

    async def second_delivery(sandbox: Any, handle: Any) -> Any:
        return await h.agent(assignment, transport_b, sandbox, handle)

    result = with_sandbox(second_delivery)
    assert result.outcome.status == "candidate_frozen" and result.outcome.validity == "valid"

    events = h.events(attempt)
    by_kind = [k for _, k, _ in events]
    assert by_kind.count("model_turn") == 2 and by_kind.count("recovery") == 1
    recovery = next(p for _, k, p in events if k == "recovery")
    assert recovery["pending_call_ids"] == ["p2", "p3"]
    assert recovery["replay_candidate_call_ids"] == ["p2"]  # only the started command may repeat
    results = {p["tool_call_id"]: p for p in h.results(attempt)}
    assert list(results) == ["p1", "p2", "p3"]  # one result per call, none duplicated
    assert results["p1"]["replayed"] is False  # the committed patch was not applied again
    assert results["p2"]["replayed"] is True and "finished" in results["p2"]["result"]["content"]
    assert (
        results["p3"]["replayed"] is False and "print(n * 2)" in results["p3"]["result"]["content"]
    )
    assert results["p1"]["workspace_digest"] != results["p3"]["workspace_digest"]
    budget = h.repo.latest_checkpoint(attempt).accumulated_budget  # type: ignore[union-attr]
    assert budget["repeated_compute_ms"] >= 5000 and budget["tool_calls"] == 3

    # --- model responses were consumed, never regenerated
    assert len(transport_a.sent) == 1 and len(transport_b.sent) == 1
    resumed_request = request_bodies(transport_b)[0]["messages"]
    assert [m["tool_call_id"] for m in resumed_request if m["role"] == "tool"] == ["p1", "p2", "p3"]
    with h.engine.connect() as connection:
        intents = (
            connection.execute(
                select(call_intent.c.logical_call_key).where(call_intent.c.attempt_id == attempt)
            )
            .scalars()
            .all()
        )
    assert sorted(intents) == ["turn-0", "turn-1"]  # one logical call per turn, no duplicates

    # --- the run inspection shows the interruption honestly
    report = inspect_attempt(h.repo, h.store(), attempt)
    assert report["recoveries"][0]["replay_candidate_call_ids"] == ["p2"]
    assert [c["replayed"] for c in report["tool_calls"]] == [False, True, False]


@needs_docker
def test_agent_turn_recorded_by_the_gateway_but_not_committed_is_consumed_once(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness

    class Crash(Exception):
        pass

    class CrashBeforeTurnOne(AgentSession):
        def _commit(self, events, *, workspace, pending):  # type: ignore[no-untyped-def]
            if any(k == "model_turn" and p["turn_index"] == 1 for k, p in events):
                raise Crash("died after turn 1 was recorded by the gateway")
            return super()._commit(events, workspace=workspace, pending=pending)

    assignment = h.assignment(0, AGENT)
    transport_a = ScriptedTransport(
        [
            reply(tool_call("a1", "apply_patch", {"diff": CREATE_SOLUTION})),
            final("finished"),
        ]
    )

    async def first(sandbox: Any, handle: Any) -> None:
        with pytest.raises(Crash):
            await h.agent(
                assignment, transport_a, sandbox, handle, session_class=CrashBeforeTurnOne
            )

    with_sandbox(first)
    assert len(transport_a.sent) == 2
    assert sum(1 for k in kinds(h, 0) if k == "model_turn") == 1

    transport_b = ScriptedTransport([])  # turn 1 must come from the stored response

    async def second(sandbox: Any, handle: Any) -> Any:
        return await h.agent(assignment, transport_b, sandbox, handle)

    result = with_sandbox(second)
    assert transport_b.sent == [] and result.outcome.status == "candidate_frozen"
    turns = [p for _, k, p in h.events(h.world.attempts[0]) if k == "model_turn"]
    assert [t["source"] for t in turns] == ["provider", "stored"]


# ============================================================ tools, caps and forbidden paths


@needs_docker
def test_tool_errors_are_typed_ordered_budgeted_and_grant_no_new_permissions(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    protected_patch = (
        "--- a/test_public.py\n+++ b/test_public.py\n@@ -1 +1 @@\n-import subprocess, sys\n+pass\n"
    )
    escape_patch = "--- a/../../x\n+++ b/../../x\n@@ -1 +1 @@\n-a\n+b\n"
    calls = [
        ("read_file", {"path": "../etc/passwd"}, None, ToolErrorCode.PATH_FORBIDDEN),
        ("read_file", {"path": "test_public.py"}, None, None),
        ("apply_patch", {"diff": protected_patch}, None, ToolErrorCode.PROTECTED_PATH),
        ("read_file", None, "{bad json", ToolErrorCode.INVALID_ARGUMENTS),
        ("list_files", {"depth": 99}, None, ToolErrorCode.INVALID_ARGUMENTS),
        ("rm_rf", {}, None, ToolErrorCode.UNKNOWN_TOOL),
        ("search", {"pattern": "(unclosed", "regex": True}, None, ToolErrorCode.REGEX_INVALID),
        ("read_file", {"path": "nope.txt"}, None, ToolErrorCode.NOT_FOUND),
        ("read_file", {"path": "repo"}, None, ToolErrorCode.NOT_A_FILE),
        ("apply_patch", {"diff": escape_patch}, None, ToolErrorCode.PATH_FORBIDDEN),
        ("list_files", {"path": ".", "depth": 2}, None, None),
        ("run_public_tests", {"group_ids": ["nope"]}, None, ToolErrorCode.UNKNOWN_TEST_GROUP),
        ("run_public_tests", {"group_ids": ["basic"]}, None, None),
        ("apply_patch", {"diff": CREATE_SOLUTION}, None, None),
        ("run_public_tests", {"group_ids": ["basic"]}, None, None),
        (
            "read_file",
            {"path": ".pcb_inbox/x", "start_line": 1},
            None,
            ToolErrorCode.PATH_FORBIDDEN,
        ),
    ]
    scripted = [
        tool_call(f"t{i}", name, args, raw=raw) for i, (name, args, raw, _) in enumerate(calls)
    ]
    transport = ScriptedTransport([reply(*scripted), final("done")])

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(h.assignment(0, AGENT), transport, sandbox, handle)

    result = with_sandbox(body)
    results = h.results(h.world.attempts[0])
    assert [r["tool_call_id"] for r in results] == [f"t{i}" for i in range(len(calls))]  # order
    assert [r["result"]["error_code"] for r in results] == [c[3] for c in calls]
    assert [r["result"]["status"] for r in results] == [
        "ok" if c[3] is None else "error" for c in calls
    ]
    seqs = [r["result"]["event_seq"] for r in results]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    consumed = h.repo.latest_checkpoint(h.world.attempts[0]).accumulated_budget  # type: ignore[union-attr]
    assert consumed["tool_calls"] == len(calls) and consumed["invalid_tool_calls"] == 3
    remaining = [r["result"]["budget_remaining"]["tool_calls"] for r in results]
    assert remaining == [100 - n for n in range(1, len(calls) + 1)]
    # the failed public run, then the passing one: only public results, no hidden feedback
    assert (
        "exit 1" in results[12]["result"]["content"]
        and "exit 0" in results[14]["result"]["content"]
    )
    assert "repo/README.md" in results[10]["result"]["content"]
    assert ".pcb_" not in results[10]["result"]["content"]
    # the protected file is unchanged and the session still produced the valid candidate
    assert result.outcome.status == "candidate_frozen"
    # a model that ignores errors gets no extra permission: the next request shows them all
    messages = [m for m in request_bodies(transport)[1]["messages"] if m["role"] == "tool"]
    assert len(messages) == len(calls) and "path_forbidden" in messages[0]["content"]
    assert all(HIDDEN_MARKER not in item["body"] for item in transport.sent)


@needs_docker
def test_outputs_are_truncated_timeouts_enforced_and_descendants_cleaned(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    transport = ScriptedTransport(
        [
            reply(
                tool_call("o1", "run_command", {"command": "yes y | head -c 3000000"}),
                tool_call("o2", "run_command", {"command": "sleep 40", "timeout_seconds": 2}),
                tool_call(
                    "o3", "run_command", {"command": "sleep 300 & setsid sleep 300 & echo started"}
                ),
                tool_call("o4", "run_command", {"command": "ls /proc | grep -c '^[0-9]'"}),
                tool_call(
                    "o5", "run_command", {"command": "ln -s /etc/passwd evil; mkfifo pipe; echo ok"}
                ),
                tool_call("o6", "list_files", {"path": ".", "depth": 1}),
                tool_call("o7", "run_command", {"command": "seq 1 100000 > big.txt"}),
                tool_call("o8", "read_file", {"path": "big.txt", "max_lines": 400}),
                tool_call("o9", "search", {"pattern": "99999", "path_glob": "big.txt"}),
                tool_call("oA", "run_command", {"command": "pwd", "working_directory": "repo"}),
                tool_call("oB", "run_command", {"command": "pwd", "working_directory": "../.."}),
            ),
            final("stopping"),
        ]
    )

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(h.assignment(0, AGENT), transport, sandbox, handle)

    result = with_sandbox(body)
    r = {item["tool_call_id"]: item for item in h.results(h.world.attempts[0])}
    big = r["o1"]["result"]
    assert big["truncated"] is True and big["original_bytes"] >= 3_000_000
    assert big["returned_bytes"] < 40_000 and big["artifact_id"]
    archived = h.artifacts.read_verified(UUID(big["artifact_id"]))[1]
    assert len(archived) < 1_500_000 and b"bytes omitted" in archived  # archive is capped too
    assert r["o2"]["result"]["error_code"] == ToolErrorCode.TIMEOUT
    assert "timed_out" in r["o2"]["result"]["content"]
    assert r["o3"]["result"]["status"] == "ok"
    assert int(r["o4"]["result"]["content"].split("--- stdout ---\n")[1].split()[0]) < 12
    assert r["o5"]["removed_unsafe"] == ["evil", "pipe"]
    assert "evil" not in r["o6"]["result"]["content"] and "pipe" not in r["o6"]["result"]["content"]
    assert (
        r["o8"]["result"]["truncated"] is True
        and "continue at line 401" in r["o8"]["result"]["content"]
    )
    assert "big.txt:99999:" in r["o9"]["result"]["content"]
    assert "/workspace/repo" in r["oA"]["result"]["content"]
    assert r["oB"]["result"]["error_code"] == ToolErrorCode.PATH_FORBIDDEN
    # an absent output is a model failure with evidence, never an infrastructure error
    assert result.outcome.status == "model_failure"
    assert "missing_required_output" in result.outcome.reason
    assert h.repo.candidate_for(h.world.attempts[0]).payload["validity"] == "contract_invalid"  # type: ignore[union-attr]


@needs_docker
def test_a_command_that_edits_a_protected_file_invalidates_the_candidate(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    transport = ScriptedTransport(
        [
            reply(
                tool_call("x1", "apply_patch", {"diff": CREATE_SOLUTION}),
                tool_call(
                    "x2",
                    "run_command",
                    {"command": "echo 'import sys; sys.exit(0)' > test_public.py"},
                ),
            ),
            final("done"),
        ]
    )

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(h.assignment(0, AGENT), transport, sandbox, handle)

    result = with_sandbox(body)
    tampered = h.results(h.world.attempts[0])[1]
    assert tampered["protected_violations"] == ["test_public.py"]
    assert "WARNING protected files were modified" in tampered["result"]["content"]
    assert (
        result.outcome.status == "model_failure"
        and "protected_path_modified" in result.outcome.reason
    )


@needs_docker
def test_a_task_can_narrow_the_tool_set_and_the_model_gets_no_extra_tools(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    transport = ScriptedTransport(
        [
            reply(
                tool_call("n1", "run_command", {"command": "echo hi"}),
                tool_call("n2", "read_file", {"path": "task.md"}),
            ),
            final("ok"),
        ]
    )
    assignment = h.assignment(
        0, AGENT, allowed_tools=["list_files", "read_file"], public_test_feedback=False
    )

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(assignment, transport, sandbox, handle)

    with_sandbox(body)
    first = request_bodies(transport)[0]
    assert [t["function"]["name"] for t in first["tools"]] == ["list_files", "read_file"]
    results = h.results(h.world.attempts[0])
    assert results[0]["result"]["error_code"] == ToolErrorCode.TOOL_NOT_ALLOWED
    assert results[1]["result"]["status"] == "ok"
    started = next(p for _, k, p in h.events(h.world.attempts[0]) if k == "session_started")
    assert started["tools"] == ["list_files", "read_file"]


# ================================================================================ budgets


@needs_docker
def test_turn_budget_exhaustion_freezes_a_valid_workspace_and_stops_requesting(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    transport = ScriptedTransport(
        [
            reply(tool_call("b1", "apply_patch", {"diff": CREATE_SOLUTION})),
            reply(tool_call("b2", "list_files", {})),
            reply(tool_call("b3", "list_files", {})),  # never requested: only two turns are allowed
        ]
    )
    assignment = h.assignment(0, AGENT, maximum_model_turns=2)

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(assignment, transport, sandbox, handle)

    result = with_sandbox(body)
    assert len(transport.sent) == 2
    assert result.outcome.status == "candidate_frozen" and result.outcome.validity == "valid"
    assert (
        result.outcome.budget_exhausted == "model_turns" and result.outcome.frozen_after_exhaustion
    )
    assert "budget_exhausted" in kinds(h, 0)


@needs_docker
def test_exhaustion_without_a_valid_workspace_is_a_model_failure_with_evidence(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    transport = ScriptedTransport(
        [reply(tool_call("e1", "list_files", {})), reply(tool_call("e2", "list_files", {}))]
    )
    assignment = h.assignment(0, AGENT, maximum_model_turns=2)

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(assignment, transport, sandbox, handle)

    result = with_sandbox(body)
    assert result.outcome.status == "model_failure"
    assert result.outcome.budget_exhausted == "model_turns"
    assert "budget_exhausted" in result.outcome.reason
    terminal = [k for k in kinds(h, 0) if k in {"candidate_frozen", "model_failure"}]
    assert terminal == ["model_failure"]


@needs_docker
def test_tool_call_budget_answers_the_overflow_with_typed_errors_then_stops(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    transport = ScriptedTransport(
        [
            reply(
                tool_call("k1", "apply_patch", {"diff": CREATE_SOLUTION}),
                tool_call("k2", "list_files", {}),
                tool_call("k3", "list_files", {}),
                tool_call("k4", "list_files", {}),
                tool_call("k5", "read_file", {"path": "solution.py"}),
            ),
            final("never requested"),
        ]
    )
    assignment = h.assignment(0, AGENT, maximum_tool_calls=3)

    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(assignment, transport, sandbox, handle)

    result = with_sandbox(body)
    results = h.results(h.world.attempts[0])
    assert [r["result"]["error_code"] for r in results] == [
        None,
        None,
        None,
        ToolErrorCode.BUDGET_EXHAUSTED,
        ToolErrorCode.BUDGET_EXHAUSTED,
    ]
    assert len(transport.sent) == 1  # the session stopped instead of asking again
    assert result.outcome.budget_exhausted == "tool_calls"
    assert result.outcome.status == "candidate_frozen"


@needs_docker
def test_gateway_spending_limit_ends_the_session_as_budget_exhaustion(database, artifacts) -> None:  # type: ignore[no-untyped-def]
    _seed_quota(database)
    limited = build_world(
        database,
        artifacts,
        resource_limits={"turns": 1, "input_tokens": 10**9, "output_tokens": 10**9},
    )
    local = Harness(limited, database.engine, artifacts)
    transport = ScriptedTransport(
        [reply(tool_call("g1", "apply_patch", {"diff": CREATE_SOLUTION})), final("unreachable")]
    )

    async def body(sandbox: Any, handle: Any) -> Any:
        return await local.agent(local.assignment(0, AGENT), transport, sandbox, handle)

    with pytest.raises(SolveInterrupted, match="spending limit"):
        with_sandbox(body)
    assert len(transport.sent) == 1  # the ledger refused turn two before it was dispatched
    # An operator limit is not the model's budget: nothing is frozen or blamed on the model.
    attempt_id = limited.attempts[0]
    kinds = [kind for _, kind, _ in local.events(attempt_id)]
    assert "candidate_frozen" not in kinds and "model_failure" not in kinds
    assert local.repo.candidate_for(attempt_id) is None


def test_agent_refuses_a_sandbox_that_cannot_honour_the_protocol_command_limit(harness) -> None:  # type: ignore[no-untyped-def]
    class ShortToolbox:
        max_command_seconds = 1

    with pytest.raises(SolveConfigurationError):
        AgentSession(
            toolbox=ShortToolbox(),  # type: ignore[arg-type]
            **harness.common(harness.assignment(0, AGENT), ScriptedTransport([])),
        )


# ====================================================================== checkpoints


def test_restore_refuses_a_workspace_paired_with_a_different_conversation(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    assignment = h.assignment(0, SINGLE)
    asyncio.run(h.single_shot(assignment, ScriptedTransport([reply_text(envelope_files())])))
    last = h.repo.latest_checkpoint(h.world.attempts[0])
    assert last is not None
    store = h.store()
    other_workspace = store.put(
        b"a different workspace revision", kind="t", media_type="text/plain"
    )
    from hashlib import sha256

    other_digest = "sha256:" + sha256(b"a different workspace revision").hexdigest()
    event = store.put(b'{"forged": true}', kind="t", media_type="application/json")
    # A checkpoint that names workspace W2 but the transcript manifest of a state built on W1.
    h.repo.commit(
        h.world.attempts[0],
        expected_last_seq=last.event_seq,
        events=[NewEvent("recovery", event)],
        checkpoint=NewCheckpoint(
            workspace_manifest_id=other_workspace,
            transcript_manifest_id=last.transcript_manifest_id,
            workspace_digest=other_digest,
            transcript_digest=last.transcript_digest,
            protocol_digest=last.protocol_digest,
            accumulated_budget=last.accumulated_budget,
            pending_call_ids=[],
        ),
    )
    with pytest.raises(CheckpointMismatch):
        asyncio.run(h.single_shot(assignment, ScriptedTransport([])))


def test_restore_refuses_a_checkpoint_from_another_protocol_identity(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    asyncio.run(
        h.single_shot(h.assignment(0, SINGLE), ScriptedTransport([reply_text(envelope_files())]))
    )
    narrower = h.assignment(0, SINGLE, maximum_wall_seconds=60)  # a different effective cohort
    with pytest.raises(CheckpointMismatch, match="different protocol"):
        asyncio.run(h.single_shot(narrower, ScriptedTransport([])))


def test_compare_and_swap_stops_a_stale_controller_from_forking_the_transcript(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    asyncio.run(
        h.single_shot(h.assignment(0, SINGLE), ScriptedTransport([reply_text(envelope_files())]))
    )
    event = h.store().put(b'{"x":1}', kind="t", media_type="application/json")
    with pytest.raises(PersistenceConflict):
        h.repo.commit(
            h.world.attempts[0],
            expected_last_seq=0,  # a controller that still believes nothing happened after seq 0
            events=[NewEvent("recovery", event)],
            checkpoint=None,
        )


def test_tool_call_blocks_round_trip_for_inspection_helpers() -> None:
    block = ToolCallBlock(call_id="c", name="read_file", arguments_json="{}")
    assert block.arguments_valid


@needs_docker
def test_a_workspace_beyond_the_checkpoint_limits_is_a_recorded_model_failure(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    calls = {"n": 0}

    class Oversized(GuestToolbox):
        async def snapshot(self):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] > 1:  # the initial snapshot is fine; the model's edit is not
                raise WorkspaceLimitExceeded("workspace has 200000 files and 1 bytes")
            return await super().snapshot()

    transport = ScriptedTransport(
        [reply(tool_call("w1", "apply_patch", {"diff": CREATE_SOLUTION})), final("unreachable")]
    )

    async def body(sandbox: Any, handle: Any) -> Any:
        session = AgentSession(
            toolbox=Oversized(sandbox, handle), **h.common(h.assignment(0, AGENT), transport)
        )
        return await session.run()

    result = with_sandbox(body)
    assert result.outcome.status == "model_failure"
    assert result.outcome.reason.startswith("workspace_limit_exceeded")
    assert h.repo.candidate_for(h.world.attempts[0]) is None


def test_helper_death_with_a_live_sandbox_is_a_tool_failure_not_infrastructure(harness) -> None:  # type: ignore[no-untyped-def]
    from polycodebench_core.model_contracts import ToolCallBlock
    from polycodebench_orchestration.solve.tools import ToolRunner

    class Flaky:
        alive = True

        async def run_command(self, *_args: Any) -> Any:
            raise GuestInfrastructureError("helper exited 137")

        async def process_count(self) -> list[int]:
            if not self.alive:
                raise GuestInfrastructureError("sandbox is gone")
            return []

    toolbox = Flaky()
    runner = ToolRunner(
        toolbox,  # type: ignore[arg-type]
        harness.assignment(0, AGENT),
        harness.store(),
        tools=["run_command"],
        per_command_seconds=10,
    )
    call = ToolCallBlock(
        call_id="c1", name="run_command", arguments_json=json.dumps({"command": "true"})
    )
    outcome = asyncio.run(runner.run(call, max_command_seconds=10))
    assert outcome.result.status == "error" and outcome.result.error_code == "tool_failure"
    assert outcome.mutated  # the workspace must be re-snapshotted after an uncertain command
    toolbox.alive = False
    with pytest.raises(GuestInfrastructureError):
        asyncio.run(runner.run(call, max_command_seconds=10))
