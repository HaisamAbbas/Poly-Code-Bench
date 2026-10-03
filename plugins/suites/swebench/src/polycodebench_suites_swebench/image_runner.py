"""Run a native repository-repair task's frozen test command for real.

The fixture-tier runner in ``polycodebench_suites_swebench.overlay`` executes a command on the host,
which is right for the Python fixture but not for the Rust port: nothing here would compile the
candidate, so a port that does not build would be graded from a log that claims otherwise.

This runner executes the frozen command inside a pinned OCI image, in the local Docker driver. The
output is bracketed into the upstream log shape and graded by the same pinned upstream evaluator as
every other candidate, so a port is compiler-verified exactly like a Python fixture is executed.
The image is pinned by digest through the administrator allowlist, never by a floating tag.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from polycodebench_suites_swebench.overlay import (
    END_TEST_OUTPUT,
    START_TEST_OUTPUT,
    TEST_EXIT_CODE,
    GradingWorkspaceError,
)

#: The workspace key prefix under which a native repository snapshot is stored.
REPO_PREFIX = "repo/"


class DockerTestRunner:
    """Execute the frozen test command inside a pinned image, once per workspace."""

    def __init__(
        self,
        image: str,
        *,
        argv: tuple[str, ...],
        container_repo_dir: str = "/workspace",
        timeout_seconds: int = 600,
    ) -> None:
        self._image = image
        self._argv = argv
        self._container_repo_dir = container_repo_dir
        self._timeout = timeout_seconds

    def __call__(self, workspace: Mapping[str, bytes], timeout_seconds: int) -> bytes:  # noqa: ARG002
        """Run the command over the workspace and return the bracketed upstream log."""
        if not any(path.startswith(REPO_PREFIX) for path in workspace):
            raise GradingWorkspaceError("the grading workspace contains no repository snapshot")
        with tempfile.TemporaryDirectory(prefix="pcb-native-image-") as temp:
            root = Path(temp) / "repo"
            for path, data in workspace.items():
                if not path.startswith(REPO_PREFIX):
                    continue
                target = root / path.removeprefix(REPO_PREFIX)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{root.as_posix()}:{self._container_repo_dir}",
                "-w",
                self._container_repo_dir,
                self._image,
                *self._argv,
            ]
            try:
                completed = subprocess.run(  # noqa: S603
                    command,
                    capture_output=True,
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return _wrap("test command timed out", "", 124).encode("utf-8")
            body = completed.stdout.decode("utf-8", errors="replace")
            errors = completed.stderr.decode("utf-8", errors="replace")
            return _wrap(body, errors, completed.returncode).encode("utf-8")


def _wrap(stdout: str, stderr: str, exit_code: int) -> str:
    """Bracket combined output the way the upstream evaluation script writes it.

    Both streams are included: a Rust compile error goes to stderr while the test summary goes to
    stdout, and an upstream parser that sees only one of them would read a failed build as a suite
    that ran cleanly.
    """
    body = stdout
    if stderr.strip():
        body = f"{stdout}\n{stderr}" if stdout.strip() else stderr
    return f"{START_TEST_OUTPUT}\n{body}\n{END_TEST_OUTPUT}\n{TEST_EXIT_CODE}: {exit_code}\n"
