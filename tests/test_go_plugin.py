"""Go language plugin tests: validation, the frozen view, plans, symbols and applicability.

Everything here runs offline against the recorded image identities and the authored fixture
package; real tool execution lives in ``tests/test_go_docker.py``.
"""

from __future__ import annotations

import json

import pytest
from go_plugin_support import TOP_WORDS, draft, frozen, manifest, package_files
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, MeasurementStatus
from polycodebench_lang_go import GoLanguagePlugin
from polycodebench_lang_go import plans as go_plans
from polycodebench_lang_go.identities import load_identities
from polycodebench_lang_go.taskspec import discover_cases
from polycodebench_plugins_api import AnalysisContext, DictArtifactReader

CANDIDATE = "topwords/topwords.go"


@pytest.fixture(scope="module")
def plugin() -> GoLanguagePlugin:
    return GoLanguagePlugin()


def candidate(view) -> Candidate:  # type: ignore[no-untyped-def]
    return Candidate.model_validate(
        {
            "schema_version": 1,
            "kind": "candidate",
            "candidate_id": new_entity_id(),
            "run_id": new_entity_id(),
            "task_id": view.task_id,
            "task_version": 1,
            "sample_index": 0,
            "submission_kind": "source_bundle",
            "payload_digest": "sha256:" + "0" * 64,
            "artifact_ids": [],
            "frozen_at": None,
        }
    )


def context(view):  # type: ignore[no-untyped-def]
    return AnalysisContext(
        task=view, candidate_digest="sha256:" + "4" * 64, candidate_paths=(CANDIDATE,)
    )


def concurrent_view(plugin: GoLanguagePlugin, view):  # type: ignore[no-untyped-def]
    """The same frozen task read as concurrent: a required race run over a frozen opportunity."""
    quality = {
        **view.quality,
        "race": "required",
        "opportunities": {**view.quality["opportunities"], "goroutines_channels": 1},
        "required_analyzers": (*view.quality["required_analyzers"], "race"),
    }
    return view.model_copy(
        update={"quality": quality, "required_analyzers": (*view.required_analyzers, "race")}
    )


# ------------------------------------------------------------------------------ validation


def test_recorded_image_tools_identify_go_vet_and_gosec() -> None:
    identities = load_identities()
    for record in identities.images.values():
        assert record.tools["go-vet"] == record.go
    assert identities.evaluator.tools["gosec"] == "v2.29.0"


def test_the_authored_fixture_validates(plugin: GoLanguagePlugin) -> None:
    report = plugin.validate_task(draft())
    assert report.ok, [issue.model_dump() for issue in report.issues]
    assert "module-identity" in report.checks
    assert "oracle-inventory" in report.checks


def test_a_wrong_primary_language_is_refused(plugin: GoLanguagePlugin) -> None:
    data = draft()
    wrong = data.model_copy(update={"primary_language": "rust"})
    assert "wrong-language" in {issue.code for issue in plugin.validate_task(wrong).issues}


def test_a_missing_module_file_is_refused(plugin: GoLanguagePlugin) -> None:
    files = {k: v for k, v in package_files().items() if k != "visible/repo/go.mod"}
    report = plugin.validate_task(draft(files=files))
    assert "missing-file" in {issue.code for issue in report.issues}


def test_an_undeclared_oracle_case_is_refused(plugin: GoLanguagePlugin) -> None:
    files = dict(package_files())
    files["hidden/topwords/behaviour_test.go"] += b"\nfunc TestUndeclared(t *testing.T) {}\n"
    report = plugin.validate_task(draft(files=files))
    assert "oracle-case-undeclared" in {issue.code for issue in report.issues}


def test_an_invented_oracle_case_is_refused(plugin: GoLanguagePlugin) -> None:
    files = dict(package_files())
    files["hidden/topwords/behaviour_test.go"] = files["hidden/topwords/behaviour_test.go"].replace(
        b"func TestZeroK", b"func TestRenamed"
    )
    report = plugin.validate_task(draft(files=files))
    codes = {issue.code for issue in report.issues}
    assert "oracle-case-absent" in codes


def test_a_module_graph_without_pinned_content_is_refused(plugin: GoLanguagePlugin) -> None:
    files = dict(package_files())
    files["visible/repo/go.mod"] = (
        b"module pcb.local/topwords\n\ngo 1.26\n\nrequire x.com/y v1.0.0\n"
    )
    report = plugin.validate_task(draft(files=files))
    assert "go-module-invalid" in {issue.code for issue in report.issues}


def test_an_undeclared_variant_is_refused(plugin: GoLanguagePlugin) -> None:
    data = manifest()
    data["fixtures"] = [item for item in data["fixtures"] if item["variant"] != "timeout"]
    files = package_files()
    report = plugin.validate_task(draft(files=files).model_copy(update={"manifest": data}))
    assert "variant-missing" in {issue.code for issue in report.issues}


def test_discovery_finds_only_runnable_test_functions() -> None:
    source = b"""package topwords_test

func TestOne(t *testing.T) {}

func helper(t *testing.T) {}

type TestCase struct{}

func (c TestCase) TestMethod(t *testing.T) {}
"""
    assert discover_cases("x_test.go", source) == ["TestOne"]


# ----------------------------------------------------------------------------- frozen view


def test_the_frozen_view_carries_the_module_digest_and_scaffolding(
    plugin: GoLanguagePlugin,
) -> None:
    view = frozen(plugin)
    assert view.required_outputs == (CANDIDATE,)
    assert view.quality["module_files"].keys() == {"go.mod", "go.sum", "topwords/contract.go"}
    assert str(view.quality["go_module_digest"]).startswith("sha256:")
    assert view.inventory_digest.startswith("sha256:")
    assert plugin.profile("go-profile-v1").profile_version == "go-profile-v1"
    with pytest.raises(ValueError, match="unknown Go profile version"):
        plugin.profile("go-profile-v2")


def test_trusted_inputs_are_the_scaffolding_keyed_by_plan_paths(plugin: GoLanguagePlugin) -> None:
    files = package_files()
    view = plugin.freeze_view(draft(files=files), "sha256:" + "1" * 64)
    inputs = plugin.trusted_inputs(files, view)
    assert set(inputs) == {"work/go.mod", "work/go.sum", "work/topwords/contract.go"}
    assert inputs["work/go.mod"] == files["visible/repo/go.mod"]


# ------------------------------------------------------------------------------------ plans


def test_the_build_plan_is_a_typed_offline_go_invocation(plugin: GoLanguagePlugin) -> None:
    view = frozen(plugin)
    plan = plugin.build_plan(view, candidate(view))
    assert plan.plan_id == "go.build"
    assert plan.argv[0] == "python"
    assert "go" in plan.argv and "build" in plan.argv
    assert plan.exit_semantics.success == (0,)
    assert plan.exit_semantics.findings == (1,)
    assert 2 in plan.exit_semantics.error and 127 in plan.exit_semantics.error
    assert plan.environment["GOTOOLCHAIN"] == "local"
    assert plan.environment["GOPROXY"] == "off"
    assert plan.environment["CGO_ENABLED"] == "0"
    assert plan.argv[plan.argv.index("--cwd") + 1] == "work"
    assert plan.resources.executable_workspace is True
    roles = {item.path: item.role for item in plan.inputs}
    assert roles["work/topwords/topwords.go"] == "candidate"
    assert roles["work/go.mod"] == "config"
    assert next(item.digest for item in plan.inputs if item.path == "work/go.mod")


def test_the_test_plan_targets_each_oracle_group_with_its_own_selector(
    plugin: GoLanguagePlugin,
) -> None:
    view = frozen(plugin)
    plan = plugin.test_plan(view)
    assert [group.group_id for group in plan.groups] == ["behaviour", "stress"]
    behaviour = plan.groups[0]
    assert behaviour.plan.argv[-1] == "./..."
    assert "-json" in behaviour.plan.argv
    assert "-run" in behaviour.plan.argv
    assert behaviour.plan.argv[behaviour.plan.argv.index("--cwd") + 1] == "work"
    assert "^(" in behaviour.plan.argv[behaviour.plan.argv.index("-run") + 1]
    assert (
        "TestTiesBreakAlphabetically" in behaviour.plan.argv[behaviour.plan.argv.index("-run") + 1]
    )
    # A quality-only group is repeated; an acceptance group is not.
    assert behaviour.repetitions == 1
    assert plan.groups[1].repetitions == 3
    overlay = {item.path for item in behaviour.plan.inputs if item.role == "overlay"}
    assert overlay == {"work/topwords/behaviour_test.go"}
    assert plan.expected_inventory_digest == view.inventory_digest


def test_a_nonconcurrent_task_produces_no_race_plan(plugin: GoLanguagePlugin) -> None:
    view = frozen(plugin)
    ids = [plan.analyzer_id for plan in plugin.analysis_plans(context(view))]
    assert "race" not in ids
    assert ids == ["gofmt", "vet", "staticcheck", "gosec", "context"]
    by_id = {plan.analyzer_id: plan for plan in plugin.analysis_plans(context(view))}
    assert by_id["vet"].argv[by_id["vet"].argv.index("--cwd") + 1] == "work"
    assert by_id["staticcheck"].argv[by_id["staticcheck"].argv.index("--cwd") + 1] == "work"
    assert by_id["gosec"].argv[by_id["gosec"].argv.index("--cwd") + 1] == "work"


def test_a_required_race_run_is_planned_with_its_own_identity(plugin: GoLanguagePlugin) -> None:
    view = concurrent_view(plugin, frozen(plugin))
    plan = next(p for p in plugin.analysis_plans(context(view)) if p.analyzer_id == "race")
    assert plan.required is True
    assert plan.environment["CGO_ENABLED"] == "1"
    assert plan.tool.version.endswith("-race")
    assert plan.image_digest == plugin.identities.runtime.digest
    assert plan.image_digest != plugin.identities.performance.digest
    assert plan.evidence_ownership["measured-data-race"].value == "robustness"


def test_an_instrumented_build_can_never_be_measured() -> None:
    with pytest.raises(ValueError, match="instrumented build flags"):
        go_plans.assert_release(("go", "test", "-race", "./..."), where="a plan")
    with pytest.raises(ValueError, match="instrumented build flags"):
        go_plans.assert_release(("go", "test", "-cover", "./..."), where="a plan")
    go_plans.assert_release(("go", "run", "./cmd/pcb_workload"), where="a plan")


def test_a_task_without_a_performance_declaration_plans_no_iteration(
    plugin: GoLanguagePlugin,
) -> None:
    assert plugin.performance_plan(frozen(plugin)) is None


def test_the_analyzers_declare_the_checks_they_can_report(plugin: GoLanguagePlugin) -> None:
    capabilities = plugin.analyzer("gosec").capabilities()
    assert capabilities.languages == ("go",)
    assert capabilities.requires_network is False
    assert capabilities.check_ids
    assert plugin.analyzer("context").capabilities().check_ids[0].startswith("go.context.")
    assert plugin.analyzer("context").plan(context(frozen(plugin))).analyzer_id == "context"


# --------------------------------------------------------------------------------- symbols


def test_the_symbol_index_finds_package_level_declarations_and_methods(
    plugin: GoLanguagePlugin,
) -> None:
    source = DictArtifactReader(
        {
            "topwords/topwords.go": (
                b"package topwords\n\n"
                b"type Count struct{ Word string }\n\n"
                b"func TopWords(text string, k int) []Count { return nil }\n\n"
                b"func (c Count) Count2() int { return 0 }\n\n"
                b"func unexported() { var local = 1; _ = local }\n"
            )
        }
    )
    index = plugin.symbols(source)
    kinds = {(item.qualified_name, item.symbol_kind, item.public) for item in index.symbols}
    assert ("topwords.Count", "class", True) in kinds
    assert ("topwords.TopWords", "function", True) in kinds
    assert ("topwords.Count.Count2", "method", True) in kinds
    assert ("topwords.unexported", "function", False) in kinds
    assert index.unparsed_paths == ()


def test_an_unbalanced_file_is_reported_unparsed_rather_than_guessed(
    plugin: GoLanguagePlugin,
) -> None:
    index = plugin.symbols(DictArtifactReader({"broken.go": b"package x\nfunc f() {\n"}))
    assert index.unparsed_paths == ("broken.go",)
    assert index.symbols == ()


def test_test_files_are_not_part_of_the_candidate_surface(plugin: GoLanguagePlugin) -> None:
    index = plugin.symbols(
        DictArtifactReader({"behaviour_test.go": b"package x\nfunc TestA() {}\n"})
    )
    assert index.symbols == ()


# ------------------------------------------------------------------------- parse_build path


def test_the_build_verdict_reads_the_compiler_not_a_line_pattern(plugin: GoLanguagePlugin) -> None:
    view = frozen(plugin)
    plan = plugin.build_plan(view, candidate(view))
    ok = DictArtifactReader(
        {
            "out/build.out": b"",
            "out/build.err": b"",
            "out/build.run.json": b'{"exit_code":0,"timed_out":false}',
            "_execution.json": _record(0),
        }
    )
    assert plugin.parse_build(plan, ok) == ("pass", "the module compiles")

    failing = DictArtifactReader(
        {
            "out/build.out": b"",
            "out/build.err": b"topwords/topwords.go:5:2: undefined: TopWords\n",
            "out/build.run.json": b'{"exit_code":1,"timed_out":false}',
            "_execution.json": _record(1),
        }
    )
    status, detail = plugin.parse_build(plan, failing)
    assert status == "fail"
    assert "undefined: TopWords" in detail

    missing = DictArtifactReader({"_execution.json": _record(1, timed_out=True)})
    assert plugin.parse_build(plan, missing) == ("incomplete", "build timed out")


def test_a_clean_staticcheck_run_is_measured_zero_not_missing(plugin: GoLanguagePlugin) -> None:
    """An empty report is a result; only an absent one is missing evidence (PCB-22-2).

    staticcheck prints one JSON object per finding and nothing at all when there are none, so a
    clean candidate produces a zero-byte report that the supervisor classifies as
    ``empty_report``. Reading that as MISSING makes the reference solution - the cleanest code in
    the package - the only candidate those two required scans cannot measure.
    """
    view = frozen(plugin)
    plans = {p.analyzer_id: p for p in plugin.analysis_plans(context(view))}
    for tool in ("staticcheck", "gosec"):
        plan = plans[tool]
        reader = DictArtifactReader(
            {
                f"out/{tool}.out": b"",
                f"out/{tool}.err": b"",
                f"out/{tool}.run.json": b'{"exit_code":0,"timed_out":false}',
                "_execution.json": _record(0),
            }
        )
        scan = next(o for o in plugin.parse_analysis(reader, plan) if o.check_id.endswith(".scan"))
        assert scan.check_id == f"go.{tool}.scan"
        assert scan.status == MeasurementStatus.MEASURED, (tool, scan.explanation)
        assert scan.value == 0


def test_an_analyzer_that_died_before_reporting_is_still_missing(
    plugin: GoLanguagePlugin,
) -> None:
    """The clean case above must not weaken the crash case.

    A nonzero exit with no report is missing evidence, not a clean scan.
    """
    view = frozen(plugin)
    plans = {p.analyzer_id: p for p in plugin.analysis_plans(context(view))}
    plan = plans["staticcheck"]
    reader = DictArtifactReader(
        {
            "out/staticcheck.out": b"",
            "out/staticcheck.err": b"internal error\n",
            "out/staticcheck.run.json": b'{"exit_code":3,"timed_out":false}',
            "_execution.json": _record(3),
        }
    )
    scan = next(o for o in plugin.parse_analysis(reader, plan) if o.check_id.endswith(".scan"))
    assert scan.status == MeasurementStatus.MISSING
    assert scan.value is None


def _record(exit_code: int, *, timed_out: bool = False) -> bytes:
    """A supervisor execution record in the shape ``plan_status`` validates."""
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "execution_record",
            "plan_id": "go.build",
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": 10,
            "stdout_digest": "sha256:" + "0" * 64,
            "stderr_digest": "sha256:" + "0" * 64,
            "stdout_tail": "",
            "stderr_tail": "",
            "isolation_tier": "development",
            "sandbox_id": "00000000-0000-4000-8000-000000000000",
        }
    ).encode("utf-8")


def test_the_fixture_module_is_the_one_the_manifest_declares() -> None:
    data = manifest()
    assert data["output_contract"]["allowed_paths"] == [CANDIDATE]
    assert data["runtime"]["language_plugin_id"] == "go"
    assert (TOP_WORDS / "visible" / "repo" / "go.mod").is_file()
