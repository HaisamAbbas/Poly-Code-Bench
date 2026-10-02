"""Normalized C++ observations: the shape every tool is reduced to.

A tool never speaks for itself. ``clang-tidy`` text, ``cppcheck`` XML, the context scanner's JSON
and a sanitizer report all become the same ``Observation`` records here, so the profile can merge
them, the scorer can key them and a reviewer can walk from a finding back to the bytes that
produced it.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Literal

from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import (
    Confidence,
    MeasurementStatus,
    Observation,
    ScoreDimension,
    SourceLocation,
)
from polycodebench_plugins_api.results import raw_report_ids

if TYPE_CHECKING:  # pragma: no cover - typing only
    from polycodebench_plugins_api import AnalysisPlan

_NON_SLUG = re.compile(r"[^a-z0-9]+")

Severity = Literal["critical", "high", "medium", "low"]
# Families whose canonical key includes the column. Two reports that name the same *expression*
# collapse; two that merely name the same *line* do not, because one C++ line can carry several
# independent ownership or copy faults.
COLUMN_KEYED_FAMILIES: frozenset[str] = frozenset({"manual-ownership"})


def slug(value: str) -> str:
    cleaned = _NON_SLUG.sub("-", value.lower()).strip("-.")
    return (cleaned or "unknown")[:100]


def issue_key(family: str, path: str, line: int, column: int | None = None) -> str:
    """The canonical key: one defect, one key, however many tools report it."""
    material = f"{family}|{path}|{line}" + ("" if column is None else f"|{column}")
    return f"cc.{slug(family)}.{hashlib.sha256(material.encode()).hexdigest()[:12]}"


def relative_candidate_path(path: str) -> str:
    """A recorded path relative to the candidate root, however the tool spelled it."""
    text = path.replace("\\", "/")
    for prefix in ("/workspace/", "./"):
        while text.startswith(prefix):
            text = text[len(prefix) :]
    if text.startswith("work/"):
        text = text[len("work/") :]
    return text


def tool_digest(plan: AnalysisPlan) -> str:
    return str(canonical_digest(plan.tool.model_dump(mode="json")))


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
    """One finding. Only ``MEASURED`` findings count; every other status is evidence only."""
    location = SourceLocation(
        schema_version=1,
        kind="source_location",
        path=relative_candidate_path(path),
        start_line=max(line, 1),
        end_line=max(end_line if end_line is not None else line, line),
        start_column=column if column is not None and column >= 1 else None,
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
        raw_artifact_ids=raw_report_ids(plan),
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
    incomplete, crashed, unsupported or timed-out check can never read as clean (PCB-21-2).
    """
    resolved = status or (
        MeasurementStatus.MEASURED if findings is not None else MeasurementStatus.MISSING
    )
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=f"cpp.{tool}.scan",
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
        raw_artifact_ids=raw_report_ids(plan),
        explanation=explanation[:500],
    )
