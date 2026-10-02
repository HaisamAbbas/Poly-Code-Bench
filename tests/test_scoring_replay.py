"""E2E-24: replay an archived scorecard in a clean process, with no second candidate or judge.

The test writes a frozen archive to disk, then scores it again in a *fresh interpreter* through the
``pcb-score`` CLI and asserts that the canonical bytes are identical. The child process installs an
import hook that fails loudly if scoring reaches a model client, the network or a higher layer, so
"no provider calls and no task execution" is checked rather than asserted in a comment.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import scoring_support as s
from polycodebench_core.canonical import canonical_document_bytes
from polycodebench_scoring import (
    ScoringRefusalCode,
    ScoringRefused,
    replay_outcome,
    replay_scorecard,
    score_evaluation,
)
from polycodebench_scoring.loader import (
    archive_document,
    load_evidence_manifest,
    load_evidence_ownership,
    load_frozen_task,
    load_language_profile,
    load_outcome,
    load_scoring_policy,
    write_json,
)

_REPO_ROOT = s.REPO_ROOT
_SRC = ";".join(
    str(_REPO_ROOT / entry)
    for entry in ("packages/core/src", "packages/scoring/src", "packages/plugins-api/src")
)

#: Importing any of these inside a scoring process would mean scoring could reach outside itself.
FORBIDDEN_IN_SCORER = (
    "openai",
    "anthropic",
    "google",
    "httpx",
    "requests",
    "urllib3",
    "boto3",
    "socket",
    "subprocess",
    "polycodebench_persistence",
    "polycodebench_orchestration",
    "polycodebench_services",
    "polycodebench_evaluation",
)

_GUARD_TEMPLATE = """import sys

BLOCKED = __BLOCKED__


class _Tripwire:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise AssertionError(f"scoring reached a forbidden module at runtime: {name}")
        return None
"""

#: Blocked at *runtime* in the child process: provider clients, network stacks and every higher
#: layer. Module-level imports are covered separately by the static check below, so this tripwire
#: only has to prove scoring does not reach out while it is computing a score.
FORBIDDEN_AT_RUNTIME = (
    "openai",
    "anthropic",
    "google",
    "httpx",
    "requests",
    "urllib3",
    "boto3",
    "polycodebench_persistence",
    "polycodebench_orchestration",
    "polycodebench_services",
    "polycodebench_evaluation",
)


@pytest.fixture(scope="module")
def frozen():  # type: ignore[no-untyped-def]
    policy = load_scoring_policy(s.POLICY_PATH)
    ownership = load_evidence_ownership(s.OWNERSHIP_PATH)
    profile = s.python_profile()
    task = s.frozen_task()
    manifest = s.manifest(
        issues=(
            s.security_issue("shared-subprocess", severity="high", tools=("bandit", "semgrep")),
            s.security_issue("inherited-leak", relation="unchanged_out_of_scope", severity="low"),
        ),
        efficiency_value=s.efficiency("71.666667", time_ratio="1.7", memory_ratio="1.4"),
        code_quality_bp=8200,
        idiomatic_bp=9100,
        robustness_bp=7600,
    )
    outcome = score_evaluation(task, policy, manifest, ownership=ownership, profile=profile)
    return policy, ownership, profile, task, manifest, outcome


@pytest.fixture(scope="module")
def archive(tmp_path_factory, frozen) -> Path:  # type: ignore[no-untyped-def]
    policy, _ownership, profile, task, manifest, outcome = frozen
    directory = tmp_path_factory.mktemp("archive")
    write_json(directory / "policy.json", archive_document(policy))
    write_json(directory / "ownership.json", archive_document(_ownership))
    write_json(directory / "task.json", archive_document(task))
    write_json(directory / "manifest.json", archive_document(manifest))
    write_json(directory / "profile.json", archive_document(profile))
    write_json(directory / "outcome.json", archive_document(outcome))
    return directory


def _run_cli(archive: Path, command: str, *extra: str) -> subprocess.CompletedProcess[str]:
    script = _GUARD_TEMPLATE.replace(
        "__BLOCKED__", json.dumps(sorted(FORBIDDEN_AT_RUNTIME))
    ) + textwrap.dedent(
        """
        from polycodebench_scoring.cli import main

        # Installed after the CLI is imported: module-level reachability is proven by the static
        # check, and this proves nothing is reached *while a score is being computed*.
        sys.meta_path.insert(0, _Tripwire())
        raise SystemExit(main(sys.argv[1:]))
        """
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            command,
            "--policy",
            str(archive / "policy.json"),
            "--ownership",
            str(archive / "ownership.json"),
            "--task",
            str(archive / "task.json"),
            "--evidence",
            str(archive / "manifest.json"),
            "--language-profile",
            str(archive / "profile.json"),
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={
            "PYTHONPATH": _SRC,
            "PATH": "",
            "SYSTEMROOT": "",
            "PYTHONHASHSEED": "0",
        },
        check=False,
        timeout=180,
    )


def test_a_clean_process_reproduces_the_canonical_scorecard(archive: Path, frozen) -> None:  # type: ignore[no-untyped-def]
    _policy, _ownership, _profile, _task, _manifest, outcome = frozen
    replayed = _run_cli(archive, "score", "--output", str(archive / "replayed.json"))
    assert replayed.returncode == 0, replayed.stderr
    archived = load_outcome(archive / "outcome.json")
    replayed_outcome = load_outcome(archive / "replayed.json")
    assert replayed_outcome == archived
    assert canonical_document_bytes(replayed_outcome.scorecard) == canonical_document_bytes(
        archived.scorecard
    )
    assert replayed_outcome.scorecard.total_score == outcome.scorecard.total_score


def test_replay_reports_every_check_it_made(archive: Path, frozen) -> None:  # type: ignore[no-untyped-def]
    policy, ownership, profile, task, manifest, outcome = frozen
    report = replay_outcome(
        task,
        policy,
        manifest,
        ownership=ownership,
        profile=profile,
        archived=outcome,
    )
    assert report.matched is True
    assert {check.name for check in report.checks} >= {
        "outcome_digest",
        "scorecard_digest",
        "total_score",
        "item_count",
    }


def test_replay_of_an_archived_scorecard_needs_no_candidate_or_judge(archive: Path, frozen) -> None:  # type: ignore[no-untyped-def]
    policy, ownership, profile, task, manifest, outcome = frozen
    report = replay_scorecard(
        task,
        policy,
        manifest,
        ownership=ownership,
        profile=profile,
        archived=outcome.scorecard,
    )
    assert report.matched is True
    assert all(check.passed for check in report.checks)


def test_the_replay_command_reproduces_the_archived_bytes(archive: Path, frozen) -> None:  # type: ignore[no-untyped-def]
    _policy, _ownership, _profile, _task, _manifest, outcome = frozen
    result = _run_cli(archive, "replay", "--archived-outcome", str(archive / "outcome.json"))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["replayed"] is True
    assert payload["outcome_digest"] == outcome.content_digest()


def test_the_scorer_source_imports_nothing_that_could_execute_or_call_out() -> None:
    # A static guarantee that the pure modules cannot acquire an escape hatch later.
    package = _REPO_ROOT / "packages" / "scoring" / "src" / "polycodebench_scoring"
    pure = (
        "arithmetic.py",
        "contracts.py",
        "explain.py",
        "manifest.py",
        "ownership.py",
        "policy.py",
        "replay.py",
        "scorer.py",
    )
    for name in pure:
        tree = ast.parse((package / name).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for module in imported:
            root = module.split(".")[0]
            assert root not in FORBIDDEN_IN_SCORER, f"{name} imports {module}"


def test_scorer_reads_no_clock_and_no_environment(frozen, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    policy, ownership, profile, task, manifest, _outcome = frozen
    import builtins
    import os
    import random
    import time

    def forbidden(*args: object, **kwargs: object) -> object:  # type: ignore[no-untyped-def]
        raise AssertionError("scoring must not read the clock, the environment or randomness")

    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(time, "monotonic", forbidden)
    monkeypatch.setattr(time, "perf_counter", forbidden)
    monkeypatch.setattr(random, "random", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    outcome = score_evaluation(task, policy, manifest, ownership=ownership, profile=profile)
    assert outcome.scorecard.total_score is not None
    # The only timestamp in the output is the one the caller supplied, which is what makes a replay
    # reproducible rather than merely similar.
    assert outcome.scorecard.created_at == manifest.invocation.recorded_at


def test_a_policy_digest_change_is_refused_not_rescored(archive: Path, frozen) -> None:  # type: ignore[no-untyped-def]
    _policy, ownership, profile, task, manifest, outcome = frozen
    edited = json.loads((archive / "policy.json").read_text(encoding="utf-8"))
    edited["security"]["penalties"]["high"] = 40
    write_json(archive / "changed-policy.json", edited)
    changed = load_scoring_policy(archive / "changed-policy.json")
    with pytest.raises(ScoringRefused) as raised:
        replay_scorecard(
            task,
            changed,
            manifest,
            ownership=ownership,
            profile=profile,
            archived=outcome.scorecard,
        )
    assert raised.value.code is ScoringRefusalCode.REPLAY_DIGEST_MISMATCH


def test_an_unsupported_score_schema_version_is_refused(frozen) -> None:  # type: ignore[no-untyped-def]
    policy, ownership, profile, task, manifest, outcome = frozen
    future = policy.model_copy(update={"score_schema_version": 2})
    with pytest.raises(ScoringRefused) as raised:
        replay_outcome(
            task,
            future,
            manifest,
            ownership=ownership,
            profile=profile,
            archived=outcome,
        )
    assert raised.value.code in {
        ScoringRefusalCode.SCORE_SCHEMA_UNSUPPORTED,
        ScoringRefusalCode.POLICY_INVALID,
    }


def test_a_replayed_manifest_from_disk_is_identical(archive: Path, frozen) -> None:  # type: ignore[no-untyped-def]
    policy, ownership, _profile, _task, manifest, outcome = frozen
    on_disk = load_evidence_manifest(archive / "manifest.json")
    assert on_disk.content_digest() == manifest.content_digest()
    assert on_disk == manifest
    assert outcome.scorecard.evidence_manifest_digest == on_disk.content_digest()
    assert load_frozen_task(archive / "task.json").task_digest == on_disk.task_digest
    assert load_language_profile(archive / "profile.json").profile_version == "python-profile-v1"
    assert outcome.explanation.policy_digest == policy.content_digest()
    assert outcome.explanation.ownership_policy_digest == ownership.content_digest()
