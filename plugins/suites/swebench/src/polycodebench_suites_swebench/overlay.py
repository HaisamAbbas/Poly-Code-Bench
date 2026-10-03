"""Protected grading overlay for native repository repair (Prompt 24, PCB-24-3).

Grading runs on a workspace the solve session never sees: the frozen pre-fix snapshot, then the
candidate patch, then the hidden test patch. Two properties are enforced rather than assumed.

**One candidate cannot receive another's grade.** Every result is stored under
``run_identity(task_digest, candidate_digest, evaluator_digest)``. A cache lookup requires all
three to match, so a different candidate, a re-imported task, or a changed upstream evaluator each
force a fresh evaluation. A weak upstream cache key that reused the first candidate's report would
silently inflate a score, which is exactly the failure this binding exists to prevent.

**A visible test edit cannot replace native acceptance.** The candidate's patch is applied first;
the frozen test patch is applied *after* it, so a candidate that rewrites a graded test file has
its edit replaced by the frozen test body. The expected test lists come from the record, never from
anything the candidate submitted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from polycodebench_core.canonical import sha256_bytes

from polycodebench_suites_swebench import patching
from polycodebench_suites_swebench.grading import (
    UpstreamRunCache,
    evaluator_digest,
    grade_log_bytes,
    run_identity,
)
from polycodebench_suites_swebench.patching import PatchRejected
from polycodebench_suites_swebench.records import (
    NativeGradeResult,
    NativeTaskDraft,
    check_instance_leakage,
    enforce_patch_paths,
    graded_test_modules,
)

#: Upstream log markers. The upstream parser slices the log between these; without them
#: ``get_logs_eval`` finds no results and grades the instance unresolved.
START_TEST_OUTPUT = ">>>>> Start Test Output"
END_TEST_OUTPUT = ">>>>> End Test Output"
TEST_EXIT_CODE = ">>>>> Test Exit Code"

DEFAULT_TIMEOUT_SECONDS = 300

#: Runs one test command over a materialised workspace and returns the upstream-shaped log.
TestRunner = Callable[[Mapping[str, bytes], int], bytes]


class GradingWorkspaceError(RuntimeError):
    """The grading workspace could not be built; no candidate may be graded from it."""


@dataclass(frozen=True, slots=True)
class GradingOutcome:
    """One candidate graded once: the native result plus what proves where it came from."""

    run_identity: str
    task_digest: str
    candidate_digest: str
    evaluator_digest: str
    result: NativeGradeResult
    test_command: str
    #: True when the candidate edited a graded test file and the frozen test body replaced it.
    test_edit_overwritten: bool
    from_cache: bool


def candidate_digest_of(patch: str) -> str:
    return sha256_bytes(patch.encode("utf-8"))


def apply_repository_patch(
    workspace: Mapping[str, bytes], patch: str, *, prefix: str
) -> dict[str, bytes]:
    """Apply a repository-relative unified diff to a prefixed workspace, preserving the prefix.

    A patch addresses ``calculator/parser.py`` while the solve workspace keys are
    ``repo/calculator/parser.py``. Stripping the prefix, applying, and re-adding it keeps one
    representation, so the graded test overlay and the candidate patch are applied by the same code
    path and neither can silently diverge.

    An empty patch is not an error: a candidate that changes nothing is a legitimate submission the
    resolution measure grades as unresolved. Only a patch the engine cannot parse, or whose hunks
    do not apply, is refused.
    """
    stripped = {
        path.removeprefix(prefix): data
        for path, data in workspace.items()
        if path.startswith(prefix)
    }
    others = {path: data for path, data in workspace.items() if not path.startswith(prefix)}
    try:
        for change in patching.parse(patch):
            applied = patching.apply_one(stripped.get(change.path, b""), change)
            if applied is None:
                stripped.pop(change.path, None)
                continue
            stripped[change.path] = applied
    except PatchRejected as error:
        raise GradingWorkspaceError(str(error)) from error
    return {**others, **{f"{prefix}{path}": data for path, data in stripped.items()}}


def build_candidate_workspace(draft: NativeTaskDraft, *, candidate_patch: str) -> dict[str, bytes]:
    """The frozen snapshot with the candidate patch applied, before the hidden test overlay.

    A patch touching a path outside the frozen allowlist is rejected before anything is computed,
    so a candidate cannot edit the hidden test overlay, the expected test lists, or the issue
    statement.
    """
    enforce_patch_paths(candidate_patch, draft.allowed_change_paths)
    return apply_repository_patch(draft.visible_files(), candidate_patch, prefix="repo/")


def overlay_frozen_tests(
    draft: NativeTaskDraft, workspace: Mapping[str, bytes]
) -> tuple[dict[str, bytes], bool]:
    """Apply the hidden test patch over the candidate workspace.

    Returns the overlaid workspace and whether the overlay replaced a graded test file the
    candidate had edited: the frozen test body wins, so a candidate that rewrites the graded test
    still has to fix the source.
    """
    instance = draft.instance
    if not instance.test_patch:
        return dict(workspace), False
    overlaid = apply_repository_patch(workspace, instance.test_patch, prefix="repo/")
    overwritten = any(
        workspace.get(f"repo/{module}") is not None
        and workspace[f"repo/{module}"] != overlaid.get(f"repo/{module}")
        for module in graded_test_modules(instance)
    )
    return overlaid, overwritten


def grade_native_candidate(
    draft: NativeTaskDraft,
    *,
    candidate_patch: str,
    test_command: str,
    runner: TestRunner | None = None,
    cache: UpstreamRunCache | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> GradingOutcome:
    """Apply the candidate, overlay the frozen tests, run, and grade with the upstream evaluator."""
    from polycodebench_suites_swebench.adapter import test_spec_for

    task_digest = draft.package_digest
    candidate_digest = candidate_digest_of(candidate_patch)
    evaluator = evaluator_digest()
    identity = run_identity(
        task_digest=task_digest, candidate_digest=candidate_digest, evaluator_digest=evaluator
    )
    if cache is not None:
        hit = cache.get(
            task_digest=task_digest,
            candidate_digest=candidate_digest,
            evaluator_digest=evaluator,
        )
        if hit is not None:
            return GradingOutcome(
                run_identity=identity,
                task_digest=task_digest,
                candidate_digest=candidate_digest,
                evaluator_digest=evaluator,
                result=hit,
                test_command=test_command,
                test_edit_overwritten=False,
                from_cache=True,
            )

    # The leakage check belongs to the *solve* view: the hidden test patch is supposed to put the
    # fail-to-pass tests into the grading workspace, so checking it there would flag the task's own
    # design. What must hold is that the candidate's edits did not bring graded tests in with them.
    candidate_workspace = build_candidate_workspace(draft, candidate_patch=candidate_patch)
    leaks = check_instance_leakage(draft.instance, candidate_workspace)
    if leaks:
        raise GradingWorkspaceError(
            "candidate workspace leaked hidden material: " + "; ".join(leaks)
        )
    workspace, overwritten = overlay_frozen_tests(draft, candidate_workspace)
    log = (runner or run_tests_locally)(workspace, timeout_seconds)
    result = grade_log_bytes(
        spec=test_spec_for(draft),
        log_bytes=log,
        task_digest=task_digest,
        candidate_digest=candidate_digest,
        cache=cache,
    )
    return GradingOutcome(
        run_identity=identity,
        task_digest=task_digest,
        candidate_digest=candidate_digest,
        evaluator_digest=evaluator,
        result=result,
        test_command=test_command,
        test_edit_overwritten=overwritten,
        from_cache=False,
    )


def run_tests_locally(workspace: Mapping[str, bytes], timeout_seconds: int) -> bytes:
    """Fixture-tier runner: the task's own test command in a throwaway directory.

    This is *not* the production isolation path - the evaluation stage supplies a runner backed by
    the sandbox provider. It exists so admission of a local fixture can execute the same command
    the frozen protocol names, and its output is bracketed into the upstream log shape.
    """
    with tempfile.TemporaryDirectory(prefix="pcb-native-run-") as temp:
        root = Path(temp)
        for path, data in workspace.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        command, overrides = _repo_command(workspace)
        # The test command names repository paths, so it runs with the repository root as its
        # working directory: the solve workspace keeps ``repo/`` as a key prefix for bundle
        # separation, but the repository itself is the directory the command addresses.
        workdir = root / "repo"
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            **overrides,
        }
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                cwd=workdir,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            return _wrap_log("", "test command timed out", 124).encode("utf-8")
        body = completed.stdout.decode("utf-8", errors="replace")
        if not body.strip():
            body = completed.stderr.decode("utf-8", errors="replace")
        return _wrap_log(body, body, completed.returncode).encode("utf-8")


def _repo_command(workspace: Mapping[str, bytes]) -> tuple[list[str], dict[str, str]]:
    """The test command for a repository workspace, plus the environment it needs.

    The command names repository paths (``tests/test_x.py``) while the workspace keys carry the
    ``repo/`` prefix, so the prefix is stripped from every argument. The repository root goes on
    ``PYTHONPATH`` because a repository's own package must be importable by its tests; ``-E`` drops
    the supervisor's inherited environment and ``-s`` keeps the path set here, which ``-I`` would
    discard along with everything else. A command that addresses no graded module is refused:
    running it would collect nothing, and the upstream evaluator reads "no tests collected" as
    unresolved rather than as a task error.
    """
    modules = sorted(
        path.removeprefix("repo/")
        for path in workspace
        if path.startswith("repo/tests/") and path.endswith((".py", "_test.py", ".rs"))
    )
    if not modules:
        raise GradingWorkspaceError("the grading workspace contains no graded test module")
    # ``-rA`` is required, not cosmetic: the upstream pytest parser reads lines that begin with a
    # status word followed by the test id (``PASSED path::name``), which is the short-test-summary
    # format. Neither quiet mode nor plain verbose emits it, and without it the parser returns an
    # empty status map and grades every fail-to-pass test as unresolved.
    command = [
        sys.executable,
        "-E",
        "-s",
        "-B",
        "-m",
        "pytest",
        "-rA",
        "--tb=short",
        *modules,
    ]
    environment = {"PYTHONPATH": "repo", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    return command, environment


def _wrap_log(stdout: str, stderr: str, exit_code: int) -> str:
    body = stdout if stdout.strip() else stderr
    return f"{START_TEST_OUTPUT}\n{body}\n{END_TEST_OUTPUT}\n{TEST_EXIT_CODE}: {exit_code}\n"
