"""Test-output prediction fixture: predict a declared test's result (Prompt 28, PCB-28-2).

The task hands the model a module, a named test and a stated scenario; the model predicts the
test's result and its reported output. The oracle is frozen and includes the *reason* a test
behaves as it does, so the model cannot answer correctly by guessing a status string - it has to
trace the code path the scenario exercises.

The candidates are answers: the reference names the real status and the real assertion failure,
the faulty answer names the right status with the wrong output, and the parse-error answer is not
the declared shape. A judge has no role here - the oracle is deterministic, so a mismatch is a
wrong answer however it is argued.
"""

from __future__ import annotations

from collections.abc import Mapping

INSTANCE_ID = "pcb-test-prediction-clamp"

#: The target module. Its ``clamp`` rejects a non-int rather than truncating, which is the
#: behaviour the scenario pins down.
TARGET_SOURCE = '''\
"""Bounds helpers whose test behaviour is read, not run."""

UPPER = 100
LOWER = 0


def clamp(value: int, upper: int = UPPER) -> int:
    """Return ``value`` bound into ``[LOWER, upper]``; reject a non-int outright."""
    if not isinstance(value, int):
        raise TypeError(f"clamp expects an int, got {type(value).__name__}")
    if value > upper:
        return upper
    if value < LOWER:
        return LOWER
    return value
'''

TESTED_FUNCTION = "clamp"
TEST_ID = "tests/test_clamp.py::test_clamp_rejects_non_int"
SCENARIO = (
    'test_clamp_rejects_non_int calls clamp("7", upper=100) inside '
    "pytest.raises(TypeError), then asserts the message names the received type."
)

#: The frozen oracle: what the test does and what it reports. Derived at authoring time by
#: running the test once, then frozen.
EXPECTED_STATUS = "passed"
EXPECTED_OUTPUT = "PASSED tests/test_clamp.py::test_clamp_rejects_non_int"
ORACLE_REASON = (
    "clamp('7', upper=100) raises TypeError with the received type in the message, so the "
    "pytest.raises context succeeds and the test passes"
)

INPUTS: Mapping[str, object] = {
    "target_paths": ["bounds.py", "tests/test_clamp.py"],
    "stdin": None,
    "scenario": SCENARIO,
    "test_id": TEST_ID,
    "execution_required": True,
}

#: The declared normalization. This task grades as exact bytes: a test id is an identifier, and
#: whitespace folding would let a wrong test name through.
NORMALIZATION: Mapping[str, object] = {"mode": "exact_bytes"}

PROBLEM_STATEMENT = (
    "Predict the declared test's result and the line pytest prints for it.\n\n"
    "Answer with the status word (passed/failed/error), then the test id exactly as pytest's "
    "short test summary would print it. You cannot execute anything: no command, test or patch "
    "tool exists in this protocol, so the answer has to come from reading the module and the "
    "scenario."
)

#: Candidate answers. The reference names the real status and the real summary line; the faulty
#: answer has the right status but the wrong summary; the parse-error answer is not even text.
CANDIDATE_ANSWERS: Mapping[str, str] = {
    "reference": f"{EXPECTED_STATUS} {TEST_ID}",
    # Right status, wrong test id: a different test passing is not this test passing.
    "faulty": f"{EXPECTED_STATUS} tests/test_clamp.py::test_clamp_accepts_ints",
    # The oracle's reason is not the answer: a report of the mechanism is not the declared
    # output, and no judge may substitute it.
    "wrong-output": ORACLE_REASON,
    "no-op": "cannot determine the test result",
    "parse-error": "",
}


def record() -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "repo": "polycodebench-authored/clamp",
        "base_commit": "a1b2c3d4e5f60718",
        "problem_statement": PROBLEM_STATEMENT,
        "version": "authored-fixture-v1",
        "created_at": "2026-10-03T00:00:00Z",
        "test_patch": "",
        "gold_patch": "",
        "fail_to_pass": [],
        "pass_to_pass": [],
        "fail_to_fail": [],
        "pass_to_fail": [],
        "eval_type": "pass_and_fail",
        "log_parser": "none",
        "protocol_deviations": [],
        "test_command": "",
    }


def oracle() -> Mapping[str, object]:
    """The frozen oracle for the declared test."""
    return {
        "normalization": NORMALIZATION,
        "expected": f"{EXPECTED_STATUS} {TEST_ID}",
        "reason": ORACLE_REASON,
    }
