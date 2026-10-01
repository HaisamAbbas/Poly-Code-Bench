"""Run one tool, capture stdout/stderr to files and propagate its exit status.

Usage: python pcb_capture.py --stdout PATH --stderr PATH [--max-bytes N] -- TOOL ARG...

Exit status is the tool's own. A tool that cannot be started exits 127 (missing dependency) and a
tool killed by a signal exits 128+signal, so a crash is never confused with a findings exit.
Output beyond ``--max-bytes`` is truncated and a ``<file>.truncated`` marker is written.
"""

import os
import subprocess
import sys

DEFAULT_MAX_BYTES = 4 * 1024 * 1024


def _parse(argv):
    options = {"stdout": None, "stderr": None, "max_bytes": DEFAULT_MAX_BYTES}
    index = 1
    while index < len(argv) and argv[index] != "--":
        flag = argv[index]
        if flag in ("--stdout", "--stderr", "--max-bytes") and index + 1 < len(argv):
            options[flag[2:].replace("-", "_")] = argv[index + 1]
            index += 2
        else:
            raise SystemExit(2)
    command = argv[index + 1 :]
    if not command or options["stdout"] is None or options["stderr"] is None:
        raise SystemExit(2)
    options["max_bytes"] = int(options["max_bytes"])
    return options, command


def _bound(path, limit):
    if os.path.getsize(path) > limit:
        with open(path, "r+b") as handle:
            handle.truncate(limit)
        with open(path + ".truncated", "wb") as marker:
            marker.write(b"1")


def main(argv):
    options, command = _parse(argv)
    for key in ("stdout", "stderr"):
        parent = os.path.dirname(options[key])
        if parent:
            os.makedirs(parent, exist_ok=True)
    with open(options["stdout"], "wb") as out, open(options["stderr"], "wb") as err:
        try:
            completed = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False
            )
        except FileNotFoundError:
            err.write(("tool not found: %s\n" % command[0]).encode("utf-8"))
            return 127
        except PermissionError:
            err.write(("tool not executable: %s\n" % command[0]).encode("utf-8"))
            return 126
    for key in ("stdout", "stderr"):
        _bound(options[key], options["max_bytes"])
    code = completed.returncode
    return 128 + (-code) if code < 0 else code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
