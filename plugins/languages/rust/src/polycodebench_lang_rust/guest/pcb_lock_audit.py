"""Offline dependency audit of a ``Cargo.lock`` against a *pinned* advisory snapshot.

Usage: python pcb_lock_audit.py --lock FILE --advisories FILE --output FILE

The lock is read for every registry package (name, version). The snapshot maps a crate name to a
list of ``{"id", "affected": [versions], "severity"}`` and declares where it came from. A missing,
unreadable or *empty* snapshot is an error (exit 2), never a clean result: an absent or moving
advisory source cannot certify anything, and an empty one has simply not been populated.

Exit status: 0 no advisory matches, 1 advisories found, 2 lock/snapshot unusable.
"""

import json
import os
import re
import sys

_PACKAGE = re.compile(r"^\[\[package\]\]\s*$")
_FIELD = re.compile(r'^(name|version|source)\s*=\s*"([^"]*)"\s*$')


def parse_lock(text):
    packages, current = [], None
    for line in text.splitlines():
        if _PACKAGE.match(line):
            current = {}
            packages.append(current)
            continue
        match = _FIELD.match(line)
        if match and current is not None:
            current[match.group(1)] = match.group(2)
    return [p for p in packages if "name" in p and "version" in p]


def main(argv):
    args = argv[1:]
    try:
        lock_path = args[args.index("--lock") + 1]
        advisory_path = args[args.index("--advisories") + 1]
        output = args[args.index("--output") + 1]
    except (ValueError, IndexError):
        return 2
    try:
        with open(lock_path, encoding="utf-8") as handle:
            packages = parse_lock(handle.read())
        with open(advisory_path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError):
        sys.stderr.write("advisory snapshot or lock unavailable\n")
        return 2
    advisories = snapshot.get("advisories")
    if not isinstance(advisories, dict) or not advisories:
        sys.stderr.write("advisory snapshot is empty; it cannot certify a lock\n")
        return 2
    found, checked = [], []
    for package in sorted(packages, key=lambda p: (p["name"], p["version"])):
        if "source" not in package:
            continue  # the task crate itself (path package), not a dependency
        checked.append("%s@%s" % (package["name"], package["version"]))
        for advisory in advisories.get(package["name"].lower(), []):
            if package["version"] in advisory.get("affected", []):
                found.append(
                    {
                        "package": package["name"],
                        "version": package["version"],
                        "advisory": advisory["id"],
                        "severity": advisory.get("severity", "medium"),
                    }
                )
    parent = os.path.dirname(output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema": "pcb-lock-audit-v1",
                "snapshot_source": snapshot.get("source"),
                "snapshot_entries": len(advisories),
                "checked": checked,
                "findings": found,
            },
            handle,
            sort_keys=True,
            separators=(",", ":"),
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
