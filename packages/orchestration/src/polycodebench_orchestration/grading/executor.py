"""Run frozen code evaluations in claim-scoped guests and persist private evidence bundles."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from typing import Literal, cast

from polycodebench_core.canonical import canonical_json_bytes
from polycodebench_core.jobs import JobClaim, StageOutcome
from polycodebench_evaluation.evaluator import Evaluation, Evaluator
from polycodebench_evaluation.plan_runner import PlanRunner
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_plugins_api import PluginAllowlist, assert_plan_allowed
from polycodebench_runner.contracts import SandboxHandle
from polycodebench_runner.provider import SandboxProvider

from polycodebench_orchestration.grading.assignment import EvaluationAssignment
from polycodebench_orchestration.worker import StageResult

MAX_EVALUATION_BUNDLE_BYTES = 512 * 1024**2
EVIDENCE_DOMAIN = "evaluation-evidence"
EVIDENCE_MEDIA_TYPE = "application/vnd.polycodebench.evaluation+zip"


class EvaluationStageExecutor:
    """Execute one frozen evaluation; each plugin plan owns and destroys its own guest."""

    def __init__(
        self,
        *,
        load_assignment: Callable[[JobClaim], EvaluationAssignment],
        plugin_allowlist: PluginAllowlist,
        execution_tier: Literal[
            "local_fixture", "development_sandbox", "production_worker"
        ] = "development_sandbox",
        artifact_owner: str = "evaluation-worker",
    ) -> None:
        if not artifact_owner or len(artifact_owner) > 255:
            raise ValueError("evaluation artifact owner is invalid")
        self._load = load_assignment
        self._plugins = plugin_allowlist
        self._tier = execution_tier
        self._artifact_owner = artifact_owner

    async def __call__(
        self,
        claim: JobClaim,
        handle: SandboxHandle | None,
        sandbox: SandboxProvider,
        artifacts: ArtifactRepository,
    ) -> StageResult:
        if claim.scope_type != "evaluation" or claim.stage != "evaluate":
            raise ValueError("evaluator received a non-evaluation stage claim")
        if handle is not None:
            raise ValueError("evaluation plans manage their own short-lived sandbox guests")
        assignment = self._load(claim)
        runner = PlanRunner(
            sandbox,
            lane="grading",
            first_fence=claim.fence - 1,
            plan_validator=lambda plan: assert_plan_allowed(
                self._plugins, assignment.plugin.language_id, plan
            ),
        )
        evaluation: Evaluation = await Evaluator(
            assignment.plugin, runner, execution_tier=self._tier, max_parallel=1
        ).evaluate(
            view=assignment.view,
            candidate=assignment.candidate,
            candidate_files=assignment.candidate_files,
            overlay=assignment.overlay_files,
            config=assignment.config_files,
            allowed_paths=assignment.allowed_paths,
            baseline_files=assignment.baseline_files,
            label=str(assignment.evaluation_id),
            evaluation_id=str(assignment.evaluation_id),
        )
        bundle = evaluation_archive(evaluation)
        digest = "sha256:" + hashlib.sha256(bundle).hexdigest()
        upload_id = artifacts.begin_upload(
            owner=self._artifact_owner,
            visibility="internal",
            encryption_domain=EVIDENCE_DOMAIN,
            expected_digest=digest,
            expected_size=len(bundle),
            media_type=EVIDENCE_MEDIA_TYPE,
        )
        artifacts.upload(upload_id=upload_id, owner=self._artifact_owner, body=bundle)
        artifact_id = artifacts.finalize(upload_id=upload_id, owner=self._artifact_owner)

        gate = cast(
            Literal["pass", "fail", "unknown"],
            {"pass": "pass", "fail": "fail", "incomplete": "unknown"}[
                evaluation.evidence.gate
            ],
        )
        outcome = StageOutcome(
            quality_gate=gate,
            event_details={
                "evaluation_id": str(assignment.evaluation_id),
                "task_id": assignment.task.task_id,
                "evidence_digest": digest,
            },
        )
        return StageResult(
            output_artifact_id=artifact_id,
            outcome=outcome,
            evaluation_gate=gate,
        )


def evaluation_archive(evaluation: Evaluation) -> bytes:
    """Package canonical evidence with every retained report and an integrity index."""
    if sum(len(data) for data in evaluation.raw.values()) > MAX_EVALUATION_BUNDLE_BYTES:
        raise ValueError("evaluation raw evidence exceeds the artifact size limit")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        _write_member(
            archive,
            "evidence.json",
            canonical_json_bytes(evaluation.evidence.model_dump(mode="json")),
        )
        index: list[dict[str, str | int]] = []
        for index_number, (raw_key, body) in enumerate(sorted(evaluation.raw.items())):
            stage, path = _raw_identity(raw_key)
            stored_path = f"raw/{index_number:05d}.bin"
            _write_member(archive, stored_path, body)
            index.append(
                {
                    "stage": stage,
                    "path": path,
                    "stored_path": stored_path,
                    "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                }
            )
        _write_member(archive, "raw-index.json", canonical_json_bytes(index))
    bundle = output.getvalue()
    if len(bundle) > MAX_EVALUATION_BUNDLE_BYTES:
        raise ValueError("compressed evaluation evidence exceeds the artifact size limit")
    return bundle


def _write_member(archive: zipfile.ZipFile, path: str, body: bytes) -> None:
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    archive.writestr(info, body)


def _raw_identity(key: str) -> tuple[str, str]:
    if key.startswith("build/"):
        return "build", key.removeprefix("build/")
    if key.startswith("test/"):
        _, group_id, repetition, path = key.split("/", maxsplit=3)
        return f"test:{group_id}:{repetition}", path
    if key.startswith("analysis/"):
        _, side, path = key.split("/", maxsplit=2)
        return f"analysis:{side}", path
    raise ValueError("evaluation raw report has an unknown identity")


__all__ = [
    "EVIDENCE_DOMAIN",
    "EVIDENCE_MEDIA_TYPE",
    "EvaluationStageExecutor",
    "evaluation_archive",
]
