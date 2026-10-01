"""Rust guest tooling: context scanner, Miri result classifier and libtest report parsing.

The Miri tests below run the classifier against **recorded output from the real pinned Miri
interpreter** (`tests/fixtures/rust_miri_output/`, captured by running `cargo miri test` inside
`pcb-rust-evaluator` with `--network none`). Prompt 11 requires that a Miri *unsupported operation*
is distinct both from a clean scan and from candidate undefined behaviour, and that distinction is
the whole point of the evidence: the three real transcripts must classify differently.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GUEST = ROOT / "plugins" / "languages" / "rust" / "src" / "polycodebench_lang_rust" / "guest"
MIRI_FIXTURES = ROOT / "tests" / "fixtures" / "rust_miri_output"


def load_guest(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, GUEST / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = load_guest("pcb_rust_scan")
miri = load_guest("pcb_miri_report")
testreport = load_guest("pcb_rust_test_report")


def scan_text(path: str, source: str) -> list[dict[str, Any]]:
    return scanner.scan_text(path, textwrap.dedent(source))


# ------------------------------------------------------------------ Miri: three outcomes


def test_real_miri_output_distinguishes_clean_unsupported_and_candidate_ub() -> None:
    """The three outcomes must never be read as each other (PCB-11-2).

    These transcripts are verbatim output of the pinned Miri interpreter inside the offline
    evaluator image, not hand-written approximations.
    """
    real = {
        "clean": (0, False, "clean"),
        "unsupported-ffi": (1, False, "unsupported"),
        "candidate-ub": (1, False, "candidate-ub"),
    }
    for name, (code, timed_out, expected) in real.items():
        path = MIRI_FIXTURES / f"{name}.txt"
        assert path.is_file(), f"missing recorded Miri transcript: {path}"
        text = path.read_text(encoding="utf-8")
        observed = miri.classify(text, code, timed_out)
        assert observed == expected, (
            f"{name}: expected {expected}, got {observed}\n"
            f"first diagnostics: {miri._diagnostics(text)[:3]}"
        )


def test_unsupported_and_ub_differ_even_though_both_exit_non_zero() -> None:
    """Exit status alone cannot separate them; only the diagnostic body can."""
    unsupported = (MIRI_FIXTURES / "unsupported-ffi.txt").read_text(encoding="utf-8")
    ub = (MIRI_FIXTURES / "candidate-ub.txt").read_text(encoding="utf-8")
    # Both are non-zero exits, so a status-only reading would call them the same thing.
    assert miri.classify(unsupported, 1, False) != miri.classify(ub, 1, False)
    # An unsupported operation is inconclusive even when the program also panics.
    panicking_unsupported = unsupported + "\nerror: the evaluated program panicked\n"
    assert miri.classify(panicking_unsupported, 1, False) == "unsupported"
    # A timeout is never evidence of anything.
    assert miri.classify(ub, None, True) == "failed"
    # A compile error is not UB and not clean.
    assert miri.classify("error[E0425]: cannot find value `x` in this scope", 1, False) == "failed"


def test_miri_report_writes_a_machine_readable_verdict(tmp_path: Path) -> None:
    out = tmp_path / "stdout.txt"
    err = tmp_path / "stderr.txt"
    report = tmp_path / "miri.json"
    out.write_text(
        (MIRI_FIXTURES / "candidate-ub.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    err.write_text("", encoding="utf-8")
    assert (
        miri.main(
            [
                "pcb_miri_report",
                "--report",
                str(report),
                "--stdout",
                str(out),
                "--stderr",
                str(err),
                "--exit-code",
                "1",
                "--tool-version",
                "miri 0.1.0",
            ]
        )
        == 0
    )
    document = json.loads(report.read_text(encoding="utf-8"))
    assert document["schema"] == "pcb-miri-report-v1"
    assert document["verdict"] == "candidate-ub"
    assert document["candidate_ub"] is True
    assert document["inconclusive"] is False


# ------------------------------------------------------------------ context scanner


def test_unwrap_is_only_a_violation_when_the_result_is_not_guarded() -> None:
    found = scan_text(
        "src/lib.rs",
        """
        pub fn guarded(input: &str) -> usize {
            let parsed = input.parse::<i32>();
            if parsed.is_ok() {
                return parsed.unwrap() as usize;
            }
            0
        }

        pub fn matched(input: &str) -> i32 {
            match input.parse::<i32>() {
                Ok(value) => value,
                // The error arm is handled explicitly, so this is not a discarded error path.
                Err(_) => input.parse::<i32>().unwrap_or_default(),
            }
        }

        pub fn unguarded(input: &str) -> i32 {
            input.parse::<i32>().unwrap()
        }

        #[cfg(test)]
        mod tests {
            #[test]
            fn in_a_test_unwrap_is_fine() {
                assert_eq!(super::unguarded("1"), 1);
            }
        }
        """,
    )
    by_symbol = {(f["symbol"], f["rule"]): f["verdict"] for f in found}
    assert by_symbol[("guarded", "unwrap-guarded")] == "benign_in_context"
    assert by_symbol[("matched", "unwrap-guarded")] == "benign_in_context"
    assert by_symbol[("unguarded", "unwrap-unguarded")] == "violation"
    # A test asserting success may unwrap; the token proves nothing on its own.
    assert not any(name.startswith("tests") for name, _ in by_symbol)


def test_clone_and_unsafe_tokens_are_not_automatically_violations() -> None:
    found = scan_text(
        "src/lib.rs",
        """
        use std::sync::Arc;

        pub fn redundant(value: &&String) -> String {
            value.clone()
        }

        pub fn moves_out_of_a_collection(items: &[String]) -> Vec<String> {
            items.to_vec()
        }

        pub fn shares_an_arc(value: &Arc<String>) -> Arc<String> {
            Arc::clone(value)
        }

        pub fn justified(p: *const i32) -> i32 {
            // SAFETY: the caller guarantees p points at a live, initialised i32.
            unsafe { *p }
        }

        pub fn unjustified(p: *const i32) -> i32 {
            unsafe { *p }
        }
        """,
    )
    by_symbol = {(f["symbol"], f["rule"]): f["verdict"] for f in found}
    assert by_symbol[("redundant", "clone-redundant")] == "violation"
    assert by_symbol[("moves_out_of_a_collection", "clone-contextual")] == "benign_in_context"
    assert by_symbol[("shares_an_arc", "clone-redundant")] == "violation"
    assert by_symbol[("justified", "unsafe-documented")] == "benign_in_context"
    # unsafe without a justification is a hint for reviewer/judge evidence, never a silent penalty.
    assert by_symbol[("unjustified", "unsafe-unjustified")] == "hint"


def test_idiomatic_rust_produces_no_violations() -> None:
    found = scan_text(
        "src/lib.rs",
        """
        pub fn total(values: &[u32]) -> u32 {
            values.iter().copied().sum()
        }

        pub fn label(value: Option<u32>) -> String {
            match value {
                Some(v) => format!("{v}"),
                None => String::from("none"),
            }
        }

        pub fn lookup(values: &[u32], key: u32) -> Option<u32> {
            values.binary_search(&key).ok().map(|index| values[index])
        }
        """,
    )
    assert [f for f in found if f["verdict"] == "violation"] == []


# ------------------------------------------------------------------ libtest report parsing


def test_libtest_output_becomes_case_and_control_records() -> None:
    stdout = (
        "running 3 tests\n"
        "test tests::adds ... ok\n"
        "test tests::rejects ... FAILED\n"
        "test tests::ignored_one ... ignored\n"
        "\n"
        "test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out\n"
    )
    records = list(testreport.build_records(stdout, "", 101, timed_out=False))
    cases = {r["name"]: r["outcome"] for r in records if r["kind"] == "case"}
    assert cases == {
        "tests::adds": "pass",
        "tests::rejects": "fail",
        "tests::ignored_one": "skipped",
    }
    control = next(r for r in records if r["kind"] == "session_finish")
    assert control["declared_tests"] == 3
    assert control["observed_cases"] == 3
    assert control["exit_code"] == 101
    assert control["timed_out"] is False
    # A control record is what separates a candidate failure from a harness failure.
    assert control["summary"]


def test_a_hung_run_records_the_timeout_rather_than_failing_silently() -> None:
    records = list(testreport.build_records("running 2 tests\n", "", None, timed_out=True))
    control = next(r for r in records if r["kind"] == "session_finish")
    assert control["timed_out"] is True
    assert control["exit_code"] is None
    assert control["observed_cases"] == 0
    # The declared count is still reported so the caller can see what never finished.
    assert control["declared_tests"] == 2
