"""Plan execution evidence and outcome classification shared by every parser."""

from __future__ import annotations

import hashlib
from typing import Literal

from polycodebench_core.canonical import canonical_json_bytes, parse_json_strict
from polycodebench_core.identity import derived_entity_id
from pydantic import Field

from polycodebench_plugins_api.contracts import (
    EXECUTION_RECORD_PATH,
    AnalysisPlan,
    ArtifactReader,
    ExecutionPlan,
    PluginModel,
)

PlanStatus = Literal[
    "completed",  # exit matched the success contract
    "completed_with_findings",  # exit matched the findings contract
    "tool_error",  # declared or unexpected error exit, or no execution record
    "timed_out",
    "output_missing",  # a required output is absent
    "empty_report",  # a successful structured report is present but contains no evidence
]


def raw_report_ids(plan: AnalysisPlan) -> list[str]:
    """Stable identities for the exact required output paths declared by an analyzer plan."""
    return [
        derived_entity_id(plan.plan_id, plan.tool.name, output.path)
        for output in plan.outputs
        if output.required
    ]


class ExecutionRecord(PluginModel):
    """What the supervisor observed; written beside the plan outputs for the parser."""

    kind: Literal["execution_record"] = "execution_record"
    plan_id: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int = Field(ge=0)
    stdout_digest: str
    stderr_digest: str
    stdout_tail: str = Field(max_length=4000)
    stderr_tail: str = Field(max_length=4000)
    isolation_tier: Literal["development", "production"]
    sandbox_id: str


def make_record(
    plan: ExecutionPlan,
    *,
    exit_code: int | None,
    timed_out: bool,
    duration_ms: int,
    stdout: bytes,
    stderr: bytes,
    isolation_tier: Literal["development", "production"],
    sandbox_id: str,
) -> ExecutionRecord:
    return ExecutionRecord(
        plan_id=plan.plan_id,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
        stdout_digest="sha256:" + hashlib.sha256(stdout).hexdigest(),
        stderr_digest="sha256:" + hashlib.sha256(stderr).hexdigest(),
        stdout_tail=stdout[-4000:].decode("utf-8", errors="replace"),
        stderr_tail=stderr[-4000:].decode("utf-8", errors="replace"),
        isolation_tier=isolation_tier,
        sandbox_id=sandbox_id,
    )


def record_bytes(record: ExecutionRecord) -> bytes:
    return bytes(canonical_json_bytes(record.model_dump(mode="json")))


def read_record(raw: ArtifactReader) -> ExecutionRecord | None:
    try:
        return ExecutionRecord.model_validate(parse_json_strict(raw.read(EXECUTION_RECORD_PATH)))
    except (FileNotFoundError, ValueError):
        return None


def plan_status(
    plan: ExecutionPlan, raw: ArtifactReader
) -> tuple[PlanStatus, ExecutionRecord | None]:
    """Status of a plan from supervisor evidence only; never from the tool's own claims.

    A missing execution record, missing required output or unexpected exit is never "clean".
    """
    record = read_record(raw)
    if record is None:
        return "tool_error", None
    verdict = plan.exit_semantics.classify(record.exit_code, record.timed_out)
    if verdict == "timeout":
        return "timed_out", record
    if verdict in {"error", "unexpected"}:
        return "tool_error", record
    present = set(raw.list())
    if any(output.required and output.path not in present for output in plan.outputs):
        return "output_missing", record
    if verdict == "success":
        for output in plan.outputs:
            if (
                output.required
                and output.format != "text"
                and not output.empty_is_clean
                and not raw.read(output.path).strip()
            ):
                return "empty_report", record
    return ("completed" if verdict == "success" else "completed_with_findings"), record
