"""Durable evaluation execution and private report-bundle contract checks."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from polycodebench_core.jobs import JobClaim
from polycodebench_core.models import Candidate
from polycodebench_evaluation.evaluator import Evaluation, Evaluator
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_orchestration.grading.assignment import (
    EvaluationAssignment,
    _archive_files,
    freeze_task_view,
)
from polycodebench_orchestration.grading.executor import (
    EVIDENCE_DOMAIN,
    EVIDENCE_MEDIA_TYPE,
    EvaluationStageExecutor,
    evaluation_archive,
)
from polycodebench_persistence.artifacts import ArtifactRepository
from polycodebench_plugins_api import ExecutableLanguagePlugin, load_allowlist
from polycodebench_runner.provider import SandboxProvider
from polycodebench_services.task_packages import TaskPackageImporter
from test_task_admission_postgres import _task_document

ROOT = Path(__file__).resolve().parents[1]
TASK_PACKAGE = ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"


class _MemoryArtifacts:
    def __init__(self) -> None:
        self.body: bytes | None = None
        self.expected_digest: str | None = None
        self.expected_size: int | None = None
        self.visibility: str | None = None
        self.domain: str | None = None
        self.media_type: str | None = None
        self.artifact_id = uuid4()

    def begin_upload(self, **kwargs: object) -> UUID:
        self.expected_digest = str(kwargs["expected_digest"])
        self.expected_size = int(kwargs["expected_size"])
        self.visibility = str(kwargs["visibility"])
        self.domain = str(kwargs["encryption_domain"])
        self.media_type = str(kwargs["media_type"])
        return uuid4()

    def upload(self, *, upload_id: UUID, owner: str, body: bytes) -> None:
        self.body = body

    def finalize(self, *, upload_id: UUID, owner: str) -> UUID:
        assert self.body is not None
        assert self.expected_size == len(self.body)
        assert self.expected_digest == "sha256:" + hashlib.sha256(self.body).hexdigest()
        return self.artifact_id


def _assignment(evaluation_id: UUID) -> EvaluationAssignment:
    imported = TaskPackageImporter().import_package(TASK_PACKAGE)
    task = _task_document(
        task_id="grading-executor-python",
        report_digest="sha256:" + "a" * 64,
        visible_id=uuid4(),
        hidden_id=uuid4(),
        imported=imported,
    )
    plugin = PythonLanguagePlugin()
    visible_files = _archive_files(imported.visible_archive)
    hidden_files = _archive_files(imported.hidden_archive)
    package_files = {**visible_files, **hidden_files}
    view = freeze_task_view(
        task,
        package_files,
        "sha256:" + "d" * 64,
        cast(ExecutableLanguagePlugin, plugin),
    )
    candidate = Candidate(
        schema_version=1,
        kind="candidate",
        candidate_id=str(uuid4()),
        run_id=str(uuid4()),
        task_id=task.task_id,
        task_version=task.version,
        sample_index=0,
        submission_kind="source_bundle",
        payload_digest="sha256:" + "2" * 64,
        artifact_ids=[str(uuid4())],
        frozen_at="2026-10-02T00:00:00.000000Z",
    )
    return EvaluationAssignment(
        evaluation_id=evaluation_id,
        attempt_id=uuid4(),
        run_id=UUID(candidate.run_id),
        oracle_digest="sha256:" + "e" * 64,
        policy_config_id=uuid4(),
        task=task,
        view=view,
        plugin=cast(ExecutableLanguagePlugin, plugin),
        candidate=candidate,
        candidate_files={"solution.py": b"def top_words(text, limit): return []\n"},
        visible_files=visible_files,
        baseline_files={},
        overlay_files={"tests/test_words.py": b"hidden"},
        config_files={},
        allowed_paths=tuple(str(path) for path in task.output_contract.allowed_paths),
    )


def _claim(evaluation_id: UUID) -> JobClaim:
    return JobClaim(
        job_id=uuid4(),
        execution_id=uuid4(),
        slot_id=uuid4(),
        worker_id=str(uuid4()),
        slot_key="grading-slot-1",
        stage="evaluate",
        scope_type="evaluation",
        scope_id=evaluation_id,
        input_artifact_id=None,
        input_digest="sha256:" + "1" * 64,
        resource_class="grading-small",
        queue_class="grading",
        fence=7,
        deliveries=1,
        lease_until_epoch=1_800_000_000,
    )


def test_stage_executor_persists_an_internal_bundle_and_uses_database_identity() -> None:
    evaluation_id = uuid4()
    assignment = _assignment(evaluation_id)
    artifacts = _MemoryArtifacts()
    allowlist = load_allowlist(ROOT / "config" / "plugins" / "allowlist-v1.yaml")
    executor = EvaluationStageExecutor(
        load_assignment=lambda _claim: assignment,
        plugin_allowlist=allowlist,
    )

    result = asyncio.run(
        executor(
            _claim(evaluation_id),
            None,
            cast(SandboxProvider, object()),
            cast(ArtifactRepository, artifacts),
        )
    )

    assert result.output_artifact_id == artifacts.artifact_id
    assert result.evaluation_gate == result.outcome.quality_gate == "fail"
    assert artifacts.visibility == "internal"
    assert artifacts.domain == EVIDENCE_DOMAIN
    assert artifacts.media_type == EVIDENCE_MEDIA_TYPE
    assert artifacts.body is not None
    with zipfile.ZipFile(io.BytesIO(artifacts.body)) as archive:
        evidence = json.loads(archive.read("evidence.json"))
        assert evidence["evaluation_id"] == str(evaluation_id)
        assert json.loads(archive.read("raw-index.json")) == []


def test_evidence_archive_keeps_repeated_report_paths_separate_and_verifiable() -> None:
    assignment = _assignment(uuid4())
    empty = Evaluator(assignment.plugin, runner=None)._empty_evidence(
        view=assignment.view,
        candidate=assignment.candidate,
        overlay={},
        config={},
        allowed_paths=assignment.allowed_paths,
        baseline_files=None,
        gate="fail",
        reasons=("fixture",),
        evaluation_id=str(assignment.evaluation_id),
    )
    evaluation = Evaluation(
        evidence=empty,
        raw={
            "test/acceptance/r0/report.jsonl": b"first",
            "test/robustness/r0/report.jsonl": b"second",
        },
        observations=[],
        baseline_observations=[],
    )

    with zipfile.ZipFile(io.BytesIO(evaluation_archive(evaluation))) as archive:
        index = json.loads(archive.read("raw-index.json"))
        assert [(item["stage"], item["path"]) for item in index] == [
            ("test:acceptance:r0", "report.jsonl"),
            ("test:robustness:r0", "report.jsonl"),
        ]
        assert [archive.read(item["stored_path"]) for item in index] == [b"first", b"second"]
