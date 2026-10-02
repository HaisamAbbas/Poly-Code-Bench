"""Run one Maven or JVM command in the guest, capture its output and clean the build directory.

Usage:
    python pcb_java_run.py --name out/NAME --deadline SECONDS [--cleanup DIR]... [--merge] -- CMD ARG...

Writes ``NAME.out`` / ``NAME.err`` (the tool's stdout and stderr, truncated at ``--max-bytes``) and
``NAME.run.json`` (what this wrapper observed), then exits with the tool's own status:

* a tool that cannot be started exits 127 (missing) or 126 (not executable) - which for Maven means
  a plugin the image does not carry, a broken image rather than a candidate defect;
* a tool killed by a signal exits 128+signal, so a crash is never confused with a findings exit;
* with ``--merge`` stderr is written into the same stream as stdout, in order, because Maven
  announces which module it is running on stderr and prints results on stdout, so only a merged
  stream says which result line belongs to which module;
* a tool still running at ``--deadline`` is killed (whole process group, so a hung forked test JVM
  dies with its Maven parent), its partial output is kept, and the wrapper exits 124.

``JAVA_HOME``, ``MAVEN_OPTS`` and ``HOME`` are forced to the image's own pinned paths regardless of
what the plan's environment says, so a plan cannot point Maven at a different repository or a
different JDK than the one the image was sealed with.

The build directory is removed on every path. The sandbox snapshots the workspace after the run,
and ``target/`` is thousands of class files the evidence does not need; removing it here, rather
than trusting the caller, is what keeps a killed or crashed build from poisoning the snapshot.
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
PCB_ROOT = "/opt/pcb"


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


def _pinned_env():
    """The environment every command runs with: the image's own JDK, Maven and repository."""
    env = dict(os.environ)
    # The pinned Maven image is Eclipse Temurin; its JDK lives here. The old Debian OpenJDK path
    # does not exist in this image and made Maven select a missing java executable.
    env["JAVA_HOME"] = "/opt/java/openjdk"
    env["MAVEN_OPTS"] = "-Dmaven.repo.local=" + os.environ.get(
        "MAVEN_REPO_LOCAL", PCB_ROOT + "/m2/repository"
    )
    env["MAVEN_CONFIG"] = PCB_ROOT + "/maven"
    env["HOME"] = PCB_ROOT + "/home"
    env["LC_ALL"] = "C"
    env["TZ"] = "UTC"
    # A JVM that tries to open a perf data file on a read-only mount prints a warning per fork and,
    # for some tools, fails outright. Performance measurement is a separate image; for grading the
    # file is noise at best.
    env.setdefault("JAVA_TOOL_OPTIONS", "-XX:-UsePerfData")
    return env


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
                    env=_pinned_env(),
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
                "schema": "pcb-java-run-v1",
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
