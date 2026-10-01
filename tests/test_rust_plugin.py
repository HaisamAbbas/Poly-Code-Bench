"""PCB-11-2: Rust plans, task validation, symbol index and plan inputs (offline).

Nothing here needs Docker: plans are data, and the Docker-backed behaviour of those plans is
covered by ``test_rust_conformance_docker.py`` and the recorded-output tests.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from polycodebench_core.identity import new_entity_id
from polycodebench_core.models import Candidate
from polycodebench_evaluation.plan_runner import PlanInputError, materialize_inputs
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_lang_rust.taskspec import discover_cases
from polycodebench_plugins_api import AnalysisContext, DictArtifactReader
from rust_plugin_support import draft, frozen, package_files, with_quality

plugin = RustLanguagePlugin()
CONTEXT_FILES = ("src/lib.rs",)


def candidate() -> Candidate:
    return Candidate.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "candidate",
                "candidate_id": new_entity_id(),
                "run_id": new_entity_id(),
                "task_id": "conformance-rust-top-words",
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


# ------------------------------------------------------------------------------ validation


def test_the_authored_fixture_package_validates() -> None:
    report = plugin.validate_task(draft())
    assert report.ok, [(i.code, i.message) for i in report.issues]
    assert report.plugin_id == "rust"


def codes(files: dict[str, bytes]) -> set[str]:
    return {issue.code for issue in plugin.validate_task(draft(files=files)).issues}


def test_validation_detects_oracle_inventory_drift_in_both_directions() -> None:
    files = package_files()
    files["hidden/tests/behaviour.rs"] += b"\n#[test]\nfn surprise() {}\n"
    assert "oracle-case-undeclared" in codes(files)
    files = package_files()
    oracle = json.loads(files["hidden/oracle.json"])
    oracle["groups"][0]["cases"].append(
        {"case_id": "behaviour::deleted", "required": True, "case_kind": "edge"}
    )
    files["hidden/oracle.json"] = json.dumps(oracle).encode()
    assert "oracle-case-absent" in codes(files)


def test_validation_rejects_missing_crate_files_references_and_bad_locks() -> None:
    files = package_files()
    del files["visible/repo/Cargo.toml"]
    assert "missing-file" in codes(files)
    files = package_files()
    del files["hidden/reference/src/lib.rs"]
    assert "missing-reference" in codes(files)
    files = package_files()
    # A registry package without Cargo's checksum does not pin the resolution.
    files["visible/repo/Cargo.lock"] += (
        b'\n[[package]]\nname = "dep"\nversion = "1.0.0"\n'
        b'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    )
    assert "cargo-lock-invalid" in codes(files)


def test_miri_applicability_is_internally_consistent() -> None:
    files = package_files()
    plan = files["hidden/quality-plan.yaml"].decode()
    files["hidden/quality-plan.yaml"] = plan.replace("miri: optional", "miri: required").encode()
    assert "quality-plan-invalid" in codes(files)
    files["hidden/quality-plan.yaml"] = (
        (plan.replace("miri: optional", "miri: unsupported") + "\n")
        .replace("clippy: 3", "clippy: 3\n  unsafe_soundness: 1")
        .encode()
    )
    assert "quality-plan-invalid" in codes(files)


def test_opportunity_items_are_the_profile_items_not_python_names() -> None:
    files = package_files()
    plan = files["hidden/quality-plan.yaml"].decode().replace("clippy: 3", "type_hints: 1")
    files["hidden/quality-plan.yaml"] = plan.encode()
    assert "quality-plan-invalid" in codes(files)


def test_case_discovery_follows_libtest_paths_and_ignores_comments_and_strings() -> None:
    source = b"""
    // #[test] fn commented() {}
    const NOTE: &str = "#[test] fn in_string() {}";
    #[test] fn top() {}
    mod outer {
        #[test] fn mid() {}
        mod inner {
            #[test]
            #[ignore]
            fn deep() {}
            fn helper() {}
        }
    }
    fn not_a_test() {}
    """
    assert discover_cases("tests/cases.rs", source) == [
        "cases::top",
        "cases::outer::mid",
        "cases::outer::inner::deep",
    ]


# ------------------------------------------------------------------------------ freeze view


def test_freeze_view_pins_the_crate_scaffold_by_digest_and_the_lock_identity() -> None:
    view = frozen(plugin)
    crate = view.quality["crate_files"]
    assert set(crate) == {"Cargo.toml", "Cargo.lock"}  # the candidate's src/lib.rs is excluded
    assert view.quality["cargo_lock_digest"].startswith("sha256:")
    other = package_files()
    other["visible/repo/Cargo.lock"] = (
        other["visible/repo/Cargo.lock"] + b'\n[[package]]\nname = "extra"\nversion = "0.2.0"\n'
    )
    changed = plugin.freeze_view(draft(files=other), "sha256:" + "1" * 64)
    assert changed.quality["cargo_lock_digest"] != view.quality["cargo_lock_digest"]


# ------------------------------------------------------------------------------------ plans


def test_every_plan_is_a_typed_argument_vector_pinned_to_a_recipe_image() -> None:
    view = frozen(plugin)
    ids = plugin.identities
    plans = [plugin.build_plan(view, candidate())]
    plans += [group.plan for group in plugin.test_plan(view).groups]
    plans += analysis(view)
    for plan in plans:
        assert plan.argv[0] in {"python"}  # only the in-guest runner / scripts are launched
        assert plan.resources.network == "none"
        assert plan.resources.executable_workspace is plan.plan_id.startswith(
            ("rust.build", "rust.test", "rust.analysis.clippy", "rust.analysis.miri")
        ) or plan.plan_id.startswith("rust.analysis")
    build = plans[0]
    assert build.image_digest == ids.runtime.digest
    clippy = next(p for p in plans if p.plan_id == "rust.analysis.clippy")
    assert clippy.image_digest == ids.evaluator.digest
    assert build.image_digest != clippy.image_digest  # the candidate cannot see the analyzers


def test_cargo_runs_offline_locked_with_the_deadline_inside_the_guest() -> None:
    view = frozen(plugin)
    build = plugin.build_plan(view, candidate())
    argv = list(build.argv)
    assert "--offline" in argv and "--locked" in argv
    assert argv[argv.index("--deadline") + 1] == str(build.resources.timeout_seconds - 3)
    assert "--cleanup" in argv  # the build directory never reaches the workspace snapshot
    assert build.resources.timeout_seconds <= 110  # the local Docker driver caps one exec at 120s


def test_test_plan_runs_one_group_per_invocation_with_one_thread_and_hidden_tests_as_overlay() -> (
    None
):
    view = frozen(plugin)
    plan = plugin.test_plan(view)
    groups = {g.group_id: g for g in plan.groups}
    assert set(groups) == {"behaviour", "stress"}
    behaviour = groups["behaviour"].plan
    argv = list(behaviour.argv)
    assert argv[argv.index("--test") + 1] == "behaviour"
    assert argv[-2:] == ["--", "--test-threads=1"] or "--test-threads=1" in argv
    roles = {item.path: item.role for item in behaviour.inputs}
    assert roles["work/tests/behaviour.rs"] == "overlay"
    assert roles["work/src/lib.rs"] == "candidate"
    assert roles["work/Cargo.toml"] == "config" and roles["work/Cargo.lock"] == "config"
    assert groups["stress"].repetitions == 3 and groups["behaviour"].repetitions == 1
    assert plan.expected_inventory_digest == view.inventory_digest


def test_a_candidate_cannot_supply_the_tests_or_the_crate_scaffold() -> None:
    view = frozen(plugin)
    group = plugin.test_plan(view).groups[0].plan
    files = package_files()
    config = {f"work/{path}": files[f"visible/repo/{path}"] for path in view.quality["crate_files"]}
    overlay = {"work/tests/behaviour.rs": files["hidden/tests/behaviour.rs"]}
    ok = materialize_inputs(
        group,
        {"candidate": {"src/lib.rs": b"x"}, "overlay": overlay, "config": config},
    )
    assert ok["work/tests/behaviour.rs"] == overlay["work/tests/behaviour.rs"]
    # A candidate that ships its own Cargo.toml / tests file is simply not consulted for them.
    with pytest.raises(PlanInputError):
        materialize_inputs(
            group,
            {"candidate": {"src/lib.rs": b"x", "tests/behaviour.rs": b"y", "Cargo.toml": b"z"}},
        )
    tampered = dict(config)
    tampered["work/Cargo.lock"] = b"tampered"
    with pytest.raises(PlanInputError):
        materialize_inputs(
            group, {"candidate": {"src/lib.rs": b"x"}, "overlay": overlay, "config": tampered}
        )


def test_analysis_plans_follow_task_applicability() -> None:
    view = frozen(plugin)
    plans = {p.analyzer_id: p for p in analysis(view)}
    assert set(plans) == {"clippy", "context", "miri"}  # miri: optional runs but never gates
    assert plans["clippy"].required and plans["context"].required
    assert not plans["miri"].required
    unsupported = with_quality(view, miri="unsupported")
    assert {p.analyzer_id for p in analysis(unsupported)} == {"clippy", "context"}
    required = with_quality(view, miri="required", required_analyzers=["clippy", "context", "miri"])
    assert {p.analyzer_id: p.required for p in analysis(required)}["miri"] is True


def test_clippy_uses_the_selected_lints_and_never_the_token_lints() -> None:
    plan = next(p for p in analysis(frozen(plugin)) if p.analyzer_id == "clippy")
    argv = list(plan.argv)
    assert argv[argv.index("--") + 1 :][:2] == ["--", "-A"] or "clippy::all" in argv
    enabled = {argv[i + 1] for i, part in enumerate(argv) if part == "-W"}
    assert "clippy::redundant_clone" in enabled
    # A bare token is not evidence: these lints must not be turned on by the plan.
    assert not enabled & {"clippy::unwrap_used", "clippy::expect_used", "clippy::clone_on_copy"}
    assert plan.exit_semantics.classify(101, False) == "error"  # a compile failure is not a scan
    assert plan.exit_semantics.classify(0, False) == "success"


def test_miri_plan_interprets_the_named_groups_with_the_pinned_nightly() -> None:
    plan = next(p for p in analysis(frozen(plugin)) if p.analyzer_id == "miri")
    argv = list(plan.argv)
    assert "+nightly-2026-09-30" in argv and "miri" in argv
    assert [argv[i + 1] for i, part in enumerate(argv) if part == "--test"] == ["behaviour"]
    assert {i.path for i in plan.inputs if i.role == "overlay"} == {"work/tests/behaviour.rs"}
    assert plan.tool.version.endswith("@nightly-2026-09-30")
    # Miri exits non-zero for UB, unsupported operations and compile errors alike.
    assert plan.exit_semantics.classify(1, False) == "findings"


def test_tool_identity_includes_the_cargo_lock_for_lock_dependent_tools_only() -> None:
    view = frozen(plugin)
    lock = view.quality["cargo_lock_digest"]
    by_id = {p.analyzer_id: p for p in analysis(view)}
    assert by_id["clippy"].tool.lock_digest == lock
    assert by_id["miri"].tool.lock_digest == lock
    assert by_id["context"].tool.lock_digest != lock
    assert plugin.build_plan(view, candidate()).tool.lock_digest != lock


def test_dependency_audit_is_planned_only_when_the_task_declares_an_inventory() -> None:
    view = frozen(plugin)
    assert "dependency" not in {p.analyzer_id for p in analysis(view)}
    with_inventory = view.model_copy(update={"dependency_inventory": ("serde",)})
    plans = {p.analyzer_id: p for p in analysis(with_inventory)}
    assert "dependency" in plans
    assert plans["dependency"].exit_semantics.classify(2, False) == "error"


def test_performance_plan_requires_a_declared_workload() -> None:
    assert plugin.performance_plan(frozen(plugin)) is None
    performance = {
        "workload_file": "perf/workload.rs",
        "workloads": [{"workload_id": "small", "scale": 100, "weight_bp": 10000, "input_seed": 1}],
        "hard_timeout_seconds": 30,
    }
    plan = plugin.performance_plan(with_quality(frozen(plugin), performance=performance))
    assert plan is not None
    assert plan.iteration_plan.image_digest == plugin.identities.performance.digest
    assert plan.iteration_plan.resources.executable_workspace


# ----------------------------------------------------------------------------------- symbols


def test_symbol_index_finds_public_surface_methods_and_ignores_local_items() -> None:
    source = b"""
    use std::collections::HashMap;
    pub const LIMIT: usize = 3; // not a function
    pub fn top_words(text: &str, k: usize) -> Vec<(String, usize)> {
        fn helper() {}            // local item, not part of the surface
        let _ = "pub fn fake() {}";
        Vec::new()
    }
    struct Private { field: i32 }
    pub struct Counter;
    impl Counter {
        pub fn new() -> Self { Counter }
        fn hidden(&self) {}
    }
    impl Default for Counter {
        fn default() -> Self { Counter }
    }
    pub mod nested { pub fn deep() {} }
    """
    index = plugin.symbols(DictArtifactReader({"src/lib.rs": source}))
    found = {s.qualified_name: s for s in index.symbols}
    assert found["crate::top_words"].public and found["crate::top_words"].symbol_kind == "function"
    assert "crate::top_words::helper" not in found and "crate::fake" not in found
    assert found["crate::LIMIT"].symbol_kind == "variable"
    assert not found["crate::Private"].public and found["crate::Counter"].public
    assert (
        found["crate::Counter::new"].symbol_kind == "method" and found["crate::Counter::new"].public
    )
    assert not found["crate::Counter::hidden"].public
    assert found["crate::Counter::default"].public  # a trait impl method has the trait's visibility
    assert found["crate::nested::deep"].public
    assert found["crate::top_words"].start_line < found["crate::top_words"].end_line
    assert index.language_id == "rust" and not index.unparsed_paths


def test_unbalanced_source_is_reported_unparsed_not_guessed() -> None:
    index = plugin.symbols(DictArtifactReader({"src/lib.rs": b"pub fn broken() { \n}\n}\n"}))
    assert index.unparsed_paths == ("src/lib.rs",)
    assert not index.symbols


def test_the_plugin_publishes_its_profile_and_rejects_unknown_versions() -> None:
    assert plugin.profile("rust-profile-v1").language_id == "rust"
    with pytest.raises(ValueError):
        plugin.profile("rust-profile-v0")


def test_comment_markers_inside_literals_do_not_desync_case_discovery() -> None:
    """Found by the pilot authors: `"//host"` and `'"'` once truncated lines / opened strings."""
    source = b"""
    #[test] fn unc_prefix() { assert!(reject("//host/share")); let _q = '"'; }
    #[test] fn after_literals() { let _s = "///"; }
    mod nested {
        #[test] fn deep() { let _t = r#"// not a comment "# ; }
    }
    """
    assert discover_cases("tests/lit.rs", source) == [
        "lit::unc_prefix",
        "lit::after_literals",
        "lit::nested::deep",
    ]
    index = plugin.symbols(
        DictArtifactReader(
            {"src/lib.rs": b'pub fn a() -> &\'static str { "//x" }\npub fn b() {}\n'}
        )
    )
    assert {s.qualified_name for s in index.symbols} >= {"crate::a", "crate::b"}
