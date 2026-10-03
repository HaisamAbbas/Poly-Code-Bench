"""Parse recorded tool output into canonical observations (Prompt 19, PCB-19-2).

Every parser is wrapped in the same prologue, so one rule holds for all four analyzers without
anyone having to remember it:

* a plan that timed out, errored or produced no output is a single ``MISSING`` scan observation, and
  a body that raises on unexpected input is ``MISSING`` too. A crashed, absent or misconfigured
  analyzer is never read as "no violations" (D-10-06);
* an exit status that disagrees with the tool's own output is ``MISSING``. If ESLint exits 0 while
  its JSON lists errors, or exits 1 while it lists none, the output is not trustworthy evidence and
  the parser says so rather than guessing which half is true;
* a scan whose declared scope it did not cover is ``MISSING``: a scanner that quietly skipped a file
  has not judged it.

Ownership comes from the profile mapping, never from the parser, so one canonical issue can only be
charged to one composite dimension (Technical Spec 14.5).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from polycodebench_core.models import Confidence, Observation
from polycodebench_plugins_api import AnalysisPlan, ArtifactReader, plan_status

from polycodebench_lang_javascript.observations import (
    Severity,
    finding,
    relative_candidate_path,
    scan_observation,
    slug,
)
from polycodebench_lang_javascript.profile import JsProfile

# ESLint severity is 2 for an error and 1 for a warning; anything above 2 is still an error.
_ESLINT_SEVERITY: dict[int, Severity] = {2: "high", 1: "medium"}
_CONTEXT_SEVERITY: dict[str, Severity] = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
_TSC_SEVERITY: dict[str, Severity] = {"error": "high", "warning": "medium"}
_ADVISORY_SEVERITY: dict[str, Severity] = {
    "critical": "critical",
    "high": "high",
    "moderate": "medium",
    "low": "low",
}
# A line the compiler printed: ``src/x.ts(12,5): error TS2345: Argument of type ...``
_TSC_LINE = re.compile(
    r"^(?P<file>[^(]+)\((?P<line>\d+),\d+\):\s+(?P<severity>error|warning)\s+(?P<code>TS\d+):\s+(?P<message>.*)$"
)


def _scope(plan: AnalysisPlan) -> set[str]:
    return {relative_candidate_path(path) for path in plan.scope}


def _missing(plan: AnalysisPlan, tool: str, reason: str) -> list[Observation]:
    return [
        scan_observation(
            plan,
            tool,
            findings=None,
            explanation=f"incomplete: {reason}",
            language_id=plan.language_id,
        )
    ]


def _text(raw: ArtifactReader, path: str) -> str:
    return raw.read(path).decode("utf-8", errors="replace")


def _severity(value: object, table: dict[str, Severity]) -> Severity:
    return table.get(str(value).lower(), "medium")


def _guarded(
    tool: str,
    body: Callable[[AnalysisPlan, ArtifactReader, JsProfile, str, int | None], list[Observation]],
) -> Callable[[ArtifactReader, AnalysisPlan, JsProfile], list[Observation]]:
    """Shared prologue: classify the execution, then parse defensively."""

    def parse(raw: ArtifactReader, plan: AnalysisPlan, profile: JsProfile) -> list[Observation]:
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
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as error:
            return _missing(plan, tool, f"output could not be parsed ({type(error).__name__})")

    return parse


# ------------------------------------------------------------------------------- eslint


def _eslint(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JsProfile, status: str, code: int | None
) -> list[Observation]:
    del code  # the declared exit semantics already classified the run; the JSON is the authority
    document = json.loads(_text(raw, "out/eslint.json"))
    if not isinstance(document, list):
        raise ValueError("eslint report is not a list of files")
    reported: set[str] = set()
    observations: list[Observation] = []
    for entry in document:
        path = relative_candidate_path(str(entry.get("filePath", "")))
        reported.add(path)
        for message in entry.get("messages") or []:
            # A parse error carries no rule id. It is still a defect, and naming the family by
            # severity keeps it in evidence instead of being silently dropped.
            rule = message.get("ruleId") or f"parse-error-{message.get('severity', 'error')}"
            check = f"{plan.language_id}.eslint.{slug(str(rule))}"
            line = int(message.get("line", 1))
            column = message.get("column")
            observations.append(
                finding(
                    plan,
                    check_id=check,
                    path=path,
                    line=line,
                    column=int(column) if column else None,
                    severity=_ESLINT_SEVERITY.get(int(message.get("severity", 1)), "low"),
                    confidence=Confidence.HIGH,
                    key=profile.key_for(check, path, line),
                    owner=profile.owner(check),
                    explanation=str(message.get("message", ""))[:300],
                    language_id=plan.language_id,
                )
            )
    uncovered = sorted(_scope(plan) - reported)
    if uncovered:
        return _missing(plan, "eslint", f"scope not linted: {', '.join(uncovered[:3])}")
    if status == "completed" and observations:
        return _missing(plan, "eslint", "exit 0 but violations were reported")
    if status == "completed_with_findings" and not observations:
        return _missing(plan, "eslint", "findings exit without violations")
    return [
        scan_observation(
            plan,
            "eslint",
            findings=len(observations),
            explanation=f"linted {len(reported)} files",
            language_id=plan.language_id,
        ),
        *observations,
    ]


# ------------------------------------------------------------------------------ context


def _context(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JsProfile, status: str, code: int | None
) -> list[Observation]:
    del code  # as above: the declared exit semantics classified the run
    document = json.loads(_text(raw, "out/context.json"))
    if document["schema"] != "pcb-js-scan-v1":
        return _missing(plan, "context", "scan incomplete or unknown schema")
    scanned = {relative_candidate_path(path) for path in document.get("scanned") or ()}
    uncovered = sorted(_scope(plan) - scanned)
    if uncovered:
        return _missing(plan, "context", f"scope not scanned: {', '.join(uncovered[:3])}")
    findings = document["findings"]
    if status == "completed" and findings:
        return _missing(plan, "context", "exit 0 but violations were reported")
    if status == "completed_with_findings" and not findings:
        return _missing(plan, "context", "findings exit without violations")
    observations: list[Observation] = []
    for item in findings:
        check = str(item["check_id"])
        path = relative_candidate_path(str(item["path"]))
        line = int(item["line"])
        column = item.get("column")
        observations.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                column=int(column) if column else None,
                severity=_severity(item.get("severity", "medium"), _CONTEXT_SEVERITY),
                confidence=Confidence.HIGH,
                key=profile.key_for(check, path, line),
                owner=profile.owner(check),
                explanation=str(item.get("explanation", ""))[:300],
                language_id=plan.language_id,
            )
        )
    return [
        scan_observation(
            plan,
            "context",
            findings=len(observations),
            explanation="context scan completed",
            language_id=plan.language_id,
        ),
        *observations,
    ]


# --------------------------------------------------------------------------- typescript


def _typescript(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JsProfile, status: str, code: int | None
) -> list[Observation]:
    del status  # tsc's own exit code is the authority here, not the shared classification
    # tsc exits 2 for a type error and 1 for a build failure; only a type error is evidence, and a
    # crash or a missing compiler is `missing` rather than a clean result.
    if code not in (0, 2):
        return _missing(plan, "typescript", f"tsc exited {code}")
    observations: list[Observation] = []
    for raw_line in _text(raw, "out/analysis.typescript.out").splitlines():
        match = _TSC_LINE.match(raw_line.strip())
        if match is None:
            continue
        check = f"{plan.language_id}.typescript.{match.group('code').lower()}"
        path = relative_candidate_path(match.group("file"))
        line = int(match.group("line"))
        observations.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                severity=_TSC_SEVERITY[match.group("severity")],
                confidence=Confidence.CONFIRMED,
                key=profile.key_for(check, path, line),
                owner=profile.owner(check),
                explanation=f"{match.group('code')}: {match.group('message')}"[:300],
                language_id=plan.language_id,
            )
        )
    return [
        scan_observation(
            plan,
            "typescript",
            findings=len(observations),
            explanation="the pinned strict compiler reported type errors"
            if observations
            else "the pinned strict compiler reported no type errors",
            language_id=plan.language_id,
        ),
        *observations,
    ]


# --------------------------------------------------------------------------- dependency


def _dependency(
    plan: AnalysisPlan, raw: ArtifactReader, profile: JsProfile, status: str, code: int | None
) -> list[Observation]:
    del code  # as above
    document = json.loads(_text(raw, "out/dependency.json"))
    if document["schema"] != "pcb-npm-audit-v1" or not document.get("snapshot_entries"):
        return _missing(plan, "dependency", "advisory snapshot is empty or unknown")
    advisories = document["findings"]
    if status == "completed" and advisories:
        return _missing(plan, "dependency", "exit 0 but advisories were reported")
    if status == "completed_with_findings" and not advisories:
        return _missing(plan, "dependency", "findings exit without advisories")
    observations: list[Observation] = []
    for item in advisories:
        check = f"{plan.language_id}.dependency.{slug(str(item['advisory']))}"
        observed = f"{item['package']}@{item['version']}"
        observations.append(
            finding(
                plan,
                check_id=check,
                path="package-lock.json",
                line=1,
                severity=_severity(item["severity"], _ADVISORY_SEVERITY),
                confidence=Confidence.HIGH,
                key=profile.key_for(check, observed, 1),
                owner=profile.owner(check),
                explanation=f"{item['advisory']} affects {observed}",
                language_id=plan.language_id,
            )
        )
    return [
        scan_observation(
            plan,
            "dependency",
            findings=len(observations),
            explanation=f"audited {len(document['checked'])} packages",
            language_id=plan.language_id,
        ),
        *observations,
    ]


PARSERS: dict[str, Callable[[ArtifactReader, AnalysisPlan, JsProfile], list[Observation]]] = {
    "eslint": _guarded("eslint", _eslint),
    "context": _guarded("context", _context),
    "typescript": _guarded("typescript", _typescript),
    "dependency": _guarded("dependency", _dependency),
}
