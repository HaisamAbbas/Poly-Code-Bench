"""Sanitizer result classifier: the four outcomes must never be confused.

An instrumented C++ run can end in four materially different ways, and each has to be reported as
its own thing:

1. **Clean** - the instrumented binary ran to completion with exit 0 and emitted no sanitizer
   banner. This is the only outcome that may be recorded as a completed scan with no defects.
2. **Candidate defect** - ASan, LSan, UBSan or TSan printed a real report whose frames name a
   candidate source file. That is a defect introduced by the candidate and becomes a finding.
3. **Unsupported** - the runtime refused to run at all: shadow memory could not be mapped, the
   sanitizer runtime was not first in the library list, an interceptor could not be installed. The
   run was *inconclusive*, which is emphatically not a clean run and emphatically not a candidate
   defect: it is MISSING evidence.
4. **Failed** - crashed, aborted or timed out with no sanitizer verdict to read.

Exit status alone is never sufficient: an instrumented binary exits non-zero for a sanitizer
report, for a plain non-zero exit and for an unsupported runtime alike. Only the diagnostic body
separates them.

Usage:
    python pcb_sanitizer_report.py --detector asan|ubsan|tsan --report out/asan.json
                                  --stdout out/asan.out --stderr out/asan.err
                                  --exit-code N [--timed-out]

The module also exports ``classify(text, exit_code, timed_out, detector)`` so the host parser and
its tests reuse exactly the classification the guest ran.
"""

import argparse
import hashlib
import json
import os
import re
import sys

RECORD_VERSION = 1
SCHEMA = "pcb-cpp-sanitizer-v1"
TAIL_BYTES = 4000
DETECTORS = ("asan", "ubsan", "tsan")

# The banner each runtime prints for a real report. UBSan has no banner of its own: its runtime
# error lines are the report, so `_UBSAN_DIAGNOSTIC` below carries it.
_BANNER = {
    "asan": re.compile(
        r"ERROR:\s*(?:AddressSanitizer|LeakSanitizer):\s*([A-Za-z0-9_-]+)"
        r"|WARNING:\s*ThreadSanitizer:\s*([A-Za-z0-9_ -]+?)\s*\(",
    ),
    "ubsan": re.compile(r"runtime error:"),
    "tsan": re.compile(
        r"WARNING:\s*ThreadSanitizer:\s*([A-Za-z0-9_ -]+?)\s*\(|"
        r"SUMMARY:\s*ThreadSanitizer:\s*([A-Za-z0-9_ -]+)"
    ),
}
# The runtime refused to run. Every one of these names the loader, the shadow mapping or an
# interceptor rather than the candidate's code.
_UNSUPPORTED = re.compile(
    r"(Shadow memory range interleaves with an existing memory mapping"
    r"|Failed to map the shadow memory"
    r"|AddressSanitizer failed to allocate 0x"
    r"|Shadow memory range interleaves"
    r"|ASan runtime does not come first in initial library list"
    r"|Interceptors are not working\. \(*ASan runtime does not come first"
    r"|ThreadSanitizer: unexpected memory mapping"
    r"|ThreadSanitizer failed to allocate"
    r"|Unable to allocate shadow memory"
    r"|can't be installed due to the conflict with existing"
    r"|unable to map shadow memory"
    r"|ERROR: Failed to mmap"
    r"|unsupported hardware|UNSUPPORTED hardware"
    r"|ASan is not supported on this platform"
    r"|detect_leaks is not supported on this platform)",
    re.IGNORECASE,
)
# A crash with no sanitizer verdict at all.
_CRASH = re.compile(r"(Segmentation fault|SIGSEGV|SIGABRT|Bus error|Aborted \(core dumped\))")
# One symbolized stack frame. C++ function names can contain spaces, notably
# `(anonymous namespace)`, so parse the source path by its C/C++ suffix rather than one token.
_FRAME = re.compile(
    r"#\d+\s+(?:0x[0-9a-fA-F]+\s+)?(?:in\s+.*?)?\(?([^\s():]+\.(?:cpp|cc|cxx|hpp|hh|hxx|h|c|ipp)):(\d+)(?::(\d+))?"
)
# A UBSan line: `src/top_words.cpp:12:5: runtime error: ...`
_UBSAN_LINE = re.compile(
    # The path group must not swallow preceding text on the same line: it is anchored to the
    # start of the line so a UBSan report in the middle of a report is still found, and so the
    # file name it attributes the defect to is the real one.
    r"^(\S+\.(?:cpp|cc|cxx|hpp|h|c|ipp)):(\d+):(\d+):\s*runtime error:",
    # Without MULTILINE the `^` can only match at offset 0, so `finditer` over a whole report
    # returns nothing and every UBSan diagnostic is lost -- recorded as a clean scan.
    re.MULTILINE,
)
# The `SUMMARY:` line repeats the kind and the first candidate frame, and is the stable headline.
_SUMMARY = re.compile(
    r"SUMMARY:\s*(?:AddressSanitizer|UndefinedBehaviorSanitizer|ThreadSanitizer|LeakSanitizer):"
    r"\s*([A-Za-z0-9_ -]+?)\s*(?:\(|in|$)",
    re.MULTILINE,
)
# Frames in the sanitizer's own runtime or in the system libraries are never the candidate's.
_SYSTEM_PREFIXES = (
    "/usr/",
    "/opt/pcb/",
    "/lib/",
    "/build/",
    "libclang_rt",
    "sanitizer_common",
    "system_suppression",
    "interception",
)


def _kind_from_banner(detector, text):
    pattern = _BANNER.get(detector)
    if pattern is None:
        return ""
    match = pattern.search(text)
    if not match:
        return ""
    for group in match.groups():
        if group:
            return group.strip()
    # UBSan prints no banner of its own: its `runtime error:` lines are the report, and the kind
    # is whatever the diagnostic names.
    return "undefined-behaviour" if detector == "ubsan" else ""


def classify(text: str, exit_code: int, timed_out: bool, detector: str):
    """Return one of: clean | candidate-defect | unsupported | failed."""
    if timed_out:
        return "failed"
    if _UNSUPPORTED.search(text):
        # An unsupported runtime makes the run inconclusive even if a report-looking line also
        # appears: nothing the sanitizer printed can be attributed to the candidate.
        return "unsupported"
    kind = _kind_from_banner(detector, text)
    if kind or _UBSAN_LINE.search(text):
        if diagnostic_locations(text, detector):
            return "candidate-defect"
    if exit_code == 0:
        return "clean"
    if _CRASH.search(text):
        return "failed"
    return "failed"


def _is_candidate_frame(path):
    if path in ("", "unknown", "<unknown module>", "???"):
        return False
    if path.startswith(("file:", "binary:", "archive:")):
        return path.startswith("file:")
    return not any(path.startswith(prefix) for prefix in _SYSTEM_PREFIXES)


def _normalise_kind(kind):
    """A kind names the defect class, not the whole summary sentence."""
    text = " ".join(kind.split()).strip().lower()
    if re.match(r"^\d+\s+byte", text) or "leak" in text:
        return "memory-leak"
    text = re.sub(r"\s*\(.*$", "", text)
    return text.strip(" .:-") or "undefined-behaviour"


def diagnostic_locations(text, detector):
    """Every (kind, path, line, column) the runtime named, candidate files only."""
    kind = _normalise_kind(_kind_from_banner(detector, text) or "undefined-behaviour")
    summary = _SUMMARY.search(text)
    if summary:
        kind = _normalise_kind(summary.group(1) or kind)
    elif detector == "ubsan":
        kind = "undefined-behaviour"
    found = []
    seen = set()
    for match in _FRAME.finditer(text):
        path = match.group(1)
        if not _is_candidate_frame(path):
            continue
        entry = (kind, path, int(match.group(2)), int(match.group(3) or 0))
        if entry not in seen:
            seen.add(entry)
            found.append(entry)
    for match in _UBSAN_LINE.finditer(text):
        path = match.group(1)
        if not _is_candidate_frame(path):
            continue
        entry = (kind, path, int(match.group(2)), int(match.group(3)))
        if entry not in seen:
            seen.add(entry)
            found.append(entry)
    return found[:40]


def messages(text, limit=40):
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if (
            stripped.startswith("ERROR:")
            or stripped.startswith("WARNING:")
            or stripped.startswith("SUMMARY:")
            or "runtime error:" in stripped
        ):
            out.append(stripped[:300])
        if len(out) >= limit:
            break
    return out


def digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _tail(text):
    data = text.encode("utf-8", errors="replace")
    return data[-TAIL_BYTES:].decode("utf-8", errors="replace")


def build_report(combined, stdout, stderr, exit_code, timed_out, detector):
    verdict = classify(combined, exit_code, timed_out, detector)
    candidate_defect = verdict == "candidate-defect"
    diagnostics = []
    if candidate_defect:
        for kind, path, line, column in diagnostic_locations(combined, detector):
            diagnostics.append(
                {"kind": kind, "message": kind, "path": path, "line": line, "column": column}
            )
    return {
        "schema": SCHEMA,
        "report_version": RECORD_VERSION,
        "detector": detector,
        "verdict": verdict,
        "inconclusive": verdict in ("unsupported", "failed"),
        "candidate_defect": candidate_defect,
        "exit_code": None if timed_out else exit_code,
        "timed_out": bool(timed_out),
        "diagnostics": diagnostics,
        "messages": messages(combined),
        "stdout_digest": digest(stdout),
        "stderr_digest": digest(stderr),
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }


def main(argv):
    parser = argparse.ArgumentParser(prog="pcb_sanitizer_report")
    parser.add_argument("--detector", required=True, choices=list(DETECTORS))
    parser.add_argument("--report", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--timed-out", action="store_true")
    args = parser.parse_args(argv[1:])

    with open(args.stdout, encoding="utf-8", errors="replace") as handle:
        stdout = handle.read()
    with open(args.stderr, encoding="utf-8", errors="replace") as handle:
        stderr = handle.read()
    combined = stdout + ("\n" + stderr if stderr else "")
    document = build_report(combined, stdout, stderr, args.exit_code, args.timed_out, args.detector)
    parent = os.path.dirname(args.report)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
