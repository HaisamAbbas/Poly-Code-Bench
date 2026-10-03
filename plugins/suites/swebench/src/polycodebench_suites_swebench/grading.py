"""The pinned upstream grading call, and the run identity that binds a result to its inputs.

PCB-24-2 requires the upstream evaluator rather than a re-derived test fraction, so this module
*calls* ``swebench.harness.grading.get_eval_report`` at a pinned revision and reads the returned
report. It never recomputes ``fail_to_pass``/``pass_to_pass`` itself: if the upstream package is
absent or the pinned revision does not match, the call raises rather than falling back to a local
approximation, because a locally "recreated" resolution number is exactly the defect the ticket
names.

PCB-24-3 requires that one candidate can never receive another candidate's cached grade. The run
identity below is derived from the task, candidate and evaluator digests, and a cached result is
returned only when every one of those digests matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol, cast

from polycodebench_core.canonical import canonical_digest, sha256_bytes

from polycodebench_suites_swebench.records import (
    FAIL_TO_PASS,
    PASS_TO_PASS,
    NativeGradeResult,
    NativeTestSpec,
)

#: The upstream revision this adapter is written against. A different installed version is a
#: recorded discrepancy, not something to silently accept: the resolution rule changed upstream
#: (skip semantics, suite-ran evidence, exit-code cross-checks), so a stale pin would make two
#: releases incomparable.
PINNED_EVALUATOR_PACKAGE = "swebench"
PINNED_EVALUATOR_REVISION = "5.0.2"

#: The upstream constants that bracket the parsed region of a test log.
_START_TEST_OUTPUT = ">>>>> Start Test Output"
_END_TEST_OUTPUT = ">>>>> End Test Output"


class UpstreamEvaluatorUnavailable(RuntimeError):
    """The pinned upstream evaluator is not importable; the native metric is unavailable."""


class UpstreamRevisionMismatch(RuntimeError):
    """The installed upstream package is not the pinned revision this adapter was written for."""


class _UpstreamGrading(Protocol):
    def get_eval_report(
        self,
        test_spec: Any,
        prediction: dict[str, str],
        test_log_path: str,
        include_tests_status: bool,
    ) -> dict[str, Any]: ...

    def get_resolution_status(self, report: dict[str, Any]) -> str: ...


@lru_cache(maxsize=1)
def upstream_revision() -> str:
    """The installed upstream package revision, or raise when it is absent."""
    try:
        return version(PINNED_EVALUATOR_PACKAGE)
    except PackageNotFoundError as error:
        raise UpstreamEvaluatorUnavailable(
            f"{PINNED_EVALUATOR_PACKAGE}=={PINNED_EVALUATOR_REVISION} is required to report the "
            + "native metric; install the pinned evaluator rather than approximating it"
        ) from error


def upstream_grading() -> _UpstreamGrading:
    """Import the pinned upstream grading module, refusing a different revision."""
    found = upstream_revision()
    if found != PINNED_EVALUATOR_REVISION:
        raise UpstreamRevisionMismatch(
            f"pinned evaluator is {PINNED_EVALUATOR_REVISION}, found {found}; the native "
            + "resolution rule is version-specific"
        )
    from swebench.harness import grading  # type: ignore[import-untyped]

    return cast("_UpstreamGrading", grading)


def evaluator_digest() -> str:
    """A digest of the grading surface this adapter depends on.

    It covers the upstream package revision and the upstream symbols the adapter calls, so an
    upstream patch release that changes ``get_eval_report`` semantics cannot silently reuse a
    cached grade under the same identity.
    """
    return str(
        canonical_digest(
            {
                "package": PINNED_EVALUATOR_PACKAGE,
                "revision": upstream_revision(),
                "entry_points": [
                    "swebench.harness.grading.get_eval_report",
                    "swebench.harness.grading.get_eval_tests_report",
                    "swebench.harness.grading.get_resolution_status",
                    "swebench.harness.grading.get_logs_eval",
                    "swebench.harness.grading.test_passed",
                    "swebench.harness.grading.test_maintained",
                    "swebench.harness.grading.test_failed",
                ],
            }
        )
    )


def run_identity(*, task_digest: str, candidate_digest: str, evaluator_digest: str) -> str:
    """The upstream run identity: a unique key per (task, candidate, evaluator) triple.

    Spec 17.2 requires this because upstream cache keys are weak: reusing one for a second
    candidate would return the first candidate's grade. Binding all three digests means a cache
    hit is only ever possible for the same task *and* the same candidate *and* the same evaluator.
    """
    return str(
        canonical_digest(
            {"task": task_digest, "candidate": candidate_digest, "evaluator": evaluator_digest}
        )
    )


@dataclass(slots=True)
class _CacheEntry:
    result: NativeGradeResult


class UpstreamRunCache:
    """A cache keyed strictly by upstream run identity.

    Deliberately not an LRU or a TTL cache: correctness of a scored release depends on a stored
    result being reachable *only* through its own identity, so the store is a plain dict and a
    lookup requires the full triple.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    def get(
        self,
        *,
        task_digest: str,
        candidate_digest: str,
        evaluator_digest: str,
    ) -> NativeGradeResult | None:
        entry = self._entries.get(
            run_identity(
                task_digest=task_digest,
                candidate_digest=candidate_digest,
                evaluator_digest=evaluator_digest,
            )
        )
        return entry.result if entry is not None else None

    def put(self, result: NativeGradeResult) -> None:
        self._entries[result.run_identity] = _CacheEntry(result)

    def __len__(self) -> int:
        return len(self._entries)


def wrap_test_log(stdout: str, stderr: str, exit_code: int | None) -> str:
    """Bracket the run output the way the upstream evaluation script does.

    The upstream parser slices the log between these two markers; without them ``get_logs_eval``
    reports "no results found" and the instance is silently graded unresolved.
    """
    body = stdout if stdout.strip() else stderr
    code = 0 if exit_code is None else exit_code
    return f"{_START_TEST_OUTPUT}\n{body}\n{_END_TEST_OUTPUT}\n>>>>>> Test Exit Code: {code}\n"


def upstream_test_spec(spec: NativeTestSpec) -> Any:
    """The upstream ``swebench.types.TestSpec`` that grading is written against.

    The grading code reads attributes off a real upstream dataclass, so the record's fields are
    copied into it verbatim under the names it expects (``FAIL_TO_PASS``/``PASS_TO_PASS``). Nothing
    is translated or renamed, which is what keeps its result the native measure rather than a
    re-interpretation of it.
    """
    from swebench.types import TestSpec as UpstreamTestSpec  # type: ignore[import-untyped]

    return UpstreamTestSpec(
        instance_id=spec.instance_id,
        image=spec.image,
        eval_script_list=[],
        repo=spec.repo,
        version=spec.version,
        FAIL_TO_PASS=list(spec.fail_to_pass),
        PASS_TO_PASS=list(spec.pass_to_pass),
        log_parser=spec.log_parser,
        eval_type=spec.eval_type,
    )


def native_grade(
    *,
    spec: NativeTestSpec,
    test_log: str,
    task_digest: str,
    candidate_digest: str,
    log_path: str,
    cache: UpstreamRunCache | None = None,
) -> NativeGradeResult:
    """Grade one candidate with the upstream evaluator and return the native measure.

    ``test_log`` must already be in upstream log shape (see :func:`wrap_test_log`). The result is
    cached only under its own run identity, and a caller that passes a different candidate digest
    gets a fresh evaluation rather than the previous candidate's grade.
    """
    grader = upstream_grading()
    evaluator = evaluator_digest()
    if cache is not None:
        hit = cache.get(
            task_digest=task_digest,
            candidate_digest=candidate_digest,
            evaluator_digest=evaluator,
        )
        if hit is not None:
            return hit

    upstream = upstream_test_spec(spec)
    report = grader.get_eval_report(
        upstream,
        {"instance_id": upstream.instance_id, "model_patch": "applied"},
        log_path,
        True,
    )[upstream.instance_id]

    statuses: dict[str, Any] = report.get("tests_status") or {}

    def _bucket(name: str, outcome: str) -> tuple[str, ...]:
        entry = statuses.get(name)
        if not isinstance(entry, dict):
            return ()
        values = entry.get(outcome, ())
        return tuple(str(item) for item in values) if isinstance(values, list) else ()

    f2p_success = _bucket(FAIL_TO_PASS, "success")
    f2p_failure = _bucket(FAIL_TO_PASS, "failure")
    p2p_success = _bucket(PASS_TO_PASS, "success")
    p2p_failure = _bucket(PASS_TO_PASS, "failure")
    resolution = grader.get_resolution_status(statuses) if statuses else None

    result = NativeGradeResult(
        instance_id=spec.instance_id,
        task_digest=task_digest,
        candidate_digest=candidate_digest,
        evaluator_digest=evaluator,
        run_identity=run_identity(
            task_digest=task_digest,
            candidate_digest=candidate_digest,
            evaluator_digest=evaluator,
        ),
        resolved=bool(report.get("resolved", False)),
        resolution_status="RESOLVED_NO" if resolution is None else str(resolution),
        fail_to_pass_success=f2p_success,
        fail_to_pass_failure=f2p_failure,
        pass_to_pass_success=p2p_success,
        pass_to_pass_failure=p2p_failure,
        patch_applied=bool(report.get("patch_successfully_applied", False)),
        infra_failure=bool(report.get("infra_failure", False)),
        infra_failure_reason=report.get("infra_failure_reason"),
        notes=(),
    )
    if cache is not None:
        cache.put(result)
    return result


def grade_log_bytes(
    *,
    spec: NativeTestSpec,
    log_bytes: bytes,
    task_digest: str,
    candidate_digest: str,
    cache: UpstreamRunCache | None = None,
) -> NativeGradeResult:
    """Write the wrapped log to a temp file and grade it, so the upstream file-based API is used."""
    import tempfile
    from pathlib import Path

    text = log_bytes.decode("utf-8", errors="replace")
    if _START_TEST_OUTPUT not in text:
        text = wrap_test_log(text, text, 0)
    with tempfile.TemporaryDirectory(prefix="pcb-native-grade-") as temp:
        log_path = str(Path(temp) / "test_output.txt")
        Path(log_path).write_text(text, encoding="utf-8")
        return native_grade(
            spec=spec,
            test_log=text,
            task_digest=task_digest,
            candidate_digest=candidate_digest,
            log_path=log_path,
            cache=cache,
        )


def test_log_digest(log_bytes: bytes) -> str:
    return sha256_bytes(log_bytes)
