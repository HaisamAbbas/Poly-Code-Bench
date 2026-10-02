"""Turn a ``cargo test`` run (supervisor record + runner capture) into test evidence.

Candidate errors (failed assertions, panics, a test killed by a signal, a case that hangs until the
deadline, compile errors in candidate code or against the candidate's API) are kept apart from
harness errors (no runner record, no libtest summary, an unreadable capture, compile errors in the
hidden test files that the candidate cannot have caused). The distinction uses the structured
capture and the supervisor's execution record, not the exit code alone (Technical Spec 12.2).
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

from polycodebench_lang_rust.guestmods import load_guest

_RANK = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}
# Compile errors that mean "the candidate does not provide what the tests import or call".
_API_MISMATCH_CODES = frozenset(
    {
        "E0412",
        "E0423",
        "E0425",
        "E0432",
        "E0433",
        "E0599",
        "E0603",
        "E0616",
        "E0061",
        "E0308",
        "E0560",
        "E0609",
        "E0614",
        "E0618",
    }
)
_ERROR_AT = re.compile(
    r"^error(?:\[(?P<code>E\d+)\])?: (?P<msg>.*)\n\s*--> "
    r"(?P<path>[^:\n]+):(?P<line>\d+):(?P<col>\d+)",
    re.MULTILINE,
)
_SIGNAL = re.compile(r"process didn't exit successfully: .*\(signal: (?P<signal>\d+)")
_PANIC = re.compile(
    r"thread '(?P<name>[^']*)' panicked at [^\r\n]*:\r?\n(?P<message>[^\r\n]*)"
)
_FAILURE_HEAD = re.compile(r"^---- (?P<name>\S+) stdout ----\r?$", re.MULTILINE)
_NOISE = ("could not compile", "aborting due to", "test failed", "unused", "warning")


def candidate_error_sites(text: str, candidate_paths: set[str]) -> tuple[list[str], list[str]]:
    """Split compile errors into (candidate-attributable, harness-attributable) sites."""
    candidate: list[str] = []
    harness: list[str] = []
    for match in _ERROR_AT.finditer(text):
        if any(noise in match.group("msg") for noise in _NOISE):
            continue
        path = match.group("path").replace("\\", "/").removeprefix("work/")
        site = f"{path}:{match.group('line')}"
        if path in candidate_paths or match.group("code") in _API_MISMATCH_CODES:
            candidate.append(site)
        else:
            harness.append(site)
    return candidate, harness


def _reasons(text: str) -> dict[str, str]:
    """Panic message per failed case, from libtest's ``failures:`` section."""
    reasons: dict[str, str] = {}
    heads = list(_FAILURE_HEAD.finditer(text))
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        panic = _PANIC.search(text[head.end() : end])
        if panic:
            reasons[head.group("name")] = panic.group("message").strip()[:200]
    return reasons


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
    timed_out = bool(run.get("timed_out")) or status == "timed_out"
    events = list(
        load_guest("pcb_rust_test_report").build_records(
            text, "", None if timed_out else run.get("exit_code"), timed_out
        )
    )
    finish = next(e for e in events if e["kind"] == "session_finish")
    known = {case.case_id for case in inventory.cases}
    required = {case.case_id: case.required for case in inventory.cases}
    reasons = _reasons(text)
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
        short = node.split("::", 1)[-1]
        cases[node] = TestCaseRecord(
            group_id=group.group_id,
            repetition=repetition,
            case_id=node,
            required=required.get(node, False),
            input_seed=None,
            expected_outcome_digest=canonical_digest({"case": node, "expected": "pass"}),
            outcome=outcome,  # type: ignore[arg-type]
            reason=reasons.get(short, reasons.get(node, ""))[:200],
            duration_ms=int(event.get("duration_ms", 0)),
            max_rss_kb=None,
            stdout_digest=None,
            stderr_digest=None,
            execution_identity=identity,
        )
    records = [cases[key] for key in sorted(cases)]
    in_flight = [e["name"] for e in events if e["kind"] == "case_start"]
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
    candidate_sites, harness_sites = candidate_error_sites(text, candidate_paths or set())
    if finish["declared_tests"] is None and "could not compile" in text:
        # Nothing ran. Compile errors in candidate code (or against its API) are the candidate's;
        # errors confined to the hidden test files are not something the candidate can cause.
        return records, control(
            "finished",
            "",
            candidate_collection_errors=tuple(candidate_sites),
            harness_collection_errors=tuple(harness_sites) if not candidate_sites else (),
        )
    if finish["summary"] is None:
        return records, control("harness_failure", "no libtest summary in the captured output")
    if status == "tool_error":
        return records, control("harness_failure", f"unexpected exit code {run.get('exit_code')}")
    return records, control("finished", "", unexpected_cases=tuple(sorted(set(unexpected))))
