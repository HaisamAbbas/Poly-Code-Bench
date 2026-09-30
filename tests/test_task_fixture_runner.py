"""Fixture snapshot replay and Docker lifecycle regression coverage."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from polycodebench_core.canonical import canonical_digest
from polycodebench_core.models import AdmissionExecutionReport
from polycodebench_services.task_fixture_runner import (
    DockerFixtureRunner,
    FixtureAdmissionResult,
    FixtureExecution,
    validate_authored_fixtures,
)
from polycodebench_services.task_packages import TaskPackageImporter

ROOT = Path(__file__).resolve().parents[1]


def _scratch() -> Path:
    path = ROOT / ".cache" / f"p05-runner-test-{uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_timeout_removes_its_named_container(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def invoke(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", invoke)
    result = DockerFixtureRunner(timeout_seconds=1, scratch_root=_scratch()).execute(
        b"while True: pass", b""
    )
    assert result[3] is True
    name = calls[0][calls[0].index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "--force", name]


def test_unconfirmed_cleanup_cannot_return_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def invoke(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[1] == "run":
            return subprocess.CompletedProcess(command, 0, b"42\n", b"")
        return subprocess.CompletedProcess(command, 1, b"", b"daemon unavailable")

    monkeypatch.setattr(subprocess, "run", invoke)
    with pytest.raises(ValueError, match="cleanup could not be confirmed"):
        DockerFixtureRunner(scratch_root=_scratch()).execute(b"print(42)", b"")


def test_validation_replays_the_imported_bytes_after_source_changes() -> None:
    root = _scratch()
    package_root = root / "package"
    shutil.copytree(ROOT / "taskpacks/admission-smoke", package_root)
    package = TaskPackageImporter().import_package(package_root)
    reference = (package_root / "admission/reference.py").read_bytes()
    alternative = (package_root / "admission/alternative.py").read_bytes()
    (package_root / "admission/reference.py").write_bytes(b"print(0)\n")
    candidates: list[bytes] = []

    class RecordingRunner(DockerFixtureRunner):
        def execute(self, candidate: bytes, stdin: bytes) -> tuple[int | None, bytes, bytes, bool]:
            candidates.append(candidate)
            output = b"42\n" if candidate in (reference, alternative) else b"41\n"
            return 0, output, b"", False

    validate_authored_fixtures(package, RecordingRunner(scratch_root=root))
    assert candidates[:5] == [reference] * 5
    assert b"print(0)\n" not in candidates


@pytest.mark.parametrize("mutation", ["fabricated_stdout", "production_tier", "stale_package"])
def test_cli_freeze_rejects_untrusted_reports_before_persistence(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    monkeypatch.syspath_prepend(str(ROOT))
    from scripts import pcb

    body = json.loads(
        (
            ROOT / "docs/implementation/evidence/prompt-05-authored-fixture-admission-v3.json"
        ).read_text(encoding="utf-8")
    )
    actual = AdmissionExecutionReport.model_validate(body)
    replay = FixtureAdmissionResult(
        profile_id=actual.profile_id,
        execution_tier=actual.execution_tier,
        runtime_image_digest=actual.runtime_image_digest,
        reference_check=actual.checks.reference,
        faulty_check=actual.checks.faulty,
        alternative_check=actual.checks.alternative,
        flakiness_check=actual.checks.flakiness,
        rights_check=actual.checks.rights,
        disclosure_check=actual.checks.disclosure,
        executions=tuple(
            FixtureExecution(
                name=item.name,
                variant=item.variant,
                repetitions=item.repetitions,
                exit_codes=tuple(item.exit_codes),
                matched_expected=tuple(item.matched_expected),
                timed_out=tuple(item.timed_out),
                stdout_digests=tuple(item.stdout_digests),
                stderr_digests=tuple(item.stderr_digests),
            )
            for item in actual.executions
        ),
        passed=actual.passed,
    )
    if mutation == "fabricated_stdout":
        body["executions"][0]["stdout_digests"] = ["sha256:" + "f" * 64] * 5
        expected = "current authored-fixture replay"
    elif mutation == "production_tier":
        body["execution_tier"] = "production_worker"
        expected = "trusted worker evidence authority"
    else:
        body["package_digest"] = "sha256:" + "f" * 64
        expected = "does not belong to this package"
    body["report_digest"] = canonical_digest(
        {key: value for key, value in body.items() if key != "report_digest"}
    )
    # Even internally consistent, freshly rehashed claims need actual replay.
    AdmissionExecutionReport.model_validate(body)
    monkeypatch.setattr(pcb, "_load_json", lambda path: body)
    monkeypatch.setattr(pcb, "validate_authored_fixtures", lambda package: replay)

    def unexpected_service() -> None:
        pytest.fail("invalid admission reached persistence")

    monkeypatch.setattr(pcb, "_service", unexpected_service)
    with pytest.raises(ValueError, match=expected):
        pcb._task_freeze(
            argparse.Namespace(package=ROOT / "taskpacks/admission-smoke", report=Path("unused")),
            str(uuid4()),
        )


@pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1", reason="local Docker validation is opt-in"
)
def test_actual_docker_timeout_leaves_no_container() -> None:
    def live_ids() -> set[str]:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--no-trunc",
                "--filter",
                "label=pcb.task-admission=true",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return set(result.stdout.split())

    before = live_ids()
    try:
        result = DockerFixtureRunner(timeout_seconds=2, scratch_root=_scratch()).execute(
            b"while True:\n    pass\n", b""
        )
        assert result[3] is True
        assert live_ids() == before
    finally:
        for container_id in live_ids() - before:
            subprocess.run(
                ["docker", "rm", "--force", container_id],
                check=True,
                capture_output=True,
                timeout=10,
            )
