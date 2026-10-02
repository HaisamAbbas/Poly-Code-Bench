"""Prompt 18 sandbox proofs (authored internal examples, never model benchmark results)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from polycodebench_core.canonical import canonical_document_digest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_core.solve_extraction import Finding
from polycodebench_evaluation.evaluator import Evaluator
from polycodebench_evaluation.plan_runner import PlanRunner, digest_files
from polycodebench_evaluation.track_a import (
    MatchEdge,
    OracleBug,
    RepairResult,
    build_authored_history_source,
    build_injected_source,
    build_mutation_source,
    evaluate_repair,
    mutate_source,
    score_detection,
    validate_source_admission,
)
from polycodebench_plugins_api import ExecutionPlan, ExitSemantics, ResourcePolicy
from polycodebench_runner.provider import LocalDockerSandboxProvider

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "taskpacks" / "track-a" / "internal-examples"
pytestmark = pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="real Docker integration tests are opt-in (PCB_TEST_DOCKER=1)",
)


def _write_evidence(name: str, body: dict[str, Any]) -> None:
    if os.environ.get("PCB_WRITE_EVIDENCE") != "1":
        return
    path = ROOT / "docs" / "implementation" / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _policy(*, timeout: int = 120, executable_workspace: bool = False) -> ResourcePolicy:
    return ResourcePolicy(
        cpu_millis=1500,
        memory_bytes=1024 * 1024**2,
        pids_limit=128,
        disk_bytes=192 * 1024**2,
        timeout_seconds=timeout,
        max_output_bytes=65536,
        executable_workspace=executable_workspace,
    )


def _python_run(source: bytes, *, label: str) -> Any:
    from polycodebench_lang_python import PythonLanguagePlugin

    plugin = PythonLanguagePlugin()
    identity = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={identity.runtime.reference: identity.runtime.digest},
        state_dir=ROOT / ".cache" / "track-a-python",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    plan = ExecutionPlan(
        plan_id=f"tracka.python.{label}",
        image=identity.runtime.reference,
        image_digest=identity.runtime.digest,
        argv=(
            "python",
            "-I",
            "-B",
            "-c",
            "import sys; sys.path.insert(0, 'work'); import test_path_guard; "
            "test_path_guard.test_sibling_prefix_is_outside_root()",
        ),
        environment={"PYTHONDONTWRITEBYTECODE": "1"},
        resources=_policy(),
        exit_semantics=ExitSemantics(success=(0,), error=(1, 2)),
        tool=identity.tool("python", image_kind="runtime"),
        parser_id="tracka.python.reproducer.v1",
    )
    files = {
        "work/path_guard.py": source,
        "work/test_path_guard.py": (EXAMPLES / "python" / "test_path_guard.py").read_bytes(),
    }
    assert all("injection-log" not in path for path in files)
    return asyncio.run(runner.run(plan, files, stage_id=f"tracka-python-{label}")).record


def _rust_runs(sources: dict[str, bytes]) -> dict[str, Any]:
    """Reuse one pinned Rust guest while keeping each Cargo test a fresh process."""
    from polycodebench_lang_rust import RustLanguagePlugin

    plugin = RustLanguagePlugin()
    identity = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={identity.runtime.reference: identity.runtime.digest},
        state_dir=ROOT / ".cache" / "track-a-rust",
        operation_timeout_seconds=120,
    )
    runner = PlanRunner(provider, lane="admission")
    plan = ExecutionPlan(
        plan_id="tracka.rust.reproducer-set",
        image=identity.runtime.reference,
        image_digest=identity.runtime.digest,
        argv=("cargo", "--version"),
        environment={"CARGO_TARGET_DIR": "work/target"},
        resources=_policy(timeout=150, executable_workspace=True),
        exit_semantics=ExitSemantics(success=(0,), error=(1, 101)),
        tool=identity.tool("cargo", recipe="runtime"),
        parser_id="tracka.rust.reproducer.v1",
    )
    template = (EXAMPLES / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    files: dict[str, bytes] = {}
    for label, source in sources.items():
        package = "pcb_track_a_" + label.replace("-", "_")
        cargo = template.replace("pcb_track_a_path_guard_internal", package)
        prefix = f"work/{label}/"
        files[prefix + "Cargo.toml"] = cargo.encode("utf-8")
        files[prefix + "src/lib.rs"] = source
    assert all("injection-log" not in path for path in files)

    async def run_all() -> dict[str, Any]:
        records: dict[str, Any] = {}
        async with runner.reserved_guest(
            plan, files, stage_id="tracka-rust-reproducer-set"
        ) as guest:
            for label in sources:
                records[label] = await guest.execute(
                    plan,
                    (
                        "cargo",
                        "test",
                        "--offline",
                        "--manifest-path",
                        f"work/{label}/Cargo.toml",
                        "--quiet",
                    ),
                )
        return records

    return asyncio.run(run_all())


def test_authored_pre_fix_injected_and_reference_repair_run_in_python_and_rust() -> None:
    python_dir = EXAMPLES / "python"
    py_bad = (python_dir / "path_guard_pre_fix.py").read_bytes()
    py_fixed = (python_dir / "path_guard_fixed.py").read_bytes()
    py_mutant, py_record = mutate_source(
        py_fixed,
        path="path_guard.py",
        old=b"base in child.parents",
        new=b"child.as_posix().startswith(base.as_posix())",
        operator="path-prefix-mutation",
        operator_version="1",
        seed=1801,
        behavior_change_proof="will-bind-to-docker-record-after-reproduction",
        reference_repair_proof="authored-python-fixed-v1",
        build_proof="python-runtime-pytest-v1",
    )
    assert py_record.mutated_digest == "sha256:" + hashlib.sha256(py_mutant).hexdigest()

    rust_dir = EXAMPLES / "rust"
    rs_bad = (rust_dir / "src" / "lib.rs").read_bytes()
    rs_fixed = (rust_dir / "src" / "lib_fixed.rs").read_bytes()
    rs_mutant, rs_record = mutate_source(
        rs_fixed,
        path="src/lib.rs",
        old=b"candidate.starts_with(root)",
        new=b"candidate.to_string_lossy().starts_with(&root.to_string_lossy())",
        operator="path-prefix-mutation",
        operator_version="1",
        seed=1802,
        behavior_change_proof="will-bind-to-docker-record-after-reproduction",
        reference_repair_proof="authored-rust-fixed-v1",
        build_proof="rust-runtime-cargo-test-v1",
    )
    assert rs_record.mutated_digest == "sha256:" + hashlib.sha256(rs_mutant).hexdigest()

    py_base_record = _python_run(py_bad, label="history-pre-fix")
    py_injected_record = _python_run(py_mutant, label="mutation-from-reference")
    py_fixed_record = _python_run(py_fixed, label="history-reference-fix")
    rust_records = _rust_runs(
        {
            "history-pre-fix": rs_bad,
            "mutation-from-reference": rs_mutant,
            "history-reference-fix": rs_fixed,
        }
    )
    rs_base_record = rust_records["history-pre-fix"]
    rs_injected_record = rust_records["mutation-from-reference"]
    rs_fixed_record = rust_records["history-reference-fix"]
    assert py_base_record.exit_code != 0 and py_injected_record.exit_code != 0
    assert py_fixed_record.exit_code == 0
    assert rs_base_record.exit_code != 0 and rs_injected_record.exit_code != 0
    assert rs_fixed_record.exit_code == 0
    py_mutant, py_record = mutate_source(
        py_fixed,
        path="path_guard.py",
        old=b"base in child.parents",
        new=b"child.as_posix().startswith(base.as_posix())",
        operator="path-prefix-mutation",
        operator_version="1",
        seed=1801,
        behavior_change_proof=canonical_document_digest(py_injected_record),
        reference_repair_proof=canonical_document_digest(py_fixed_record),
        build_proof=canonical_document_digest(py_injected_record),
    )
    rs_mutant, rs_record = mutate_source(
        rs_fixed,
        path="src/lib.rs",
        old=b"candidate.starts_with(root)",
        new=b"candidate.to_string_lossy().starts_with(&root.to_string_lossy())",
        operator="path-prefix-mutation",
        operator_version="1",
        seed=1802,
        behavior_change_proof=canonical_document_digest(rs_injected_record),
        reference_repair_proof=canonical_document_digest(rs_fixed_record),
        build_proof=canonical_document_digest(rs_injected_record),
    )
    assert py_record.mutated_digest == "sha256:" + hashlib.sha256(py_mutant).hexdigest()
    assert rs_record.mutated_digest == "sha256:" + hashlib.sha256(rs_mutant).hexdigest()
    reproducers = {
        "python": {"test_path_guard.py": (EXAMPLES / "python" / "test_path_guard.py").read_bytes()},
        "rust": {"src/lib.rs": rs_bad},
    }
    source_examples: dict[str, Any] = {}
    for language, pre_fix, fixed, mutant, mutation, base_record, injected_record, fixed_record in (
        (
            "python",
            {"path_guard.py": py_bad},
            {"path_guard.py": py_fixed},
            {"path_guard.py": py_mutant},
            py_record,
            py_base_record,
            py_injected_record,
            py_fixed_record,
        ),
        (
            "rust",
            {"src/lib.rs": rs_bad},
            {"src/lib.rs": rs_fixed},
            {"src/lib.rs": rs_mutant},
            rs_record,
            rs_base_record,
            rs_injected_record,
            rs_fixed_record,
        ),
    ):
        clean_control = f"{language}-path-component-clean-v1"
        history = build_authored_history_source(
            language=language,
            repository_id=f"track-a/{language}/path-prefix",
            issue_id=f"internal-path-prefix-{language}",
            pre_fix=pre_fix,
            reference_fix=fixed,
            reproducer=reproducers[language],
            clean_control_id=clean_control,
            rights_record_id="internal-authored-rights-v1",
            authorship_record_id="polycodebench-internal-authorship-v1",
            rights_verified=True,
            evidence_verified=True,
        )
        mutation_log = mutation.model_dump_json().encode("utf-8")
        proof = canonical_document_digest(injected_record)
        injected = build_injected_source(
            language=language,
            task_id=f"track-a-{language}-path-prefix-injected",
            injected_source=mutant,
            reference_fix=fixed,
            reproducer=reproducers[language],
            private_injection_log=mutation_log,
            clean_control_id=clean_control,
            rights_record_id="internal-authored-rights-v1",
            authorship_record_id="polycodebench-internal-authorship-v1",
            behavior_change_proof=proof,
            build_proof=proof,
            rights_verified=True,
            evidence_verified=True,
        )
        mutated = build_mutation_source(
            language=language,
            task_id=f"track-a-{language}-path-prefix-mutated",
            mutated_source=mutant,
            reference_fix=fixed,
            reproducer=reproducers[language],
            private_mutation_log=mutation_log,
            clean_control_id=clean_control,
            rights_record_id="internal-authored-rights-v1",
            authorship_record_id="polycodebench-internal-authorship-v1",
            mutation=mutation,
            rights_verified=True,
            evidence_verified=True,
        )
        assert validate_source_admission(history).admitted
        assert validate_source_admission(injected).admitted
        assert validate_source_admission(mutated).admitted
        source_examples[language] = {
            "authored_history": history.model_dump(mode="json"),
            "injected": injected.model_dump(mode="json"),
            "mutation": mutated.model_dump(mode="json"),
            "mutation_record": mutation.model_dump(mode="json"),
            "executions": {
                "pre_fix_reproduces_failure": {
                    "digest": canonical_document_digest(base_record),
                    "record": base_record.model_dump(mode="json"),
                },
                "mutated_defect_reproduces_failure": {
                    "digest": canonical_document_digest(injected_record),
                    "record": injected_record.model_dump(mode="json"),
                },
                "reference_fix_passes": {
                    "digest": canonical_document_digest(fixed_record),
                    "record": fixed_record.model_dump(mode="json"),
                },
            },
        }
    _write_evidence(
        "prompt-18-source-reproduction.json",
        {
            "schema_version": 1,
            "kind": "prompt18_source_reproduction_evidence",
            "evidence_tier": "development_sandbox",
            "claims_model_benchmark_results": False,
            "fixture_status": "authored_internal_only",
            "injection_log_visibility": "hidden; only its digest is recorded here",
            "languages": source_examples,
        },
    )


def test_e2e_34_combined_patch_uses_fresh_candidate_and_independent_evaluator() -> None:
    from polycodebench_lang_python import PythonLanguagePlugin
    from polycodebench_plugins_api import TaskDraft

    sys.path.insert(0, str(ROOT / "scripts"))
    from python_task_tool import _files, _load

    package = EXAMPLES.parents[2] / "plugins" / "languages" / "python" / "fixtures" / "top-words"
    manifest = _load(package)
    package_files = _files(package)
    plugin = PythonLanguagePlugin()
    view = plugin.freeze_view(
        TaskDraft(
            task_id=manifest["task"]["task_id"],
            primary_language="python",
            manifest=manifest,
            files=package_files,
        ),
        "sha256:" + "9" * 64,
    )
    identity = plugin.identities
    provider = LocalDockerSandboxProvider(
        allowed_images={
            identity.runtime.reference: identity.runtime.digest,
            identity.evaluator.reference: identity.evaluator.digest,
        },
        state_dir=ROOT / ".cache" / "track-a-evaluator-python",
        operation_timeout_seconds=120,
    )
    evaluator = Evaluator(plugin, PlanRunner(provider, lane="grading"))
    faulty = {"solution.py": b"def top_words(lines, k):\n    return []\n"}
    patch = (
        "--- a/solution.py\n+++ b/solution.py\n@@ -2 +2 @@\n"
        '-    return []\n+    return [("wrong", 1)]\n'
    )
    candidate = Candidate.model_validate(
        {
            "schema_version": 1,
            "kind": "candidate",
            "candidate_id": new_entity_id(),
            "run_id": new_entity_id(),
            "task_id": view.task_id,
            "task_version": view.task_version,
            "sample_index": 0,
            "submission_kind": "findings",
            "payload_digest": digest_files(faulty),
            "artifact_ids": [],
            "frozen_at": None,
        }
    )
    overlay = {
        path.removeprefix("hidden/"): data
        for path, data in package_files.items()
        if path.startswith(("hidden/tests/", "hidden/perf/"))
    }
    reported_finding = Finding.model_validate(
        {
            "local_id": "good-detection",
            "path": "solution.py",
            "start_line": 2,
            "end_line": 2,
            "root_cause": "the result list omits every qualifying word",
            "evidence": "independent test reproduces the missing output",
            "severity": "high",
        }
    )
    known_bug = OracleBug(
        bug_id="top-words-missing-results",
        path="solution.py",
        causal_start_line=2,
        causal_end_line=2,
        function_start_line=1,
        function_end_line=2,
        taxonomy="missing results",
        trigger="a call requests the top k words from nonempty input",
        incorrect_behavior="the implementation returns an empty list",
        mechanism="the function discards all counted terms",
        consequence="callers receive no requested results",
        accepted_severities=("high",),
    )
    accepted_edge = MatchEdge(
        finding_id=reported_finding.local_id,
        bug_id=known_bug.bug_id,
        decision="accepted",
        causal_equivalent=True,
        independent_evidence_ref="fixture:hidden-test-report",
        reviewer_id="fixture-reviewer",
        review_rationale="The hidden test and source establish the exact failed behavior.",
        explanation_facts=(10000, 10000, 10000, 10000),
        evidence_id="fixture:review-packet",
    )
    detection = score_detection(
        findings=(reported_finding,),
        bugs=(known_bug,),
        edges=(accepted_edge,),
    )

    async def run() -> tuple[RepairResult, Any]:
        return await evaluate_repair(
            evaluator=evaluator,
            view=view,
            candidate=candidate,
            base_files=faulty,
            patch=patch,
            overlay=overlay,
            config={},
            allowed_paths=("solution.py",),
            protected_paths=tuple(view.protected_paths),
            repair_required=True,
            code_score_bp=5000,
            label="track-a-e2e34-bad-final-patch",
        )

    repair, evaluation = asyncio.run(run())
    assert detection.status == "complete" and detection.true_positive == 1
    assert evaluation is not None and evaluation.evidence.gate == "fail"
    assert repair.gate == "fail" and repair.composite_score_bp == 0
    assert repair.evaluation_digest == canonical_document_digest(evaluation.evidence)
    _write_evidence(
        "prompt-18-e2e-34.json",
        {
            "schema_version": 1,
            "kind": "prompt18_e2e34_internal_integration_evidence",
            "evidence_tier": "development_sandbox",
            "claims_model_benchmark_results": False,
            "candidate_kind": "authored_fixture",
            "candidate_digest": candidate.payload_digest,
            "final_patch_digest": "sha256:" + hashlib.sha256(patch.encode()).hexdigest(),
            "detection": detection.model_dump(mode="json"),
            "repair": repair.model_dump(mode="json"),
            "evaluation": evaluation.evidence.model_dump(mode="json"),
        },
    )
