"""Miri result classifier: the three outcomes must never be confused.

Miri's ``cargo miri test`` can end in three materially different ways, and Prompt 11 requires each
to be reported as its own thing:

1. **Clean** - the interpreter checked every reachable path and found no UB, and nothing in the
   task was outside Miri's supported subset. This is the only outcome that may be recorded as a
   completed scan with zero findings.
2. **Candidate UB** - Miri found actual undefined behaviour introduced by the candidate (an
   out-of-bounds access, a data race, an invalid enum discriminant, ...). These are candidate
   defects and become findings against the candidate.
3. **Unsupported** - the task used an operation Miri does not support (foreign functions, some
   syscalls, networking, ...), so the scan was *inconclusive*. This is emphatically **not** a clean
   scan and emphatically **not** candidate UB: it means Miri could not judge, so the scan is
   ``missing``/not-applicable evidence. Miri reports these as ``error: unsupported operation`` or
   ``-Zmiri-... is not supported`` rather than as an Undefined Behavior diagnostic, so the two are
   separable by the diagnostic text, not by the exit status.

Exit status alone is never sufficient: Miri exits non-zero for unsupported operations, UB and
compile errors alike. Only the diagnostic body distinguishes them.

Usage: python pcb_miri_report.py --report FILE --stdout FILE --stderr FILE --exit-code N
"""

import argparse
import hashlib
import json
import os
import re
import sys

RECORD_VERSION = 1
TAIL_BYTES = 4000

# Miri prints Undefined Behavior as an explicit error with this banner.
_UB_BANNER = re.compile(r"error:\s*Undefined Behavior", re.IGNORECASE)
# These are the UB diagnostic heads Miri emits; they are *evidence of candidate UB*.
_UB_DIAGNOSTIC = re.compile(
    r"error:\s*"
    r"(?:attempted to leave an object dangling|"
    r"constructing invalid value|"
    r"encountered a panic|"
    r"integer-to-pointer cast|"
    r"out-of-bounds|"
    r"misaligned pointer dereference|"
    r"a dangling pointer|"
    r"retagging from .* to .*|"
    r"data race)",
    re.IGNORECASE,
)
# Anything that names an unsupported feature means Miri could not decide.
_UNSUPPORTED = re.compile(
    r"(unsupported operation|"
    r"operation not supported|"
    r"-Zmiri-[a-z-]+ (?:is )?not supported|"
    r"is not supported on this target|"
    r"extern (?:static|fn) .* is not supported|"
    r"not implemented on this target|"
    r"simulated `extern`|"
    r"target OS is not supported)",
    re.IGNORECASE,
)


def classify(text: str, exit_code: int, timed_out: bool):
    """Return one of: clean | candidate-ub | unsupported | failed."""
    if timed_out:
        return "failed"
    if _UNSUPPORTED.search(text):
        # An unsupported operation makes the scan inconclusive even if a UB-looking line also
        # appears: Miri could not evaluate the task, so nothing can be attributed to the candidate.
        return "unsupported"
    if _UB_BANNER.search(text) or _UB_DIAGNOSTIC.search(text):
        return "candidate-ub"
    if exit_code == 0:
        return "clean"
    # Non-zero without a recognised diagnostic: a build/compile failure or an unparsable result.
    return "failed"


def _diagnostics(text: str):
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("error:") or stripped.startswith("warning:"):
            out.append(stripped[:300])
    return out[:40]


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _tail(text):
    data = text.encode("utf-8", errors="replace")
    return data[-TAIL_BYTES:].decode("utf-8", errors="replace")


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_miri_report")
    parser.add_argument("--report", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--timed-out", action="store_true")
    parser.add_argument("--tool-version", default="")
    args = parser.parse_args(argv[1:])

    stdout = open(args.stdout, encoding="utf-8", errors="replace").read()
    stderr = open(args.stderr, encoding="utf-8", errors="replace").read()
    combined = stdout + ("\n" + stderr if stderr else "")
    verdict = classify(combined, args.exit_code, args.timed_out)

    document = {
        "schema": "pcb-miri-report-v1",
        "record_version": RECORD_VERSION,
        "tool_version": args.tool_version,
        "verdict": verdict,
        "inconclusive": verdict in {"unsupported", "failed"},
        "candidate_ub": verdict == "candidate-ub",
        "exit_code": None if args.timed_out else args.exit_code,
        "timed_out": bool(args.timed_out),
        "diagnostics": _diagnostics(combined),
        "stdout_digest": _digest(stdout),
        "stderr_digest": _digest(stderr),
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }
    parent = os.path.dirname(args.report)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
