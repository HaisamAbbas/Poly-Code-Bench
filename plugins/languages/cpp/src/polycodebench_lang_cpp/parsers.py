"""C++ analyzer parsers: real tool output -> normalized observations.

Each analyzer declares what it runs; this module only reads back what the recorded run produced.
Every parser obeys one rule: **an unanswered check is not a clean one**. A crashed tool, an
undeclared exit code, a timeout, a missing report and an unsupported runtime each produce exactly
one ``MISSING`` (or ``NOT_APPLICABLE``) scan observation and no findings, so an analyzer that did
not run can never quietly raise a candidate's score (PCB-21-2).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from typing import Literal

from polycodebench_core.models import (
    Confidence,
    Observation,
)
from polycodebench_core.models import (
    MeasurementStatus as Status,
)
from polycodebench_plugins_api import AnalysisPlan, ArtifactReader, ExecutionPlan, plan_status

from polycodebench_lang_cpp import observations as obs
from polycodebench_lang_cpp.observations import Severity, finding, scan_observation
from polycodebench_lang_cpp.profile import CppProfile
from polycodebench_lang_cpp.testparse import ClangDiagnostic, clang_diagnostics

Parser = Callable[[ArtifactReader, AnalysisPlan, CppProfile], list[Observation]]

_CPPCHECK_SEVERITY: dict[str, Severity] = {
    "error": "high",
    "warning": "medium",
    "style": "low",
    "performance": "low",
    "portability": "low",
    "information": "low",
}
_CONTEXT_SEVERITY: dict[str, Severity] = {"high": "high", "medium": "medium", "low": "low"}
_CONTEXT_CONFIDENCE = {
    "high": Confidence.CONFIRMED,
    "medium": Confidence.HIGH,
    "low": Confidence.MEDIUM,
}
_CONTEXT_STATUS = {
    "violation": Status.MEASURED,
    "benign_in_context": Status.NOT_APPLICABLE,
    "hint": Status.NEEDS_REVIEW,
}
_SCAN_SCHEMA = "pcb-cpp-scan-v1"
_SANITIZER_SCHEMA = "pcb-cpp-sanitizer-v1"
# A leak is the same ownership fault the static analyzers report, seen by the runtime instead, so
# it keeps one family with them and is never a second penalty for one defect.
_LEAK_CHECK = "cpp.asan.resource-leak"
_DEFECT_CHECKS = {"asan": "cpp.asan.candidate-defect", "ubsan": "cpp.ubsan.candidate-defect"}


def _missing(plan: AnalysisPlan, tool: str, reason: str) -> list[Observation]:
    return [scan_observation(plan, tool, findings=None, explanation=f"incomplete: {reason}")]


def _guard(tool: str, body: Parser) -> Parser:
    """Classify the run from supervisor evidence before any recorded output is trusted."""

    def parse(raw: ArtifactReader, plan: AnalysisPlan, profile: CppProfile) -> list[Observation]:
        status, record = plan_status(plan, raw)
        if status == "timed_out":
            return _missing(plan, tool, f"{tool} scan timed out")
        if status == "tool_error":
            detail = f"exit {record.exit_code}" if record is not None else "no execution record"
            return _missing(plan, tool, f"tool error ({detail})")
        if status == "output_missing":
            return _missing(plan, tool, "the analyzer wrote no report")
        try:
            return body(raw, plan, profile)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            IndexError,
            UnicodeDecodeError,
            ElementTree.ParseError,
        ) as error:
            return _missing(plan, tool, f"output could not be parsed ({type(error).__name__})")

    return parse


def _scope(plan: AnalysisPlan) -> set[str]:
    return {obs.relative_candidate_path(path) for path in plan.scope}


def _text(raw: ArtifactReader, path: str) -> str:
    return raw.read(path).decode("utf-8", errors="replace")


def _reported_anything(text: str) -> bool:
    """Whether an analyzer printed evidence that this parser recognises at all.

    A static analyzer that genuinely finds nothing in clean code prints its banner and nothing
    else, so an empty stream is not evidence of a clean scan: it is evidence that the tool never
    reported. Distinguishing the two is what stops an omission from scoring as a pass.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if clang_diagnostics(stripped):
        return True
    # A run that did report, but about a file outside the candidate's scope or with a diagnostic
    # shape this parser does not model, still proves the tool looked at the work.
    return bool(stripped)


def _clang_tidy(raw: ArtifactReader, plan: AnalysisPlan, profile: CppProfile) -> list[Observation]:
    """clang-tidy prints `path:line:col: severity: message [check]`; the exit code is not it."""
    scope = _scope(plan)
    found: list[Observation] = []
    # clang-tidy writes its diagnostics to stderr on the pinned build and to stdout on others, and
    # an invocation that merges them puts both in one stream. Reading a single stream would let a
    # real finding be dropped while the run still reported zero findings -- which reads as MEASURED
    # and would score a candidate *above* one whose source was genuinely clean.
    text = "".join(_text(raw, path) for path in ("out/clang_tidy.out", "out/clang_tidy.err"))
    for diagnostic in clang_diagnostics(text):
        if diagnostic.severity == "note" or diagnostic.check is None:
            continue
        path = obs.relative_candidate_path(diagnostic.path)
        if path not in scope:
            continue
        check_id = f"cpp.clang_tidy.{obs.slug(diagnostic.check)}"
        found.append(
            finding(
                plan,
                check_id=check_id,
                path=path,
                line=diagnostic.line,
                column=diagnostic.column,
                severity="medium" if diagnostic.severity == "warning" else "high",
                confidence=Confidence.HIGH,
                key=profile.key_for(check_id, path, diagnostic.line) or "",
                owner=profile.owner(check_id),
                explanation=diagnostic.message[:300],
            )
        )
    # A clean source produces no diagnostics at all, so "zero findings" is only evidence of a
    # clean scan if the run demonstrably read the candidate. A run that printed nothing this
    # parser can recognise -- an unfamiliar format, a truncated capture, a tool that never ran --
    # is not a clean result; it is missing evidence, and is reported as such (PCB-21-2).
    if not found and not _reported_anything(text):
        raise ValueError("clang_tidy exited successfully but printed no readable diagnostics")
    return [
        *found,
        scan_observation(
            plan,
            "clang_tidy",
            findings=len(found),
            explanation=f"{len(found)} clang-tidy diagnostics",
        ),
    ]


def _cppcheck_xml(raw: ArtifactReader) -> ElementTree.Element:
    """cppcheck writes its XML to stderr on some builds and stdout on others; take either."""
    for path in ("out/cppcheck.out", "out/cppcheck.err"):
        try:
            text = raw.read(path).decode("utf-8", "replace")
        except FileNotFoundError:
            continue
        if "<results" in text:
            start = text.index("<?xml") if "<?xml" in text else 0
            return ElementTree.fromstring(text[start:])
    raise ValueError("no cppcheck XML report in the recorded output")


def _cppcheck(raw: ArtifactReader, plan: AnalysisPlan, profile: CppProfile) -> list[Observation]:
    """cppcheck's XML: `<error id=... severity=...><location file= line=/></error>`."""
    scope = _scope(plan)
    found: list[Observation] = []
    for node in _cppcheck_xml(raw).iter("error"):
        location = node.find("location")
        if location is None:
            continue
        path = obs.relative_candidate_path(location.get("file") or "")
        if path not in scope:
            continue
        identifier = node.get("id") or "unknown"
        line = int(location.get("line") or 1)
        check_id = f"cpp.cppcheck.{obs.slug(identifier)}"
        found.append(
            finding(
                plan,
                check_id=check_id,
                path=path,
                line=line,
                column=int(location.get("column") or 0) or None,
                severity=_CPPCHECK_SEVERITY.get(node.get("severity") or "", "low"),
                # A static analyser claims; only a sanitizer run measures. cppcheck findings stay
                # medium-confidence even when they name a real defect.
                confidence=Confidence.MEDIUM,
                key=profile.key_for(check_id, path, line) or "",
                owner=profile.owner(check_id),
                explanation=(node.get("verbose") or node.get("msg") or identifier)[:300],
            )
        )
    return [
        *found,
        scan_observation(
            plan, "cppcheck", findings=len(found), explanation=f"{len(found)} cppcheck diagnostics"
        ),
    ]


def _context(raw: ArtifactReader, plan: AnalysisPlan, profile: CppProfile) -> list[Observation]:
    """The context scanner is the authority on whether a token is a defect."""
    document = json.loads(raw.read("out/context.json").decode("utf-8"))
    if document.get("schema") != _SCAN_SCHEMA:
        raise ValueError(f"context scanner wrote schema {document.get('schema')!r}")
    if not document.get("complete"):
        raise ValueError("the context scanner did not parse every file it was given")
    _status, execution = plan_status(plan, raw)
    if execution is None:
        raise ValueError("the supervisor recorded no context-scan execution")
    violations = sum(1 for item in document["findings"] if item.get("verdict") == "violation")
    if execution.exit_code != 0 and violations == 0:
        raise ValueError("the context scanner failed without recording a violation")
    scope = _scope(plan)
    found: list[Observation] = []
    for item in document["findings"]:
        path = obs.relative_candidate_path(str(item["path"]))
        if path not in scope:
            continue
        verdict = str(item["verdict"])
        check_id = f"cpp.context.{obs.slug(str(item['rule']))}"
        line = int(item["line"])
        found.append(
            finding(
                plan,
                check_id=check_id,
                path=path,
                line=line,
                end_line=int(item.get("end_line") or line),
                column=int(item.get("column") or 0) or None,
                severity=_CONTEXT_SEVERITY.get(str(item.get("severity", "medium")), "medium"),
                confidence=_CONTEXT_CONFIDENCE.get(
                    str(item.get("confidence", "medium")), Confidence.HIGH
                ),
                key=profile.key_for(check_id, path, line) or "",
                owner=profile.owner(check_id),
                explanation=str(item["message"])[:300],
                status=_CONTEXT_STATUS[verdict],
            )
        )
    return [
        *found,
        scan_observation(
            plan, "context", findings=violations, explanation=f"{violations} context violations"
        ),
    ]


def _sanitizer(analyzer: str, detector: str) -> Parser:
    def parse(raw: ArtifactReader, plan: AnalysisPlan, profile: CppProfile) -> list[Observation]:
        report = json.loads(raw.read(f"out/{analyzer}.report.json").decode("utf-8"))
        if report.get("schema") != _SANITIZER_SCHEMA:
            raise ValueError(f"sanitizer report schema is {report.get('schema')!r}")
        verdict = str(report.get("verdict"))
        if verdict == "unsupported":
            # Unsupported is not clean: the runtime refused to judge, so the scan answers nothing.
            return [
                scan_observation(
                    plan,
                    analyzer,
                    findings=None,
                    status=Status.NOT_APPLICABLE,
                    explanation=f"unsupported runtime: {detector} could not judge this task",
                )
            ]
        if verdict == "clean":
            if int(report.get("exit_code", 0)) != 0 or report.get("timed_out"):
                raise ValueError(f"{detector} claims clean but the run did not succeed")
            return [
                scan_observation(
                    plan, analyzer, findings=0, explanation=f"{detector}: clean instrumented run"
                )
            ]
        if verdict != "candidate-defect":
            raise ValueError(
                f"{detector} produced no verdict (exit {report.get('exit_code')}, "
                f"timed_out={report.get('timed_out')})"
            )
        defect_check = _DEFECT_CHECKS[detector]
        found: list[Observation] = []
        for item in report["diagnostics"]:
            path = obs.relative_candidate_path(str(item.get("path") or ""))
            line = int(item.get("line") or 1)
            if not path:
                continue
            kind = str(item.get("kind") or "")
            leak = "leak" in kind.lower()
            check_id = _LEAK_CHECK if leak else defect_check
            family = (
                "manual-ownership"
                if leak
                else ("data-race" if detector == "tsan" else "undefined-behaviour")
            )
            found.append(
                finding(
                    plan,
                    check_id=check_id,
                    path=path,
                    line=line,
                    column=int(item.get("column") or 0) or None,
                    severity=("medium" if leak else "high"),
                    confidence=Confidence.CONFIRMED,
                    key=obs.issue_key(family, path, line),
                    owner=profile.owner(check_id),
                    explanation=str(item.get("message") or kind or detector)[:300],
                )
            )
        if not found:
            raise ValueError(f"{detector} reported a defect without naming a candidate file")
        return [
            *found,
            scan_observation(
                plan,
                analyzer,
                findings=len(found),
                explanation=f"{detector}: {len(found)} instrumented findings",
            ),
        ]

    return parse


PARSERS: dict[str, Parser] = {
    "clang_tidy": _guard("clang_tidy", _clang_tidy),
    "cppcheck": _guard("cppcheck", _cppcheck),
    "context": _guard("context", _context),
    "asan": _guard("asan", _sanitizer("asan", "asan")),
    "tsan": _guard("tsan", _sanitizer("tsan", "tsan")),
}


def build_status(
    raw: ArtifactReader, plan: ExecutionPlan
) -> tuple[Literal["pass", "fail", "incomplete"], list[ClangDiagnostic]]:
    """Build-plan outcome: ``pass``, ``fail``, or ``incomplete`` when the evidence disagrees."""
    status, _record = plan_status(plan, raw)
    if status in {"timed_out", "tool_error", "output_missing"}:
        return "incomplete", []
    try:
        text = raw.read("out/build.err").decode("utf-8", "replace")
    except FileNotFoundError:
        return "incomplete", []
    diagnostics = clang_diagnostics(text)
    errors = [item for item in diagnostics if item.severity == "error"]
    if status == "completed" and not errors:
        return "pass", diagnostics
    if status == "completed_with_findings" and errors:
        return "fail", diagnostics
    return "incomplete", diagnostics


__all__ = ["PARSERS", "Parser", "build_status"]
