"""Run one tool, capture stdout and stderr into separate files, and emit a JSON status.

Usage:
    python pcb_js_capture.py --stdout PATH --stderr PATH [--status PATH] [--max-bytes N]
        -- TOOL ARG...

Node tooling splits its streams meaningfully: ``eslint`` prints findings on stdout and its
``--format json`` document there too, ``npm`` prints its progress lines on stdout while every
diagnostic goes to stderr, and ``tsc`` reports on stdout but writes a build-failure summary to
stderr. Merging them (``pcb_js_run.py --merge``) destroys the ordering, so a status captured
for a *single* tool keeps the streams apart and records where each one landed.

The status document has the same schema as the run wrapper's::

    {"schema":"pcb-js-capture-v1","argv":[...],"exit_code":int,"timed_out":false,
     "duration_ms":int,"stdout_bytes":int,"stderr_bytes":int,"truncated":[...]}

``--status`` is optional: without it the document is printed to stdout, which keeps the tool
usable when the caller only wants the streams. ``truncated`` names the streams whose bytes were
clipped at ``--max-bytes``, so a downstream parser can tell "the tool printed nothing" from
"the tool printed more than we kept".

Exit status is the tool's own. A tool that cannot be started exits 127 (missing) or 126 (not
executable), and a tool killed by a signal exits 128+signal, so a crash is never confused with
a findings exit.
"""

import json
import os
import subprocess
import sys
import time

DEFAULT_MAX_BYTES = 4 * 1024 * 1024
SCHEMA = "pcb-js-capture-v1"


def _parse(argv):
    options = {
        "stdout": None,
        "stderr": None,
        "status": None,
        "max_bytes": DEFAULT_MAX_BYTES,
    }
    index = 1
    while index < len(argv) and argv[index] != "--":
        flag = argv[index]
        if flag in ("--stdout", "--stderr", "--status", "--max-bytes") and index + 1 < len(argv):
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
    size = os.path.getsize(path) if os.path.exists(path) else 0
    if size > limit:
        with open(path, "r+b") as handle:
            handle.truncate(limit)
        return size
    return size


def main(argv):
    options, command = _parse(argv)
    for key in ("stdout", "stderr"):
        parent = os.path.dirname(options[key])
        if parent:
            os.makedirs(parent, exist_ok=True)

    started = time.monotonic()
    with open(options["stdout"], "wb") as out, open(options["stderr"], "wb") as err:
        try:
            completed = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False
            )
            code = completed.returncode
        except FileNotFoundError:
            err.write(("tool not found: %s\n" % command[0]).encode("utf-8"))
            code = 127
        except PermissionError:
            err.write(("tool not executable: %s\n" % command[0]).encode("utf-8"))
            code = 126

    status = {"schema": SCHEMA, "argv": list(command), "exit_code": code, "timed_out": False}
    truncated = []
    for key in ("stdout", "stderr"):
        size = _bound(options[key], options["max_bytes"])
        if size > options["max_bytes"]:
            truncated.append(key)
        status[key + "_bytes"] = min(size, options["max_bytes"])
    status["duration_ms"] = int((time.monotonic() - started) * 1000)
    status["truncated"] = truncated

    document = json.dumps(status, sort_keys=True, separators=(",", ":"))
    if options["status"]:
        parent = os.path.dirname(options["status"])
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(options["status"], "w", encoding="utf-8") as handle:
            handle.write(document + "\n")
    else:
        sys.stdout.write(document + "\n")
    return 128 + (-code) if code < 0 else code


if __name__ == "__main__":
    sys.exit(main(sys.argv))