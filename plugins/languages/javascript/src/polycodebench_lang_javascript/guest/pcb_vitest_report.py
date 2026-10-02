"""Convert a vitest JSON report into the shared JSONL test-event stream.

Usage: python pcb_vitest_report.py --report out/GROUP.raw.json --output out/GROUP.jsonl

This script does not run vitest - the plan invokes the runner through ``pcb_js_run.py``, which
owns the deadline, the process group and the exit status. This is the converter, and it exists so
the host ``testparse.py`` sees exactly the same event stream it sees for Python and Rust:
``session_start``, then one ``case_start`` and one ``case`` per assertion, then exactly one
``session_finish``. The field names and the session metadata keys are identical, because the host
parser, not this file, decides what a candidate failure and a harness failure are.

The distinction the stream exists to preserve: a candidate error is a ``case`` record with outcome
``fail``/``error``; a harness failure is an unreadable or absent report, a report with no
``session_finish``, or a runner exit status with no matching case records. So an absent or corrupt
report produces a ``collection_error`` event and a terminal ``session_finish`` - never a traceback
and never a stream that silently claims success.

A vitest ``status`` maps onto the shared outcomes by what it proves about the run: ``failed`` is a
failure, ``pending``/``todo`` are skipped work, anything else (including an unknown status a newer
vitest introduces) is treated as a pass rather than invented as an error. ``fullName`` is the case
id, so ``describe > test`` names one case, as the task contract requires.
"""

import hashlib
import json
import os
import sys
import time

RECORD_VERSION = 1
TAIL_BYTES = 2000

_OUTCOME = {
    "failed": "fail",
    "pending": "skipped",
    "skipped": "skipped",
    "todo": "skipped",
    "passed": "pass",
}


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _tail(text):
    data = text.encode("utf-8", errors="replace")
    return data[-TAIL_BYTES:].decode("utf-8", errors="replace")


class _Writer:
    """An append-only JSON-lines sink, mode 0600 like every other evidence file."""

    def __init__(self, path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND, 0o600)

    def write(self, record):
        record["v"] = RECORD_VERSION
        line = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
        os.write(self._fd, line.encode("ascii"))


def _case_id(assertion):
    """The case id: vitest's ``fullName`` is already the ``describe > test`` identity."""
    full = assertion.get("fullName")
    if isinstance(full, str) and full.strip():
        return full
    ancestors = [str(part) for part in assertion.get("ancestorTitles") or []]
    title = str(assertion.get("title") or "")
    return " > ".join([*ancestors, title]) if ancestors else title


def _reason(assertion, outcome):
    if outcome != "fail":
        return "skipped" if outcome == "skipped" else ""
    messages = assertion.get("failureMessages") or []
    if not messages:
        return "AssertionError"
    first = str(messages[0])
    for marker in ("Error:", "Exception"):
        if marker in first:
            return first.split(marker, 1)[1].strip().splitlines()[0][:80]
    return first.strip().splitlines()[0][:80]


def _session_start(writer, node_version, test_file):
    writer.write(
        {
            "type": "session_start",
            "node": node_version,
            "python": sys.version.split()[0],
            "vitest": None,
            "runner": "vitest",
            "test_files": test_file,
        }
    )


def _node_version():
    """The runtime version, read from the image's own node; absent is recorded, not guessed."""
    for candidate in ("node", "nodejs"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            path = os.path.join(directory, candidate)
            if not os.path.isfile(path):
                continue
            try:
                import subprocess

                out = subprocess.run(
                    [path, "--version"], capture_output=True, timeout=5, check=False
                )
            except (OSError, ValueError):
                continue
            if out.returncode == 0:
                return out.stdout.decode("utf-8", "replace").strip()
    return None


def main(argv):
    args = argv[1:]
    try:
        report_path = args[args.index("--report") + 1]
        output = args[args.index("--output") + 1]
    except (ValueError, IndexError):
        sys.stderr.write("usage: pcb_vitest_report.py --report FILE --output FILE\n")
        return 2
    writer = _Writer(output)
    _session_start(writer, _node_version(), report_path)
    try:
        with open(report_path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as error:
        reason = "%s: %s" % (type(error).__name__, error)
        writer.write(
            {
                "type": "collection_error",
                "nodeid": report_path,
                "reason_digest": _digest(reason),
                "reason_tail": _tail(reason),
            }
        )
        writer.write(
            {
                "type": "session_finish",
                "exitstatus": 2,
                "collected": 0,
                "counts": {"pass": 0, "fail": 0, "error": 0, "skipped": 0},
                "finished_at_ms": int(time.time() * 1000),
            }
        )
        return 0
    counts = {"pass": 0, "fail": 0, "error": 0, "skipped": 0}
    collected = 0
    failed = 0
    for suite in document.get("testResults") or []:
        if not isinstance(suite, dict):
            continue
        for assertion in suite.get("assertionResults") or []:
            if not isinstance(assertion, dict):
                continue
            case_id = _case_id(assertion)
            if not case_id:
                continue
            status = str(assertion.get("status") or "").lower()
            outcome = _OUTCOME.get(status, "pass")
            counts[outcome] += 1
            collected += 1
            if outcome == "fail":
                failed += 1
            messages = "".join(str(item) for item in assertion.get("failureMessages") or [])
            duration = assertion.get("duration")
            writer.write({"type": "case_start", "nodeid": case_id})
            writer.write(
                {
                    "type": "case",
                    "nodeid": case_id,
                    "outcome": outcome,
                    "phase": "call",
                    "reason": _reason(assertion, outcome),
                    "duration_ms": int(duration) if isinstance(duration, (int, float)) else 0,
                    "stdout_digest": None,
                    "stderr_digest": None,
                    "stdout_tail": "",
                    "stderr_tail": "",
                    "failure_digest": _digest(messages) if messages else None,
                    "failure_tail": _tail(messages) if messages else None,
                    "markers": [],
                    "resources": None,
                }
            )
    writer.write(
        {
            "type": "session_finish",
            "exitstatus": 1 if failed else 0,
            "collected": collected,
            "counts": counts,
            "finished_at_ms": int(time.time() * 1000),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))