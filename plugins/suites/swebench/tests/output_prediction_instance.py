"""Output-prediction fixture: predict a program's output from reading it (Prompt 28, PCB-28-1/3).

The target is a small Python module whose behaviour is fully determined by reading it. The graded
question is what the model *says*, so the fixture's candidates are answers, not programs: the
reference is the correct output, the faulty answer mis-states one character, and the no-op answer
predicts nothing.

The oracle's expected output was derived by running the target once at authoring time, and the
fixture records that fact (``execution_required``) on the methodology record rather than hiding it.
The model still cannot execute anything: the protocol carries no command tool, and
``validate_prediction_tools`` refuses any policy that could.

All three normalization modes are exercised across the variants, so the frozen rules bind rather
than merely get restated: exact bytes, collapsed-whitespace text, and typed JSON.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parent

INSTANCE_ID = "pcb-output-prediction-ops"

#: The target module. The oracle below was derived by running ``run(3)`` once at authoring time.
TARGET_SOURCE = '''\
"""Arithmetic helpers whose behaviour is read, not run."""

CAP = 40


def flow(start: int) -> list[int]:
    """Double ``start`` until it reaches ``CAP``, capping the final step."""
    values = [start]
    while values[-1] < CAP:
        nxt = values[-1] * 2
        values.append(min(nxt, CAP))
    return values


def report(values: list[int]) -> str:
    """Render one run as ``step=value`` fields, pipe-separated."""
    return " | ".join(f"{index + 1}={value}" for index, value in enumerate(values))


def run(start: int) -> str:
    return report(flow(start))
'''

#: Derived by executing ``run(3)`` once at authoring time. Recorded as provenance, never shipped to
#: the model: the oracle lives in the hidden bundle and the visible bundle carries only the source.
_EXPECTED_EXACT = "1=3 | 2=6 | 3=12 | 4=24 | 5=40"
_EXPECTED_TYPED: Mapping[str, object] = {"values": [3, 6, 12, 24, 40], "cap": 40}

INPUTS: Mapping[str, object] = {
    "target_paths": ["seqcalc.py"],
    "stdin": None,
    "scenario": "predict the exact string ``run(3)`` prints",
    "execution_required": True,
}

#: The normalization rules each variant grades under: one mode per task, so a grader cannot
#: accidentally apply the wrong mode.
NORMALIZATION: Mapping[str, Mapping[str, object]] = {
    "exact": {"mode": "exact_bytes"},
    "collapsed": {
        "mode": "normalized_text",
        "line_endings": "normalize_to_lf",
        "whitespace": "collapse_runs",
        "final_newline": "ignored",
    },
    "typed": {
        "mode": "typed_json",
        "numeric_tolerance": "0",
        "ignore_key_order": True,
        "required_types": ("object",),
    },
}

PROBLEM_STATEMENT = (
    "Predict the output of ``run(3)`` in the module below.\n\n"
    "State exactly what the function prints, character for character. You cannot execute the "
    "module: no command, test or patch tool exists in this protocol, so the answer has to come "
    "from reading the code and the scenario."
)

#: Candidate answers per mode. The reference is the correct prediction; the faulty answer
#: mis-states one digit; the no-op answer declines to predict.
CANDIDATE_ANSWERS: Mapping[str, Mapping[str, str]] = {
    "exact": {
        "reference": _EXPECTED_EXACT,
        "faulty": "1=3 | 2=6 | 3=12 | 4=24 | 5=41",
        "no-op": "the module prints nothing",
        # exact_bytes defines no parsing, so this is simply a different text answer, graded as
        # one - only typed_json can produce a parse error (Technical Spec 17.4).
        "different-text": "1=3 | 2=6 | 3=12 | 4=24",
    },
    "collapsed": {
        # Leading and trailing whitespace plus a newline collapse away, so this *is* a match:
        # grading it a mismatch would mean the rules were not actually frozen.
        "reference": "  1=3 | 2=6 | 3=12 | 4=24 | 5=40  \n",
        # Different run shapes, same characters: still a match under collapse_runs.
        "alternative": "1=3 |  2=6 |  3=12 |  4=24 |  5=40",
        "faulty": "1=3 | 2=6 | 3=12 | 4=24 | 5=41",
    },
    "typed": {
        "reference": '{"values": [3, 6, 12, 24, 40], "cap": 40}',
        # Key order is declared insignificant, so this matches too.
        "alternative": '{"cap": 40, "values": [3, 6, 12, 24, 40]}',
        # 41 is not 40 under a zero numeric tolerance: no judge may soften it.
        "faulty": '{"values": [3, 6, 12, 24, 41], "cap": 40}',
        "parse-error": '{"values": [3, 6, 12, 24, 40], "cap": }',
    },
}


def record() -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "repo": "polycodebench-authored/seqcalc",
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


def oracle(mode: str) -> Mapping[str, object]:
    """The frozen oracle for one normalization mode."""
    if mode == "exact":
        return {
            "normalization": NORMALIZATION["exact"],
            "expected": _EXPECTED_EXACT,
            "reason": "derived by executing run(3) at authoring time",
        }
    if mode == "collapsed":
        return {
            "normalization": NORMALIZATION["collapsed"],
            "expected": _EXPECTED_EXACT,
            "reason": "same run, graded under collapsed whitespace",
        }
    if mode == "typed":
        return {
            "normalization": NORMALIZATION["typed"],
            "expected_typed": _EXPECTED_TYPED,
            "reason": "structured value of the same run; numeric tolerance zero",
        }
    raise ValueError(f"unknown normalization mode {mode!r}")
