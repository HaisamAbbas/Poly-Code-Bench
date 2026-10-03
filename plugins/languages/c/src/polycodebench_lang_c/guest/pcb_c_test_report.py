"""Parse the C test harness's JSONL records into structured evidence.

Usage:
    python pcb_c_test_report.py --report FILE --exit-code N [--timed-out] [--group NAME]

The harness prints one JSON object per line on stdout. A record stream is *incomplete* unless it ends
with a ``session_finish`` that accounted for every declared case: a binary that died mid-suite, or a
stream truncated by a killed process, must read as incomplete rather than as a short pass.

Exit codes:
    0  the stream is complete and every case passed
    1  the stream is complete and at least one case failed
    2  the stream is incomplete, unparsable or missing
"""

import hashlib
import json
import sys

RECORD_VERSION = 1
EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_INCOMPLETE = 2
TAIL_BYTES = 4000


def digest(value):
    if value is None:
        return None
    return "sha256:" + hashlib.sha256(value).hexdigest()


def build_records(stdout, exit_code=None, timed_out=False, group=None):
    """Yield one dict per parsed record, in stream order.

    ``case_start`` records are what a timeout uses to name the case in flight; they are kept
    separately from ``case`` records so a case that started and never finished is not reported as a
    pass.
    """
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("v") != RECORD_VERSION:
            continue
        if group is not None and record.get("group") != group:
            continue
        yield record


def summarize(stdout, exit_code=None, timed_out=False, group=None, stderr=None):
    """One normalized document describing what the run proved."""
    records = list(build_records(stdout, exit_code, timed_out, group))
    cases = [r for r in records if r.get("kind") == "case"]
    starts = [r for r in records if r.get("kind") == "case_start"]
    finish = next((r for r in records if r.get("kind") == "session_finish"), None)
    observed = {str(r["case"]): str(r.get("outcome", "error")) for r in cases}
    declared = finish.get("declared") if finish else None
    incomplete = (
        finish is None
        or timed_out
        or (declared is not None and len(observed) != declared)
        or (exit_code is not None and exit_code not in (0, 1))
    )
    reason = "all declared cases passed"
    if incomplete:
        reason = "the harness did not account for every declared case"
    elif any(outcome != "pass" for outcome in observed.values()):
        reason = "at least one declared case failed"
    return {
        "schema": "pcb-c-test-report-v1",
        "record_version": RECORD_VERSION,
        "group": group,
        "declared_cases": declared,
        "observed_cases": len(observed),
        "cases": [
            {"case": case_id, "outcome": outcome} for case_id, outcome in sorted(observed.items())
        ],
        "in_flight_case": str(starts[-1]["case"]) if starts else None,
        "timed_out": bool(timed_out),
        "exit_code": exit_code,
        "complete": not incomplete,
        "summary": reason,
        "stdout_digest": digest(stdout.encode("utf-8", "replace") if stdout else None),
        "stderr_digest": digest(stderr.encode("utf-8", "replace") if stderr else None),
    }


def _parse(argv):
    options = {"report": None, "exit_code": None, "timed_out": False, "group": None}
    index = 1
    while index < len(argv):
        flag = argv[index]
        if flag == "--timed-out":
            options["timed_out"] = True
            index += 1
        elif flag.startswith("--") and index + 1 < len(argv):
            options[flag[2:].replace("-", "_")] = argv[index + 1]
            index += 2
        else:
            raise SystemExit(2)
    if options["report"] is None:
        raise SystemExit(2)
    if options["exit_code"] is not None:
        options["exit_code"] = int(options["exit_code"])
    return options


def main(argv):
    options = _parse(argv)
    try:
        with open(options["report"], encoding="utf-8", errors="replace") as handle:
            stdout = handle.read()
    except OSError:
        return EXIT_INCOMPLETE
    document = summarize(
        stdout,
        options["exit_code"],
        options["timed_out"],
        options["group"],
    )
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    if not document["complete"]:
        return EXIT_INCOMPLETE
    return EXIT_FAILURES if document["summary"].startswith("at least one") else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
