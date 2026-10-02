"""Turn a vitest run (supervisor record + the converted runner report) into test evidence.

Candidate errors (assertion failures, unhandled rejections, case timeouts, a module that cannot
be imported, a hung or killed run) are kept apart from harness errors (no control evidence,
unreadable report, a report the runner never finished writing). The distinction uses the structured
records and the supervisor's execution record, not the exit code alone (Technical Spec 12.2).

Vitest reports a case's identity as ``fullName`` (``describe`` titles joined by `` > `` plus the
test title), and that is exactly what ``hidden/oracle.json`` declares as ``case_id``. There is no
node id to normalise: the shared parser sees the same event vocabulary as the Python and Rust
plugins, so the supervisor learns nothing about which runner a language uses.
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
_KILL_CODES = {137, 139, 143, 124}
# Vitest exit statuses that describe the runner itself, not the candidate: 2 is an unhandled error
# outside any test, and 3 is a bad configuration. Both are harness failures.
_RUNNER_FAILURES = {2, 3}


def _candidate_text(text: str) -> bool:
    return "work/" in text.replace("\\", "/")


def parse_group_report(
    group: TestGroupPlan,
    inventory: InventoryGroup,
    raw: ArtifactReader,
    *,
    repetition: int,
    candidate_paths: set[str] | None = None,
) -> tuple[list[TestCaseRecord], GroupControl]:
    """Parse one repetition of one group.

    ``candidate_paths`` lets a failure be attributed to the candidate rather than to the harness:
    an unhandled rejection whose stack names a candidate module is a candidate error, while one
    that names only the hidden test file is the harness's own problem.
    """
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
    report_path = f"out/test.{group.group_id}.jsonl"
    if report_path in raw.list():
        for line in raw.read(report_path).decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                unreadable = True
    finished = [e for e in events if e.get("type") == "session_finish"]
    started = {str(e["nodeid"]) for e in events if e.get("type") == "case_start"}
    done = {str(e["nodeid"]) for e in events if e.get("type") == "case"}
    in_flight = sorted(started - done)
    collection_errors = [e for e in events if e.get("type") == "collection_error"]
    candidate_errors = tuple(
        str(e["nodeid"])
        for e in collection_errors
        if _candidate_text(str(e.get("reason_tail", "")))
        or _names_candidate(str(e.get("reason_tail", "")), candidate_paths)
    )
    harness_errors = tuple(
        str(e["nodeid"]) for e in collection_errors if str(e["nodeid"]) not in candidate_errors
    )
    known = {case.case_id for case in inventory.cases}
    required = {case.case_id: case.required for case in inventory.cases}
    cases: dict[str, TestCaseRecord] = {}
    unexpected: list[str] = []
    for event in events:
        if event.get("type") != "case":
            continue
        node = str(event["nodeid"])
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
            input_seed=event.get("input_seed"),
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
            in_flight_case=in_flight[0] if in_flight else None,
        )
    exit_code = record.exit_code
    if exit_code in _KILL_CODES and (in_flight or not finished):
        return records, control(
            "candidate_killed",
            f"terminated by signal (exit {exit_code})",
            in_flight_case=in_flight[0] if in_flight else None,
        )
    if unreadable:
        return records, control("harness_failure", "test report contains unreadable lines")
    if not finished:
        return records, control("harness_failure", "report has no session_finish record")
    runner_status = int(finished[-1]["exitstatus"])
    if runner_status in _RUNNER_FAILURES and not collection_errors:
        return records, control(
            "harness_failure", f"vitest exit status {runner_status}"
        )
    if status == "tool_error" and not collection_errors:
        return records, control("harness_failure", f"unexpected exit code {exit_code}")
    return records, control(
        "finished",
        "",
        unexpected_cases=tuple(sorted(set(unexpected))),
        candidate_collection_errors=candidate_errors,
        harness_collection_errors=harness_errors,
    )


def _names_candidate(text: str, candidate_paths: set[str] | None) -> bool:
    """Whether a failure's text names one of the candidate's own files."""
    if not candidate_paths:
        return False
    normalised = text.replace("\\", "/")
    return any(f"work/{path}" in normalised or f"/{path}" in normalised for path in candidate_paths)