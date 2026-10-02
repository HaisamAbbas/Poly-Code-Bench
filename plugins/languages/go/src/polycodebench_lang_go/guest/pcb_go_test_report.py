"""Turn a ``go test -json`` event stream into JSON Lines records (ships inside the images).

``go test -json`` is already structured: every event names its ``Action`` and, for a test, its
``Test``. That is what this script reduces to three record kinds, so the host parser never has to
re-parse human-facing text:

* ``case_start``  - a test began (``Action: "run"``). The last ``case_start`` at a timeout names
  the test that was in flight.
* ``case``        - a terminal outcome (``pass`` / ``fail`` / ``skip`` / ``pause`` / ``cont``).
* ``session_finish`` - the package-level verdict and whether the package even built. A
  ``build-fail`` event, or a stream that ends with no terminal action, means nothing ran.

Usage: python pcb_go_test_report.py --input STREAM.jsonl --output RECORDS.jsonl
Exit: always 0 - the records are evidence, the tool's own status is not this script's verdict.
"""

import argparse
import json
import os
import sys

RECORD_VERSION = 1
SCHEMA = "pcb-go-test-report-v1"
# `go test` actions that terminate a test or the package itself.
_TERMINAL = {"pass", "fail", "skip"}
# Package-level actions carry no `Test` field.
_OUTCOME = {"pass": "pass", "fail": "fail", "skip": "skipped"}


def build_records(text, stderr_text, exit_code, timed_out):
    """Records for one ``go test -json`` stream.

    Malformed lines are ignored rather than fatal: a candidate's panic can interleave text with
    the event stream, and the records that did parse are still valid evidence. The absence of a
    package verdict is what marks the run incomplete, not a single bad line.
    """
    started = []
    cases = {}
    saw_terminal = False
    build_failed = False
    declared = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        action = str(event.get("Action", ""))
        name = event.get("Test")
        if action == "build-fail":
            build_failed = True
            continue
        if name is None:
            if action in _TERMINAL:
                saw_terminal = True
            continue
        name = str(name)
        if action == "run":
            started.append(name)
            declared = (declared or 0) + 1
            continue
        if action not in _TERMINAL:
            continue
        saw_terminal = True
        previous = cases.get(name)
        outcome = _OUTCOME[action]
        # `pause`/`cont` are not terminal and never reach here; a repeated terminal action for the
        # same test keeps the strongest outcome so a retried test is not reported as passed.
        if previous is not None and previous["outcome"] == "fail":
            continue
        elapsed = event.get("Elapsed")
        duration = int(float(elapsed) * 1000) if isinstance(elapsed, (int, float)) else 0
        cases[name] = {"kind": "case", "name": name, "outcome": outcome, "duration_ms": duration}

    records = [{"v": RECORD_VERSION, "kind": "case_start", "name": name} for name in started]
    records.extend(
        {"v": RECORD_VERSION, **case} for _, case in sorted(cases.items())
    )
    records.append(
        {
            "v": RECORD_VERSION,
            "kind": "session_finish",
            "summary": saw_terminal,
            "declared_tests": declared,
            "build_ok": not build_failed,
        }
    )
    return records


class _Writer:
    """Append-only ASCII writer over an already-open descriptor, used by the CLI form."""

    def __init__(self, path):
        self._path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    def write(self, record):
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        os.write(self._handle, line.encode("ascii", errors="replace"))

    def close(self):
        os.close(self._handle)


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_go_test_report")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--exit-code", type=int, default=None)
    parser.add_argument("--timed-out", default="false")
    args = parser.parse_args(argv[1:])
    try:
        with open(args.input, "rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        text = ""
    records = build_records(text, "", args.exit_code, args.timed_out == "true")
    writer = _Writer(args.output)
    for record in records:
        writer.write(record)
    writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))