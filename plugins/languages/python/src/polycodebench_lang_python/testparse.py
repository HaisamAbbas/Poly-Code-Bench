"""Turn a pytest run (supervisor record + trusted report plugin output) into test evidence.

Candidate errors (assertion failures, exceptions, case timeouts, import errors in candidate
modules, a hung or OOM-killed run) are kept apart from harness errors (no control evidence,
unreadable report, internal pytest error, errors in the hidden test modules themselves). The
distinction uses the structured records and the supervisor's execution record, not the exit code
alone (Technical Spec 12.2).
"""

from __future__ import annotations

import json
from typing import Any

from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api import ArtifactReader, TestGroupPlan, plan_status
from polycodebench_plugins_api.testreport import (
    GroupControl,
    InventoryGroup,
    TestCaseRecord,
)

_RANK = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}
_KILL_CODES = {137, 139, 143}


def _base_id(node_id: str) -> str:
    return node_id.split("[", 1)[0]


def _candidate_text(text: str) -> bool:
    return "work/" in text.replace("\\", "/")


def parse_group_report(
    group: TestGroupPlan,
    inventory: InventoryGroup,
    raw: ArtifactReader,
    *,
    repetition: int,
) -> tuple[list[TestCaseRecord], GroupControl]:
    plan = group.plan
    identity = f"{plan.tool.name}-{plan.tool.version}@{plan.image_digest[:19]}"
    status, record = plan_status(plan, raw)

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
    events: list[dict[str, Any]] = []
    unreadable = False
    report_path = f"out/{group.group_id}.jsonl"
    if report_path in raw.list():
        for line in raw.read(report_path).decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                unreadable = True
    finished = [e for e in events if e.get("type") == "session_finish"]
    started = {e["nodeid"] for e in events if e.get("type") == "case_start"}
    done = {e["nodeid"] for e in events if e.get("type") == "case"}
    in_flight = sorted(started - done)
    collection_errors = [e for e in events if e.get("type") == "collection_error"]
    candidate_errors = tuple(
        e["nodeid"] for e in collection_errors if _candidate_text(e.get("reason_tail", ""))
    )
    harness_errors = tuple(
        e["nodeid"] for e in collection_errors if not _candidate_text(e.get("reason_tail", ""))
    )
    known = {case.case_id for case in inventory.cases}
    required = {case.case_id: case.required for case in inventory.cases}
    cases: dict[str, TestCaseRecord] = {}
    unexpected: list[str] = []
    for event in events:
        if event.get("type") != "case":
            continue
        node = _base_id(str(event["nodeid"]))
        if node not in known:
            unexpected.append(node)
        resources = event.get("resources") or {}
        outcome = str(event["outcome"])
        existing = cases.get(node)
        if existing is not None and _RANK[existing.outcome] >= _RANK[outcome]:
            cases[node] = existing.model_copy(
                update={"duration_ms": existing.duration_ms + int(event.get("duration_ms", 0))}
            )
            continue
        cases[node] = TestCaseRecord(
            group_id=group.group_id,
            repetition=repetition,
            case_id=node,
            required=required.get(node, False),
            input_seed="hypothesis-derandomized"
            if "property" in event.get("markers", [])
            else None,
            expected_outcome_digest=canonical_digest({"case": node, "expected": "pass"}),
            outcome=outcome,  # type: ignore[arg-type]
            reason=str(event.get("reason", ""))[:200],
            duration_ms=int(event.get("duration_ms", 0))
            + (existing.duration_ms if existing else 0),
            max_rss_kb=resources.get("max_rss_kb"),
            stdout_digest=event.get("stdout_digest"),
            stderr_digest=event.get("stderr_digest"),
            execution_identity=identity,
        )
    records = [cases[key] for key in sorted(cases)]
    if status == "timed_out":
        return records, control(
            "candidate_timeout",
            "run exceeded its time limit",
            in_flight_case=_base_id(in_flight[0]) if in_flight else None,
        )
    exit_code = record.exit_code
    if exit_code in _KILL_CODES and (in_flight or not finished):
        return records, control(
            "candidate_killed",
            f"terminated by signal (exit {exit_code})",
            in_flight_case=_base_id(in_flight[0]) if in_flight else None,
        )
    if unreadable:
        return records, control("harness_failure", "test report contains unreadable lines")
    if not finished:
        return records, control("harness_failure", "report has no session_finish record")
    pytest_status = int(finished[-1]["exitstatus"])
    if pytest_status in {3, 4} or (pytest_status == 5 and not collection_errors):
        return records, control("harness_failure", f"pytest exit status {pytest_status}")
    if pytest_status == 2 and not collection_errors:
        return records, control(
            "harness_failure", "pytest was interrupted without a collection error"
        )
    if status == "tool_error" and not (collection_errors and pytest_status == 2):
        return records, control("harness_failure", f"unexpected exit code {exit_code}")
    return records, control(
        "finished",
        "",
        unexpected_cases=tuple(sorted(set(unexpected))),
        candidate_collection_errors=candidate_errors,
        harness_collection_errors=harness_errors,
    )
