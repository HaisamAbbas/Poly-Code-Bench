"""Audit the frozen Java dependency resolution against a pinned advisory snapshot.

The Java analogue of ``pcb_lock_audit.py``. ``mvn -o dependency:list`` reports what the POM resolved
to on this run; the *authoritative* resolution is the task's frozen ``deps.lock.json``, and this
script checks both against a snapshot of advisories pinned in the image. Two things follow:

* a dependency that the snapshot flags is a finding with the security dimension as its single
  composite owner, so it is never also counted as a diagnostic item;
* a resolution that disagrees with the frozen lock is itself reported. A drifted resolution means
  the run did not use the environment admission froze, which is an integrity problem rather than a
  style note.

The snapshot is data, never code: an advisory names a coordinate prefix and a severity, so adding a
new one is a reviewed data edit rather than a code change.
"""

import argparse
import json
import sys

SCHEMA = "pcb-dependency-audit-v1"


def load_snapshot(path):
    with open(path, "rb") as handle:
        document = json.load(handle)
    entries = document.get("advisories", [])
    if not entries:
        raise SystemExit("advisory snapshot is empty")
    return document, entries


def resolve_report(path):
    """``groupId:artifactId:type:version:scope`` lines from ``dependency:list`` output."""
    found = []
    if not path:
        return found
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return found
    for line in text.splitlines():
        # `dependency:list` writes each coordinate as
        # `group:artifact:type:version:scope -- module <name>`. The trailing clause is part of the
        # line, so the version and scope fields must be cut at it before splitting; otherwise the
        # version parses as `5.10.2:test` and the scope as `test -- module ...`, and nothing is
        # recognised.
        coordinate = line.split(" -- ", 1)[0].strip()
        parts = coordinate.split(":")
        if len(parts) == 5 and parts[0] and parts[1] and parts[3]:
            found.append(
                {
                    "coordinate": "%s:%s:%s" % (parts[0], parts[1], parts[3]),
                    "group_id": parts[0],
                    "artifact_id": parts[1],
                    "version": parts[3],
                    "scope": parts[4],
                }
            )
    return found


def lock_coordinates(path):
    if not path:
        return []
    try:
        with open(path, "rb") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return []
    return [
        "%s:%s:%s" % (item["group_id"], item["artifact_id"], item["version"])
        for item in document.get("artifacts", [])
    ]


def audit(resolved, locked, entries):
    """Which resolved coordinates an advisory covers, and how the two resolutions differ."""
    findings = []
    for entry in entries:
        prefix = entry["coordinate_prefix"]
        for item in resolved:
            coordinate = item["coordinate"]
            if coordinate.startswith(prefix):
                findings.append(
                    {
                        "advisory": entry["id"],
                        "coordinate": coordinate,
                        "severity": entry.get("severity", "medium"),
                        "summary": entry.get("summary", ""),
                    }
                )
                break
    return findings, sorted({item["coordinate"] for item in resolved} ^ set(locked))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pcb_dependency_audit")
    parser.add_argument("--report", default="")
    parser.add_argument("--lock", default="")
    parser.add_argument("--advisories", required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args(argv)
    document, entries = load_snapshot(options.advisories)
    resolved = resolve_report(options.report)
    locked = lock_coordinates(options.lock)
    findings, drifted = audit(resolved, locked, entries)
    payload = {
        "schema": SCHEMA,
        "snapshot": document.get("snapshot_id", ""),
        "snapshot_entries": len(entries),
        "checked": [item["coordinate"] for item in resolved],
        "locked": locked,
        "resolution_drift": drifted,
        "findings": findings,
    }
    with open(options.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
