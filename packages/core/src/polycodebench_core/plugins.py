"""Static plugin interfaces; implementations are administrator-installed code."""

from __future__ import annotations

from typing import Protocol

from polycodebench_core.models import (
    Artifact,
    Candidate,
    Digest,
    EntityId,
    Observation,
    Scorecard,
    ScoreItem,
    Slug,
    TaskVersion,
)


class SuiteAdapter(Protocol):
    def validate_task(self, task: TaskVersion) -> None: ...

    def export_metrics(self, observations: tuple[Observation, ...]) -> dict[str, str]: ...


class LanguagePlugin(Protocol):
    def validate_candidate(self, task: TaskVersion, candidate: Candidate) -> None: ...


class AnalyzerPlugin(Protocol):
    def analyze(
        self, candidate: Candidate, artifact_ids: tuple[EntityId, ...]
    ) -> tuple[Observation, ...]: ...


class ModelAdapter(Protocol):
    def capabilities(self) -> tuple[str, ...]: ...


class SandboxProvider(Protocol):
    def execute_bounded(self, command_id: Slug, timeout_seconds: int) -> Artifact: ...


class JudgeAdapter(Protocol):
    def score_packet(self, packet_digest: Digest) -> tuple[ScoreItem, ...]: ...


class ScoringPolicy(Protocol):
    def score(
        self, task: TaskVersion, candidate: Candidate, observations: tuple[Observation, ...]
    ) -> Scorecard: ...
