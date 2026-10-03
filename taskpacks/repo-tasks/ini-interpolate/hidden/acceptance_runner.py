"""Hidden acceptance inventory for the ini-interpolate repository task.

Runner contract (docs/implementation/repo-task-method.md):

    python acceptance_runner.py --workspace DIR --report FILE

The runner writes {"cases": [{"case_id", "outcome", "reason"}]} covering exactly the frozen
case inventory and exits 0 when the harness itself is healthy. A failing case is recorded in the
report, never raised out of ``main``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys

CASES = {}


def case(case_id):
    def register(function):
        CASES[case_id] = function
        return function

    return register


def _cfgkit(workspace):
    if workspace not in sys.path:
        sys.path.insert(0, workspace)
    importlib.invalidate_caches()
    import cfgkit

    return cfgkit


@case("expands-single-var")
def expands_single_var(workspace):
    cfgkit = _cfgkit(workspace)
    sections = cfgkit.load("[app]\nhome = ${PCB_TEST_HOME}/bin\n", {"PCB_TEST_HOME": "/opt/app"})
    if sections["app"]["home"] != "/opt/app/bin":
        raise AssertionError(f"got {sections['app']['home']!r}")


@case("expands-multiple-vars")
def expands_multiple_vars(workspace):
    cfgkit = _cfgkit(workspace)
    environ = {"PCB_TEST_SCHEME": "https", "PCB_TEST_HOST": "example.test", "PCB_TEST_PORT": "8443"}
    sections = cfgkit.load(
        "[app]\nurl = ${PCB_TEST_SCHEME}://${PCB_TEST_HOST}:${PCB_TEST_PORT}\n", environ
    )
    if sections["app"]["url"] != "https://example.test:8443":
        raise AssertionError(f"got {sections['app']['url']!r}")


@case("escaped-stays-literal")
def escaped_stays_literal(workspace):
    cfgkit = _cfgkit(workspace)
    sections = cfgkit.load("[app]\ntoken = \\${PCB_TEST_HOME}\n", {"PCB_TEST_HOME": "/opt/app"})
    if sections["app"]["token"] != "${PCB_TEST_HOME}":
        raise AssertionError(f"got {sections['app']['token']!r}")


@case("keys-untouched")
def keys_untouched(workspace):
    cfgkit = _cfgkit(workspace)
    sections = cfgkit.load(
        "[zone-${PCB_TEST_HOST}]\nname = ${PCB_TEST_HOST}\n", {"PCB_TEST_HOST": "example.test"}
    )
    if "zone-${PCB_TEST_HOST}" not in sections:
        raise AssertionError(f"section names must stay raw: {sorted(sections)}")
    if sections["zone-${PCB_TEST_HOST}"]["name"] != "example.test":
        raise AssertionError("values must still expand")


@case("missing-var-raises")
def missing_var_raises(workspace):
    cfgkit = _cfgkit(workspace)
    try:
        cfgkit.load("[app]\nhome = ${PCB_MISSING_VAR}\n", {})
    except cfgkit.ConfigError as error:
        if "PCB_MISSING_VAR" not in str(error):
            raise AssertionError(f"error must name the variable: {error}") from error
        return
    raise AssertionError("expected ConfigError for a missing variable")


@case("parse-unchanged")
def parse_unchanged(workspace):
    cfgkit = _cfgkit(workspace)
    sections = cfgkit.parse("[app]\nhome = ${PCB_TEST_HOME}/bin\n")
    if sections["app"]["home"] != "${PCB_TEST_HOME}/bin":
        raise AssertionError(f"parse() must stay raw: {sections['app']['home']!r}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--report", required=True)
    arguments = parser.parse_args(argv)
    results = []
    for case_id, function in CASES.items():
        try:
            function(arguments.workspace)
        except Exception as error:  # noqa: BLE001 - the report is the error channel
            results.append(
                {"case_id": case_id, "outcome": "fail", "reason": f"{type(error).__name__}: {error}"}
            )
        else:
            results.append({"case_id": case_id, "outcome": "pass", "reason": ""})
    with open(arguments.report, "w", encoding="utf-8") as handle:
        json.dump({"cases": results}, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
