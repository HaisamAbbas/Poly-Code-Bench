"""Turn a ``mvn -o test`` run (supervisor record + runner capture) into test evidence.

Candidate errors (a failed assertion, a thrown exception, a test JVM killed by a signal, a test that
hangs until the deadline, compile errors in candidate code or against the candidate's API) are kept
apart from harness errors (no runner record, no surefire report, an unreadable capture, compile
errors in the hidden test files that the candidate cannot have caused). The distinction uses the
structured surefire XML and the supervisor's execution record, not the exit code alone (Technical
Spec 12.2).

JUnit 5's own XML is the primary evidence here rather than a text scrape: surefire records each
test case, its outcome and its failure message in ``target/surefire-reports/TEST-*.xml``, which is
exactly the shape the shared contract wants. The captured Maven stream is used only for the cases
the XML cannot describe - a compile error, a fork that died, a suite that never started.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, cast
from xml.etree import ElementTree

from polycodebench_core.canonical import canonical_digest
from polycodebench_plugins_api import ArtifactReader, TestGroupPlan, plan_status
from polycodebench_plugins_api.testreport import (
    CaseOutcome,
    GroupControl,
    InventoryGroup,
    TestCaseRecord,
)

_RANK = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}
#: JUnit 5 and the shared contract name an outcome differently (``passed``/``failed`` against
#: ``pass``/``fail``). The translation lives here, once, so no caller ever has to know both.
_OUTCOME = {"passed": "pass", "failed": "fail", "error": "error", "skipped": "skipped"}
#: Compile errors that mean "the candidate does not provide what the tests import or call".
_API_MISMATCH_TOKENS = (
    "cannot find symbol",
    "package demo does not exist",
    "has private access",
    "is not public in",
    "incompatible types",
    "no suitable method found",
    "method does not override",
)
#: Compile errors the candidate cannot be blamed for.
_HARNESS_TOKENS = (
    "cannot access",
    "bad class file",
    "class file contains",
    "invalid target release",
)
_MAVEN_ERROR = re.compile(
    r"^\[ERROR\]\s+(?P<msg>.*?)/(?P<path>[^/\s:]+\.java):\[(?P<line>\d+),\d+\]"
)
_KILLED = re.compile(r"The forked VM terminated without properly saying goodbye|Process Exit Code")
_NOISE = ("Failed to execute goal", "Reactor Summary", "BUILD FAILURE")


def candidate_error_sites(text: str, candidate_paths: set[str]) -> tuple[list[str], list[str]]:
    """Split Maven compile errors into (candidate-attributable, harness-attributable) sites."""
    candidate: list[str] = []
    harness: list[str] = []
    for line in text.splitlines():
        match = _MAVEN_ERROR.match(line.strip())
        if match is None:
            continue
        message = match.group("msg")
        if any(token in message for token in _NOISE):
            continue
        path = match.group("path").replace("\\", "/")
        site = f"{path}:{match.group('line')}"
        blamed_on_tests = path in candidate_paths or any(
            token in message for token in _API_MISMATCH_TOKENS
        )
        harness_owned = any(token in message for token in _HARNESS_TOKENS) and not blamed_on_tests
        if blamed_on_tests or harness_owned:
            candidate.append(site)
        else:
            harness.append(site)
    return candidate, harness


def surefire_reports(raw: ArtifactReader, paths: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every ``<testcase>`` in the explicitly captured Surefire XML reports a run produced."""
    cases: list[dict[str, Any]] = []
    present = set(raw.list())
    for path in paths:
        if path not in present:
            continue
        try:
            document = ElementTree.fromstring(raw.read(path).decode("utf-8", errors="replace"))
        except ElementTree.ParseError:
            # A truncated XML file is a harness problem, not a test outcome; it is reported by the
            # caller's "no usable report" branch rather than as a failing case.
            continue
        classname = ""
        for element in document.iter():
            if element.tag == "testsuite":
                classname = element.get("name", "")
                break
        for case in document.iter("testcase"):
            name = case.get("name", "")
            outcome: CaseOutcome = "pass"
            reason = ""
            for child in case:
                if child.tag == "failure":
                    outcome = "fail"
                    reason = (child.get("message") or child.text or "")[:200]
                elif child.tag == "error":
                    outcome = "error"
                    reason = (child.get("message") or child.text or "")[:200]
                elif child.tag == "skipped":
                    outcome = "skipped"
                    reason = (child.get("message") or "")[:200]
            cases.append(
                {
                    "classname": case.get("classname") or classname,
                    "name": name,
                    "outcome": outcome,
                    "reason": reason,
                    "time": case.get("time", "0"),
                }
            )
    return cases


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

    def control(
        kind: Literal["finished", "candidate_timeout", "candidate_killed", "harness_failure"],
        detail: str = "",
        **extra: Any,
    ) -> GroupControl:
        return GroupControl(
            group_id=group.group_id,
            repetition=repetition,
            status=kind,
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
    report_paths = tuple(output.path for output in plan.outputs if output.format == "junit_xml")
    events = surefire_reports(raw, report_paths)
    known = {case.case_id for case in inventory.cases}
    required = {case.case_id: case.required for case in inventory.cases}
    cases: dict[str, TestCaseRecord] = {}
    unexpected: list[str] = []
    for event in events:
        node = f"{event['classname']}#{event['name']}"
        if node not in known:
            unexpected.append(node)
        outcome = cast("CaseOutcome", event["outcome"])
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
            outcome=outcome,
            reason=str(event["reason"])[:200],
            duration_ms=int(float(event["time"]) * 1000),
            max_rss_kb=None,
            stdout_digest=None,
            stderr_digest=None,
            execution_identity=identity,
        )
    records = [cases[key] for key in sorted(cases)]

    if timed_out:
        # Partial evidence is kept: the records that did complete are real, and the control says the
        # group did not finish rather than silently reporting a short run as a pass.
        return records, control("candidate_timeout", "run exceeded its time limit")
    if not events:
        if _KILLED.search(text):
            return records, control("candidate_killed", "the forked test JVM terminated abnormally")
        candidate_sites, harness_sites = candidate_error_sites(text, candidate_paths or set())
        if candidate_sites or harness_sites:
            # Nothing ran because the project did not compile. Errors in candidate code, or against
            # its API, are the candidate's; errors confined to the hidden tests are not.
            return records, control(
                "finished",
                "",
                candidate_collection_errors=tuple(candidate_sites),
                harness_collection_errors=tuple(harness_sites) if not candidate_sites else (),
            )
        return records, control(
            "harness_failure", "no surefire report and no compile error in the captured output"
        )
    if status == "tool_error":
        return records, control("harness_failure", f"unexpected exit code {run.get('exit_code')}")
    return records, control("finished", "", unexpected_cases=tuple(sorted(set(unexpected))))
