"""Canonical issue identities and observation construction for JavaScript/TypeScript findings.

Two analyzers may describe one defect (``eslint@require-await`` and the context scanner's
``await-missing``, say). The key a finding is filed under comes from a *reviewed* equivalence family
in the profile, never from the raw rule name, so the normaliser can merge duplicates before anything
is counted. Keys are content-addressed by family, path and line, so the same defect is the same key
across runs, across tools and across the two identities.

The prefix is per identity - ``js.`` for a JavaScript candidate, ``ts.`` for a TypeScript one - so
a finding from one language can never be mistaken for a finding from the other.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
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
# No JS/TS family is keyed by column. The context scanner reports whole lines (column 1), so a
# column in the key would stop an eslint finding and a scanner finding for one site from merging.
# The price is that two defects of one family on one line count once - the conservative direction.
COLUMN_KEYED_FAMILIES: frozenset[str] = frozenset()


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


@dataclass(frozen=True, slots=True)
class IssueKeys:
    """Content-addressed issue keys under one identity's prefix.

    The key of a site is a hash of its equivalence family, path and line, so one canonical defect
    is one key however many tools report it, and the same key on the next run.
    """

    prefix: str = "js."

    def key(self, family: str, path: str, line: int, column: int | None = None) -> str:
        material = f"{family}|{path}|{line}" + ("" if column is None else f"|{column}")
        return f"{self.prefix}{slug(family)}.{hashlib.sha256(material.encode()).hexdigest()[:12]}"


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
    language_id: str = "javascript",
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
    language_id: str = "javascript",
) -> Observation:
    """Scan-level evidence.

    A completed scan is ``MEASURED`` with its finding count; anything else is ``MISSING``, so an
    incomplete, crashed, unsupported or timed-out check can never look clean.
    """
    resolved = status or (
        MeasurementStatus.MEASURED if findings is not None else MeasurementStatus.MISSING
    )
    return Observation(
        schema_version=1,
        kind="observation",
        check_id=f"{language_id}.{tool}.scan",
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


__all__ = [
    "COLUMN_KEYED_FAMILIES",
    "IssueKeys",
    "Severity",
    "finding",
    "relative_candidate_path",
    "scan_observation",
    "slug",
    "tool_digest",
]
