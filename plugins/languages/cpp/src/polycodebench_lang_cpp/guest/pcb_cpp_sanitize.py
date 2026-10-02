"""Compile and run every hidden test group under one sanitizer, as a single plan.

Usage:
    python -B pcb_cpp_sanitize.py --name out/asan --deadline SECONDS [--detector asan|ubsan|tsan]
                                  [--compiler clang++] [--cxxflag FLAG]... [--include DIR]...
                                  --source FILE... --group behaviour=tests/behaviour.cpp
                                  [--group stress=tests/stress.cpp]... [--binary-dir out/asan]

One AnalysisPlan can only run one command, so the instrumented run of every hidden group is one
command here: compile and link each group against the candidate sources with the flags the plan
picked (they already carry the profile's ``-fsanitize=...``), run each binary, and classify each
run's output with the same ``classify()`` the single-group report uses. The plan's verdict is the
strongest verdict any group reached, so one group that found a real defect is never averaged away
by groups that were clean.

A group that does not compile is recorded as ``compile_failed`` and does not stop the other groups:
the remaining groups can still find a defect, and a link error in one group must not hide the
memory error in another.

Writes ``NAME.report.json`` in the ``pcb-cpp-sanitizer-v1`` schema, one combined ``NAME.out``/
``NAME.err`` pair and ``NAME.report.json``'s per-group list::

    {"schema": "pcb-cpp-sanitizer-v1", "detector": "asan", "verdict": "candidate-defect",
     "groups": [{"group_id": "behaviour", "compile_exit_code": 0, "exit_code": 1,
                 "timed_out": false, "verdict": "candidate-defect"}]}
Exit codes: 1 when the verdict is ``candidate-defect``, 3 when it is ``unsupported``, 0 when it is
``clean``, and the first failing group's own code otherwise. A deadline that fires with no verdict
exits 0 and writes ``timed_out: true``: the supervisor's own timeout classification stays
authoritative, and this wrapper must not turn "we stopped it" into a candidate defect.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

# 3 is the plan's declared `findings` code for a runtime that refused to judge. It is not 2: 2 is
# the runner's own argument/usage failure, and reusing it would make "unsupported sanitizer" and
# "the tool could not start" indistinguishable to the supervisor.
UNSUPPORTED_EXIT = 3

# The guest directory is on sys.path (the images copy these scripts flat into /opt/pcb/guest), so
# the classification the plan reports is literally the classification this script ran.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pcb_sanitizer_report import (  # noqa: E402  (import after the path is set up)
    DETECTORS,
    RECORD_VERSION,
    SCHEMA,
    classify,
    diagnostic_locations,
    digest,
    messages,
)

TIMEOUT_EXIT = 124
MAX_BYTES = 4 * 1024 * 1024
MAX_DIAGNOSTICS = 40
# Verdicts in the order that matters: one real defect anywhere outranks an inconclusive runtime
# anywhere, which outranks a group that merely crashed.
_SEVERITY = ("clean", "failed", "unsupported", "candidate-defect")


def _parse(argv):
    options = {
        "name": None,
        "deadline": None,
        "detector": "asan",
        "compiler": "clang++",
        "cxxflags": [],
        "includes": [],
        "sources": [],
        "groups": [],
        "binary_dir": None,
    }
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in ("--name", "--deadline", "--detector", "--compiler", "--binary-dir"):
            if index + 1 >= len(argv):
                raise SystemExit(2)
            options[flag[2:].replace("-", "_")] = argv[index + 1]
            index += 2
        elif flag in ("--cxxflag", "--include", "--source", "--group"):
            if index + 1 >= len(argv):
                raise SystemExit(2)
            value = argv[index + 1]
            options[
                {
                    "--cxxflag": "cxxflags",
                    "--include": "includes",
                    "--source": "sources",
                    "--group": "groups",
                }[flag]
            ].append(value)
            index += 2
        else:
            raise SystemExit(2)
    if options["name"] is None or options["deadline"] is None or not options["sources"]:
        raise SystemExit(2)
    if not options["groups"]:
        raise SystemExit(2)
    if options["detector"] not in DETECTORS:
        raise SystemExit(2)
    for group in options["groups"]:
        if "=" not in group or not group.split("=", 1)[0]:
            raise SystemExit(2)
    options["deadline"] = float(options["deadline"])
    options["binary_dir"] = options["binary_dir"] or (options["name"] + ".bin")
    return options


def _start(command, stream):
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ), 0
    except FileNotFoundError:
        stream.write(("command not found: %s\n" % command[0]).encode("utf-8"))
        return None, 127
    except PermissionError:
        stream.write(("command not executable: %s\n" % command[0]).encode("utf-8"))
        return None, 126
    except OSError as error:
        stream.write(("command cannot be started: %s\n" % error).encode("utf-8"))
        return None, 126


def _runner_command(binary):
    """Line-buffer the group's output where the image can; the harness also flushes per line."""
    stdbuf = shutil.which("stdbuf")
    return ["stdbuf", "-oL", "-eL", binary] if stdbuf else [binary]


def _kill(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _bounded(text, limit=MAX_BYTES):
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def main(argv):
    options = _parse(argv)
    name = options["name"]
    parent = os.path.dirname(name)
    if parent:
        os.makedirs(parent, exist_ok=True)
    os.makedirs(options["binary_dir"], exist_ok=True)
    started = time.monotonic()
    groups = []
    combined_out, combined_err = [], []
    timed_out = False
    for spec in options["groups"]:
        group_id, _, test_source = spec.partition("=")
        binary = os.path.join(options["binary_dir"], "%s.bin" % group_id)
        compile_log = name + ".%s.compile.log" % group_id
        run_log = name + ".%s.out" % group_id
        command = [options["compiler"]]
        for include in options["includes"]:
            command += ["-I", include]
        command += list(options["cxxflags"])
        command += list(options["sources"]) + [test_source, "-o", binary]
        remaining = options["deadline"] - (time.monotonic() - started)
        with open(compile_log, "wb") as log:
            child, failure = _start(command, log)
            if failure:
                compile_exit_code = failure
            else:
                try:
                    compile_exit_code = child.wait(timeout=max(0.0, remaining))
                except subprocess.TimeoutExpired:
                    _kill(child)
                    compile_exit_code = TIMEOUT_EXIT
                    timed_out = True
        if compile_exit_code != 0:
            # A group that never linked never ran: recording it as a run-phase verdict would
            # blame the candidate's memory safety for the test's own compile error.
            groups.append(
                {
                    "group_id": group_id,
                    "compile_exit_code": compile_exit_code,
                    "exit_code": compile_exit_code,
                    "timed_out": timed_out,
                    "verdict": "failed",
                    "reason": "compile_failed",
                }
            )
            combined_err.append("=== %s: compile failed ===\n%s" % (group_id, _read(compile_log)))
            if timed_out:
                break
            continue
        remaining = options["deadline"] - (time.monotonic() - started)
        group_timed_out = False
        exit_code = 0
        with open(run_log, "wb") as out:
            runner, failure = _start(_runner_command(binary), out)
            if failure:
                exit_code = failure
            else:
                try:
                    returned = runner.wait(timeout=max(0.0, remaining))
                    exit_code = 128 + (-returned) if returned < 0 else returned
                except subprocess.TimeoutExpired:
                    group_timed_out = True
                    timed_out = True
                    exit_code = TIMEOUT_EXIT
                    _kill(runner)
        output = _read(run_log)
        verdict = classify(output, exit_code, group_timed_out, options["detector"])
        groups.append(
            {
                "group_id": group_id,
                "compile_exit_code": 0,
                "exit_code": exit_code,
                "timed_out": group_timed_out,
                "reason": "",
                "verdict": verdict,
            }
        )
        combined_out.append("=== %s ===\n%s" % (group_id, output))
        if group_timed_out:
            break

    combined_text = "\n".join(combined_out) + (
        "\n" + "\n".join(combined_err) if combined_err else ""
    )
    combined_text, _ = _bounded(combined_text)
    runnable = [group for group in groups if group.get("reason") != "compile_failed"]
    if runnable:
        verdict = max((group["verdict"] for group in runnable), key=_SEVERITY.index)
    else:
        verdict = "failed"
    diagnostics = []
    if verdict == "candidate-defect":
        for kind, path, line, column in diagnostic_locations(combined_text, options["detector"]):
            diagnostics.append(
                {"kind": kind, "message": kind, "path": path, "line": line, "column": column}
            )
            if len(diagnostics) >= MAX_DIAGNOSTICS:
                break
    stdout_text = "\n".join(combined_out)
    stderr_text = "\n".join(combined_err)
    document = {
        "schema": SCHEMA,
        "report_version": RECORD_VERSION,
        "detector": options["detector"],
        "verdict": verdict,
        "inconclusive": verdict in ("unsupported", "failed"),
        "candidate_defect": verdict == "candidate-defect",
        "exit_code": max((group["exit_code"] for group in groups), default=0),
        "timed_out": timed_out,
        "groups": groups,
        "diagnostics": diagnostics,
        "messages": messages(combined_text)[:MAX_DIAGNOSTICS],
        "stdout_digest": digest(stdout_text),
        "stderr_digest": digest(stderr_text),
        "stdout_tail": stdout_text[-4000:],
        "stderr_tail": stderr_text[-4000:],
    }
    for stream, text in ((name + ".out", stdout_text), (name + ".err", stderr_text)):
        bounded, _ = _bounded(text)
        with open(stream, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(bounded)
    with open(name + ".report.json", "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))
    if timed_out and verdict == "failed":
        # The deadline decided this run, not the sanitizer; the supervisor owns that classification.
        return 0
    if verdict == "candidate-defect":
        return 1
    if verdict == "unsupported":
        return UNSUPPORTED_EXIT
    if verdict == "clean":
        return 0
    return document["exit_code"] or 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
