"""Parsers for Clippy, the context scanner, Miri and the dependency audit, plus the build log.

Every parser starts from supervisor evidence (``plan_status``), never from the tool's own output
alone. The rules that keep an unreliable check from looking clean:

* a missing/invalid output, a timeout, a crash or an exit code outside the declared contract
  produces a single ``rust.<tool>.scan`` observation with status ``missing``;
* Clippy exits 0 with warnings, so a scan is complete only when Cargo's own ``build-finished``
  message says the compilation succeeded; its absence is never "no findings";
* Miri has three outcomes that must never be confused: **clean** (a measured scan with zero
  findings), **candidate UB** (a measured finding) and **unsupported** (the scan is
  ``not_applicable``: Miri could not judge, which says nothing about the candidate).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from polycodebench_core.models import Confidence, MeasurementStatus, Observation
from polycodebench_plugins_api import AnalysisPlan, ArtifactReader, plan_status

from polycodebench_lang_rust.guestmods import load_guest
from polycodebench_lang_rust.observations import (
    finding,
    relative_candidate_path,
    scan_observation,
    slug,
)
from polycodebench_lang_rust.profile import RustProfile

_CONTEXT_CONFIDENCE = {"high": Confidence.HIGH, "medium": Confidence.MEDIUM, "low": Confidence.LOW}
_CONTEXT_SEVERITY = {"high": "medium", "medium": "low", "low": "low"}
_DEPENDENCY_SEVERITY = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
_SITE = re.compile(r"-->\s+(?P<path>[^:\s]+):(?P<line>\d+):(?P<col>\d+)")
_SUMMARY = re.compile(r"^test result:", re.MULTILINE)
_STATUS = MeasurementStatus


def _scope(plan: AnalysisPlan) -> set[str]:
    return {relative_candidate_path(path) for path in plan.scope}


def _missing(plan: AnalysisPlan, tool: str, reason: str) -> list[Observation]:
    return [scan_observation(plan, tool, findings=None, explanation=f"incomplete: {reason}")]


def _text(raw: ArtifactReader, path: str) -> str:
    return raw.read(path).decode("utf-8", errors="replace")


def _guarded(
    tool: str,
    body: Callable[[AnalysisPlan, ArtifactReader, RustProfile, str, int | None], list[Observation]],
) -> Callable[[ArtifactReader, AnalysisPlan, RustProfile], list[Observation]]:
    """Shared prologue: classify the execution, then parse defensively."""

    def parse(raw: ArtifactReader, plan: AnalysisPlan, profile: RustProfile) -> list[Observation]:
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


# ----------------------------------------------------------------------------- cargo JSON


def cargo_messages(raw: ArtifactReader, name: str) -> list[dict[str, Any]]:
    """Cargo's ``--message-format=json`` stream: one JSON object per line, in order."""
    messages: list[dict[str, Any]] = []
    for line in _text(raw, f"out/{name}.out").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        item = json.loads(stripped)
        if not isinstance(item, dict):
            raise ValueError("cargo message is not an object")
        messages.append(item)
    return messages


def build_finished(messages: list[dict[str, Any]]) -> bool | None:
    """True/False from Cargo's own ``build-finished``; None when the stream never reached it."""
    for message in reversed(messages):
        if message.get("reason") == "build-finished":
            return bool(message.get("success"))
    return None


def _primary_span(message: dict[str, Any]) -> dict[str, Any] | None:
    for span in message.get("spans", []):
        if span.get("is_primary"):
            return span  # type: ignore[no-any-return]
    return None


def first_build_error(messages: list[dict[str, Any]]) -> str:
    for item in messages:
        diagnostic = item.get("message") if item.get("reason") == "compiler-message" else None
        if diagnostic and diagnostic.get("level") == "error":
            span = _primary_span(diagnostic)
            where = f" at {span['file_name']}:{span['line_start']}" if span else ""
            return f"{str(diagnostic.get('message'))[:160]}{where}"
    return "compilation failed"


# ----------------------------------------------------------------------------- clippy


def _clippy(
    plan: AnalysisPlan, raw: ArtifactReader, profile: RustProfile, status: str, code: int | None
) -> list[Observation]:
    messages = cargo_messages(raw, "clippy")
    finished = build_finished(messages)
    if finished is None:
        return _missing(plan, "clippy", "cargo never reported build-finished")
    if not finished:
        return _missing(plan, "clippy", "the crate did not compile")
    scope = _scope(plan)
    findings: list[Observation] = []
    for item in messages:
        if item.get("reason") != "compiler-message":
            continue
        diagnostic = item["message"]
        lint = (diagnostic.get("code") or {}).get("code") or ""
        if not str(lint).startswith("clippy::"):
            continue  # rustc's own lints (unused code, unknown lint names) are not Clippy evidence
        span = _primary_span(diagnostic)
        if span is None:
            continue
        path = relative_candidate_path(span["file_name"])
        if path not in scope:
            continue  # a dependency or generated file, not the candidate
        check = f"rust.clippy.{slug(str(lint).removeprefix('clippy::').replace('_', '-'))}"
        line = int(span["line_start"])
        column = int(span["column_start"])
        findings.append(
            finding(
                plan,
                check_id=check,
                path=path,
                line=line,
                end_line=int(span["line_end"]),
                column=column,
                severity="medium",
                confidence=Confidence.HIGH,
                key=profile.key_for(check, path, line, column),
                owner=profile.owner(check),
                explanation=f"{lint}: {diagnostic.get('message', '')}",
            )
        )
    return [
        scan_observation(
            plan, "clippy", findings=len(findings), explanation="clippy completed and compiled"
        ),
        *findings,
    ]


# ----------------------------------------------------------------------------- context


def _context(
    plan: AnalysisPlan, raw: ArtifactReader, profile: RustProfile, status: str, code: int | None
) -> list[Observation]:
    document = json.loads(_text(raw, "out/context.json"))
    if document["schema"] != "pcb-rust-scan-v1" or not document["complete"]:
        return _missing(plan, "context", "scan incomplete or unknown schema")
    parsed = {relative_candidate_path(f["path"]) for f in document["files"] if f["parsed"]}
    absent = sorted({p for p in _scope(plan) if p.endswith(".rs")} - parsed)
    if absent:
        return _missing(plan, "context", f"scope not parsed: {', '.join(absent[:3])}")
    violations = sum(1 for f in document["findings"] if f["verdict"] == "violation")
    if status == "completed" and violations:
        return _missing(plan, "context", "exit 0 but violations were reported")
    if status == "completed_with_findings" and not violations:
        return _missing(plan, "context", "findings exit without violations")
    observations: list[Observation] = []
    for item in document["findings"]:
        check = f"rust.context.{slug(item['rule'])}"
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
        scan_observation(
            plan, "context", findings=violations, explanation="context scan completed"
        ),
        *observations,
    ]


# ----------------------------------------------------------------------------- miri


def classify_miri(text: str, exit_code: int | None, timed_out: bool) -> str:
    """clean | candidate-ub | unsupported | failed, from the diagnostic text (not the exit code).

    A run whose tests *failed* under Miri but that reported neither UB nor an unsupported
    operation did complete: the interpreter judged every path it executed, so it is ``clean`` as
    far as undefined behaviour goes. Failing tests are the acceptance gate's business.
    """
    verdict: str = load_guest("pcb_miri_report").classify(text, exit_code or 0, timed_out)
    if verdict == "failed" and not timed_out and _SUMMARY.search(text):
        return "clean"
    if verdict == "clean" and not _SUMMARY.search(text):
        # Exit 0 with no libtest summary proves nothing ran: empty or garbled output is not a
        # clean interpretation of the tests.
        return "failed"
    return verdict


def _miri(
    plan: AnalysisPlan, raw: ArtifactReader, profile: RustProfile, status: str, code: int | None
) -> list[Observation]:
    run = json.loads(_text(raw, "out/miri.run.json"))
    text = _text(raw, "out/miri.out") if "out/miri.out" in raw.list() else ""
    verdict = classify_miri(text, run.get("exit_code"), bool(run.get("timed_out")))
    if verdict == "unsupported":
        return [
            scan_observation(
                plan,
                "miri",
                findings=None,
                status=_STATUS.NOT_APPLICABLE,
                explanation="unsupported operation: Miri could not judge this task",
            )
        ]
    if verdict == "failed":
        return _missing(plan, "miri", "Miri did not complete (build failure or no verdict)")
    if verdict == "clean":
        return [scan_observation(plan, "miri", findings=0, explanation="Miri found no UB")]
    scope = sorted(_scope(plan))
    site = next(
        (m for m in _SITE.finditer(text) if relative_candidate_path(m.group("path")) in set(scope)),
        None,
    )
    path = (
        relative_candidate_path(site.group("path"))
        if site
        else (scope[0] if scope else "src/lib.rs")
    )
    line = int(site.group("line")) if site else 1
    check = "rust.miri.candidate-ub"
    head = next((ln.strip() for ln in text.splitlines() if "Undefined Behavior" in ln), "UB")
    return [
        scan_observation(plan, "miri", findings=1, explanation="Miri reported UB"),
        finding(
            plan,
            check_id=check,
            path=path,
            line=line,
            severity="high",
            confidence=Confidence.HIGH if site else Confidence.MEDIUM,
            key=profile.key_for(check, path, line),
            owner=profile.owner(check),
            explanation=head[:300],
        ),
    ]


# ----------------------------------------------------------------------------- dependency


def _dependency(
    plan: AnalysisPlan, raw: ArtifactReader, profile: RustProfile, status: str, code: int | None
) -> list[Observation]:
    document = json.loads(_text(raw, "out/dependency.json"))
    if document["schema"] != "pcb-lock-audit-v1" or not document.get("snapshot_entries"):
        return _missing(plan, "dependency", "advisory snapshot is empty or unknown")
    advisories = document["findings"]
    if status == "completed" and advisories:
        return _missing(plan, "dependency", "exit 0 but advisories were reported")
    if status == "completed_with_findings" and not advisories:
        return _missing(plan, "dependency", "findings exit without advisories")
    observations: list[Observation] = []
    for item in advisories:
        check = f"rust.dependency.{slug(item['advisory'])}"
        observations.append(
            finding(
                plan,
                check_id=check,
                path="Cargo.lock",
                line=1,
                severity=_DEPENDENCY_SEVERITY.get(str(item["severity"]).lower(), "medium"),  # type: ignore[arg-type]
                confidence=Confidence.HIGH,
                key=profile.key_for(check, f"{item['package']}@{item['version']}", 1),
                owner=profile.owner(check),
                explanation=f"{item['advisory']} affects {item['package']} {item['version']}",
            )
        )
    return [
        scan_observation(
            plan,
            "dependency",
            findings=len(observations),
            explanation=f"audited {len(document['checked'])} packages",
        ),
        *observations,
    ]


PARSERS: dict[str, Callable[[ArtifactReader, AnalysisPlan, RustProfile], list[Observation]]] = {
    "clippy": _guarded("clippy", _clippy),
    "context": _guarded("context", _context),
    "miri": _guarded("miri", _miri),
    "dependency": _guarded("dependency", _dependency),
}
