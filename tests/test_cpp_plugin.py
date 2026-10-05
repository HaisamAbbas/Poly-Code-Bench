"""PCB-21-2: C++ task validation, frozen recipe, plans and analyzer capabilities (offline).

Nothing here needs Docker: plans are data. The Docker-backed behaviour of those plans is covered by
``tests/test_cpp_docker.py`` and by the recorded-output tests in ``tests/test_cpp_parsers.py``.

The contract under test is C++-specific in three places: the hidden recipe is digest-pinned and
refuses a release or sanitized profile, a release measurement plan may carry no ``-fsanitize=``
flag anywhere in its argv, and ``address``+``thread`` is refused at task validation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cpp_plugin_support import TOP_WORDS, draft, frozen, package_files, with_quality
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate, ScoreDimension
from polycodebench_lang_cpp import CppLanguagePlugin
from polycodebench_lang_cpp.locks import LockError, load_lock
from polycodebench_lang_cpp.plugin import recipe_digest_of
from polycodebench_lang_cpp.taskspec import CppRecipe, parse_recipe
from polycodebench_plugins_api import API_VERSION, AnalysisContext, DictArtifactReader
from polycodebench_plugins_api.protocols import ExecutableLanguagePlugin, LanguagePlugin

plugin = CppLanguagePlugin()
LOCK = load_lock()
CONTEXT_FILES = ("src/top_words.cpp", "include/top_words.hpp")
SANITIZERS = ("-fsanitize=address,undefined",)


def candidate() -> Candidate:
    return Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": "conformance-cpp-top-words",
                "task_version": 1,
                "sample_index": 0,
                "submission_kind": "source_bundle",
                "payload_digest": "sha256:" + "0" * 64,
                "artifact_ids": [],
                "frozen_at": None,
            }
        )
    )


def analysis(view: Any, paths: tuple[str, ...] = CONTEXT_FILES) -> list[Any]:
    context = AnalysisContext(
        task=view, candidate_digest="sha256:" + "2" * 64, candidate_paths=paths
    )
    return plugin.analysis_plans(context)


def codes(files: dict[str, bytes]) -> set[str]:
    return {issue.code for issue in plugin.validate_task(draft(files=files)).issues}


# ------------------------------------------------------------------------------ validation


def test_the_authored_fixture_package_validates() -> None:
    report = plugin.validate_task(draft())
    assert report.ok, [(issue.code, issue.message) for issue in report.issues]
    assert report.plugin_id == "cpp"
    assert "recipe-identity" in report.checks and "quality-plan" in report.checks


def test_a_wrong_primary_language_is_refused() -> None:
    files = package_files()
    assert "language" not in codes(files)
    from cpp_plugin_support import manifest

    data = manifest()
    data["task"]["primary_language"] = "c"
    broken = draft(files=files)
    report = plugin.validate_task(broken.model_copy(update={"manifest": data}))
    assert "language" in {issue.code for issue in report.issues}


def test_a_standard_the_pinned_toolchain_does_not_carry_is_refused() -> None:
    """A recipe naming c++23 cannot be built by the pinned clang, so it is rejected at authoring."""
    files = package_files()
    files["hidden/recipe.json"] = files["hidden/recipe.json"].replace(b'"c++20"', b'"c++23"')
    assert "recipe-invalid" in codes(files)


@pytest.mark.parametrize("pair", [("address", "thread"), ("undefined", "thread")])
def test_an_incompatible_instrumentation_pair_is_refused_at_validation(
    pair: tuple[str, str],
) -> None:
    files = package_files()
    import yaml

    plan = yaml.safe_load(files["hidden/quality-plan.yaml"])
    plan["sanitizer_lanes"] = [list(pair)]
    files["hidden/quality-plan.yaml"] = yaml.safe_dump(plan).encode()
    assert "quality-plan-invalid" in codes(files)


def test_a_recipe_naming_a_release_or_sanitized_profile_is_refused() -> None:
    for profile in ("release", "sanitize_address_undefined", "sanitize_thread"):
        files = package_files()
        files["hidden/recipe.json"] = files["hidden/recipe.json"].replace(
            b'"build_profile": "debug"', f'"build_profile": "{profile}"'.encode()
        )
        assert "recipe-invalid" in codes(files), profile


def test_a_missing_reference_solution_is_refused() -> None:
    files = package_files()
    del files["hidden/reference/src/top_words.cpp"]
    assert "reference-missing" in codes(files)
    files = package_files()
    del files["visible/repo/include/top_words.hpp"]
    assert "reference-missing" in codes(files)


def test_a_missing_stub_or_reference_for_any_required_output_is_refused() -> None:
    files = package_files()
    del files["visible/repo/src/top_words.cpp"]
    assert "stub-missing" in codes(files)


def test_an_oracle_case_the_test_file_does_not_run_is_refused() -> None:
    files = package_files()
    oracle = json.loads(files["hidden/oracle.json"])
    oracle["groups"][0]["cases"].append(
        {"case_id": "behaviour::never_checked", "required": True, "case_kind": "edge"}
    )
    files["hidden/oracle.json"] = json.dumps(oracle).encode()
    assert "oracle-case-missing" in codes(files)


def test_a_case_the_test_file_runs_but_the_oracle_does_not_declare_is_refused() -> None:
    files = package_files()
    files["hidden/tests/behaviour.cpp"] += (
        b'\nint main() { PCB_CHECK("behaviour::smuggled", true, "smuggled"); }\n'
    )
    assert "oracle-case-undeclared" in codes(files)


def test_case_discovery_reads_the_harness_macros_and_ignores_comments_and_strings() -> None:
    """The same macro call is the static declaration, so the inventory cannot drift
    from the file.
    """
    from polycodebench_lang_cpp.taskspec import discover_cases

    source = b"""
    // PCB_CHECK("behaviour::commented", true, "x");
    const char* note = "PCB_CHECK(\\"behaviour::in_string\\", true, \\"x\\")";
    PCB_CHECK("behaviour::top", true, "real");
    PCB_CHECKF("behaviour::checked", true, "real");
    PCB_SKIP("behaviour::skipped", "not applicable here");
    """
    assert discover_cases("tests/behaviour.cpp", source) == [
        "behaviour::top",
        "behaviour::checked",
        "behaviour::skipped",
    ]
    with pytest.raises(ValueError, match="declares no PCB_CHECK"):
        discover_cases("tests/behaviour.cpp", b"int main() { return 0; }\n")


def test_a_missing_exposure_rights_record_is_refused() -> None:
    files = package_files()
    del files["admission/exposure-rights.json"]
    assert "package-file-missing" in codes(files)


def test_an_invalid_exposure_rights_record_is_refused() -> None:
    files = package_files()
    record = json.loads(files["admission/exposure-rights.json"])
    record["rights"]["status"] = "unknown_status"
    files["admission/exposure-rights.json"] = json.dumps(record).encode()
    assert "exposure-rights-invalid" in codes(files)
    files = package_files()
    del record["rights"]["license_expression"]
    files["admission/exposure-rights.json"] = json.dumps(record).encode()
    assert "exposure-rights-invalid" in codes(files)


def test_an_analyzer_with_no_declaration_sanitizer_is_refused() -> None:
    """`asan` is required, so the task must declare the sanitizer it needs."""
    files = package_files()
    import yaml

    plan = yaml.safe_load(files["hidden/quality-plan.yaml"])
    plan.pop("sanitizer_lanes")
    plan["sanitizers"] = []
    files["hidden/quality-plan.yaml"] = yaml.safe_dump(plan).encode()
    assert "quality-plan-invalid" in codes(files)


def test_a_required_analyzer_the_task_never_declares_is_refused() -> None:
    files = package_files()
    plan = files["hidden/quality-plan.yaml"].decode()
    import yaml

    data = yaml.safe_load(plan)
    data["required_analyzers"].append("klock")
    files["hidden/quality-plan.yaml"] = yaml.safe_dump(data).encode()
    assert "quality-plan-invalid" in codes(files)


def test_an_efficiency_dimension_without_a_workload_is_refused() -> None:
    files = package_files()
    import yaml

    plan = yaml.safe_load(files["hidden/quality-plan.yaml"])
    plan.pop("performance")
    files["hidden/quality-plan.yaml"] = yaml.safe_dump(plan, sort_keys=False).encode()
    assert "dimension-evidence" in codes(files)


def test_a_fixture_pointing_at_a_missing_solution_is_refused() -> None:
    files = package_files()
    manifest = json.loads(json.dumps({}))  # keep the type checker honest about json round-trips
    del manifest
    import yaml

    data = yaml.safe_load((TOP_WORDS / "manifest.yaml").read_text(encoding="utf-8"))
    data["fixtures"][0]["solution_path"] = "hidden/reference/src/absent.cpp"
    report = plugin.validate_task(draft(files=files).model_copy(update={"manifest": data}))
    assert "fixture-file-missing" in {issue.code for issue in report.issues}


def test_a_package_missing_a_fixture_variant_is_refused() -> None:
    import yaml

    files = package_files()
    data = yaml.safe_load((TOP_WORDS / "manifest.yaml").read_text(encoding="utf-8"))
    data["fixtures"] = [item for item in data["fixtures"] if item["variant"] != "timeout"]
    report = plugin.validate_task(draft(files=files).model_copy(update={"manifest": data}))
    assert "fixture-variant-missing" in {issue.code for issue in report.issues}


# ------------------------------------------------------------------------- the frozen recipe


def test_freeze_view_carries_the_pinned_recipe_and_its_digest() -> None:
    view = frozen(plugin)
    recipe = CppRecipe.model_validate_json(json.dumps(view.quality["recipe"]))
    assert recipe.compiler == "clang" and recipe.standard == "c++20"
    assert recipe.build_profile == "debug"
    assert recipe.include_dirs == ("include",)
    # The digest travels with the view and is recomputed, never trusted from the file.
    assert view.quality["recipe_digest"] == recipe.digest()
    assert view.quality["recipe_digest"] == recipe_digest_of(view)
    assert recipe.digest().startswith("sha256:")


def test_a_changed_recipe_moves_the_frozen_digest_and_the_plan_identity() -> None:
    view = frozen(plugin)
    files = package_files()
    files["hidden/recipe.json"] = files["hidden/recipe.json"].replace(
        b'"build_profile": "debug"', b'"build_profile": "sanitize_thread"'
    )
    changed = plugin.freeze_view(draft(files=files), "sha256:" + "1" * 64)
    assert changed.quality["recipe_digest"] != view.quality["recipe_digest"]
    # And the plans built from it now name a different config input digest.
    before = {i.digest for i in plugin.build_plan(view, candidate()).inputs if i.digest}
    after = {i.digest for i in plugin.build_plan(changed, candidate()).inputs if i.digest}
    assert before and after and before != after


def test_the_recipe_is_hidden_from_the_candidate_but_pinned_in_the_frozen_task() -> None:
    """A candidate that adapts to the recipe is not being measured on the recipe."""
    view = frozen(plugin)
    plan = plugin.build_plan(view, candidate())
    roles = {item.path: item.role for item in plan.inputs}
    assert roles["work/pcb_recipe.json"] == "config"
    assert roles["work/src/top_words.cpp"] == "candidate"
    assert roles["work/include/top_words.hpp"] == "candidate"
    # The manifest's visible area never names the recipe.
    manifest = (TOP_WORDS / "manifest.yaml").read_text(encoding="utf-8")
    assert "hidden/recipe.json" in manifest
    assert "recipe" not in (TOP_WORDS / "visible/task.md").read_text(encoding="utf-8").lower()


def test_trusted_inputs_returns_exactly_the_pinned_recipe_and_nothing_else() -> None:
    view = frozen(plugin)
    files = package_files()
    trusted = plugin.trusted_inputs(files, view)
    assert set(trusted) == {"work/pcb_recipe.json"}
    # The bytes handed to the runner are the canonical recipe the digest was taken over, so the
    # plan's own input check passes rather than failing on a cosmetic reformat.
    recipe = CppRecipe.model_validate_json(json.dumps(view.quality["recipe"]))
    canonical = json.dumps(
        recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert trusted["work/pcb_recipe.json"] == canonical
    from polycodebench_evaluation.plan_runner import materialize_inputs

    staged = materialize_inputs(
        plugin.build_plan(view, candidate()),
        {
            "candidate": {
                "src/top_words.cpp": b"x",
                "include/top_words.hpp": b"header",
            },
            "config": trusted,
        },
    )
    assert staged["work/pcb_recipe.json"] == canonical


def test_the_candidate_lane_runs_in_the_runtime_image_which_carries_no_analyzers() -> None:
    """A candidate must not be able to read the checks that will judge it from inside its image."""
    view = frozen(plugin)
    ids = plugin.identities
    build = plugin.build_plan(view, candidate())
    assert build.image_digest == ids.runtime.digest
    for group in plugin.test_plan(view).groups:
        assert group.plan.image_digest == ids.runtime.digest
    # The static analyzers run in the evaluator image, which the candidate never enters.
    for plan in analysis(view):
        if plan.analyzer_id in {"clang_tidy", "cppcheck", "context"}:
            assert plan.image_digest == ids.evaluator.digest, plan.analyzer_id
    # The instrumented run executes the candidate, so it belongs in the runtime image too --
    # but that image records clang-tidy and cppcheck as absent.
    asan = next(plan for plan in analysis(view) if plan.analyzer_id == "asan")
    assert asan.image_digest == ids.runtime.digest
    assert ids.images["runtime"].tools["clang-tidy"] == "absent"
    assert ids.images["runtime"].tools["cppcheck"] == "absent"
    assert build.image_digest != ids.evaluator.digest


def test_the_inventory_is_the_oracles_and_its_digest_moves_with_it() -> None:
    view = frozen(plugin)
    groups = {group.group_id: group for group in plugin.inventory(view)}
    assert set(groups) == {"behaviour", "stress"}
    assert groups["behaviour"].required and not groups["stress"].required
    assert len(groups["behaviour"].cases) == 8
    assert plugin.test_plan(view).expected_inventory_digest == view.inventory_digest
    # A timeout bound is not part of the case inventory, so it does not move the identity.
    retimed = view.model_copy(update={"inventory": {**view.inventory, "suite_timeout_seconds": 30}})
    assert plugin.test_plan(retimed).expected_inventory_digest == view.inventory_digest
    # A *case* does move it: the inventory digest is what the gate reconciles against.
    groups = json.loads(json.dumps(view.inventory["groups"]))
    groups[0]["cases"].append(
        {
            "case_id": "behaviour::added",
            "required": True,
            "case_kind": "edge",
            "predeclared_skip": False,
        }
    )
    widened = view.model_copy(update={"inventory": {**view.inventory, "groups": groups}})
    assert plugin.test_plan(widened).expected_inventory_digest != view.inventory_digest


# ------------------------------------------------------------------------------------ plans


def test_every_plan_is_a_typed_argument_vector_pinned_to_its_recipe_image() -> None:
    view = frozen(plugin)
    ids = plugin.identities
    declared = {record.reference: record.digest for record in ids.images.values()}
    plans: list[Any] = [plugin.build_plan(view, candidate())]
    plans += [group.plan for group in plugin.test_plan(view).groups]
    plans += analysis(view)
    performance = plugin.performance_plan(view)
    assert performance is not None
    plans.append(performance.iteration_plan)
    for plan in plans:
        assert plan.argv[0] == "python"
        assert "-c" not in plan.argv and "sh" not in plan.argv
        assert declared.get(plan.image) == plan.image_digest, plan.plan_id
        assert plan.resources.network == "none"
        assert plan.resources.timeout_seconds <= 110  # the local driver caps one exec at 120s
        assert plan.scope


def test_the_build_and_the_candidate_lane_run_in_the_runtime_image_not_the_analyzers() -> None:
    """A candidate must not be able to read the checks that will judge it from inside its image."""
    view = frozen(plugin)
    ids = plugin.identities
    build = plugin.build_plan(view, candidate())
    assert build.image_digest == ids.runtime.digest
    for group in plugin.test_plan(view).groups:
        assert group.plan.image_digest == ids.runtime.digest
    for plan in analysis(view):
        if plan.analyzer_id in {"clang_tidy", "cppcheck", "context"}:
            assert plan.image_digest == ids.evaluator.digest, plan.analyzer_id
        else:
            assert plan.image_digest == ids.runtime.digest, plan.analyzer_id
    assert build.image_digest != ids.evaluator.digest


def test_the_build_plan_carries_the_pinned_standard_and_the_recipes_build_profile() -> None:
    view = frozen(plugin)
    plan = plugin.build_plan(view, candidate())
    argv = list(plan.argv)
    flags = [argv[i + 1] for i, token in enumerate(argv) if token == "--cxxflag"]
    profile = LOCK.build_profile("debug")
    assert flags == [LOCK.standard_flag("c++20"), *profile.cxxflags]
    assert argv[argv.index("--compiler") + 1] == LOCK.compiler("clang").binary
    assert argv[argv.index("--name") + 1] == "/workspace/out/build"
    assert argv[argv.index("--archive") + 1].endswith("libpcb.a")
    assert "out/build.run.json" in {output.path for output in plan.outputs}
    assert argv[argv.index("--deadline") + 1] == str(
        plugin.build_plan(view, candidate()).resources.timeout_seconds - 3
    )


def test_the_test_plan_runs_one_group_per_invocation_with_the_hidden_tests_as_overlay() -> None:
    view = frozen(plugin)
    plan = plugin.test_plan(view)
    groups = {group.group_id: group for group in plan.groups}
    assert set(groups) == {"behaviour", "stress"}
    assert groups["stress"].repetitions == 3 and groups["behaviour"].repetitions == 1
    behaviour = groups["behaviour"].plan
    argv = list(behaviour.argv)
    assert argv[argv.index("--test") + 1] == "/workspace/work/tests/behaviour.cpp"
    roles = {item.path: item.role for item in behaviour.inputs}
    assert roles["work/tests/behaviour.cpp"] == "overlay"
    assert roles["work/src/top_words.cpp"] == "candidate"
    assert roles["work/pcb_recipe.json"] == "config"
    assert plan.expected_inventory_digest == view.inventory_digest


def test_a_candidate_cannot_supply_the_hidden_tests_or_the_pinned_recipe() -> None:
    from polycodebench_evaluation.plan_runner import PlanInputError, materialize_inputs

    view = frozen(plugin)
    group = plugin.test_plan(view).groups[0].plan
    trusted = plugin.trusted_inputs(package_files(), view)
    overlay = {"work/tests/behaviour.cpp": package_files()["hidden/tests/behaviour.cpp"]}
    ok = materialize_inputs(
        group,
        {
            "candidate": {
                "src/top_words.cpp": b"x",
                "include/top_words.hpp": b"header",
            },
            "overlay": overlay,
            "config": trusted,
        },
    )
    assert ok["work/tests/behaviour.cpp"] == overlay["work/tests/behaviour.cpp"]
    # A candidate that ships its own tests or recipe is simply not consulted for them.
    with pytest.raises(PlanInputError):
        materialize_inputs(
            group,
            {"candidate": {"src/top_words.cpp": b"x", "tests/behaviour.cpp": b"y"}},
        )
    with pytest.raises(PlanInputError):
        materialize_inputs(
            group,
            {
                "candidate": {"src/top_words.cpp": b"x"},
                "overlay": overlay,
                "config": {"work/pcb_recipe.json": b'{"tampered": true}'},
            },
        )


def test_analysis_plans_follow_the_quality_plan_and_the_task_applicability() -> None:
    view = frozen(plugin)
    plans = {plan.analyzer_id: plan for plan in analysis(view)}
    assert set(plans) == {"clang_tidy", "cppcheck", "context", "asan"}
    assert all(plan.required for plan in plans.values())
    # No sanitizer declared: no instrumented plan is built at all.
    unsupported = with_quality(
        view,
        sanitizers=[],
        sanitizer_lanes=[],
        required_analyzers=["clang_tidy", "cppcheck", "context"],
        opportunities={**view.quality["opportunities"], "undefined_behavior_memory_safety": 0},
    )
    assert {plan.analyzer_id for plan in analysis(unsupported)} == {
        "clang_tidy",
        "cppcheck",
        "context",
    }
    # `tsan` is the analyzer a `thread` task builds, and `asan` is not built for it.
    threaded = with_quality(
        view,
        sanitizers=["thread"],
        sanitizer_lanes=[],
        required_analyzers=["clang_tidy", "cppcheck", "context", "tsan"],
    )
    threaded_plans = {plan.analyzer_id: plan for plan in analysis(threaded)}
    assert set(threaded_plans) == {"clang_tidy", "cppcheck", "context", "tsan"}
    assert "asan" not in threaded_plans
    assert "-fsanitize=thread" in threaded_plans["tsan"].argv
    assert "-fsanitize=address,undefined" not in threaded_plans["tsan"].argv


def test_incompatible_sanitizers_are_planned_as_separate_lanes() -> None:
    view = frozen(plugin)
    dual = with_quality(
        view,
        sanitizers=[],
        sanitizer_lanes=[["address", "undefined"], ["thread"]],
        required_analyzers=["clang_tidy", "cppcheck", "context", "asan", "tsan"],
    )
    plans = {plan.analyzer_id: plan for plan in analysis(dual)}
    assert {"asan", "tsan"} <= set(plans)
    assert "-fsanitize=address,undefined" in plans["asan"].argv
    assert "-fsanitize=thread" in plans["tsan"].argv


def test_the_analyzer_plans_use_the_pinned_invocations_and_the_pinned_environment() -> None:
    view = frozen(plugin)
    plans = {plan.analyzer_id: plan for plan in analysis(view)}
    tidy = LOCK.analyzer("clang_tidy")
    assert plans["clang_tidy"].argv[plans["clang_tidy"].argv.index("--") + 1 :][
        : len(tidy.flags) + 1
    ] == (
        tidy.binary,
        *tidy.flags,
    )
    assert "--config-file=/opt/pcb/rules/.clang-tidy" in plans["clang_tidy"].argv
    cppcheck = LOCK.analyzer("cppcheck")
    assert plans["cppcheck"].argv[plans["cppcheck"].argv.index("--") + 1] == cppcheck.binary
    assert "--xml" in plans["cppcheck"].argv
    # The report is the evidence, so a cppcheck finding must not change the exit status.
    assert plans["cppcheck"].exit_semantics.classify(0, False) == "success"
    assert plans["cppcheck"].exit_semantics.classify(1, False) == "findings"
    # clang-tidy exits 0 with warnings, so a non-zero exit is a compile failure, not a scan.
    assert plans["clang_tidy"].exit_semantics.classify(0, False) == "success"
    assert plans["clang_tidy"].exit_semantics.classify(1, False) == "error"


def test_the_instrumented_plan_runs_in_the_runtime_image_under_the_pinned_environment() -> None:
    view = frozen(plugin)
    plans = {plan.analyzer_id: plan for plan in analysis(view)}
    asan = plans["asan"]
    assert asan.image_digest == plugin.identities.runtime.digest
    assert asan.output_schema == "pcb-cpp-sanitizer-v1"
    assert asan.evidence_ownership["candidate-undefined-behaviour"] == ScoreDimension.ROBUSTNESS
    assert asan.environment == LOCK.sanitizer_environment(("address", "undefined"))
    assert "detect_leaks=1" in asan.environment["ASAN_OPTIONS"]
    assert asan.baseline_reusable is True
    argv = list(asan.argv)
    flags = [argv[i + 1] for i, token in enumerate(argv) if token == "--cxxflag"]
    # The pinned standard, then the pinned sanitized profile -- the same order as the build plan.
    assert flags == [
        LOCK.standard_flag("c++20"),
        *LOCK.profile_for(("address", "undefined")).cxxflags,
    ]
    # Every hidden group is built and run under the one sanitized profile.
    groups = [argv[i + 1] for i, token in enumerate(argv) if token == "--group"]
    assert sorted(groups) == [
        "behaviour=/workspace/work/tests/behaviour.cpp",
        "stress=/workspace/work/tests/stress.cpp",
    ]


def test_an_exit_semantics_never_places_one_code_in_two_groups() -> None:
    """The contract requires disjoint code groups; a shared code makes a verdict undecidable."""
    view = frozen(plugin)
    plans: list[Any] = [plugin.build_plan(view, candidate())]
    plans += [group.plan for group in plugin.test_plan(view).groups]
    plans += analysis(view)
    performance = plugin.performance_plan(view)
    assert performance is not None
    plans.append(performance.iteration_plan)
    for plan in plans:
        semantics = plan.exit_semantics
        groups = [set(semantics.success), set(semantics.findings), set(semantics.error)]
        for index, left in enumerate(groups):
            for right in groups[index + 1 :]:
                assert not left & right, (plan.plan_id, sorted(left & right))
        assert set().union(*groups), plan.plan_id
        # And the classification agrees: no exit code gets two different readings.
        for code in set().union(*groups):
            readings = {semantics.classify(code, timed_out=False)}
            assert len(readings) == 1, (plan.plan_id, code, readings)


# ------------------------------------------------------------------- the release measurement


def test_a_task_without_a_declared_workload_has_no_performance_plan() -> None:
    view = frozen(plugin)
    assert plugin.performance_plan(with_quality(view, performance=None)) is None


def test_the_release_performance_plan_carries_the_release_flags_and_no_sanitizer() -> None:
    """A sanitized binary is an order of magnitude slower, so it can never be a release timing."""
    view = frozen(plugin)
    performance = plugin.performance_plan(view)
    assert performance is not None
    plan = performance.iteration_plan
    assert plan.image_digest == plugin.identities.performance.digest
    assert plan.image_digest != plugin.identities.runtime.digest
    argv = list(plan.argv)
    flags = [argv[i + 1] for i, token in enumerate(argv) if token == "--cxxflag"]
    release = LOCK.release_profile()
    assert flags == [LOCK.standard_flag("c++20"), *release.cxxflags]
    assert "-O3" in flags and "-DNDEBUG" in flags
    # Not one `-fsanitize=` token anywhere in the timed plan's argv.
    assert not [token for token in argv if token.startswith("-fsanitize=")]
    assert not any("-fsanitize=" in token for token in argv)
    assert performance.language_runtime.startswith("clang++")
    assert performance.hardware_class


def test_a_sanitized_plan_by_contrast_carries_the_sanitizer_flags() -> None:
    """The control: the sanitizer flags really do reach an argv, they are just not the timed one."""
    view = frozen(plugin)
    asan = next(plan for plan in analysis(view) if plan.analyzer_id == "asan")
    argv = list(asan.argv)
    assert [token for token in argv if token.startswith("-fsanitize=")]
    assert "-fsanitize=address,undefined" in argv
    # It runs in the runtime image, never the performance one.
    assert asan.image_digest != plugin.identities.performance.digest


def test_the_performance_plan_declares_the_tasks_workloads_and_a_bounded_deadline() -> None:
    view = frozen(plugin)
    performance = plugin.performance_plan(view)
    assert performance is not None
    assert [workload.workload_id for workload in performance.workloads] == [
        "small",
        "medium",
        "large",
    ]
    assert sum(workload.weight_bp for workload in performance.workloads) == 10_000
    assert performance.warmup_iterations == 5 and performance.measured_iterations == 20
    assert performance.metric_ids == ("elapsed_ns", "peak_rss_kb")
    argv = list(performance.iteration_plan.argv)
    assert "--scale" in argv and "{scale}" in argv and "{seed}" in argv
    assert performance.iteration_plan.resources.timeout_seconds <= 110


def test_a_release_recipe_is_refused_for_a_candidate_but_the_release_profile_still_works() -> None:
    """The two halves of the rule: never build a candidate with release flags,
    never time without.
    """
    recipe = parse_recipe((TOP_WORDS / "hidden/recipe.json").read_bytes())
    with pytest.raises(LockError, match="release measurement profile"):
        recipe.model_copy(update={"build_profile": "release"}).check(LOCK)
    for profile in ("sanitize_address_undefined", "sanitize_thread"):
        with pytest.raises(LockError, match="instrumentation"):
            recipe.model_copy(update={"build_profile": profile}).check(LOCK)
    # The shipped recipe is a plain debug build, and that is what the candidate lane uses.
    recipe.check(LOCK)
    release = LOCK.release_profile()
    assert release.release and not release.sanitized


# ------------------------------------------------------------- analyzer capabilities


def test_analyzer_capabilities_declare_the_right_check_ids_and_dimensions() -> None:
    cpp_profile = plugin.cpp_profile.profile
    for analyzer_id in ("clang_tidy", "cppcheck", "context", "asan", "tsan"):
        capabilities = plugin.analyzer(analyzer_id).capabilities()
        assert capabilities.analyzer_id == analyzer_id
        assert capabilities.languages == ("cpp",)
        assert capabilities.check_ids
        declared = {mapping.check_id for mapping in cpp_profile.rule_mappings if mapping.check_id}
        prefixes = {
            mapping.check_prefix for mapping in cpp_profile.rule_mappings if mapping.check_prefix
        }
        for check in capabilities.check_ids:
            # Either the id is written out in the profile, or it is the `.<tool>.all` summary of
            # the tool's own catch-all prefix mapping.
            assert check in declared or any(
                check == f"{prefix.rstrip('.')}.all" for prefix in prefixes
            ), (analyzer_id, check)


def test_the_capability_dimensions_are_the_ones_the_evidence_is_owned_by() -> None:
    expected = {
        "clang_tidy": (ScoreDimension.CODE_QUALITY, ScoreDimension.IDIOMATIC),
        "cppcheck": (ScoreDimension.CODE_QUALITY, ScoreDimension.ROBUSTNESS),
        "context": (
            ScoreDimension.CODE_QUALITY,
            ScoreDimension.IDIOMATIC,
            ScoreDimension.ROBUSTNESS,
        ),
        "asan": (ScoreDimension.ROBUSTNESS,),
        "tsan": (ScoreDimension.ROBUSTNESS,),
    }
    for analyzer_id, dimensions in expected.items():
        capabilities = plugin.analyzer(analyzer_id).capabilities()
        assert capabilities.produces_dimensions == dimensions, analyzer_id


def test_the_declared_check_ids_name_this_analyzer_and_resolve_in_the_profile() -> None:
    for analyzer_id in ("clang_tidy", "cppcheck", "context", "asan", "tsan"):
        for check in plugin.analyzer(analyzer_id).capabilities().check_ids:
            assert f".{analyzer_id}." in check, (analyzer_id, check)
            # Every capability a plugin advertises must be resolvable by the profile, or a caller
            # would be told a check exists that nothing can score.
            assert plugin.cpp_profile.resolve(check) is not None, check


def test_an_analyzer_with_no_explicit_mapping_advertises_the_tools_prefix_summary() -> None:
    """clang-tidy and cppcheck have catch-all hygiene prefixes; the summary names the namespace."""
    for analyzer_id in ("clang_tidy", "cppcheck"):
        checks = plugin.analyzer(analyzer_id).capabilities().check_ids
        assert f"cpp.{analyzer_id}.all" in checks, analyzer_id
    # The scanner and the sanitizers are declared check by check, with no catch-all.
    assert "cpp.context.all" not in plugin.analyzer("context").capabilities().check_ids
    assert "cpp.asan.all" not in plugin.analyzer("asan").capabilities().check_ids


def test_an_unknown_analyzer_cannot_plan_and_cannot_parse() -> None:
    """An analyzer this plugin does not implement is refused, not silently aliased to another."""
    unknown = plugin.analyzer("include-what-you-use")
    with pytest.raises(ValueError, match="does not apply to this task"):
        unknown.plan(_context(frozen(plugin)))
    with pytest.raises(KeyError):
        unknown.parse(DictArtifactReader({}), _any_analysis_plan(plugin, frozen(plugin)))


def test_an_analyzer_declares_the_same_plan_the_plugin_builds() -> None:
    view = frozen(plugin)
    context = _context(view)
    for plan in analysis(view):
        declared = plugin.analyzer(plan.analyzer_id).plan(context)
        assert declared.plan_id == plan.plan_id
        assert declared.argv == plan.argv
        assert declared.exit_semantics.classify(1, False) == plan.exit_semantics.classify(1, False)


def _context(view: Any) -> AnalysisContext:
    return AnalysisContext(
        task=view, candidate_digest="sha256:" + "2" * 64, candidate_paths=CONTEXT_FILES
    )


def _any_analysis_plan(target: CppLanguagePlugin, view: Any) -> Any:
    return analysis(view)[0]


def test_the_tool_identity_names_the_image_the_lock_and_the_rule_bundle() -> None:
    view = frozen(plugin)
    plans = {plan.analyzer_id: plan for plan in analysis(view)}
    tidy = plans["clang_tidy"].tool
    assert tidy.image_digest == plugin.identities.evaluator.digest
    assert tidy.lock_digest == plugin.toolchain.digest
    assert tidy.rule_bundle_digest == plugin.identities.rule_bundle_digest
    assert tidy.parser_version.startswith("pcb-cpp-parsers")
    assert plans["clang_tidy"].parser_id == "cpp-clang_tidy"
    assert plans["clang_tidy"].output_schema == "clang-tidy-text-v1"
    assert plans["cppcheck"].output_schema == "cppcheck-xml-v2"
    assert plans["context"].output_schema == "pcb-cpp-scan-v1"
    assert plans["asan"].output_schema == "pcb-cpp-sanitizer-v1"


# -------------------------------------------------------------------------- the protocol


def test_the_plugin_satisfies_the_shared_language_and_executable_protocols() -> None:
    """Structural conformance: every member both protocols require is present and callable."""
    missing = sorted(
        {
            (protocol.__name__, name)
            for protocol in (LanguagePlugin, ExecutableLanguagePlugin)
            for name in vars(protocol)
            if not name.startswith("_") and not hasattr(plugin, name)
        }
    )
    assert missing == [], f"CppLanguagePlugin does not implement {missing}"
    assert plugin.api_version == API_VERSION
    assert plugin.language_id == "cpp"
    assert plugin.overlay_prefix == "work/"
    assert set(plugin.candidate_suffixes) == {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}


def test_the_profile_the_protocol_expects_is_the_one_the_plugin_publishes() -> None:
    """``ExecutableLanguagePlugin.language_profile`` is the object that answers resolve/owner."""
    evaluator = plugin.language_profile
    assert evaluator is not plugin.profile("cpp-profile-v1")
    assert evaluator.resolve("cpp.asan.candidate-defect").owner == ScoreDimension.ROBUSTNESS
    assert evaluator.owner("cpp.context.throw-in-destructor") == ScoreDimension.ROBUSTNESS
    result = evaluator.evaluate(
        opportunities={"undefined_behavior_memory_safety": 1},
        # A clean set of scans with no findings: one measured item, a perfect score.
        observations=[],
        required_tools=(),
    )
    item = next(i for i in result.diagnostic if i.item_id == "undefined_behavior_memory_safety")
    assert (item.status, item.score_bp) == ("measured", 10_000)


def test_the_plugin_publishes_its_profile_and_rejects_unknown_versions() -> None:
    published = plugin.profile("cpp-profile-v1")
    assert published.language_id == "cpp"
    assert published.effective_for_scoring is False
    with pytest.raises(ValueError, match="unknown C\\+\\+ profile version"):
        plugin.profile("cpp-profile-v0")
    with pytest.raises(ValueError, match="unknown C\\+\\+ profile version"):
        plugin.profile("rust-profile-v1")


def test_the_plugin_exposes_its_lock_and_identities_as_facts_not_as_construction() -> None:
    assert plugin.toolchain is LOCK
    assert set(plugin.identities.images) == {"runtime", "evaluator", "performance"}
    assert plugin.cpp_profile.profile.language_id == "cpp"


def test_lock_errors_reach_the_caller_rather_than_being_swallowed() -> None:
    """A refused recipe is a hard failure at authoring time, not a silent fallback."""
    with pytest.raises(LockError):
        LOCK.check_sanitizers(("address", "thread"))
    recipe = parse_recipe((TOP_WORDS / "hidden/recipe.json").read_bytes())
    with pytest.raises(LockError):
        recipe.model_copy(update={"compiler": "gcc"}).check(LOCK)
