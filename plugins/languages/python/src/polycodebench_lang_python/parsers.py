"""Parsers for ruff, mypy, Bandit, Semgrep and the context scanner.

Every parser starts from supervisor evidence (``plan_status``), never from the tool's own output
alone. The rules that keep an unreliable check from looking clean:

* a missing/invalid output, a timeout, a crash or an exit code outside the declared contract
  produces a single ``python.<tool>.scan`` observation with status ``missing``;
* a findings exit with no parsable findings, or a success exit that still reports findings, is an
  inconsistency and also ``missing`` (Bandit exits 1 both for findings and for an uncaught crash);
* empty output counts as clean only where the tool's own contract proves it (exit 0 and, for
  mypy, empty stdout and stderr) and the scanned scope is accounted for where the tool reports it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from polycodebench_core.models import Confidence, MeasurementStatus, Observation
from polycodebench_plugins_api import AnalysisPlan, ArtifactReader, plan_status

from polycodebench_lang_python.observations import (
    finding,
    relative_candidate_path,
    scan_observation,
    slug,
)
from polycodebench_lang_python.profile import PythonProfile

_BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
_BANDIT_CONFIDENCE = {"HIGH": Confidence.HIGH, "MEDIUM": Confidence.MEDIUM, "LOW": Confidence.LOW}
_CONTEXT_CONFIDENCE = {"high": Confidence.HIGH, "medium": Confidence.MEDIUM, "low": Confidence.LOW}
_CONTEXT_SEVERITY = {"high": "medium", "medium": "low", "low": "low"}


def _load_json(raw: ArtifactReader, path: str) -> Any:
    return json.loads(raw.read(path).decode("utf-8"))


def _scope(plan: AnalysisPlan) -> set[str]:
    return {relative_candidate_path(path) for path in plan.scope}


def _missing(plan: AnalysisPlan, tool: str, reason: str) -> list[Observation]:
    return [scan_observation(plan, tool, findings=None, explanation=f"incomplete: {reason}")]


def _guarded(
    tool: str,
    body: Callable[
        [AnalysisPlan, ArtifactReader, PythonProfile, str, int | None], list[Observation]
    ],
) -> Callable[[ArtifactReader, AnalysisPlan, PythonProfile], list[Observation]]:
    """Shared prologue: classify the execution, then parse defensively."""

    def parse(raw: ArtifactReader, plan: AnalysisPlan, profile: PythonProfile) -> list[Observation]:
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


# ------------------------------------------------------------------------------------ ruff


def _ruff(
    plan: AnalysisPlan, raw: ArtifactReader, profile: PythonProfile, status: str, code: int | None
) -> list[Observation]:
    records = _load_json(raw, "out/ruff.json")
    if not isinstance(records, list):
        raise ValueError("ruff output is not a list")
    if status == "completed" and records:
        return _missing(plan, "ruff", "exit 0 but findings were reported")
    if status == "completed_with_findings" and not records:
        return _missing(plan, "ruff", "findings exit without findings")
    findings: list[Observation] = []
    for item in records:
        name = item["code"] or "invalid-syntax"
        check = f"python.ruff.{slug(str(name))}"
        row = item["location"]["row"]
        col = item["location"]["column"]
        end = item["end_location"]
        path = relative_candidate_path(item["filename"])
        findings.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=row,
                end_line=end["row"],
                column=col,
                end_column=end["column"],
                severity=None,
                confidence=Confidence.HIGH,
                key=profile.key_for(check, path, row, col),
                owner=profile.owner(check),
                explanation=f"{name}: {item['message']}",
            )
        )
    return [
        scan_observation(plan, "ruff", findings=len(findings), explanation="ruff scan completed"),
        *findings,
    ]


# ------------------------------------------------------------------------------------ mypy


def _mypy(
    plan: AnalysisPlan, raw: ArtifactReader, profile: PythonProfile, status: str, code: int | None
) -> list[Observation]:
    text = raw.read("out/mypy.json").decode("utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    errors = [json.loads(line) for line in lines]
    if status == "completed":
        stderr_present = "out/mypy.err" in raw.list() and raw.read("out/mypy.err").strip()
        if lines or stderr_present:
            return _missing(plan, "mypy", "exit 0 with unexpected output")
        return [scan_observation(plan, "mypy", findings=0, explanation="mypy: no issues (exit 0)")]
    found = [e for e in errors if e.get("severity") == "error"]
    if not found:
        return _missing(plan, "mypy", "findings exit without error records")
    result: list[Observation] = []
    for item in found:
        error_code = item.get("code") or "error"
        check = f"python.mypy.{slug(str(error_code))}"
        path = relative_candidate_path(str(item["file"]))
        result.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=int(item["line"]),
                column=int(item["column"]) + 1 if int(item.get("column", 0)) >= 0 else None,
                severity=None,
                confidence=Confidence.HIGH,
                key=profile.key_for(check, path, int(item["line"]), None),
                owner=profile.owner(check),
                explanation=str(item["message"]),
            )
        )
    return [
        scan_observation(plan, "mypy", findings=len(result), explanation="mypy scan completed"),
        *result,
    ]


# ----------------------------------------------------------------------------------- bandit


def _bandit(
    plan: AnalysisPlan, raw: ArtifactReader, profile: PythonProfile, status: str, code: int | None
) -> list[Observation]:
    document = _load_json(raw, "out/bandit.json")
    results = document["results"]
    metrics = document["metrics"]
    if document["errors"]:
        return _missing(plan, "bandit", f"{len(document['errors'])} file(s) could not be analysed")
    scanned = {relative_candidate_path(name) for name in metrics if name != "_totals"}
    absent = sorted(_scope(plan) - scanned)
    if absent:
        return _missing(plan, "bandit", f"scope not covered: {', '.join(absent[:3])}")
    if status == "completed" and results:
        return _missing(plan, "bandit", "exit 0 but findings were reported")
    if status == "completed_with_findings" and not results:
        return _missing(plan, "bandit", "findings exit without findings")
    found: list[Observation] = []
    for item in results:
        test_id = str(item["test_id"])
        check = f"python.bandit.{slug(test_id)}"
        path = relative_candidate_path(str(item["filename"]))
        line = int(item["line_number"])
        span = item.get("line_range") or [line]
        found.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=max(span),
                column=int(item["col_offset"]) + 1 if item.get("col_offset", -1) >= 0 else None,
                severity=_BANDIT_SEVERITY[item["issue_severity"]],  # type: ignore[arg-type]
                confidence=_BANDIT_CONFIDENCE[item["issue_confidence"]],
                key=profile.key_for(check, path, line, None),
                owner=profile.owner(check),
                explanation=f"{test_id}: {item['issue_text']}",
            )
        )
    return [
        scan_observation(plan, "bandit", findings=len(found), explanation="bandit scan completed"),
        *found,
    ]


# ---------------------------------------------------------------------------------- semgrep


def _semgrep(
    plan: AnalysisPlan, raw: ArtifactReader, profile: PythonProfile, status: str, code: int | None
) -> list[Observation]:
    document = _load_json(raw, "out/semgrep.json")
    results = document["results"]
    errors = [e for e in document["errors"] if str(e.get("level", "error")).lower() == "error"]
    if errors:
        return _missing(plan, "semgrep", f"{len(errors)} analysis error(s) reported")
    paths = document.get("paths", {})
    scanned = {relative_candidate_path(name) for name in paths.get("scanned", [])}
    absent = sorted(_scope(plan) - scanned)
    if absent or paths.get("skipped"):
        return _missing(plan, "semgrep", "scope not fully scanned")
    if status == "completed" and results:
        return _missing(plan, "semgrep", "exit 0 but findings were reported")
    if status == "completed_with_findings" and not results:
        return _missing(plan, "semgrep", "findings exit without findings")
    found: list[Observation] = []
    for item in results:
        rule = str(item["check_id"]).rsplit("pcb.python.", 1)[-1]
        check = f"python.semgrep.{slug(rule)}"
        path = relative_candidate_path(str(item["path"]))
        line = int(item["start"]["line"])
        meta = item["extra"].get("metadata", {})
        severity = meta.get("pcb_severity", "medium")
        found.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=int(item["end"]["line"]),
                column=int(item["start"]["col"]),
                severity=severity
                if severity in {"critical", "high", "medium", "low"}
                else "medium",
                confidence=Confidence.MEDIUM,
                key=profile.key_for(check, path, line, None),
                owner=profile.owner(check),
                explanation=str(item["extra"].get("message", rule)),
            )
        )
    return [
        scan_observation(
            plan, "semgrep", findings=len(found), explanation="semgrep scan completed"
        ),
        *found,
    ]


# ------------------------------------------------------------------------- context scanner


def _context(
    plan: AnalysisPlan, raw: ArtifactReader, profile: PythonProfile, status: str, code: int | None
) -> list[Observation]:
    document = _load_json(raw, "out/context.json")
    if document["schema"] != "pcb-context-scan-v1" or not document["complete"]:
        return _missing(plan, "context", "scan incomplete or unknown schema")
    parsed = {relative_candidate_path(f["path"]) for f in document["files"] if f["parsed"]}
    absent = sorted(_scope(plan) - parsed)
    if absent:
        return _missing(plan, "context", f"scope not parsed: {', '.join(absent[:3])}")
    violations = sum(1 for f in document["findings"] if f["verdict"] == "violation")
    if status == "completed" and violations:
        return _missing(plan, "context", "exit 0 but violations were reported")
    if status == "completed_with_findings" and not violations:
        return _missing(plan, "context", "findings exit without violations")
    observations: list[Observation] = []
    for item in document["findings"]:
        check = f"python.context.{slug(item['rule'])}"
        path = relative_candidate_path(item["path"])
        line = int(item["line"])
        verdict = item["verdict"]
        confidence = _CONTEXT_CONFIDENCE[item["confidence"]]
        key = profile.key_for(check, path, line, int(item["column"]))
        observations.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=int(item["end_line"]),
                column=int(item["column"]),
                severity=_CONTEXT_SEVERITY[item["confidence"]],  # type: ignore[arg-type]
                confidence=confidence,
                key=key,
                owner=profile.owner(check),
                explanation=f"{item['message']} [{item['symbol']}]",
                status={
                    "violation": MeasurementStatus.MEASURED,
                    "benign_in_context": MeasurementStatus.NOT_APPLICABLE,
                    "hint": MeasurementStatus.NEEDS_REVIEW,
                }[verdict],
            )
        )
    return [
        scan_observation(
            plan, "context", findings=violations, explanation="context scan completed"
        ),
        *observations,
    ]


# ------------------------------------------------------------------------------ dependencies


def _dependency(
    plan: AnalysisPlan, raw: ArtifactReader, profile: PythonProfile, status: str, code: int | None
) -> list[Observation]:
    document = _load_json(raw, "out/dependency.json")
    if document["schema"] != "pcb-dependency-check-v1":
        return _missing(plan, "dependency", "unknown schema")
    found = document["findings"]
    if (status == "completed") == bool(found):
        return _missing(plan, "dependency", "exit status disagrees with reported advisories")
    observations = []
    for item in found:
        severity = item.get("severity", "medium")
        observations.append(
            finding(
                plan,
                check_id="python.dependency.advisory",
                path="dependencies.json",
                line=1,
                severity=severity
                if severity in {"critical", "high", "medium", "low"}
                else "medium",
                confidence=Confidence.HIGH,
                key=profile.key_for(
                    "python.dependency.advisory", f"{item['package']}=={item['version']}", 1, None
                ),
                owner=profile.owner("python.dependency.advisory"),
                explanation=f"{item['package']} {item['version']}: {item['advisory']}",
            )
        )
    return [
        scan_observation(
            plan, "dependency", findings=len(found), explanation="advisory check done"
        ),
        *observations,
    ]


PARSERS: dict[str, Callable[[ArtifactReader, AnalysisPlan, PythonProfile], list[Observation]]] = {
    "ruff": _guarded("ruff", _ruff),
    "mypy": _guarded("mypy", _mypy),
    "bandit": _guarded("bandit", _bandit),
    "semgrep": _guarded("semgrep", _semgrep),
    "context": _guarded("context", _context),
    "dependency": _guarded("dependency", _dependency),
}
