"""Strict, frozen records exchanged by the screening pipeline.

Every persisted value here is canonical-JSON safe: integers and strings only. Overlap and
agreement measures are integer basis points (0-10000), because the canonical profile in
``polycodebench_core.canonical`` rejects floating-point numbers and digests must be reproducible.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SLUG = r"^[a-z0-9][a-z0-9._-]{0,127}$"
MAX_TEXT_BYTES = 1_000_000

SplitName = Literal["public_development", "public_validation", "private_heldout"]
Audience = Literal["internal", "generator_model", "evaluated_model", "external_party", "public"]


class ScreeningDecision(StrEnum):
    """Output of the pre-execution screen. Neither value means the task is admitted."""

    REJECTED = "rejected"
    READY_FOR_EXECUTABLE_ADMISSION = "ready_for_executable_admission"


class TaskgenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CandidateTask(TaskgenModel):
    """Text proposed by a generator. Untrusted until every gate and executable admission pass."""

    candidate_id: str = Field(pattern=SLUG)
    family_id: str = Field(pattern=SLUG)
    generator_id: str = Field(min_length=1, max_length=256)
    generator_is_external: bool
    statement: str = Field(min_length=1, max_length=MAX_TEXT_BYTES)
    reference_solution: str = Field(min_length=1, max_length=MAX_TEXT_BYTES)
    hidden_tests: str = Field(min_length=1, max_length=MAX_TEXT_BYTES)


class ExposureEvent(TaskgenModel):
    """One time a task text reached an audience. ``recipient_id`` names the exact endpoint."""

    task_id: str = Field(pattern=SLUG)
    audience: Audience
    recipient_id: str = Field(min_length=1, max_length=256)
    occurred_at: datetime
    evidence_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @property
    def is_external(self) -> bool:
        return self.audience != "internal"


class GateResult(TaskgenModel):
    gate: str = Field(pattern=SLUG)
    passed: bool
    reasons: tuple[str, ...] = ()
    metrics: tuple[tuple[str, int], ...] = ()


class ScreeningReport(TaskgenModel):
    kind: Literal["candidate_screening_report"] = "candidate_screening_report"
    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=SLUG)
    family_id: str = Field(pattern=SLUG)
    generator_id: str
    policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reference_corpus_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    canary: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    gates: tuple[GateResult, ...]
    decision: ScreeningDecision
    cluster_id: str | None = Field(default=None, pattern=SLUG)
    split: SplitName | None = None
