"""C++ test evidence: the pinned harness protocol, compile diagnostics and group control.

A C++ test group is a program, not a test-runner invocation: the plan compiles the candidate and
one hidden translation unit into a binary and runs it. Three outcomes must stay apart here,
because conflating any two of them changes a verdict:

* the candidate does not compile (``candidate_collection_errors`` ⇒ candidate failure),
* the hidden harness does not compile (``harness_collection_errors`` ⇒ incomplete evidence),
* the run was killed while a case was in flight (``candidate_timeout`` naming that case).

None of them is "the tests passed", and none of them is "the tools broke".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api import ArtifactReader, TestGroupPlan, plan_status
from polycodebench_plugins_api.testreport import (
    CaseOutcome,
    GroupControl,
    InventoryGroup,
    TestCaseRecord,
)

ControlStatus = Literal["finished", "candidate_timeout", "candidate_killed", "harness_failure"]

_CASE = re.compile(r"^PCBCASE\s+(?P<id>\S+)\s+(?P<event>start|pass|fail|skip)\s*(?P<detail>.*)$")
_DIAGNOSTIC = re.compile(
    # A clang driver indents its `note (bounding context ...)` lines and prefixes them, so a
    # diagnostic is matched anywhere on the line rather than only at its start. A record that
    # cannot be matched is a finding that would be silently dropped; over-matching a prose line
    # is caught by the caller, which keeps only diagnostics naming an in-scope candidate file.
    r"^\s*(?P<path>[^\s:]+):(?P<line>\d+):(?P<column>\d+):\s+"
    r"(?P<severity>error|warning|note):\s+(?P<message>.*)$"
)
# clang-tidy appends the check name in brackets; a compile error may append a warning group.
_CHECK = re.compile(r"\[(?P<check>[A-Za-z0-9_,.-]+)\]\s*$")
_RANK: dict[str, int] = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}
_EVENT_TO_OUTCOME: dict[str, CaseOutcome] = {
    "pass": "pass",
    "fail": "fail",
    "skip": "skipped",
}


@dataclass(frozen=True)
class ClangDiagnostic:
    path: str
    line: int
    column: int
    severity: str
    message: str
    check: str | None = None


def clang_diagnostics(text: str) -> list[ClangDiagnostic]:
    """Every ``path:line:col: severity: message [check]`` line a clang driver printed."""
    found: list[ClangDiagnostic] = []
    for line in text.splitlines():
        match = _DIAGNOSTIC.match(line)
        if match is None:
            continue
        message = match.group("message").strip()
        check: str | None = None
        bracketed = _CHECK.search(message)
        if bracketed is not None:
            check = bracketed.group("check").split(",")[-1]
            message = message[: bracketed.start()].strip()
        found.append(
            ClangDiagnostic(
                path=match.group("path"),
                line=int(match.group("line")),
                column=int(match.group("column")),
                severity=match.group("severity"),
                message=message,
                check=check,
            )
        )
    return found


def relative(path: str) -> str:
    text = path.replace("\\", "/")
    for prefix in ("/workspace/", "./"):
        while text.startswith(prefix):
            text = text[len(prefix) :]
    return text[len("work/") :] if text.startswith("work/") else text


def error_sites(
    text: str, candidate_paths: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split compiler errors into the ones in candidate files and the ones in the harness."""
    candidate: set[str] = set()
    harness: set[str] = set()
    wanted = set(candidate_paths)
    for diagnostic in clang_diagnostics(text):
        if diagnostic.severity != "error":
            continue
        path = relative(diagnostic.path)
        site = f"{path}:{diagnostic.line}"
        (candidate if path in wanted else harness).add(site)
    return tuple(sorted(candidate)), tuple(sorted(harness))


def first_build_error(diagnostics: list[ClangDiagnostic]) -> str:
    """The message a reviewer should see first when a C++ build fails."""
    for diagnostic in diagnostics:
        if diagnostic.severity == "error":
            return (
                f"{diagnostic.path}:{diagnostic.line}:{diagnostic.column}: {diagnostic.message}"
            )[:160]
    return "the compiler reported a failure without a diagnostic"


def events(text: str) -> list[tuple[str, str, str]]:
    parsed: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = _CASE.match(line.strip())
        if match is not None:
            parsed.append((match.group("id"), match.group("event"), match.group("detail").strip()))
    return parsed


def _run_json(raw: ArtifactReader, name: str) -> dict[str, Any] | None:
    try:
        document = json.loads(raw.read(f"out/{name}.run.json").decode("utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _int(document: dict[str, Any], key: str) -> int:
    value = document.get(key, 0)
    return value if isinstance(value, int) else 0


def _control(
    group_id: str,
    repetition: int,
    status: ControlStatus,
    *,
    detail: str = "",
    in_flight_case: str | None = None,
    unexpected: tuple[str, ...] = (),
    candidate_errors: tuple[str, ...] = (),
    harness_errors: tuple[str, ...] = (),
) -> GroupControl:
    return GroupControl(
        group_id=group_id,
        repetition=repetition,
        status=status,
        in_flight_case=in_flight_case,
        detail=detail[:300],
        unexpected_cases=unexpected,
        candidate_collection_errors=candidate_errors,
        harness_collection_errors=harness_errors,
    )


def parse_group_report(
    group: TestGroupPlan,
    inventory: InventoryGroup,
    raw: ArtifactReader,
    *,
    repetition: int,
    candidate_paths: tuple[str, ...] = (),
) -> tuple[list[TestCaseRecord], GroupControl]:
    """Recorded group execution -> case records plus how the run itself ended."""
    plan = group.plan
    group_id = group.group_id
    status, supervisor_record = plan_status(plan, raw)
    identity = f"{plan.tool.name}-{plan.tool.version}@{plan.image_digest[:19]}"
    run = _run_json(raw, group_id)
    if run is None:
        return [], _control(
            group_id,
            repetition,
            "harness_failure",
            detail=f"no {group_id} run record was written (plan status {status})",
        )

    try:
        text = raw.read(f"out/{group_id}.out").decode("utf-8", errors="replace")
    except FileNotFoundError:
        text = ""

    completed: dict[str, tuple[CaseOutcome, str]] = {}
    started: list[str] = []
    for case_id, event, detail in events(text):
        if event == "start":
            started.append(case_id)
            continue
        outcome = _EVENT_TO_OUTCOME.get(event)
        if outcome is None:
            continue
        previous = completed.get(case_id)
        if previous is None or _RANK[outcome] > _RANK[previous[0]]:
            completed[case_id] = (outcome, detail)

    known = {case.case_id for case in inventory.cases}
    records = [
        TestCaseRecord(
            group_id=group_id,
            repetition=repetition,
            case_id=case_id,
            required=case.required
            if (case := next((c for c in inventory.cases if c.case_id == case_id), None))
            else True,
            expected_outcome_digest=str(canonical_digest({"case": case_id, "expected": "pass"})),
            outcome=outcome,
            reason=detail[:200],
            duration_ms=0,
            execution_identity=identity,
        )
        for case_id, (outcome, detail) in sorted(completed.items())
    ]

    timed_out = bool(run.get("timed_out")) or status == "timed_out"
    compile_failed = str(run.get("phase")) == "compile" and _int(run, "compile_exit_code") != 0
    unexpected = tuple(sorted(case_id for case_id in completed if case_id not in known))

    if timed_out:
        in_flight = next(
            (case_id for case_id in reversed(started) if case_id not in completed), None
        )
        return records, _control(
            group_id,
            repetition,
            "candidate_timeout",
            detail="run exceeded its time limit",
            in_flight_case=in_flight,
        )
    if compile_failed:
        candidate_errors, harness_errors = error_sites(text, candidate_paths)
        return records, _control(
            group_id,
            repetition,
            "finished",
            detail="the group did not compile",
            candidate_errors=candidate_errors,
            harness_errors=() if candidate_errors else harness_errors,
        )
    run_exit = _int(run, "exit_code")
    if (
        str(run.get("phase")) == "run"
        and 129 <= run_exit <= 192
        and supervisor_record is not None
        and supervisor_record.exit_code == run_exit
    ):
        in_flight = next(
            (case_id for case_id in reversed(started) if case_id not in completed), None
        )
        return records, _control(
            group_id,
            repetition,
            "candidate_killed",
            detail="the candidate process terminated abnormally",
            in_flight_case=in_flight,
        )
    if status in {"tool_error", "output_missing"}:
        return records, _control(
            group_id,
            repetition,
            "harness_failure",
            detail=f"unexpected exit code {run.get('exit_code')} (plan status {status})",
        )
    if not events(text) and _int(run, "exit_code") != 0:
        return records, _control(
            group_id,
            repetition,
            "harness_failure",
            detail=f"the group exited {run.get('exit_code')} without reporting any case",
        )
    return records, _control(group_id, repetition, "finished", unexpected=unexpected)


__all__ = [
    "ClangDiagnostic",
    "ControlStatus",
    "clang_diagnostics",
    "error_sites",
    "events",
    "first_build_error",
    "parse_group_report",
]
