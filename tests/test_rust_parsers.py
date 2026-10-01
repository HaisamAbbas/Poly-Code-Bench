"""PCB-11-2: Rust parsers, libtest evidence and Miri handling, replayed from real recordings.

The recordings under ``tests/fixtures/rust_tool_output`` are real executions of the plugin's plans
in the pinned images (``scripts/record_rust_tool_fixtures.py``), so these tests check the parsers
against what the toolchain actually prints, not hand-written output.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from polycodebench_core.models import MeasurementStatus, Observation
from polycodebench_lang_rust import RustLanguagePlugin
from polycodebench_plugins_api.testreport import reconcile
from rust_plugin_support import Recording, frozen

plugin = RustLanguagePlugin()
profile = plugin.rust_profile
MEASURED = MeasurementStatus.MEASURED
MISSING = MeasurementStatus.MISSING
NOT_APPLICABLE = MeasurementStatus.NOT_APPLICABLE
VIEW = frozen(plugin)
INVENTORY = {g.group_id: g for g in plugin.inventory(VIEW)}


def analyze(scenario: str, analyzer: str, **reader: Any) -> list[Observation]:
    rec = Recording(scenario, f"rust.analysis.{analyzer}")
    return plugin.parse_analysis(rec.reader(**reader), rec.plan)


def scan(observations: list[Observation], tool: str) -> Observation:
    return next(o for o in observations if o.check_id == f"rust.{tool}.scan")


def checks(observations: list[Observation], status: MeasurementStatus = MEASURED) -> set[str]:
    return {
        o.check_id for o in observations if o.status == status and not o.check_id.endswith(".scan")
    }


def build(scenario: str, **reader: Any) -> tuple[str, str]:
    rec = Recording(scenario, "rust.build")
    return plugin.parse_build(rec.plan, rec.reader(**reader))


def group(scenario: str, group_id: str, **reader: Any):  # type: ignore[no-untyped-def]
    rec = Recording(scenario, f"rust.test.{group_id}")
    plan = next(g for g in plugin.test_plan(VIEW).groups if g.group_id == group_id)
    return plugin.parse_test_group(plan, INVENTORY[group_id], rec.reader(**reader), repetition=0)


def gate(scenario: str) -> str:
    records, controls = [], []
    for group_id in INVENTORY:
        got, control = group(scenario, group_id)
        records += got
        controls.append(control)
    return reconcile(tuple(INVENTORY.values()), records, controls).gate


# ------------------------------------------------------------------------- build evidence


def test_a_good_crate_builds_and_a_syntax_error_fails_with_the_compiler_message() -> None:
    assert build("reference")[0] == "pass"
    verdict, detail = build("compile-error")
    assert verdict == "fail" and "expected" in detail.lower()


def test_build_without_cargos_own_report_or_record_is_incomplete_never_pass() -> None:
    rec = Recording("reference", "rust.build")
    assert build("reference", drop_record=True)[0] == "incomplete"
    assert build("reference", drop=("out/build.run.json",))[0] == "incomplete"
    truncated = {**rec.files, "out/build.out": b'{"reason":"compiler-artifact"}\n'}
    assert build("reference", files=truncated)[0] == "incomplete"
    garbage = {**rec.files, "out/build.out": b"not json at all\n"}
    assert build("reference", files=garbage)[0] == "incomplete"


# ----------------------------------------------------------------------------- test evidence


def test_reference_and_alternative_pass_every_declared_case_with_qualified_ids() -> None:
    for scenario in ("reference", "alternative"):
        records, control = group(scenario, "behaviour")
        assert control.status == "finished", (scenario, control)
        assert {r.case_id for r in records} == {c.case_id for c in INVENTORY["behaviour"].cases}
        assert all(r.outcome == "pass" for r in records)
        assert gate(scenario) == "pass"


def test_nested_module_cases_are_matched_by_their_full_libtest_path() -> None:
    records, _ = group("reference", "behaviour")
    assert "behaviour::ordering::counts_never_increase_down_the_list" in {
        r.case_id for r in records
    }


def test_a_faulty_candidate_fails_exactly_the_intended_case() -> None:
    records, control = group("faulty-ties", "behaviour")
    assert control.status == "finished"
    failed = {r.case_id: r for r in records if r.outcome != "pass"}
    assert set(failed) == {"behaviour::ties_break_alphabetically"}
    assert "assertion" in failed["behaviour::ties_break_alphabetically"].reason.lower()
    assert gate("faulty-ties") == "fail"


def test_a_panicking_candidate_is_a_candidate_failure_with_the_panic_message() -> None:
    records, control = group("panics", "behaviour")
    assert control.status == "finished"
    zero = next(r for r in records if r.case_id == "behaviour::zero_k")
    assert zero.outcome == "fail" and "k must be positive" in zero.reason
    assert gate("panics") == "fail"


def test_a_hung_case_is_a_candidate_timeout_naming_the_case_in_flight() -> None:
    records, control = group("timeout", "behaviour")
    assert control.status == "candidate_timeout"
    assert control.in_flight_case == "behaviour::uppercase_runs_are_ordinary_words"
    # Cases that finished before the hang keep their evidence.
    assert any(r.outcome == "pass" for r in records)
    assert gate("timeout") == "fail"


def test_a_stack_overflow_kills_the_test_binary_and_is_attributed_to_the_candidate() -> None:
    _records, control = group("stack-overflow", "behaviour")
    assert control.status == "candidate_killed"
    assert control.in_flight_case is not None and control.in_flight_case.startswith("behaviour::")
    assert gate("stack-overflow") == "fail"


def test_compile_errors_in_candidate_code_are_candidate_collection_errors() -> None:
    records, control = group("compile-error", "behaviour")
    assert not records
    assert control.status == "finished"
    assert control.candidate_collection_errors and not control.harness_collection_errors
    assert gate("compile-error") == "fail"


def test_missing_evidence_is_a_harness_failure_never_a_pass() -> None:
    assert group("reference", "behaviour", drop_record=True)[1].status == "harness_failure"
    assert group("reference", "behaviour", drop=("out/behaviour.run.json",))[1].status == (
        "harness_failure"
    )
    rec = Recording("reference", "rust.test.behaviour")
    no_summary = {**rec.files, "out/behaviour.out": b"   Compiling x v0.1.0\n"}
    assert group("reference", "behaviour", files=no_summary)[1].status == "harness_failure"
    # A removed result line leaves a declared case unaccounted for: incomplete, not a pass.
    lines = [
        line
        for line in rec.files["out/behaviour.out"].decode().splitlines()
        if "zero_k" not in line
    ]
    dropped = {**rec.files, "out/behaviour.out": "\n".join(lines).encode()}
    records, control = group("reference", "behaviour", files=dropped)
    verdict = reconcile((INVENTORY["behaviour"],), records, [control])
    assert verdict.gate == "incomplete"


def test_an_undeclared_case_in_the_overlay_is_reported() -> None:
    rec = Recording("reference", "rust.test.behaviour")
    extra = (
        rec.files["out/behaviour.out"]
        .decode()
        .replace("test zero_k ... ok", "test zero_k ... ok\ntest surprise ... ok")
    )
    _records, control = group(
        "reference", "behaviour", files={**rec.files, "out/behaviour.out": extra.encode()}
    )
    assert control.unexpected_cases == ("behaviour::surprise",)


# --------------------------------------------------------------------- clippy and context


def test_clean_scans_are_measured_zero_and_only_then() -> None:
    for tool in ("clippy", "context"):
        observations = analyze("reference", tool)
        assert scan(observations, tool).status == MEASURED
        assert scan(observations, tool).value == 0
        assert not checks(observations)


def test_clippy_findings_carry_their_site_and_family() -> None:
    observations = analyze("defects", "clippy")
    found = checks(observations)
    assert found, "the quality-defective candidate must trip at least one selected lint"
    assert all(check.startswith("rust.clippy.") for check in found)
    assert all(o.location and o.location.path == "src/lib.rs" for o in observations if o.location)


def test_the_context_scan_reports_the_intended_defect_families() -> None:
    observations = analyze("defects", "context")
    families = {
        profile.resolve(o.check_id).equivalence_family  # type: ignore[union-attr]
        for o in observations
        if o.status == MEASURED and not o.check_id.endswith(".scan") and profile.resolve(o.check_id)
    }
    assert {"clone-redundant", "index-loop", "unwrap-unguarded"} <= families


def test_a_candidate_that_does_not_compile_makes_clippy_missing_not_clean() -> None:
    observations = analyze("compile-error", "clippy")
    assert scan(observations, "clippy").status == MISSING
    assert not checks(observations)


def test_clippy_without_cargos_build_finished_message_is_not_clean() -> None:
    rec = Recording("reference", "rust.analysis.clippy")
    lines = [
        line
        for line in rec.files["out/clippy.out"].decode().splitlines()
        if '"build-finished"' not in line
    ]
    observations = analyze(
        "reference", "clippy", files={**rec.files, "out/clippy.out": "\n".join(lines).encode()}
    )
    assert scan(observations, "clippy").status == MISSING


@pytest.mark.parametrize("tool", ["clippy", "context", "miri"])
def test_missing_record_unparsable_output_and_timeouts_never_look_clean(tool: str) -> None:
    rec = Recording("reference", f"rust.analysis.{tool}")
    assert scan(analyze("reference", tool, drop_record=True), tool).status == MISSING
    timeout = {**rec.record, "timed_out": True, "exit_code": None}
    assert scan(analyze("reference", tool, record=timeout), tool).status == MISSING
    garbage = {
        name: (
            b"\x00 not valid" if name.endswith((".out", ".json")) and "run" not in name else data
        )
        for name, data in rec.files.items()
    }
    result = analyze("reference", tool, files=garbage)
    assert scan(result, tool).status in {MISSING, NOT_APPLICABLE} and not checks(result)


def test_the_context_scan_must_account_for_its_whole_scope() -> None:
    rec = Recording("reference", "rust.analysis.context")
    document = json.loads(rec.files["out/context.json"])
    document["files"][0]["parsed"] = False
    changed = {**rec.files, "out/context.json": json.dumps(document).encode()}
    assert scan(analyze("reference", "context", files=changed), "context").status == MISSING


# ------------------------------------------------------------------------------------ miri


def test_miri_clean_ub_and_unsupported_are_three_different_results() -> None:
    clean = analyze("reference", "miri")
    ub = analyze("ub", "miri")
    unsupported = analyze("unsupported", "miri")
    assert (scan(clean, "miri").status, scan(clean, "miri").value) == (MEASURED, 0)
    assert scan(ub, "miri").status == MEASURED and scan(ub, "miri").value == 1
    assert checks(ub) == {"rust.miri.candidate-ub"}
    site = next(o for o in ub if o.check_id == "rust.miri.candidate-ub")
    assert site.location is not None and site.location.path == "src/lib.rs"
    assert scan(unsupported, "miri").status == NOT_APPLICABLE
    assert not checks(unsupported) and not checks(clean)


def test_unsupported_miri_is_neither_ub_nor_a_missing_scan_for_the_profile() -> None:
    """Only a task that requires Miri is hurt by an unsupported run; an optional one is scored."""
    opportunities = {"unsafe_soundness": 1}
    observations = [
        *analyze("unsupported", "miri"),
        *analyze("unsupported", "context"),
        *analyze("unsupported", "clippy"),
    ]
    optional = profile.evaluate(
        opportunities=opportunities, observations=observations, required_tools=("clippy", "context")
    )
    required = profile.evaluate(
        opportunities=opportunities,
        observations=observations,
        required_tools=("clippy", "context", "miri"),
    )
    unsafe = {i.item_id: i for i in optional.diagnostic}["unsafe_soundness"]
    assert unsafe.status == "measured" and unsafe.unique_violations == 0
    blocked = {i.item_id: i for i in required.diagnostic}["unsafe_soundness"]
    assert blocked.status == "missing" and blocked.reasons == ("required scan unsupported: miri",)


def test_miri_that_did_not_complete_is_missing_and_a_test_failure_alone_is_not_ub() -> None:
    rec = Recording("reference", "rust.analysis.miri")
    broken = {**rec.files, "out/miri.out": b"error[E0432]: unresolved import\n"}
    run = json.loads(rec.files["out/miri.run.json"])
    run["exit_code"] = 101
    broken["out/miri.run.json"] = json.dumps(run).encode()
    assert scan(analyze("reference", "miri", files=broken), "miri").status == MISSING
    failed_tests = {
        **rec.files,
        "out/miri.out": (
            b"running 1 test\ntest x ... FAILED\n\ntest result: FAILED. 0 passed; 1 failed;\n"
        ),
        "out/miri.run.json": json.dumps(run).encode(),
    }
    result = analyze("reference", "miri", files=failed_tests)
    assert scan(result, "miri").status == MEASURED and not checks(result)


# -------------------------------------------------------------- normalisation across tools


def test_defects_normalise_to_unique_issues_and_tokens_stay_out_of_the_score() -> None:
    raw = [*analyze("defects", "clippy"), *analyze("defects", "context")]
    merged = plugin.normalize(raw)
    keyed = [o.issue_key for o in merged if o.issue_key is not None]
    assert len(keyed) == len(set(keyed))
    opportunities = {
        "ownership_borrowing": 2,
        "iterators_traits": 2,
        "result_option": 1,
        "clippy": 3,
        "ownership_borrowing_api": 2,
        "iterator_trait_composition": 2,
        "result_option_modeling": 1,
    }
    defective = profile.evaluate(
        opportunities=opportunities, observations=merged, required_tools=("clippy", "context")
    )
    reference = profile.evaluate(
        opportunities=opportunities,
        observations=plugin.normalize(
            [*analyze("reference", "clippy"), *analyze("reference", "context")]
        ),
        required_tools=("clippy", "context"),
    )
    assert reference.diagnostic_score_bp == 10_000
    assert defective.diagnostic_score_bp is not None
    assert defective.diagnostic_score_bp < reference.diagnostic_score_bp
    # The reference clones and unwraps too (to_ascii_lowercase, or_insert...): tokens never score.
    assert all(i.unique_violations == 0 for i in reference.diagnostic if i.status == "measured")


def test_every_analyzer_declares_capabilities_that_the_profile_can_route() -> None:
    for analyzer in ("clippy", "context", "miri", "dependency"):
        capabilities = plugin.analyzer(analyzer).capabilities()
        assert capabilities.languages == ("rust",) and capabilities.check_ids
