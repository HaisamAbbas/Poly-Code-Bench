"""Offline checks for the C plugin (Prompt 20: PCB-20-1, PCB-20-2, PCB-20-3).

Report texts are copied from real captures in ``tests/fixtures/c_tool_output`` (recorded by
``scripts/record_c_tool_fixtures.py`` against the pinned images), trimmed to the lines the
classifier reads. ``test_recorded_*`` replays those captures through the plugin's own parsers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polycodebench_core.models import MeasurementStatus
from polycodebench_lang_c import CLanguagePlugin, guestmods
from polycodebench_lang_c.recipe import (
    SANITIZER_FLAGS,
    SANITIZER_LINK_FLAGS,
    BuildRecipe,
    resolve_recipe,
)
from polycodebench_lang_c.taskspec import WarningPolicy
from polycodebench_plugins_api import AnalysisPlan, DictArtifactReader
from polycodebench_plugins_api.contracts import EXECUTION_RECORD_PATH

RECORDINGS = Path(__file__).parent / "fixtures" / "c_tool_output"
REPORT = guestmods.load_guest("pcb_c_sanitize_report")

ASAN_OVERFLOW = """=================================================================
==26==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x612000000160 at pc 0x59a1 bp 0x7ffd sp 0x7ffd
READ of size 72 at 0x612000000160 thread T0
    #0 0x59a155ea96ba in count_words /workspace/work/src/topwords.c:100:5
    #1 0x59a155ea9a6b in case_ties_break_alphabetically /workspace/work/tests/behaviour.c:22:5
    #2 0x59a155eac6ca in pcb_ctest_run /opt/pcb/rules/pcb_ctest.c:60:9
SUMMARY: AddressSanitizer: heap-buffer-overflow /workspace/work/src/topwords.c:100:5 in count_words
"""
LSAN_LEAK = """=================================================================
==26==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 288 byte(s) in 1 object(s) allocated from:
    #0 0x5efcd6c8d358 in __interceptor_calloc (/workspace/build/asan-behaviour+0xa6358)
    #1 0x5efcd6cc816d in count_words /workspace/work/src/topwords.c:66:15
    #2 0x5efcd6cc898b in case_empty_input_returns_zero /workspace/work/tests/behaviour.c:22:5

Indirect leak of 64 byte(s) in 1 object(s) allocated from:
    #0 0x5efcd6c8d358 in __interceptor_malloc (/workspace/build/asan-behaviour+0xa6358)
    #1 0x5efcd6cc816d in count_words /workspace/work/src/topwords.c:70:15

SUMMARY: AddressSanitizer: 352 byte(s) leaked in 2 allocation(s).
"""
UBSAN_SHIFT = (
    "/workspace/work/src/topwords.c:74:16: runtime error: shift exponent 512 is too large for "
    "32-bit type 'unsigned int'\n"
    "SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior /workspace/work/src/topwords.c:74:16\n"
)
VALGRIND_LEAK = """==26== Memcheck, a memory error detector
==26== 4,608 bytes in 1 blocks are definitely lost in loss record 4 of 5
==26==    at 0x48465EF: calloc (in /usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so)
==26==    by 0x109311: count_words (topwords.c:66)
==26==    by 0x10959D: case_long_input_is_handled (stress.c:30)
==26== ERROR SUMMARY: 1 errors from 1 contexts (suppressed: 0 from 0)
"""


# --------------------------------------------------------------------------- PCB-20-1 recipes


def test_guest_and_recipe_sanitizer_flags_agree() -> None:
    """The guest builds with its own copy of the flags; the plugin digests the recipe's copy."""
    build = guestmods.load_guest("pcb_c_build")
    assert dict(build._SANITIZER_FLAGS) == SANITIZER_FLAGS
    assert dict(build._SANITIZER_LINK_FLAGS) == SANITIZER_LINK_FLAGS


def test_address_lane_is_non_pie_and_release_links_are_hardened() -> None:
    """D-20-01: a PIE ASan binary dies before main on a high-entropy kernel, so the lane is non-PIE."""
    address = resolve_recipe(recipe="instrumented", sanitizer="address")
    assert "-fno-pie" in address.compile_flags()
    assert "-no-pie" in address.link_flags()
    # `-no-pie` is a link-driver flag; on a `-c` compile it would be a warning against the candidate.
    assert "-no-pie" not in address.compile_flags()
    undefined = resolve_recipe(recipe="instrumented", sanitizer="undefined")
    assert "-no-pie" not in undefined.link_flags()
    for recipe in ("runtime", "performance"):
        release = resolve_recipe(recipe=recipe)  # type: ignore[arg-type]
        assert release.link_flags() == ("-Wl,-z,now",)
        assert not any(flag.startswith("-fsanitize") for flag in release.compile_flags())


def test_performance_recipe_never_takes_instrumentation_or_werror() -> None:
    with pytest.raises(ValueError):
        resolve_recipe(recipe="performance", sanitizer="address")
    with pytest.raises(ValueError, match="measurement recipe"):
        BuildRecipe(
            recipe="performance",
            standard="c17",
            warning_set="standard",
            warnings_as_errors=True,
            sanitizer=None,
        ).validate()


def test_recorded_image_identities_keep_the_lanes_apart() -> None:
    ids = CLanguagePlugin().identities
    digests = {ids.runtime.digest, ids.evaluator.digest, ids.instrumented.digest, ids.performance.digest}
    assert len(digests) == 4
    assert ids.performance.instrumentation == "none"
    assert ids.runtime.instrumentation == "none"
    assert ids.instrumented.instrumentation != "none"
    assert ids.performance.tools.get("asan-runtime") == "absent"
    assert ids.instrumented.tools.get("asan-runtime") not in (None, "absent")


# --------------------------------------------------------------------------- PCB-20-3 warnings


def test_werror_requires_a_measured_clean_baseline() -> None:
    with pytest.raises(ValueError, match="baseline_warning_clean"):
        WarningPolicy(mode="werror", baseline_warning_clean=False, baseline_warnings=8)
    assert WarningPolicy(mode="werror", baseline_warning_clean=True).warnings_as_errors
    legacy = WarningPolicy(mode="warn", baseline_warnings=8)
    assert not legacy.warnings_as_errors


# --------------------------------------------------------------------------- PCB-20-2 lanes


def test_asan_finding_is_located_in_the_candidate() -> None:
    document = REPORT.report(ASAN_OVERFLOW, "address", 1, False)
    assert document["verdict"] == "finding"
    [entry] = document["findings"]
    assert entry["family"] == "bounds-violation"
    assert entry["file"].endswith("src/topwords.c") and entry["line"] == 100


def test_leak_sanitizer_reports_direct_leaks_only_once() -> None:
    """LeakSanitizer reports under its own banner; ignoring it read a leaking candidate as clean."""
    document = REPORT.report(LSAN_LEAK, "address", 1, False)
    assert document["verdict"] == "finding"
    assert [entry["family"] for entry in document["findings"]] == ["resource-leak"]
    assert document["findings"][0]["line"] == 66


def test_ubsan_family_comes_from_the_message() -> None:
    [entry] = REPORT.report(UBSAN_SHIFT, "undefined", 1, False)["findings"]
    assert entry["family"] == "shift-out-of-range"
    assert REPORT._ubsan_family("signed integer overflow: 2147483647 + 1") == "signed-overflow"
    assert REPORT._ubsan_family("division by zero") == "division-by-zero"
    assert REPORT._ubsan_family("load of misaligned address 0x1") == "misaligned-access"
    assert REPORT._ubsan_family("something new") == "undefined-behaviour"


def test_valgrind_leak_record_with_thousands_separator_is_a_leak() -> None:
    document = REPORT.report(VALGRIND_LEAK, "valgrind", 0, False)
    assert document["verdict"] == "finding"
    [entry] = document["findings"]
    assert entry["family"] == "resource-leak"
    assert entry["summary"] == "4608 bytes definitely lost"
    assert entry["file"] == "topwords.c" and entry["line"] == 66


@pytest.mark.parametrize(
    ("text", "lane", "exit_code", "timed_out", "verdict"),
    [
        # A startup SIGSEGV with no report is an instrument failure, never a clean scan.
        ("", "address", 139, False, "failed"),
        ("Segmentation fault (core dumped)\n", "address", 139, False, "failed"),
        ("", "undefined", None, True, "failed"),
        ("", "address", 124, False, "failed"),
        # Valgrind must print its own verdict; silence is not "0 errors".
        ("==1== Memcheck, a memory error detector\n", "valgrind", 0, False, "failed"),
        ("==1== ERROR SUMMARY: 0 errors from 0 contexts\n", "valgrind", 0, False, "clean"),
        ("==1==ASan runtime does not come first in initial library list\n", "address", 1, False, "unsupported"),
        ('{"v":1,"kind":"case","outcome":"pass"}\n', "address", 0, False, "clean"),
    ],
)
def test_lane_outcomes_are_classified_distinctly(
    text: str, lane: str, exit_code: int | None, timed_out: bool, verdict: str
) -> None:
    assert REPORT.classify(text, lane, exit_code, timed_out)[0] == verdict


def _recorded(scenario: str, plan_name: str, *, replace_out: str | None = None) -> tuple[AnalysisPlan, DictArtifactReader]:
    directory = RECORDINGS / scenario / plan_name
    plan = AnalysisPlan.model_validate_json((directory / "plan.json").read_text(encoding="utf-8"))
    files: dict[str, bytes] = {
        "out/" + path.name.removeprefix("out__"): path.read_bytes()
        for path in directory.iterdir()
        if path.name.startswith("out__")
    }
    files[EXECUTION_RECORD_PATH] = (directory / "execution.json").read_bytes()
    if replace_out is not None:
        stdout = next(key for key in files if key.endswith(".out"))
        files[stdout] = replace_out.encode("utf-8")
    return plan, DictArtifactReader(files)


def _scan(plugin: CLanguagePlugin, plan: AnalysisPlan, reader: DictArtifactReader) -> tuple[object, list[object]]:
    observations = plugin.parse_analysis(reader, plan)
    scan = next(o for o in observations if o.check_id.endswith(".scan"))
    return scan, [o for o in observations if o.issue_key is not None]


def test_an_unlocated_finding_is_still_a_finding_not_a_clean_scan() -> None:
    plugin = CLanguagePlugin()
    harness_only = ASAN_OVERFLOW.replace("/workspace/work/src/topwords.c:100:5", "/opt/pcb/rules/pcb_ctest.c:9:1")
    plan, reader = _recorded("reference", "c.analysis.asan.behaviour", replace_out=harness_only)
    scan, found = _scan(plugin, plan, reader)
    assert scan.status == MeasurementStatus.MEASURED and scan.value == 1
    [observation] = found
    assert observation.check_id == "c.asan.bounds-violation"
    assert "unlocated" in (observation.explanation or "")


@pytest.mark.parametrize(
    ("scenario", "plan_name", "check_id"),
    [
        ("buffer-overflow", "c.analysis.asan.behaviour", "c.asan.bounds-violation"),
        ("buffer-overflow", "c.analysis.valgrind.stress", "c.valgrind.bounds-violation"),
        ("resource-leak", "c.analysis.asan.behaviour", "c.asan.resource-leak"),
        ("resource-leak", "c.analysis.valgrind.stress", "c.valgrind.resource-leak"),
        ("undefined-shift", "c.analysis.ubsan.stress", "c.ubsan.shift-out-of-range"),
    ],
)
def test_recorded_defects_are_reported_under_the_profile_family(
    scenario: str, plan_name: str, check_id: str
) -> None:
    plugin = CLanguagePlugin()
    scan, found = _scan(plugin, *_recorded(scenario, plan_name))
    assert scan.status == MeasurementStatus.MEASURED and scan.value and scan.value > 0
    assert {o.check_id for o in found} == {check_id}
    # The profile maps the check, so the finding has an owner and a canonical issue family.
    assert all(o.primary_owner is not None for o in found)


@pytest.mark.parametrize(
    "plan_name",
    [
        "c.analysis.asan.behaviour",
        "c.analysis.asan.stress",
        "c.analysis.ubsan.behaviour",
        "c.analysis.ubsan.stress",
        "c.analysis.valgrind.stress",
    ],
)
def test_recorded_reference_is_clean_on_every_dynamic_lane(plan_name: str) -> None:
    plugin = CLanguagePlugin()
    scan, found = _scan(plugin, *_recorded("reference", plan_name))
    assert scan.status == MeasurementStatus.MEASURED and scan.value == 0
    assert not found


def test_recorded_compile_error_leaves_every_dynamic_lane_missing() -> None:
    plugin = CLanguagePlugin()
    for directory in sorted((RECORDINGS / "compile-error").glob("c.analysis.*")):
        if directory.name.startswith(("c.analysis.clang_tidy", "c.analysis.cppcheck")):
            continue
        scan, found = _scan(plugin, *_recorded("compile-error", directory.name))
        assert scan.status == MeasurementStatus.MISSING, directory.name
        assert not found
