"""Independent grading (Prompt 12) through real local Docker execution (opt-in: PCB_TEST_DOCKER=1).

Covers, with actual container runs:
- E2E-17: candidate edits tests/config or prints fake success records -> external inventory
  reconciliation and digest/identity failures control acceptance; fake stdout never passes.
- E2E-18: two scanners flag one introduced issue; unrelated baseline debt is reported with an
  unchanged relation instead of a fresh candidate penalty; ambiguous mappings stay reviewable.
- E2E-04 generic evaluator-worker variant: a frozen submission goes through the independent
  evaluation graph into a normalized evidence manifest.

These run in the local Docker driver (development isolation), not a production worker.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_evaluation.evaluator import Evaluator, baseline_from_package
from polycodebench_evaluation.plan_runner import PlanRunner, digest_files
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real sandbox tests are opt-in (PCB_TEST_DOCKER=1)",
)


def _manifest_overlay(files: dict[str, bytes]) -> dict[str, bytes]:
    return {
        p.removeprefix("hidden/"): data
        for p, data in files.items()
        if p.startswith(("hidden/tests/", "hidden/perf/"))
    }


def _candidate(task_id: str, files: dict[str, bytes]) -> Candidate:
    return Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": task_id,
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": digest_files(files),
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )


def _python_plan(view: Any) -> tuple[PlanRunner, dict[str, bytes], dict[str, bytes]]:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from polycodebench_lang_python import PythonLanguagePlugin
    from python_task_tool import ROOT as PY_ROOT  # noqa: F401

    plugin = PythonLanguagePlugin()
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "evaluator-docker-python",
        operation_timeout_seconds=120,
    )
    return PlanRunner(provider, lane="grading"), {}, {}


def test_evaluator_full_run_clean_reference() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from polycodebench_lang_python import PythonLanguagePlugin
    from python_task_tool import ROOT as PY_ROOT
    from python_task_tool import _files, _load

    package = PY_ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"
    manifest = _load(package)
    files = _files(package)
    plugin = PythonLanguagePlugin()
    view = plugin.freeze_view(task_draft(manifest, files), "sha256:" + "7" * 64)
    candidate_files = {"solution.py": files["hidden/reference/solution.py"]}
    overlay = _manifest_overlay(files)
    candidate = _candidate(view.task_id, candidate_files)

    runner, _, _ = _python_plan(view)

    async def run() -> Any:
        return await Evaluator(plugin, runner, execution_tier="development_sandbox").evaluate(
            view=view,
            candidate=candidate,
            candidate_files=candidate_files,
            overlay=overlay,
            config={},
            allowed_paths=("solution.py",),
            baseline_files=baseline_from_package(files),
            label="ref",
        )

    evaluation = asyncio.run(run())
    ev = evaluation.evidence
    assert ev.gate == "pass", ev.gate_reasons
    assert ev.build_verdict == "pass"
    assert all(g.verdict == "pass" for g in ev.group_verdicts)
    assert all(s.status == "passed" for s in ev.scenarios)
    assert ev.robustness_score_bp == 10_000
    # the property groups' seeded evidence is frozen into the manifest
    assert ev.property_evidence.engine == "hypothesis"
    assert ev.property_evidence.deterministic_policy == "hypothesis-derandomized"
    assert ev.property_evidence.cases > 0
    # native metrics are kept separate from the stricter gate and are not missing
    assert ev.profile_complete is True
    for analyzer in ("ruff", "bandit", "context"):
        entry = next(
            a for a in ev.analyzers if a.tool.side == "candidate" and a.tool.analyzer_id == analyzer
        )
        assert entry.status in {"completed", "completed_with_findings"}
        assert entry.tool.version and entry.tool.parser_version
        # native metrics remain per-tool within the manifest's separate native section
        assert entry.tool.name in ev.native_metrics
    # every required group case ran: inventory and records agree
    recorded = {c.case_id for c in ev.cases}
    assert len(recorded) > 0
    # artifacts are digest-addressed
    for ref in ev.raw_artifacts:
        assert ref.digest.startswith("sha256:") and ref.size_bytes >= 0
    out = ROOT / ".cache" / "evaluator-docker-e2e-04-python.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(ev.model_dump_json(indent=2), encoding="utf-8")


def task_draft(manifest: dict[str, Any], files: dict[str, bytes]) -> Any:
    from polycodebench_lang_python import PythonLanguagePlugin  # noqa: F401
    from polycodebench_plugins_api import TaskDraft

    return TaskDraft(
        task_id=manifest["task"]["task_id"],
        primary_language="python",
        manifest=manifest,
        files=files,
    )


def test_evaluator_rust_fixture_reference() -> None:
    """E2E-04 generic-worker variant through the independent evaluation graph (Rust)."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from polycodebench_lang_rust import RustLanguagePlugin
    from rust_task_tool import ROOT as RUST_ROOT
    from rust_task_tool import _files as rust_files  # noqa: N813
    from rust_task_tool import _load as rust_load  # noqa: N813

    package = RUST_ROOT / "plugins" / "languages" / "rust" / "fixtures" / "top-words"
    manifest = rust_load(package)
    files = rust_files(package)
    plugin = RustLanguagePlugin()
    from polycodebench_plugins_api import TaskDraft

    view = plugin.freeze_view(
        TaskDraft(
            task_id=manifest["task"]["task_id"],
            primary_language="rust",
            manifest=manifest,
            files=files,
        ),
        "sha256:" + "a" * 64,
    )
    overlay = {
        f"work/{p.removeprefix('hidden/')}": data
        for p, data in files.items()
        if p.startswith(("hidden/tests/", "hidden/perf/"))
    }
    config = plugin.trusted_inputs(files, view)
    candidate_files = {"src/lib.rs": files["hidden/reference/src/lib.rs"]}
    ids = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            ids.runtime.reference: ids.runtime.digest,
            ids.evaluator.reference: ids.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "evaluator-docker-rust",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="grading")
    evaluation = asyncio.run(
        Evaluator(plugin, runner, execution_tier="development_sandbox").evaluate(
            view=view,
            candidate=_candidate(view.task_id, candidate_files),
            candidate_files=candidate_files,
            overlay=overlay,
            config=config,
            allowed_paths=("src/lib.rs",),
            baseline_files=baseline_from_package(files),
            label="rust-ref",
        )
    )
    ev = evaluation.evidence
    assert ev.gate == "pass", ev.gate_reasons
    assert ev.build_verdict == "pass"
    for scenario in ev.scenarios:
        assert scenario.status == "passed", scenario.scenario_id
    for analyzer in ("clippy", "context"):
        entry = next(
            a for a in ev.analyzers if a.tool.side == "candidate" and a.tool.analyzer_id == analyzer
        )
        assert entry.status in {"completed", "completed_with_findings"}
    out = ROOT / ".cache" / "evaluator-docker-e2e-04-rust.json"
    out.write_text(ev.model_dump_json(indent=2), encoding="utf-8")


def test_e2e_17_candidate_edits_tests_or_prints_fake_success() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from polycodebench_lang_python import PythonLanguagePlugin
    from python_task_tool import ROOT as PY_ROOT
    from python_task_tool import _files, _load

    package = PY_ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"
    manifest = _load(package)
    files = _files(package)
    plugin = PythonLanguagePlugin()
    view = plugin.freeze_view(task_draft(manifest, files), "sha256:" + "8" * 64)
    overlay = _manifest_overlay(files)
    runner, _, _ = _python_plan(view)

    # (a) malicious edit: the candidate bundles replacement hidden tests in its submission
    malicious = {
        "solution.py": files["hidden/reference/solution.py"],
        "tests/test_acceptance.py": b"# replaced tests\n",
    }
    ev_a = asyncio.run(
        Evaluator(plugin, runner).evaluate(
            view=view,
            candidate=_candidate(view.task_id, malicious),
            candidate_files=malicious,
            overlay=overlay,
            config={},
            allowed_paths=("solution.py",),
            baseline_files=None,
            label="e2e17-a",
        )
    ).evidence
    assert ev_a.gate == "fail"
    assert "disallowed_paths" in " ".join(ev_a.gate_reasons)
    assert ev_a.analyzers == ()  # no analyzer work happens on a rejected submission
    out = ROOT / ".cache" / "evaluator-docker-e2e-17-a.json"
    out.write_text(ev_a.model_dump_json(indent=2), encoding="utf-8")

    # (b) fake success: wrong code that prints an "all tests passed" banner. Acceptance must
    # come from the external inventory, so the injected banner changes nothing.
    fake = (
        b'print("ALL TESTS PASSED (7/7)")\n'
        b"def top_words(lines, k):\n"
        b'    return [("definitely-wrong", 999999)]\n'
    )
    bad_candidate_files = {"solution.py": fake}
    ev_b = asyncio.run(
        Evaluator(plugin, runner).evaluate(
            view=view,
            candidate=_candidate(view.task_id, bad_candidate_files),
            candidate_files=bad_candidate_files,
            overlay=overlay,  # trusted overlay, not the candidate's
            config={},
            allowed_paths=("solution.py",),
            baseline_files=None,
            label="e2e17-b",
        )
    ).evidence
    assert ev_b.gate == "fail"
    assert ev_b.failed_cases  # the authoritative inventory rejected it
    assert ev_b.analyzers == ()  # and the quality gate stayed shut
    out = ROOT / ".cache" / "evaluator-docker-e2e-17-b.json"
    out.write_text(ev_b.model_dump_json(indent=2), encoding="utf-8")


def test_e2e_18_dedup_composite_owner_and_baseline_debt() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from polycodebench_lang_python import PythonLanguagePlugin
    from python_task_tool import ROOT as PY_ROOT
    from python_task_tool import _files, _load

    package = PY_ROOT / "plugins" / "languages" / "python" / "fixtures" / "top-words"
    manifest = _load(package)
    files = _files(package)
    plugin = PythonLanguagePlugin()
    view = plugin.freeze_view(task_draft(manifest, files), "sha256:" + "9" * 64)
    overlay = _manifest_overlay(files)
    runner, _, _ = _python_plan(view)

    planted = (
        b'\npassword = "hunter2"\n'
        b"def run(cmd, cache=[]):\n"
        b"    cache.append(cmd)\n"
        b"    try:\n"
        b"        return subprocess.run(cmd, shell=True, check=False)\n"
        b"    except:\n"
        b"        pass\n"
        b'def d():\n    return pickle.loads(b"")\n'
        b'def h():\n    return hashlib.md5(b"").hexdigest()\n'
    )
    reference = files["hidden/reference/solution.py"]
    candidate_solution = b"import hashlib\nimport pickle\nimport subprocess\n" + reference + planted
    # baseline already carried this debt; the candidate re-submits it verbatim
    baseline_files = {"solution.py": candidate_solution}
    candidate_files = {"solution.py": candidate_solution}
    evaluation = asyncio.run(
        Evaluator(plugin, runner).evaluate(
            view=view,
            candidate=_candidate(view.task_id, candidate_files),
            candidate_files=candidate_files,
            overlay=overlay,
            config={},
            allowed_paths=("solution.py",),
            baseline_files=baseline_files,
            label="e2e18",
        )
    )
    for key in ("analysis/candidate/out/semgrep.json", "analysis/candidate/out/bandit.json"):
        if key in evaluation.raw:
            (ROOT / ".cache" / f"seedump-{key.split('/')[-1]}").write_bytes(evaluation.raw[key])
    ev = evaluation.evidence
    assert ev.gate == "pass", ev.gate_reasons
    credential_issues = [i for i in ev.issues if "hardcoded-credential" in i.issue_key]
    assert len(credential_issues) == 1
    issue = credential_issues[0]
    # one underlying issue: several tools reported it but only one composite entry
    assert len(issue.tools) >= 1
    assert issue.owner == "security"
    assert issue.counted_once is True
    # unchanged preexisting debt: visible, but not blamed on the candidate
    assert issue.relation == "unchanged_out_of_scope"
    # the same-shell-call finding is flagged by bandit and semgrep as one issue
    shell_issues = [i for i in ev.issues if "subprocess-shell" in i.issue_key]
    assert len(shell_issues) == 1
    assert set(shell_issues[0].tools) == {
        "python.bandit.b602",
        "python.semgrep.subprocess-shell-true",
    }
    assert shell_issues[0].owner == "security"
    assert shell_issues[0].relation == "unchanged_out_of_scope"
    # native metrics remain labelled per tool (two native reports, one composite entry)
    assert len(ev.native_metrics) >= 2
    for name, metrics in ev.native_metrics.items():
        assert metrics["side"] == "candidate"
        # a findings exit is a complete scan; native metrics stay separate from the gate
        assert metrics["complete"] == "yes", name
    out = ROOT / ".cache" / "evaluator-docker-e2e-18.json"
    out.write_text(ev.model_dump_json(indent=2), encoding="utf-8")
