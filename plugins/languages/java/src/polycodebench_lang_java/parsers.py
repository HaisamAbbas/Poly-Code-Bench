"""Parsers for SpotBugs, PMD, Checkstyle, the dependency audit and the context scanner.

Every parser starts from supervisor evidence (``plan_status``), never from the tool's own output
alone. The rules that keep an unreliable check from looking clean:

* a missing/invalid output, a timeout, a crash or an exit code outside the declared contract
  produces a single ``java.<tool>.scan`` observation with status ``missing``;
* **an empty report is not a clean report.** SpotBugs omits ``target/spotbugs.xml`` entirely when
  it finds nothing, and PMD and Checkstyle write a file with zero violations; the parsers treat an
  absent report as a scan that did not complete, because a scan that produced no artifact at all
  cannot be distinguished from a scan that was never run;
* the analysers' own exit codes are never read as verdicts. SpotBugs exits non-zero on findings, so
  its exit status carries no information about the candidate; PMD and Checkstyle report goals always
  exit 0. In all three cases the evidence is the XML.
* findings from a file outside the plan's scope are dropped: a SpotBugs finding in a dependency's
  bytecode is not the candidate's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, cast
from xml.etree import ElementTree

from polycodebench_core.models import Confidence, MeasurementStatus, Observation
from polycodebench_plugins_api import AnalysisPlan, ArtifactReader, plan_status

from polycodebench_lang_java.observations import (
    Severity,
    finding,
    relative_candidate_path,
    scan_observation,
    slug,
)
from polycodebench_lang_java.profile import JavaProfile

_CONTEXT_CONFIDENCE = {"high": Confidence.HIGH, "medium": Confidence.MEDIUM, "low": Confidence.LOW}
_CONTEXT_SEVERITY = {"high": "medium", "medium": "low", "low": "low"}
_SEVERITY_ORDER = ("critical", "high", "medium", "low")
#: SpotBugs states its own confidence ranking; these are the two values it emits that matter here.
_SPOTBUGS_CONFIDENCE = {"high": Confidence.HIGH, "medium": Confidence.MEDIUM, "low": Confidence.LOW}
_DEPENDENCY_SEVERITY = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
_BUILD_ERROR = re.compile(r"^\[ERROR\]\s+.*?/([^/\s]+\.java):\[(\d+),(\d+)\]")
_STATUS = MeasurementStatus


def _scope(plan: AnalysisPlan) -> set[str]:
    return {relative_candidate_path(path) for path in plan.scope}


def _missing(plan: AnalysisPlan, tool: str, reason: str) -> list[Observation]:
    return [scan_observation(plan, tool, findings=None, explanation=f"incomplete: {reason}")]


def _text(raw: ArtifactReader, path: str) -> str:
    return raw.read(path).decode("utf-8", errors="replace")


def _guarded(
    tool: str,
    body: Callable[[AnalysisPlan, ArtifactReader, JavaProfile, str, int | None], list[Observation]],
) -> Callable[[ArtifactReader, AnalysisPlan, JavaProfile], list[Observation]]:
    """Shared prologue: classify the execution, then parse defensively."""

    def parse(raw: ArtifactReader, plan: AnalysisPlan, profile: JavaProfile) -> list[Observation]:
        status, record = plan_status(plan, raw)
        if status == "timed_out":
            return _missing(plan, tool, "timed out")
        if status == "tool_error":
            detail = record.stderr_tail[-200:].strip() if record else "no execution record"
            code = None if record is None else record.exit_code
            return _missing(plan, tool, f"tool error (exit {code}): {detail}")
        if status == "output_missing":
            return _missing(plan, tool, "required output is missing")
        try:
            return body(plan, raw, profile, status, None if record is None else record.exit_code)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            UnicodeDecodeError,
            ElementTree.ParseError,
        ) as error:
            return _missing(plan, tool, f"output could not be parsed ({type(error).__name__})")

    return parse


# ------------------------------------------------------------------------------- spotbugs


def spotbugs_bug_elements(raw: ArtifactReader) -> list[dict[str, Any]]:
    """``<BugInstance>`` records from a SpotBugs XML report."""
    document = ElementTree.fromstring(_text(raw, "work/target/spotbugs.xml"))
    bugs: list[dict[str, Any]] = []
    for element in document.findall("BugInstance"):
        source = element.find("SourceLine")
        bugs.append(
            {
                "type": element.get("type", "UNKNOWN"),
                "category": element.get("category", ""),
                "priority": element.get("priority", "NORMAL"),
                "confidence": element.get("confidence", "NORMAL"),
                "sourcepath": "" if source is None else (source.get("sourcepath") or ""),
                "sourcepath_zip": "" if source is None else (source.get("sourcepath_zip") or ""),
                "start": 0 if source is None else int(source.get("start", 0) or 0),
                "end": 0 if source is None else int(source.get("end", 0) or 0),
            }
        )
    return bugs


def _spotbugs(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JavaProfile, status: str, code: int | None
) -> list[Observation]:
    if "work/target/spotbugs.xml" not in raw.list():
        # SpotBugs writes no report at all when it finds nothing; that is a scan that produced no
        # evidence, not a scan that proved the code clean.
        return _missing(plan, "spotbugs", "no spotbugs report was produced")
    bugs = spotbugs_bug_elements(raw)
    scope = _scope(plan)
    findings: list[Observation] = []
    for bug in bugs:
        path = relative_candidate_path(bug["sourcepath"] or bug["sourcepath_zip"])
        if path not in scope:
            continue  # a generated file or a dependency, not the candidate
        check = f"java.spotbugs.{slug(bug['type'].lower())}"
        line = max(int(bug["start"]), 1)
        findings.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=max(int(bug["end"]), 1),
                column=line,
                severity=_spotbugs_severity(bug["priority"]),
                confidence=_SPOTBUGS_CONFIDENCE.get(
                    str(bug["confidence"]).lower(), Confidence.MEDIUM
                ),
                key=profile.key_for(check, path, line, line),
                owner=profile.owner(check),
                explanation=f"{bug['type']} ({bug['category']})",
            )
        )
    return [
        scan_observation(
            plan, "spotbugs", findings=len(findings), explanation="spotbugs analysis completed"
        ),
        *findings,
    ]


def _spotbugs_severity(priority: str) -> Severity:
    """SpotBugs ``priority`` (1 highest) mapped onto the shared severity vocabulary.

    SpotBugs does not report severity directly; it reports a bug priority. This mapping is fixed and
    reviewed rather than invented per finding, because a priority read as a severity would let a
    low-priority style note outrank a real defect.
    """
    return cast(
        "Severity",
        {
            "1": "high",
            "2": "medium",
            "3": "low",
            "4": "low",
            "HIGH": "high",
            "NORMAL": "medium",
            "LOW": "low",
            "IGNORE": "low",
            "EXP": "low",
        }.get(str(priority).upper(), "medium"),
    )


# ------------------------------------------------------------------------------------ pmd


def pmd_violations(raw: ArtifactReader) -> list[dict[str, Any]]:
    """``<violation>`` records from a PMD XML report."""
    document = ElementTree.fromstring(_text(raw, "work/target/pmd.xml"))
    found: list[dict[str, Any]] = []
    for file_element in document.findall("file"):
        name = file_element.get("name", "")
        for violation in file_element.findall("violation"):
            found.append(
                {
                    "path": name,
                    "rule": violation.get("rule", ""),
                    "ruleset": violation.get("ruleset", ""),
                    "priority": violation.get("priority", "3"),
                    "beginline": int(violation.get("beginline", 1) or 1),
                    "begincolumn": int(violation.get("begincolumn", 1) or 1),
                    "endline": int(violation.get("endline", violation.get("beginline", 1)) or 1),
                }
            )
    return found


def _pmd(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JavaProfile, status: str, code: int | None
) -> list[Observation]:
    if "work/target/pmd.xml" not in raw.list():
        return _missing(plan, "pmd", "no pmd report was produced")
    scope = _scope(plan)
    findings: list[Observation] = []
    for item in pmd_violations(raw):
        path = relative_candidate_path(item["path"])
        if path not in scope:
            continue
        check = f"java.pmd.{slug(item['rule'].split('.')[-1])}"
        line = int(item["beginline"])
        findings.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=int(item["endline"]),
                column=int(item["begincolumn"]),
                severity=_pmd_severity(item["priority"]),
                confidence=Confidence.MEDIUM,
                key=profile.key_for(check, path, line),
                owner=profile.owner(check),
                explanation=f"{item['rule']} ({item['ruleset']})",
            )
        )
    return [
        scan_observation(plan, "pmd", findings=len(findings), explanation="pmd analysis completed"),
        *findings,
    ]


def _pmd_severity(priority: str) -> Severity:
    """PMD priority (1 highest) onto the shared severity vocabulary."""
    order = {"1": "high", "2": "medium", "3": "low", "4": "low", "5": "low"}
    return cast("Severity", order.get(str(priority), "low"))


# --------------------------------------------------------------------------- checkstyle


def checkstyle_errors(raw: ArtifactReader) -> list[dict[str, Any]]:
    """``<error>`` records from a Checkstyle XML report."""
    document = ElementTree.fromstring(_text(raw, "work/target/checkstyle-result.xml"))
    found: list[dict[str, Any]] = []
    for file_element in document.findall("file"):
        name = file_element.get("name", "")
        for error in file_element.findall("error"):
            found.append(
                {
                    "path": name,
                    "source": (error.get("source", "") or "").rsplit(".", 1)[-1],
                    "message": error.get("message", ""),
                    "severity": error.get("severity", "warning"),
                    "line": int(error.get("line", 1) or 1),
                    "column": int(error.get("column", 1) or 1),
                }
            )
    return found


def _checkstyle(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JavaProfile, status: str, code: int | None
) -> list[Observation]:
    if "work/target/checkstyle-result.xml" not in raw.list():
        return _missing(plan, "checkstyle", "no checkstyle report was produced")
    scope = _scope(plan)
    findings: list[Observation] = []
    for item in checkstyle_errors(raw):
        path = relative_candidate_path(item["path"])
        if path not in scope:
            continue
        check = f"java.checkstyle.{slug(item['source'].lower())}"
        line = int(item["line"])
        findings.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=line,
                column=int(item["column"]),
                # Checkstyle reports ``error``/``warning``; anything else is treated as a warning
                # rather than escalated, because the tool's own vocabulary stops at those two.
                severity="medium" if item["severity"] == "error" else "low",
                confidence=Confidence.MEDIUM,
                key=profile.key_for(check, path, line),
                owner=profile.owner(check),
                explanation=f"{item['source']}: {item['message']}",
            )
        )
    return [
        scan_observation(
            plan, "checkstyle", findings=len(findings), explanation="checkstyle analysis completed"
        ),
        *findings,
    ]


# ------------------------------------------------------------------------------- context


def _context(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JavaProfile, status: str, code: int | None
) -> list[Observation]:
    document = json.loads(_text(raw, "out/context.json"))
    if document["schema"] != "pcb-java-scan-v1" or not document["complete"]:
        return _missing(plan, "context", "scan incomplete or unknown schema")
    parsed = {relative_candidate_path(f["path"]) for f in document["files"] if f["parsed"]}
    absent = sorted({p for p in _scope(plan) if p.endswith(".java")} - parsed)
    if absent:
        return _missing(plan, "context", f"scope not parsed: {', '.join(absent[:3])}")
    violations = sum(1 for f in document["findings"] if f["verdict"] == "violation")
    if status == "completed" and violations:
        return _missing(plan, "context", "exit 0 but violations were reported")
    if status == "completed_with_findings" and not violations:
        return _missing(plan, "context", "findings exit without violations")
    observations: list[Observation] = []
    for item in document["findings"]:
        check = f"java.context.{slug(item['rule'])}"
        path = relative_candidate_path(item["path"])
        line = int(item["line"])
        observations.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=int(item["end_line"]),
                column=int(item["column"]),
                severity=_CONTEXT_SEVERITY[item["confidence"]],  # type: ignore[arg-type]
                confidence=_CONTEXT_CONFIDENCE[item["confidence"]],
                key=profile.key_for(check, path, line),
                owner=profile.owner(check),
                explanation=f"{item['message']} [{item['symbol']}]",
                status={
                    "violation": _STATUS.MEASURED,
                    "benign_in_context": _STATUS.NOT_APPLICABLE,
                    "hint": _STATUS.NEEDS_REVIEW,
                }[item["verdict"]],
            )
        )
    return [
        scan_observation(
            plan, "context", findings=violations, explanation="context scan completed"
        ),
        *observations,
    ]


# -------------------------------------------------------------------------- dependency


def dependency_findings(raw: ArtifactReader) -> tuple[list[str], list[dict[str, Any]], bool]:
    """``(resolved coordinates, advisories, audit_ran)`` from the dependency plan's evidence.

    The advisory snapshot is applied by the guest audit rather than from Maven's output, so the same
    pinned snapshot decides which coordinates are affected on every run. ``audit_ran`` is reported
    separately: a plan whose audit step never produced ``out/dependency.json`` has applied no
    advisories at all, and that must not be readable as "no advisories found".
    """
    listing = _text(raw, "work/target/deps.txt")
    resolved: list[str] = []
    for line in listing.splitlines():
        stripped = line.strip()
        if (
            ":" in stripped
            and ":" in stripped.split(":")[-1]
            or (stripped.startswith(" ") and stripped.count(":") >= 2)
        ):
            parts = stripped.split(":")
            if len(parts) >= 5:
                resolved.append(f"{parts[0]}:{parts[1]}:{parts[3]}")
    audit_path = "out/dependency.json"
    if audit_path not in raw.list():
        return resolved, [], False
    document = json.loads(_text(raw, audit_path))
    return resolved, list(document.get("findings", [])), True


def _dependency(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JavaProfile, status: str, code: int | None
) -> list[Observation]:
    resolved, advisories, audit_ran = dependency_findings(raw)
    if not resolved:
        return _missing(plan, "dependency", "no dependency resolution was produced")
    if not audit_ran:
        # Maven resolved the graph but the advisory audit produced nothing. Reporting zero
        # advisories here would make an analyzer that never ran indistinguishable from a clean
        # dependency set, which is the exact failure the shared contract forbids.
        return _missing(plan, "dependency", "the advisory audit produced no result")
    observations: list[Observation] = []
    for item in advisories:
        check = f"java.dependency.{slug(item['advisory'])}"
        observations.append(
            finding(
                plan,
                check_id=check,
                path="deps.lock.json",
                line=1,
                severity=_DEPENDENCY_SEVERITY.get(str(item["severity"]).lower(), "medium"),  # type: ignore[arg-type]
                confidence=Confidence.HIGH,
                key=profile.key_for(check, f"{item['coordinate']}", 1),
                owner=profile.owner(check),
                explanation=f"{item['advisory']} affects {item['coordinate']}",
            )
        )
    return [
        scan_observation(
            plan,
            "dependency",
            findings=len(observations),
            explanation=f"audited {len(resolved)} resolved artifacts",
        ),
        *observations,
    ]


PARSERS: dict[str, Callable[[ArtifactReader, AnalysisPlan, JavaProfile], list[Observation]]] = {
    "spotbugs": _guarded("spotbugs", _spotbugs),
    "pmd": _guarded("pmd", _pmd),
    "checkstyle": _guarded("checkstyle", _checkstyle),
    "context": _guarded("context", _context),
    "dependency": _guarded("dependency", _dependency),
}


# ----------------------------------------------------------------------------- build report


def maven_build_errors(text: str) -> list[tuple[str, int]]:
    """``(path, line)`` sites Maven reported a compilation error at."""
    sites: list[tuple[str, int]] = []
    for match in _BUILD_ERROR.finditer(text):
        sites.append((relative_candidate_path(match.group(1)), int(match.group(2))))
    return sites


def first_build_error(text: str) -> str:
    sites = maven_build_errors(text)
    if sites:
        path, line = sites[0]
        return f"compilation failed at {path}:{line}"
    for log_line in text.splitlines():
        if log_line.startswith("[ERROR]") and "BUILD FAILURE" not in log_line:
            return log_line[:160]
    return "compilation failed"
