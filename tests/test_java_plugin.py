"""Java language-plugin plan, profile and frozen task tests."""

from __future__ import annotations

import json

from java_plugin_support import draft, identities
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_lang_java import JavaLanguagePlugin
from polycodebench_lang_java.parsers import PARSERS
from polycodebench_lang_java.taskspec import PerformanceDecl
from polycodebench_plugins_api import AnalysisContext, DictArtifactReader
from polycodebench_plugins_api.results import make_record, record_bytes

SOURCE = "src/main/java/demo/TopWords.java"


def _candidate(task_id: str) -> Candidate:
    return Candidate.model_validate(
        {
            "kind": "candidate",
            "schema_version": 1,
            "candidate_id": new_entity_id(),
            "run_id": new_entity_id(),
            "task_id": task_id,
            "task_version": 1,
            "sample_index": 0,
            "submission_kind": "source_bundle",
            "payload_digest": "sha256:" + "1" * 64,
            "artifact_ids": [],
            "frozen_at": None,
        }
    )


def test_java_freeze_build_test_and_analysis_use_shared_plan_contracts() -> None:
    plugin = JavaLanguagePlugin(identities())
    task = draft()
    view = plugin.freeze_view(task, "sha256:" + "2" * 64)
    assert view.primary_language == "java"
    assert view.required_outputs == (SOURCE,)
    assert set(plugin.trusted_inputs(task.files, view)) == {
        "work/pom.xml",
        "work/deps.lock.json",
    }

    build = plugin.build_plan(view, _candidate(view.task_id))
    assert build.argv[0:3] == ("python", "-B", "/opt/pcb/guest/pcb_java_run.py")
    assert build.exit_semantics.success == (0,)
    assert "--offline" in build.argv
    assert {item.path for item in build.inputs if item.role == "candidate"} == {f"work/{SOURCE}"}

    tests = plugin.test_plan(view)
    assert [group.group_id for group in tests.groups] == [
        "behaviour",
        "resource-probe",
        "concurrency-probe",
    ]
    assert next(arg for arg in tests.groups[0].plan.argv if arg.startswith("-Dtest=")) == (
        "-Dtest=demo.TopWordsTest"
    )
    behavior_xml = {
        output.path for output in tests.groups[0].plan.outputs if output.format == "junit_xml"
    }
    assert behavior_xml == {
        "work/target/surefire-reports/TEST-demo.TopWordsTest.xml",
    }
    assert tests.groups[1].plan.outputs[-1].format == "junit_xml"
    assert tests.groups[2].plan.outputs[-1].format == "junit_xml"
    assert [group.repetitions for group in tests.groups] == [1, 3, 3]
    assert tests.expected_inventory_digest == view.inventory_digest

    context = AnalysisContext(
        task=view,
        candidate_digest="sha256:" + "3" * 64,
        candidate_paths=(SOURCE,),
    )
    plans = plugin.analysis_plans(context)
    assert {plan.analyzer_id for plan in plans} == {
        "spotbugs",
        "pmd",
        "checkstyle",
        "context",
        "dependency",
    }
    dependency = next(plan for plan in plans if plan.analyzer_id == "dependency")
    assert dependency.tool.lock_digest == view.quality["dependency_lock_digest"]
    # Exit 1 means "the audit ran and found advisories", which is the same contract the Rust lock
    # audit and the Go module audit use. The advisory audit is a guest step that applies the pinned
    # snapshot, so it needs a findings exit; Maven's own exit alone cannot carry that meaning.
    assert dependency.exit_semantics.findings == (1,)
    assert dependency.exit_semantics.error
    assert all(plan.baseline_reusable is False for plan in plans)


def test_dependency_lock_drift_is_missing_evidence_not_a_clean_scan() -> None:
    plugin = JavaLanguagePlugin(identities())
    task = draft()
    view = plugin.freeze_view(task, "sha256:" + "8" * 64)
    plan = next(
        plan
        for plan in plugin.analysis_plans(
            AnalysisContext(
                task=view,
                candidate_digest="sha256:" + "9" * 64,
                candidate_paths=(SOURCE,),
            )
        )
        if plan.analyzer_id == "dependency"
    )
    outputs = {
        "out/dependency-list.run.json": b"{}",
        "out/dependency-audit.run.json": b"{}",
        "work/target/deps.txt": b"   org.example:dependency:jar:1.0:test -- module org.example\n",
        "out/dependency.json": json.dumps(
            {
                "schema": "pcb-dependency-audit-v1",
                "findings": [],
                "resolution_drift": ["org.example:dependency:1.0"],
            }
        ).encode(),
    }
    outputs["_execution.json"] = record_bytes(
        make_record(
            plan,
            exit_code=0,
            timed_out=False,
            duration_ms=5,
            stdout=b"",
            stderr=b"",
            isolation_tier="development",
            sandbox_id=new_entity_id(),
        )
    )
    observations = PARSERS["dependency"](
        DictArtifactReader(outputs), plan, plugin.profile("java-profile-v1")
    )
    scan = next(item for item in observations if item.check_id == "java.dependency.scan")
    assert scan.status.value == "missing"
    assert "frozen dependency lock" in scan.explanation


def test_java_profile_does_not_score_syntax_presence() -> None:
    plugin = JavaLanguagePlugin(identities())
    profile = plugin.profile("java-profile-v1")
    assert profile.language_id == "java"
    assert profile.effective_for_scoring is False
    assert profile.syntax_count_bonus is False
    assert profile.duplicate_composite_penalty is False
    scored_families = {item.equivalence_family for item in profile.rule_mappings}
    assert not ({"stream-present", "record-present", "solid-terminology"} & scored_families)
    assert plugin.language_profile.resolve("java.context.record-present") is None


def test_java_perf_plan_uses_one_frozen_mode_and_fixed_warmup() -> None:
    plugin = JavaLanguagePlugin(identities())
    task = draft()
    view = plugin.freeze_view(task, "sha256:" + "4" * 64)
    quality = dict(view.quality)
    performance = PerformanceDecl.model_validate(
        {
            "workload_file": "tests/demo/Workload.java",
            "workloads": (
                {"workload_id": "fixed", "scale": 3, "weight_bp": 10000, "input_seed": 17},
            ),
            "measurement_mode": "steady_state",
            "warmup_iterations": 20,
            "measured_iterations": 30,
        }
    )
    quality["performance"] = json.loads(performance.model_dump_json())
    measured_task = view.model_copy(update={"quality": quality})
    plan = plugin.performance_plan(measured_task)
    assert plan is not None
    assert plan.warmup_iterations == 20
    assert plan.measured_iterations == 30
    assert plan.iteration_plan.argv[0:3] == ("python", "-B", "/opt/pcb/guest/pcb_java_run.py")
    assert "-XX:+UseParallelGC" in plan.iteration_plan.argv
    assert "-XX:-UsePerfData" in plan.iteration_plan.argv
    assert plan.iteration_plan.environment == {}
    assert "HOME" not in plan.iteration_plan.environment
    assert "PCB_JVM_MEASUREMENT" not in plan.iteration_plan.environment
    assert plan.iteration_plan.tool.image_digest == plugin.identities.performance.digest
