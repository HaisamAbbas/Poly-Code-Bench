"""Turn a ``go test`` run (supervisor record + runner capture) into test evidence.

Candidate errors (failed assertions, a panicking test, a case that hangs until the deadline,
compile errors in candidate code) are kept apart from harness errors (no runner record, no package
verdict, an unreadable capture, compile errors in the hidden test files that the candidate cannot
have caused). The distinction uses the structured ``go test -json`` event stream and the
supervisor's execution record, not the exit code alone - ``go test`` exits 1 for a failing test, a
failing build and a data race alike (Technical Spec 12.2).
"""

from __future__ import annotations

import json
import re
from typing import Any

from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api import ArtifactReader, TestGroupPlan, plan_status
from polycodebench_plugins_api.testreport import (
    GroupControl,
    InventoryGroup,
    TestCaseRecord,
)

from polycodebench_lang_go.guestmods import load_guest

_RANK = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}
_COMPILE_AT = re.compile(r"^(?P<path>[^\s:]+\.go):(?P<line>\d+):(?P<col>\d+):\s*(?P<msg>[^\n]+)$")
_NOISE = ("is unused", "imported and not used", "declared and not used")
_SIGNAL = re.compile(r"signal: (?P<signal>[a-z]+)")


def candidate_error_sites(text: str, candidate_paths: set[str]) -> tuple[list[str], list[str]]:
    """Split compile errors into (candidate-attributable, harness-attributable) sites."""
    candidate: list[str] = []
    harness: list[str] = []
    for line in text.splitlines():
        match = _COMPILE_AT.match(line.strip())
        if match is None:
            continue
        message = match.group("msg")
        if any(noise in message for noise in _NOISE):
            continue
        path = match.group("path").replace("\\", "/").removeprefix("work/")
        site = f"{path}:{match.group('line')}"
        if path in candidate_paths:
            candidate.append(site)
        else:
            harness.append(site)
    return candidate, harness


def parse_group_report(
    group: TestGroupPlan,
    inventory: InventoryGroup,
    raw: ArtifactReader,
    *,
    repetition: int,
    candidate_paths: set[str] | None = None,
) -> tuple[list[TestCaseRecord], GroupControl]:
    plan = group.plan
    identity = f"{plan.tool.name}-{plan.tool.version}@{plan.image_digest[:19]}"
    status, record = plan_status(plan, raw)
    name = group.group_id

    def control(kind: str, detail: str = "", **extra: Any) -> GroupControl:
        return GroupControl(
            group_id=group.group_id,
            repetition=repetition,
            status=kind,  # type: ignore[arg-type]
            detail=detail[:300],
            **extra,
        )

    if record is None:
        return [], control("harness_failure", "no supervisor execution record")
    present = set(raw.list())
    if f"out/{name}.run.json" not in present:
        return [], control("harness_failure", "no runner record")
    try:
        run = json.loads(raw.read(f"out/{name}.run.json"))
    except ValueError:
        return [], control("harness_failure", "runner record is unreadable")
    text = (
        raw.read(f"out/{name}.out").decode("utf-8", errors="replace")
        if f"out/{name}.out" in present
        else ""
    )
    stderr = (
        raw.read(f"out/{name}.err").decode("utf-8", errors="replace")
        if f"out/{name}.err" in present
        else ""
    )
    timed_out = bool(run.get("timed_out")) or status == "timed_out"
    events = list(
        load_guest("pcb_go_test_report").build_records(
            text, stderr, None if timed_out else run.get("exit_code"), timed_out
        )
    )
    finish = next(e for e in events if e["kind"] == "session_finish")
    known = {case.case_id for case in inventory.cases}
    required = {case.case_id: case.required for case in inventory.cases}
    cases: dict[str, TestCaseRecord] = {}
    unexpected: list[str] = []
    for event in events:
        if event["kind"] != "case":
            continue
        node = str(event["name"])
        if node not in known:
            unexpected.append(node)
        outcome = str(event["outcome"])
        existing = cases.get(node)
        if existing is not None and _RANK[existing.outcome] >= _RANK[outcome]:
            continue
        cases[node] = TestCaseRecord(
            group_id=group.group_id,
            repetition=repetition,
            case_id=node,
            required=required.get(node, False),
            input_seed=None,
            expected_outcome_digest=canonical_digest({"case": node, "expected": "pass"}),
            outcome=outcome,  # type: ignore[arg-type]
            reason=_failure_reason(text, node)[:200],
            duration_ms=int(event.get("duration_ms", 0)),
            max_rss_kb=None,
            stdout_digest=None,
            stderr_digest=None,
            execution_identity=identity,
        )
    records = [cases[key] for key in sorted(cases)]
    in_flight = [str(e["name"]) for e in events if e["kind"] == "case_start"]
    if timed_out:
        return records, control(
            "candidate_timeout",
            "run exceeded its time limit",
            in_flight_case=in_flight[0] if in_flight else None,
        )
    signal = _SIGNAL.search(text)
    if signal:
        return records, control(
            "candidate_killed",
            f"a test binary was terminated by signal {signal.group('signal')}",
            in_flight_case=in_flight[0] if in_flight else None,
        )
    if not finish.get("build_ok", True):
        # Nothing ran. Compile errors in candidate code are the candidate's; errors confined to the
        # hidden test files are not something the candidate can cause.
        candidate_sites, harness_sites = candidate_error_sites(
            text + "\n" + stderr, candidate_paths or set()
        )
        return records, control(
            "finished",
            "",
            candidate_collection_errors=tuple(candidate_sites),
            harness_collection_errors=tuple(harness_sites) if not candidate_sites else (),
        )
    if not finish.get("summary"):
        return records, control("harness_failure", "no package verdict in the captured output")
    if status == "tool_error":
        return records, control("harness_failure", f"unexpected exit code {run.get('exit_code')}")
    return records, control("finished", "", unexpected_cases=tuple(sorted(set(unexpected))))


def _failure_reason(text: str, node: str) -> str:
    """The test binary's own failure output for one case, when it printed any."""
    marker = f"--- FAIL: {node}"
    index = text.find(marker)
    if index < 0:
        return ""
    tail = text[index : index + 1200]
    for line in tail.splitlines():
        stripped = line.strip()
        if stripped.startswith(("%", "Error", "error", "panic", "expected", "got")):
            return stripped
    return tail.splitlines()[0].strip()
