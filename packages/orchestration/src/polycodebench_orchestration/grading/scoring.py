"""Score one completed private evaluation through the frozen policy adapter."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from polycodebench_core.canonical import (
    canonical_document_digest,
    canonical_json_bytes,
)
from polycodebench_evaluation.evidence import EvaluationEvidence
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_persistence.scoring import (
    PostgresScoringRepository,
    ScorecardRecord,
    ScorecardWriteResult,
    ScoreItemRecord,
)
from polycodebench_plugins_api import LanguageProfile
from polycodebench_scoring.loader import archive_document, load_evidence_ownership
from polycodebench_scoring.manifest import ScoringInvocation
from polycodebench_scoring.scorer import ScoringOutcome, score_evaluation

from polycodebench_orchestration.grading.assignment import (
    DatabaseEvaluationAssignmentLoader,
)
from polycodebench_orchestration.grading.scoring_adapter import evaluation_to_manifest

MAX_EVIDENCE_ARCHIVE_BYTES = 512 * 1024**2
MAX_EVIDENCE_DOCUMENT_BYTES = 64 * 1024**2
SCORING_OUTCOME_DOMAIN = "scoring-outcomes"
SCORING_OUTCOME_MEDIA_TYPE = "application/vnd.polycodebench.scoring+json"


class EvaluationScoringRejected(ValueError):
    """A completed evaluation is not safe to score from its frozen records."""


@dataclass(frozen=True, slots=True)
class ScoringBatchResult:
    selected: int
    created: int
    already_scored: int


class DatabaseEvaluationScorer:
    """Create and persist one internal scorecard without executing a candidate or provider."""

    def __init__(
        self,
        *,
        assignments: DatabaseEvaluationAssignmentLoader,
        artifacts: ArtifactRepository,
        scorecards: PostgresScoringRepository,
        ownership_path: Path,
        profile_source_path: Path,
        actor: str,
    ) -> None:
        if not actor or len(actor) > 255 or not actor.isascii():
            raise ValueError("scoring actor identity is invalid")
        self._assignments = assignments
        self._artifacts = artifacts
        self._scorecards = scorecards
        self._ownership = load_evidence_ownership(ownership_path)
        self._profile_source_path = profile_source_path
        self._actor = actor

    def score(self, evaluation_id: UUID) -> tuple[ScoringOutcome, ScorecardWriteResult]:
        with self._scorecards.evaluation_lock(evaluation_id):
            return self._score_locked(evaluation_id)

    def score_pending(self, *, limit: int = 100) -> ScoringBatchResult:
        evaluation_ids = self._scorecards.pending_evaluation_ids(limit=limit)
        created = 0
        already_scored = 0
        for evaluation_id in evaluation_ids:
            with self._scorecards.evaluation_lock(evaluation_id):
                if self._scorecards.has_scorecard(evaluation_id):
                    already_scored += 1
                    continue
                _, written = self._score_locked(evaluation_id)
                if written.created:
                    created += 1
                else:
                    already_scored += 1
        return ScoringBatchResult(
            selected=len(evaluation_ids),
            created=created,
            already_scored=already_scored,
        )

    def _score_locked(self, evaluation_id: UUID) -> tuple[ScoringOutcome, ScorecardWriteResult]:
        assignment = self._assignments.load(evaluation_id)
        if assignment.policy is None or assignment.policy_digest is None:
            raise EvaluationScoringRejected("evaluation has no validated frozen scoring policy")
        if canonical_document_digest(assignment.policy) != assignment.policy_digest:
            raise EvaluationScoringRejected("frozen scoring policy digest changed after scheduling")
        if assignment.evidence_manifest_id is None:
            raise EvaluationScoringRejected("completed evaluation has no evidence artifact")
        evidence_metadata, evidence_bundle = self._artifacts.read_verified(
            assignment.evidence_manifest_id
        )
        if (
            evidence_metadata["status"] != "verified"
            or evidence_metadata["visibility"] != "internal"
            or evidence_metadata["encryption_domain"] != "evaluation-evidence"
            or len(evidence_bundle) > MAX_EVIDENCE_ARCHIVE_BYTES
            or "sha256:" + hashlib.sha256(evidence_bundle).hexdigest()
            != evidence_metadata["content_digest"]
        ):
            raise EvaluationScoringRejected("evaluation evidence artifact failed integrity checks")
        evidence = _read_evaluation_evidence(evidence_bundle)
        expected_gate = {"pass": "pass", "fail": "fail", "unknown": "incomplete"}.get(
            assignment.evaluation_gate
        )
        if (
            expected_gate is None
            or assignment.evaluation_state not in {"ready", "failed"}
            or evidence.evaluation_id != str(assignment.evaluation_id)
            or evidence.gate != expected_gate
        ):
            raise EvaluationScoringRejected("evaluation state and evidence gate disagree")
        profile = getattr(assignment.plugin, "language_profile", None)
        if callable(profile):
            profile = profile()
        if profile is not None and not isinstance(profile, LanguageProfile):
            raise EvaluationScoringRejected("language plugin returned an invalid scoring profile")
        profile_source_digest = _profile_source_digest(profile, self._profile_source_path)

        invocation = ScoringInvocation(
            run_id=str(assignment.run_id),
            candidate_id=assignment.candidate.candidate_id,
            recorded_at=_artifact_recorded_at(evidence_metadata),
            scorer_digest=scorer_source_digest(),
        )
        manifest = evaluation_to_manifest(
            task=assignment.view,
            evidence=evidence,
            policy=assignment.policy,
            ownership=self._ownership,
            profile=profile,
            profile_source_digest=profile_source_digest,
            invocation=invocation,
            evidence_artifact_id=str(assignment.evidence_manifest_id),
            evidence_artifact_digest=str(evidence_metadata["content_digest"]),
        )
        outcome = score_evaluation(
            assignment.view,
            assignment.policy,
            manifest,
            ownership=self._ownership,
            profile=profile,
        )
        outcome_bytes = canonical_json_bytes(archive_document(outcome))
        outcome_digest = "sha256:" + hashlib.sha256(outcome_bytes).hexdigest()
        upload_id = self._artifacts.begin_upload(
            owner=self._actor,
            visibility="internal",
            encryption_domain=SCORING_OUTCOME_DOMAIN,
            expected_digest=outcome_digest,
            expected_size=len(outcome_bytes),
            media_type=SCORING_OUTCOME_MEDIA_TYPE,
        )
        self._artifacts.upload(upload_id=upload_id, owner=self._actor, body=outcome_bytes)
        outcome_artifact_id = self._artifacts.finalize(upload_id=upload_id, owner=self._actor)
        record = scorecard_record(
            outcome,
            evaluation_id=evaluation_id,
            evidence_artifact_id=assignment.evidence_manifest_id,
            evidence_artifact_digest=str(evidence_metadata["content_digest"]),
            artifact_id=outcome_artifact_id,
            artifact_digest=outcome_digest,
        )
        written = self._scorecards.store(record, actor=self._actor)
        return outcome, written


def scorecard_record(
    outcome: ScoringOutcome,
    *,
    evaluation_id: UUID,
    evidence_artifact_id: UUID,
    evidence_artifact_digest: str,
    artifact_id: UUID,
    artifact_digest: str,
) -> ScorecardRecord:
    """Bind DB item status/explanations to the canonical scorecard item list."""
    explanations = {(row.dimension, row.item_id): row for row in outcome.explanation.items}
    rows: list[ScoreItemRecord] = []
    for item in outcome.scorecard.items:
        explanation = explanations.get((item.dimension, item.item_id))
        if explanation is None:
            raise EvaluationScoringRejected("scorecard item has no matching explanation row")
        rows.append(
            ScoreItemRecord(
                dimension=item.dimension,
                item_id=item.item_id,
                status=explanation.status,
                applicable=item.applicable,
                raw_value=item.raw_value,
                effective_weight_bps=item.effective_weight_bps,
                contribution=item.contribution,
                reason="; ".join(explanation.reasons) or None,
                evidence_refs=tuple(
                    reference.model_dump(mode="json") for reference in explanation.evidence_refs
                ),
            )
        )
    return ScorecardRecord(
        evaluation_id=evaluation_id,
        scorecard=outcome.scorecard,
        evidence_artifact_id=evidence_artifact_id,
        evidence_artifact_digest=evidence_artifact_digest,
        artifact_id=artifact_id,
        artifact_digest=artifact_digest,
        items=tuple(rows),
    )


def _read_evaluation_evidence(archive_bytes: bytes) -> EvaluationEvidence:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            infos = [item for item in archive.infolist() if item.filename == "evidence.json"]
            if len(infos) != 1 or infos[0].file_size > MAX_EVIDENCE_DOCUMENT_BYTES:
                raise EvaluationScoringRejected(
                    "evaluation evidence document is missing or too large"
                )
            body = archive.read(infos[0])
        return EvaluationEvidence.model_validate_json(body, strict=True)
    except EvaluationScoringRejected:
        raise
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        raise EvaluationScoringRejected("evaluation evidence archive is invalid") from None


def _artifact_recorded_at(metadata: dict[str, object]) -> str:
    """Use the immutable evidence artifact time so a replay has the same score identity."""
    created_at = metadata.get("created_at")
    if not isinstance(created_at, datetime) or created_at.tzinfo is None:
        raise EvaluationScoringRejected("evaluation evidence artifact has no stable creation time")
    return created_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _profile_source_digest(profile: LanguageProfile | None, source_path: Path) -> str:
    """Bind profile semantics and both shared and language-specific frozen configuration."""
    paths = [source_path]
    if profile is not None:
        language_path = source_path.with_name(f"{profile.language_id}-profile-v1.yaml")
        paths.append(language_path)
    digest = hashlib.sha256(b"polycodebench-language-profile-source-v1\0")
    for path in sorted(paths):
        try:
            body = path.read_bytes()
        except OSError:
            raise EvaluationScoringRejected(
                "frozen language-profile source is unavailable"
            ) from None
        name = path.name.encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    if profile is not None:
        profile_body = canonical_json_bytes(profile.model_dump(mode="json"))
        digest.update(len(profile_body).to_bytes(8, "big"))
        digest.update(profile_body)
    return "sha256:" + digest.hexdigest()


def scorer_source_digest() -> str:
    """Hash scorer and evaluation-adapter code used to produce each scorecard."""
    import polycodebench_scoring

    package_file = getattr(polycodebench_scoring, "__file__", None)
    if not isinstance(package_file, str):
        raise EvaluationScoringRejected("scoring package source files are unavailable")
    root = Path(package_file).resolve().parent
    adapter_path = Path(__file__).with_name("scoring_adapter.py").resolve()
    source_files = [*root.rglob("*.py"), adapter_path]
    digest = hashlib.sha256(b"polycodebench-scoring-source-v2\0")
    for path in sorted(source_files):
        if not path.is_file():
            continue
        relative_path = (
            path.relative_to(root).as_posix()
            if path.is_relative_to(root)
            else f"adapter/{path.name}"
        )
        relative = relative_path.encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        body = path.read_bytes()
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return "sha256:" + digest.hexdigest()


__all__ = [
    "DatabaseEvaluationScorer",
    "EvaluationScoringRejected",
    "MAX_EVIDENCE_ARCHIVE_BYTES",
    "ScoringBatchResult",
    "SCORING_OUTCOME_DOMAIN",
    "_profile_source_digest",
    "scorecard_record",
    "scorer_source_digest",
]
