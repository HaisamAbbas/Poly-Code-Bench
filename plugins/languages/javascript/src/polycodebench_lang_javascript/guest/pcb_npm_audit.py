"""Offline dependency audit of a ``package-lock.json`` against a *pinned* advisory snapshot.

Usage: python pcb_npm_audit.py --lock FILE --advisories FILE --output FILE

Every registry package in the lock is read as a ``(name, version)`` pair: the ``packages`` map of
a lockfile-version-2-or-3 document is keyed by install path, and an entry that carries a
``resolved`` registry URL is a dependency rather than a workspace link or the task's own root. The
snapshot maps a package name to a list of ``{"id", "affected": [versions], "severity"}`` and
declares where it came from.

A missing, unreadable or *empty* snapshot is an error (exit 2), never a clean result: an absent
advisory source cannot certify anything, and an empty one has simply not been populated. The same
is true of a lock with no ``lockfileVersion`` or no readable packages - the audit reports what it
actually checked, so a lock it could not enumerate is a failure to audit rather than a clean bill.

Exit status: 0 no advisory matches, 1 advisories found, 2 lock/snapshot unusable.
"""

import json
import os
import sys


def parse_lock(document):
    """``(name, version)`` for every registry package, or raise ``ValueError`` on a bad lock.

    An entry without a ``version`` is the workspace root or a link entry and is skipped; an entry
    without ``resolved`` was never fetched from a registry, so there is nothing to audit.
    """
    if not isinstance(document, dict):
        raise ValueError("lock is not an object")
    if not isinstance(document.get("lockfileVersion"), (int, float)):
        raise ValueError("lock has no lockfileVersion")
    packages = document.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise ValueError("lock has no packages map")
    found = []
    for path, entry in sorted(packages.items()):
        if not isinstance(entry, dict) or not path:
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[-1]
        version = entry.get("version")
        if not name or not version or not entry.get("resolved"):
            continue
        found.append((name, version))
    return found


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
            packages = parse_lock(json.load(handle))
        with open(advisory_path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError):
        sys.stderr.write("advisory snapshot or lock unavailable\n")
        return 2
    advisories = snapshot.get("advisories") if isinstance(snapshot, dict) else None
    if not isinstance(advisories, dict) or not advisories:
        sys.stderr.write("advisory snapshot is empty; it cannot certify a lock\n")
        return 2
    found, checked = [], []
    for name, version in sorted(packages):
        checked.append("%s@%s" % (name, version))
        for advisory in advisories.get(name.lower(), []):
            if version in advisory.get("affected", []):
                found.append(
                    {
                        "package": name,
                        "version": version,
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
                "schema": "pcb-npm-audit-v1",
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
