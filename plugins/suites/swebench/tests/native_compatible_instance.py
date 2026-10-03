"""A native-compatible instance: authored, native record shape, native metric (Prompt 24).

The repository is a small calculator whose ``normalise`` drops a trailing separator, so
``parse_bool("on/")`` raises instead of returning True. The graded test for that behaviour does not
exist in the pre-fix snapshot - it arrives with the hidden test patch, exactly as an upstream task
introduces its new tests - so the solver must fix the source rather than satisfy a visible test.

This fixture is authored, so it is labelled ``inspired``: it keeps the native record format and the
native fail-to-pass/pass-to-pass evaluation, but no upstream dataset stands behind it (D-24-01).
"""

from __future__ import annotations

from collections.abc import Mapping

from fixture_support import AUTHORED_BASE_COMMIT, unified_diff

INSTANCE_ID = "pcb-native-compatible-calc"

_BASE_INIT = b'"""A small calculator package."""\n\n__all__ = ["parse_bool", "add"]\n'
_BASE_OPS = b"""\
\"\"\"Arithmetic helpers.\"\"\"


def add(left: int, right: int) -> int:
    return left + right
"""
_BASE_README = b"# calculator\n\nA tiny calculator package used as a repair fixture.\n"

_PARSER_BROKEN = b"""\
\"\"\"Value parsing for the calculator.\"\"\"

TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


def parse_bool(text: str) -> bool:
    \"\"\"Return the boolean value of a token.\"\"\"
    token = normalise(text)
    if token in TRUE_TOKENS:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"not a boolean token: {text!r}")


def normalise(text: str) -> str:
    \"\"\"Lowercase a token and trim surrounding whitespace.\"\"\"
    return text.strip().lower()
"""

_PARSER_FIXED = b"""\
\"\"\"Value parsing for the calculator.\"\"\"

TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


def parse_bool(text: str) -> bool:
    \"\"\"Return the boolean value of a token.\"\"\"
    token = normalise(text)
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    raise ValueError(f"not a boolean token: {text!r}")


def normalise(text: str) -> str:
    \"\"\"Lowercase a token and trim whitespace and stray separators.\"\"\"
    return text.strip().strip("/.").lower()
"""

_TESTS_BEFORE = b"""\
\"\"\"Tests present before the fix.\"\"\"

from calculator.ops import add
from calculator.parser import parse_bool


def test_add_sums() -> None:
    assert add(2, 3) == 5


def test_parse_bool_rejects_unknown() -> None:
    try:
        parse_bool("maybe")
    except ValueError as error:
        assert "not a boolean token" in str(error)
    else:
        raise AssertionError("expected ValueError")
"""

_TESTS_AFTER = (
    _TESTS_BEFORE
    + b"""

def test_parse_bool_tolerates_trailing_separator() -> None:
    assert parse_bool("on/") is True
    assert parse_bool("off.") is False
"""
)

#: A wrong answer that handles only the separator the reported symptom used. The graded test
#: asserts both "/" and "."; trimming one leaves the other raising, so the candidate fails the
#: resolution measure while keeping every pass-to-pass test green.
_PARSER_SYMPTOM_FIX = _PARSER_BROKEN.replace(
    b"    token = normalise(text)\n",
    b'    token = normalise(text).rstrip("/")\n',
)

#: A correct fix that also widens the accepted vocabulary, changing documented behaviour the
#: pass-to-pass tests do not cover. It resolves the instance, which is why the report records it
#: as a resolved alternative rather than a rejection.
_PARSER_OVERREACHING = _PARSER_FIXED.replace(
    b'{"1", "true", "yes", "on"}', b'{"1", "true", "yes", "on", "enable", "enabled"}'
).replace(b'{"0", "false", "no", "off"}', b'{"0", "false", "no", "off", "disable"}')

BASE: Mapping[str, bytes] = {
    "calculator/__init__.py": _BASE_INIT,
    "calculator/parser.py": _PARSER_BROKEN,
    "calculator/ops.py": _BASE_OPS,
    "tests/test_calculator.py": _TESTS_BEFORE,
    "README.md": _BASE_README,
}

#: The pre-fix behaviour is wrong when a token carries a trailing separator.
FIXED: Mapping[str, bytes] = {**BASE, "calculator/parser.py": _PARSER_FIXED}

#: The new test arrives with the hidden test patch, so the pre-fix snapshot cannot pass it.
ADDED_TESTS = {**BASE, "tests/test_calculator.py": _TESTS_AFTER}

FAIL_TO_PASS = ("tests/test_calculator.py::test_parse_bool_tolerates_trailing_separator",)
PASS_TO_PASS = (
    "tests/test_calculator.py::test_add_sums",
    "tests/test_calculator.py::test_parse_bool_rejects_unknown",
)

PROBLEM_STATEMENT = (
    "parse_bool rejects perfectly ordinary tokens.\n\n"
    'Calling parse_bool("on/") raises ValueError even though "on" is a documented true token. '
    "Configuration files in this project commonly carry a trailing separator (a slash or a dot) "
    'after a value, so tokens such as "yes." must still parse. normalise should discard those '
    "stray separators along with surrounding whitespace."
)


def gold_patch() -> str:
    return unified_diff(BASE, FIXED)


def test_patch() -> str:
    return unified_diff(BASE, ADDED_TESTS)


def record() -> dict[str, object]:
    return {
        "instance_id": INSTANCE_ID,
        "repo": "polycodebench-authored/calculator",
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
        "log_parser": "parse_log_pytest",
        "protocol_deviations": [],
        "test_command": "python -m pytest -q tests/test_calculator.py",
    }


#: Candidate workspaces admission exercises: the gold patch and three wrong answers.
CANDIDATES: Mapping[str, Mapping[str, bytes]] = {
    "reference": FIXED,
    "alternative": {**BASE, "calculator/parser.py": _PARSER_OVERREACHING},
    "faulty": {**BASE, "calculator/parser.py": _PARSER_SYMPTOM_FIX},
    "no-op": dict(BASE),
}


def candidate_patches() -> dict[str, str]:
    """Each candidate as the unified diff a solve session would submit."""
    return {name: unified_diff(BASE, tree) for name, tree in CANDIDATES.items()}
