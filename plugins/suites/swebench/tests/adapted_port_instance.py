"""A deliberately adapted/ported instance: the same bug shape in Rust (Prompt 24, PCB-24-4).

This is the "altered/ported" half of E2E-36. It ports the native-compatible calculator defect to
Rust and departs from the native evaluation rules in two declared ways:

* the test runner's log parser differs (Rust's ``libtest`` output, not pytest's), and
* the maintenance rule differs - ``cargo test`` reports an ignored test as ``ignored``, which the
  native pass-to-pass rule treats as maintained while a strict port would call it a regression.

Those departures are recorded in ``protocol_deviations``, which makes the record ``adapted``: its
score is a PolyCodeBench measure for the port, not the native resolution rate, and must never be
mixed with native scores.
"""

from __future__ import annotations

from collections.abc import Mapping

from fixture_support import AUTHORED_BASE_COMMIT, unified_diff

INSTANCE_ID = "pcb-adapted-calc-rs"

_LIB_BROKEN = b"""\
//! Boolean token parsing for the ported calculator.

const TRUE_TOKENS: [&str; 4] = ["1", "true", "yes", "on"];
const FALSE_TOKENS: [&str; 4] = ["0", "false", "no", "off"];

/// Return the boolean value of a token.
pub fn parse_bool(text: &str) -> Result<bool, String> {
    let token = normalise(text);
    if TRUE_TOKENS.contains(&token.as_str()) {
        return Ok(true);
    }
    if FALSE_TOKENS.contains(&token.as_str()) {
        return Ok(false);
    }
    Err(format!("not a boolean token: {text:?}"))
}

/// Lowercase a token and trim surrounding whitespace.
pub fn normalise(text: &str) -> String {
    text.trim().to_lowercase()
}
"""

_LIB_FIXED = b"""\
//! Boolean token parsing for the ported calculator.

const TRUE_TOKENS: [&str; 4] = ["1", "true", "yes", "on"];
const FALSE_TOKENS: [&str; 4] = ["0", "false", "no", "off"];

/// Return the boolean value of a token.
pub fn parse_bool(text: &str) -> Result<bool, String> {
    let token = normalise(text);
    if TRUE_TOKENS.contains(&token.as_str()) {
        return Ok(true);
    }
    if FALSE_TOKENS.contains(&token.as_str()) {
        return Ok(false);
    }
    Err(format!("not a boolean token: {text:?}"))
}

/// Lowercase a token and trim whitespace and stray separators.
pub fn normalise(text: &str) -> String {
    text.trim().trim_matches(|c| c == '/' || c == '.').to_lowercase()
}
"""

#: A wrong answer that handles only the separator the reported symptom used. The graded test
#: asserts both "/" and "."; trimming one leaves the other returning ``Err``, so the candidate
#: compiles, keeps every pass-to-pass test green, and still fails the resolution measure.
_LIB_SYMPTOM_FIX = _LIB_BROKEN.replace(
    b"    let token = normalise(text);\n",
    b"    let token = normalise(text);\n    let token = token.trim_end_matches('/').to_string();\n",
)

_CARGO = b"""\
[package]
name = "calc"
version = "0.1.0"
edition = "2021"

[lib]
path = "src/lib.rs"
"""

_LIB_RS = b"""\
//! A ported calculator library.
pub mod calc;
"""

_TESTS_BEFORE = b"""\
use calc::calc::parse_bool;

#[test]
fn rejects_unknown_token() {
    assert!(parse_bool("maybe").is_err());
}
"""

_TESTS_AFTER = (
    _TESTS_BEFORE
    + b"""
#[test]
fn tolerates_trailing_separator() {
    assert_eq!(parse_bool("on/"), Ok(true));
    assert_eq!(parse_bool("off."), Ok(false));
}
"""
)

BASE: Mapping[str, bytes] = {
    "Cargo.toml": _CARGO,
    "src/lib.rs": _LIB_RS,
    "src/calc.rs": _LIB_BROKEN,
    "tests/parse_bool.rs": _TESTS_BEFORE,
    "README.md": b"# calc (ported)\n\nA Rust port of the calculator repair fixture.\n",
}

FIXED: Mapping[str, bytes] = {**BASE, "src/calc.rs": _LIB_FIXED}
ADDED_TESTS = {**BASE, "tests/parse_bool.rs": _TESTS_AFTER}

#: Upstream's Rust records key their test lists by bare test name, because ``parse_log_cargo``
#: reports names, not paths. Keeping the port's ids in that form is what lets the *upstream*
#: evaluator grade it; inventing path-qualified ids here would score it unresolved.
FAIL_TO_PASS = ("tolerates_trailing_separator",)
PASS_TO_PASS = ("rejects_unknown_token",)

#: The command that produces the log ``parse_log_cargo`` reads, recorded so the frozen protocol
#: names the same command the grading overlay runs. It carries no ``--quiet`` (that prints dots the
#: parser cannot read) and uses the repository's own ``--no-fail-fast`` and single-threaded test
#: convention, so one failure cannot hide the rest of the suite's results.
TEST_COMMAND = "cargo test --offline --no-fail-fast -- --test-threads=1"

PROBLEM_STATEMENT = (
    "parse_bool rejects tokens with a trailing separator.\n\n"
    'parse_bool("on/") returns Err even though "on" is a documented true token. The ported '
    "calculator reads configuration files whose values often carry a trailing slash or dot, so "
    "normalise must discard those separators as well as surrounding whitespace."
)

#: The two declared departures from the native evaluation rules.
PROTOCOL_DEVIATIONS = (
    "the log parser is Rust libtest output rather than pytest, so status lines are read with "
    "parse_log_cargo instead of the native pytest parser",
    "the pass-to-pass rule treats an ignored test as maintained only when the run reports it as "
    "ignored; the native rule treats skipped and ignored results identically, so this port's "
    "maintenance count is not the native one",
)


def gold_patch() -> str:
    return unified_diff(BASE, FIXED)


def test_patch() -> str:
    return unified_diff(BASE, ADDED_TESTS)


def record() -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "repo": "polycodebench-authored/calc-rs",
        "base_commit": AUTHORED_BASE_COMMIT,
        "problem_statement": PROBLEM_STATEMENT,
        "version": "authored-fixture-v1",
        "created_at": "2026-10-03T00:00:00Z",
        "test_patch": test_patch(),
        "gold_patch": gold_patch(),
        "fail_to_pass": list(FAIL_TO_PASS),
        "pass_to_pass": list(PASS_TO_PASS),
        "fail_to_fail": [],
        "pass_to_fail": [],
        "eval_type": "pass_and_fail",
        "log_parser": "parse_log_cargo",
        "protocol_deviations": list(PROTOCOL_DEVIATIONS),
        "test_command": TEST_COMMAND,
    }


#: A genuinely different correct fix: it lowercases before trimming and uses ``trim_matches`` over
#: a character slice, so it is a distinct patch from the reference rather than the same bytes. An
#: identical "alternative" would be a cache hit, not evidence that a second valid solution passes.
_LIB_ALTERNATIVE = _LIB_FIXED.replace(
    b"    text.trim().trim_matches(|c| c == '/' || c == '.').to_lowercase()\n",
    b"    text.to_lowercase().trim().trim_matches(['/', '.']).to_string()\n",
)

CANDIDATES: Mapping[str, Mapping[str, bytes]] = {
    "reference": FIXED,
    "alternative": {**BASE, "src/calc.rs": _LIB_ALTERNATIVE},
    "faulty": {**BASE, "src/calc.rs": _LIB_SYMPTOM_FIX},
    "no-op": dict(BASE),
}


def candidate_patches() -> dict[str, str]:
    return {name: unified_diff(BASE, tree) for name, tree in CANDIDATES.items()}
