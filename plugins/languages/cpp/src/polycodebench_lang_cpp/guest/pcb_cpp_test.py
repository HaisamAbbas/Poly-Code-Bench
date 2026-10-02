"""Compile, link and run one hidden test group against the candidate sources.

Usage:
    python pcb_cpp_test.py --name out/GROUP --deadline SECONDS [--compiler clang++]
                           [--cxxflag FLAG]... [--include DIR]... --source FILE...
                           --test FILE --binary out/GROUP.bin [--arg A]...

The candidate sources and the group's test translation unit are compiled and linked into
``--binary`` first; the binary then runs with whatever is left of the single ``--deadline``, so a
compile that eats the budget cannot buy itself extra run time.

``phase`` distinguishes the two failures that must never be confused: a test group that never got
to run (``"compile"``, with a non-zero ``compile_exit_code``) and a group that ran and failed
(``"run"``). A hidden test that does not link is an unverifiable claim, not a failing one.

Writes ``NAME.err`` (compiler diagnostics), ``NAME.out`` (the program's combined output) and
``NAME.run.json``::

    {"schema": "pcb-cpp-test-v1", "phase": "run", "compile_exit_code": 0, "exit_code": 1,
     "timed_out": false, "duration_ms": 55, "truncated": false}

Exit codes: the program's own status, 124 when the run hits the deadline (whole process group
killed, partial output kept), 126/127 when the compiler cannot be started.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

SCHEMA = "pcb-cpp-test-v1"
TIMEOUT_EXIT = 124
DEFAULT_MAX_BYTES = 4 * 1024 * 1024

_VALUE_KEYS = {
    "--name": "name",
    "--deadline": "deadline",
    "--compiler": "compiler",
    "--test": "test",
    "--binary": "binary",
}
_VALUE_LISTS = {
    "--cxxflag": "cxxflags",
    "--include": "includes",
    "--source": "sources",
    "--arg": "args",
}


def _parse(argv):
    options = {
        "name": None,
        "deadline": None,
        "compiler": "clang++",
        "cxxflags": [],
        "includes": [],
        "sources": [],
        "test": None,
        "binary": None,
        "args": [],
    }
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in _VALUE_KEYS or flag in _VALUE_LISTS:
            if index + 1 >= len(argv):
                raise SystemExit(2)
            value = argv[index + 1]
            if flag in _VALUE_KEYS:
                options[_VALUE_KEYS[flag]] = value
            else:
                options[_VALUE_LISTS[flag]].append(value)
            index += 2
        else:
            raise SystemExit(2)
    missing = (
        options["name"] is None
        or options["deadline"] is None
        or options["test"] is None
        or options["binary"] is None
        or not options["sources"]
    )
    if missing:
        raise SystemExit(2)
    options["deadline"] = float(options["deadline"])
    return options


def _command_line(options):
    command = [options["compiler"]]
    for include in options["includes"]:
        command += ["-I", include]
    command += list(options["cxxflags"])
    command += list(options["sources"]) + [options["test"], "-o", options["binary"]]
    return command


def _start(command, err):
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=err,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ), 0
    except FileNotFoundError:
        err.write(("compiler not found: %s\n" % command[0]).encode("utf-8"))
        return None, 127
    except PermissionError:
        err.write(("compiler not executable: %s\n" % command[0]).encode("utf-8"))
        return None, 126
    except OSError as error:
        err.write(("compiler cannot be started: %s\n" % error).encode("utf-8"))
        return None, 126


def _runner_command(binary, args):
    """Line-buffer the test binary's output where the image can.

    The protocol lines are read while the group is still running, so a block-buffered stdout would
    hide the case that was in flight when a hung group was killed. `stdbuf` is used when the image
    has it; the harness flushes every line itself, so without it the output is still per-line.
    """
    command = [binary] + list(args)
    stdbuf = shutil.which("stdbuf")
    return ["stdbuf", "-oL", "-eL"] + command if stdbuf else command


def _record(name, document):
    with open(name + ".run.json", "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))


def main(argv):
    options = _parse(argv)
    name = options["name"]
    parent = os.path.dirname(name)
    if parent:
        os.makedirs(parent, exist_ok=True)
    binary_parent = os.path.dirname(options["binary"])
    if binary_parent:
        os.makedirs(binary_parent, exist_ok=True)
    if os.path.exists(options["binary"]):
        os.remove(options["binary"])
    started = time.monotonic()
    with open(name + ".err", "wb") as err, open(name + ".out", "wb") as out:
        child, failure = _start(_command_line(options), err)
        if failure:
            err.flush()
            _record(
                name,
                {
                    "schema": SCHEMA,
                    "phase": "compile",
                    "compile_exit_code": failure,
                    "exit_code": failure,
                    "timed_out": False,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "truncated": False,
                },
            )
            return failure
        remaining = options["deadline"] - (time.monotonic() - started)
        try:
            compile_exit_code = child.wait(timeout=max(0.0, remaining))
        except subprocess.TimeoutExpired:
            _kill(child)
            compile_exit_code = TIMEOUT_EXIT
        err.flush()
        if compile_exit_code != 0:
            # The binary was never produced, so this group never ran: reporting it as a run-phase
            # failure would blame the candidate for a link error.
            _record(
                name,
                {
                    "schema": SCHEMA,
                    "phase": "compile",
                    "compile_exit_code": compile_exit_code,
                    "exit_code": compile_exit_code,
                    "timed_out": compile_exit_code == TIMEOUT_EXIT,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "truncated": False,
                },
            )
            return compile_exit_code
        remaining = options["deadline"] - (time.monotonic() - started)
        timed_out = False
        try:
            runner, failure = _start(_runner_command(options["binary"], options["args"]), out)
            if failure:
                exit_code = failure
            else:
                try:
                    returned = runner.wait(timeout=max(0.0, remaining))
                    exit_code = 128 + (-returned) if returned < 0 else returned
                except subprocess.TimeoutExpired:
                    timed_out = True
                    exit_code = TIMEOUT_EXIT
                    _kill(runner)
        finally:
            out.flush()
    truncated = False
    try:
        if os.path.getsize(name + ".out") > DEFAULT_MAX_BYTES:
            with open(name + ".out", "r+b") as handle:
                handle.truncate(DEFAULT_MAX_BYTES)
            truncated = True
    except OSError:
        pass
    _record(
        name,
        {
            "schema": SCHEMA,
            "phase": "run",
            "compile_exit_code": 0,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "truncated": truncated,
        },
    )
    return exit_code


def _kill(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv))
