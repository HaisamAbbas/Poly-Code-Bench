"""Bounded local Docker execution for trusted authored admission fixtures only."""

from __future__ import annotations

import hashlib
import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from polycodebench_services.task_packages import ImportedTaskPackage

PYTHON_FIXTURE_IMAGE = (
    "docker.io/library/python:3.12-slim@sha256:"
    "44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b"
)
MAX_FIXTURE_OUTPUT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FixtureExecution:
    name: str
    variant: str
    repetitions: int
    exit_codes: tuple[int | None, ...]
    matched_expected: tuple[bool, ...]
    timed_out: tuple[bool, ...]
    stdout_digests: tuple[str, ...]
    stderr_digests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixtureAdmissionResult:
    profile_id: str
    execution_tier: str
    runtime_image_digest: str
    reference_check: str
    faulty_check: str
    alternative_check: str
    flakiness_check: str
    rights_check: str
    disclosure_check: str
    executions: tuple[FixtureExecution, ...]
    passed: bool


class DockerFixtureRunner:
    """Runs a single authored candidate file without mounting any hidden assets."""

    def __init__(
        self,
        image: str = PYTHON_FIXTURE_IMAGE,
        timeout_seconds: int = 10,
        scratch_root: Path | None = None,
    ) -> None:
        if image != PYTHON_FIXTURE_IMAGE:
            raise ValueError("local fixture runner image is not on the approved digest allowlist")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("fixture timeout must be in [1,60] seconds")
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.scratch_root = (scratch_root or Path.cwd() / ".cache" / "task-admission").resolve()
        self.scratch_root.mkdir(parents=True, exist_ok=True)

    def execute(self, candidate: bytes, stdin: bytes) -> tuple[int | None, bytes, bytes, bool]:
        if len(candidate) > 1_000_000 or len(stdin) > 1_000_000:
            raise ValueError("fixture candidate or input exceeds the 1 MiB development limit")
        with tempfile.TemporaryDirectory(
            prefix="pcb-candidate-", dir=self.scratch_root
        ) as temporary:
            candidate_dir = Path(temporary)
            if not candidate_dir.resolve().is_relative_to(self.scratch_root):
                raise ValueError("fixture scratch directory is outside its configured root")
            candidate_path = candidate_dir / "main.py"
            candidate_path.write_bytes(candidate)
            container_name = f"pcb-admission-{uuid4().hex}"
            command = [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--label",
                "pcb.task-admission=true",
                "-i",
                "--pull=never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "32",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--user",
                "65532:65532",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m",
                "--mount",
                f"type=bind,source={candidate_dir.resolve()},target=/candidate,readonly",
                "--workdir",
                "/tmp",
                self.image,
                "python",
                "-I",
                "-B",
                "-S",
                "/candidate/main.py",
            ]
            try:
                completed = subprocess.run(
                    command,
                    input=stdin,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired as error:
                partial = error.stdout if isinstance(error.stdout, bytes) else b""
                stderr = error.stderr if isinstance(error.stderr, bytes) else b""
                return (
                    None,
                    partial[:MAX_FIXTURE_OUTPUT_BYTES],
                    stderr[:MAX_FIXTURE_OUTPUT_BYTES],
                    True,
                )
            finally:
                # Killing the Docker client does not stop its container. Remove this
                # invocation's unique container before releasing its host scratch files.
                try:
                    cleanup = subprocess.run(
                        ["docker", "rm", "--force", container_name],
                        capture_output=True,
                        timeout=10,
                        check=False,
                        shell=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    raise ValueError("fixture container cleanup could not be confirmed") from error
                if cleanup.returncode != 0 and b"No such container" not in cleanup.stderr:
                    raise ValueError("fixture container cleanup could not be confirmed")
            output = completed.stdout[:MAX_FIXTURE_OUTPUT_BYTES]
            error_output = (completed.stderr or b"")[:MAX_FIXTURE_OUTPUT_BYTES]
            return completed.returncode, output, error_output, False


def validate_authored_fixtures(
    package: ImportedTaskPackage, runner: DockerFixtureRunner | None = None
) -> FixtureAdmissionResult:
    """Actually execute fixture variants and report outcomes without output leakage."""
    active_runner = runner or DockerFixtureRunner()
    manifest = package.manifest
    approved_image_digest = "sha256:" + PYTHON_FIXTURE_IMAGE.rsplit("@sha256:", 1)[1]
    if (
        manifest.task.primary_language != "python"
        or manifest.runtime.image_digest != approved_image_digest
    ):
        raise ValueError(
            "authored fixture profile requires Python and its approved immutable image"
        )
    rights_passed = manifest.rights.redistribution_status in {"cleared", "authored_fixture"}
    executions: list[FixtureExecution] = []
    with (
        ZipFile(io.BytesIO(package.visible_archive)) as visible,
        ZipFile(io.BytesIO(package.hidden_archive)) as hidden,
    ):
        snapshot_files = {name: visible.read(name) for name in visible.namelist()}
        snapshot_files.update({name: hidden.read(name) for name in hidden.namelist()})
    for fixture in manifest.fixtures:
        solution = snapshot_files[fixture.solution_path]
        input_data = snapshot_files[fixture.input_path]
        expected = snapshot_files[fixture.expected_output_path]
        repetitions = 5 if fixture.variant == "reference" else 1
        codes: list[int | None] = []
        matches: list[bool] = []
        timeouts: list[bool] = []
        digests: list[str] = []
        error_digests: list[str] = []
        for _ in range(repetitions):
            exit_code, stdout, stderr, timed_out = active_runner.execute(solution, input_data)
            codes.append(exit_code)
            matches.append(not timed_out and exit_code == 0 and stdout == expected)
            timeouts.append(timed_out)
            digests.append("sha256:" + hashlib.sha256(stdout).hexdigest())
            error_digests.append("sha256:" + hashlib.sha256(stderr).hexdigest())
        executions.append(
            FixtureExecution(
                name=fixture.name,
                variant=fixture.variant,
                repetitions=repetitions,
                exit_codes=tuple(codes),
                matched_expected=tuple(matches),
                timed_out=tuple(timeouts),
                stdout_digests=tuple(digests),
                stderr_digests=tuple(error_digests),
            )
        )
    by_variant: dict[str, list[FixtureExecution]] = {}
    for execution in executions:
        by_variant.setdefault(execution.variant, []).append(execution)
    reference_ok = all(all(item.matched_expected) for item in by_variant.get("reference", []))
    alternative_ok = all(all(item.matched_expected) for item in by_variant.get("alternative", []))
    faulty_detected = all(
        any(
            code == 0 and not matched
            for code, matched in zip(item.exit_codes, item.matched_expected, strict=True)
        )
        for item in by_variant.get("faulty", [])
    )
    flakiness_ok = all(
        len(set(item.stdout_digests)) == 1
        and len(set(item.exit_codes)) == 1
        and all(not timeout for timeout in item.timed_out)
        for item in by_variant.get("reference", [])
    )
    disclosure_passed = True  # Importer has already checked byte/path/credential disclosure.
    passed = all(
        (
            reference_ok,
            faulty_detected,
            alternative_ok,
            flakiness_ok,
            rights_passed,
            disclosure_passed,
        )
    )
    return FixtureAdmissionResult(
        profile_id="admission-v1-authored-fixture",
        execution_tier="local_fixture",
        runtime_image_digest=approved_image_digest,
        reference_check="pass" if reference_ok else "fail",
        faulty_check="pass" if faulty_detected else "fail",
        alternative_check="pass" if alternative_ok else "fail",
        flakiness_check="pass" if flakiness_ok else "fail",
        rights_check="pass" if rights_passed else "fail",
        disclosure_check="pass" if disclosure_passed else "fail",
        executions=tuple(executions),
        passed=passed,
    )
