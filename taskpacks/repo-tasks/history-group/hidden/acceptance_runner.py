"""Hidden acceptance inventory for the history-group repository task.

Runner contract (docs/implementation/repo-task-method.md):

    python acceptance_runner.py --workspace DIR --report FILE

The runner writes {"cases": [{"case_id", "outcome", "reason"}]} covering exactly the frozen
case inventory and exits 0 when the harness itself is healthy.
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


def _histkit(workspace):
    if workspace not in sys.path:
        sys.path.insert(0, workspace)
    importlib.invalidate_caches()
    import histkit

    return histkit


@case("groups-by-area")
def groups_by_area(workspace):
    histkit = _histkit(workspace)
    out = histkit.render_summary(histkit.parse_entries("- core: fix parsing\n- cli: add flag\n"))
    if out != "core\n- fix parsing\n\ncli\n- add flag":
        raise AssertionError(f"got {out!r}")


@case("unprefixed-to-other")
def unprefixed_to_other(workspace):
    histkit = _histkit(workspace)
    out = histkit.render_summary(histkit.parse_entries("- update docs\n"))
    if out != "other\n- update docs":
        raise AssertionError(f"got {out!r}")


@case("preserves-order")
def preserves_order(workspace):
    histkit = _histkit(workspace)
    out = histkit.render_summary(histkit.parse_entries("- core: a\n- core: b\n- other: c\n"))
    if out != "core\n- a\n- b\n\nother\n- c":
        raise AssertionError(f"got {out!r}")


@case("empty-input")
def empty_input(workspace):
    histkit = _histkit(workspace)
    out = histkit.render_summary([])
    if out != "":
        raise AssertionError(f"got {out!r}")


@case("parser-api-unchanged")
def parser_api_unchanged(workspace):
    histkit = _histkit(workspace)
    entries = histkit.parse_entries("# Release notes\n\n- core: t\n")
    if entries != [("core", "t")]:
        raise AssertionError(f"got {entries!r}")
    try:
        histkit.parse_entries("not an entry")
    except histkit.HistoryError:
        return
    raise AssertionError("expected HistoryError for a non-entry line")


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
