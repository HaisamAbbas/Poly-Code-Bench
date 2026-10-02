"""Canonical issue identities and observation construction for Java findings (PCB-23-2).

Three of the four Java analyzers report *bytecode* or *source* positions in their own shapes, and
all of them can report the same defect. The rules that keep a token or a duplicate from becoming a
penalty are here:

* one issue is one **canonical key**, so SpotBugs and PMD reporting the same unclosed resource
  counts once. Cross-tool equivalence comes only from reviewed ``equivalence_family`` mappings in
  the profile; a tool that invents a family of its own gets no free credit for merging.
* a scan that did not complete is **MISSING**, never zero findings. A tool that crashed, timed out
  or whose output could not be parsed produces one ``java.<tool>.scan`` observation with status
  ``missing``, and every profile item fed by that tool becomes ``missing`` rather than clean.
* **spotbugs** findings carry a confidence the tool states; **checkstyle** and **pmd** do not, so
  their confidence is derived from severity by the profile's rule mapping rather than invented here.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import (
    Confidence,
    MeasurementStatus,
    Observation,
    ScoreDimension,
    SourceLocation,
)
from polycodebench_plugins_api import AnalysisPlan

Severity = Literal["critical", "high", "medium", "low"]

_NON_SLUG = re.compile(r"[^a-z0-9._-]+")
# Families whose *position within the line* distinguishes two real defects on one line. SpotBugs
# reports a start column and its pattern families really are column-precise, so keying them by
# column stops two `NP`-style defects on one line collapsing into one. The scanner-style families
# below report whole lines (column 1) and are deliberately not column-keyed, so a scanner verdict
# and a lint at the same site merge instead of double-counting.
COLUMN_KEYED_FAMILIES: frozenset[str] = frozenset(
    {
        "resource-not-closed",
        "unchecked-return",
        "expose-representation",
        "missing-override",
        "self-assignment",
        "dead-store",
        "null-dereference",
    }
)


def slug(value: str) -> str:
    cleaned = _NON_SLUG.sub("-", value.lower()).strip("-.")
    return (cleaned or "unknown")[:100]


def relative_candidate_path(path: str) -> str:
    """Candidate-relative POSIX path from whatever form a tool printed."""
    text = path.replace("\\", "/")
    for prefix in ("/workspace/", "./"):
        while text.startswith(prefix):
            text = text[len(prefix) :]
    if text.startswith("work/"):
        text = text[len("work/") :]
    return text


def tool_digest(plan: AnalysisPlan) -> str:
    return str(canonical_digest(plan.tool.model_dump(mode="json")))


def issue_key(family: str, path: str, line: int, column: int | None = None) -> str:
    material = f"{family}|{path}|{line}" + ("" if column is None else f"|{column}")
    return f"jv.{slug(family)}.{hashlib.sha256(material.encode()).hexdigest()[:12]}"


def finding(
    plan: AnalysisPlan,
    *,
    check_id: str,
    path: str,
    line: int,
    severity: Severity | None,
    confidence: Confidence | None,
    key: str,
    owner: ScoreDimension | None,
    explanation: str,
    end_line: int | None = None,
    column: int | None = None,
    status: MeasurementStatus = MeasurementStatus.MEASURED,
) -> Observation:
    """One finding. Only ``MEASURED`` findings count; other statuses are evidence only."""
    rel = relative_candidate_path(path)
    last = max(end_line or line, line)
    location = SourceLocation(
        schema_version=1,
        kind="source_location",
        path=rel,
        start_line=max(line, 1),
        end_line=max(last, 1),
        start_column=column if column and column >= 1 else None,
        end_column=None,
        base_or_candidate_digest=plan.candidate_digest,
    )
    measured = status == MeasurementStatus.MEASURED
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=slug(check_id),
        tool_digest=tool_digest(plan),
        candidate_digest=plan.candidate_digest,
        status=status,
        value=True if measured else None,
        severity=severity if measured else None,
        confidence=confidence if measured else None,
        location=location,
        baseline_relation=None,
        issue_key=key,
        primary_owner=owner,
        raw_artifact_ids=[],
        explanation=explanation[:500],
    )


def scan_observation(
    plan: AnalysisPlan,
    tool: str,
    *,
    findings: int | None,
    explanation: str,
    status: MeasurementStatus | None = None,
) -> Observation:
    """Scan-level evidence.

    A completed scan is ``MEASURED`` with its finding count; anything else is ``MISSING``, so an
    incomplete, crashed, unsupported or timed-out check can never look clean (PCB-23-2 DoD).
    """
    resolved = status or (
        MeasurementStatus.MEASURED if findings is not None else MeasurementStatus.MISSING
    )
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=f"java.{tool}.scan",
        tool_digest=tool_digest(plan),
        candidate_digest=plan.candidate_digest,
        status=resolved,
        value=findings if resolved == MeasurementStatus.MEASURED else None,
        severity=None,
        confidence=None,
        location=None,
        baseline_relation=None,
        issue_key=None,
        primary_owner=None,
        raw_artifact_ids=[],
        explanation=explanation[:500],
    )
