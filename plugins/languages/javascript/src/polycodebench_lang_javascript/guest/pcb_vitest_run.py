"""Run one vitest group and convert its JSON report into the shared JSONL test-event stream.

Usage::

    python pcb_vitest_run.py --raw out/GROUP.raw.json --output out/GROUP.jsonl
        --root work --timeout-ms 10000 [-- file ...]

Why this exists as one script rather than two plan steps: an ``ExecutionPlan`` carries exactly one
typed argument vector and the contract bans shell launchers, so there is no place to put a second
command. The runner (``pcb_js_run.py``) owns the deadline, the process group and the exit status;
this script owns the *sequence* - run vitest, then convert what it wrote - and mirrors what
``pcb_c_build.py`` already does for C (compile, then execute) without a shell.

The exit status is vitest's own, so the plan's declared ``ExitSemantics`` keep their meaning:
``0`` all green, ``1`` findings, and anything else an infrastructure failure the host parser
classifies as ``missing`` rather than as a candidate result.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

VITEST = "/opt/pcb/js/node_modules/vitest/vitest.mjs"
CONVERTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pcb_vitest_report.py")


def _ensure_parent(path: str) -> None:
    """Create the directory a declared output lands in; the runner makes `out/`, not subpaths."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _parse(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    options: dict[str, Any] = {"raw": None, "output": None, "root": "work", "timeout_ms": 10000}
    index = 1
    files: list[str] = []
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            files = argv[index + 1 :]
            break
        if argument.startswith("--") and index + 1 < len(argv):
            key = argument[2:].replace("-", "_")
            value = argv[index + 1]
            options[key] = int(value) if key == "timeout_ms" else value
            index += 2
            continue
        raise SystemExit(2)
    if not options["raw"] or not options["output"]:
        raise SystemExit(2)
    return options, files


def main(argv: list[str]) -> int:
    options, files = _parse(argv)
    _ensure_parent(str(options["raw"]))
    _ensure_parent(str(options["output"]))
    command = [
        "node",
        VITEST,
        "run",
        "--reporter=json",
        f"--outputFile={options['raw']}",
        "--root",
        str(options["root"]),
        "--testTimeout",
        str(options["timeout_ms"]),
        "--passWithNoTests=false",
        *files,
    ]
    # No timeout of our own: the enclosing `pcb_js_run.py` owns the deadline and signals the whole
    # process group, reporting `timed_out: true` with exit 124. A competing internal deadline fired
    # first and turned a genuine candidate hang into `exit 2` - infrastructure failure - which is
    # exactly the distinction the timeout fixture exists to preserve.
    try:
        completed = subprocess.run(command, capture_output=True, check=False, cwd="/workspace")
    except OSError as error:
        sys.stderr.write(f"vitest did not run: {type(error).__name__}: {error}\n")
        return 2

    # An absent report must still produce a stream, so the converter runs unconditionally: it turns
    # a missing or corrupt report into a `collection_error` plus a terminal `session_finish`,
    # which the host reads as a harness failure rather than as a candidate result.
    converted = subprocess.run(
        [
            # `sys.executable` would be the *copied* interpreter under /opt/pcb/python, which cannot
            # start on its own: it needs the bundled loader and library path that
            # /usr/local/bin/python supplies (D-11-10). Invoking `python` goes through that shim;
            # anything else prints "GLIBC_2.38 not found" and the report is never converted.
            "python",
            "-I",
            "-B",
            "-S",
            CONVERTER,
            "--report",
            str(options["raw"]),
            "--output",
            str(options["output"]),
        ],
        capture_output=True,
        check=False,
    )
    if converted.returncode != 0:
        sys.stderr.write(
            (converted.stderr or b"").decode("utf-8", "replace")[-400:] or "conversion failed\n"
        )
        return 2
    if completed.returncode != 0 and not os.path.exists(str(options["raw"])):
        sys.stderr.write(
            (completed.stderr or b"").decode("utf-8", "replace")[-400:]
            or "vitest wrote no report\n"
        )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
