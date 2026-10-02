"""Parsers for gofmt, ``go vet``, staticcheck, gosec, the context scanner, the race detector,
the module audit and the build log.

Every parser starts from supervisor evidence (``plan_status``), never from the tool's own output
alone. The rules that keep an unreliable check from looking clean:

* a missing/invalid output, a timeout, a crash or an exit code outside the declared contract
  produces a single ``go.<tool>.scan`` observation with status ``missing``;
* several Go tools report findings while exiting 0 (``gofmt -l`` lists files and still exits 0) and
  others exit 1 for a *build* failure rather than for findings (``go vet`` on a package that does not
  compile), so a scan is complete only when its own report is present and names what it saw;
* the race detector has three outcomes that must never be confused: **clean** (the instrumented
  suite ran and reported no race), **race** (a measured data race) and **unsupported** (the
  toolchain could not run the detector at all, which says nothing about the candidate).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from polycodebench_core.models import Confidence, MeasurementStatus, Observation
from polycodebench_plugins_api import AnalysisPlan, ArtifactReader, plan_status

from polycodebench_lang_go.observations import (
    finding,
    relative_candidate_path,
    scan_observation,
    slug,
)
from polycodebench_lang_go.profile import GoProfile

_CONTEXT_CONFIDENCE = {"high": Confidence.HIGH, "medium": Confidence.MEDIUM, "low": Confidence.LOW}
_CONTEXT_SEVERITY = {"high": "medium", "medium": "low", "low": "low"}
_STATICCHECK_SEVERITY = {"error": "high", "warning": "medium"}
_DEPENDENCY_SEVERITY = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
_RACE_MARKER = re.compile(r"WARNING: DATA RACE")
_RACE_FOOTER = re.compile(r"Found (\d+) data race")
_RACE_SUMMARY = re.compile(r"^(?:ok|FAIL|---)\s|\s+0\.0\d+s", re.MULTILINE)
_VET_TEXT = re.compile(
    r"^(?P<path>[^\s:][^:\n]*\.go):(?P<line>\d+):(?P<col>\d+):\s*(?P<message>[^\n]+)$"
)
_BUILD_ERROR_AT = (
    "error:",
    "undefined:",
    "cannot use",
    "syntax error",
    "no required module",
    "missing go.sum entry",
)
_STATUS = MeasurementStatus


# ------------------------------------------------------------------------------- build


def go_build_messages(raw: ArtifactReader, name: str) -> str:
    """Everything the compiler printed for a build, from both captured streams.

    The Go toolchain has no machine-readable build stream, so the verdict comes from the exit
    status and the compiler's own first error line - never from a line pattern alone, because a
    clean build prints nothing at all and an empty log is a pass, not a missing report.
    """
    return _optional_text(raw, f"out/{name}.out") + _optional_text(raw, f"out/{name}.err")


def build_error(text: str) -> str:
    """The compiler's first error, or why the build failed when it named no error."""
    for line in text.splitlines():
        stripped = line.strip()
        if any(marker in stripped for marker in _BUILD_ERROR_AT):
            return stripped[:200]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:200]
    return "compilation failed"


def _scope(plan: AnalysisPlan) -> set[str]:
    return {relative_candidate_path(path) for path in plan.scope}


def _missing(plan: AnalysisPlan, tool: str, reason: str) -> list[Observation]:
    return [scan_observation(plan, tool, findings=None, explanation=f"incomplete: {reason}")]


def _text(raw: ArtifactReader, path: str) -> str:
    return raw.read(path).decode("utf-8", errors="replace")


def _optional_text(raw: ArtifactReader, path: str) -> str:
    return _text(raw, path) if path in raw.list() else ""


def _guarded(
    tool: str,
    body: Callable[[AnalysisPlan, ArtifactReader, GoProfile, str, int | None], list[Observation]],
) -> Callable[[ArtifactReader, AnalysisPlan, GoProfile], list[Observation]]:
    """Shared prologue: classify the execution, then parse defensively."""

    def parse(raw: ArtifactReader, plan: AnalysisPlan, profile: GoProfile) -> list[Observation]:
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


def _in_scope(plan: AnalysisPlan, path: str) -> bool:
    return relative_candidate_path(path) in _scope(plan)


# --------------------------------------------------------------------------------- gofmt


def _gofmt(
    plan: AnalysisPlan, raw: ArtifactReader, profile: GoProfile, status: str, code: int | None
) -> list[Observation]:
    # `gofmt -l` lists every file it would rewrite and still exits 0, so the list is the verdict
    # and the exit status is not. A non-zero exit means gofmt could not do the job at all.
    if status != "completed":
        return _missing(plan, "gofmt", f"gofmt exited {code} instead of listing files")
    listed = [line.strip() for line in _text(raw, "out/gofmt.out").splitlines() if line.strip()]
    errors = _optional_text(raw, "out/gofmt.err").strip()
    if errors and not listed:
        return _missing(plan, "gofmt", errors[:200])
    findings: list[Observation] = []
    for path in listed:
        rel = relative_candidate_path(path)
        if rel not in _scope(plan):
            continue  # a file outside the candidate's scope is not its formatting
        check = "go.gofmt.unformatted"
        findings.append(
            finding(
                plan,
                check_id=check,
                path=rel,
                line=1,
                severity="low",
                confidence=Confidence.HIGH,
                key=profile.key_for(check, rel, 1),
                owner=profile.owner(check),
                explanation="gofmt would rewrite this file",
            )
        )
    return [
        scan_observation(plan, "gofmt", findings=len(findings), explanation="gofmt completed"),
        *findings,
    ]


# ----------------------------------------------------------------------------------- vet


def _vet_findings(raw: ArtifactReader) -> list[dict[str, Any]]:
    """``go vet -json`` output, with a text fallback for toolchains that ignore ``-json``."""
    text = _optional_text(raw, "out/vet.out").strip()
    if not text:
        return []
    try:
        document = json.loads(text)
    except ValueError:
        document = None
    if isinstance(document, dict):
        document = [document]
    if isinstance(document, list):
        parsed: list[dict[str, Any]] = []
        for item in document:
            if not isinstance(item, dict):
                raise ValueError("vet diagnostic is not an object")
            position = item.get("posn") or {}
            parsed.append(
                {
                    "path": str(position.get("filename", "")),
                    "line": int(position.get("line", 1) or 1),
                    "column": int(position.get("column", 0) or 0),
                    "message": str(item.get("message", "")),
                    "code": str(item.get("code") or ""),
                }
            )
        return parsed
    found = []
    for line in text.splitlines():
        match = _VET_TEXT.match(line.strip())
        if match:
            found.append(
                {
                    "path": match.group("path"),
                    "line": int(match.group("line")),
                    "column": int(match.group("col")),
                    "message": match.group("message").strip(),
                    "code": "",
                }
            )
    if not found:
        raise ValueError("vet output is neither JSON nor a diagnostic listing")
    return found


def _vet(
    plan: AnalysisPlan, raw: ArtifactReader, profile: GoProfile, status: str, code: int | None
) -> list[Observation]:
    items = _vet_findings(raw)
    # `go vet` exits 1 both for a finding and for a package it could not analyse; only a report
    # that names diagnostics is evidence of a completed scan.
    if status == "tool_error" and not items:
        return _missing(plan, "vet", f"vet exited {code} without a diagnostic report")
    findings: list[Observation] = []
    for item in items:
        rel = relative_candidate_path(item["path"])
        if rel not in _scope(plan):
            continue
        check = f"go.vet.{slug(item['code'] or 'diagnostic')}"
        line = max(int(item["line"]), 1)
        findings.append(
            finding(
                plan,
                check_id=check,
                path=rel,
                line=line,
                severity="medium",
                confidence=Confidence.HIGH,
                key=profile.key_for(check, rel, line),
                owner=profile.owner(check),
                explanation=str(item["message"])[:300],
            )
        )
    return [
        scan_observation(plan, "vet", findings=len(findings), explanation="go vet completed"),
        *findings,
    ]


# --------------------------------------------------------------------------- staticcheck


def _staticcheck(
    plan: AnalysisPlan, raw: ArtifactReader, profile: GoProfile, status: str, code: int | None
) -> list[Observation]:
    text = _optional_text(raw, "out/staticcheck.out").strip()
    if not text:
        return _missing(plan, "staticcheck", "staticcheck produced no report")
    document = json.loads(text)
    if not isinstance(document, list):
        raise ValueError("staticcheck report is not a list")
    findings: list[Observation] = []
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("staticcheck issue is not an object")
        location = item.get("location") or {}
        rel = relative_candidate_path(str(location.get("file", "")))
        if rel not in _scope(plan):
            continue
        identifier = str(item.get("code") or "").lower()
        check = f"go.staticcheck.{slug(identifier or 'issue')}"
        line = max(int(location.get("line", 1) or 1), 1)
        severity = _STATICCHECK_SEVERITY.get(str(item.get("severity", "warning")).lower(), "medium")
        findings.append(
            finding(
                plan,
                check_id=check,
                path=rel,
                line=line,
                severity=severity,  # type: ignore[arg-type]
                confidence=Confidence.HIGH,
                key=profile.key_for(check, rel, line),
                owner=profile.owner(check),
                explanation=f"{identifier}: {str(item.get('message', ''))[:240]}",
            )
        )
    return [
        scan_observation(
            plan, "staticcheck", findings=len(findings), explanation="staticcheck completed"
        ),
        *findings,
    ]


# ------------------------------------------------------------------------------- gosec


def _gosec(
    plan: AnalysisPlan, raw: ArtifactReader, profile: GoProfile, status: str, code: int | None
) -> list[Observation]:
    text = _optional_text(raw, "out/gosec.json").strip()
    if not text:
        return _missing(plan, "gosec", "gosec produced no report")
    document = json.loads(text)
    if not isinstance(document, dict) or not isinstance(document.get("Issues"), list):
        raise ValueError("gosec report has no Issues list")
    findings: list[Observation] = []
    for item in document["Issues"]:
        if not isinstance(item, dict):
            raise ValueError("gosec issue is not an object")
        rel = relative_candidate_path(str(item.get("file", "")))
        if rel not in _scope(plan):
            continue
        rule = slug(str(item.get("rule_id", "issue")))
        check = f"go.gosec.{rule}"
        line = max(int(item.get("line", 1) or 1), 1)
        severity = _DEPENDENCY_SEVERITY.get(str(item.get("severity", "")).lower(), "medium")
        findings.append(
            finding(
                plan,
                check_id=check,
                path=rel,
                line=line,
                severity=severity,  # type: ignore[arg-type]
                confidence=Confidence.HIGH,
                key=profile.key_for(check, rel, line),
                owner=profile.owner(check),
                explanation=f"{rule}: {str(item.get('details', ''))[:240]}",
            )
        )
    return [
        scan_observation(plan, "gosec", findings=len(findings), explanation="gosec completed"),
        *findings,
    ]


# ---------------------------------------------------------------------------- context


def _context(
    plan: AnalysisPlan, raw: ArtifactReader, profile: GoProfile, status: str, code: int | None
) -> list[Observation]:
    document = json.loads(_text(raw, "out/context.json"))
    if document["schema"] != "pcb-go-scan-v1" or not document["complete"]:
        return _missing(plan, "context", "scan incomplete or unknown schema")
    parsed = {relative_candidate_path(f["path"]) for f in document["files"] if f["parsed"]}
    absent = sorted({p for p in _scope(plan) if p.endswith(".go")} - parsed)
    if absent:
        return _missing(plan, "context", f"scope not parsed: {', '.join(absent[:3])}")
    violations = sum(1 for f in document["findings"] if f["verdict"] == "violation")
    if status == "completed" and violations:
        return _missing(plan, "context", "exit 0 but violations were reported")
    if status == "completed_with_findings" and not violations:
        return _missing(plan, "context", "findings exit without violations")
    observations: list[Observation] = []
    for item in document["findings"]:
        check = f"go.context.{slug(item['rule'])}"
        path = relative_candidate_path(item["path"])
        line = int(item["line"])
        confidence = _CONTEXT_CONFIDENCE[item["confidence"]]
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
        scan_observation(plan, "context", findings=violations, explanation="context scan completed"),
        *observations,
    ]


# ------------------------------------------------------------------------------- race


def classify_race(text: str, exit_code: int | None, timed_out: bool) -> str:
    """clean | race | unsupported | failed, from the detector's own output (not the exit code).

    An instrumented run that reports no race is only *clean* when the suite actually ran: an
    empty or truncated stream proves nothing, and it must not be read as a clean instrumented run.
    """
    if timed_out:
        return "failed"
    if _RACE_MARKER.search(text):
        return "race"
    if "race detector" in text and ("requires cgo" in text or "not supported" in text):
        return "unsupported"
    if _RACE_FOOTER.search(text):
        return "race"
    if text.strip() and _RACE_SUMMARY.search(text):
        return "clean"
    return "failed"


def _race(
    plan: AnalysisPlan, raw: ArtifactReader, profile: GoProfile, status: str, code: int | None
) -> list[Observation]:
    text = _optional_text(raw, "out/race.out") + _optional_text(raw, "out/race.err")
    verdict = classify_race(text, code, status == "timed_out")
    if verdict == "unsupported":
        return [
            scan_observation(
                plan,
                "race",
                findings=None,
                status=_STATUS.NOT_APPLICABLE,
                explanation="unsupported: this toolchain cannot run the race detector",
            )
        ]
    if verdict == "failed":
        return _missing(plan, "race", "the instrumented run did not complete")
    if verdict == "clean":
        return [scan_observation(plan, "race", findings=0, explanation="no data race reported")]
    found = _RACE_FOOTER.search(text)
    site = _race_site(plan, text)
    check = "go.race.data-race"
    return [
        scan_observation(
            plan,
            "race",
            findings=int(found.group(1)) if found else 1,
            explanation="the race detector reported a data race",
        ),
        finding(
            plan,
            check_id=check,
            path=site[0],
            line=site[1],
            severity="high",
            confidence=Confidence.HIGH,
            key=profile.key_for(check, site[0], site[1]),
            owner=profile.owner(check),
            explanation=_race_headline(text)[:300],
        ),
    ]


def _race_site(plan: AnalysisPlan, text: str) -> tuple[str, int]:
    """The first candidate site the detector named, as ``(relative path, line)``."""
    scope = sorted(_scope(plan))
    in_scope = set(scope)
    for match in re.finditer(r"(\S+\.go):(\d+)", text):
        rel = relative_candidate_path(match.group(1))
        if rel in in_scope:
            return rel, int(match.group(2))
    return (scope[0] if scope else "candidate.go"), 1


def _race_headline(text: str) -> str:
    index = text.find("WARNING: DATA RACE")
    if index < 0:
        return "data race reported"
    return text[index : index + 300].splitlines()[0].strip()


# ------------------------------------------------------------------------- dependency


def _dependency(
    plan: AnalysisPlan, raw: ArtifactReader, profile: GoProfile, status: str, code: int | None
) -> list[Observation]:
    document = json.loads(_text(raw, "out/dependency.json"))
    if document["schema"] != "pcb-go-mod-audit-v1" or not document.get("snapshot_entries"):
        return _missing(plan, "dependency", "advisory snapshot is empty or unknown")
    advisories = document["findings"]
    unpinned = document.get("unpinned") or []
    if status == "completed" and (advisories or unpinned):
        return _missing(plan, "dependency", "exit 0 but advisories were reported")
    if status == "completed_with_findings" and not (advisories or unpinned):
        return _missing(plan, "dependency", "findings exit without advisories")
    observations: list[Observation] = []
    for item in advisories:
        check = f"go.dependency.{slug(item['advisory'])}"
        observations.append(
            finding(
                plan,
                check_id=check,
                path="go.mod",
                line=1,
                severity=_DEPENDENCY_SEVERITY.get(str(item["severity"]).lower(), "medium"),  # type: ignore[arg-type]
                confidence=Confidence.HIGH,
                key=profile.key_for(check, f"{item['package']}@{item['version']}", 1),
                owner=profile.owner(check),
                explanation=f"{item['advisory']} affects {item['package']} {item['version']}",
            )
        )
    for item in unpinned:
        check = "go.dependency.unpinned-module"
        observations.append(
            finding(
                plan,
                check_id=check,
                path="go.sum",
                line=1,
                severity="high",
                confidence=Confidence.HIGH,
                key=profile.key_for(check, item, 1),
                owner=profile.owner(check),
                explanation=f"{item} has no recorded content hash, so the resolution is not pinned",
            )
        )
    return [
        scan_observation(
            plan,
            "dependency",
            findings=len(observations),
            explanation=f"audited {len(document['checked'])} required modules",
        ),
        *observations,
    ]


PARSERS: dict[str, Callable[[ArtifactReader, AnalysisPlan, GoProfile], list[Observation]]] = {
    "gofmt": _guarded("gofmt", _gofmt),
    "vet": _guarded("vet", _vet),
    "staticcheck": _guarded("staticcheck", _staticcheck),
    "gosec": _guarded("gosec", _gosec),
    "context": _guarded("context", _context),
    "race": _guarded("race", _race),
    "dependency": _guarded("dependency", _dependency),
}