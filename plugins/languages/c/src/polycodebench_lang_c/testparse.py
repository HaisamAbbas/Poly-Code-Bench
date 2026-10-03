"""C test evidence: harness records plus the supervisor's execution record.

C has no per-case alarm and no built-in runner, so the split between a candidate fault and a
harness fault is made from the harness's own records plus the supervisor record:

* a case that recorded a failure is the candidate's;
* a case that started and never finished is what a timeout or a crash names, through ``case_start``;
* a stream with no ``session_finish``, or one that accounts for fewer cases than it declared, is a
  harness failure - never a short pass;
* a compile error in a candidate source is the candidate's; one confined to the hidden group or the
  harness is not something the candidate can have caused.
"""

from __future__ import annotations

import json
import re
from typing import Any

from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api import ArtifactReader, TestGroupPlan, plan_status
from polycodebench_plugins_api.testreport import GroupControl, InventoryGroup, TestCaseRecord

from polycodebench_lang_c.guestmods import load_guest

_RANK = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}
_ERROR_AT = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?:fatal error|error):\s*(?P<msg>.+)$",
    re.MULTILINE,
)
_NOISE = ("warning:", "note:", "-W")
_SEGFAULT = re.compile(
    r"AddressSanitizer:\s*(?:SEGV|heap-buffer-overflow|stack-buffer-overflow|"
    r"heap-use-after-free|global-buffer-overflow|stack-overflow)"
)
_SIGNAL_EXIT = "candidate_killed"


def candidate_error_sites(text: str, candidate_paths: set[str]) -> tuple[list[str], list[str]]:
    """Split compile errors into (candidate-attributable, harness-attributable) sites."""
    candidate: list[str] = []
    harness: list[str] = []
    for match in _ERROR_AT.finditer(text):
        if any(noise in match.group("msg") for noise in _NOISE):
            continue
        path = match.group("path").replace("\\", "/").removeprefix("work/")
        site = f"{path}:{match.group('line')}"
        if path in candidate_paths or "test" in path.split("/")[-1]:
            candidate.append(site)
        else:
            harness.append(site)
    return candidate, harness


def _run_record(raw: ArtifactReader, name: str) -> dict[str, Any] | None:
    try:
        return json.loads(raw.read(f"out/{name}.run.json").decode("utf-8", errors="replace"))
    except (FileNotFoundError, ValueError):
        return None


def _build_record(raw: ArtifactReader, name: str) -> dict[str, Any] | None:
    """The build driver's normalized document, when the plan declared one."""
    try:
        return json.loads(raw.read(f"out/{name}.build.json").decode("utf-8", errors="replace"))
    except (FileNotFoundError, ValueError):
        return None


def _program_exit(raw: ArtifactReader, name: str, run: dict[str, Any]) -> tuple[int | None, bool]:
    """The *test binary's* exit status and timeout flag.

    This matters more in C than anywhere else. A C test group is one process: the compile-and-link
    driver links it and then runs it, so the status the supervisor sees belongs to the driver, which
    exits 0 as soon as the link succeeds. A binary that segfaults, aborts on heap corruption, or is
    killed at the deadline is invisible in that status. The driver records what the binary itself
    returned, and that is the evidence a candidate-fault classification has to use.
    """
    build = _build_record(raw, name)
    if build and isinstance(build.get("run"), dict):
        return build["run"].get("exit_code"), bool(build["run"].get("timed_out"))
    return run.get("exit_code"), bool(run.get("timed_out"))


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
    run = _run_record(raw, name)
    if run is None:
        return [], control("harness_failure", "no runner record")
    text = (
        raw.read(f"out/{name}.out").decode("utf-8", errors="replace")
        if f"out/{name}.out" in set(raw.list())
        else ""
    )
    merge = (
        raw.read(f"out/{name}.err").decode("utf-8", errors="replace")
        if f"out/{name}.err" in set(raw.list())
        else ""
    )
    combined = text + ("\n" + merge if merge else "")
    program_exit, program_timed_out = _program_exit(raw, name, run)
    timed_out = bool(program_timed_out) or status == "timed_out"

    report = load_guest("pcb_c_test_report")
    document = report.summarize(text, program_exit, timed_out, name, merge or None)
    known = {case.case_id for case in inventory.cases}
    required = {case.case_id: case.required for case in inventory.cases}
    cases: dict[str, TestCaseRecord] = {}
    unexpected: list[str] = []
    for entry in document["cases"]:
        case_id = str(entry["case"])
        if case_id not in known:
            unexpected.append(case_id)
        outcome = str(entry["outcome"])
        existing = cases.get(case_id)
        if existing is not None and _RANK[existing.outcome] >= _RANK[outcome]:
            continue
        cases[case_id] = TestCaseRecord(
            group_id=group.group_id,
            repetition=repetition,
            case_id=case_id,
            required=required.get(case_id, False),
            input_seed=None,
            expected_outcome_digest=canonical_digest({"case": case_id, "expected": "pass"}),
            outcome=outcome,  # type: ignore[arg-type]
            reason=str(entry.get("reason", ""))[:200],
            duration_ms=0,
            max_rss_kb=None,
            stdout_digest=document["stdout_digest"],
            stderr_digest=document["stderr_digest"],
            execution_identity=identity,
        )
    ordered = [cases[key] for key in sorted(cases)]
    in_flight = document.get("in_flight_case")
    if timed_out:
        return ordered, control(
            "candidate_timeout",
            "the group exceeded its deadline",
            in_flight_case=in_flight,
        )
    if program_exit is not None and int(program_exit) > 128:
        signal = int(program_exit) - 128
        # A binary killed by a signal is the candidate's fault, not the harness's: the harness had
        # already linked and started it. Reading this from the *driver's* status would miss it
        # entirely, because the driver exits 0 as soon as the link succeeds.
        return ordered, control(
            _SIGNAL_EXIT,
            f"the test binary was terminated by signal {signal}",
            in_flight_case=in_flight,
        )
    candidate_sites, harness_sites = candidate_error_sites(combined, candidate_paths or set())
    if document["declared_cases"] is None:
        # The binary produced no harness stream at all. A crash has already been caught
        # above; what is left is either a build that never linked or a harness that
        # could not start.
        build = _build_record(raw, name)
        if build is not None and not build.get("linked", False):
            # The compile driver recorded why, and that is a candidate-attributable
            # error rather than an unexplained absence of evidence.
            return ordered, control(
                "finished",
                "",
                candidate_collection_errors=tuple(candidate_sites)
                or (f"{build.get('fatal') or 'the target did not link'}",),
                harness_collection_errors=tuple(harness_sites),
            )
        if candidate_sites:
            return ordered, control(
                "finished",
                "",
                candidate_collection_errors=tuple(candidate_sites),
                harness_collection_errors=() if candidate_sites else tuple(harness_sites),
            )
        return ordered, control(
            "harness_failure",
            f"the binary produced no harness stream and exited {program_exit}",
        )
    if not document["complete"]:
        return ordered, control(
            "harness_failure",
            document["summary"] or "the harness did not account for every declared case",
            in_flight_case=in_flight,
        )
    if status == "tool_error":
        return ordered, control("harness_failure", f"unexpected exit code {run.get('exit_code')}")
    return ordered, control("finished", "", unexpected_cases=tuple(sorted(set(unexpected))))
