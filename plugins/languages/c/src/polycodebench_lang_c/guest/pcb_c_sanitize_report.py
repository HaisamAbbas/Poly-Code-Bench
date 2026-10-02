"""Classify dynamic-instrumentation output for C (Technical Spec 12.5, 13.3; PCB-20-2, PCB-20-4).

Three lanes reach this module, and they answer different questions:

* **AddressSanitizer** - did an executed path read or write memory it does not own?
* **UndefinedBehaviorSanitizer** - did an executed path do something the C standard leaves undefined?
* **Valgrind memcheck** - did an executed path use an uninitialised value, leak, or misaddress memory?

The classification is deliberately coarse and evidence-bound. The hard part is not telling a real
defect from a clean run; it is refusing to read as "clean" the cases where the lane could not judge:

* a lane that could not be started, or crashed before reporting, is ``failed``;
* a lane that ran but found nothing is ``clean`` - **and says so in terms of executed paths**, because
  a dynamic tool has no evidence about code the tests never reached;
* an abort whose output carries no diagnostic is ``failed``, not ``clean``, even though the exit status
  could be zero.

A sanitizer aborting with a diagnostic is a *finding*, not a tool failure. That distinction is why
the text is read and the exit code is not: ASan exits 1 on a finding and UBSan exits 1 on a finding,
but a Valgrind run with ``--error-exitcode`` also exits 1 on a finding while a plain crash exits 134.
"""

import hashlib
import re
import sys

VERDICT_CLEAN = "clean"
VERDICT_FINDING = "finding"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_FAILED = "failed"

#: One normalized report record per finding.
_REPORT_BANNER = re.compile(
    r"^==(?P<pid>\d+)==\s*(?P<kind>ERROR|WARNING):\s*(?P<sanitizer>[A-Za-z]+):\s*(?P<summary>.+?)\s*$",
    re.MULTILINE,
)
_RUNTIME_ERROR = re.compile(
    r"(?P<file>[^\s:]+):(?P<line>\d+):(?P<column>\d+):\s*runtime error:\s*(?P<message>.+?)\s*$",
    re.MULTILINE,
)
_FRAME = re.compile(
    r"^\s*#(?P<index>\d+)\s+0x[0-9a-f]+\s+in\s+(?P<symbol>[^\s]+)\s+"
    r"(?P<file>[^\s:()]+):(?P<line>\d+)(?::(?P<column>\d+))?",
    re.MULTILINE,
)
#: A frame inside the candidate tree. Everything above the first libc frame is harness code.
_HARNESS_MARKERS = ("pcb_ctest", "/usr/lib/", "interceptor.c", "sanitizer_common")
_ASAN_KINDS = {
    "heap-buffer-overflow": ("high", "bounds-violation"),
    "stack-buffer-overflow": ("high", "bounds-violation"),
    "global-buffer-overflow": ("high", "bounds-violation"),
    "heap-use-after-free": ("high", "use-after-free"),
    "stack-use-after-return": ("high", "use-after-free"),
    "use-after-poison": ("high", "use-after-free"),
    "stack-use-after-scope": ("high", "use-after-free"),
    "double-free": ("high", "double-free"),
    "alloc-dealloc-mismatch": ("high", "invalid-free"),
    "attempting free on address which was not malloc()-ed": ("high", "invalid-free"),
    "invalid-free": ("high", "invalid-free"),
    "SEGV on unknown address": ("critical", "bounds-violation"),
    "stack-overflow": ("high", "stack-overflow"),
    "dynamic-stack-buffer-overflow": ("high", "bounds-violation"),
    "negative-size-param": ("high", "bounds-violation"),
    "container-overflow": ("medium", "bounds-violation"),
    "odr-violation": ("low", "definition-mismatch"),
}
_UBSAN_KINDS = {
    "shift-exponent": ("high", "shift-out-of-range"),
    "shift-base": ("medium", "shift-out-of-range"),
    "signed-integer-overflow": ("high", "signed-overflow"),
    "integer-overflow": ("high", "signed-overflow"),
    "division-by-zero": ("high", "division-by-zero"),
    "null": ("high", "null-pointer-dereference"),
    "pointer-overflow": ("medium", "pointer-arithmetic"),
    "misaligned-address": ("medium", "misaligned-access"),
    "invalid-load": ("high", "invalid-access"),
    "store-to-const": ("medium", "invalid-access"),
    "member-access-within-misaligned-address": ("medium", "misaligned-access"),
    "vptr": ("high", "invalid-access"),
    "function": ("high", "invalid-access"),
}
_UBSAN_CANONICAL = {
    "shift-exponent": "shift-exponent-too-large",
    "shift-base": "shift-exponent-too-large",
    "signed-integer-overflow": "signed-integer-overflow",
    "integer-overflow": "signed-integer-overflow",
    "division-by-zero": "division-by-zero",
    "null": "null-pointer-dereference",
    "misaligned-address": "misaligned-access",
    "member-access-within-misaligned-address": "misaligned-access",
    "invalid-load": "invalid-memory-access",
    "store-to-const": "invalid-memory-access",
    "function": "invalid-memory-access",
    "vptr": "invalid-memory-access",
    "pointer-overflow": "pointer-arithmetic-out-of-bounds",
}
_VALGRIND_KINDS = {
    "Invalid read": ("high", "bounds-violation"),
    "Invalid write": ("high", "bounds-violation"),
    "Invalid free": ("high", "invalid-free"),
    "Mismatched free": ("high", "invalid-free"),
    "Conditional jump or move depends on uninitialised value": ("medium", "uninitialised-value"),
    "Use of uninitialised value": ("high", "uninitialised-value"),
    "Invalid read of size": ("high", "bounds-violation"),
    "Syscall param": ("low", "uninitialised-value"),
}
#: Diagnostics that state the tool cannot judge this binary. Distinct from "found nothing".
_UNSUPPORTED = re.compile(
    r"(?:is not supported|does not support|cannot be run|unsupported operation|"
    r"DebuginfoDwarf is unsupported|no known bugs|AddressSanitizer is disabled|"
    r"ASan runtime does not come first)",
    re.IGNORECASE,
)
#: A C task can make a sanitizer unjudgeable on purpose.
_TASK_UNSUPPORTED = re.compile(
    r"(?:ASan runtime does not come first|initial-exec|interceptors not installed|"
    r"CHECK failed: .*Sanitizer)",
    re.IGNORECASE,
)
_TAIL_BYTES = 4000


def _digest(text):
    return None if text is None else "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _candidates(text):
    """First candidate frame (file, line, column, symbol) outside harness code."""
    for match in _FRAME.finditer(text):
        file = match.group("file")
        if any(marker in file for marker in _HARNESS_MARKERS):
            continue
        return {
            "file": file,
            "line": int(match.group("line")),
            "column": int(match.group("column")) if match.group("column") else None,
            "symbol": match.group("symbol"),
        }
    return None


def _asan_findings(text):
    findings = []
    banners = list(_REPORT_BANNER.finditer(text))
    for index, banner in enumerate(banners):
        if banner.group("sanitizer") not in {"AddressSanitizer", "UndefinedBehaviorSanitizer"}:
            continue
        end = banners[index + 1].start() if index + 1 < len(banners) else len(text)
        block = text[banner.start() : end]
        summary = banner.group("summary").strip()
        sanitizer = banner.group("sanitizer")
        kind = _kind_from_summary(summary)
        if sanitizer == "AddressSanitizer":
            severity, family = _ASAN_KINDS.get(kind, ("high", "memory-error"))
            canonical = family
        else:
            runtime = _RUNTIME_ERROR.search(block)
            sub = runtime.group("message").strip() if runtime else ""
            canonical = _UBSAN_CANONICAL.get(sub.split(":")[0].strip(), "undefined-behaviour")
            severity, family = _UBSAN_KINDS.get(sub.split(":")[0].strip(), ("high", "undefined-behaviour"))
        site = _candidates(block)
        findings.append(
            {
                "tool": sanitizer,
                "kind": kind,
                "family": canonical,
                "detail_family": family,
                "severity": severity,
                "summary": summary[:300],
                "file": site["file"] if site else None,
                "line": site["line"] if site else 0,
                "column": site["column"] if site else None,
                "symbol": site["symbol"] if site else None,
            }
        )
    return findings


def _kind_from_summary(summary):
    for known in sorted(_ASAN_KINDS, key=len, reverse=True):
        if summary.startswith(known) or known in summary:
            return known
    return summary.split(" on ")[0][:80]


def _ubsan_findings(text):
    findings = []
    for match in _RUNTIME_ERROR.finditer(text):
        raw = match.group("message").strip()
        key = raw.split(":")[0].strip()
        severity, family = _UBSAN_KINDS.get(key, ("high", "undefined-behaviour"))
        findings.append(
            {
                "tool": "UndefinedBehaviorSanitizer",
                "kind": key,
                "family": _UBSAN_CANONICAL.get(key, "undefined-behaviour"),
                "detail_family": family,
                "severity": severity,
                "summary": raw[:300],
                "file": match.group("file"),
                "line": int(match.group("line")),
                "column": int(match.group("column")),
                "symbol": None,
            }
        )
    return findings


def _valgrind_findings(text):
    findings = []
    pattern = re.compile(
        r"==(?P<pid>\d+)==\s*(?P<kind>Invalid read|Invalid write|Invalid free|Mismatched free|"
        r"Conditional jump or move depends on uninitialised value\S*|Use of uninitialised value\S*|"
        r"Invalid read of size \d+|Syscall param\S*)\b",
    )
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : end]
        severity, family = _VALGRIND_KINDS.get(match.group("kind"), ("medium", "memory-error"))
        site = _candidates(block)
        findings.append(
            {
                "tool": "valgrind-memcheck",
                "kind": match.group("kind"),
                "family": family,
                "detail_family": family,
                "severity": severity,
                "summary": match.group("kind")[:300],
                "file": site["file"] if site else None,
                "line": site["line"] if site else 0,
                "column": site["column"] if site else None,
                "symbol": site["symbol"] if site else None,
            }
        )
    # Valgrind's "definitely lost" blocks are leaks, which are a distinct consequence family from a
    # bounds violation; they are recorded rather than folded into the same count.
    for match in re.finditer(
        r"==(?P<pid>\d+)==\s+(?P<bytes>\d+) bytes definitely lost in loss record \d+", text
    ):
        end = text.find("\n==", match.end())
        block = text[match.end() : end if end > 0 else len(text)]
        site = _candidates(block)
        findings.append(
            {
                "tool": "valgrind-memcheck",
                "kind": "definitely-lost",
                "family": "resource-leak",
                "detail_family": "resource-leak",
                "severity": "medium",
                "summary": "%s bytes definitely lost" % match.group("bytes"),
                "file": site["file"] if site else None,
                "line": site["line"] if site else 0,
                "column": site["column"] if site else None,
                "symbol": site["symbol"] if site else None,
            }
        )
    return findings


_LANE_PARSERS = {
    "address": _asan_findings,
    "undefined": _ubsan_findings,
    "valgrind": _valgrind_findings,
}


def classify(text, lane, exit_code=None, timed_out=False):
    """Verdict for one dynamic lane over the captured output."""
    if timed_out:
        return VERDICT_FAILED, "the lane exceeded its deadline and reported nothing"
    if exit_code is not None and exit_code in (124,):
        return VERDICT_FAILED, "the lane was killed at its deadline"
    if _TASK_UNSUPPORTED.search(text):
        return VERDICT_UNSUPPORTED, "the binary cannot be instrumented in this configuration"
    findings = _LANE_PARSERS[lane](text)
    if findings:
        return VERDICT_FINDING, "%d dynamic finding(s) on executed paths" % len(findings)
    if _UNSUPPORTED.search(text):
        return VERDICT_UNSUPPORTED, "the lane reported that it cannot judge this binary"
    if exit_code is not None and exit_code not in (0, 1):
        return (
            VERDICT_FAILED,
            "the lane aborted with status %d and produced no diagnostic" % exit_code,
        )
    if lane == "valgrind" and "ERROR SUMMARY: 0 errors" not in text:
        return VERDICT_FAILED, "the memcheck summary is missing or non-zero without a parsed finding"
    return VERDICT_CLEAN, "no defect was reported on the paths this lane executed"


def findings(text, lane):
    return _LANE_PARSERS[lane](text)


def report(text, lane, exit_code=None, timed_out=False, tool_version=None):
    """One normalized document for the lane."""
    verdict, reason = classify(text, lane, exit_code, timed_out)
    return {
        "schema": "pcb-c-sanitizer-report-v1",
        "lane": lane,
        "tool_version": tool_version,
        "verdict": verdict,
        "inconclusive": verdict in {VERDICT_UNSUPPORTED, VERDICT_FAILED},
        "finding_count": len(findings(text, lane)) if verdict == VERDICT_FINDING else 0,
        "findings": findings(text, lane),
        "exit_code": exit_code,
        "timed_out": bool(timed_out),
        "reason": reason,
        "stdout_digest": _digest(text),
        "stdout_tail": (text or "")[-_TAIL_BYTES:],
    }


def main(argv):
    if len(argv) < 3:
        print("usage: pcb_c_sanitize_report.py --report FILE --lane LANE [--exit-code N]", file=sys.stderr)
        return 2
    options = {"report": argv[1], "lane": argv[2], "exit_code": None, "timed_out": False}
    index = 3
    while index < len(argv):
        if argv[index] == "--timed-out":
            options["timed_out"] = True
            index += 1
        elif index + 1 < len(argv):
            options["exit_code"] = int(argv[index + 1])
            index += 2
        else:
            return 2
    try:
        with open(options["report"], encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        print('{"schema":"pcb-c-sanitizer-report-v1","verdict":"failed",'
              '"reason":"the capture is unreadable"}')
        return 2
    import json

    print(json.dumps(report(text, options["lane"], options["exit_code"], options["timed_out"]),
                     sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))