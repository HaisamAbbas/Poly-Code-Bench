"""Run one command in the C guest, capture its output and clean up the build directory.

Usage:
    python pcb_c_run.py --name out/NAME --deadline SECONDS [--cleanup DIR]... [--merge] -- CMD ARG...

Writes ``NAME.out`` / ``NAME.err`` (the tool's stdout and stderr, truncated at ``--max-bytes``) and
``NAME.run.json`` (what this wrapper observed), then exits with the tool's own status:

* a tool that cannot be started exits 127 (missing) or 126 (not executable);
* a tool killed by a signal exits 128+signal, so a crash is never confused with a findings exit -
  which matters here because ASan, UBSan and Valgrind all abort, and that abort must not read as
  "the tool failed";
* with ``--merge`` stderr is written into the same stream as stdout, in order. The C test harness
  prints its case records on stdout while a sanitizer prints its report on stderr, so only a merged
  stream says which case a memory error belongs to;
* a tool still running at ``--deadline`` is killed (whole process group, so a hung test binary dies
  with its driver), its partial output is kept, and the wrapper exits 124.

The build directory is removed on every path. The sandbox snapshots the workspace after the run, and
an object-file tree is thousands of files the evidence does not need; removing it here, rather than
trusting the caller, is what keeps a killed or crashed build from poisoning the snapshot.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

DEFAULT_MAX_BYTES = 4 * 1024 * 1024
TIMEOUT_EXIT = 124
SCHEMA = "pcb-c-run-v1"


def _parse(argv):
    options = {
        "name": None,
        "deadline": None,
        "max_bytes": DEFAULT_MAX_BYTES,
        "cleanup": [],
        "merge": False,
    }
    index = 1
    while index < len(argv) and argv[index] != "--":
        flag = argv[index]
        if flag == "--merge":
            options["merge"] = True
            index += 1
        elif flag in ("--name", "--deadline", "--max-bytes", "--cleanup") and index + 1 < len(argv):
            value = argv[index + 1]
            if flag == "--cleanup":
                options["cleanup"].append(value)
            else:
                options[flag[2:].replace("-", "_")] = value
            index += 2
        else:
            raise SystemExit(2)
    command = argv[index + 1 :]
    if not command or options["name"] is None or options["deadline"] is None:
        raise SystemExit(2)
    options["deadline"] = float(options["deadline"])
    options["max_bytes"] = int(options["max_bytes"])
    return options, command


def _bound(path, limit):
    if os.path.getsize(path) > limit:
        with open(path, "r+b") as handle:
            handle.truncate(limit)
        return True
    return False


def main(argv):
    options, command = _parse(argv)
    name = options["name"]
    parent = os.path.dirname(name)
    if parent:
        os.makedirs(parent, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    code = 0
    note = None
    try:
        with open(name + ".out", "wb") as out, open(name + ".err", "wb") as err:
            try:
                child = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT if options["merge"] else err,
                    start_new_session=True,
                )
            except FileNotFoundError:
                err.write(("tool not found: %s\n" % command[0]).encode("utf-8"))
                code, note = 127, "not-found"
            except PermissionError:
                err.write(("tool not executable: %s\n" % command[0]).encode("utf-8"))
                code, note = 126, "not-executable"
            else:
                try:
                    returned = child.wait(timeout=options["deadline"])
                    code = 128 + (-returned) if returned < 0 else returned
                except subprocess.TimeoutExpired:
                    timed_out = True
                    code = TIMEOUT_EXIT
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
    finally:
        for directory in options["cleanup"]:
            shutil.rmtree(directory, ignore_errors=True)
    truncated = [
        key
        for key, path in (("stdout", name + ".out"), ("stderr", name + ".err"))
        if os.path.exists(path) and _bound(path, options["max_bytes"])
    ]
    with open(name + ".run.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": SCHEMA,
                "command": command[:3],
                "exit_code": code,
                "timed_out": timed_out,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "truncated": truncated,
                "note": note,
            },
            handle,
            sort_keys=True,
            separators=(",", ":"),
        )
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))