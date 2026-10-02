"""Run one Node/npm command in the guest, capture its output and clean up build directories.

Usage:
    python pcb_js_run.py --name out/NAME --deadline SECONDS [--cleanup PATH]... [--merge]
        [--max-bytes N] -- CMD ARG...

Writes ``NAME.out`` / ``NAME.err`` (the tool's stdout and stderr, truncated at ``--max-bytes``)
and ``NAME.run.json`` with exactly five keys::

    {"schema":"pcb-js-run-v1","argv":[...],"exit_code":int|null,
     "timed_out":bool,"duration_ms":int}

The wrapper classifies every outcome, so a crash is never mistaken for a findings exit:

* a tool that exits normally reports its own status in ``exit_code``;
* a tool killed by a signal reports ``128 + signal`` (the shell convention), with
  ``timed_out`` false — a crashed run is not a slow run;
* a tool still running at ``--deadline`` is signalled (SIGTERM, then SIGKILL after a short
  grace period) against its whole process group, so a hung ``npm test`` dies with its npm
  parent instead of leaking; its partial output is kept and the wrapper exits 124 with
  ``timed_out`` true.

``argv`` is the command exactly as given, and ``exit_code`` is ``null`` only if the process
died in a way the platform refused to report.

Every ``--cleanup PATH`` is removed on every exit path. ``node_modules/.vite`` and
``.next/cache`` style directories are thousands of files the evidence never reads; removing
them here, rather than trusting the caller, is what stops a killed or crashed run from
poisoning the workspace snapshot.

Exit status: the tool's own status, 127 (missing) / 126 (not executable) when it could not be
started, or 124 when the deadline fired.
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
# Seconds between SIGTERM and SIGKILL. Long enough for a Node process to flush and exit, short
# enough that the deadline stays the dominant bound on the wall clock.
KILL_GRACE_SECONDS = 5.0

_POSIX = os.name == "posix"
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if not _POSIX else 0


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
    if os.path.exists(path) and os.path.getsize(path) > limit:
        with open(path, "r+b") as handle:
            handle.truncate(limit)
        return True
    return False


def _signal_process_group(child, sig):
    """Send ``sig`` to the whole process group, falling back to the direct child."""
    if _POSIX:
        try:
            os.killpg(os.getpgid(child.pid), sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        if sig == getattr(signal, "SIGKILL", None):
            child.kill()
        else:
            child.terminate()
    except (ProcessLookupError, OSError):
        pass


def _spawn(command, out, err, merge):
    spawn_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": out,
        "stderr": subprocess.STDOUT if merge else err,
    }
    if _POSIX:
        # A new session is what makes killpg work: without it the child shares the wrapper's
        # process group and killing the group would kill the wrapper too.
        spawn_kwargs["start_new_session"] = True
    elif _NEW_PROCESS_GROUP:
        spawn_kwargs["creationflags"] = _NEW_PROCESS_GROUP
    return subprocess.Popen(command, **spawn_kwargs)


def _remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def main(argv):
    options, command = _parse(argv)
    name = options["name"]
    parent = os.path.dirname(name)
    if parent:
        os.makedirs(parent, exist_ok=True)

    started = time.monotonic()
    timed_out = False
    code = None
    with open(name + ".out", "wb") as out, open(name + ".err", "wb") as err:
        try:
            try:
                child = _spawn(command, out, err, options["merge"])
            except FileNotFoundError:
                err.write(("tool not found: %s\n" % command[0]).encode("utf-8"))
                code = 127
            except PermissionError:
                err.write(("tool not executable: %s\n" % command[0]).encode("utf-8"))
                code = 126
            else:
                try:
                    returned = child.wait(timeout=options["deadline"])
                except subprocess.TimeoutExpired:
                    timed_out = True
                    code = TIMEOUT_EXIT
                    _signal_process_group(child, getattr(signal, "SIGTERM", signal.SIGTERM))
                    try:
                        child.wait(timeout=KILL_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        _signal_process_group(child, getattr(signal, "SIGKILL", signal.SIGKILL))
                        child.wait()
                else:
                    code = 128 + (-returned) if returned < 0 else returned
        finally:
            for directory in options["cleanup"]:
                _remove(directory)

    for key, path in (("stdout", name + ".out"), ("stderr", name + ".err")):
        _bound(path, options["max_bytes"])

    with open(name + ".run.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "pcb-js-run-v1",
                "argv": list(command),
                "exit_code": code,
                "timed_out": timed_out,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
            handle,
            sort_keys=True,
            separators=(",", ":"),
        )
    return TIMEOUT_EXIT if timed_out else (code if code is not None else 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv))