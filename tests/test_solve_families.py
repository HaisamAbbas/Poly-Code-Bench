# ruff: noqa: F811 - pytest fixtures are imported from the PostgreSQL test module
"""Agent sessions under the other family output contracts: patch, text and findings.

EVIDENCE LABEL: FIXTURE model; real PostgreSQL, artifact store and local Docker sandbox.
Needs ``PCB_TEST_DOCKER=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from model_gateway_support import ScriptedTransport
from polycodebench_core.models import TaskOutputContract
from polycodebench_runner import guest_helper
from solve_support import CREATE_SOLUTION, SOLUTION, VISIBLE, final, reply, tool_call
from test_model_gateway_postgres import artifacts, database  # noqa: F401
from test_solve_sessions import harness, with_sandbox  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1", reason="agent tests need PCB_TEST_DOCKER=1"
)
AGENT = "standard-agent-v1"


def contract(kind: str, **overrides: Any) -> TaskOutputContract:
    values: dict[str, Any] = {
        "schema_version": 1,
        "kind": "task_output_contract",
        "submission_kind": kind,
        "allowed_paths": ["solution.py"],
        "maximum_artifact_bytes": 100_000,
        "maximum_file_bytes": 50_000,
        "maximum_files": 1,
        "findings_limit": None,
    }
    values.update(overrides)
    return TaskOutputContract.model_validate(values, strict=False)


def candidate_content(h: Any, result: Any) -> dict[str, Any]:
    return json.loads(h.store().get(UUID(result.outcome.candidate_artifact_id)))  # type: ignore[no-any-return]


def run_agent(h: Any, assignment: Any, transport: ScriptedTransport) -> Any:
    async def body(sandbox: Any, handle: Any) -> Any:
        return await h.agent(assignment, transport, sandbox, handle)

    return with_sandbox(body)


def test_patch_task_freezes_a_unified_diff_that_reapplies_to_the_base(
    harness, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    h = harness
    transport = ScriptedTransport(
        [
            reply(
                tool_call("p1", "apply_patch", {"diff": CREATE_SOLUTION}),
                tool_call("p2", "run_command", {"command": "echo scratch > notes.tmp"}),
            ),
            final("done"),
        ]
    )
    assignment = h.assignment(0, AGENT, contract=contract("patch"), required=[])
    result = run_agent(h, assignment, transport)
    assert result.outcome.status == "candidate_frozen" and result.outcome.validity == "valid"
    stored = candidate_content(h, result)
    assert stored["submission_kind"] == "patch"
    diff = stored["payload"]["patch"]
    assert diff.startswith("--- /dev/null\n+++ b/solution.py\n") and "notes.tmp" not in diff
    row = h.repo.candidate_for(h.world.attempts[0])
    assert row is not None and "outside_contract:notes.tmp" in row.payload["ignored_changes"]

    # round trip: the frozen diff applies to a pristine visible workspace and reproduces the file
    for path, data in VISIBLE.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    inbox = tmp_path / ".pcb_inbox"
    inbox.mkdir()
    (inbox / "frozen.patch").write_text(diff, encoding="utf-8")
    applied = guest_helper.op_apply_patch(
        str(tmp_path),
        {"patch_file": ".pcb_inbox/frozen.patch", "protected": [], "max_file_bytes": 10**6},
    )
    assert applied["files"][0]["action"] == "create"
    assert (tmp_path / "solution.py").read_text() == SOLUTION


def test_text_task_reads_only_the_final_reply_and_keeps_invalid_replies_as_evidence(
    harness,
) -> None:  # type: ignore[no-untyped-def]
    h = harness
    text_contract = contract("text", allowed_paths=["answer.txt"])
    good = ScriptedTransport(
        [
            reply(tool_call("t1", "read_file", {"path": "task.md"})),
            final(json.dumps({"answer": "42"})),
        ]
    )
    result = run_agent(h, h.assignment(0, AGENT, contract=text_contract, required=[]), good)
    assert result.outcome.status == "candidate_frozen"
    assert candidate_content(h, result)["payload"]["answer"] == "42"

    prose = ScriptedTransport([final("The answer is forty-two, I believe.")])
    failed = run_agent(h, h.assignment(1, AGENT, contract=text_contract, required=[]), prose)
    assert failed.outcome.status == "model_failure"
    assert "envelope_not_json" in failed.outcome.reason
    assert h.repo.candidate_for(h.world.attempts[1]) is not None  # the invalid submission is kept


def test_findings_over_the_limit_are_invalid_but_a_valid_patch_stays_evaluable(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    findings = [
        {
            "local_id": f"f{n}",
            "path": "solution.py",
            "start_line": n,
            "end_line": n,
            "root_cause": "off by one",
            "severity": "low",
        }
        for n in (1, 2, 3)
    ]
    patch = "--- a/solution.py\n+++ b/solution.py\n@@ -1 +1 @@\n-a\n+b\n"
    transport = ScriptedTransport([final(json.dumps({"findings": findings, "patch": patch}))])
    assignment = h.assignment(
        0, AGENT, contract=contract("structured_findings", findings_limit=2), required=[]
    )
    result = run_agent(h, assignment, transport)
    assert result.outcome.validity == "contract_invalid"
    assert result.outcome.status == "candidate_frozen"  # the patch part can still be evaluated
    payload = candidate_content(h, result)["payload"]
    assert payload["findings"] == [] and payload["findings_count"] == 3  # never silently trimmed
    assert payload["patch_digest"].startswith("sha256:")

    no_patch = ScriptedTransport([final(json.dumps({"findings": findings}))])
    second = run_agent(
        h,
        h.assignment(
            1, AGENT, contract=contract("structured_findings", findings_limit=2), required=[]
        ),
        no_patch,
    )
    assert (
        second.outcome.status == "model_failure" and "findings_over_limit" in second.outcome.reason
    )


def test_answer_tasks_cannot_freeze_a_workspace_when_the_budget_runs_out(harness) -> None:  # type: ignore[no-untyped-def]
    h = harness
    text_contract = contract("text", allowed_paths=["answer.txt"])
    transport = ScriptedTransport([reply(tool_call("x1", "list_files", {}))])
    assignment = h.assignment(0, AGENT, contract=text_contract, required=[], maximum_model_turns=1)
    result = run_agent(h, assignment, transport)
    assert result.outcome.status == "model_failure"
    assert result.outcome.budget_exhausted == "model_turns"
    assert "no_final_answer" in result.outcome.reason
    assert h.repo.candidate_for(h.world.attempts[0]) is None  # nothing was submitted, nothing faked
    assert len(transport.sent) == 1
