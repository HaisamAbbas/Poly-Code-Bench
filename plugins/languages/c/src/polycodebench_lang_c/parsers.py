"""Turn a recorded C tool run into observations.

Every parser starts from supervisor evidence (``plan_status``) and never from the tool's own claim.
The rule the whole module exists to enforce: a scan that did not complete is ``MISSING``, never zero
findings. That includes the cases C makes easy to get wrong - a compiler crash, a clang-tidy run that
timed out, a sanitizer that could not load its runtime, a Valgrind summary that never appeared.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from polycodebench_core.models import Confidence, MeasurementStatus, Observation, ScoreDimension
from polycodebench_plugins_api import (
    AnalysisPlan,
    ArtifactReader,
    plan_status,
)

from polycodebench_lang_c import guestmods
from polycodebench_lang_c.diagnostics import (
    Diagnostic,
    checked_files,
    clang_tidy_rule,
    cppcheck_rule,
    cppcheck_severity,
    first_bare_error,
    parse_diagnostics,
    tidy_severity,
)
from polycodebench_lang_c.observations import (
    EXECUTION_COVERAGE_NOTE,
    finding,
    relative_candidate_path,
    scan_observation,
)
from polycodebench_lang_c.profile import CProfile

_BUILD_SCHEMA = "pcb-c-build-v1"


def _guarded(
    tool: str,
    body: Callable[[AnalysisPlan, ArtifactReader, CProfile], list[Observation]],
) -> Callable[[ArtifactReader, AnalysisPlan, CProfile], list[Observation]]:
    def parser(raw: ArtifactReader, plan: AnalysisPlan, profile: CProfile) -> list[Observation]:
        status, record = plan_status(plan, raw)
        if status == "timed_out":
            return _missing(plan, tool, "the scan exceeded its deadline")
        if status == "tool_error":
            tail = (record.stderr_tail[-200:] if record else "") or "no supervisor record"
            return _missing(plan, tool, f"the scan did not run ({status}): {tail}")
        if status == "output_missing":
            return _missing(plan, tool, "a required output is missing")
        try:
            return body(plan, raw, profile)
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as error:
            return _missing(plan, tool, f"output could not be parsed ({type(error).__name__})")

    return parser


def _missing(plan: AnalysisPlan, tool: str, reason: str) -> list[Observation]:
    return [scan_observation(plan, tool, findings=None, explanation=f"incomplete: {reason}")]


def _text(raw: ArtifactReader, path: str) -> str:
    return raw.read(path).decode("utf-8", errors="replace")


def capture_stem(plan: AnalysisPlan) -> str:
    """The ``out/<stem>`` prefix this plan's captures were written under.

    Read from the plan's own declared outputs rather than reconstructed from the analyzer id: a
    dynamic lane emits one plan per oracle group, so the stem carries the group and reconstructing it
    from the analyzer would send every plan to the same file.
    """
    for output in plan.outputs:
        if output.path.endswith(".build.json"):
            return output.path.removeprefix("out/").removesuffix(".build.json")
    for output in plan.outputs:
        if output.path.startswith("out/") and output.path.endswith(".out"):
            return output.path.removeprefix("out/").removesuffix(".out")
    raise ValueError(f"plan {plan.plan_id} declares no recognisable capture")


def _scope(plan: AnalysisPlan) -> set[str]:
    return {relative_candidate_path(path) for path in plan.scope}


def build_document(raw: ArtifactReader, name: str) -> dict[str, Any]:
    """The build driver's normalized document, refusing a capture it cannot trust."""
    document = json.loads(_text(raw, f"out/{name}.build.json"))
    if document.get("schema") != _BUILD_SCHEMA:
        raise ValueError(f"unexpected build document schema {document.get('schema')!r}")
    if not document.get("complete"):
        raise ValueError(document.get("fatal") or "the build did not complete")
    return document


def first_build_error(document: dict[str, Any]) -> str:
    """The most specific explanation a build document can give.

    A positioned diagnostic names the file and line. A link or archive failure usually has none - the
    linker says ``ld: cannot find ...`` - so the driver's own ``fatal`` field is the evidence, and it
    is quoted rather than replaced by a generic sentence.
    """
    for entry in document.get("diagnostics", []):
        if entry.get("raw_severity") in {"error", "fatal error"} and entry.get("path"):
            return f"{entry['path']}:{entry['line']}: {entry['message']}"[:200]
    fatal = document.get("fatal")
    if fatal:
        return str(fatal)[:200]
    return first_bare_error(json.dumps(document.get("diagnostics", [])))


def first_warning(document: dict[str, Any]) -> str:
    for entry in document.get("diagnostics", []):
        if entry.get("raw_severity") == "warning":
            return f"{entry.get('path')}:{entry.get('line')}: {entry.get('message')}"[:200]
    return "a warning the frozen policy promotes to an error"


def _static(
    raw: ArtifactReader,
    plan: AnalysisPlan,
    profile: CProfile,
    *,
    tool: str,
    kind: str,
    check_prefix: str,
    rule_of: Callable[[str], str | None],
    severity_of: Callable[[str | None], str],
) -> list[Observation]:
    name = capture_stem(plan)
    text = _text(raw, f"out/{name}.out") + _text(raw, f"out/{name}.err")
    diagnostics = parse_diagnostics(text, kind=kind)  # type: ignore[arg-type]
    scope = _scope(plan)
    observations: list[Observation] = []
    for diagnostic in diagnostics:
        path = relative_candidate_path(diagnostic.path)
        if scope and path not in scope:
            # A finding in the frozen header or the harness is the task's, not the candidate's.
            continue
        if diagnostic.is_note:
            continue
        check_id = f"{check_prefix}.{diagnostic.rule}" if diagnostic.rule else f"{check_prefix}.scan"
        rule = rule_of(check_id)
        observations.append(
            finding(
                plan,
                check_id=check_id,
                path=diagnostic.path,
                line=diagnostic.line,
                column=diagnostic.column,
                severity=severity_of(rule),  # type: ignore[arg-type]
                confidence=Confidence.HIGH,
                key=profile.key_for(check_id, path, diagnostic.line, diagnostic.column),
                owner=profile.owner(check_id),
                explanation=f"{diagnostic.message} [{rule or 'no rule'}]",
            )
        )
    return [
        scan_observation(
            plan,
            tool,
            findings=len(observations),
            explanation=f"{tool} completed and reported {len(observations)} diagnostic(s)",
        ),
        *observations,
    ]


def _clang_tidy(plan: AnalysisPlan, raw: ArtifactReader, profile: CProfile) -> list[Observation]:
    return _static(
        raw,
        plan,
        profile,
        tool="clang_tidy",
        kind="tidy",
        check_prefix="c.tidy",
        rule_of=clang_tidy_rule,
        severity_of=tidy_severity,
    )


def _cppcheck(plan: AnalysisPlan, raw: ArtifactReader, profile: CProfile) -> list[Observation]:
    name = capture_stem(plan)
    text = _text(raw, f"out/{name}.out") + _text(raw, f"out/{name}.err")
    # cppcheck prints nothing and exits 0 on input it cannot parse, so a scan that never says which
    # files it read is not evidence of anything. Reporting it as missing is the whole point: without
    # this, uncompilable code would collect a clean cppcheck scan and a quality score built on it.
    checked = {
        relative_candidate_path(path) for path in checked_files(text)
    }
    scope = _scope(plan)
    unread = sorted(scope - checked)
    if unread:
        return _missing(
            plan,
            "cppcheck",
            "cppcheck examined no candidate source (" + ", ".join(unread[:3]) + ")",
        )
    return _static(
        raw,
        plan,
        profile,
        tool="cppcheck",
        kind="cppcheck",
        check_prefix="c.cppcheck",
        rule_of=cppcheck_rule,
        severity_of=cppcheck_severity,
    )


def _sanitizer(analyzer: str, lane: str, owner: ScoreDimension | None):  # type: ignore[no-untyped-def]
    """Parse one dynamic lane's capture.

    Two names are in play and they are not interchangeable. ``analyzer`` is the tool that produced the
    plan (``asan``, ``ubsan``, ``valgrind``) and names the observation, because that is what a task
    requires and what the profile's feeders are keyed on. ``lane`` is the capability being judged
    (``address``, ``undefined``, ``valgrind``) and is what the report document is keyed by. Getting
    them the wrong way round produces an observation nothing downstream is looking for.
    """

    def parse(plan: AnalysisPlan, raw: ArtifactReader, profile: CProfile) -> list[Observation]:
        report = guestmods.load_guest("pcb_c_sanitize_report")
        name = capture_stem(plan)
        text = _text(raw, f"out/{name}.out")
        run = _run_record(raw, name)
        document = report.report(
            text, lane, run.get("exit_code"), bool(run.get("timed_out"))
        )
        verdict = document["verdict"]
        if verdict == "unsupported":
            # Unsupported is not clean: a task that required this lane cannot be scored from it,
            # and an optional one is simply not evidence.
            return [
                scan_observation(
                    plan,
                    analyzer,
                    findings=None,
                    status=MeasurementStatus.NOT_APPLICABLE,
                    explanation=f"{lane} cannot judge this task: {document['reason']}",
                )
            ]
        if verdict == "failed":
            return _missing(plan, analyzer, f"{lane} did not complete: {document['reason']}")
        if verdict == "clean":
            return [
                scan_observation(
                    plan,
                    analyzer,
                    findings=0,
                    explanation=f"{lane} reported no defect; {EXECUTION_COVERAGE_NOTE}",
                )
            ]
        scope = _scope(plan)
        observations: list[Observation] = []
        for entry in document["findings"]:
            path = relative_candidate_path(entry.get("file") or "")
            line = int(entry.get("line") or 1)
            column = entry.get("column")
            located = bool(path) and (not scope or path in scope)
            if not located:
                # No frame in the candidate tree: the defect is real but its site is in the harness,
                # libc or an unsymbolized frame. Dropping it would make a lane that *found* something
                # report zero findings - a false clean - so it is charged to the candidate's first
                # file at line 1 and says it is unlocated.
                path = sorted(scope)[0] if scope else "src/unknown.c"
                line, column = 1, None
            # Named by the analyzer (`c.asan.*`), not the lane (`address`): the profile's rules and
            # the task's required analyzers are keyed on the analyzer.
            check_id = f"c.{analyzer}.{entry['family']}"
            observations.append(
                finding(
                    plan,
                    check_id=check_id,
                    path=path,
                    line=line,
                    column=column,
                    severity=entry["severity"],  # type: ignore[arg-type]
                    confidence=Confidence.HIGH,
                    key=profile.key_for(check_id, path, line),
                    owner=owner,
                    explanation=f"{entry['tool']}: {entry['summary']}"
                    + ("" if located else " (unlocated: no candidate frame)"),
                )
            )
        return [
            scan_observation(
                plan,
                analyzer,
                findings=len(observations),
                explanation=(
                    f"{lane} reported {len(observations)} defect(s) on executed paths; "
                    f"{EXECUTION_COVERAGE_NOTE}"
                ),
            ),
            *observations,
        ]

    return parse


def _run_record(raw: ArtifactReader, name: str) -> dict[str, Any]:
    try:
        return json.loads(_text(raw, f"out/{name}.run.json"))
    except (FileNotFoundError, ValueError):
        return {}


PARSERS: dict[str, Callable[[ArtifactReader, AnalysisPlan, CProfile], list[Observation]]] = {
    "clang_tidy": _guarded("clang_tidy", _clang_tidy),
    "cppcheck": _guarded("cppcheck", _cppcheck),
    "asan": _guarded("asan", _sanitizer("asan", "address", ScoreDimension.ROBUSTNESS)),
    "ubsan": _guarded("ubsan", _sanitizer("ubsan", "undefined", ScoreDimension.ROBUSTNESS)),
    "valgrind": _guarded(
        "valgrind", _sanitizer("valgrind", "valgrind", ScoreDimension.ROBUSTNESS)
    ),
}

__all__ = ["PARSERS", "build_document", "first_build_error", "first_warning"]


def _unused(_diagnostic: Diagnostic) -> None:  # pragma: no cover - keeps the type import honest
    raise AssertionError