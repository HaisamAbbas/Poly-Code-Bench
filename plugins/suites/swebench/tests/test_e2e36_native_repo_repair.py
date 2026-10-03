"""E2E-36: native metric preserved, adapted labels correct, cache identity candidate-bound.

The two fixtures are the halves of the scenario:

* ``pcb-native-compatible-calc`` is authored and keeps the native record format and the native
  fail-to-pass/pass-to-pass evaluation, so it is labelled ``inspired``;
* ``pcb-adapted-calc-rs`` ports the same defect to Rust and declares two departures from the
  native evaluation rules, so it is labelled ``adapted`` and its record names what was altered.

Both are graded by the *pinned upstream evaluator*, never by a locally recomputed test fraction, and
both are graded through the protected overlay that binds every result to one frozen candidate.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import adapted_port_instance as adapted
import native_compatible_instance as native
import pytest
import yaml
from fixture_support import write_instance, write_manifest
from polycodebench_suites_swebench import (
    FAIL_TO_PASS,
    PASS_TO_PASS,
    GradingWorkspaceError,
    MethodologyRecord,
    NativeTaskDraft,
    SourceManifest,
    SuiteImportError,
    SweBenchStyleSuiteAdapter,
    UpstreamRunCache,
    build_candidate_workspace,
    candidate_digest_of,
    check_instance_leakage,
    evaluator_digest,
    grade_native_candidate,
    instance_digest,
    overlay_frozen_tests,
    run_identity,
    upstream_revision,
)
from polycodebench_suites_swebench import (
    test_spec_for as upstream_test_spec,
)
from polycodebench_suites_swebench.image_runner import DockerTestRunner

#: The pinned Rust runtime image, by digest, used to compile and run the port.
RUST_RUNTIME_IMAGE = (
    "pcb-rust-runtime@sha256:a3f88da16577b2516a9f243811fe407bf58158030686b869e8fb84b203bdfb69"
)

REGISTER_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "methodology" / "deviations-v1.yaml"
)
REGISTER = yaml.safe_load(REGISTER_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def adapter() -> SweBenchStyleSuiteAdapter:
    return SweBenchStyleSuiteAdapter(deviations=REGISTER)


def _authored_manifest(tmp_path: Path, module: object, name: str) -> SourceManifest:
    """Write an authored source manifest for one fixture module and return it parsed."""
    record = module.record()  # type: ignore[attr-defined]
    snapshot = module.BASE  # type: ignore[attr-defined]
    record_path = write_instance(tmp_path, record=record, snapshot=snapshot)
    return SourceManifest.from_yaml(
        write_manifest(
            tmp_path,
            {
                "schema_version": 1,
                "kind": "suite_source_manifest",
                "name": name,
                "dataset_revision": "polycodebench-authored-2026-10-03",
                "source_url": None,
                "authored": True,
                "license_expression": "CC0-1.0",
                "records": [record_path.relative_to(tmp_path).as_posix()],
            },
        )
    )


@pytest.fixture
def native_draft(adapter: SweBenchStyleSuiteAdapter, tmp_path: Path) -> NativeTaskDraft:
    manifest = _authored_manifest(tmp_path, native, "pcb-authored-native-compatible")
    drafts = adapter.import_tasks(manifest)
    assert len(drafts) == 1
    return drafts[0]


@pytest.fixture
def adapted_draft(adapter: SweBenchStyleSuiteAdapter, tmp_path: Path) -> NativeTaskDraft:
    manifest = _authored_manifest(tmp_path, adapted, "pcb-authored-adapted-port")
    drafts = adapter.import_tasks(manifest)
    assert len(drafts) == 1
    return drafts[0]


def _grade(draft: NativeTaskDraft, patch: str, cache: UpstreamRunCache | None = None):
    """Grade one candidate patch by running the task's own tests, in a fresh cache."""
    return grade_native_candidate(
        draft,
        candidate_patch=patch,
        test_command="python -m pytest -q",
        cache=cache if cache is not None else UpstreamRunCache(),
    )


# ------------------------------------------------------------------- pinned upstream


def test_the_native_metric_comes_from_the_pinned_upstream_evaluator() -> None:
    """A native metric exists only because the upstream package is installed at its pin."""
    assert upstream_revision() == "5.0.2"
    digest = evaluator_digest()
    assert digest.startswith("sha256:")
    assert digest != "sha256:" + "0" * 64


# ------------------------------------------------------------------------- the labels


def test_the_two_fixtures_carry_differing_and_correct_methodology_labels(
    adapter: SweBenchStyleSuiteAdapter,
    native_draft: NativeTaskDraft,
    adapted_draft: NativeTaskDraft,
) -> None:
    assert native_draft.methodology.compatibility_level == "inspired"
    assert adapted_draft.methodology.compatibility_level == "adapted"
    assert native_draft.methodology.compatibility_level != (
        adapted_draft.methodology.compatibility_level
    )
    # Neither may claim ``native``: both are authored and no official dataset is cleared.
    assert native_draft.methodology.upstream_source_url is None
    assert adapted_draft.methodology.upstream_source_url is None
    assert adapter.validate_methodology(native_draft.methodology).ok
    assert adapter.validate_methodology(adapted_draft.methodology).ok
    # The adapted record names exactly what it altered, which is what makes it adapted.
    assert adapted_draft.methodology.deviations == tuple(adapted.PROTOCOL_DEVIATIONS)
    assert adapted_draft.instance.protocol_deviations == tuple(adapted.PROTOCOL_DEVIATIONS)
    assert native_draft.methodology.deviations == ()


def test_a_record_claiming_native_without_an_upstream_identity_is_refused() -> None:
    with pytest.raises(ValueError, match="upstream source URL"):
        MethodologyRecord(
            suite_id="swebench",
            compatibility_level="native",
            official_sources=("https://example.invalid/",),
            native_metrics=("fail_to_pass_resolution_with_pass_to_pass_maintenance",),
            license_expression="MIT",
        )


def test_an_inspired_record_may_not_carry_an_upstream_url(
    adapter: SweBenchStyleSuiteAdapter, native_draft: NativeTaskDraft
) -> None:
    forged = native_draft.methodology.model_copy(
        update={"upstream_source_url": "https://www.swebench.com/data/"}
    )
    report = adapter.validate_methodology(forged)
    assert not report.ok
    assert any("official provenance" in issue for issue in report.issues)


def test_an_adapted_record_without_deviations_is_refused(
    adapter: SweBenchStyleSuiteAdapter, adapted_draft: NativeTaskDraft
) -> None:
    stripped = adapted_draft.methodology.model_copy(update={"deviations": ()})
    report = adapter.validate_methodology(stripped)
    assert not report.ok
    assert any("must record what was altered" in issue for issue in report.issues)


def test_an_official_manifest_cannot_import_an_authored_fixture(
    adapter: SweBenchStyleSuiteAdapter, tmp_path: Path
) -> None:
    record = native.record()
    record_path = write_instance(tmp_path, record=record, snapshot=native.BASE)
    manifest = SourceManifest.from_yaml(
        write_manifest(
            tmp_path,
            {
                "schema_version": 1,
                "kind": "suite_source_manifest",
                "name": "pcb-official",
                "dataset_revision": "swe-bench-verified-1",
                "source_url": "https://www.swebench.com/data/",
                "authored": False,
                "license_expression": "MIT",
                "records": [record_path.relative_to(tmp_path).as_posix()],
            },
        )
    )
    with pytest.raises(SuiteImportError, match="cannot be imported as official data"):
        adapter.import_tasks(manifest)


def test_an_official_manifest_must_name_its_source(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path,
        {
            "schema_version": 1,
            "kind": "suite_source_manifest",
            "name": "pcb-official",
            "dataset_revision": "swe-bench-verified-1",
            "source_url": None,
            "authored": False,
            "records": [],
        },
    )
    with pytest.raises(SuiteImportError, match="needs a source_url"):
        SourceManifest.from_yaml(path)


# --------------------------------------------------------------- no leakage of fixes


def test_the_visible_bundle_hides_the_gold_patch_the_test_patch_and_the_lists(
    native_draft: NativeTaskDraft,
) -> None:
    assert check_instance_leakage(native_draft.instance, native_draft.visible_files()) == ()
    visible = b"\n".join(native_draft.visible_files().values())
    assert native_draft.instance.gold_patch.encode("utf-8") not in visible
    assert native_draft.instance.test_patch.encode("utf-8") not in visible
    hidden = native_draft.hidden_files()
    assert set(hidden) == {"gold.patch", "test.patch", "expected-tests.json"}
    expected = json.loads(hidden["expected-tests.json"])
    assert expected[FAIL_TO_PASS] == list(native.FAIL_TO_PASS)
    assert expected[PASS_TO_PASS] == list(native.PASS_TO_PASS)


def test_a_snapshot_that_already_contains_the_post_fix_test_is_a_leak() -> None:
    """A solve bundle carrying the graded test body is a leak, and the check fails closed."""
    from polycodebench_suites_swebench import NativeTaskInstance

    record = native.record()
    list_fields = ("fail_to_pass", "pass_to_pass", "fail_to_fail", "pass_to_fail")
    instance = NativeTaskInstance.model_validate(
        {
            **record,
            **{key: tuple(record[key]) for key in list_fields},
            "protocol_deviations": tuple(record["protocol_deviations"]),
            "repo_files": dict(native.ADDED_TESTS),
        }
    )
    leaks = check_instance_leakage(
        instance, {f"repo/{p}": d for p, d in native.ADDED_TESTS.items()}
    )
    assert any("defines graded fail-to-pass test" in leak for leak in leaks)


def test_an_instance_whose_snapshot_leaks_is_refused(
    adapter: SweBenchStyleSuiteAdapter, tmp_path: Path
) -> None:
    record_path = write_instance(tmp_path, record=native.record(), snapshot=native.ADDED_TESTS)
    manifest = SourceManifest.from_yaml(
        write_manifest(
            tmp_path,
            {
                "schema_version": 1,
                "kind": "suite_source_manifest",
                "name": "pcb-leaky",
                "dataset_revision": "polycodebench-authored-2026-10-03",
                "source_url": None,
                "authored": True,
                "license_expression": "CC0-1.0",
                "records": [record_path.relative_to(tmp_path).as_posix()],
            },
        )
    )
    with pytest.raises(SuiteImportError, match="defines graded fail-to-pass test"):
        adapter.import_tasks(manifest)


# ------------------------------------------------------------- native metric preserved


def test_the_reference_patch_resolves_and_a_wrong_answer_does_not(
    native_draft: NativeTaskDraft,
) -> None:
    patches = native.candidate_patches()
    resolved = _grade(native_draft, patches["reference"])
    assert resolved.result.resolved
    assert resolved.result.resolution_status == "RESOLVED_FULL"
    assert resolved.result.native_metrics["fail_to_pass"] == len(native.FAIL_TO_PASS)
    assert resolved.result.native_metrics["pass_to_pass"] == len(native.PASS_TO_PASS)

    faulty = _grade(native_draft, patches["faulty"])
    assert not faulty.result.resolved
    assert faulty.result.native_metrics["fail_to_pass"] == 0

    unchanged = _grade(native_draft, patches["no-op"])
    assert not unchanged.result.resolved


def test_the_native_metric_is_reported_separately_from_the_polycodebench_gate(
    adapter: SweBenchStyleSuiteAdapter, native_draft: NativeTaskDraft
) -> None:
    outcome = _grade(native_draft, native.candidate_patches()["reference"])
    export = adapter.native_metrics(outcome.result, native_draft.methodology.compatibility_level)
    assert export.gate_evidence_separate
    assert export.quality_evidence_separate
    document = json.loads(export.as_json())
    assert document["methodology_label"] == "inspired"
    # The export carries the benchmark's own counts, not a PolyCodeBench correctness score.
    assert set(document["native_metrics"]) >= {
        "fail_to_pass",
        "pass_to_pass",
        "resolved",
        "resolution_status",
    }
    assert not any(key.endswith("score") for key in document["native_metrics"])


# ------------------------------------------------------------------ candidate identity


def test_a_cached_grade_is_only_returned_for_the_same_candidate(
    native_draft: NativeTaskDraft,
) -> None:
    cache = UpstreamRunCache()
    patches = native.candidate_patches()
    first = _grade(native_draft, patches["reference"], cache)
    assert not first.from_cache
    same = _grade(native_draft, patches["reference"], cache)
    assert same.from_cache
    assert same.run_identity == first.run_identity

    # A different candidate must never receive the resolved grade of the first one.
    other = _grade(native_draft, patches["faulty"], cache)
    assert not other.from_cache
    assert other.run_identity != first.run_identity
    assert not other.result.resolved
    assert other.result.candidate_digest != first.result.candidate_digest
    assert len(cache) == 2


def test_a_reimported_task_cannot_reuse_the_stale_grade(native_draft: NativeTaskDraft) -> None:
    """The task digest is part of the identity, so a changed task cannot reuse a cached result."""
    cache = UpstreamRunCache()
    outcome = _grade(native_draft, native.candidate_patches()["reference"], cache)
    assert (
        cache.get(
            task_digest="sha256:" + "f" * 64,
            candidate_digest=outcome.result.candidate_digest,
            evaluator_digest=outcome.result.evaluator_digest,
        )
        is None
    )
    # The same candidate against the same task is still reachable under its own identity.
    assert (
        cache.get(
            task_digest=native_draft.package_digest,
            candidate_digest=outcome.result.candidate_digest,
            evaluator_digest=outcome.result.evaluator_digest,
        )
        is outcome.result
    )


def test_the_evaluator_digest_is_part_of_the_run_identity() -> None:
    identity_a = run_identity(
        task_digest="sha256:" + "1" * 64,
        candidate_digest="sha256:" + "2" * 64,
        evaluator_digest=evaluator_digest(),
    )
    identity_b = run_identity(
        task_digest="sha256:" + "1" * 64,
        candidate_digest="sha256:" + "2" * 64,
        evaluator_digest="sha256:" + "9" * 64,
    )
    assert identity_a != identity_b


# ---------------------------------------------------------- protected grading overlay


def test_editing_a_visible_test_does_not_replace_native_acceptance(
    native_draft: NativeTaskDraft,
) -> None:
    """A candidate that rewrites the graded test still has to fix the source."""
    delete_graded_test = (
        "--- a/tests/test_calculator.py\n"
        "+++ b/tests/test_calculator.py\n"
        "@@ -1,3 +1,2 @@\n"
        ' """Tests present before the fix."""\n'
        " \n"
        "-from calculator.ops import add\n"
    )
    outcome = _grade(native_draft, delete_graded_test)
    assert not outcome.result.resolved
    # The frozen test patch restored the graded test body over the candidate's deletion.
    assert outcome.test_edit_overwritten


def test_a_candidate_patch_outside_the_allowlist_is_refused(native_draft: NativeTaskDraft) -> None:
    escape = "--- a/../escape.py\n+++ b/../escape.py\n@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n"
    with pytest.raises((GradingWorkspaceError, ValueError)):
        _grade(native_draft, escape)


def test_the_grading_workspace_never_contains_the_graded_test_before_the_overlay(
    native_draft: NativeTaskDraft,
) -> None:
    candidate = build_candidate_workspace(
        native_draft, candidate_patch=native.candidate_patches()["reference"]
    )
    graded = native.FAIL_TO_PASS[0].split("::")[0]
    assert "tolerates_trailing_separator" not in candidate[f"repo/{graded}"].decode("utf-8")
    overlaid, _ = overlay_frozen_tests(native_draft, candidate)
    assert "tolerates_trailing_separator" in overlaid[f"repo/{graded}"].decode("utf-8")


def test_the_adapted_port_is_graded_under_its_own_parser(
    adapted_draft: NativeTaskDraft,
) -> None:
    """The port declares its departures, and the record carries them into the test spec."""
    assert adapted_draft.instance.log_parser == "parse_log_cargo"
    assert adapted_draft.methodology.compatibility_level == "adapted"
    spec = upstream_test_spec(adapted_draft)
    assert spec.log_parser == "parse_log_cargo"
    assert spec.fail_to_pass == adapted.FAIL_TO_PASS
    from swebench.harness.log_parsers import PARSER_REGISTRY

    assert spec.log_parser in PARSER_REGISTRY


@pytest.mark.skipif(
    os.environ.get("PCB_TEST_DOCKER") != "1",
    reason="the Rust port is compiled in the pinned image (opt-in: PCB_TEST_DOCKER=1)",
)
def test_the_adapted_port_is_compiled_and_resolves_only_on_a_correct_patch(
    adapted_draft: NativeTaskDraft,
) -> None:
    """The port is compiled and executed, not described.

    The reference and alternative patches are distinct correct fixes, so both must compile and
    resolve; the faulty patch compiles but handles only one separator, so it fails resolution while
    keeping maintenance green. Grading goes through the pinned upstream ``parse_log_cargo`` on a
    real ``cargo test`` log, which is why a patch that does not build could not pass here.
    """
    runner = DockerTestRunner(RUST_RUNTIME_IMAGE, argv=tuple(adapted.TEST_COMMAND.split()))
    cache = UpstreamRunCache()
    resolved = grade_native_candidate(
        adapted_draft,
        candidate_patch=adapted.candidate_patches()["reference"],
        test_command=adapted.TEST_COMMAND,
        runner=runner,
        cache=cache,
    )
    assert resolved.result.resolved
    assert resolved.result.native_metrics["fail_to_pass"] == len(adapted.FAIL_TO_PASS)

    alternative = grade_native_candidate(
        adapted_draft,
        candidate_patch=adapted.candidate_patches()["alternative"],
        test_command=adapted.TEST_COMMAND,
        runner=runner,
        cache=cache,
    )
    assert alternative.result.resolved
    # A distinct patch is a distinct candidate: it must not reuse the reference's grade.
    assert not alternative.from_cache
    assert alternative.run_identity != resolved.run_identity

    faulty = grade_native_candidate(
        adapted_draft,
        candidate_patch=adapted.candidate_patches()["faulty"],
        test_command=adapted.TEST_COMMAND,
        runner=runner,
        cache=cache,
    )
    assert not faulty.result.resolved


# ------------------------------------------------------------ protocol and patch output


def test_the_frozen_protocol_freezes_the_base_commit_and_the_graded_lists(
    adapter: SweBenchStyleSuiteAdapter, native_draft: NativeTaskDraft
) -> None:
    protocol = adapter.protocol(native_draft)
    assert protocol.base_commit == native_draft.instance.base_commit
    assert protocol.fail_to_pass == native.FAIL_TO_PASS
    assert protocol.pass_to_pass == native.PASS_TO_PASS
    assert protocol.output_kind == "patch"
    overlay = protocol.grading_overlay()
    assert overlay["apply_test_patch"] is True
    assert overlay["expected_test_lists"][FAIL_TO_PASS] == list(native.FAIL_TO_PASS)


def test_the_patch_output_is_bound_to_the_frozen_task_and_candidate(
    adapter: SweBenchStyleSuiteAdapter, native_draft: NativeTaskDraft
) -> None:
    patch = native.candidate_patches()["reference"]
    output = adapter.patch_output(native_draft, patch)
    assert output["task_digest"] == native_draft.package_digest
    assert output["task_digest"] == instance_digest(native_draft.instance)
    assert output["changed_paths"] == ["calculator/parser.py"]
    assert output["base_commit"] == native_draft.instance.base_commit
    assert output["candidate_digest"] == candidate_digest_of(patch)


def test_the_evaluation_plan_overlays_the_test_patch_and_the_expected_lists(
    adapter: SweBenchStyleSuiteAdapter, native_draft: NativeTaskDraft
) -> None:
    plan = adapter.evaluation_plan(native_draft, native.candidate_patches()["reference"])
    assert plan["apply_test_patch"] is True
    assert plan["base_commit"] == native_draft.instance.base_commit
    assert plan["task_digest"] == native_draft.package_digest
    lists: Mapping[str, object] = plan["expected_test_lists"]  # type: ignore[assignment]
    assert lists[FAIL_TO_PASS] == list(native.FAIL_TO_PASS)
