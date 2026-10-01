"""Rust test-report parser: libtest output -> the same structured case/control records.

Rust has no pytest, so this is the guest-side equivalent of ``pcb_pytest_report``: it runs the
compiled test binary once per group and writes JSON lines so the supervisor can tell a *candidate*
failure (a ``case`` record with outcome fail/error, including a case timeout) from a *harness*
failure (no ``session_finish`` record, an unreadable report, a build error, or a non-zero status
without matching case records).

Every test runs single-threaded and in declaration order so outcomes are deterministic and a
timeout can be attributed to the case that was in flight. Output is captured by the caller; this
script only turns that captured text into records.

Usage: python pcb_rust_test_report.py --report FILE --stdout FILE --stderr FILE --exit-code N
"""

import argparse
import hashlib
import json
import os
import re
import sys

RECORD_VERSION = 1
TAIL_BYTES = 4000

# `test tests::foo ... ok` / `... FAILED` / `... ignored` / `... ok (12ms)`. Horizontal whitespace
# only: with a single test thread libtest prints `test name ... ` *before* the test runs, so a hung
# test leaves a line with no outcome, and a pattern that could cross a newline would swallow the
# next line as that test's result.
_CASE = re.compile(r"^test[ 	]+(?P<name>\S+)[ 	]+\.\.\.[ 	]+(?P<status>\S.*?)[ 	]*$")
_STARTED = re.compile(r"^test[ 	]+(?P<name>\S+)[ 	]+\.\.\.[ 	]*$")
_SUMMARY = re.compile(r"^test result:\s+(?P<verdict>ok|FAILED)\.\s+(?P<detail>.*)$", re.MULTILINE)
_RUNNING = re.compile(r"^running\s+(?P<count>\d+)\s+tests?$", re.MULTILINE)
# `Running unittests src/lib.rs (target/...)`, `Running tests/hidden_core.rs (target/...)`.
_UNIT = re.compile(r"^\s*Running\s+(?:unittests\s+)?(?P<path>\S+)\s+\(")
_DOC = re.compile(r"^\s*Doc-tests\s+(?P<crate>\S+)")


def _stem(path):
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-3] if base.endswith(".rs") else base


class _Writer:
    def __init__(self, path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND, 0o600)

    def write(self, record):
        record["v"] = RECORD_VERSION
        line = json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
        os.write(self._fd, line.encode("ascii"))

    def close(self):
        os.close(self._fd)


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _tail(text):
    data = text.encode("utf-8", errors="replace")
    return data[-TAIL_BYTES:].decode("utf-8", errors="replace")


def _outcome(status: str):
    status = status.strip()
    if status.startswith("ok"):
        return "pass"
    if status.startswith("ignored"):
        return "skipped"
    if status.startswith("FAILED"):
        return "fail"
    return "error"


def build_records(stdout: str, stderr: str, exit_code, timed_out: bool):
    """Turn one run's captured output into candidate case records plus a session control record.

    Case ids are qualified by the test binary that printed them (``hidden_core::nested::deep``;
    ``lib::tests::adds`` for unit tests), because two binaries may both contain ``tests::adds``.
    A ``test x ... `` line with no outcome is reported as ``case_start`` with no matching ``case``:
    that is how the supervisor learns which case was in flight when a run was killed.
    """
    combined = stdout + ("\n" + stderr if stderr else "")
    declared = None
    match = _RUNNING.search(combined)
    if match:
        declared = int(match.group("count"))
    seen = []
    started = []
    unit = None
    for line in stdout.splitlines():
        found = _UNIT.match(line)
        if found:
            unit = _stem(found.group("path"))
            continue
        found = _DOC.match(line)
        if found:
            unit = "doc"
            continue
        found = _CASE.match(line)
        if found:
            name = found.group("name")
            qualified = "%s::%s" % (unit, name) if unit else name
            if qualified in seen:
                continue  # a retried/duplicated line is not a second case
            seen.append(qualified)
            status = _outcome(found.group("status"))
            yield {
                "kind": "case",
                "name": qualified,
                "outcome": status,
                "ignored": status == "skipped",
                "xfail": False,
                "duration_ms": _duration(found.group("status")),
            }
            continue
        found = _STARTED.match(line)
        if found:
            name = found.group("name")
            started.append("%s::%s" % (unit, name) if unit else name)
    for name in started:
        if name not in seen:
            yield {"kind": "case_start", "name": name}
    summary = None
    for match in _SUMMARY.finditer(combined):
        summary = match
    yield {
        "kind": "session_finish",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "declared_tests": declared,
        "observed_cases": len(seen),
        "summary": (summary.group("detail").strip() if summary else None),
        "stdout_digest": _digest(stdout),
        "stderr_digest": _digest(stderr),
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }


def _duration(status):
    match = re.search(r"\((\d+)ms\)", status)
    return int(match.group(1)) if match else 0


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_rust_test_report")
    parser.add_argument("--report", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--timed-out", action="store_true")
    args = parser.parse_args(argv[1:])

    stdout = open(args.stdout, encoding="utf-8", errors="replace").read()
    stderr = open(args.stderr, encoding="utf-8", errors="replace").read()
    writer = _Writer(args.report)
    for record in build_records(stdout, stderr, args.exit_code, args.timed_out):
        writer.write(record)
    writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
