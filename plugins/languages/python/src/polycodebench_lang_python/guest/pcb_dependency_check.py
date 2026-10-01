"""Offline dependency advisory check against a *pinned* advisory snapshot.

Usage: python pcb_dependency_check.py --inventory FILE --advisories FILE --output FILE
The inventory is a JSON object ``{"package": "version"}``; the advisory snapshot maps package names
to lists of ``{"id", "affected": [versions], "severity"}``. A missing or unreadable snapshot is an
error (exit 2), never a clean result: a moving or absent advisory source cannot certify anything.
Exit status: 0 no advisory matches, 1 advisories found, 2 snapshot/inventory unusable.
"""

import json
import os
import sys


def main(argv):
    args = argv[1:]
    try:
        inventory_path = args[args.index("--inventory") + 1]
        advisory_path = args[args.index("--advisories") + 1]
        output = args[args.index("--output") + 1]
    except (ValueError, IndexError):
        return 2
    try:
        with open(inventory_path, encoding="utf-8") as handle:
            inventory = json.load(handle)
        with open(advisory_path, encoding="utf-8") as handle:
            advisories = json.load(handle)
    except (OSError, ValueError):
        sys.stderr.write("advisory snapshot or inventory unavailable\n")
        return 2
    found = []
    for name, version in sorted(inventory.items()):
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
            {"schema": "pcb-dependency-check-v1", "checked": sorted(inventory), "findings": found},
            handle,
            sort_keys=True,
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
