"""Compile the candidate's translation units and archive them (plain stdlib; ships in the images).

Usage:
    python pcb_cpp_build.py --name out/build --deadline SECONDS [--root /workspace]
                            [--compiler clang++] [--cxxflag FLAG]... [--include DIR]...
                            [--define NAME=VALUE]... --source FILE [--source FILE]...
                            [--archive out/libpcb.a]

Each ``--source`` is compiled on its own with ``-c`` into an object file under a temporary
directory, so one bad translation unit is attributed to a named file rather than to "the build".
The first failing source stops the sweep: later units cannot change a verdict that is already
decided, and stopping keeps a runaway candidate from burning the whole deadline.

Writes ``NAME.err`` (the compiler's own diagnostics) and ``NAME.run.json``::

    {"schema": "pcb-cpp-build-v1", "exit_code": 0, "timed_out": false, "duration_ms": 431,
     "compiled": ["src/top_words.cpp"], "failed_sources": []}

Exit codes: 0 when every source compiled, 1 when a source failed to compile, 124 when the deadline
fires, and 126/127 when the compiler itself cannot be started.
"""

import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SCHEMA = "pcb-cpp-build-v1"
TIMEOUT_EXIT = 124

_VALUE_FLAGS = {
    "--cxxflag": "cxxflags",
    "--include": "includes",
    "--define": "defines",
    "--source": "sources",
}
_VALUE_KEYS = {
    "--name": "name",
    "--deadline": "deadline",
    "--root": "root",
    "--compiler": "compiler",
    "--archive": "archive",
}


def _parse(argv):
    options = {
        "name": None,
        "deadline": None,
        "root": None,
        "compiler": "clang++",
        "cxxflags": [],
        "includes": [],
        "defines": [],
        "sources": [],
        "archive": None,
    }
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag in _VALUE_KEYS or flag in _VALUE_FLAGS:
            if index + 1 >= len(argv):
                raise SystemExit(2)
            value = argv[index + 1]
            if flag in _VALUE_KEYS:
                options[_VALUE_KEYS[flag]] = value
            else:
                options[_VALUE_FLAGS[flag]].append(value)
            index += 2
        else:
            raise SystemExit(2)
    if options["name"] is None or options["deadline"] is None or not options["sources"]:
        raise SystemExit(2)
    options["deadline"] = float(options["deadline"])
    return options


def _object_name(index, source):
    """A flat, collision-free object name that still carries the translation unit's identity."""
    stem = re.sub(r"[^A-Za-z0-9]+", "_", os.path.basename(source)).strip("_") or "unit"
    return "%02d_%s.o" % (index, stem)


def _command_line(options, source, obj):
    command = [options["compiler"]]
    for include in options["includes"]:
        command += ["-I", include]
    for define in options["defines"]:
        command += ["-D", define]
    command += list(options["cxxflags"])
    command += ["-c", source, "-o", obj]
    return command


def _start(command, err):
    """Start one process, mapping "cannot start" to the documented 126/127."""
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


def _kill(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _archiver():
    """`llvm-ar` when the pinned toolchain ships it, otherwise binutils `ar`."""
    candidates = ["llvm-ar", "ar"] + sorted(glob.glob("/usr/lib/llvm-*/bin/llvm-ar"))
    for candidate in candidates:
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            path = os.path.join(directory, candidate)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None


def _archive(options, compiled, workdir, err):
    """Pack the objects just produced; the archive is a deliverable, so its failure is the build's."""
    archiver = _archiver()
    if archiver is None:
        err.write(b"no archiver found (llvm-ar or ar)\n")
        return 126, "not-executable"
    target = options["archive"]
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(target):
        os.remove(target)
    objects = [
        os.path.join(workdir, _object_name(index, source)) for index, source in enumerate(compiled)
    ]
    child, failure = _start([archiver, "rcs", target] + objects, err)
    if failure:
        return failure, "not-found" if failure == 127 else "not-executable"
    if child.wait() != 0:
        return 1, ""
    return 0, ""


def main(argv):
    options = _parse(argv)
    if options["root"]:
        os.makedirs(options["root"], exist_ok=True)
        os.chdir(options["root"])
    name = options["name"]
    parent = os.path.dirname(name)
    if parent:
        os.makedirs(parent, exist_ok=True)
    workdir = tempfile.mkdtemp(prefix="pcb-cpp-build-")
    started = time.monotonic()
    compiled, failed = [], []
    timed_out = False
    code = 0
    note = ""
    try:
        with open(name + ".err", "wb") as err:
            for index, source in enumerate(options["sources"]):
                obj = os.path.join(workdir, _object_name(index, source))
                child, failure = _start(_command_line(options, source, obj), err)
                if failure:
                    code = failure
                    note = "not-found" if failure == 127 else "not-executable"
                    failed.append(source)
                    break
                remaining = options["deadline"] - (time.monotonic() - started)
                try:
                    returned = child.wait(timeout=max(0.0, remaining))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    code, note = TIMEOUT_EXIT, "deadline"
                    _kill(child)
                    failed.append(source)
                    break
                if returned != 0:
                    failed.append(source)
                    # A driver that cannot exec anything reports 126/127 itself; anything else is
                    # an ordinary compile error.
                    code = 127 if returned == 127 else (126 if returned == 126 else 1)
                    if code in (126, 127):
                        note = "not-found" if code == 127 else "not-executable"
                    break
                compiled.append(source)
            if not timed_out and code == 0 and options["archive"]:
                code, note = _archive(options, compiled, workdir, err)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    with open(name + ".run.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": SCHEMA,
                "exit_code": code,
                "timed_out": timed_out,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "compiled": compiled,
                "failed_sources": failed,
                "note": note,
            },
            handle,
            sort_keys=True,
            separators=(",", ":"),
        )
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
