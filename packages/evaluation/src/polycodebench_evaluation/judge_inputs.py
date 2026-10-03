"""Turn one frozen ``EvaluationEvidence`` manifest into a judge packet input.

Technical Spec 15.1: a packet shows anonymized task constraints, approved evidence spans and the
rubric items, and nothing else. This module is the only place that reads grader evidence for
judging, and it deliberately drops everything a judge must not see: the gate result, correctness
case outcomes, robustness and diagnostic scores, the candidate digest that identifies the
submission, and any model identity the evaluation stage might have carried.

Candidate comments are extracted from the *candidate's own source text*, because that is where a
judge-facing instruction attempt actually lives. The extraction is mechanical and every extracted
comment is recorded, so a reviewer can see what the packet contained.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from polycodebench_core.judge_contracts import (
    InputComment,
    InputSpan,
    JudgePacketInput,
)
from polycodebench_core.models import ScoreDimension  # noqa: F401

from polycodebench_evaluation.evidence import EvaluationEvidence, IssueEvidence

MAX_SPAN_LINES = 120
MAX_SPANS = 24
MAX_COMMENTS = 24
MAX_TEXT_CHARS = 8_000
LINE_COMMENT = re.compile(r"(?://|#)(.*)$")


@dataclass(frozen=True)
class JudgeTaskContext:
    """What the evaluation stage knows about the task, minus anything identifying."""

    language: str
    task_statement: str
    constraints: tuple[str, ...]
    item_ids: tuple[str, ...]


def judge_packet_input(
    evidence: EvaluationEvidence,
    files: Mapping[str, bytes],
    context: JudgeTaskContext,
    *,
    withheld_values: tuple[str, ...] = (),
) -> JudgePacketInput:
    """Build the packet input for one evaluated candidate.

    ``files`` are the candidate's own bytes. Only the paths that carry judged code are read, each
    read is bounded, and the result is hashed into anchor identities so the same evidence always
    produces the same packet.
    """
    return judge_packet_input_from(
        language=context.language,
        task_statement=context.task_statement,
        constraints=context.constraints,
        item_ids=context.item_ids,
        files=files,
        issues=evidence.issues,
        withheld_values=withheld_values,
    )


def judge_packet_input_from(
    *,
    language: str,
    task_statement: str,
    constraints: tuple[str, ...],
    item_ids: tuple[str, ...],
    files: Mapping[str, bytes],
    issues: Sequence[IssueEvidence],
    withheld_values: tuple[str, ...] = (),
) -> JudgePacketInput:
    """The same bounded, anonymized packet assembly for any grading path (Prompt 25 repo tasks).

    The bounds and the anonymity rules are identical to :func:`judge_packet_input`: candidate
    bytes and introduced/worsened analyzer findings only, bounded spans and comments, no gate
    result, no candidate identity, no model identity.
    """
    spans: list[InputSpan] = []
    comments: list[InputComment] = []
    for path in sorted(files):
        text = _decode(files[path])
        if text is None:
            continue
        body = "\n".join(text.splitlines()[:MAX_SPAN_LINES])[:MAX_TEXT_CHARS]
        spans.append(InputSpan(origin="candidate_code", path=path, text=body))
        comments.extend(_comments(path, text))
        if len(spans) >= MAX_SPANS:
            break
    spans.extend(_issue_spans(issues))
    if not spans:
        raise ValueError("no judge-visible evidence span could be built")
    return JudgePacketInput(
        language=_language(language),
        task_statement=task_statement,
        constraints=tuple(constraints),
        item_ids=tuple(item_ids),
        spans=tuple(spans[:MAX_SPANS]),
        comments=tuple(comments[:MAX_COMMENTS]),
        withheld_values=tuple(value for value in withheld_values if len(value.strip()) >= 3),
    )


def evidence_digest(evidence: EvaluationEvidence) -> str:
    """The evidence manifest's own identity, binding a packet to the grading that produced it."""
    if evidence.report_digest is None:
        raise ValueError("an unreported evidence manifest cannot identify judge input")
    return evidence.report_digest


def _language(value: str) -> Literal["python", "rust"]:
    if value == "python":
        return "python"
    if value == "rust":
        return "rust"
    raise ValueError(f"pilot judge languages are python and rust, not {value!r}")


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _issue_spans(issues: Sequence[IssueEvidence]) -> list[InputSpan]:
    """Analyzer findings the candidate introduced or worsened, as cited evidence.

    The finding text is the analyzer's own explanation and its file location. Baseline debt is
    excluded: unchanged pre-existing findings are already visible in the evidence manifest and are
    not the candidate's evidence.
    """
    spans: list[InputSpan] = []
    for issue in issues:
        if issue.relation not in {"introduced", "worsened"} or not issue.path:
            continue
        spans.append(
            InputSpan(
                origin="analyzer_evidence",
                path=issue.path,
                start_line=issue.start_line,
                end_line=issue.end_line,
                text=(
                    f"{issue.issue_key} severity={issue.severity or 'unknown'} "
                    f"relation={issue.relation} tools={','.join(issue.tools)} "
                    f"explanation={issue.explanation or 'no explanation recorded'}"
                )[:2_000],
            )
        )
    return spans


def _comments(path: str, text: str) -> list[InputComment]:
    extracted: list[InputComment] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = LINE_COMMENT.search(line)
        if match is None:
            continue
        body = match.group(1).strip()
        if len(body) < 12:
            continue
        extracted.append(InputComment(path=path, text=f"line {number}: {body}"[:2_000]))
        if len(extracted) >= MAX_COMMENTS:
            break
    return extracted
