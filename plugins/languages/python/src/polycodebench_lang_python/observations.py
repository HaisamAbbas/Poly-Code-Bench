"""Observation construction shared by every Python parser."""

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
    return f"py.{slug(family)}.{hashlib.sha256(material.encode()).hexdigest()[:12]}"


def finding(
    plan: AnalysisPlan,
    *,
    check_id: str,
    path: str,
    line: int,
    end_line: int | None = None,
    column: int | None = None,
    end_column: int | None = None,
    severity: Severity | None,
    confidence: Confidence | None,
    key: str,
    owner: ScoreDimension | None,
    explanation: str,
    status: MeasurementStatus = MeasurementStatus.MEASURED,
) -> Observation:
    """One finding. ``MEASURED`` findings count; other statuses are kept as evidence only."""
    rel = relative_candidate_path(path)
    last = max(end_line or line, line)
    location = SourceLocation(
        schema_version=1,
        kind="source_location",
        path=rel,
        start_line=max(line, 1),
        end_line=max(last, 1),
        start_column=column if column and column >= 1 else None,
        end_column=(
            end_column
            if end_column and end_column >= 1 and (last > line or (column or 0) <= end_column)
            else None
        ),
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
        primary_owner=owner if measured else None,
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
    """Scan-level evidence. A completed scan is ``MEASURED`` with its finding count; anything
    else is ``MISSING`` so an incomplete, crashed or timed-out check can never look clean."""
    resolved = status or (
        MeasurementStatus.MEASURED if findings is not None else MeasurementStatus.MISSING
    )
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=f"python.{tool}.scan",
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
