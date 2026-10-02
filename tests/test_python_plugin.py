"""Python language plugin: contracts, registry, plans, task validation and symbols (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polycodebench_core.models import ScoreDimension
from polycodebench_lang_python import PythonLanguagePlugin
from polycodebench_lang_python.taskspec import discover_cases, parse_oracle
from polycodebench_plugins_api import (
    AnalysisContext,
    DictArtifactReader,
    ExitSemantics,
    PerformancePlan,
    PluginAllowlist,
    RegistryError,
    ResourcePolicy,
    TaskDraft,
    ToolIdentity,
    assert_plan_allowed,
    load_allowlist,
    load_language_plugin,
)
from polycodebench_plugins_api.contracts import ExecutionPlan, WorkloadSpec
from pydantic import ValidationError
from python_plugin_support import ROOT, TOP_WORDS, draft, frozen, package_files

plugin = PythonLanguagePlugin()
ALLOWLIST = ROOT / "config" / "plugins" / "allowlist-v1.yaml"


def context(task=None, paths=("solution.py",)):  # type: ignore[no-untyped-def]
    return AnalysisContext(
        task=task or frozen(plugin), candidate_digest="sha256:" + "2" * 64, candidate_paths=paths
    )


# ----------------------------------------------------------------------- contracts


def test_exit_semantics_never_treat_an_undeclared_exit_as_clean() -> None:
    semantics = ExitSemantics(success=(0,), findings=(1,), error=(2,))
    assert semantics.classify(0, False) == "success"
    assert semantics.classify(1, False) == "findings"
    assert semantics.classify(2, False) == "error"
    assert semantics.classify(3, False) == "unexpected"
    assert semantics.classify(0, True) == "timeout"
    assert semantics.classify(None, False) == "timeout"
    with pytest.raises(ValidationError):
        ExitSemantics(success=(0,), findings=(0,))


def _plan_fields(**changes):  # type: ignore[no-untyped-def]
    ids = plugin.identities
    image = ids.evaluator
    tool = ToolIdentity(
        name="demo",
        version="1",
        image_digest=image.digest,
        lock_digest=image.lock_digest,
        rule_bundle_digest=None,
        advisory_snapshot_digest=None,
        parser_version="p1",
    )
    fields = {
        "plan_id": "demo.plan",
        "image": image.reference,
        "image_digest": image.digest,
        "argv": ("python", "-V"),
        "resources": ResourcePolicy(
            cpu_millis=1000,
            memory_bytes=256 * 1024**2,
            pids_limit=32,
            disk_bytes=64 * 1024**2,
            timeout_seconds=10,
            max_output_bytes=65536,
        ),
        "exit_semantics": ExitSemantics(success=(0,)),
        "tool": tool,
        "parser_id": "demo",
    }
    fields.update(changes)
    return fields


def test_plans_are_typed_argument_arrays_pinned_to_their_image() -> None:
    ExecutionPlan(**_plan_fields())
    for shell in ("sh", "/bin/bash", "cmd.exe"):
        with pytest.raises(ValidationError):
            ExecutionPlan(**_plan_fields(argv=(shell, "-c", "echo hi")))
    with pytest.raises(ValidationError):
        ExecutionPlan(**_plan_fields(image="python:3.12"))
    other = plugin.identities.runtime
    with pytest.raises(ValidationError):  # tool identity must name the plan's own image
        ExecutionPlan(**_plan_fields(image=other.reference, image_digest=other.digest))
    with pytest.raises(ValidationError):
        ExecutionPlan(**_plan_fields(environment={"lower": "x"}))


def test_performance_plan_requires_normalised_workload_weights() -> None:
    task = frozen(plugin)
    plan = plugin.performance_plan(task)
    assert isinstance(plan, PerformancePlan)
    assert sum(w.weight_bp for w in plan.workloads) == 10_000
    assert plan.warmup_iterations == 5 and plan.measured_iterations == 20 and plan.threads == 1
    assert "{scale}" in plan.iteration_plan.argv and "{seed}" in plan.iteration_plan.argv
    with pytest.raises(ValidationError):
        PerformancePlan(
            **{
                **plan.model_dump(),
                "workloads": (
                    WorkloadSpec(workload_id="only", scale=10, weight_bp=9000, input_seed=1),
                ),
                "iteration_plan": plan.iteration_plan,
            },
        )


# ------------------------------------------------------------------------ registry


def test_registry_loads_only_allowlisted_entry_points_and_images() -> None:
    allowlist = load_allowlist(ALLOWLIST)
    loaded = load_language_plugin(allowlist, "python")
    assert loaded.language_id == "python" and loaded.api_version == 1
    rust = load_language_plugin(allowlist, "rust")
    assert rust.language_id == "rust" and rust.api_version == 1
    java = load_language_plugin(allowlist, "java")
    assert java.language_id == "java" and java.api_version == 1
    plan = plugin.build_plan(frozen(plugin), _candidate())
    assert_plan_allowed(allowlist, "python", plan)
    tampered = _allowlist(allowlist, entry_point="evil.module:Plugin")
    with pytest.raises(RegistryError):
        load_language_plugin(tampered, "python")
    unapproved = _allowlist(allowlist, image_digests=["sha256:" + "9" * 64])
    with pytest.raises(RegistryError):
        assert_plan_allowed(unapproved, "python", plan)


def _allowlist(base: PluginAllowlist, **changes):  # type: ignore[no-untyped-def]
    entry = {**base.plugins[0].model_dump(mode="json"), **changes}
    document = {**base.model_dump(mode="json"), "plugins": [entry]}
    return PluginAllowlist.model_validate_json(json.dumps(document))


def _candidate():  # type: ignore[no-untyped-def]
    from polycodebench_core.identity import new_entity_id
    from polycodebench_core.models import Candidate

    return Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": "conformance-top-words",
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": "sha256:" + "3" * 64,
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )


# --------------------------------------------------------------------------- plans


def test_build_and_test_plans_for_the_fixture_task() -> None:
    task = frozen(plugin)
    build = plugin.build_plan(task, _candidate())
    assert build.argv[:3] == ("python", "-B", "/opt/pcb/guest/pcb_syntax_check.py")
    assert [i.role for i in build.inputs] == ["candidate"]
    tests = plugin.test_plan(task)
    assert [g.group_id for g in tests.groups] == ["behaviour", "properties", "streaming-memory"]
    assert [g.required for g in tests.groups] == [True, True, False]
    assert tests.expected_inventory_digest == task.inventory_digest
    first = tests.groups[0].plan
    assert "-p" in first.argv and "pcb_pytest_report" in first.argv
    assert first.environment["PYTHONPATH"] == "/opt/pcb/guest:/workspace/work"
    # candidate input comes from the candidate role; tests only from the trusted overlay
    roles = {i.path: i.role for i in first.inputs}
    assert (
        roles["work/solution.py"] == "candidate" and roles["tests/test_acceptance.py"] == "overlay"
    )
    assert first.exit_semantics.classify(1, False) == "findings"
    assert first.exit_semantics.classify(5, False) == "error"  # nothing collected is not a pass


def test_analysis_plans_follow_the_task_contract() -> None:
    base = plugin.analysis_plans(context())
    assert [p.analyzer_id for p in base] == ["ruff", "bandit", "semgrep", "context"]
    assert {p.analyzer_id for p in base if p.required} == {"ruff", "bandit", "context"}
    assert all(p.image_digest == plugin.identities.evaluator.digest for p in base)
    for p in base:  # offline guarantees: no installer, no network client, no shell
        assert p.resources.network == "none"
        assert not {"pip", "uv", "curl", "wget"} & set(p.argv)
    assert all(p.exit_semantics.classify(126, False) == "error" for p in base)
    assert all(p.exit_semantics.classify(127, False) == "error" for p in base)  # missing tool

    typed = frozen(
        plugin,
        quality={
            **frozen(plugin).quality,
            "typing_expectation": "required",
            "required_analyzers": ["ruff", "bandit", "context", "mypy"],
        },
        required_analyzers=("ruff", "bandit", "context", "mypy"),
    )
    plans = {p.analyzer_id: p for p in plugin.analysis_plans(context(typed))}
    assert "mypy-strict.ini" in " ".join(plans["mypy"].argv)
    assert plans["mypy"].required
    with_deps = frozen(plugin, dependency_inventory=("requests",))
    ids = [p.analyzer_id for p in plugin.analysis_plans(context(with_deps))]
    assert ids[-1] == "dependency"  # absent advisory snapshot must surface as a failed scan
    assert plans["bandit"].evidence_ownership == {
        "canonical-security-issue": ScoreDimension.SECURITY
    }


def test_every_planned_tool_has_a_recorded_identity() -> None:
    ids = plugin.identities
    for name in ("pytest", "ruff", "mypy", "bandit", "semgrep"):
        identity = ids.tool(name, image_kind="evaluator")
        assert identity.version == ids.evaluator.tools[name]
        assert identity.lock_digest == ids.evaluator.lock_digest
        assert identity.rule_bundle_digest == ids.rule_bundle_digest
    assert ids.runtime.python.startswith("Python 3.12")
    assert ids.build["offline_install"] is True and ids.build["network"] == "none"


# ------------------------------------------------------------------ task validation


def test_fixture_task_validates() -> None:
    report = plugin.validate_task(draft())
    assert report.ok, [i.message for i in report.issues]
    assert {"layout", "oracle-inventory", "exposure-rights"} <= set(report.checks)


def _mutated(edits: dict[str, bytes | None]):  # type: ignore[no-untyped-def]
    files: dict[str, bytes] = package_files()
    for path, data in edits.items():
        if data is None:
            files.pop(path, None)
        else:
            files[path] = data
    return draft(files=files)


def _codes(report) -> set[str]:  # type: ignore[no-untyped-def]
    return {i.code for i in report.issues if i.severity == "error"}


def test_validation_rejects_undeclared_and_missing_inventory_cases() -> None:
    extra = (
        package_files()["hidden/tests/test_acceptance.py"]
        + b"\n\ndef test_sneaked():\n    assert True\n"
    )
    assert "oracle-case-undeclared" in _codes(
        plugin.validate_task(_mutated({"hidden/tests/test_acceptance.py": extra}))
    )
    oracle = json.loads(package_files()["hidden/oracle.json"])
    oracle["groups"][0]["cases"].append(
        {
            "case_id": "tests/test_acceptance.py::test_ghost",
            "required": True,
            "case_kind": "example",
        }
    )
    codes = _codes(
        plugin.validate_task(_mutated({"hidden/oracle.json": json.dumps(oracle).encode()}))
    )
    assert "oracle-case-absent" in codes


def test_validation_rejects_missing_variants_records_and_unbacked_dimensions() -> None:
    assert "missing-file" in _codes(
        plugin.validate_task(_mutated({"admission/exposure-rights.json": None}))
    )
    assert "missing-reference" in _codes(
        plugin.validate_task(_mutated({"hidden/reference/solution.py": None}))
    )
    data = draft()
    manifest = json.loads(json.dumps(data.manifest))
    manifest["fixtures"] = [f for f in manifest["fixtures"] if f["variant"] != "timeout"]
    assert "variant-missing" in _codes(
        plugin.validate_task(
            TaskDraft(
                task_id=data.task_id, primary_language="python", manifest=manifest, files=data.files
            )
        )
    )
    manifest = json.loads(json.dumps(data.manifest))
    manifest["quality_plan"]["applicable_dimensions"].append("security")
    assert "security-without-surface" in _codes(
        plugin.validate_task(
            TaskDraft(
                task_id=data.task_id, primary_language="python", manifest=manifest, files=data.files
            )
        )
    )
    wrong = TaskDraft(
        task_id=data.task_id, primary_language="rust", manifest=data.manifest, files=data.files
    )
    assert "wrong-language" in _codes(plugin.validate_task(wrong))


def test_validation_rejects_an_inconsistent_acceptance_link() -> None:
    data = draft()
    manifest = json.loads(json.dumps(data.manifest))
    manifest["acceptance"]["required_test_group_ids"] = ["behaviour"]
    codes = _codes(
        plugin.validate_task(
            TaskDraft(
                task_id=data.task_id, primary_language="python", manifest=manifest, files=data.files
            )
        )
    )
    assert "acceptance-mismatch" in codes


def test_oracle_inventory_matches_static_discovery_of_the_hidden_tests() -> None:
    oracle = parse_oracle(package_files()["hidden/oracle.json"])
    discovered = {
        case
        for group in oracle.groups
        for rel in group.files
        for case in discover_cases(rel, package_files()[f"hidden/{rel}"])
    }
    declared = {case.case_id for group in oracle.groups for case in group.cases}
    assert declared == discovered and len(declared) == 10


# ----------------------------------------------------------------------- symbols


def test_symbol_index_is_static_and_reports_annotation_state() -> None:
    source = b"""
import os

LIMIT = 3
_hidden = 1


class Box:
    def put(self, item: int) -> None: ...
    def _private(self, item): ...


def run(a: int, b) -> int:
    return a


async def go() -> None: ...
"""
    index = plugin.symbols(
        DictArtifactReader(
            {"pkg/mod.py": source, "pkg/bad.py": b"def (:", "README.md": b"# not python"}
        )
    )
    by_name = {s.qualified_name: s for s in index.symbols}
    assert index.unparsed_paths == ("pkg/bad.py",)
    assert by_name["pkg.mod.Box"].symbol_kind == "class" and by_name["pkg.mod.Box"].public
    assert (
        by_name["pkg.mod.Box.put"].symbol_kind == "method" and by_name["pkg.mod.Box.put"].annotated
    )
    assert not by_name["pkg.mod.Box._private"].public
    assert by_name["pkg.mod.run"].annotated is False  # parameter b is unannotated
    assert by_name["pkg.mod.go"].annotated is True
    assert by_name["pkg.mod.LIMIT"].public and not by_name["pkg.mod._hidden"].public


def test_profile_versions_and_unknown_version_refusal() -> None:
    profile = plugin.profile("python-profile-v1")
    assert profile.duplicate_composite_penalty is False and profile.syntax_count_bonus is False
    assert sum(i.weight_bp for i in profile.diagnostic_items) == 10_000
    assert {i.item_id: i.weight_bp for i in profile.idiom_items} == {
        "iteration_laziness": 3000,
        "stdlib_api_choice": 3000,
        "data_protocol_modeling": 2500,
        "context_resource_abstraction": 1500,
    }
    with pytest.raises(ValueError):
        plugin.profile("python-profile-v0")


def test_top_words_fixture_lives_in_the_repository() -> None:
    assert (Path(TOP_WORDS) / "manifest.yaml").is_file()
